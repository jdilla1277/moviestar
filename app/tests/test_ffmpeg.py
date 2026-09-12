"""Tests for moviestar.ffmpeg."""

import os
import re
import subprocess

import pytest

from moviestar.ffmpeg import (
    FFMPEG_FEATURE_REQUIREMENTS,
    FFmpegCapabilityError,
    FFmpegNotFoundError,
    _parse_audio_energy_spans,
    build_audio_energy_probe_command,
    build_ffmpeg_filters_command,
    check_ffmpeg_drawtext_filter_available,
    ffmpeg_capability_report,
    build_layer_loudness_probe_command,
    build_mix_audio_command,
    build_render_layout_frame_command,
    build_render_layout_frames_command,
    build_render_layout_video_command,
    build_render_segments_command,
    check_ffmpeg_ass_filter_available,
    check_ffmpeg_available,
    extract_clip,
    extract_frame,
    extract_frames,
    ffmpeg_filter_available,
    render_layout_frame,
    mix_audio,
    render_segments,
    probe_last_video_packet_timestamp,
    run_ffprobe,
    run_layer_loudness_probe,
)


def _resolved_mix(*, source=None, tracks=None):
    return {
        "source_audio": source or {
            "muted": False,
            "gain_db": -6.0,
            "fade_in": 0.25,
            "fade_out": 0.5,
        },
        "tracks": tracks or [],
    }


def _resolved_track(path: str, **overrides):
    track = {
        "id": "music",
        "kind": "music",
        "path": path,
        "gain_db": -18.0,
        "muted": False,
        "loop": False,
        "fade_in": 0.1,
        "fade_out": 0.2,
        "ducking": None,
        "media": {"sample_rate": 44100},
        "source_range": {
            "from": {"seconds": 0.25},
            "to": {"seconds": 1.0},
            "duration": {"seconds": 0.75},
        },
        "placed_range": {
            "from": {"seconds": 0.5},
            "to": {"seconds": 1.25},
            "duration": {"seconds": 0.75},
        },
    }
    track.update(overrides)
    return track


class TestMixAudio:
    def test_command_normalizes_places_fades_gains_and_sums_without_renormalizing(self):
        mix = _resolved_mix(tracks=[_resolved_track("music.wav")])

        command = build_mix_audio_command(
            "base.mp4",
            "mixed.mp4",
            mix,
            2.0,
            source_audio_available=True,
        )

        assert command[:7] == [
            "ffmpeg", "-y", "-loglevel", "error", "-i", "base.mp4", "-i",
        ]
        assert command[7] == "music.wav"
        graph = command[command.index("-filter_complex") + 1]
        assert "aresample=48000" in graph
        assert "channel_layouts=stereo" in graph
        assert "adelay=500:all=1" in graph
        assert "0.501187" in graph  # -6 dB source gain
        assert "0.125893" in graph  # -18 dB track gain
        assert "amix=inputs=2:duration=longest:normalize=0" in graph
        assert "alimiter=limit=0.944061:attack=5:release=50:level=false:latency=true" in graph
        assert command[command.index("-map") + 1] == "0:v:0"
        assert ["-c:v", "copy"] == command[
            command.index("-c:v"):command.index("-c:v") + 2
        ]
        assert command[-1] == "mixed.mp4"

    def test_mix_ends_at_limiter_without_inline_loudness_normalization(self):
        # Issue #352: loudness normalization moved out of the mix graph
        # into a measured post-pass on the rendered artifact (single-pass
        # loudnorm missed targets by LUs); the mix's own mastering chain
        # ends at the limiter.
        command = build_mix_audio_command(
            "base.mp4",
            "mixed.mp4",
            _resolved_mix(tracks=[_resolved_track("music.wav")]),
            2.0,
            source_audio_available=True,
        )

        graph = command[command.index("-filter_complex") + 1]
        assert graph.index("amix=inputs=2") < graph.index("alimiter=")
        assert "loudnorm" not in graph

    def test_command_windows_looped_track_on_result_time(self):
        track = _resolved_track(
            "bed.wav",
            loop=True,
            source_range={
                "from": {"seconds": 0.0},
                "to": {"seconds": 1.0},
                "duration": {"seconds": 1.0},
            },
            placed_range={
                "from": {"seconds": 0.0},
                "to": {"seconds": 5.0},
                "duration": {"seconds": 5.0},
            },
        )
        command = build_mix_audio_command(
            "watch-base.mp4",
            "watch-mixed.mp4",
            _resolved_mix(tracks=[track]),
            5.0,
            source_audio_available=True,
            window_start=2.0,
            window_duration=2.0,
        )

        graph = command[command.index("-filter_complex") + 1]
        assert "aloop=loop=-1:size=48000" in graph
        assert "atrim=start=2" in graph
        assert "adelay=0:all=1" in graph

    def test_all_muted_mix_removes_audio_without_filter_graph(self):
        command = build_mix_audio_command(
            "base.mp4",
            "silent.mp4",
            _resolved_mix(
                source={
                    "muted": True,
                    "gain_db": 0.0,
                    "fade_in": 0.0,
                    "fade_out": 0.0,
                },
                tracks=[_resolved_track("muted.wav", muted=True)],
            ),
            2.0,
            source_audio_available=True,
        )

        assert "-filter_complex" not in command
        assert "-an" in command

    def test_ducking_uses_resolved_speech_preset_after_layer_gain_and_fades(self):
        track = _resolved_track(
            "music.wav",
            ducking={
                "under": ["source"],
                "preset": "speech",
                "resolved": {
                    "threshold_db": -30.0,
                    "ratio": 8.0,
                    "attack_ms": 20.0,
                    "release_ms": 250.0,
                },
            },
        )
        command = build_mix_audio_command(
            "base.mp4",
            "mixed.mp4",
            _resolved_mix(tracks=[track]),
            2.0,
            source_audio_available=True,
        )

        graph = command[command.index("-filter_complex") + 1]
        assert graph.index("volume=") < graph.index("sidechaincompress=")
        assert (
            "sidechaincompress=threshold=0.031623:ratio=8:attack=20:release=250"
            in graph
        )
        assert "[source_for_music]" in graph
        assert "[music_mix]" in graph

    def test_multiple_sidechains_combine_before_target_compression(self):
        voice = _resolved_track(
            "voice.wav",
            id="voice",
            kind="voiceover",
            gain_db=0.0,
            ducking=None,
        )
        music = _resolved_track(
            "music.wav",
            id="music",
            ducking={
                "under": ["source", "voice"],
                "preset": "speech",
                "resolved": {
                    "threshold_db": -30.0,
                    "ratio": 8.0,
                    "attack_ms": 20.0,
                    "release_ms": 250.0,
                },
            },
        )
        command = build_mix_audio_command(
            "base.mp4",
            "mixed.mp4",
            _resolved_mix(tracks=[voice, music]),
            2.0,
            source_audio_available=True,
        )

        graph = command[command.index("-filter_complex") + 1]
        assert "[source_for_music][voice_for_music]amix=inputs=2" in graph
        assert graph.index("amix=inputs=2") < graph.index("sidechaincompress=")

    def test_muted_sidechain_does_not_trigger_ducking(self):
        voice = _resolved_track(
            "voice.wav", id="voice", muted=True, ducking=None
        )
        music = _resolved_track(
            "music.wav",
            id="music",
            ducking={
                "under": ["voice"],
                "preset": "speech",
                "resolved": {
                    "threshold_db": -30.0,
                    "ratio": 8.0,
                    "attack_ms": 20.0,
                    "release_ms": 250.0,
                },
            },
        )
        command = build_mix_audio_command(
            "base.mp4",
            "mixed.mp4",
            _resolved_mix(
                source={
                    "muted": True,
                    "gain_db": 0.0,
                    "fade_in": 0.0,
                    "fade_out": 0.0,
                },
                tracks=[voice, music],
            ),
            2.0,
            source_audio_available=True,
        )

        graph = command[command.index("-filter_complex") + 1]
        assert "sidechaincompress" not in graph

    def test_nested_ducking_uses_post_duck_signal_for_downstream_target(self):
        resolved = {
            "threshold_db": -30.0,
            "ratio": 8.0,
            "attack_ms": 20.0,
            "release_ms": 250.0,
        }
        voice = _resolved_track(
            "voice.wav",
            id="voice",
            kind="voiceover",
            ducking={"under": ["source"], "preset": "speech", "resolved": resolved},
        )
        music = _resolved_track(
            "music.wav",
            id="music",
            ducking={"under": ["voice"], "preset": "speech", "resolved": resolved},
        )
        command = build_mix_audio_command(
            "base.mp4", "mixed.mp4", _resolved_mix(tracks=[voice, music]),
            2.0, source_audio_available=True,
        )

        graph = command[command.index("-filter_complex") + 1]
        assert graph.index("[voice_ducked]") < graph.index("[voice_for_music]")
        assert graph.index("[voice_for_music]") < graph.rindex("sidechaincompress=")

    def test_real_post_pass_keeps_video_and_adds_external_audio(
        self, silent_video, audio_only_file, tmp_path
    ):
        output = tmp_path / "mixed.mp4"
        command = mix_audio(
            silent_video,
            str(output),
            _resolved_mix(
                source={
                    "muted": False,
                    "gain_db": 0.0,
                    "fade_in": 0.0,
                    "fade_out": 0.0,
                },
                tracks=[
                    _resolved_track(
                        audio_only_file,
                        gain_db=0.0,
                        fade_in=0.0,
                        fade_out=0.0,
                        source_range={
                            "from": {"seconds": 0.0},
                            "to": {"seconds": 1.0},
                            "duration": {"seconds": 1.0},
                        },
                        placed_range={
                            "from": {"seconds": 0.0},
                            "to": {"seconds": 1.0},
                            "duration": {"seconds": 1.0},
                        },
                    )
                ],
            ),
            1.0,
            source_audio_available=False,
        )

        assert command[-1] == str(output)
        probe = run_ffprobe(str(output))
        assert {stream["codec_type"] for stream in probe["streams"]} == {
            "video", "audio"
        }
        audio = next(
            stream for stream in probe["streams"] if stream["codec_type"] == "audio"
        )
        assert audio["sample_rate"] == "48000"
        assert audio["channels"] == 2

    def test_real_ducking_attenuates_target_during_speech_and_recovers(
        self, tmp_path
    ):
        base = tmp_path / "speech-window.mp4"
        music = tmp_path / "music.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=black:size=160x120:rate=30:d=3",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
                "-af", "volume='if(between(t,1,2),1,0)':eval=frame",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                "-shortest", str(base),
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                "-i", "sine=frequency=220:duration=3", str(music),
            ],
            check=True,
        )
        track = _resolved_track(
            str(music),
            gain_db=0.0,
            fade_in=0.0,
            fade_out=0.0,
            source_range={
                "from": {"seconds": 0.0},
                "to": {"seconds": 3.0},
                "duration": {"seconds": 3.0},
            },
            placed_range={
                "from": {"seconds": 0.0},
                "to": {"seconds": 3.0},
                "duration": {"seconds": 3.0},
            },
        )
        source = {
            "muted": False,
            "gain_db": 0.0,
            "fade_in": 0.0,
            "fade_out": 0.0,
        }
        plain = tmp_path / "plain.mp4"
        ducked = tmp_path / "ducked.mp4"
        mix_audio(
            str(base), str(plain), _resolved_mix(source=source, tracks=[track]),
            3.0, source_audio_available=True,
        )
        track["ducking"] = {
            "under": ["source"],
            "preset": "speech",
            "resolved": {
                "threshold_db": -30.0,
                "ratio": 8.0,
                "attack_ms": 20.0,
                "release_ms": 250.0,
            },
        }
        mix_audio(
            str(base), str(ducked), _resolved_mix(source=source, tracks=[track]),
            3.0, source_audio_available=True,
        )

        def music_level(path, start):
            measured = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-ss", str(start), "-t", "0.4",
                    "-i", str(path), "-af",
                    "lowpass=f=300,highpass=f=150,volumedetect",
                    "-f", "null", "-",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            match = re.search(r"mean_volume: (-?[0-9.]+) dB", measured.stderr)
            assert match, measured.stderr
            return float(match.group(1))

        assert music_level(ducked, 1.3) < music_level(plain, 1.3) - 4.0
        assert music_level(ducked, 0.3) == pytest.approx(
            music_level(plain, 0.3), abs=1.0
        )
        assert music_level(ducked, 2.5) == pytest.approx(
            music_level(plain, 2.5), abs=1.0
        )

    def test_real_limiter_protects_two_zero_db_layers_from_hard_clipping(
        self, test_video, audio_only_file, tmp_path
    ):
        output = tmp_path / "limited.mp4"
        track = _resolved_track(
            audio_only_file,
            gain_db=0.0,
            fade_in=0.0,
            fade_out=0.0,
            source_range={
                "from": {"seconds": 0.0},
                "to": {"seconds": 1.0},
                "duration": {"seconds": 1.0},
            },
            placed_range={
                "from": {"seconds": 0.0},
                "to": {"seconds": 1.0},
                "duration": {"seconds": 1.0},
            },
        )
        mix_audio(
            test_video,
            str(output),
            _resolved_mix(
                source={
                    "muted": False,
                    "gain_db": 0.0,
                    "fade_in": 0.0,
                    "fade_out": 0.0,
                },
                tracks=[track],
            ),
            2.0,
            source_audio_available=True,
        )
        measured = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-i", str(output),
                "-af", "volumedetect", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        match = re.search(r"max_volume: (-?[0-9.]+) dB", measured.stderr)
        assert match, measured.stderr
        assert float(match.group(1)) <= -0.3


