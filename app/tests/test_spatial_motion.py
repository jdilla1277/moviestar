"""Scene-time movement shares layout, pixels, and reusable occurrence identity."""

from copy import deepcopy
import json
import subprocess

from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project

import pytest

from moviestar.resolved import resolve_project
from moviestar.spatial_layout import SpatialLayoutError, browser_available, measure_spatial_layout, prepare_layout_frame
from moviestar.spatial_model import compile_spatial_frame, edit_spatial_model
from moviestar.spec import SpecValidationError, load_spec, pop_revision, save_spec, validate_spec
from moviestar.spatial_render import render_spatial_frame
from tests.test_spatial_captions import caption_project
from tests.test_spatial_render import project, _frame as _render_frame, _rgb
from tests.test_spatial_layout import _project, _source_project


def _track(start, end):
    return [{'at': 1, 'value': start}, {'at': 3, 'value': end}]


def _moving_project():
    spec = _project()
    instance = spec['spatial']['scenes']['scene_a']['layout']['children'][0]
    instance['motion'] = {'translate_x': _track('-100%', '0%'),
                          'translate_y': _track(0, 40), 'scale': _track(1, 1.5),
                          'opacity': _track(0, 1)}
    return spec


def _frame(spec, at):
    return compile_spatial_frame(spec, resolve_project(_source_project(spec), spec), at)


@pytest.mark.parametrize('at,x,y,scale,opacity', [
    (0, '-100%', '0px', 1, 0), (1, '-100%', '0px', 1, 0),
    (2, '-50%', '20px', 1.25, .5), (3, '0%', '40px', 1.5, 1),
    (4, '0%', '40px', 1.5, 1)])
def test_motion_samples_scene_time_without_mutating_authored_tracks(at, x, y, scale, opacity):
    spec = _moving_project()
    before = deepcopy(spec)
    frame = _frame(spec, at)
    motion = prepare_layout_frame(frame, {'width': 640, 'height': 360})['nodes'][0]['motion']
    assert motion == {'scene_time_s': at, 'translate_x': x, 'translate_y': y,
                      'scale': scale, 'opacity': opacity}
    assert frame['children'][0]['motion'] == spec['spatial']['scenes']['scene_a']['layout']['children'][0]['motion']
    assert spec == before


@pytest.mark.parametrize('motion', [
    None, [], {'rotate': _track(0, 90)}, {'scale': []}, {'scale': [{'at': 0, 'value': 0}]},
    {'opacity': _track(0, 2)}, {'opacity': _track(True, 1)},
    {'translate_x': _track('0%', 100)}, {'translate_x': _track('0px', 'calc(10px)')},
    {'translate_y': [{'at': -1, 'value': 0}]},
    {'scale': [{'at': 1, 'value': 1}, {'at': 1, 'value': 2}]},
    {'scale': [{'at': 2, 'value': 1}, {'at': 1, 'value': 2}]},
    {'scale': [{'at': 0, 'value': float('nan')}]},
    {'scale': [{'at': 0, 'value': 1, 'easing': 'surprise'}]},
])
def test_invalid_motion_fails_when_saving_not_only_when_rendering(motion):
    spec = _project()
    spec['spatial']['scenes']['scene_a']['layout']['children'][0]['motion'] = motion
    with pytest.raises(SpecValidationError, match='motion'):
        validate_spec(spec)


@pytest.mark.parametrize('owner', ['definition', 'scene'])
def test_motion_belongs_on_rendered_nodes(owner):
    spec = _project()
    target = (spec['spatial']['definitions']['pair'] if owner == 'definition'
              else spec['spatial']['scenes']['scene_a']['layout'])
    target['motion'] = {'scale': _track(1, 2)}
    with pytest.raises(SpecValidationError, match='motion.*child.*instance'):
        validate_spec(spec)


