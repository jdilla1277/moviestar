"""Persisted spatial definitions and scene-owned visual trees."""

from copy import deepcopy
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.spatial_model import (
    SpatialModelError, compile_spatial_frame, edit_spatial_model, expand_spatial_scene,
    spatial_state_id,
)
from moviestar.spatial_edit import SpatialEditConflict
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
    spec["opening_transition"] = {"type": "dip-black", "duration": 1}
    save_spec(spec, cwd=tmp_path)
    transition = CliRunner().invoke(cli, ["composition", "model", "frame", "--at", "0:00:00.500"])
    assert transition.exit_code == 1
    error = json.loads(transition.output)
    assert error["command"] == "composition model frame"
    assert "opening_transition" in error["error"]


@pytest.mark.parametrize("pacing,at_s,expected_source_time,expected_scene_duration", [
    ({"id": "slow", "mode": "speed", "range": {
        "from": "0:00:00.000", "to": "0:00:08.000", "space": "source-local"},
      "speed": 0.5}, 10, 5, 16),
    ({"id": "pause", "mode": "hold", "at": "0:00:02.000",
      "space": "source-local", "duration": "0:00:02.000"}, 3, 2, 10),
])
def test_spatial_frame_follows_canonical_speed_and_hold(
    pacing, at_s, expected_source_time, expected_scene_duration
):
    spec = _project()
    spec["motion"] = {"version": 1, "scenes": [{
        "scene": "scene_a", "scene_id": "scene_a",
        "slots": [{"slot": "main", "pacing": [pacing], "camera": []}],
    }]}
    validate_spec(spec)
    timeline = resolve_project(_source_project(spec), spec)
    frame = compile_spatial_frame(spec, timeline, at_s, source_durations={"alice": 20, "bob": 20})
    media = frame["children"][0]["children"][0]["media"]
    assert frame["scene_duration_s"] == expected_scene_duration
    assert frame["scene_source_time_s"] == expected_source_time
    assert media["source_time_s"] == expected_source_time
    assert media["visible"] is True
    assert not any(item["code"] == "media_ends_before_scene" for item in frame["diagnostics"])


def test_spatial_frame_exact_end_samples_last_output_frame_under_slow_pacing():
    spec = _project()
    spec["composition"] = spec["composition"][:1]
    del spec["spatial"]["scenes"]["scene_b"]
    spec["motion"] = {"version": 1, "scenes": [{"scene": "scene_a", "slots": [{
        "slot": "main", "pacing": [{"id": "slow", "mode": "speed", "range": {
            "from": "0:00:00.000", "to": "0:00:08.000", "space": "source-local"},
            "speed": 0.5}], "camera": [],
    }]}]}
    timeline = resolve_project(_source_project(spec), spec)
    frame = compile_spatial_frame(spec, timeline, timeline.duration_s)
    assert frame["children"][0]["children"][0]["media"]["visible"] is True
    assert frame["scene_source_time_s"] < 8


def test_spatial_frame_exact_end_uses_last_grid_time_in_short_final_scene():
    spec = _project()
    spec["composition"][1]["slots"][0]["source_to"] = "0:00:00.010"
    bindings = spec["spatial"]["scenes"]["scene_b"]["layout"]["children"][0]["bindings"]
    bindings["left"]["source"]["end"] = 0.010
    bindings["right"]["source"]["end"] = 0.010
    validate_spec(spec)
    timeline = resolve_project(_source_project(spec), spec)
    assert timeline.duration_s == 8.010
    assert timeline.output_fps == 30
    for at_s in (8.0, 8.009, timeline.duration_s):
        frame = compile_spatial_frame(spec, timeline, at_s)
        assert frame["scene_id"] == "scene_b"
        assert frame["children"][0]["children"][0]["media"]["visible"] is True
    assert frame["scene_time_s"] == 0
    assert frame["result_time_s"] == 8.010
    assert frame["sample_time_s"] == 8.0
    assert frame["children"][0]["children"][0]["media"]["source_time_s"] == 0