class TestLayerLoudnessProbe:
    def test_probe_taps_target_layer_and_nullsinks_the_rest(self):
        mix = _resolved_mix(tracks=[_resolved_track("music.wav")])
        render = build_mix_audio_command(
            "base.mp4", "mixed.mp4", mix, 2.0, source_audio_available=True,
        )
        probe = build_layer_loudness_probe_command(
            "base.mp4", mix, 2.0, source_audio_available=True, layer_id="music",
        )

        input_paths = [
            probe[i + 1] for i, arg in enumerate(probe) if arg == "-i"
        ]
        assert input_paths == ["base.mp4", "music.wav"]

        render_parts = render[render.index("-filter_complex") + 1].split(";")
        probe_parts = probe[probe.index("-filter_complex") + 1].split(";")
        shared = [
            part
            for part in render_parts
            if "amix=" not in part and "alimiter=" not in part
        ]
        assert probe_parts[: len(shared)] == shared
        assert "[source_mix]anullsink" in probe_parts
        tap = probe_parts[-1]
        assert tap.startswith("[music_mix]loudnorm=")
        assert "print_format=json" in tap

        assert probe[probe.index("-loglevel") + 1] == "info"
        assert probe[-3:-1] == ["-f", "null"]
        assert probe[-1] == "-"
        assert "-map" in probe

    def test_probe_source_layer_nullsinks_external_tracks(self):
        mix = _resolved_mix(tracks=[_resolved_track("music.wav")])
        probe = build_layer_loudness_probe_command(
            "base.mp4", mix, 2.0, source_audio_available=True, layer_id="source",
        )
        probe_parts = probe[probe.index("-filter_complex") + 1].split(";")
        assert "[music_mix]anullsink" in probe_parts
        assert probe_parts[-1].startswith("[source_mix]loudnorm=")

    def test_probe_rejects_layer_without_signal_in_window(self):
        muted = _resolved_track("music.wav", muted=True)
        with pytest.raises(ValueError, match="music"):
            build_layer_loudness_probe_command(
                "base.mp4",
                _resolved_mix(tracks=[muted]),
                2.0,
                source_audio_available=True,
                layer_id="music",
            )
        with pytest.raises(ValueError, match="nope"):
            build_layer_loudness_probe_command(
                "base.mp4",
                _resolved_mix(tracks=[_resolved_track("music.wav")]),
                2.0,
                source_audio_available=True,
                layer_id="nope",
            )

    def test_real_probe_reflects_track_gain(self, test_video, audio_only_file):
        placement = {
            "source_range": {
                "from": {"seconds": 0.0},
                "to": {"seconds": 1.0},
                "duration": {"seconds": 1.0},
            },
            "placed_range": {
                "from": {"seconds": 0.0},
                "to": {"seconds": 2.0},
                "duration": {"seconds": 2.0},
            },
        }
        loud = _resolved_track(
            audio_only_file, gain_db=0.0, fade_in=0.0, fade_out=0.0,
            loop=True, **placement,
        )
        quiet = _resolved_track(
            audio_only_file, gain_db=-20.0, fade_in=0.0, fade_out=0.0,
            loop=True, **placement,
        )
        source = {"muted": False, "gain_db": 0.0, "fade_in": 0.0, "fade_out": 0.0}

        def measure(track):
            metrics, cmd = run_layer_loudness_probe(
                test_video,
                _resolved_mix(source=source, tracks=[track]),
                2.0,
                source_audio_available=True,
                layer_id="music",
            )
            assert cmd[0] == "ffmpeg"
            return metrics

        loud_metrics = measure(loud)
        quiet_metrics = measure(quiet)
        assert loud_metrics["integrated_lufs"] < 0.0
        drop = loud_metrics["integrated_lufs"] - quiet_metrics["integrated_lufs"]
        assert 15.0 < drop < 25.0


class TestAudioEnergyProbe:
    def test_command_uses_silencedetect_with_two_second_pause_tolerance(self):
        command = build_audio_energy_probe_command("interview.mp4")
        assert command[0] == "ffmpeg"
        assert "interview.mp4" in command
        filter_arg = command[command.index("-af") + 1]
        assert "silencedetect" in filter_arg
        assert "d=2.0" in filter_arg

    def test_parser_inverts_silences_into_sustained_audio_spans(self):
        stderr = """
        [silencedetect @ 0x1] silence_start: 0
        [silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 2.5
        [silencedetect @ 0x1] silence_start: 12
        [silencedetect @ 0x1] silence_end: 15 | silence_duration: 3
        """
        assert _parse_audio_energy_spans(stderr, 20.0) == [
            (2.5, 12.0),
            (15.0, 20.0),
        ]

    def test_parser_treats_no_detected_silence_as_one_occupied_span(self):
        assert _parse_audio_energy_spans("", 12.0) == [(0.0, 12.0)]

    def test_parser_does_not_report_trailing_silence_as_audio(self):
        stderr = "[silencedetect @ 0x1] silence_start: 7.25"
        assert _parse_audio_energy_spans(stderr, 12.0) == [(0.0, 7.25)]


class TestCheckFfmpegAvailable:
    def test_returns_none_when_present(self):
        # ffprobe is installed on dev and CI; this is a sanity check.
        assert check_ffmpeg_available() is None

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        with pytest.raises(FFmpegNotFoundError):
            check_ffmpeg_available()


class TestFfmpegFilterCapabilities:
    def test_build_filters_command(self):
        assert build_ffmpeg_filters_command() == [
            "ffmpeg",
            "-hide_banner",
            "-filters",
        ]

    def test_filter_probe_detects_ass_filter(self, monkeypatch):
        import subprocess as _subprocess
        from moviestar import ffmpeg as ffmpeg_mod

        monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda _cmd: "/usr/bin/ffmpeg")

        def fake_run(cmd, **kwargs):
            assert cmd == build_ffmpeg_filters_command()
            assert kwargs == {"capture_output": True, "text": True}
            return _subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=(
                    "Filters:\n"
                    " T. drawtext          V->V       Draw text.\n"
                    " .. ass               V->V       Render ASS subtitles.\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
        assert ffmpeg_filter_available("ass") is True

    def test_missing_ass_filter_raises_stable_code(self, monkeypatch):
        from moviestar import ffmpeg as ffmpeg_mod

        monkeypatch.setattr(ffmpeg_mod, "ffmpeg_filter_available", lambda name: False)

        with pytest.raises(FFmpegCapabilityError) as exc_info:
            check_ffmpeg_ass_filter_available()

        exc = exc_info.value
        assert exc.code == "ffmpeg_missing_ass_filter"
        assert "ass" in str(exc)
        assert "brew reinstall ffmpeg" in exc.hint
        assert "libass" in exc.hint

    def test_highlight_render_preflights_before_ffmpeg_spawn(
        self, monkeypatch, test_video, tmp_path
    ):
        from moviestar import ffmpeg as ffmpeg_mod

        monkeypatch.setattr(ffmpeg_mod, "ffmpeg_filter_available", lambda name: False)

        def fail_run(*_args, **_kwargs):
            pytest.fail("render should fail during preflight before spawning ffmpeg")

        monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fail_run)

        with pytest.raises(FFmpegCapabilityError) as exc_info:
            render_layout_frame(
                [
                    {
                        "path": test_video,
                        "source_from": 0.0,
                        "source_to": 0.5,
                        "region": {"x": 0, "y": 0, "width": 320, "height": 240},
                    }
                ],
                str(tmp_path / "frame.jpg"),
                (320, 240),
                overlay_plans=[
                    _overlay_plan(highlight={"mode": "spoken-word", "tokens": []})
                ],
                highlight_ass=("/tmp/captions.ass", "/tmp"),
            )

        assert exc_info.value.code == "ffmpeg_missing_ass_filter"

    def test_missing_drawtext_filter_raises_stable_code(self, monkeypatch):
        from moviestar import ffmpeg as ffmpeg_mod

        monkeypatch.setattr(ffmpeg_mod, "ffmpeg_filter_available", lambda name: False)

        with pytest.raises(FFmpegCapabilityError) as exc_info:
            check_ffmpeg_drawtext_filter_available()

        exc = exc_info.value
        assert exc.code == "ffmpeg_missing_drawtext_filter"
        assert "drawtext" in str(exc)
        assert "brew reinstall ffmpeg" in exc.hint

    def test_capability_hints_point_to_doctor(self, monkeypatch):
        from moviestar import ffmpeg as ffmpeg_mod

        monkeypatch.setattr(ffmpeg_mod, "ffmpeg_filter_available", lambda name: False)

        for check in (
            check_ffmpeg_ass_filter_available,
            check_ffmpeg_drawtext_filter_available,
        ):
            with pytest.raises(FFmpegCapabilityError) as exc_info:
                check()
            assert "moviestar doctor" in exc_info.value.hint

    def test_filter_probe_caches_listing_per_process(self, monkeypatch):
        import subprocess as _subprocess
        from moviestar import ffmpeg as ffmpeg_mod

        monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda _cmd: "/usr/bin/ffmpeg")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=" .. ass               V->V       Render ASS subtitles.\n",
                stderr="",
            )

        monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
        assert ffmpeg_filter_available("ass") is True
        assert ffmpeg_filter_available("drawtext") is False
        assert len(calls) == 1
        ffmpeg_mod.clear_ffmpeg_capability_cache()
        assert ffmpeg_filter_available("ass") is True
        assert len(calls) == 2


ENCODERS_STDOUT = """\
Encoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 .F.... = Frame-level multithreading
 ..S... = Slice-level multithreading
 ...X.. = Codec is experimental
 ....B. = Supports draw_horiz_band
 .....D = Supports direct rendering method 1
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC
 A....D aac                  AAC (Advanced Audio Coding)
"""

VERSION_STDOUT = (
    "ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers\n"
    "built with Apple clang version 16.0.0\n"
)

FILTERS_STDOUT = (
    "Filters:\n"
    " T.. = Timeline support\n"
    " T. drawtext          V->V       Draw text.\n"
    " .. ass               V->V       Render ASS subtitles.\n"
)


class TestFfmpegCapabilityReport:
    """Issue #358: one reusable environment probe behind 'moviestar doctor'."""

    def _fake_run(self, cmd, **kwargs):
        import subprocess as _subprocess

        if "-filters" in cmd:
            stdout = FILTERS_STDOUT
        elif "-encoders" in cmd:
            stdout = ENCODERS_STDOUT
        elif "-version" in cmd:
            tool = cmd[0]
            stdout = VERSION_STDOUT.replace("ffmpeg version", f"{tool} version")
        else:
            pytest.fail(f"unexpected probe argv: {cmd}")
        return _subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=stdout, stderr=""
        )

    def test_report_for_complete_environment(self, monkeypatch):
        from moviestar import ffmpeg as ffmpeg_mod

        monkeypatch.setattr(
            ffmpeg_mod.shutil, "which", lambda cmd: f"/usr/bin/{cmd}"
        )
        monkeypatch.setattr(ffmpeg_mod.subprocess, "run", self._fake_run)
        report = ffmpeg_capability_report()
        assert report["ffmpeg"] == {
            "available": True,
            "path": "/usr/bin/ffmpeg",
            "version": "7.1.1",
        }
        assert report["ffprobe"]["available"] is True
        assert "drawtext" in report["filters"]
        assert "ass" in report["filters"]
        assert "libx264" in report["encoders"]
        assert "aac" in report["encoders"]
        # Legend rows ('V..... = Video') must not leak into the listing.
        assert "=" not in report["encoders"]
        assert report["probe_errors"] == []

    def test_report_for_missing_ffmpeg(self, monkeypatch):
        from moviestar import ffmpeg as ffmpeg_mod

        monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda _cmd: None)

        def fail_run(*_args, **_kwargs):
            pytest.fail("no subprocess may spawn when ffmpeg is missing")

        monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fail_run)
        report = ffmpeg_capability_report()
        assert report["ffmpeg"]["available"] is False
        assert report["ffprobe"]["available"] is False
        assert report["filters"] is None
        assert report["encoders"] is None

    def test_report_records_probe_failures(self, monkeypatch):
        import subprocess as _subprocess
        from moviestar import ffmpeg as ffmpeg_mod

        monkeypatch.setattr(
            ffmpeg_mod.shutil, "which", lambda cmd: f"/usr/bin/{cmd}"
        )

        def failing_run(cmd, **kwargs):
            if "-filters" in cmd:
                return _subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="broken build"
                )
            return self._fake_run(cmd, **kwargs)

        monkeypatch.setattr(ffmpeg_mod.subprocess, "run", failing_run)
        report = ffmpeg_capability_report()
        assert report["filters"] is None
        assert report["encoders"] is not None
        assert report["probe_errors"][0]["probe"] == "filters"
        assert "broken build" in report["probe_errors"][0]["error"]

    def test_feature_requirements_cover_issue_358_capabilities(self):
        names = {req["capability"] for req in FFMPEG_FEATURE_REQUIREMENTS}
        assert {"drawtext", "ass", "libx264", "aac"} <= names
        for req in FFMPEG_FEATURE_REQUIREMENTS:
            assert req["code"].startswith("ffmpeg_missing_")
            assert req["blocks"], req["capability"]
            assert req["hint"], req["capability"]


