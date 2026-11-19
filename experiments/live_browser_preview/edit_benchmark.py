"""Actual visual commands -> fresh, semantically correct native preview."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys

from caption_benchmark import command
from moviestar.local_preview import read_snapshot
from moviestar.native_preview import compile_snapshot
from moviestar.spec import append_revision, load_spec, save_spec

HERE = Path(__file__).resolve().parent


def _static_initial(value):
    if isinstance(value, dict):
        return {k: _static_initial(v) for k, v in value.items() if k not in ('motion', 'media_motion', 'content', 'caption')}
    if isinstance(value, list):
        return [_static_initial(v) for v in value]
    return value


def semantic_packet(packet):
    """Keep authored content, excluding request IDs and generated paint assets."""
    if 'segments' in packet:
        result = {'segments': [{'offset': s['offset'], 'packet': semantic_packet(s['packet'])}
            for s in packet['segments']],
            **{k: packet[k] for k in ('range', 'canvas', 'fps')}}
    else:
        result = {k: packet[k] for k in ('range', 'canvas', 'fps', 'initial', 'media', 'samples')}
        result['initial'] = _static_initial(packet['initial'])
        if packet.get('caption_plane'):
            plane = packet['caption_plane']
            result['caption_plane'] = {'frames': plane['frames'], 'states': [
                {k: state[k] for k in ('frame', 'cues')} for state in plane['states']]}
    if packet.get('audio_plane'):
        result['audio_plane'] = {k: packet['audio_plane'][k] for k in ('source_audio', 'tracks')}
    return result


def expected(root, out, painted):
    from moviestar.native_captions import paint_plane
    packet, assets = compile_snapshot(read_snapshot(root, 0, 5))
    result = semantic_packet(packet)
    raw = [s['packet'] for s in packet['segments']] if 'segments' in packet else [packet]
    normalized = [s['packet'] for s in result['segments']] if 'segments' in result else [result]
    for source, target in zip(raw, normalized):
        if not source.get('caption_plane'):
            continue
        job = source['caption_plane']['job']
        if job not in painted:
            # Independent canonical paint: never use the runtime reuse cache.
            directory = out / 'reference-captions' / job
            directory.parent.mkdir(exist_ok=True)
            paths = paint_plane(assets[job], directory)
            painted[job] = [{'sha256': sha256(path.read_bytes()).hexdigest(), 'rect': rect}
                for path, rect in paths]
        target['caption_plane']['rasters'] = painted[job]
    return result


def _prepare_case(root, out, depth):
    painted = {}
    spec = load_spec(root)
    spec['revisions'] = []
    for index in range(depth):
        after = deepcopy(spec)
        after['composition'][0]['name'] = f'History {index}'
        spec = append_revision(spec, after, f'History setup {index}')
    save_spec(spec, cwd=root, record=False)
    original = (root / 'moviestar/spec.json').read_bytes()
    project = (root / 'moviestar/project.json').read_bytes()
    # Scene commands select the name. Seeded history intentionally changes it.
    scene = spec['composition'][0]['name']
    first = next(o for o in spec['overlays'] if o.get('tokens'))
    word = re.search(r'[A-Za-z]+', first['text']).group()
    steps = [
        ('caption-wording', ['captions', 'rules', 'add', '--replace', word + '=Really']),
        ('highlight-color', ['captions', 'generate', '--highlight', 'spoken-word',
                             '--highlight-color', '#65ffb2']),
        ('speaker-framing', None),
        ('pip-position', ['scenes', 'geometry', scene + ':inset', '--at', 'top-left']),
        ('main-picture', None)]
    (out / 'initial-spec.json').write_bytes(original)
    (out / 'initial-project.json').write_bytes(project)
    initial = expected(root, out, painted)
    (out / 'initial-expected.json').write_text(json.dumps(initial))
    actions = []
    for name, args in steps:
        if args is None:
            model = load_spec(root)
            if name == 'speaker-framing':
                slot = next(s for s in model['composition'][0]['slots'] if s['slot'] == 'main')
                slot['framing'] = {'mode': 'fill', 'x': .25, 'y': .5}
            else:
                for slot in model['composition'][0]['slots']:
                    slot['slot'] = 'inset' if slot['slot'] == 'main' else 'main'
            path = out / (name + '.model.json')
            path.write_text(json.dumps(model))
            args = ['spec', '--edit', str(path)]
        response = command(root, args)
        wanted = expected(root, out, painted)
        if wanted == (actions[-1]['expected'] if actions else initial):
            raise ValueError(name + ' did not change the canonical preview')
        (out / (name + '.setup-output.json')).write_text(json.dumps(response, indent=2))
        actions.append({'name': name, 'args': args, 'expected': wanted})
    # Rules belong to project.json, outside the spec undo journal. Reverse four
    # spec edits with undo, then remove the rule explicitly using its returned ID.
    for name, _ in reversed(steps[1:]):
        response = command(root, ['undo'])
        (out / ('undo-' + name + '.setup-output.json')).write_text(json.dumps(response, indent=2))
        actions.append({'name': 'undo-' + name, 'args': ['undo'], 'expected': expected(root, out, painted)})
    rule = json.loads((out / 'caption-wording.setup-output.json').read_text())['rule']['id']
    args = ['captions', 'rules', 'remove', rule]
    response = command(root, args)
    (out / 'remove-caption-rule.setup-output.json').write_text(json.dumps(response, indent=2))
    actions.append({'name': 'remove-caption-rule', 'args': args, 'expected': expected(root, out, painted)})
    if actions[-1]['expected'] != initial:
        raise ValueError('Reversals did not restore original canonical packet')
    # Restore BOTH files, including the rule ID counter, before timed commands.
    (root / 'moviestar/spec.json').write_bytes(original)
    (root / 'moviestar/project.json').write_bytes(project)
    case = {'initial': initial, 'actions': actions, 'initial_history': depth,
        'canvas': initial['canvas'], 'probe_time': .2,
        'expected_history': max(0, min(50, depth + 4) - 4)}
    (out / 'case.json').write_text(json.dumps(case, indent=2))
    return case


def prepare_case(root, out, depth=50):
    """Use only an owned test copy; model files stay outside the watched project."""
    root, out = Path(root).resolve(), Path(out).resolve()
    if root.parent != out:
        raise ValueError('Benchmark project must be an owned child of output directory')
    previous = Path.cwd()
    try:
        os.chdir(root)  # Match project font discovery during expected compilation.
        return _prepare_case(root, out, depth)
    finally:
        os.chdir(previous)


def prepare_generated(root, canvas):
    """Three cut scenes, highlighted words and a separate prepared dialogue mix."""
    from caption_benchmark import prepare
    from audio import add_dialogue
    from moviestar.timecodes import format_timecode
    prepare(root, canvas)
    spec = load_spec(root)
    original = deepcopy(spec['composition'][0])
    spec['composition'] = []
    for identity, offset, duration in [('review', 0, 1), ('middle', 1, 2), ('return', 3, 2)]:
        part = deepcopy(original)
        part.update(id=identity, name=identity)
        if identity == 'middle':
            part['slots'].reverse()
        for index, slot in enumerate(part['slots']):
            origin = (1 if slot['source'] == 'camera' else 2) + offset
            slot.update(id=f'{identity}_{index}', slot='main' if index == 0 else 'inset',
                source_from=format_timecode(origin)['text'],
                source_to=format_timecode(origin + duration)['text'])
        part['audio_from'] = part['slots'][0]['source']
        spec['composition'].append(part)
    save_spec(spec, cwd=root)
    command(root, ['captions', 'generate', '--highlight', 'spoken-word'])
    add_dialogue(root)


def run_case(folder, depth, chrome, canvas):
    from benchmark import driver_hashes
    import moviestar
    folder.mkdir()
    root = folder / 'project'
    prepare_generated(root, canvas)
    case = prepare_case(root, folder, depth)
    package = Path(moviestar.__file__).parent
    case.update(producer_hashes=driver_hashes(), environment={
        'python': sys.version,
        'chrome': subprocess.check_output([chrome, '--version'], text=True).strip(),
        'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0],
        'node': subprocess.check_output(['node', '--version'], text=True).strip()},
        package_source_hashes={p.name: sha256(p.read_bytes()).hexdigest()
            for p in sorted(package.iterdir()) if p.suffix in ('.py', '.mjs')})
    (folder / 'case.json').write_text(json.dumps(case, indent=2))
    # Retain startup errors and partial driver reports. Never overwrite a run.
    with (folder / 'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview',
            '--workspace', str(root), '--mode', 'native', '--from', '0', '--to', '5',
            '--port', '0', '--no-open'], stdout=subprocess.PIPE, stderr=log, text=True,
            env={**os.environ, 'MOVIESTAR_PREVIEW_TIMING': '1'})
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                if not selector.select(30):
                    raise TimeoutError('Visual preview startup')
                ready = json.loads(server.stdout.readline())
                (folder / 'ready.json').write_text(json.dumps(ready))
                if not ready.get('url'):
                    raise RuntimeError(str(ready))
            with (folder / 'driver.log').open('w') as driver:
                code = subprocess.run(['node', str(HERE / 'edit_benchmark.mjs'), ready['url'],
                    str(root), str(folder), sys.executable, chrome], stdout=driver,
                    stderr=subprocess.STDOUT, timeout=240).returncode
            (folder / 'exit.json').write_text(json.dumps({'driver_exit': code}))
            return code
        finally:
            server.terminate()
            try:
                server.wait(timeout=9)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            server.stdout.close()


if __name__ == '__main__':
    from moviestar.spatial_layout import _chrome_path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--canvas', choices=('360x640', '1080x1920'), default='360x640')
    parser.add_argument('--history', type=int, choices=(0, 20, 50), action='append',
        help='Run only these history depths (default: all three).')
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        parser.error('--out must be new; retain previous failures')
    out.mkdir()
    canvas = tuple(map(int, args.canvas.split('x')))
    codes = [run_case(out / f'history-{depth}', depth, str(_chrome_path()), canvas)
        for depth in (args.history or (0, 20, 50))]
    raise SystemExit(1 if any(codes) else 0)
