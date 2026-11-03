"""Editable built-in arrangements, copied into ordinary project spatial trees.

Only the catalog lives in the package. Captured definitions are project-owned;
subsequent applications reuse those definitions, including the user's edits.
"""

from __future__ import annotations

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from moviestar.spatial_model import (
    SpatialModelError, _binding, expand_spatial_scene, spatial_state_id,
)


VIDEO = 'builtin:video-frame'
PIP = 'builtin:picture-in-picture'
TEXT = 'builtin:text-panel'
CAPTION = 'builtin:caption-region'


def _input(name: str, kind: str, *, required: bool = True, **extra) -> dict:
    return {'name': name, 'type': kind, 'required': required, **extra}


def _instance(identity: str, definition: str, **extra) -> dict:
    return {'instance_id': identity, 'definition_id': definition, **extra}


DEFINITIONS = {
    CAPTION: {
        'name': 'CaptionRegion', 'origin': CAPTION,
        'inputs': [_input('track', 'caption', required=False, default={'empty': True})],
        'children': [{'id': 'caption', 'type': 'caption', 'content': {'input': 'track'},
                      'layout': {'width': '100%', 'height': '100%', 'overflow': 'hidden'},
                      'style': {'font_family': 'sans-serif', 'font_size': 'clamp(12px, 2.8vw, 44px)', 'color': '#ffffff',
                                'text_align': 'center', 'line_height': 1.2}}],
    },
    VIDEO: {
        'name': 'VideoFrame', 'origin': VIDEO,
        'inputs': [_input('footage', 'video')],
        'children': [{'id': 'video', 'type': 'video', 'content': {'input': 'footage'},
                      'layout': {'width': '100%', 'height': '100%', 'overflow': 'hidden'},
                      'style': {'object_fit': 'cover'}}],
    },
    PIP: {
        'name': 'PictureInPicture', 'origin': PIP,
        'inputs': [_input('main', 'video'), _input('inset', 'video')],
        'children': [{'id': 'panel', 'type': 'shape',
                      'layout': {'width': '100%', 'height': '100%', 'overflow': 'hidden'},
                      'children': [
                          _instance('main', VIDEO, bindings={'footage': {'forward': 'main'}},
                                    layout={'width': '100%', 'height': '100%'}),
                          _instance('inset', VIDEO, bindings={'footage': {'forward': 'inset'}},
                                    layout={'position': 'absolute', 'right': '4%', 'bottom': '12%',
                                            'width': '30%', 'height': '35%', 'overflow': 'hidden'},
                                    style={'border_radius': 12, 'border_width': 2,
                                           'border_style': 'solid', 'border_color': '#ffffff'}),
                      ]}],
    },
    TEXT: {
        'name': 'TextPanel', 'origin': TEXT,
        'inputs': [_input('title', 'text'), _input('body', 'text'),
                   _input('image', 'image', required=False, default={'empty': True})],
        'children': [{'id': 'panel', 'type': 'shape',
                      'layout': {'display': 'flex', 'flex_direction': 'column',
                                 'justify_content': 'center', 'gap': '3%',
                                 'height': '100%', 'overflow': 'hidden'},
                      'style': {'font_family': 'sans-serif'},
                      'children': [
                          {'id': 'title', 'type': 'text', 'content': {'input': 'title'},
                           'style': {'font_size': 'clamp(18px, 4vw, 64px)', 'font_weight': 700,
                                     'color': '#ffffff', 'line_height': 1.15}},
                          {'id': 'body', 'type': 'text', 'content': {'input': 'body'},
                           'style': {'font_size': 'clamp(12px, 2vw, 32px)', 'color': '#ccd5df',
                                     'line_height': 1.4}},
                          {'id': 'image', 'type': 'image', 'content': {'input': 'image'},
                           'layout': {'width': '25%', 'height': '15%'},
                           'style': {'object_fit': 'contain'}},
                      ]}],
    },
}


def _slot(name: str, kind: str, node: str, component_input: str,
          *, required: bool = True) -> dict:
    target = {'node': node, 'input': component_input}
    return _input(name, kind, required=required, target=target)


