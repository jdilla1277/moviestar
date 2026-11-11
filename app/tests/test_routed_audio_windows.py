"""Render cut-aware routed audio, rather than only inspecting FFmpeg argv."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import render_layout_video, render_segments, run_ffprobe


RATE = 48000


@pytest.fixture(scope="module", params=["pcm_s16le", "aac"])
def tone_source(tmp_path_factory, request):
    path = tmp_path_factory.mktemp(f"routed-{request.param}") / "clock.mov"
    # Distinct tones identify each source half second, including skipped ranges.
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=red:s=96x64:r=24:d=3", "-f", "lavfi", "-i",
        "aevalsrc=0.3*sin(2*PI*(400+200*floor(t/0.5))*t):s=48000:d=3",
        "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-c:a", request.param,
        str(path),
    ], check=True, capture_output=True, timeout=30)
    return path


def assert_audio_windows(output, windows):
    """Check duration and tones on the authored clock, including join edges."""
    duration = sum(end - start for start, end in windows)
    probe = run_ffprobe(str(output))
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert float(video["duration"]) == pytest.approx(duration, abs=1 / 24)
    assert float(audio["duration"]) == pytest.approx(duration, abs=1 / RATE)
    pcm = np.frombuffer(subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(output), "-vn", "-ac", "1",
        "-af", f"aresample={RATE}:async=1:first_pts=0,atrim=end={duration}",
        "-f", "f32le", "-",
    ], timeout=30), dtype=np.float32)
    assert len(pcm) == round(duration * RATE)
    offset = 0.0
    for start, end in windows:
        # Check every 20ms, including the first and last playable samples.
        for local in np.arange(0, end - start - .019, .02):
            sample_start = round((offset + local) * RATE)
            block = pcm[sample_start:sample_start + 960]
            assert np.sqrt(np.mean(block ** 2)) > .1, (offset, local, "gap")
            spectrum = np.abs(np.fft.rfft(block * np.hanning(len(block))))
            frequency = np.fft.rfftfreq(len(block), 1 / RATE)[np.argmax(spectrum)]
            expected = 400 + 200 * int((start + local + .01) / .5)
            assert frequency == pytest.approx(expected, abs=51), (offset, local)
        offset += end - start


@pytest.mark.parametrize("renderer", ["layout", "segments", "segments-cfr"])
@pytest.mark.parametrize("windows", [
    [(0.0, 1.0)],
    # Nonchronological order catches accidental sorting and input-index errors;
    # the zero-time middle window also exercises AAC priming handling.
    [(2.0, 2.25), (0.0, .25), (1.0, 1.5)],
], ids=["single", "multiple"])
def test_render_routed_windows(tone_source, tmp_path, renderer, windows):
    output = tmp_path / "routed.mp4"
    route = (str(tone_source), windows)
    if renderer == "layout":
        render_layout_video([{
            "path": str(tone_source), "source_from": 0.0, "source_to": 1.0,
            "region": {"x": 0, "y": 0, "width": 96, "height": 64},
            "framing": {"mode": "fill", "anchor": "center"},
        }], str(output), (96, 64), 1.0, audio_from_input=route, output_fps=24)
    else:
        render_segments([
            (str(tone_source), 0.0, .25), (str(tone_source), 1.0, 1.75),
        ], str(output), audio_from_input=route,
            output_fps=24 if renderer == "segments-cfr" else None)
    assert_audio_windows(output, windows)


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("command", ["export", "watch"])
def test_cli_audio_cut_inside_continuous_video(tone_source, tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    invoke("load", str(tone_source), str(tone_source), "--as", "voice",
           "--as", "camera", "--no-transcribe", "--no-frames")
    invoke("cut", "--source", "voice", "--from", ".5", "--to", "1")
    # The audio cut at movie time .5 lies inside the camera's continuous window.
    invoke("concat", "--segment", "voice", "--from", "0", "--to", ".25",
           "--segment", "camera", "--from", "0", "--to", ".75",
           "--audio-from", "voice")
    output = tmp_path / "result.mp4"
    args = [command]
    if command == "watch":
        args += ["--from", ".25", "--to", ".85"]
    spec_before = Path("moviestar/spec.json").read_bytes()
    dry = invoke(*args, "--out", str(output), "--dry-run")
    assert not output.exists()
    receipt = invoke(*args, "--out", str(output))
    assert receipt["scene_render_commands"] == dry["scene_render_commands"]
    assert receipt["concat_command"] == dry["concat_command"]
    assert any("concat=n=2:v=0:a=1" in entry["command"][
        entry["command"].index("-filter_complex") + 1
    ] for entry in receipt["scene_render_commands"])
    assert Path("moviestar/spec.json").read_bytes() == spec_before
    windows = [(0.0, .5), (1.0, 1.5)] if command == "export" else [(.25, .5), (1.0, 1.35)]
    assert_audio_windows(output, windows)
