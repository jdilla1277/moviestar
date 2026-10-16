"""Persisted spatial definitions and scene-owned visual trees."""

from copy import deepcopy
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.spatial_model import compile_spatial_frame, expand_spatial_scene
from moviestar.resolved import resolve_project
from moviestar.spec import (
    SpecValidationError, empty_spec, load_spec, pop_revision, save_spec,
    set_scene_composition, validate_spec,
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


def _source_project(spec):
    return {"sources": [
        {**source, "duration": {"seconds": 20}, "fps": 30,
         "width": 1280, "height": 720, "has_audio": False}
        for source in spec["sources"]
    ]}


def test_spatial_frame_uses_result_clock_and_component_occurrence_paths():
    spec = _project()
    spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["left"]["source"] = {
        "start": 2, "end": 10,
    }
    project = _source_project(spec)
    timeline = resolve_project(project, spec)
    frame = compile_spatial_frame(spec, timeline, 3, source_durations={
        source["id"]: 20 for source in project["sources"]
    })
    assert frame["scene_id"] == "scene_a"
    assert frame["result_time_s"] == 3
    assert frame["scene_time_s"] == 3
    left, right, _ = frame["children"][0]["children"]
    assert left["occurrence_path"] == ["scene_a", "listeners", "left_video"]
    assert left["media"] == {"asset_id": "alice", "source_time_s": 5,
                             "source_range": {"start": 2, "end": 10}, "visible": True}
    assert right["media"]["source_time_s"] == 3
    assert frame["diagnostics"] == []

    next_scene = compile_spatial_frame(spec, timeline, 8, source_durations={
        source["id"]: 20 for source in project["sources"]
    })
    assert next_scene["scene_id"] == "scene_b"
    assert next_scene["scene_time_s"] == 0
    assert next_scene["children"][0]["children"][0]["media"]["asset_id"] == "carol"
    final_frame = compile_spatial_frame(spec, timeline, timeline.duration_s)
    assert final_frame["scene_id"] == "scene_b"
    assert final_frame["children"][0]["children"][0]["media"]["visible"] is True


def test_short_spatial_video_ends_without_looping_or_changing_scene_duration():
    spec = _project()
    spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["right"]["source"]["end"] = 5
    timeline = resolve_project(_source_project(spec), spec)
    frame = compile_spatial_frame(spec, timeline, 6, source_durations={"alice": 20, "bob": 20})
    media = frame["children"][0]["children"][1]["media"]
    assert media == {"asset_id": "bob", "source_time_s": None,
                     "source_range": {"start": 0, "end": 5}, "visible": False}
    assert frame["scene_duration_s"] == 8
    assert {"code": "media_ends_before_scene", "severity": "warning",
            "path": ["scene_a", "listeners", "right_video"], "media_duration_s": 5,
            "scene_duration_s": 8} in frame["diagnostics"]


def test_spatial_frame_keeps_missing_media_diagnostic_and_rejects_bad_time():
    spec = _project()
    del spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["right"]
    timeline = resolve_project(_source_project(spec), spec)
    frame = compile_spatial_frame(spec, timeline, 2, source_durations={"alice": 20})
    assert frame["children"][0]["children"][1]["media"] is None
    assert frame["diagnostics"][0]["code"] == "missing_binding"
    with pytest.raises(ValueError, match="result time"):
        compile_spatial_frame(spec, timeline, -0.1)
    with pytest.raises(ValueError, match="result time"):
        compile_spatial_frame(spec, timeline, 16.1)


def test_spatial_frame_resolves_full_asset_range_and_rejects_unknown_media():
    spec = _project()
    bindings = spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]
    bindings["left"] = {"asset_id": "alice"}
    bindings["right"] = {"asset_id": "missing", "source": {"start": 0, "end": 8}}
    timeline = resolve_project(_source_project(spec), spec)
    frame = compile_spatial_frame(spec, timeline, 3, source_durations={"alice": 20})
    left, right, _ = frame["children"][0]["children"]
    assert left["media"] == {"asset_id": "alice", "source_time_s": 3,
                             "source_range": {"start": 0, "end": 20}, "visible": True}
    assert right["media"] is None
    assert [item["code"] for item in frame["diagnostics"]] == ["unknown_asset"]

    bindings["right"] = {"asset_id": "bob", "source": {"start": 17, "end": 22}}
    frame = compile_spatial_frame(spec, timeline, 3, source_durations={"alice": 20, "bob": 20})
    assert frame["children"][0]["children"][1]["media"] is None
    assert frame["diagnostics"][0]["code"] == "source_range_out_of_bounds"


