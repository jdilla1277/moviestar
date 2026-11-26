"""Bulk overlay previews must validate the same persisted state as apply (#194)."""

import copy
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.overlays import plan_overlays


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, (result.stdout, result.exception)
    return json.loads(result.stdout)


@pytest.fixture
def project(loaded_project, test_video, tmp_path):
    loaded_project(test_video, frames=False)
    invoke("overlays", "add", "--text", "Keep until valid", "--from", "0", "--to", "1")
    return tmp_path


def snapshot(project):
    return {str(p.relative_to(project)): p.read_bytes()
            for p in (project / "moviestar").rglob("*.json")}


def document(path, token):
    overlay = {"kind": "caption", "text": "Hello", "from": "0", "to": "1",
               "highlight": {"mode": "spoken-word", "color": "#ffe94a"},
               "tokens": [token]}
    if path == "explicit":
        overlay.update(kind="manual", timing={"space": "result", "from": "0", "to": "1"})
        del overlay["from"], overlay["to"]
    records = [overlay]
    if path == "mixed":
        records.insert(0, {"text": "Title", "timing": {"space": "result", "from": "0", "to": "1"}})
    return {"overlays": records}


@pytest.mark.parametrize("path", ["legacy", "explicit", "mixed"])
@pytest.mark.parametrize("start,end", [("0.000", "0.500"), (0, 0.5), ("0:00", "0:00.5"), ("0:00:00.000", "0:00:00.500")])
def test_supported_times_canonicalize_identically_without_preview_writes(project, path, start, end):
    before = snapshot(project)
    original = json.loads((project / "moviestar/spec.json").read_text())
    token = {"text": "Hello", "from": start, "to": end, "source": "source-proof"}
    file = project / "replacement.json"
    file.write_text(json.dumps(document(path, token)))
    preview = invoke("overlays", "set", str(file), "--dry-run")
    assert snapshot(project) == before
    invoke("overlays", "set", str(file))
    stored = json.loads((project / "moviestar/spec.json").read_text())
    normalized = stored["overlays"][-1]["tokens"][0]
    assert normalized == {**token, "from": "0:00:00.000", "to": "0:00:00.500"}
    assert preview["overlays"][-1]["tokens"][0]["from"]["text"] == normalized["from"]
    for field, value in original.items():
        if field not in {"overlays", "revisions"}:
            assert stored[field] == value
    assert len(stored["revisions"]) == len(original["revisions"]) + 1
    invoke("undo")
    assert json.loads((project / "moviestar/spec.json").read_text())["overlays"] == original["overlays"]


@pytest.mark.parametrize("path", ["legacy", "explicit", "mixed"])
@pytest.mark.parametrize("token", [
    {"text": "Hello", "from": "bad", "to": "0.5"},
    {"text": "Hello", "from": "0.7", "to": "0.2"},
    {"text": "Hello", "from": float("nan"), "to": 0.5},
    {"text": "Hello", "from": "0:00:nan", "to": "0.5"},
    {"text": "Hello", "from": "0", "to": float("inf")},
    {"text": "Hello", "from": "0", "to": "1e309"},
    {"text": "Hello", "from": -0.1, "to": 0.5},
    {"text": "Hello", "from": False, "to": "0.5"},
    {"text": "Hello", "from": None, "to": "0.5"},
    {"text": "Hello", "from": {}, "to": "0.5"},
    {"text": "Hello", "to": "0.5"},
    {"text": 17, "from": "0", "to": "0.5"},
    None,
])
def test_invalid_tokens_reject_identically_and_atomically(project, path, token):
    file = project / "invalid.json"
    file.write_text(json.dumps(document(path, copy.deepcopy(token))))
    before = snapshot(project)
    errors = []
    for flags in (["--dry-run"], []):
        result = CliRunner().invoke(cli, ["overlays", "set", str(file), *flags])
        assert result.exit_code == 1, (result.stdout, result.exception)
        data = json.loads(result.stdout)
        assert data["command"] == "overlays set"
        assert "tokens[0]" in str(data)
        errors.append(data)
        assert snapshot(project) == before
    assert errors[0] == errors[1]


@pytest.mark.parametrize("start,end", [("0.5", "0.5"), ("2", "3"), ("0", "2")])
def test_zero_duration_and_outside_cue_tokens_keep_existing_clipping_semantics(project, start, end):
    file = project / "clipped.json"
    file.write_text(json.dumps(document("legacy", {"text": "Hello", "from": start, "to": end})))
    before = snapshot(project)
    invoke("overlays", "set", str(file), "--dry-run")
    assert snapshot(project) == before
    invoke("overlays", "set", str(file))
    records = json.loads((project / "moviestar/spec.json").read_text())["overlays"]
    [plan] = plan_overlays(records, (320, 240), 0, 1)["plans"]
    tokens = plan["highlight"]["tokens"]
    if start == "0":
        assert tokens[0]["from_local"] == 0
        assert tokens[0]["to_local"] == 1
    else:
        assert tokens == []


@pytest.mark.parametrize("path", ["legacy", "explicit", "mixed"])
@pytest.mark.parametrize("tokens", [{}, "bad", 17])
def test_invalid_token_containers_are_structured_and_atomic(project, path, tokens):
    data = document(path, {"text": "Hello", "from": "0", "to": "0.5"})
    data["overlays"][-1]["tokens"] = tokens
    file = project / "bad-container.json"
    file.write_text(json.dumps(data))
    before = snapshot(project)
    for flags in (["--dry-run"], []):
        result = CliRunner().invoke(cli, ["overlays", "set", str(file), *flags])
        assert result.exit_code == 1
        assert "tokens must be an array" in json.loads(result.stdout)["overlay_errors"][0]["error"]
        assert snapshot(project) == before


@pytest.mark.parametrize("path", ["legacy", "mixed"])
def test_invalid_records_are_aggregated_without_applying_valid_siblings(project, path):
    data = document(path, {"text": "Hello", "from": "bad", "to": "0.5"})
    second = copy.deepcopy(data["overlays"][-1])
    second["tokens"][0].update({"from": "0", "to": "inf"})
    data["overlays"].append(second)
    file = project / "multiple-errors.json"
    file.write_text(json.dumps(data))
    before = snapshot(project)
    for flags in (["--dry-run"], []):
        result = CliRunner().invoke(cli, ["overlays", "set", str(file), *flags])
        assert result.exit_code == 1
        errors = json.loads(result.stdout)["overlay_errors"]
        assert [e["index"] for e in errors] == ([0, 1] if path == "legacy" else [1, 2])
        assert snapshot(project) == before
