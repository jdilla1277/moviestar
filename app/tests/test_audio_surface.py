"""M21 multi-track audio schema/resolver and authoring surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from tests.conftest import assert_error_envelope


@pytest.fixture
def runner():
    return CliRunner()


def _invoke_json(runner: CliRunner, args: list[str]):
    result = runner.invoke(cli, args)
    data = json.loads(result.stdout)
    return result, data


class TestAudioSchemaResolverSurface:
    def test_top_level_and_group_help_make_audio_discoverable(self, runner):
        top = runner.invoke(cli, ["--help"])
        assert top.exit_code == 0
        editing = top.output.split("EDITING COMMANDS", 1)[1].split(
            "RE-INDEX COMMANDS", 1
        )[0]
        assert "audio" in editing
        assert "voiceover" in editing
        assert "music" in editing

        group = runner.invoke(cli, ["audio", "--help"])
        assert group.exit_code == 0
        for token in ("source", "add", "dump", "set"):
            assert token in group.output
        assert "finished" in group.output.lower()
        assert "result time" in group.output.lower()

    def test_bare_audio_invocation_explains_both_candidate_workflows(self, runner):
        result, data = _invoke_json(runner, ["audio"])
        assert result.exit_code == 0
        assert data["status"] == "audio_authoring"
        assert data["writes_spec"] is False
        assert data["renders_audio_mix"] is True
        assert data["renderer_status"] == "complete"
        commands = json.dumps(data["commands"])
        assert "audio source" in commands
        assert "audio add" in commands
        assert "audio dump" in commands
        assert "audio set" in commands

    def test_source_controls_persist_in_spec_and_status(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(test_video, frames=False)
        spec_path = tmp_path / "moviestar" / "spec.json"
        assert not spec_path.exists()

        result, data = _invoke_json(
            runner,
            [
                "audio", "source", "--gain-db", "-8", "--fade-in", "0.1",
                "--fade-out", "0.2",
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert data["status"] == "source_audio_updated"
        assert data["writes_spec"] is True
        assert data["renders_audio_mix"] is True
        assert data["audio_mix"]["source_audio"] == {
            "muted": False,
            "gain_db": -8.0,
            "fade_in": 0.1,
            "fade_out": 0.2,
        }
        spec = json.loads(spec_path.read_text())
        assert spec["audio_mix"]["source_audio"]["gain_db"] == -8.0
        revision = spec["revisions"][-1]
        assert revision["command"] == "audio source"
        assert revision["changed"]["audio_mix"]["source_audio"]["gain_db"] == 0.0
        assert not (tmp_path / "moviestar" / "audio-mock.json").exists()

        status, status_data = _invoke_json(runner, ["status"])
        assert status.exit_code == 0
        assert status_data["audio_mix"]["source_audio"]["gain_db"] == -8.0
        assert status_data["audio_mix"]["tracks_count"] == 0
        assert status_data["audio_mix"]["renders_audio_mix"] is True

    def test_source_mute_and_unmute_are_explicit(
        self, runner, loaded_project, test_video
    ):
        loaded_project(test_video, frames=False)
        muted, muted_data = _invoke_json(runner, ["audio", "source", "--mute"])
        assert muted.exit_code == 0
        assert muted_data["audio_mix"]["source_audio"]["muted"] is True

        unmuted, unmuted_data = _invoke_json(
            runner, ["audio", "source", "--unmute"]
        )
        assert unmuted.exit_code == 0
        assert unmuted_data["audio_mix"]["source_audio"]["muted"] is False

    def test_add_probes_and_persists_voiceover_then_ducked_music(
        self, runner, loaded_project, test_video, audio_only_file
    ):
        loaded_project(test_video, frames=False)
        narration, narration_data = _invoke_json(
            runner,
            [
                "audio", "add", audio_only_file, "--as", "narration",
                "--kind", "voiceover", "--at", "0", "--gain-db", "0",
                "--fade-in", "0.1", "--fade-out", "0.2",
            ],
        )
        assert narration.exit_code == 0, narration.stdout
        assert narration_data["status"] == "audio_track_added"
        assert narration_data["writes_spec"] is True
        [track] = narration_data["tracks"]
        assert track["id"] == "narration"
        assert track["kind"] == "voiceover"
        assert track["media"]["duration"]["seconds"] == pytest.approx(1.0)
        assert track["media"]["sample_rate"]
        assert track["source_range"]["from"]["seconds"] == 0
        assert track["placed_range"]["from"]["seconds"] == 0

        music, music_data = _invoke_json(
            runner,
            [
                "audio", "add", audio_only_file, "--as", "music",
                "--kind", "music", "--gain-db", "-18", "--loop",
                "--fade-in", "0.25", "--fade-out", "0.25",
                "--duck-under", "narration",
            ],
        )
        assert music.exit_code == 0, music.stdout
        [music_track] = music_data["tracks"]
        assert music_track["id"] == "music"
        assert music_track["loop"] is True
        assert music_track["placed_range"]["to"]["seconds"] == pytest.approx(2.0)
        assert music_track["ducking"]["under"] == ["narration"]
        assert music_track["ducking"]["preset"] == "speech"
        assert music_track["ducking"]["resolved"]["ratio"] == 8.0

        _, status_data = _invoke_json(runner, ["status"])
        assert status_data["audio_mix"]["tracks_count"] == 2
        assert status_data["audio_mix"]["track_ids"] == ["narration", "music"]

    def test_add_accepts_source_as_duck_sidechain(
        self, runner, loaded_project, test_video, audio_only_file
    ):
        loaded_project(test_video, frames=False)
        result, data = _invoke_json(
            runner,
            [
                "audio", "add", audio_only_file, "--as", "music",
                "--kind", "music", "--gain-db", "-18", "--loop",
                "--duck-under", "source",
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert data["tracks"][0]["ducking"]["under"] == ["source"]

    def test_add_rejects_duplicate_id_and_non_audio_file(
        self, runner, loaded_project, test_video, silent_video, audio_only_file
    ):
        loaded_project(test_video, frames=False)
        first = runner.invoke(
            cli,
            ["audio", "add", audio_only_file, "--as", "narration", "--kind", "voiceover"],
        )
        assert first.exit_code == 0, first.stdout

        duplicate, duplicate_data = _invoke_json(
            runner,
            ["audio", "add", audio_only_file, "--as", "narration", "--kind", "music"],
        )
        assert duplicate.exit_code == 1
        assert_error_envelope(duplicate_data, command="audio add")
        assert "narration" in duplicate_data["error"]

        silent, silent_data = _invoke_json(
            runner,
            ["audio", "add", silent_video, "--as", "silent", "--kind", "other"],
        )
        assert silent.exit_code == 1
        assert_error_envelope(silent_data, command="audio add")
        assert "audio stream" in silent_data["error"]

    def test_dump_edit_set_dry_run_then_apply_round_trip(
        self, runner, loaded_project, test_video, audio_only_file, tmp_path
    ):
        loaded_project(test_video, frames=False)
        add = runner.invoke(
            cli,
            ["audio", "add", audio_only_file, "--as", "music", "--kind", "music"],
        )
        assert add.exit_code == 0, add.stdout

        dump_path = tmp_path / "audio.json"
        dumped, dumped_data = _invoke_json(
            runner, ["audio", "dump", "--out", str(dump_path)]
        )
        assert dumped.exit_code == 0, dumped.stdout
        assert dumped_data["status"] == "dumped_audio_mix"
        assert dumped_data["out"] == str(dump_path.resolve())
        document = json.loads(dump_path.read_text())
        assert document["version"] == 1
        assert document["audio_mix"]["tracks"][0]["id"] == "music"
        assert document["_examples"]["voiceover_track"]["kind"] == "voiceover"
        assert document["_examples"]["music_track"]["ducking"] == {
            "under": ["narration"],
            "preset": "speech",
        }

        document["audio_mix"]["tracks"][0]["gain_db"] = -22
        dump_path.write_text(json.dumps(document))
        dry, dry_data = _invoke_json(
            runner, ["audio", "set", str(dump_path), "--dry-run"]
        )
        assert dry.exit_code == 0, dry.stdout
        assert dry_data["status"] == "would_set_audio_mix"
        assert dry_data["writes_spec"] is False
        assert dry_data["requested_dry_run"] is True
        assert dry_data["audio_mix"]["tracks"][0]["gain_db"] == -22.0

        _, before_apply = _invoke_json(runner, ["status"])
        assert before_apply["audio_mix"]["tracks"][0]["gain_db"] == 0.0

        applied, applied_data = _invoke_json(
            runner, ["audio", "set", str(dump_path)]
        )
        assert applied.exit_code == 0, applied.stdout
        assert applied_data["status"] == "audio_mix_set"
        assert applied_data["writes_spec"] is True
        _, after_apply = _invoke_json(runner, ["status"])
        assert after_apply["audio_mix"]["tracks"][0]["gain_db"] == -22.0

    def test_fresh_dump_is_self_sufficient_and_examples_are_ignored_on_set(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(test_video, frames=False)
        dump_path = tmp_path / "audio.json"
        dumped, _ = _invoke_json(
            runner, ["audio", "dump", "--out", str(dump_path)]
        )
        assert dumped.exit_code == 0, dumped.stdout

        document = json.loads(dump_path.read_text())
        assert document["audio_mix"]["tracks"] == []
        assert set(document["_examples"]) == {"voiceover_track", "music_track"}

        dry, data = _invoke_json(
            runner, ["audio", "set", str(dump_path), "--dry-run"]
        )
        assert dry.exit_code == 0, dry.stdout
        assert data["audio_mix"]["tracks"] == []

    def test_status_hint_distinguishes_audio_edits_from_visual_edits(
        self, runner, loaded_project, test_video
    ):
        loaded_project(test_video, frames=False)
        changed = runner.invoke(cli, ["audio", "source", "--gain-db", "-8"])
        assert changed.exit_code == 0, changed.output

        result, data = _invoke_json(runner, ["status"])
        assert result.exit_code == 0, result.output
        assert "audio edits exist" in data["hint"]
        assert "audio dump" in data["hint"]

    def test_set_reports_path_addressed_unknown_duck_and_cycle_errors(
        self, runner, loaded_project, test_video, audio_only_file, tmp_path
    ):
        loaded_project(test_video, frames=False)
        payload = {
            "version": 1,
            "audio_mix": {
                "source_audio": {
                    "muted": False,
                    "gain_db": 0,
                    "fade_in": 0,
                    "fade_out": 0,
                },
                "tracks": [
                    {
                        "id": "music",
                        "kind": "music",
                        "path": audio_only_file,
                        "at": "0",
                        "source_from": "0",
                        "source_to": None,
                        "gain_db": -18,
                        "muted": False,
                        "loop": True,
                        "fade_in": 0,
                        "fade_out": 0,
                        "ducking": {"under": ["missing"], "preset": "speech"},
                    }
                ],
            },
        }
        audio_file = tmp_path / "audio.json"
        audio_file.write_text(json.dumps(payload))
        missing, missing_data = _invoke_json(
            runner, ["audio", "set", str(audio_file), "--dry-run"]
        )
        assert missing.exit_code == 1
        assert missing_data["command"] == "audio set"
        [unknown] = [
            error for error in missing_data["errors"]
            if "ducking.under[0]" in error["path"]
        ]
        assert "missing" in unknown["error"]
        assert "source" in unknown["error"]

        second = dict(payload["audio_mix"]["tracks"][0])
        second.update(
            {
                "id": "voice",
                "kind": "voiceover",
                "ducking": {"under": ["music"], "preset": "speech"},
            }
        )
        payload["audio_mix"]["tracks"][0]["ducking"]["under"] = ["voice"]
        payload["audio_mix"]["tracks"].append(second)
        audio_file.write_text(json.dumps(payload))
        cycle, cycle_data = _invoke_json(
            runner, ["audio", "set", str(audio_file), "--dry-run"]
        )
        assert cycle.exit_code == 1
        assert any("cycle" in error["error"] for error in cycle_data["errors"])

    def test_invalid_track_does_not_cascade_into_unknown_sidechain_noise(
        self, runner, loaded_project, test_video, audio_only_file, tmp_path
    ):
        loaded_project(test_video, frames=False)
        base = {
            "kind": "voiceover",
            "path": audio_only_file,
            "at": "0",
            "source_from": "0",
            "source_to": None,
            "gain_db": 0,
            "muted": False,
            "loop": False,
            "fade_in": 0,
            "fade_out": 0,
            "ducking": None,
        }
        narration = {**base, "id": "narration", "at": "99"}
        music = {
            **base,
            "id": "music",
            "kind": "music",
            "ducking": {"under": ["narration"], "preset": "speech"},
        }
        payload = {
            "version": 1,
            "audio_mix": {
                "source_audio": {
                    "muted": False,
                    "gain_db": 0,
                    "fade_in": 0,
                    "fade_out": 0,
                },
                "tracks": [narration, music],
            },
        }
        audio_file = tmp_path / "audio.json"
        audio_file.write_text(json.dumps(payload))

        result, data = _invoke_json(
            runner, ["audio", "set", str(audio_file), "--dry-run"]
        )
        assert result.exit_code == 1
        assert [error["path"] for error in data["errors"]] == [
            "audio_mix.tracks[0].at"
        ]

    def test_undo_audio_restores_prior_complete_mix(
        self, runner, loaded_project, test_video, audio_only_file
    ):
        loaded_project(test_video, frames=False)
        source = runner.invoke(cli, ["audio", "source", "--gain-db", "-8"])
        assert source.exit_code == 0, source.stdout
        add = runner.invoke(
            cli,
            ["audio", "add", audio_only_file, "--as", "music", "--kind", "music"],
        )
        assert add.exit_code == 0, add.stdout

        undone, data = _invoke_json(runner, ["undo", "--audio"])
        assert undone.exit_code == 0, undone.stdout
        # Stage 7: undo pops the last command's revision atomically.
        assert data["status"] == "command_undone"
        assert data["writes_spec"] is True
        assert data["undone"]["command"] == "audio add"
        source_check, source_data = _invoke_json(runner, ["audio", "source"])
        assert source_check.exit_code == 0, source_check.stdout
        assert source_data["audio_mix"]["tracks_count"] == 0
        assert source_data["audio_mix"]["source_audio"]["gain_db"] == -8.0

    def test_stale_mock_file_is_ignored(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(test_video, frames=False)
        stale = {
            "version": 1,
            "audio_mix": {
                "source_audio": {
                    "muted": True,
                    "gain_db": -40,
                    "fade_in": 0,
                    "fade_out": 0,
                },
                "tracks": [],
            },
            "audio_mix_history": [],
        }
        (tmp_path / "moviestar" / "audio-mock.json").write_text(json.dumps(stale))

        result, data = _invoke_json(runner, ["status"])
        assert result.exit_code == 0
        assert data["audio_mix"]["source_audio"]["muted"] is False
        assert data["audio_mix"]["source_audio"]["gain_db"] == 0.0

    def test_export_dry_run_resolves_and_builds_base_mix_command(
        self, runner, loaded_project, test_video, audio_only_file
    ):
        loaded_project(test_video, frames=False)
        added = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file, "--as", "music",
                "--kind", "music", "--loop", "--gain-db", "-18",
            ],
        )
        assert added.exit_code == 0, added.output

        dry, data = _invoke_json(runner, ["export", "--dry-run"])
        assert dry.exit_code == 0, dry.output
        assert data["audio_mix"]["track_ids"] == ["music"]
        assert (
            data["audio_mix"]["tracks"][0]["placed_range"]["to"]["seconds"]
            == 2.0
        )
        assert data["audio_mix_applied_to_ffmpeg_command"] is True
        assert data["render_available"] is True
        assert data["audio_mix_command"][0] == "ffmpeg"
        graph = data["audio_mix_command"][
            data["audio_mix_command"].index("-filter_complex") + 1
        ]
        assert "aloop=loop=-1" in graph
        assert "amix=inputs=2:duration=longest:normalize=0" in graph
        assert data["audio_working_format"] == {
            "sample_rate_hz": 48000,
            "channel_layout": "stereo",
            "sample_format": "fltp",
        }

    def test_real_export_applies_source_audio_edits(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(test_video, frames=False)
        changed = runner.invoke(cli, ["audio", "source", "--gain-db", "-8"])
        assert changed.exit_code == 0, changed.output

        result, data = _invoke_json(
            runner, ["export", "--out", str(tmp_path / "out.mp4")]
        )
        assert result.exit_code == 0, result.output
        assert data["status"] == "exported"
        assert data["audio_mix_applied_to_ffmpeg_command"] is True
        assert data["audio_mix_command"][0] == "ffmpeg"
        assert (tmp_path / "out.mp4").exists()

    def test_real_export_sums_source_with_looped_external_track(
        self, runner, loaded_project, test_video, audio_only_file, tmp_path
    ):
        loaded_project(test_video, frames=False)
        added = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file, "--as", "music",
                "--kind", "music", "--gain-db", "-18", "--loop",
                "--fade-in", "0.1", "--fade-out", "0.2",
            ],
        )
        assert added.exit_code == 0, added.output

        output = tmp_path / "source-plus-music.mp4"
        exported, data = _invoke_json(
            runner, ["export", "--out", str(output)]
        )

        assert exported.exit_code == 0, exported.output
        graph = data["audio_mix_command"][
            data["audio_mix_command"].index("-filter_complex") + 1
        ]
        assert "aloop=loop=-1:size=48000" in graph
        assert "amix=inputs=2:duration=longest:normalize=0" in graph
        assert output.exists()
        assert data["file_size_bytes"] == output.stat().st_size

    def test_nondefault_mix_applies_limiter_then_measured_loudness_postpass(
        self, runner, loaded_project, test_video, audio_only_file, tmp_path
    ):
        loaded_project(test_video, frames=False)
        added = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file, "--as", "music",
                "--kind", "music", "--loop", "--gain-db", "-18",
            ],
        )
        assert added.exit_code == 0, added.output

        output = tmp_path / "mastered.mp4"
        dry, data = _invoke_json(
            runner,
            [
                "export", "--out", str(output), "--loudness-target", "-14",
                "--dry-run",
            ],
        )

        assert dry.exit_code == 0, dry.output
        base_graph = " ".join(data["ffmpeg_command"])
        mix_graph = data["audio_mix_command"][
            data["audio_mix_command"].index("-filter_complex") + 1
        ]
        # Issue #352: normalization is a measured post-pass on the
        # rendered artifact, not an inline loudnorm in either graph.
        assert "loudnorm" not in base_graph
        assert "loudnorm" not in mix_graph
        assert "alimiter=" in mix_graph
        assert data["loudness_normalization"]["target_lufs"] == -14.0
        assert data["loudness_normalization"]["measured"] is None
        assert data["audio_mix_safety"]["limiter"]["ceiling_dbfs"] == -0.5
        assert data["audio_mix_filter_order"][-1] == "final_limiter"

        rendered, rendered_data = _invoke_json(
            runner,
            ["export", "--out", str(output), "--loudness-target", "-14"],
        )
        assert rendered.exit_code == 0, rendered.output
        assert rendered_data["audio_mix_command"] == data["audio_mix_command"]
        assert rendered_data["loudness_normalization"]["measured"] is not None
        assert "target_met" in rendered_data["loudness_normalization"]
        assert output.exists()

    def test_source_join_fades_precede_mix_limiter(
        self, runner, loaded_project, test_video
    ):
        loaded_project(test_video, frames=False)
        cut = runner.invoke(cli, ["cut", "--from", "0.8", "--to", "1.2"])
        assert cut.exit_code == 0, cut.output
        changed = runner.invoke(cli, ["audio", "source", "--gain-db", "-1"])
        assert changed.exit_code == 0, changed.output

        dry, data = _invoke_json(
            runner,
            ["export", "--audio-join-fade", "0.05", "--dry-run"],
        )

        assert dry.exit_code == 0, dry.output
        base_graph = data["ffmpeg_command"][
            data["ffmpeg_command"].index("-filter_complex") + 1
        ]
        mix_graph = data["audio_mix_command"][
            data["audio_mix_command"].index("-filter_complex") + 1
        ]
        assert "afade=t=out" in base_graph
        assert "alimiter=" in mix_graph
        assert data["audio_mix_filter_order"][0] == "source_join_fades"
        assert data["audio_mix_filter_order"][-1] == "final_limiter"

    def test_external_track_can_be_loudness_normalized_when_source_is_silent(
        self, runner, loaded_project, silent_video, audio_only_file, tmp_path
    ):
        loaded_project(silent_video, frames=False)
        added = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file, "--as", "voice",
                "--kind", "voiceover",
            ],
        )
        assert added.exit_code == 0, added.output

        output = tmp_path / "external-mastered.mp4"
        rendered, data = _invoke_json(
            runner,
            ["export", "--out", str(output), "--loudness-target", "-16"],
        )

        assert rendered.exit_code == 0, rendered.output
        assert output.exists()
        env = data["loudness_normalization"]
        assert env["target_lufs"] == -16.0
        # The post-pass measured the mixed artifact and applied a real
        # correction toward the target (a sine voiceover has plenty of
        # true-peak headroom, so the target is achievable).
        assert env["target_met"] is True
        assert env["measured"]["integrated_lufs"] == pytest.approx(
            -16.0, abs=1.0
        )

    def test_real_export_renders_ducking_and_reports_filter_order(
        self, runner, loaded_project, test_video, audio_only_file, tmp_path
    ):
        loaded_project(test_video, frames=False)
        added = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file, "--as", "music",
                "--kind", "music", "--duck-under", "source",
            ],
        )
        assert added.exit_code == 0, added.output

        dry, dry_data = _invoke_json(
            runner,
            ["export", "--out", str(tmp_path / "out.mp4"), "--dry-run"],
        )
        assert dry.exit_code == 0, dry.output
        result, data = _invoke_json(
            runner, ["export", "--out", str(tmp_path / "out.mp4")]
        )
        assert result.exit_code == 0, result.output
        assert data["audio_mix_command"] == dry_data["audio_mix_command"]
        assert data["audio_mix_applied_to_ffmpeg_command"] is True
        assert data["audio_ducking"]["targets"][0]["id"] == "music"
        assert data["audio_ducking"]["targets"][0]["sidechains"] == ["source"]
        assert data["audio_ducking"]["filter_order"] == [
            "layer_gain_and_fades",
            "combine_sidechains",
            "sidechaincompress",
            "final_mix",
        ]
        graph = data["audio_mix_command"][
            data["audio_mix_command"].index("-filter_complex") + 1
        ]
        assert "sidechaincompress=" in graph
        assert (tmp_path / "out.mp4").exists()

    def test_single_source_watch_dry_run_uses_windowed_audio_mix(
        self, runner, loaded_project, test_video, audio_only_file
    ):
        loaded_project(test_video, frames=False)
        added = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file, "--as", "music",
                "--kind", "music", "--at", "0", "--loop",
                "--duck-under", "source",
            ],
        )
        assert added.exit_code == 0, added.output

        watched, data = _invoke_json(
            runner, ["watch", "--from", "0.25", "--to", "0.75", "--dry-run"]
        )

        assert watched.exit_code == 0, watched.output
        assert data["audio_mix_applied_to_ffmpeg_command"] is True
        graph = data["audio_mix_command"][
            data["audio_mix_command"].index("-filter_complex") + 1
        ]
        assert "atrim=start=0.25" in graph
        assert "aloop=loop=-1" in graph
        assert "sidechaincompress=" in graph

    def test_single_source_watch_renders_windowed_audio_mix(
        self, runner, loaded_project, test_video, audio_only_file, tmp_path
    ):
        loaded_project(test_video, frames=False)
        added = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file, "--as", "music",
                "--kind", "music", "--at", "0", "--loop",
                "--duck-under", "source",
            ],
        )
        assert added.exit_code == 0, added.output

        output = tmp_path / "single-source-mix-watch.mp4"
        watched, data = _invoke_json(
            runner,
            [
                "watch", "--from", "0.25", "--to", "0.75",
                "--out", str(output),
            ],
        )

        assert watched.exit_code == 0, watched.output
        assert data["audio_mix_applied_to_ffmpeg_command"] is True
        assert "sidechaincompress=" in data["audio_mix_command"][
            data["audio_mix_command"].index("-filter_complex") + 1
        ]
        assert output.exists()
        assert data["file_size_bytes"] == output.stat().st_size

    def test_layout_watch_dry_run_uses_same_windowed_audio_mix(
        self, runner, loaded_project, test_video, audio_only_file
    ):
        loaded_project(
            [test_video, test_video], names=["left", "right"], frames=False
        )
        composed = runner.invoke(
            cli,
            [
                "concat", "--canvas", "320x240", "--layout", "two-up",
                "--slot", "left=left", "--from", "0", "--to", "1",
                "--slot", "right=right", "--from", "0", "--to", "1",
                "--audio-from", "left",
            ],
        )
        assert composed.exit_code == 0, composed.output
        added = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file, "--as", "music",
                "--kind", "music", "--at", "0", "--loop",
                "--duck-under", "source",
            ],
        )
        assert added.exit_code == 0, added.output

        watched, data = _invoke_json(
            runner, ["watch", "--from", "0.25", "--to", "0.75", "--dry-run"]
        )
        assert watched.exit_code == 0, watched.output
        assert data["audio_mix_applied_to_ffmpeg_command"] is True
        graph = data["audio_mix_command"][
            data["audio_mix_command"].index("-filter_complex") + 1
        ]
        assert "atrim=start=0.25" in graph
        assert "aloop=loop=-1" in graph
        assert "sidechaincompress=" in graph

    def test_layout_watch_renders_windowed_audio_mix(
        self, runner, loaded_project, test_video, audio_only_file, tmp_path
    ):
        loaded_project(
            [test_video, test_video], names=["left", "right"], frames=False
        )
        composed = runner.invoke(
            cli,
            [
                "concat", "--canvas", "320x240", "--layout", "two-up",
                "--slot", "left=left", "--from", "0", "--to", "1",
                "--slot", "right=right", "--from", "0", "--to", "1",
                "--audio-from", "left",
            ],
        )
        assert composed.exit_code == 0, composed.output
        added = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file, "--as", "music",
                "--kind", "music", "--at", "0", "--loop",
                "--duck-under", "source",
            ],
        )
        assert added.exit_code == 0, added.output

        output = tmp_path / "mix-watch.mp4"
        watched, data = _invoke_json(
            runner,
            [
                "watch", "--from", "0.25", "--to", "0.75",
                "--out", str(output),
            ],
        )

        assert watched.exit_code == 0, watched.output
        assert data["audio_mix_applied_to_ffmpeg_command"] is True
        assert "sidechaincompress=" in data["audio_mix_command"][
            data["audio_mix_command"].index("-filter_complex") + 1
        ]
        assert output.exists()
        assert data["file_size_bytes"] == output.stat().st_size

    def test_status_reprobes_external_files_and_fails_if_one_moves(
        self, runner, loaded_project, test_video, audio_only_file, tmp_path
    ):
        loaded_project(test_video, frames=False)
        track_path = tmp_path / "track.wav"
        track_path.write_bytes(Path(audio_only_file).read_bytes())
        added = runner.invoke(
            cli,
            [
                "audio", "add", str(track_path), "--as", "music",
                "--kind", "music",
            ],
        )
        assert added.exit_code == 0, added.output
        track_path.unlink()

        result, data = _invoke_json(runner, ["status"])
        assert result.exit_code == 1
        assert data["command"] == "status"
        assert "file not found" in data["error"]

    def test_export_reprobes_moved_track_before_writing_base_video(
        self, runner, loaded_project, test_video, audio_only_file, tmp_path
    ):
        loaded_project(test_video, frames=False)
        track_path = tmp_path / "track.wav"
        track_path.write_bytes(Path(audio_only_file).read_bytes())
        added = runner.invoke(
            cli,
            [
                "audio", "add", str(track_path), "--as", "music",
                "--kind", "music",
            ],
        )
        assert added.exit_code == 0, added.output
        track_path.unlink()

        output = tmp_path / "must-not-exist.mp4"
        exported, data = _invoke_json(
            runner, ["export", "--out", str(output)]
        )

        assert exported.exit_code == 1
        assert_error_envelope(data, command="export")
        assert "file not found" in data["error"]
        assert not output.exists()

    def test_status_reresolves_placement_after_visual_duration_changes(
        self, runner, loaded_project, test_video, audio_only_file
    ):
        loaded_project(test_video, frames=False)
        added = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file, "--as", "voice",
                "--kind", "voiceover",
            ],
        )
        assert added.exit_code == 0, added.output
        trimmed = runner.invoke(cli, ["trim", "--from", "0", "--to", "0.5"])
        assert trimmed.exit_code == 0, trimmed.output

        status, data = _invoke_json(runner, ["status"])
        assert status.exit_code == 0, status.output
        [voice] = data["audio_mix"]["tracks"]
        assert voice["placed_range"]["to"]["seconds"] == 0.5
        assert voice["clipped_at_result_end"] is True

    def test_audio_subcommands_require_a_project(self, runner, audio_only_file):
        for args, command in (
            (["audio", "source", "--mute"], "audio source"),
            (["audio", "add", audio_only_file, "--as", "music", "--kind", "music"], "audio add"),
            (["audio", "dump"], "audio dump"),
        ):
            result, data = _invoke_json(runner, args)
            assert result.exit_code == 1
            assert data["command"] == command
            assert "load" in data["hint"]


# -------------------- Issue #357: per-track loudness report --------------------
#
# The field report behind #357: an agent that cannot audition audio has no
# machine-readable way to see that a music bed is drowning dialogue. The
# report measures each routed layer through the exact render graph, so the
# numbers reflect gain, fades, looping, placement, and ducking as exported.


@pytest.fixture(scope="module")
def quiet_speech_video(tmp_path_factory) -> str:
    """2s video whose audio stands in for quiet dialogue (sine at -30 dB)."""
    import subprocess

    path = tmp_path_factory.mktemp("loudness") / "quiet_speech.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=2:size=160x120:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
            "-filter_complex", "[1:a]volume=-30dB[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.fixture(scope="module")
def loud_music_wav(tmp_path_factory) -> str:
    """1s music bed markedly louder than quiet_speech_video (sine at -6 dB)."""
    import subprocess

    path = tmp_path_factory.mktemp("loudness") / "loud_music.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=800:duration=1",
            "-af", "volume=-6dB",
            str(path),
        ],
        check=True,
    )
    return str(path)


class TestLoudnessReport:
    def _project_with_music(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav, **flags
    ):
        loaded_project(quiet_speech_video, frames=False)
        args = [
            "audio", "add", loud_music_wav, "--as", "music", "--kind", "music",
        ]
        for flag, value in flags.items():
            option = "--" + flag.replace("_", "-")
            if value is True:
                args.append(option)
            else:
                args += [option, str(value)]
        added = runner.invoke(cli, args)
        assert added.exit_code == 0, added.output

    def test_report_reveals_music_louder_than_source(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav, tmp_path
    ):
        self._project_with_music(
            runner, loaded_project, quiet_speech_video, loud_music_wav, loop=True,
        )
        result, data = _invoke_json(
            runner,
            [
                "export", "--out", str(tmp_path / "out.mp4"),
                "--loudness-report",
            ],
        )
        assert result.exit_code == 0, result.output

        report = data["audio_loudness"]
        assert report["measured"] is True
        by_id = {entry["id"]: entry for entry in report["tracks"]}
        assert set(by_id) == {"source", "music"}

        source_lufs = by_id["source"]["loudness"]["integrated_lufs"]
        music_lufs = by_id["music"]["loudness"]["integrated_lufs"]
        assert music_lufs > source_lufs + 10.0

        assert by_id["music"]["gain_db"] == 0.0
        assert by_id["music"]["muted"] is False
        assert by_id["music"]["measured_stage"] == "post_processing_pre_final_mix"
        assert by_id["source"]["kind"] == "source_audio"

        final = report["final_mix"]
        assert final["measured_stage"] == "as_rendered"
        assert final["loudness"]["integrated_lufs"] < 0.0
        assert report["window"]["duration"]["seconds"] == pytest.approx(2.0, abs=0.1)

    def test_gain_edit_shifts_reported_track_loudness(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav, tmp_path
    ):
        self._project_with_music(
            runner, loaded_project, quiet_speech_video, loud_music_wav, loop=True,
        )

        def music_lufs(out_name):
            result, data = _invoke_json(
                runner,
                ["export", "--out", str(tmp_path / out_name), "--loudness-report"],
            )
            assert result.exit_code == 0, result.output
            by_id = {e["id"]: e for e in data["audio_loudness"]["tracks"]}
            return by_id["music"]["loudness"]["integrated_lufs"]

        loud = music_lufs("loud.mp4")

        dump_path = tmp_path / "mix.json"
        dumped = runner.invoke(cli, ["audio", "dump", "--out", str(dump_path)])
        assert dumped.exit_code == 0, dumped.output
        document = json.loads(dump_path.read_text())
        document["audio_mix"]["tracks"][0]["gain_db"] = -20.0
        dump_path.write_text(json.dumps(document))
        applied = runner.invoke(cli, ["audio", "set", str(dump_path)])
        assert applied.exit_code == 0, applied.output

        quiet = music_lufs("quiet.mp4")
        assert 15.0 < loud - quiet < 25.0

    def test_muted_track_is_reported_explicitly(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav, tmp_path
    ):
        self._project_with_music(
            runner, loaded_project, quiet_speech_video, loud_music_wav,
            loop=True, mute=True,
        )
        result, data = _invoke_json(
            runner,
            ["export", "--out", str(tmp_path / "out.mp4"), "--loudness-report"],
        )
        assert result.exit_code == 0, result.output

        by_id = {e["id"]: e for e in data["audio_loudness"]["tracks"]}
        assert by_id["music"]["excluded_reason"] == "muted"
        assert by_id["music"]["loudness"] is None
        assert by_id["music"]["muted"] is True
        assert by_id["source"]["loudness"]["integrated_lufs"] < 0.0

    def test_partial_coverage_reported_for_non_looping_track(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav, tmp_path
    ):
        self._project_with_music(
            runner, loaded_project, quiet_speech_video, loud_music_wav,
        )
        result, data = _invoke_json(
            runner,
            ["export", "--out", str(tmp_path / "out.mp4"), "--loudness-report"],
        )
        assert result.exit_code == 0, result.output

        by_id = {e["id"]: e for e in data["audio_loudness"]["tracks"]}
        music = by_id["music"]
        assert music["coverage"]["fraction_of_window"] == pytest.approx(0.5, abs=0.05)
        assert music["coverage"]["duration"]["seconds"] == pytest.approx(1.0, abs=0.05)
        assert music["loudness"]["integrated_lufs"] < 0.0
        assert by_id["source"]["coverage"]["fraction_of_window"] == pytest.approx(
            1.0, abs=0.01
        )

    def test_dry_run_reports_skip_note_not_measurements(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav, tmp_path
    ):
        self._project_with_music(
            runner, loaded_project, quiet_speech_video, loud_music_wav, loop=True,
        )
        result, data = _invoke_json(
            runner,
            [
                "export", "--out", str(tmp_path / "out.mp4"),
                "--loudness-report", "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output

        report = data["audio_loudness"]
        assert report["measured"] is False
        assert "tracks" not in report
        assert "--dry-run" in report["note"]
        assert report["window"]["from"]["seconds"] == 0.0
        assert report["window"]["duration"]["seconds"] == pytest.approx(
            2.0, abs=0.1
        )

    def test_report_absent_without_flag(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav, tmp_path
    ):
        self._project_with_music(
            runner, loaded_project, quiet_speech_video, loud_music_wav, loop=True,
        )
        result, data = _invoke_json(
            runner, ["export", "--out", str(tmp_path / "out.mp4")]
        )
        assert result.exit_code == 0, result.output
        assert "audio_loudness" not in data

    def test_passthrough_project_still_reports_source_and_final(
        self, runner, loaded_project, quiet_speech_video, tmp_path
    ):
        loaded_project(quiet_speech_video, frames=False)
        result, data = _invoke_json(
            runner,
            ["export", "--out", str(tmp_path / "out.mp4"), "--loudness-report"],
        )
        assert result.exit_code == 0, result.output

        report = data["audio_loudness"]
        assert report["measured"] is True
        by_id = {e["id"]: e for e in report["tracks"]}
        assert set(by_id) == {"source"}
        assert by_id["source"]["loudness"]["integrated_lufs"] < 0.0
        assert report["final_mix"]["loudness"]["integrated_lufs"] < 0.0

    def test_result_without_audio_is_reported_explicitly(
        self, runner, loaded_project, silent_video, tmp_path
    ):
        loaded_project(silent_video, frames=False)
        result, data = _invoke_json(
            runner,
            ["export", "--out", str(tmp_path / "out.mp4"), "--loudness-report"],
        )
        assert result.exit_code == 0, result.output

        report = data["audio_loudness"]
        assert report["tracks"] == []
        assert report["final_mix"] is None
        assert "no audio" in report["note"]
        assert report["window"]["from"]["seconds"] == 0.0
        assert report["window"]["duration"]["seconds"] == pytest.approx(
            1.0, abs=0.1
        )

    def test_export_help_documents_loudness_report(self, runner):
        result = runner.invoke(cli, ["export", "--help"])
        assert result.exit_code == 0
        assert "--loudness-report" in result.output
        assert "LUFS" in result.output

    def test_watch_report_is_bounded_to_requested_window(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav, tmp_path
    ):
        self._project_with_music(
            runner, loaded_project, quiet_speech_video, loud_music_wav,
        )

        result, data = _invoke_json(
            runner,
            [
                "watch", "--from", "0.5", "--to", "1.5", "--precise",
                "--out", str(tmp_path / "watch.mp4"), "--loudness-report",
            ],
        )

        assert result.exit_code == 0, result.output
        report = data["audio_loudness"]
        assert report["measured"] is True
        assert report["window"]["from"]["seconds"] == 0.5
        assert report["window"]["to"]["seconds"] == 1.5
        assert report["window"]["duration"]["seconds"] == 1.0

        by_id = {entry["id"]: entry for entry in report["tracks"]}
        assert set(by_id) == {"source", "music"}
        assert by_id["source"]["coverage"]["fraction_of_window"] == 1.0
        assert by_id["music"]["coverage"]["fraction_of_window"] == 0.5
        assert by_id["music"]["coverage"]["from"]["seconds"] == 0.5
        assert by_id["music"]["coverage"]["to"]["seconds"] == 1.0
        assert by_id["music"]["loudness"]["integrated_lufs"] < 0.0
        assert report["final_mix"]["loudness"]["integrated_lufs"] < 0.0

    def test_watch_report_keeps_tracks_outside_window_explicit(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav, tmp_path
    ):
        self._project_with_music(
            runner, loaded_project, quiet_speech_video, loud_music_wav, at=1.0,
        )

        result, data = _invoke_json(
            runner,
            [
                "watch", "--from", "0", "--to", "0.5", "--precise",
                "--out", str(tmp_path / "watch.mp4"), "--loudness-report",
            ],
        )

        assert result.exit_code == 0, result.output
        by_id = {entry["id"]: entry for entry in data["audio_loudness"]["tracks"]}
        assert by_id["music"]["loudness"] is None
        assert by_id["music"]["excluded_reason"] == "outside_window"
        assert by_id["source"]["coverage"]["fraction_of_window"] == 1.0

    def test_watch_loudness_report_dry_run_does_not_fabricate_measurements(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav
    ):
        self._project_with_music(
            runner, loaded_project, quiet_speech_video, loud_music_wav, loop=True,
        )

        result, data = _invoke_json(
            runner,
            [
                "watch", "--from", "0.5", "--to", "1.5",
                "--loudness-report", "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.output
        assert data["audio_loudness"]["measured"] is False
        assert "tracks" not in data["audio_loudness"]
        assert "--dry-run" in data["audio_loudness"]["note"]
        assert data["audio_loudness"]["window"]["from"]["seconds"] == 0.5
        assert data["audio_loudness"]["window"]["to"]["seconds"] == 1.5

    def test_watch_passthrough_report_measures_requested_window(
        self, runner, loaded_project, quiet_speech_video, tmp_path
    ):
        loaded_project(quiet_speech_video, frames=False)

        result, data = _invoke_json(
            runner,
            [
                "watch", "--from", "0.5", "--to", "1.5",
                "--out", str(tmp_path / "passthrough-watch.mp4"),
                "--loudness-report",
            ],
        )

        assert result.exit_code == 0, result.output
        report = data["audio_loudness"]
        assert report["window"]["from"]["seconds"] == 0.5
        assert report["window"]["to"]["seconds"] == 1.5
        assert [entry["id"] for entry in report["tracks"]] == ["source"]
        assert report["tracks"][0]["coverage"]["fraction_of_window"] == 1.0
        assert report["tracks"][0]["loudness"]["integrated_lufs"] < 0.0
        assert report["final_mix"]["loudness"]["integrated_lufs"] < 0.0

    def test_watch_silent_result_still_reports_requested_window(
        self, runner, loaded_project, silent_video, tmp_path
    ):
        loaded_project(silent_video, frames=False)

        result, data = _invoke_json(
            runner,
            [
                "watch", "--from", "0.25", "--to", "0.75", "--precise",
                "--out", str(tmp_path / "silent-watch.mp4"),
                "--loudness-report",
            ],
        )

        assert result.exit_code == 0, result.output
        report = data["audio_loudness"]
        assert report["measured"] is True
        assert report["window"]["from"]["seconds"] == 0.25
        assert report["window"]["to"]["seconds"] == 0.75
        assert report["tracks"] == []
        assert report["final_mix"] is None

    def test_scene_watch_report_uses_same_windowed_schema(
        self, runner, loaded_project, quiet_speech_video, loud_music_wav, tmp_path
    ):
        loaded_project(
            [quiet_speech_video, quiet_speech_video],
            names=["left", "right"],
            frames=False,
        )
        composed = runner.invoke(
            cli,
            [
                "concat", "--canvas", "320x240", "--layout", "two-up",
                "--slot", "left=left", "--from", "0", "--to", "2",
                "--slot", "right=right", "--from", "0", "--to", "2",
                "--audio-from", "left",
            ],
        )
        assert composed.exit_code == 0, composed.output
        added = runner.invoke(
            cli,
            [
                "audio", "add", loud_music_wav, "--as", "music",
                "--kind", "music", "--loop",
            ],
        )
        assert added.exit_code == 0, added.output

        result, data = _invoke_json(
            runner,
            [
                "watch", "--from", "0.5", "--to", "1.5",
                "--out", str(tmp_path / "scene-watch.mp4"),
                "--loudness-report",
            ],
        )

        assert result.exit_code == 0, result.output
        report = data["audio_loudness"]
        assert report["measured"] is True
        assert report["window"]["from"]["seconds"] == 0.5
        assert report["window"]["to"]["seconds"] == 1.5
        assert {entry["id"] for entry in report["tracks"]} == {"source", "music"}
        assert report["final_mix"]["loudness"]["integrated_lufs"] < 0.0

    def test_watch_loudness_report_rejects_explicit_raw_source(
        self, runner, loaded_project, quiet_speech_video
    ):
        loaded_project(quiet_speech_video, frames=False)

        result, data = _invoke_json(
            runner,
            [
                "watch", "--source", "src_0", "--from", "0", "--to", "1",
                "--loudness-report",
            ],
        )

        assert result.exit_code == 1
        assert data["command"] == "watch"
        assert "--source" in data["error"]
        assert "authored mix" in data["hint"]

    def test_watch_help_documents_range_bounded_loudness_report(self, runner):
        result = runner.invoke(cli, ["watch", "--help"])
        assert result.exit_code == 0
        assert "--loudness-report" in result.output
        assert "--from/--to" in result.output
        assert "coverage relative to that window" in result.output
