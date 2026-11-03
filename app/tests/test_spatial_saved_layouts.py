"""Saved arrangements expose fresh content without changing the source design."""

from copy import deepcopy
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.spatial_model import SpatialModelError, edit_spatial_model, spatial_state_id
from moviestar.spatial_edit import SpatialEditConflict
from moviestar.spec import load_spec, pop_revision, save_spec, validate_spec
from tests.test_spatial_model import _project


def _arrangement():
    spec = _project()
    spec['spatial']['assets'] = {'logo_old': {'type': 'image', 'path': '/missing/old-logo.png'}}
    spec['spatial']['definitions'] = {
        'card': {'name': 'SpeakerCard', 'inputs': [
            {'name': 'footage', 'type': 'video', 'required': True},
            {'name': 'unused', 'type': 'video', 'default': {'asset_id': 'dan'}}],
            'children': [
                {'id': 'video', 'type': 'video', 'content': {'input': 'footage'}},
                {'id': 'logo', 'type': 'image', 'content': {'asset_id': 'logo_old'}},
                {'id': 'label', 'type': 'text', 'content': {'text': 'Old speaker'}},
                {'id': 'captions', 'type': 'caption', 'content': {'track': 'prior_captions'}}]},
        'outer': {'name': 'Outer', 'children': [
            {'instance_id': 'card', 'definition_id': 'card',
             'bindings': {'footage': {'asset_id': 'bob', 'source': {'start': 2, 'end': 7}}}}]},
    }
    layout = {'display': 'grid', 'columns': '1fr 1fr', 'gap': 16,
        'format_rules': [{'id': 'portrait', 'when': {'orientation': 'portrait'}, 'layout': {'columns': '1fr'}}],
        'children': [
            {'id': 'main', 'type': 'video', 'content': {'asset_id': 'alice', 'source': {'start': 1, 'end': 6}}},
            {'instance_id': 'speaker', 'definition_id': 'outer',
             'motion': {'translate_x': [{'at': 0, 'value': 0}, {'at': 2, 'value': 24}]},
             'customizations': [
                 {'op': 'property_set', 'path': ['card', 'video'], 'property': 'style.border_width', 'value': 3},
                 {'op': 'child_add', 'path': ['card'], 'after_id': 'label', 'node': {
                     'id': 'local_video', 'type': 'video', 'content': {'asset_id': 'carol', 'source': {'start': 3, 'end': 7}}}}]},
            {'id': 'title', 'type': 'text', 'content': {'text': 'The old interview'},
             'style': {'font_family': 'sans-serif', 'font_size': 40}}],
        'reserved_regions': [{'id': 'title_area', 'target': ['title']}]}
    spec['spatial']['scenes'] = {'scene_a': {'layout': layout}}
    validate_spec(spec)
    return spec


def _content_references(value):
    if isinstance(value, dict):
        if 'asset_id' in value or 'source' in value or 'track' in value:
            yield value
        for child in value.values():
            yield from _content_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _content_references(child)


def test_save_exposes_direct_nested_inherited_and_local_content_without_mutating_source():
    from moviestar.spatial_saved_layouts import save_layout, inspect_saved_layout
    spec = _arrangement()
    before = deepcopy(spec)
    changed, report = save_layout(spec, 'interview', 'scene_a', name='Interview')
    assert spec == before
    assert changed['composition'] == before['composition']
    assert changed['spatial']['definitions'] == before['spatial']['definitions']
    assert changed['spatial']['scenes'] == before['spatial']['scenes']
    saved = changed['spatial']['layouts']['interview']
    assert not list(_content_references(saved))
    assert {entry['type'] for entry in saved['inputs']} == {'video','image','text','caption'}
    assert len(saved['inputs']) == 7
    assert len({entry['name'] for entry in saved['inputs']}) == 7
    assert all('default' not in entry for entry in saved['inputs'])
    assert saved['layout']['children'][1]['definition_id'] == 'outer'
    assert saved['layout']['children'][1]['customizations'][0] == before['spatial']['scenes']['scene_a']['layout']['children'][1]['customizations'][0]
    view = inspect_saved_layout(changed, 'interview')
    assert not list(_content_references(view))
    assert view['design_dependencies'] == {'components': ['card','outer'], 'fonts': ['sans-serif']}
    assert view['motion']['last_keyframe_s'] == 2
    assert view['diagnostics'] == []
    assert view['can_apply'] is False
    assert 'scene_a' not in json.dumps(saved)
    assert saved['layout']['reserved_regions'] == before['spatial']['scenes']['scene_a']['layout']['reserved_regions']
    assert report['state_id_before'] == spatial_state_id(before)
    validate_spec(changed)


