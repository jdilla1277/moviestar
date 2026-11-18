"""Nested caption CLI edits/undos -> correct fresh preview within 3,000 ms.

Generated media only. Compare word color, geometry and line count against the
canonical export DOM at four poses, plus caption pixels at the ready pose.
"""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

import av
import numpy as np
from benchmark import driver_hashes
from caption_benchmark import command
from edit_benchmark import expected
from moviestar.project import load_project
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import _chrome_path, measure_spatial_layout
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spec import append_revision, load_spec, save_spec

HERE = Path(__file__).resolve().parent
POSES = (.2, 1.5, 3, 4.5)


def prepare(root, canvas, mode):
    if mode == 'pip':
        from pip import prepare_pip
        from audio import add_dialogue
        prepare_pip(root, spatial=True, cuts=True)
        add_dialogue(root)
    else:
        from still_benchmark import prepare as still
        still(root, canvas, 'single')
    spec=load_spec(root);spec['composition_canvas'].update(width=canvas[0],height=canvas[1])
    # An imported/frozen word-timed track is legal over both footage and cards.
    # Live recipe refresh is separately covered by test_native_captions.
    spec['captions']=[];spec['overlays']=[]
    for i in range(5):
        spec['overlays'].append({'id':f'cue-{i}','kind':'caption','track':'captions',
            'from':f'0:00:0{i}.000','to':f'0:00:0{i+1}.000','text':'First second',
            'position':{'preset':'bottom'},'style':{'preset':'plain'},
            'highlight':{'mode':'spoken-word','color':'#ffe94a'},'tokens':[
                {'text':'First','from':f'0:00:0{i}.000','to':f'0:00:0{i}.400'},
                {'text':'second','from':f'0:00:0{i}.400','to':f'0:00:0{i+1}.000'}]})
    spec['spatial']['definitions'].update({
        'words':{'name':'WordCaption','inputs':[{'name':'track','type':'caption','required':True}],
            'children':[{'id':'text','type':'caption','content':{'input':'track'},
                'layout':{'width':'100%','height':'100%','padding':10},
                'style':{'font_family':'monospace','font_size':24,'line_height':1.3,
                    'color':'#ffffff','background_color':'#182838','stroke_color':'#000000','stroke_width':1}}]},
        'band':{'name':'CaptionBand','inputs':[{'name':'track','type':'caption','required':True}],
            'children':[{'instance_id':'words','definition_id':'words','bindings':{'track':{'forward':'track'}},
                'layout':{'width':'100%','height':'100%'}}]}})
    for scene in spec['spatial']['scenes'].values():
        scene['layout']['children'].append({'instance_id':'captions','definition_id':'band',
            'bindings':{'track':{'track':'captions'}},'style':{'highlight_color':'#ffe94a'},
            'layout':{'position':'absolute','left':20,'bottom':40,'width':canvas[0]-40,'height':120}})
    save_spec(spec,cwd=root)


def wanted(root,folder,painted,poses):
    result=expected(root,folder,painted)
    spec=load_spec(root);timeline=resolve_project(load_project(root),spec)
    for at in POSES:
        frame=compile_spatial_frame(spec,timeline,at)
        # Reference cache excludes timing-independent histories, never live DOM.
        key=sha256(json.dumps(frame,sort_keys=True).encode()).hexdigest()
        if key not in poses:
            measured=measure_spatial_layout(frame,result['canvas'])
            assert not any(d.get('severity')!='warning' for d in measured['diagnostics']),measured
            poses[key]=[{**{k:b[k] for k in ('path','rect','text_lines','caption_words') if k in b},
                **({'text':b['caption']['text']} if 'caption' in b else {})}
                for b in measured['boxes']]
        segment=next((s for s in reversed(result.get('segments',[])) if s['offset']<=at),None)
        packet=segment['packet'] if segment else result
        local=at-(segment['offset'] if segment else 0)
        packet.setdefault('pose_boxes',{})[round(local*packet['fps'])]=poses[key]
    return result


