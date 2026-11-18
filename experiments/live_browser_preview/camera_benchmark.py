"""Media-only pan/zoom edits inside fixed frames, with unchanged preview gates."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

import av
import numpy as np
from caption_benchmark import command
from moviestar.spec import load_spec,save_spec,append_revision
from moviestar.spatial_layout import _chrome_path
from spatial_caption_benchmark import prepare as captions, wanted, run_case, POSES


def tracks(zoom=1.3,pan=-15,end=5):
    return {'scale':[{'at':0,'value':1},{'at':end,'value':zoom}],
        'translate_x':[{'at':0,'value':'0%'},{'at':end,'value':str(pan)+'%'}],
        'translate_y':[{'at':0,'value':'0%'},{'at':end,'value':str(pan)+'%'}]}


def prepare(root,canvas,mode):
    if mode=='still':
        captions(root,canvas,'still');spec=load_spec(root)
        media=spec['spatial']['definitions']['panel']['children'][0]
        spec['spatial']['definitions']['panel']['children'][2]['media_motion']=tracks(1.2,-10)
    else:
        from framing_benchmark import prepare as framing
        framing(root,canvas,'framing');spec=load_spec(root)
        media=spec['spatial']['definitions']['speaker_card']['children'][0]
        for scene in spec['spatial']['scenes'].values():
            scene['layout']['children'][-1]['media_motion']=tracks(1.2,-10)
    media['media_motion']=tracks()
    media['layout']['padding']=8
    media['style'].update(border_width=4,border_style='solid',border_color='#65ffb2',
        border_radius=24,background_color='#183848',object_position='right 10px bottom 5px')
    save_spec(spec,cwd=root)


def camera_target(spec):
    # Still fixtures retain unused footage definitions. Choose an instantiated
    # occurrence, and identify its media by ID rather than child ordering.
    for scene_id, scene in spec['spatial']['scenes'].items():
        for instance in scene['layout']['children']:
            definition=instance.get('definition_id')
            if definition not in ('speaker_card','panel'):continue
            leaf='footage' if definition=='speaker_card' else 'background'
            media=next(c for c in spec['spatial']['definitions'][definition]['children'] if c.get('id')==leaf)
            return definition,media,[scene_id,instance['instance_id'],leaf]
    raise AssertionError('No active camera fixture occurrence')


def prepare_case(root,folder,depth):
    spec=load_spec(root);spec['revisions']=[]
    for i in range(depth):
        after=deepcopy(spec);after['composition'][0]['name']=f'History {i}'
        spec=append_revision(spec,after,f'History setup {i}')
    save_spec(spec,cwd=root,record=False)
    original=(root/'moviestar/spec.json').read_bytes();previous=Path.cwd()
    actions=[];references={};painted={};poses={}
    definition,_,target=camera_target(spec)
    def capture(name):
        references[name]={}
        for at in POSES:
            path=folder/f'reference-{name}-{at}.png'
            command(root,['screenshot','--at',str(at),'--out',str(path)])
            references[name][str(at)]=str(path)
    try:
        os.chdir(root);initial=wanted(root,folder,painted,poses)
        for name in ('zoom','pan','timing','local','reset'):
            model=load_spec(root);_,video,_=camera_target(model)
            if name=='zoom':video['media_motion']['scale']=tracks(1.6)['scale']
            if name=='pan':video['media_motion'].update(tracks(1.6,-25))
            if name=='timing':video['media_motion']=tracks(1.6,-25,2)
            if name=='local':
                from moviestar.spatial_edit import edit_spatial_model
                model,_=edit_spatial_model(model,{'op':'instance.customize','target':{'occurrence_path':target},
                    'property':'media_motion.translate_x','value':tracks(1.6,-10,2)['translate_x']})
            if name=='reset':
                from moviestar.spatial_edit import edit_spatial_model
                model,_=edit_spatial_model(model,{'op':'instance.reset','target':{'occurrence_path':target},
                    'property':'media_motion.translate_x'})
            path=folder/(name+'.model.json');path.write_text(json.dumps(model));args=['spec','--edit',str(path)]
            command(root,args);value=wanted(root,folder,painted,poses)
            assert value!=(actions[-1]['expected']if actions else initial)
            actions.append({'name':name,'args':args,'expected':value});capture(name)
        for name in ('reset','local','timing','pan','zoom'):
            command(root,['undo']);actions.append({'name':'undo-'+name,'args':['undo'],'expected':wanted(root,folder,painted,poses)})
        capture('undo-zoom');assert actions[-1]['expected']==initial
        (root/'moviestar/spec.json').write_bytes(original)
    finally:os.chdir(previous)
    return {'initial':initial,'canvas':initial['canvas'],'actions':actions,'pose_image_references':references,
        'unclipped_scene':True,'spatial_caption_playback':True,'playback_after':'timing','initial_history':depth,'expected_history':min(50,depth+5)-5}


def pixel_parity(folder,case,report):
    report['camera_pixel_parity']=[]
    def pixels(path):
        with av.open(str(path))as container:return next(container.decode(video=0)).to_ndarray(format='rgb24').astype(int)
    for name,references in case['pose_image_references'].items():
        index=next(i for i,a in enumerate(case['actions'],1)if a['name']==name)
        for at,reference in references.items():
            actual=folder/f'edit-{index}-{name}-at-{at}-scene.png'
            if not actual.exists():report['problems'].append(f'Missing camera sample {name}/{at}');continue
            expected,observed=pixels(reference),pixels(actual)
            # Select flat reference interiors before examining the observed image.
            # This tests source crop/color, padding, borders, alpha and backgrounds
            # without turning subpixel codec/curve antialiasing into a new gate.
            if expected.shape!=observed.shape:report['problems'].append('Framing raster size mismatch');continue
            h,w=expected.shape[:2];center=expected[1:h-1,1:w-1]
            mask=np.ones((h-2,w-2),dtype=bool)
            for dy in (-1,0,1):
                for dx in (-1,0,1):mask&=(np.abs(expected[1+dy:h-1+dy,1+dx:w-1+dx]-center).max(axis=2)<=3)
            count=int(mask.sum());error=int(np.abs(observed[1:h-1,1:w-1]-center)[mask].max()) if count else None
            report['camera_pixel_parity'].append({'edit':name,'at':at,'sampled_pixels':count,'maximum_pixel_error':error})
            if error is None or error>8:report['problems'].append(f'Canonical camera mismatch {name}/{at}: {error}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--canvas',choices=['360x640','1080x1920'],default='360x640')
    parser.add_argument('--mode',choices=['pip','still'],action='append')
    parser.add_argument('--history',type=int,choices=[0,20,50],action='append')
    args=parser.parse_args();out=args.out.resolve()
    if out.exists():parser.error('--out must be new; retain previous failures')
    out.mkdir();canvas=tuple(map(int,args.canvas.split('x')))
    codes=[]
    for mode in (args.mode or ('pip','still')):
        folder=out/mode;folder.mkdir()
        for d in (args.history or (0,20,50)):
            codes.append(run_case(folder/f'history-{d}',d,str(_chrome_path()),canvas,mode,fixture_builder=prepare,
                case_builder=prepare_case,parity_checker=pixel_parity))
    raise SystemExit(1 if any(codes)else 0)
