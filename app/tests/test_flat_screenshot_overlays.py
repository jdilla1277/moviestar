"""Flat screenshot receipts and pixels must include the stored text being reviewed."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from moviestar.cli import cli


@pytest.fixture(scope="module")
def source_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("flat-screenshots") / "colors.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=red:s=320x240:r=30:d=6", "-vf",
        "drawbox=color=blue:t=fill:enable='between(t,2,4)',"
        "drawbox=color=green:t=fill:enable='gte(t,4)'",
        "-c:v", "mpeg4", str(path),
    ], check=True, capture_output=True)
    return path


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def pixels(path, at=None):
    seek = ["-ss", str(at)] if at is not None else []
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", *seek, "-i", str(path), "-frames:v", "1",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ])
    return np.frombuffer(raw, dtype=np.uint8).reshape(240, 320, 3).astype(int)


def add_title(start="0.25", end="0.5"):
    return invoke(
        "overlays", "add", "--text", "Frame proof", "--from", start, "--to", end,
        "--x", "0.5", "--y", "0.25", "--css",
        "font-size: 30px; color: white; background-color: transparent",
    )


@pytest.mark.parametrize("at,active", [("0.25", 1), ("0.499", 1), ("0.5", 0), ("0.501", 1), ("0.75", 0)])
def test_flat_screenshot_selects_exact_instant_and_burns_manual_text(
    source_video, loaded_project, tmp_path, at, active
):
    loaded_project(source_video, frames=False)
    add_title()
    add_title("0.501", "0.75")
    spec_path = tmp_path / "moviestar/spec.json"
    before = spec_path.read_bytes()
    out = tmp_path / "project.png"
    shot = invoke("screenshot", "--at", at, "--out", str(out))
    raw = tmp_path / "raw.png"
    invoke("screenshot", "--file", str(source_video), "--at", at, "--out", str(raw))
    assert shot["mode"] == "project"
    assert shot["overlays"]["burned_in"] is True
    assert shot["overlays"]["active_count"] == active
    assert (shot["width"], shot["height"]) == (320, 240)
    delta = np.abs(pixels(out) - pixels(raw))
    assert (delta.max() > 100) == bool(active)
    assert spec_path.read_bytes() == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_generated_highlight_captions_use_result_time_after_trim_and_cut(
    source_video, loaded_project, tmp_path, dry_run
):
    loaded_project(source_video, frames=False)
    captions = tmp_path / "words.vtt"
    captions.write_text(
        "WEBVTT\n\n00:00:03.100 --> 00:00:04.100\n"
        "<00:00:03.100>Clear <00:00:03.600>captions\n"
    )
    invoke("retranscribe", "--captions", str(captions))
    invoke("trim", "--from", "1", "--to", "5")
    invoke("cut", "--from", "1", "--to", "2")
    generated = invoke(
        "captions", "generate", "--source", "src_0", "--highlight", "spoken-word",
        "--css", "font-size: 32px; color: white", "--position", "bottom",
    )
    add_title("1", "2")
    expected_ids = {o["id"] for o in generated["overlays"]}
    spec_path = tmp_path / "moviestar/spec.json"
    before = spec_path.read_bytes()
    outputs = []
    for at, source_at in [("1.25", 3.25), ("1.75", 3.75)]:
        out = tmp_path / f"caption-{at}.png"
        options = ["--dry-run"] if dry_run else []
        shot = invoke("screenshot", "--at", at, "--out", str(out), *options)
        assert shot["result_timecode"]["seconds"] == float(at)
        assert shot["source_timecode"]["seconds"] == source_at
        assert shot["operations_applied"] == 2
        assert shot["overlays"]["burned_in"] is True
        assert shot["overlays"]["active_count"] == 2
        assert expected_ids <= {item["id"] for item in shot["overlays"]["items"]}
        ass_path = Path(shot["overlays"]["highlight"]["ass_file"])
        assert "ass=filename=" in " ".join(shot["ffmpeg_command"])
        assert out.exists() == (not dry_run)
        assert ass_path.exists() == (not dry_run)
        if not dry_run:
            assert "PlayResX: 320" in ass_path.read_text()
            assert "PlayResY: 240" in ass_path.read_text()
            image = pixels(out)
            assert image[0, 0, 2] > 200  # source 3.x is blue, not source 1.x red
            assert np.any(image[140:220, :, 0] > 100), "caption pixels are missing"
            assert np.any(image[:120, :, 0] > 100), "manual title pixels are missing"
            outputs.append(image)
    if not dry_run:
        assert np.any(outputs[0][140:220] != outputs[1][140:220]), "active word did not change"
        exported = invoke("export", "--out", str(tmp_path / "export.mp4"))
        assert exported["overlays"]["highlight"]["rendered"] is True
        reference = pixels(exported["out"], "1.25")
        # Export re-encodes to H.264; allow codec noise while comparing the
        # complete frame, including the caption and manual title regions.
        assert np.abs(outputs[0] - reference).mean() < 3
    assert spec_path.read_bytes() == before


def test_dry_run_reports_manual_overlay_without_writing_any_files(source_video, loaded_project, tmp_path):
    loaded_project(source_video, frames=False)
    add_title()
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    shot = invoke("screenshot", "--at", "0.3", "--out", str(tmp_path / "dry.png"), "--dry-run")
    assert shot["overlays"]["active_count"] == 1
    assert "drawtext=" in " ".join(shot["ffmpeg_command"])
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("file_option", [True, False])
def test_explicit_file_modes_ignore_stored_overlays_and_trims(source_video, loaded_project, tmp_path, file_option):
    loaded_project(source_video, frames=False)
    invoke("trim", "--from", "3", "--to", "5")
    add_title()
    args = ["--file", str(source_video)] if file_option else [str(source_video)]
    shot = invoke("screenshot", *args, "--at", "0.3", "--out", str(tmp_path / "file.png"))
    assert shot["mode"] == "file"
    assert "overlays" not in shot
    assert "result_timecode" not in shot
    image = pixels(shot["out"])
    assert image[0, 0, 0] > 200  # raw source 0.3 is red
    assert np.abs(image - image[0, 0]).max() <= 2


def test_platform_preview_receives_flat_overlay_geometry(source_video, loaded_project):
    loaded_project(source_video, frames=False)
    add_title()
    shot = invoke("screenshot", "--at", "0.3", "--platform", "tiktok", "--dry-run")
    assert shot["overlays"]["active_count"] == 1
    assert any(target["type"] == "overlay" for target in shot["platform_preview"]["known_targets"])


def test_source_specific_multisource_screenshot_keeps_source_only_behavior(source_video, loaded_project, tmp_path):
    loaded_project([source_video, source_video], frames=False)
    add_title()
    shot = invoke("screenshot", "--source", "src_1", "--at", "0.3", "--out", str(tmp_path / "source-only.png"))
    assert shot["mode"] == "project"
    assert "overlays" not in shot
    assert "-filter_complex" not in shot["ffmpeg_command"]
    image = pixels(shot["out"])
    assert np.abs(image - image[0, 0]).max() <= 2


def test_unresolvable_font_fails_before_writing_a_frame(source_video, loaded_project, tmp_path):
    loaded_project(source_video, frames=False)
    add_title()
    spec_path = tmp_path / "moviestar/spec.json"
    spec = json.loads(spec_path.read_text())
    spec["overlays"][0]["style"]["resolved"]["font_family"] = "Missing Screenshot Font"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "missing-font.png"
    result = CliRunner().invoke(cli, ["screenshot", "--at", "0.3", "--out", str(out)])
    assert result.exit_code == 1
    error = json.loads(result.stdout)
    assert error["command"] == "screenshot"
    assert "Missing Screenshot Font" in error["error"]
    assert not out.exists()


def test_missing_ass_support_returns_actionable_capability_error(source_video, loaded_project, tmp_path, monkeypatch):
    loaded_project(source_video, frames=False)
    captions = tmp_path / "words.vtt"
    captions.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<00:00:00.000>Clear <00:00:00.500>captions\n")
    invoke("retranscribe", "--captions", str(captions))
    invoke("captions", "generate", "--source", "src_0", "--highlight", "spoken-word")
    monkeypatch.setattr("moviestar.ffmpeg.ffmpeg_filter_available", lambda name: False)
    out = tmp_path / "no-ass.png"
    result = CliRunner().invoke(cli, ["screenshot", "--at", "0.25", "--out", str(out)])
    assert result.exit_code == 1
    error = json.loads(result.stdout)
    assert error["command"] == "screenshot"
    assert error["code"] == "ffmpeg_missing_ass_filter"
    assert "doctor" in error["hint"]
    assert not out.exists()