def test_spatial_frame_rejects_active_internal_dissolve_without_partial_scene():
    spec = _project()
    for scene in spec["composition"]:
        scene["slots"][0]["source_from"] = "0:00:02.000"
        scene["slots"][0]["source_to"] = "0:00:10.000"
    for scene in spec["spatial"]["scenes"].values():
        for binding in scene["layout"]["children"][0]["bindings"].values():
            if "source" in binding:
                binding["source"] = {"start": 2, "end": 10}
    spec["composition"][1]["transition_in"] = {"type": "dissolve", "duration": 1}
    validate_spec(spec)
    timeline = resolve_project(_source_project(spec), spec)
    for at_s in (7.5, 8, 8.499):
        with pytest.raises(SpatialModelError, match=r"composition\[1\]\.transition_in.*unsupported"):
            compile_spatial_frame(spec, timeline, at_s)
    assert compile_spatial_frame(spec, timeline, 8.5)["scene_id"] == "scene_b"


@pytest.mark.parametrize("field,at_s", [
    ("opening_transition", 0),
    ("closing_transition", 16),
])
def test_spatial_frame_rejects_active_project_edge_transition(field, at_s):
    spec = _project()
    spec[field] = {"type": "dip-black", "duration": 1}
    validate_spec(spec)
    timeline = resolve_project(_source_project(spec), spec)
    with pytest.raises(SpatialModelError, match=field + ".*unsupported"):
        compile_spatial_frame(spec, timeline, at_s)


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


def _edit(spec, op, **fields):
    return edit_spatial_model(spec, {"op": op, **fields})[0]


def test_local_property_inherits_shared_updates_and_reset():
    spec = _project()
    alice = ["scene_a", "listeners", "left_video"]
    spec = _edit(spec, "instance.customize", target={"occurrence_path": alice},
                 property="style.border_color", value="blue")
    spec, impact = edit_spatial_model(spec, {"op": "component.update",
                                      "target": {"definition_id": "pair", "element_id": "left_video"},
                                      "property": "style.border_color", "value": "green"})
    assert impact["affected"] == ["scene_b"]
    assert impact["affected_occurrences"] == [["scene_b", "listeners", "left_video"]]
    assert expand_spatial_scene(spec, "scene_a")["children"][0]["children"][0]["style"] == {"border_color": "blue"}
    assert expand_spatial_scene(spec, "scene_b")["children"][0]["children"][0]["style"] == {"border_color": "green"}
    spec = _edit(spec, "instance.reset", target={"occurrence_path": alice},
                 property="style.border_color")
    assert expand_spatial_scene(spec, "scene_a")["children"][0]["children"][0]["style"] == {"border_color": "green"}


def test_local_structural_edits_survive_shared_update_and_conflict_atomically():
    spec = _project()
    parent = ["scene_a", "listeners"]
    spec = _edit(spec, "instance.child_insert", target={"occurrence_path": parent},
                 child={"id": "subtitle", "type": "text", "content": {"text": "Alice"}},
                 after_id="left_video")
    spec = _edit(spec, "instance.child_remove",
                 target={"occurrence_path": [*parent, "right_video"]})
    spec = _edit(spec, "instance.child_move",
                 target={"occurrence_path": [*parent, "subtitle"]}, after_id="name")
    spec = _edit(spec, "component.update",
                 target={"definition_id": "pair", "element_id": "left_video"},
                 property="style.border_width", value=3)
    alice = expand_spatial_scene(spec, "scene_a")["children"][0]["children"]
    bob = expand_spatial_scene(spec, "scene_b")["children"][0]["children"]
    assert [child["occurrence_path"][-1] for child in alice] == ["left_video", "name", "subtitle"]
    assert [child["occurrence_path"][-1] for child in bob] == ["left_video", "right_video", "name"]
    assert alice[0]["style"] == bob[0]["style"] == {"border_width": 3}
    before = deepcopy(spec)
    with pytest.raises(SpatialModelError, match="scene_a.*listeners.*right_video"):
        _edit(spec, "component.child_remove",
              target={"definition_id": "pair", "element_id": "right_video"})
    assert spec == before
    spec = _edit(spec, "instance.reset",
                 target={"occurrence_path": [*parent, "right_video"]})
    spec = _edit(spec, "component.child_remove",
                 target={"definition_id": "pair", "element_id": "right_video"})
    assert [child["occurrence_path"][-1] for child in expand_spatial_scene(spec, "scene_a")["children"][0]["children"]] == [
        "left_video", "name", "subtitle"]


