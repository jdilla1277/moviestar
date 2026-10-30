"""Standard frame commands show the same editable scene as model preview."""

from copy import deepcopy
import base64
import json
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.spatial_layout import browser_available
from moviestar.spec import load_spec, save_spec
from tests.test_spatial_render import project, _rgb
from tests.test_spatial_static import _static
from tests.test_spatial_video import _pixels
from tests.test_spatial_captions import caption_project


def _invoke(args):
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _save(spec, metadata, tmp_path, monkeypatch):
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('static', [False, True])
def test_screenshot_matches_model_preview_and_motion_without_legacy_carrier_pixels(project, tmp_path, monkeypatch, static):
    spec, metadata = project
    if static:
        _static(spec)
        Path(spec['sources'][0]['path']).unlink()
    else:
        spec['spatial']['scenes']['interview']['layout']['children'][0]['motion'] = {
            'translate_x': [{'at': 0, 'value': 0}, {'at': .3, 'value': 15}]}
    _save(spec, metadata, tmp_path, monkeypatch)
    before = Path('moviestar/spec.json').read_bytes()
    expected = _invoke(['composition', 'model', 'preview', '--at', '0:00:00.200', '--out', 'model.png'])
    actual = _invoke(['screenshot', '--at', '.2', '--out', 'shot.png'])
    assert _rgb(Path('shot.png')) == _rgb(Path('model.png'))
    assert actual['spatial_preview']['boxes'] == expected['preview']['boxes']
    assert actual['spatial_frame'] == expected['frame']
    assert actual['width'] == 320 and actual['height'] == 180
    assert Path('moviestar/spec.json').read_bytes() == before


def test_spatial_dry_runs_plan_without_rendering_or_writing(project, tmp_path, monkeypatch):
    spec, metadata = project
    _static(spec)
    _save(spec, metadata, tmp_path, monkeypatch)
    import moviestar.spatial_render as renderer
    import moviestar.spatial_session as worker
    def unexpected(*args, **kwargs):
        raise AssertionError('dry-run performed rendering')
    monkeypatch.setattr(renderer, '_decode', unexpected)
    monkeypatch.setattr(renderer, '_run_browser', unexpected)
    monkeypatch.setattr(worker.BrowserSession, '_start', unexpected)
    before = Path('moviestar/spec.json').read_bytes()
    shot = _invoke(['screenshot', '--at', '.1', '--out', 'shot.png', '--dry-run'])
    assert shot['spatial_render_plan']['scene_id'] == 'title'
    inspected = _invoke(['inspect', '--from', '0', '--to', '.3', '--dry-run'])
    assert inspected['would_reuse_cache'] is False
    assert inspected['spatial_render_plan'][0]['scene_id'] == 'title'
    assert not Path('shot.png').exists()
    assert not Path(inspected['would_write_to']).exists()
    assert Path('moviestar/spec.json').read_bytes() == before


def test_inspect_keeps_exact_scene_boundary_separate_from_display_timecodes(project, tmp_path, monkeypatch):
    spec, metadata = project
    footage = deepcopy(spec['composition'][0])
    footage['slots'][0]['source_to'] = '0:00:00.400'
    _static(spec, .1234)
    spec['composition'].append(footage)
    _save(spec, metadata, tmp_path, monkeypatch)
    result = _invoke(['inspect', '--from', '0', '--to', '.3', '--interval', '1', '--dry-run'])
    first, second = result['spatial_render_plan']
    assert first['scene_id'] == 'title' and first['renderer'] == 'spatial'
    assert second['result_time_s'] == .1234
    assert second['scene_id'] == 'interview' and second['renderer'] == 'preset'


