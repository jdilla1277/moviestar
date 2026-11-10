"""Opt-in private-media checks. Outputs can contain media/text: keep them private."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from benchmark import run_project_case
from scaling import prepare_window
from moviestar.spec import save_spec
from moviestar.timecodes import format_timecode


def srt_cues(path, start, end):
    if path is None:
        return None
    def seconds(text):
        h, m, s = text.strip().replace(',', '.').split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    cues = []
    for block in re.split(r'\r?\n\s*\r?\n', path.read_text(encoding='utf-8-sig').strip()):
        lines = block.splitlines()
        if len(lines) < 3 or '-->' not in lines[1]:
            continue
        left, right = map(seconds, lines[1].split(' --> '))
        left, right = max(left, start), min(right, end)
        if left >= right:
            continue
        cues.append({'id': 'cue_' + str(len(cues)), 'kind': 'caption', 'track': 'captions',
            'from': format_timecode(1801 + left - start)['text'],
            'to': format_timecode(1801 + right - start)['text'], 'text': '\n'.join(lines[2:]),
            'position': {'preset': 'bottom'}, 'style': {'preset': 'plain'}})
    return cues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', type=Path, required=True)
    parser.add_argument('--srt', type=Path, help='optional source-clock subtitles, used as frozen manual cues')
    parser.add_argument('--out', type=Path, required=True, help='new private output directory')
    parser.add_argument('--positions', nargs='+', choices=['beginning', 'middle', 'end'], default=['beginning', 'middle', 'end'])
    parser.add_argument('--orientation', choices=['landscape', 'portrait', 'both'], default='both')
    parser.add_argument('--baseline', action='store_true', help='measure an older installed player without requiring the byte buffer')
    parser.add_argument('--baseline-results', type=Path, help='require exact decoded source pixel hashes from a prior run')
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    chrome = os.environ.get('MOVIESTAR_CHROME_PATH') or shutil.which('chrome-headless-shell')
    if not chrome:
        parser.error('set MOVIESTAR_CHROME_PATH')
    if os.environ.get('PYTHONPATH'):
        os.environ['PYTHONPATH'] = os.pathsep.join(str(Path(item).resolve()) for item in os.environ['PYTHONPATH'].split(os.pathsep) if item)
    references = {}
    if args.baseline_results:
        for case in json.loads(args.baseline_results.read_text())['cases']:
            references[case['canvas']] = {str(item['at']): item['source_pixels']['sha256'] for item in case['parity']}
    report = {'profile': 'private-real-media', 'cases': [], 'resources': [], 'problems': [],
              'versions': {'python': sys.version.split()[0], 'node': subprocess.check_output(['node', '--version'], text=True).strip(),
                           'chrome': subprocess.check_output([chrome, '--version'], text=True).strip()},
              'scope': 'Private source paths/text/pixels may appear in this directory. No upload is performed.'}
    orientations = [('landscape', False), ('portrait', True)] if args.orientation == 'both' else [(args.orientation, args.orientation == 'portrait')]
    for index, source in enumerate(args.source):
        source = source.resolve(strict=True)
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(source)], text=True))
        duration = float(probe['format']['duration'])
        if duration < 30:
            parser.error('Real-media scaling cases require a native-compatible recording of at least 30 seconds.')
        offsets = {'beginning': 0, 'middle': max(0, int(duration / 2) - 4), 'end': max(0, int(duration) - 10)}
        for orientation, portrait in orientations:
            for position in args.positions:
                name = f'recording-{index + 1}-{orientation}-{position}'
                folder = out / name; folder.mkdir()
                root = folder / 'project'; offset = offsets[position]
                spec, project = prepare_window(root, portrait, source, offset)
                cues = srt_cues(args.srt, offset + 1, offset + 6)
                if cues is not None:
                    spec['overlays'] = cues; save_spec(spec, cwd=root)
                fields = {'media_kind': 'real', 'source_position': position, 'source_bytes': source.stat().st_size,
                          'require_source_buffering': not args.baseline}
                if args.baseline_results:
                    if name not in references:
                        raise ValueError('Baseline does not contain case ' + name)
                    fields['reference_hashes'] = references[name]
                result, resources = run_project_case(root, folder, 1801, 1806, chrome, fields)
                result['canvas'] = name
                report['cases'].append(result); report['resources'].append({'case': name, **resources})
                report['problems'].extend(name + ': ' + problem for problem in result['problems'])
                (out / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'report': str(out / 'report.json'), 'cases': [
        {'case': case['canvas'], **case.get('summary', {})} for case in report['cases']], 'problems': report['problems']}))
    return bool(report['problems'])


if __name__ == '__main__':
    raise SystemExit(main())
