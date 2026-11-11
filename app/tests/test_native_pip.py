"""Two visible clocks must keep canonical speaker routing and independent seeks."""
from copy import deepcopy

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.native_preview import compile_snapshot, NativePreviewUnsupported
from moviestar.project import save_project
from moviestar.spec import save_spec
from tests.test_live_browser_preview import live_project


@pytest.fixture
def pip_project(live_project):
    spec, project, root = live_project
    path = root / 'listener.mp4'; path.write_bytes(b'other camera')
    spec['sources'].append({'id': 'listener', 'path': str(path), 'operations': []})
    meta = deepcopy(project['sources'][0]); meta.update(id='listener', path=str(path))
    project['sources'].append(meta)
    spec['composition'][0]['layout'] = {'preset': 'picture-in-picture'}
    spec['composition'][0]['slots'].append({'slot': 'inset', 'source': 'listener',
        'source_from': '0:00:02.000', 'source_to': '0:00:07.000'})
    extra = deepcopy(spec['spatial']['scenes']['scene']['layout']['children'][0])
    extra.update(instance_id='inset', layout={'position': 'absolute', 'right': 10,
        'top': 10, 'width': 120, 'height': 90})
    extra.pop('motion', None)
    extra['bindings']['clip'] = {'asset_id': 'listener', 'source': {'start': 2, 'end': 7}}
    spec['spatial']['scenes']['scene']['layout']['children'].append(extra)
    save_spec(spec, cwd=root); save_project(project, cwd=root)
    return spec, project, root


@pytest.mark.parametrize('speaker,start', [('camera', 1), ('listener', 2)])
def test_two_video_packet_chooses_resolved_speaker_and_preserves_both_clocks(pip_project, speaker, start):
    spec, _, root = pip_project
    spec['composition'][0]['audio_from'] = speaker; save_spec(spec, cwd=root)
    packet, assets = compile_snapshot(read_snapshot(root, 1, 4))
    videos = [m for m in packet['media'] if m['kind'] == 'video']
    assert len(videos) == len(assets) == 2
    assert [(m['source_start'], m['source_end']) for m in videos] == [(2, 5), (3, 6)]
    selected = next(m for m in videos if m['path'] == packet['video_path'])
    assert assets[selected['asset']]['path'] == root / ('camera.mp4' if speaker == 'camera' else 'listener.mp4')
    assert packet['source_start'] == start + 1 and packet['source_end'] == start + 4


def test_silent_listener_is_allowed_but_selected_speaker_needs_audio(pip_project):
    _, project, root = pip_project
    project['sources'][1]['audio_codec'] = None; save_project(project, cwd=root)
    assert len(compile_snapshot(read_snapshot(root, 0, 5))[0]['media']) == 2
    spec = pip_project[0]; spec['composition'][0]['audio_from'] = 'listener'; save_spec(spec, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='audio'):
        compile_snapshot(read_snapshot(root, 0, 5))


@pytest.mark.parametrize('change,reason', [('third', 'two'), ('rate', 'rate'),
    ('operations', 'source operations'), ('clock', 'audio'), ('held', 'unit-speed')])
def test_all_pip_media_and_audio_clocks_are_validated(pip_project, change, reason):
    spec, project, root = pip_project
    if change == 'third':
        child = deepcopy(spec['spatial']['scenes']['scene']['layout']['children'][-1])
        child['instance_id'] = 'third'
        spec['spatial']['scenes']['scene']['layout']['children'].append(child)
    elif change == 'rate':
        project['sources'][1]['fps'] = 24
    elif change == 'operations':
        spec['sources'][1]['operations'] = [{'type': 'cut', 'from': '0:00:00.000', 'to': '0:00:01.000'}]
    elif change == 'clock':
        spec['spatial']['scenes']['scene']['layout']['children'][0]['bindings']['clip']['source'] = {'start': 0, 'end': 5}
    else:
        spec['spatial']['scenes']['scene']['layout']['children'][-1]['bindings']['clip']['source']['end'] = 3
    save_spec(spec, cwd=root); save_project(project, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match=reason):
        compile_snapshot(read_snapshot(root, 0, 5))


def test_preset_pip_uses_authoring_geometry_without_mutating_the_project(pip_project, monkeypatch):
    import subprocess
    spec, _, root = pip_project
    spec.pop('spatial'); spec['overlays'] = []
    spec['composition'][0]['layout'] = {'preset': 'picture-in-picture', 'geometry': {
        'inset': {'size': .28, 'anchor': 'top-right', 'shape': {'kind': 'rounded',
            'radius': .12, 'border': {'width': 4, 'color': '#f4da67'}}}}}
    spec['composition'][0]['slots'][0]['framing'] = {'mode': 'fill', 'x': .55, 'y': .5}
    save_spec(spec, cwd=root)
    before = (root/'moviestar/spec.json').read_bytes()
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('No native decoding or rendering'))
    packet, _ = compile_snapshot(read_snapshot(root, 0, 5))
    assert len(packet['media']) == 2 and len(packet['samples']) == 150
    assert packet['media'][0]['object_position'] == '55% 50%'
    assert before == (root/'moviestar/spec.json').read_bytes()
    from moviestar.spec import layout_regions
    region = layout_regions(spec['composition'][0]['layout'], spec['composition_canvas'], result_time_s=0)['inset']
    inset = packet['initial']['nodes'][1]
    assert inset['style']['left'] == f"{region['x']-4}px"
    assert inset['children'][0]['style']['width'] == f"{region['width']}px"


@pytest.mark.parametrize('change', ['overlay', 'motion', 'mix'])
def test_preset_pip_rejects_unimplemented_content_instead_of_dropping_it(pip_project, change):
    spec, _, root = pip_project
    spec.pop('spatial'); spec['overlays'] = []
    if change == 'overlay':
        spec['overlays'] = [{'id': 'note', 'track': 'notes', 'kind': 'manual', 'text': 'Must not disappear',
            'timing': {'space': 'scene', 'scene': 'scene', 'from': '0:00:01.000', 'to': '0:00:03.000'},
            'position': {'preset': 'bottom'}, 'style': {'preset': 'plain'}}]
    elif change == 'motion':
        spec['composition'][0]['layout']['geometry'] = {'inset': {'motion': {'preset': 'bounce'}}}
    else:
        spec['audio_mix']['source_audio']['gain_db'] = -3
    save_spec(spec, cwd=root)
    with pytest.raises(NativePreviewUnsupported):
        compile_snapshot(read_snapshot(root, 0, 5))


@pytest.mark.parametrize('speaker', [None, 'listener'])
def test_preset_invalid_audio_routing_is_a_native_capability_error(pip_project, speaker):
    spec, project, root = pip_project
    spec.pop('spatial'); spec['overlays'] = []; spec['composition'][0]['audio_from'] = speaker
    if speaker == 'listener': project['sources'][1]['audio_codec'] = None
    save_spec(spec, cwd=root); save_project(project, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='audio'):
        compile_snapshot(read_snapshot(root, 0, 5))


def test_flat_compositions_fail_before_native_scene_preparation(pip_project):
    spec, _, root = pip_project
    spec.pop('spatial'); spec['overlays'] = []
    spec['composition'] = [{'source': 'camera', 'source_from': '0:00:01.000', 'source_to': '0:00:06.000'}]
    save_spec(spec, cwd=root)
    with pytest.raises(ValueError, match='scene composition'):
        read_snapshot(root, 0, 5)
