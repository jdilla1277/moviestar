"""Native review selects one canonical span without resetting its clocks."""

from copy import deepcopy
import json
import selectors
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.native_preview import compile_snapshot
from moviestar.native_preview import NativePreviewUnsupported
from moviestar.spec import load_spec, save_spec
from moviestar.spatial_layout import browser_available
from tests.test_live_browser_preview import live_project
from tests.test_native_preview import loaded_native_project


def surround_scene(spec):
    """Keep source time equal to result time, but shift the scene origin to 1s."""
    selected = spec['composition'][0]
    before, after = deepcopy(selected), deepcopy(selected)
    before.update(id='before', name='Before')
    before['slots'][0].update(source_from='0:00:00.000', source_to='0:00:01.000')
    after.update(id='after', name='After')
    after['slots'][0].update(source_from='0:00:06.000', source_to='0:00:08.000')
    spec['composition'] = [before, selected, after]
    for scene in spec['composition']:
        scene['slots'][0]['id'] = 'slot_' + scene['id']
    for overlay in spec['overlays']:
        from moviestar.spec import parse_timecode_string
        from moviestar.timecodes import format_timecode
        for key in ['from', 'to']:
            overlay[key] = format_timecode(parse_timecode_string(overlay[key], key) + 1)['text']


@pytest.mark.parametrize('route', ['explicit', 'inferred', 'global'])
@pytest.mark.parametrize('window', [(1, 6), (2, 5), (1, 1 + 1 / 30)])
def test_native_window_preserves_selected_scene_source_caption_and_motion_clocks(live_project, route, window):
    spec, _, root = live_project
    surround_scene(spec)
    if route != 'explicit':
        spec['composition'][1]['audio_from'] = None
    if route == 'global':
        spec['composition_audio_from'] = 'camera'
    save_spec(spec, cwd=root)
    packet, assets = compile_snapshot(read_snapshot(root, *window))
    start, end = window
    assert packet['initial']['scene_id'] == 'scene'
    assert packet['source_start'] == pytest.approx(start)
    assert packet['source_end'] == pytest.approx(end)
    assert packet['range'] == {'from': start, 'to': end}
    assert len(packet['samples']) == round((end - start) * 30)
    assert len(assets) == 1
    motion = packet['samples'][0]['nodes'][0]['motion']
    assert motion['translate_x'] == f'{(start - 1) * 20:g}px'
    assert packet['samples'][0]['nodes'][2]['content'] == ('First' if start < 2 else 'Second\nline')


@pytest.mark.parametrize('window', [(.5, 1.5), (5.5, 6.5), (0, 8)])
def test_native_window_rejects_crossing_scenes_whose_visible_source_disagrees_with_audio(live_project, window):
    spec, _, root = live_project
    surround_scene(spec)
    for name in ['before', 'after']:
        spec['spatial']['scenes'][name] = deepcopy(spec['spatial']['scenes']['scene'])
    save_spec(spec, cwd=root)
    with pytest.raises(ValueError, match='audio'):
        compile_snapshot(read_snapshot(root, *window))


def test_native_window_ignores_unrelated_source_edits_and_unplaced_live_caption_tracks(live_project):
    spec, project, root = live_project
    surround_scene(spec)
    snapshot = read_snapshot(root, 2, 5)
    snapshot['spec'] = spec
    # The unrelated preset scene may use a source the native player cannot play.
    other = deepcopy(spec['sources'][0])
    other.update(id='other', operations=[{'type': 'cut', 'from': '0:00:07.000', 'to': '0:00:08.000'}])
    spec['sources'].append(other)
    metadata = deepcopy(project['sources'][0]); metadata['id'] = 'other'
    snapshot['project']['sources'].append(metadata)
    spec['composition'][0]['slots'][0]['source'] = 'other'
    spec['composition'][0]['audio_from'] = 'other'
    spec['captions'] = [{'track': 'unplaced', 'frozen': False}]
    packet, _ = compile_snapshot(snapshot)
    assert packet['source_start'] == 2


@pytest.mark.parametrize('other_layout', ['draft', 'two_videos'])
def test_native_window_does_not_require_other_scenes_to_be_native_ready(live_project, other_layout):
    spec, _, root = live_project
    surround_scene(spec)
    tree = deepcopy(spec['spatial']['scenes']['scene'])
    if other_layout == 'draft':
        tree['layout']['children'][0]['bindings'] = {}
    else:
        extra = deepcopy(tree['layout']['children'][0])
        extra['instance_id'] = 'second_video'
        tree['layout']['children'].append(extra)
    spec['spatial']['scenes']['before'] = tree
    save_spec(spec, cwd=root)
    packet, _ = compile_snapshot(read_snapshot(root, 2, 5))
    assert packet['initial']['scene_id'] == 'scene'