CAPTIONS = {
    'instance_id': 'captions', 'definition_id': CAPTION,
    'layout': {'position': 'absolute', 'left': '8%', 'bottom': '3%', 'width': '84%',
               'height': '16%', 'z_index': 10, 'overflow': 'hidden'},
}


LAYOUTS = {
    'single': {
        'name': 'Single video', 'description': 'One editable video frame filling the canvas.',
        'inputs': [_slot('video', 'video', 'main', 'footage')],
        'layout': {'children': [_instance('main', VIDEO, layout={'width': '100%', 'height': '100%'})]},
    },
    'two-up': {
        'name': 'Two-up', 'description': 'Two equal video frames; stacked in portrait.',
        'inputs': [_slot('left', 'video', 'left', 'footage'), _slot('right', 'video', 'right', 'footage')],
        'layout': {'display': 'grid', 'columns': 'minmax(0, 1fr) minmax(0, 1fr)',
                   'rows': 'minmax(0, 1fr)', 'gap': '2%',
                   'format_rules': [{'id': 'portrait', 'when': {'orientation': 'portrait'},
                                     'layout': {'columns': 'minmax(0, 1fr)',
                                                'rows': 'minmax(0, 1fr) minmax(0, 1fr)'}}],
                   'children': [_instance(name, VIDEO) for name in ('left', 'right')]},
    },
    'pip': {
        'name': 'Picture-in-picture', 'description': 'A main video and a reusable bordered inset.',
        'inputs': [_slot('main', 'video', 'picture', 'main'), _slot('inset', 'video', 'picture', 'inset')],
        'layout': {'children': [_instance('picture', PIP, layout={'width': '100%', 'height': '100%'})]},
    },
    'text-video': {
        'name': 'Text beside video', 'description': 'A non-video TextPanel beside footage; stacked in portrait.',
        'inputs': [_slot('video', 'video', 'speaker', 'footage'),
                   _slot('title', 'text', 'text', 'title'), _slot('body', 'text', 'text', 'body'),
                   _slot('image', 'image', 'text', 'image', required=False)],
        'layout': {'display': 'grid', 'columns': 'minmax(0, 3fr) minmax(0, 2fr)',
                   'rows': 'minmax(0, 1fr)', 'gap': '4%', 'padding': '4% 4% 12%',
                   'format_rules': [{'id': 'portrait', 'when': {'orientation': 'portrait'},
                                     'layout': {'columns': 'minmax(0, 1fr)',
                                                'rows': 'minmax(0, 1fr) minmax(0, 1fr)'}}],
                   'children': [_instance('text', TEXT), _instance('speaker', VIDEO)]},
    },
}
for _layout in LAYOUTS.values():
    _layout['inputs'].append(_slot('captions', 'caption', 'captions', 'track', required=False))
    _layout['layout']['children'].append(deepcopy(CAPTIONS))
    _layout['layout']['background_color'] = '#101820'


def _dependencies(layout: dict) -> dict:
    result = {}

    def visit(nodes: list) -> None:
        for node in nodes:
            identity = node.get('definition_id')
            if identity is not None and identity not in result:
                result[identity] = deepcopy(DEFINITIONS[identity])
                visit(result[identity]['children'])
            visit(node.get('children', []))

    visit(layout['children'])
    return result


def builtin_layout(identity: str) -> dict:
    if identity not in LAYOUTS:
        raise SpatialModelError(f'layout: unknown built-in {identity!r}; choose {", ".join(LAYOUTS)}')
    result = deepcopy(LAYOUTS[identity])
    return {'id': identity, **result, 'definitions': _dependencies(result['layout'])}


def list_builtin_layouts() -> list[dict]:
    return [{'id': identity, **deepcopy({key: item[key] for key in ('name', 'description', 'inputs')})}
            for identity, item in LAYOUTS.items()]


