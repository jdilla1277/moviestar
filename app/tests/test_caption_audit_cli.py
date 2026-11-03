"""CLI integration for complete timing reports and optional rendered proof."""

import json
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from tests.conftest import inject_synthetic_transcript, make_segment, make_word


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def add_text(text, start, end, track="titles", scene=None):
    args = ["overlays", "add", "--text", text, "--from", str(start),
           "--to", str(end), "--track", track, "--style", "caption-default",
           "--css", "font-size: 24px", "--position", "center"]
    if scene:
        args += ["--scene", scene]
    invoke(*args)


def snapshot(project):
    return {str(path.relative_to(project)): path.read_bytes()
            for path in project.rglob("*") if path.is_file()}


def test_audit_is_discoverable_and_has_no_media_or_state_side_effects(
    test_video, loaded_project, tmp_path, monkeypatch
):
    loaded_project(test_video, frames=False)
    add_text("Start", 0, 0.7)
    add_text("and", 0.7, 0.8)
    add_text("Finish", 0.8, 2)
    assert "audit" in CliRunner().invoke(cli, ["captions", "--help"]).output
    before = snapshot(tmp_path / "moviestar")

    def no_media(*args, **kwargs):
        pytest.fail("default audit must not launch subprocesses")

    monkeypatch.setattr(subprocess, "run", no_media)
    result = invoke("captions", "audit", "--track", "titles", "--language", "en")
    assert result["status"] == "caption_audited"
    assert result["writes_spec"] is False
    assert result["writes_files"] is False
    assert snapshot(tmp_path / "moviestar") == before
    assert result["range"]["to"]["seconds"] == 2
    assert any(w["code"] == "caption_connector_only_state" for w in result["warnings"])
    assert any(s["range"]["from"]["seconds"] == 0.7 for s in result["states"])
    assert result["proofs"]["rendered"] == 0
    assert "out" not in result


def test_derived_and_imported_captions_are_audited_on_the_current_clock(
    test_video, loaded_project, tmp_path
):
    loaded_project(test_video, frames=False)
    inject_synthetic_transcript(
        tmp_path,
        [make_word("First", 0, 0.8), make_word("second", 1.0, 1.8)],
        [make_segment("First second", 0, 1.8)],
    )
    invoke("captions", "generate", "--source", "src_0")
    captions = tmp_path / "translation.srt"
    captions.write_text("1\n00:00:00,500 --> 00:00:01,500\nAnother track\n")
    invoke("captions", "import", str(captions), "--track", "translated")
    invoke("trim", "--from", "0", "--to", "1.6")
    before = snapshot(tmp_path / "moviestar")
    result = invoke("captions", "audit")
    assert result["tracks"] == ["captions", "translated"]
    assert result["range"]["to"]["seconds"] == 1.6
    assert {c["track"] for c in result["components"]} == {"captions", "translated"}
    assert all(c["range"]["to"]["seconds"] <= 1.6 for c in result["components"])
    assert snapshot(tmp_path / "moviestar") == before


def test_scene_bound_manual_overlays_follow_scene_reordering(
    test_video, loaded_project, tmp_path
):
    loaded_project(test_video, frames=False)
    invoke("scenes", "set", "--scene", "intro=single", "--slot", "intro:main=src_0",
           "--from", "0", "--to", "1", "--scene", "ending=single",
           "--slot", "ending:main=src_0", "--from", "1", "--to", "2")
    add_text("and", 0.2, 0.3, scene="ending")
    result = invoke("captions", "audit", "--track", "titles")
    assert result["components"][0]["range"]["from"]["seconds"] == 1.2
    invoke("scenes", "set", "--scene", "ending=single", "--slot", "ending:main=src_0",
           "--from", "1", "--to", "2", "--scene", "intro=single",
           "--slot", "intro:main=src_0", "--from", "0", "--to", "1")
    result = invoke("captions", "audit", "--track", "titles")
    assert result["components"][0]["range"]["from"]["seconds"] == 0.2


@pytest.mark.parametrize("args", [
    ["--min-duration", "nan"], ["--min-duration", "inf"],
    ["--min-duration", "-1"], ["--track", "missing"], ["--combine-tracks"],
    ["--connector-word", "two words"],
])
def test_invalid_policies_are_structured_errors(test_video, loaded_project, args):
    loaded_project(test_video, frames=False)
    result = CliRunner().invoke(cli, ["captions", "audit", *args])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["command"] == "captions audit"
    assert data["error"]


def test_proof_frames_contain_rendered_text_and_never_change_the_project(
    loaded_project, tmp_path
):
    source = tmp_path / "black.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=black:s=320x240:r=30:d=2", "-c:v", "libx264", str(source),
    ], check=True)
    loaded_project(source, frames=False)
    invoke("scenes", "set", "--canvas", "320x240", "--scene", "proof=single",
           "--slot", "proof:main=src_0", "--from", "0", "--to", "2")
    add_text("PROOF", 0, 0.7)
    add_text("and", 0.7, 0.8)
    add_text("FINISH", 0.8, 2)
    before = snapshot(tmp_path / "moviestar")
    root = tmp_path / "proof"
    result = invoke("captions", "audit", "--track", "titles", "--language", "en",
                    "--proof-dir", str(root), "--max-proof-frames", "3")
    assert result["proofs"]["rendered"] == 3
    assert result["coverage"]["visual_review"] == "not_performed"
    assert result["writes_files"] is True
    assert Path(result["out"]).is_file()
    assert json.loads(Path(result["out"]).read_text()) == result
    assert snapshot(tmp_path / "moviestar") == before
    for frame in result["proofs"]["frames"]:
        pixels = subprocess.check_output([
            "ffmpeg", "-v", "error", "-i", frame["out"], "-pix_fmt", "gray",
            "-f", "rawvideo", "-",
        ])
        assert sum(value > 200 for value in pixels) > 40  # text on black
        assert frame["at"]["seconds"] < 2
    assert any(0.7 <= f["at"]["seconds"] < 0.8 for f in result["proofs"]["frames"])


