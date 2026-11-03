"""Saved designs become independent scene copies with linked components."""
from copy import deepcopy
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.spatial_model import SpatialModelError, edit_spatial_model, expand_spatial_scene, spatial_state_id
from moviestar.spec import load_spec, pop_revision, save_spec, validate_spec
from tests.test_spatial_saved_layouts import _arrangement, _shared_content_draft


def _saved(keep_text=False):
    from moviestar.spatial_saved_layouts import save_layout
    spec = save_layout(_arrangement(), 'interview', 'scene_a', keep_text=keep_text)[0]
    spec['spatial']['assets']['new_logo'] = {'type': 'image', 'path': 'new-logo.png'}
    return spec


def _bindings(spec, start=4, text='New interview'):
    values = {'video': {'asset_id': 'dan', 'source': {'start': start, 'end': start+4}},
              'image': {'asset_id': 'new_logo'}, 'caption': {'track': 'new_captions'}, 'text': {'text': text}}
    return {entry['name']: deepcopy(values[entry['type']])
            for entry in spec['spatial']['layouts']['interview']['inputs']}


def _leaves(tree):
    result = {}
    def visit(nodes):
        for node in nodes:
            if node.get('type') in {'video','text','image','caption'}:
                result[tuple(node['occurrence_path'])] = node
            visit(node.get('children', []))
    visit(tree['children'])
    return result


def test_apply_rebinds_every_nested_leaf_without_content_fallback_or_definition_changes():
    from moviestar.spatial_saved_layouts import apply_saved_layout
    spec = _saved()
    before = deepcopy(spec)
    values = _bindings(spec)
    changed, report = apply_saved_layout(spec, 'interview', 'scene_b', values)
    assert spec == before
    assert changed['composition'] == before['composition']
    assert changed['sources'] == before['sources']
    assert changed['spatial']['definitions'] == before['spatial']['definitions']
    assert changed['spatial']['scenes']['scene_a'] == before['spatial']['scenes']['scene_a']
    assert changed['spatial']['layouts'] == before['spatial']['layouts']
    expanded = expand_spatial_scene(changed, 'scene_b')
    assert expanded['diagnostics'] == []
    leaves = _leaves(expanded)
    assert len(leaves) == 7
    for name, target in report['input_targets'].items():
        assert leaves[tuple(target['occurrence_path'])]['content'] == values[name]
    assert report['reused_definitions'] == ['card','outer']
    assert report['captured_definitions'] == []
    assert report['state_id_after'] == spatial_state_id(changed)
    assert expanded['reserved_regions'][0]['target'][0] != 'title'
    assert changed['spatial']['scenes']['scene_b']['layout']['children'][1]['motion'] == before['spatial']['scenes']['scene_a']['layout']['children'][1]['motion']
    validate_spec(changed)


def test_draft_has_one_missing_diagnostic_per_required_leaf_and_never_uses_old_media():
    from moviestar.spatial_saved_layouts import apply_saved_layout
    changed, report = apply_saved_layout(_saved(), 'interview', 'scene_b')
    leaves = _leaves(expand_spatial_scene(changed, 'scene_b'))
    assert len(report['diagnostics']) == 7
    assert {entry['code'] for entry in report['diagnostics']} == {'missing_binding'}
    assert {tuple(entry['path']) for entry in report['diagnostics']} == set(leaves)
    assert all(node['content'] in (None, {'empty': True}) for node in leaves.values())


@pytest.mark.parametrize('kind', ['image','caption'])
@pytest.mark.parametrize('structure', ['direct','wrapper','nested','inner_required'])
def test_shared_required_input_can_be_replaced_independently_at_every_consumer(kind, structure):
    from moviestar.spatial_saved_layouts import apply_saved_layout, save_layout
    spec = _shared_content_draft(kind, structure)
    spec = save_layout(spec, 'pair', 'scene_a')[0]
    entries = spec['spatial']['layouts']['pair']['inputs']
    values = {entries[0]['name']: {'empty': True}, entries[1]['name']:
              {'asset_id': 'new_logo'} if kind == 'image' else {'track': 'new_words'}}
    spec['spatial']['assets'] = {'new_logo': {'type':'image','path':'new.png'}}
    changed, report = apply_saved_layout(spec, 'pair', 'scene_b', values, replace=True)
    assert len(report['diagnostics']) == 1
    assert report['diagnostics'][0]['path'] == report['input_targets'][entries[0]['name']]['occurrence_path']
    leaves = _leaves(expand_spatial_scene(changed, 'scene_b'))
    assert leaves[tuple(report['input_targets'][entries[1]['name']]['occurrence_path'])]['content'] == values[entries[1]['name']]


