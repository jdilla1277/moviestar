"""A reused nested design uses new content through real preview and export."""
from copy import deepcopy
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available, measure_spatial_layout
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_render import render_spatial_frame
from moviestar.spatial_saved_layouts import apply_saved_layout, save_layout
from moviestar.spec import load_spec, save_spec
from tests.test_spatial_captions import _cue
from tests.test_spatial_render import _rgb, project
from tests.test_spatial_video import _pixels, _rms, video_project


def _reuse(spec):
    # A customized nested component, two video ranges, text, transparent image,
    # explicit caption region, motion, and a responsive rule use one saved design.
    original = spec['spatial']['scenes']['interview']['layout']
    original['children'][0]['customizations'] = [
        {'op':'property_set','path':['panel','footage'],'property':'style.border_radius','value':4},
        {'op':'child_add','path':['panel'],'after_id':'badge','node':{
            'id':'tag','type':'text','content':{'text':'Old tag'},
            'layout':{'position':'absolute','left':4,'bottom':0},'style':{'font_size':10}}}]
    original['children'][0]['motion'] = {'translate_x':[{'at':0,'value':0},{'at':.3,'value':6}]}
    original['format_rules'] = [{'id':'portrait','when':{'orientation':'portrait'},
                                 'layout':{'columns':'1fr','rows':'1fr 1fr'}}]
    original['children'].append({'id':'words','type':'caption','content':{'track':'captions'},
        'layout':{'position':'absolute','left':'10%','bottom':2,'width':'80%','height':20},
        'style':{'font_size':10,'color':'white'}})
    second = deepcopy(spec['composition'][0])
    second['id'] = second['name'] = 'second'
    second['slots'][0].update(source_from='0:00:00.400',source_to='0:00:00.800')
    spec['composition'].append(second)
    spec['overlays'] = [_cue('words','0:00:00.000','0:00:00.800','Fresh captions')]
    spec, _ = save_layout(spec,'interview','interview')
    entries = spec['spatial']['layouts']['interview']['inputs']
    for scene, start, text in [('interview',.2,'First'),('second',.8,'Second')]:
        bindings = {entry['name']: {'video':{'asset_id':'camera','source':{'start':start,'end':start+.4}},
            'image':{'asset_id':'logo'},'text':{'text':text},'caption':{'track':'captions'}}[entry['type']]
            for entry in entries}
        spec, _ = apply_saved_layout(spec,'interview',scene,bindings,replace=scene=='interview')
    return spec


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('portrait',[False,True])
def test_reused_design_preview_export_geometry_new_frames_and_audio_match(video_project, tmp_path, portrait):
    spec, metadata = video_project
    spec = _reuse(spec)
    if portrait:
        spec['composition_canvas'].update(width=180,height=320,aspect_ratio='9:16')
    save_spec(spec)
    before = load_spec()
    runner = CliRunner()
    result = runner.invoke(cli,['export','--out','reused.mp4','--video-encoder','x264','--quiet'])
    assert result.exit_code == 0, result.output
    canvas = {k:spec['composition_canvas'][k] for k in ('width','height')}
    timeline = resolve_project(metadata,spec)
    for at, index, start, text in [(.2,2,.4,'First'),(.6,6,1.,'Second')]:
        frame = compile_spatial_frame(spec,timeline,at,source_durations={'camera':2})
        assert not [d for d in frame['diagnostics'] if d.get('severity')!='warning']
        png, labels = tmp_path/f'{index}.png', tmp_path/f'{index}-layout.png'
        rendered = render_spatial_frame(spec,frame,canvas,output_path=png,layout_output_path=labels,project_root=tmp_path)
        measured = measure_spatial_layout(frame,canvas)
        assert [(b['path'],b['rect']) for b in rendered['boxes']] == [(b['path'],b['rect']) for b in measured['boxes']]
        videos = [b for b in measured['boxes'] if b['kind']=='video']
        assert len(videos)==3
        # Independently check source time; matching two wrong renders is insufficient.
        def walk(nodes):
            for node in nodes:
                yield node
                yield from walk(node.get('children',[]))
        nodes = list(walk(frame['children']))
        assert {n['media']['source_time_s'] for n in nodes if n.get('type')=='video'} == {start}
        assert {n['content']['text'] for n in nodes if n.get('type')=='text'} == {text}
        caption = next(n for n in nodes if n.get('type')=='caption')
        assert caption['caption']['text']=='Fresh captions'
        actual, expected = _pixels(tmp_path/'reused.mp4',index), _rgb(png)
        width,height = canvas['width'],canvas['height']
        for box in videos:
            rect=box['rect']
            x,y=int(rect['x']+rect['width']/2),int(rect['y']+rect['height']*.85)
            if not (0<=x<width and 0<=y<height): continue
            offset=(y*width+x)*3
            assert max(abs(a-b) for a,b in zip(actual[offset:offset+3],expected[offset:offset+3])) <= 12
        assert labels.stat().st_size > 0
    assert _rms(tmp_path/'reused.mp4',duration=.7) == pytest.approx(_rms(tmp_path/'with-audio.mkv',duration=.7),rel=.06)
    preview = runner.invoke(cli,['composition','model','preview','--at','0:00:00.600',
        '--canvas',f"{canvas['width']}x{canvas['height']}",'--out','cli.png','--layout-out','cli-layout.png'])
    assert preview.exit_code == 0, preview.output
    assert (tmp_path/'cli.png').read_bytes() == (tmp_path/'6.png').read_bytes()
    assert (tmp_path/'cli-layout.png').read_bytes() == (tmp_path/'6-layout.png').read_bytes()
    assert load_spec()==before


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_saved_draft_has_measurable_placeholders_and_blocks_export(project,tmp_path,monkeypatch):
    spec, metadata = project
    spec, _ = save_layout(spec,'mine','interview')
    spec, _ = apply_saved_layout(spec,'mine','interview',replace=True)
    save_spec(spec,cwd=tmp_path)
    save_project(metadata,cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    frame=compile_spatial_frame(spec,resolve_project(metadata,spec),.2)
    measured=measure_spatial_layout(frame,{'width':320,'height':180},output_path=tmp_path/'draft.png')
    assert any(d['code']=='missing_binding' for d in measured['diagnostics'])
    assert len([b for b in measured['boxes'] if b['kind']=='video'])==3
    assert (tmp_path/'draft.png').is_file()
    prior=tmp_path/'movie.mp4'
    prior.write_bytes(b'prior output')
    result=CliRunner().invoke(cli,['export','--out',str(prior),'--video-encoder','x264','--quiet'])
    assert result.exit_code==1
    assert json.loads(result.output)['error']
    assert prior.read_bytes()==b'prior output'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('replacement',[False,True])
def test_replaced_or_removed_last_media_consumer_previews_and_exports(project,tmp_path,monkeypatch,replacement):
    spec,metadata=project
    spec['composition'][0]['slots'][0]['source_to']='0:00:00.200'
    # No source media is needed after the video child is removed/replaced. This
    # checks readiness at the public commands, not merely structural expansion.
    spec['sources'][0]['path']='/missing/original-footage.mkv'
    metadata['sources'][0]['path']='/missing/original-footage.mkv'
    spec['spatial']['definitions']={'card':{'name':'Card','inputs':[
        {'name':'footage','type':'video','required':True}], 'children':[
        {'id':'media','type':'video','content':{'input':'footage'}},
        {'id':'title','type':'text','content':{'text':'Old'}}]}}
    edits=[{'op':'child_remove','path':['media']}]
    if replacement:
        edits.append({'op':'child_add','path':[],'after_id':'title','node':{
            'id':'media','type':'image','content':{'asset_id':'logo'},
            'layout':{'width':32,'height':32}}})
    spec['spatial']['scenes']['interview']['layout']={'children':[
        {'instance_id':'card','definition_id':'card','bindings':{'footage':{'asset_id':'camera'}},
         'customizations':edits}]}
    spec,_=save_layout(spec,'card','interview')
    values={'card_title':{'text':'Fresh title'}}
    if replacement:
        values['card_media']={'asset_id':'logo'}
    spec,report=apply_saved_layout(spec,'card','interview',values,replace=True)
    assert not report['diagnostics']
    save_spec(spec,cwd=tmp_path)
    save_project(metadata,cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    before=load_spec()
    for args in [
        ['composition','model','preview','--at','0:00:00.100','--out','ready.png','--layout-out','boxes.png'],
        ['export','--out','ready.mp4','--video-encoder','x264','--quiet']]:
        result=CliRunner().invoke(cli,args)
        assert result.exit_code==0,result.output
    assert (tmp_path/'ready.png').is_file() and (tmp_path/'ready.mp4').stat().st_size>0
    assert load_spec()==before


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_inherited_style_on_replacement_keeps_pixels_and_remains_live(project,tmp_path):
    from tests.test_spatial_inherited_replacements import _project_with_inherited_edit
    from moviestar.spec import pop_revision
    fixture,metadata=project
    spec,_,_=_project_with_inherited_edit(descendants=True)
    spec['sources']=fixture['sources']
    spec['composition']=fixture['composition']
    spec['composition_canvas']=fixture['composition_canvas']
    source=spec['spatial']['scenes'].pop('scene_a')
    spec['spatial']['scenes']={'interview':source}
    spec['spatial']['assets']=fixture['spatial']['assets']
    source['layout']['background_color']='#102030'
    replacement=source['layout']['children'][0]['customizations'][1]['node']['children'][0]
    replacement['content']={'asset_id':'logo'}
    replacement['layout']={'width':64,'height':64}
    spec['composition'][0]['slots'][0]['source_to']='0:00:00.200'
    frame=compile_spatial_frame(spec,resolve_project(metadata,spec),.1,source_durations={'camera':2})
    canvas={'width':320,'height':180}
    render_spatial_frame(spec,frame,canvas,output_path=tmp_path/'before.png',project_root=tmp_path)
    save_spec(spec,cwd=tmp_path)
    spec=load_spec(cwd=tmp_path)
    spec,_=save_layout(spec,'replacement','interview')
    name=spec['spatial']['layouts']['replacement']['inputs'][0]['name']
    spec,report=apply_saved_layout(spec,'replacement','interview',{name:{'asset_id':'logo'}},replace=True)
    assert not report['diagnostics']
    save_spec(spec,cwd=tmp_path,command='apply saved replacement layout')
    spec=load_spec(cwd=tmp_path)
    frame=compile_spatial_frame(spec,resolve_project(metadata,spec),.1,source_durations={'camera':2})
    render_spatial_frame(spec,frame,canvas,output_path=tmp_path/'copied.png',project_root=tmp_path)
    assert (tmp_path/'copied.png').read_bytes()==(tmp_path/'before.png').read_bytes()
    spec['spatial']['definitions']['wrapper']['children'][0]['customizations'][0]['value']=.25
    frame=compile_spatial_frame(spec,resolve_project(metadata,spec),.1,source_durations={'camera':2})
    render_spatial_frame(spec,frame,canvas,output_path=tmp_path/'updated.png',project_root=tmp_path)
    assert (tmp_path/'updated.png').read_bytes()!=(tmp_path/'copied.png').read_bytes()
    restored,_=pop_revision(load_spec(cwd=tmp_path))
    assert restored['spatial']['scenes']['interview']==source
