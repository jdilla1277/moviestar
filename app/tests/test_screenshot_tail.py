"""A tail screenshot must contain a real frame, never a stale/missing image."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import extract_frame


@pytest.fixture(scope="module", params=["cfr", "vfr", "offset"])
def tail_video(request, tmp_path_factory):
    path = tmp_path_factory.mktemp("screenshot-tail") / "source.mp4"
    vfr = request.param == "vfr"
    command = [
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=s=160x90:r=25:d=2",
    ]
    if vfr:
        command += ["-vf", "select='eq(n,0)+eq(n,10)+eq(n,49)'", "-fps_mode", "vfr"]
    command += ["-c:v", "libx264", "-g", "50", "-bf", "3"]
    if request.param == "offset":
        command += ["-output_ts_offset", "5"]
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


def test_empty_extraction_is_structured_and_never_returns_stale_output(
    tail_video, tmp_path, monkeypatch,
):
    from moviestar import ffmpeg
    path, _ = tail_video
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "existing.png"
    out.write_bytes(b"prior proof")
    real_run = ffmpeg.subprocess.run

    def no_image(command, **kwargs):
        if command[0] == "ffmpeg":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real_run(command, **kwargs)

    monkeypatch.setattr(ffmpeg.subprocess, "run", no_image)
    result = CliRunner().invoke(cli, [
        "screenshot", "--file", str(path), "--at", "1.999", "--out", str(out),
    ])
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
    command = extract_frame(str(path), 0.5, str(out))
    assert out.read_bytes().startswith(b"\x89PNG")
    assert str(out) in command
    assert not list(tmp_path.glob(".moviestar-frame-*"))


def test_failed_retry_restores_existing_output(tail_video, tmp_path, monkeypatch):
    from moviestar import ffmpeg
    path, _ = tail_video
    out = tmp_path / "proof.png"
    out.write_bytes(b"old proof")
    monkeypatch.setattr(ffmpeg, "_preceding_frame_seek", lambda *_: 1.96)
    attempts = []

    def run(command, **kwargs):
        attempts.append(command)
        Path(command[-1]).write_bytes(b"")
        return SimpleNamespace(returncode=len(attempts) - 1, stderr="encoder failed")

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
