"""Compile bounded native review descriptors; no media decoding or output encode."""

from hashlib import sha256
import json
from math import nextafter
from pathlib import Path

from moviestar.resolved import resolve_project
from moviestar.spatial_layout import prepare_layout_frame
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_transition import active_transitions


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get('children', []))


PROFILE = {"name": "native", "transport": "native-browser", "max_window_s": 10,
           "clip_end_tolerance_ms": 50, "clip_end": "approximate",
           "min_fps": 24, "max_fps": 60, "max_segments": 3, "max_videos_per_segment": 2,
           "fallback_mode": "rendered"}


class NativePreviewUnsupported(ValueError):
    """The native profile cannot faithfully review this saved state."""


def fail(reason):
    raise NativePreviewUnsupported('Native preview unavailable: ' + reason
        + '. Stop this session and retry preview --mode rendered with the same range.')


def asset_identity(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def compile_snapshot(snapshot):
    spec, project = snapshot['spec'], snapshot['project']
    root = Path(snapshot['root'])
    timeline = resolve_project(project, spec)
    start, end = snapshot['range']['from'], snapshot['range']['to']
    fps = timeline.output_fps
    if not 0 < end-start <= 10 or not 24 <= fps <= 60:
        fail('use an at-most-ten-second window at 24–60 fps')
    if not 0 <= start < end <= timeline.duration_s:
        fail('the review window must stay inside the result timeline')
    if any(abs(value*fps-round(value*fps)) > 1e-6 for value in [start, end]):
        fail('native review requires frame-aligned review boundaries')
    spans = timeline.spans_overlapping(start, end)
    if not 1 <= len(spans) <= PROFILE['max_segments']:
        fail('native review supports at most three spatial spans')
    if len({span.scene_index for span in spans}) != len(spans):
        fail('pacing boundaries inside a scene are not implemented')
    packets, assets = [], {}
    for span in spans:
        left, right = max(start, span.start_s), min(end, span.end_s)
        if any(abs(value*fps-round(value*fps)) > 1e-6 for value in (left, right)):
            fail('native cuts require frame-aligned scene boundaries')
        child, registered = _compile_span(snapshot, timeline, left, right, span)
        packets.append({'offset': left-start, 'packet': child})
        assets.update(registered)
    if len(packets) == 1:
        return packets[0]['packet'], assets
    return {'state_id': snapshot['state_id'], 'canvas': packets[0]['packet']['canvas'],
            'fps': fps, 'range': snapshot['range'], 'segments': packets,
            'media': [media for segment in packets for media in segment['packet']['media']]}, assets


def _compile_span(snapshot, timeline, start, end, span):
    spec, project, root = snapshot['spec'], snapshot['project'], Path(snapshot['root'])
    fps = timeline.output_fps
    scene = spec['composition'][span.scene_index]
    # Inside a single span, scene/edge transition windows can intersect the
    # review only at its ends. Check the half-open interval, including the
    # unsampled tail after the last frame; unrelated transitions are harmless.
    if active_transitions(spec, timeline, start) or active_transitions(spec, timeline, nextafter(end, start)):
        fail('the review window must not overlap transitions')
    expected_mix = {'source_audio': {'muted': False, 'gain_db': 0.0, 'fade_in': 0.0, 'fade_out': 0.0}, 'tracks': []}
    if spec['audio_mix'] != expected_mix:
        fail('audio mixing, gain and fades are not implemented')
    preset = None
    if span.scene_id not in (spec.get('spatial') or {}).get('scenes', {}):
        if not scene['slots']:
            fail('native preset review requires video slots')
        from moviestar.native_preset import prepare_preset
        preset = prepare_preset(spec, project, timeline, span, start, end, fail)
    metadata = {source['id']: source for source in project['sources']}
    durations = {key: source['duration']['seconds'] for key, source in metadata.items()}
    source_paths = {source['id']: (root / source['path']).resolve() for source in spec['sources']}
    image_paths = {key: (root / value['path']).resolve()
                   for key, value in spec.get('spatial', {}).get('assets', {}).items()}
    assets, media, samples = {}, [], []
    initial = shape = None
    source_start = video_path = None
    clocks = {}
    for index in range(round((end-start)*fps)):
        at = start+index/fps
        frame = preset(at) if preset else compile_spatial_frame(spec, timeline, at, source_durations=durations)
        if frame.get('kind') in {'transition', 'preset'}:
            fail('this native profile requires a spatial scene without transitions')
        prepared = prepare_layout_frame(frame, timeline.canvas)
        if any(item.get('severity') != 'warning' for item in prepared['diagnostics']):
            fail('the canonical frame has unresolved diagnostics')
        expanded = {tuple(node['occurrence_path']): node for node in walk(frame['children'])}
        if index == 0:
            placed_tracks = {node['caption']['track'] for node in expanded.values()
                             if node.get('type') == 'caption'}
            if any(recipe['track'] in placed_tracks and not recipe.get('frozen')
                   for recipe in spec.get('captions', [])):
                fail('live caption recipes are not implemented; use manual/frozen cues')
        videos = [node for node in expanded.values() if node.get('type') == 'video']
        if not 1 <= len(videos) <= PROFILE['max_videos_per_segment']:
            fail('one or two videos must be visible throughout this review window')
        if any(not (v.get('media') or {}).get('visible') for v in videos):
            fail('every video must remain visible with an ordinary unit-speed source clock')
        if index == 0:
            for video in videos:
                asset = video['media']['asset_id']
                if any(s.get('operations') for s in spec['sources'] if s['id'] == asset):
                    fail('source operations are not implemented in this native profile')
                native_fps = metadata[asset].get('fps')
                if not isinstance(native_fps, (int, float)) or abs(native_fps-fps) > 1e-6:
                    fail('every indexed source rate must match the preview rate')
                if source_paths[asset].suffix.lower() != '.mp4':
                    fail('use native-compatible MP4 sources')
                clock = video['media']['source_time_s']
                if clock < 0 or clock+end-start > durations[asset]+1e-6:
                    fail('the review window exceeds a video source duration')
                clocks[tuple(video['occurrence_path'])] = clock
            audio = span.audio
            if (len(audio.slices) != 1 or abs(audio.speed-1) > 1e-8
                    or not metadata.get(audio.source_id, {}).get('audio_codec')):
                fail('audio must follow one visible video with embedded audio')
            audio_start, audio_end = audio.slices[0]
            selected = [v for v in videos if v['media']['asset_id'] == audio.source_id
                        and abs(v['media']['source_time_s']-(audio_start+start-span.start_s)) < 1e-6]
            carriers = [span.slot(s['slot']) for s in scene['slots'] if s['source'] == audio.source_id]
            if (not selected or not any(s is not None and not s.held and abs(s.speed-1) < 1e-8
                    and abs(s.source_from_s-audio_start) < 1e-6 for s in carriers)
                    or audio_end+1e-6 < audio_start+end-span.start_s):
                fail('audio and video must share an ordinary unit-speed source clock')
            video_path = selected[0]['occurrence_path']
            source_start = selected[0]['media']['source_time_s']
        if ({tuple(v['occurrence_path']) for v in videos} != set(clocks) or any(
                abs(v['media']['source_time_s']-(clocks[tuple(v['occurrence_path'])]+index/fps)) > 1e-5 for v in videos)):
            fail('all videos require stable paths and ordinary unit-speed source clocks')
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
                    fail('media/text children are not implemented in this native profile')
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
                              'object_fit': style.get('object-fit', 'fill'),
                              'object_position': original.get('native_object_position', '50% 50%'),
                              **({'source_start': clocks[tuple(node['path'])],
                                  'source_end': clocks[tuple(node['path'])]+end-start} if kind == 'video' else {})})
        def dynamic(nodes):
            return [{'path': node['path'], 'content': node['content'],
                     'motion': node.get('motion'), 'caption': node.get('caption'),
                     'children': dynamic(node['children'])} for node in nodes]
        samples.append({'nodes': dynamic(prepared['nodes'])})
    return {'state_id': snapshot['state_id'], 'canvas': initial['canvas'], 'fps': fps,
            'range': {'from': start, 'to': end}, 'source_start': source_start, 'source_end': source_start+end-start,
            'video_path': video_path, 'initial': initial, 'media': media, 'samples': samples}, assets


def build(request_file):
    """Worker entry: descriptor and private asset registry stay in its owned folder."""
    request_file = Path(request_file)
    request = json.loads(request_file.read_text())
    packet, assets = compile_snapshot(request)
    packet.update(request_id=request['request_id'], requested_at=request['requested_at'])
    folder = request_file.parent
    (folder/'assets.json').write_text(json.dumps({key: {**value, 'path': str(value['path'])}
        for key, value in assets.items()}))
    staged = folder/'packet.tmp'
    staged.write_text(json.dumps(packet, separators=(',', ':')))
    staged.replace(folder/'preview.json')


if __name__ == '__main__':
    import sys
    try:
        build(sys.argv[1])
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
