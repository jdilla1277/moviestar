"""One result-time description for spatial transition stills.

Scene layers retain their own layout, media, motion, caption, and reservation
rules. Blending adds no clock and does not change the saved timeline.
"""
from __future__ import annotations

from copy import deepcopy

from moviestar.transitions import resolve_edge_transition, resolve_internal_transition, transition_weights


def active_transitions(spec, timeline, at_s):
    """Select authored windows at the actual output sample, not display time."""
    result = []
    starts = {span.scene_index: span.scene_start_s for span in timeline.spans}
    for index, scene in enumerate(spec['composition']):
        transition = scene.get('transition_in')
        if not index or not transition or index not in starts:
            continue
        cut = starts[index]
        half = transition['duration']/2
        if round(cut-half, 6) <= at_s < round(cut+half, 6):
            result.append({'scope': 'internal', 'incoming_index': index, 'type': transition['type'],
                'duration_s': transition['duration'], 'cut_s': cut,
                'from_s': round(cut-half, 6), 'to_s': round(cut+half, 6)})
    for field, scope in [('opening_transition', 'opening'), ('closing_transition', 'closing')]:
        transition = spec.get(field)
        if not transition:
            continue
        left = 0 if scope == 'opening' else round(timeline.duration_s-transition['duration'], 6)
        right = round(left+transition['duration'], 6)
        if left <= at_s < right:
            result.append({'scope': scope, 'type': transition['type'], 'duration_s': transition['duration'],
                           'from_s': left, 'to_s': right})
    return result


def _fail(message):
    from moviestar.spatial_model import SpatialModelError
    raise SpatialModelError(message)


def compile_transition_frame(spec, timeline, result_time_s, sample_time_s, source_durations):
    from moviestar.spatial_model import compile_spatial_scene_frame

    transitions = active_transitions(spec, timeline, sample_time_s)
    if not transitions:
        return None
    internal = [t for t in transitions if t['scope'] == 'internal']
    if len(internal) > 1:
        _fail('spatial transition: overlapping internal transitions require watch or export')
    owner = timeline.span_at(sample_time_s)
    indices = ([internal[0]['incoming_index']-1, internal[0]['incoming_index']]
               if internal else [owner.scene_index])
    scenes = (spec.get('spatial') or {}).get('scenes', {})
    for index in indices:
        identity = spec['composition'][index].get('id')
        if identity not in scenes:
            _fail(f'spatial transition: preset scene {identity!r} is unsupported in a still; use watch or export')
    weights = [1.0] * len(indices)
    colors = {'black': 0.0, 'white': 0.0}
    if internal:
        transition = internal[0]
        resolution = resolve_internal_transition(timeline, source_durations,
            incoming_scene_index=transition['incoming_index'], transition_type=transition['type'],
            duration_s=transition['duration_s'])
        if not resolution.sufficient:
            _fail(f"composition[{transition['incoming_index']}].transition_in: insufficient transition handles or scene duration")
        progress = (sample_time_s-transition['from_s']) / transition['duration_s']
        outgoing, incoming, dip = transition_weights(transition['type'], max(0, min(1, progress)))
        weights = [outgoing, incoming]
        if transition['type'] != 'dissolve':
            colors['white' if transition['type'] == 'dip-white' else 'black'] = dip
    for transition in transitions:
        if transition['scope'] == 'internal':
            continue
        resolution = resolve_edge_transition(timeline, edge=transition['scope'], duration_s=transition['duration_s'])
        if not resolution.sufficient:
            _fail(f"{transition['scope']}_transition: duration exceeds project limit")
        progress = (sample_time_s-transition['from_s']) / transition['duration_s']
        keep = progress if transition['scope'] == 'opening' else 1-progress
        weights = [w*keep for w in weights]
        colors = {key: value*keep for key, value in colors.items()}
        colors['white' if transition['type'] == 'dip-white' else 'black'] += 1-keep
    layers = []
    for index, weight in zip(indices, weights):
        spans = [span for span in timeline.spans if span.scene_index == index]
        start, end = spans[0].scene_start_s, spans[-1].scene_end_s
        local = sample_time_s-start
        # Dips have no source handles. Their invisible layer is described at
        # its owning edge, without inventing footage beyond the authored range.
        if internal and internal[0]['type'] != 'dissolve':
            local = max(0, min(local, end-start-.000001))
        frame = compile_spatial_scene_frame(spec, timeline, index, local, source_durations=source_durations)
        layers.append({'scene_id': frame['scene_id'], 'scene_index': index,
            'role': ('outgoing' if index == indices[0] else 'incoming') if internal else 'scene',
            'weight': round(max(0, min(1, weight)), 12), 'frame': frame})
    anchor = next(layer['frame'] for layer in layers if layer['scene_index'] == owner.scene_index)
    metadata = {key: deepcopy(anchor[key]) for key in ('scene_id', 'scene_time_s', 'scene_duration_s',
        'scene_source_time_s', 'scene_source_duration_s', 'pacing_segment_id')}
    return {**metadata, 'kind': 'transition', 'result_time_s': round(result_time_s, 6),
        'sample_time_s': round(sample_time_s, 6), 'canvas': deepcopy(timeline.canvas),
        'transitions': [{**t, 'progress': round((sample_time_s-t['from_s'])/t['duration_s'], 12)} for t in transitions], 'layers': layers,
        'color_weights': {key: round(max(0, min(1, value)), 12) for key, value in colors.items()},
        'diagnostics': [d for layer in layers for d in layer['frame']['diagnostics']]}


