"""Project-owned layout prototypes with explicit inputs for every content leaf.

Component references and occurrence customizations remain linked. Inputs address
expanded leaves, including hardcoded content inherited from nested definitions;
no inherited media default is part of the saved layout's content contract.
"""

from __future__ import annotations

from copy import deepcopy

from moviestar.spatial_model import (
    SpatialModelError, _children, _format_rules, _identifier, _inputs,
    expand_spatial_scene, spatial_state_id,
)


CONTENT_TYPES = {'video', 'image', 'caption', 'text'}
_COMMON_FIELDS = {'layout', 'style', 'format_rules', 'motion'}


def _node_fields(node):
    return _COMMON_FIELDS | ({'instance_id', 'definition_id', 'bindings', 'customizations'}
                             if 'instance_id' in node else {'id', 'type', 'content', 'children'})


class SavedLayoutError(SpatialModelError):
    def __init__(self, diagnostics: list[dict]):
        self.diagnostics = diagnostics
        super().__init__('Resolve the reported layout paths before saving; no project changes were written.')


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get('children', []))


def _unsupported(path, reason):
    raise SavedLayoutError([{'code': 'unsupported_saved_layout_content', 'path': path,
                             'reason': reason}])


def _design_properties(owner, path, *, root=False):
    from moviestar.spatial_layout import SpatialLayoutError, _css, _LAYOUT, _ROOT, _STYLE
    try:
        layout = ({key: value for key, value in owner.items()
                   if key not in {'children', 'format_rules', 'reserved_regions'}}
                  if root else owner.get('layout', {}))
        _css(layout, _ROOT if root else _LAYOUT, '/'.join(path) + '.layout')
        if not root:
            _css(owner.get('style', {}), _STYLE, '/'.join(path) + '.style')
        for rule in owner.get('format_rules', []):
            where = '/'.join([*path, 'format_rules', rule['id']])
            _css(rule.get('layout', {}), _ROOT if root else _LAYOUT, where + '.layout')
            _css(rule.get('style', {}), _STYLE, where + '.style')
        for edit in owner.get('customizations', []):
            if edit['op'] == 'property_set':
                group, key = edit['property'].split('.')
                if group in {'style', 'layout'}:
                    _css({key: edit['value']}, _STYLE if group == 'style' else _LAYOUT,
                         '/'.join([*path, *edit['path'], group]))
    except SpatialLayoutError as exc:
        _unsupported(path, str(exc))


def _audit(spec: dict, layout: dict, expanded: dict) -> None:
    """Check inherited authored nodes too; expansion intentionally omits metadata."""
    active = {tuple(node['occurrence_path']) for node in _walk(expanded['children'])}
    definitions = spec['spatial'].get('definitions', {})

    def visit(nodes, parent, ancestry=()):
        for node in nodes:
            path = [*parent, node.get('id', node.get('instance_id'))]
            if tuple(path) not in active:
                continue
            extra = set(node) - _node_fields(node)
            if extra:
                _unsupported(path, f'unsupported fields: {sorted(extra)}; resolve source-specific cues or metadata first')
            if node.get('type') == 'shape' and node.get('content'):
                _unsupported(path, 'non-empty shape content cannot be exposed as a typed input')
            _design_properties(node, path)
            visit(node.get('children', []), path, ancestry)
            for edit in node.get('customizations', []):
                if edit['op'] == 'child_add':
                    visit([edit['node']], [*path, *edit['path']], ancestry)
            identity = node.get('definition_id')
            if identity in definitions and identity not in ancestry:
                visit(definitions[identity]['children'], path, (*ancestry, identity))

    visit(layout['children'], [expanded['scene_id']])


def _prototype(layout: dict, definitions: dict, path: list[str], *, capture: bool) -> dict:
    """Remove local content and mask component content defaults, without baking design."""
    from moviestar.spatial_layout import _ROOT
    allowed = {key.replace('-', '_') for key in _ROOT} | {'children', 'format_rules', 'reserved_regions'}
    for key in layout:
        if key.replace('-', '_') not in allowed:
            _unsupported([*path, key], 'this field cannot be preserved as scene-independent design')
    _design_properties(layout, path, root=True)
    result = deepcopy(layout)

    def nodes(children, parent):
        for node in children:
            here = [*parent, node.get('id', node.get('instance_id'))]
            extra = set(node) - _node_fields(node)
            if extra:
                _unsupported(here, f'unsupported fields: {sorted(extra)}; remove source-specific cues or metadata')
            _design_properties(node, here)
            if 'instance_id' in node:
                declarations = _inputs(definitions.get(node['definition_id'], {'inputs': []}), '/'.join(here))
                bindings = node.setdefault('bindings', {}) if capture else node.get('bindings', {})
                for name, declaration in declarations.items():
                    if declaration['type'] in CONTENT_TYPES and (capture or name in bindings):
                        bindings[name] = {'empty': True}
                for name, value in list(bindings.items()):
                    if name not in declarations and value != {'empty': True}:
                        _unsupported([*here, 'bindings', name], 'unknown input type; expose or resolve this binding first')
                for edit in node.get('customizations', []):
                    if edit['op'] == 'child_add':
                        nodes([edit['node']], [*here, *edit['path']])
            else:
                if node['type'] in CONTENT_TYPES:
                    node['content'] = {'empty': True}
                elif node['type'] == 'shape' and node.get('content'):
                    _unsupported(here, 'non-empty shape content cannot be exposed as a typed content input')
                nodes(node.get('children', []), here)

    nodes(result['children'], path)
    return result


