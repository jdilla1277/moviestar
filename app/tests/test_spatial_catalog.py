"""Editable starting layouts use the ordinary spatial model and undo boundary."""

from copy import deepcopy
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.spatial_model import edit_spatial_model, expand_spatial_scene, spatial_state_id
from moviestar.spec import load_spec, pop_revision, save_spec, validate_spec
from tests.test_spatial_model import _project


def invoke(*args):
    result = CliRunner().invoke(cli, ['composition', 'model', 'layouts', *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_catalog_is_discoverable_without_a_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    catalog = invoke()
    assert {item['id'] for item in catalog['layouts']} == {'single', 'two-up', 'pip', 'text-video'}
    assert catalog['writes_spec'] is False
    assert invoke('list') == catalog
    detail = invoke('show', 'text-video')
    assert {item['name']: item['type'] for item in detail['inputs']} == {
        'video': 'video', 'title': 'text', 'body': 'text', 'image': 'image', 'captions': 'caption'}
    assert detail['definitions']['builtin:text-panel']['children']
    assert not list(tmp_path.iterdir())


@pytest.fixture
def saved_project(tmp_path, monkeypatch):
    spec = _project()
    spec.pop('spatial')
    save_spec(spec, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path, load_spec(cwd=tmp_path)


def test_apply_draft_is_one_undoable_revision_and_keeps_timing(saved_project):
    root, before = saved_project
    preview = invoke('apply', 'pip', '--scene', 'scene_a', '--dry-run')
    assert preview['writes_spec'] is False
    assert preview['affected'] == ['scene_a']
    assert {'main', 'inset'} <= {item['input'] for item in preview['diagnostics']}
    assert load_spec() == before
    applied = invoke('apply', 'pip', '--scene', 'scene_a')
    after = load_spec()
    assert applied['writes_spec'] is True
    assert applied['state_id_after'] == preview['state_id_after'] == spatial_state_id(after)
    assert after['spatial']['scenes']['scene_a'] == preview['scene']
    assert len(after['revisions']) == len(before['revisions']) + 1
    assert after['composition'] == before['composition']
    assert after['sources'] == before['sources']
    assert after['composition_canvas'] == before['composition_canvas']
    restored, _ = pop_revision(after)
    assert restored.get('spatial') is None
    assert restored['composition'] == before['composition']
    validate_spec(after)


def test_bindings_flow_to_nested_components_and_optional_empty_is_explicit(saved_project):
    root, _ = saved_project
    path = root / 'bindings.json'
    path.write_text(json.dumps({'main': {'asset_id': 'alice', 'source': {'start': 2, 'end': 7}},
                                'inset': {'asset_id': 'bob'}, 'captions': {'track': 'dialogue'}}))
    report = invoke('apply', 'pip', '--scene', 'scene_a', '--bindings', str(path))
    spec = load_spec()
    expanded = expand_spatial_scene(spec, 'scene_a')
    assert not expanded['diagnostics']
    panel = expanded['children'][0]['children'][0]
    assert panel['children'][0]['children'][0]['content'] == {
        'asset_id': 'alice', 'source': {'start': 2, 'end': 7}}
    assert panel['children'][1]['children'][0]['content'] == {'asset_id': 'bob'}
    assert expanded['children'][1]['children'][0]['content'] == {'track': 'dialogue'}
    assert report['captured_definitions'] == ['builtin:caption-region', 'builtin:picture-in-picture', 'builtin:video-frame']
    invoke('apply', 'single', '--scene', 'scene_b')
    assert expand_spatial_scene(load_spec(), 'scene_b')['children'][1]['children'][0]['content'] == {'empty': True}


def test_copies_have_fresh_ids_and_keep_edited_captured_definitions(saved_project, monkeypatch):
    from moviestar import spatial_catalog
    invoke('apply', 'single', '--scene', 'scene_a')
    spec = load_spec()
    definition = 'builtin:video-frame'
    edited, _ = edit_spatial_model(spec, {'op': 'component.update',
        'target': {'definition_id': definition, 'element_id': 'video'},
        'property': 'style.border_radius', 'value': 14})
    save_spec(edited)
    original_definition = deepcopy(edited['spatial']['definitions'][definition])
    # Simulate an upgraded package catalog: captured project edits win.
    monkeypatch.setitem(spatial_catalog.DEFINITIONS[definition]['children'][0]['style'], 'border_radius', 99)
    second = invoke('apply', 'single', '--scene', 'scene_b')
    current = load_spec()
    first_layout = current['spatial']['scenes']['scene_a']['layout']
    second_layout = current['spatial']['scenes']['scene_b']['layout']
    assert first_layout['children'][0]['instance_id'] != second_layout['children'][0]['instance_id']
    assert current['spatial']['definitions'][definition] == original_definition
    assert second['captured_definitions'] == []
    assert second['reused_definitions'] == ['builtin:caption-region', definition]
    assert len(current['spatial']['definitions']) == 2
    first_layout['padding'] = 99
    assert 'padding' not in second_layout


@pytest.mark.parametrize('bindings', [[], {'typo': {'text': 'oops'}}, {'main': {'text': 'wrong type'}},
                                      {'main': {'asset_id': 'alice', 'source': {'start': 3, 'end': 2}}}])
def test_bad_bindings_fail_without_writing(saved_project, bindings):
    root, before = saved_project
    path = root / 'bindings.json'
    path.write_text(json.dumps(bindings))
    result = CliRunner().invoke(cli, ['composition', 'model', 'layouts', 'apply', 'pip',
        '--scene', 'scene_a', '--bindings', str(path)])
    assert result.exit_code == 1
    assert 'bindings' in json.loads(result.output)['error']
    assert load_spec() == before


def test_replace_and_stale_state_guards_preserve_other_drafts(saved_project):
    invoke('apply', 'single', '--scene', 'scene_a')
    invoke('apply', 'text-video', '--scene', 'scene_b')
    before = load_spec()
    for extra, error in [([], 'replace'), (['--replace', '--expected-state-id', 'sha256:stale'], 'expected_state_id')]:
        result = CliRunner().invoke(cli, ['composition', 'model', 'layouts', 'apply', 'two-up',
                                         '--scene', 'scene_a', *extra])
        assert result.exit_code == 1
        assert error in json.loads(result.output)['error']
        assert load_spec() == before
    replaced = invoke('apply', 'two-up', '--scene', 'scene_a', '--replace',
                       '--expected-state-id', spatial_state_id(before))
    after = load_spec()
    assert replaced['replaces_layout'] is True
    assert after['spatial']['scenes']['scene_b'] == before['spatial']['scenes']['scene_b']
    assert after['spatial']['definitions'] == before['spatial']['definitions']
    restored, _ = pop_revision(after)
    assert restored['spatial'] == before['spatial']


def test_unknown_names_and_definition_id_collision_do_not_overwrite(saved_project):
    for args in [('show', 'unknown'), ('apply', 'single', '--scene', 'unknown')]:
        result = CliRunner().invoke(cli, ['composition', 'model', 'layouts', *args])
        assert result.exit_code == 1
        assert 'unknown' in json.loads(result.output)['error']
    spec = load_spec()
    spec['spatial'] = {'definitions': {'builtin:video-frame': {
        'name': 'My unrelated component', 'inputs': [], 'children': []}}, 'scenes': {}}
    save_spec(spec)
    before = load_spec()
    result = CliRunner().invoke(cli, ['composition', 'model', 'layouts', 'apply', 'single', '--scene', 'scene_a'])
    assert result.exit_code == 1
    assert 'builtin:video-frame' in json.loads(result.output)['error']
    assert load_spec() == before


def test_capture_is_detached_from_catalog_and_supplied_bindings():
    from moviestar.spatial_catalog import DEFINITIONS, apply_builtin_layout, builtin_layout
    spec = _project()
    spec.pop('spatial')
    before = deepcopy(spec)
    bindings = {'video': {'asset_id': 'alice', 'source': {'start': 1, 'end': 7}}}
    changed, _ = apply_builtin_layout(spec, 'single', 'scene_a', bindings)
    bindings['video']['source']['end'] = 2
    assert spec == before
    assert expand_spatial_scene(changed, 'scene_a')['children'][0]['children'][0]['content']['source']['end'] == 7
    changed['spatial']['definitions']['builtin:video-frame']['name'] = 'Mine'
    assert DEFINITIONS['builtin:video-frame']['name'] == 'VideoFrame'
    item = builtin_layout('single')
    item['layout']['children'].clear()
    assert builtin_layout('single')['layout']['children']


@pytest.mark.parametrize('with_bindings', [False, True])
def test_captured_component_interface_mismatch_fails_atomically(saved_project, with_bindings):
    invoke('apply', 'single', '--scene', 'scene_a')
    spec = load_spec()
    definition = spec['spatial']['definitions']['builtin:video-frame']
    definition['inputs'][0]['type'] = 'image'
    definition['children'][0]['type'] = 'image'
    save_spec(spec)
    before = load_spec()
    root, _ = saved_project
    path = root / 'bindings.json'
    path.write_text(json.dumps({'video': {'asset_id': 'alice', 'source': {'start': 0, 'end': 3}}}))
    result = CliRunner().invoke(cli, ['composition', 'model', 'layouts', 'apply', 'single',
        '--scene', 'scene_b', *(['--bindings', str(path)] if with_bindings else [])])
    assert result.exit_code == 1
    assert 'binding' in json.loads(result.output)['error']
    assert load_spec() == before


def test_optional_captions_can_be_bound_later_with_the_normal_edit_command(saved_project):
    invoke('apply', 'single', '--scene', 'scene_a')
    spec = load_spec()
    caption = spec['spatial']['scenes']['scene_a']['layout']['children'][1]
    assert 'instance_id' in caption
    repaired, _ = edit_spatial_model(spec, {'op': 'input.bind',
        'target': {'occurrence_path': ['scene_a', caption['instance_id']]},
        'input': 'track', 'value': {'track': 'dialogue'}})
    assert expand_spatial_scene(repaired, 'scene_a')['children'][1]['children'][0]['content'] == {'track': 'dialogue'}


def test_apply_returns_targets_for_binding_and_local_shared_edit_loop(saved_project):
    first = invoke('apply', 'single', '--scene', 'scene_a')
    second = invoke('apply', 'single', '--scene', 'scene_b')
    spec = load_spec()
    a = first['input_targets']['video']
    b = second['input_targets']['video']
    assert a['input'] == b['input'] == 'footage'
    assert a['occurrence_path'] != b['occurrence_path']
    spec, _ = edit_spatial_model(spec, {'op': 'input.bind', 'target': a, 'input': a['input'],
                                       'value': {'asset_id': 'alice'}})
    path = a['occurrence_path'] + ['video']
    spec, _ = edit_spatial_model(spec, {'op': 'instance.customize', 'target': {'occurrence_path': path},
                                       'property': 'style.border_radius', 'value': 14})
    spec, report = edit_spatial_model(spec, {'op': 'component.update',
        'target': {'definition_id': 'builtin:video-frame', 'element_id': 'video'},
        'property': 'style.border_radius', 'value': 4})
    assert expand_spatial_scene(spec, 'scene_a')['children'][0]['children'][0]['style']['border_radius'] == 14
    assert expand_spatial_scene(spec, 'scene_b')['children'][0]['children'][0]['style']['border_radius'] == 4
    spec, _ = edit_spatial_model(spec, {'op': 'instance.reset', 'target': {'occurrence_path': path},
                                       'property': 'style.border_radius'})
    assert expand_spatial_scene(spec, 'scene_a')['children'][0]['children'][0]['style']['border_radius'] == 4


@pytest.mark.parametrize('raw', ['null', '{broken', 'false'])
def test_invalid_binding_file_is_structured_and_read_only(saved_project, raw):
    root, before = saved_project
    path = root/'bad.json'
    path.write_text(raw)
    result = CliRunner().invoke(cli, ['composition', 'model', 'layouts', 'apply', 'single',
        '--scene', 'scene_a', '--bindings', str(path)])
    assert result.exit_code == 1
    assert json.loads(result.output)['error']
    assert load_spec() == before


def test_unavailable_asset_is_an_addressable_saveable_draft(saved_project):
    root, _ = saved_project
    path = root/'bindings.json'
    path.write_text(json.dumps({'video': {'asset_id': 'unregistered'}}))
    result = invoke('apply', 'single', '--scene', 'scene_a', '--bindings', str(path))
    assert any(d['code'] == 'unknown_asset' and d['asset_id'] == 'unregistered'
               and d['path'][0] == 'scene_a' for d in result['diagnostics'])
    assert load_spec()['spatial']['scenes']['scene_a'] == result['scene']


def test_repeated_replacement_does_not_recycle_old_occurrence_ids(saved_project):
    seen = set()
    for index in range(4):
        result = invoke('apply', 'single', '--scene', 'scene_a', *(['--replace'] if index else []))
        current = {node['instance_id'] for node in result['scene']['layout']['children']}
        assert not current & seen
        seen.update(current)


def test_optional_null_spatial_document_can_start_a_layout(saved_project):
    spec = load_spec()
    spec['spatial'] = None
    save_spec(spec)
    result = invoke('apply', 'single', '--scene', 'scene_a')
    assert result['writes_spec'] is True
    assert load_spec()['spatial']['scenes']['scene_a'] == result['scene']