def test_end_frame_owner_uses_unrounded_global_sample(project, tmp_path, monkeypatch):
    spec, metadata = project
    _static(spec, .066667)
    second = deepcopy(spec['composition'][0])
    second.update({'id': 'second', 'name': 'Second', 'duration': .033333})
    spec['composition'].append(second)
    spec['spatial']['scenes']['second'] = deepcopy(spec['spatial']['scenes']['title'])
    _save(spec, metadata, tmp_path, monkeypatch)
    # Last sample is 2/30, just before the cut. Its reported microsecond
    # timestamp rounds to the cut and must not be fed back into selection.
    result = _invoke(['screenshot', '--at', '.1', '--out', 'end.png', '--dry-run'])
    assert result['spatial_frame']['scene_id'] == 'title'
    assert result['active_scene']['scene'] == 'Title'
    assert result['active_scene']['index'] == 0


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_screenshot_default_jpeg_and_platform_annotation_preserve_spatial_pixels(project, tmp_path, monkeypatch):
    spec, metadata = project
    _static(spec)
    _save(spec, metadata, tmp_path, monkeypatch)
    result = _invoke(['screenshot', '--at', '.1', '--inline'])
    image = Path(result['out'])
    assert image.suffix == '.jpg'
    assert image.read_bytes().startswith(b'\xff\xd8')
    assert base64.b64decode(result['image']['base64']) == image.read_bytes()
    assert result['width'] == 320 and result['height'] == 180
    assert max(abs(a-b) for a,b in zip(_rgb(image)[-3:], (16,32,48))) <= 5
    annotated = _invoke(['screenshot', '--at', '.1', '--platform', 'tiktok', '--out', 'platform.png'])
    assert annotated['platform_preview']['annotated'] is True
    assert annotated['spatial_preview']['boxes']
    assert annotated['platform_preview']['known_targets_checked'] >= 1


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_inspect_mixed_static_and_preset_scenes_reports_real_geometry_and_new_images(project, tmp_path, monkeypatch):
    spec, metadata = project
    footage = deepcopy(spec['composition'][0])
    footage['slots'][0]['source_to'] = '0:00:00.400'
    _static(spec)
    spec['composition'].append(footage)
    _save(spec, metadata, tmp_path, monkeypatch)
    before = Path('moviestar/spec.json').read_bytes()
    result = _invoke(['inspect', '--from', '0', '--to', '.8', '--interval', '.2', '--inline'])
    assert result['thumbnails_returned'] == 4
    first, second, third, fourth = result['thumbnails']
    assert first['scene_id'] == 'title'
    assert first['spatial_preview']['boxes'][0]['path'] == ['title', 'headline']
    assert 'spatial_preview' not in third
    assert third['scene'] == 'Interview'
    for thumbnail in result['thumbnails']:
        assert Path(thumbnail['path']).is_file()
        assert len(_rgb(Path(thumbnail['path']))) == 640 * 360 * 3
        assert base64.b64decode(thumbnail['image']['base64']) == Path(thumbnail['path']).read_bytes()
    assert first['spatial_preview']['canvas']['width'] == 320
    assert first['spatial_preview']['image_dimensions']['width'] == 640
    assert result['extraction']['cached'] is False
    assert Path('moviestar/spec.json').read_bytes() == before
    # Replacing an image at the same path must not return a stale thumbnail.
    original = _rgb(Path(first['path']))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=red:s=32x32',
                    '-frames:v', '1', str(tmp_path / 'logo.png')], check=True)
    updated = _invoke(['inspect', '--from', '0', '--to', '.8', '--interval', '.2'])
    assert updated['extraction']['cached'] is False
    assert updated['thumbnails'][0]['path'] != first['path']
    assert _rgb(Path(updated['thumbnails'][0]['path'])) != original
    assert _rgb(Path(first['path'])) == original


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('failure', ['conversion', 'shutdown'])
def test_failed_inspection_batch_removes_partial_files_and_closes_one_browser(project, tmp_path, monkeypatch, failure):
    spec, metadata = project
    _static(spec)
    _save(spec, metadata, tmp_path, monkeypatch)
    import moviestar.spatial_inspection as inspection
    from moviestar.cli import INSPECT_FRAMES_DIR
    prior = _invoke(['inspect', '--from', '0', '--to', '.4', '--interval', '.2'])
    prior_paths = {Path(t['path']): Path(t['path']).read_bytes() for t in prior['thumbnails']}
    original = inspection._convert_image
    sessions = []
    class TrackedSession(inspection.BrowserSession):
        def __init__(self):
            super().__init__()
            sessions.append(self)
        def close(self, *, abort=False):
            super().close(abort=abort)
            if failure == 'shutdown' and not abort:
                raise RuntimeError('browser shutdown failure')
    calls = 0
    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2 and failure == 'conversion':
            raise RuntimeError('late conversion failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(inspection, 'BrowserSession', TrackedSession)
    monkeypatch.setattr(inspection, '_convert_image', fail_second)
    result = CliRunner().invoke(cli, ['inspect', '--from', '0', '--to', '.4', '--interval', '.2'])
    assert result.exit_code == 1
    assert 'failure' in json.loads(result.stdout)['error']
    assert len(sessions) == 1 and sessions[0]._process.poll() is not None
    assert not Path(sessions[0]._profile.name).exists()
    directories = list((tmp_path / 'moviestar' / INSPECT_FRAMES_DIR).iterdir())
    assert directories == [Path(prior['extraction']['frames_dir'])]
    assert all(path.read_bytes() == data for path, data in prior_paths.items())


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_screenshot_source_override_still_uses_the_source_edit(project, tmp_path, monkeypatch):
    spec, metadata = project
    _save(spec, metadata, tmp_path, monkeypatch)
    raw = _invoke(['screenshot', '--source', 'camera', '--at', '.2', '--out', 'source.png'])
    assert 'spatial_preview' not in raw
    assert _rgb(Path('source.png')) == bytes((36,40,60)) * (160*90)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_standard_screenshot_matches_exported_component_pixels(project, tmp_path, monkeypatch):
    spec, metadata = project
    spec['composition'][0]['slots'][0]['source_to'] = '0:00:00.400'
    spec['spatial']['scenes']['interview']['layout']['children'][0]['motion'] = {
        'translate_x': [{'at': 0, 'value': 0}, {'at': .3, 'value': 15}]}
    _save(spec, metadata, tmp_path, monkeypatch)
    shot = _invoke(['screenshot', '--at', '.2', '--out', 'shot.png'])
    _invoke(['export', '--out', 'movie.mp4', '--video-encoder', 'x264', '--quiet'])
    png, video = _rgb(Path('shot.png')), _pixels('movie.mp4', 2)
    points = [(32,16), (310,175)]
    for media in shot['spatial_preview']['media']:
        rect = media['rect']
        points.append((int(rect['x']+4), int(rect['y']+rect['height']-6)))
    for x,y in points:
        offset = (y*320+x)*3
        assert max(abs(a-b) for a,b in zip(png[offset:offset+3], video[offset:offset+3])) <= 12


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_failed_platform_annotation_preserves_prior_screenshot(project, tmp_path, monkeypatch):
    import moviestar.cli as c
    spec, metadata = project
    _static(spec)
    _save(spec, metadata, tmp_path, monkeypatch)
    Path('prior.png').write_bytes(b'prior image')
    def fail(*args, **kwargs):
        raise RuntimeError('platform annotation failed')
    monkeypatch.setattr(c, 'render_platform_preview', fail)
    result = CliRunner().invoke(cli, ['screenshot', '--at', '.1', '--platform', 'tiktok', '--out', 'prior.png'])
    assert result.exit_code == 1
    assert 'platform annotation failed' in json.loads(result.stdout)['error']
    assert Path('prior.png').read_bytes() == b'prior image'


@pytest.mark.parametrize('last_spatial', [True, False])
@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_exact_end_selects_last_global_frame_even_when_final_scene_has_no_sample(project, tmp_path, monkeypatch, last_spatial):
    spec, metadata = project
    # Overall duration .15s at 10 fps: the final output sample is .1s in
    # the first scene, rather than the final scene beginning at .12s.
    first = deepcopy(spec['composition'][0])
    first['slots'][0]['source_to'] = '0:00:00.120'
    _static(spec, .03)
    spec['composition'].insert(0, first)
    if not last_spatial:
        spec['spatial']['scenes']['interview'] = spec['spatial']['scenes'].pop('title')
        spec['composition'][1].pop('duration')
        spec['composition'][1]['slots'] = [{'slot': 'main', 'source': 'camera',
            'source_from': '0:00:00.000', 'source_to': '0:00:00.030'}]
    _save(spec, metadata, tmp_path, monkeypatch)
    end = _invoke(['screenshot', '--at', '.15', '--out', 'end.png'])
    sample = _invoke(['screenshot', '--at', '.1', '--out', 'sample.png'])
    assert end['active_scene']['scene_id'] == 'interview'
    assert _rgb(Path('end.png')) == _rgb(Path('sample.png'))


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('command', ['screenshot', 'inspect'])
def test_standard_frame_commands_refresh_only_placed_live_captions_in_memory(caption_project, tmp_path, monkeypatch, command):
    from tests.conftest import inject_synthetic_transcript, make_segment, make_word
    spec, metadata = caption_project
    metadata['sources'][0]['has_audio'] = True
    for scene in spec['composition']:
        scene['audio_from'] = 'camera'
    spec['composition'][1]['slots'][0].update({'source_from': '0:00:02.000', 'source_to': '0:00:04.000'})
    _save(spec, metadata, tmp_path, monkeypatch)
    inject_synthetic_transcript(tmp_path, [make_word('Opening', .2, .8), make_word('Closing', 2.2, 2.8)],
                                [make_segment('Opening', .2, .8), make_segment('Closing', 2.2, 2.8)])
    _invoke(['captions', 'generate', '--highlight', 'none'])
    spec = load_spec()
    spec['composition'].reverse()
    save_spec(spec)
    before = Path('moviestar/spec.json').read_bytes()
    if command == 'screenshot':
        result = _invoke(['screenshot', '--at', '.5', '--out', 'captions.png'])
        report = result['spatial_preview']
    else:
        result = _invoke(['inspect', '--from', '.5', '--to', '.6'])
        report = result['thumbnails'][0]['spatial_preview']
    caption = next(box for box in report['boxes'] if box['kind'] == 'caption')
    assert caption['caption']['text'] == 'Closing'
    assert Path('moviestar/spec.json').read_bytes() == before


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_failed_frame_conversion_preserves_prior_screenshot(project, tmp_path, monkeypatch):
    spec, metadata = project
    _static(spec)
    _save(spec, metadata, tmp_path, monkeypatch)
    Path('prior.jpg').write_bytes(b'prior image')
    import moviestar.spatial_inspection as inspection
    def broken(*args, **kwargs):
        raise RuntimeError('simulated JPEG conversion failure')
    monkeypatch.setattr(inspection, '_convert_image', broken)
    result = CliRunner().invoke(cli, ['screenshot', '--at', '.1', '--out', 'prior.jpg'])
    assert result.exit_code == 1
    assert 'conversion failure' in json.loads(result.stdout)['error']
    assert Path('prior.jpg').read_bytes() == b'prior image'


@pytest.mark.parametrize('arguments', [['screenshot', '--at', '.1', '--out', 'logo.png'],
                                     ['screenshot', '--at', '.1', '--out', 'bad.gif']])
def test_spatial_screenshot_rejects_asset_overwrite_and_unsupported_format(project, tmp_path, monkeypatch, arguments):
    spec, metadata = project
    _static(spec)
    _save(spec, metadata, tmp_path, monkeypatch)
    before = Path('logo.png').read_bytes()
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 1
    assert json.loads(result.stdout)['error']
    assert Path('logo.png').read_bytes() == before


@pytest.mark.parametrize('command', ['screenshot', 'inspect'])
def test_spatial_frame_commands_reject_incomplete_transition_views(project, tmp_path, monkeypatch, command):
    spec, metadata = project
    _static(spec)
    spec['opening_transition'] = {'type': 'dip-black', 'duration': .2}
    _save(spec, metadata, tmp_path, monkeypatch)
    args = [command, '--at', '.1', '--out', 'prior.png'] if command == 'screenshot' else [command, '--from', '0', '--to', '.3']
    Path('prior.png').write_bytes(b'prior')
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1
    assert 'transition' in json.loads(result.stdout)['error']
    assert Path('prior.png').read_bytes() == b'prior'


@pytest.mark.parametrize('command', ['screenshot', 'inspect'])
def test_active_preset_side_of_mixed_transition_also_requires_complete_video(project, tmp_path, monkeypatch, command):
    from tests.test_spatial_video import _transition_project
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy')
    _save(spec, metadata, tmp_path, monkeypatch)
    args = ([command, '--at', '.4', '--out', 'cut.png'] if command == 'screenshot'
            else [command, '--from', '.4', '--to', '.5'])
    result = CliRunner().invoke(cli, [*args, '--dry-run'])
    assert result.exit_code == 1
    assert 'transition' in json.loads(result.stdout)['error']