def test_default_text_optional_regions_and_explicit_empty_are_preserved():
    from moviestar.spatial_saved_layouts import apply_saved_layout
    spec = _saved(keep_text=True)
    changed, report = apply_saved_layout(spec, 'interview', 'scene_b')
    leaves = _leaves(expand_spatial_scene(changed, 'scene_b'))
    assert {n['content']['text'] for n in leaves.values() if n['type']=='text'} == {'Old speaker','The old interview'}
    title = next(e for e in spec['spatial']['layouts']['interview']['inputs'] if e['target']==['title'])
    changed, report = apply_saved_layout(spec, 'interview', 'scene_b', {title['name']:{'empty':True}})
    assert _leaves(expand_spatial_scene(changed,'scene_b'))[tuple(report['input_targets'][title['name']]['occurrence_path'])]['content'] is None


def test_scene_inputs_can_be_rebound_after_reload_and_copies_keep_linked_styles(tmp_path):
    from moviestar.spatial_saved_layouts import apply_saved_layout, delete_saved_layout
    spec = _saved()
    spec, a = apply_saved_layout(spec, 'interview', 'scene_a', _bindings(spec), replace=True)
    spec, b = apply_saved_layout(spec, 'interview', 'scene_b', _bindings(spec, 8, 'Second'))
    assert {tuple(t['occurrence_path']) for t in a['input_targets'].values()}.isdisjoint(
        {tuple(t['occurrence_path']) for t in b['input_targets'].values()})
    path = a['input_targets']['speaker_card_video']['occurrence_path']
    spec, _ = edit_spatial_model(spec, {'op':'instance.customize','target':{'occurrence_path':path},
                                      'property':'style.border_radius','value':12})
    spec, _ = edit_spatial_model(spec, {'op':'component.update','target':{'definition_id':'card','element_id':'video'},
                                      'property':'style.border_radius','value':4})
    assert _leaves(expand_spatial_scene(spec,'scene_a'))[tuple(path)]['style']['border_radius'] == 12
    other = b['input_targets']['speaker_card_video']['occurrence_path']
    assert _leaves(expand_spatial_scene(spec,'scene_b'))[tuple(other)]['style']['border_radius'] == 4
    spec, _ = delete_saved_layout(spec, 'interview')
    save_spec(spec, cwd=tmp_path)
    spec = load_spec(cwd=tmp_path)
    spec, _ = edit_spatial_model(spec, {'op':'scene.bind','target':{'scene_id':'scene_b'},
                                      'input':'speaker_card_label','value':{'text':'Updated'}})
    assert _leaves(expand_spatial_scene(spec,'scene_b'))[tuple(b['input_targets']['speaker_card_label']['occurrence_path'])]['content'] == {'text':'Updated'}
    assert 'interview' not in spec['spatial']['layouts']


@pytest.mark.parametrize('problem', ['unknown','type','range','stale','scene','replace','topology'])
def test_apply_rejects_invalid_proposals_without_mutating(problem):
    from moviestar.spatial_saved_layouts import apply_saved_layout
    spec = _saved()
    values = _bindings(spec)
    options = {}
    scene = 'scene_b'
    if problem=='unknown': values['typo'] = {'text':'oops'}
    if problem=='type': values['main'] = {'text':'oops'}
    if problem=='range': values['main']['source']['end'] = 1
    if problem=='stale': options['expected_state_id'] = 'stale'
    if problem=='scene': scene = 'missing'
    if problem=='replace': scene = 'scene_a'
    if problem=='topology': spec['spatial']['definitions']['card']['children'].pop()
    before = deepcopy(spec)
    with pytest.raises(SpatialModelError):
        apply_saved_layout(spec, 'interview', scene, values, **options)
    assert spec == before


def test_cli_apply_delete_and_undo_are_atomic_and_dry_run_matches(tmp_path, monkeypatch):
    before = save_spec(_saved(), cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    bindings = tmp_path/'bindings.json'
    bindings.write_text(json.dumps(_bindings(before)))
    def invoke(*args):
        result = CliRunner().invoke(cli, ['composition','model','layouts',*args])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)
    assert invoke('show','saved:interview')['can_apply'] is True
    planned = invoke('apply','saved:interview','--scene','scene_b','--bindings',str(bindings),'--dry-run')
    assert load_spec() == before
    applied = invoke('apply','saved:interview','--scene','scene_b','--bindings',str(bindings),
                     '--expected-state-id',planned['state_id_before'])
    after = load_spec()
    assert applied['state_id_after'] == planned['state_id_after'] == spatial_state_id(after)
    assert applied['scene'] == planned['scene']
    assert len(after['revisions']) == len(before['revisions'])+1
    assert pop_revision(after)[0]['spatial'] == before['spatial']
    scenes = deepcopy(after['spatial']['scenes'])
    invoke('delete','saved:interview','--dry-run')
    assert load_spec() == after
    invoke('delete','saved:interview')
    deleted = load_spec()
    assert deleted['spatial']['scenes'] == scenes
    assert pop_revision(deleted)[0]['spatial'] == after['spatial']


