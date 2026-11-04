"""Shared definition edits keep addressing freshly copied local replacements."""
from copy import deepcopy

import pytest

from moviestar.spatial_model import edit_spatial_model, expand_spatial_scene
from moviestar.spatial_saved_layouts import apply_saved_layout, save_layout
from moviestar.spec import load_spec, save_spec
from tests.test_spatial_model import _project


def _project_with_inherited_edit(*, depth=1, component=False, descendants=False):
    spec=_project()
    leaf={'id':'left','type':'image','content':{'empty':True}}
    if descendants:
        leaf={'id':'group','type':'shape','children':[leaf]}
    target=['group','left'] if descendants else ['left']
    definitions={
        'card':{'name':'Card','children':[leaf]},
        'wrapper':{'name':'Wrapper','children':[{
            'instance_id':'inner','definition_id':'card','customizations':[
                {'op':'property_set','path':target,'property':'style.opacity','value':.5}]}]}}
    prefix=['inner']
    root='wrapper'
    for index in range(1,depth):
        identity=f'wrapper{index}'
        definitions[identity]={'name':identity,'children':[{'instance_id':'nested','definition_id':root}]}
        prefix=['nested',*prefix]
        root=identity
    replacement={'id':'left','type':'image','content':{'empty':True}}
    if component:
        definitions['image']={'name':'Image','children':[{'id':'picture','type':'image','content':{'empty':True}}]}
        replacement={'instance_id':'left','definition_id':'image'}
    if descendants:
        replacement={'id':'group','type':'shape','children':[replacement]}
    removed='group' if descendants else 'left'
    edits=[{'op':'child_remove','path':[*prefix,removed]},
           {'op':'child_add','path':prefix,'node':replacement}]
    spec['spatial']['definitions']=definitions
    spec['spatial']['scenes']={'scene_a':{'layout':{'children':[
        {'instance_id':'use','definition_id':root,'customizations':edits}]}}}
    before=expand_spatial_scene(spec,'scene_a')
    assert not before['diagnostics']
    return spec,prefix,target


def _at(expanded,path):
    children=expanded['children']
    for identity in path:
        node=next(n for n in children if n['occurrence_path'][-1]==identity)
        children=node.get('children',[])
    return node


@pytest.mark.parametrize('depth',[1,2])
@pytest.mark.parametrize('component',[False,True])
@pytest.mark.parametrize('descendants',[False,True])
def test_inherited_edit_follows_renamed_local_replacement(depth,component,descendants):
    spec,prefix,target=_project_with_inherited_edit(depth=depth,component=component,descendants=descendants)
    definitions=deepcopy(spec['spatial']['definitions'])
    spec,_=save_layout(spec,'replacement','scene_a')
    changed,report=apply_saved_layout(spec,'replacement','scene_b')
    assert not report['diagnostics']
    path=next(iter(report['input_targets'].values()))['occurrence_path']
    target_path=path[1:-1] if component else path[1:]
    node=_at(expand_spatial_scene(changed,'scene_b'),target_path)
    assert node['style']['opacity']==.5
    assert node['occurrence_path'][-1]!='left'
    assert changed['spatial']['definitions']==definitions
    # Updating the inherited edit later must remain live, rather than baking .5
    # as a new scene override. Explicit outer overrides still take precedence.
    changed['spatial']['definitions']['wrapper']['children'][0]['customizations'][0]['value']=.25
    assert _at(expand_spatial_scene(changed,'scene_b'),target_path)['style']['opacity']==.25
    changed,_=edit_spatial_model(changed,{'op':'instance.customize',
        'target':{'occurrence_path':['scene_b',*target_path]},'property':'style.opacity','value':.8})
    changed['spatial']['definitions']['wrapper']['children'][0]['customizations'][0]['value']=.1
    assert _at(expand_spatial_scene(changed,'scene_b'),target_path)['style']['opacity']==.8