@pytest.mark.parametrize('boundary', ['incoming', 'outgoing', 'opening', 'closing'])
def test_native_window_allows_transitions_outside_the_review(live_project, boundary):
    spec, _, root = live_project
    surround_scene(spec)
    if boundary in {'incoming', 'outgoing'}:
        index = 1 if boundary == 'incoming' else 2
        spec['composition'][index]['transition_in'] = {'type': 'dip-black', 'duration': .4}
    else:
        spec[boundary + '_transition'] = {'type': 'dip-black', 'duration': .4}
    save_spec(spec, cwd=root)
    packet, _ = compile_snapshot(read_snapshot(root, 2, 5))
    assert packet['source_start'] == 2


@pytest.mark.parametrize('transition,window', [
    ('incoming', (1, 2)), ('outgoing', (5, 6)),
    # Last affected interval is shorter than one frame. Check the range,
    # not just sampled frame times, before accepting its audio/playback tail.
    ('subframe_outgoing', (5, 6)),
])
def test_native_window_rejects_touching_a_transition(live_project, transition, window):
    spec, _, root = live_project
    surround_scene(spec)
    index = 1 if transition == 'incoming' else 2
    spec['composition'][index]['transition_in'] = {
        'type': 'dip-black', 'duration': .02 if transition == 'subframe_outgoing' else .4}
    save_spec(spec, cwd=root)
    with pytest.raises(ValueError, match='transition'):
        compile_snapshot(read_snapshot(root, *window))


def test_native_window_can_end_exactly_before_an_outgoing_transition(live_project):
    spec, _, root = live_project
    surround_scene(spec)
    spec['composition'][2]['transition_in'] = {'type': 'dip-black', 'duration': .4}
    save_spec(spec, cwd=root)
    packet, _ = compile_snapshot(read_snapshot(root, 2, 5.8))
    assert packet['source_end'] == 5.8


def test_native_window_rejects_misaligned_composition_wide_audio(live_project):
    spec, _, root = live_project
    surround_scene(spec)
    # The global route begins at source 2 at result 0; the selected scene still
    # plays source 1 at result 1. Audio from the same asset is not sufficient.
    spec['composition'][0]['slots'][0].update(source_from='0:00:02.000', source_to='0:00:03.000')
    spec['composition_audio_from'] = 'camera'
    save_spec(spec, cwd=root)
    with pytest.raises(ValueError, match='audio'):
        compile_snapshot(read_snapshot(root, 2, 5))


def test_native_window_accepts_a_static_preset_without_rewriting_it(live_project):
    spec, _, root = live_project
    surround_scene(spec)
    save_spec(spec, cwd=root)
    packet, _ = compile_snapshot(read_snapshot(root, 0, 1))
    assert len(packet['media']) == 1
    assert packet['source_start'] == 0