def validate_saved_layouts(spec: dict) -> None:
    """Validate readable prototypes, not the readiness of their linked definitions."""
    spatial = spec.get('spatial') or {}
    layouts = spatial.get('layouts', {})
    if not isinstance(layouts, dict):
        raise SpatialModelError('spatial.layouts: must be an object')
    definitions = spatial.get('definitions', {})
    for identity, item in layouts.items():
        where = f'spatial.layouts.{identity}'
        _identifier(identity, where)
        if identity.startswith(('builtin:', 'saved:')):
            raise SpatialModelError(f'{where}: store a project ID without a catalog prefix')
        if not isinstance(item, dict) or set(item) != {'name', 'layout', 'inputs'}:
            raise SpatialModelError(f'{where}: expected name, layout, and inputs')
        _identifier(item['name'], f'{where}.name')
        declarations = _inputs(item, where)
        targets = set()
        for entry in declarations.values():
            if set(entry) - {'name', 'type', 'required', 'default', 'target'} or entry['type'] not in CONTENT_TYPES:
                raise SpatialModelError(f'{where}.inputs.{entry["name"]}: expected a typed content input and target')
            target = entry.get('target')
            if (not isinstance(target, list) or not target
                    or any(not isinstance(part, str) or not part.strip() for part in target)):
                raise SpatialModelError(f'{where}.inputs.{entry["name"]}.target: expected stable relative IDs')
            if tuple(target) in targets:
                raise SpatialModelError(f'{where}.inputs: duplicate target {target}')
            targets.add(tuple(target))
            if 'default' in entry and entry['type'] != 'text':
                raise SpatialModelError(f'{where}.inputs.{entry["name"]}.default: only explicit text defaults may be retained')
        layout = item['layout']
        if not isinstance(layout, dict):
            raise SpatialModelError(f'{where}.layout: expected an object')
        _format_rules(layout, f'{where}.layout')
        from moviestar.spatial_regions import SpatialRegionError, validate_reserved_regions
        try:
            validate_reserved_regions(layout.get('reserved_regions', []), f'{where}.layout.reserved_regions')
        except SpatialRegionError as exc:
            raise SpatialModelError(str(exc)) from exc
        _children(layout.get('children'), f'{where}.layout.children', definitions, {})
        if _prototype(layout, definitions, ['layouts', identity], capture=False) != layout:
            raise SpatialModelError(f'{where}.layout: content must be empty and exposed as named inputs, never implicit defaults')


def _expand(spec: dict, layout: dict, identity: str) -> dict:
    # A temporary structural owner lets the existing compiler resolve linked
    # definitions and customizations without a scene clock or an authored scene.
    root = f'saved:{identity}'
    spatial = spec.get('spatial') or {}
    probe = {'sources': spec['sources'], 'composition': [{'id': root, 'layout': {}}],
             'spatial': {'assets': spatial.get('assets', {}), 'definitions': spatial.get('definitions', {}),
                         'scenes': {root: {'layout': layout}}}}
    return expand_spatial_scene(probe, root)


def _content_nodes(expanded: dict) -> dict:
    return {tuple(node['occurrence_path'][1:]): node for node in _walk(expanded['children'])
            if node.get('type') in CONTENT_TYPES}


def saved_layout_diagnostics(spec: dict, identity: str) -> list[dict]:
    item = (spec.get('spatial') or {}).get('layouts', {}).get(identity)
    if item is None:
        raise SpatialModelError(f'layouts.{identity}: unknown saved layout')
    expanded = _expand(spec, item['layout'], identity)
    diagnostics = [entry for entry in expanded['diagnostics']
                   if entry['code'] not in {'missing_binding', 'unknown_asset'}]
    try:
        _audit(spec, item['layout'], expanded)
    except SavedLayoutError as exc:
        diagnostics.extend(exc.diagnostics)
    current = _content_nodes(expanded)
    declared = {tuple(entry['target']): entry['type'] for entry in item['inputs']}
    for path in sorted(current.keys() | declared.keys()):
        if current.get(path, {}).get('type') != declared.get(path):
            diagnostics.append({'code': 'saved_layout_content_changed', 'severity': 'error',
                'path': ['layouts', identity, *path],
                'reason': 'a content target was added, removed, or changed type; replace the saved layout to review its inputs'})
    return diagnostics