def test_inherited_reference_survives_repeated_save_apply_and_reload(tmp_path):
    spec,_,_=_project_with_inherited_edit(descendants=True)
    spec,_=save_layout(spec,'original','scene_a')
    spec,first=apply_saved_layout(spec,'original','scene_b')
    spec,_=save_layout(spec,'again','scene_b')
    spec,second=apply_saved_layout(spec,'again','scene_a',replace=True)
    save_spec(spec,cwd=tmp_path)
    spec=load_spec(cwd=tmp_path)
    assert not expand_spatial_scene(spec,'scene_a')['diagnostics']
    path=next(iter(second['input_targets'].values()))['occurrence_path']
    assert _at(expand_spatial_scene(spec,'scene_a'),path[1:])['style']['opacity']==.5
    assert path!=next(iter(first['input_targets'].values()))['occurrence_path']
    assert '_copy_identity' not in repr(spec)


def test_inherited_structural_steps_use_the_current_siblings_before_replacement():
    spec,_,_=_project_with_inherited_edit()
    definition=spec['spatial']['definitions']['card']
    definition['children'].append({'id':'right','type':'shape'})
    edits=spec['spatial']['definitions']['wrapper']['children'][0]['customizations']
    edits[:0]=[
        {'op':'child_add','path':[],'after_id':'left','node':{'id':'tag','type':'shape'}},
        {'op':'child_move','path':['left'],'after_id':'right'}]
    before=expand_spatial_scene(spec,'scene_a')
    assert not before['diagnostics']
    spec,_=save_layout(spec,'ordered','scene_a')
    changed,report=apply_saved_layout(spec,'ordered','scene_b')
    assert not report['diagnostics']
    children=expand_spatial_scene(changed,'scene_b')['children'][0]['children'][0]['children']
    assert [n.get('type') for n in children]==['image','shape','shape']
    assert children[0]['style']['opacity']==.5
    assert children[1]['occurrence_path'][-1]=='tag'
    assert children[2]['occurrence_path'][-1]=='right'


def test_inherited_descendant_removal_follows_the_replaced_parent():
    spec,_,_=_project_with_inherited_edit(descendants=True)
    replacement=spec['spatial']['scenes']['scene_a']['layout']['children'][0]['customizations'][1]['node']
    replacement['children'].append({'id':'label','type':'text','content':{'text':'Original'}})
    inherited=spec['spatial']['definitions']['wrapper']['children'][0]['customizations']
    inherited[:]=[{'op':'child_remove','path':['group','left']},
                  {'op':'property_set','path':['group','label'],'property':'style.opacity','value':.5}]
    assert not expand_spatial_scene(spec,'scene_a')['diagnostics']
    spec,_=save_layout(spec,'descendants','scene_a')
    name=spec['spatial']['layouts']['descendants']['inputs'][0]['name']
    changed,report=apply_saved_layout(spec,'descendants','scene_b',{name:{'text':'Fresh'}})
    assert not report['diagnostics']
    group=expand_spatial_scene(changed,'scene_b')['children'][0]['children'][0]['children'][0]
    assert len(group['children'])==1
    assert group['children'][0]['content']=={'text':'Fresh'}
    assert group['children'][0]['style']['opacity']==.5


