"""Persisted, scene-owned visual trees and reusable component definitions.

This module resolves structure and bindings only. The established composition
still owns scene order and timing; pixel layout and rendering follow later.
Incomplete references remain readable drafts and appear as diagnostics during
expansion. Malformed structure and ambiguous identities fail validation.
"""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from moviestar.resolved import ResolvedProject


class SpatialModelError(ValueError):
    """A spatial document cannot be read unambiguously."""


_INPUT_TYPES = {"video", "image", "text", "number", "color", "boolean"}
_ELEMENT_TYPES = _INPUT_TYPES | {"caption", "shape"}


def _fail(path: str, message: str) -> None:
    raise SpatialModelError(f"{path}: {message}")


def _identifier(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-empty string")
    return value


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _binding(value: object, kind: str, path: str, *, forward: bool = False) -> None:
    if forward and isinstance(value, dict) and set(value) == {"forward"}:
        _identifier(value["forward"], f"{path}.forward")
        return
    if isinstance(value, dict) and value.get("empty") is True and len(value) == 1:
        return
    if kind in {"video", "image"}:
        if not isinstance(value, dict) or not isinstance(value.get("asset_id"), str) or not value["asset_id"]:
            _fail(path, f"{kind} binding needs a non-empty asset_id")
        allowed = {"asset_id", "source"} if kind == "video" else {"asset_id"}
        if set(value) - allowed:
            _fail(path, f"unexpected {kind} binding field")
        if "source" in value:
            source = value["source"]
            if not isinstance(source, dict) or set(source) != {"start", "end"}:
                _fail(f"{path}.source", "needs numeric start and end")
            start, end = source["start"], source["end"]
            if not _finite_number(start) or not _finite_number(end) or start < 0 or end <= start:
                _fail(f"{path}.source", "needs finite 0 <= start < end")
        return
    key = {"text": "text", "number": "number", "color": "color", "boolean": "boolean"}.get(kind)
    if key is None:
        _fail(path, f"unsupported binding type {kind!r}")
    if not isinstance(value, dict) or set(value) != {key}:
        _fail(path, f"{kind} binding needs {key!r}")
    item = value[key]
    expected = {"text": str, "number": (int, float), "color": str, "boolean": bool}[kind]
    if not isinstance(item, expected) or (kind == "number" and not _finite_number(item)):
        _fail(path, f"{kind} binding has wrong value type")


def _inputs(definition: dict, path: str) -> dict[str, dict]:
    if not isinstance(definition, dict):
        _fail(path, "must be an object")
    inputs = definition.get("inputs", [])
    if not isinstance(inputs, list):
        _fail(f"{path}.inputs", "must be an array")
    result = {}
    for index, item in enumerate(inputs):
        here = f"{path}.inputs[{index}]"
        if not isinstance(item, dict):
            _fail(here, "must be an object")
        name = _identifier(item.get("name"), f"{here}.name")
        kind = item.get("type")
        if not isinstance(kind, str) or kind not in _INPUT_TYPES:
            _fail(f"{here}.type", f"must be one of {', '.join(sorted(_INPUT_TYPES))}")
        if name in result:
            _fail(f"{here}.name", f"duplicate input {name!r}")
        if "required" in item and not isinstance(item["required"], bool):
            _fail(f"{here}.required", "must be boolean")
        if "default" in item:
            default = item["default"]
            if not isinstance(default, dict):
                default = {kind: default}
            _binding(default, kind, f"{here}.default")
        result[name] = item
    return result


def _children(children: object, path: str, definitions: dict, inputs: dict[str, dict]) -> None:
    if not isinstance(children, list):
        _fail(path, "must be an array")
    seen = set()
    for index, node in enumerate(children):
        here = f"{path}[{index}]"
        if not isinstance(node, dict):
            _fail(here, "must be an object")
        is_instance = "instance_id" in node
        if is_instance == ("id" in node):
            _fail(here, "needs exactly one of id or instance_id")
        field = "instance_id" if is_instance else "id"
        identity = _identifier(node[field], f"{here}.{field}")
        if identity in seen:
            _fail(f"{here}.{field}", f"duplicate sibling identity {identity!r}")
        seen.add(identity)
        if "layout" in node and not isinstance(node["layout"], dict):
            _fail(f"{here}.layout", "must be an object")
        if "style" in node and not isinstance(node["style"], dict):
            _fail(f"{here}.style", "must be an object")
        if is_instance:
            target = _identifier(node.get("definition_id"), f"{here}.definition_id")
            bindings = node.get("bindings", {})
            if not isinstance(bindings, dict):
                _fail(f"{here}.bindings", "must be an object")
            if target in definitions and not isinstance(definitions[target], dict):
                _fail(f"spatial.definitions.{target}", "must be an object")
            target_inputs = _inputs(definitions[target], f"spatial.definitions.{target}") if target in definitions else {}
            for name, value in bindings.items():
                _identifier(name, f"{here}.bindings key")
                if target in definitions and name not in target_inputs:
                    _fail(f"{here}.bindings.{name}", f"unknown input on {target!r}")
                if name in target_inputs:
                    _binding(value, target_inputs[name]["type"], f"{here}.bindings.{name}", forward=bool(inputs))
                    if isinstance(value, dict) and "forward" in value:
                        forwarded = value["forward"]
                        if forwarded not in inputs:
                            _fail(f"{here}.bindings.{name}", f"unknown enclosing input {forwarded!r}")
                        if inputs[forwarded]["type"] != target_inputs[name]["type"]:
                            _fail(f"{here}.bindings.{name}", "forwarded input type differs")
        else:
            kind = node.get("type")
            if not isinstance(kind, str) or kind not in _ELEMENT_TYPES:
                _fail(f"{here}.type", f"unknown element type {kind!r}")
            content = node.get("content")
            if isinstance(content, dict) and set(content) == {"input"}:
                name = _identifier(content["input"], f"{here}.content.input")
                if name not in inputs:
                    _fail(f"{here}.content.input", f"unknown input {name!r}")
                if inputs[name]["type"] != kind:
                    _fail(f"{here}.content.input", "element and input types differ")
            elif content is not None and kind in _INPUT_TYPES:
                _binding(content, kind, f"{here}.content")
            elif content is not None and kind not in _INPUT_TYPES and not isinstance(content, dict):
                _fail(f"{here}.content", "must be an object")
            if "children" in node:
                _children(node["children"], f"{here}.children", definitions, inputs)


def validate_spatial_model(spec: dict) -> None:
    """Check structure while allowing unresolved references and draft bindings."""
    spatial = spec.get("spatial")
    if spatial is None:
        return
    if not isinstance(spatial, dict):
        _fail("spatial", "must be an object")
    definitions = spatial.get("definitions", {})
    scenes = spatial.get("scenes", {})
    if not isinstance(definitions, dict) or not isinstance(scenes, dict):
        _fail("spatial", "definitions and scenes must be objects")
    assets = spatial.get("assets", {})
    if not isinstance(assets, dict):
        _fail("spatial.assets", "must be an object")
    for asset_id, asset in assets.items():
        _identifier(asset_id, f"spatial.assets.{asset_id}")
        if not isinstance(asset, dict) or asset.get("type") != "image":
            _fail(f"spatial.assets.{asset_id}.type", "must be image")
        _identifier(asset.get("path"), f"spatial.assets.{asset_id}.path")
    if set(spatial) - {"definitions", "scenes", "assets"}:
        _fail("spatial", "unknown field")
    for definition_id, definition in definitions.items():
        path = f"spatial.definitions.{definition_id}"
        _identifier(definition_id, path)
        if not isinstance(definition, dict):
            _fail(path, "must be an object")
        _identifier(definition.get("name"), f"{path}.name")
        inputs = _inputs(definition, path)
        _children(definition.get("children"), f"{path}.children", definitions, inputs)
    timeline_ids = {scene.get("id") for scene in (spec.get("composition") or []) if isinstance(scene, dict) and "layout" in scene}
    for scene_id, scene in scenes.items():
        path = f"spatial.scenes.{scene_id}"
        _identifier(scene_id, path)
        if scene_id not in timeline_ids:
            _fail(path, "unknown timeline scene id")
        if not isinstance(scene, dict) or not isinstance(scene.get("layout"), dict):
            _fail(f"{path}.layout", "must be an object")
        _children(scene["layout"].get("children"), f"{path}.layout.children", definitions, {})


def expand_spatial_scene(spec: dict, scene_id: str) -> dict:
    """Resolve a scene tree using current shared definitions and local bindings.

    The result carries stable occurrence paths and addressable diagnostics. A
    cycle or missing definition stops only that branch, never recursion itself.
    """
    validate_spatial_model(spec)
    spatial = spec.get("spatial") or {}
    scenes = spatial.get("scenes", {})
    if scene_id not in scenes:
        raise SpatialModelError(f"spatial.scenes.{scene_id}: no visual tree")
    definitions = spatial.get("definitions", {})
    sources = {item["id"] for item in spec["sources"]}
    images = set(spatial.get("assets", {}))
    diagnostics = []

    def resolve_value(value: object, inputs: dict, path: list[str], kind: str,
                      required: set[str], reported: set[str]) -> object:
        if isinstance(value, dict) and "input" in value:
            name = value["input"]
            value = inputs.get(name)
            if name in required and (value is None or (isinstance(value, dict) and value.get("empty") is True)):
                if name not in reported:
                    diagnostics.append({"code": "missing_binding", "path": path, "input": name})
                reported.add(name)
                return None
        if isinstance(value, dict) and value.get("empty") is True:
            return None
        if isinstance(value, dict) and "asset_id" in value and kind == "video" and value["asset_id"] not in sources:
            diagnostics.append({"code": "unknown_asset", "path": path, "asset_id": value["asset_id"]})
        if isinstance(value, dict) and "asset_id" in value and kind == "image" and value["asset_id"] not in images:
            diagnostics.append({"code": "unknown_asset", "path": path, "asset_id": value["asset_id"]})
        return deepcopy(value)

    def expand_nodes(nodes: list, path: list[str], inputs: dict,
                     required: set[str], reported: set[str],
                     ancestry: tuple[str, ...]) -> list[dict]:
        expanded = []
        for node in nodes:
            identity = node.get("id", node.get("instance_id"))
            address = [*path, identity]
            common = {"occurrence_path": address, "layout": deepcopy(node.get("layout", {})),
                      "style": deepcopy(node.get("style", {}))}
            if "id" in node:
                kind = node["type"]
                content = resolve_value(node.get("content"), inputs, address,
                                        kind, required, reported)
                item = {**common, "type": kind, "content": content}
                if "children" in node:
                    item["children"] = expand_nodes(node["children"], address, inputs,
                                                    required, reported, ancestry)
                expanded.append(item)
                continue
            definition_id = node["definition_id"]
            item = {**common, "definition_id": definition_id, "children": []}
            expanded.append(item)
            if definition_id not in definitions:
                diagnostics.append({"code": "unknown_definition", "path": address, "definition_id": definition_id})
                continue
            if definition_id in ancestry:
                diagnostics.append({"code": "component_cycle", "path": address, "definition_id": definition_id})
                continue
            definition = definitions[definition_id]
            bindings = {}
            for name, declaration in _inputs(definition, f"spatial.definitions.{definition_id}").items():
                supplied = node.get("bindings", {}).get(name)
                if isinstance(supplied, dict) and "forward" in supplied:
                    supplied = inputs.get(supplied["forward"])
                if supplied is None and "default" in declaration:
                    supplied = declaration["default"]
                    if not isinstance(supplied, dict):
                        supplied = {declaration["type"]: supplied}
                bindings[name] = supplied
            declarations = _inputs(definition, f"spatial.definitions.{definition_id}")
            mandatory = {name for name, entry in declarations.items() if entry.get("required")}
            reported_here: set[str] = set()
            item["children"] = expand_nodes(definition["children"], address, bindings,
                                            mandatory, reported_here,
                                            (*ancestry, definition_id))
            for name in mandatory:
                value = bindings[name]
                if (value is None or (isinstance(value, dict) and value.get("empty") is True)) and name not in reported_here:
                    diagnostics.append({"code": "missing_binding", "path": address, "input": name})
        return expanded

    layout = scenes[scene_id]["layout"]
    return {"scene_id": scene_id, **{k: deepcopy(v) for k, v in layout.items() if k != "children"},
            "children": expand_nodes(layout["children"], [scene_id], {}, set(), set(), ()),
            "diagnostics": diagnostics}


def compile_spatial_frame(
    spec: dict,
    timeline: ResolvedProject,
    result_time_s: float,
    *,
    source_durations: dict[str, float] | None = None,
) -> dict:
    """Resolve visual content at one point on the canonical result timeline.

    The caller may reuse ``timeline`` and ``source_durations`` across frames.
    This resolves content and media clocks; measured boxes and pixels are the
    next compiler stage. A short media range becomes transparent after its end.
    """
    if not _finite_number(result_time_s) or not 0 <= result_time_s <= timeline.duration_s:
        _fail("result time", f"must be within 0-{timeline.duration_s} seconds")
    if timeline.shape != "scenes":
        _fail("spatial frame", "requires a scene composition timeline")
    _reject_active_transition(spec, timeline, result_time_s)
    # The exact project end addresses its last output frame, even when a slow
    # pacing segment would round a microsecond-before-end sample up to the
    # half-open media boundary.
    lookup_time = result_time_s
    if result_time_s == timeline.duration_s:
        lookup_time = max(0.0, timeline.duration_s - 1 / timeline.output_fps)
    span = timeline.span_at(lookup_time)
    if span.scene_id is None:
        _fail("spatial frame", "resolved scene has no stable id")
    scene_duration = round(span.scene_end_s - span.scene_start_s, 6)
    scene_time = round(min(scene_duration, lookup_time - span.scene_start_s), 6)
    from moviestar.spec import parse_timecode_string

    authored_scene = spec["composition"][span.scene_index]
    first_slot = authored_scene["slots"][0]
    slot = span.slot(first_slot["slot"])
    if slot is None:
        _fail("spatial frame", "resolved scene has no pacing reference slot")
    source_start = parse_timecode_string(first_slot["source_from"], "slot.source_from")
    source_end = parse_timecode_string(first_slot["source_to"], "slot.source_to")
    scene_source_duration = round(source_end - source_start, 6)
    scene_source_time = round(
        slot.source_from_s - source_start
        + (0 if slot.held else (lookup_time - span.start_s) * slot.speed), 6
    )
    expanded = expand_spatial_scene(spec, span.scene_id)
    diagnostics = expanded["diagnostics"]
    durations = source_durations or {}
    known_sources = {source["id"] for source in spec["sources"]}

    def place_media(nodes: list[dict]) -> None:
        for node in nodes:
            if node.get("type") == "video":
                content = node.get("content")
                node["media"] = None
                if isinstance(content, dict) and "asset_id" in content:
                    asset_id = content["asset_id"]
                    if asset_id not in known_sources:
                        place_media(node.get("children", []))
                        continue
                    source = content.get("source")
                    if source is None and asset_id in durations:
                        source = {"start": 0, "end": durations[asset_id]}
                    if source is None:
                        diagnostics.append({"code": "unknown_source_duration",
                                            "path": node["occurrence_path"], "asset_id": asset_id})
                    elif asset_id in durations and source["end"] > durations[asset_id]:
                        diagnostics.append({"code": "source_range_out_of_bounds",
                                            "path": node["occurrence_path"], "asset_id": asset_id,
                                            "source_end_s": source["end"],
                                            "asset_duration_s": durations[asset_id]})
                    else:
                        duration = round(source["end"] - source["start"], 6)
                        visible = scene_source_time < duration
                        node["media"] = {
                            "asset_id": asset_id,
                            "source_time_s": round(source["start"] + scene_source_time, 6) if visible else None,
                            "source_range": deepcopy(source),
                            "visible": visible,
                        }
                        if duration < scene_source_duration:
                            diagnostics.append({"code": "media_ends_before_scene",
                                                "severity": "warning", "path": node["occurrence_path"],
                                                "media_duration_s": duration,
                                                "scene_duration_s": scene_duration})
            place_media(node.get("children", []))

    place_media(expanded["children"])
    return {
        **expanded,
        "result_time_s": round(result_time_s, 6),
        "scene_time_s": scene_time,
        "scene_duration_s": scene_duration,
        "scene_source_time_s": scene_source_time,
        "scene_source_duration_s": scene_source_duration,
        "canvas": deepcopy(timeline.canvas),
        "pacing_segment_id": span.pacing_segment_id,
    }


def _reject_active_transition(spec: dict, timeline: ResolvedProject, at_s: float) -> None:
    """Do not present a single scene tree as a complete transition frame."""
    opening = spec.get("opening_transition")
    if opening and at_s < round(opening["duration"], 6):
        _fail("opening_transition", f"spatial frame at {at_s}s has an unsupported project-edge transition")
    closing = spec.get("closing_transition")
    if closing and at_s >= round(timeline.duration_s - closing["duration"], 6):
        _fail("closing_transition", f"spatial frame at {at_s}s has an unsupported project-edge transition")
    starts = {span.scene_index: span.scene_start_s for span in timeline.spans}
    for index, scene in enumerate(spec["composition"]):
        transition = scene.get("transition_in")
        if not transition or index not in starts:
            continue
        cut = starts[index]
        half = transition["duration"] / 2
        if round(cut - half, 6) <= at_s < round(cut + half, 6):
            outgoing = spec["composition"][index - 1].get("id")
            incoming = scene.get("id")
            _fail(f"composition[{index}].transition_in",
                  f"spatial frame at {at_s}s has an unsupported {transition['type']} "
                  f"between {outgoing!r} and {incoming!r}")
