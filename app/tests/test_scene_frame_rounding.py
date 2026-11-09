"""Scene boundaries must use authored time, rather than rounded clip lengths."""

import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import render_segments, run_ffprobe


SCENE_COUNT = 24
FPS = 24
SAMPLE_RATE = 48000


@pytest.fixture(scope="module", params=["pcm_s16le", "aac"])
def clock_source(tmp_path_factory, request):
    codec = request.param
    path = tmp_path_factory.mktemp(f"scene-clock-{codec}") / "clock.mov"
    # Each half second has a distinct tone and one of three solid colors.
    # Test both uncompressed audio and AAC's real seek/priming behavior.
    video = (
        "nullsrc=s=96x64:r=24:d=13,geq="
        "r='255*eq(mod(floor(T/0.5),3),0)':"
        "g='255*eq(mod(floor(T/0.5),3),1)':"
        "b='255*eq(mod(floor(T/0.5),3),2)'"
    )
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", video,
        "-f", "lavfi", "-i",
        "aevalsrc=0.3*sin(2*PI*(300+80*floor(t/0.5))*t):s=48000:d=13",
        "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-c:a", codec, str(path),
    ], check=True, capture_output=True)
    return path


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def author_scenes(source, duration, *, routed=True):
    invoke("load", str(source), "--as", "clock", "--no-transcribe", "--no-frames")
    args = ["scenes", "set", "--canvas", "96x64"]
    for i in range(SCENE_COUNT):
        args += [
            "--scene", f"s{i}=single", "--slot", f"s{i}:main=clock",
            "--from", str(i / 2), "--to", str(i / 2 + duration),
        ]
        if routed:
            args += ["--audio-from", f"s{i}=clock"]
    invoke(*args)


@pytest.mark.parametrize("duration", [0.11, 0.13])
@pytest.mark.parametrize("command", ["export", "preview", "watch", "partial-watch", "transitions"])
def test_many_fractional_scenes_preserve_video_clock_and_audio_boundaries(
    clock_source, tmp_path, monkeypatch, duration, command
):
    monkeypatch.chdir(tmp_path)
    author_scenes(clock_source, duration)
    if command == "transitions":
        for scene, transition in [("s8", "dissolve"), ("s9", "dissolve"), ("s17", "dip-white")]:
            invoke("scenes", "transition", "--before", scene, "--type", transition, "--duration", "0.08")
    total = round(SCENE_COUNT * duration, 6)
    start = .07 if command == "partial-watch" else 0
    end = total - start
    duration_out = round(end - start, 6)
    output = tmp_path / "result.mp4"
    if command in {"watch", "partial-watch"}:
        args = ["watch", "--from", str(start), "--to", str(end)]
    else:
        args = ["export", *(["--preview"] if command == "preview" else [])]
    before = Path("moviestar/spec.json").read_bytes()
    dry = invoke(*args, "--out", str(output), "--dry-run")
    assert not output.exists()
    receipt = invoke(*args, "--out", str(output))
    assert receipt["scene_render_commands"] == dry["scene_render_commands"]
    assert receipt["concat_command"] == dry["concat_command"]
    assert Path("moviestar/spec.json").read_bytes() == before
    probe = run_ffprobe(str(output))
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert int(video["nb_frames"]) == math.ceil(duration_out * FPS - 1e-6)
    assert float(video["duration"]) == pytest.approx(duration_out, abs=1 / FPS)
    assert float(audio["duration"]) == pytest.approx(duration_out, abs=1 / SAMPLE_RATE)

    decoded = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(output), "-an", "-vf", "scale=1:1",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ])
    colors = np.frombuffer(decoded, dtype=np.uint8).reshape(-1, 3)
    pcm = np.frombuffer(subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(output), "-vn",
        "-af", f"aresample=48000:async=1:first_pts=0,atrim=end={duration_out}",
        "-ac", "1", "-f", "f32le", "-",
    ]), dtype=np.float32)
    assert len(pcm) == round(duration_out * SAMPLE_RATE)
    for i in range(SCENE_COUNT):
        global_center = (i + 0.5) * duration
        if not start + .02 < global_center < end - .02:
            continue
        center = global_center - start
        frame = colors[round(center * FPS)]
        assert int(np.argmax(frame)) == i % 3, (i, center, frame)
        start, end = [round(t * SAMPLE_RATE) for t in (center - .02, center + .02)]
        window = pcm[start:end]
        assert np.sqrt(np.mean(window ** 2)) > .1, (i, "unexpected audio gap")
        spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window))))
        frequencies = np.fft.rfftfreq(len(window), 1 / SAMPLE_RATE)
        assert frequencies[np.argmax(spectrum)] == pytest.approx(300 + 80 * i, abs=26), i

    # Check actual frame PTS as well as duration metadata: no hidden join holes.
    frames = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "frame=pts_time", "-of", "json", str(output),
    ]))["frames"]
    times = [float(frame["pts_time"]) for frame in frames]
    assert times == pytest.approx([i / FPS for i in range(len(times))], abs=1e-5)


