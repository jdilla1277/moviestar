"""Real CLI still-scene edits -> correct fresh preview within 3,000 ms."""
import argparse
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

import av
import numpy as np
from fixtures import generate
from caption_benchmark import command
from edit_benchmark import expected
from benchmark import driver_hashes
from moviestar.spec import append_revision, load_spec, save_spec
from moviestar.spatial_layout import _chrome_path

HERE = Path(__file__).resolve().parent


def prepare(root, canvas, mode='silent'):
    spec, _ = generate(root, portrait=canvas[1] > canvas[0])
    for name, colors in [('background', [(180, 30, 40), (30, 60, 180)]),
                         ('alternate', [(30, 180, 60), (180, 140, 30)])]:
        pixels = np.empty((2160, 3840, 3), dtype=np.uint8)
        pixels[:, :1920] = colors[0]; pixels[:, 1920:] = colors[1]
        encoder = av.CodecContext.create('png', 'w'); encoder.width = 3840; encoder.height = 2160
        encoder.pix_fmt = 'rgb24'; encoder.time_base = Fraction(1, 30); encoder.thread_count = 1
        (root / (name + '.png')).write_bytes(b''.join(bytes(p) for p in encoder.encode(
            av.VideoFrame.from_ndarray(pixels, format='rgb24'))))
    spec['composition_canvas'].update(width=canvas[0], height=canvas[1])
    original = deepcopy(spec['composition'][0]); video = deepcopy(spec['spatial']['scenes']['review'])
    spec['spatial']['assets'].update({name: {'type': 'image', 'path': name + '.png'}
        for name in ('background', 'alternate')})
    spec['spatial']['definitions']['panel'] = {'name': 'TextImagePanel', 'inputs': [
        {'name': 'title', 'type': 'text', 'required': True},
        {'name': 'picture', 'type': 'image', 'required': True}], 'children': [
        {'id': 'background', 'type': 'image', 'content': {'input': 'picture'},
         'layout': {'position': 'absolute', 'left': 0, 'top': 0, 'width': '100%', 'height': '100%'},
         'style': {'object_fit': 'cover'}},
        {'id': 'title', 'type': 'text', 'content': {'input': 'title'},
         'layout': {'position': 'absolute', 'left': 20, 'top': 30, 'width': '80%', 'height': 140},
         'style': {'font_size': 32, 'color': '#ffffff'}},
        {'id': 'logo', 'type': 'image', 'content': {'asset_id': 'logo'},
         'layout': {'position': 'absolute', 'right': 20, 'bottom': 20, 'width': 64, 'height': 64},
         'style': {'object_fit': 'contain'}}]}
    spec['composition'] = []; spec['spatial']['scenes'] = {}; spec['overlays'] = []
    for identity, duration in ([('title', 5)] if mode == 'single' else [('title', 1.5), ('middle', 2), ('closing', 1.5)]):
        scene = {'id': identity, 'name': identity, 'layout': {'preset': 'single'}, 'slots': [], 'duration': duration}
        layout = {'layout': {'background_color': '#102030', 'children': [
            {'instance_id': 'panel', 'definition_id': 'panel',
             'bindings': {'title': {'text': identity.title()}, 'picture': {'asset_id': 'background'}},
             'layout': {'position': 'absolute', 'left': 0, 'top': 0, 'width': canvas[0] * .9, 'height': canvas[1]}}]}}
        if mode == 'mixed' and identity == 'middle':
            scene = deepcopy(original); scene.update(id=identity, name=identity)
            scene['slots'][0].update(source_from='0:00:01.000', source_to='0:00:03.000')
            layout = deepcopy(video)
            # Keep the footage visible at both reference sizes.
            layout['layout']['children'] = layout['layout']['children'][:1]
            layout['layout']['children'][0].update(layout={'width': '100%', 'height': '100%'})
        spec['composition'].append(scene); spec['spatial']['scenes'][identity] = layout
    save_spec(spec, cwd=root)
    if mode in ('audio', 'single'):
        from audio import add_dialogue
        add_dialogue(root)


