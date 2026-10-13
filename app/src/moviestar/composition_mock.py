"""Executable, read-only preview of the spatial composition authoring contract.

Milestone 0 deliberately keeps these responses canned. Nothing in this module
resolves a project, creates an asset, or persists a composition.
"""

from __future__ import annotations

import json
import re
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


# Each operation shows a canonical request and response shape. These are sample
# values, not a parser or a hidden state machine.
EDIT_CASES: dict[str, dict] = {
    "start": {
        "example_request": {"scope": "scene", "op": "scene.create", "scene_id": SCENE_A,
                            "from_layout": "builtin:interview", "duration": {"seconds": 8}},
        "example_result": {"affected": [SCENE_A], "created": [SCENE_A], "revision": 1},
    },
    "define": {
        "example_request": {"scope": "definition", "op": "component.define",
                            "definition_id": PAIR, "name": "ListenerPair",
                            "inputs": [{"name": "left", "type": "video", "required": True},
                                       {"name": "right", "type": "video", "required": True},
                                       {"name": "optional_tag", "type": "text", "required": False,
                                        "default": "Listener"}],
                            "children": ["el_left_frame", "el_right_frame"]},
        "example_result": {"affected": [], "created": [PAIR], "revision": 2},
    },
    "place": {
        "example_request": {"scope": "scene", "op": "instance.place",
                            "scene_id": SCENE_A, "instance_id": "inst_listeners_a",
                            "definition_id": PAIR,
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
                            "expected_revision": 9},
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
        "example_request": {"scope": "scene", "op": "scene.create", "scene_id": "scene_explainer",
                            "duration": {"seconds": 6},
                            "layout": {"children": ["el_headline", "el_logo"]}},
        "example_result": {"affected": ["scene_explainer"], "revision": 15,
                           "saved": True, "render_ready": False,
                           "hint": "Run 'moviestar composition check draft' for missing bindings."},
    },
    "undo": {
        "example_request": {"scope": "project", "op": "revision.undo", "revision": 15},
        "example_result": {"affected": ["scene_explainer"], "restored_revision": 14},
    },
}


@click.group("composition", invoke_without_command=True)
@click.pass_context
def composition(ctx: click.Context) -> None:
    """MOCK: explore scene layouts and reusable component editing.

    This milestone-zero surface returns fixed worked examples. Every response
    has status=mock_only and writes_spec=false. It never reads project state,
    saves changes, resolves geometry, or renders pixels.

    Start with `composition edit --example start`, then `--example define`,
    `--example place`, `--example bind`, `--example customize`,
    `--example update`, and `--example vary`.
    `composition check --example draft` and `composition inspect scene_interview_a`
    show proposed diagnostics and frame inspection.
    """
    if ctx.invoked_subcommand is None:
        _emit({"cases": list(EDIT_CASES),
               "hint": "Run 'moviestar composition edit --help' for the worked edit sequence and JSON request form."})


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
                 "diagnostics": [{"code": "shared_edit_conflict", "severity": "error",
                                  "target": LEFT_A, "definition_id": PAIR,
                                  "message": "This definition child has a local customization; resolve before deleting it."}]},
    "ready": {"can_save": True, "can_export": True, "diagnostics": []},
    "referenced": {"can_save": True, "can_apply": False, "can_export": True,
                   "diagnostics": [{"code": "referenced_definition", "severity": "error",
                                    "target": {"definition_id": PAIR},
                                    "uses": [_target(SCENE_A, "inst_listeners_a"),
                                             _target(SCENE_B, "inst_listeners_b")],
                                    "message": "Replace or remove every use before deleting this definition."}]},
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
    width, height = map(int, match.groups())
    _emit({"scene_id": scene_id, "view": view, "artifact": None,
           "frame": {"time": timecode, "canvas": {"width": width, "height": height},
                     "boxes": [{"occurrence_path": [scene_id, "inst_listeners_a", "el_left_frame"],
                                "rect": {"x": 48, "y": 320, "width": 470, "height": 300},
                                "content": "asset_alice" if view == "rendered" else "VIDEO: left",
                                "source": "scene layout + ListenerPair definition + local overrides"}]},
           "hint": "Run 'moviestar composition check --example draft' for addressable readiness diagnostics."})
