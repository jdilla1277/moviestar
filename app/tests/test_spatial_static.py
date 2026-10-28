"""Explicit scene time supports visual trees without a carrier footage slot."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spec import SpecValidationError, load_spec, save_spec, validate_spec
from tests.test_spatial_render import project, _rgb
from tests.test_spatial_video import video_project, _pixels, _rms


def _static(spec, duration=.4):
    spec['composition'] = [{'id': 'title', 'name': 'Title', 'layout': {'preset': 'single'},
                            'duration': duration, 'slots': []}]
    spec['spatial']['scenes'] = {'title': {'layout': {'background_color': '#102030', 'children': [
        {'id': 'headline', 'type': 'text', 'content': {'text': 'Hello'},
         'layout': {'width': 120, 'height': 40}, 'style': {'font_size': 20, 'color': '#ffffff'}},
        {'id': 'logo', 'type': 'image', 'content': {'asset_id': 'logo'},
         'layout': {'position': 'absolute', 'left': 120, 'top': 60, 'width': 32, 'height': 32}},
    ]}}}
    return spec


def _invoke(args):
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_static_scene_owns_time_and_survives_save_reload(project, tmp_path):
    spec, metadata = project
    _static(spec, 3.25)
    save_spec(spec, cwd=tmp_path)
    timeline = resolve_project(metadata, load_spec(cwd=tmp_path))
    assert timeline.duration_s == 3.25
    assert timeline.output_fps == 30
    assert timeline.spans[0].slots == ()
    assert timeline.spans[0].audio.source_id is None
    frame = compile_spatial_frame(spec, timeline, 2.5)
    assert frame['scene_time_s'] == frame['scene_source_time_s'] == 2.5
    assert frame['scene_duration_s'] == frame['scene_source_duration_s'] == 3.25
    assert frame['diagnostics'] == []


@pytest.mark.parametrize('duration', [None, True, 0, -1, '4', float('inf'), float('nan'), .00000001])
def test_invalid_explicit_duration_preserves_saved_spec(project, tmp_path, duration):
    spec, _ = project
    save_spec(spec, cwd=tmp_path)
    path = tmp_path / 'moviestar' / 'spec.json'
    before = path.read_bytes()
    _static(spec, duration)
    with pytest.raises(SpecValidationError, match='duration'):
        save_spec(spec, cwd=tmp_path)
    assert path.read_bytes() == before


def test_empty_slots_require_explicit_timing_and_spatial_scene(project):
    spec, _ = project
    _static(spec)
    del spec['composition'][0]['duration']
    with pytest.raises(SpecValidationError, match='duration'):
        validate_spec(spec)
    spec['composition'][0]['duration'] = .4
    del spec['spatial']['scenes']['title']
    with pytest.raises(SpecValidationError, match='spatial'):
        validate_spec(spec)


def test_explicit_duration_cannot_silently_override_legacy_slot_time(project):
    spec, _ = project
    spec['composition'][0]['duration'] = .4
    with pytest.raises(SpecValidationError, match='duration'):
        validate_spec(spec)


def test_static_scene_cannot_route_implicit_source_audio(project):
    spec, _ = project
    _static(spec)
    spec['composition'][0]['audio_from'] = 'camera'
    with pytest.raises(SpecValidationError, match='audio_from'):
        validate_spec(spec)


def test_slot_free_scene_maps_bound_video_at_one_times_scene_time(project):
    spec, metadata = project
    _static(spec, .6)
    spec['spatial']['scenes']['title']['layout']['children'] = [
        {'id': 'short', 'type': 'video', 'content': {'asset_id': 'camera', 'source': {'start': .2, 'end': .6}},
         'layout': {'width': 100, 'height': 60}},
        {'id': 'long', 'type': 'video', 'content': {'asset_id': 'camera', 'source': {'start': .5, 'end': 2}},
         'layout': {'width': 100, 'height': 60}},
    ]
    timeline = resolve_project(metadata, spec)
    early = compile_spatial_frame(spec, timeline, .3, source_durations={'camera': 2})
    assert [n['media']['source_time_s'] for n in early['children']] == [.5, .8]
    late = compile_spatial_frame(spec, timeline, .5, source_durations={'camera': 2})
    assert [n['media']['visible'] for n in late['children']] == [False, True]
    assert late['children'][1]['media']['source_time_s'] == 1
    assert timeline.duration_s == .6
    assert timeline.spans[0].audio.source_id is None
    assert late['diagnostics'][0]['code'] == 'media_ends_before_scene'


def test_file_authoring_creates_blank_layout_and_preserves_identity_undo(project, tmp_path, monkeypatch):
    spec, metadata = project
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    document = {'canvas': '320x180', 'scenes': [
        {'id': 'card', 'name': 'Card', 'layout': 'single', 'slots': [], 'duration': .45}]}
    Path('scenes.json').write_text(json.dumps(document))
    original = load_spec()
    _invoke(['scenes', 'set', 'scenes.json', '--dry-run'])
    assert load_spec() == original
    _invoke(['scenes', 'set', 'scenes.json'])
    saved = load_spec()
    assert saved['composition'][0]['duration'] == .45
    assert saved['composition'][0]['id'] == 'card'
    assert saved['spatial']['scenes']['card']['layout']['children'] == []
    assert saved['spatial']['scenes']['card']['layout']['background_color'] == '#000000'
    listed = _invoke(['scenes', 'list'])
    assert listed['scenes'][0]['result_range']['duration']['seconds'] == .45
    visual = saved['spatial']
    visual['scenes']['card']['layout']['children'] = [
        {'id': 'headline', 'type': 'text', 'content': {'text': 'Keep this design'}}]
    Path('visual.json').write_text(json.dumps(visual))
    _invoke(['composition', 'model', 'apply', 'visual.json'])
    saved = load_spec()
    document['scenes'][0].update({'name': 'Renamed', 'duration': .6})
    Path('scenes.json').write_text(json.dumps(document))
    _invoke(['scenes', 'set', 'scenes.json'])
    assert load_spec()['composition'][0]['id'] == 'card'
    assert load_spec()['spatial'] == saved['spatial']
    _invoke(['undo'])
    assert load_spec() == saved


@pytest.mark.parametrize('existing_spatial', ['missing', None])
def test_file_authoring_assigns_ids_without_existing_spatial_document(project, tmp_path, monkeypatch, existing_spatial):
    spec, metadata = project
    if existing_spatial == 'missing':
        del spec['spatial']
    else:
        spec['spatial'] = None
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    Path('scenes.json').write_text(json.dumps({'canvas': '320x180', 'scenes': [
        {'name': 'Auto', 'layout': 'single', 'slots': [], 'duration': .4},
        {'id': 'scene_0001', 'name': 'Explicit', 'layout': 'single', 'slots': [], 'duration': .5}]}))
    _invoke(['scenes', 'set', 'scenes.json'])
    saved = load_spec()
    ids = [s['id'] for s in saved['composition']]
    assert len(set(ids)) == 2
    assert ids[1] == 'scene_0001'
    assert set(ids) == set(saved['spatial']['scenes'])


@pytest.mark.parametrize('args', [['screenshot', '--at', '.1', '--out', 'prior.png'],
                                 ['inspect', '--from', '0', '--to', '.3']])
def test_legacy_frame_commands_direct_slot_free_scenes_to_model_preview(project, tmp_path, monkeypatch, args):
    spec, metadata = project
    _static(spec)
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    Path('prior.png').write_bytes(b'prior frame')
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1, result.output
    assert 'composition model preview' in json.loads(result.stdout)['hint']
    assert Path('prior.png').read_bytes() == b'prior frame'


def test_removing_required_static_tree_fails_atomically(project, tmp_path, monkeypatch):
    spec, metadata = project
    _static(spec)
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    before = Path('moviestar/spec.json').read_bytes()
    Path('remove.json').write_text(json.dumps({'scenes': {}}))
    result = CliRunner().invoke(cli, ['composition', 'model', 'apply', 'remove.json'])
    assert result.exit_code == 1
    assert 'slot-free scene requires a spatial scene layout' in json.loads(result.stdout)['error']
    assert Path('moviestar/spec.json').read_bytes() == before


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_static_preview_watch_and_export_need_no_source_file(project, tmp_path, monkeypatch):
    spec, metadata = project
    _static(spec)
    # No access to this unused footage is necessary, even though the loaded
    # project retains its source entry. The logo is a real non-video asset.
    Path(spec['sources'][0]['path']).unlink()
    tree = spec['spatial']['scenes']['title']['layout']
    spec['spatial']['definitions']['text_panel'] = {
        'name': 'TextPanel', 'inputs': [], 'children': tree['children']}
    tree['children'] = [{'instance_id': 'panel', 'definition_id': 'text_panel',
        'layout': {'width': '100%', 'height': '100%'},
        'motion': {'translate_x': [{'at': 0, 'value': 0}, {'at': .3, 'value': 30}]}}]
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    original = load_spec()
    dry = _invoke(['export', '--out', 'movie.mp4', '--dry-run', '--video-encoder', 'x264'])
    assert dry['output_fps'] == 30
    assert dry['spatial_render_plan'][0]['frame_count'] == 12
    result = _invoke(['export', '--out', 'movie.mp4', '--video-encoder', 'x264', '--quiet'])
    assert result['actual_duration']['seconds'] == pytest.approx(.4, abs=.001)
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', 'movie.mp4']))
    assert [s['codec_type'] for s in probe['streams']] == ['video']
    _invoke(['watch', '--from', '.1', '--to', '.3', '--out', 'watch.mp4', '--video-encoder', 'x264'])
    preview = _invoke(['composition', 'model', 'preview', '--at', '0:00:00.200', '--out', 'frame.png', '--layout-out', 'boxes.png'])
    headline = next(box for box in preview['preview']['boxes'] if box['path'][-1] == 'headline')
    assert headline['rect']['x'] == pytest.approx(headline['base_rect']['x'] + 20, abs=.01)
    assert Path('boxes.png').is_file()
    exported = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', 'movie.mp4', '-vf', r'select=eq(n\,6)',
                                       '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
    # An unchanged background patch proves the scene pixels reached MP4.
    assert max(abs(a-b) for a,b in zip(exported[-3:], _rgb(Path('frame.png'))[-3:])) <= 4
    # The logo rides with its non-video component, on the same scene clock in
    # preview, whole export, and a watch window beginning inside the scene.
    offset = (75 * 320 + 164) * 3
    for pixels in [exported, _pixels('watch.mp4', 3), _rgb(Path('frame.png'))]:
        assert max(abs(a-b) for a,b in zip(pixels[offset:offset+3], (255, 255, 0))) <= 8
    assert load_spec() == original


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('kind', ['dissolve', 'dip-black', 'dip-white'])
@pytest.mark.parametrize('static_first', [True, False])
def test_static_and_footage_scenes_share_transition_and_audio_pipeline(video_project, tmp_path, monkeypatch, kind, static_first):
    spec, metadata = video_project
    footage = deepcopy(spec['composition'][0])
    footage['slots'][0].update({'source_from': '0:00:00.200', 'source_to': '0:00:00.600'})
    _static(spec)
    spec['composition'].append(footage)
    if not static_first:
        spec['composition'].reverse()
    spec['composition'][1]['transition_in'] = {'type': kind, 'duration': .2}
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    result = _invoke(['export', '--out', 'mixed.mp4', '--quiet', '--video-encoder', 'x264'])
    assert result['composition_duration']['seconds'] == .8
    assert result['output_fps'] == 10
    assert result['transition_render_plan'][0]['type'] == kind
    offset = (160 * 320 + 300) * 3
    pixel = _pixels('mixed.mp4', 4)[offset:offset+3]
    expected = {'dissolve': ((26, 36, 54) if static_first else (42, 36, 54)),
                'dip-black': (0, 0, 0), 'dip-white': (255, 255, 255)}[kind]
    assert max(abs(a-b) for a,b in zip(pixel, expected)) <= 8
    assert _rms(Path('mixed.mp4'), start=.05 if static_first else .45, duration=.2) < .001
    assert _rms(Path('mixed.mp4'), start=.45 if static_first else .05, duration=.2) > .05
