"""Generated A/B/A cut fixtures; optional supplied source paths stay local."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from fixtures import generate
from moviestar.project import load_project
from moviestar.spec import save_spec
from moviestar.timecodes import format_timecode


def prepare_cuts(root, portrait=False, sources=None, offsets=(1,0,4), long_project=False):
    # Historical #157 fixture convention: older state IDs distinguished 1.0
    # from 1. Keep offsets consistent with archived runs; current production
    # identity handles equivalent numbers without this workaround.
    offsets=tuple(int(value) if float(value).is_integer() else value for value in offsets)
    spec, _ = generate(root,portrait)
    pilot = sources is None
    if sources is None:
        a,b=root/'source-a.mp4',root/'source-b.mp4'
        # Left: clocked flash/beep cues. Right: continuous pilot for gap checks.
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(root/'cues.mp4'),
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=8',
            '-filter_complex','[0:a][1:a]amerge=inputs=2[a]','-map','0:v','-map','[a]',
            '-c:v','copy','-c:a','aac','-movflags','+faststart',str(a)],check=True,timeout=30)
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(a),'-vf','hue=h=120',
            '-c:v','libx264','-pix_fmt','yuv420p','-c:a','copy','-movflags','+faststart',str(b)],check=True,timeout=30)
        sources=(a,b)
    sources=tuple(Path(source).resolve() for source in sources)
    loaded=subprocess.run([sys.executable,'-m','moviestar.cli','load',*(str(x) for x in sources),
        '--as','camera','--as','other','--force','--no-transcribe','--no-frames'],cwd=root,capture_output=True,text=True,timeout=30)
    if loaded.returncode:raise RuntimeError('Could not load cut sources: '+loaded.stdout+loaded.stderr)
    project=load_project(root)
    spec['sources']=[{'id':identity,'path':str(path),'operations':[]} for identity,path in zip(['camera','other'],sources)]
    initial=deepcopy(spec['composition'][0]);tree=deepcopy(spec['spatial']['scenes']['review'])
    spec['composition']=[];spec['spatial']['scenes']={}
    for index,(identity,source,start) in enumerate(zip(['review','cut_b','cut_return'],['camera','other','camera'],offsets)):
        scene=deepcopy(initial);scene.update(id=identity,name='Cut '+str(index+1),audio_from=source)
        scene['slots'][0].update(id='slot_'+identity,source=source,
            source_from=format_timecode(start)['text'],source_to=format_timecode(start+3)['text'])
        design=deepcopy(tree);speaker=design['layout']['children'][0]
        speaker['bindings']['clip'].update(asset_id=source,source={'start':start,'end':start+3})
        speaker.pop('motion',None)
        design['layout']['background_color']=['#0b122b','#17283a','#0b122b'][index]
        design['layout']['children'][2]['content']['text']='Live edit A'
        spec['composition'].append(scene);spec['spatial']['scenes'][identity]=design
    review_start=0
    if long_project:
        if project['sources'][0]['duration']['seconds']<30:raise ValueError('Large-project cut fixture requires a first source of at least 30 seconds')
        before,after=[],[]
        for index in range(120):
            identity='outside_'+str(index);duration=21 if index==119 else 30
            scene=deepcopy(spec['composition'][0]);scene.update(id=identity,name=identity)
            scene['slots'][0].update(id='slot_'+identity,source_from='0:00:00.000',source_to=format_timecode(duration)['text'])
            design=deepcopy(spec['spatial']['scenes']['review']);design['layout']['children'][0]['bindings']['clip']['source']={'start':0,'end':duration}
            spec['spatial']['scenes'][identity]=design;(before if index<60 else after).append(scene)
        spec['composition']=before+spec['composition']+after;review_start=1800
    spec['overlays']=[{'id':'cut_caption_'+str(index),'kind':'caption','track':'captions',
        'from':format_timecode(review_start+index*3)['text'],'to':format_timecode(review_start+(index+1)*3)['text'],
        'text':['Source A\nFirst scene','Source B\nSecond scene','Back to source A'][index],
        'position':{'preset':'bottom'},'style':{'preset':'plain'}} for index in range(3)]
    save_spec(spec,cwd=root)
    return spec,project,{'from':review_start,'to':review_start+9,'continuous_pilot':pilot}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--portrait',action='store_true');parser.add_argument('--offsets',type=float,nargs=3,default=[1,0,4]);args=parser.parse_args()
    root=args.out.resolve()
    if root.exists():parser.error('--out must be new')
    _,_,review=prepare_cuts(root,args.portrait,offsets=args.offsets)
    print(json.dumps({'project':str(root),'range':review}))
