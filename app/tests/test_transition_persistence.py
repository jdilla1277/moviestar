"""M29 persisted transition schema and revision contracts."""

import copy
import json
from importlib.resources import files

import pytest

from moviestar.spec import (
    REVISION_FIELDS,
    SpecValidationError,
    empty_spec,
    load_spec,
    pop_revision,
    set_scene_composition,
    validate_spec,
)


def _scene_spec():
    spec = empty_spec([{"id": "camera", "path": "/camera.mp4"}])
    return set_scene_composition(
        spec,
        scenes=[
            {
                "name": "intro",
                "layout": "single",
                "slots": [("main", "camera", 0.2, 0.8, None)],
            },
            {
                "name": "demo",
                "layout": "single",
                "slots": [("main", "camera", 1.0, 1.6, None)],
            },
        ],
        source_durations={"camera": 4.0},
        canvas={
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        },
    )


def test_transition_fields_validate_on_scenes_and_project_edges():
    spec = _scene_spec()
    spec["composition"][1]["transition_in"] = {
        "type": "dissolve",
        "duration": 0.5,
    }
    spec["opening_transition"] = {"type": "dip-black", "duration": 0.4}
    spec["closing_transition"] = {"type": "dip-white", "duration": 0.3}

    validate_spec(spec)


def test_agent_readable_schema_documents_transition_storage():
    schema_path = files("moviestar.schema").joinpath("spec-0.2.json")
    schema = json.loads(schema_path.read_text())

    transition = schema["$defs"]["transition"]
    assert transition["required"] == ["type", "duration"]
    assert transition["properties"]["type"]["enum"] == [
        "dissolve",
        "dip-black",
        "dip-white",
    ]
    assert schema["$defs"]["layout_scene"]["properties"]["transition_in"][
        "$ref"
    ] == "#/$defs/transition"
    assert schema["properties"]["opening_transition"]["oneOf"][1][
        "$ref"
    ] == "#/$defs/edge_transition"
    assert "opening_transition" in schema["properties"]["revisions"][
        "items"
    ]["properties"]["changed"]["propertyNames"]["enum"]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda spec: spec["composition"][0].__setitem__(
                "transition_in", {"type": "dissolve", "duration": 0.5}
            ),
            "first scene",
        ),
        (
            lambda spec: spec["composition"][1].__setitem__(
                "transition_in", {"type": "spin", "duration": 0.5}
            ),
            "transition_in.type",
        ),
        (
            lambda spec: spec["composition"][1].__setitem__(
                "transition_in", {"type": "dissolve", "duration": 0}
            ),
            "transition_in.duration",
        ),
        (
            lambda spec: spec.__setitem__(
                "opening_transition", {"type": "dissolve", "duration": 0.5}
            ),
            "opening_transition.type",
        ),
    ],
)
def test_invalid_transition_storage_is_rejected(mutate, match):
    spec = _scene_spec()
    mutate(spec)

    with pytest.raises(SpecValidationError, match=match):
        validate_spec(spec)


def test_edge_transition_fields_participate_in_global_revision_undo():
    assert "opening_transition" in REVISION_FIELDS
    assert "closing_transition" in REVISION_FIELDS
    spec = _scene_spec()
    before = copy.deepcopy(spec)
    spec["opening_transition"] = {"type": "dip-black", "duration": 0.4}
    from moviestar.spec import append_revision

    stored = append_revision(before, spec, "scenes transition")
    assert stored["revisions"][-1]["changed"] == {"opening_transition": None}

    restored, revision = pop_revision(stored)
    assert revision["command"] == "scenes transition"
    assert restored["opening_transition"] is None


def test_pre_transition_spec_loads_with_null_edge_defaults(tmp_path):
    project_dir = tmp_path / "moviestar"
    project_dir.mkdir()
    legacy = _scene_spec()
    legacy.pop("opening_transition", None)
    legacy.pop("closing_transition", None)
    (project_dir / "spec.json").write_text(json.dumps(legacy))

    loaded = load_spec(tmp_path)

    assert loaded is not None
    assert loaded["opening_transition"] is None
    assert loaded["closing_transition"] is None