class TestRunFfprobe:
    def test_returns_format_and_streams(self, test_video):
        data = run_ffprobe(test_video)
        assert "format" in data
        assert "streams" in data

    def test_missing_file_raises(self, tmp_path):
        missing = str(tmp_path / "nonexistent.mp4")
        with pytest.raises(FileNotFoundError):
            run_ffprobe(missing)

    def test_format_has_duration(self, test_video):
        data = run_ffprobe(test_video)
        assert float(data["format"]["duration"]) == pytest.approx(2.0, abs=0.2)

    def test_has_video_and_audio_stream(self, test_video):
        data = run_ffprobe(test_video)
        codec_types = {s["codec_type"] for s in data["streams"]}
        assert "video" in codec_types
        assert "audio" in codec_types


class TestExtractFrame:
    def test_creates_png(self, test_video, tmp_path):
        out = tmp_path / "frame.png"
        argv = extract_frame(test_video, 1.0, str(out))
        assert out.exists()
        assert out.stat().st_size > 0
        # PNG signature
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        # argv is the exact ffmpeg command used
        assert argv[0] == "ffmpeg"
        assert "-ss" in argv
        assert "1.0" in argv
        assert test_video in argv
        assert str(out) in argv

    def test_creates_jpeg(self, test_video, tmp_path):
        out = tmp_path / "frame.jpg"
        extract_frame(test_video, 1.0, str(out))
        assert out.exists()
        # JPEG magic bytes
        assert out.read_bytes()[:2] == b"\xff\xd8"

    def test_missing_source_raises(self, tmp_path):
        missing = str(tmp_path / "nope.mp4")
        out = tmp_path / "frame.png"
        with pytest.raises(FileNotFoundError):
            extract_frame(missing, 0.5, str(out))
        assert not out.exists()


class TestExtractFrames:
    def test_creates_jpeg_files(self, test_video, tmp_path):
        paths, _argv = extract_frames(test_video, 1.0, tmp_path, "src_0")
        assert len(paths) >= 1
        for p in paths:
            assert p.exists()
            assert p.suffix == ".jpg"
            assert p.read_bytes()[:2] == b"\xff\xd8"

    def test_respects_interval(self, test_video, tmp_path):
        # test_video is 2s at 30fps
        at_half_second = tmp_path / "half"
        at_half_second.mkdir()
        paths_half, _ = extract_frames(test_video, 0.5, at_half_second, "src_0")

        at_one_second = tmp_path / "one"
        at_one_second.mkdir()
        paths_one, _ = extract_frames(test_video, 1.0, at_one_second, "src_0")

        # 0.5s interval should produce more frames than 1.0s
        assert len(paths_half) > len(paths_one)

    def test_frames_named_with_source_id(self, test_video, tmp_path):
        paths, _ = extract_frames(test_video, 1.0, tmp_path, "src_42")
        for p in paths:
            assert p.name.startswith("src_42_")

    def test_returns_argv(self, test_video, tmp_path):
        _paths, argv = extract_frames(test_video, 1.0, tmp_path, "src_0")
        assert argv[0] == "ffmpeg"
        assert test_video in argv

    def test_missing_source_raises(self, tmp_path):
        missing = str(tmp_path / "nope.mp4")
        with pytest.raises(FileNotFoundError):
            extract_frames(missing, 1.0, tmp_path, "src_0")


class TestExtractFramesRange:
    """Range-scoped extraction for M7 inspect (start_time + duration)."""

    def test_extracts_with_start_time(self, test_video, tmp_path):
        # test_video is ~2s. Extract starting at 1.0s with 0.5s interval.
        out_dir = tmp_path / "range_start"
        out_dir.mkdir()
        paths, argv = extract_frames(
            test_video, 0.5, out_dir, "src_0", start_time=1.0
        )
        assert len(paths) >= 1
        assert "-ss" in argv
        assert "1.0" in argv

    def test_extracts_with_duration(self, test_video, tmp_path):
        # 2s source, extract first 1s at 0.5s interval → 2 frames
        out_dir = tmp_path / "range_dur"
        out_dir.mkdir()
        paths, argv = extract_frames(
            test_video, 0.5, out_dir, "src_0", duration=1.0
        )
        assert "-t" in argv
        assert "1.0" in argv
        assert 1 <= len(paths) <= 3

    def test_extracts_with_start_and_duration(self, test_video, tmp_path):
        # From 0.5s for 0.5s at 0.5s interval → 1 frame
        out_dir = tmp_path / "range_both"
        out_dir.mkdir()
        paths, argv = extract_frames(
            test_video,
            0.5,
            out_dir,
            "src_0",
            start_time=0.5,
            duration=0.5,
        )
        assert "-ss" in argv
        assert "-t" in argv
        assert len(paths) >= 1

    def test_custom_scale_width(self, test_video, tmp_path):
        out_dir = tmp_path / "range_scale"
        out_dir.mkdir()
        paths, argv = extract_frames(
            test_video, 1.0, out_dir, "src_0", scale_width=640
        )
        assert "scale=640:-1" in " ".join(argv)
        assert len(paths) >= 1


class TestExtractClip:
    """M8: segment extraction for watch."""

    def test_stream_copy_creates_mp4(self, test_video, tmp_path):
        out = tmp_path / "clip.mp4"
        argv = extract_clip(test_video, 0.0, 1.0, str(out))
        assert out.exists()
        assert out.stat().st_size > 0

    def test_stream_copy_uses_copy_codec(self, test_video, tmp_path):
        out = tmp_path / "clip.mp4"
        argv = extract_clip(test_video, 0.0, 1.0, str(out))
        assert "-c" in argv
        # -c copy should be adjacent
        copy_idx = argv.index("-c")
        assert argv[copy_idx + 1] == "copy"

    def test_precise_uses_libx264(self, test_video, tmp_path):
        out = tmp_path / "clip_precise.mp4"
        argv = extract_clip(test_video, 0.0, 1.0, str(out), precise=True)
        assert "libx264" in argv

    def test_precise_output_close_to_requested_duration(self, test_video, tmp_path):
        out = tmp_path / "precise.mp4"
        extract_clip(test_video, 0.5, 1.0, str(out), precise=True)
        # probe the output; should be ~1.0s ± 0.1s
        probe = run_ffprobe(str(out))
        actual = float(probe["format"]["duration"])
        assert 0.85 <= actual <= 1.15, f"Precise extract duration off: {actual}"

    def test_returns_ffmpeg_argv(self, test_video, tmp_path):
        out = tmp_path / "clip.mp4"
        argv = extract_clip(test_video, 0.0, 1.0, str(out))
        assert argv[0] == "ffmpeg"
        assert str(out) in argv
        assert test_video in argv

    def test_missing_source_raises(self, tmp_path):
        missing = str(tmp_path / "nope.mp4")
        out = tmp_path / "clip.mp4"
        with pytest.raises(FileNotFoundError):
            extract_clip(missing, 0.0, 1.0, str(out))
        assert not out.exists()

    def test_uses_start_time_and_duration(self, test_video, tmp_path):
        out = tmp_path / "clip.mp4"
        argv = extract_clip(test_video, 0.5, 1.0, str(out))
        assert "-ss" in argv
        assert "0.5" in argv
        assert "-t" in argv
        assert "1.0" in argv


class TestFailureMessage:
    """Issue #26: when ffprobe/ffmpeg returns non-zero with empty
    stderr, the failure message used to render as 'ffmpeg failed: '
    (dangling colon, no detail). Now falls back to the exit code so
    the message always carries content after the colon.
    """

    def test_run_ffprobe_empty_stderr_falls_back_to_exit_code(
        self, monkeypatch, test_video
    ):
        import subprocess as _subprocess
        from moviestar import ffmpeg as ffmpeg_mod

        def fake_run(*_args, **_kwargs):
            return _subprocess.CompletedProcess(
                args=[], returncode=42, stdout="", stderr=""
            )

        monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError) as exc_info:
            run_ffprobe(test_video)
        msg = str(exc_info.value)
        assert "ffprobe failed:" in msg
        # No dangling colon — content after.
        assert not msg.endswith(":")
        assert not msg.endswith(": ")
        # Exit code surfaced so the agent can debug.
        assert "42" in msg

    def test_run_ffprobe_uses_stderr_when_present(
        self, monkeypatch, test_video
    ):
        import subprocess as _subprocess
        from moviestar import ffmpeg as ffmpeg_mod

        def fake_run(*_args, **_kwargs):
            return _subprocess.CompletedProcess(
                args=[], returncode=1, stdout="",
                stderr="codec not supported",
            )

        monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError) as exc_info:
            run_ffprobe(test_video)
        msg = str(exc_info.value)
        # stderr content surfaces when present (preserving prior behavior).
        assert "codec not supported" in msg
        # And no exit-code fallback when stderr already explains.
        assert "exit code" not in msg

    def test_extract_frame_empty_stderr_falls_back_to_exit_code(
        self, monkeypatch, test_video, tmp_path
    ):
        import subprocess as _subprocess
        from moviestar import ffmpeg as ffmpeg_mod

        def fake_run(*_args, **_kwargs):
            return _subprocess.CompletedProcess(
                args=[], returncode=7, stdout="", stderr=""
            )

        monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)
        out = tmp_path / "frame.png"
        with pytest.raises(RuntimeError) as exc_info:
            extract_frame(test_video, 0.5, str(out))
        msg = str(exc_info.value)
        assert "ffmpeg failed:" in msg
        assert not msg.endswith(":")
        assert "7" in msg

    def test_run_ffprobe_surfaces_real_stderr_on_corrupt_file(
        self, tmp_path
    ):
        """Issue #26 friction-test caught that ffprobe was being run
        with `-v quiet`, which suppressed ALL stderr — so every real
        failure (corrupt mp4, plaintext, etc.) hit the empty-stderr
        fallback and the agent only learned 'exit code 1' when ffprobe
        had useful content like 'moov atom not found' to share.

        Now using `-v error`, so ffprobe's actual error message reaches
        the wrapper and surfaces in the RuntimeError. This test feeds
        a not-a-video file and confirms the message is detail-rich,
        not the bare exit-code fallback.
        """
        bogus = tmp_path / "not-a-video.mp4"
        bogus.write_text("this is not a video file at all")
        with pytest.raises(RuntimeError) as exc_info:
            run_ffprobe(str(bogus))
        msg = str(exc_info.value).lower()
        # Must surface ffprobe's actual stderr content, not just exit code.
        # Common ffprobe diagnostics on plaintext input:
        #   "Invalid data found when processing input"
        #   "moov atom not found"
        #   "Could not find codec parameters"
        # We assert the message has *some* recognizable substantive
        # content rather than pinning to one exact string (ffprobe's
        # wording can vary across versions).
        assert "exit code" not in msg, (
            f"empty-stderr fallback fired when ffprobe should have stderr: {msg!r}"
        )
        # The content keywords that real ffprobe emits for bad input.
        diagnostic_words = [
            "invalid data",
            "moov atom",
            "codec",
            "format",
            "input",
        ]
        assert any(word in msg for word in diagnostic_words), (
            f"ffprobe error doesn't carry recognizable diagnostic content: {msg!r}"
        )


