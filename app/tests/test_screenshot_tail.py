"""A tail screenshot must contain a real frame, never a stale/missing image."""

import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import extract_frame


@pytest.fixture(scope="module", params=["cfr", "vfr", "offset", "streaming-webm"])
def tail_video(request, tmp_path_factory):
    streaming = request.param == "streaming-webm"
    path = tmp_path_factory.mktemp("screenshot-tail") / ("source.webm" if streaming else "source.mp4")
    vfr = request.param == "vfr"
    command = [
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=s=160x90:r=25:d=2",
    ]
    if vfr:
        command += ["-vf", "select='eq(n,0)+eq(n,10)+eq(n,49)'", "-fps_mode", "vfr"]
    command += (["-c:v", "libvpx-vp9"] if streaming
                else ["-c:v", "libx264", "-g", "50", "-bf", "3"])
    if request.param == "offset":
        command += ["-output_ts_offset", "5"]
    if streaming:
        with path.open("wb") as output:
            subprocess.run(command + ["-f", "webm", "pipe:1"], stdout=output, check=True)
    else:
        subprocess.run(command + [str(path)], check=True)
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-fps_mode", "passthrough",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    return path, raw[-160 * 90 * 3:]


def _invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, (result.stdout, result.exception)
    return json.loads(result.stdout)


def _pixels(path):
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])


@pytest.mark.parametrize("mode", ["file", "project"])
@pytest.mark.parametrize("at", ["1.96", "1.999"])
def test_tail_frame_pixels_and_receipt(tail_video, tmp_path, monkeypatch, mode, at):
    path, expected = tail_video
    monkeypatch.chdir(tmp_path)
    args = ["screenshot", "--at", at, "--out", "last.png"]
    if mode == "file":
        args += ["--file", str(path)]
    else:
        _invoke("load", str(path), "--no-transcribe", "--no-frames")
        _invoke("trim", "--from", "0.5", "--to", "2")
        _invoke("cut", "--from", "0", "--to", "0.25")
        args[2] = str(float(at) - 0.75)
    receipt = _invoke(*args)
    assert _pixels(receipt["out"]) == expected
    if mode == "project":
        assert receipt["source_timecode"]["seconds"] == float(at)
        assert receipt["result_timecode"]["seconds"] == pytest.approx(float(at) - 0.75)
    else:
        assert receipt["timecode"]["seconds"] == float(at)


