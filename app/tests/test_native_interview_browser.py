"""The reusable interview stays editable, audible and identical to canonical rendering."""
import json
import os
from pathlib import Path
import subprocess
import sys

import av
import numpy as np
import pytest

from moviestar.project import load_project
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available, measure_spatial_layout
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_render import render_spatial_frame
from moviestar.spec import load_spec
from tests.test_native_edit_benchmark import benchmark


def _image(path):
    with av.open(str(path)) as container:
        return next(container.decode(video=0)).to_ndarray(format='rgb24').astype(int)


def _audio(path):
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=48000)
        return np.concatenate([f.to_ndarray() for src in container.decode(audio=0)
                               for f in resampler.resample(src)], axis=1)


def _assert_pcm(review, reference):
    m = review['metrics']; a = m['prepared_audio']; capture = m['audio_pcm']
    actual = np.asarray(capture['samples']); expected = _audio(reference)
    index = round((capture['start_context_time'] - a['started_context_time'] + a['started_offset']) * 48000)
    stop = round((m['pause_context_time'] - capture['start_context_time']) * 48000)
    errors = []
    for shift in (-1, 0, 1):
        begin = index + shift; left = max(0, -begin)
        right = min(actual.shape[1], expected.shape[1] - begin, stop - 128)
        errors.append((actual[:, left:right] - expected[:, begin + left:begin + right]).ravel())
    error = min(errors, key=lambda e: np.sqrt(np.mean(e * e)))
    assert len(error) > 2.8 * 48000 * 2, 'Compare the whole scene, allowing only the final stop quantum'
    assert abs(error).max() < 1e-5


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
@pytest.mark.parametrize('canvas,scene', [('360x640', 'intro'), ('640x360', 'reply')])
def test_actual_interview_edits_undo_playback_and_export(tmp_path, benchmark, canvas, scene):
    from interview_benchmark import run_case
    folder = tmp_path / 'browser'
    assert run_case(folder, canvas, 0, capture_pcm=True, scene=scene) == 0, (folder / 'driver.log').read_text()
    report = json.loads((folder / 'report.json').read_text())
    assert len(report['edits']) == 20 and len(report['playback']) == 2
    assert report['problems'] == []
    for review in report['playback']:
        _assert_pcm(review, folder / (review['label'] + '-audio-reference.wav'))
    root = folder / 'project'; spec = load_spec(root)
    assert all(spec['spatial']['scenes'][scene]['layout']['children'][2]['definition_id'] == 'listener_pair'
               for scene in ('intro', 'reply'))
    width, height = map(int, canvas.split('x')); size = {'width': width, 'height': height}
    before = Path.cwd()
    try:
        os.chdir(root)
        timeline = resolve_project(load_project(root), spec)
        for shot in report['captures']:
            frame = compile_spatial_frame(spec, timeline, shot['at'] + (0 if scene == 'intro' else 3))
            boxes = measure_spatial_layout(frame, size)['boxes']
            actual_boxes = {tuple(b['path']): b for b in shot['snapshot']['boxes']}
            for box in boxes:
                actual = actual_boxes[tuple(box['path'])]
                for key in ('x', 'y', 'width', 'height'):
                    assert abs(actual['rect'][key] - box['rect'][key]) <= .2
                if box.get('text'):
                    assert actual['text'] == box['text']
            reference = folder / (shot['label'] + '-canonical.png')
            render_spatial_frame(spec, frame, size, project_root=root, output_path=reference)
            actual, expected = _image(folder / (shot['label'] + '.png')), _image(reference)
            assert actual.shape == expected.shape == (height, width, 3)
            for box in boxes:
                if not box.get('media_box'):
                    continue
                rect = box['media_box']['content_rect']
                # Interior picture colors avoid glyph/rounded-border antialiasing.
                for fx, fy in ((.2, .35), (.5, .5), (.8, .65)):
                    x, y = int(rect['x'] + fx * rect['width']), int(rect['y'] + fy * rect['height'])
                    assert abs(actual[y, x] - expected[y, x]).max() <= 8, (shot['label'], box['path'], x, y)
    finally:
        os.chdir(before)
    # Exercise both sides of the real scene cut through the canonical export
    # renderer. The full six-second owner demo uses `export`; this smaller
    # watch window keeps the real pixel/audio regression bounded in CI.
    result = subprocess.run([sys.executable, '-m', 'moviestar.cli', 'watch', '--from', '2.1', '--to', '3.3',
        '--precise', '--out', str(folder / 'interview.mp4')],
        cwd=root, capture_output=True, text=True, timeout=120)
    (folder / 'export.stdout.json').write_text(result.stdout)
    (folder / 'export.stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    with av.open(str(folder / 'interview.mp4')) as container:
        video = container.streams.video[0]
        assert (video.width, video.height) == (width, height)
        assert abs(container.duration / av.time_base - 1.2) <= 1 / 30
        frames = list(container.decode(video=0))
        assert len(frames) == 36
        for index, at in ((0, 2.1), (30, 3.1)):
            before = Path.cwd()
            try:
                os.chdir(root)
                frame = compile_spatial_frame(spec, timeline, at)
                boxes = measure_spatial_layout(frame, size)['boxes']
                reference = folder / f'cut-{index}-canonical.png'
                render_spatial_frame(spec, frame, size, project_root=root, output_path=reference)
            finally:
                os.chdir(before)
            expected = _image(reference)
            actual = frames[index].to_ndarray(format='rgb24').astype(int)
            # H.264 introduces error at edges; compare source interiors again.
            for box in boxes:
                if box.get('media_box'):
                    rect = box['media_box']['content_rect']
                    x, y = int(rect['x'] + .5 * rect['width']), int(rect['y'] + .5 * rect['height'])
                    assert abs(actual[y, x] - expected[y, x]).max() <= 8
    audio = _audio(folder / 'interview.mp4')
    for at, low, high in ((.11, .14, .22), (1.11, .07, .12)):
        samples = audio[:, round(at*48000):round((at+.04)*48000)]
        assert low < np.sqrt(np.mean(samples*samples)) < high, 'Export selects the speaker across the cut'
