"""Recorder containers without duration metadata (issue #192)."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import run_ffprobe


@pytest.fixture(params=["regular", "irregular", "offset"])
def recorder(request, tmp_path):
    irregular = request.param != "regular"
    path = tmp_path / "recorder.webm"
    filters = (
        "drawbox=color=blue:t=fill:enable='gte(n,10)',"
        "settb=1/1000,setpts='if(lt(N,10),N*40,400+(N-10)*80)'"
        if irregular else "null"
    )
    cmd = [
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "color=c=red:s=160x90:r=1000:d=0.025" if irregular
        else "color=c=red:s=160x90:r=25:d=2",
        "-vf", filters, "-fps_mode", "passthrough",
        "-enc_time_base", "1:1000", "-c:v", "libvpx-vp9",
    ]
    if request.param == "offset":
        cmd += ["-output_ts_offset", "5"]
    with path.open("wb") as output:
        subprocess.run(cmd + ["-f", "webm", "pipe:1"], stdout=output, check=True)
    raw = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(path),
    ]))
    assert "duration" not in raw["format"]
    return path, (1.521 if irregular else 2.0), raw


def test_recorder_probe_load_and_last_frame(recorder, tmp_path, monkeypatch):
    path, expected, raw = recorder
    recovered = run_ffprobe(str(path))
    assert float(recovered["format"]["duration"]) == pytest.approx(expected)
    assert recovered["streams"] == raw["streams"]  # clocks/FPS stay authored
    evidence = recovered["format"]["duration_probe"]
    assert evidence["method"] == "packet_endpoints"
    assert evidence["origin_seconds"] == float(raw["format"]["start_time"])

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    args = ["load", str(path), "--no-transcribe", "--no-frames"]
    preview = runner.invoke(cli, args + ["--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert not (tmp_path / "moviestar").exists()
    applied = runner.invoke(cli, args)
    assert applied.exit_code == 0, applied.output
    stored = json.loads((tmp_path / "moviestar/project.json").read_text())
    source = stored["sources"][0]
    assert source["duration"]["seconds"] == pytest.approx(expected)
    assert source["duration_probe"] == evidence
    assert json.loads(applied.stdout)["sources"][0]["duration_probe"] == evidence
    status = runner.invoke(cli, ["status"])
    assert json.loads(status.stdout)["sources"][0]["duration_probe"] == evidence
    report = runner.invoke(cli, ["probe", str(path)])
    assert json.loads(report.stdout)["duration_probe"] == evidence
    assert json.loads(preview.stdout)["sources"][0]["duration_probe"] == evidence
    shot = tmp_path / "last.png"
    result = runner.invoke(cli, [
        "screenshot", "--at", str(1.96 if expected == 2.0 else 1.52), "--out", str(shot),
    ])
    assert result.exit_code == 0, result.output
    # Compare actual pixels with the independently decoded last source frame,
    # without using Moviestar's seek/filter planner as the oracle.
    frames = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path),
        "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    pixels = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(shot),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    assert pixels == frames[-160 * 90 * 3:]

    # Exercise the recovered range through actual trim/export, then inspect
    # delivered duration and pixels independently of the render receipt.
    trimmed = runner.invoke(cli, ["trim", "--from", "0.32", "--to", str(expected)])
    assert trimmed.exit_code == 0, trimmed.output
    out = tmp_path / "cut.mp4"
    exported = runner.invoke(cli, ["export", "--out", str(out)])
    assert exported.exit_code == 0, exported.output
    delivered = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(out),
    ]))
    assert float(delivered["format"]["duration"]) == pytest.approx(expected - 0.32, abs=0.041)
    video = next(stream for stream in delivered["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"]) == (160, 90)
    decoded = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(out),
        "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    first, last = decoded[:160 * 90 * 3], decoded[-160 * 90 * 3:]
    # Uniform red opening survives; the irregular fixtures end on blue.
    assert sum(first[0::3]) / (160 * 90) > 240
    if expected < 2:
        assert sum(last[2::3]) / (160 * 90) > 240
    else:
        assert sum(last[0::3]) / (160 * 90) > 240


def _mock_probe(monkeypatch, tmp_path, packet_lines, *, duration=None, origin="5"):
    path = tmp_path / "source.webm"
    path.touch()
    calls = []
    metadata = {"format": {"start_time": origin, "format_name": "matroska,webm"}, "streams": [
        {"codec_type": "video", "index": 0, "r_frame_rate": "1000/1"},
    ]}
    if duration is not None:
        metadata["format"]["duration"] = duration

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if "-show_packets" not in cmd:
            return SimpleNamespace(returncode=0, stdout=json.dumps(metadata))
        kwargs["stdout"].write(packet_lines)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("moviestar.ffmpeg.subprocess.run", run)
    return path, calls


@pytest.mark.parametrize("duration", [None, "N/A", "0", "-1", "nan", "inf"])
def test_invalid_duration_uses_endpoints_not_frame_rate(monkeypatch, tmp_path, duration):
    path, calls = _mock_probe(monkeypatch, tmp_path, (
        "codec_type=video|stream_index=0|pts_time=5|duration_time=0.04\n"
        # Deliberate gap and nonmonotonic packet ordering (B frames).
        "codec_type=video|stream_index=0|pts_time=7|duration_time=0.08\n"
        "codec_type=video|stream_index=0|pts_time=6|duration_time=0.04\n"
        "codec_type=audio|stream_index=1|pts_time=7.1|duration_time=0.02\n"
    ), duration=duration)
    data = run_ffprobe(str(path))
    assert float(data["format"]["duration"]) == pytest.approx(2.12)
    assert data["format"]["start_time"] == "5"
    assert calls[1][1]["timeout"] == 60
    assert calls[1][0][calls[1][0].index("-read_intervals") + 1] == "%+#1000001"


def test_existing_positive_duration_does_not_scan(monkeypatch, tmp_path):
    path, calls = _mock_probe(monkeypatch, tmp_path, "", duration="2.5")
    data = run_ffprobe(str(path))
    assert data["format"]["duration"] == "2.5"
    assert "duration_probe" not in data["format"]
    assert len(calls) == 1


def test_missing_origin_uses_earliest_packet_including_negative_audio(monkeypatch, tmp_path):
    path, _ = _mock_probe(monkeypatch, tmp_path, (
        "codec_type=video|stream_index=0|pts_time=0|duration_time=2\n"
        "codec_type=audio|stream_index=1|pts_time=-0.007|duration_time=0.02\n"
    ), origin="N/A")
    assert float(run_ffprobe(str(path))["format"]["duration"]) == pytest.approx(2.007)


@pytest.mark.parametrize("packets", [
    "", "codec_type=video|stream_index=0|pts_time=N/A|duration_time=0.04\n",
    "codec_type=video|stream_index=0|pts_time=0|duration_time=N/A\n",
    "codec_type=video|stream_index=0|pts_time=0|duration_time=0.04\n"
    "codec_type=video|stream_index=0|pts_time=1|duration_time=N/A\n",
    "codec_type=video|stream_index=0|pts_time=5|duration_time=nan\n",
    "codec_type=video|stream_index=0|pts_time=5|duration_time=3\n"
    "codec_type=video|stream_index=0|pts_time=6|duration_time=N/A\n",
])
def test_unknown_endpoints_fail_instead_of_guessing(monkeypatch, tmp_path, packets):
    path, _ = _mock_probe(monkeypatch, tmp_path, packets)
    with pytest.raises(RuntimeError, match="Remux the source"):
        run_ffprobe(str(path))


def test_packet_limit_never_returns_partial_duration(monkeypatch, tmp_path):
    monkeypatch.setattr("moviestar.ffmpeg._DURATION_PACKET_LIMIT", 1)
    path, _ = _mock_probe(monkeypatch, tmp_path, (
        "codec_type=video|stream_index=0|pts_time=5|duration_time=0.04\n" * 2
    ))
    with pytest.raises(RuntimeError, match="limit exceeded"):
        run_ffprobe(str(path))


def test_timed_out_scan_fails_actionably(monkeypatch, tmp_path):
    path, _ = _mock_probe(monkeypatch, tmp_path, "")
    from moviestar import ffmpeg
    prior = ffmpeg.subprocess.run

    def timeout(cmd, **kwargs):
        if "-show_packets" in cmd:
            raise subprocess.TimeoutExpired(cmd, 60)
        return prior(cmd, **kwargs)

    monkeypatch.setattr(ffmpeg.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="Packet scan timed out"):
        run_ffprobe(str(path))


def test_force_preserves_old_workspace_when_duration_recovery_fails(
    test_video, tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    args = ["load", test_video, "--no-transcribe", "--no-frames"]
    assert runner.invoke(cli, args).exit_code == 0
    project = tmp_path / "moviestar/project.json"
    before = project.read_bytes()

    def fail(path):
        raise RuntimeError("Could not recover media duration. Packet scan timed out.")

    monkeypatch.setattr("moviestar.cli.run_ffprobe", fail)
    result = runner.invoke(cli, args + ["--force"])
    assert result.exit_code == 1
    assert "timed out" in json.loads(result.stdout)["error"]
    assert project.read_bytes() == before


@pytest.mark.parametrize("code,stderr", [(1, "demux failed"), (0, "corrupt packet")])
def test_failed_or_incomplete_demux_does_not_supply_a_duration(monkeypatch, tmp_path, code, stderr):
    path, _ = _mock_probe(monkeypatch, tmp_path, "")
    from moviestar import ffmpeg
    prior = ffmpeg.subprocess.run

    def failed(cmd, **kwargs):
        if "-show_packets" in cmd:
            kwargs["stdout"].write("codec_type=video|stream_index=0|pts_time=5|duration_time=1\n")
            return SimpleNamespace(returncode=code, stderr=stderr)
        return prior(cmd, **kwargs)

    monkeypatch.setattr(ffmpeg.subprocess, "run", failed)
    with pytest.raises(RuntimeError, match=stderr):
        run_ffprobe(str(path))


def test_timeless_image_metadata_is_not_given_a_recorder_duration(tmp_path):
    image = tmp_path / "still.png"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=16x16",
        "-frames:v", "1", "-threads", "1", str(image),
    ], check=True)
    raw = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(image),
    ]))
    assert run_ffprobe(str(image)) == raw