@pytest.mark.parametrize("mode", ["file", "project"])
@pytest.mark.parametrize("dry", [False, True])
@pytest.mark.parametrize("at", ["2", "2.001", "nan", "inf", "-0.001"])
def test_half_open_bounds_are_structured_and_preserve_output(
    tail_video, tmp_path, monkeypatch, mode, dry, at,
):
    path, _ = tail_video
    monkeypatch.chdir(tmp_path)
    if mode == "project":
        _invoke("load", str(path), "--no-transcribe", "--no-frames")
    out = tmp_path / "existing.png"
    out.write_bytes(b"prior proof")
    args = ["screenshot", "--at", at, "--out", str(out)]
    if mode == "file":
        args += ["--file", str(path)]
    if dry:
        args += ["--dry-run"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1
    assert json.loads(result.stdout)["command"] == "screenshot"
    assert out.read_bytes() == b"prior proof"


@pytest.mark.parametrize("mode", ["file", "project"])
def test_empty_extraction_is_structured_and_never_returns_stale_output(
    tail_video, tmp_path, monkeypatch, mode,
):
    from moviestar import ffmpeg
    path, _ = tail_video
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "existing.png"
    out.write_bytes(b"prior proof")
    if mode == "project":
        _invoke("load", str(path), "--no-transcribe", "--no-frames")
    real_run = ffmpeg.subprocess.run

    def no_image(command, **kwargs):
        if command[0] == "ffmpeg":
            return SimpleNamespace(returncode=0, stdout="", stderr="[info] Stream #0:0 -> #0:0 (h264 -> png)")
        return real_run(command, **kwargs)

    monkeypatch.setattr(ffmpeg.subprocess, "run", no_image)
    args = ["screenshot", "--at", "1.999", "--out", str(out)]
    if mode == "file":
        args += ["--file", str(path)]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1
    assert json.loads(result.stdout)["hint"]
    assert out.read_bytes() == b"prior proof"


def test_tail_fallback_keeps_overlay_clock_at_requested_result_time(
    tail_video, tmp_path, monkeypatch,
):
    path, _ = tail_video
    monkeypatch.chdir(tmp_path)
    _invoke("load", str(path), "--no-transcribe", "--no-frames")
    _invoke("trim", "--from", "1", "--to", "2")
    _invoke("overlays", "add", "--text", "END", "--from", "0.98", "--to", "1",
            "--css", "font-size: 20px; color: white", "--position", "center")
    shot = _invoke("screenshot", "--at", "0.999", "--out", "with-text.png")
    raw = _invoke("screenshot", "--file", str(path), "--at", "1.999", "--out", "raw.png")
    assert shot["overlays"]["active_count"] == 1
    assert _pixels(shot["out"]) != _pixels(raw["out"])
    assert shot["source_timecode"]["seconds"] == 1.999


@pytest.mark.parametrize("failure", ["empty", "partial", "invalid", "timestamp", "beyond", "timeout", "decoder"])
def test_incomplete_recovery_evidence_cannot_return_an_arbitrary_frame(monkeypatch, failure):
    from moviestar import ffmpeg
    monkeypatch.setattr(ffmpeg, "run_ffprobe", lambda _path: {
        "format": {"start_time": "5", "duration": "2"},
    })
    payload = {"streams": [{"nb_read_packets": "50"}], "frames": [
        {"best_effort_timestamp_time": "6.96"},
    ]}
    if failure == "empty":
        payload["frames"] = []
    elif failure == "partial":
        payload["streams"][0]["nb_read_packets"] = "10001"
    elif failure == "timestamp":
        payload["frames"][0]["best_effort_timestamp_time"] = "nan"
    elif failure == "beyond":
        payload["frames"][0]["best_effort_timestamp_time"] = "7"

    def probe(command, **kwargs):
        assert command[command.index("-read_intervals") + 1] == "6.999%+#10001"
        assert kwargs["timeout"] == 30
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        return SimpleNamespace(
            returncode=0, stderr="decode failed" if failure == "decoder" else "",
            stdout="not json" if failure == "invalid" else json.dumps(payload),
        )

    monkeypatch.setattr(ffmpeg.subprocess, "run", probe)
    with pytest.raises(ffmpeg.FrameNotProducedError):
        ffmpeg._preceding_frame_seek("source.mp4", 1.999)


def test_recovery_uses_presentation_times_and_origin_not_packet_order_or_fps(monkeypatch):
    from moviestar import ffmpeg
    monkeypatch.setattr(ffmpeg, "run_ffprobe", lambda _path: {
        "format": {"start_time": "5", "duration": "2"},
        "streams": [{"r_frame_rate": "1000/1"}],
    })
    monkeypatch.setattr(ffmpeg.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stderr="", stdout=json.dumps({
            "streams": [{"nb_read_packets": "3"}], "frames": [
                {"best_effort_timestamp_time": value} for value in ["6.96", "6.5", "5"]
            ],
        }),
    ))
    assert ffmpeg._preceding_frame_seek("source.mp4", 1.999) == pytest.approx(1.96)


def test_normal_seek_replaces_existing_output_and_cleans_backup(tail_video, tmp_path):
    path, _ = tail_video
    out = tmp_path / "proof.png"
    out.write_bytes(b"old proof")
    out.chmod(0o600)
    command = extract_frame(str(path), 0.5, str(out))
    assert out.read_bytes().startswith(b"\x89PNG")
    assert str(out) in command
    if os.name != "nt":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".moviestar-frame-*"))


