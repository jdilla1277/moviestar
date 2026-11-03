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
