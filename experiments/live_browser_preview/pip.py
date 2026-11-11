"""Portable two-camera PiP fixture; optional private inputs are referenced in place."""
from copy import deepcopy
import argparse
import json
from pathlib import Path
import subprocess
import sys

from fixtures import generate
from moviestar.project import load_project
from moviestar.spec import save_spec
from moviestar.timecodes import format_timecode


def prepare_pip(root, *, spatial=False, sources=None, offsets=(1, 2), cuts=False, silent_listener=False):
    spec, _ = generate(root, portrait=True, pattern=True)
    if sources is None:
        other = root/'other.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(root/'cues.mp4'),
            '-vf', 'hue=h=120', *(['-an'] if silent_listener else ['-af', 'volume=0.25', '-c:a', 'aac']),
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(other)], check=True, timeout=30)
        sources = (root/'cues.mp4', other)
    sources = tuple(Path(p).resolve() for p in sources)
    subprocess.run([sys.executable, '-m', 'moviestar.cli', 'load', *(str(p) for p in sources),
        '--as', 'camera', '--as', 'other', '--force', '--no-transcribe', '--no-frames'],
        cwd=root, check=True, capture_output=True, timeout=30)
    spec['sources'] = [{'id': name, 'path': str(path), 'operations': []}
                       for name, path in zip(('camera', 'other'), sources)]
    # Small but portrait, so browser pixel checks remain affordable in CI.
    spec['composition_canvas'] = {'preset': None, 'width': 360, 'height': 640, 'aspect_ratio': '9:16'}
    scene = {'id': 'review', 'name': 'Review', 'audio_from': 'camera',
        'layout': {'preset': 'picture-in-picture', 'geometry': {'inset': {
            'size': .28, 'anchor': 'top-right', 'shape': {'kind': 'rounded', 'radius': .12,
                'border': {'width': 4, 'color': '#f4da67'}}}}},
        'slots': [{'slot': name, 'source': source, 'source_from': format_timecode(start)['text'],
            'source_to': format_timecode(start+5)['text'], 'framing': {'mode': 'fill', 'x': .55, 'y': .5}}
            for name, source, start in zip(('main', 'inset'), ('camera', 'other'), offsets)]}
    spec['composition'] = [scene]; spec['overlays'] = []
    if spatial:
        speaker = deepcopy(spec['spatial']['scenes']['review']['layout']['children'][0])
        speaker.pop('motion', None); speaker['instance_id'] = 'main'
        speaker['layout'] = {'position': 'absolute', 'left': 0, 'top': 0, 'width': 360, 'height': 640}
        speaker['bindings']['clip'] = {'asset_id': 'camera', 'source': {'start': offsets[0], 'end': offsets[0]+5}}
        inset = deepcopy(speaker); inset['instance_id'] = 'inset'
        inset['layout'] = {'position': 'absolute', 'right': 16, 'top': 16, 'width': 100, 'height': 100}
        inset['bindings']['clip'] = {'asset_id': 'other', 'source': {'start': offsets[1], 'end': offsets[1]+5}}
        spec['spatial']['scenes']['review']['layout']['children'] = [speaker, inset]
    else:
        spec.pop('spatial')
    if cuts:
        spec['composition'] = []
        tree = deepcopy(spec['spatial']['scenes']['review']) if spatial else None
        if spatial: spec['spatial']['scenes'] = {}
        for index, (identity, offset, duration) in enumerate([('review', 0, 1), ('middle', 1, 2), ('return', 3, 2)]):
            part = deepcopy(scene); part.update(id=identity, name=identity)
            if index == 1: part['slots'].reverse()
            children = []
            for i, slot in enumerate(part['slots']):
                role = 'main' if i == 0 else 'inset'; slot['slot'] = role
                origin = offsets[0 if slot['source'] == 'camera' else 1]+offset
                slot.update(id=f'{identity}_{role}', source_from=format_timecode(origin)['text'], source_to=format_timecode(origin+duration)['text'])
                if spatial:
                    child = deepcopy(tree['layout']['children'][i]); child['bindings']['clip'] = {
                        'asset_id': slot['source'], 'source': {'start': origin, 'end': origin+duration}}
                    children.append(child)
            part['audio_from'] = part['slots'][0]['source']; spec['composition'].append(part)
            if spatial:
                design = deepcopy(tree); design['layout']['children'] = children
                spec['spatial']['scenes'][identity] = design
    save_spec(spec, cwd=root)
    return spec, load_project(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--spatial', action='store_true')
    parser.add_argument('--cuts', action='store_true')
    parser.add_argument('--silent-listener', action='store_true')
    args = parser.parse_args()
    prepare_pip(args.out.resolve(), spatial=args.spatial, cuts=args.cuts, silent_listener=args.silent_listener)