def test_nested_occurrence_customization_and_variation_do_not_change_other_uses():
    spec = _project()
    spec["spatial"]["definitions"]["badge"] = {"name": "Badge", "inputs": [], "children": [
        {"id": "words", "type": "text", "content": {"text": "default"}},
    ]}
    spec["spatial"]["definitions"]["pair"]["children"].append(
        {"instance_id": "badge_use", "definition_id": "badge"})
    target = ["scene_a", "listeners", "badge_use", "words"]
    spec = _edit(spec, "instance.customize", target={"occurrence_path": target},
                 property="style.color", value="blue")
    assert expand_spatial_scene(spec, "scene_a")["children"][0]["children"][-1]["children"][0]["style"] == {"color": "blue"}
    assert expand_spatial_scene(spec, "scene_b")["children"][0]["children"][-1]["children"][0]["style"] == {}
    spec = _edit(spec, "component.save_variation",
                 source={"occurrence_path": ["scene_a", "listeners"]},
                 new_definition_id="pair_blue", relink_source=True)
    assert spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["definition_id"] == "pair_blue"
    assert spec["spatial"]["scenes"]["scene_b"]["layout"]["children"][0]["definition_id"] == "pair"
    assert expand_spatial_scene(spec, "scene_a")["children"][0]["children"][-1]["children"][0]["style"] == {"color": "blue"}
    assert expand_spatial_scene(spec, "scene_b")["children"][0]["children"][-1]["children"][0]["style"] == {}


