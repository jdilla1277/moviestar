"""Decoded-source regressions for delayed external audio (issue #95)."""

import json
import subprocess

import numpy as np
import pytest
from click.testing import CliRunner

from moviestar.audio_surface import default_audio_mix, resolve_audio_mix
from moviestar.cli import _audio_probe_file, cli
from moviestar.ffmpeg import mix_audio, normalize_loudness


SAMPLE_RATE = 16_000


def _decode_audio(path):
    pcm = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-",
    ])
    return np.frombuffer(pcm, dtype="<f4").astype(np.float64)


@pytest.fixture(scope="module")
def placement_media(tmp_path_factory):
    """Generated, band-limited noise cannot match a different source passage."""
    directory = tmp_path_factory.mktemp("audio-placement")
    wav = directory / "source.wav"
    video = directory / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "anoisesrc=r=44100:d=40:a=0.3:seed=95,lowpass=f=2000,"
        "volume='0.6+0.3*sin(2*PI*0.7*t)':eval=frame",
        "-c:a", "pcm_s16le", str(wav),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=black:s=160x120:r=10:d=40", "-i", str(wav),
        "-c:v", "mpeg4", "-q:v", "12", "-c:a", "aac", "-b:a", "128k",
        "-shortest", str(video),
    ], check=True)
    return {"wav": wav, "mp4": video}


def _tracks(path, *, reordered=False):
    starts = (16, 0) if reordered else (0, 16)
    return [
        {
            "id": identity, "kind": "voiceover", "path": str(path),
            "at": str(at), "source_from": str(start),
            "source_to": str(start + 16), "gain_db": 0.0, "muted": False,
            "loop": False, "fade_in": 0.0, "fade_out": 0.0, "ducking": None,
        }
        for identity, at, start in zip(("first", "second"), (0, 16), starts)
    ]


def _assert_source_window(actual, source, result_at, *, window_start=0, reordered=False):
    """Check content and <=1ms lag; energy alone cannot prove placement."""
    source_at = result_at
    if reordered:
        source_at = result_at + 16 if result_at < 16 else result_at - 16
    at = round((result_at - window_start) * SAMPLE_RATE)
    reference_at = round(source_at * SAMPLE_RATE)
    length = SAMPLE_RATE // 4
    reference = source[reference_at:reference_at + length]
    reference = reference - reference.mean()
    reference_energy = np.dot(reference, reference)
    assert reference_energy > 0.01, "generated reference must contain signal"
    assert len(actual[at:at + length]) == length, "export ended before this window"
    assert np.linalg.norm(actual[at:at + length]) > 0.01, (
        f"missing external audio at result {result_at}s"
    )

    matches = []
    for lag in range(-48, 49):  # search +/-3ms, then enforce <=1ms
        segment = actual[at + lag:at + lag + length]
        if len(segment) != length:
            continue
        segment = segment - segment.mean()
        denominator = np.sqrt(reference_energy * np.dot(segment, segment))
        correlation = np.dot(reference, segment) / denominator if denominator else 0
        matches.append((correlation, lag))
    correlation, lag = max(matches)
    assert correlation > 0.97, (
        f"wrong source audio at result {result_at}s: correlation={correlation:.4f}"
    )
    assert abs(lag) <= 16, f"external audio shifted by {lag / SAMPLE_RATE}s"


@pytest.mark.parametrize("source_format", ["wav", "mp4"])
@pytest.mark.parametrize("normalized", [False, True])
def test_sequential_external_audio_preserves_source_timing(
    placement_media, tmp_path, source_format, normalized
):
    path = placement_media[source_format]
    intent = default_audio_mix()
    intent["source_audio"]["muted"] = True
    intent["tracks"] = _tracks(path)
    mix = resolve_audio_mix(intent, result_duration=32, base_dir=tmp_path,
                            probe_audio=_audio_probe_file)
    output = tmp_path / "mixed.mp4"
    mix_audio(str(placement_media["mp4"]), str(output), mix, 32,
              source_audio_available=True)
    if normalized:
        normalize_loudness(str(output), -20)

    actual = _decode_audio(output)
    source = _decode_audio(path)
    # Late signal is the original failure; also cover opening, both sides of
    # the join, and ending. AAC re-encoding/normalization must preserve content.
    for at in (18, 9, 27, 0.5, 15.5, 16.25, 31.5):
        _assert_source_window(actual, source, at)


@pytest.mark.parametrize("start,end", [(8, 24), (18, 30), (0, 12), (15.75, 17.25)])
def test_windowed_mix_keeps_delays_and_source_offsets(
    placement_media, tmp_path, start, end
):
    intent = default_audio_mix()
    intent["source_audio"]["muted"] = True
    intent["tracks"] = _tracks(placement_media["wav"])
    mix = resolve_audio_mix(intent, result_duration=32, base_dir=tmp_path,
                            probe_audio=_audio_probe_file)
    output = tmp_path / "window.mp4"
    mix_audio(str(placement_media["mp4"]), str(output), mix, 32,
              source_audio_available=True, window_start=start,
              window_duration=end - start)
    actual = _decode_audio(output)
    source = _decode_audio(placement_media["wav"])
    for at in (start + 0.5, end - 0.5):
        _assert_source_window(actual, source, at, window_start=start)


@pytest.mark.parametrize("command", ["export", "watch"])
def test_cli_renders_the_authored_external_audio(
    placement_media, loaded_project, tmp_path, command
):
    loaded_project(placement_media["mp4"], frames=False)
    runner = CliRunner()

    def invoke(args):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    invoke(["trim", "--from", "0", "--to", "32"])
    invoke(["audio", "source", "--mute"])
    # Reordering also ensures accidentally retained source audio cannot mask
    # a failure to place the external tracks in the CLI paths.
    for track in _tracks(placement_media["mp4"], reordered=True):
        invoke([
            "audio", "add", track["path"], "--as", track["id"],
            "--kind", "voiceover", "--at", track["at"],
            "--from", track["source_from"], "--to", track["source_to"],
        ])
    spec = tmp_path / "moviestar" / "spec.json"
    before = spec.read_bytes()
    output = tmp_path / "rendered.mp4"
    args = [command, "--out", str(output)]
    if command == "export":
        args += ["--loudness-target", "-20"]
        start, samples = 0, (18, 9, 27)
    else:
        args += ["--from", "8", "--to", "24", "--precise"]
        start, samples = 8, (9, 18, 23)
    invoke(args)
    assert spec.read_bytes() == before
    actual = _decode_audio(output)
    source = _decode_audio(placement_media["mp4"])
    for at in samples:
        _assert_source_window(actual, source, at, window_start=start, reordered=True)
