"""One saved composition adapts to canvases without changing its clock."""

from copy import deepcopy
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available, measure_spatial_layout, prepare_layout_frame
from moviestar.spatial_model import compile_spatial_frame, edit_spatial_model
from moviestar.spatial_render import SpatialRenderError, render_spatial_frame
from moviestar.spec import SpecValidationError, load_spec, save_spec, validate_spec
from tests.test_spatial_captions import caption_project


@pytest.fixture
def responsive_project(caption_project):
    spec, project = caption_project
    spec['spatial']['definitions']['card'] = {
        'name': 'Card', 'inputs': [], 'children': [{
            'id': 'panel', 'type': 'shape', 'content': {},
            'layout': {'display': 'grid', 'columns': '1fr 1fr', 'gap': 8, 'height': '100%'},
            'style': {'background_color': '#234567'},
            'format_rules': [{'id': 'stack', 'when': {'orientation': 'portrait'},
                              'layout': {'columns': '1fr', 'rows': '1fr 1fr'},
                              'style': {'background_color': '#345678'}}],
            'children': [{'id': 'headline', 'type': 'text', 'content': {'text': 'One reusable card'},
                          'style': {'font_size': 20, 'color': '#ffffff'},
                          'format_rules': [{'id': 'large-type', 'when': {'orientation': 'portrait'},
                                            'style': {'font_size': 28}}]},
                         {'id': 'graphic', 'type': 'shape', 'style': {'background_color': '#60dfc4'}}]}]}
    for scene in spec['spatial']['scenes'].values():
        captions = scene['layout']['children'][0]
        scene['layout'] = {
            'display': 'grid', 'columns': '1fr 1fr', 'gap': 16, 'padding': '16px 16px 120px',
            'background_color': '#07111d',
            'format_rules': [
                {'id': 'portrait', 'when': {'orientation': 'portrait'},
                 'layout': {'columns': '1fr', 'rows': '1fr 1fr'}},
                {'id': 'square', 'when': {'orientation': 'square'}, 'layout': {'gap': 20}},
                {'id': 'wide', 'when': {'min_width': 900}, 'layout': {'gap': 24}}],
            'children': [{'instance_id': name, 'definition_id': 'card'} for name in ('alice', 'bob')]
                        + [captions]}
    return spec, project


def _frame(spec, project):
    return compile_spatial_frame(spec, resolve_project(project, spec), 1)


@pytest.mark.parametrize('width,height,columns,gap,matched', [
    (640, 360, '1fr 1fr', '16px', []), (360, 640, '1fr', '16px', ['portrait']),
    (640, 640, '1fr 1fr', '20px', ['square']), (960, 540, '1fr 1fr', '24px', ['wide'])])
def test_scene_and_component_format_rules_resolve_for_canvas(
        responsive_project, width, height, columns, gap, matched):
    spec, project = responsive_project
    before = deepcopy(spec)
    validate_spec(spec)
    frame = _frame(spec, project)
    prepared = prepare_layout_frame(frame, {'width': width, 'height': height})
    assert prepared['root_style']['grid-template-columns'] == columns
    assert prepared['root_style']['gap'] == gap
    assert prepared['scene_layout']['matched_format_rules'] == matched
    panel = prepared['nodes'][0]['children'][0]
    assert panel['style']['grid-template-columns'] == ('1fr' if height > width else '1fr 1fr')
    assert panel['matched_format_rules'] == (['stack'] if height > width else [])
    assert panel['children'][0]['style']['font-size'] == ('28px' if height > width else '20px')
    assert frame['scene_time_s'] == 1
    assert frame['children'][2]['children'][0]['caption']['text'] == 'Second line\nwith two rows'
    assert spec == before


def test_local_customization_beats_format_rules_and_reset_restores_them(responsive_project):
    spec, project = responsive_project
    target = {'occurrence_path': ['first', 'alice', 'panel']}
    spec, _ = edit_spatial_model(spec, {'op': 'instance.customize', 'target': target,
                                       'property': 'layout.grid_template_columns', 'value': '2fr 1fr'})
    prepared = prepare_layout_frame(_frame(spec, project), {'width': 360, 'height': 640})
    alice, bob = [node['children'][0] for node in prepared['nodes'][:2]]
    assert alice['style']['grid-template-columns'] == '2fr 1fr'
    assert alice['rule_sources']['grid-template-columns']['layer'] == 'local_customization'
    assert bob['style']['grid-template-columns'] == '1fr'
    assert bob['rule_sources']['grid-template-columns']['rule_ids'] == ['stack']
    spec, _ = edit_spatial_model(spec, {'op': 'instance.reset', 'target': target,
                                       'property': 'layout.grid_template_columns'})
    prepared = prepare_layout_frame(_frame(spec, project), {'width': 360, 'height': 640})
    assert prepared['nodes'][0]['children'][0]['style']['grid-template-columns'] == '1fr'


