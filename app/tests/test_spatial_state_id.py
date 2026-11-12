"""Numeric identity survives lossless authoring round trips without weakening fencing."""

from copy import deepcopy
import json
import math
import shutil
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.local_preview import PreviewSession, read_snapshot
from moviestar.project import save_project
from moviestar.spatial_model import spatial_state_id
from moviestar.spec import load_spec, pop_revision, save_spec, validate_spec
from tests.test_live_browser_preview import live_project  # noqa: F401 -- fixture
from tests.test_spatial_model import _project, _source_project


def state(value):
    return {"spatial": {"nested": [{"value": value}]}, "revisions": []}


@pytest.mark.parametrize("left,right", [
    (1, 1.0), (0, -0.0), (0, 0.0), (-17, -17.0),
    (2**60, float(2**60)),
    ({"x": [1, -2, {"at": 0}]}, {"x": [1.0, -2.0, {"at": -0.0}]}),
])
def test_equivalent_numbers_have_the_same_identity(left, right):
    assert spatial_state_id(state(left)) == spatial_state_id(state(right))


@pytest.mark.parametrize("left,right", [
    (False, 0), (True, 1), (True, 1.0), ("1", 1), (None, 0),
    (2**53, 2**53+1), (2**100, 2**100+1),
    (0.0, math.nextafter(0.0, 1.0)),
    (1.1, math.nextafter(1.1, math.inf)),
    (2, math.nextafter(2.0, math.inf)),
    ([1, 2], [2, 1]),
])
def test_distinct_values_do_not_share_an_identity(left, right):
    assert spatial_state_id(state(left)) != spatial_state_id(state(right))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_numbers_are_not_hashed_as_json(value):
    with pytest.raises(ValueError, match="finite"):
        spatial_state_id(state(value))


def numeric_project():
    spec = _project()
    # Normal loaded projects already have slot IDs. Keep legacy ID backfilling
    # during CLI undo separate from this numeric identity regression.
    for index, scene in enumerate(spec["composition"], 1):
        scene["slots"][0]["id"] = f"slot_{index:04d}"
    binding = spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["left"]
    binding["source"] = {"start": 1.0, "end": 8.5}
    node = spec["spatial"]["definitions"]["pair"]["children"][0]
    node["layout"]["width"] = 200.0
    node["style"] = {"border_width": 2.0, "opacity": 1.0}
    validate_spec(spec)
    return spec


def test_hashing_does_not_rewrite_authored_values_or_include_undo_history():
    spec = numeric_project()
    spec["revisions"] = [{"command": "prior", "changed": {"spatial": {"old": 1.0}}}]
    before = json.dumps(spec, sort_keys=True)
    identity = spatial_state_id(spec)
    assert json.dumps(spec, sort_keys=True) == before
    without_history = deepcopy(spec)
    without_history["revisions"] = []
    assert spatial_state_id(without_history) == identity