def test_motion_customization_shared_edits_reset_and_saved_variation(tmp_path):
    spec = _moving_project()
    definition = spec['spatial']['definitions']['pair']
    definition['children'][0]['motion'] = {'translate_y': _track(0, 20)}
    target = {'occurrence_path': ['scene_a', 'listeners', 'left_video']}
    custom = {'op': 'instance.customize', 'target': target,
              'property': 'motion.translate_y', 'value': _track(0, 80)}
    spec, _ = edit_spatial_model(spec, custom)
    spec, _ = edit_spatial_model(spec, {'op': 'component.update',
        'target': {'definition_id': 'pair', 'path': ['left_video']},
        'property': 'motion.translate_y', 'value': _track(0, 40)})
    child = _frame(spec, 2)['children'][0]['children'][0]
    assert child['motion']['translate_y'] == _track(0, 80)
    save_spec(spec, cwd=tmp_path)
    assert load_spec(cwd=tmp_path) == spec
    reset, _ = edit_spatial_model(spec, {'op': 'instance.reset', 'target': target,
                                        'property': 'motion.translate_y'})
    assert _frame(reset, 2)['children'][0]['children'][0]['motion']['translate_y'] == _track(0, 40)
    varied, _ = edit_spatial_model(spec, {'op': 'component.save_variation',
        'source': {'occurrence_path': ['scene_a', 'listeners']},
        'new_definition_id': 'custom_pair', 'relink_source': True})
    assert _frame(varied, 2)['children'][0]['children'][0]['motion'] == child['motion']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('canvas', [{'width': 640, 'height': 360}, {'width': 360, 'height': 640}])
@pytest.mark.parametrize('at', [0, 2, 4])
def test_group_and_child_motion_follow_responsive_base_boxes(canvas, at):
    spec = _moving_project()
    layout = spec['spatial']['scenes']['scene_a']['layout']
    layout['format_rules'] = [{'id': 'portrait', 'when': {'orientation': 'portrait'},
                              'layout': {'columns': '1fr'}}]
    spec['spatial']['definitions']['pair']['children'][0]['motion'] = {'translate_y': _track(0, 20)}
    frame = _frame(spec, at)
    result = measure_spatial_layout(frame, canvas)
    group, child = result['boxes'][:2]
    scale = 1 if at == 0 else 1.25 if at == 2 else 1.5
    x_fraction = -1 if at == 0 else -.5 if at == 2 else 0
    y = 0 if at == 0 else 20 if at == 2 else 40
    assert group['rect']['x'] == pytest.approx(group['base_rect']['x'] + x_fraction * group['base_rect']['width'], abs=.01)
    assert group['rect']['y'] == group['base_rect']['y'] + y
    assert group['rect']['width'] == pytest.approx(group['base_rect']['width'] * scale, abs=.01)
    assert child['rect']['y'] == pytest.approx(group['rect']['y'] +
        (child['base_rect']['y'] - group['base_rect']['y'] + y / 2) * scale, abs=.01)
    assert child['effective_opacity'] == (0 if at == 0 else .5 if at == 2 else 1)
    assert group['motion']['scene_time_s'] == at
    assert not result['diagnostics']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('canvas', [{'width': 320, 'height': 180}, {'width': 180, 'height': 320}])
@pytest.mark.parametrize('at', [0, .5, 1])
def test_moving_nested_components_keep_footage_graphics_and_inspection_aligned(project, tmp_path, canvas, at):
    spec, metadata = project
    layout = spec['spatial']['scenes']['interview']['layout']
    layout['format_rules'] = [{'id': 'stack', 'when': {'orientation': 'portrait'},
                              'layout': {'columns': '1fr', 'rows': '1fr 1fr'}}]
    listeners = layout['children'][1]
    listeners['motion'] = {'translate_x': [{'at': 0, 'value': '-10%'}, {'at': 1, 'value': '10%'}],
                           'translate_y': [{'at': 0, 'value': -10}, {'at': 1, 'value': 15}],
                           'scale': [{'at': 0, 'value': .75}, {'at': 1, 'value': 1.25}],
                           'opacity': [{'at': 0, 'value': 0}, {'at': 1, 'value': .7}]}
    spec['spatial']['definitions']['card']['children'][0]['children'][0]['motion'] = {
        'translate_x': [{'at': 0, 'value': -8}, {'at': 1, 'value': 12}],
        'scale': [{'at': 0, 'value': 1}, {'at': 1, 'value': 1.1}]}
    layout['children'][2]['motion'] = {'translate_y': [{'at': 0, 'value': 0}, {'at': 1, 'value': 50}]}
    frame = _render_frame(spec, metadata, at)
    output, reference, labels = (tmp_path / name for name in ('moving.png', 'reference.png', 'labels.png'))
    actual = render_spatial_frame(spec, frame, canvas, output_path=output, project_root=tmp_path,
                                  reference_path=reference, layout_output_path=labels)
    assert max(abs(a - b) for a, b in zip(_rgb(output), _rgb(reference))) <= 3
    assert actual['boxes'] == measure_spatial_layout(frame, canvas)['boxes']
    assert sorted(item['source_time_s'] for item in actual['media']) == [at, round(at + .2, 6), round(at + .4, 6)]
    assert labels.is_file()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('scale,offset', [(1.25, 15), (.73, -15.3)])
