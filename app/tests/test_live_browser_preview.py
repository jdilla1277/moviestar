"""Native descriptors consume canonical samples without decoding/exporting."""

from copy import deepcopy

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.project import save_project
from moviestar.spec import empty_spec, save_spec

@pytest.fixture
def live_project(tmp_path):
    source = tmp_path / 'camera.mp4'
    source.write_bytes(b'unit fixture; browser tests use an actual H.264/AAC file')
    spec = empty_spec([{'id': 'camera', 'path': str(source)}])
    spec['composition_canvas'] = {'preset': None, 'width': 640, 'height': 360, 'aspect_ratio': '16:9'}
    spec['composition'] = [{'id': 'scene', 'name': 'Scene', 'layout': {'preset': 'single'},
        'slots': [{'slot': 'main', 'source': 'camera', 'source_from': '0:00:01.000', 'source_to': '0:00:06.000'}],
        'audio_from': 'camera'}]
    spec['spatial'] = {'definitions': {'card': {'name': 'Card',
        'inputs': [{'name': 'clip', 'type': 'video', 'required': True}],
        'children': [{'id': 'video', 'type': 'video', 'content': {'input': 'clip'},
                      'layout': {'width': '100%', 'height': '100%'},
                      'style': {'object_fit': 'cover', 'border_radius': 16}}]}},
        'scenes': {'scene': {'layout': {'background_color': '#112244', 'children': [
            {'instance_id': 'card', 'definition_id': 'card',
             'bindings': {'clip': {'asset_id': 'camera', 'source': {'start': 1, 'end': 6}}},
             'layout': {'position': 'absolute', 'left': 20, 'top': 20, 'width': 400, 'height': 220},
             'motion': {'translate_x': [{'at': 0, 'value': 0}, {'at': 4, 'value': 80}]}},
            {'id': 'title', 'type': 'text', 'content': {'text': 'Original title'},
             'layout': {'position': 'absolute', 'left': 20, 'top': 260, 'width': 500, 'height': 50}},
            {'id': 'captions', 'type': 'caption', 'content': {'track': 'captions'},
             'layout': {'position': 'absolute', 'left': 20, 'bottom': 0, 'width': 500, 'height': 80}},
        ]}}}}
    spec['overlays'] = [{'id': identity, 'kind': 'caption', 'track': 'captions',
                         'from': start, 'to': end, 'text': text, 'position': {'preset': 'bottom'},
                         'style': {'preset': 'plain'}} for identity, start, end, text in [
        ('one', '0:00:00.000', '0:00:01.000', 'First'),
        ('two', '0:00:01.000', '0:00:05.000', 'Second\nline')]]
    project = {'sources': [{'id': 'camera', 'path': str(source), 'duration': {'seconds': 8},
                            'width': 640, 'height': 360, 'fps': 30, 'audio_codec': 'aac', 'video_codec': 'h264'}]}
    save_spec(spec, cwd=tmp_path)
    save_project(project, cwd=tmp_path)
    return spec, project, tmp_path


def _compiler():
    from moviestar.native_preview import compile_snapshot
    return compile_snapshot


def test_live_packet_keeps_canonical_motion_captions_and_source_offset(live_project, monkeypatch):
    import subprocess
    spec, _, root = live_project
    snapshot = read_snapshot(root, 0, 5)
    def unexpected(*args, **kwargs):
        raise AssertionError('Live packet must not probe, decode or render media')
    monkeypatch.setattr(subprocess, 'run', unexpected)
    monkeypatch.setattr(subprocess, 'Popen', unexpected)
    packet, assets = _compiler()(snapshot)
    assert packet['fps'] == 30 and len(packet['samples']) == 150
    assert packet['source_start'] == 1 and packet['source_end'] == 6
    assert packet['samples'][0]['nodes'][0]['motion']['translate_x'] == '0px'
    assert packet['samples'][60]['nodes'][0]['motion']['translate_x'] == '40px'
    assert packet['samples'][29]['nodes'][2]['content'] == 'First'
    assert packet['samples'][30]['nodes'][2]['content'] == 'Second\nline'
    assert packet['state_id'] == snapshot['state_id']
    assert str(root) not in __import__('json').dumps(packet)
    assert len(assets) == 1
    assert spec['spatial']['scenes']['scene']['layout']['children'][1]['content']['text'] == 'Original title'


