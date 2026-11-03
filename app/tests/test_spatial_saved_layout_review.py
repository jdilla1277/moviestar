"""Saved reuse respects structural edit identity and surviving content demand."""
from copy import deepcopy

import pytest

from moviestar.spatial_model import expand_spatial_scene
from moviestar.spatial_saved_layouts import apply_saved_layout, save_layout
from tests.test_spatial_model import _project


def _replacement(*, nested=False, rounds=1, early_property=False):
    spec = _project()
    spec['spatial']['definitions'] = {'card': {'name':'Card','children':[
        {'id':'left','type':'image','content':{'empty':True}},
        {'id':'right','type':'image','content':{'empty':True}},
        {'id':'title','type':'text','content':{'text':'Original'}}]}}
    prefix, definition = [], 'card'
    if nested:
        spec['spatial']['definitions']['wrapper'] = {'name':'Wrapper','children':[
            {'instance_id':'inner','definition_id':'card'}]}
        prefix, definition = ['inner'], 'wrapper'
    property_edit = {'op':'property_set','path':[*prefix,'left'],'property':'style.opacity','value':.5}
    # An earlier anchor addresses the inherited node. Later anchors address its
    # replacement. Property overrides use the final node, as the compiler does.
    edits = [{'op':'child_add','path':prefix,'after_id':'left',
              'node':{'id':'tag','type':'shape'}}]
    if early_property:
        edits.append(property_edit)
    for index in range(rounds):
        edits.extend([{'op':'child_remove','path':[*prefix,'left']},
                      {'op':'child_add','path':prefix,'after_id':'right',
                       'node':{'id':'left','type':'image','content':{'empty':True},
                               'style':{'border_width':index+1}}}])
    edits.append({'op':'child_move','path':[*prefix,'right'],'after_id':'left'})
    if not early_property:
        edits.append(property_edit)
    layout = {'children':[{'instance_id':'use','definition_id':definition,'customizations':edits}],
              'reserved_regions':[{'id':'left-region','target':['use',*prefix,'left'],
                                   'allow':[['use',*prefix,'tag']]}]}
    spec['spatial']['scenes'] = {'scene_a':{'layout':layout}}
    assert not expand_spatial_scene(spec,'scene_a')['diagnostics']
    return spec


@pytest.mark.parametrize('nested',[False,True])
@pytest.mark.parametrize('rounds',[1,2])
@pytest.mark.parametrize('early_property',[False,True])
def test_remove_reinsert_reused_ids_preserve_ordered_references(nested,rounds,early_property):
    spec = _replacement(nested=nested,rounds=rounds,early_property=early_property)
    before = deepcopy(spec)
    spec, _ = save_layout(spec,'pair','scene_a')
    values = {e['name']:{'text':'Fresh title'} for e in spec['spatial']['layouts']['pair']['inputs'] if e['type']=='text'}
    planned, report = apply_saved_layout(spec,'pair','scene_b',values)
    assert spec['spatial']['scenes']==before['spatial']['scenes']
    assert planned['spatial']['definitions']==before['spatial']['definitions']
    assert not report['diagnostics']
    expanded = expand_spatial_scene(planned,'scene_b')
    root = expanded['children'][0]
    parent = root['children'][0] if nested else root
    assert [n.get('type') for n in parent['children']] == ['shape','image','image','text']
    _, left, right, _ = parent['children']
    replacement = left['occurrence_path'][-1]
    edits = report['scene']['layout']['children'][0]['customizations']
    removals = [e for e in edits if e['op']=='child_remove']
    assert edits[0]['after_id']=='left'
    assert removals[0]['path'][-1]=='left'
    assert len({e['node']['id'] for e in edits if e['op']=='child_add'})==rounds+1
    if rounds==2:
        first_add = next(e for e in edits if e['op']=='child_add' and e['node']['type']=='image')
        assert removals[1]['path'][-1]==first_add['node']['id']
        assert first_add['node']['id']!=replacement
    assert left['style']=={'border_width':rounds,'opacity':.5}
    assert right['occurrence_path'][-1]=='right'
    assert next(e for e in edits if e['op']=='child_move')['after_id']==replacement
    assert next(e for e in edits if e['op']=='property_set')['path'][-1]==replacement
    assert expanded['reserved_regions'][0]['target']==left['occurrence_path'][1:]
    assert any(t['occurrence_path']==left['occurrence_path'] for t in report['input_targets'].values())
    repeated, repeated_report = apply_saved_layout(planned,'pair','scene_b',values,replace=True)
    assert not repeated_report['diagnostics']
    assert repeated_report['scene']!=report['scene']


def _removed(*, nested=False, consumers=1, title=True):
    spec = _project()
    inputs = [{'name':'footage','type':'video','required':True}]
    children = [{'id':f'clip{i}','type':'video','content':{'input':'footage'}} for i in range(consumers)]
    if title:
        children.append({'id':'title','type':'text','content':{'text':'Original'}})
    definitions = {'card':{'name':'Card','inputs':inputs,'children':children}}
    definition, prefix = 'card', []
    if nested:
        definitions['wrapper'] = {'name':'Wrapper','inputs':inputs,'children':[
            {'instance_id':'inner','definition_id':'card','bindings':{'footage':{'forward':'footage'}}}]}
        definition, prefix = 'wrapper',['inner']
    spec['spatial']['definitions'] = definitions
    spec['spatial']['scenes'] = {'scene_a':{'layout':{'children':[
        {'instance_id':'use','definition_id':definition,'bindings':{'footage':{'asset_id':'alice'}},
         'customizations':[{'op':'child_remove','path':[*prefix,f'clip{i}']} for i in range(consumers)]}]}}}
    assert not expand_spatial_scene(spec,'scene_a')['diagnostics']
    return spec