def test_model_edit_dry_run_save_reload_and_undo(tmp_path, monkeypatch):
    spec = _project()
    save_spec(spec, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    inspected = CliRunner().invoke(cli, ["composition", "model", "inspect", "scene_a"])
    state_id = json.loads(inspected.output)["state_id"]
    request = {"op": "instance.child_insert", "target": {"occurrence_path": ["scene_a", "listeners"]},
               "child": {"id": "subtitle", "type": "text", "content": {"text": "Alice"}},
               "after_id": "left_video", "expected_state_id": state_id}
    document = tmp_path / "edit.json"
    document.write_text(json.dumps(request))
    preview = CliRunner().invoke(cli, ["composition", "model", "edit", str(document), "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)["writes_spec"] is False
    assert json.loads(preview.output)["state_id_before"] == state_id
    assert load_spec(cwd=tmp_path)["spatial"] == spec["spatial"]
    applied = CliRunner().invoke(cli, ["composition", "model", "edit", str(document)])
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["writes_spec"] is True
    assert json.loads(applied.output)["state_id_after"] != state_id
    assert expand_spatial_scene(load_spec(cwd=tmp_path), "scene_a")["children"][0]["children"][1]["occurrence_path"][-1] == "subtitle"
    stale = CliRunner().invoke(cli, ["composition", "model", "edit", str(document)])
    assert stale.exit_code == 1
    assert "expected_state_id" in json.loads(stale.output)["error"]
    restored, _ = pop_revision(load_spec(cwd=tmp_path))
    assert restored["spatial"] == spec["spatial"]


def test_shared_removal_reports_every_customized_occurrence_without_writing(tmp_path, monkeypatch):
    spec = _project()
    for scene_id in ("scene_a", "scene_b"):
        spec = _edit(spec, "instance.customize",
                     target={"occurrence_path": [scene_id, "listeners", "right_video"]},
                     property="style.border_color", value="blue")
    save_spec(spec, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    request = {"op": "component.child_remove",
               "target": {"definition_id": "pair", "element_id": "right_video"}}
    document = tmp_path / "edit.json"
    document.write_text(json.dumps(request))
    rejected = CliRunner().invoke(cli, ["composition", "model", "edit", str(document), "--dry-run"])
    assert rejected.exit_code == 1
    output = json.loads(rejected.output)
    assert output["writes_spec"] is False
    assert [conflict["path"] for conflict in output["conflicts"]] == [
        ["scene_a", "listeners", "right_video"], ["scene_b", "listeners", "right_video"]]
    assert load_spec(cwd=tmp_path)["spatial"] == spec["spatial"]


def test_nested_structure_and_reset_subtree():
    spec = _project()
    spec["spatial"]["definitions"]["badge"] = {"name": "Badge", "children": [
        {"id": "words", "type": "text", "content": {"text": "default"}},
    ]}
    spec["spatial"]["definitions"]["pair"]["children"].append(
        {"instance_id": "badge_use", "definition_id": "badge"})
    parent = ["scene_a", "listeners", "badge_use"]
    spec = _edit(spec, "instance.child_insert", target={"occurrence_path": parent},
                 child={"id": "detail", "type": "text", "content": {"text": "Alice"}},
                 after_id="words")
    spec = _edit(spec, "instance.customize", target={"occurrence_path": [*parent, "detail"]},
                 property="style.color", value="blue")
    nested = expand_spatial_scene(spec, "scene_a")["children"][0]["children"][-1]["children"]
    assert [item["occurrence_path"][-1] for item in nested] == ["words", "detail"]
    assert nested[1]["style"] == {"color": "blue"}
    spec = _edit(spec, "instance.reset", target={"occurrence_path": [*parent, "detail"]})
    assert [item["occurrence_path"][-1] for item in expand_spatial_scene(spec, "scene_a")["children"][0]["children"][-1]["children"]] == ["words"]
    assert expand_spatial_scene(spec, "scene_a")["diagnostics"] == []


def test_nested_insert_can_bind_to_nested_component_input():
    spec = _project()
    spec["spatial"]["definitions"]["badge"] = {"name": "Badge", "inputs": [
        {"name": "caption", "type": "text", "default": "hello"}],
        "children": [{"id": "words", "type": "text", "content": {"input": "caption"}}]}
    spec["spatial"]["definitions"]["pair"]["children"].append(
        {"instance_id": "badge_use", "definition_id": "badge"})
    spec = _edit(spec, "instance.child_insert",
                 target={"occurrence_path": ["scene_a", "listeners", "badge_use"]},
                 child={"id": "echo", "type": "text", "content": {"input": "caption"}},
                 after_id="words")
    nested = expand_spatial_scene(spec, "scene_a")["children"][0]["children"][-1]["children"]
    assert nested[1]["content"] == {"text": "hello"}


def test_component_and_image_deletion_guard_includes_unused_drafts():
    spec = _project()
    spec["spatial"]["definitions"]["draft"] = {"name": "Unplaced draft", "children": [
        {"instance_id": "nested", "definition_id": "pair"},
        {"id": "logo", "type": "image", "content": {"asset_id": "logo"}},
    ]}
    spec["spatial"]["assets"] = {"logo": {"type": "image", "path": "assets/logo.png"}}
    with pytest.raises(SpatialModelError, match="referenced.*draft.*nested"):
        _edit(spec, "component.delete", definition_id="pair")
    with pytest.raises(SpatialModelError, match="referenced.*definitions.*draft"):
        _edit(spec, "asset.delete", asset_id="logo")
    spec = _edit(spec, "component.delete", definition_id="draft")
    spec = _edit(spec, "asset.delete", asset_id="logo")
    assert "draft" not in spec["spatial"]["definitions"]
    assert "logo" not in spec["spatial"]["assets"]


def test_shared_edit_checks_customizations_in_unplaced_definition():
    spec = _project()
    spec["spatial"]["definitions"]["draft"] = {"name": "Unplaced draft", "children": [
        {"instance_id": "nested", "definition_id": "pair", "customizations": [
            {"op": "property_set", "path": ["left_video"],
             "property": "style.border_color", "value": "blue"}]}]}
    with pytest.raises(SpatialEditConflict) as rejected:
        _edit(spec, "component.child_remove",
              target={"definition_id": "pair", "element_id": "left_video"})
    assert ["definition:draft", "nested", "left_video"] in [
        conflict["path"] for conflict in rejected.value.conflicts]


def test_shared_insert_and_local_reorder_follow_stable_anchors():
    spec = _project()
    spec = _edit(spec, "instance.child_move",
                 target={"occurrence_path": ["scene_a", "listeners", "name"]},
                 after_id="left_video")
    spec = _edit(spec, "component.child_insert", target={"definition_id": "pair"},
                 child={"id": "divider", "type": "shape"}, after_id="right_video")
    alice = expand_spatial_scene(spec, "scene_a")["children"][0]["children"]
    bob = expand_spatial_scene(spec, "scene_b")["children"][0]["children"]
    assert [item["occurrence_path"][-1] for item in alice] == ["left_video", "name", "right_video", "divider"]
    assert [item["occurrence_path"][-1] for item in bob] == ["left_video", "right_video", "divider", "name"]
    with pytest.raises(SpatialEditConflict, match="scene_a/listeners/name"):
        _edit(spec, "component.child_remove",
              target={"definition_id": "pair", "element_id": "left_video"})


def test_duplicate_rebind_and_rename_preserve_component_identity():
    spec = _project()
    spec = _edit(spec, "instance.duplicate", source={"occurrence_path": ["scene_a", "listeners"]},
                 new_instance_id="listeners_copy")
    spec = _edit(spec, "input.bind", target={"occurrence_path": ["scene_a", "listeners_copy"]},
                 input="left", value={"asset_id": "dan", "source": {"start": 3, "end": 11}})
    spec = _edit(spec, "component.rename", definition_id="pair", name="Two listeners")
    first, copy = expand_spatial_scene(spec, "scene_a")["children"]
    assert first["definition_id"] == copy["definition_id"] == "pair"
    assert first["children"][0]["content"]["asset_id"] == "alice"
    assert copy["children"][0]["content"]["asset_id"] == "dan"
    assert first["children"][0]["occurrence_path"] != copy["children"][0]["occurrence_path"]
    assert spec["spatial"]["definitions"]["pair"]["name"] == "Two listeners"


def test_variation_bakes_local_structure_without_losing_nested_references():
    spec = _project()
    parent = ["scene_a", "listeners"]
    spec = _edit(spec, "instance.child_insert", target={"occurrence_path": parent},
                 child={"id": "subtitle", "type": "text", "content": {"text": "Alice"}},
                 after_id="left_video")
    spec = _edit(spec, "instance.child_remove",
                 target={"occurrence_path": [*parent, "right_video"]})
    spec = _edit(spec, "component.save_variation", source={"occurrence_path": parent},
                 new_definition_id="pair_solo", relink_source=True)
    assert "customizations" not in spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]
    assert [item["id"] for item in spec["spatial"]["definitions"]["pair_solo"]["children"]] == [
        "left_video", "subtitle", "name"]
    assert [item["id"] for item in spec["spatial"]["definitions"]["pair"]["children"]] == [
        "left_video", "right_video", "name"]
    assert expand_spatial_scene(spec, "scene_a")["diagnostics"] == []


def test_variation_keeps_child_added_under_original_leaf_element():
    spec = _project()
    spec = _edit(spec, "instance.child_insert",
                 target={"occurrence_path": ["scene_a", "listeners", "left_video"]},
                 child={"id": "overlay", "type": "text", "content": {"text": "Alice"}})
    before = expand_spatial_scene(spec, "scene_a")["children"][0]["children"][0]
    assert before["children"][0]["occurrence_path"][-1] == "overlay"
    spec = _edit(spec, "component.save_variation",
                 source={"occurrence_path": ["scene_a", "listeners"]},
                 new_definition_id="pair_with_overlay", relink_source=True)
    after = expand_spatial_scene(spec, "scene_a")["children"][0]["children"][0]
    assert after["children"] == before["children"]


def test_scene_root_layout_property_changes_and_resets_existing_layout():
    spec = _project()
    target = {"occurrence_path": ["scene_a"]}
    spec = _edit(spec, "instance.customize", target=target,
                 property="layout.columns", value="1fr 2fr")
    assert spec["spatial"]["scenes"]["scene_a"]["layout"]["columns"] == "1fr 2fr"
    assert "layout" not in spec["spatial"]["scenes"]["scene_a"]["layout"]
    assert expand_spatial_scene(spec, "scene_a")["columns"] == "1fr 2fr"
    spec = _edit(spec, "instance.reset", target=target, property="layout.columns")
    assert "columns" not in spec["spatial"]["scenes"]["scene_a"]["layout"]


@pytest.mark.parametrize("bad_op", [[], {"name": "child_add"}])
def test_model_apply_reports_malformed_customization_op_as_json(tmp_path, monkeypatch, bad_op):
    spec = _project()
    save_spec(spec, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    document = deepcopy(spec["spatial"])
    document["scenes"]["scene_a"]["layout"]["children"][0]["customizations"] = [
        {"op": bad_op, "path": ["left_video"]}]
    source = tmp_path / "invalid-spatial.json"
    source.write_text(json.dumps(document))
    result = CliRunner().invoke(cli, ["composition", "model", "apply", str(source)])
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["command"] == "composition model apply"
    assert "customizations[0].op" in error["error"]
    assert load_spec(cwd=tmp_path)["spatial"] == spec["spatial"]


def test_reorder_impact_reports_changed_occurrence_positions():
    spec = _project()
    spec, local = edit_spatial_model(spec, {"op": "instance.child_move",
        "target": {"occurrence_path": ["scene_a", "listeners", "name"]},
        "after_id": "left_video"})
    assert local["affected"] == ["scene_a"]
    assert ["scene_a", "listeners", "name"] in local["affected_occurrences"]
    assert ["scene_a", "listeners", "right_video"] in local["affected_occurrences"]
    _, shared = edit_spatial_model(spec, {"op": "component.child_move",
        "target": {"definition_id": "pair", "element_id": "name"},
        "after_id": "left_video"})
    assert ["scene_b", "listeners", "right_video"] in shared["affected_occurrences"]
    assert ["scene_b", "listeners", "name"] in shared["affected_occurrences"]


def test_state_id_rejects_stale_edit_after_history_cap_and_undo_branch(tmp_path):
    spec = _project()
    save_spec(spec, cwd=tmp_path)
    for value in range(51):
        current = load_spec(cwd=tmp_path)
        if value == 50:
            stale_id = spatial_state_id(current)
            assert len(current["revisions"]) == 50
        changed = deepcopy(current)
        changed["spatial"]["definitions"]["pair"]["children"][0]["style"] = {"border_width": value}
        save_spec(changed, cwd=tmp_path, command="change border")
    latest = load_spec(cwd=tmp_path)
    assert len(latest["revisions"]) == 50
    assert spatial_state_id(latest) != stale_id
    with pytest.raises(SpatialModelError, match="expected_state_id"):
        edit_spatial_model(latest, {"op": "component.update", "expected_state_id": stale_id,
            "target": {"definition_id": "pair", "element_id": "left_video"},
            "property": "style.border_color", "value": "blue"})

    restored, _ = pop_revision(latest)
    save_spec(restored, cwd=tmp_path, record=False)
    branch = deepcopy(restored)
    branch["spatial"]["definitions"]["pair"]["children"][0]["style"] = {"border_width": 999}
    save_spec(branch, cwd=tmp_path, command="branch edit")
    assert len(load_spec(cwd=tmp_path)["revisions"]) == len(latest["revisions"])
    with pytest.raises(SpatialModelError, match="expected_state_id"):
        edit_spatial_model(load_spec(cwd=tmp_path), {"op": "component.update",
            "expected_state_id": spatial_state_id(latest),
            "target": {"definition_id": "pair", "element_id": "left_video"},
            "property": "style.border_color", "value": "green"})


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


def test_export_plans_saved_spatial_scene_tree(tmp_path, monkeypatch):
    spec = _project()
    save_spec(spec, cwd=tmp_path)
    save_project(_source_project(spec), cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["export", "--dry-run"])
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["mode"] == "spatial_composition"
    assert len(output["spatial_render_plan"]) == 2


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