@pytest.mark.parametrize('depth',[1,2])
@pytest.mark.parametrize('explicit_children',[False,True])
def test_inherited_insertion_expands_a_renamed_leaf_parent(tmp_path,depth,explicit_children):
    spec=_project()
    group={'id':'group','type':'shape'}
    if explicit_children:
        group['children']=[]
    definitions={
        'card':{'name':'Card','children':[deepcopy(group)]},
        'wrapper':{'name':'Wrapper','children':[{
            'instance_id':'inner','definition_id':'card','customizations':[
                {'op':'child_add','path':['group'],
                 'node':{'id':'label','type':'text','content':{'text':'Original'}}}]}]}}
    root='wrapper'
    prefix=['inner']
    if depth==2:
        definitions['outer']={'name':'Outer','children':[
            {'instance_id':'nested','definition_id':root}]}
        root='outer'
        prefix=['nested',*prefix]
    spec['spatial']['definitions']=definitions
    spec['spatial']['scenes']={'scene_a':{'layout':{'children':[
        {'instance_id':'use','definition_id':root,'customizations':[
            {'op':'child_remove','path':[*prefix,'group']},
            {'op':'child_add','path':prefix,'node':deepcopy(group)}]}]}}}
    assert not expand_spatial_scene(spec,'scene_a')['diagnostics']
    spec,_=save_layout(spec,'inserted','scene_a')
    name=spec['spatial']['layouts']['inserted']['inputs'][0]['name']
    spec,report=apply_saved_layout(spec,'inserted','scene_b',{name:{'text':'Fresh'}})
    assert not report['diagnostics']
    path=report['input_targets'][name]['occurrence_path']
    assert path[-2]!='group'
    assert _at(expand_spatial_scene(spec,'scene_b'),path[1:])['content']=={'text':'Fresh'}
    assert spec['spatial']['definitions']==definitions
    # The inherited insertion remains linked through another copy and reload,
    # while its content is supplied by the destination scene's leaf contract.
    spec,_=save_layout(spec,'again','scene_b')
    next_name=spec['spatial']['layouts']['again']['inputs'][0]['name']
    spec,second=apply_saved_layout(spec,'again','scene_a',
        {next_name:{'text':'Again'}},replace=True)
    save_spec(spec,cwd=tmp_path)
    spec=load_spec(cwd=tmp_path)
    expanded=expand_spatial_scene(spec,'scene_a')
    assert not expanded['diagnostics']
    next_path=second['input_targets'][next_name]['occurrence_path']
    assert next_path!=path
    assert _at(expanded,next_path[1:])['content']=={'text':'Again'}
    assert spec['spatial']['definitions']==definitions


@pytest.mark.parametrize('references',[None,'left',[{}],['left','left'],['use']])
def test_invalid_reference_id_metadata_is_rejected(references):
    from moviestar.spec import SpecValidationError,validate_spec
    spec,_,_=_project_with_inherited_edit()
    spec['spatial']['scenes']['scene_a']['layout']['children'][0]['reference_ids']=references
    with pytest.raises(SpecValidationError,match='reference_ids'):
        validate_spec(spec)


def test_ambiguous_prior_child_identity_is_reported_without_choosing_a_target():
    spec,_,_=_project_with_inherited_edit()
    definition=spec['spatial']['definitions']['card']
    definition['children'].append({'id':'other','type':'shape','reference_ids':['left']})
    expanded=expand_spatial_scene(spec,'scene_a')
    assert any(d['code']=='customization_conflict' and 'ambiguous' in d['reason'] for d in expanded['diagnostics'])


def test_duplicate_starts_a_new_sibling_scope_but_preserves_descendant_inheritance():
    spec,_,_=_project_with_inherited_edit()
    spec,_=save_layout(spec,'design','scene_a')
    spec,report=apply_saved_layout(spec,'design','scene_b')
    root=spec['spatial']['scenes']['scene_b']['layout']['children'][0]
    root['reference_ids']=['original_scope']
    path=next(iter(report['input_targets'].values()))['occurrence_path']
    changed,_=edit_spatial_model(spec,{'op':'instance.duplicate','source':{'occurrence_path':path[:2]},
                                     'new_instance_id':'duplicate'})
    duplicate=changed['spatial']['scenes']['scene_b']['layout']['children'][1]
    assert 'reference_ids' not in duplicate
    assert not expand_spatial_scene(changed,'scene_b')['diagnostics']
    replacement=expand_spatial_scene(changed,'scene_b')['children'][1]['children'][0]['children'][0]
    assert replacement['style']['opacity']==.5


@pytest.mark.parametrize('metadata',[42,[{}]])
def test_unrelated_asset_metadata_does_not_become_node_identity_references(metadata):
    spec,_,_=_project_with_inherited_edit()
    spec['spatial']['assets']={'unused':{'type':'image','path':'unused.png','reference_ids':metadata}}
    spec,_=save_layout(spec,'design','scene_a')
    changed,report=apply_saved_layout(spec,'design','scene_b')
    assert not report['diagnostics']
    assert changed['spatial']['assets']==spec['spatial']['assets']
