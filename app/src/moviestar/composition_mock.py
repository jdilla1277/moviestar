"""Spatial composition commands.

The original milestone-0 examples remain read-only and canned. The ``model``
group below reads and edits real project-owned spatial documents.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import click


def _emit(data: dict) -> None:
    click.echo(json.dumps({"status": "mock_only", "writes_spec": False, **data}, indent=2))


def _target(*path: str) -> dict:
    return {"occurrence_path": list(path)}


SCENE_A = "scene_interview_a"
SCENE_B = "scene_interview_b"
PAIR = "cmp_listener_pair"
LEFT_A = _target(SCENE_A, "inst_listeners_a", "el_left_frame")
EXPLAINER = "scene_explainer"


# This is one coherent illustrative snapshot after the draft edit. It is not
# loaded from a project or accepted as a production document.
PROJECT_FIXTURE = {
    "revision": 15,
    "assets": {
        "asset_host": {"type": "video", "duration_seconds": 20},
        "asset_alice": {"type": "video", "duration_seconds": 20},
        "asset_bob": {"type": "video", "duration_seconds": 20},
        "asset_carol": {"type": "video", "duration_seconds": 20},
        "asset_dan": {"type": "video", "duration_seconds": 20},
        "asset_logo": {"type": "image"},
    },
    "definitions": {
        "cmp_badge": {
            "origin": "captured_builtin", "name": "Badge",
            "inputs": [{"name": "label", "type": "text", "required": False}],
            "children": [{"id": "el_badge_text", "type": "text", "content": {"input": "label"}}],
        },
        PAIR: {
            "origin": "user", "name": "ListenerPair",
            "inputs": [{"name": "left", "type": "video", "required": True},
                       {"name": "right", "type": "video", "required": True},
                       {"name": "optional_tag", "type": "text", "required": False,
                        "default": "Listener"}],
            "children": [
                {"id": "el_left_frame", "type": "video", "content": {"input": "left"},
                 "style": {"border_width": 3}},
                {"id": "el_right_frame", "type": "video", "content": {"input": "right"}},
                {"instance_id": "inst_pair_badge", "definition_id": "cmp_badge",
                 "bindings": {"label": {"forward": "optional_tag"}}},
            ],
        },
        "cmp_text_panel": {
            "origin": "captured_builtin", "name": "TextPanel",
            "inputs": [{"name": "headline", "type": "text", "required": True},
                       {"name": "image", "type": "image", "required": True}],
            "children": [
                {"id": "el_headline", "type": "text", "content": {"input": "headline"}},
                {"id": "el_logo", "type": "image", "content": {"input": "image"}},
            ],
        },
    },
    "scenes": {
        SCENE_A: {
            "duration_seconds": 8,
            "layout": {"children": [
                {"id": "el_host", "type": "video",
                 "content": {"asset_id": "asset_host", "source": {"start": 0, "end": 8}}},
                {"instance_id": "inst_listeners_a", "definition_id": PAIR,
                 "bindings": {
                     "left": {"asset_id": "asset_alice", "source": {"start": 10, "end": 18}},
                     "right": {"asset_id": "asset_bob", "source": {"start": 4, "end": 12}},
                     "optional_tag": {"empty": True},
                 }},
                {"instance_id": "inst_listeners_a_copy", "definition_id": PAIR,
                 "bindings": {
                     "left": {"asset_id": "asset_alice", "source": {"start": 10, "end": 18}},
                     "right": {"asset_id": "asset_bob", "source": {"start": 4, "end": 12}},
                 }},
                {"id": "el_caption_a", "type": "caption", "content": {"transcript": "project"}},
            ]},
        },
        SCENE_B: {
            "duration_seconds": 8,
            "layout": {"children": [
                {"instance_id": "inst_listeners_b", "definition_id": PAIR,
                 "bindings": {
                     "left": {"asset_id": "asset_carol", "source": {"start": 2, "end": 10}},
                     "right": {"asset_id": "asset_dan", "source": {"start": 5, "end": 13}},
                 }},
            ]},
        },
        EXPLAINER: {
            "duration_seconds": 6,
            "layout": {"children": [
                {"instance_id": "inst_text_panel", "definition_id": "cmp_text_panel",
                 "bindings": {"headline": {"text": "A static explainer can contain text and images"}}},
            ]},
        },
    },
}
PROJECT_FIXTURE["definitions"]["cmp_listener_pair_blue"] = deepcopy(PROJECT_FIXTURE["definitions"][PAIR])
PROJECT_FIXTURE["definitions"]["cmp_listener_pair_blue"]["name"] = "ListenerPairBlue"
PROJECT_FIXTURE["definitions"]["cmp_listener_pair_blue"]["variation_of"] = PAIR
PROJECT_FIXTURE["definitions"]["cmp_listener_pair_blue"]["children"][0]["style"]["border_color"] = "#1E90FF"


# Each operation shows a canonical request and response shape. These are sample
# values, not a parser or a hidden state machine.
EDIT_CASES: dict[str, dict] = {
    "start": {
        "example_request": {"scope": "scene", "op": "scene.create", "scene_id": SCENE_A,
                            "from_layout": "builtin:interview", "duration": {"seconds": 8}},
        "example_result": {"affected": [SCENE_A], "created": [SCENE_A], "revision": 1,
                           "captured_definitions": ["cmp_badge", "cmp_text_panel"]},
    },
    "define": {
        "example_request": {"scope": "definition", "op": "component.define",
                            "definition_id": PAIR, "name": "ListenerPair",
                            "inputs": PROJECT_FIXTURE["definitions"][PAIR]["inputs"],
                            "children": PROJECT_FIXTURE["definitions"][PAIR]["children"]},
        "example_result": {"affected": [], "created": [PAIR], "revision": 2},
    },
    "place": {
        "example_request": {"scope": "scene", "op": "instance.place",
                            "scene_id": SCENE_A, "instance_id": "inst_listeners_a",
                            "definition_id": PAIR,
                            "bindings": {"right": {"asset_id": "asset_bob",
                                                   "source": {"start": 4, "end": 12}}},
                            "layout": {"grid_area": "listeners", "min_width": "35%"}},
        "example_result": {"affected": [SCENE_A],
                           "created": [_target(SCENE_A, "inst_listeners_a")], "revision": 3},
    },
    "reuse": {
        "example_request": {"scope": "scene", "op": "scene.create",
                            "scene_id": SCENE_B, "duration": {"seconds": 8},
                            "layout": {"children": [{"instance_id": "inst_listeners_b",
                                                     "definition_id": PAIR,
                                                     "bindings": {"left": {"asset_id": "asset_carol", "source": {"start": 2, "end": 10}},
                                                                  "right": {"asset_id": "asset_dan", "source": {"start": 5, "end": 13}}}}]}},
        "example_result": {"affected": [SCENE_B], "revision": 4,
                           "created": [_target(SCENE_B, "inst_listeners_b")],
                           "definition_id": PAIR},
    },
    "bind": {
        "example_request": {"scope": "occurrence", "op": "input.bind",
                            "target": _target(SCENE_A, "inst_listeners_a"),
                            "input": "left", "value": {"asset_id": "asset_alice",
                                                      "source": {"start": 10, "end": 18}}},
        "example_result": {"affected": [SCENE_A], "revision": 5},
    },
    "customize": {
        "example_request": {"scope": "occurrence", "op": "instance.customize",
                            "target": LEFT_A, "property": "style.border_color", "value": "#1E90FF"},
        "example_result": {"affected": [SCENE_A], "revision": 6,
                           "inheritance": "local override at this occurrence only"},
    },
    "insert": {
        "example_request": {"scope": "occurrence", "op": "instance.child_insert",
                            "target": _target(SCENE_A, "inst_listeners_a"),
                            "child": {"id": "el_guest_tag_a", "type": "text", "content": "Guest"},
                            "after_id": "el_left_frame"},
        "example_result": {"affected": [SCENE_A], "revision": 7,
                           "inheritance": "local structural addition"},
    },
    "reorder": {
        "example_request": {"scope": "occurrence", "op": "instance.child_move",
                            "target": _target(SCENE_A, "inst_listeners_a", "el_guest_tag_a"),
                            "after_id": "el_right_frame"},
        "example_result": {"affected": [SCENE_A], "revision": 8,
                           "preserved_id": "el_guest_tag_a"},
    },
    "remove": {
        "example_request": {"scope": "occurrence", "op": "instance.child_remove",
                            "target": _target(SCENE_A, "inst_listeners_a", "el_guest_tag_a")},
        "example_result": {"affected": [SCENE_A], "revision": 9,
                           "inheritance": "local structural removal"},
    },
    "update": {
        "example_request": {"scope": "definition", "op": "component.update",
                            "target": {"definition_id": PAIR, "element_id": "el_left_frame"},
                            "property": "style.border_width", "value": 3,
                            "expected_state_id": "sha256:<current-spec-fingerprint>"},
        "example_result": {"affected": [SCENE_A, SCENE_B], "revision": 10,
                           "preserved_local_overrides": [LEFT_A]},
    },
    "vary": {
        "example_request": {"scope": "occurrence", "op": "component.save_variation",
                            "source": _target(SCENE_A, "inst_listeners_a"),
                            "new_definition_id": "cmp_listener_pair_blue",
                            "relink_source": False},
        "example_result": {"affected": [], "created": ["cmp_listener_pair_blue"],
                           "revision": 11},
    },
    "reset": {
        "example_request": {"scope": "occurrence", "op": "instance.reset",
                            "target": LEFT_A, "property": "style.border_color"},
        "example_result": {"affected": [SCENE_A], "revision": 12,
                           "inheritance": "definition value restored"},
    },
    "duplicate": {
        "example_request": {"scope": "scene", "op": "instance.duplicate",
                            "source": _target(SCENE_A, "inst_listeners_a"),
                            "new_instance_id": "inst_listeners_a_copy"},
        "example_result": {"affected": [SCENE_A], "revision": 13,
                           "created": [_target(SCENE_A, "inst_listeners_a_copy")],
                           "definition_id": PAIR},
    },
    "clear": {
        "example_request": {"scope": "occurrence", "op": "input.bind",
                            "target": _target(SCENE_A, "inst_listeners_a"),
                            "input": "optional_tag", "value": {"empty": True}},
        "example_result": {"affected": [SCENE_A], "revision": 14,
                           "inheritance": "explicit empty overrides default"},
    },
    "draft": {
        "example_request": {"scope": "scene", "op": "scene.create", "scene_id": EXPLAINER,
                            "duration": {"seconds": 6},
                            "layout": PROJECT_FIXTURE["scenes"][EXPLAINER]["layout"]},
        "example_result": {"affected": [EXPLAINER], "revision": 15,
                           "saved": True, "render_ready": False,
                           "hint": "Run 'moviestar composition check --example draft' for missing bindings."},
    },
    "repair": {
        "example_request": {"scope": "occurrence", "op": "input.bind",
                            "target": _target(EXPLAINER, "inst_text_panel"),
                            "input": "image", "value": {"asset_id": "asset_logo"}},
        "example_result": {"affected": [EXPLAINER], "revision": 16,
                           "render_ready": True,
                           "hint": "Run 'moviestar composition check --example static_only' for the repaired scene."},
    },
    "undo": {
        "example_request": {"scope": "project", "op": "revision.undo", "revision": 16},
        "example_result": {"affected": [EXPLAINER], "restored_revision": 15},
    },
}


@click.group("composition", invoke_without_command=True)
@click.pass_context
def composition(ctx: click.Context) -> None:
    """Explore spatial composition and persist reusable visual models.

    The worked examples in edit/check/inspect remain fixed milestone-zero
    examples. The `model` commands persist and expand real project data.

    Start with `composition edit --example start`, then `--example define`,
    `--example place`, `--example bind`, `--example customize`,
    `--example update`, and `--example vary`.
    `composition fixture` shows the complete canned graph.
    `composition check --example draft` shows proposed diagnostics.
    `composition inspect scene_interview_a --at 00:03.000 --canvas 1080x1920 --view layout`
    shows proposed frame inspection.
    """
    if ctx.invoked_subcommand is None:
        _emit({"cases": list(EDIT_CASES),
               "hint": "Run 'moviestar composition model --help' for real project data, or 'composition edit --help' for worked examples."})


@composition.command("fixture")
def fixture() -> None:
    """MOCK: show the canonical project graph behind the worked examples.

    This is an illustrative snapshot before the repair operation. It remains
    read-only and is not a valid persisted project document.
    """
    _emit({"project": PROJECT_FIXTURE,
           "hint": "Run 'moviestar composition check --example draft' to inspect its missing image binding."})


@composition.command("edit")
@click.argument("request_file", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--example", type=click.Choice(list(EDIT_CASES)), help="Show a worked operation without a file.")
def edit(request_file: Path | None, example: str | None) -> None:
    """MOCK: preview a JSON edit request or a worked operation.

    REQUEST_FILE is a JSON object with an op and scope. The mock reads it but
    does not validate, execute, or persist it. --example selects a step in
    a fixed ListenerPair walkthrough without needing a file.

    Requests distinguish scene, definition, and occurrence scopes. A nested
    occurrence_path starts at a scene ID, passes through instance IDs, and
    ends at an element ID. IDs survive rename and reorder; duplicates get new
    occurrence IDs. Definition edits affect linked uses unless locally
    overridden. Structural conflicts block the whole revision.

    Example request: {"op":"input.bind","scope":"occurrence",
    "target":{"occurrence_path":["scene_interview_a","inst_listeners_a"]},
    "input":"left","value":{"asset_id":"asset_alice",
    "source":{"start":10,"end":18}}}.

    Example envelope: {"status":"mock_only","writes_spec":false,
    "submitted_request":{...},"example_result":{...},"hint":"..."}.
    """
    if (request_file is None) == (example is None):
        raise click.UsageError("Supply REQUEST_FILE or --example CASE, but not both.")
    submitted_request = None
    if request_file is not None:
        try:
            submitted_request = json.loads(request_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise click.BadParameter(f"cannot read a JSON request: {exc}", param_hint="REQUEST_FILE") from exc
        if not isinstance(submitted_request, dict) or not isinstance(submitted_request.get("op"), str):
            raise click.BadParameter("expected a JSON object with an op string", param_hint="REQUEST_FILE")
        example = next((name for name, data in EDIT_CASES.items()
                        if data["example_request"]["op"] == submitted_request["op"]), None)
        if example is None:
            raise click.BadParameter("no canned example for this op; run 'moviestar composition' to list cases",
                                     param_hint="REQUEST_FILE")
    assert example is not None
    names = list(EDIT_CASES)
    next_case = names[(names.index(example) + 1) % len(names)]
    _emit({"case": example, "submitted_request": submitted_request, **EDIT_CASES[example],
           "hint": (f"Run 'moviestar composition edit --example {next_case}' for another operation, "
                    "or 'moviestar composition check --example draft'.")})


CHECK_CASES = {
    "draft": {"can_save": True, "can_export": False, "reloaded_revision": 15,
              "diagnostics": [{"code": "required_input_missing", "severity": "error",
                               "target": _target("scene_explainer", "inst_text_panel", "el_logo"),
                               "input": "image", "message": "Bind a logo asset or set an explicit empty value if optional."}]},
    "conflict": {"can_save": True, "can_apply": False, "can_export": True,
                 "scenario": "Propose removing the left definition child before the local override is reset.",
                 "diagnostics": [{"code": "shared_edit_conflict", "severity": "error",
                                  "target": LEFT_A, "definition_id": PAIR,
                                  "message": "This definition child has a local customization; resolve before deleting it."}]},
    "ready": {"can_save": True, "can_export": True, "diagnostics": []},
    "referenced": {"can_save": True, "can_apply": False, "can_export": True,
                   "diagnostics": [{"code": "referenced_definition", "severity": "error",
                                    "target": {"definition_id": PAIR},
                                    "uses": [_target(SCENE_A, "inst_listeners_a"),
                                             _target(SCENE_A, "inst_listeners_a_copy"),
                                             _target(SCENE_B, "inst_listeners_b")],
                                    "message": "Replace or remove every use before deleting this definition."}]},
    "unequal_media": {
        "example_scene_id": SCENE_A, "scenario": "Remove the explicit duration; bind left for 8 s and right for 5 s.",
        "resolved_duration_seconds": 8, "can_save": True, "can_export": True,
        "diagnostics": [{"code": "media_ends_before_scene", "severity": "warning",
                         "target": _target(SCENE_A, "inst_listeners_a", "el_right_frame"),
                         "last_frame_before_seconds": 5,
                         "message": "The right video becomes transparent after 5 s; the scene continues to 8 s."}]},
    "missing_media": {
        "example_scene_id": SCENE_A, "scenario": "Remove the explicit duration and the required left binding.",
        "resolved_duration_seconds": None, "can_save": True, "can_export": False,
        "diagnostics": [{"code": "required_input_missing", "severity": "error",
                         "target": LEFT_A, "input": "left",
                         "message": "Bind the missing required video before inferring duration or exporting."}]},
    "explicit_duration": {
        "example_scene_id": SCENE_A, "scenario": "Set duration to 8 s; bind left for 12 s and right for 5 s.",
        "resolved_duration_seconds": 8, "can_save": True, "can_export": True,
        "diagnostics": [{"code": "media_ends_before_scene", "severity": "warning",
                         "target": _target(SCENE_A, "inst_listeners_a", "el_right_frame"),
                         "last_frame_before_seconds": 5,
                         "message": "The right video becomes transparent after 5 s; left video is cut at 8 s."}]},
    "static_only": {
        "example_scene_id": EXPLAINER, "scenario": "Apply the repair binding for the logo image.",
        "resolved_duration_seconds": 6, "can_save": True, "can_export": True,
        "diagnostics": []},
    "with_caption": {
        "example_scene_id": SCENE_A, "can_save": True, "can_export": True,
        "generated_captions": True,
        "caption_occurrences": [_target(SCENE_A, "el_caption_a")], "diagnostics": []},
    "without_caption": {
        "example_scene_id": SCENE_B, "can_save": True, "can_export": True,
        "generated_captions": False, "caption_occurrences": [], "diagnostics": []},
}


@composition.command("check")
@click.option("--scene", "scene_id", help="Restrict readiness to a selected scene and its dependencies.")
@click.option("--example", type=click.Choice(list(CHECK_CASES)), default="ready", show_default=True,
              help="Select canned diagnostics in this mock.")
def check(scene_id: str | None, example: str) -> None:
    """MOCK: inspect draft, conflict, referenced, or ready diagnostics.

    A readable draft can be saved while export remains blocked. Export checks
    selected scenes and their transitive dependencies. Shared-edit conflicts
    block the proposed atomic revision and name affected occurrence paths.

    Example envelope: {"status":"mock_only","writes_spec":false,
    "can_save":true,"can_export":false,"diagnostics":[...],"hint":"..."}.
    """
    example_scene_id = CHECK_CASES[example].get("example_scene_id")
    if scene_id is not None and example_scene_id is not None and scene_id != example_scene_id:
        raise click.BadParameter(f"{example} is a canned case for {example_scene_id}", param_hint="--scene")
    _emit({"case": example, "scene_id": scene_id, **CHECK_CASES[example],
           "hint": "Run 'moviestar composition inspect scene_interview_a --at 00:03.000 --canvas 1080x1920 --view layout'."})


@composition.command("inspect")
@click.argument("scene_id")
@click.option("--at", "timecode", required=True, help="Result time, MM:SS.mmm or HH:MM:SS.mmm.")
@click.option("--canvas", required=True, help="Output dimensions, WIDTHxHEIGHT.")
@click.option("--view", type=click.Choice(["layout", "rendered"]), required=True)
def inspect(scene_id: str, timecode: str, canvas: str, view: str) -> None:
    """MOCK: show one proposed frame in layout or rendered mode.

    Both views refer to the same frame description. Layout replaces asset
    pixels with labeled boxes; rendered uses media. This mock returns sample
    geometry and artifact=null in either mode, so it creates no screenshot.
    Motion uses scene/result time and applies to an element or whole subtree.

    Example envelope: {"status":"mock_only","writes_spec":false,
    "frame":{"time":"00:03.000","boxes":[...]},"artifact":null,
    "hint":"..."}.
    """
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", canvas)
    if match is None:
        raise click.BadParameter("use WIDTHxHEIGHT, for example 1080x1920", param_hint="--canvas")
    if re.fullmatch(r"(?:[0-9]{2}:)?[0-9]{2}:[0-9]{2}\.[0-9]{3}", timecode) is None:
        raise click.BadParameter("use MM:SS.mmm or HH:MM:SS.mmm", param_hint="--at")
    if scene_id not in PROJECT_FIXTURE["scenes"]:
        raise click.BadParameter("use a scene from 'moviestar composition fixture'", param_hint="SCENE_ID")
    width, height = map(int, match.groups())
    if scene_id in (SCENE_A, SCENE_B):
        instance_id = "inst_listeners_a" if scene_id == SCENE_A else "inst_listeners_b"
        asset_id = "asset_alice" if scene_id == SCENE_A else "asset_carol"
        boxes = [
            {"occurrence_path": [scene_id, instance_id, "el_left_frame"],
             "rect": {"x": 48, "y": 320, "width": 470, "height": 300},
             "content": asset_id if view == "rendered" else "VIDEO: left",
             "source": "scene layout + ListenerPair definition + local overrides"},
            {"occurrence_path": [scene_id, instance_id, "inst_pair_badge", "el_badge_text"],
             "rect": {"x": 48, "y": 280, "width": 160, "height": 32},
             "content": ("" if scene_id == SCENE_A else "Listener") if view == "rendered"
                        else "TEXT: optional_tag",
             "source": "ListenerPair.optional_tag forwarded to Badge.label"},
        ]
        if scene_id == SCENE_A:
            boxes.append({"occurrence_path": [scene_id, "el_caption_a"],
                          "rect": {"x": 48, "y": 1720, "width": 984, "height": 120},
                          "content": "transcript cue" if view == "rendered" else "CAPTIONS",
                          "source": "declared caption element"})
    else:
        boxes = [
            {"occurrence_path": [EXPLAINER, "inst_text_panel", "el_headline"],
             "rect": {"x": 48, "y": 320, "width": 600, "height": 160},
             "content": "A static explainer can contain text and images" if view == "rendered" else "TEXT: headline",
             "source": "TextPanel.headline binding"},
            {"occurrence_path": [EXPLAINER, "inst_text_panel", "el_logo"],
             "rect": {"x": 48, "y": 520, "width": 160, "height": 160},
             "content": "MISSING IMAGE" if view == "rendered" else "IMAGE: unbound",
             "source": "TextPanel.image missing required binding"},
        ]
    _emit({"scene_id": scene_id, "view": view, "artifact": None,
           "frame": {"time": timecode, "canvas": {"width": width, "height": height},
                     "boxes": boxes},
           "hint": "Run 'moviestar composition check --example draft' for addressable readiness diagnostics."})


@composition.group("model")
def model() -> None:
    """Save and inspect real reusable components and scene visual trees.

    These structural trees are stored in spec.json. Existing scene order and
    timing stay authoritative. `model layout` measures boxes in local Chrome;
    `model preview` renders a real frame; watch and export render the scene trees as video.
    """


@model.command("apply")
@click.argument("document", type=click.Path(path_type=Path))
def model_apply(document: Path) -> None:
    """Replace the spatial document with a JSON file, recording one undo step.

    DOCUMENT contains definitions, scenes, and optional image assets. A scene
    key must be an existing ID in the composition timeline. Incomplete input
    bindings may be saved and diagnosed by `model inspect`.
    """
    from moviestar.cli import _error_exit
    from moviestar.spatial_model import spatial_state_id
    from moviestar.spec import SpecValidationError, load_spec, save_spec

    try:
        spatial = json.loads(document.read_text())
    except (OSError, ValueError) as exc:
        _error_exit("composition model apply", f"Cannot read JSON document: {exc}")
    if not isinstance(spatial, dict):
        _error_exit("composition model apply", "Document must contain a JSON object")
    try:
        spec = load_spec()
    except SpecValidationError as exc:
        _error_exit("composition model apply", str(exc))
    if spec is None:
        _error_exit("composition model apply", "No spec.json found. Load a project first.")
    spec["spatial"] = spatial
    try:
        saved = save_spec(spec, command="composition model apply")
    except SpecValidationError as exc:
        _error_exit("composition model apply", str(exc))
    click.echo(json.dumps({"status": "saved", "writes_spec": True,
                           "revision": len(saved["revisions"]),
                           "state_id": spatial_state_id(saved),
                           "definitions": sorted(spatial.get("definitions", {})),
                           "scenes": sorted(spatial.get("scenes", {}))}, indent=2))


@model.command("inspect")
@click.argument("scene_id")
def model_inspect(scene_id: str) -> None:
    """Expand a saved scene tree with current definitions and bindings.

    This reports structure and content references, without pixel geometry.
    Diagnostics identify incomplete bindings, unknown assets, and cycles.
    """
    from moviestar.cli import _error_exit
    from moviestar.spatial_model import SpatialModelError, expand_spatial_scene, spatial_state_id
    from moviestar.spec import SpecValidationError, load_spec

    try:
        spec = load_spec()
    except SpecValidationError as exc:
        _error_exit("composition model inspect", str(exc))
    if spec is None:
        _error_exit("composition model inspect", "No spec.json found. Load a project first.")
    try:
        expanded = expand_spatial_scene(spec, scene_id)
    except SpatialModelError as exc:
        _error_exit("composition model inspect", str(exc))
    click.echo(json.dumps({"status": "expanded", "renders_pixels": False,
                           "state_id": spatial_state_id(spec), "scene": expanded}, indent=2))


@model.command("edit")
@click.argument("document", type=click.Path(path_type=Path))
@click.option("--dry-run", is_flag=True, help="Validate and report impact without saving.")
def model_edit(document: Path, dry_run: bool) -> None:
    """Apply one local or shared component edit from a JSON request.

    Targets use the stable occurrence_path shown by `model inspect`, or a
    definition_id and element_id. Shared edits fail before writing if they
    conflict with an occurrence customization. Include expected_state_id from
    inspect to reject a stale edit. One edit is one undo revision.
    """
    from moviestar.cli import _error_exit
    from moviestar.spatial_edit import SpatialEditConflict
    from moviestar.spatial_model import SpatialModelError, edit_spatial_model
    from moviestar.spec import SpecValidationError, load_spec, save_spec

    try:
        request = json.loads(document.read_text())
        spec = load_spec()
        if spec is None:
            _error_exit("composition model edit", "No spec.json found. Load a project first.")
        changed, report = edit_spatial_model(spec, request)
        if not dry_run:
            saved = save_spec(changed, command=f"composition model edit {request['op']}")
            report["writes_spec"] = True
            report["revision"] = len(saved["revisions"])
    except SpatialEditConflict as exc:
        click.echo(json.dumps({"command": "composition model edit", "error": str(exc),
                               "conflicts": exc.conflicts, "writes_spec": False,
                               "hint": exc.hint}, indent=2))
        raise SystemExit(1) from exc
    except (OSError, ValueError, TypeError, KeyError, SpecValidationError, SpatialModelError) as exc:
        _error_exit("composition model edit", str(exc))
    click.echo(json.dumps({"status": "preview" if dry_run else "saved", **report}, indent=2))


@model.command("frame")
@click.option("--at", "at_time", required=True, help="Global result time, H:MM:SS.mmm.")
def model_frame(at_time: str) -> None:
    """Resolve a saved visual tree and its media positions at result time.

    This is the temporal input for layout measurement and pixel rendering. It does
    not measure boxes or draw an image.
    Spatial transitions return named scene layers and blend weights.
    """
    from moviestar.cli import _error_exit
    from moviestar.resolved import ResolvedProjectError
    from moviestar.spatial_model import SpatialModelError
    from moviestar.spec import SpecValidationError

    try:
        _, frame, _ = _model_frame_context(at_time, None)
    except (OSError, ValueError, KeyError, SpecValidationError,
            ResolvedProjectError, SpatialModelError) as exc:
        _error_exit("composition model frame", str(exc))
    click.echo(json.dumps({"status": "resolved", "renders_pixels": False,
                           "frame": frame}, indent=2))


@model.command("layout")
@click.option("--at", "at_time", required=True, help="Global result time, H:MM:SS.mmm.")
@click.option("--canvas", help="Measure at WIDTHxHEIGHT; defaults to the project canvas.")
@click.option("--out", type=click.Path(path_type=Path), help="Write a labeled layout PNG.")
def model_layout(at_time: str, canvas: str | None, out: Path | None) -> None:
    """Measure identified boxes in local Chrome and optionally draw their labels.

    This view shows layout and text geometry with video/image placeholders.
    Declared reserved regions show guides and report unrelated box collisions.
    It does not composite source pixels or change the saved project.
    """
    from moviestar.cli import _error_exit
    from moviestar.resolved import ResolvedProjectError
    from moviestar.spatial_layout import SpatialLayoutError, measure_spatial_layout
    from moviestar.spatial_model import SpatialModelError
    from moviestar.spec import SpecValidationError

    try:
        _, frame, dimensions = _model_frame_context(at_time, canvas)
        layout = measure_spatial_layout(frame, dimensions, output_path=out)
    except (OSError, ValueError, KeyError, SpecValidationError, ResolvedProjectError,
            SpatialModelError, SpatialLayoutError) as exc:
        _error_exit("composition model layout", str(exc))
    click.echo(json.dumps({"status": "measured", "renders_assets": False,
                           "frame": frame, "layout": layout}, indent=2))


def _model_frame_context(at_time: str, canvas: str | None) -> tuple[dict, dict, dict]:
    from moviestar.project import load_project
    from moviestar.resolved import resolve_project
    from moviestar.spec import load_spec, parse_timecode_string

    at_s = parse_timecode_string(at_time, "--at")
    spec = load_spec()
    if spec is None:
        raise ValueError("No spec.json found. Load a project first.")
    project = load_project()
    timeline = resolve_project(project, spec)
    dimensions = timeline.canvas
    if canvas is not None:
        match = re.fullmatch(r"([0-9]+)x([0-9]+)", canvas)
        if match is None:
            raise ValueError("canvas: use WIDTHxHEIGHT")
        dimensions = {"width": int(match[1]), "height": int(match[2])}
    from moviestar.spatial_inspection import spatial_frame_context
    return spatial_frame_context(project, spec, timeline, at_s, dimensions)


@model.command("preview")
@click.option("--at", "at_time", required=True, help="Global result time, H:MM:SS.mmm.")
@click.option("--canvas", help="Render at WIDTHxHEIGHT; defaults to the project canvas.")
@click.option("--out", required=True, type=click.Path(path_type=Path), help="Write the rendered frame PNG.")
@click.option("--layout-out", type=click.Path(path_type=Path), help="Also write labels from the same measured frame.")
def model_preview(at_time: str, canvas: str | None, out: Path, layout_out: Path | None) -> None:
    """Render a real spatial frame with Chrome graphics and FFmpeg source pixels.

    Includes video, images, text, timed caption elements, shapes, and nested
    components, including scene-relative motion. Required content must be bound
    and available. Standard screenshot/inspect share this frame path; watch and
    export also render transitions. Stills support spatial-scene transitions and
    project-edge fades; mixed/preset transitions require watch or export.
    The saved project is read-only.
    """
    from moviestar.cli import _error_exit
    from moviestar.project import get_project_dir
    from moviestar.resolved import ResolvedProjectError
    from moviestar.spatial_render import render_spatial_frame
    from moviestar.spec import SpecValidationError

    try:
        spec, frame, dimensions = _model_frame_context(at_time, canvas)
        preview = render_spatial_frame(spec, frame, dimensions, output_path=out,
                                       layout_output_path=layout_out, project_root=get_project_dir().parent)
    except (OSError, ValueError, KeyError, SpecValidationError, ResolvedProjectError) as exc:
        _error_exit("composition model preview", str(exc))
    click.echo(json.dumps({"status": "rendered", "renders_assets": True,
                           "frame": frame, "preview": preview}, indent=2))