def test_keep_text_is_explicit_and_never_keeps_media_or_caption_defaults():
    from moviestar.spatial_saved_layouts import save_layout
    spec, _ = save_layout(_arrangement(), 'interview', 'scene_a', keep_text=True)
    inputs = spec['spatial']['layouts']['interview']['inputs']
    assert {i['default']['text'] for i in inputs if 'default' in i} == {'Old speaker', 'The old interview'}
    assert all(i['type'] == 'text' for i in inputs if 'default' in i)
    assert not list(_content_references(spec['spatial']['layouts']['interview']))


@pytest.fixture
def project(tmp_path, monkeypatch):
    save_spec(_arrangement(), cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _invoke(*args):
    result = CliRunner().invoke(cli, ['composition','model','layouts',*args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_cli_save_dry_run_discovery_reload_and_undo(project):
    before = load_spec()
    preview = _invoke('save','interview','--scene','scene_a','--name','Interview','--dry-run')
    assert preview['writes_spec'] is False
    assert load_spec() == before
    result = _invoke('save','interview','--scene','scene_a','--name','Interview',
                      '--expected-state-id',preview['state_id_before'])
    assert result['writes_spec'] is True
    saved = load_spec()
    assert saved['spatial']['layouts']['interview'] == preview['saved_layout']
    assert spatial_state_id(saved) == result['state_id_after'] == preview['state_id_after']
    assert len(saved['revisions']) == len(before['revisions'])+1
    listed = _invoke('list')
    assert {i['id'] for i in listed['layouts']} == {'single','two-up','pip','text-video','saved:interview'}
    shown = _invoke('show','saved:interview')
    assert shown['name'] == 'Interview' and shown['writes_spec'] is False
    assert not list(_content_references(shown))
    restored, _ = pop_revision(saved)
    assert restored['spatial'] == before['spatial']


def test_replace_and_stale_state_fail_before_writing(project):
    _invoke('save','interview','--scene','scene_a')
    before = load_spec()
    for extra, error in [([], 'replace'), (['--replace','--expected-state-id','stale'], 'expected_state_id')]:
        result = CliRunner().invoke(cli, ['composition','model','layouts','save','interview','--scene','scene_a',*extra])
        assert result.exit_code == 1
        assert error in json.loads(result.output)['error']
        assert load_spec() == before
    _invoke('save','interview','--scene','scene_a','--replace','--keep-text')
    restored, _ = pop_revision(load_spec())
    assert restored['spatial'] == before['spatial']


@pytest.mark.parametrize('problem', ['cycle','unknown','customization'])
def test_unresolvable_nested_design_reports_paths_without_saving(project, problem):
    spec = load_spec()
    if problem == 'cycle':
        spec['spatial']['definitions']['card']['children'].append({'instance_id':'loop','definition_id':'outer'})
    elif problem == 'unknown':
        spec['spatial']['definitions']['outer']['children'][0]['definition_id'] = 'missing'
    else:
        spec['spatial']['scenes']['scene_a']['layout']['children'][1]['customizations'][0]['path'] = ['gone']
    save_spec(spec)
    before = load_spec()
    result = CliRunner().invoke(cli, ['composition','model','layouts','save','interview','--scene','scene_a'])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload['diagnostics'] and all(i['path'] for i in payload['diagnostics'])
    assert load_spec() == before


def test_saved_layout_references_protect_components_after_source_scene_is_removed(project):
    _invoke('save','interview','--scene','scene_a')
    spec = load_spec()
    spec['spatial']['scenes'] = {}
    save_spec(spec)
    with pytest.raises(SpatialModelError, match='referenced'):
        edit_spatial_model(load_spec(), {'op':'component.delete','definition_id':'outer'})


def test_shared_design_edits_flow_but_content_target_changes_conflict(project):
    from moviestar.spatial_saved_layouts import inspect_saved_layout
    _invoke('save','interview','--scene','scene_a')
    spec = load_spec()
    changed, _ = edit_spatial_model(spec, {'op':'component.update',
        'target':{'definition_id':'card','element_id':'logo'}, 'property':'style.border_radius','value':10})
    assert inspect_saved_layout(changed,'interview')['diagnostics'] == []
    with pytest.raises(SpatialEditConflict) as caught:
        edit_spatial_model(changed, {'op':'component.child_insert','target':{'definition_id':'card'},
            'child':{'id':'new_footage','type':'video','content':{'asset_id':'dan'}},'after_id':'video'})
    assert any(i['code'] == 'saved_layout_content_changed' for i in caught.value.conflicts)
    assert 'saved layout' in caught.value.hint.lower()


def test_malformed_saved_inputs_cannot_smuggle_content_defaults(project):
    _invoke('save','interview','--scene','scene_a')
    before = load_spec()
    bad = deepcopy(before)
    video = next(i for i in bad['spatial']['layouts']['interview']['inputs'] if i['type']=='video')
    video['default'] = {'asset_id':'alice'}
    from moviestar.spec import SpecValidationError
    with pytest.raises(SpecValidationError, match='default'):
        save_spec(bad)
    assert load_spec() == before


@pytest.mark.parametrize('problem', ['shape','metadata'])
def test_unexposable_content_in_an_inherited_definition_is_reported(project, problem):
    spec = load_spec()
    if problem == 'shape':
        spec['spatial']['definitions']['card']['children'].append({
            'id':'flattened','type':'shape','content':{'asset_id':'logo_old'}})
    else:
        spec['spatial']['definitions']['card']['children'][0]['source_cue'] = {'word_id':'prior-word'}
    save_spec(spec)
    before = load_spec()
    result = CliRunner().invoke(cli, ['composition','model','layouts','save','interview','--scene','scene_a'])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert any('card' in d['path'] for d in payload['diagnostics'])
    assert load_spec() == before


@pytest.mark.parametrize('problem', ['duplicate','media_default','unmasked','bad_target','bad_region'])
def test_malformed_layout_document_is_rejected_before_replacing_readable_state(project, problem):
    from moviestar.spec import SpecValidationError
    _invoke('save','interview','--scene','scene_a')
    before = load_spec()
    bad = deepcopy(before)
    item = bad['spatial']['layouts']['interview']
    if problem == 'duplicate':
        item['inputs'].append({**deepcopy(item['inputs'][0]), 'name':'another_name'})
    elif problem == 'media_default':
        item['inputs'][0]['default'] = {'asset_id':'alice'}
    elif problem == 'unmasked':
        item['layout']['children'][0]['content'] = {'asset_id':'alice'}
    elif problem == 'bad_target':
        item['inputs'][0]['target'] = []
    else:
        item['layout']['reserved_regions'][0]['target'] = []
    with pytest.raises(SpecValidationError):
        save_spec(bad)
    assert load_spec() == before


def test_saved_layout_input_names_are_unique_even_when_paths_share_a_joined_name(project):
    from moviestar.spatial_saved_layouts import save_layout
    spec = load_spec()
    spec['spatial']['scenes']['scene_a']['layout'] = {'children':[
        {'id':'a_b','type':'shape','children':[{'id':'c','type':'text','content':{'text':'one'}}]},
        {'id':'a','type':'shape','children':[{'id':'b_c','type':'text','content':{'text':'two'}}]}]}
    changed, _ = save_layout(spec, 'names','scene_a')
    names = [entry['name'] for entry in changed['spatial']['layouts']['names']['inputs']]
    assert len(names) == len(set(names)) == 2


def test_incomplete_source_can_be_saved_without_inheriting_old_unknown_assets(project):
    spec = load_spec()
    spec['spatial']['scenes']['scene_a']['layout']['children'][0]['content'] = {'asset_id':'unknown'}
    save_spec(spec)
    _invoke('save','draft','--scene','scene_a')
    assert not list(_content_references(_invoke('show','saved:draft')))


@pytest.mark.parametrize('identity', ['single','two-up','pip','text-video'])
def test_builtin_arrangements_can_be_saved_with_all_content_open(identity):
    from moviestar.spatial_catalog import apply_builtin_layout
    from moviestar.spatial_saved_layouts import save_layout, inspect_saved_layout
    spec = _project()
    spec['spatial']['scenes'] = {}
    spec, _ = apply_builtin_layout(spec, identity, 'scene_a')
    changed, _ = save_layout(spec, 'mine','scene_a')
    view = inspect_saved_layout(changed, 'mine')
    assert view['diagnostics'] == []
    assert all('default' not in entry for entry in view['inputs'])
    assert any(entry['type'] == 'caption' for entry in view['inputs'])
    assert changed['spatial']['definitions'] == spec['spatial']['definitions']


def test_compatible_structural_design_changes_flow_to_saved_layouts(project):
    from moviestar.spatial_saved_layouts import inspect_saved_layout
    _invoke('save','interview','--scene','scene_a')
    changed, _ = edit_spatial_model(load_spec(), {'op':'component.child_insert',
        'target':{'definition_id':'card'}, 'child':{'id':'decoration','type':'shape',
        'style':{'background_color':'blue'}},'after_id':'video'})
    view = inspect_saved_layout(changed,'interview')
    assert view['diagnostics'] == []
    assert 'decoration' in json.dumps(view['expanded'])


def test_external_content_change_is_inspectable_as_stale_without_importing_the_asset(project):
    _invoke('save','interview','--scene','scene_a')
    spec = load_spec()
    spec['spatial']['definitions']['card']['children'].append({
        'id':'new_video','type':'video','content':{'asset_id':'unreviewed_asset'}})
    save_spec(spec)
    view = _invoke('show','saved:interview')
    assert any(i['code'] == 'saved_layout_content_changed' for i in view['diagnostics'])
    assert 'unreviewed_asset' not in json.dumps(view)


def test_shared_edit_reports_saved_layout_impact_even_without_source_scene(project):
    _invoke('save','interview','--scene','scene_a')
    spec = load_spec()
    spec['spatial']['scenes'] = {}
    changed, report = edit_spatial_model(spec, {'op':'component.update',
        'target':{'definition_id':'card','element_id':'logo'}, 'property':'style.border_radius','value':10})
    assert report['affected'] == []
    assert report['affected_layouts'] == ['saved:interview']
    assert changed['spatial']['layouts'] == spec['spatial']['layouts']


def test_raw_text_defaults_use_the_normal_typed_content_form(project):
    _invoke('save','interview','--scene','scene_a','--keep-text')
    spec = load_spec()
    entry = next(i for i in spec['spatial']['layouts']['interview']['inputs'] if i['type']=='text')
    entry['default'] = 'A retained heading'
    save_spec(spec)
    shown = _invoke('show','saved:interview')
    assert 'A retained heading' in json.dumps(shown['expanded'])


@pytest.mark.parametrize('operation', ['remove','insert'])
def test_content_topology_changes_in_saved_only_components_conflict(project, operation):
    _invoke('save','interview','--scene','scene_a')
    spec = load_spec()
    spec['spatial']['scenes'] = {}
    request = {'op':'component.child_remove','target':{'definition_id':'card','element_id':'logo'}}
    if operation == 'insert':
        request = {'op':'component.child_insert','target':{'definition_id':'card'},
            'child':{'id':'new_video','type':'video','content':{'asset_id':'dan'}},'after_id':'video'}
    with pytest.raises(SpatialEditConflict, match='saved layout'):
        edit_spatial_model(spec, request)


def test_empty_optional_image_and_caption_regions_do_not_become_mandatory_content():
    from moviestar.spatial_catalog import apply_builtin_layout
    from moviestar.spatial_saved_layouts import save_layout
    spec = _project()
    spec['spatial']['scenes'] = {}
    spec, _ = apply_builtin_layout(spec, 'text-video', 'scene_a')
    spec, _ = save_layout(spec,'mine','scene_a')
    inputs = spec['spatial']['layouts']['mine']['inputs']
    assert all(not i['required'] for i in inputs if i['type'] in ('image','caption'))
    assert all(i['required'] for i in inputs if i['type'] in ('video','text'))


@pytest.mark.parametrize('problem', ['instance_content','element_bindings','style','customization'])
def test_ignored_or_invalid_fields_cannot_hide_old_content_in_a_saved_prototype(project, problem):
    spec = load_spec()
    nodes = spec['spatial']['scenes']['scene_a']['layout']['children']
    if problem == 'instance_content':
        nodes[1]['content'] = {'asset_id':'alice'}
    elif problem == 'element_bindings':
        nodes[0]['bindings'] = {'unused':{'asset_id':'alice'}}
    elif problem == 'style':
        nodes[0]['style'] = {'background_color':{'asset_id':'alice'}}
    else:
        nodes[1]['customizations'].append({'op':'property_set','path':['card','video'],
            'property':'style.background_color','value':{'asset_id':'alice'}})
    save_spec(spec)
    before = load_spec()
    result = CliRunner().invoke(cli,['composition','model','layouts','save','interview','--scene','scene_a'])
    assert result.exit_code == 1
    assert json.loads(result.output)['diagnostics']
    assert load_spec() == before