def test_rule_constraints_are_inclusive_and_priority_is_explicit(responsive_project):
    spec, project = responsive_project
    layout = spec['spatial']['scenes']['first']['layout']
    layout['format_rules'] = [
        {'id': 'exact', 'when': {'min_width': 640, 'max_width': 640, 'min_height': 360,
                               'max_height': 360, 'min_aspect_ratio': 16 / 9, 'max_aspect_ratio': 16 / 9},
         'layout': {'gap': 32}},
        {'id': 'broad', 'when': {'orientation': 'landscape'}, 'layout': {'gap': 40}, 'priority': -1}]
    prepared = prepare_layout_frame(_frame(spec, project), {'width': 640, 'height': 360})
    assert prepared['root_style']['gap'] == '32px'
    assert prepared['scene_layout']['rule_sources']['gap']['rule_ids'] == ['exact']
    prepared = prepare_layout_frame(_frame(spec, project), {'width': 641, 'height': 360})
    assert prepared['root_style']['gap'] == '40px'


def test_saving_variation_preserves_local_choices_across_formats(responsive_project):
    spec, project = responsive_project
    target = {'occurrence_path': ['first', 'alice', 'panel']}
    for prop, value in [('layout.columns', '3fr 1fr'), ('layout.grid_template_columns', '2fr 1fr')]:
        spec, _ = edit_spatial_model(spec, {'op': 'instance.customize', 'target': target,
                                           'property': prop, 'value': value})
    before = [prepare_layout_frame(_frame(spec, project), canvas)['nodes'][0]['children'][0]['style']
              for canvas in ({'width': 640, 'height': 360}, {'width': 360, 'height': 640})]
    spec, _ = edit_spatial_model(spec, {'op': 'component.save_variation',
        'source': {'occurrence_path': ['first', 'alice']}, 'new_definition_id': 'custom_card',
        'relink_source': True})
    after = [prepare_layout_frame(_frame(spec, project), canvas)['nodes'][0]['children'][0]['style']
             for canvas in ({'width': 640, 'height': 360}, {'width': 360, 'height': 640})]
    assert after == before
    assert all(style['grid-template-columns'] == '2fr 1fr' for style in after)


def test_identical_ties_and_local_overrides_resolve_without_ambiguity(responsive_project):
    spec, project = responsive_project
    panel = spec['spatial']['definitions']['card']['children'][0]
    panel['format_rules'].append({'id': 'same', 'when': {'max_width': 500},
                                  'layout': {'grid_template_columns': '1fr'}})
    prepared = prepare_layout_frame(_frame(spec, project), {'width': 360, 'height': 640})
    assert prepared['diagnostics'] == []
    assert prepared['nodes'][0]['children'][0]['rule_sources']['grid-template-columns']['rule_ids'] == ['stack', 'same']
    panel['format_rules'][-1]['layout']['grid_template_columns'] = '3fr 1fr'
    for name in ('alice', 'bob'):
        spec, _ = edit_spatial_model(spec, {'op': 'instance.customize',
            'target': {'occurrence_path': ['first', name, 'panel']},
            'property': 'layout.columns', 'value': '2fr 1fr'})
    assert prepare_layout_frame(_frame(spec, project), {'width': 360, 'height': 640})['diagnostics'] == []


def test_ambiguous_matching_rules_are_diagnostics_and_preview_preserves_artifact(responsive_project, tmp_path):
    spec, project = responsive_project
    panel = spec['spatial']['definitions']['card']['children'][0]
    panel['format_rules'].append({'id': 'also-stack', 'when': {'max_width': 500},
                                  'layout': {'grid_template_columns': '3fr 1fr'}})
    frame = _frame(spec, project)
    prepared = prepare_layout_frame(frame, {'width': 360, 'height': 640})
    conflicts = prepared['diagnostics']
    assert len(conflicts) == 2
    assert conflicts[0] == {'code': 'ambiguous_format_rule', 'severity': 'error',
        'path': ['first', 'alice', 'panel'], 'property': 'grid-template-columns',
        'rule_ids': ['stack', 'also-stack'], 'priority': 0}
    output = tmp_path / 'previous.png'
    output.write_bytes(b'previous preview')
    with pytest.raises(SpatialRenderError, match='ambiguous_format_rule.*first/alice/panel'):
        render_spatial_frame(spec, frame, {'width': 360, 'height': 640}, output_path=output)
    assert output.read_bytes() == b'previous preview'
    panel['format_rules'][-1]['priority'] = 1
    assert prepare_layout_frame(_frame(spec, project), {'width': 360, 'height': 640})['diagnostics'] == []