@pytest.mark.parametrize('nested',[False,True])
@pytest.mark.parametrize('consumers',[1,2])
@pytest.mark.parametrize('title',[False,True])
def test_removed_content_consumers_do_not_leave_unfillable_requirements(nested,consumers,title):
    spec, _ = save_layout(_removed(nested=nested,consumers=consumers,title=title),'card','scene_a')
    entries = spec['spatial']['layouts']['card']['inputs']
    assert len(entries)==int(title)
    values = {e['name']:{'text':'Fresh title'} for e in entries}
    changed, report = apply_saved_layout(spec,'card','scene_b',values)
    assert report['diagnostics']==[]
    assert expand_spatial_scene(changed,'scene_b')['diagnostics']==[]
    assert changed['spatial']['definitions']==spec['spatial']['definitions']


def test_scene_contract_keeps_non_content_requirements_and_legacy_behavior():
    spec = _removed()
    spec['spatial']['definitions']['card']['inputs'].append({'name':'setting','type':'number','required':True})
    spec['spatial']['scenes']['scene_a']['layout']['children'][0]['bindings']['setting']={'number':2}
    spec, _ = save_layout(spec,'card','scene_a')
    # Save keeps non-content settings; an explicitly unfilled setting must still
    # report its requirement under the copied leaf-content contract.
    spec['spatial']['layouts']['card']['layout']['children'][0]['bindings']['setting']={'empty':True}
    _, report=apply_saved_layout(spec,'card','scene_b',{'use_title':{'text':'Fresh'}})
    assert [d['input'] for d in report['diagnostics']]==['setting']
    legacy = _removed()
    legacy['spatial']['scenes']['scene_a']['layout']['children'][0]['bindings']={}
    assert any(d['input']=='footage' for d in expand_spatial_scene(legacy,'scene_a')['diagnostics'])


def test_descendant_operations_resolve_under_the_final_replacement_parent():
    spec = _project()
    spec['spatial']['definitions'] = {'card':{'name':'Card','children':[
        {'id':'group','type':'shape','children':[{'id':'left','type':'image','content':{'empty':True}}]}]}}
    spec['spatial']['scenes'] = {'scene_a':{'layout':{'children':[
        {'instance_id':'use','definition_id':'card','customizations':[
            {'op':'child_remove','path':['group','left']},
            {'op':'child_remove','path':['group']},
            {'op':'child_add','path':[],'node':{'id':'group','type':'shape','children':[
                {'id':'left','type':'image','content':{'empty':True}},
                {'id':'label','type':'text','content':{'text':'Original'}}]}},
            {'op':'property_set','path':['group','label'],'property':'style.opacity','value':.6}]}],
        'reserved_regions':[{'id':'label','target':['use','group','label']}]}}}
    assert not expand_spatial_scene(spec,'scene_a')['diagnostics']
    spec, _=save_layout(spec,'nested','scene_a')
    changed, report=apply_saved_layout(spec,'nested','scene_b',{'use_group_label':{'text':'Fresh'}})
    assert not report['diagnostics']
    expanded=expand_spatial_scene(changed,'scene_b')
    group=expanded['children'][0]['children'][0]
    assert len(group['children'])==1
    label=group['children'][0]
    assert label['content']=={'text':'Fresh'} and label['style']=={'opacity':.6}
    assert expanded['reserved_regions'][0]['target']==label['occurrence_path'][1:]
    edits=report['scene']['layout']['children'][0]['customizations']
    replacement=edits[2]['node']
    assert edits[0]['path']==[replacement['id'],replacement['children'][0]['id']]
    assert edits[1]['path']==['group']
    assert '_copy_identity' not in repr(changed)
    assert '_copy_identity' not in repr(report)


def test_definition_owned_replacements_keep_shared_ids_and_content_rebinds():
    spec=_replacement(rounds=2)
    source=spec['spatial']['scenes']['scene_a']['layout']['children'][0]
    spec['spatial']['definitions']['wrapper']={'name':'Wrapper','children':[deepcopy(source)]}
    spec['spatial']['scenes']['scene_a']['layout']={'children':[
        {'instance_id':'outer','definition_id':'wrapper'}]}
    before=deepcopy(spec['spatial']['definitions'])
    spec, _=save_layout(spec,'nested','scene_a')
    entries=spec['spatial']['layouts']['nested']['inputs']
    values={e['name']:{'text':'Fresh'} for e in entries if e['type']=='text'}
    changed,report=apply_saved_layout(spec,'nested','scene_b',values)
    assert not report['diagnostics']
    assert changed['spatial']['definitions']==before
    wrapper=expand_spatial_scene(changed,'scene_b')['children'][0]
    assert wrapper['children'][0]['children'][1]['occurrence_path'][-1]=='left'


def test_surviving_consumer_still_requires_binding_when_its_sibling_was_removed():
    spec=_removed(consumers=2)
    # Keep one video consumer; the advertised video input must remain required.
    spec['spatial']['scenes']['scene_a']['layout']['children'][0]['customizations'].pop()
    spec,_=save_layout(spec,'partial','scene_a')
    entries=spec['spatial']['layouts']['partial']['inputs']
    values={e['name']:{'text':'Fresh'} for e in entries if e['type']=='text'}
    _,report=apply_saved_layout(spec,'partial','scene_b',values)
    assert len(report['diagnostics'])==1
    assert report['diagnostics'][0]['code']=='missing_binding'
    assert report['diagnostics'][0]['input']=='use_clip1'
    values['use_clip1']={'asset_id':'alice','source':{'start':1,'end':9}}
    _,report=apply_saved_layout(spec,'partial','scene_b',values)
    assert not report['diagnostics']
