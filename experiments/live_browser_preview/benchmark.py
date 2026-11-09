"""Run portable canonical-layout, CLI-edit and native audio/video review checks."""
import argparse
import json
import os
from pathlib import Path
import platform
import selectors
import shutil
import subprocess
import sys
import time

from fixtures import generate
from moviestar.local_preview import read_snapshot
from moviestar.resolved import resolve_project
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_layout import measure_spatial_layout

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='new output directory')
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    os.environ['PYTHONPATH'] = str(HERE.parents[1]/'app'/'src') + os.pathsep + os.environ.get('PYTHONPATH', '')
    chrome = os.environ.get('MOVIESTAR_CHROME_PATH') or shutil.which('chrome-headless-shell')
    if not chrome:
        parser.error('set MOVIESTAR_CHROME_PATH to Chrome/Chrome for Testing')
    report = {'versions': {'python': sys.version.split()[0], 'platform': platform.platform(),
        'chrome': subprocess.check_output([chrome, '--version'], text=True).strip(),
        'node': subprocess.check_output(['node', '--version'], text=True).strip()},
        'load_start': os.getloadavg() if hasattr(os, 'getloadavg') else None, 'cases': [], 'problems': []}
    for name, portrait in [('landscape', False), ('portrait', True)]:
        folder = out / name;folder.mkdir()
        root = folder / 'project';generate(root, portrait)
        # Establish the same CLI-normalized state before the timed edit series.
        model = folder/'initial.json'; model.write_text(json.dumps(json.loads((root/'moviestar'/'spec.json').read_text())['spatial']))
        subprocess.run([sys.executable, '-m', 'moviestar.cli', 'composition', 'model', 'apply', str(model)], cwd=root, check=True, capture_output=True)
        snapshot = read_snapshot(root, 0, 5)
        timeline = resolve_project(snapshot['project'], snapshot['spec'])
        durations = {'camera': 8}
        canonical = []
        for at in [0, 2, 4]:
            frame = compile_spatial_frame(snapshot['spec'], timeline, at, source_durations=durations)
            layout = measure_spatial_layout(frame, timeline.canvas)
            if layout['diagnostics']:
                raise RuntimeError('Reference layout has diagnostics: '+json.dumps(layout['diagnostics']))
            canonical.append({'at': at, 'boxes': layout['boxes']})
        (folder / 'canonical.json').write_text(json.dumps(canonical))
        started = time.monotonic()
        with (folder / 'server.log').open('w') as stderr:
            server = subprocess.Popen([sys.executable, str(HERE/'serve.py'), '--project', str(root), '--port', '0'], stdout=subprocess.PIPE, stderr=stderr, text=True)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(server.stdout, selectors.EVENT_READ)
                    if not selector.select(15):
                        raise TimeoutError('Live server did not start in 15 seconds')
                    url = json.loads(server.stdout.readline())['url']
                startup = (time.monotonic()-started)*1000
                with (folder/'driver.log').open('w') as log:
                    driver = subprocess.Popen(['node', str(HERE/'benchmark.mjs'), url, str(root), str(folder), sys.executable, chrome], stdout=log, stderr=subprocess.STDOUT)
                    try:
                        code = driver.wait(timeout=180)
                    finally:
                        if driver.poll() is None:
                            driver.terminate()  # Let the driver close its owned Chrome group.
                            try:driver.wait(timeout=5)
                            except subprocess.TimeoutExpired:driver.kill();driver.wait()
                result = json.loads((folder/'browser-report.json').read_text())
                result.update(canvas=name, server_start_ms=round(startup), driver_exit=code)
                report['cases'].append(result)
                report['problems'].extend(f'{name}: {problem}' for problem in result['problems'])
                if code and not result['problems']:
                    report['problems'].append(f'{name}: driver exited {code}')
            except Exception as exc:
                report['problems'].append(f'{name}: {exc}')
            finally:
                server.terminate()
                try:server.wait(timeout=5)
                except subprocess.TimeoutExpired:server.kill();server.wait()
                server.stdout.close()
                (out/'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'report': str(out/'report.json'), 'cases': [
        {'canvas': case['canvas'], 'cold_ms': case['cold'].get('navigation_to_visible_ms'), **case.get('summary', {})}
        for case in report['cases']], 'problems': report['problems']}))
    return 1 if report['problems'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
