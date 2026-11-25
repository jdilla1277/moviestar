"""Compare the installed compositor with the frozen pre-optimization oracle."""

from fractions import Fraction
from pathlib import Path
import runpy

import av
import numpy as np
import pytest

from moviestar import spatial_render


REFERENCE = runpy.run_path(str(Path(__file__).parents[2] / 'experiments' /
                              'spatial_preview_performance' / 'compositors.py'))['ffmpeg_reference']


def _write_png(path, pixels):
    frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
    encoder = av.CodecContext.create('png', 'w')
    encoder.thread_count = 1
    encoder.width, encoder.height = frame.width, frame.height
    encoder.pix_fmt, encoder.time_base = 'rgb24', Fraction(1, 25)
    frame.pts, frame.time_base = 0, Fraction(1, 25)
    path.write_bytes(b''.join(bytes(packet) for packet in encoder.encode(frame)))


def _rgb(path):
    with av.open(str(path)) as container:
        return next(container.decode(video=0)).to_ndarray(format='rgb24')


@pytest.fixture
def composition(tmp_path):
    def prepare(size, rectangles):
        width, height = size
        rng = np.random.default_rng(20261107)
        _write_png(tmp_path / 'base.png', rng.integers(0, 256, (height, width, 3), dtype=np.uint8))
        planes, videos = [], {}
        for index, (x, y, w, h) in enumerate(rectangles):
            identity = f'video{index}'
            _write_png(tmp_path / f'{identity}-white.png', rng.integers(0, 256, (height, width, 3), dtype=np.uint8))
            source = tmp_path / f'{identity}-source.png'
            _write_png(source, rng.integers(0, 256, (17, 23, 3), dtype=np.uint8))
            planes.append({'id': identity, 'path': ['scene', identity],
                           'rect': {'x': x, 'y': y, 'width': w, 'height': h}})
            videos[identity] = {'decoded': source}
        return {'width': width, 'height': height}, planes, videos
    return prepare


@pytest.mark.parametrize('size,rectangles', [
    ((37, 29), []),
    ((37, 29), [(3, 2, 27, 21)]),
    ((29, 73), [(0, 0, 29, 73)]),
    ((65, 129), [(-11, -5, 47, 83)]),
    ((37, 29), [(22, 16, 41, 33)]),
    ((37, 29), [(50, 50, 23, 17)]),
    ((37, 29), [(-36, -28, 37, 29)]),
    ((37, 29), [(36, 28, 37, 29)]),
    ((37, 29), [(4, 7, 1, 1)]),
    ((37, 29), [(0, -40, 8, 10)]),
    ((37, 29), [(0, 0, 37, 29), (5, 3, 30, 24), (-4, 7, 37, 31)]),
])
def test_composition_matches_ffmpeg_without_starting_processes(tmp_path, composition, monkeypatch, size, rectangles):
    canvas, planes, videos = composition(size, rectangles)
    expected, actual = tmp_path / 'expected.png', tmp_path / 'actual.png'
    REFERENCE(tmp_path, canvas, planes, videos, expected)
    def unexpected(*args, **kwargs):
        raise AssertionError('Composition must not launch FFmpeg')
    monkeypatch.setattr(spatial_render, '_ffmpeg', unexpected)
    spatial_render._compose(tmp_path, canvas, planes, videos, actual)
    assert np.array_equal(_rgb(actual), _rgb(expected))


def test_invalid_geometry_keeps_the_component_address(tmp_path, composition):
    canvas, planes, videos = composition((37, 29), [(0, 0, 10, 10)])
    planes[0]['rect']['width'] = 0
    with pytest.raises(spatial_render.SpatialRenderError, match='scene/video0: unresolved video geometry'):
        spatial_render._compose(tmp_path, canvas, planes, videos, tmp_path / 'output.png')


def test_small_placement_allocates_only_its_visible_rectangle():
    from moviestar.spatial_composite import _place_video
    source=av.VideoFrame.from_ndarray(np.zeros((9,13,3),dtype=np.uint8),format='rgb24')
    placed,bounds=_place_video(source,{'x':-3,'y':7,'width':20,'height':11},320,180)
    assert bounds==(0,7,17,18)
    assert (placed.width,placed.height)==(17,11)


def test_mask_arithmetic_is_bounded_by_visible_media_area(tmp_path,composition,monkeypatch):
    import moviestar.spatial_composite as compositor
    canvas,planes,videos=composition((37,29),[(3,2,10,7),(-4,25,12,10),(50,50,5,5)])
    original=compositor._accumulate;areas=[]
    def accumulate(accumulators,base,white,media):
        areas.append(sum(channel.size for channel in base))
        return original(accumulators,base,white,media)
    monkeypatch.setattr(compositor,'_accumulate',accumulate)
    spatial_render._compose(tmp_path,canvas,planes,videos,tmp_path/'actual.png')
    assert sum(areas)==3*(10*7+8*4)