@pytest.mark.parametrize('rule', [
    {'id': 'bad', 'when': {}}, {'id': 'bad', 'when': {'orientation': 'mobile'}},
    {'id': 'bad', 'when': {'min_width': True}}, {'id': 'bad', 'when': {'max_width': -1}},
    {'id': 'bad', 'when': {'min_width': 800, 'max_width': 400}},
    {'id': 'bad', 'when': {'min_aspect_ratio': 0}},
    {'id': 'bad', 'when': {'min_height': float('inf')}},
    {'id': 'bad', 'when': {'device': 'phone'}},
    {'id': 'bad', 'when': {'orientation': 'portrait'}, 'content': {'text': 'new clock'}},
    {'id': 'bad', 'when': {'orientation': 'portrait'}, 'priority': True},
    {'id': 'bad', 'when': {'orientation': 'portrait'}, 'layout': []}])
def test_invalid_format_rule_structure_is_addressed(responsive_project, rule):
    spec, _ = responsive_project
    spec['spatial']['scenes']['first']['layout']['format_rules'] = [rule]
    with pytest.raises(SpecValidationError, match='format_rules'):
        validate_spec(spec)


def test_duplicate_format_rule_ids_are_rejected(responsive_project):
    spec, _ = responsive_project
    node = spec['spatial']['definitions']['card']['children'][0]
    node['format_rules'] *= 2
    with pytest.raises(SpecValidationError, match='duplicate.*stack'):
        validate_spec(spec)


def test_definition_rules_need_a_rendered_child_or_instance_target(responsive_project):
    spec, _ = responsive_project
    spec['spatial']['definitions']['card']['format_rules'] = [
        {'id': 'portrait', 'when': {'orientation': 'portrait'}, 'layout': {'columns': '1fr'}}]
    with pytest.raises(SpecValidationError, match='format_rules.*child.*instance'):
        validate_spec(spec)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('canvas', [{'width': 640, 'height': 360}, {'width': 360, 'height': 640}])
def test_responsive_render_and_labeled_inspection_agree(responsive_project, tmp_path, canvas):
    spec, project = responsive_project
    frame = _frame(spec, project)
    output = tmp_path / 'preview.png'
    rendered = render_spatial_frame(spec, frame, canvas, output_path=output,
                                   layout_output_path=tmp_path / 'layout.png')
    measured = measure_spatial_layout(frame, canvas)
    assert rendered['boxes'] == measured['boxes']
    assert rendered['scene_layout'] == measured['scene_layout']
    cards = [box for box in rendered['boxes'] if box['path'] in [['first', 'alice'], ['first', 'bob']]]
    if canvas['height'] > canvas['width']:
        assert cards[0]['rect']['x'] == cards[1]['rect']['x']
        assert cards[1]['rect']['y'] > cards[0]['rect']['y']
    else:
        assert cards[0]['rect']['y'] == cards[1]['rect']['y']
        assert cards[1]['rect']['x'] > cards[0]['rect']['x']
    assert output.read_bytes().startswith(b'\x89PNG')


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('extra_rule', [False, True])
def test_format_shorthand_applies_after_base_and_lower_priority_longhand(responsive_project, extra_rule):
    spec, project = responsive_project
    layout = spec['spatial']['scenes']['first']['layout']
    layout['row_gap'] = 2
    layout['format_rules'][0]['layout']['gap'] = 24
    if extra_rule:
        layout['format_rules'].append({'id': 'low-priority-row-gap', 'when': {'max_width': 500},
                                       'priority': -1, 'layout': {'row_gap': 12}})
    result = measure_spatial_layout(_frame(spec, project), {'width': 360, 'height': 640})
    cards = [box for box in result['boxes'] if box['path'] in [['first', 'alice'], ['first', 'bob']]]
    assert cards[1]['rect']['y'] - cards[0]['rect']['y'] - cards[0]['rect']['height'] == 24


def test_responsive_model_survives_save_reload_and_cli_canvas_is_read_only(responsive_project, tmp_path, monkeypatch):
    if not browser_available():
        pytest.skip('Chrome and Node are required')
    spec, project = responsive_project
    save_spec(spec, cwd=tmp_path)
    save_project(project, cwd=tmp_path)
    assert load_spec(cwd=tmp_path)['spatial'] == spec['spatial']
    monkeypatch.chdir(tmp_path)
    path = tmp_path / 'moviestar' / 'spec.json'
    before = path.read_bytes()
    result = CliRunner().invoke(cli, ['composition', 'model', 'layout', '--at', '0:00:01.000',
                                      '--canvas', '360x640'])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data['layout']['scene_layout']['matched_format_rules'] == ['portrait']
    assert data['frame']['scene_time_s'] == 1
    assert path.read_bytes() == before
