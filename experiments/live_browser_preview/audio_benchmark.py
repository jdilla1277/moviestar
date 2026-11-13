"""Actual audio commands -> fresh native audio readiness at histories 0/20/50."""
from copy import deepcopy
from hashlib import sha256
import argparse
import json
from pathlib import Path
import selectors
import subprocess
import sys

from benchmark import driver_hashes
from pip import prepare_pip
from moviestar.project import load_project, save_project
from moviestar.resolved import resolve_project, resolve_overlays
from moviestar.overlays import plan_overlays
from moviestar.spatial_layout import _chrome_path
from moviestar.spec import append_revision, load_spec, save_spec
from moviestar.timecodes import format_timecode

HERE = Path(__file__).resolve().parent


def command(root, args):
    result = subprocess.run([sys.executable, '-m', 'moviestar.cli', *args], cwd=root,
        capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def prepare(root, canvas=(360,640), *, with_track=False):
    from audio import prepare_audio
    prepare_audio(root,with_track=with_track)
    spec=load_spec(root);spec['composition_canvas'].update(width=canvas[0],height=canvas[1]);save_spec(spec,cwd=root)


def expected(root):
    from moviestar.native_preview import compile_snapshot
    from moviestar.local_preview import read_snapshot
    packet,_=compile_snapshot(read_snapshot(root,0,5))
    return {k:packet['audio_plane'][k] for k in ('source_audio','tracks')}


def run_case(folder, depth, chrome, canvas=(360, 640), *, scenario='gain'):
    folder.mkdir(); root = folder/'project'; prepare(root, canvas,with_track=scenario in ('fades','two-tracks'))
    spec = load_spec(root); spec['revisions'] = []
    for index in range(depth):
        after = deepcopy(spec); after['composition'][0]['name'] = f'History {index}'
        spec = append_revision(spec, after, f'History setup {index}')
    save_spec(spec, cwd=root, record=False)
    original = (root/'moviestar/spec.json').read_bytes()
    steps = [
        ('add-dialogue',['audio','add',str(root/'dialogue.wav'),'--as','dialogue','--kind','voiceover','--from','1','--to','6']),
        ('source-gain',['audio','source','--gain-db','-9']),
        ('unmute-source',['audio','source','--unmute']),
        ('mute-source',['audio','source','--mute'])]
    if scenario=='fades':
        from moviestar.audio_surface import editable_audio_document
        # Fixed model files are authored before timing. Their content is the
        # exact saved mix after the preceding source-control commands.
        source=deepcopy(spec['audio_mix']);source['source_audio'].update(muted=False,gain_db=-9,fade_in=.2,fade_out=.2)
        for name,values in [('track-fade-in',{'fade_in':.2}),('track-fade-out',{'fade_in':.2,'fade_out':.2})]:
            model=deepcopy(source);model['tracks'][0].update(values)
            (root/(name+'.json')).write_text(json.dumps(editable_audio_document(model)))
        steps=[('source-fade-in',['audio','source','--unmute','--gain-db','-9','--fade-in','.2']),
            ('source-fade-out',['audio','source','--fade-out','.2']),
            ('track-fade-in',['audio','set',str(root/'track-fade-in.json')]),
            ('track-fade-out',['audio','set',str(root/'track-fade-out.json')])]
    if scenario=='two-tracks':
        from audio import add_bed
        from moviestar.audio_surface import editable_audio_document
        add_bed(root,with_track=False)
        model=deepcopy(spec['audio_mix'])
        model['tracks'].append({'id':'bed','kind':'music','path':str(root/'bed.wav'),
            'at':'0:00:00.500','source_from':'0:00:00.250','source_to':'0:00:04.250',
            'gain_db':-12,'muted':False,'loop':False,'fade_in':.2,'fade_out':.2,'ducking':None})
        steps=[('add-bed',['audio','add',str(root/'bed.wav'),'--as','bed','--kind','music',
            '--at','.5','--from','.25','--to','4.25','--gain-db','-12','--fade-in','.2','--fade-out','.2'])]
        for name,values in [('bed-gain',{'gain_db':-18}),('bed-mute',{'muted':True}),('bed-unmute',{'muted':False})]:
            model['tracks'][1].update(values)
            path=root/(name+'.json');path.write_text(json.dumps(editable_audio_document(model)))
            steps.append((name,['audio','set',str(path)]))
    initial = expected(root); actions = []
    for name, args in steps + [('undo-'+name, ['undo']) for name, _ in reversed(steps)]:
        output = command(root, args)
        wanted = expected(root)
        (folder/(name+'.setup-output.json')).write_text(json.dumps(output))
        actions.append({'name': name, 'args': args, 'expected': wanted})
    (root/'moviestar/spec.json').write_bytes(original)
    case = {'initial': initial, 'scenario':scenario, 'actions': actions, 'initial_history': depth, 'canvas': canvas,
        'producer_hashes': driver_hashes(), 'environment': {'python': sys.version,
            'chrome': subprocess.check_output([chrome, '--version'], text=True).strip(),
            'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0]},
        'package_source_hashes': {str(p.relative_to(HERE.parents[1])): sha256(p.read_bytes()).hexdigest()
            for p in sorted((HERE.parents[1]/'app/src/moviestar').iterdir()) if p.suffix in ('.py', '.mjs')}}
    if scenario in ('fades','two-tracks'):
        case['playback_args']=steps[-1][1]
        case['playback_expected']=actions[3]['expected']
    (folder/'case.json').write_text(json.dumps(case, indent=2))
    with (folder/'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root),
            '--mode', 'native', '--from', '0', '--to', '5', '--port', '0', '--no-open'],
            stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                if not selector.select(15): raise TimeoutError('Audio preview startup')
                ready = json.loads(server.stdout.readline())
                if not ready.get('url'): raise RuntimeError(str(ready))
            with (folder/'driver.log').open('w') as driver:
                return subprocess.run(['node', str(HERE/'audio_benchmark.mjs'), ready['url'], str(root),
                    str(folder), sys.executable, chrome], stdout=driver, stderr=subprocess.STDOUT, timeout=180).returncode
        finally:
            server.terminate()
            try: server.wait(timeout=9)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            server.stdout.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--canvas', choices=('360x640', '1080x1920'), default='360x640')
    parser.add_argument('--scenario', choices=('gain','fades','two-tracks'), default='gain')
    parser.add_argument('--history', type=int, choices=(0, 20, 50), action='append',
        help='Run only these history depths (default: all three).')
    args = parser.parse_args(); out = args.out.resolve()
    if out.exists(): parser.error('--out must be new; retain previous failures')
    out.mkdir()
    canvas = tuple(map(int, args.canvas.split('x')))
    codes = [run_case(out/f'history-{depth}', depth, str(_chrome_path()), canvas,scenario=args.scenario) for depth in (args.history or (0, 20, 50))]
    raise SystemExit(1 if any(codes) else 0)