def javascript_round_trip(value):
    node = shutil.which("node")
    if node is None:
        pytest.skip("real JavaScript authoring round trip requires Node")
    result = subprocess.run([
        node, "-e", "process.stdin.setEncoding('utf8');let text='';process.stdin.on('data',x=>text+=x);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(text))));",
    ], input=json.dumps(value), capture_output=True, text=True, check=True)
    return result.stdout


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_real_javascript_cli_round_trip_noop_edit_and_undo(tmp_path, monkeypatch):
    spec = numeric_project()
    save_spec(spec, cwd=tmp_path)
    save_project(_source_project(spec), cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    before = load_spec(tmp_path)
    inspected = invoke("composition", "model", "inspect", "scene_a")
    identity = inspected["state_id"]
    document = tmp_path / "model.json"
    document.write_text(javascript_round_trip(before["spatial"]))
    assert '"start":1,' in document.read_text()
    response = invoke("composition", "model", "apply", str(document))
    assert response["state_id"] == identity
    assert response["revision"] == len(before["revisions"])
    saved = load_spec(tmp_path)
    assert saved["spatial"] == before["spatial"]
    # Apply still preserves what the incoming document actually authored.
    binding = saved["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["left"]
    assert type(binding["source"]["start"]) is int
    assert binding["source"]["end"] == 8.5
    request = {"op": "component.update", "expected_state_id": identity,
               "target": {"definition_id": "pair", "element_id": "left_video"},
               "property": "style.border_width", "value": 2.0}
    edit = tmp_path / "edit.json"
    edit.write_text(json.dumps(request))
    noop = invoke("composition", "model", "edit", str(edit))
    assert noop["state_id_before"] == noop["state_id_after"] == identity
    assert len(load_spec(tmp_path)["revisions"]) == len(before["revisions"])
    request["value"] = math.nextafter(2.0, math.inf)
    edit.write_text(json.dumps(request))
    preview = invoke("composition", "model", "edit", str(edit), "--dry-run")
    changed = invoke("composition", "model", "edit", str(edit))
    assert changed["state_id_before"] == identity
    assert changed["state_id_after"] == preview["state_id_after"] != identity
    assert len(load_spec(tmp_path)["revisions"]) == len(before["revisions"]) + 1
    assert load_spec(tmp_path)["spatial"]["definitions"]["pair"]["children"][0]["style"]["border_width"] == request["value"]
    durable = (tmp_path / "moviestar/spec.json").read_bytes()
    stale = CliRunner().invoke(cli, ["composition", "model", "edit", str(edit)])
    assert stale.exit_code == 1
    assert "expected_state_id" in json.loads(stale.stdout)["error"]
    assert (tmp_path / "moviestar/spec.json").read_bytes() == durable
    invoke("undo")
    restored = load_spec(tmp_path)
    assert {key: value for key, value in restored.items() if key != "revisions"} == {
        key: value for key, value in before.items() if key != "revisions"
    }
    assert invoke("composition", "model", "inspect", "scene_a")["state_id"] == identity
    assert len(load_spec(tmp_path)["revisions"]) == len(before["revisions"])


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True])
def test_invalid_numeric_document_fails_before_changing_project(tmp_path, monkeypatch, value):
    spec = numeric_project()
    save_spec(spec, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "moviestar/spec.json"
    before = path.read_bytes()
    spec["spatial"]["scenes"]["scene_a"]["layout"]["children"][0]["bindings"]["left"]["source"]["start"] = value
    document = tmp_path / "invalid.json"
    document.write_text(json.dumps(spec["spatial"]))
    result = CliRunner().invoke(cli, ["composition", "model", "apply", str(document)])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["command"] == "composition model apply"
    assert "finite" in body["error"]
    assert path.read_bytes() == before


def test_numeric_noop_does_not_refresh_but_undo_gets_a_new_fenced_request(live_project):
    spec, _, root = live_project
    spec["spatial"]["scenes"]["scene"]["layout"]["children"][0]["bindings"]["clip"]["source"]["start"] = 1.0
    save_spec(spec, cwd=root)
    session = PreviewSession(root, 0, 5, root / "packets", mode="native")
    session.refresh()
    initial = session.queue.take()
    assert session.queue.finish(initial["request_id"], media="initial.json")
    equivalent = load_spec(root)
    equivalent["spatial"]["scenes"]["scene"]["layout"]["children"][0]["bindings"]["clip"]["source"]["start"] = 1
    save_spec(equivalent, cwd=root, command="composition model apply")
    session.refresh()
    assert read_snapshot(root, 0, 5)["state_id"] == initial["state_id"]
    assert session.queue.state()["latest"]["request_id"] == initial["request_id"]
    assert load_spec(root)["revisions"] == []
    changed = load_spec(root)
    changed["spatial"]["scenes"]["scene"]["layout"]["children"][1]["content"]["text"] = "Changed"
    save_spec(changed, cwd=root, command="composition model edit")
    session.refresh()
    editing = session.queue.take()
    assert editing["state_id"] != initial["state_id"]
    restored, _ = pop_revision(load_spec(root))
    save_spec(restored, cwd=root, record=False)
    session.refresh()
    latest = session.queue.state()["latest"]
    assert latest["state_id"] == initial["state_id"]
    assert latest["request_id"] not in {initial["request_id"], editing["request_id"]}
    assert not session.queue.finish(editing["request_id"], media="stale.json")
    assert not session.queue.state()["is_current"]
    assert session.queue.take()["request_id"] == latest["request_id"]
    assert session.queue.finish(latest["request_id"], media="restored.json")
    assert session.queue.state()["is_current"]


def test_unchanged_state_still_refreshes_for_changed_project_metadata_and_source(live_project):
    _, project, root = live_project
    session = PreviewSession(root, 0, 5, root / "packets", mode="native")
    session.refresh()
    initial = session.queue.state()["latest"]
    project["sources"][0]["width"] = 1280
    save_project(project, cwd=root)
    session.refresh()
    metadata = session.queue.state()["latest"]
    assert metadata["state_id"] == initial["state_id"]
    assert metadata["request_id"] != initial["request_id"]
    (root / "camera.mp4").touch()
    session.refresh()
    asset = session.queue.state()["latest"]
    assert asset["state_id"] == metadata["state_id"]
    assert asset["request_id"] != metadata["request_id"]


def test_existing_id_is_preserved_when_numbers_already_have_canonical_spelling():
    spec = {"spatial": {"label": "café", "width": 320, "ratio": 0.625, "enabled": True}, "revisions": []}
    assert spatial_state_id(spec) == "sha256:dc9d6304f25090eddf141b2b9c3e21f0bb4450e56af12e16d69abc134728f777"


def test_real_javascript_keeps_lossless_numeric_values_equivalent():
    value = [1.0, -0.0, 0.000001, 0.0000001, 1.2345678901234567,
             math.nextafter(2.0, math.inf), math.nextafter(0.0, 1.0), 2**53-1,
             True, False, "1.0"]
    returned = json.loads(javascript_round_trip(value))
    assert spatial_state_id(state(returned)) == spatial_state_id(state(value))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_unhashable_metadata_is_rejected_before_model_apply_writes(tmp_path, monkeypatch, value):
    spec = numeric_project()
    save_spec(spec, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "moviestar/spec.json"
    before = path.read_bytes()
    # Opaque metadata passes the structural schema; identity must be checked
    # before committing a document that cannot produce the promised state ID.
    spec["spatial"]["definitions"]["pair"]["metadata"] = {"nested": [value]}
    validate_spec(spec)
    document = tmp_path / "metadata.json"
    document.write_text(json.dumps(spec["spatial"]))
    result = CliRunner().invoke(cli, ["composition", "model", "apply", str(document)])
    assert result.exit_code == 1
    assert path.read_bytes() == before
    body = json.loads(result.stdout)
    assert body["command"] == "composition model apply"
    assert "finite" in body["error"]


def test_inspecting_existing_nonfinite_metadata_reports_error_without_rewriting(tmp_path, monkeypatch):
    spec = numeric_project()
    spec["spatial"]["definitions"]["pair"]["metadata"] = {"value": math.nan}
    save_spec(spec, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "moviestar/spec.json"
    before = path.read_bytes()
    result = CliRunner().invoke(cli, ["composition", "model", "inspect", "scene_a"])
    assert result.exit_code == 1
    assert result.stdout, repr(result.exception)
    body = json.loads(result.stdout)
    assert body["command"] == "composition model inspect"
    assert "finite" in body["error"]
    assert path.read_bytes() == before
