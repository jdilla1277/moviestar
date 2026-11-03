"""Starting points render through the same inspection/export path as custom work."""

from copy import deepcopy

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.resolved import resolve_project
from moviestar.spatial_catalog import apply_builtin_layout
from moviestar.spatial_layout import browser_available, measure_spatial_layout
from moviestar.spatial_model import compile_spatial_frame, edit_spatial_model
from moviestar.spatial_render import render_spatial_frame
from moviestar.spec import load_spec, save_spec
from tests.test_spatial_render import project, _rgb
from tests.test_spatial_video import _pixels, _rms, video_project


BINDINGS = {
    'single': {'video': {'asset_id': 'camera'}},
    'two-up': {'left': {'asset_id': 'camera'}, 'right': {'asset_id': 'camera'}},
    'pip': {'main': {'asset_id': 'camera'}, 'inset': {'asset_id': 'camera'}},
    'text-video': {'video': {'asset_id': 'camera'}, 'title': {'text': 'A new chapter'},
                   'body': {'text': 'Editable text beside footage.'}, 'image': {'asset_id': 'logo'}},
}


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('identity', ['single', 'two-up', 'pip', 'text-video'])
@pytest.mark.parametrize('canvas', [{'width': 640, 'height': 360}, {'width': 360, 'height': 640}])
def test_all_starting_layouts_render_and_inspect_the_same_boxes(project, tmp_path, identity, canvas):
    spec, metadata = project
    spec['spatial'].pop('scenes')
    spec, report = apply_builtin_layout(spec, identity, 'interview', BINDINGS[identity])
    assert not report['diagnostics']
    before = deepcopy(spec)
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .5, source_durations={'camera': 2})
    output, reference = tmp_path / 'render.png', tmp_path / 'reference.png'
    rendered = render_spatial_frame(spec, frame, canvas, output_path=output,
        layout_output_path=tmp_path / 'labels.png', reference_path=reference, project_root=tmp_path)
    measured = measure_spatial_layout(frame, canvas)
    assert [(b['path'], b['rect']) for b in rendered['boxes']] == [(b['path'], b['rect']) for b in measured['boxes']]
    assert max(abs(a-b) for a,b in zip(_rgb(output), _rgb(reference))) <= 3
    assert not [d for d in measured['diagnostics'] if d.get('severity') == 'error' or d['code'] == 'text_overflow']
    videos = [b for b in measured['boxes'] if b['kind'] == 'video']
    assert len(videos) == (2 if identity in ('pip', 'two-up') else 1)
    for box in videos:
        rect = box['rect']
        assert rect['width'] > 0 and rect['height'] > 0
        assert rect['x'] >= -0.1 and rect['y'] >= -0.1
        assert rect['x'] + rect['width'] <= canvas['width'] + .1
        assert rect['y'] + rect['height'] <= canvas['height'] + .1
    if identity == 'two-up':
        left, right = videos
        if canvas['height'] > canvas['width']:
            assert right['rect']['y'] > left['rect']['y'] + left['rect']['height']
        else:
            assert right['rect']['x'] > left['rect']['x'] + left['rect']['width']
    if identity == 'text-video':
        title = next(b for b in measured['boxes'] if b['path'][-1] == 'title')
        assert title['text_lines']
    assert spec == before


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_draft_layout_can_be_measured_without_assets_then_repaired(project, tmp_path):
    spec, metadata = project
    spec['spatial']['scenes'] = {}
    draft, _ = apply_builtin_layout(spec, 'text-video', 'interview')
    frame = compile_spatial_frame(draft, resolve_project(metadata, draft), .5)
    measured = measure_spatial_layout(frame, {'width': 640, 'height': 360}, output_path=tmp_path/'draft.png')
    assert {d['input'] for d in measured['diagnostics'] if d['code'] == 'missing_binding'} == {'footage','title','body'}
    repaired, _ = apply_builtin_layout(draft, 'text-video', 'interview', BINDINGS['text-video'], replace=True)
    frame = compile_spatial_frame(repaired, resolve_project(metadata, repaired), .5, source_durations={'camera': 2})
    render_spatial_frame(repaired, frame, {'width': 640, 'height': 360}, output_path=tmp_path/'ready.png', project_root=tmp_path)
    assert (tmp_path/'ready.png').exists()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_builtin_pip_export_keeps_one_audio_route_and_matches_screenshot(video_project, tmp_path):
    spec, metadata = video_project
    spec['spatial']['scenes'] = {}
    spec, _ = apply_builtin_layout(spec, 'pip', 'interview', BINDINGS['pip'])
    save_spec(spec)
    before = (tmp_path/'moviestar/spec.json').read_bytes()
    runner = CliRunner()
    for args in [['screenshot', '--at', '.2', '--out', 'still.png'],
                 ['export', '--out', 'movie.mp4', '--video-encoder', 'x264']]:
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
    actual, expected = _pixels(tmp_path/'movie.mp4', 2), _rgb(tmp_path/'still.png')
    # Compare footage interiors using the established H.264 patch tolerance;
    # the PNG/reference tests above independently cover borders and clipping.
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .2, source_durations={'camera': 2})
    measured = measure_spatial_layout(frame, {'width': 320, 'height': 180})
    for box in measured['boxes']:
        if box['kind'] != 'video':
            continue
        rect = box['rect']
        x, y = int(rect['x'] + rect['width']/2), int(rect['y'] + rect['height']/2)
        offset = (y*320+x)*3
        assert max(abs(a-b) for a,b in zip(actual[offset:offset+3], expected[offset:offset+3])) <= 12
    assert _rms(tmp_path/'movie.mp4') == pytest.approx(_rms(tmp_path/'with-audio.mkv'), rel=.06)
    assert load_spec()['composition'][0]['audio_from'] == 'camera'
    assert (tmp_path/'moviestar/spec.json').read_bytes() == before


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_text_panel_remains_a_reusable_component_without_video(project, tmp_path):
    spec, metadata = project
    spec['spatial']['scenes'] = {}
    values = {key: value for key, value in BINDINGS['text-video'].items() if key != 'video'}
    spec, report = apply_builtin_layout(spec, 'text-video', 'interview', values)
    speaker = report['input_targets']['video']['occurrence_path']
    spec, _ = edit_spatial_model(spec, {'op': 'instance.child_remove', 'target': {'occurrence_path': speaker}})
    # The unused timeline source need not decode to render text, shapes and an image.
    from pathlib import Path
    Path(spec['sources'][0]['path']).unlink()
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .5)
    assert not frame['diagnostics']
    result = render_spatial_frame(spec, frame, {'width': 640, 'height': 360}, output_path=tmp_path/'text-only.png', project_root=tmp_path)
    assert not [box for box in result['boxes'] if box['kind'] == 'video']
    assert any(box['path'][-1] == 'title' and box['text_lines'] for box in result['boxes'])


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('bound', [False, True])
def test_catalog_caption_region_is_explicit_and_renders_once(project, tmp_path, bound):
    from tests.test_spatial_captions import _cue
    spec, metadata = project
    spec['spatial']['scenes'] = {}
    spec['overlays'] = [_cue('one', '0:00:00.000', '0:00:01.000', 'A caption')]
    bindings = deepcopy(BINDINGS['single'])
    if bound:
        bindings['captions'] = {'track': 'captions'}
    spec, _ = apply_builtin_layout(spec, 'single', 'interview', bindings)
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .5, source_durations={'camera': 2})
    result = render_spatial_frame(spec, frame, {'width': 640, 'height': 360}, output_path=tmp_path/'caption.png', project_root=tmp_path)
    captions = [box for box in result['boxes'] if box['kind'] == 'caption']
    assert len(captions) == 1
    assert bool(captions[0]['text_lines']) is bound
    assert captions[0]['caption']['text'] == ('A caption' if bound else '')
