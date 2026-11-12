"""Native captions reuse export painting and timing, without rendering video."""
from copy import deepcopy
import json
import subprocess
from pathlib import Path

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.native_preview import build, compile_snapshot, NativePreviewUnsupported
from moviestar.spec import save_spec
from click.testing import CliRunner
from moviestar.cli import cli
from tests.conftest import inject_synthetic_transcript, make_word, make_segment
from tests.test_native_pip import pip_project
from tests.test_live_browser_preview import live_project


def cue(*, highlight=True):
    return {'id': 'words', 'kind': 'caption', 'track': 'captions',
        'from': '0:00:00.000', 'to': '0:00:02.000', 'text': 'First second',
        'position': {'anchor': 'bottom-center', 'margin_y': 20},
        'style': {'preset': 'plain', 'resolved': {'font_size': 32, 'color': '#ffffff'}},
        **({'highlight': {'mode': 'spoken-word', 'color': '#ffe94a'}, 'tokens': [
            {'text': 'First', 'from': '0:00:00.000', 'to': '0:00:00.500'},
            {'text': 'second', 'from': '0:00:00.700', 'to': '0:00:02.000'}]} if highlight else {})}


def preset(pip_project):
    spec, _, root = pip_project
    spec.pop('spatial'); spec['overlays'] = [cue()]
    save_spec(spec, cwd=root)
    return root


def test_caption_schedule_uses_export_word_events_and_half_open_visibility(pip_project, monkeypatch):
    root = preset(pip_project)
    before = (root/'moviestar/spec.json').read_bytes()
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('planning must not paint'))
    packet, jobs = compile_snapshot(read_snapshot(root, 0, 5))
    plane = packet['caption_plane']
    states = plane['states']
    assert [s['frame'] for s in states] == [0, 15, 21, 60]
    assert [s['cues'][0]['active_word'] if s['cues'] else None for s in states] == [0, None, 1, None]
    assert plane['frames'][14:22] == [0, 1, 1, 1, 1, 1, 1, 2]
    assert states[0]['cues'][0]['text'] == 'First second'
    assert states[-1]['cues'] == []
    assert len([v for v in jobs.values() if v['kind'] == 'caption_job']) == 1
    assert (root/'moviestar/spec.json').read_bytes() == before


