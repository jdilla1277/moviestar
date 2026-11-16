"""Full-spec imports must not silently ignore authored overlay CSS (#193)."""

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.fonts import bundled_font_dir
from moviestar.spec import save_spec
from tests.conftest import inject_synthetic_transcript, make_segment, make_word


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, (result.stdout, result.exception)
    return json.loads(result.stdout)


def stored(tmp_path):
    return json.loads((tmp_path / "moviestar/spec.json").read_text())


def snapshot(tmp_path):
    return {str(path.relative_to(tmp_path)): path.read_bytes()
            for path in (tmp_path / "moviestar").rglob("*.json")}


def pixels(path):
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path),
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ])


@pytest.fixture
def project(test_video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    invoke("load", test_video, "--as", "src", "--no-transcribe", "--no-frames")
    invoke("trim", "--from", "0.1", "--to", "1.9")
    invoke("audio", "source", "--gain-db", "-3")
    invoke("overlays", "add", "--text", "X", "--from", "0", "--to", "1",
           "--css", "font-size: 77px; color: #ff0000")
    return tmp_path


@pytest.mark.parametrize("dry", [False, True])
@pytest.mark.parametrize("defect", ["missing", "stale", "partial"])
def test_unresolved_styles_reject_before_changing_rendered_state(project, dry, defect):
    before = snapshot(project)
    reference = invoke("screenshot", "--at", "0.5", "--out", "reference.png")
    document = stored(project)
    overlay = document["overlays"][0]
    overlay["text"] = "Y"
    if defect == "missing":
        overlay["style"].pop("resolved")
    elif defect == "stale":
        overlay["style"]["resolved"]["font_size"] = 48
    else:
        overlay["style"]["resolved"] = {"font_size": 77}
    path = project / "replacement.json"
    path.write_text(json.dumps(document))
    args = ["spec", "--edit", str(path)] + (["--dry-run"] if dry else [])
    result = CliRunner().invoke(cli, args)
    shot = invoke("screenshot", "--at", "0.5", "--out", "after.png")
    actual_size = shot["overlays"]["items"][0]["font_size"]
    assert result.exit_code == 1, (result.stdout, "rendered font size", actual_size)
    error = json.loads(result.stdout)
    assert error["command"] == "spec"
    assert "overlays[0].style" in error["error"]
    assert "overlays set" in error["hint"]
    assert snapshot(project) == before
    assert actual_size == 77
    assert pixels(shot["out"]) == pixels(reference["out"])


@pytest.mark.parametrize("dry", [False, True])
@pytest.mark.parametrize("css", ["position: absolute", "font-size: banana", 17, "font-family: MissingFontForRegression"])
def test_bad_authored_css_is_structured_and_atomic(project, dry, css):
    before = snapshot(project)
    document = stored(project)
    document["overlays"][0]["style"]["css"] = css
    path = project / "bad-style.json"
    path.write_text(json.dumps(document))
    result = CliRunner().invoke(cli, ["spec", "--edit", str(path), *(["--dry-run"] if dry else [])])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["command"] == "spec"
    assert "overlays[0].style" in data["error"]
    assert data["hint"]
    assert snapshot(project) == before


def test_canonical_overlay_import_and_full_spec_import_render_identically(project, test_video):
    before = stored(project)
    source_hash = hashlib.sha256(Path(test_video).read_bytes()).hexdigest()
    overlay_file = project / "overlays.json"
    overlay_file.write_text(json.dumps({"overlays": before["overlays"]}))
    invoke("overlays", "set", str(overlay_file))
    expected = invoke("screenshot", "--at", "0.5", "--out", "overlay-import.png")
    # Render the proposed content through canonical overlay import first.
    proposed = stored(project)
    proposed["overlays"][0]["text"] = "Z"
    overlay_file.write_text(json.dumps({"overlays": proposed["overlays"]}))
    invoke("overlays", "set", str(overlay_file))
    expected_new = invoke("screenshot", "--at", "0.5", "--out", "overlay-new.png")
    invoke("undo")
    candidate = stored(project)
    candidate["overlays"][0]["text"] = "Z"
    # The same content through full-spec import must yield identical pixels.
    path = project / "canonical.json"
    path.write_text(json.dumps(candidate))
    preview_before = snapshot(project)
    preview = invoke("spec", "--edit", str(path), "--dry-run")
    assert snapshot(project) == preview_before
    applied = invoke("spec", "--edit", str(path))
    assert preview["spec"]["overlays"] == applied["spec"]["overlays"]
    for key, value in candidate.items():
        if key != "revisions":
            assert applied["spec"][key] == value
    assert len(applied["spec"]["revisions"]) == len(candidate["revisions"]) + 1
    actual_new = invoke("screenshot", "--at", "0.5", "--out", "spec-new.png")
    assert pixels(actual_new["out"]) == pixels(expected_new["out"])
    invoke("undo")
    restored = invoke("screenshot", "--at", "0.5", "--out", "restored.png")
    assert pixels(restored["out"]) == pixels(expected["out"])
    assert hashlib.sha256(Path(test_video).read_bytes()).hexdigest() == source_hash


def test_complete_legacy_defaults_and_resolved_only_styles_are_not_rewritten(project):
    legacy = stored(project)
    style = legacy["overlays"][0]["style"]
    style["css"] = None
    style["resolved"].update({"font_size": 96, "stroke_width": 3, "max_width": None})
    save_spec(legacy, project)
    expected = invoke("screenshot", "--at", "0.5", "--out", "legacy.png")
    path = project / "legacy.json"
    path.write_text(json.dumps(legacy))
    applied = invoke("spec", "--edit", str(path))
    assert applied["spec"]["overlays"][0]["style"] == style
    actual = invoke("screenshot", "--at", "0.5", "--out", "legacy-after.png")
    assert pixels(expected["out"]) == pixels(actual["out"])
    explicit = stored(project)
    explicit["overlays"][0]["style"] = {"resolved": {"font_size": 31, "color": "#123456"}}
    path.write_text(json.dumps(explicit))
    result = invoke("spec", "--edit", str(path))
    assert result["spec"]["overlays"][0]["style"] == explicit["overlays"][0]["style"]


def test_project_font_survives_canonical_full_spec_import(project):
    invoke("fonts", "add", str(bundled_font_dir() / "Inter-Regular.ttf"),
           "--name", "ProjectProofFont", "--scope", "project")
    invoke("overlays", "add", "--text", "Font", "--from", "0", "--to", "1",
           "--css", "font-family: ProjectProofFont; font-size: 24px", "--position", "bottom")
    document = stored(project)
    path = project / "custom-font.json"
    path.write_text(json.dumps(document))
    before = invoke("screenshot", "--at", "0.5", "--out", "font-before.png")
    invoke("spec", "--edit", str(path))
    after = invoke("screenshot", "--at", "0.5", "--out", "font-after.png")
    assert pixels(before["out"]) == pixels(after["out"])


@pytest.mark.parametrize("dry", [False, True])
def test_derived_cues_remain_recipe_owned_and_unresolved_cache_rejects(project, dry):
    words = [make_word("Hello", 0.2, 0.4), make_word("world", 0.5, 0.8)]
    inject_synthetic_transcript(project, words, [make_segment("Hello world", 0.2, 0.8)])
    invoke("captions", "generate", "--source", "src")
    canonical = invoke("spec")
    before = snapshot(project)
    path = project / "derived.json"
    path.write_text(json.dumps(canonical))
    preview = invoke("spec", "--edit", str(path), "--dry-run")
    assert preview["spec"]["captions"] == canonical["captions"]
    assert snapshot(project) == before
    broken = copy.deepcopy(canonical)
    cue = next(overlay for overlay in broken["overlays"] if overlay["kind"] == "caption")
    cue["style"].pop("resolved")
    path.write_text(json.dumps(broken))
    result = CliRunner().invoke(cli, ["spec", "--edit", str(path), *(["--dry-run"] if dry else [])])
    assert result.exit_code == 1
    assert "Derived captions" in json.loads(result.stdout)["hint"]
    assert snapshot(project) == before
    # The canonical input still imports as a unit without taking ownership of the recipe.
    path.write_text(json.dumps(canonical))
    applied = invoke("spec", "--edit", str(path))
    assert applied["spec"]["captions"] == canonical["captions"]
    assert applied["spec"]["overlays"] == canonical["overlays"]


@pytest.mark.parametrize("css,key,value", [
    ("color: #ff0000", "color", "#ffffff"),
    ("-moviestar-stroke: 2px #123456", "stroke_width", 7),
    ("-moviestar-stroke: 2px #123456", "stroke_color", "#ffffff"),
    ("font-size: 1px", "font_size", True),
])
def test_css_override_consistency_includes_colors_strokes_and_boolean_types(project, css, key, value):
    invoke("overlays", "add", "--text", "Check", "--from", "0", "--to", "1", "--css", css)
    document = stored(project)
    document["overlays"][-1]["style"]["resolved"][key] = value
    path = project / "inconsistent.json"
    path.write_text(json.dumps(document))
    before = snapshot(project)
    result = CliRunner().invoke(cli, ["spec", "--edit", str(path)])
    assert result.exit_code == 1
    assert key in json.loads(result.stdout)["error"]
    assert snapshot(project) == before


def test_stroke_width_override_preserves_unspecified_legacy_color(project):
    invoke("overlays", "add", "--text", "Stroke", "--from", "0", "--to", "1",
           "--css", "-moviestar-stroke: 2px")
    document = stored(project)
    document["overlays"][-1]["style"]["resolved"]["stroke_color"] = "#123456"
    path = project / "width-only.json"
    path.write_text(json.dumps(document))
    result = invoke("spec", "--edit", str(path))
    assert result["spec"]["overlays"][-1]["style"] == document["overlays"][-1]["style"]


@pytest.mark.parametrize("preset", ["unknown-preset", ["title"]])
def test_invalid_preset_is_structured_before_mutation(project, preset):
    document = stored(project)
    document["overlays"][0]["style"]["preset"] = preset
    path = project / "invalid-preset.json"
    path.write_text(json.dumps(document))
    before = snapshot(project)
    result = CliRunner().invoke(cli, ["spec", "--edit", str(path)])
    assert result.exit_code == 1
    assert "overlays[0].style" in json.loads(result.stdout)["error"]
    assert snapshot(project) == before


def test_cross_project_source_guard_precedes_style_resolution(project, monkeypatch):
    document = stored(project)
    document["sources"][0]["id"] = "different_project_source"
    path = project / "other-project.json"
    path.write_text(json.dumps(document))
    before = snapshot(project)
    def must_not_resolve(**kwargs):
        raise AssertionError("Cross-project imports must fail before style resolution")
    monkeypatch.setattr("moviestar.cli._overlay_style_envelope", must_not_resolve)
    result = CliRunner().invoke(cli, ["spec", "--edit", str(path)])
    assert result.exit_code == 1
    assert "source IDs" in json.loads(result.stdout)["error"]
    assert snapshot(project) == before
