"""Compile a bounded native-playback feasibility packet without media decoding."""

from hashlib import sha256
import json
from pathlib import Path

from moviestar.resolved import resolve_project
from moviestar.spatial_layout import prepare_layout_frame
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spec import parse_timecode_string


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get('children', []))


def fail(reason):
    raise ValueError('live feasibility: ' + reason)


def asset_identity(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def compile_snapshot(snapshot):
    spec, project = snapshot['spec'], snapshot['project']
    if any(not recipe.get('frozen') for recipe in spec.get('captions', [])):
        fail('live caption recipes are not implemented; use manual/frozen cues')
    if any(source.get('operations') for source in spec['sources']):
        fail('source operations are not implemented in this probe')
    root = Path(snapshot['root'])
    timeline = resolve_project(project, spec)
    start, end = snapshot['range']['from'], snapshot['range']['to']
    fps = timeline.output_fps
    if len(spec['composition']) != 1 or len(timeline.spans) != 1:
        fail('this probe supports one spatial scene without cuts, pacing or transitions')
    if any(spec.get(key) for key in ['opening_transition', 'closing_transition']):
        fail('project-edge transitions are not implemented')
    if not 0 < end-start <= 10 or not 1 <= fps <= 60:
        fail('use an at-most-ten-second window at 1–60 fps')
    if any(abs(value*fps-round(value*fps)) > 1e-6 for value in [start, end]):
        fail('the first probe requires frame-aligned review boundaries')
    expected_mix = {'source_audio': {'muted': False, 'gain_db': 0.0, 'fade_in': 0.0, 'fade_out': 0.0}, 'tracks': []}
    if spec['audio_mix'] != expected_mix:
        fail('audio mixing, gain and fades are not implemented')
    scene = spec['composition'][0]
    metadata = {source['id']: source for source in project['sources']}
    durations = {key: source['duration']['seconds'] for key, source in metadata.items()}
    source_paths = {source['id']: (root / source['path']).resolve() for source in spec['sources']}
    image_paths = {key: (root / value['path']).resolve()
                   for key, value in spec.get('spatial', {}).get('assets', {}).items()}
    assets, media, samples = {}, [], []
    initial = shape = None
    source_start = video_path = video_asset = None
    for index in range(round((end-start)*fps)):
        at = start+index/fps
        frame = compile_spatial_frame(spec, timeline, at, source_durations=durations)
        if frame.get('kind') in {'transition', 'preset'}:
            fail('this probe requires a spatial scene without transitions')
        prepared = prepare_layout_frame(frame, timeline.canvas)
        if any(item.get('severity') != 'warning' for item in prepared['diagnostics']):
            fail('the canonical frame has unresolved diagnostics')
        expanded = {tuple(node['occurrence_path']): node for node in walk(frame['children'])}
        videos = [node for node in expanded.values() if node.get('type') == 'video']
        if len(videos) != 1 or not (videos[0].get('media') or {}).get('visible'):
            fail('exactly one video must be visible throughout this review window')
        video = videos[0]
        if index == 0:
            video_path = video['occurrence_path']
            video_asset = video['media']['asset_id']
            source_start = video['media']['source_time_s']
            native_fps = metadata[video_asset].get('fps')
            if not isinstance(native_fps, (int, float)) or abs(native_fps-fps) > 1e-6:
                fail('the indexed source rate must match the preview rate')
            if source_paths[video_asset].suffix.lower() != '.mp4' or not metadata[video_asset].get('has_audio'):
                fail('use a native-compatible MP4 with embedded audio for this probe')
            audio = scene.get('audio_from', spec.get('composition_audio_from'))
            if audio != video_asset or len(scene['slots']) != 1 or scene['slots'][0]['source'] != video_asset:
                fail('audio must follow the single visible video and its carrier slot')
            slot = timeline.spans[0].slot(scene['slots'][0]['slot'])
            if slot.held or abs(slot.speed-1) > 1e-8 or abs(slot.source_from_s+start-source_start) > 1e-6:
                fail('audio and video must share an ordinary unit-speed source clock')
        if abs(video['media']['source_time_s']-(source_start+index/fps)) > 1e-5:
            fail('holds and source-speed changes are not implemented')
        current_shape = [(node['path'], node['kind'], node['style']) for node in walk(prepared['nodes'])]
        if shape is not None and current_shape != shape:
            fail('topology/style changes inside a window are not implemented')
        if index == 0:
            shape, initial = current_shape, prepared
            for node in walk(prepared['nodes']):
                kind = node['kind']
                if kind not in {'component', 'shape', 'text', 'caption', 'video', 'image'}:
                    fail(f'unsupported element {kind}')
                if kind in {'video', 'image', 'text', 'caption'} and node['children']:
                    fail('media/text children are not implemented in this probe')
                if kind not in {'video', 'image'}:
                    continue
                style = node['style']
                if style.get('object-fit', 'fill') not in {'fill', 'contain', 'cover', 'none', 'scale-down'}:
                    fail('use an explicit object-fit value for native media')
                if style.get('position', 'relative') not in {'relative', 'absolute'}:
                    fail('native media requires a relative/absolute containing block')
                edge_keys = [key for key in style if key == 'padding' or key.startswith('padding-')
                             or (key.startswith('border') and key.endswith('width'))]
                if any(any(value not in {'0px', '0'} for value in style[key].split()) for key in edge_keys):
                    fail('media padding and borders are not implemented')
                original = expanded[tuple(node['path'])]
                asset = original.get('content', {}).get('asset_id')
                path = (source_paths if kind == 'video' else image_paths).get(asset)
                if path is None or not path.is_file():
                    fail('unavailable media asset')
                identity = asset_identity(path)
                key = sha256(json.dumps([str(path), identity]).encode()).hexdigest()[:24]
                assets[key] = {'path': path, 'identity': identity, 'kind': kind}
                media.append({'path': node['path'], 'kind': kind, 'asset': key,
                              'object_fit': style.get('object-fit', 'fill')})
        def dynamic(nodes):
            return [{'path': node['path'], 'content': node['content'],
                     'motion': node.get('motion'), 'caption': node.get('caption'),
                     'children': dynamic(node['children'])} for node in nodes]
        samples.append({'nodes': dynamic(prepared['nodes'])})
    return {'state_id': snapshot['state_id'], 'canvas': initial['canvas'], 'fps': fps,
            'range': snapshot['range'], 'source_start': source_start, 'source_end': source_start+end-start,
            'video_path': video_path, 'initial': initial, 'media': media, 'samples': samples}, assets
