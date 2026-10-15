"""Persisted spatial definitions and scene-owned visual trees."""

from copy import deepcopy
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.spatial_model import expand_spatial_scene
from moviestar.spec import (
    SpecValidationError, empty_spec, load_spec, pop_revision, save_spec, validate_spec,
)


def _project():
    spec = empty_spec([
        {"id": "alice", "path": "/tmp/alice.mp4"},
        {"id": "bob", "path": "/tmp/bob.mp4"},
        {"id": "carol", "path": "/tmp/carol.mp4"},
        {"id": "dan", "path": "/tmp/dan.mp4"},
    ])
    spec["composition_canvas"] = {"preset": None, "width": 1280, "height": 720, "aspect_ratio": "16:9"}
    spec["composition"] = [
        {"id": scene, "name": scene, "layout": {"preset": "single"},
         "slots": [{"slot": "main", "source": source,
                    "source_from": "0:00:00.000", "source_to": "0:00:08.000"}]}
        for scene, source in (("scene_a", "alice"), ("scene_b", "carol"))
    ]
    spec["spatial"] = {
        "definitions": {
            "pair": {
                "name": "ListenerPair",
                "inputs": [
                    {"name": "left", "type": "video", "required": True},
                    {"name": "right", "type": "video", "required": True},
                    {"name": "label", "type": "text", "required": False, "default": "Listener"},
                ],
                "children": [
                    {"id": "left_video", "type": "video", "content": {"input": "left"},
                     "layout": {"grid_area": "left"}},
                    {"id": "right_video", "type": "video", "content": {"input": "right"},
                     "layout": {"grid_area": "right"}},
                    {"id": "name", "type": "text", "content": {"input": "label"}},
                ],
            }
        },
        "scenes": {
            scene: {"layout": {"display": "grid", "columns": "1fr 1fr", "children": [
                {"instance_id": "listeners", "definition_id": "pair",
                 "bindings": {"left": {"asset_id": left, "source": {"start": 0, "end": 8}},
                              "right": {"asset_id": right, "source": {"start": 0, "end": 8}},
                              "label": {"text": label}},
                 "layout": {"grid_area": "listeners"}},
            ]}}
            for scene, left, right, label in (
                ("scene_a", "alice", "bob", "Alice and Bob"),
                ("scene_b", "carol", "dan", "Carol and Dan"),
            )
        },
    }
    return spec


def test_component_is_shared_and_scene_layouts_survive_save_reload_and_undo(tmp_path):
    spec = _project()
    validate_spec(spec)
    save_spec(spec, cwd=tmp_path, command="spatial.define")
    loaded = load_spec(cwd=tmp_path)
    first = expand_spatial_scene(loaded, "scene_a")
    second = expand_spatial_scene(loaded, "scene_b")
    assert first["children"][0]["children"][0]["content"]["asset_id"] == "alice"
    assert second["children"][0]["children"][0]["content"]["asset_id"] == "carol"
    assert first["children"][0]["children"][0]["occurrence_path"] == [
        "scene_a", "listeners", "left_video"
    ]
    assert first["display"] == "grid"
    assert first["children"][0]["layout"] == {"grid_area": "listeners"}
    assert first["diagnostics"] == []
    assert second["diagnostics"] == []
    changed = deepcopy(loaded)
    changed["spatial"]["definitions"]["pair"]["children"][0]["layout"] = {"grid_area": "top"}
    save_spec(changed, cwd=tmp_path, command="spatial.update")
    assert expand_spatial_scene(load_spec(cwd=tmp_path), "scene_b")["children"][0]["children"][0]["layout"] == {"grid_area": "top"}
    restored, _ = pop_revision(load_spec(cwd=tmp_path))
    assert restored["spatial"] == loaded["spatial"]


def test_incomplete_binding_is_a_repairable_draft():
    spec = _project()
    del spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["right"]
    validate_spec(spec)
    expanded = expand_spatial_scene(spec, "scene_a")
    assert expanded["diagnostics"] == [{
        "code": "missing_binding", "path": ["scene_a", "listeners", "right_video"],
        "input": "right",
    }]
    assert expanded["children"][0]["children"][1]["content"] is None
    spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["right"] = {"empty": True}
    validate_spec(spec)
    assert expand_spatial_scene(spec, "scene_a")["diagnostics"] == expanded["diagnostics"]


def test_ambiguous_node_ids_and_wrong_binding_types_are_rejected():
    spec = _project()
    pair = spec["spatial"]["definitions"]["pair"]
    pair["children"][1]["id"] = "left_video"
    with pytest.raises(SpecValidationError, match="duplicate"):
        validate_spec(spec)
    spec = _project()
    spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["left"] = {"text": "wrong"}
    with pytest.raises(SpecValidationError, match="video"):
        validate_spec(spec)


