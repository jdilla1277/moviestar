"""Diagnostic base/head comparison; never replaces the ordinary acceptance run."""
import argparse
from hashlib import sha256
import json
import os
import platform
from pathlib import Path
import re
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def run(args, *, env=None, log=None):
    return subprocess.run(args, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT if log else None,
                          check=True, text=True)


def hashes(folder, pattern):
    return {str(p.relative_to(folder)): sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob(pattern)) if p.is_file()}


def compare(base, head, out):
    out = out.resolve(); out.mkdir(parents=True, exist_ok=False)
    runtimes = {}
    for label, ref in (('base', base), ('head', head)):
        assert re.fullmatch('[a-f0-9]{40}', ref), 'Use full immutable commit SHAs'
        tree = out / (label + '-source'); tree.mkdir()
        archive = out / (label + '.tar')
        run(['git', 'archive', '--output', str(archive), ref, 'app'])
        with tarfile.open(archive) as bundle:
            bundle.extractall(tree, filter='data')
        wheels = out / (label + '-wheel')
        with (out / (label + '-build.log')).open('w') as log:
            run([sys.executable, '-m', 'build', '--wheel', '--outdir', str(wheels), str(tree/'app')], log=log)
        wheel, = wheels.glob('*.whl'); installed = out / (label + '-installed')
        with (out / (label + '-install.log')).open('w') as log:
            run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--target', str(installed), str(wheel)], log=log)
        source = tree / 'app/src/moviestar'
        runtime = {p.name: sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.suffix in ('.py', '.mjs')}
        for name, digest in runtime.items():
            assert sha256((installed/'moviestar'/name).read_bytes()).hexdigest() == digest, name
        runtimes[label] = {'commit': ref, 'installed': str(installed), 'source_hashes': runtime,
                           'wheel_sha256': sha256(wheel.read_bytes()).hexdigest()}
    # All children use this interpreter/dependency set and the same producer.
    envs = {label: {**os.environ, 'PYTHONPATH': item['installed']} for label,item in runtimes.items()}
    fixture = out / 'fixture'
    setup = "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from edit_benchmark import prepare_generated;prepare_generated(Path(sys.argv[2]),(1080,1920))"
    with (out / 'fixture.log').open('w') as log:
        run([sys.executable, '-c', setup, str(HERE), str(fixture)], env=envs['head'], log=log)
    inputs = {str(p.relative_to(fixture)): sha256(p.read_bytes()).hexdigest()
              for p in fixture.rglob('*') if p.suffix in ('.mp4', '.wav')}
    assert inputs
    # Sources in the copied projects deliberately refer to this one fixture's
    # immutable media. Only each copy's spec/journal and review files change.
    driver = """
import sys,shutil
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import edit_benchmark as bench
from moviestar.spatial_layout import _chrome_path
bench.prepare_generated=lambda root,canvas:shutil.copytree(sys.argv[2],root)
raise SystemExit(bench.run_case(Path(sys.argv[3]),0,str(_chrome_path()),(1080,1920)))
"""
    from importlib.metadata import distributions
    ledger = {'purpose': 'diagnostic only; ordinary zero-drop acceptance is unchanged',
        'order': ['base', 'head', 'head', 'base'], 'runtimes': runtimes, 'media_hashes': inputs,
        'producer_hashes': {p.name: sha256(p.read_bytes()).hexdigest() for p in HERE.iterdir() if p.suffix in ('.py','.mjs')},
        'python': sys.version, 'platform': platform.platform(), 'cpu_count': os.cpu_count(),
        'cpu_info': Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else None,
        'dependencies': {d.metadata['Name']: d.version for d in distributions()}, 'trials': []}
    manifest = out / 'comparison.json'; manifest.write_text(json.dumps(ledger, indent=2))
    for index,label in enumerate(ledger['order']):
        folder = out / f'{index}-{label}'
        with (out / f'{index}-{label}.log').open('w') as log:
            result = subprocess.run([sys.executable, '-c', driver, str(HERE), str(fixture), str(folder)],
                cwd=REPO, env=envs[label], stdout=log, stderr=subprocess.STDOUT, timeout=360)
        case = json.loads((folder / 'case.json').read_text())
        assert case['package_source_hashes'] == runtimes[label]['source_hashes'], 'Wrong imported runtime'
        if ledger['trials']:
            assert case['environment'] == ledger['trials'][0]['environment'], 'Environment changed'
        report_path = folder / 'browser-report.json'
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        ledger['trials'].append({'runtime':label,'exit_code':result.returncode,'report':str(report_path),
            'environment':case['environment'], 'problems':report.get('problems', ['No browser report']),
            'playbacks':[{'label':r['label'],'quality':r['metrics'].get('video_quality'),
                'error':r['metrics'].get('error'),'stalls':r['metrics'].get('stalls')}
                for r in report.get('playbacks',[])]})
        for name,digest in inputs.items():
            assert sha256((fixture/name).read_bytes()).hexdigest()==digest, name
        manifest.write_text(json.dumps(ledger, indent=2))
        print(json.dumps(ledger['trials'][-1]), flush=True)
    return any(trial['exit_code'] for trial in ledger['trials'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(compare(args.base, args.head, args.out))
