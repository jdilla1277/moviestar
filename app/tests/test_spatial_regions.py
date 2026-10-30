"""Declared layout reservations use the same measured boxes in every view."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available, measure_spatial_layout, prepare_layout_frame
from moviestar.spatial_model import compile_spatial_frame, expand_spatial_scene
from moviestar.spec import SpecValidationError, load_spec, save_spec
from tests.test_spatial_render import project
from tests.test_spatial_static import _static
from tests.test_spatial_inspection import _save


def _reserved(spec):
    _static(spec, .4)
    layout = spec['spatial']['scenes']['title']['layout']
    layout['children'] = [
        {'id': 'band', 'type': 'shape', 'content': {},
         'layout': {'position': 'absolute', 'left': 0, 'top': '70%', 'width': '100%', 'height': '30%'},
         'children': [{'id': 'label', 'type': 'text', 'content': {'text': 'Caption space'},
                       'layout': {'width': '100%', 'height': '100%'}, 'style': {'font_size': 12}}]},
        {'id': 'badge', 'type': 'shape', 'content': {},
         'layout': {'position': 'absolute', 'left': 20, 'top': 10, 'width': 30, 'height': 20},
         'style': {'background_color': '#ff6600'}}]
    layout['reserved_regions'] = [{'id': 'captions', 'target': ['band'], 'allow': []}]
    return layout


def _frame(spec, metadata, at=.1):
    return compile_spatial_frame(spec, resolve_project(metadata, spec), at)


@pytest.mark.parametrize('regions', [None, {}, [None], [{'id': 'captions', 'target': []}],
    [{'id': 'captions', 'target': 'band'}], [{'id': 'captions', 'target': ['']}],
    [{'id': 'captions', 'target': ['band'], 'allow': ['badge']}],
    [{'id': 'captions', 'target': ['band'], 'allow': [['badge'], ['badge']]}],
    [{'id': 'captions', 'target': ['band'], 'unexpected': True}],
    [{'id': 'captions', 'target': ['band']}, {'id': 'captions', 'target': ['badge']}],
])
def test_invalid_reservations_fail_before_overwriting_spec(project, tmp_path, regions):
    spec, _ = project
    layout = _reserved(spec)
    save_spec(spec, cwd=tmp_path)
    path = tmp_path / 'moviestar/spec.json'
    before = path.read_bytes()
    layout['reserved_regions'] = regions
    with pytest.raises(SpecValidationError, match='reserved_regions'):
        save_spec(spec, cwd=tmp_path)
    assert path.read_bytes() == before


def test_reservations_survive_reload_and_missing_paths_are_addressable_drafts(project, tmp_path):
    spec, metadata = project
    layout = _reserved(spec)
    layout['reserved_regions'][0]['target'] = ['missing']
    layout['reserved_regions'][0]['allow'] = [['other']]
    save_spec(spec, cwd=tmp_path)
    loaded = load_spec(cwd=tmp_path)
    frame = _frame(loaded, metadata)
    errors = [d for d in frame['diagnostics'] if d['code'] == 'unknown_reserved_region_target']
    assert {(d['field'], tuple(d['path'])) for d in errors} == {
        ('target', ('title', 'missing')), ('allow', ('title', 'other'))}
    assert all(d['region_id'] == 'captions' and d['severity'] == 'error' for d in errors)
    assert frame['reserved_regions'] == layout['reserved_regions']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_measured_regions_track_responsive_layout_and_allow_only_declared_subtrees(project):
    spec, metadata = project
    layout = _reserved(spec)
    layout['children'][1]['layout']['top'] = '75%'
    frame = _frame(spec, metadata)
    measured = measure_spatial_layout(frame, {'width': 320, 'height': 180})
    region = measured['reserved_regions'][0]
    assert region['path'] == ['title', 'band']
    assert region['rect'] == {'x': 0, 'y': 126, 'width': 320, 'height': 54}
    errors = [d for d in measured['diagnostics'] if d['code'] == 'reserved_region_overlap']
    assert [d['path'] for d in errors] == [['title', 'badge']]
    assert errors[0]['region_id'] == 'captions'
    assert errors[0]['region_path'] == ['title', 'band']
    assert errors[0]['intersection'] == {'x': 20, 'y': 135, 'width': 30, 'height': 20}
    layout['reserved_regions'][0]['allow'] = [['badge']]
    allowed = measure_spatial_layout(_frame(spec, metadata), {'width': 180, 'height': 320})
    assert not [d for d in allowed['diagnostics'] if d['code'] == 'reserved_region_overlap']
    assert allowed['reserved_regions'][0]['rect']['y'] == 224
    assert allowed['reserved_regions'][0]['allow'] == [['title', 'badge']]


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_moving_component_enters_reserved_region_only_at_intermediate_time(project):
    spec, metadata = project
    layout = _reserved(spec)
    badge = layout['children'].pop()
    badge['layout'].update({'left': 0, 'top': 0})
    spec['spatial']['definitions']['tag'] = {'name': 'Tag', 'children': [badge]}
    layout['children'].append({'instance_id': 'tag', 'definition_id': 'tag',
        'layout': {'position': 'absolute', 'left': 20, 'top': 10, 'width': 30, 'height': 20},
        'motion': {'translate_y': [{'at': 0, 'value': 0}, {'at': .2, 'value': 130}, {'at': .4, 'value': 0}]}})
    for at, collides in [(0, False), (.2, True), (.399, False)]:
        report = measure_spatial_layout(_frame(spec, metadata, at), {'width': 320, 'height': 180})
        errors = [d for d in report['diagnostics'] if d['code'] == 'reserved_region_overlap']
        assert bool(errors) is collides
        if errors:
            assert errors[0]['result_time_s'] == .2
    layout['reserved_regions'][0]['allow'] = [['tag']]
    allowed = measure_spatial_layout(_frame(spec, metadata, .2), {'width': 320, 'height': 180})
    assert not [d for d in allowed['diagnostics'] if d['code'] == 'reserved_region_overlap']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('command', ['preview', 'screenshot', 'inspect', 'watch', 'export'])
def test_frame_and_video_commands_reject_collision_without_replacing_prior_output(
        project, tmp_path, monkeypatch, command):
    spec, metadata = project
    layout = _reserved(spec)
    layout['children'][1]['layout']['top'] = '75%'
    _save(spec, metadata, tmp_path, monkeypatch)
    before = Path('moviestar/spec.json').read_bytes()
    out = 'prior.mp4' if command in {'watch', 'export'} else 'prior.png'
    Path(out).write_bytes(b'prior')
    args = {'preview': ['composition', 'model', 'preview', '--at', '0:00:00.100', '--out', out],
            'screenshot': ['screenshot', '--at', '.1', '--out', out],
            'inspect': ['inspect', '--from', '.1', '--to', '.2'],
            'watch': ['watch', '--from', '.1', '--to', '.2', '--out', out, '--video-encoder', 'x264'],
            'export': ['export', '--out', out, '--video-encoder', 'x264', '--quiet']}[command]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1, result.output
    assert 'reserved_region_overlap' in json.loads(result.stdout)['error']
    assert 'title/badge' in json.loads(result.stdout)['error']
    assert Path(out).read_bytes() == b'prior'
    assert Path('moviestar/spec.json').read_bytes() == before


def test_reservations_can_be_edited_dry_run_saved_and_undone(project, tmp_path, monkeypatch):
    spec, metadata = project
    layout = _reserved(spec)
    layout.pop('reserved_regions')
    _save(spec, metadata, tmp_path, monkeypatch)
    before = Path('moviestar/spec.json').read_bytes()
    request = {'op': 'instance.customize', 'target': {'occurrence_path': ['title']},
               'property': 'layout.reserved_regions',
               'value': [{'id': 'captions', 'target': ['band'], 'allow': [['badge']]}]}
    Path('edit.json').write_text(json.dumps(request))
    result = CliRunner().invoke(cli, ['composition', 'model', 'edit', 'edit.json', '--dry-run'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)['affected'] == ['title']
    assert Path('moviestar/spec.json').read_bytes() == before
    result = CliRunner().invoke(cli, ['composition', 'model', 'edit', 'edit.json'])
    assert result.exit_code == 0, result.output
    assert load_spec()['spatial']['scenes']['title']['layout']['reserved_regions'] == request['value']
    result = CliRunner().invoke(cli, ['undo'])
    assert result.exit_code == 0, result.output
    assert 'reserved_regions' not in load_spec()['spatial']['scenes']['title']['layout']


def test_shared_removal_of_reserved_target_is_atomic_and_reordering_preserves_identity(project):
    from moviestar.spatial_edit import SpatialEditConflict, edit_spatial_model
    spec, _ = project
    layout = _reserved(spec)
    band = layout['children'].pop(0)
    spec['spatial']['definitions']['captions'] = {'name': 'CaptionBand', 'children': [band]}
    layout['children'].insert(0, {'instance_id': 'caption_area', 'definition_id': 'captions',
                                 'layout': {'width': '100%', 'height': '100%'}})
    layout['reserved_regions'][0]['target'] = ['caption_area', 'band']
    before = deepcopy(spec)
    with pytest.raises(SpatialEditConflict) as error:
        edit_spatial_model(spec, {'op': 'component.child_remove',
            'target': {'definition_id': 'captions', 'path': ['band']}})
    assert error.value.conflicts[0]['region_id'] == 'captions'
    assert error.value.conflicts[0]['path'] == ['title', 'caption_area', 'band']
    assert spec == before
    moved, _ = edit_spatial_model(spec, {'op': 'instance.child_move',
        'target': {'occurrence_path': ['title', 'badge']}, 'after_id': None})
    assert expand_spatial_scene(moved, 'title')['diagnostics'] == []
    assert moved['spatial']['scenes']['title']['layout']['reserved_regions'] == layout['reserved_regions']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('state', ['clipped', 'offscreen', 'invisible', 'touching'])
def test_hidden_or_edge_touching_boxes_do_not_intrude(project, state):
    spec, metadata = project
    layout = _reserved(spec)
    badge = layout['children'][1]
    if state == 'clipped':
        badge['layout']['top'] = 135
        layout['children'][1] = {'id': 'clip', 'type': 'shape', 'content': {},
            'layout': {'position': 'absolute', 'left': 0, 'top': 0, 'width': 100,
                       'height': 30, 'overflow': 'hidden'}, 'children': [badge]}
    elif state == 'offscreen':
        badge['layout'].update({'top': 135, 'left': 400})
    elif state == 'invisible':
        badge['layout']['top'] = 135
        badge['style']['opacity'] = 0
    else:
        badge['layout']['top'] = 106  # Bottom exactly touches the reserved top at 126.
    measured = measure_spatial_layout(_frame(spec, metadata), {'width': 320, 'height': 180})
    assert not [d for d in measured['diagnostics'] if d['code'] == 'reserved_region_overlap']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_clear_and_explicitly_allowed_rendered_and_labeled_views_agree(project, tmp_path):
    from moviestar.spatial_render import render_spatial_frame
    spec, metadata = project
    layout = _reserved(spec)
    layout['children'][1]['layout']['top'] = 135
    layout['reserved_regions'][0]['allow'] = [['badge']]
    frame = _frame(spec, metadata)
    before = deepcopy(spec)
    rendered = render_spatial_frame(spec, frame, {'width': 320, 'height': 180},
        output_path=tmp_path / 'preview.png', layout_output_path=tmp_path / 'layout.png', project_root=tmp_path)
    measured = measure_spatial_layout(frame, {'width': 320, 'height': 180})
    assert rendered['boxes'] == measured['boxes']
    assert rendered['reserved_regions'] == measured['reserved_regions']
    assert rendered['diagnostics'] == measured['diagnostics'] == []
    assert (tmp_path / 'preview.png').is_file() and (tmp_path / 'layout.png').is_file()
    assert spec == before
    # Without reservations, the same intentional overlap remains unrestricted.
    layout.pop('reserved_regions')
    plain = measure_spatial_layout(_frame(spec, metadata), {'width': 320, 'height': 180})
    assert plain['reserved_regions'] == [] and plain['diagnostics'] == []


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_browser_clipping_respects_positioned_containing_block(project):
    spec, metadata = project
    layout = _reserved(spec)
    badge = layout['children'][1]
    badge['layout']['top'] = 135
    # This absolute child belongs to the scene's containing block. An intervening
    # static overflow box does not clip it; the browser decides this relationship.
    layout['children'][1] = {'id': 'wrapper', 'type': 'shape', 'content': {},
        'layout': {'position': 'static', 'width': 100, 'height': 30, 'overflow': 'hidden'},
        'children': [badge]}
    measured = measure_spatial_layout(_frame(spec, metadata), {'width': 320, 'height': 180})
    errors = [d for d in measured['diagnostics'] if d['code'] == 'reserved_region_overlap']
    assert [d['path'] for d in errors] == [['title', 'wrapper', 'badge']]


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_export_checks_intermediate_motion_and_preserves_existing_movie(project, tmp_path, monkeypatch):
    spec, metadata = project
    layout = _reserved(spec)
    layout['children'][1]['motion'] = {'translate_y': [
        {'at': 0, 'value': 0}, {'at': .2, 'value': 130}, {'at': .4, 'value': 0}]}
    _save(spec, metadata, tmp_path, monkeypatch)
    Path('prior.mp4').write_bytes(b'prior')
    result = CliRunner().invoke(cli, ['export', '--out', 'prior.mp4', '--video-encoder', 'x264', '--quiet'])
    assert result.exit_code == 1, result.output
    assert 'reserved_region_overlap' in json.loads(result.stdout)['error']
    assert 'title/badge' in json.loads(result.stdout)['error']
    assert Path('prior.mp4').read_bytes() == b'prior'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_allowance_is_scoped_to_one_region_and_hidden_target_still_reserves_space(project):
    spec, metadata = project
    layout = _reserved(spec)
    layout['children'][1]['layout']['top'] = 135
    layout['children'][0]['style'] = {'opacity': 0}
    measured = measure_spatial_layout(_frame(spec, metadata), {'width': 320, 'height': 180})
    assert any(d['code'] == 'reserved_region_overlap' and d['path'] == ['title', 'badge']
               for d in measured['diagnostics'])
    layout['children'][0].pop('style')
    layout['reserved_regions'][0]['allow'] = [['badge']]
    layout['reserved_regions'].append({'id': 'branding', 'target': ['badge']})
    measured = measure_spatial_layout(_frame(spec, metadata), {'width': 320, 'height': 180})
    errors = [d for d in measured['diagnostics'] if d['code'] == 'reserved_region_overlap']
    assert errors and {d['region_id'] for d in errors} == {'branding'}


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_reserved_target_without_canvas_area_is_not_silently_treated_as_clear(project):
    spec, metadata = project
    layout = _reserved(spec)
    layout['children'][0]['layout']['top'] = 400
    measured = measure_spatial_layout(_frame(spec, metadata), {'width': 320, 'height': 180})
    errors = [d for d in measured['diagnostics'] if d['code'] == 'unresolved_reserved_region_geometry']
    assert len(errors) == 1
    assert errors[0]['path'] == ['title', 'band'] and errors[0]['region_id'] == 'captions'
