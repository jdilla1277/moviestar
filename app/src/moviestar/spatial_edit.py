"""Atomic edits to project-owned spatial definitions and occurrences.

Local edits are stored as operations against stable child IDs on the nearest
scene-owned component instance. They are replayed over the current definition,
so compatible shared changes continue to reach that occurrence.
"""

from __future__ import annotations

from copy import deepcopy

from moviestar.spatial_model import SpatialModelError, expand_spatial_scene


class SpatialEditConflict(SpatialModelError):
    """A proposed shared edit would invalidate one or more linked uses."""

    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        paths = ["/".join(item["path"]) for item in conflicts]
        super().__init__(f"shared edit conflict at {', '.join(paths)}; reset the affected customization or save a variation")


def _fail(message: str) -> None:
    raise SpatialModelError(message)


def _path(value: object, field: str) -> list[str]:
    if not isinstance(value, dict) or not isinstance(value.get(field), list):
        _fail(f"target.{field}: expected an array of stable IDs")
    result = value[field]
    if not result or any(not isinstance(part, str) or not part.strip() for part in result):
        _fail(f"target.{field}: expected non-empty stable IDs")
    return result


def _identity(node: dict) -> str:
    return node.get("id", node.get("instance_id"))


def _find_child(children: list[dict], identity: str, where: str) -> tuple[dict, int]:
    for index, child in enumerate(children):
        if _identity(child) == identity:
            return child, index
    _fail(f"{where}: child {identity!r} does not exist")


def _scene_target(spatial: dict, path: list[str]) -> tuple[dict | None, list[str], dict | None, list[dict] | None, int | None]:
    """Return owning component, relative path, direct node, siblings, index."""
    scene_id = path[0]
    scene = spatial.get("scenes", {}).get(scene_id)
    if scene is None:
        _fail(f"spatial.scenes.{scene_id}: no visual tree")
    if len(path) == 1:
        return None, [], scene["layout"], None, None
    children = scene["layout"]["children"]
    for depth, identity in enumerate(path[1:], start=1):
        node, index = _find_child(children, identity, "/".join(path[:depth]))
        if depth == len(path) - 1:
            return None, [], node, children, index
        if "instance_id" in node:
            return node, path[depth + 1:], None, None, None
        children = node.get("children", [])
    raise AssertionError("unreachable")


def _definition_target(spatial: dict, target: dict) -> tuple[dict, list[dict] | None, int | None]:
    definition_id = target.get("definition_id") if isinstance(target, dict) else None
    definition = spatial.get("definitions", {}).get(definition_id)
    if definition is None:
        _fail(f"spatial.definitions.{definition_id}: unknown component")
    path = target.get("path")
    if path is None and "element_id" in target:
        path = [target["element_id"]]
    if path is None:
        return definition, None, None
    if not isinstance(path, list) or not path or any(not isinstance(part, str) or not part for part in path):
        _fail("target.path: expected stable child IDs")
    children = definition["children"]
    for depth, identity in enumerate(path):
        node, index = _find_child(children, identity, f"spatial.definitions.{definition_id}")
        if depth == len(path) - 1:
            return node, children, index
        if "instance_id" in node:
            _fail(f"target.path: {identity!r} belongs to a different definition; edit that definition or an occurrence")
        children = node.get("children", [])
    raise AssertionError("unreachable")


