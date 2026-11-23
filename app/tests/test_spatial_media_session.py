"""Job-owned decoders retain the reference renderer's exact frame selection."""
from pathlib import Path
import subprocess

import av
import pytest

from moviestar.spatial_media import MediaSession, SpatialMediaError
from moviestar.spatial_render import _decode
from tests.test_spatial_render import project


def rgb(path):
    with av.open(str(path)) as container:
        return next(container.decode(video=0)).to_ndarray(format='rgb24').tobytes()


def references(video, times, directory):
    results = []
    for index, at in enumerate(times):
        path = directory / f'reference-{index}.png'
        _decode(video, path, source_time=at)
        results.append(rgb(path))
    return results


def test_forward_decode_reuses_source_and_matches_reference(project, tmp_path, monkeypatch):
    video = Path(project[0]['sources'][0]['path'])
    times = [.05, .15, .15, .25, .35, .45, .55]
    expected = references(video, times, tmp_path)
    real_open = av.open; opened = []
    def opening(path, *args, **kwargs):
        if str(path) == str(video):
            opened.append(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(av, 'open', opening)
    with MediaSession() as session:
        for index, at in enumerate(times):
            out = tmp_path / f'actual-{index}.png'
            session.decode(video, out, source_time=at, occurrence=('speaker',))
            assert rgb(out) == expected[index]
    assert len(opened) == 1, 'Adjacent frames must not reopen the recording'


@pytest.mark.parametrize('offset', [0, 5])
def test_fractional_b_frames_and_backward_requests_match_reference(project, tmp_path, offset):
    video = tmp_path / 'fractional.mp4'
    frames = b''.join(bytes((20 + 8 * n, 40, 60)) * (160 * 90) for n in range(20))
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb24',
        '-video_size', '160x90', '-framerate', '30000/1001', '-i', 'pipe:0',
        '-c:v', 'libx264rgb', '-crf', '12', '-bf', '2', '-g', '20',
        '-output_ts_offset', str(offset), str(video)], input=frames, check=True)
    times = [.05, .1, .3, .65, .1, .5, .65]
    expected = references(video, times, tmp_path)
    with MediaSession() as session:
        for i, at in enumerate(times):
            out = tmp_path / f'actual-{i}.png'
            session.decode(video, out, source_time=at)
            assert rgb(out) == expected[i]


def test_independent_occurrences_and_vfr_holds_do_not_share_clock(project, tmp_path):
    source = Path(project[0]['sources'][0]['path'])
    video = tmp_path / 'vfr.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source), '-vf',
        'setpts=PTS+gte(N\\,10)/TB', '-fps_mode', 'vfr', '-c:v', 'ffv1',
        '-pix_fmt', 'bgr0', str(video)], check=True)
    times = [.05, 1.5, .15, 1.75, .25, 2.1]
    expected = references(video, times, tmp_path)
    with MediaSession(max_decoders=2) as session:
        for i, at in enumerate(times):
            out = tmp_path / f'actual-{i}.png'
            session.decode(video, out, source_time=at, occurrence=(str(i % 2),))
            assert rgb(out) == expected[i]


def test_source_end_and_changed_source_fail_without_stale_output(project, tmp_path):
    video = Path(project[0]['sources'][0]['path'])
    out = tmp_path / 'actual.png'
    with MediaSession() as session:
        session.decode(video, out, source_time=1.95)
        with pytest.raises(SpatialMediaError, match='no source frame'):
            session.decode(video, tmp_path / 'past-end.png', source_time=2.1)
        assert not (tmp_path / 'past-end.png').exists()
        video.touch()
        with pytest.raises(SpatialMediaError, match='changed'):
            session.decode(video, tmp_path / 'changed.png', source_time=1.95)
        assert not (tmp_path / 'changed.png').exists()


@pytest.mark.parametrize('space,range_', [('bt709', 'tv'), ('smpte170m', 'tv'), ('bt709', 'pc')])
@pytest.mark.parametrize('sar', ['1/1', '4/3'])
@pytest.mark.parametrize('pixel_format', ['yuv420p', 'yuv420p10le', 'yuv444p'])
def test_yuv_color_and_sample_aspect_match_ffmpeg(tmp_path, space, range_, sar, pixel_format):
    video = tmp_path / 'tagged.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=s=160x90:r=24:d=1', '-vf', f'setsar={sar}', '-c:v', 'libx264',
        '-pix_fmt', pixel_format, '-colorspace', space, '-color_range', range_, str(video)], check=True)
    expected = references(video, [.125, .25, .5], tmp_path)
    with MediaSession() as session:
        for i, at in enumerate([.125, .25, .5]):
            out = tmp_path / f'actual-{i}.png'
            session.decode(video, out, source_time=at)
            assert rgb(out) == expected[i]


def test_mpegts_seek_recovery_retains_exact_frame(project, tmp_path):
    video = tmp_path / 'reordered.ts'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=s=160x90:r=10:d=2', '-c:v', 'libx264', '-g', '10', '-bf', '2',
        str(video)], check=True)
    times = [.05, .5, 1.5, 1.95, .5]
    expected = references(video, times, tmp_path)
    with MediaSession() as session:
        for i, at in enumerate(times):
            out = tmp_path / f'actual-{i}.png'
            session.decode(video, out, source_time=at)
            assert rgb(out) == expected[i]