def scene_frames(frame):
    return [layer['frame'] for layer in frame['layers']] if frame.get('kind') == 'transition' else [frame]


def prepare_transition_frame(frame, canvas):
    from moviestar.spatial_layout import prepare_layout_frame, _canvas
    size = _canvas(canvas)
    prepared = [prepare_layout_frame(layer['frame'], size) for layer in frame['layers']]
    diagnostics = list(frame['diagnostics'])
    for item in prepared:
        diagnostics.extend(d for d in item['diagnostics'] if d not in diagnostics)
    return {'canvas': size, 'scene_id': frame['scene_id'], 'nodes': [], 'diagnostics': diagnostics,
        'scene_layout': {'kind': 'transition', 'layers': [
            {'scene_id': layer['scene_id'], 'weight': layer['weight'], 'layout': item['scene_layout']}
            for layer, item in zip(frame['layers'], prepared)]}}


def _blend_layers(frame, paths, output):
    from moviestar.spatial_render import _ffmpeg
    weights = [layer['weight'] for layer in frame['layers']]
    white = frame['color_weights']['white']
    inputs = [part for path in paths for part in ('-i', str(path))]
    if len(paths) == 1:
        expression = f'val*{weights[0]:.12f}+65535*{white:.12f}'
        graph = f"[0:v]format=gbrp16le,lutrgb=r='{expression}':g='{expression}':b='{expression}',format=rgb24[out]"
    else:
        expression = f'A*{weights[0]:.12f}+B*{weights[1]:.12f}+65535*{white:.12f}'
        graph = (f"[0:v]format=gbrp16le[a];[1:v]format=gbrp16le[b];"
                 f"[a][b]blend=all_expr='{expression}',format=rgb24[out]")
    _ffmpeg([*inputs, '-filter_complex', graph, '-map', '[out]', '-frames:v', '1',
             '-threads', '1', str(output)])


def _report(frame, canvas, reports, *, view, artifact=None, layout_artifact=None):
    diagnostics = list(frame['diagnostics'])
    boxes, media, regions = [], [], []
    layers = []
    for index, (layer, report) in enumerate(zip(frame['layers'], reports)):
        tags = {'scene_id': layer['scene_id'], 'scene_role': layer['role'],
                'transition_weight': layer['weight'], 'layer_index': index}
        boxes.extend({**box, **tags} for box in report['boxes'])
        media.extend({**item, **tags} for item in report.get('media', []))
        regions.extend({**item, **tags} for item in report.get('reserved_regions', []))
        diagnostics.extend(d for d in report['diagnostics'] if d not in diagnostics)
        # Layer artifact paths are temporary and must not escape in JSON.
        layers.append({**{key: value for key, value in layer.items() if key != 'frame'},
                       'scene_time_s': layer['frame']['scene_time_s'],
                       'scene_source_time_s': layer['frame']['scene_source_time_s'],
                       'scene_layout': report['scene_layout']})
    return {'view': view, 'kind': 'transition', 'canvas': canvas, 'layers': layers,
        'result_time_s': frame['result_time_s'], 'sample_time_s': frame['sample_time_s'],
        'transitions': frame['transitions'], 'color_weights': frame['color_weights'],
        'scene_layout': {'kind': 'transition', 'layers': layers},
        'boxes': boxes, 'media': media, 'reserved_regions': regions,
        'diagnostics': diagnostics, 'artifact': str(artifact) if artifact else None,
        'layout_artifact': str(layout_artifact) if layout_artifact else None}