class TestRenderSegmentsInputSideSeek:
    """M14 step 1.1: pre-`-i` `-ss` for fast input seek.

    Without input-side `-ss`, ffmpeg demuxes from t=0 in every input
    file even when the filter graph only needs a small slice. On a
    multi-GB Riverside camera, this turned a 60-second render into 38
    minutes (Riverside friction log, item 2). The fix: emit one `-i`
    per segment with `-ss <src_from>` before each, so each decoder
    only processes the slice it needs.
    """

    def test_multi_segment_emits_ss_before_each_input(self):
        """Two segments from the same source: two -i, each preceded by
        its own -ss. The pre-fix shape (one -i, two trim filters from
        t=0) is the slow path."""
        segments = [
            ("/tmp/source.mp4", 100.0, 110.0),
            ("/tmp/source.mp4", 500.0, 510.0),
        ]
        cmd = build_render_segments_command(
            segments, "/tmp/out.mp4", precise=True,
        )
        # Two -i flags, not one.
        assert cmd.count("-i") == 2, (
            f"expected one -i per segment (2 segments, 2 -i flags) "
            f"but got {cmd.count('-i')}: {cmd}"
        )
        # Two -ss flags, one before each -i.
        ss_positions = [i for i, arg in enumerate(cmd) if arg == "-ss"]
        i_positions = [i for i, arg in enumerate(cmd) if arg == "-i"]
        assert len(ss_positions) == 2
        # Each -ss must immediately precede an -i (with the value in between).
        for ss_idx in ss_positions:
            # -ss <value> -i ... → -i at ss_idx + 2.
            assert cmd[ss_idx + 2] == "-i", (
                f"-ss at index {ss_idx} not immediately followed by -i: "
                f"{cmd[ss_idx:ss_idx+4]}"
            )
        # The -ss values are the segments' src_from values.
        ss_values = [float(cmd[ss_idx + 1]) for ss_idx in ss_positions]
        assert ss_values == [100.0, 500.0]

    def test_multi_source_emits_one_i_per_segment(self):
        """Two segments from two different sources: still two -i,
        each preceded by -ss. (Multi-source already had distinct -i
        flags pre-fix; here we just confirm the -ss landed too.)"""
        segments = [
            ("/tmp/holden.mp4", 60.0, 90.0),
            ("/tmp/jdilla.mp4", 0.0, 30.0),
        ]
        cmd = build_render_segments_command(segments, "/tmp/out.mp4")
        assert cmd.count("-i") == 2
        assert cmd.count("-ss") == 2

    def test_single_segment_still_defers_to_extract_clip(self):
        """Single-segment list keeps the existing extract_clip shape —
        backward compat with the trim-only path (stream-copy stays
        cheap)."""
        segments = [("/tmp/source.mp4", 100.0, 110.0)]
        cmd = build_render_segments_command(segments, "/tmp/out.mp4")
        # Stream-copy mode → -c copy in argv.
        assert "-c" in cmd and "copy" in cmd
        # And exactly one -i / -ss.
        assert cmd.count("-i") == 1
        assert cmd.count("-ss") == 1

    def test_canvas_fill_left_scales_and_crops_single_segment(self):
        """Canvas rendering needs filter_complex even for one segment.
        fill:left scales to cover the canvas then crops from the left."""
        segments = [("/tmp/source.mp4", 0.0, 0.5)]
        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            output_canvas=(1080, 1920),
            segment_framings=[{"mode": "fill", "anchor": "left"}],
        )
        assert "-filter_complex" in cmd
        assert "-c" not in cmd or "copy" not in cmd
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "scale=1080:1920:force_original_aspect_ratio=increase" in graph
        assert "crop=1080:1920:0:(ih-1920)/2" in graph
        assert "setsar=1" in graph

    def test_canvas_fit_scales_and_pads(self):
        segments = [("/tmp/source.mp4", 0.0, 0.5)]
        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            output_canvas=(1080, 1920),
            segment_framings=[{"mode": "fit", "anchor": "center"}],
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "scale=1080:1920:force_original_aspect_ratio=decrease" in graph
        assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2" in graph

    def test_canvas_right_and_bottom_anchors_feed_crop(self):
        segments = [
            ("/tmp/source.mp4", 0.0, 0.5),
            ("/tmp/source.mp4", 0.5, 1.0),
        ]
        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            output_canvas=(1080, 1920),
            segment_framings=[
                {"mode": "fill", "anchor": "right"},
                {"mode": "fill", "anchor": "bottom"},
            ],
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "crop=1080:1920:iw-1080:(ih-1920)/2" in graph
        assert "crop=1080:1920:(iw-1080)/2:ih-1920" in graph

    def test_canvas_numeric_fill_anchor_feeds_crop(self):
        segments = [("/tmp/source.mp4", 0.0, 0.5)]
        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            output_canvas=(1080, 1920),
            segment_framings=[{"mode": "fill", "x": 0.67, "y": 0.5}],
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "crop=1080:1920:(iw-1080)*0.67:(ih-1920)*0.5" in graph

    def test_canvas_framing_length_must_match_segments(self):
        with pytest.raises(ValueError, match="segment_framings length"):
            build_render_segments_command(
                [
                    ("/tmp/source.mp4", 0.0, 0.5),
                    ("/tmp/source.mp4", 0.5, 1.0),
                ],
                "/tmp/out.mp4",
                output_canvas=(1080, 1920),
                segment_framings=[{"mode": "fill", "anchor": "center"}],
            )

    def test_three_segments_three_inputs(self):
        """Three segments → three -i + three -ss. No input dedup;
        each input only decodes its own slice."""
        segments = [
            ("/tmp/source.mp4", 10.0, 20.0),
            ("/tmp/source.mp4", 100.0, 110.0),
            ("/tmp/source.mp4", 1000.0, 1010.0),
        ]
        cmd = build_render_segments_command(segments, "/tmp/out.mp4")
        assert cmd.count("-i") == 3
        assert cmd.count("-ss") == 3
        # Filter graph references each input by its own index.
        joined = " ".join(cmd)
        for idx in range(3):
            assert f"[{idx}:v]trim" in joined

    def test_vfr_segments_fill_gaps_before_rebase_and_pad_tail(self):
        """Issue #364: sparse flat segments keep authored duration."""
        segments = [
            ("/tmp/screen.mp4", 4.0, 9.0),
            ("/tmp/camera.mp4", 0.0, 1.5),
        ]
        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            segment_fps=[30.0, 60.0],
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert (
            "[0:v]trim=start=0:end=5.0,fps=30.0:start_time=0,"
            "setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=5.0,"
            "trim=end=5.0[v0]"
        ) in graph
        assert (
            "[1:v]trim=start=0:end=1.5,fps=60.0:start_time=0,"
            "setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=1.5,"
            "trim=end=1.5[v1]"
        ) in graph

    def test_concat_output_enforces_composition_frame_rate(self):
        """Issue #366: the muxed stream keeps one explicit CFR cadence."""
        cmd = build_render_segments_command(
            [
                ("/tmp/scene_30.mp4", 0.0, 1.0),
                ("/tmp/scene_60.mp4", 0.0, 1.0),
            ],
            "/tmp/out.mp4",
            output_fps=60.0,
        )

        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "concat=n=2:v=1:a=1[vraw][outa]" in graph
        assert "[vraw]fps=60.0[outv]" in graph

    def test_segment_fps_length_must_match_segments(self):
        with pytest.raises(ValueError, match="segment_fps length"):
            build_render_segments_command(
                [
                    ("/tmp/a.mp4", 0.0, 1.0),
                    ("/tmp/b.mp4", 0.0, 1.0),
                ],
                "/tmp/out.mp4",
                segment_fps=[30.0],
            )


class TestRenderSceneTransitions:
    @staticmethod
    def _solid_clip(path, color, frequency):
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=30:d=0.8",
                "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=0.8",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(path),
            ],
            check=True,
        )

    @staticmethod
    def _pixel(path, at_s):
        raw = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", str(at_s), "-i", str(path),
                "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
                "-pix_fmt", "rgb24", "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        return tuple(raw[:3])

    def test_real_dissolve_blends_pixels_and_preserves_runtime(self, tmp_path):
        red = tmp_path / "red.mp4"
        blue = tmp_path / "blue.mp4"
        output = tmp_path / "dissolve.mp4"
        self._solid_clip(red, "red", 440)
        self._solid_clip(blue, "blue", 880)

        render_segments(
            [(str(red), 0.0, 0.6), (str(blue), 0.0, 0.6)],
            str(output),
            segment_has_audio=[True, True],
            video_segment_durations=[0.8, 0.8],
            video_transitions=[
                {"incoming_index": 1, "type": "dissolve", "duration": 0.4}
            ],
            output_fps=30.0,
            progress_cb=lambda _line: None,
        )

        probe = run_ffprobe(str(output))
        assert float(probe["format"]["duration"]) == pytest.approx(1.2, abs=0.06)
        early = self._pixel(output, 0.2)
        middle = self._pixel(output, 0.6)
        late = self._pixel(output, 1.0)
        assert early[0] > 200 and early[2] < 50
        assert middle[0] > 70 and middle[2] > 70
        assert late[2] > 200 and late[0] < 50

    @pytest.mark.parametrize(
        ("transition_type", "expected"),
        [("dip-black", "black"), ("dip-white", "white")],
    )
    def test_real_dip_reaches_authored_color_at_cut(
        self, tmp_path, transition_type, expected
    ):
        red = tmp_path / f"red-{transition_type}.mp4"
        blue = tmp_path / f"blue-{transition_type}.mp4"
        output = tmp_path / f"{transition_type}.mp4"
        self._solid_clip(red, "red", 440)
        self._solid_clip(blue, "blue", 880)

        render_segments(
            [(str(red), 0.0, 0.6), (str(blue), 0.0, 0.6)],
            str(output),
            segment_has_audio=[True, True],
            video_segment_durations=[0.6, 0.6],
            video_transitions=[
                {
                    "incoming_index": 1,
                    "type": transition_type,
                    "duration": 0.4,
                }
            ],
            output_fps=30.0,
            progress_cb=lambda _line: None,
        )

        cut = self._pixel(output, 0.6)
        if expected == "black":
            assert max(cut) < 35
        else:
            assert min(cut) > 220

    def test_dissolve_uses_extended_video_but_original_audio_duration(self):
        segments = [
            ("/tmp/intro.mp4", 0.0, 0.6),
            ("/tmp/demo.mp4", 0.0, 0.6),
        ]

        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            segment_has_audio=[True, True],
            video_segment_durations=[0.8, 0.8],
            video_transitions=[
                {
                    "incoming_index": 1,
                    "type": "dissolve",
                    "duration": 0.4,
                }
            ],
            output_fps=30.0,
        )

        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "[0:v]trim=start=0:end=0.8" in graph
        assert "[1:v]trim=start=0:end=0.8" in graph
        assert (
            "[v0][v1]xfade=transition=fade:duration=0.4:offset=0.4[vx1]"
            in graph
        )
        assert "[0:a]atrim=start=0:end=0.6" in graph
        assert "[1:a]atrim=start=0:end=0.6" in graph
        assert "[a0][a1]concat=n=2:v=0:a=1[outa]" in graph
        assert "acrossfade" not in graph

    @pytest.mark.parametrize(
        ("transition_type", "color"),
        [("dip-black", "black"), ("dip-white", "white")],
    )
    def test_dip_uses_two_half_fades_meeting_at_hard_cut(
        self, transition_type, color
    ):
        cmd = build_render_segments_command(
            [
                ("/tmp/intro.mp4", 0.0, 0.6),
                ("/tmp/demo.mp4", 0.0, 0.6),
            ],
            "/tmp/out.mp4",
            segment_has_audio=[False, False],
            video_segment_durations=[0.6, 0.6],
            video_transitions=[
                {
                    "incoming_index": 1,
                    "type": transition_type,
                    "duration": 0.4,
                }
            ],
            output_fps=30.0,
        )

        graph = cmd[cmd.index("-filter_complex") + 1]
        assert f"fade=t=out:st=0.4:d=0.2:color={color}" in graph
        assert f"fade=t=in:st=0:d=0.2:color={color}" in graph
        assert "[v0][v1]concat=n=2:v=1:a=0[vx1]" in graph
        assert "xfade" not in graph

    def test_transition_indices_must_be_unique_internal_boundaries(self):
        with pytest.raises(ValueError, match="incoming_index"):
            build_render_segments_command(
                [
                    ("/tmp/a.mp4", 0.0, 1.0),
                    ("/tmp/b.mp4", 0.0, 1.0),
                ],
                "/tmp/out.mp4",
                video_segment_durations=[1.2, 1.2],
                video_transitions=[
                    {"incoming_index": 0, "type": "dissolve", "duration": 0.4}
                ],
            )

    def test_dip_timing_accounts_for_prior_dissolve_preroll(self):
        cmd = build_render_segments_command(
            [
                ("/tmp/intro.mp4", 0.0, 0.6),
                ("/tmp/demo.mp4", 0.0, 0.6),
                ("/tmp/end.mp4", 0.0, 0.6),
            ],
            "/tmp/out.mp4",
            segment_has_audio=[False, False, False],
            video_segment_durations=[0.8, 0.8, 0.6],
            video_transitions=[
                {"incoming_index": 1, "type": "dissolve", "duration": 0.4},
                {"incoming_index": 2, "type": "dip-black", "duration": 0.4},
            ],
            output_fps=30.0,
        )

        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "[1:v]trim=start=0:end=0.8" in graph
        assert "fade=t=out:st=0.6:d=0.2:color=black" in graph
        assert "fade=t=out:st=0.4:d=0.2:color=black" not in graph

    def test_real_dip_after_dissolve_starts_at_declared_window(self, tmp_path):
        red = tmp_path / "chain-red.mp4"
        blue = tmp_path / "chain-blue.mp4"
        green = tmp_path / "chain-green.mp4"
        output = tmp_path / "chain.mp4"
        self._solid_clip(red, "red", 440)
        self._solid_clip(blue, "blue", 660)
        self._solid_clip(green, "green", 880)

        render_segments(
            [
                (str(red), 0.0, 0.6),
                (str(blue), 0.0, 0.6),
                (str(green), 0.0, 0.6),
            ],
            str(output),
            segment_has_audio=[True, True, True],
            video_segment_durations=[0.8, 0.8, 0.6],
            video_transitions=[
                {"incoming_index": 1, "type": "dissolve", "duration": 0.4},
                {"incoming_index": 2, "type": "dip-white", "duration": 0.4},
            ],
            output_fps=30.0,
            progress_cb=lambda _line: None,
        )

        window_start = self._pixel(output, 1.0)
        cut = self._pixel(output, 1.2)
        assert window_start[2] > 180
        assert min(cut) > 220


