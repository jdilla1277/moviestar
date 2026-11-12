"""Measure representative preset PiP edits and undo at explicit history depths."""
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
from moviestar.native_preview import asset_identity
from moviestar.spatial_layout import _chrome_path
from moviestar.spec import append_revision, load_spec, save_spec, layout_regions

HERE = Path(__file__).resolve().parent


def recipe(spec):
    scene = spec['composition'][0]
    return {'canvas': f"{spec['composition_canvas']['width']}x{spec['composition_canvas']['height']}",
        'scenes': [{'id': scene['id'], 'name': scene['name'], 'layout': 'picture-in-picture',
            'audio_from': scene['audio_from'], 'slots': [
                {**{k: slot[k] for k in ('slot', 'source')}, 'from': slot['source_from'], 'to': slot['source_to'],
                 'framing': f"fill:x={slot.get('framing', {}).get('x', .5)}",
                 **({'geometry': scene['layout']['geometry']['inset']} if slot['slot'] == 'inset' else {})}
                for slot in scene['slots']]}]}


def expected(spec, root):
    scene = spec['composition'][0]
    regions = layout_regions(scene['layout'], spec['composition_canvas'], result_time_s=0)
    sources = {s['id']: (root/s['path']).resolve() for s in spec['sources']}
    from moviestar.spec import parse_timecode_string
    def asset(source):
        path = sources[source]
        return sha256(json.dumps([str(path), asset_identity(path)]).encode()).hexdigest()[:24]
    return {'speaker_asset': asset(scene['audio_from']), 'videos': [
        {'path': [scene['id'], 'slots', s['slot'], 'video'], 'asset': asset(s['source']),
         'start': parse_timecode_string(s['source_from'], 'from'), 'rect': regions[s['slot']],
         'object_position': f"{s.get('framing', {}).get('x', .5)*100:g}% 50%"}
        for s in scene['slots']]}


def run_case(folder, depth, chrome, sources=None):
    folder.mkdir()
    root = folder/'project'
    prepare_pip(root, sources=sources, offsets=(3, 3) if sources else (1, 2))
    initial = load_spec(root)
    initial_file = folder/'initial.json'; initial_file.write_text(json.dumps(recipe(initial)))
    # Normalize using the same public command as the agent; outside edit timing.
    subprocess.run([sys.executable, '-m', 'moviestar.cli', 'scenes', 'set', str(initial_file)],
        cwd=root, capture_output=True, check=True, timeout=30)
    spec = load_spec(root); spec['revisions'] = []
    for i in range(depth):
        after = deepcopy(spec)
        after['composition'][0]['layout']['geometry']['inset']['anchor'] = 'top-left' if i % 2 == 0 else 'top-right'
        spec = append_revision(spec, after, f'PiP history setup {i+1}')
    save_spec(spec, cwd=root, record=False)
    states = [deepcopy(spec)]
    state = deepcopy(spec); state['composition'][0]['layout']['geometry']['inset']['anchor'] = 'bottom-right'
    states.append(deepcopy(state))
    state['composition'][0]['layout']['geometry']['inset']['size'] = .22
    states.append(deepcopy(state))
    state['composition'][0]['slots'][0]['framing'] = {'mode': 'fill', 'x': .35, 'y': .5}
    states.append(deepcopy(state))
    scene = state['composition'][0]
    a, b = scene['slots']
    for field in ('source', 'source_from', 'source_to'): a[field], b[field] = b[field], a[field]
    scene['audio_from'] = a['source']
    states.append(deepcopy(state))
    names = ['move-inset', 'resize-inset', 'reframe-main', 'swap-speakers']
    actions = []
    for i, name in enumerate(names, 1):
        file = folder/f'{i:02}-{name}.input.json'; file.write_text(json.dumps(recipe(states[i])))
        args = (['scenes', 'geometry', 'Review:inset', '--at', 'bottom-right'] if i == 1 else
                ['scenes', 'geometry', 'Review:inset', '--size', '0.22'] if i == 2 else ['scenes', 'set', str(file)])
        actions.append({'name': name, 'args': args, 'expected': expected(states[i], root)})
    actions.extend({'name': 'undo-'+names[i], 'args': ['undo'], 'expected': expected(states[i], root)}
                   for i in reversed(range(4)))
    case = {'initial_history': depth, 'media_kind': 'private' if sources else 'generated',
        'code_commit': subprocess.check_output(['git', '-C', str(HERE.parents[1]), 'rev-parse', 'HEAD'], text=True).strip(),
        'initial': expected(states[0], root), 'actions': actions, 'producer_hashes': driver_hashes(),
        'environment': {'python': sys.version, 'node': subprocess.check_output(['node', '--version'], text=True).strip(),
            'chrome': subprocess.check_output([chrome, '--version'], text=True).strip(),
            'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0]},
        'package_source_hashes': {str(path.relative_to(HERE.parents[1])): sha256(path.read_bytes()).hexdigest()
            for path in sorted((HERE.parents[1]/'app/src/moviestar').iterdir()) if path.suffix in ('.py', '.mjs')}}
    (folder/'case.json').write_text(json.dumps(case, indent=2))
    with (folder/'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root),
            '--mode', 'native', '--from', '0', '--to', '5', '--port', '0', '--no-open'], stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                if not selector.select(15): raise TimeoutError('PiP preview server startup')
                ready = json.loads(server.stdout.readline())
                if not ready.get('url'): raise RuntimeError(str(ready))
            with (folder/'driver.log').open('w') as driver_log:
                result = subprocess.run(['node', str(HERE/'pip_benchmark.mjs'), ready['url'], str(root), str(folder), sys.executable, chrome],
                    stdout=driver_log, stderr=subprocess.STDOUT, timeout=180)
            return result.returncode
        finally:
            server.terminate()
            try: server.wait(timeout=9)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            server.stdout.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--sources', nargs=2, type=Path)
    args = parser.parse_args(); out = args.out.resolve()
    if out.exists(): parser.error('--out must be new; previous failures must be retained')
    out.mkdir()
    codes = [run_case(out/f'history-{depth}', depth, str(_chrome_path()), args.sources) for depth in (0, 20, 50)]
    raise SystemExit(1 if any(codes) else 0)