def test_model_frame_cli_reports_global_time_and_json_errors(tmp_path, monkeypatch):
    spec = _project()
    save_spec(spec, cwd=tmp_path)
    save_project(_source_project(spec), cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    rendered = CliRunner().invoke(cli, ["composition", "model", "frame", "--at", "0:00:09.000"])
    assert rendered.exit_code == 0, rendered.output
    envelope = json.loads(rendered.output)
    assert envelope["renders_pixels"] is False
    assert envelope["frame"]["scene_id"] == "scene_b"
    assert envelope["frame"]["scene_time_s"] == 1
    assert envelope["frame"]["children"][0]["children"][0]["media"]["source_time_s"] == 1

    bad = CliRunner().invoke(cli, ["composition", "model", "frame", "--at", "0:00:17.000"])
    assert bad.exit_code == 1
    assert json.loads(bad.output)["command"] == "composition model frame"


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


def test_scene_replacement_removes_owned_tree_and_undo_restores_it(tmp_path):
    spec = _project()
    save_spec(spec, cwd=tmp_path)
    updated = set_scene_composition(
        spec,
        scenes=[{"id": "scene_a", "name": "scene_a", "layout": "single",
                 "slots": [("main", "alice", 0.0, 8.0, None)]}],
        source_durations={source["id"]: 20.0 for source in spec["sources"]},
        canvas=spec["composition_canvas"],
    )
    assert list(updated["spatial"]["scenes"]) == ["scene_a"]
    assert updated["spatial"]["scenes"]["scene_a"] == spec["spatial"]["scenes"]["scene_a"]
    assert updated["spatial"]["definitions"] == spec["spatial"]["definitions"]
    save_spec(updated, cwd=tmp_path, command="scenes set")
    reloaded = load_spec(cwd=tmp_path)
    assert set(reloaded["revisions"][-1]["changed"]) == {"composition", "spatial"}
    restored, _ = pop_revision(reloaded)
    assert restored["composition"] == spec["composition"]
    assert restored["spatial"] == spec["spatial"]


def test_required_forwarding_cannot_hide_missing_parent_input():
    spec = _project()
    pair = spec["spatial"]["definitions"]["pair"]
    pair["inputs"][2] = {"name": "label", "type": "text", "required": True}
    pair["children"] = pair["children"][:2]
    spec["spatial"]["definitions"]["badge"] = {
        "name": "Badge",
        "inputs": [{"name": "caption", "type": "text", "default": "fallback"}],
        "children": [{"id": "words", "type": "text", "content": {"input": "caption"}}],
    }
    pair["children"].append({"instance_id": "badge_use", "definition_id": "badge",
                             "bindings": {"caption": {"forward": "label"}}})
    instance = spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]
    del instance["bindings"]["label"]
    validate_spec(spec)
    missing = expand_spatial_scene(spec, "scene_a")
    assert missing["diagnostics"] == [{"code": "missing_binding",
                                       "path": ["scene_a", "listeners"], "input": "label"}]
    assert missing["children"][0]["children"][-1]["children"][0]["content"] == {"text": "fallback"}
    instance["bindings"]["label"] = {"empty": True}
    assert expand_spatial_scene(spec, "scene_a")["diagnostics"] == missing["diagnostics"]


