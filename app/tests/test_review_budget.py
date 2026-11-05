"""Byte limits are checked against complete, playable review files."""

import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli


def invoke(*args, expected=0):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == expected, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("command", ["export", "watch"])
@pytest.mark.parametrize("limit", ["0", "-1", "1.5", "20MB"])
def test_byte_limit_rejects_invalid_values(test_video, loaded_project, command, limit):
    loaded_project(test_video, frames=False)
    args = ["--from", "0", "--to", "1"] if command == "watch" else []
    data = invoke(command, *args, "--review", "--max-bytes", limit, expected=2)
    assert data["error"]


@pytest.mark.parametrize("command", ["export", "watch"])
def test_byte_limit_requires_review_and_dry_run_never_claims_compliance(
    test_video, loaded_project, tmp_path, command
):
    loaded_project(test_video, frames=False)
    args = ["--from", "0", "--to", "1"] if command == "watch" else []
    invalid = invoke(command, *args, "--max-bytes", "100000", "--dry-run", expected=1)
    assert "--review" in invalid["error"]
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    data = invoke(command, *args, "--review", "--max-bytes", "100000", "--dry-run")
    budget = data["size_budget"]
    assert budget["max_bytes"] == 100000
    assert budget["met"] is None and budget["actual_bytes"] is None
    assert budget["max_retries"] == 2
    assert budget["attempts"] == []
    assert budget["minimum_video_bitrate_bps"] > 0
    assert budget["audio"] == "copy_final_encoded_audio"
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("command", ["export", "watch"])
def test_already_fitting_review_is_not_reencoded(test_video, loaded_project, tmp_path, command):
    loaded_project(test_video, frames=False)
    args = ["--from", "0", "--to", "1.5"] if command == "watch" else []
    out = tmp_path / "fits.mp4"
    data = invoke(command, *args, "--review", "--max-bytes", "10000000", "--out", str(out))
    budget = data["size_budget"]
    assert budget["met"] is True
    assert budget["actual_bytes"] == data["file_size_bytes"] == out.stat().st_size
    assert len(budget["attempts"]) == 1
    assert data["render_profile"]["video_encoding"]["crf"] == 23


def test_impossible_limit_keeps_complete_video_and_returns_actionable_failure(
    test_video, loaded_project, tmp_path
):
    loaded_project(test_video, frames=False)
    spec = tmp_path / "moviestar" / "spec.json"
    before = spec.read_bytes() if spec.exists() else None
    out = tmp_path / "too-big.mp4"
    data = invoke("export", "--review", "--max-bytes", "1", "--out", str(out), expected=1)
    assert data["status"] == "size_limit_unmet"
    assert data["size_budget"]["met"] is False
    assert data["size_budget"]["actual_bytes"] == out.stat().st_size > 1
    assert "shorter" in data["hint"].lower()
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(out),
    ]))
    assert float(probe["format"]["duration"]) == pytest.approx(2, abs=.08)
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert (spec.read_bytes() if spec.exists() else None) == before


@pytest.fixture(scope="session")
def complex_review_source(tmp_path_factory):
    out = tmp_path_factory.mktemp("byte-budget") / "busy.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=s=1280x720:r=30:d=3", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=3", "-c:v", "ffv1", "-c:a", "pcm_s16le",
        "-shortest", str(out),
    ], check=True)
    return out


def test_retry_fits_final_mux_and_preserves_audio_geometry_and_original(
    complex_review_source, loaded_project, tmp_path
):
    loaded_project(complex_review_source, frames=False)
    master = tmp_path / "master.mp4"
    original = invoke("export", "--review", "--loudness-target", "-18", "--out", str(master))
    before = master.read_bytes()
    limit = int(master.stat().st_size * .65)
    out = tmp_path / "budget.mp4"
    data = invoke("export", "--review", "--loudness-target", "-18",
                  "--max-bytes", str(limit), "--out", str(out), "--review-report")
    budget = data["size_budget"]
    assert budget["met"] is True and budget["actual_bytes"] == out.stat().st_size <= limit
    assert 2 <= len(budget["attempts"]) <= 3
    assert data["render_profile"]["actual_dimensions"] == {"width": 1280, "height": 720}
    assert data["render_profile"]["video_encoding"]["rate_control"] == "two_pass"
    assert data["loudness_normalization"]["measured"] == original["loudness_normalization"]["measured"]
    def audio(path):
        return subprocess.check_output([
            "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy",
            "-f", "adts", "-",
        ])
    assert audio(out) == audio(master)
    assert master.read_bytes() == before
    report = json.loads(Path(data["review_report"]["out"]).read_text())
    assert report["artifact"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert report["artifact"]["size_bytes"] == out.stat().st_size
    assert report["render_facts"]["size_budget"]["met"] is True
    assert not list(tmp_path.glob(".moviestar-budget-*"))


def test_retry_command_keeps_selected_encoder_and_audio():
    from moviestar.review_budget import build_budget_commands

    for encoder in ("x264", "videotoolbox"):
        commands, settings = build_budget_commands("original.mp4", "candidate.mp4", encoder, 500000, "pass-log")
        assert len(commands) == (2 if encoder == "x264" else 1)
        final = commands[-1]
        assert final[final.index("-c:a") + 1] == "copy"
        assert final[final.index("-b:v") + 1] == "500000"
        assert "-fs" not in final and "-vf" not in final
        assert settings["encoder"] == encoder
        assert settings["target_bitrate_bps"] == 500000
        assert final[final.index("-c:v") + 1] == settings["codec"]


def test_budget_retry_handles_a_source_with_no_audio(
    complex_review_source, loaded_project, tmp_path
):
    silent = tmp_path / "silent.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(complex_review_source),
                    "-c:v", "copy", "-an", str(silent)], check=True)
    loaded_project(silent, frames=False)
    master = tmp_path / "silent-original.mp4"
    invoke("export", "--review", "--out", str(master))
    limit = int(master.stat().st_size * .65)
    out = tmp_path / "silent-budget.mp4"
    data = invoke("export", "--review", "--max-bytes", str(limit), "--out", str(out))
    assert data["size_budget"]["met"] is True
    assert data["size_budget"]["audio_payload_bytes"] == 0
    streams = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out),
    ]))["streams"]
    assert len(streams) == 1 and streams[0]["codec_type"] == "video"