def test_failed_retry_restores_existing_output(tail_video, tmp_path, monkeypatch):
    from moviestar import ffmpeg
    path, _ = tail_video
    out = tmp_path / "proof.png"
    out.write_bytes(b"old proof")
    monkeypatch.setattr(ffmpeg, "_preceding_frame_seek", lambda *args, **kwargs: 1.96)
    attempts = []

    def run(command, **kwargs):
        attempts.append(command)
        Path(command[-1]).write_bytes(b"" if len(attempts) == 1 else b"partial image")
        return SimpleNamespace(returncode=len(attempts) - 1, stderr=(
            "[info] Stream #0:0 -> #0:0 (h264 -> png)" if len(attempts) == 1 else "encoder failed"
        ))

    monkeypatch.setattr(ffmpeg.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="encoder failed"):
        extract_frame(str(path), 1.999, str(out))
    assert len(attempts) == 2
    assert out.read_bytes() == b"old proof"
    assert not list(tmp_path.glob(".moviestar-frame-*"))


def test_output_backup_permission_error_is_structured(tail_video, tmp_path, monkeypatch):
    path, _ = tail_video
    out = tmp_path / "proof.png"
    out.write_bytes(b"old proof")
    def denied(**kwargs):
        raise PermissionError("test output directory is not writable")
    monkeypatch.setattr("moviestar.ffmpeg.tempfile.mkstemp", denied)
    result = CliRunner().invoke(cli, [
        "screenshot", "--file", str(path), "--at", "0.5", "--out", str(out),
    ])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["hint"]
    assert out.read_bytes() == b"old proof"


def test_recovery_metadata_failure_is_an_unavailable_frame(monkeypatch):
    from moviestar import ffmpeg
    def unavailable(_path):
        raise RuntimeError("source metadata could not be read")
    monkeypatch.setattr(ffmpeg, "run_ffprobe", unavailable)
    with pytest.raises(ffmpeg.FrameNotProducedError, match="source metadata"):
        ffmpeg._preceding_frame_seek("source.mp4", 1.999)


def test_real_interleaved_audio_video_scan_cannot_hide_packet_limit(test_video, monkeypatch):
    from moviestar import ffmpeg
    monkeypatch.setattr(ffmpeg, "_FRAME_PROBE_PACKET_LIMIT", 1)
    with pytest.raises(ffmpeg.FrameNotProducedError, match="packet scan budget"):
        ffmpeg._preceding_frame_seek(test_video, 1.999)


@pytest.fixture(scope="module", params=["second-default", "first-default", "no-default", "equal-default"])
def multistream_video(request, tmp_path_factory):
    path = tmp_path_factory.mktemp("multistream-tail") / "source.mkv"
    first_size = "320x180" if request.param == "equal-default" else "160x90"
    default = "0" if request.param == "first-default" else "1"
    # Audio first makes absolute stream indexes differ from video ordinals.
    command = [
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=2",
        "-f", "lavfi", "-i", f"testsrc2=s={first_size}:r=1:d=2",
        "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=2",
        "-map", "0:a", "-map", "1:v", "-map", "2:v", "-c:a", "pcm_s16le",
        "-c:v", "ffv1", "-disposition:v:0", "0", "-disposition:v:1", "0",
    ]
    if request.param != "no-default":
        command += [f"-disposition:v:{default}", "default"]
    subprocess.run(command + [str(path)], check=True)
    expected_index = 1 if request.param == "first-default" else 2
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{expected_index}",
        "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    width, height = (160, 90) if expected_index == 1 else (320, 180)
    return path, expected_index, width, height, raw[-width * height * 3:]