def test_nested_forwarding_and_cycle_stop_at_inspection():
    spec = _project()
    pair = spec["spatial"]["definitions"]["pair"]
    pair["children"].append({"instance_id": "nested", "definition_id": "pair",
                             "bindings": {"left": {"forward": "left"},
                                          "right": {"forward": "right"}}})
    validate_spec(spec)
    result = expand_spatial_scene(spec, "scene_a")
    assert result["diagnostics"][0]["code"] == "component_cycle"
    assert result["diagnostics"][0]["path"] == ["scene_a", "listeners", "nested"]


def test_nested_component_forwards_input_and_two_uses_keep_distinct_paths():
    spec = _project()
    spec["spatial"]["definitions"]["badge"] = {
        "name": "Badge",
        "inputs": [{"name": "caption", "type": "text", "required": True}],
        "children": [{"id": "words", "type": "text", "content": {"input": "caption"}}],
    }
    spec["spatial"]["definitions"]["pair"]["children"].append(
        {"instance_id": "badge_use", "definition_id": "badge",
         "bindings": {"caption": {"forward": "label"}}}
    )
    duplicate = deepcopy(spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0])
    duplicate["instance_id"] = "listeners_copy"
    duplicate["bindings"]["label"] = {"text": "Second use"}
    spec["spatial"]["scenes"]["scene_a"]["layout"]["children"].append(duplicate)
    validate_spec(spec)
    result = expand_spatial_scene(spec, "scene_a")
    first, second = result["children"]
    assert first["children"][-1]["children"][0]["content"] == {"text": "Alice and Bob"}
    assert second["children"][-1]["children"][0]["content"] == {"text": "Second use"}
    assert second["children"][-1]["children"][0]["occurrence_path"] == [
        "scene_a", "listeners_copy", "badge_use", "words"
    ]
    spec["composition"].reverse()
    validate_spec(spec)
    assert expand_spatial_scene(spec, "scene_a")["children"][0]["occurrence_path"] == ["scene_a", "listeners"]


def test_spatial_scenes_must_follow_existing_timeline_ids():
    spec = _project()
    spec["spatial"]["scenes"]["missing"] = spec["spatial"]["scenes"]["scene_a"]
    with pytest.raises(SpecValidationError, match="unknown timeline scene"):
        validate_spec(spec)


def test_real_cli_apply_inspect_and_image_asset_diagnostic(tmp_path, monkeypatch):
    spec = _project()
    del spec["spatial"]
    save_spec(spec, cwd=tmp_path)
    model = _project()["spatial"]
    model["definitions"]["pair"]["children"].append(
        {"id": "logo", "type": "image", "content": {"asset_id": "logo"},
         "style": {"object_fit": "contain"}}
    )
    model["assets"] = {"logo": {"type": "image", "path": "assets/logo.png"}}
    document = tmp_path / "spatial.json"
    document.write_text(json.dumps(model))
    monkeypatch.chdir(tmp_path)
    applied = CliRunner().invoke(cli, ["composition", "model", "apply", str(document)])
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["writes_spec"] is True
    inspected = CliRunner().invoke(cli, ["composition", "model", "inspect", "scene_b"])
    assert inspected.exit_code == 0, inspected.output
    scene = json.loads(inspected.output)["scene"]
    assert scene["children"][0]["children"][-1]["style"] == {"object_fit": "contain"}
    assert scene["diagnostics"] == []
    model["assets"] = {}
    document.write_text(json.dumps(model))
    CliRunner().invoke(cli, ["composition", "model", "apply", str(document)])
    missing = CliRunner().invoke(cli, ["composition", "model", "inspect", "scene_b"])
    assert json.loads(missing.output)["scene"]["diagnostics"][0]["code"] == "unknown_asset"


def test_export_refuses_to_ignore_spatial_scene_tree(tmp_path, monkeypatch):
    spec = _project()
    save_spec(spec, cwd=tmp_path)
    save_project({"sources": spec["sources"]}, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["export", "--dry-run"])
    assert result.exit_code == 1
    output = json.loads(result.output)
    assert output["command"] == "export"
    assert "spatial" in output["error"].lower()


def test_invalid_apply_keeps_previous_spec(tmp_path, monkeypatch):
    spec = _project()
    save_spec(spec, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    invalid = deepcopy(spec["spatial"])
    invalid["definitions"]["pair"]["children"][1]["id"] = "left_video"
    document = tmp_path / "invalid.json"
    document.write_text(json.dumps(invalid))
    result = CliRunner().invoke(cli, ["composition", "model", "apply", str(document)])
    assert result.exit_code != 0
    assert load_spec(cwd=tmp_path)["spatial"] == spec["spatial"]