@pytest.mark.parametrize('rotation', [90, 180])
def test_display_matrix_keeps_reference_autorotation(tmp_path, rotation):
    source, video = tmp_path / 'source.mp4', tmp_path / 'rotated.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=s=160x90:r=24:d=1', '-c:v', 'libx264', str(source)], check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source), '-c', 'copy',
        '-metadata:s:v:0', f'rotate={rotation}', str(video)], check=True)
    expected = references(video, [.125, .5], tmp_path)
    with MediaSession() as session:
        for i, at in enumerate([.125, .5]):
            out = tmp_path / f'actual-{i}.png'
            session.decode(video, out, source_time=at)
            assert rgb(out) == expected[i]


@pytest.mark.parametrize('failure', [RuntimeError, KeyboardInterrupt])
def test_decoder_limit_and_cleanup_on_error_or_cancel(project, tmp_path, monkeypatch, failure):
    video = Path(project[0]['sources'][0]['path'])
    real_open = av.open
    handles = []
    class Handle:
        def __init__(self, container):
            self.container, self.closed = container, False
        def __getattr__(self, name):
            return getattr(self.container, name)
        def close(self):
            self.closed = True
            self.container.close()
    def opening(path, *args, **kwargs):
        container = real_open(path, *args, **kwargs)
        if str(path) != str(video):
            return container
        handle = Handle(container); handles.append(handle)
        return handle
    monkeypatch.setattr(av, 'open', opening)
    with pytest.raises(failure):
        with MediaSession(max_decoders=2) as session:
            for i in range(5):
                session.decode(video, tmp_path / f'{i}.png', source_time=.15, occurrence=(str(i),))
                assert sum(not h.closed for h in handles) <= 2
            raise failure()
    assert len(handles) == 5 and all(h.closed for h in handles)
    with pytest.raises(SpatialMediaError, match='closed'):
        session.decode(video, tmp_path / 'late.png', source_time=.15)


def test_static_images_and_metadata_are_prepared_once_per_job(project, tmp_path, monkeypatch):
    import moviestar.spatial_render as render
    video = Path(project[0]['sources'][0]['path']);image = tmp_path / 'logo.png'
    real_run = render._run; commands = []
    def run(command):
        commands.append(command)
        return real_run(command)
    monkeypatch.setattr(render, '_run', run)
    with MediaSession() as session:
        for i in range(3):
            session.probe(video)
            session.decode(image, tmp_path / f'logo-{i}.png')
        assert rgb(tmp_path / 'logo-0.png') == rgb(tmp_path / 'logo-2.png')
    assert sum(c[0] == 'ffprobe' for c in commands) == 1
    assert sum(c[0] == 'ffmpeg' for c in commands) == 1


@pytest.mark.parametrize('canvas,opacity,moving', [
    ({'width': 320, 'height': 180}, 1, False),
    ({'width': 321, 'height': 181}, .55, False),
    ({'width': 180, 'height': 320}, .65, False),
    ({'width': 320, 'height': 180}, .8, True),
])
def test_session_frames_match_full_reference_composition(project, tmp_path, canvas, opacity, moving):
    from moviestar.spatial_layout import browser_available
    from moviestar.spatial_render import render_spatial_frame
    from moviestar.spatial_session import BrowserSession
    from tests.test_spatial_render import _frame
    if not browser_available():
        pytest.skip('Chrome and Node are required')
    spec, metadata = project
    speaker = spec['spatial']['scenes']['interview']['layout']['children'][0]
    speaker['style'] = {'opacity': opacity}
    if moving:
        speaker['motion'] = {
            'translate_x': [{'at': 0, 'value': 0}, {'at': 1, 'value': 5}],
            'scale': [{'at': 0, 'value': 1}, {'at': 1, 'value': 1.1}]}
    with MediaSession() as media, BrowserSession() as browser:
        for index, at in enumerate([.05, .15, .55, .25]):
            frame = _frame(spec, metadata, at)
            before, after = tmp_path / f'old-{index}.png', tmp_path / f'new-{index}.png'
            expected = render_spatial_frame(spec, frame, canvas, output_path=before,
                project_root=tmp_path, browser_session=browser)
            actual = render_spatial_frame(spec, frame, canvas, output_path=after,
                project_root=tmp_path, browser_session=browser, media_session=media)
            assert rgb(before) == rgb(after)
            assert actual['media'] == expected['media']
            assert actual['boxes'] == expected['boxes']


def test_long_forward_gap_and_rewind_select_exact_frames(tmp_path):
    video = tmp_path / 'long.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=s=160x90:r=24:d=6', '-c:v', 'libx264', '-g', '48', '-bf', '2', str(video)], check=True)
    times = [.05, .15, 4.5, 4.6, .25, 5.95]
    expected = references(video, times, tmp_path)
    with MediaSession() as session:
        for i, at in enumerate(times):
            out = tmp_path / f'actual-{i}.png'
            session.decode(video, out, source_time=at)
            assert rgb(out) == expected[i]


def test_static_image_cache_evicts_within_job_budget(project, tmp_path, monkeypatch):
    import moviestar.spatial_render as render
    image = tmp_path / 'logo.png'
    other = tmp_path / 'other.png'
    other.write_bytes(image.read_bytes())
    prepared = tmp_path / 'sized.png'
    _decode(image, prepared)
    original = render._decode
    calls = []
    def decode(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(render, '_decode', decode)
    with MediaSession(image_cache_bytes=prepared.stat().st_size) as session:
        directory = Path(session._temporary.name)
        for source in [image, other, image]:
            session.decode(source, tmp_path / 'output.png')
            assert len(list(directory.iterdir())) == 1
            assert session._image_bytes <= session.image_budget
    assert len(calls) == 3
    assert not directory.exists()