def test_scaled_patterned_video_matches_browser_sampling(project, tmp_path, scale, offset):
    spec, metadata = project
    video = tmp_path / 'patterned.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi',
                    '-i', 'testsrc2=size=160x90:rate=10:duration=2', '-c:v', 'ffv1', str(video)], check=True)
    spec['sources'][0]['path'] = str(video)
    layout = spec['spatial']['scenes']['interview']['layout']
    layout['children'] = layout['children'][:1]
    layout['children'][0]['layout'] = {'position': 'absolute', 'left': 24, 'top': 10, 'width': 220, 'height': 150}
    layout['children'][0]['motion'] = {'scale': [{'at': 0, 'value': scale}],
                                      'translate_x': [{'at': 0, 'value': offset}],
                                      'opacity': [{'at': 0, 'value': .65}]}
    output, reference = tmp_path / 'actual.png', tmp_path / 'reference.png'
    render_spatial_frame(spec, _render_frame(spec, metadata), {'width': 320, 'height': 180},
                          output_path=output, reference_path=reference, project_root=tmp_path)
    assert max(abs(a - b) for a, b in zip(_rgb(output), _rgb(reference))) <= 3


@pytest.mark.parametrize('pacing,at,source_time', [
    ({'id': 'slow', 'mode': 'speed', 'range': {'from': '0:00:00.000', 'to': '0:00:08.000',
      'space': 'source-local'}, 'speed': .5}, 4, 2),
    ({'id': 'pause', 'mode': 'hold', 'at': '0:00:02.000', 'space': 'source-local',
      'duration': '0:00:02.000'}, 3, 2),
])
def test_visual_motion_advances_in_result_time_during_source_speed_and_holds(pacing, at, source_time):
    spec = _moving_project()
    spec['motion'] = {'version': 1, 'scenes': [{'scene': 'scene_a', 'scene_id': 'scene_a',
        'slots': [{'slot': 'main', 'pacing': [pacing], 'camera': []}]}]}
    frame = _frame(spec, at)
    assert frame['scene_source_time_s'] == source_time
    assert frame['children'][0]['children'][0]['media']['source_time_s'] == source_time
    sampled = prepare_layout_frame(frame, {'width': 640, 'height': 360})['nodes'][0]['motion']
    assert sampled['scene_time_s'] == at
    assert sampled['translate_y'] == '40px'


def test_motion_restarts_at_reordered_scene_and_uses_last_grid_frame_at_project_end():
    spec = _moving_project()
    second = deepcopy(spec['composition'][0])
    second['id'] = second['name'] = 'second'
    spec['composition'].insert(0, second)
    spec['spatial']['scenes']['second'] = deepcopy(spec['spatial']['scenes']['scene_a'])
    frame = _frame(spec, 10)
    assert frame['scene_id'] == 'scene_a'
    assert prepare_layout_frame(frame, {'width': 640, 'height': 360})['nodes'][0]['motion']['scene_time_s'] == 2
    frame = _frame(spec, 16)
    assert frame['sample_time_s'] < 16
    assert prepare_layout_frame(frame, {'width': 640, 'height': 360})['nodes'][0]['motion']['scene_time_s'] == pytest.approx(frame['sample_time_s'] - 8)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_moving_foreground_video_and_image_over_stationary_video(project, tmp_path):
    spec, metadata = project
    layout = spec['spatial']['scenes']['interview']['layout']
    # The speaker remains in the mask/FFmpeg compositor. The moving listeners
    # overlap it and are painted with Chrome, including semi-transparent edges.
    listeners = layout['children'][1]
    listeners['layout'].update({'position': 'absolute', 'left': 20, 'top': 30,
                                'width': 180, 'height': 130, 'z_index': 8})
    listeners['motion'] = {'translate_x': [{'at': 0, 'value': 24.5}],
                           'scale': [{'at': 0, 'value': 1.1}],
                           'opacity': [{'at': 0, 'value': .6}]}
    layout['children'][2]['motion'] = {'scale': [{'at': 0, 'value': 1.7}]}
    actual, reference = tmp_path / 'mixed.png', tmp_path / 'reference.png'
    result = render_spatial_frame(spec, _render_frame(spec, metadata), {'width': 320, 'height': 180},
                                  output_path=actual, reference_path=reference, project_root=tmp_path)
    assert len(result['media']) == 3
    assert max(abs(a - b) for a, b in zip(_rgb(actual), _rgb(reference))) <= 3


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_static_opacity_target_can_fade_without_changing_containing_blocks():
    frame = _frame(_moving_project(), 2)
    group = frame['children'][0]
    group['layout']['position'] = 'static'
    group['motion'] = {'opacity': _track(0, 1)}
    group['children'][0]['layout'].update({'position': 'absolute', 'left': 10, 'top': 20})
    result = measure_spatial_layout(frame, {'width': 640, 'height': 360})
    assert result['boxes'][1]['base_rect'] == result['boxes'][1]['rect']
    assert result['boxes'][1]['effective_opacity'] == .5


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_motion_rejects_containing_block_changes_before_replacing_artifacts(project, tmp_path):
    spec, metadata = project
    group = spec['spatial']['scenes']['interview']['layout']['children'][0]
    group['motion'] = {'translate_x': [{'at': 0, 'value': 20}]}
    child = spec['spatial']['definitions']['card']['children'][0]['children'][0]
    child['layout']['position'] = 'fixed'
    output = tmp_path / 'previous.png'
    output.write_bytes(b'keep prior output')
    with pytest.raises(SpatialLayoutError, match='footage.*motion.*fixed'):
        render_spatial_frame(spec, _render_frame(spec, metadata), {'width': 320, 'height': 180},
                              output_path=output, project_root=tmp_path)
    assert output.read_bytes() == b'keep prior output'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('scale', [.5, 1.7])