def apply_builtin_layout(spec: dict, identity: str, scene_id: str, bindings: dict | None = None,
                         *, replace: bool = False, expected_state_id: str | None = None) -> tuple[dict, dict]:
    """Plan a copy and definition capture without mutating the supplied revision."""
    current = spatial_state_id(spec)
    if expected_state_id is not None and expected_state_id != current:
        raise SpatialModelError(f'expected_state_id: project changed; current state is {current}')
    item = builtin_layout(identity)
    if scene_id not in {scene.get('id') for scene in spec.get('composition') or [] if 'layout' in scene}:
        raise SpatialModelError(f'scene: unknown timeline scene ID {scene_id!r}; use scenes set first')
    if bindings is None:
        bindings = {}
    if not isinstance(bindings, dict):
        raise SpatialModelError('bindings: expected an object of named typed values')
    declarations = {entry['name']: entry for entry in item['inputs']}
    unknown = bindings.keys() - declarations.keys()
    if unknown:
        raise SpatialModelError(f'bindings: unknown inputs {sorted(unknown)}')
    for name, value in bindings.items():
        _binding(value, declarations[name]['type'], f'bindings.{name}')
    result = deepcopy(spec)
    if result.get('spatial') is None:
        result['spatial'] = {}
    spatial = result['spatial']
    scenes = spatial.setdefault('scenes', {})
    replaces = scene_id in scenes
    if replaces and not replace:
        raise SpatialModelError(f'scene {scene_id!r} already has a spatial layout; use --replace to replace it')
    definitions = spatial.setdefault('definitions', {})
    captured, reused = [], []
    for definition_id, definition in item['definitions'].items():
        if definition_id in definitions:
            if definitions[definition_id].get('origin') != definition_id:
                raise SpatialModelError(f'definitions.{definition_id}: ID already belongs to another component')
            inputs = {entry['name']: entry['type'] for entry in definitions[definition_id].get('inputs', [])}
            for entry in definition['inputs']:
                if inputs.get(entry['name']) != entry['type']:
                    raise SpatialModelError(f"definitions.{definition_id}.inputs.{entry['name']}: "
                                            f"captured component is incompatible with the catalog's {entry['type']} binding")
            reused.append(definition_id)
        else:
            definitions[definition_id] = definition
            captured.append(definition_id)
    layout = item['layout']
    nodes = {node.get('id', node.get('instance_id')): node for node in layout['children']}
    for name, value in bindings.items():
        target = declarations[name]['target']
        node = nodes[target['node']]
        node.setdefault('bindings', {})[target['input']] = deepcopy(value)

    # Deterministic IDs let dry-run describe the exact eventual tree. Include
    # the state fingerprint so repeated replacements do not recycle old IDs.
    used = set()

    def scan(value):
        if isinstance(value, dict):
            for field in ('id', 'instance_id'):
                if isinstance(value.get(field), str):
                    used.add(value[field])
            for child in value.values():
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)

    scan(spec.get('spatial', {}))

    def identify(children, parent: tuple[str, ...] = ()):
        for node in children:
            field = 'instance_id' if 'instance_id' in node else 'id'
            path = (*parent, node[field])
            token = uuid5(NAMESPACE_URL, f'moviestar:{current}:{scene_id}:{identity}:{path}').hex
            base, suffix = f"{node[field]}_{token}", 1
            allocated = base
            while allocated in used:
                allocated = f'{base}_{suffix}'
                suffix += 1
            node[field] = allocated
            used.add(node[field])
            identify(node.get('children', []), path)

    identify(layout['children'])
    scenes[scene_id] = {'layout': layout}
    from moviestar.spec import validate_spec
    validate_spec(result)
    expanded = expand_spatial_scene(result, scene_id)
    # Missing bindings/assets are saveable drafts. Reusing an incompatible
    # edited component must never silently detach its customized children.
    conflicts = [entry for entry in expanded['diagnostics'] if entry['code'] == 'customization_conflict']
    if conflicts:
        raise SpatialModelError(f'layout: incompatible captured components: {conflicts}')
    return result, {
        'layout_id': identity, 'affected': [scene_id], 'scene': deepcopy(scenes[scene_id]),
        'input_targets': {name: {'occurrence_path': [scene_id, nodes[entry['target']['node']]['instance_id']],
                                 'input': entry['target']['input']}
                          for name, entry in declarations.items()},
        'captured_definitions': sorted(captured), 'reused_definitions': sorted(reused),
        'replaces_layout': replaces, 'diagnostics': expanded['diagnostics'], 'writes_spec': False,
        'state_id_before': current, 'state_id_after': spatial_state_id(result),
    }