@pytest.mark.parametrize("sizes,break_retry,expected_size,reason", [
    ([350000, 320000], None, 320000, "retry_limit"),
    ([350000], 2, 350000, "retry_failed"),
    ([450000, 430000], None, 400000, "retry_limit"),
])
def test_bounded_retries_always_read_original_and_keep_smallest_verified_file(
    tmp_path, monkeypatch, sizes, break_retry, expected_size, reason
):
    from moviestar import review_budget as budget_module

    output = tmp_path / "original.mp4"
    output.write_bytes(b"x" * 400000)
    media = {"streams": [{"codec_type": "video", "width": 1280, "height": 720,
                          "nb_frames": "60"}], "format": {"duration": "2"}}
    monkeypatch.setattr(budget_module, "_media_info", lambda path: (media, media["streams"][0]))
    final_encodes = []

    def run(command):
        if command[0] == "ffprobe":
            return ""
        assert command[command.index("-i") + 1] == str(output)
        assert output.read_bytes() == b"x" * 400000
        if command[-1].endswith(".mp4"):
            final_encodes.append(command)
            if len(final_encodes) == break_retry:
                raise RuntimeError("Encoder unavailable")
            Path(command[-1]).write_bytes(b"y" * sizes[len(final_encodes) - 1])
        return ""

    monkeypatch.setattr(budget_module, "_run", run)
    receipt, settings = budget_module.fit_review_budget(str(output), "x264", 300000)
    assert len(final_encodes) == 2
    assert receipt["met"] is False
    assert receipt["actual_bytes"] == output.stat().st_size == expected_size
    assert receipt["stop_reason"] == reason
    assert sum(attempt["selected"] for attempt in receipt["attempts"]) == 1
    assert (settings.get("crf") == 23) == (expected_size == 400000)
    assert not list(tmp_path.glob(".moviestar-budget-*"))


@pytest.mark.parametrize("defect", ["dimensions", "duration", "audio", "frames"])
def test_incomplete_or_altered_candidate_never_replaces_original(tmp_path, monkeypatch, defect):
    from moviestar import review_budget as budget_module

    output = tmp_path / "original.mp4"
    before = b"x" * 400000
    output.write_bytes(before)
    video = {"codec_type": "video", "width": 1280, "height": 720, "nb_frames": "60"}
    original = {"streams": [video, {"codec_type": "audio"}], "format": {"duration": "2"}}
    changed = json.loads(json.dumps(original))
    if defect == "dimensions":
        changed["streams"][0]["width"] = 640
    elif defect == "duration":
        changed["format"]["duration"] = "1"
    elif defect == "frames":
        changed["streams"][0]["nb_frames"] = "59"
    else:
        changed["streams"].pop()
    monkeypatch.setattr(budget_module, "_media_info", lambda path:
                        (original, video) if path == str(output) else (changed, changed["streams"][0]))

    def run(command):
        if command[0] == "ffprobe":
            return "100\n"
        if command[-1].endswith(".mp4"):
            Path(command[-1]).write_bytes(b"small")
        return ""

    monkeypatch.setattr(budget_module, "_run", run)
    receipt, _ = budget_module.fit_review_budget(str(output), "x264", 300000)
    assert receipt["met"] is False
    assert receipt["stop_reason"] == "retry_failed"
    assert receipt["attempts"][-1]["error"]
    assert output.read_bytes() == before


@pytest.mark.parametrize("encoder", ["x264", "videotoolbox"])
def test_silent_scene_watch_budget_preserves_text_and_result_window(
    complex_review_source, loaded_project, tmp_path, encoder
):
    import sys
    if encoder == "videotoolbox" and sys.platform != "darwin":
        pytest.skip("VideoToolbox requires macOS")
    loaded_project(complex_review_source, frames=False)
    invoke("scenes", "set", "--canvas", "1280x720", "--scene", "one=single",
           "--slot", "one:main=src_0", "--from", "0", "--to", "3")
    invoke("audio", "source", "--mute")
    invoke("overlays", "add", "--text", "Keep this review text in place",
           "--from", "0", "--to", "3", "--position", "center", "--style", "caption-default",
           "--css", "font-size: 72px; max-width: 60%")
    spec = tmp_path / "moviestar" / "spec.json"
    before = spec.read_bytes()
    master = tmp_path / "watch-original.mp4"
    original = invoke("watch", "--from", ".3", "--to", "2.7", "--review",
                      "--video-encoder", encoder, "--out", str(master))
    limit = int(master.stat().st_size * .70)
    out = tmp_path / "watch-budget.mp4"
    data = invoke("watch", "--from", ".3", "--to", "2.7", "--review", "--max-bytes", str(limit),
                  "--video-encoder", encoder, "--out", str(out))
    assert data["size_budget"]["met"] is True
    assert out.stat().st_size <= limit
    assert data["render_profile"]["video_encoding"]["encoder"] == encoder
    assert data["overlays"] == original["overlays"]
    assert data["range"] == original["range"]
    assert spec.read_bytes() == before