def prepare_case(root, folder, depth):
    spec = load_spec(root); spec['revisions'] = []
    for i in range(depth):
        after = deepcopy(spec); after['composition'][0]['name'] = f'History {i}'
        spec = append_revision(spec, after, f'History setup {i}')
    save_spec(spec, cwd=root, record=False)
    original = (root / 'moviestar/spec.json').read_bytes()
    painted = {}; actions = []; references = {}; previous = Path.cwd()
    try:
        os.chdir(root)
        initial = expected(root, folder, painted)
        for index, name in enumerate(['title', 'color', 'image', 'fit', 'position']):
            model = load_spec(root)
            panel = model['spatial']['scenes']['title']['layout']['children'][0]
            if name == 'title': panel['bindings']['title']['text'] = 'Updated title'
            if name == 'color': model['spatial']['scenes']['title']['layout']['background_color'] = '#ff00ff'
            if name == 'image': panel['bindings']['picture']['asset_id'] = 'alternate'
            if name == 'fit': model['spatial']['definitions']['panel']['children'][0]['style']['object_fit'] = 'contain'
            if name == 'position': panel['layout'].update(left=10, width=model['composition_canvas']['width'] * .8)
            path = folder / (name + '.model.json'); path.write_text(json.dumps(model))
            args = ['spec', '--edit', str(path)]; response = command(root, args)
            (folder / (name + '.setup.json')).write_text(json.dumps(response))
            wanted = expected(root, folder, painted)
            assert wanted != (actions[-1]['expected'] if actions else initial)
            actions.append({'name': name, 'args': args, 'expected': wanted})
            if name in ('image', 'fit', 'position'):
                path = folder / ('reference-' + name + '.png')
                command(root, ['screenshot', '--at', '.2', '--out', str(path)])
                references[name] = str(path)
        for name in ['position', 'fit', 'image', 'color', 'title']:
            response = command(root, ['undo'])
            (folder / ('undo-' + name + '.setup.json')).write_text(json.dumps(response))
            actions.append({'name': 'undo-' + name, 'args': ['undo'], 'expected': expected(root, folder, painted)})
        path = folder / 'reference-undo-title.png'
        command(root, ['screenshot', '--at', '.2', '--out', str(path)])
        references['undo-title'] = str(path)
        assert actions[-1]['expected'] == initial
        (root / 'moviestar/spec.json').write_bytes(original)
    finally:
        os.chdir(previous)
    return {'initial': initial, 'canvas': initial['canvas'], 'actions': actions,
        'image_references': references, 'initial_history': depth, 'playback_after': 'position', 'expected_history': min(50, depth + 5) - 5}


def run_case(folder, depth, chrome, canvas, mode):
    folder.mkdir(); root = folder / 'project'; prepare(root, canvas, mode)
    case = prepare_case(root, folder, depth)
    import moviestar
    package = Path(moviestar.__file__).parent
    case.update(producer_hashes=driver_hashes(), mode=mode, package_source_hashes={
        p.name: sha256(p.read_bytes()).hexdigest() for p in package.iterdir() if p.suffix in ('.py', '.mjs')})
    (folder / 'case.json').write_text(json.dumps(case, indent=2))
    with (folder / 'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root),
            '--mode', 'native', '--from', '0', '--to', '5', '--port', '0', '--no-open'],
            stdout=subprocess.PIPE, stderr=log, text=True, env={**os.environ, 'MOVIESTAR_PREVIEW_TIMING': '1'})
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                if not selector.select(30): raise TimeoutError('Still preview startup')
                ready = json.loads(server.stdout.readline()); (folder / 'ready.json').write_text(json.dumps(ready))
            with (folder / 'driver.log').open('w') as driver:
                result = subprocess.run(['node', str(HERE / 'edit_benchmark.mjs'), ready['url'], str(root),
                    str(folder), sys.executable, chrome], stdout=driver, stderr=subprocess.STDOUT, timeout=240)
            report_path = folder / 'browser-report.json'
            report = json.loads(report_path.read_text())
            report['image_parity'] = []
            expected_cues = {'silent': 0, 'audio': 5, 'mixed': 2, 'single': 5}[mode]
            for playback in report.get('playbacks', []):
                cues = len(playback['metrics'].get('beeps', []))
                if cues != expected_cues:
                    report['problems'].append(f'Expected {expected_cues} output audio cues, observed {cues}')
            for name, reference in case['image_references'].items():
                index = next(i for i, action in enumerate(case['actions'], 1) if action['name'] == name)
                actual = folder / f'edit-{index}-{name}-scene.png'
                if not actual.exists():
                    report['problems'].append(f'Missing image parity sample: {name}')
                    continue
                def pixels(path):
                    with av.open(str(path)) as container:
                        return next(container.decode(video=0)).to_ndarray(format='rgb24')
                left, right = pixels(actual), pixels(reference)
                error = None
                if left.shape == right.shape:
                    h, w = left.shape[:2]
                    points = [(int(w*x), int(h*y)) for x, y in [(.05,.5),(.35,.5),(.7,.5),(.98,.5),(.05,.85)]]
                    error = max(int(np.abs(left[y,x].astype(int)-right[y,x].astype(int)).max()) for x,y in points)
                report['image_parity'].append({'edit': name, 'maximum_pixel_error': error})
                if error is None or error > 8:
                    report['problems'].append(f'Canonical image mismatch for {name}: {error}')
            report_path.write_text(json.dumps(report, indent=2))
            return 1 if report['problems'] else result.returncode
        finally:
            server.terminate()
            try: server.wait(timeout=9)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            server.stdout.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--canvas', choices=['360x640', '1080x1920', '640x360'], default='360x640')
    parser.add_argument('--history', choices=[0, 20, 50], type=int, action='append')
    parser.add_argument('--mode', choices=['silent', 'audio', 'mixed', 'single', 'all'], default='all')
    args = parser.parse_args(); out = args.out.resolve()
    if out.exists(): parser.error('--out must be new; retain previous failures')
    out.mkdir()
    codes = []
    for mode in (['silent', 'audio', 'mixed', 'single'] if args.mode == 'all' else [args.mode]):
        folder = out / mode; folder.mkdir()
        for depth in (args.history or (0, 20, 50)):
            codes.append(run_case(folder / f'history-{depth}', depth, str(_chrome_path()),
                tuple(map(int, args.canvas.split('x'))), mode))
    raise SystemExit(1 if any(codes) else 0)
