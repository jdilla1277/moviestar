"""Read-only preset participants in the shared transition inspection path.

Preset pixels still use the native FFmpeg compositor. Slot bounds come from
that compositor's plan; overlay bounds retain its explicit estimate label.
This adapter never converts or saves a preset as a spatial component tree.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory


def _canvas_spec(spec, canvas):
    """Adapt two-up role names in a temporary plan; keep stored identities."""
    from moviestar.spec import layout_regions
    from math import gcd
    aliases = {'left': 'top', 'right': 'bottom', 'top': 'left', 'bottom': 'right'}
    changed = deepcopy(spec)
    changed['composition_canvas'] = {**spec['composition_canvas'], **canvas}
    if any(canvas[key] != spec['composition_canvas'][key] for key in ('width', 'height')):
        divisor = gcd(int(canvas['width']), int(canvas['height']))
        changed['composition_canvas'].update({'preset': None,
            'aspect_ratio': f"{canvas['width']//divisor}:{canvas['height']//divisor}"})
    identities = {}
    for index, scene in enumerate(changed['composition']):
        if scene['layout']['preset'] != 'two-up':
            continue
        regions = layout_regions(scene['layout'], canvas, result_time_s=0)
        mapping = {slot['slot']: aliases[slot['slot']] for slot in scene['slots'] if slot['slot'] not in regions}
        for slot in scene['slots']:
            original = slot['slot']
            slot['slot'] = mapping.get(original, original)
            identities[index, slot['slot']] = original
        for motion in (changed.get('motion') or {}).get('scenes', []):
            if (motion.get('scene_id') and motion['scene_id'] == scene.get('id')) or motion.get('scene') == scene['name']:
                for slot in motion.get('slots', []):
                    slot['slot'] = mapping.get(slot.get('slot'), slot.get('slot'))
    return changed, identities


def compile_preset_scene_frame(spec, timeline, index, local, *, source_durations, project=None, canvas=None):
    from moviestar import cli as c
    from moviestar.spatial_model import SpatialModelError
    from moviestar.spec import parse_timecode_string

    size = canvas or timeline.canvas
    if project is None:
        if any(slot.get('camera') for scene in (spec.get('motion') or {}).get('scenes', [])
               for slot in scene.get('slots', [])):
            raise SpatialModelError('preset camera inspection requires project source dimensions; pass project metadata')
        project = {'sources': [{**source, 'duration': {'seconds': source_durations.get(source['id'], 0)},
            'fps': timeline.output_fps, 'width': size['width'], 'height': size['height']}
            for source in spec['sources']]}
    spans = [span for span in timeline.spans if span.scene_index == index]
    start, end = spans[0].scene_start_s, spans[-1].scene_end_s
    sample = round(start+local, 6)
    planned_spec, identities = _canvas_spec(spec, size)
    plan = c._preset_scene_render_plan_at(project=project, spec=planned_spec, composition=planned_spec['composition'],
        at_s=sample, command='composition model frame', scene_index=index, canvas=size, prepare_assets=False)
    for slot in plan['render_slots']:
        slot['inspection_slot'] = identities.get((index, slot['slot']), slot['slot'])
    scene = spec['composition'][index]
    identity = scene.get('id') or f'preset:{index}'
    diagnostics = []
    for slot in plan['render_slots']:
        available = source_durations.get(slot['source'])
        if slot['source_from'] < 0 or available is None or slot['source_from'] >= available:
            diagnostics.append({'code': 'insufficient_preset_transition_handle', 'severity': 'error',
                'path': [identity, 'slots', slot['slot']], 'source_time_s': slot['source_from']})
    context = c._overlay_render_context(project, spec, plan['canvas_tuple'], sample, sample+.001,
                                         'composition model frame')
    if context:
        context['plans'] = [overlay for overlay in context['plans']
            if overlay['from_result'] <= sample < overlay['to_result']]
        # Freeze the selected word at the requested moment. ASS has a 10ms
        # event grid; a tiny point window must not erase an active caption.
        for overlay in context['plans']:
            overlay['enable_from'], overlay['enable_to'] = 0, .01
            if overlay.get('highlight'):
                overlay['highlight']['tokens'] = [{**token, 'from_local': 0, 'to_local': .01}
                    for token in overlay['highlight']['tokens'] if token['from_local'] <= 0 < token['to_local']]
        context['highlight_ass'] = c._highlight_ass_assets(context, plan['canvas_tuple'])
    diagnostics.extend({'code': 'preset_overlay_estimated_bounds', 'severity': 'warning',
        'path': [identity, 'overlays', overlay['id']],
        'reason': 'preset overlay bounds are estimates from the native renderer, not browser glyph measurements'}
        for overlay in (context or {}).get('plans', []))
    source_start = parse_timecode_string(scene['slots'][0]['source_from'], 'slot.source_from')
    source_end = parse_timecode_string(scene['slots'][0]['source_to'], 'slot.source_to')
    return {'kind': 'preset', 'scene_id': identity, 'children': [], 'diagnostics': diagnostics,
        'scene_time_s': round(max(0, min(end-start, local)), 6), 'scene_duration_s': round(end-start, 6),
        'scene_source_time_s': round(plan['render_slots'][0]['source_from']-source_start, 6),
        'scene_source_duration_s': round(source_end-source_start, 6),
        'pacing_segment_id': plan['scene_window']['pacing_segment_id'],
        'result_time_s': sample, 'sample_time_s': sample, 'canvas': deepcopy(size),
        'preset_plan': plan, 'preset_overlays': context}


def prepare_preset_frame(frame, canvas):
    from moviestar.spatial_layout import _canvas, SpatialLayoutError

    size = _canvas(canvas)
    if any(size[key] != frame['canvas'][key] for key in ('width', 'height')):
        raise SpatialLayoutError('preset frame canvas differs from its plan; compile the frame with canvas= first')
    return {'canvas': size, 'scene_id': frame['scene_id'], 'nodes': [],
        'diagnostics': list(frame['diagnostics']),
        'scene_layout': {'kind': 'preset', 'layout': frame['preset_plan']['layout'],
            'geometry_source': 'preset_renderer', 'overlay_bounds': 'estimated'}}


def measure_preset_layout(frame, canvas, *, output_path=None, browser_session=None):
    prepared = prepare_preset_frame(frame, canvas)
    boxes, media = [], []
    plan = frame['preset_plan']
    for slot in plan['render_slots']:
        path = [frame['scene_id'], 'slots', slot.get('inspection_slot', slot['slot'])]
        rect = deepcopy(slot['region'])
        boxes.append({'path': path, 'kind': 'video', 'dom_order': len(boxes), 'rect': rect,
            'base_rect': deepcopy(rect), 'geometry_source': 'preset_renderer',
            'framing': slot['framing'], 'camera': plan['camera_states'].get(slot['slot']),
            'shape': slot.get('shape')})
        media.append({'path': path, 'rect': rect, 'asset_id': slot['source'],
            'source_time_s': slot['source_from'], 'held': slot['held'], 'speed': slot['speed']})
    for overlay in (frame['preset_overlays'] or {}).get('plans', []):
        rect = {key: overlay['estimated_bounds'][key] for key in ('x','y','width','height')}
        boxes.append({'path': [frame['scene_id'], 'overlays', overlay['id']],
            'kind': 'caption' if overlay['kind'] == 'caption' else 'text',
            'dom_order': len(boxes), 'rect': rect, 'base_rect': deepcopy(rect),
            'geometry_source': 'preset_overlay_estimate', 'geometry_is': 'estimated',
            'text': overlay['text'], 'track': overlay['track']})
    report = {'view': 'layout', 'renderer': 'preset_ffmpeg',
        'canvas': prepared['canvas'], 'scene_layout': prepared['scene_layout'],
        'boxes': boxes, 'media': media, 'reserved_regions': [], 'diagnostics': prepared['diagnostics'],
        'artifact': str(output_path.resolve()) if output_path else None}
    if output_path:
        from contextlib import nullcontext
        from moviestar.spatial_session import BrowserSession
        from moviestar.spatial_transition import _draw_transition_guides
        from moviestar.spatial_render import _publish
        with TemporaryDirectory(prefix='moviestar-preset-layout-') as directory:
            staged = Path(directory) / 'layout.png'
            with (nullcontext(browser_session) if browser_session is not None else BrowserSession()) as browser:
                _draw_transition_guides({'scene_id': frame['scene_id'], 'transitions': [],
                    'layers': [{'role': 'preset', 'weight': 1}]}, prepared['canvas'], [report], staged, browser)
            _publish(staged, output_path.resolve())
    return report


def render_preset_frame(spec, frame, canvas, *, output_path, project_root=None,
                        layout_output_path=None, reference_path=None, browser_session=None):
    from moviestar import cli as c
    from moviestar.spatial_render import _run, _publish, _validate_frame_destinations, _asset_path

    root = (project_root or Path.cwd()).resolve()
    _validate_frame_destinations(spec, root, output_path, layout_output_path, reference_path)
    report = measure_preset_layout(frame, canvas)
    slots = deepcopy(frame['preset_plan']['render_slots'])
    for slot in slots:
        slot['path'] = str(_asset_path(slot['path'], root))
        if not Path(slot['path']).is_file():
            raise ValueError(f"preset asset {slot['source']!r} is unavailable: {slot['path']}")
        if not slot['held'] and slot['source_from'] > 0:
            prior = c.probe_last_video_packet_timestamp(slot['path'], slot['source_from'])
            if prior is not None:
                slot['video_seek_from'] = max(0, min(prior, slot['source_from']))
    with TemporaryDirectory(prefix='moviestar-preset-frame-') as directory:
        temporary = Path(directory)
        staged = temporary / 'frame.png'
        for slot in slots:
            if slot.get('shape'):
                slot['shape'] = c._render_slot_shape(slot['shape'], slot['region'], masks_dir=temporary / 'masks')
        context = deepcopy(frame['preset_overlays'])
        if context and context.get('highlight_ass'):
            # A still must not leave generated ASS assets in the project.
            context['highlight_ass']['path'] = str(temporary / 'highlight.ass')
            c._write_highlight_ass(context)
        command = c.build_render_layout_frame_command(slots, str(staged),
            frame['preset_plan']['canvas_tuple'], overlay_plans=context['plans'] if context else None,
            highlight_ass=c._highlight_ass_arg(context))
        _run(command)
        if layout_output_path:
            measure_preset_layout(frame, canvas, output_path=temporary / 'layout.png', browser_session=browser_session)
        for destination in (output_path, reference_path):
            if destination:
                _publish(staged, Path(destination).resolve())
        if layout_output_path:
            _publish(temporary / 'layout.png', Path(layout_output_path).resolve())
    return {**report, 'view': 'rendered', 'renderer': 'preset_ffmpeg',
        'artifact': str(Path(output_path).resolve()),
        'layout_artifact': str(Path(layout_output_path).resolve()) if layout_output_path else None}