def _property(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or len(value.split(".")) != 2:
        _fail("property: use style.KEY or layout.KEY")
    group, key = value.split(".")
    if group not in {"style", "layout"} or not key:
        _fail("property: use style.KEY or layout.KEY")
    return group, key


def _insert(children: list[dict], child: dict, after_id: str | None, where: str) -> None:
    if not isinstance(child, dict) or not isinstance(_identity(child), str):
        _fail(f"{where}: child needs an id or instance_id")
    identity = _identity(child)
    if any(_identity(item) == identity for item in children):
        _fail(f"{where}: duplicate sibling identity {identity!r}")
    if after_id is None:
        children.insert(0, deepcopy(child))
    else:
        _, index = _find_child(children, after_id, where)
        children.insert(index + 1, deepcopy(child))


def _move(children: list[dict], identity: str, after_id: str | None, where: str) -> None:
    node, index = _find_child(children, identity, where)
    if after_id == identity:
        _fail(f"{where}: cannot move a child after itself")
    children.pop(index)
    try:
        _insert(children, node, after_id, where)
    except SpatialModelError:
        children.insert(index, node)
        raise


def _local_edit(spatial: dict, request: dict) -> None:
    op = request["op"]
    path = _path(request.get("target"), "occurrence_path")
    root, relative, direct, siblings, index = _scene_target(spatial, path)
    if op == "instance.customize":
        group, key = _property(request.get("property"))
        if "value" not in request:
            _fail("value: required")
        if root is None:
            direct.setdefault(group, {})[key] = deepcopy(request["value"])
        else:
            root.setdefault("customizations", []).append({"op": "property_set", "path": relative,
                                                            "property": f"{group}.{key}",
                                                            "value": deepcopy(request["value"])})
    elif op == "instance.child_insert":
        child = request.get("child")
        after_id = request.get("after_id")
        if root is None and "instance_id" not in direct:
            direct.setdefault("children", [])
            _insert(direct["children"], child, after_id, "/".join(path))
        else:
            if not isinstance(child, dict) or not isinstance(_identity(child), str):
                _fail("child: needs an id or instance_id")
            owner = root or direct
            owner.setdefault("customizations", []).append({"op": "child_add", "path": relative,
                                                             "node": deepcopy(child), "after_id": after_id})
    elif op in {"instance.child_remove", "instance.child_move"}:
        if len(path) < 2:
            _fail("target.occurrence_path: choose a child")
        if root is None:
            if op == "instance.child_remove":
                siblings.pop(index)
            else:
                _move(siblings, path[-1], request.get("after_id"), "/".join(path[:-1]))
        else:
            edit = {"op": "child_remove" if op.endswith("remove") else "child_move",
                    "path": relative}
            if op == "instance.child_move":
                edit["after_id"] = request.get("after_id")
            root.setdefault("customizations", []).append(edit)
    elif op == "instance.reset":
        prop = request.get("property")
        if prop is not None:
            group, key = _property(prop)
        if root is None:
            if prop is None:
                _fail("instance.reset: a direct scene node needs a property")
            direct.get(group, {}).pop(key, None)
        else:
            edits = root.get("customizations", [])
            if prop is None:
                # Reset this subtree, including edits to descendants of a local child.
                root["customizations"] = [edit for edit in edits if not (
                    (edit["path"] if edit["op"] != "child_add" else
                     [*edit["path"], _identity(edit["node"])])[:len(relative)] == relative
                )]
            else:
                root["customizations"] = [edit for edit in edits if not (
                    edit["op"] == "property_set" and edit["path"] == relative and edit["property"] == prop
                )]


def _shared_edit(spatial: dict, request: dict) -> None:
    op = request["op"]
    target = request.get("target")
    if not isinstance(target, dict):
        _fail("target: expected a definition target")
    definition_id = target.get("definition_id")
    node, siblings, index = _definition_target(spatial, target)
    if op == "component.update":
        if siblings is None:
            _fail("component.update: choose a child; definitions do not have rendered style")
        group, key = _property(request.get("property"))
        if "value" not in request:
            _fail("value: required")
        node.setdefault(group, {})[key] = deepcopy(request["value"])
    elif op == "component.child_insert":
        if "instance_id" in node:
            _fail("component.child_insert: choose the referenced definition or customize an occurrence")
        node.setdefault("children", [])
        _insert(node["children"], request.get("child"), request.get("after_id"), str(definition_id))
    elif op == "component.child_remove":
        if siblings is None:
            _fail("component.child_remove: choose a child")
        siblings.pop(index)
    elif op == "component.child_move":
        if siblings is None:
            _fail("component.child_move: choose a child")
        _move(siblings, _identity(node), request.get("after_id"), str(definition_id))


def _bake_variation(children: list[dict], edits: list[dict], path: list[str]) -> list[dict]:
    """Fold outer occurrence changes into a copied definition, retaining nested links."""
    result = deepcopy(children)
    for edit in edits:
        op, target = edit["op"], edit["path"]
        if op == "child_add" and target == path:
            _insert(result, edit["node"], edit.get("after_id"), "/".join(path))
        elif op in {"child_remove", "child_move"} and target[:-1] == path:
            if op == "child_remove":
                _, index = _find_child(result, target[-1], "/".join(path))
                result.pop(index)
            else:
                _move(result, target[-1], edit.get("after_id"), "/".join(path))
    for node in result:
        node_path = [*path, _identity(node)]
        for edit in edits:
            if edit["op"] == "property_set" and edit["path"] == node_path:
                group, key = _property(edit["property"])
                node.setdefault(group, {})[key] = deepcopy(edit["value"])
        if "instance_id" in node:
            nested = []
            for edit in edits:
                if len(edit["path"]) > len(node_path) and edit["path"][:len(node_path)] == node_path:
                    nested.append({**deepcopy(edit), "path": edit["path"][len(node_path):]})
                elif edit["op"] == "child_add" and edit["path"] == node_path:
                    nested.append({**deepcopy(edit), "path": []})
            if nested:
                node.setdefault("customizations", []).extend(nested)
        elif "children" in node:
            node["children"] = _bake_variation(node["children"], edits, node_path)
    return result


def _save_variation(spatial: dict, request: dict) -> list[str]:
    path = _path(request.get("source"), "occurrence_path")
    root, relative, direct, _, _ = _scene_target(spatial, path)
    if root is not None or direct is None or "instance_id" not in direct:
        _fail("source.occurrence_path: choose a scene-owned component instance")
    new_id = request.get("new_definition_id")
    if not isinstance(new_id, str) or not new_id.strip() or new_id in spatial["definitions"]:
        _fail("new_definition_id: use an unused, non-empty component ID")
    definition = deepcopy(spatial["definitions"].get(direct["definition_id"]))
    if definition is None:
        _fail("source.occurrence_path: unknown component definition")
    definition["name"] = request.get("name", f"{definition['name']} variation")
    definition["children"] = _bake_variation(definition["children"], direct.get("customizations", []), [])
    spatial["definitions"][new_id] = definition
    if request.get("relink_source", False):
        direct["definition_id"] = new_id
        direct.pop("customizations", None)
    return [new_id]


def _bind_input(spatial: dict, request: dict) -> None:
    path = _path(request.get("target"), "occurrence_path")
    root, _, direct, _, _ = _scene_target(spatial, path)
    if root is not None or direct is None or "instance_id" not in direct:
        _fail("input.bind: choose a scene-owned component instance")
    name = request.get("input")
    if not isinstance(name, str) or not name.strip() or "value" not in request:
        _fail("input.bind: provide an input name and value")
    direct.setdefault("bindings", {})[name] = deepcopy(request["value"])


def _duplicate_instance(spatial: dict, request: dict) -> None:
    path = _path(request.get("source"), "occurrence_path")
    root, _, direct, siblings, index = _scene_target(spatial, path)
    if root is not None or direct is None or siblings is None or "instance_id" not in direct:
        _fail("instance.duplicate: choose a scene-owned component instance")
    new_id = request.get("new_instance_id")
    if not isinstance(new_id, str) or not new_id.strip():
        _fail("new_instance_id: provide a non-empty stable ID")
    if any(_identity(node) == new_id for node in siblings):
        _fail(f"new_instance_id: duplicate sibling identity {new_id!r}")
    copy = deepcopy(direct)
    copy["instance_id"] = new_id
    siblings.insert(index + 1, copy)


def _reference_paths(spatial: dict, definition_id: str) -> list[list[str]]:
    uses = []

    def walk(nodes: list[dict], prefix: list[str]) -> None:
        for node in nodes:
            path = [*prefix, _identity(node)]
            if node.get("definition_id") == definition_id:
                uses.append(path)
            if "children" in node:
                walk(node["children"], path)
            for edit in node.get("customizations", []):
                if edit["op"] == "child_add":
                    walk([edit["node"]], path)

    for scene_id, scene in spatial.get("scenes", {}).items():
        walk(scene["layout"]["children"], [scene_id])
    for owner_id, definition in spatial.get("definitions", {}).items():
        if owner_id != definition_id:
            walk(definition["children"], [owner_id])
    return uses


def _asset_reference_paths(spatial: dict, asset_id: str) -> list[list[str]]:
    uses = []

    def scan(value: object, path: list[str]) -> None:
        if isinstance(value, dict):
            if value.get("asset_id") == asset_id:
                uses.append(path)
            for key, child in value.items():
                scan(child, [*path, key])
        elif isinstance(value, list):
            for index, child in enumerate(value):
                scan(child, [*path, str(index)])

    scan(spatial.get("definitions", {}), ["definitions"])
    scan(spatial.get("scenes", {}), ["scenes"])
    return uses


def _conflicts(spec: dict) -> list[dict]:
    conflicts = []
    spatial = spec.get("spatial", {})
    for scene_id in spatial.get("scenes", {}):
        conflicts.extend(item for item in expand_spatial_scene(spec, scene_id)["diagnostics"]
                         if item["code"] == "customization_conflict")
    # Definitions can contain locally customized nested instances even when
    # no scene currently places them. Probe each definition without writing a
    # synthetic scene to the project; shared edits must preserve those drafts.
    for definition_id in spatial.get("definitions", {}):
        probe_id = f"definition:{definition_id}"
        probe = {
            "sources": spec["sources"],
            "composition": [{"id": probe_id, "layout": {}}],
            "spatial": {"assets": spatial.get("assets", {}),
                        "definitions": spatial["definitions"],
                        "scenes": {probe_id: {"layout": {"children": [
                            {"instance_id": "__root__", "definition_id": definition_id}
                        ]}}}},
        }
        for item in expand_spatial_scene(probe, probe_id)["diagnostics"]:
            if item["code"] == "customization_conflict":
                conflicts.append({**item, "path": [probe_id, *item["path"][2:]]})
    return conflicts


def _occurrences(scene: dict) -> dict[tuple[str, ...], dict]:
    result = {}

    def walk(children: list[dict]) -> None:
        for child in children:
            result[tuple(child["occurrence_path"])] = {
                key: value for key, value in child.items() if key not in {"children", "occurrence_path"}
            }
            walk(child.get("children", []))

    walk(scene["children"])
    return result


def edit_spatial_model(spec: dict, request: dict) -> tuple[dict, dict]:
    """Return a valid edited spec and a JSON-ready impact report, without writing."""
    if not isinstance(request, dict) or not isinstance(request.get("op"), str):
        _fail("request: expected an object with an op string")
    expected = request.get("expected_revision")
    if expected is not None and expected != len(spec.get("revisions", [])):
        _fail(f"expected_revision: current revision is {len(spec.get('revisions', []))}")
    spatial = spec.get("spatial")
    if not isinstance(spatial, dict):
        _fail("spatial: apply a spatial document before editing")
    before_scenes = {scene_id: expand_spatial_scene(spec, scene_id)
                     for scene_id in spatial.get("scenes", {})}
    before = _conflicts(spec)
    result = deepcopy(spec)
    spatial = result["spatial"]
    op = request["op"]
    created = []
    if op in {"instance.customize", "instance.child_insert", "instance.child_remove",
              "instance.child_move", "instance.reset"}:
        _local_edit(spatial, request)
    elif op in {"component.update", "component.child_insert", "component.child_remove",
                "component.child_move"}:
        _shared_edit(spatial, request)
    elif op == "component.save_variation":
        created = _save_variation(spatial, request)
    elif op == "input.bind":
        _bind_input(spatial, request)
    elif op == "instance.duplicate":
        _duplicate_instance(spatial, request)
    elif op == "component.rename":
        definition_id = request.get("definition_id")
        definition = spatial.get("definitions", {}).get(definition_id)
        if definition is None:
            _fail(f"spatial.definitions.{definition_id}: unknown component")
        name = request.get("name")
        if not isinstance(name, str) or not name.strip():
            _fail("name: provide a non-empty component name")
        definition["name"] = name
    elif op == "component.delete":
        definition_id = request.get("definition_id")
        if definition_id not in spatial.get("definitions", {}):
            _fail(f"spatial.definitions.{definition_id}: unknown component")
        uses = _reference_paths(spatial, definition_id)
        if uses:
            _fail(f"spatial.definitions.{definition_id}: referenced at {uses}")
        del spatial["definitions"][definition_id]
    elif op == "asset.delete":
        asset_id = request.get("asset_id")
        if asset_id not in spatial.get("assets", {}):
            _fail(f"spatial.assets.{asset_id}: unknown image asset")
        uses = _asset_reference_paths(spatial, asset_id)
        if uses:
            _fail(f"spatial.assets.{asset_id}: referenced at {uses}")
        del spatial["assets"][asset_id]
    else:
        _fail(f"op: unsupported model edit {op!r}")
    from moviestar.spec import SpecValidationError, validate_spec
    try:
        validate_spec(result)
    except SpecValidationError as exc:
        _fail(str(exc))
    after_scenes = {scene_id: expand_spatial_scene(result, scene_id)
                    for scene_id in spatial.get("scenes", {})}
    new_conflicts = [item for item in _conflicts(result) if item not in before]
    if new_conflicts:
        raise SpatialEditConflict(new_conflicts)
    affected = [scene_id for scene_id in before_scenes if before_scenes[scene_id] != after_scenes[scene_id]]
    affected_occurrences = []
    for scene_id in affected:
        prior = _occurrences(before_scenes[scene_id])
        current = _occurrences(after_scenes[scene_id])
        affected_occurrences.extend(list(path) for path in sorted(prior.keys() | current.keys())
                                    if prior.get(path) != current.get(path))
    return result, {"op": op, "affected": sorted(affected), "created": created,
                    "affected_occurrences": affected_occurrences,
                    "conflicts": [], "writes_spec": False}