@pytest.mark.parametrize('window,accepted', [((4, 6), True), ((2, 5), False), ((3, 4), False)])
def test_native_window_uses_later_span_origin_after_pacing_outside_the_range(live_project, window, accepted):
    spec, _, root = live_project
    surround_scene(spec)
    spec['motion'] = {'version': 1, 'scenes': [{'scene': 'scene', 'scene_id': 'scene',
        'slots': [{'slot': 'main', 'pacing': [{'id': 'pause', 'mode': 'hold',
            'at': '0:00:02.000', 'space': 'source-local', 'duration': '0:00:01.000'}], 'camera': []}]}]}
    save_spec(spec, cwd=root)
    if not accepted:
        with pytest.raises(NativePreviewUnsupported):
            compile_snapshot(read_snapshot(root, *window))
    else:
        packet, _ = compile_snapshot(read_snapshot(root, *window))
        assert packet['source_start'] == 3 and packet['source_end'] == 5
        # Visual motion retains scene-local time 3 at the later span's start,
        # not zero or the source's local time 2.
        assert packet['samples'][0]['nodes'][0]['motion']['translate_x'] == '60px'


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX foreground service')
@pytest.mark.parametrize('route', ['explicit', 'inferred', 'global'])
def test_loaded_multi_scene_native_cli_recovers_and_undoes_at_nonzero_result_time(loaded_native_project, route):
    from click.testing import CliRunner
    from moviestar.cli import cli
    spec, _, root = loaded_native_project
    surround_scene(spec)
    if route != 'explicit':
        spec['composition'][1]['audio_from'] = None
    if route == 'global':
        spec['composition_audio_from'] = 'camera'
    save_spec(spec, cwd=root)
    # Establish the initial model through the real authoring command before
    # comparing revision IDs.
    initial_path = root / 'initial.json'
    initial_path.write_text(json.dumps(spec['spatial']))
    initial_edit = CliRunner().invoke(cli, ['composition', 'model', 'apply', str(initial_path)])
    assert initial_edit.exit_code == 0, initial_edit.output
    spec = load_spec(root)
    process = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview',
        '--workspace', str(root), '--mode', 'native', '--from', '2', '--to', '5',
        '--port', '0', '--no-open'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for(url, predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with urlopen(url + 'state') as response:
                state = json.load(response)
            if predicate(state):
                return state
            time.sleep(.02)
        pytest.fail('Native CLI state timeout: ' + str(state))

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(10), 'CLI did not print ready response'
            line = process.stdout.readline()
            assert line.strip().startswith('{"'), line
            ready = json.loads(line)
        initial = wait_for(ready['url'], lambda state: state['is_current'])
        original = initial['displayed']['request_id']
        with urlopen(ready['url'] + 'packet?request=' + original) as response:
            packet = json.load(response)
        assert packet['source_start'] == 2 and packet['source_end'] == 5
        assert packet['range'] == {'from': 2, 'to': 5}
        # Move a cut inside the fixed result window: fail visibly, without
        # silently changing scenes or rendering a fallback MP4.
        invalid = deepcopy(spec)
        invalid['composition'][1]['slots'][0]['source_to'] = '0:00:03.000'
        save_spec(invalid, cwd=root)
        failed = wait_for(ready['url'], lambda state: state['latest']['status'] == 'failed')
        assert failed['displayed']['request_id'] == original and not failed['is_current']
        save_spec(spec, cwd=root)
        restored = wait_for(ready['url'], lambda state: state['is_current'] and state['displayed']['request_id'] != original)
        # An actual model edit and undo traverse the revision journal as well
        # as creating fresh worker requests at the same selected result range.
        model = deepcopy(spec['spatial'])
        model['scenes']['scene']['layout']['children'][1]['content']['text'] = 'New title'
        model_path = root / 'model.json'; model_path.write_text(json.dumps(model))
        edited = CliRunner().invoke(cli, ['composition', 'model', 'apply', str(model_path)])
        assert edited.exit_code == 0, edited.output
        changed = wait_for(ready['url'], lambda state: state['is_current'] and state['displayed']['state_id'] != restored['displayed']['state_id'])
        undone = CliRunner().invoke(cli, ['undo'])
        assert undone.exit_code == 0, undone.output
        final = wait_for(ready['url'], lambda state: state['is_current'] and state['displayed']['state_id'] == restored['displayed']['state_id'])
        assert final['displayed']['request_id'] != changed['displayed']['request_id']
    finally:
        process.terminate()
        try:
            process.wait(timeout=9)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        process.stdout.close(); process.stderr.close()
    assert process.returncode == 0
    assert not list((root / 'moviestar/local-preview').glob('session-*'))


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_selected_native_window_agrees_with_real_watch_and_export(loaded_native_project):
    from click.testing import CliRunner
    from moviestar.cli import cli
    from tests.test_spatial_video import _pixels, _rms
    spec, _, root = loaded_native_project
    surround_scene(spec)
    for scene, start, end in zip(spec['composition'], [0, 1, 2], [.4, 1.4, 2.4]):
        from moviestar.timecodes import format_timecode
        scene['slots'][0].update(source_from=format_timecode(start)['text'],
                                 source_to=format_timecode(end)['text'])
    spec['composition_canvas'].update(width=320, height=180)
    children = spec['spatial']['scenes']['scene']['layout']['children']
    children[0]['bindings']['clip']['source'] = {'start': 1, 'end': 1.4}
    children[0]['layout'].update(left=10, top=10, width=180, height=90)
    children[0]['motion'] = {'translate_x': [{'at': 0, 'value': 0}, {'at': .3, 'value': 30}]}
    children[1]['layout'].update(left=10, top=110, width=300, height=30)
    children[2]['layout'].update(left=10, width=300, height=40)
    spec['overlays'][0].update({'from': '0:00:00.400', 'to': '0:00:00.600'})
    spec['overlays'][1].update({'from': '0:00:00.600', 'to': '0:00:00.800'})
    save_spec(spec, cwd=root)
    before = (root / 'moviestar/spec.json').read_bytes()
    packet, _ = compile_snapshot(read_snapshot(root, .4, .8))
    assert packet['source_start'] == 1 and packet['source_end'] == pytest.approx(1.4)
    assert packet['samples'][3]['nodes'][0]['motion']['translate_x'] == '10px'
    assert packet['samples'][5]['nodes'][2]['content'] == 'First'
    assert packet['samples'][6]['nodes'][2]['content'] == 'Second\nline'
    for args in [['export', '--out', 'export.mp4', '--quiet'],
                 ['watch', '--from', '.4', '--to', '.8', '--out', 'watch.mp4']]:
        result = CliRunner().invoke(cli, [*args, '--video-encoder', 'x264'])
        assert result.exit_code == 0, result.output
    for index in [0, 3, 9]:
        watch = _pixels(root / 'watch.mp4', index)
        exported = _pixels(root / 'export.mp4', index + 12)
        # Patch interiors avoid independent H.264 glyph/chroma edge artifacts.
        for x, y in [(80, 60), (300, 10)]:
            offset = (y * 320 + x) * 3
            assert max(abs(a - b) for a, b in zip(watch[offset:offset + 3], exported[offset:offset + 3])) <= 8
    assert _rms(root / 'watch.mp4') == pytest.approx(_rms(root / 'export.mp4', start=.4), rel=.12)
    assert _rms(root / 'watch.mp4') == pytest.approx(_rms(root / 'camera.mp4', start=1), rel=.12)
    assert (root / 'moviestar/spec.json').read_bytes() == before