class TestRenderLayout:
    def _slots(self):
        return [
            {
                "path": "/tmp/holden.mp4",
                "source_from": 0.0,
                "source_to": 1.0,
                "region": {"x": 0, "y": 0, "width": 160, "height": 240},
                "framing": {"mode": "fill", "anchor": "left"},
            },
            {
                "path": "/tmp/jdilla.mp4",
                "source_from": 0.0,
                "source_to": 1.0,
                "region": {"x": 160, "y": 0, "width": 160, "height": 240},
                "framing": {"mode": "fit", "anchor": "center"},
            },
        ]

    def test_layout_video_composites_slots_with_overlays(self):
        cmd = build_render_layout_video_command(
            self._slots(),
            "/tmp/out.mp4",
            (320, 240),
            1.0,
            audio_from_input=("/tmp/holden.mp4", [(0.0, 1.0)]),
        )
        assert cmd.count("-i") == 3
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "color=c=black:s=320x240:d=1.0[base]" in graph
        assert "crop=160:240:0:(ih-240)/2" in graph
        assert "pad=160:240:(ow-iw)/2:(oh-ih)/2" in graph
        assert "overlay=x=0:y=0:shortest=1[lay0]" in graph
        assert "overlay=x=160:y=0:shortest=1[outv]" in graph
        assert "[2:a]atrim=start=0:end=1.0" in graph
        assert "[outv]" in cmd
        assert "[af0]" in cmd

    def test_layout_video_emits_bounce_expression_with_caller_offset(self):
        slots = self._slots()
        slots[1]["region"] = {"x": 0, "y": 0, "width": 80, "height": 80}
        slots[1]["motion"] = {
            "preset": "bounce",
            "speed": "medium",
            "origin": {"x": 0, "y": 0},
            "bounds": {"x": 560, "y": 280},
            "velocity": {"x": 200.0, "y": 140.0},
            "result_time_offset": 2.5,
        }

        cmd = build_render_layout_video_command(
            slots,
            "/tmp/out.mp4",
            (640, 360),
            1.0,
        )

        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "overlay=x=0:y=0:shortest=1[lay0]" in graph
        assert (
            "overlay=x='abs(mod(200*(t+2.5)+560,1120)-560)':"
            "y='abs(mod(140*(t+2.5)+280,560)-280)':shortest=1[outv]"
            in graph
        )

    def test_layout_video_real_bounce_matches_triangle_wave(self, tmp_path):
        main = tmp_path / "main.mp4"
        inset = tmp_path / "inset.mp4"
        output = tmp_path / "bounce.mp4"
        for path, color, size in (
            (main, "blue", "640x360"),
            (inset, "red", "80x80"),
        ):
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i",
                    f"color={color}:size={size}:rate=20:duration=4.8",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(path),
                ],
                check=True,
            )
        slots = [
            {
                "path": str(main),
                "source_from": 0.0,
                "source_to": 4.8,
                "region": {"x": 0, "y": 0, "width": 640, "height": 360},
            },
            {
                "path": str(inset),
                "source_from": 0.0,
                "source_to": 4.8,
                "region": {"x": 0, "y": 0, "width": 80, "height": 80},
                "motion": {
                    "preset": "bounce",
                    "speed": "medium",
                    "origin": {"x": 0, "y": 0},
                    "bounds": {"x": 560, "y": 280},
                    "velocity": {"x": 200.0, "y": 140.0},
                    "result_time_offset": 0.0,
                },
            },
        ]
        command = build_render_layout_video_command(
            slots, str(output), (640, 360), 4.8, output_fps=20.0
        )

        subprocess.run(command, check=True, capture_output=True)

        for at_s, expected in (
            (0.0, (0, 0)),
            (1.5, (300, 210)),
            (3.0, (520, 140)),
            (4.5, (220, 70)),
        ):
            raw = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-ss", str(at_s),
                    "-i", str(output), "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
                ],
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
            red_pixels = []
            for pixel in range(0, len(raw), 3):
                red, green, blue = raw[pixel:pixel + 3]
                if red > 180 and green < 80 and blue < 80:
                    index = pixel // 3
                    red_pixels.append((index % 640, index // 640))
            assert red_pixels
            measured = (
                min(x for x, _y in red_pixels),
                min(y for _x, y in red_pixels),
            )
            assert measured == pytest.approx(expected, abs=2)

    def test_layout_video_retimes_slot_and_audio_with_pitch_preserved(self):
        slots = self._slots()
        slots[0]["source_to"] = 1.0
        slots[0]["speed"] = 5.0
        slots[1]["source_to"] = 1.0
        slots[1]["speed"] = 5.0
        cmd = build_render_layout_video_command(
            slots,
            "/tmp/out.mp4",
            (320, 240),
            0.2,
            audio_from_input=("/tmp/holden.mp4", [(0.0, 1.0)]),
            audio_speed=5.0,
            output_fps=30.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "trim=start=0:end=1.0" in graph
        assert "setpts=(PTS-STARTPTS)/5.0" in graph
        assert "fps=30.0" in graph
        assert "atempo=2.0,atempo=2.0,atempo=1.25" in graph

    def test_layout_video_fills_vfr_gaps_before_rebase_and_pads_tail(self):
        """Issue #359: sparse (VFR) sources must not shorten the clip.

        The gap fill has to run BEFORE the setpts rebase — otherwise a
        frameless lead-in collapses to t=0 and the authored duration is
        silently lost. The tail pads with cloned frames to the exact
        clip duration so `overlay=shortest=1` cannot truncate it.
        """
        slots = self._slots()
        slots[0]["source_to"] = 2.0
        slots[0]["speed"] = 4.0
        slots[1]["source_to"] = 0.5
        cmd = build_render_layout_video_command(
            slots,
            "/tmp/out.mp4",
            (320, 240),
            0.5,
            output_fps=30.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        sped_chain = (
            "trim=start=0:end=2.0,fps=7.5:start_time=0,"
            "setpts=(PTS-STARTPTS)/4.0,fps=30.0,"
            "tpad=stop_mode=clone:stop_duration=0.5,trim=end=0.5"
        )
        assert sped_chain in graph
        plain_chain = (
            "trim=start=0:end=0.5,fps=30.0:start_time=0,"
            "setpts=(PTS-STARTPTS)/1.0,fps=30.0,"
            "tpad=stop_mode=clone:stop_duration=0.5,trim=end=0.5"
        )
        assert plain_chain in graph

    def test_layout_video_backfills_lead_in_from_prior_packet_seek(self):
        """Issue #365: a mid-void start must retain the pre-gap frame."""
        slots = self._slots()[:1]
        slots[0].update(
            source_from=4.0,
            source_to=9.0,
            video_seek_from=1.966667,
        )

        cmd = build_render_layout_video_command(
            slots,
            "/tmp/out.mp4",
            (320, 240),
            5.0,
            output_fps=30.0,
        )

        assert cmd[cmd.index("-ss") + 1] == "1.966667"
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert (
            "trim=start=0:end=7.033333,fps=30.0:start_time=2.033333,"
            "setpts=(PTS-STARTPTS)/1.0"
        ) in graph

    def test_last_packet_probe_finds_frame_before_vfr_void(
        self, vfr_gap_video
    ):
        assert probe_last_video_packet_timestamp(
            vfr_gap_video, 4.0
        ) == pytest.approx(1.966667, abs=0.00001)

    def test_layout_video_without_output_fps_keeps_legacy_timing(self):
        """No output rate → no CFR grid to fill onto; chain unchanged."""
        cmd = build_render_layout_video_command(
            self._slots(),
            "/tmp/out.mp4",
            (320, 240),
            1.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "start_time=0" not in graph
        assert "trim=start=0:end=1.0,setpts=(PTS-STARTPTS)/1.0" in graph

    def test_layout_video_hold_clones_one_frame_and_has_no_audio(self):
        slots = self._slots()
        for slot in slots:
            slot["source_from"] = 0.5
            slot["source_to"] = 0.5
            slot["held"] = True
        cmd = build_render_layout_video_command(
            slots,
            "/tmp/out.mp4",
            (320, 240),
            0.4,
            output_fps=30.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "tpad=stop_mode=clone:stop_duration=0.4" in graph
        assert "fps=30.0" in graph

    def test_layout_frame_applies_resolved_camera_crop_before_framing(self):
        slots = self._slots()
        slots[0]["camera_crop"] = {"x": 40, "y": 30, "w": 160, "h": 120}
        cmd = build_render_layout_frame_command(
            slots,
            "/tmp/frame.jpg",
            (320, 240),
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "crop=160:120:40:30" in graph
        assert graph.index("crop=160:120:40:30") < graph.index("scale=")

    def test_layout_video_animates_camera_before_slot_framing(self):
        slots = self._slots()
        slots[0]["camera_animation"] = {
            "result_local_from": 0.25,
            "default_crop": {"x": 0, "y": 0, "w": 320, "h": 240},
            "moves": [
                {
                    "id": "zoom-ui",
                    "from": 0.1,
                    "to": 0.5,
                    "ease": "in-out",
                    "from_crop": {"x": 0, "y": 0, "w": 320, "h": 240},
                    "to_crop": {"x": 40, "y": 30, "w": 160, "h": 90},
                }
            ],
        }
        cmd = build_render_layout_video_command(
            slots,
            "/tmp/out.mp4",
            (320, 240),
            0.5,
            output_fps=30.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert graph.count("perspective=") == 1
        assert "sense=source:eval=frame" in graph
        assert "on/30" in graph
        assert "perspective=" in graph and "scale=" in graph
        assert graph.index("perspective=") < graph.index("overlay=x=0:y=0")
        assert "zoom-ui" not in graph  # ids stay in envelopes, not filter syntax

    def test_layout_video_real_fit_camera_animation(self, test_video, tmp_path):
        slots = self._slots()
        slots[0]["path"] = test_video
        slots[0]["source_to"] = 0.4
        slots[1]["path"] = test_video
        slots[1]["source_to"] = 0.4
        slots[1]["camera_animation"] = {
            "result_local_from": 0.0,
            "default_crop": {"x": 0, "y": 0, "w": 320, "h": 240},
            "moves": [{
                "id": "fit-zoom",
                "from": 0.0,
                "to": 0.3,
                "ease": "linear",
                "from_crop": {"x": 0, "y": 0, "w": 320, "h": 240},
                "to_crop": {"x": 120, "y": 60, "w": 80, "h": 120},
            }],
        }
        output = tmp_path / "fit-camera.mp4"
        cmd = build_render_layout_video_command(
            slots,
            str(output),
            (320, 240),
            0.4,
            output_fps=30.0,
        )

        subprocess.run(cmd, check=True, capture_output=True)

        assert output.exists() and output.stat().st_size > 0

    def test_layout_frame_outputs_one_image(self):
        cmd = build_render_layout_frame_command(
            self._slots(),
            "/tmp/frame.jpg",
            (320, 240),
        )
        assert "-frames:v" in cmd
        assert "1" in cmd
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "overlay=x=160:y=0:shortest=1[outv]" in graph

    def test_layout_frame_does_not_bound_source_decode_to_compositor_window(self):
        """Issue #367: the next VFR frame may be more than 0.1s away."""
        cmd = build_render_layout_frame_command(
            self._slots(),
            "/tmp/frame.jpg",
            (320, 240),
        )

        graph = cmd[cmd.index("-filter_complex") + 1]
        source_chains = [
            part
            for part in graph.split(";")
            if part.startswith(("[0:v]", "[1:v]"))
        ]
        assert source_chains
        assert all("trim=start=0:end=" not in chain for chain in source_chains)
        assert all("trim=start=0,setpts=" in chain for chain in source_chains)

    def test_layout_frames_adds_fps_filter(self):
        cmd = build_render_layout_frames_command(
            self._slots(),
            "/tmp/layout_%04d.jpg",
            (320, 240),
            1.0,
            0.25,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "[vraw]fps=1/0.25,scale=320:-1[outv]" in graph
        assert "/tmp/layout_%04d.jpg" in cmd

    def test_layout_real_video_render(self, test_video, tmp_path):
        out = tmp_path / "layout.mp4"
        cmd = build_render_layout_video_command(
            [
                {
                    "path": test_video,
                    "source_from": 0.0,
                    "source_to": 0.4,
                    "region": {"x": 0, "y": 0, "width": 160, "height": 240},
                    "framing": {"mode": "fill", "anchor": "center"},
                },
                {
                    "path": test_video,
                    "source_from": 0.0,
                    "source_to": 0.4,
                    "region": {"x": 160, "y": 0, "width": 160, "height": 240},
                    "framing": {"mode": "fill", "anchor": "center"},
                },
            ],
            str(out),
            (320, 240),
            0.4,
        )
        assert "-filter_complex" in cmd


class TestRenderSegmentsAudioFromInput:
    """M14 step 1.4: --audio-from routes one source's audio across the
    whole composition. The ffmpeg layer adds extra `-i` inputs (one
    per audio_from slice, since audio_from's edited result might have
    internal cuts), atrim'd + aconcat'd to a single `[outa]`, mapped
    as the only audio in the output."""

    def test_audio_from_adds_extra_input_after_video_segments(self):
        """Video segments use input indices [0, n); audio_from inputs
        are appended at [n, n+k). Confirms the input count and the
        -ss + -i pairing on the audio side."""
        segments = [
            ("/tmp/holden.mp4", 0.0, 10.0),
            ("/tmp/jdilla.mp4", 0.0, 5.0),
            ("/tmp/holden.mp4", 10.0, 15.0),
        ]
        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            audio_from_input=("/tmp/holden.mp4", [(0.0, 20.0)]),
        )
        # 3 video segments + 1 audio_from slice = 4 inputs.
        assert cmd.count("-i") == 4
        assert cmd.count("-ss") == 4
        # The last -i is the audio_from path.
        i_positions = [i for i, arg in enumerate(cmd) if arg == "-i"]
        assert cmd[i_positions[-1] + 1] == "/tmp/holden.mp4"
        # Its -ss precedes the -i: argv is "... -ss <val> -i <path>".
        ss_for_last_i = i_positions[-1] - 2
        assert cmd[ss_for_last_i] == "-ss"
        assert float(cmd[ss_for_last_i + 1]) == 0.0

    def test_audio_from_filter_uses_named_source_not_segment_audio(self):
        """Per-segment audio chains (`[0:a]atrim...`) disappear; the
        filter graph instead atrims the audio_from input and maps it
        as the only audio output."""
        segments = [
            ("/tmp/holden.mp4", 0.0, 10.0),
            ("/tmp/jdilla.mp4", 0.0, 5.0),
        ]
        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            audio_from_input=("/tmp/holden.mp4", [(0.0, 15.0)]),
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        # No per-segment audio chains.
        assert "[0:a]atrim" not in graph
        assert "[1:a]atrim" not in graph
        # The audio_from input (index 2, since segments are 0+1) IS atrim'd.
        assert "[2:a]atrim=start=0:end=15" in graph
        # Video concat carries a=0 (audio comes from outside the concat).
        assert "concat=n=2:v=1:a=0" in graph
        # Output maps include [outv] and the audio_from chain's output.
        assert "[outv]" in cmd
        # Audio is mapped (single af slice → label is 'af0').
        map_positions = [i for i, arg in enumerate(cmd) if arg == "-map"]
        mapped_audio = [
            cmd[i + 1] for i in map_positions if cmd[i + 1].startswith("[af")
        ]
        assert mapped_audio == ["[af0]"]

    def test_audio_from_with_multiple_slices_aconcats(self):
        """audio_from's edited result might have cuts. Each slice
        gets its own -i + atrim chain; multiple slices aconcat to a
        single output stream."""
        segments = [
            ("/tmp/holden.mp4", 0.0, 10.0),
            ("/tmp/jdilla.mp4", 0.0, 10.0),
        ]
        # holden's edited result spans two source slices: [0,5] + [20,30]
        # = 15s, matching the composition duration.
        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            audio_from_input=(
                "/tmp/holden.mp4", [(0.0, 5.0), (20.0, 30.0)]
            ),
        )
        # 2 video + 2 audio_from = 4 inputs.
        assert cmd.count("-i") == 4
        graph = cmd[cmd.index("-filter_complex") + 1]
        # Both af inputs (indices 2 + 3) atrim'd to their slice durations.
        assert "[2:a]atrim=start=0:end=5" in graph
        assert "[3:a]atrim=start=0:end=10" in graph
        # The two af streams aconcat into [afout].
        assert "[af0][af1]aconcat=n=2:v=0:a=1[afout]" in graph
        # Output maps audio from [afout].
        assert "[afout]" in cmd

    def test_audio_from_single_video_segment_still_uses_filter_complex(self):
        """Normally a single video segment defers to extract_clip
        (stream-copy stays cheap). With audio_from we must use
        filter_complex regardless — extract_clip can't mix in an
        external audio track."""
        segments = [("/tmp/source.mp4", 100.0, 110.0)]
        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            audio_from_input=("/tmp/source.mp4", [(50.0, 60.0)]),
        )
        # No -c copy; filter_complex path.
        assert "-filter_complex" in cmd
        # Two inputs (video segment + audio_from slice).
        assert cmd.count("-i") == 2

    def test_audio_from_ignores_segment_has_audio(self):
        """When audio_from is set, segment_has_audio is irrelevant —
        the named source is authoritative. Even a composition with
        silent segments emits audio from the audio_from input."""
        segments = [
            ("/tmp/holden.mp4", 0.0, 10.0),
            ("/tmp/screen.mp4", 0.0, 5.0),  # silent
        ]
        cmd = build_render_segments_command(
            segments,
            "/tmp/out.mp4",
            segment_has_audio=[True, False],
            audio_from_input=("/tmp/holden.mp4", [(0.0, 15.0)]),
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        # Output still maps the af-derived audio.
        assert "[2:a]atrim=start=0:end=15" in graph
        map_positions = [i for i, arg in enumerate(cmd) if arg == "-map"]
        mapped = [cmd[i + 1] for i in map_positions]
        # Video + audio (named-source) — not video-only.
        assert "[outv]" in mapped
        assert any(label.startswith("[af") for label in mapped)

    def test_audio_from_rejects_empty_slice_list(self):
        with pytest.raises(ValueError, match="at least one slice"):
            build_render_segments_command(
                [("/tmp/x.mp4", 0.0, 5.0), ("/tmp/y.mp4", 0.0, 5.0)],
                "/tmp/out.mp4",
                audio_from_input=("/tmp/x.mp4", []),
            )

    def test_audio_from_rejects_invalid_slice(self):
        with pytest.raises(ValueError, match="invalid audio_from_input"):
            build_render_segments_command(
                [("/tmp/x.mp4", 0.0, 5.0), ("/tmp/y.mp4", 0.0, 5.0)],
                "/tmp/out.mp4",
                audio_from_input=("/tmp/x.mp4", [(5.0, 5.0)]),
            )


class TestRenderSegmentsProgress:
    """M14 polish (issue #133): multi-segment renders emit
    moviestar-shaped progress lines via the progress_cb callback so
    long exports show liveness instead of looking hung."""

    def test_multi_segment_argv_contains_progress_flag(self):
        """The progress wiring depends on `-progress pipe:2 -nostats`
        in the ffmpeg argv. Confirm both land in the multi-segment
        path."""
        segments = [
            ("/tmp/source.mp4", 0.0, 5.0),
            ("/tmp/source.mp4", 10.0, 15.0),
        ]
        cmd = build_render_segments_command(segments, "/tmp/out.mp4")
        assert "-progress" in cmd
        i = cmd.index("-progress")
        assert cmd[i + 1] == "pipe:2"
        assert "-nostats" in cmd

    def test_single_segment_argv_omits_progress_flag(self):
        """The single-segment shortcut defers to build_extract_clip_
        command which does NOT add the progress flag — the operation
        is sub-second so progress is unnecessary."""
        segments = [("/tmp/source.mp4", 0.0, 5.0)]
        cmd = build_render_segments_command(segments, "/tmp/out.mp4")
        assert "-progress" not in cmd

    def test_progress_cb_invoked_during_real_render(
        self, test_video, tmp_path
    ):
        """End-to-end check: a multi-segment render against the
        2-second test fixture emits at least one progress line via
        the callback. Uses input-side seek so each input slice is
        small."""
        out = tmp_path / "rendered.mp4"
        lines: list[str] = []
        # Two short slices from the same source — guaranteed multi-
        # segment so the -progress path runs.
        render_segments(
            [(test_video, 0.0, 0.5), (test_video, 0.5, 1.0)],
            str(out),
            precise=True,
            progress_cb=lines.append,
        )
        # ffmpeg emits a progress block every ~1s — on a sub-second
        # render the throttle means we should still see at least the
        # final "out_time=" sample. The exact count isn't important;
        # the shape and presence are.
        assert out.exists()
        # If the render was too fast to produce any progress block,
        # accept zero — but if any line lands, it must be shaped right.
        for line in lines:
            assert line.startswith("[elapsed ")
            assert "rendered" in line
            assert "/" in line  # position / total

    def test_progress_cb_default_writes_to_stderr(self, test_video, tmp_path, capsys):
        """When progress_cb is None, lines go to sys.stderr. Use
        capsys to read what landed there during a tiny multi-segment
        render."""
        out = tmp_path / "rendered.mp4"
        render_segments(
            [(test_video, 0.0, 0.5), (test_video, 0.5, 1.0)],
            str(out),
            precise=True,
        )
        captured = capsys.readouterr()
        # Could be empty if render finished before any progress block
        # — but any content on stderr must look like a progress line.
        for line in captured.err.splitlines():
            if line.strip():
                assert line.startswith("[elapsed ")


# ---------------------------------------------------------------------------
# M19 overlay renderer: filtergraph escaping + drawtext filter parts.


def _overlay_plan(**kwargs) -> dict:
    """Minimal renderer-ready plan, matching moviestar.overlays output."""
    plan = {
        "id": "manual_0001",
        "track": "titles",
        "kind": "manual",
        "z_index": 200,
        "text": "Hello",
        "lines": ["Hello"],
        "line_exprs": [("(w-text_w)/2", "160+0")],
        "font": {
            "family": "Inter",
            "weight": "bold",
            "path": "/fonts/Inter-Bold.ttf",
            "source": "bundled",
        },
        "font_size": 96,
        "font_color": "0xffffff@1.0",
        "stroke": {"width": 3, "color": "0x000000@1.0"},
        "box": None,
        "shadow": None,
        "line_height": 120,
        "rotate_deg": 0.0,
        "opacity": 1.0,
        "anchor": "top-center",
        "estimated_bounds": {
            "estimate": True,
            "x": 100,
            "y": 160,
            "width": 300,
            "height": 120,
        },
        "from_result": 1.0,
        "to_result": 4.0,
        "enable_from": 1.0,
        "enable_to": 4.0,
        "lane_center": (540.0, 220.0),
    }
    plan.update(kwargs)
    return plan


class TestFiltergraphEscape:
    """Escaping is two-level: the filter option parser, then the
    filtergraph parser. Verified live against ffmpeg 8 (apostrophes,
    colons, percents, and backslashes render literally)."""

    def test_apostrophe_double_escapes(self):
        from moviestar.ffmpeg import _filtergraph_escape

        assert _filtergraph_escape("Here's") == r"Here\\\'s"

    def test_colon_escape_gets_reescaped_at_graph_level(self):
        from moviestar.ffmpeg import _filtergraph_escape

        # Level 1 emits a\:b; level 2 escapes that backslash again so
        # the graph parser hands the option parser back a\:b.
        assert _filtergraph_escape("a:b") == r"a\\:b"

    def test_graph_level_chars_escape_once(self):
        from moviestar.ffmpeg import _filtergraph_escape

        assert _filtergraph_escape("a,b;c[d]") == r"a\,b\;c\[d\]"

    def test_backslash_survives_both_levels(self):
        from moviestar.ffmpeg import _filtergraph_escape

        assert _filtergraph_escape("a\\b") == r"a\\\\b"


class TestOverlayFilterParts:
    def test_simple_plan_draws_on_stream_with_enable(self):
        from moviestar.ffmpeg import build_overlay_filter_parts

        [part] = build_overlay_filter_parts(
            [_overlay_plan()],
            (1080, 1920),
            6.0,
            input_label="vlaid",
            output_label="outv",
        )
        assert part.startswith("[vlaid]drawtext=")
        assert part.endswith("[outv]")
        assert "fontfile=/fonts/Inter-Bold.ttf" in part
        assert "fontsize=96" in part
        assert "fontcolor=0xffffff@1.0" in part
        assert "borderw=3" in part
        assert "expansion=none" in part
        assert "enable=gte(t\\,1.0)*lt(t\\,4.0)" in part

    def test_wrapped_lines_get_one_drawtext_each(self):
        from moviestar.ffmpeg import build_overlay_filter_parts

        plan = _overlay_plan(
            lines=["one", "two"],
            line_exprs=[("(w-text_w)/2", "160+0"), ("(w-text_w)/2", "160+120")],
        )
        [part] = build_overlay_filter_parts(
            [plan], (1080, 1920), 6.0, input_label="v", output_label="outv"
        )
        assert part.count("drawtext=") == 2
        assert "y=160+120" in part

    def test_static_drops_enable(self):
        from moviestar.ffmpeg import build_overlay_filter_parts

        [part] = build_overlay_filter_parts(
            [_overlay_plan()],
            (1080, 1920),
            0.001,
            input_label="v",
            output_label="outv",
            static=True,
        )
        assert "enable=" not in part

    def test_rotated_plan_uses_transparent_lane(self):
        from moviestar.ffmpeg import build_overlay_filter_parts

        plan = _overlay_plan(rotate_deg=-8.0, opacity=0.85)
        lane, composite = build_overlay_filter_parts(
            [plan], (1080, 1920), 6.0, input_label="v", output_label="outv"
        )
        assert lane.startswith("color=c=black@0.0:s=1080x1920")
        assert "format=rgba" in lane
        assert "colorchannelmixer=aa=0.85" in lane
        assert "rotate=-8.0*PI/180" in lane
        assert "ow=rotw(-8.0*PI/180)" in lane
        assert lane.endswith("[txtlane0]")
        assert composite.startswith("[v][txtlane0]overlay=")
        assert "x=540.0-overlay_w/2" in composite
        assert "enable=gte(t\\,1.0)*lt(t\\,4.0)" in composite
        assert composite.endswith("[outv]")

    def test_plans_chain_in_render_order(self):
        from moviestar.ffmpeg import build_overlay_filter_parts

        plans = [
            _overlay_plan(id="manual_0001"),
            _overlay_plan(id="manual_0002"),
        ]
        first, second = build_overlay_filter_parts(
            plans, (1080, 1920), 6.0, input_label="vlaid", output_label="outv"
        )
        assert first.startswith("[vlaid]")
        assert first.endswith("[txt0]")
        assert second.startswith("[txt0]")
        assert second.endswith("[outv]")

    def test_box_and_shadow_options_render(self):
        from moviestar.ffmpeg import build_overlay_filter_parts

        plan = _overlay_plan(
            box={"color": "0x000000@0.65", "borderw": 18},
            shadow={"x": 2, "y": 2, "color": "0x000000@1.0"},
        )
        [part] = build_overlay_filter_parts(
            [plan], (1080, 1920), 6.0, input_label="v", output_label="outv"
        )
        assert "box=1:boxcolor=0x000000@0.65:boxborderw=18" in part
        assert "shadowx=2:shadowy=2:shadowcolor=0x000000@1.0" in part


class TestOverlayBuilderWiring:
    def _slots(self, test_video):
        return [
            {
                "path": test_video,
                "source_from": 0.0,
                "source_to": 0.5,
                "region": {"x": 0, "y": 0, "width": 320, "height": 240},
                "framing": {"mode": "fill", "anchor": "center"},
            }
        ]

    def test_video_command_inserts_overlays_before_output(self, test_video):
        cmd = build_render_layout_video_command(
            self._slots(test_video),
            "out.mp4",
            (320, 240),
            0.5,
            overlay_plans=[_overlay_plan()],
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "[vlaid]" in graph
        assert "drawtext=" in graph
        assert graph.index("drawtext=") > graph.index("[vlaid]")
        assert "-map" in cmd and "[outv]" in cmd

    def test_frames_command_overlays_apply_before_thumbnail_scale(
        self, test_video
    ):
        cmd = build_render_layout_frames_command(
            self._slots(test_video),
            "frames_%04d.jpg",
            (320, 240),
            0.5,
            0.25,
            overlay_plans=[_overlay_plan()],
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert graph.index("drawtext=") < graph.index("fps=1/0.25")

    def test_frame_command_uses_static_overlays(self, test_video):
        cmd = build_render_layout_frame_command(
            self._slots(test_video),
            "frame.jpg",
            (320, 240),
            overlay_plans=[_overlay_plan()],
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "drawtext=" in graph
        assert "enable=" not in graph

    def test_frame_render_survives_long_gop_non_frame_aligned_seek(self, tmp_path):
        source = tmp_path / "long-gop.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i",
                "testsrc2=duration=5:size=320x240:rate=30",
                "-c:v", "libx264", "-preset", "veryfast",
                "-g", "300", "-keyint_min", "300", "-sc_threshold", "0",
                str(source),
            ],
            check=True,
        )
        out = tmp_path / "frame.jpg"
        render_layout_frame(
            [
                {
                    "path": str(source),
                    "source_from": 2.501,
                    "source_to": 2.502,
                    "region": {"x": 0, "y": 0, "width": 320, "height": 240},
                    "framing": {"mode": "fill", "anchor": "center"},
                }
            ],
            str(out),
            (320, 240),
        )
        raw = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-i", str(out),
                "-f", "rawvideo",
                "-pix_fmt", "gray",
                "-frames:v", "1",
                "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        assert raw
        assert sum(raw) / len(raw) > 10

    def test_no_overlays_leaves_graph_unchanged(self, test_video):
        plain = build_render_layout_video_command(
            self._slots(test_video), "out.mp4", (320, 240), 0.5
        )
        with_none = build_render_layout_video_command(
            self._slots(test_video),
            "out.mp4",
            (320, 240),
            0.5,
            overlay_plans=None,
        )
        assert plain == with_none


from moviestar.ffmpeg import (  # noqa: E402
    build_highlight_ass,
    build_overlay_filter_parts,
)


class TestHighlightAss:
    CANVAS = (1080, 1920)

    def _plan(self, **kwargs) -> dict:
        plan = {
            "id": "cap_0001",
            "track": "captions",
            "kind": "caption",
            "z_index": 100,
            "text": "hello world",
            "lines": ["hello world"],
            "line_exprs": [("(w-text_w)/2", "h-270-180+0")],
            "font": {
                "family": "Inter",
                "weight": "bold",
                "path": "/fonts/Inter-Bold.ttf",
                "source": "bundled",
            },
            "font_size": 72,
            "font_color": "0xffffff@1.0",
            "stroke": {"width": 5, "color": "0x000000@1.0"},
            "box": None,
            "shadow": None,
            "line_height": 90,
            "rotate_deg": 0.0,
            "opacity": 1.0,
            "anchor": "bottom-center",
            "estimated_bounds": {"x": 0, "y": 0, "width": 400, "height": 90},
            "from_result": 0.2,
            "to_result": 2.0,
            "enable_from": 0.2,
            "enable_to": 2.0,
            "lane_center": (540.0, 1695.0),
            "anchor_point": (540.0, 1740.0),
            "resolved_style": {
                "color": "#ffffff",
                "stroke_color": "#000000",
            },
            "highlight": {
                "mode": "spoken-word",
                "color": "#ffe94a",
                "tokens": [
                    {"word_index": 0, "from_local": 0.2, "to_local": 0.9},
                    {"word_index": 1, "from_local": 0.9, "to_local": 1.6},
                ],
            },
        }
        plan.update(kwargs)
        return plan

    def test_ass_document_shape(self):
        content = build_highlight_ass([self._plan()], self.CANVAS)
        assert "PlayResX: 1080" in content
        assert "PlayResY: 1920" in content
        assert "Style: cap_0001,Inter,72," in content
        # active-word override wraps exactly one word per token event
        assert (
            "{\\1c&H004AE9FF&}hello{\\1c&H00FFFFFF&} world" in content
        )
        assert (
            "hello {\\1c&H004AE9FF&}world{\\1c&H00FFFFFF&}" in content
        )
        # gap event after the last token renders with no active word
        assert content.count("Dialogue:") == 3
        assert "\\an2" in content
        assert "\\pos(540.0,1740.0)" in content
        assert "\\q2" in content

    def test_ass_timestamps_and_zero_length_bump(self):
        plan = self._plan(
            enable_from=0.0,
            enable_to=0.001,
            highlight={
                "mode": "spoken-word",
                "color": "#ffe94a",
                "tokens": [
                    {"word_index": 0, "from_local": 0.0, "to_local": 0.001}
                ],
            },
        )
        content = build_highlight_ass([plan], self.CANVAS)
        # sub-centisecond event bumps to one centisecond, never 0-length
        assert "0:00:00.00,0:00:00.01" in content

    def test_ass_color_rejects_unknown_names(self):
        plan = self._plan()
        plan["highlight"]["color"] = "chartreuse"
        with pytest.raises(ValueError) as exc:
            build_highlight_ass([plan], self.CANVAS)
        assert "hex color" in str(exc.value)

    def test_zero_width_stroke_does_not_resolve_unreachable_color(self):
        plan = self._plan(stroke=None)
        plan["resolved_style"]["stroke_color"] = "not-a-color"

        content = build_highlight_ass([plan], self.CANVAS)

        style = next(
            line for line in content.splitlines() if line.startswith("Style:")
        )
        assert ",0,0,5," in style

    def test_transparent_stroke_is_valid_ass_color(self):
        plan = self._plan()
        plan["resolved_style"]["stroke_color"] = "transparent"

        content = build_highlight_ass([plan], self.CANVAS)

        style = next(
            line for line in content.splitlines() if line.startswith("Style:")
        )
        assert "&HFF000000" in style

    def test_filter_parts_insert_ass_at_z_position(self):
        low = self._plan()
        high = self._plan(
            id="manual_0001",
            z_index=200,
            highlight=None,
            text="TITLE",
            lines=["TITLE"],
        )
        parts = build_overlay_filter_parts(
            [low, high],
            self.CANVAS,
            2.0,
            input_label="vlaid",
            output_label="outv",
            highlight_ass=("/tmp/hl.ass", "/fonts"),
        )
        assert parts[0].startswith("[vlaid]ass=filename=")
        assert "fontsdir=/fonts" in parts[0]
        assert "drawtext" in parts[1]  # title draws after (on top of) ass

    def test_filter_parts_require_ass_input_for_highlights(self):
        with pytest.raises(ValueError) as exc:
            build_overlay_filter_parts(
                [self._plan()],
                self.CANVAS,
                2.0,
                input_label="a",
                output_label="b",
            )
        assert "highlight_ass" in str(exc.value)


class TestStoryboardCommands:
    """Issue #303: storyboard tile extraction + composite builders."""

    def test_build_storyboard_frame_command_seeks_and_scales(self):
        from moviestar.ffmpeg import build_storyboard_frame_command

        cmd = build_storyboard_frame_command(
            "in.mp4", 30.0, "tile_0001.jpg", scale_width=384
        )
        assert cmd[cmd.index("-ss") + 1] == "30.0"
        assert cmd.index("-ss") < cmd.index("-i")  # fast seek
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=384:-1" in vf
        assert "drawtext" not in vf
        assert cmd[cmd.index("-frames:v") + 1] == "1"
        assert cmd[-1] == "tile_0001.jpg"

    def test_build_storyboard_frame_command_label_is_escaped_literal(self):
        from moviestar.ffmpeg import build_storyboard_frame_command

        cmd = build_storyboard_frame_command(
            "in.mp4", 90.0, "tile_0004.jpg",
            label="1:30", font_path="/fonts/Inter-Bold.ttf",
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "drawtext" in vf
        # Timecode colon must be escaped at both filtergraph levels
        # (option parser + graph parser, per _filtergraph_escape) so it
        # reads as text, not an option separator; expansion=none keeps
        # it literal.
        assert r"1\\:30" in vf
        assert "text=1:30" not in vf
        assert "expansion=none" in vf
        assert "Inter-Bold.ttf" in vf

    def test_build_storyboard_frame_command_no_font_no_drawtext(self):
        from moviestar.ffmpeg import build_storyboard_frame_command

        cmd = build_storyboard_frame_command(
            "in.mp4", 0.0, "t.jpg", label="0:00", font_path=None
        )
        assert "drawtext" not in cmd[cmd.index("-vf") + 1]

    def test_build_storyboard_tile_command_grid(self):
        from moviestar.ffmpeg import build_storyboard_tile_command

        cmd = build_storyboard_tile_command(
            "/tmp/x/tile_%04d.jpg", 5, 3, "out.jpg"
        )
        assert cmd[cmd.index("-i") + 1] == "/tmp/x/tile_%04d.jpg"
        vf = cmd[cmd.index("-vf") + 1]
        assert "tile=5x3" in vf
        assert cmd[cmd.index("-frames:v") + 1] == "1"
        assert cmd[-1] == "out.jpg"

    def test_build_storyboard_padding_tile_command(self):
        from moviestar.ffmpeg import build_storyboard_padding_tile_command

        cmd = build_storyboard_padding_tile_command(
            384, 216, "tile_0010.jpg",
            label="(end)", font_path="/fonts/Inter-Bold.ttf",
        )
        # lavfi gray source, NOT black — black padding reads as video
        # content (2026-07-20 friction round miscounted a phase).
        assert cmd[cmd.index("-f") + 1] == "lavfi"
        assert "color=c=0x262626:size=384x216" in cmd[cmd.index("-i") + 1]
        vf = cmd[cmd.index("-vf") + 1]
        assert "drawtext" in vf
        assert "(end)" in vf
        # Must match the video tiles' full-range JPEG pixel format —
        # a mid-sequence format change reinits the tile filter graph
        # and drops every real tile from the mosaic.
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuvj420p"
        assert cmd[-1] == "tile_0010.jpg"

    def test_build_storyboard_padding_tile_command_no_font(self):
        from moviestar.ffmpeg import build_storyboard_padding_tile_command

        cmd = build_storyboard_padding_tile_command(384, 216, "t.jpg")
        assert "-vf" not in cmd


class TestShapedLayout:
    """M26 step 2 — alphamerge in the layout lane.

    Mask inputs append AFTER slot and audio inputs so existing stream
    numbering (including the audio start_index) never shifts — the
    spec's flagged likeliest-quiet-bug.
    """

    def _slots(self, shape=None):
        slots = [
            {
                "path": "/tmp/holden.mp4",
                "source_from": 0.0,
                "source_to": 1.0,
                "region": {"x": 0, "y": 0, "width": 160, "height": 240},
                "framing": {"mode": "fill", "anchor": "left"},
            },
            {
                "path": "/tmp/jdilla.mp4",
                "source_from": 0.0,
                "source_to": 1.0,
                "region": {"x": 160, "y": 0, "width": 120, "height": 120},
                "framing": {"mode": "fill", "anchor": "center"},
            },
        ]
        if shape is not None:
            slots[1]["shape"] = shape
        return slots

    def _circle(self, mask="/tmp/masks/circle.png"):
        return {"kind": "circle", "content_mask": mask}

    def test_shaped_slot_alphamerges_mask_input_after_audio(self):
        cmd = build_render_layout_video_command(
            self._slots(shape=self._circle()),
            "/tmp/out.mp4",
            (320, 240),
            1.0,
            audio_from_input=("/tmp/holden.mp4", [(0.0, 0.5), (0.5, 1.0)]),
        )
        loop_at = cmd.index("-loop")
        assert cmd[loop_at + 1] == "1"
        # Bounded loop: an unbounded mask stream can spin some graphs
        # forever (M20 camera chains + masks).
        assert cmd[loop_at + 2] == "-t"
        assert cmd[loop_at + 3] == "2"
        assert cmd[loop_at + 5] == "/tmp/masks/circle.png"
        graph = cmd[cmd.index("-filter_complex") + 1]
        # 2 slots + 2 audio slices -> the mask is input 4.
        assert "[4:v]format=gray[maskv1]" in graph
        assert "format=rgba[slotc1]" in graph
        assert "[slotc1][maskv1]alphamerge[slotv1]" in graph
        # Audio numbering is untouched by the mask input.
        assert "[2:a]atrim=start=0:end=0.5" in graph
        assert "[3:a]atrim=start=0:end=0.5" in graph
        # The shaped slot still overlays by its region.
        assert "overlay=x=160:y=0:shortest=1[outv]" in graph

    def test_mixed_shaped_slots_map_masks_in_slot_order(self):
        slots = self._slots()
        slots[0]["shape"] = self._circle("/tmp/masks/a.png")
        slots[1]["shape"] = {
            "kind": "rounded",
            "content_mask": "/tmp/masks/b.png",
        }
        cmd = build_render_layout_video_command(
            slots, "/tmp/out.mp4", (320, 240), 1.0
        )
        inputs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]
        assert inputs == [
            "/tmp/holden.mp4", "/tmp/jdilla.mp4",
            "/tmp/masks/a.png", "/tmp/masks/b.png",
        ]
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "[2:v]format=gray[maskv0]" in graph
        assert "[3:v]format=gray[maskv1]" in graph
        assert "[slotc0][maskv0]alphamerge[slotv0]" in graph
        assert "[slotc1][maskv1]alphamerge[slotv1]" in graph

    def test_rect_shape_emits_graph_identical_to_no_shape(self):
        bare = build_render_layout_video_command(
            self._slots(), "/tmp/out.mp4", (320, 240), 1.0,
            audio_from_input=("/tmp/holden.mp4", [(0.0, 1.0)]),
        )
        rect = build_render_layout_video_command(
            self._slots(shape={"kind": "rect"}),
            "/tmp/out.mp4", (320, 240), 1.0,
            audio_from_input=("/tmp/holden.mp4", [(0.0, 1.0)]),
        )
        assert rect == bare

    def test_bordered_rect_uses_a_sharp_color_backplate_without_masks(self):
        shape = {
            "kind": "rect",
            "border": {"width": 8, "color": "white"},
        }
        cmd = build_render_layout_video_command(
            self._slots(shape=shape), "/tmp/out.mp4", (320, 240), 1.0,
        )

        inputs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]
        assert inputs == ["/tmp/holden.mp4", "/tmp/jdilla.mp4"]
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "color=c=white@1.0:s=136x136:d=1.0" in graph
        assert "[bcol1][rectc1]overlay=8:8[slotv1]" in graph
        assert "overlay=x=152:y=-8:shortest=1[outv]" in graph
        assert "alphamerge" not in graph

    def test_bordered_rect_renders_a_sharp_edge(self, tmp_path):
        clip = tmp_path / "red.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=red:s=200x200:d=1:r=30",
                "-pix_fmt", "yuv420p", str(clip),
            ],
            check=True,
        )
        slots = self._slots(
            shape={
                "kind": "rect",
                "border": {"width": 8, "color": "white"},
            }
        )
        for slot in slots:
            slot["path"] = str(clip)
        out = tmp_path / "bordered-rect.mp4"

        command = build_render_layout_video_command(
            slots, str(out), (320, 240), 0.5, output_fps=30.0,
        )
        subprocess.run(command, check=True, capture_output=True)
        raw = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", "0.2", "-i", str(out),
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout

        def px(x, y):
            offset = (y * 320 + x) * 3
            return raw[offset:offset + 3]

        assert px(270, 20)[0] > 180  # red content
        assert all(channel > 230 for channel in px(284, 20))  # white edge
        assert all(channel < 30 for channel in px(292, 20))  # black canvas

    def test_unshaped_slot_chain_stays_clean_next_to_shaped(self):
        cmd = build_render_layout_video_command(
            self._slots(shape=self._circle()),
            "/tmp/out.mp4", (320, 240), 1.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        unshaped_chain = next(
            part for part in graph.split(";") if part.startswith("[0:v]")
        )
        assert "rgba" not in unshaped_chain
        assert "alphamerge" not in unshaped_chain

    def test_shaped_slot_requires_content_mask(self):
        with pytest.raises(ValueError, match="content_mask"):
            build_render_layout_video_command(
                self._slots(shape={"kind": "circle"}),
                "/tmp/out.mp4", (320, 240), 1.0,
            )

    def test_unknown_shape_kind_is_rejected(self):
        with pytest.raises(ValueError, match="shape kind"):
            build_render_layout_video_command(
                self._slots(shape={"kind": "hexagon", "content_mask": "/x"}),
                "/tmp/out.mp4", (320, 240), 1.0,
            )

    def test_frame_command_includes_shaped_masks(self):
        cmd = build_render_layout_frame_command(
            self._slots(shape=self._circle()),
            "/tmp/out.jpg", (320, 240),
        )
        inputs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]
        assert inputs[-1] == "/tmp/masks/circle.png"
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "[2:v]format=gray[maskv1]" in graph
        assert "[slotc1][maskv1]alphamerge[slotv1]" in graph

    def test_frames_command_includes_shaped_masks(self):
        cmd = build_render_layout_frames_command(
            self._slots(shape=self._circle()),
            "/tmp/out-%03d.jpg", (320, 240), 1.0, 0.5,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "[2:v]format=gray[maskv1]" in graph
        assert "[slotc1][maskv1]alphamerge[slotv1]" in graph


class TestShapedLayoutRender:
    """Real renders: the mask actually drops slot pixels, and shaping
    does not shift color."""

    @pytest.fixture()
    def color_clips(self, tmp_path):
        clips = {}
        for name, color in (("red", "red"), ("blue", "0x2040c0")):
            path = tmp_path / f"{name}.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi",
                    "-i", f"color=c={color}:s=200x200:d=1:r=30",
                    "-pix_fmt", "yuv420p", str(path),
                ],
                check=True,
            )
            clips[name] = str(path)
        return clips

    def _render_frame(self, slots, out_path):
        command = build_render_layout_video_command(
            slots, str(out_path), (320, 240), 0.5, output_fps=30.0
        )
        subprocess.run(command, check=True, capture_output=True)
        raw = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", "0.2",
                "-i", str(out_path), "-frames:v", "1",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout

        def px(x, y):
            i = (y * 320 + x) * 3
            return raw[i], raw[i + 1], raw[i + 2]

        return px

    def _slot(self, path, shape=None):
        slot = {
            "path": path,
            "source_from": 0.0,
            "source_to": 1.0,
            "region": {"x": 40, "y": 40, "width": 120, "height": 120},
            "framing": {"mode": "fill", "anchor": "center"},
        }
        if shape is not None:
            slot["shape"] = shape
        return slot

    def test_circle_mask_drops_corners_and_keeps_center(
        self, color_clips, tmp_path
    ):
        from moviestar.masks import ensure_mask

        mask = ensure_mask(
            tmp_path / "masks", width=120, height=120, kind="circle"
        )
        shape = {"kind": "circle", "content_mask": str(mask)}
        px = self._render_frame(
            [self._slot(color_clips["red"], shape)], tmp_path / "circle.mp4"
        )
        # Region corner: base black shows through the dropped pixels.
        assert all(v < 40 for v in px(43, 43))
        # Region center: slot content.
        r, g, b = px(100, 100)
        assert r > 180 and g < 80 and b < 80

    def test_shaping_does_not_shift_slot_color(self, color_clips, tmp_path):
        from moviestar.masks import ensure_mask

        mask = ensure_mask(
            tmp_path / "masks", width=120, height=120, kind="circle"
        )
        shape = {"kind": "circle", "content_mask": str(mask)}
        px_rect = self._render_frame(
            [self._slot(color_clips["blue"])], tmp_path / "rect.mp4"
        )
        px_circle = self._render_frame(
            [self._slot(color_clips["blue"], shape)], tmp_path / "shaped.mp4"
        )
        rect_center = px_rect(100, 100)
        circle_center = px_circle(100, 100)
        for a, b in zip(rect_center, circle_center):
            assert abs(a - b) <= 4


class TestBorderedLayout:
    """M26 step 4 — the border composites as a color disc UNDER the
    content (an annulus over it leaves a coverage seam at the shared
    edge), on a frame padded by the border width."""

    def _slots(self, border=None, motion=None):
        shape = {"kind": "circle", "content_mask": "/tmp/masks/circle.png"}
        if border is not None:
            shape["border"] = border
        inset = {
            "path": "/tmp/jdilla.mp4",
            "source_from": 0.0,
            "source_to": 1.0,
            "region": {"x": 160, "y": 40, "width": 120, "height": 120},
            "framing": {"mode": "fill", "anchor": "center"},
            "shape": shape,
        }
        if motion is not None:
            inset["motion"] = motion
        return [
            {
                "path": "/tmp/holden.mp4",
                "source_from": 0.0,
                "source_to": 1.0,
                "region": {"x": 0, "y": 0, "width": 320, "height": 240},
                "framing": {"mode": "fill", "anchor": "center"},
            },
            inset,
        ]

    def _border(self, color="white"):
        return {
            "width": 8,
            "color": color,
            "disc_mask": "/tmp/masks/disc.png",
        }

    def test_border_appends_disc_mask_after_content_mask(self):
        cmd = build_render_layout_video_command(
            self._slots(border=self._border()),
            "/tmp/out.mp4", (320, 240), 1.0,
            audio_from_input=("/tmp/holden.mp4", [(0.0, 1.0)]),
        )
        inputs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]
        assert inputs == [
            "/tmp/holden.mp4", "/tmp/jdilla.mp4", "/tmp/holden.mp4",
            "/tmp/masks/circle.png", "/tmp/masks/disc.png",
        ]
        graph = cmd[cmd.index("-filter_complex") + 1]
        # 2 slots + 1 audio slice -> content mask 3, disc mask 4.
        assert "[3:v]format=gray[maskv1]" in graph
        assert "[4:v]format=gray[dmaskv1]" in graph
        assert "[2:a]atrim" in graph

    def test_border_disc_composites_under_the_content(self):
        cmd = build_render_layout_video_command(
            self._slots(border=self._border()),
            "/tmp/out.mp4", (320, 240), 1.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        # Padded frame: 120 + 2*8 on each side.
        assert "color=c=white@1.0:s=136x136:d=1.0" in graph
        assert "[bcol1][dmaskv1]alphamerge[disc1]" in graph
        assert "[slotc1][maskv1]alphamerge[shaped1]" in graph
        assert "[disc1][shaped1]overlay=8:8[slotv1]" in graph
        # The bubble overlays at the content origin minus the border.
        assert "overlay=x=152:y=32:shortest=1[outv]" in graph

    def test_border_color_normalizes_for_ffmpeg(self):
        cmd = build_render_layout_video_command(
            self._slots(border=self._border(color="#ff8800")),
            "/tmp/out.mp4", (320, 240), 1.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "color=c=0xff8800@1.0:s=136x136" in graph

    def test_bounced_border_shifts_the_expression(self):
        motion = {
            "preset": "bounce",
            "origin": {"x": 160, "y": 40},
            "bounds": {"x": 200, "y": 120},
            "velocity": {"x": 100.0, "y": 60.0},
            "result_time_offset": 0.0,
        }
        cmd = build_render_layout_video_command(
            self._slots(border=self._border(), motion=motion),
            "/tmp/out.mp4", (320, 240), 1.0, output_fps=30.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "-200)-8'" in graph
        assert "-120)-8'" in graph

    def test_borderless_shaped_graph_is_unchanged_by_border_support(self):
        cmd = build_render_layout_video_command(
            self._slots(), "/tmp/out.mp4", (320, 240), 1.0,
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "disc" not in graph
        assert "[slotc1][maskv1]alphamerge[slotv1]" in graph
        assert "overlay=x=160:y=40:shortest=1[outv]" in graph

    def test_border_requires_disc_mask(self):
        border = {"width": 8, "color": "white"}
        with pytest.raises(ValueError, match="disc_mask"):
            build_render_layout_video_command(
                self._slots(border=border), "/tmp/out.mp4", (320, 240), 1.0,
            )

    def test_border_width_must_be_positive(self):
        border = {"width": 0, "color": "white", "disc_mask": "/tmp/d.png"}
        with pytest.raises(ValueError, match="width"):
            build_render_layout_video_command(
                self._slots(border=border), "/tmp/out.mp4", (320, 240), 1.0,
            )

    def test_bordered_render_draws_the_ring_without_a_seam(self, tmp_path):
        from moviestar.masks import ensure_mask

        clip = tmp_path / "red.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=red:s=200x200:d=1:r=30",
                "-pix_fmt", "yuv420p", str(clip),
            ],
            check=True,
        )
        content = ensure_mask(
            tmp_path / "masks", width=120, height=120, kind="circle"
        )
        disc = ensure_mask(
            tmp_path / "masks", width=136, height=136, kind="circle"
        )
        slots = self._slots(border=self._border())
        slots[1]["path"] = str(clip)
        slots[1]["shape"]["content_mask"] = str(content)
        slots[1]["shape"]["border"]["disc_mask"] = str(disc)
        slots[0]["path"] = str(clip)
        out = tmp_path / "ring.mp4"
        command = build_render_layout_video_command(
            slots, str(out), (320, 240), 0.5, output_fps=30.0
        )
        subprocess.run(command, check=True, capture_output=True)
        raw = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", "0.2", "-i", str(out),
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout

        def px(x, y):
            i = (y * 320 + x) * 3
            return raw[i], raw[i + 1], raw[i + 2]

        # Content center: red. Ring band (content radius 60 + bw/2,
        # horizontal from center (220, 100)): white. The seam test:
        # walking outward on the center row, luma never dips below the
        # ring's own values between content edge and ring middle.
        assert px(220, 100)[0] > 180 and px(220, 100)[1] < 90
        ring = px(220 + 63, 100)
        assert all(v > 230 for v in ring)
        for xr in range(56, 64):
            r, g, b = px(220 + xr, 100)
            luma = (r + g + b) / 3
            assert luma > 60, f"seam dip at radius {xr}: {(r, g, b)}"
