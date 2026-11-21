"""A speaker and reusable ListenerPair retain three independent source clocks."""
from copy import deepcopy

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.native_preview import compile_snapshot, NativePreviewUnsupported
from moviestar.project import save_project
from moviestar.spec import save_spec
from tests.test_live_browser_preview import live_project


@pytest.fixture
def interview_project(live_project):
    spec, project, root = live_project
    for identity in ('bob', 'casey'):
        path = root / f'{identity}.mp4'; path.write_bytes(b'packet fixture only')
        spec['sources'].append({'id': identity, 'path': str(path), 'operations': []})
        meta = deepcopy(project['sources'][0]); meta.update(id=identity, path=str(path))
        project['sources'].append(meta)
    spec['spatial']['definitions']['listeners'] = {
        'name': 'ListenerPair',
        'inputs': [{'name': name, 'type': 'video', 'required': True} for name in ('left', 'right')],
        'children': [{'instance_id': name, 'definition_id': 'card',
            'bindings': {'clip': {'forward': name}},
            'layout': {'position': 'absolute', 'left': index * 90, 'top': 0, 'width': 80, 'height': 220}}
            for index, name in enumerate(('left', 'right'))]}
    children = spec['spatial']['scenes']['scene']['layout']['children']
    children[0].pop('motion', None)
    children.append({'instance_id': 'listeners', 'definition_id': 'listeners',
        'layout': {'position': 'absolute', 'left': 440, 'top': 20, 'width': 180, 'height': 220},
        'bindings': {'left': {'asset_id': 'bob', 'source': {'start': 2, 'end': 7}},
                     'right': {'asset_id': 'casey', 'source': {'start': 3, 'end': 8}}}})
    save_spec(spec, cwd=root); save_project(project, cwd=root)
    return spec, project, root


@pytest.mark.parametrize('speaker,origin,path', [
    ('camera', 1, ['scene', 'card', 'video']),
    ('bob', 2, ['scene', 'listeners', 'left', 'video']),
    ('casey', 3, ['scene', 'listeners', 'right', 'video']),
])
def test_three_person_packet_selects_any_visible_speaker_without_flattening(interview_project, speaker, origin, path):
    spec, _, root = interview_project
    spec['composition'][0]['audio_from'] = speaker
    spec['composition'][0]['slots'][0].update(source=speaker,
        source_from=f'0:00:0{origin}.000', source_to=f'0:00:0{origin + 5}.000')
    save_spec(spec, cwd=root)
    before = (root / 'moviestar/spec.json').read_bytes()
    packet, assets = compile_snapshot(read_snapshot(root, 1, 4))
    videos = [m for m in packet['media'] if m['kind'] == 'video']
    assert len(videos) == 3 and len(assets) == 4
    assert [(m['source_start'], m['source_end']) for m in videos] == [(2, 5), (3, 6), (4, 7)]
    assert packet['prepared_audio'] is True
    # Prepared PCM owns the voice; the first picture supplies the group clock.
    assert packet['video_path'] == ['scene', 'card', 'video']
    assert (packet['source_start'], packet['source_end']) == (2, 5)
    job = assets[packet['audio_plane']['job']]
    assert [(p['source_id'], p['source_start']) for p in job['source_pieces']] == [(speaker, origin + 1)]
    assert path in [m['path'] for m in videos]
    assert [m['path'] for m in videos] == [
        ['scene', 'card', 'video'], ['scene', 'listeners', 'left', 'video'],
        ['scene', 'listeners', 'right', 'video']]
    assert (root / 'moviestar/spec.json').read_bytes() == before


def test_four_visible_videos_remain_an_explicit_capability_error(interview_project):
    spec, _, root = interview_project
    child = deepcopy(spec['spatial']['scenes']['scene']['layout']['children'][0])
    child['instance_id'] = 'fourth'
    spec['spatial']['scenes']['scene']['layout']['children'].append(child)
    save_spec(spec, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='three'):
        compile_snapshot(read_snapshot(root, 0, 5))


def test_three_person_scene_allows_listeners_without_embedded_audio(interview_project):
    spec, project, root = interview_project
    for source in project['sources']:
        if source['id'] in ('bob', 'casey'):
            source['audio_codec'] = None
    save_project(project, cwd=root)
    packet, _ = compile_snapshot(read_snapshot(root, 0, 5))
    assert [m['has_audio'] for m in packet['media']] == [True, False, False]
    assert packet['prepared_audio'] is True


def test_three_person_audio_must_match_a_visible_picture_clock(interview_project):
    spec, _, root = interview_project
    spec['composition'][0]['audio_from'] = 'bob'
    # The visible Bob starts at two seconds; this carrier starts at one.
    spec['composition'][0]['slots'][0].update(source='bob')
    save_spec(spec, cwd=root)
    before = (root / 'moviestar/spec.json').read_bytes()
    with pytest.raises(NativePreviewUnsupported, match='audio and video.*source clock'):
        compile_snapshot(read_snapshot(root, 0, 5))
    assert (root / 'moviestar/spec.json').read_bytes() == before


@pytest.mark.parametrize('scenes', [2, 3])
def test_three_picture_cut_windows_require_explicit_rendered_fallback(interview_project, scenes):
    spec, _, root = interview_project
    original = deepcopy(spec['composition'][0]); tree = deepcopy(spec['spatial']['scenes']['scene'])
    spec['composition'] = []; spec['spatial']['scenes'] = {}
    for index in range(scenes):
        identity = f'part_{index}'
        part = deepcopy(original); part.update(id=identity, name=identity)
        part['slots'][0].update(id=f'slot_{index}', source_to='0:00:03.000')
        spec['composition'].append(part); spec['spatial']['scenes'][identity] = deepcopy(tree)
    save_spec(spec, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='three-picture.*single scene'):
        compile_snapshot(read_snapshot(root, 0, 2*scenes))
    packet, _ = compile_snapshot(read_snapshot(root, 0, 2))
    assert len(packet['media']) == 3, 'Review each scene independently without flattening'