def inspect_saved_layout(spec: dict, identity: str) -> dict:
    item = (spec.get('spatial') or {}).get('layouts', {}).get(identity)
    if item is None:
        raise SpatialModelError(f'layouts.{identity}: unknown saved layout')
    expanded = _expand(spec, item['layout'], identity)
    declarations = {tuple(entry['target']): entry for entry in item['inputs']}
    components, fonts = set(), set()
    last_keyframe = 0

    def design(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.replace('-', '_') == 'font_family' and isinstance(child, str):
                    fonts.add(child)
                design(child)
        elif isinstance(value, list):
            for child in value:
                design(child)

    design(expanded)
    for node in _walk(expanded['children']):
        if 'definition_id' in node:
            components.add(node['definition_id'])
        for frames in node.get('motion', {}).values():
            last_keyframe = max(last_keyframe, frames[-1]['at'])
        if node.get('type') in CONTENT_TYPES:
            declaration = declarations.get(tuple(node['occurrence_path'][1:]))
            value = declaration.get('default', {'input': declaration['name']}) if declaration else {'empty': True}
            node['content'] = deepcopy(value) if isinstance(value, dict) else {'text': value}
    expanded.pop('scene_id')
    expanded.pop('diagnostics', None)
    return {'id': f'saved:{identity}', 'kind': 'saved', **deepcopy(item), 'expanded': expanded,
            'design_dependencies': {'components': sorted(components), 'fonts': sorted(fonts)},
            'motion': {'clock': 'scene', 'last_keyframe_s': last_keyframe},
            'diagnostics': saved_layout_diagnostics(spec, identity), 'can_apply': False}


def save_layout(spec: dict, identity: str, scene_id: str, *, name: str | None = None,
                keep_text: bool = False, replace: bool = False,
                expected_state_id: str | None = None) -> tuple[dict, dict]:
    current = spatial_state_id(spec)
    if expected_state_id is not None and expected_state_id != current:
        raise SpatialModelError(f'expected_state_id: project changed; current state is {current}')
    _identifier(identity, 'layout_id')
    scene = (spec.get('spatial') or {}).get('scenes', {}).get(scene_id)
    if scene is None:
        raise SpatialModelError(f'scene {scene_id!r}: no spatial tree; choose a scene with an editable layout')
    layouts = spec['spatial'].get('layouts', {})
    if identity in layouts and not replace:
        raise SpatialModelError(f'layouts.{identity}: already exists; use --replace after reviewing dry-run')
    expanded = expand_spatial_scene(spec, scene_id)
    blocking = [entry for entry in expanded['diagnostics'] if entry['code'] not in {'missing_binding', 'unknown_asset'}]
    if blocking:
        raise SavedLayoutError(blocking)
    _audit(spec, scene['layout'], expanded)
    inputs, names = [], set()
    missing = {tuple(entry['path'][1:]) for entry in expanded['diagnostics'] if entry['code'] == 'missing_binding'}
    for path, node in _content_nodes(expanded).items():
        base, suffix = '_'.join(path), 2
        candidate = base
        while candidate in names:
            candidate = f'{base}_{suffix}'
            suffix += 1
        names.add(candidate)
        optional_empty = (node['type'] in {'caption', 'image'} and path not in missing
                          and node.get('content') in (None, {'empty': True}))
        entry = {'name': candidate, 'type': node['type'], 'required': not optional_empty, 'target': list(path)}
        if keep_text and node['type'] == 'text' and isinstance(node.get('content'), dict):
            entry.update(required=False, default=deepcopy(node['content']))
        inputs.append(entry)
    item = {'name': name if name is not None else identity,
            'layout': _prototype(scene['layout'], spec['spatial'].get('definitions', {}), [scene_id], capture=True),
            'inputs': inputs}
    changed = deepcopy(spec)
    changed['spatial'].setdefault('layouts', {})[identity] = item
    from moviestar.spec import validate_spec
    validate_spec(changed)
    view = inspect_saved_layout(changed, identity)
    if view['diagnostics']:
        raise SavedLayoutError(view['diagnostics'])
    return changed, {'layout_id': f'saved:{identity}', 'saved_layout': deepcopy(item),
                      'inputs': view['inputs'], 'design_dependencies': view['design_dependencies'],
                      'motion': view['motion'], 'affected': [], 'replaces_layout': identity in layouts,
                      'writes_spec': False, 'state_id_before': current, 'state_id_after': spatial_state_id(changed)}
