"""Create an editable two-scene interview through the public CLI, without flattening listeners."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from fixtures import generate
from moviestar.timecodes import format_timecode


def interview_document(offsets=(1, 2, 3)):
    video = {'id': 'video', 'type': 'video', 'content': {'input': 'clip'},
        'layout': {'position': 'absolute', 'left': 0, 'top': 0, 'width': '100%', 'height': '100%'},
        'style': {'object_fit': 'cover', 'border_width': 2, 'border_style': 'solid', 'border_color': '#ffb81c', 'border_radius': '2.222222vmin'}}
    label = {'id': 'name', 'type': 'text', 'content': {'input': 'name'},
        'layout': {'position': 'absolute', 'left': '2.222222vmin', 'bottom': '2.222222vmin', 'right': '2.222222vmin', 'height': '6.666667vmin'},
        'style': {'font_family': 'sans-serif', 'font_size': '4.444444vmin', 'color': '#ffffff', 'background_color': '#102030'}}
    definitions = {'speaker_card': {'name': 'SpeakerCard', 'inputs': [
        {'name': 'clip', 'type': 'video', 'required': True}, {'name': 'name', 'type': 'text', 'required': True}],
        'children': [video, label]},
        'listener_pair': {'name': 'ListenerPair', 'inputs': [
            {'name': name, 'type': kind, 'required': True}
            for name, kind in [('left', 'video'), ('right', 'video'), ('left_name', 'text'), ('right_name', 'text')]],
            'children': [{'instance_id': side, 'definition_id': 'speaker_card',
                'bindings': {'clip': {'forward': side}, 'name': {'forward': side + '_name'}},
                'layout': {'position': 'relative', 'grid_column': '1', 'grid_row': str(index + 1),
                    'min_width': 0, 'min_height': 0},
                'format_rules': [{'id': 'portrait-pair', 'when': {'orientation': 'portrait'},
                    'layout': {'grid_column': str(index + 1), 'grid_row': '1'}}]}
                for index, side in enumerate(('left', 'right'))]}}
    scenes = {}
    def binding(identity, start):
        return {'asset_id': identity, 'source': {'start': start, 'end': start + 3}}
    for identity, speaker, listeners, starts in [
        ('intro', 'alice', ('bob', 'casey'), offsets),
        ('reply', 'bob', ('alice', 'casey'), (offsets[1], offsets[0] + 2, offsets[2] + 1)),
    ]:
        children = [
            {'id': 'heading', 'type': 'text', 'content': {'text': speaker.title() + ' leads'},
             'layout': {'grid_column': '1 / -1', 'grid_row': '1'},
             'style': {'font_family': 'sans-serif', 'font_size': '5.555556vmin', 'color': '#ffffff'}},
            {'instance_id': 'speaker', 'definition_id': 'speaker_card',
             'bindings': {'clip': binding(speaker, starts[0]), 'name': {'text': speaker.title()}},
             'layout': {'position': 'relative', 'grid_column': '1', 'grid_row': '2', 'min_width': 0, 'min_height': 0}},
            {'instance_id': 'listeners', 'definition_id': 'listener_pair',
             'bindings': {'left': binding(listeners[0], starts[1]), 'right': binding(listeners[1], starts[2]),
                'left_name': {'text': listeners[0].title()}, 'right_name': {'text': listeners[1].title()}},
             'layout': {'position': 'relative', 'display': 'grid', 'columns': '1fr', 'rows': '1fr 1fr',
                'gap': '2.777778vmin', 'grid_column': '2', 'grid_row': '2', 'min_width': 0, 'min_height': 0},
             'format_rules': [{'id': 'portrait-listeners', 'when': {'orientation': 'portrait'},
                'layout': {'grid_column': '1', 'grid_row': '3', 'columns': '1fr 1fr', 'rows': '1fr'}}]},
            {'id': 'captions', 'type': 'caption', 'content': {'track': 'captions'},
             'layout': {'grid_column': '1 / -1', 'grid_row': '3'},
             'style': {'font_family': 'sans-serif', 'font_size': '4.444444vmin', 'line_height': 1.2, 'color': '#ffffff'},
             'format_rules': [{'id': 'portrait-captions', 'when': {'orientation': 'portrait'},
                'layout': {'grid_row': '4'}}]},
        ]
        scenes[identity] = {'layout': {'display': 'grid', 'columns': '2fr 1fr', 'rows': '10vmin 1fr 20vmin',
            'padding': '3.333333vmin', 'gap': '2.777778vmin', 'background_color': '#102030', 'children': children,
            'format_rules': [{'id': 'portrait-stack', 'when': {'orientation': 'portrait'},
                'layout': {'columns': '1fr', 'rows': '10vmin 2fr 1fr 20vmin'}}]}}
    return {'definitions': definitions, 'scenes': scenes}


def prepare_interview(root, *, canvas='360x640', sources=None, offsets=(1, 2, 3)):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=False)
    media_kind = 'referenced' if sources else 'generated'
    if sources is None:
        media = root / 'media'; media.mkdir()
        with TemporaryDirectory(prefix='interview-seed-', dir=root) as temporary:
            seed = Path(temporary) / 'seed'; generate(seed, pattern=True)
            for name, hue, volume in [('alice', 0, 1), ('bob', 120, .5), ('casey', 240, .25)]:
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(seed / 'cues.mp4'),
                    '-vf', f'hue=h={hue}', '-af', f'volume={volume},pan=stereo|c0=c0|c1=c0',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart',
                    str(media / f'{name}.mp4')], check=True, timeout=30)
        sources = [media / f'{name}.mp4' for name in ('alice', 'bob', 'casey')]
    sources = [str(Path(p).resolve()) for p in sources]
    env = os.environ.copy()
    if env.get('PYTHONPATH'):
        env['PYTHONPATH'] = os.pathsep.join(str(Path(p).resolve()) for p in env['PYTHONPATH'].split(os.pathsep) if p)
    setup = root / 'setup'; setup.mkdir()
    def command(label, args):
        result = subprocess.run([sys.executable, '-m', 'moviestar.cli', *args], cwd=root,
            env=env, capture_output=True, text=True, timeout=45)
        (setup / f'{label}.json').write_text(result.stdout)
        (setup / f'{label}.stderr.log').write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(f'{label} failed: {result.stdout}\n{result.stderr}')
        return json.loads(result.stdout)
    command('load', ['load', *sources, '--as', 'alice', '--as', 'bob', '--as', 'casey', '--force', '--no-transcribe', '--no-frames'])
    for size in [canvas, *[c for c in ('360x640', '640x360', '1080x1920', '1920x1080') if c != canvas]]:
        scenes = {'canvas': size, 'scenes': [{'id': identity, 'name': name.title() + ' leads', 'layout': 'single',
            'audio_from': name, 'slots': [{'slot': 'main', 'source': name,
                'from': format_timecode(start)['text'], 'to': format_timecode(start + 3)['text']}]}
            for identity, name, start in [('intro', 'alice', offsets[0]), ('reply', 'bob', offsets[1])]]}
        (root / f'scenes-{size}.json').write_text(json.dumps(scenes, indent=2) + '\n')
    command('scenes', ['scenes', 'set', str(root / f'scenes-{canvas}.json')])
    (root / 'interview.json').write_text(json.dumps(interview_document(offsets), indent=2) + '\n')
    command('model', ['composition', 'model', 'apply', str(root / 'interview.json')])
    (root / 'captions.srt').write_text('1\n00:00:00,000 --> 00:00:01,500\nAlice opens the discussion.\n\n2\n00:00:01,500 --> 00:00:03,000\nOne shared arrangement,\nthree editable pictures.\n\n3\n00:00:03,000 --> 00:00:04,500\nBob takes the lead.\n\n4\n00:00:04,500 --> 00:00:06,000\nThe listeners keep their places.\n')
    command('captions', ['captions', 'import', str(root / 'captions.srt'), '--track', 'captions', '--style', 'caption-default'])
    edits = {
        'local': {'op': 'instance.customize', 'target': {'occurrence_path': ['intro', 'listeners', 'left', 'video']},
            'property': 'style.border_color', 'value': '#00d4ff'},
        'shared': {'op': 'component.update', 'target': {'definition_id': 'speaker_card', 'element_id': 'video'},
            'property': 'style.border_color', 'value': '#c084fc'},
        'reset': {'op': 'instance.reset', 'target': {'occurrence_path': ['intro', 'listeners', 'left', 'video']},
            'property': 'style.border_color'},
    }
    for name, edit in edits.items():
        (root / f'edit-{name}.json').write_text(json.dumps(edit, indent=2) + '\n')
    result = {'project': str(root), 'media_kind': media_kind, 'canvas': canvas, 'range': {'from': 0, 'to': 6},
        'commands': {'native': ['preview', '--workspace', str(root), '--mode', 'native', '--from', '0', '--to', '3'],
                     'rendered': ['preview', '--workspace', str(root), '--mode', 'rendered', '--from', '0', '--to', '6'],
                     'export': ['export', '--out', str(root / 'interview.mp4')]}}
    (root / 'journey.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--canvas', choices=['360x640', '640x360', '1080x1920', '1920x1080'], default='360x640')
    parser.add_argument('--sources', nargs=3, type=Path)
    parser.add_argument('--offsets', nargs=3, type=float, default=(1, 2, 3))
    args = parser.parse_args()
    print(json.dumps(prepare_interview(args.out, canvas=args.canvas, sources=args.sources, offsets=args.offsets)))