def test_moving_caption_keeps_cue_wrapping_and_content_box_overflow_correct(caption_project, tmp_path, scale):
    spec, metadata = caption_project
    target = spec['spatial']['scenes']['first']['layout']['children'][0]
    target['motion'] = {'scale': [{'at': 0, 'value': scale}],
                        'translate_y': [{'at': 0, 'value': -100}]}
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), 1)
    actual = render_spatial_frame(spec, frame, {'width': 640, 'height': 360},
                                  output_path=tmp_path / 'caption.png', project_root=tmp_path)
    group, words = actual['boxes']
    assert words['caption']['text'] == 'Second line\nwith two rows'
    assert len(words['text_lines']) == 2
    assert words['overflow'] is False
    assert words['rect']['width'] == pytest.approx(words['base_rect']['width'] * scale, abs=.01)
    assert words['rect']['y'] == group['rect']['y']
    assert actual['boxes'] == measure_spatial_layout(frame, {'width': 640, 'height': 360})['boxes']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_cli_motion_edit_dry_run_undo_and_two_views_share_saved_time(project, tmp_path, monkeypatch):
    spec, metadata = project
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    request = {'op': 'instance.customize',
               'target': {'occurrence_path': ['interview', 'speaker', 'panel']},
               'property': 'motion.translate_y', 'value': _track(0, 50)}
    document = tmp_path / 'motion.json'
    document.write_text(json.dumps(request))
    runner = CliRunner()
    dry = runner.invoke(cli, ['composition', 'model', 'edit', str(document), '--dry-run'])
    assert dry.exit_code == 0, dry.output
    assert json.loads(dry.output)['affected'] == ['interview']
    assert load_spec(cwd=tmp_path) == spec
    edit = runner.invoke(cli, ['composition', 'model', 'edit', str(document)])
    assert edit.exit_code == 0, edit.output
    saved = load_spec(cwd=tmp_path)
    assert len(saved['revisions']) == 1
    before = deepcopy(saved)
    outputs = []
    for command, extra in [('layout', ['--out', 'boxes.png']), ('preview', ['--out', 'pixels.png'])]:
        result = runner.invoke(cli, ['composition', 'model', command, '--at', '0:00:01.200',
                                     '--canvas', '320x180', *extra])
        assert result.exit_code == 0, result.output
        outputs.append(json.loads(result.output)['layout' if command == 'layout' else 'preview']['boxes'])
    assert outputs[0] == outputs[1]
    assert outputs[0][1]['motion']['translate_y'] == '5px'
    assert load_spec(cwd=tmp_path) == before  # inspection/rendering write no revisions
    restored, _ = pop_revision(saved)
    assert restored['spatial'] == spec['spatial']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_opacity_motion_multiplies_the_resolved_base_without_reinheriting_it():
    frame = _frame(_moving_project(), 2)
    group = frame['children'][0]
    group['motion'] = {'opacity': _track(0, 1)}
    group['style']['opacity'] = .8
    child = group['children'][0]
    child['style']['opacity'] = 'inherit'
    measured = measure_spatial_layout(frame, {'width': 640, 'height': 360})
    assert measured['boxes'][0]['effective_opacity'] == .4
    assert measured['boxes'][1]['effective_opacity'] == .32


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('moving', [False, True])
def test_zero_width_video_fails_explicitly_in_both_paint_paths(project, tmp_path, moving):
    spec, metadata = project
    footage = spec['spatial']['definitions']['card']['children'][0]['children'][0]
    footage['layout']['width'] = 0
    if moving:
        footage['motion'] = {'translate_x': [{'at': 0, 'value': 10}]}
    output = tmp_path / 'previous.png'
    output.write_bytes(b'previous output')
    with pytest.raises(SpatialLayoutError, match='footage.*unresolved video geometry'):
        render_spatial_frame(spec, _render_frame(spec, metadata), {'width': 320, 'height': 180},
                              output_path=output, project_root=tmp_path)
    assert output.read_bytes() == b'previous output'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('scale', [None, .5, 2])
