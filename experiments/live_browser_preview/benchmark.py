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
import threading
import time

from fixtures import generate
from moviestar.local_preview import read_snapshot
from moviestar.resolved import resolve_project
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_layout import measure_spatial_layout
from moviestar.native_preview import compile_snapshot

HERE = Path(__file__).resolve().parent


class ResourceSampler:
    """Sample this runner's process family and output tree, not unrelated jobs."""

    def __init__(self, out):
        self.out = out
        self.stopped = threading.Event()
        self.peak_rss_kib = self.peak_bytes = self.samples = 0
        self.errors = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stopped.is_set():
            try:
                rows = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss='], text=True, timeout=2)
                processes = [tuple(map(int, line.split())) for line in rows.splitlines() if line.strip()]
                owned = {os.getpid()}
                while True:
                    descendants = {pid for pid, parent, _ in processes if parent in owned}
                    if descendants <= owned:
                        break
                    owned |= descendants
                self.peak_rss_kib = max(self.peak_rss_kib, sum(rss for pid, _, rss in processes if pid in owned))
                size = 0
                for path in self.out.rglob('*'):
                    try:
                        if path.is_file():
                            size += path.stat().st_size
                    except FileNotFoundError:
                        pass  # Owned Chrome profiles/request directories can expire.
                self.peak_bytes = max(self.peak_bytes, size)
                self.samples += 1
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self.errors.append(str(exc))
            self.stopped.wait(.5)

    def stop(self):
        self.stopped.set(); self.thread.join(timeout=3)
        return {'peak_family_rss_mib': round(self.peak_rss_kib / 1024, 1),
                'peak_output_mib': round(self.peak_bytes / 1024 ** 2, 1),
                'sample_count': self.samples, 'target_interval_s': .5, 'errors': self.errors,
                'scope': 'runner, preview worker/server, Node driver and owned Chrome; output includes source/fixtures'}


def run_project_case(root, folder, start, end, chrome, case_fields):
    # Establish the same CLI-normalized state before the timed edit series.
    model = folder/'initial.json'; model.write_text(json.dumps(json.loads((root/'moviestar'/'spec.json').read_text())['spatial']))
    subprocess.run([sys.executable, '-m', 'moviestar.cli', 'composition', 'model', 'apply', str(model)], cwd=root, check=True, capture_output=True)
    before = time.perf_counter()
    snapshot = read_snapshot(root, start, end)
    snapshot_ms = (time.perf_counter() - before) * 1000
    before = time.perf_counter()
    timeline = resolve_project(snapshot['project'], snapshot['spec'])
    resolve_ms = (time.perf_counter() - before) * 1000
    before = time.perf_counter()
    packet, _ = compile_snapshot(snapshot)
    compile_ms = (time.perf_counter() - before) * 1000
    case = {'range': {'from': start, 'to': end}, 'scene_id': 'review',
            'source_start': packet['source_start'], 'source_end': packet['source_end'],
            'barcode_period_frames': 240, 'fps': timeline.output_fps,
            'source_position': case_fields.get('source_position', 'reference'), 'project_duration_s': timeline.duration_s,
            'scene_count': len(snapshot['spec']['composition']),
            'diagnostics': {'read_snapshot_ms': snapshot_ms, 'resolve_project_ms': resolve_ms,
                'compile_snapshot_including_resolution_ms': compile_ms,
                'packet_bytes': len(json.dumps(packet).encode())}}
    case.update(case_fields)
    (folder / 'case.json').write_text(json.dumps(case))
    durations = {item['id']: item['duration']['seconds'] for item in snapshot['project']['sources']}
    canonical = []
    for at in [0, 2, 4]:
        frame = compile_spatial_frame(snapshot['spec'], timeline, start + at, source_durations=durations)
        layout = measure_spatial_layout(frame, timeline.canvas)
        if layout['diagnostics']:
            raise RuntimeError('Reference layout has diagnostics: '+json.dumps(layout['diagnostics']))
        canonical.append({'at': at, 'boxes': layout['boxes']})
    (folder / 'canonical.json').write_text(json.dumps(canonical))
    started = time.monotonic()
    resources = ResourceSampler(folder.parent); resources.thread.start()
    with (folder / 'server.log').open('w') as stderr:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root), '--from', str(start), '--to', str(end), '--mode', 'native', '--no-open', '--port', '0'], stdout=subprocess.PIPE, stderr=stderr, text=True)
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
            if code and not result['problems']:
                result['problems'].append(f'driver exited {code}')
            result.update(server_start_ms=round(startup), driver_exit=code, fixture=case)
        except Exception as exc:
            result = {'cold': {}, 'problems': [str(exc)], 'fixture': case}
        finally:
            server.terminate()
            try:server.wait(timeout=9)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            server.stdout.close()
            resource_result = resources.stop()
    return result, resource_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='new output directory')
    parser.add_argument('--profile', choices=['reference', 'long-source'], default='reference',
                        help='small reference, or 121-scene hour-long projects / 90min synthetic source')
    parser.add_argument('--baseline', action='store_true', help='measure an older installed player without requiring the new source buffer; keep all timing/fidelity gates')
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    if os.environ.get('PYTHONPATH'):
        os.environ['PYTHONPATH'] = os.pathsep.join(str(Path(entry).resolve())
            for entry in os.environ['PYTHONPATH'].split(os.pathsep) if entry)
    chrome = os.environ.get('MOVIESTAR_CHROME_PATH') or shutil.which('chrome-headless-shell')
    if not chrome:
        parser.error('set MOVIESTAR_CHROME_PATH to Chrome/Chrome for Testing')
    report = {'profile': args.profile, 'versions': {'python': sys.version.split()[0], 'platform': platform.platform(),
        'chrome': subprocess.check_output([chrome, '--version'], text=True).strip(),
        'node': subprocess.check_output(['node', '--version'], text=True).strip(),
        'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0]},
        'load_start': os.getloadavg() if hasattr(os, 'getloadavg') else None, 'cases': [], 'problems': []}
    source = None
    if args.profile == 'long-source':
        from scaling import long_source
        source = long_source(out)
        report['source'] = {'duration_s': 5400, 'bytes': source.stat().st_size,
                            'kind': 'synthetic repeated 640x360/30 H264, continuous AAC cues'}
    cases = [(orientation, portrait, position, offset)
             for orientation, portrait in [('landscape', False), ('portrait', True)]
             for position, offset in ([('beginning', 0), ('middle', 2704), ('end', 5384)]
                                      if source else [('reference', 0)])]
    for orientation, portrait, position, offset in cases:
        name = orientation if source is None else orientation + '-' + position
        folder = out / name;folder.mkdir()
        root = folder / 'project'
        if source:
            from scaling import prepare_window
            prepare_window(root, portrait, source, offset)
        else:
            generate(root, portrait)
        start, end = (1801, 1806) if source else (0, 5)
        result, resources = run_project_case(root, folder, start, end, chrome,
            {'source_position': position, 'require_source_buffering': not args.baseline})
        result['canvas'] = name
        report['cases'].append(result)
        report['resources'] = report.get('resources', []) + [{'case': name, **resources}]
        report['problems'].extend(f'{name}: {problem}' for problem in result['problems'])
        (out / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'report': str(out/'report.json'), 'cases': [
        {'canvas': case['canvas'], 'cold_ms': case['cold'].get('navigation_to_visible_ms'), **case.get('summary', {})}
        for case in report['cases']], 'problems': report['problems']}))
    return 1 if report['problems'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