def test_existing_proof_directory_is_preserved(test_video, loaded_project, tmp_path):
    loaded_project(test_video, frames=False)
    root = tmp_path / "proof"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("previous review")
    result = CliRunner().invoke(cli, ["captions", "audit", "--proof-dir", str(root)])
    assert result.exit_code == 1
    assert sentinel.read_text() == "previous review"
    assert list(root.iterdir()) == [sentinel]


def test_detached_manual_overlays_are_not_silently_treated_as_visible(
    test_video, loaded_project, tmp_path
):
    loaded_project(test_video, frames=False)
    invoke("scenes", "set", "--scene", "ending=single", "--slot", "ending:main=src_0",
           "--from", "0", "--to", "1")
    add_text("and", 0, 0.1, scene="ending")
    invoke("scenes", "set", "--scene", "replacement=single", "--slot", "replacement:main=src_0",
           "--from", "0", "--to", "1")
    result = invoke("captions", "audit", "--track", "titles")
    assert result["summary"]["components"] == 0
    assert result["coverage"]["detached_overlay_ids"]
    assert any(w["code"] == "overlays_detached" for w in result["warnings"])


def test_failed_derived_refresh_does_not_audit_an_old_cache_as_current(
    test_video, loaded_project, tmp_path
):
    loaded_project(test_video, frames=False)
    inject_synthetic_transcript(tmp_path, [make_word("Hello", 0, 1)],
                                [make_segment("Hello", 0, 1)])
    invoke("captions", "generate", "--source", "src_0")
    # Missing transcript plus an outdated timeline forces a failed refresh.
    (tmp_path / "moviestar" / "transcripts" / "src_0.json").unlink()
    path = tmp_path / "moviestar" / "spec.json"
    spec = json.loads(path.read_text())
    spec["captions"][0]["cache_fingerprint"] = "outdated"
    path.write_text(json.dumps(spec))
    before = snapshot(tmp_path / "moviestar")
    result = CliRunner().invoke(cli, ["captions", "audit"])
    assert result.exit_code == 1
    assert "could not refresh" in json.loads(result.stdout)["error"]
    assert snapshot(tmp_path / "moviestar") == before


def test_proof_contains_only_text_active_at_its_exact_timestamp(
    loaded_project, tmp_path
):
    source = tmp_path / "black.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=black:s=320x240:r=30:d=1", "-c:v", "libx264", str(source),
    ], check=True)
    loaded_project(source, frames=False)
    invoke("scenes", "set", "--canvas", "320x240", "--scene", "proof=single",
           "--slot", "proof:main=src_0", "--from", "0", "--to", "1")
    add_text("FLASH", 0.030, 0.034)
    add_text("NEXT", 0.034, 1)
    before = snapshot(tmp_path / "moviestar")
    report = invoke("captions", "audit", "--track", "titles",
                    "--proof-dir", str(tmp_path / "proof"))
    frame = report["proofs"]["frames"][0]
    assert frame["at"]["seconds"] == 1 / 30
    state = next(s for s in report["states"] if s["id"] in frame["state_ids"])
    assert [c["text"] for c in state["text_components"]] == ["FLASH"]
    assert snapshot(tmp_path / "moviestar") == before

    # Independent reference: remove NEXT entirely, rather than trusting the
    # renderer's overlay envelope to prove which text reached the pixels.
    spec_path = tmp_path / "moviestar" / "spec.json"
    spec_bytes = spec_path.read_bytes()
    spec = json.loads(spec_bytes)
    spec["overlays"] = [o for o in spec["overlays"] if o["text"] == "FLASH"]
    spec_path.write_text(json.dumps(spec))
    reference = tmp_path / "flash-only.jpg"
    invoke("screenshot", "--at", str(1 / 30), "--out", str(reference))
    spec_path.write_bytes(spec_bytes)

    def pixels(path):
        return subprocess.check_output([
            "ffmpeg", "-v", "error", "-i", str(path), "-pix_fmt", "gray",
            "-f", "rawvideo", "-",
        ])

    expected = pixels(reference)
    assert sum(value > 200 for value in expected) > 40
    assert pixels(frame["out"]) == expected
    # Include exact start/end membership, and an instant just before FLASH.
    for at, expected_text in [(0.0295, []), (0.030, ["FLASH"]),
                              (0.034, ["NEXT"])]:
        result = invoke("screenshot", "--at", str(at), "--dry-run")
        assert [o["text"] for o in result["overlays"]["items"]] == expected_text
