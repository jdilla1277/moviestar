"""Actual caption commands -> fresh, visible native words at histories 0/20/50."""
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


def prepare(root, canvas=(360, 640)):
    prepare_pip(root)
    spec = load_spec(root)
    spec['composition_canvas'].update(width=canvas[0], height=canvas[1])
    save_spec(spec, cwd=root)
    project = load_project(root)
    for index, source in enumerate(project['sources']):
        offset = (1, 2)[index]
        entries = [('Intro', 0, .15), ('Alpha', .2, .3), ('Bravo', .4, .6),
                   ('Charlie', 1.2, 1.3), ('Delta', 2.2, 2.3), ('Echo', 3.2, 3.3), ('Foxtrot', 4.2, 4.3)]
        words = [{'text': word, 'start': format_timecode(offset+start), 'end': format_timecode(offset+end)}
                 for word, start, end in entries]
        path = root/'moviestar/transcripts'/f'{source["id"]}.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'words': words, 'segments': [], 'language': 'en'}))
        source['transcript'] = {'path': 'transcripts/'+path.name, 'model': 'synthetic', 'source': 'synthetic'}
    save_project(project, cwd=root)
    command(root, ['captions', 'generate', '--highlight', 'spoken-word'])


def expected(root):
    spec, project = load_spec(root), load_project(root)
    timeline = resolve_project(project, spec)
    planned = plan_overlays(list(resolve_overlays(timeline, spec['overlays']).records),
        (timeline.canvas['width'], timeline.canvas['height']), 0, 5)
    active = [p for p in planned['plans'] if p['from_result'] <= .2 < p['to_result']]
    return [{'text': p['text'], 'font_size': p['font_size'], 'estimated_bounds': p['estimated_bounds'],
             'highlight_color': p['highlight']['color']} for p in active]


def run_case(folder, depth, chrome, canvas=(360, 640)):
    folder.mkdir(); root = folder/'project'; prepare(root, canvas)
    spec = load_spec(root); spec['revisions'] = []
    for index in range(depth):
        after = deepcopy(spec); after['composition'][0]['name'] = f'History {index}'
        spec = append_revision(spec, after, f'History setup {index}')
    save_spec(spec, cwd=root, record=False)
    original = (root/'moviestar/spec.json').read_bytes()
    steps = [
        ('suppress-intro', ['captions', 'suppress', '--from', '0', '--to', '0.15']),
        ('resize-words', ['captions', 'placement', '--size', '32px']),
        ('move-words', ['captions', 'placement', '--at', 'top']),
        ('highlight-color', ['captions', 'generate', '--highlight', 'spoken-word', '--highlight-color', '#65ffb2'])]
    initial = expected(root); actions = []
    for name, args in steps + [('undo-'+name, ['undo']) for name, _ in reversed(steps)]:
        output = command(root, args)
        wanted = expected(root)
        (folder/(name+'.setup-output.json')).write_text(json.dumps(output))
        actions.append({'name': name, 'args': args, 'expected': wanted})
    assert 'Intro' not in actions[0]['expected'][0]['text']
    assert actions[1]['expected'][0]['font_size'] == 32
    assert actions[2]['expected'][0]['estimated_bounds']['y'] < initial[0]['estimated_bounds']['y']
    assert actions[3]['expected'][0]['highlight_color'] == '#65ffb2'
    (root/'moviestar/spec.json').write_bytes(original)
    case = {'initial': initial, 'actions': actions, 'initial_history': depth, 'canvas': canvas,
        'producer_hashes': driver_hashes(), 'environment': {'python': sys.version,
            'chrome': subprocess.check_output([chrome, '--version'], text=True).strip(),
            'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0]},
        'package_source_hashes': {str(p.relative_to(HERE.parents[1])): sha256(p.read_bytes()).hexdigest()
            for p in sorted((HERE.parents[1]/'app/src/moviestar').iterdir()) if p.suffix in ('.py', '.mjs')}}
    (folder/'case.json').write_text(json.dumps(case, indent=2))
    with (folder/'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root),
            '--mode', 'native', '--from', '0', '--to', '5', '--port', '0', '--no-open'],
            stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                if not selector.select(15): raise TimeoutError('Caption preview startup')
                ready = json.loads(server.stdout.readline())
                if not ready.get('url'): raise RuntimeError(str(ready))
            with (folder/'driver.log').open('w') as driver:
                return subprocess.run(['node', str(HERE/'caption_benchmark.mjs'), ready['url'], str(root),
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
    parser.add_argument('--history', type=int, choices=(0, 20, 50), action='append',
        help='Run only these history depths (default: all three).')
    args = parser.parse_args(); out = args.out.resolve()
    if out.exists(): parser.error('--out must be new; retain previous failures')
    out.mkdir()
    canvas = tuple(map(int, args.canvas.split('x')))
    codes = [run_case(out/f'history-{depth}', depth, str(_chrome_path()), canvas) for depth in (args.history or (0, 20, 50))]
    raise SystemExit(1 if any(codes) else 0)