def test_omitted_optional_input_and_forwarded_optional_input_are_valid():
    spec = _project()
    pair = spec["spatial"]["definitions"]["pair"]
    del pair["inputs"][2]["default"]
    spec["spatial"]["definitions"]["badge"] = {
        "name": "Badge",
        "inputs": [{"name": "caption", "type": "text", "required": False}],
        "children": [{"id": "words", "type": "text", "content": {"input": "caption"}}],
    }
    pair["children"].append({"instance_id": "badge_use", "definition_id": "badge",
                             "bindings": {"caption": {"forward": "label"}}})
    del spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["label"]
    validate_spec(spec)
    expanded = expand_spatial_scene(spec, "scene_a")
    assert expanded["diagnostics"] == []
    assert expanded["children"][0]["children"][2]["content"] is None
    assert expanded["children"][0]["children"][3]["children"][0]["content"] is None
    spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["label"] = {"empty": True}
    assert expand_spatial_scene(spec, "scene_a")["diagnostics"] == []


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
    assert json.loads(result.output)["command"] == "composition model apply"
    assert load_spec(cwd=tmp_path)["spatial"] == spec["spatial"]


def test_model_commands_report_expected_errors_as_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    document = tmp_path / "spatial.json"
    document.write_text("{}")
    for args, command in [
        (["apply", str(document)], "composition model apply"),
        (["inspect", "scene_a"], "composition model inspect"),
    ]:
        result = CliRunner().invoke(cli, ["composition", "model", *args])
        assert result.exit_code == 1
        assert json.loads(result.output)["command"] == command

    document.write_text("not JSON")
    malformed = CliRunner().invoke(cli, ["composition", "model", "apply", str(document)])
    assert malformed.exit_code == 1
    assert json.loads(malformed.output)["command"] == "composition model apply"
    document.write_text("{}")

    spec = _project()
    save_spec(spec, cwd=tmp_path)
    missing = CliRunner().invoke(cli, ["composition", "model", "inspect", "missing"])
    assert json.loads(missing.output)["command"] == "composition model inspect"

    stored = tmp_path / "moviestar" / "spec.json"
    corrupt = deepcopy(spec)
    corrupt["spatial"]["scenes"]["missing"] = corrupt["spatial"]["scenes"]["scene_a"]
    stored.write_text(json.dumps(corrupt))
    for args, command in [
        (["apply", str(document)], "composition model apply"),
        (["inspect", "scene_a"], "composition model inspect"),
    ]:
        result = CliRunner().invoke(cli, ["composition", "model", *args])
        assert result.exit_code == 1
        error = json.loads(result.output)
        assert error["command"] == command
        assert "unknown timeline scene" in error["error"]


@pytest.mark.parametrize(
    ("case", "path"),
    [
        ("input_type", "spatial.definitions.pair.inputs[0].type"),
        ("element_type", "spatial.definitions.pair.children[0].type"),
        ("later_null_definition", "spatial.definitions.broken"),
        ("huge_number", "spatial.definitions.pair.inputs[3].default"),
    ],
)
def test_malformed_spatial_shapes_return_json_and_leave_spec_intact(
    tmp_path, monkeypatch, case, path
):
    spec = _project()
    save_spec(spec, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    document = deepcopy(spec["spatial"])
    pair = document["definitions"]["pair"]
    if case == "input_type":
        pair["inputs"][0]["type"] = []
    elif case == "element_type":
        pair["children"][0]["type"] = {}
    elif case == "huge_number":
        pair["inputs"].append({"name": "huge", "type": "number", "default": 10**1000})
    else:
        pair["children"].append({"instance_id": "bad_use", "definition_id": "broken"})
        document["definitions"]["broken"] = None
    document_path = tmp_path / "malformed-spatial.json"
    document_path.write_text(json.dumps(document))

    result = CliRunner().invoke(cli, ["composition", "model", "apply", str(document_path)])
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["command"] == "composition model apply"
    assert path in error["error"]
    assert load_spec(cwd=tmp_path)["spatial"] == spec["spatial"]
