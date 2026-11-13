"""Read-only native playback adapter for static preset scene geometry.

The export planner owns regions, source clocks and shapes. This adapter gives
those resolved regions DOM boxes; it never saves a converted spatial model.
"""
from copy import deepcopy


def prepare_preset(spec, project, timeline, span, start, end, fail):
    from moviestar.cli import _preset_scene_render_plan_at

    scene = spec['composition'][span.scene_index]
    # Camera/pacing/moving-preset support needs its own fidelity acceptance.
    if any((m.get('scene_id') == scene.get('id') or m.get('scene') == scene['name'])
           and any(s.get('camera') or s.get('pacing') for s in m.get('slots', []))
           for m in spec.get('motion', {}).get('scenes', [])):
        fail('preset camera/pacing motion is not implemented')
    plan = _preset_scene_render_plan_at(project=project, spec=spec, composition=spec['composition'],
        at_s=start, command='preview', scene_index=span.scene_index, prepare_assets=False)
    nodes = []
    for slot in plan['render_slots']:
        if slot.get('motion') or slot.get('camera_crop') or slot['held'] or abs(slot['speed']-1) > 1e-8:
            fail('preset media requires static geometry and an ordinary unit-speed source clock')
        region, shape = slot['region'], slot.get('shape') or {}
        border = shape.get('border') or {}
        pad = border.get('width', 0)
        kind = shape.get('kind', 'rect')
        radius = round(shape.get('radius', 0)*min(region['width'], region['height']))
        outer_radius = '50%' if kind == 'circle' else radius+pad if kind == 'rounded' else 0
        inner_radius = '50%' if kind == 'circle' else radius
        framing = slot['framing']
        anchor = framing.get('anchor', 'center')
        x = framing.get('x', {'left': 0, 'right': 1}.get(anchor, .5))
        y = framing.get('y', {'top': 0, 'bottom': 1}.get(anchor, .5))
        # Fit pads to the center in the export compositor, regardless of anchor.
        if framing['mode'] == 'fit': x = y = .5
        media = {'id': 'video', 'type': 'video', 'content': {'asset_id': slot['source']},
            'layout': {'position': 'absolute', 'left': pad, 'top': pad,
                'width': region['width'], 'height': region['height']},
            'style': {'object_fit': 'contain' if framing['mode'] == 'fit' else 'cover',
                'border_radius': inner_radius, 'background_color': 'black'},
            'native_object_position': f'{x*100:g}% {y*100:g}%',
            'media': {'asset_id': slot['source'], 'source_time_s': slot['source_from'], 'visible': True}}
        nodes.append({'id': slot['slot'], 'type': 'shape',
            'layout': {'position': 'absolute', 'left': region['x']-pad, 'top': region['y']-pad,
                'width': region['width']+2*pad, 'height': region['height']+2*pad},
            'style': {'border_radius': outer_radius, 'background_color': border.get('color', 'black')},
            'children': [media]})
    identity = span.scene_id
    def address(children, path):
        for node in children:
            node['occurrence_path'] = [*path, node['id']]
            address(node.get('children', []), node['occurrence_path'])
    address(nodes, [identity, 'slots'])
    frame = {'scene_id': identity, 'background_color': 'black', 'children': nodes, 'diagnostics': []}
    def sample(at):
        result = deepcopy(frame)
        for node in result['children']:
            node['children'][0]['media']['source_time_s'] += at-start
        return result
    return sample