def test_replacements_allocate_fresh_local_child_ids_and_remap_all_paths():
    from moviestar.spatial_saved_layouts import apply_saved_layout
    spec = _saved()
    saved = spec['spatial']['layouts']['interview']
    owner = saved['layout']['children'][1]
    owner['customizations'].append({'op':'property_set','path':['card','local_video'],
                                    'property':'style.opacity','value':.5})
    saved['layout']['reserved_regions'] = [{'id':'local','target':['speaker','card','local_video'],
                                           'allow':[['speaker','card','label']]}]
    seen = set()
    for index in range(3):
        spec, report = apply_saved_layout(spec,'interview','scene_b',_bindings(spec),replace=index>0)
        path = report['input_targets']['speaker_card_local_video']['occurrence_path']
        assert path[-1] != 'local_video' and path[-1] not in seen
        seen.add(path[-1])
        expanded = expand_spatial_scene(spec,'scene_b')
        assert not expanded['diagnostics']
        assert _leaves(expanded)[tuple(path)]['style']['opacity'] == .5
        assert expanded['reserved_regions'][0]['target'] == path[1:]
        assert expanded['reserved_regions'][0]['allow'][0][0] == path[1]


def test_scene_input_contract_survives_saving_again_and_saved_replacement_leaves_copies_intact():
    from moviestar.spatial_saved_layouts import apply_saved_layout, save_layout
    spec = _saved()
    spec, _ = apply_saved_layout(spec,'interview','scene_b')
    spec, _ = save_layout(spec,'again','scene_b')
    assert all(e['required'] for e in spec['spatial']['layouts']['again']['inputs'])
    scene = deepcopy(spec['spatial']['scenes']['scene_b'])
    spec, _ = save_layout(spec,'interview','scene_a',replace=True,keep_text=True)
    assert spec['spatial']['scenes']['scene_b'] == scene
    assert len(expand_spatial_scene(spec,'scene_b')['diagnostics']) == 7


def test_scene_instance_duplicate_keeps_its_content_assignments_independently():
    from moviestar.spatial_saved_layouts import apply_saved_layout, delete_saved_layout
    spec, report = apply_saved_layout(_saved(),'interview','scene_b',_bindings(_saved()))
    spec, _ = delete_saved_layout(spec,'interview')
    path = report['input_targets']['speaker_card_video']['occurrence_path']
    spec, _ = edit_spatial_model(spec, {'op':'instance.duplicate','source':{'occurrence_path':path[:2]},
                                      'new_instance_id':'copy'})
    leaves = _leaves(expand_spatial_scene(spec,'scene_b'))
    assert leaves[('scene_b','copy','card','video')]['content'] == leaves[tuple(path)]['content']
    assert leaves[('scene_b','copy','card','label')]['content'] == {'text':'New interview'}


def test_shared_removal_conflicts_with_scene_inputs_even_after_catalog_deletion():
    from moviestar.spatial_saved_layouts import apply_saved_layout, delete_saved_layout
    from moviestar.spatial_edit import SpatialEditConflict
    spec, _ = apply_saved_layout(_saved(),'interview','scene_b',_bindings(_saved()))
    spec, _ = delete_saved_layout(spec,'interview')
    before = deepcopy(spec)
    with pytest.raises(SpatialEditConflict) as exc:
        edit_spatial_model(spec,{'op':'component.child_remove','target':{'definition_id':'card','element_id':'logo'}})
    assert any(d['code']=='content_binding_conflict' for d in exc.value.conflicts)
    assert spec == before


def test_partial_forwarded_override_still_reports_unbound_required_upstream_input():
    spec = _shared_content_draft('image','nested')
    spec['spatial']['scenes']['scene_a'].update(inputs=[{'name':'one','type':'image','required':False,
        'target':['use','wrapper','pair','left']}], bindings={'one':{'empty':True}})
    expanded = expand_spatial_scene(spec,'scene_a')
    assert any(d['code']=='missing_binding' and d['path']==['scene_a','use'] for d in expanded['diagnostics'])