def test_zero_width_caption_keeps_overflow_error_and_previous_artifacts(caption_project, tmp_path, scale):
    from moviestar.spatial_render import SpatialRenderError

    spec, metadata = caption_project
    words = spec['spatial']['definitions']['subtitles']['children'][0]
    words['layout'].update({'width': 0, 'height': 88, 'padding': 0, 'overflow': 'hidden'})
    if scale is not None:
        spec['spatial']['scenes']['first']['layout']['children'][0]['motion'] = {
            'scale': [{'at': 0, 'value': scale}]}
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .5)
    measured = measure_spatial_layout(frame, {'width': 640, 'height': 360})
    caption = measured['boxes'][1]
    assert caption['base_rect']['width'] == 0
    assert caption['text_lines'][0]['width'] > 0
    assert caption['overflow'] is True
    assert any(item['code'] == 'caption_overflow' and item['path'] == ['first', 'subtitles', 'words']
               for item in measured['diagnostics'])
    preview, labels = tmp_path / 'previous.png', tmp_path / 'labels.png'
    preview.write_bytes(b'previous preview')
    labels.write_bytes(b'previous labels')
    with pytest.raises(SpatialRenderError, match='caption_overflow.*first/subtitles/words'):
        render_spatial_frame(spec, frame, {'width': 640, 'height': 360}, output_path=preview,
                              layout_output_path=labels, project_root=tmp_path)
    assert preview.read_bytes() == b'previous preview'
    assert labels.read_bytes() == b'previous labels'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('display', ['inline', 'contents', 'table-column', 'table-column-group'])
def test_geometric_motion_rejects_nontransformable_boxes(caption_project, tmp_path, display):
    spec, metadata = caption_project
    spec['spatial']['scenes']['first']['layout']['children'] = [{
        'id': 'label', 'type': 'text', 'content': {'text': 'Move me'},
        'layout': {'display': display, 'position': 'relative'}, 'style': {'font_size': 24},
        'motion': {'translate_x': [{'at': 0, 'value': 100}], 'scale': [{'at': 0, 'value': 2}]}}]
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .5)
    preview = tmp_path / 'previous.png'
    preview.write_bytes(b'previous preview')
    for inspect in (lambda: measure_spatial_layout(frame, {'width': 640, 'height': 360}),
                    lambda: render_spatial_frame(spec, frame, {'width': 640, 'height': 360},
                        output_path=preview, project_root=tmp_path)):
        with pytest.raises(SpatialLayoutError, match=f'first/label.motion:.*{display}.*inline-block'):
            inspect()
    assert preview.read_bytes() == b'previous preview'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('display', ['inline-block', 'inline'])
def test_inline_block_can_move_and_inline_can_fade_without_reflow(caption_project, tmp_path, display):
    spec, metadata = caption_project
    motion = ({'translate_x': [{'at': 0, 'value': 100}], 'scale': [{'at': 0, 'value': 2}]}
              if display == 'inline-block' else {})
    motion['opacity'] = [{'at': 0, 'value': .5}]
    spec['spatial']['scenes']['first']['layout']['children'] = [{
        'id': 'label', 'type': 'text', 'content': {'text': 'Move me'},
        'layout': {'display': display}, 'style': {'font_size': 24}, 'motion': motion}]
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .5)
    actual = render_spatial_frame(spec, frame, {'width': 640, 'height': 360},
                                  output_path=tmp_path / 'preview.png', project_root=tmp_path)
    assert actual['boxes'] == measure_spatial_layout(frame, {'width': 640, 'height': 360})['boxes']
    box = actual['boxes'][0]
    assert box['effective_opacity'] == .5
    if display == 'inline-block':
        assert box['rect']['x'] == box['base_rect']['x'] + 100
        assert box['rect']['width'] == pytest.approx(box['base_rect']['width'] * 2, abs=.002)
    else:
        assert box['rect'] == box['base_rect']