def _draw_transition_guides(frame, canvas, reports, output, browser):
    nodes = []
    for index, (layer, report) in enumerate(zip(frame['layers'], reports)):
        color = '#00d4ff' if index == 0 else '#ff80df'
        reserved = {tuple(region['path']): region['id'] for region in report.get('reserved_regions', [])}
        for box in report['boxes']:
            region = reserved.get(tuple(box['path']))
            nodes.append({'path': box['path'], 'kind': box['kind'], 'content': '', 'children': [],
                'style': {'position': 'absolute', 'left': f"{box['rect']['x']}px",
                    'top': f"{box['rect']['y']}px", 'width': f"{box['rect']['width']}px",
                    'height': f"{box['rect']['height']}px"},
                'guide_outline': f"{2 if region else 1}px {'dashed' if region else 'solid'} {color}",
                'guide_label': f"({layer['role']} {layer['weight']:.0%})" + (f" reserved: {region}" if region else '')})
    summary = ', '.join(f"{t['scope']} {t['type']}" for t in frame['transitions'])
    browser.run({'canvas': canvas, 'scene_id': frame['scene_id'], 'root_style': {'background-color': '#102030'},
        'nodes': nodes, 'scene_layout': {}, 'diagnostics': [], 'output_path': str(output),
        'transition_summary': summary})


def measure_transition_layout(frame, canvas, *, output_path=None, browser_session=None):
    from contextlib import nullcontext
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from moviestar.spatial_layout import measure_spatial_layout, _canvas, SpatialLayoutError
    from moviestar.spatial_render import _publish
    from moviestar.spatial_session import BrowserSession

    size = _canvas(canvas)
    output = Path(output_path).resolve() if output_path else None
    if output and (output.suffix.lower() != '.png' or not output.parent.is_dir()):
        raise SpatialLayoutError('transition layout output must be a PNG in an existing directory')
    context = nullcontext(browser_session) if browser_session is not None else BrowserSession()
    with TemporaryDirectory(prefix='moviestar-transition-layout-') as directory:
        temporary = Path(directory)
        staged = temporary / 'layout.png'
        with context as browser:
            reports = [measure_spatial_layout(layer['frame'], size, browser_session=browser)
                       for layer in frame['layers']]
            if output:
                _draw_transition_guides(frame, size, reports, staged, browser)
        if output:
            _publish(staged, output)
    return _report(frame, size, reports, view='layout', artifact=output)


def render_transition_frame(spec, frame, canvas, *, output_path, project_root=None,
                            layout_output_path=None, reference_path=None, browser_session=None):
    from contextlib import nullcontext
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from moviestar.spatial_layout import _canvas
    from moviestar.spatial_render import render_spatial_frame, _publish, _validate_frame_destinations
    from moviestar.spatial_session import BrowserSession

    root = (project_root or Path.cwd()).resolve()
    _validate_frame_destinations(spec, root, output_path, layout_output_path, reference_path)
    size = _canvas(canvas)
    context = nullcontext(browser_session) if browser_session is not None else BrowserSession()
    with TemporaryDirectory(prefix='moviestar-transition-') as directory:
        temporary = Path(directory)
        paths = [temporary / f'layer-{index}.png' for index in range(len(frame['layers']))]
        references = [temporary / f'reference-{index}.png' for index in range(len(paths))]
        with context as browser:
            reports = [render_spatial_frame(spec, layer['frame'], size, output_path=path,
                project_root=root, reference_path=reference if reference_path else None, browser_session=browser)
                for layer, path, reference in zip(frame['layers'], paths, references)]
            if layout_output_path:
                _draw_transition_guides(frame, size, reports, temporary / 'layout.png', browser)
        outputs = [(paths, Path(output_path))]
        if reference_path:
            outputs.append((references, Path(reference_path)))
        staged = []
        for index, (sources, destination) in enumerate(outputs):
            image = temporary / f'combined-{index}.png'
            _blend_layers(frame, sources, image)
            staged.append((image, destination))
        if layout_output_path:
            staged.append((temporary / 'layout.png', Path(layout_output_path)))
        for image, destination in staged:
            _publish(image, destination.resolve())
    return _report(frame, size, reports, view='rendered', artifact=Path(output_path).resolve(),
                   layout_artifact=Path(layout_output_path).resolve() if layout_output_path else None)