def test_caption_worker_paints_only_changed_states_to_scoped_pngs(pip_project, tmp_path, monkeypatch):
    root = preset(pip_project); monkeypatch.chdir(root)
    request = read_snapshot(root, 0, 5)
    request.update(request_id='a'*24, requested_at=1)
    path = tmp_path/'request.json'; path.write_text(json.dumps(request))
    original = subprocess.Popen; calls = []
    def run(args, **kwargs):
        calls.append(args)
        return original(args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', run)
    build(path)
    packet = json.loads((tmp_path/'preview.json').read_text())
    registry = json.loads((tmp_path/'assets.json').read_text())
    states = packet['caption_plane']['states']
    images = [v for v in registry.values() if v['kind'] == 'image']
    assert len(images) == 4
    assert all(s['asset'] in registry for s in states)
    assert all(v['path'].startswith(str(tmp_path)) and v['path'].endswith('.png') for v in images)
    assert all(open(v['path'], 'rb').read(8) == b'\x89PNG\r\n\x1a\n' for v in images)
    paint = [args for args in calls if args[0] == 'ffmpeg' and '-filter_complex' in args]
    assert len(paint) == 1
    assert not any('.mp4' in arg for args in paint for arg in args)
    assert str(root) not in json.dumps(packet) and str(tmp_path) not in json.dumps(packet)


def test_caption_scene_clock_and_nonzero_review_window(pip_project):
    spec, _, root = pip_project
    spec.pop('spatial'); record = cue(highlight=False)
    record['kind'] = 'manual'
    record.pop('from'); record.pop('to')
    record['timing'] = {'space': 'scene', 'scene': 'scene', 'from': '0:00:01.000', 'to': '0:00:03.000'}
    spec['overlays'] = [record]; save_spec(spec, cwd=root)
    packet, _ = compile_snapshot(read_snapshot(root, 2, 4))
    plane = packet['caption_plane']
    assert [s['frame'] for s in plane['states']] == [0, 30]
    assert plane['states'][0]['cues'][0]['text'] == record['text']
    assert not plane['states'][1]['cues']


def test_missing_highlight_tokens_is_an_explicit_capability_failure(pip_project):
    root = preset(pip_project)
    spec = read_snapshot(root, 0, 5)['spec']; spec['overlays'][0].pop('tokens')
    save_spec(spec, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='tokens'):
        compile_snapshot(read_snapshot(root, 0, 5))


@pytest.mark.parametrize('frozen', [False, True])
def test_live_recipe_follows_trim_without_freezing_or_saving(pip_project, monkeypatch, frozen):
    root = preset(pip_project); monkeypatch.chdir(root)
    inject_synthetic_transcript(root, [make_word('First', 1, 1.5), make_word('second', 1.7, 3)],
        [make_segment('First second', 1, 3)])
    args = ['captions', 'generate', '--highlight', 'spoken-word']
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    snapshot = read_snapshot(root, 0, 5)
    spec = snapshot['spec']
    spec['captions'][0]['frozen'] = frozen
    spec['composition'][0]['slots'][0].update(source_from='0:00:02.000', source_to='0:00:07.000')
    save_spec(spec, cwd=root)
    before = (root/'moviestar/spec.json').read_bytes()
    # Native planning must honor the explicit workspace even from another cwd.
    monkeypatch.chdir(root.parent)
    packet, _ = compile_snapshot(read_snapshot(root, 0, 5))
    cues = packet['caption_plane']['states'][0]['cues']
    assert cues[0]['text'] == ('First second' if frozen else 'second')
    assert (root/'moviestar/spec.json').read_bytes() == before


@pytest.mark.parametrize('highlight', [False, True])
def test_transparent_caption_pixels_match_canonical_export(highlight, tmp_path):
    import av
    import numpy as np
    from moviestar.native_captions import paint_plane
    from moviestar.overlays import plan_overlays
    from moviestar.ffmpeg import build_highlight_ass, build_overlay_filter_parts
    record = cue(highlight=highlight)
    record['style']['resolved'].update(stroke_width=2, stroke_color='#101010', opacity=.8)
    if not highlight:
        record['style']['resolved'].update(background_color='#345678', background_opacity=.6, padding='4px')
    canvas = (640, 360)
    plans = plan_overlays([record], canvas, 0, 3)['plans']
    job = {'plans': plans, 'canvas': canvas, 'fps': 30, 'duration': 3, 'frames': [0, 15, 21, 60]}
    paths = paint_plane(job, tmp_path/'planes')
    ass = None
    if highlight:
        ass_path = tmp_path/'reference.ass'; ass_path.write_text(build_highlight_ass(plans, canvas))
        ass = (str(ass_path), str(Path(plans[0]['font']['path']).parent))
    parts = ['[0:v]select=eq(n\\,0)+eq(n\\,15)+eq(n\\,21)+eq(n\\,60)[base]']
    parts += build_overlay_filter_parts(plans, canvas, 3, input_label='base', output_label='out', highlight_ass=ass)
    result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=0x305070:s=640x360:r=30:d=3,format=rgba', '-filter_complex', ';'.join(parts),
        '-map', '[out]', '-fps_mode', 'vfr', '-frames:v', '4', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'],
        capture_output=True, check=True, timeout=10)
    reference = np.frombuffer(result.stdout, dtype=np.uint8).reshape(4, 360, 640, 4)
    for index, (path, rect) in enumerate(paths):
        with av.open(str(path)) as container:
            crop = next(container.decode(video=0)).to_ndarray(format='rgba').astype(float)
        rgba = np.zeros((360, 640, 4))
        rgba[rect['y']:rect['y']+rect['height'], rect['x']:rect['x']+rect['width']] = crop
        alpha = rgba[:, :, 3:4]/255
        composed = np.rint(rgba[:, :, :3]*alpha + np.array([48, 80, 112])*(1-alpha))
        error = abs(composed-reference[index, :, :, :3].astype(float))
        worst = np.unravel_index(error.argmax(), error.shape)[:2]
        assert error.max() <= 5, (highlight, index, error.max(), worst,
            rgba[worst], composed[worst], reference[index][worst])
        assert (rgba[:, :, 3] == 0).any()


