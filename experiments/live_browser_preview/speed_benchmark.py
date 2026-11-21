"""Fresh CLI pacing edits/undos, source clocks and retained native playback evidence."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import moviestar

from fixtures import generate
from moviestar.spec import load_spec, save_spec, append_revision
from moviestar.spatial_layout import _chrome_path

HERE = Path(__file__).resolve().parent


def run(folder, canvas, depth):
    installed=Path(moviestar.__file__).parent
    source=HERE.parents[1]/'app/src/moviestar'
    for path in source.iterdir():
        if path.suffix in ('.py','.mjs'):
            assert sha256(path.read_bytes()).digest()==sha256((installed/path.name).read_bytes()).digest(),path.name
    folder.mkdir(); root = folder/'project'; generate(root, portrait=True)
    spec = load_spec(root)
    spec['composition_canvas'].update(width=canvas[0], height=canvas[1])
    children = spec['spatial']['scenes']['review']['layout']['children']
    children[:] = [children[0]]
    children[0].pop('motion', None)
    children[0]['layout'] = {'position':'absolute','left':0,'top':0,'width':canvas[0],'height':canvas[1]}
    spec['spatial']['definitions']['speaker_card']['children'][0]['style'] = {'object_fit':'fill'}
    # Keep captions and camera on result time while pictures accelerate.
    children.append({'id':'caption','type':'caption','content':{'track':'captions'},
        'layout':{'position':'absolute','left':20,'bottom':20,'width':canvas[0]-40,'height':100},
        'style':{'font_size':20,'font_family':'sans-serif','color':'#ffffff'}})
    spec['motion'] = {'version':1,'scenes':[{'scene':'Live review','slots':[
        {'slot':'main','camera':[],'pacing':[{'id':'pace','mode':'speed','speed':1.25,
            'range':{'from':'0:00:00.000','to':'0:00:05.000','space':'source-local'}}]}]}]}
    spec['revisions'] = []
    for index in range(depth):
        after = deepcopy(spec); after['spatial']['scenes']['review']['layout']['background_color'] = '#%06x' % (index+1)
        spec = append_revision(spec, after, f'History {index}')
    save_spec(spec, cwd=root, record=False)
    actions=[]
    for i in range(10):
        motion=deepcopy(spec['motion']);rate=1.5 if i%2==0 else 1.25
        # Ensure every command changes intent, including the initial 1.5 state.
        motion['scenes'][0]['slots'][0]['pacing'][0]['speed']=rate
        path=folder/f'pace-{i}.json';path.write_text(json.dumps(motion))
        actions.append({'args':['scenes','motion','set',str(path)],'rate':rate})
    actions += [{'args':['undo'],'rate':1.5 if i%2==0 else 1.25} for i in range(10)]
    (folder/'case.json').write_text(json.dumps({'actions':actions,'canvas':canvas,'history':depth,
        'runtime_hashes':{p.name:sha256(p.read_bytes()).hexdigest() for p in (HERE.parents[1]/'app/src/moviestar').iterdir() if p.suffix in ('.py','.mjs')},
        'environment':{'installed_package':str(installed),'python':sys.version,
            'chrome':subprocess.check_output([str(_chrome_path()),'--version'],text=True).strip(),
            'ffmpeg':subprocess.check_output(['ffmpeg','-version'],text=True).splitlines()[0],
            'node':subprocess.check_output(['node','--version'],text=True).strip()},
        'producer_hashes':{p.name:sha256(p.read_bytes()).hexdigest() for p in HERE.iterdir() if p.suffix in ('.py','.mjs')}}))
    with (folder/'server.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-m','moviestar.cli','preview','--workspace',str(root),
            '--mode','native','--from','0','--to','2','--port','0','--no-open'],stdout=subprocess.PIPE,stderr=log,text=True,env={**os.environ,'MOVIESTAR_PREVIEW_TIMING':'1'})
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout,selectors.EVENT_READ)
                if not selector.select(15): raise TimeoutError('Preview startup')
                ready=json.loads(server.stdout.readline())
                if not ready.get('url'): raise RuntimeError(str(ready))
            with (folder/'driver.log').open('w') as driver:
                return subprocess.run(['node',str(HERE/'speed_benchmark.mjs'),ready['url'],str(root),str(folder),
                    sys.executable,str(_chrome_path())],stdout=driver,stderr=subprocess.STDOUT,timeout=180).returncode
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
    args=parser.parse_args();out=args.out.resolve()
    if out.exists():parser.error('--out must be new; retain previous evidence')
    out.mkdir();canvas=tuple(map(int,args.canvas.split('x')))
    codes=[run(out/f'history-{depth}',canvas,depth) for depth in (args.history or [0,20,50])]
    raise SystemExit(any(codes))
