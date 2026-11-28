"""Qualify edits to an already composed interview without implying ripple edits.

Imported captions and external audio deliberately keep result-time positions.
Scene identity owns the reusable layout and its scene-relative motion.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from moviestar.audio_surface import AudioSurfaceValidationError, resolve_audio_mix
from moviestar.cli import _audio_probe_file
from moviestar.ffmpeg import build_mix_audio_command
from moviestar.project import load_project
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import prepare_layout_frame
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spec import load_spec


def _command(root, *args):
    result = subprocess.run([sys.executable, '-m', 'moviestar.cli', *args], cwd=root,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _nodes(children):
    for node in children:
        yield node
        yield from _nodes(node.get('children', []))


def _frame(root, at):
    spec, project = load_spec(root), load_project(root)
    timeline = resolve_project(project, spec)
    return compile_spatial_frame(spec, timeline, at), timeline


def _pictures(frame):
    return [(node['media']['asset_id'], node['media']['source_time_s'])
            for node in _nodes(frame['children']) if node.get('type') == 'video']


@pytest.fixture(scope='module')
def composed_interview(tmp_path_factory):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg required')
    repo = Path(__file__).parents[2]
    sys.path.insert(0, str(repo / 'experiments/live_browser_preview'))
    try:
        from interview import prepare_interview
        root = tmp_path_factory.mktemp('composed-edit') / 'seed'
        prepare_interview(root, canvas='640x360')
    finally:
        sys.path.pop(0)
    # Generated tones make external audio timing independently observable.
    for name, frequency in [('voice', 660), ('music', 220)]:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        f'sine=frequency={frequency}:sample_rate=48000:duration=1',
                        str(root / f'{name}.wav')], check=True, timeout=15)
    _command(root, 'audio', 'source', '--mute')
    _command(root, 'audio', 'add', str(root / 'voice.wav'), '--as', 'voice',
             '--kind', 'voiceover', '--at', '3.5')
    _command(root, 'audio', 'add', str(root / 'music.wav'), '--as', 'music',
             '--kind', 'music', '--loop', '--gain-db', '-18')
    edit = {'op': 'instance.customize',
            'target': {'occurrence_path': ['intro', 'speaker']},
            'property': 'motion.translate_y',
            'value': [{'at': 0, 'value': 0}, {'at': 2, 'value': 20}]}
    (root / 'motion.json').write_text(json.dumps(edit))
    _command(root, 'composition', 'model', 'edit', 'motion.json')
    return root


@pytest.mark.parametrize('edit,duration,scene_at_half,intro_start', [
    ('shorten', 5, 'intro', 0),
    ('reorder', 6, 'reply', 3),
    ('remove', 3, 'reply', None),
])
def test_scene_edits_keep_owned_motion_fixed_tracks_and_exact_undo(
        composed_interview, tmp_path, edit, duration, scene_at_half, intro_start):
    root = tmp_path / 'edit'
    shutil.copytree(composed_interview, root)
    before = load_spec(root)
    document = json.loads((root / 'scenes-640x360.json').read_text())
    if edit == 'shorten':
        document['scenes'][0]['slots'][0]['to'] = '3'  # source 1..3: two seconds
    elif edit == 'reorder':
        document['scenes'].reverse()
    else:
        document['scenes'].pop(0)
    (root / 'changed.json').write_text(json.dumps(document))
    baseline_bytes = (root / 'moviestar/spec.json').read_bytes()
    _command(root, 'scenes', 'set', 'changed.json', '--dry-run')
    assert (root / 'moviestar/spec.json').read_bytes() == baseline_bytes
    output = _command(root, 'scenes', 'set', 'changed.json')
    after = load_spec(root)
    frame, timeline = _frame(root, .5)
    assert timeline.duration_s == duration
    assert timeline.span_at(.5).scene_id == scene_at_half
    expected, _ = _frame(composed_interview, .5 if scene_at_half == 'intro' else 3.5)
    assert _pictures(frame) == _pictures(expected), 'Pictures follow the retained scene identity'
    assert after['audio_mix'] == before['audio_mix'], 'External audio keeps explicit result time'
    assert after['overlays'] == before['overlays'], 'Imported cues keep explicit result time'
    caption = next(n['caption'] for n in _nodes(frame['children']) if n.get('type') == 'caption')
    assert caption['text'] == 'Alice opens the discussion.'
    warning = next(w for w in output['warnings']
                   if w['code'] == 'scene_result_clock_changed_overlays_stale')
    assert warning['caption_cues_count'] == 4
    if intro_start is not None:
        moving, _ = _frame(root, intro_start + .5)
        prepared = prepare_layout_frame(moving, timeline.canvas)
        speaker = next(n for n in prepared['nodes'] if n.get('motion'))
        assert speaker['motion']['scene_time_s'] == .5
        assert speaker['motion']['translate_y'] == '5px'
        # Shortening doesn't squeeze the two-second animation into the new length.
        assert after['spatial']['scenes']['intro'] == before['spatial']['scenes']['intro']
    else:
        assert 'intro' not in after['spatial']['scenes']
    _command(root, 'undo')
    assert load_spec(root) == before, 'Undo restores media, captions, mix, motion and history together'


def test_source_cut_does_not_remove_a_finished_composition_interval(composed_interview, tmp_path):
    root = tmp_path / 'cut'
    shutil.copytree(composed_interview, root)
    before = load_spec(root)
    expected, _ = _frame(root, 1)
    _command(root, 'cut', '--source', 'alice', '--from', '1.5', '--to', '2')
    frame, timeline = _frame(root, 1)
    assert timeline.duration_s == 6
    assert _pictures(frame) == _pictures(expected)
    assert load_spec(root)['composition'] == before['composition']
    _command(root, 'undo')
    assert load_spec(root) == before


def test_shortened_mix_renders_voice_at_explicit_time_and_music_to_new_end(composed_interview, tmp_path):
    root = tmp_path / 'audio'
    shutil.copytree(composed_interview, root)
    document = json.loads((root / 'scenes-640x360.json').read_text())
    document['scenes'][0]['slots'][0]['to'] = '3'
    (root / 'changed.json').write_text(json.dumps(document))
    _command(root, 'scenes', 'set', 'changed.json')
    spec = load_spec(root)
    mix = resolve_audio_mix(spec['audio_mix'], result_duration=5, base_dir=root,
                            probe_audio=_audio_probe_file)
    # Use export's actual mix graph, not a mocked placement or command assertion.
    output = root / 'mixed.mp4'
    base = root / 'base.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i',
                    str(composed_interview / 'media/alice.mp4'), '-t', '5',
                    '-c:v', 'copy', '-an', str(base)], check=True, timeout=15)
    command = build_mix_audio_command(str(base), str(output), mix, 5,
                                      source_audio_available=False)
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 'f32le',
                              '-ac', '1', '-ar', '48000', '-t', '5', 'pipe:1'],
                             check=True, capture_output=True, timeout=15)
    samples = np.frombuffer(decoded.stdout, dtype='<f4')
    assert len(samples) == 5 * 48000
    def amplitude(at, frequency):
        start = int(at * 48000)
        window = samples[start:start + 4800]
        tone = np.exp(-2j * np.pi * frequency * np.arange(len(window)) / 48000)
        return abs(np.dot(window, tone)) * 2 / len(window)
    assert amplitude(2.6, 660) < .001, 'Voice has not automatically moved one second earlier'
    assert amplitude(3.6, 660) > .1, 'Voice retains authored 3.5-second placement'
    assert amplitude(4.8, 220) > .01, 'Looped music survives through the shortened ending'


@pytest.mark.xfail(strict=True, raises=AudioSurfaceValidationError,
                   reason='https://github.com/jdilla1277/moviestar/issues/330: scene removal saves an invalid mix')
def test_successful_scene_removal_leaves_a_resolvable_mix(composed_interview, tmp_path):
    root = tmp_path / 'validity'
    shutil.copytree(composed_interview, root)
    before = (root / 'moviestar/spec.json').read_bytes()
    document = json.loads((root / 'scenes-640x360.json').read_text())
    document['scenes'].pop(0)
    (root / 'changed.json').write_text(json.dumps(document))
    result = subprocess.run([sys.executable, '-m', 'moviestar.cli', 'scenes', 'set', 'changed.json'],
                            cwd=root, capture_output=True, text=True, timeout=45)
    if result.returncode:
        # Rejecting the prospective invalid edit atomically is also acceptable.
        assert 'audio' in result.stdout.lower()
        assert (root / 'moviestar/spec.json').read_bytes() == before
        return
    spec = load_spec(root)
    resolve_audio_mix(spec['audio_mix'], result_duration=3, base_dir=root,
                      probe_audio=_audio_probe_file)