@pytest.mark.parametrize("rate", [30.0, 60.0, 30000 / 1001])
def test_other_output_cadences_round_only_the_complete_movie(clock_source, tmp_path, rate):
    total = SCENE_COUNT * .13
    output = tmp_path / "cadence.mp4"
    render_segments(
        [(str(clock_source), i / 2, i / 2 + .13) for i in range(SCENE_COUNT)],
        str(output), output_fps=rate, progress_cb=lambda line: None,
    )
    probe = run_ffprobe(str(output))
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert int(video["nb_frames"]) == math.ceil(total * rate - 1e-6)
    assert float(video["duration"]) == pytest.approx(total, abs=1 / rate)
    assert float(audio["duration"]) == pytest.approx(total, abs=1 / SAMPLE_RATE)


def test_silent_scenes_keep_authored_timing_without_creating_audio(clock_source, tmp_path, monkeypatch):
    source = tmp_path / "silent.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(clock_source),
        "-c:v", "copy", "-an", str(source),
    ], check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    author_scenes(source, .11, routed=False)
    output = tmp_path / "silent-result.mp4"
    invoke("export", "--out", str(output))
    probe = run_ffprobe(str(output))
    [video] = probe["streams"]
    assert video["codec_type"] == "video"
    assert int(video["nb_frames"]) == math.ceil(SCENE_COUNT * .11 * FPS - 1e-6)


def test_unrouted_windows_remain_silent_without_moving_routed_audio(clock_source, tmp_path, monkeypatch):
    silent = tmp_path / "silent.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(clock_source),
        "-c:v", "copy", "-an", str(silent),
    ], check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    invoke("load", str(clock_source), str(silent), "--as", "clock", "--as", "mute", "--no-transcribe", "--no-frames")
    args = ["scenes", "set", "--canvas", "96x64"]
    for i in range(SCENE_COUNT):
        source = "mute" if i % 2 else "clock"
        args += ["--scene", f"s{i}=single", "--slot", f"s{i}:main={source}",
                 "--from", str(i / 2), "--to", str(i / 2 + .13)]
        if not i % 2:
            args += ["--audio-from", f"s{i}=clock"]
    invoke(*args)
    output = tmp_path / "mixed-audio.mp4"
    receipt = invoke("export", "--out", str(output))
    assert "audio_dropped_note" in receipt
    pcm = np.frombuffer(subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(output), "-vn", "-ac", "1",
        "-ar", str(SAMPLE_RATE), "-f", "f32le", "-",
    ]), dtype=np.float32)
    for i in range(SCENE_COUNT):
        center = (i + .5) * .13
        start, end = [round(t * SAMPLE_RATE) for t in (center - .02, center + .02)]
        rms = float(np.sqrt(np.mean(pcm[start:end] ** 2)))
        assert (rms < .01) if i % 2 else (rms > .1), (i, rms)
