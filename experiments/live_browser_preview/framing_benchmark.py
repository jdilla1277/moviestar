"""Real media framing edits/undos with unchanged three-second preview gates."""
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


def prepare(root,canvas,mode):
    captions(root,canvas,'pip');spec=load_spec(root)
    for scene in spec['spatial']['scenes'].values():
        main,inset=scene['layout']['children'][:2]
        main['layout'].update(left=0,top=0,width='100%',height='100%')
        inset['layout'].update(width=round(canvas[0]*.28),height=round(canvas[0]*.28))
        scene['layout']['children'].append({'id':'logo','type':'image','content':{'asset_id':'logo'},
            'layout':{'position':'absolute','left':20,'top':20,'width':96,'height':76,'padding':4},
            'style':{'object_fit':'contain','object_position':'left center','border_width':3,
                'border_style':'solid','border_color':'#ffe000','border_radius':14,'background_color':'#183848'}})
    save_spec(spec,cwd=root)


def prepare_case(root,folder,depth):
    spec=load_spec(root);spec['revisions']=[]
    for i in range(depth):
        after=deepcopy(spec);after['composition'][0]['name']=f'History {i}'
        spec=append_revision(spec,after,f'History setup {i}')
    save_spec(spec,cwd=root,record=False)
    original=(root/'moviestar/spec.json').read_bytes();previous=Path.cwd()
    actions=[];references={};painted={};poses={}
    def capture(name):
        references[name]={}
        for at in POSES:
            path=folder/f'reference-{name}-{at}.png'
            command(root,['screenshot','--at',str(at),'--out',str(path)])
            references[name][str(at)]=str(path)
    try:
        os.chdir(root);initial=wanted(root,folder,painted,poses)
        for name in ('crop','fit','decorated-box','resize','rounding'):
            model=load_spec(root);video=model['spatial']['definitions']['speaker_card']['children'][0]
            if name=='crop':video['style']['object_position']='0% 100%'
            if name=='fit':video['style']['object_fit']='contain'
            if name=='decorated-box':
                video['layout']['padding']='6px 10px 12px 4px'
                video['style'].update(border_width=4,border_style='solid',border_color='#ffe000',background_color='#183848')
            if name=='resize':
                for scene in model['spatial']['scenes'].values():
                    scene['layout']['children'][1]['layout'].update(width=round(model['composition_canvas']['width']*.36),height=round(model['composition_canvas']['width']*.3))
            if name=='rounding':video['style']['border_radius']='50%'
            path=folder/(name+'.model.json');path.write_text(json.dumps(model));args=['spec','--edit',str(path)]
            command(root,args);value=wanted(root,folder,painted,poses)
            assert value!=(actions[-1]['expected']if actions else initial)
            actions.append({'name':name,'args':args,'expected':value});capture(name)
        for name in ('rounding','resize','decorated-box','fit','crop'):
            command(root,['undo']);actions.append({'name':'undo-'+name,'args':['undo'],'expected':wanted(root,folder,painted,poses)})
        capture('undo-crop');assert actions[-1]['expected']==initial
        (root/'moviestar/spec.json').write_bytes(original)
    finally:os.chdir(previous)
    return {'initial':initial,'canvas':initial['canvas'],'actions':actions,'pose_image_references':references,
        'unclipped_scene':True,'spatial_caption_playback':True,'playback_after':'rounding','initial_history':depth,'expected_history':min(50,depth+5)-5}


def pixel_parity(folder,case,report):
    report['framing_pixel_parity']=[]
    def pixels(path):
        with av.open(str(path))as container:return next(container.decode(video=0)).to_ndarray(format='rgb24').astype(int)
    for name,references in case['pose_image_references'].items():
        index=next(i for i,a in enumerate(case['actions'],1)if a['name']==name)
        for at,reference in references.items():
            actual=folder/f'edit-{index}-{name}-at-{at}-scene.png'
            if not actual.exists():report['problems'].append(f'Missing framing sample {name}/{at}');continue
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
            report['framing_pixel_parity'].append({'edit':name,'at':at,'sampled_pixels':count,'maximum_pixel_error':error})
            if error is None or error>8:report['problems'].append(f'Canonical framing mismatch {name}/{at}: {error}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--canvas',choices=['360x640','1080x1920'],default='360x640')
    parser.add_argument('--history',type=int,choices=[0,20,50],action='append')
    args=parser.parse_args();out=args.out.resolve()
    if out.exists():parser.error('--out must be new; retain previous failures')
    out.mkdir();canvas=tuple(map(int,args.canvas.split('x')))
    codes=[run_case(out/f'history-{d}',d,str(_chrome_path()),canvas,'framing',fixture_builder=prepare,
        case_builder=prepare_case,parity_checker=pixel_parity)for d in (args.history or (0,20,50))]
    raise SystemExit(1 if any(codes)else 0)
