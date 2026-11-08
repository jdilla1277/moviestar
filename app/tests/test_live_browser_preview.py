"""The feasibility route consumes canonical samples without decoding/exporting."""

from copy import deepcopy
from pathlib import Path
import runpy

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.project import save_project
from moviestar.spec import empty_spec, save_spec

EXPERIMENT = Path(__file__).parents[2] / 'experiments' / 'live_browser_preview'


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
                            'width': 640, 'height': 360, 'fps': 30, 'has_audio': True}]}
    save_spec(spec, cwd=tmp_path)
    save_project(project, cwd=tmp_path)
    return spec, project, tmp_path


def _compiler():
    return runpy.run_path(str(EXPERIMENT / 'packet.py'))['compile_snapshot']


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


@pytest.mark.parametrize('mutation', ['extra_video', 'audio_mix', 'wrong_audio', 'speed', 'padding', 'media_children', 'inherited_fit', 'unknown_fps'])
def test_live_packet_rejects_unimplemented_behavior(live_project, mutation):
    spec, project, root = live_project
    if mutation == 'extra_video':
        extra = deepcopy(spec['spatial']['scenes']['scene']['layout']['children'][0])
        extra['instance_id'] = 'other'
        spec['spatial']['scenes']['scene']['layout']['children'].append(extra)
    elif mutation == 'audio_mix':
        spec['audio_mix']['source_audio']['gain_db'] = -3
    elif mutation == 'wrong_audio':
        spec['composition'][0]['audio_from'] = None
    elif mutation == 'speed':
        spec['spatial']['scenes']['scene']['layout']['children'][0]['bindings']['clip']['source']['start'] = 0
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
    with pytest.raises(ValueError, match='live feasibility'):
        _compiler()(read_snapshot(root, 0, 5))


def _session_module(monkeypatch):
    monkeypatch.syspath_prepend(str(EXPERIMENT))
    return runpy.run_path(str(EXPERIMENT / 'serve.py'))


def test_edit_during_live_compilation_cannot_publish_the_older_packet(live_project, tmp_path, monkeypatch):
    module = _session_module(monkeypatch)
    spec, _, root = live_project
    folder = tmp_path / 'packets'
    folder.mkdir()
    session = module['LiveSession'](root, folder)
    compile_packet = session.tick.__globals__['compile_snapshot']
    once = []
    def delayed(request):
        result = compile_packet(request)
        if not once:
            once.append(request['request_id'])
            spec['spatial']['scenes']['scene']['layout']['children'][1]['content']['text'] = 'New revision'
            save_spec(spec, cwd=root)
        return result
    monkeypatch.setitem(session.tick.__globals__, 'compile_snapshot', delayed)
    session.tick()
    state = session.queue.state()
    assert state['displayed'] is None and state['latest']['request_id'] != once[0]
    assert not (folder / (once[0]+'.json')).exists()
    session.tick()
    assert session.queue.state()['is_current']


def test_native_assets_are_scoped_range_served_and_invalidated_on_change(live_project, tmp_path, monkeypatch):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    module = _session_module(monkeypatch)
    _, _, root = live_project
    folder = tmp_path / 'packets'; folder.mkdir()
    session = module['LiveSession'](root, folder); session.tick()
    server = ThreadingHTTPServer(('127.0.0.1', 0), module['Handler'])
    server.daemon_threads = True
    server.token, server.session = 'unit-token', session
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}/projects/unit-token/'
    try:
        state = session.queue.state(); request_id = state['latest']['request_id']
        packet = json.loads(urlopen(base+'packet?request='+request_id).read())
        assert str(root) not in json.dumps(packet)
        asset = packet['media'][0]['asset']
        response = urlopen(Request(base+'assets/'+asset, headers={'Range': 'bytes=2-5'}))
        assert response.status == 206 and response.headers['Content-Type'] == 'video/mp4'
        assert response.read() == (root/'camera.mp4').read_bytes()[2:6]
        for route, headers, status in [('assets/unknown', {}, 404), ('../spec.json', {}, 404),
                                       ('state', {'Origin': 'https://example.com'}, 403)]:
            with pytest.raises(HTTPError) as error:
                urlopen(Request(base+route, headers=headers))
            assert error.value.code == status
        (root/'camera.mp4').write_bytes(b'regenerated source')
        with pytest.raises(HTTPError) as changed:
            urlopen(base+'assets/'+asset)
        assert changed.value.code == 409
        fresh = json.loads(urlopen(base+'state').read())
        assert fresh['latest']['request_id'] != request_id and not fresh['is_current']
    finally:
        server.shutdown();server.server_close();thread.join()


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
