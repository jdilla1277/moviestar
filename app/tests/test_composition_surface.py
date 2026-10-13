"""The milestone-zero composition surface is executable but read-only."""

import json

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
    assert len(referenced["diagnostics"][0]["uses"]) == 2


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