@pytest.mark.parametrize('problem', ['duplicate','unknown_binding','wrong_type','media_default','bad_target'])
def test_malformed_scene_contract_is_not_readable(problem):
    from moviestar.spatial_saved_layouts import apply_saved_layout
    from moviestar.spec import SpecValidationError
    spec, _ = apply_saved_layout(_saved(),'interview','scene_b')
    scene = spec['spatial']['scenes']['scene_b']
    if problem=='duplicate':
        scene['inputs'][1]['target'] = scene['inputs'][0]['target']
    if problem=='unknown_binding': scene['bindings']['typo'] = {'text':'bad'}
    if problem=='wrong_type': scene['bindings']['main'] = {'text':'bad'}
    if problem=='media_default': scene['inputs'][0]['default'] = {'asset_id':'alice'}
    if problem=='bad_target': scene['inputs'][0]['target'] = 'main'
    with pytest.raises(SpecValidationError): validate_spec(spec)


def test_shorter_destination_reports_motion_without_changing_duration_or_audio():
    from moviestar.spatial_saved_layouts import apply_saved_layout
    spec = _saved()
    spec['composition'][1].update(slots=[],duration=1)
    changed, report = apply_saved_layout(spec,'interview','scene_b',_bindings(spec))
    warnings = [d for d in report['diagnostics'] if d['code']=='motion_exceeds_scene_duration']
    assert len(warnings)==1 and warnings[0]['scene_duration_s']==1
    assert warnings[0]['last_keyframe_s']==2 and warnings[0]['severity']=='warning'
    assert changed['composition']==spec['composition']


def test_unavailable_new_media_is_a_saveable_draft_with_actual_target():
    from moviestar.spatial_saved_layouts import apply_saved_layout
    values = _bindings(_saved())
    values['main']['asset_id'] = 'unavailable'
    _, report = apply_saved_layout(_saved(),'interview','scene_b',values)
    diagnostic = next(d for d in report['diagnostics'] if d['code']=='unknown_asset')
    assert diagnostic['path'] == report['input_targets']['main']['occurrence_path']


@pytest.mark.parametrize('kind', ['image','caption'])
def test_optional_regions_remain_empty_without_required_diagnostics(kind):
    from moviestar.spatial_saved_layouts import apply_saved_layout, save_layout
    spec = _shared_content_draft(kind,'nested',required=False)
    spec = save_layout(spec,'optional','scene_a')[0]
    changed, report = apply_saved_layout(spec,'optional','scene_b')
    assert not report['diagnostics']
    assert all(n['content'] in (None,{'empty':True}) for n in _leaves(expand_spatial_scene(changed,'scene_b')).values())


@pytest.mark.parametrize('identity,expected', [('missing',None),('interview','stale')])
def test_delete_guards_are_read_only(identity, expected):
    from moviestar.spatial_saved_layouts import delete_saved_layout
    spec = _saved()
    before = deepcopy(spec)
    with pytest.raises(SpatialModelError): delete_saved_layout(spec,identity,expected_state_id=expected)
    assert spec == before


@pytest.mark.parametrize('proposal', [
    {'target':{'scene_id':'scene_b'},'input':'main','value':{'text':'wrong'}},
    {'target':{'scene_id':[]},'input':'main','value':{'asset_id':'alice'}},
    {'target':{'scene_id':'scene_b'},'input':[],'value':{'asset_id':'alice'}}])
def test_scene_bind_malformed_requests_fail_without_mutation(proposal):
    from moviestar.spatial_saved_layouts import apply_saved_layout
    spec, _ = apply_saved_layout(_saved(),'interview','scene_b')
    before = deepcopy(spec)
    with pytest.raises(SpatialModelError): edit_spatial_model(spec,{'op':'scene.bind',**proposal})
    assert spec == before


def test_asset_deletion_checks_new_scene_bindings_and_inspect_exposes_rebind_contract(tmp_path,monkeypatch):
    from moviestar.spatial_saved_layouts import apply_saved_layout, delete_saved_layout
    spec, _ = apply_saved_layout(_saved(),'interview','scene_b',_bindings(_saved()))
    spec, _ = delete_saved_layout(spec,'interview')
    with pytest.raises(SpatialModelError,match='referenced'):
        edit_spatial_model(spec,{'op':'asset.delete','asset_id':'new_logo'})
    save_spec(spec,cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    result=CliRunner().invoke(cli,['composition','model','inspect','scene_b'])
    assert result.exit_code==0, result.output
    inspection=json.loads(result.output)
    assert len(inspection['inputs'])==7
    assert inspection['bindings']==_bindings(_saved())