def test_published_caption_assets_survive_worker_cleanup_and_pruning(pip_project, tmp_path, monkeypatch):
    from moviestar import local_preview
    from tests.test_native_preview import _fake_workers
    root = preset(pip_project); monkeypatch.chdir(root)
    folder = tmp_path/'session'; folder.mkdir()
    session = local_preview.PreviewSession(root, 0, 5, folder, mode='native')
    with monkeypatch.context() as fake:
        _fake_workers(fake); session.tick()
    request = folder/session.active['request_id']/'request.json'
    build(request)
    with monkeypatch.context() as fake:
        _fake_workers(fake); session.process.returncode = 0; session.tick()
    packet = json.loads((request.parent/'preview.json').read_text())
    for state in packet['caption_plane']['states']:
        assert state['asset'] in session.assets
        assert Path(session.assets[state['asset']]['path']).is_file()
    monkeypatch.setattr(local_preview, 'RETAINED_REQUESTS', 1)
    spec = read_snapshot(root, 0, 5)['spec']; spec['overlays'][0]['style']['resolved']['font_size'] = 40
    save_spec(spec, cwd=root)
    with monkeypatch.context() as fake:
        _fake_workers(fake); session.tick()
    next_request = folder/session.active['request_id']/'request.json'; build(next_request)
    with monkeypatch.context() as fake:
        _fake_workers(fake); session.process.returncode = 0; session.tick()
    assert not request.parent.exists()
    assert not set(s['asset'] for s in packet['caption_plane']['states']) & set(session.assets)
    assert (root/'camera.mp4').is_file()


def test_unplaced_live_recipe_does_not_block_a_spatial_window(live_project):
    snapshot = read_snapshot(live_project[2], 0, 5)
    snapshot['spec']['captions'] = [{'track': 'not-placed', 'frozen': False}]
    packet, _ = compile_snapshot(snapshot)
    assert packet['samples'][30]['nodes'][2]['content'] == 'Second\nline'


def test_spatial_highlights_are_rejected_instead_of_silently_disappearing(live_project):
    spec, _, root = live_project
    spec['overlays'] = [cue()]; save_spec(spec, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='spatial caption'):
        compile_snapshot(read_snapshot(root, 0, 5))


def test_live_recipe_uses_project_word_corrections(pip_project, monkeypatch):
    root = preset(pip_project); monkeypatch.chdir(root)
    inject_synthetic_transcript(root, [make_word('First', 1, 1.5), make_word('second', 1.7, 3)],
        [make_segment('First second', 1, 3)])
    runner = CliRunner()
    assert runner.invoke(cli, ['captions', 'generate', '--highlight', 'spoken-word']).exit_code == 0
    result = runner.invoke(cli, ['captions', 'rules', 'add', '--replace', 'First=Correct'])
    assert result.exit_code == 0, result.output
    packet, _ = compile_snapshot(read_snapshot(root, 0, 5))
    assert packet['caption_plane']['states'][0]['cues'][0]['text'] == 'Correct second'


def test_preset_cuts_keep_half_open_caption_and_word_boundaries(pip_project):
    spec, _, root = pip_project
    spec.pop('spatial')
    first = spec['composition'][0]
    first['slots'][0]['source_to'] = '0:00:03.000'
    first['slots'][1]['source_to'] = '0:00:04.000'
    second = deepcopy(first); second.update(id='second', name='Second')
    second['slots'][0].update(source_from='0:00:03.000', source_to='0:00:06.000')
    second['slots'][1].update(source_from='0:00:04.000', source_to='0:00:07.000')
    spec['composition'] = [first, second]
    after = cue(); after.update(id='after', text='Next word', **{'from': '0:00:02.000', 'to': '0:00:05.000'})
    after['tokens'] = [{'text': 'Next', 'from': '0:00:02.000', 'to': '0:00:02.200'},
                       {'text': 'word', 'from': '0:00:02.200', 'to': '0:00:05.000'}]
    spec['overlays'] = [cue(), after]; save_spec(spec, cwd=root)
    packet, _ = compile_snapshot(read_snapshot(root, 1, 4))
    left, right = [s['packet']['caption_plane'] for s in packet['segments']]
    assert left['states'][left['frames'][-1]]['cues'][0]['text'] == 'First second'
    assert right['states'][right['frames'][0]]['cues'][0]['text'] == 'Next word'
    assert right['states'][right['frames'][5]]['cues'][0]['active_word'] == 0
    assert right['states'][right['frames'][6]]['cues'][0]['active_word'] == 1


def test_spatial_to_captioned_preset_cut_uses_both_canonical_caption_paths(live_project):
    from tests.test_native_preview_window import surround_scene
    spec, _, root = live_project
    surround_scene(spec)
    spec['composition'][1]['slots'][0]['source_to'] = '0:00:03.000'
    save_spec(spec, cwd=root)
    before = (root/'moviestar/spec.json').read_bytes()
    packet, _ = compile_snapshot(read_snapshot(root, 2, 5))
    spatial, preset = [s['packet'] for s in packet['segments']]
    assert 'caption_plane' not in spatial
    assert spatial['samples'][0]['nodes'][2]['content'] == 'Second\nline'
    assert preset['caption_plane']['states'][0]['cues'][0]['text'] == 'Second\nline'
    assert (root/'moviestar/spec.json').read_bytes() == before
