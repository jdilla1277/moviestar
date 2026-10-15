"""Persisted, scene-owned visual trees and reusable component definitions.

This module resolves structure and bindings only. The established composition
still owns scene order and timing; pixel layout and rendering follow later.
Incomplete references remain readable drafts and appear as diagnostics during
expansion. Malformed structure and ambiguous identities fail validation.
"""

from __future__ import annotations

from copy import deepcopy
from math import isfinite


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
            if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not isfinite(n) for n in (start, end)) or start < 0 or end <= start:
                _fail(f"{path}.source", "needs finite 0 <= start < end")
        return
    key = {"text": "text", "number": "number", "color": "color", "boolean": "boolean"}.get(kind)
    if key is None:
        _fail(path, f"unsupported binding type {kind!r}")
    if not isinstance(value, dict) or set(value) != {key}:
        _fail(path, f"{kind} binding needs {key!r}")
    item = value[key]
    expected = {"text": str, "number": (int, float), "color": str, "boolean": bool}[kind]
    if not isinstance(item, expected) or (kind == "number" and (isinstance(item, bool) or not isfinite(item))):
        _fail(path, f"{kind} binding has wrong value type")


def _inputs(definition: dict, path: str) -> dict[str, dict]:
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
        if kind not in _INPUT_TYPES:
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
            if kind not in _ELEMENT_TYPES:
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
                      required: set[str]) -> object:
        if isinstance(value, dict) and "input" in value:
            name = value["input"]
            value = inputs.get(name)
            if value is None or (name in required and isinstance(value, dict) and value.get("empty") is True):
                diagnostics.append({"code": "missing_binding", "path": path, "input": name})
                return None
        if isinstance(value, dict) and value.get("empty") is True:
            return None
        if isinstance(value, dict) and "asset_id" in value and kind == "video" and value["asset_id"] not in sources:
            diagnostics.append({"code": "unknown_asset", "path": path, "asset_id": value["asset_id"]})
        if isinstance(value, dict) and "asset_id" in value and kind == "image" and value["asset_id"] not in images:
            diagnostics.append({"code": "unknown_asset", "path": path, "asset_id": value["asset_id"]})
        return deepcopy(value)

    def expand_nodes(nodes: list, path: list[str], inputs: dict,
                     required: set[str], ancestry: tuple[str, ...]) -> list[dict]:
        expanded = []
        for node in nodes:
            identity = node.get("id", node.get("instance_id"))
            address = [*path, identity]
            common = {"occurrence_path": address, "layout": deepcopy(node.get("layout", {})),
                      "style": deepcopy(node.get("style", {}))}
            if "id" in node:
                kind = node["type"]
                content = resolve_value(node.get("content"), inputs, address, kind, required)
                item = {**common, "type": kind, "content": content}
                if "children" in node:
                    item["children"] = expand_nodes(node["children"], address, inputs, required, ancestry)
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
            item["children"] = expand_nodes(definition["children"], address, bindings,
                                            mandatory, (*ancestry, definition_id))
            used = {child.get("content", {}).get("input") for child in definition["children"] if isinstance(child.get("content"), dict)}
            used.update(value["forward"] for child in definition["children"]
                        for value in child.get("bindings", {}).values()
                        if isinstance(value, dict) and "forward" in value)
            for name in mandatory:
                value = bindings[name]
                if (value is None or (isinstance(value, dict) and value.get("empty") is True)) and name not in used:
                    diagnostics.append({"code": "missing_binding", "path": address, "input": name})
        return expanded

    layout = scenes[scene_id]["layout"]
    return {"scene_id": scene_id, **{k: deepcopy(v) for k, v in layout.items() if k != "children"},
            "children": expand_nodes(layout["children"], [scene_id], {}, set(), ()),
            "diagnostics": diagnostics}
