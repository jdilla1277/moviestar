"""A two-scene interview: real component edits, responsive canvas changes, undo and playback."""
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
from moviestar.spec import load_spec, save_spec, append_revision
from moviestar.spatial_layout import _chrome_path
from interview import prepare_interview

HERE = Path(__file__).resolve().parent


def prepare_case(folder, canvas, depth, *, capture_pcm=False, edits=True, scene='intro'):
    installed = Path(moviestar.__file__).parent; source = HERE.parents[1] / 'app/src/moviestar'
    hashes = {p.name: sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.suffix in ('.py', '.mjs')}
    for name, digest in hashes.items():
        assert sha256((installed / name).read_bytes()).hexdigest() == digest, name
    folder = Path(folder).resolve(); folder.mkdir(parents=True, exist_ok=False)
    root = folder / 'project'
    media = prepare_interview(root, canvas=canvas)
    spec = load_spec(root); spec['revisions'] = []
    for index in range(depth):
        after = deepcopy(spec); after['spatial']['scenes']['intro']['layout']['background_color'] = '#%06x' % (index + 1)
        spec = append_revision(spec, after, f'History {index}')
    save_spec(spec, cwd=root, record=False)
    other = 'x'.join(reversed(canvas.split('x')))
    yellow, cyan, purple, green, red = '#ffb81c', '#00d4ff', '#c084fc', '#34d399', '#f87171'
    commands = [
        ('local-listener', ['composition', 'model', 'edit', str(root / 'edit-local.json')], [yellow, cyan, yellow, yellow, yellow, yellow], canvas),
        ('shared-component', ['composition', 'model', 'edit', str(root / 'edit-shared.json')], [purple, cyan, purple, purple, purple, purple], canvas),
        ('reset-local', ['composition', 'model', 'edit', str(root / 'edit-reset.json')], [purple] * 6, canvas),
        ('responsive-canvas', ['scenes', 'set', str(root / f'scenes-{other}.json')], [purple] * 6, other),
    ]
    more = [
        ('local-second-scene', {'op': 'instance.customize', 'target': {'occurrence_path': ['reply', 'listeners', 'left', 'video']}, 'property': 'style.border_color', 'value': green}, [purple] * 4 + [green, purple]),
        ('reset-second-scene', {'op': 'instance.reset', 'target': {'occurrence_path': ['reply', 'listeners', 'left', 'video']}, 'property': 'style.border_color'}, [purple] * 6),
        ('local-other-listener', {'op': 'instance.customize', 'target': {'occurrence_path': ['intro', 'listeners', 'right', 'video']}, 'property': 'style.border_color', 'value': green}, [purple, purple, green, purple, purple, purple]),
        ('shared-with-local-preserved', {'op': 'component.update', 'target': {'definition_id': 'speaker_card', 'element_id': 'video'}, 'property': 'style.border_color', 'value': red}, [red, red, green, red, red, red]),
        ('reset-other-listener', {'op': 'instance.reset', 'target': {'occurrence_path': ['intro', 'listeners', 'right', 'video']}, 'property': 'style.border_color'}, [red] * 6),
    ]
    for name, edit, colors in more:
        path = root / f'edit-{name}.json'; path.write_text(json.dumps(edit, indent=2))
        commands.append((name, ['composition', 'model', 'edit', str(path)], colors, other))
    commands.append(('original-canvas', ['scenes', 'set', str(root / f'scenes-{canvas}.json')], [red] * 6, canvas))
    actions = [{'name': name, 'args': args, 'colors': colors, 'canvas': list(map(int, size.split('x')))}
               for name, args, colors, size in commands] if edits else []
    initial = {'colors': [yellow] * 6, 'canvas': list(map(int, canvas.split('x')))}
    previous = [initial, *actions[:-1]]
    if edits:
        actions += [{'name': 'undo-' + item['name'], 'args': ['undo'], 'colors': before['colors'], 'canvas': before['canvas']}
                    for item, before in zip(reversed(actions), reversed(previous))]
    (folder / 'case.json').write_text(json.dumps({'initial': initial, 'actions': actions,
        'initial_history': depth, 'capture_pcm': capture_pcm, 'media_kind': media['media_kind'], 'scene': scene,
        'runtime_hashes': hashes, 'producer_hashes': {p.name: sha256(p.read_bytes()).hexdigest() for p in HERE.iterdir() if p.suffix in ('.py', '.mjs')},
        'environment': {'installed_package': str(installed), 'python': sys.version,
            'chrome': subprocess.check_output([str(_chrome_path()), '--version'], text=True).strip(),
            'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0],
            'node': subprocess.check_output(['node', '--version'], text=True).strip()}}, indent=2))
    return root


def run_case(folder, canvas, depth, *, capture_pcm=False, edits=True, scene='intro'):
    root = prepare_case(folder, canvas, depth, capture_pcm=capture_pcm, edits=edits, scene=scene)
    start = 0 if scene == 'intro' else 3
    with (folder / 'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root),
            '--mode', 'native', '--from', str(start), '--to', str(start + 3), '--port', '0', '--no-open'],
            stdout=subprocess.PIPE, stderr=log, text=True, env={**os.environ, 'MOVIESTAR_PREVIEW_TIMING': '1'})
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                if not selector.select(20): raise TimeoutError('Interview preview startup')
                ready = json.loads(server.stdout.readline())
                if not ready.get('url'): raise RuntimeError(str(ready))
            with (folder / 'driver.log').open('w') as driver:
                return subprocess.run(['node', str(HERE / 'interview_benchmark.mjs'), ready['url'], str(root),
                    str(folder), sys.executable, str(_chrome_path())], stdout=driver, stderr=subprocess.STDOUT, timeout=180).returncode
        finally:
            server.terminate()
            try: server.wait(timeout=9)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            server.stdout.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--canvas', choices=['360x640', '640x360', '1080x1920', '1920x1080'], default='360x640')
    parser.add_argument('--history', action='append', type=int, choices=[0, 20, 50])
    parser.add_argument('--capture-pcm', action='store_true')
    parser.add_argument('--playback-only', action='store_true')
    parser.add_argument('--scene', choices=['intro', 'reply'], default='intro')
    args = parser.parse_args(); out = args.out.resolve()
    if out.exists(): parser.error('--out must be new; retain previous evidence')
    out.mkdir()
    codes = [run_case(out / f'history-{depth}', args.canvas, depth,
        capture_pcm=args.capture_pcm, edits=not args.playback_only, scene=args.scene) for depth in args.history or [0, 20, 50]]
    raise SystemExit(any(codes))