def prepare_case(root,folder,depth):
    spec=load_spec(root);spec['revisions']=[]
    for i in range(depth):
        after=deepcopy(spec);after['composition'][0]['name']=f'History {i}'
        spec=append_revision(spec,after,f'History setup {i}')
    save_spec(spec,cwd=root,record=False)
    original=(root/'moviestar/spec.json').read_bytes();painted={};poses={};actions=[];references={}
    previous=Path.cwd()
    try:
        os.chdir(root);initial=wanted(root,folder,painted,poses)
        for name in ('wording','color','size','placement','motion'):
            model=load_spec(root)
            bands=[next(c for c in s['layout']['children'] if c.get('instance_id')=='captions')
                for s in model['spatial']['scenes'].values()]
            if name=='wording':
                for cue in model['overlays']:
                    cue['text']='Fresh second';cue['tokens'][0]['text']='Fresh'
            if name=='color':
                for band in bands:band['style']['highlight_color']='#65ffb2'
            if name=='size':model['spatial']['definitions']['words']['children'][0]['style']['font_size']=28
            if name=='placement':
                for band in bands:band['layout']['bottom']=70
            if name=='motion':
                for band in bands:band['motion']={'translate_x':[{'at':0,'value':0},{'at':5,'value':10}]}
            path=folder/(name+'.model.json');path.write_text(json.dumps(model));args=['spec','--edit',str(path)]
            (folder/(name+'.setup.json')).write_text(json.dumps(command(root,args)))
            value=wanted(root,folder,painted,poses)
            assert value!=(actions[-1]['expected'] if actions else initial)
            actions.append({'name':name,'args':args,'expected':value})
            reference=folder/('reference-'+name+'.png')
            command(root,['screenshot','--at','.2','--out',str(reference)])
            references[name]=str(reference)
        for name in ('motion','placement','size','color','wording'):
            command(root,['undo']);actions.append({'name':'undo-'+name,'args':['undo'],
                'expected':wanted(root,folder,painted,poses)})
        reference=folder/'reference-undo-wording.png'
        command(root,['screenshot','--at','.2','--out',str(reference)])
        references['undo-wording']=str(reference)
        assert actions[-1]['expected']==initial
        (root/'moviestar/spec.json').write_bytes(original)
    finally:os.chdir(previous)
    return {'initial':initial,'canvas':initial['canvas'],'actions':actions,'image_references':references,
        'spatial_caption_playback':True,'initial_history':depth,'expected_history':min(50,depth+5)-5,'playback_after':'motion'}


def pixel_parity(folder,case,report):
    report['caption_pixel_parity']=[]
    def pixels(path):
        with av.open(str(path)) as container:return next(container.decode(video=0)).to_ndarray(format='rgb24').astype(int)
    for name,reference in case['image_references'].items():
        index,action=next((i,a) for i,a in enumerate(case['actions'],1) if a['name']==name)
        actual=folder/f'edit-{index}-{name}-scene.png'
        if not actual.exists():report['problems'].append('Missing caption pixel sample '+name);continue
        packet=action['expected'].get('segments',[{'packet':action['expected']}])[0]['packet']
        box=next(b for b in packet['pose_boxes'][6] if b.get('caption_words'))
        # Opaque caption background isolates glyph AA/strokes from video decoding.
        r=box['rect'];x,y=int(np.ceil(r['x']))+2,int(np.ceil(r['y']))+2
        w,h=int(r['width'])-4,int(r['height'])-4
        left,right=pixels(actual)[y:y+h,x:x+w],pixels(reference)[y:y+h,x:x+w]
        error=int(np.abs(left-right).max()) if left.shape==right.shape and left.size else None
        report['caption_pixel_parity'].append({'edit':name,'maximum_pixel_error':error})
        if error is None or error>8:report['problems'].append(f'Canonical caption pixel mismatch {name}: {error}')


def run_case(folder,depth,chrome,canvas,mode):
    import moviestar
    folder.mkdir();root=folder/'project';prepare(root,canvas,mode);case=prepare_case(root,folder,depth)
    package=Path(moviestar.__file__).parent
    case.update(mode=mode,producer_hashes=driver_hashes(),package_source_hashes={
        p.name:sha256(p.read_bytes()).hexdigest() for p in package.iterdir() if p.suffix in ('.py','.mjs')})
    (folder/'case.json').write_text(json.dumps(case,indent=2))
    with (folder/'server.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-m','moviestar.cli','preview','--workspace',str(root),
            '--mode','native','--from','0','--to','5','--port','0','--no-open'],stdout=subprocess.PIPE,
            stderr=log,text=True,env={**os.environ,'MOVIESTAR_PREVIEW_TIMING':'1'})
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout,selectors.EVENT_READ)
                if not selector.select(30):raise TimeoutError('Spatial caption startup')
                ready=json.loads(server.stdout.readline());(folder/'ready.json').write_text(json.dumps(ready))
            with (folder/'driver.log').open('w') as driver:
                code=subprocess.run(['node',str(HERE/'edit_benchmark.mjs'),ready['url'],str(root),str(folder),
                    sys.executable,chrome],stdout=driver,stderr=subprocess.STDOUT,timeout=240).returncode
            path=folder/'browser-report.json';report=json.loads(path.read_text())
            pixel_parity(folder,case,report)
            for playback in report.get('playbacks',[]):
                if len(playback['metrics'].get('beeps',[]))!=5:report['problems'].append('Missing audio cue')
            path.write_text(json.dumps(report,indent=2));return 1 if report['problems'] else code
        finally:
            server.terminate()
            try:server.wait(timeout=9)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            server.stdout.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--canvas',choices=['360x640','1080x1920'],default='360x640')
    parser.add_argument('--history',type=int,choices=[0,20,50],action='append')
    parser.add_argument('--mode',choices=['pip','still','all'],default='all')
    args=parser.parse_args();out=args.out.resolve()
    if out.exists():parser.error('--out must be new; retain previous failures')
    out.mkdir();codes=[]
    for mode in (['pip','still'] if args.mode=='all' else [args.mode]):
        folder=out/mode;folder.mkdir()
        for depth in (args.history or (0,20,50)):
            codes.append(run_case(folder/f'history-{depth}',depth,str(_chrome_path()),tuple(map(int,args.canvas.split('x'))),mode))
    raise SystemExit(1 if any(codes) else 0)
