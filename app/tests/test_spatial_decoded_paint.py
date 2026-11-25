"""Decoded paint reuse preserves pixels, invalidation and bounded ownership."""
from pathlib import Path

import numpy as np
import pytest

from moviestar import spatial_composite as compositor
from moviestar.spatial_paint import PaintCache
from moviestar.spatial_session import BrowserSession
from tests.test_spatial_composite import composition, _write_png
from tests.test_spatial_paint_cache import request, seed


def test_static_composition_decodes_paint_once_but_keeps_fresh_video(tmp_path, composition, monkeypatch):
    canvas, planes, videos = composition((37, 29), [(3, 2, 10, 7), (-4, 25, 12, 10)])
    cache = PaintCache()
    cache.store(request(tmp_path), {'video_planes': planes, 'diagnostics': []})
    original = compositor._read_png
    reads = []
    def read(path):
        reads.append(Path(path).name)
        return original(path)
    monkeypatch.setattr(compositor, '_read_png', read)
    actual = []
    for value in (20, 100, 220):
        _write_png(videos['video0']['decoded'], np.full((17, 23, 3), value, dtype=np.uint8))
        expected = compositor.compose(tmp_path, canvas, planes, videos, None).to_ndarray(format='rgb24')
        reads.clear()
        picture = compositor.compose(tmp_path, canvas, planes, videos, None, paint_cache=cache)
        actual.append(picture.to_ndarray(format='rgb24'))
        assert np.array_equal(actual[-1], expected)
        if value == 20:
            assert reads.count('base.png') == reads.count('video0-white.png') == reads.count('video1-white.png') == 1
        else:
            assert not any(name == 'base.png' or name.endswith('-white.png') for name in reads)
        assert reads.count('video0-source.png') == reads.count('video1-source.png') == 1
    assert len({pixels.tobytes() for pixels in actual}) == 3
    # Owned, compact uint16 channels: full base plus only the visible mask areas.
    assert cache.decoded_bytes == 3 * 2 * (37 * 29 + 10 * 7 + 8 * 4)
    assert cache.decoded_bytes <= cache.max_decoded_bytes


def test_decoded_budget_counts_owned_channels_and_does_not_thrash(tmp_path):
    cache = PaintCache(max_decoded_bytes=36)
    seed(cache, request(tmp_path))
    backing = np.zeros((4, 16), dtype=np.uint16)
    view = backing[:2, :3]  # Padding/backing storage must not remain retained.
    calls = []
    def load():
        calls.append(True)
        return [view] * 3
    first = cache.pixels(tmp_path/'base.png', ('base',), load)
    assert cache.decoded_bytes == 36
    assert all(channel.flags.owndata and not channel.flags.writeable for channel in first)
    backing.fill(42)
    assert all(not channel.any() for channel in first)
    assert cache.pixels(tmp_path/'base.png', ('base',), load) is first
    for _ in range(3):
        cache.pixels(tmp_path/'video0-white.png', ('mask',), load)
    assert len(calls) == 4, 'An uncacheable mask must not evict the useful base'
    assert cache.pixels(tmp_path/'base.png', ('base',), load) is first
    assert cache.decoded_bytes == 36
    cache.clear()
    assert cache.decoded_bytes == cache.retained_bytes == 0


@pytest.mark.parametrize('budget', [0, 35])
def test_oversized_decoded_entry_uses_uncached_path(tmp_path, budget):
    cache = PaintCache(max_decoded_bytes=budget)
    seed(cache, request(tmp_path))
    calls = []
    def load():
        calls.append(True)
        return [np.zeros((2, 3), dtype=np.uint16)] * 3
    for _ in range(3):
        cache.pixels(tmp_path/'base.png', (), load)
    assert len(calls) == 3 and cache.decoded_bytes == 0


def test_file_replacement_cannot_replay_cached_pixels(tmp_path):
    cache = PaintCache()
    seed(cache, request(tmp_path))
    path = tmp_path/'base.png'
    def load():
        return [np.full((2, 3), path.read_bytes()[0], dtype=np.uint16)] * 3
    before = cache.pixels(path, (), load)
    path.write_bytes(b'next')  # Same name and size; changed contents.
    after = cache.pixels(path, (), load)
    assert before[0][0, 0] != after[0][0, 0]
    assert cache.decoded_bytes == cache.retained_bytes == 0


def test_decode_failure_drops_all_paint_and_preserves_error(tmp_path):
    cache = PaintCache()
    seed(cache, request(tmp_path))
    cache.pixels(tmp_path/'base.png', (), lambda: [np.zeros((2, 3), dtype=np.uint16)] * 3)
    error = ValueError('invalid mask')
    def fail():
        raise error
    with pytest.raises(ValueError) as caught:
        cache.pixels(tmp_path/'video0-white.png', (), fail)
    assert caught.value is error
    assert cache.decoded_bytes == cache.retained_bytes == 0


def test_close_releases_decoded_paint_and_sessions_do_not_share_it(tmp_path):
    with BrowserSession() as session:
        cache = session._paint_cache
        seed(cache, request(tmp_path))
        cache.pixels(tmp_path/'base.png', (), lambda: [np.zeros((2, 3), dtype=np.uint16)] * 3)
        assert cache.decoded_bytes > 0
        with BrowserSession() as other:
            assert other._paint_cache.decoded_bytes == 0
    assert cache.decoded_bytes == cache.retained_bytes == 0


def test_changed_geometry_and_canvas_cannot_reuse_old_channel_views(tmp_path, composition):
    canvas, planes, videos = composition((37, 29), [(3, 2, 10, 7)])
    cache = PaintCache()
    cache.store(request(tmp_path), {'video_planes': planes, 'diagnostics': []})
    compositor.compose(tmp_path, canvas, planes, videos, None, paint_cache=cache)
    planes[0]['rect']['x'] = -4
    expected = compositor.compose(tmp_path, canvas, planes, videos, None).to_ndarray(format='rgb24')
    actual = compositor.compose(tmp_path, canvas, planes, videos, None, paint_cache=cache)
    assert np.array_equal(actual.to_ndarray(format='rgb24'), expected)
    with pytest.raises(ValueError, match='dimensions do not match'):
        compositor.compose(tmp_path, {'width': 38, 'height': 29}, planes, videos, None, paint_cache=cache)
    assert cache.decoded_bytes == 0


def test_uncacheable_render_request_never_retains_decoded_pixels(tmp_path):
    cache = PaintCache()
    req = request(tmp_path)
    req['nodes'][0]['motion'] = {'scale': 1}
    seed(cache, req)
    for _ in range(3):
        cache.pixels(tmp_path/'base.png', (), lambda: [np.zeros((2, 3), dtype=np.uint16)] * 3)
    assert cache.decoded_bytes == 0
