"""Synthetic duration/scene-count fixtures, not representative camera footage."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from fixtures import generate
from moviestar.project import load_project
from moviestar.spec import save_spec
from moviestar.timecodes import format_timecode


def long_source(folder):
    """Repeat the 8s video barcode; generate continuous, clocked 90min audio.

    Stream-copy video rather than writing 162,000 raw frames. The barcode is
    intentionally modulo 240 frames; it is not an absolute recording-time ID.
    Independent long audio avoids accumulating AAC priming at every repeat.
    """
    seed = folder / 'seed'
    generate(seed)
    video = folder / 'video-only.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(seed / 'cues.mp4'),
        '-map', '0:v', '-c:v', 'copy', '-an', str(video)], check=True, timeout=30)
    source = folder / 'ninety-minutes.mp4'
    expression = 'aevalsrc=if(between(mod(t\\,1)\\,0.2\\,0.2666667)\\,0.25*sin(2*PI*880*t)\\,0):s=48000:d=5400'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-stream_loop', '-1', '-i', str(video),
        '-f', 'lavfi', '-i', expression, '-map', '0:v', '-map', '1:a', '-t', '5400',
        '-c:v', 'copy', '-c:a', 'aac', '-movflags', '+faststart', str(source)],
        check=True, timeout=180)
    return source


def prepare_window(root, portrait, source, source_offset):
    """121 scenes / 3600s, selecting result 1801–1806 inside scene 61.

    Scene time begins at result 1800; its visible source begins at a selectable
    recording offset. Other scenes reuse the same component definitions with
    their own arrangement copies and ordinary thirty-second media bindings.
    """
    spec, _ = generate(root, portrait)
    source = Path(source).resolve()
    loaded = subprocess.run([sys.executable, '-m', 'moviestar.cli', 'load', str(source),
        '--as', 'camera', '--force', '--no-transcribe', '--no-frames'],
        cwd=root, capture_output=True, text=True, timeout=30)
    if loaded.returncode:
        raise RuntimeError('Could not ingest long source: ' + loaded.stdout + loaded.stderr)
    spec['sources'][0]['path'] = str(source)
    selected = spec['composition'][0]
    selected['slots'][0].update(source_from=format_timecode(source_offset)['text'],
                              source_to=format_timecode(source_offset + 8)['text'])
    speaker = spec['spatial']['scenes']['review']['layout']['children'][0]
    speaker['bindings']['clip']['source'] = {'start': source_offset, 'end': source_offset + 8}
    before, after = [], []
    for index in range(120):
        scene = deepcopy(selected)
        scene.update(id=f'other_{index}', name=f'Other scene {index + 1}')
        scene['slots'][0].update(id=f'other_slot_{index}', source_from='0:00:00.000',
            source_to=format_timecode(22 if index == 119 else 30)['text'])
        tree = deepcopy(spec['spatial']['scenes']['review'])
        tree['layout']['children'][0]['bindings']['clip']['source'] = {
            'start': 0, 'end': 22 if index == 119 else 30}
        spec['spatial']['scenes'][scene['id']] = tree
        (before if index < 60 else after).append(scene)
    spec['composition'] = before + [selected] + after
    for cue in spec['overlays']:
        # Keep the reference caption change two seconds into the review,
        # rather than two seconds into the selected scene.
        offset = 1801 + (0 if cue['id'] == 'first' else 2)
        cue.update({'from': format_timecode(offset)['text'],
                    'to': format_timecode(1803 if cue['id'] == 'first' else 1806)['text']})
    save_spec(spec, cwd=root)
    return spec, load_project(root)
