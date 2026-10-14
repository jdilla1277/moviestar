"""The milestone-zero composition surface is executable but read-only."""

import json
import re
import shlex

from click.testing import CliRunner

from moviestar.cli import cli


def invoke(*args: str):
    return CliRunner().invoke(cli, ["composition", *args])


def payload(*args: str) -> dict:
    result = invoke(*args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_discoverable_mock_and_operation_help() -> None:
    root = CliRunner().invoke(cli, ["--help"])
    assert "composition" in root.output
    for command in ("edit", "check", "inspect"):
        result = invoke(command, "--help")
        assert result.exit_code == 0
        assert "MOCK" in result.output
        assert "writes_spec" in result.output
        assert "hint" in result.output


def test_worked_edit_cases_are_explicitly_mocked_and_scoped(tmp_path, monkeypatch) -> None:
    cases = payload()["cases"]
    monkeypatch.chdir(tmp_path)
    for case in cases:
        result = payload("edit", "--example", case)
        assert result["status"] == "mock_only"
        assert result["writes_spec"] is False
        assert result["case"] == case
        assert result["example_request"]["scope"]
        assert result["example_result"]["affected"] is not None
        assert result["hint"]
    assert list(tmp_path.iterdir()) == []


def test_nested_occurrence_and_shared_update_effects() -> None:
    local = payload("edit", "--example", "customize")
    shared = payload("edit", "--example", "update")
    assert local["example_request"]["target"]["occurrence_path"] == [
        "scene_interview_a", "inst_listeners_a", "el_left_frame"
    ]
    assert local["example_result"]["affected"] == ["scene_interview_a"]
    assert shared["example_request"]["target"]["definition_id"] == "cmp_listener_pair"
    assert shared["example_result"]["affected"] == ["scene_interview_a", "scene_interview_b"]


def test_conflicts_and_incomplete_draft_are_addressed() -> None:
    ready = payload("check", "--scene", "scene_interview_b")
    assert ready["scene_id"] == "scene_interview_b"
    assert ready["can_export"] is True
    conflict = payload("check", "--example", "conflict")
    assert conflict["diagnostics"][0]["target"]["occurrence_path"]
    assert conflict["can_apply"] is False
    draft = payload("check", "--example", "draft")
    assert draft["can_save"] is True
    assert draft["can_export"] is False
    assert draft["reloaded_revision"] == payload("edit", "--example", "draft")["example_result"]["revision"]
    assert draft["diagnostics"][0]["target"]["occurrence_path"]
    referenced = payload("check", "--example", "referenced")
    assert referenced["can_apply"] is False
    assert len(referenced["diagnostics"][0]["uses"]) == 3


def test_inspection_has_layout_and_rendered_views_at_a_moment() -> None:
    layout = payload("inspect", "scene_interview_a", "--at", "00:03.000", "--canvas", "1080x1920", "--view", "layout")
    rendered = payload("inspect", "scene_interview_a", "--at", "00:03.000", "--canvas", "1080x1920", "--view", "rendered")
    assert layout["view"] == "layout"
    assert layout["frame"]["time"] == "00:03.000"
    assert layout["frame"]["canvas"] == {"width": 1080, "height": 1920}
    assert layout["frame"]["boxes"][0]["occurrence_path"]
    assert layout["artifact"] is None
    assert rendered["view"] == "rendered"
    assert rendered["artifact"] is None


def test_mock_rejects_unknown_cases_and_bad_canvas() -> None:
    assert invoke("edit", "--example", "missing").exit_code != 0
    assert invoke("inspect", "scene_interview_a", "--at", "3", "--canvas", "bad").exit_code != 0


def test_edit_accepts_a_json_request_but_never_applies_it(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    request = {"op": "component.update", "scope": "definition", "target": {"definition_id": "cmp_listener_pair"}, "value": 4}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    response = payload("edit", str(path))
    assert response["submitted_request"] == request
    assert response["example_result"]["affected"] == ["scene_interview_a", "scene_interview_b"]
    assert response["writes_spec"] is False
    assert sorted(p.name for p in tmp_path.iterdir()) == ["request.json"]


def test_duration_cases_choose_one_timeline_rule() -> None:
    unequal = payload("check", "--example", "unequal_media")
    assert unequal["resolved_duration_seconds"] == 8
    assert unequal["can_export"] is True
    assert unequal["diagnostics"][0]["severity"] == "warning"
    assert unequal["diagnostics"][0]["code"] == "media_ends_before_scene"
    missing = payload("check", "--example", "missing_media")
    assert missing["resolved_duration_seconds"] is None
    assert missing["can_export"] is False
    assert missing["diagnostics"][0]["severity"] == "error"
    explicit = payload("check", "--example", "explicit_duration")
    assert explicit["resolved_duration_seconds"] == 8
    assert explicit["can_export"] is True
    static = payload("check", "--example", "static_only")
    assert static["resolved_duration_seconds"] == 6
    assert static["can_export"] is True


def test_fixture_has_defined_children_forwarding_nested_use_and_repair() -> None:
    fixture = payload("fixture")["project"]
    definitions = fixture["definitions"]
    scenes = fixture["scenes"]
    pair = definitions["cmp_listener_pair"]
    assert {child["id"] for child in pair["children"] if "id" in child} == {
        "el_left_frame", "el_right_frame"
    }
    assert next(child for child in pair["children"] if child.get("instance_id") == "inst_pair_badge")["bindings"] == {
        "label": {"forward": "optional_tag"}
    }
    assert definitions["cmp_badge"]["children"][0]["content"] == {"input": "label"}
    assert pair["children"][0]["content"] == {"input": "left"}
    assert pair["children"][1]["content"] == {"input": "right"}
    assert scenes["scene_explainer"]["layout"]["children"][0]["instance_id"] == "inst_text_panel"
    assert payload("check", "--example", "draft")["diagnostics"][0]["target"]["occurrence_path"] == [
        "scene_explainer", "inst_text_panel", "el_logo"
    ]
    assert payload("edit", "--example", "repair")["example_request"]["target"]["occurrence_path"] == [
        "scene_explainer", "inst_text_panel"
    ]
    nested = payload("inspect", "scene_interview_a", "--at", "00:03.000", "--canvas", "1080x1920", "--view", "layout")
    assert ["scene_interview_a", "inst_listeners_a", "inst_pair_badge", "el_badge_text"] in [
        box["occurrence_path"] for box in nested["frame"]["boxes"]
    ]


def test_canned_graph_references_and_reported_occurrences_are_consistent() -> None:
    project = payload("fixture")["project"]
    definitions, assets = project["definitions"], project["assets"]
    paths = set()

    def walk(children: list[dict], path: tuple[str, ...], inputs: set[str]) -> None:
        for child in children:
            if "instance_id" in child:
                definition = definitions[child["definition_id"]]
                target_inputs = {item["name"] for item in definition["inputs"]}
                assert child.get("bindings", {}).keys() <= target_inputs
                for value in child.get("bindings", {}).values():
                    if "forward" in value:
                        assert value["forward"] in inputs
                    if "asset_id" in value:
                        assert value["asset_id"] in assets
                next_path = (*path, child["instance_id"])
                paths.add(next_path)
                walk(definition["children"], next_path, target_inputs)
            else:
                next_path = (*path, child["id"])
                paths.add(next_path)
                content = child.get("content", {})
                if "input" in content:
                    assert content["input"] in inputs
                if "asset_id" in content:
                    assert content["asset_id"] in assets

    for scene_id, scene in project["scenes"].items():
        walk(scene["layout"]["children"], (scene_id,), set())

    for example in ("draft", "conflict", "unequal_media", "missing_media", "explicit_duration", "referenced"):
        for diagnostic in payload("check", "--example", example)["diagnostics"]:
            target = diagnostic["target"]
            if "occurrence_path" in target:
                assert tuple(target["occurrence_path"]) in paths
            for use in diagnostic.get("uses", []):
                assert tuple(use["occurrence_path"]) in paths
    for scene_id in project["scenes"]:
        inspection = payload("inspect", scene_id, "--at", "00:03.000", "--canvas", "1080x1920", "--view", "layout")
        assert {tuple(box["occurrence_path"]) for box in inspection["frame"]["boxes"]} <= paths


def test_structured_captions_require_an_element_and_hints_run() -> None:
    with_caption = payload("check", "--example", "with_caption")
    without_caption = payload("check", "--example", "without_caption")
    assert with_caption["generated_captions"] is True
    assert with_caption["caption_occurrences"] == [{"occurrence_path": ["scene_interview_a", "el_caption_a"]}]
    assert without_caption["generated_captions"] is False
    assert without_caption["caption_occurrences"] == []
    assert "moviestar composition check --example draft" in payload("edit", "--example", "draft")["example_result"]["hint"]
    assert payload("check", "--example", "draft")["can_export"] is False
    help_text = " ".join(invoke("--help").output.split())
    assert "composition inspect scene_interview_a --at 00:03.000 --canvas 1080x1920 --view layout" in help_text
    assert invoke("inspect", "scene_interview_a", "--at", "00:03.000", "--canvas", "1080x1920", "--view", "layout").exit_code == 0


def test_mock_hints_are_copy_pasteable() -> None:
    responses = [payload("fixture"), payload("check", "--example", "draft")]
    responses.extend(payload("edit", "--example", case) for case in payload()["cases"])
    for response in responses:
        for hint in (response.get("hint"), response.get("example_result", {}).get("hint")):
            if not hint:
                continue
            match = re.search(r"'((?:moviestar )?composition [^']+)'", hint)
            assert match, hint
            command = shlex.split(match.group(1))
            if command[0] == "moviestar":
                command.pop(0)
            assert invoke(*command[1:]).exit_code == 0, hint