@pytest.mark.parametrize('mutation', ['extra_video', 'audio_mix', 'no_audio', 'speed', 'padding', 'media_children', 'inherited_fit', 'unknown_fps', 'low_rate'])
def test_live_packet_rejects_unimplemented_behavior(live_project, mutation):
    spec, project, root = live_project
    if mutation == 'extra_video':
        extra = deepcopy(spec['spatial']['scenes']['scene']['layout']['children'][0])
        extra['instance_id'] = 'other'
        spec['spatial']['scenes']['scene']['layout']['children'].append(extra)
    elif mutation == 'audio_mix':
        spec['audio_mix']['source_audio']['gain_db'] = -3
    elif mutation == 'no_audio':
        project['sources'][0]['audio_codec'] = None
        project['sources'][0]['has_audio'] = True  # A noncanonical flag cannot invent audio.
        save_project(project, cwd=root)
    elif mutation == 'speed':
        spec['spatial']['scenes']['scene']['layout']['children'][0]['bindings']['clip']['source']['start'] = 0
    elif mutation == 'low_rate':
        project['sources'][0]['fps'] = 10
        save_project(project, cwd=root)
    elif mutation == 'unknown_fps':
        project['sources'][0]['fps'] = None
        save_project(project, cwd=root)
    elif mutation == 'inherited_fit':
        spec['spatial']['definitions']['card']['children'][0]['style']['object_fit'] = 'inherit'
    elif mutation == 'padding':
        spec['spatial']['definitions']['card']['children'][0]['layout']['padding'] = 3
    else:
        spec['spatial']['definitions']['card']['children'][0]['children'] = [
            {'id': 'inside', 'type': 'text', 'content': {'text': 'unsupported child'}}]
    save_spec(spec, cwd=root)
    with pytest.raises(ValueError, match='Native preview unavailable'):
        _compiler()(read_snapshot(root, 0, 5))


def test_live_probe_rejects_live_caption_recipes(live_project):
    snapshot = read_snapshot(live_project[2], 0, 5)
    snapshot['spec']['captions'] = [{'track': 'captions', 'frozen': False}]
    with pytest.raises(ValueError, match='live caption'):
        _compiler()(snapshot)


def test_live_probe_rejects_source_operations_it_cannot_play(live_project):
    snapshot = read_snapshot(live_project[2], 0, 5)
    snapshot['spec']['sources'][0]['operations'] = [{'type': 'cut', 'from': '0:00:07.000', 'to': '0:00:08.000'}]
    with pytest.raises(ValueError, match='source operations'):
        _compiler()(snapshot)


@pytest.mark.parametrize('routing', ['explicit', 'inferred', 'global'])
@pytest.mark.parametrize('window', [(0, 5), (1, 3)])
def test_native_audio_uses_canonical_routes(live_project, routing, window):
    from moviestar.resolved import resolve_project
    snapshot = read_snapshot(live_project[2], *window)
    assert 'has_audio' not in snapshot['project']['sources'][0]
    if routing != 'explicit':
        snapshot['spec']['composition'][0]['audio_from'] = None
    if routing == 'global':
        snapshot['spec']['composition_audio_from'] = 'camera'
    audio = resolve_project(snapshot['project'], snapshot['spec']).spans[0].audio
    assert audio.source_id == 'camera' and audio.slices == ((1.0, 6.0),)
    assert audio.routing == ('only_audio_slot' if routing == 'inferred' else 'composition')
    packet, _ = _compiler()(snapshot)
    assert packet['source_start'] == 1 + window[0]
    assert packet['source_end'] == 1 + window[1]


@pytest.mark.parametrize('change', [
    {'source_id': None}, {'source_id': 'other'}, {'slices': ()},
    {'slices': ((2.0, 7.0),)}, {'slices': ((1.0, 3.0),)},
    {'slices': ((1.0, 2.0), (3.0, 7.0))}, {'speed': 2.0},
])
def test_native_audio_rejects_a_different_resolved_clock(live_project, monkeypatch, change):
    from dataclasses import replace
    from moviestar import native_preview
    snapshot = read_snapshot(live_project[2], 0, 5)
    timeline = native_preview.resolve_project(snapshot['project'], snapshot['spec'])
    span = timeline.spans[0]
    altered = replace(timeline, spans=(replace(span, audio=replace(span.audio, **change)),))
    monkeypatch.setattr(native_preview, 'resolve_project', lambda *_: altered)
    with pytest.raises(ValueError, match='audio'):
        native_preview.compile_snapshot(snapshot)
