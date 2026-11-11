"""Compare unchanged preview gates at explicit undo depths; use a fresh output."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from benchmark import driver_hashes, run_project_case
from scaling import long_source, prepare_window
from cuts import prepare_cuts
from real_benchmark import srt_cues
from moviestar.spec import save_spec
import moviestar.spec as spec_module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--profile', choices=['long-source', 'cuts', 'real'], default='long-source')
    parser.add_argument('--source', type=Path, help='existing long synthetic source or private real recording; never copied')
    parser.add_argument('--srt', type=Path, help='optional private source-clock cues; keep outputs private')
    parser.add_argument('--source-offset', type=int, default=2, help='real source start; selected review begins one second later')
    parser.add_argument('--histories', type=int, nargs='+', choices=[0,20,50], default=[0,20,50])
    parser.add_argument('--orientation', choices=['both','landscape','portrait'], default='both')
    parser.add_argument('--positions', nargs='+', choices=['beginning','middle','end'], default=['beginning','middle','end'])
    parser.add_argument('--revision', required=True, help='revision whose installed product module is being measured')
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    chrome = os.environ.get('MOVIESTAR_CHROME_PATH')
    if not chrome: parser.error('set MOVIESTAR_CHROME_PATH')
    if args.profile == 'real' and not args.source: parser.error('--profile real requires --source')
    if args.profile == 'cuts' and args.source: parser.error('the cut reference generates its own synthetic sources')
    if args.srt and args.profile != 'real': parser.error('--srt is only supported with private real-media evaluation')
    source = args.source.resolve(strict=True) if args.source else None
    if args.profile == 'long-source' and source is None: source = long_source(out)
    report = {'profile': args.profile, 'code_revision': args.revision,
              'spec_module_sha256': sha256(Path(spec_module.__file__).read_bytes()).hexdigest(),
              'driver_files_sha256': driver_hashes(),
              'versions': {'python': sys.version.split()[0], 'platform': platform.platform(),
                  'node': subprocess.check_output(['node','--version'], text=True).strip(),
                  'chrome': subprocess.check_output([chrome,'--version'], text=True).strip(),
                  'ffmpeg': subprocess.check_output(['ffmpeg','-version'], text=True).splitlines()[0]},
              'load_start': os.getloadavg(), 'cases': [], 'resources': [], 'problems': []}
    positions = [('reference', args.source_offset)]
    if args.profile == 'long-source':
        positions = [(name,offset) for name,offset in [('beginning',0),('middle',2704),('end',5384)] if name in args.positions]
    for orientation, portrait in [('landscape',False),('portrait',True)]:
        if args.orientation not in ('both',orientation): continue
        for position,offset in positions:
            for depth in args.histories:
                name = f'{orientation}-{position}-history-{depth}'
                folder = out/name; folder.mkdir(); root = folder/'project'
                if args.profile == 'cuts':
                    _,project,review = prepare_cuts(root,portrait)
                    start,end = review['from'],review['to']
                    sample_times = [0,2,3-1/project['sources'][0]['fps'],3,3+1/project['sources'][0]['fps'],6-1/project['sources'][0]['fps'],6,6+1/project['sources'][0]['fps'],8]
                else:
                    spec,project = prepare_window(root,portrait,source,offset)
                    start,end = 1801,1806; sample_times = (0,2,4)
                    if args.profile == 'real' and args.srt:
                        spec['overlays'] = srt_cues(args.srt,offset+1,offset+6)
                        save_spec(spec,cwd=root)
                fields = {'initial_history': depth, 'source_position': position, 'require_source_buffering': True,
                          'media_kind': 'real' if args.profile == 'real' else 'synthetic',
                          'continuous_pilot': args.profile == 'cuts'}
                result,resources = run_project_case(root,folder,start,end,chrome,fields,sample_times=sample_times)
                result['canvas'] = name; report['cases'].append(result)
                report['resources'].append({'case': name, **resources})
                report['problems'].extend(name+': '+p for p in result['problems'])
                (out/'report.json').write_text(json.dumps(report,indent=2))
                print(json.dumps({'case': name, **result.get('summary',{}), 'problems': result['problems']}),flush=True)
    return bool(report['problems'])


if __name__ == '__main__': raise SystemExit(main())