@pytest.mark.parametrize("mode", ["file", "project"])
def test_multistream_recovery_keeps_the_automatically_selected_video(
    multistream_video, tmp_path, monkeypatch, mode,
):
    path, expected_index, width, height, expected = multistream_video
    monkeypatch.chdir(tmp_path)
    if mode == "project":
        _invoke("load", str(path), "--no-transcribe", "--no-frames")
    args = ["screenshot", "--at", "1.999", "--out", "recovered.png"]
    if mode == "file":
        args += ["--file", str(path)]
    # Independently check the normal automatic path on that stream's last PTS.
    last_time = "1" if expected_index == 1 else "1.96"
    normal = _invoke("screenshot", "--file", str(path), "--at", last_time, "--out", "normal.png")
    assert _pixels(normal["out"]) == expected
    recovered = _invoke(*args)
    assert (recovered["width"], recovered["height"]) == (width, height)
    assert _pixels(recovered["out"]) == expected
    command = recovered["ffmpeg_command"]
    assert command[command.index("-map") + 1] == f"0:{expected_index}"


def test_multistream_overlay_recovery_keeps_explicit_first_video(
    multistream_video, tmp_path, monkeypatch,
):
    path = multistream_video[0]
    monkeypatch.chdir(tmp_path)
    _invoke("load", str(path), "--no-transcribe", "--no-frames")
    source = json.loads((tmp_path / "moviestar/project.json").read_text())["sources"][0]
    width, height = source["width"], source["height"]
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:1",
        "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])[-width * height * 3:]
    _invoke("overlays", "add", "--text", "END", "--from", "1.98", "--to", "2",
            "--css", "font-size: 20px; color: white", "--position", "center")
    shot = _invoke("screenshot", "--at", "1.999", "--out", "overlay.png")
    image = _pixels(shot["out"])
    assert (shot["width"], shot["height"]) == (width, height)
    assert image[:width * 10 * 3] == raw[:width * 10 * 3]
    assert image != raw  # Requested-instant overlay is still present.
    assert shot["overlays"]["active_count"] == 1
    command = shot["ffmpeg_command"]
    assert "[0:v]setpts" in command[command.index("-filter_complex") + 1]
    assert command[command.index("-ss") + 1] == "1.0"


@pytest.mark.parametrize("trace", ["", "[info] Stream #0:1 -> #0:0\n[info] Stream #0:2 -> #0:0"])
def test_ambiguous_selection_trace_never_guesses_a_recovery_stream(tmp_path, monkeypatch, trace):
    from moviestar import ffmpeg
    source = tmp_path / "source.mp4"
    source.touch()
    out = tmp_path / "proof.png"
    out.write_bytes(b"old proof")
    monkeypatch.setattr(ffmpeg.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stderr=trace,
    ))
    def must_not_probe(*args, **kwargs):
        raise AssertionError("Do not guess a stream when the mapping is unknown")
    monkeypatch.setattr(ffmpeg, "_preceding_frame_seek", must_not_probe)
    with pytest.raises(ffmpeg.FrameNotProducedError, match="identify the video stream"):
        extract_frame(str(source), 1.999, str(out))
    assert out.read_bytes() == b"old proof"


def test_captured_selection_info_is_not_copied_into_error_receipts(tmp_path, monkeypatch):
    from moviestar import ffmpeg
    source = tmp_path / "source.mp4"
    source.touch()
    monkeypatch.setattr(ffmpeg.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stderr=(
            "[info] title: private input metadata [error] still private\n"
            "[encoder @ 0x1] [error] Encoder failed\n"
        ),
    ))
    with pytest.raises(RuntimeError, match="Encoder failed") as failure:
        extract_frame(str(source), 1.999, str(tmp_path / "proof.png"))
    assert "private" not in str(failure.value)


def test_selection_trace_ignores_stream_mapping_text_inside_metadata():
    from moviestar.ffmpeg import _selected_frame_stream
    assert _selected_frame_stream(
        "[info] title: Stream #0:9 -> #0:0\n"
        "[info] Stream mapping:\n"
        "[info]   Stream #0:2 -> #0:0 (ffv1 -> png)\n"
    ) == 2
