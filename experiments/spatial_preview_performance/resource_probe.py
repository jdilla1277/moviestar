"""Check repeated large-frame composition against the frozen FFmpeg pixels.

Browser capture/setup is outside this compositor-only memory observation. Use
profile_preview.py separately for the real worker's combined process-group RSS.
"""

from copy import deepcopy
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

from benchmark import fixture, capture
from compositors import rgb_bytes
from profile_preview import Resources


def worker(directory, frames):
    from moviestar.spatial_render import _compose
    data = json.loads((directory / 'inputs.json').read_text())
    videos = {key: {'decoded': directory / value['decoded']} for key, value in data['videos'].items()}
    expected = sha256(rgb_bytes(directory / 'reference.png')).hexdigest()
    output = directory / 'repeated.png'
    resident = []
    started = time.perf_counter()
    for index in range(frames):
        _compose(directory, data['canvas'], data['planes'], videos, output)
        if index in {0, frames - 1}:
            assert sha256(rgb_bytes(output)).hexdigest() == expected
        resident.append(int(subprocess.check_output(['ps', '-o', 'rss=', '-p', str(os.getpid())], text=True)))
    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {'canvas': data['canvas'], 'planes': len(data['planes']), 'frames': frames,
              'elapsed_ms': (time.perf_counter() - started) * 1000,
              'first_and_last_pixels_exact': True,
              'worker_highwater_rss_mib': maximum / (1024**2 if sys.platform == 'darwin' else 1024),
              'post_frame_rss_mib': [value / 1024 for value in resident],
              'scope': 'Compositor worker only, including hash verification; capture/browser setup excluded.'}
    (directory.parent / 'worker-report.json').write_text(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--frames', type=int, default=20)
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.frames < 1:
        parser.error('--frames must be positive')
    if args.worker:
        worker(args.worker, args.frames)
        return
    if args.out is None:
        parser.error('--out is required')
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    spec, project = fixture(root)
    spec['composition_canvas'] = {'preset': None, 'width': 3840, 'height': 2160, 'aspect_ratio': '16:9'}
    children = spec['spatial']['scenes']['review']['layout']['children']
    second = deepcopy(children[0])
    second['id'] = 'inset'
    second['layout'] = {'position': 'absolute', 'left': '55%', 'top': '48%',
                        'width': '38%', 'height': '43%', 'z_index': 3}
    children.append(second)
    directory = capture(root, 'large-frame', spec, project)
    with (root / 'worker.log').open('wb') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', str(directory),
                                    '--frames', str(args.frames)], stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        monitor = Resources(process.pid, root)
        monitor.start()
        try:
            code = process.wait(timeout=180)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                if process.poll() is None:
                    process.kill()
            process.wait(timeout=2)
            monitor.close()
    if code:
        raise RuntimeError((root / 'worker.log').read_text()[-2000:])
    report = json.loads((root / 'worker-report.json').read_text())
    report['resources'] = monitor.report()
    report['source_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    report['app_hashes'] = {name: sha256((Path(__file__).resolve().parents[2] / 'app' / name).read_bytes()).hexdigest()
                            for name in ['src/moviestar/spatial_composite.py', 'src/moviestar/spatial_render.py']}
    (root / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
