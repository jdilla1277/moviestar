"""Reuse browser paint only when the complete visual request is unchanged."""
from copy import deepcopy
from pathlib import Path

import pytest

from moviestar.spatial_paint import PaintCache
from tests.test_spatial_render import project


def request(directory, **extra):
    directory.mkdir(exist_ok=True)
    return {'canvas': {'width': 320, 'height': 180}, 'scene_id': 'scene',
            'root_style': {}, 'nodes': [{'kind': 'video', 'path': ['scene', 'clip'],
            'children': [], 'video': {'id': 'video0', 'width': 160, 'height': 90}}],
            'scene_time_s': .1, 'result_time_s': .1, 'paint_dir': str(directory), **extra}


def seed(cache, req):
    root = Path(req['paint_dir'])
    (root/'base.png').write_bytes(b'base')
    (root/'video0-white.png').write_bytes(b'mask')
    result = {'video_planes': [{'id': 'video0'}], 'diagnostics': [], 'boxes': []}
    cache.store(req, result)
    return result


def test_reuse_ignores_only_output_directory_and_unused_root_clocks(tmp_path):
    cache = PaintCache()
    first = request(tmp_path/'first'); seed(cache, first)
    other = request(tmp_path/'second', scene_time_s=2, result_time_s=4)
    actual = cache.restore(other)
    assert actual == {'video_planes': [{'id': 'video0'}], 'diagnostics': [], 'boxes': []}
    assert (tmp_path/'second/base.png').read_bytes() == b'base'
    assert (tmp_path/'second/video0-white.png').read_bytes() == b'mask'
    actual['diagnostics'].append({'severity': 'error'})
    assert cache.restore(other)['diagnostics'] == []
    cache.clear()
    assert cache.restore(other) is None


@pytest.mark.parametrize('change', ['canvas', 'scene', 'style', 'crop', 'content', 'image', 'caption', 'regions', 'unknown'])
def test_visual_changes_invalidate_paint(tmp_path, change):
    cache = PaintCache(); first = request(tmp_path/'first'); seed(cache, first)
    import numpy as np
    cache.pixels(Path(first['paint_dir'])/'base.png', (),
                 lambda: [np.zeros((2, 3), dtype=np.uint16)] * 3)
    assert cache.decoded_bytes
    other = deepcopy(first); other['paint_dir'] = str(tmp_path/'second')
    if change == 'canvas': other['canvas']['width'] = 321
    elif change == 'scene': other['scene_id'] = 'new'
    elif change == 'style': other['root_style']['font-family'] = 'monospace'
    elif change == 'crop': other['nodes'][0]['style'] = {'object-position': 'left top'}
    elif change == 'content': other['nodes'][0]['content'] = 'new'
    elif change == 'image': other['nodes'][0]['image'] = 'data:image/png;base64,new'
    elif change == 'caption': other['nodes'][0]['caption'] = {'text': 'next', 'cues': [{'active_word': 1}]}
    elif change == 'regions': other['reserved_regions'] = [{'id': 'new'}]
    elif change == 'unknown': other['future_timing'] = .2
    assert cache.restore(other) is None
    Path(other['paint_dir']).mkdir()
    seed(cache, other)  # A completed fresh browser capture replaces both forms.
    assert cache.decoded_bytes == 0


@pytest.mark.parametrize('special', ['motion', 'media_motion', 'raster_image', 'reference_image', 'reference_path', 'layout_output_path', 'output_path', 'live_dom'])
def test_dynamic_and_auxiliary_views_bypass_reuse(tmp_path, special):
    cache = PaintCache(); req = request(tmp_path/'first')
    if special in ('motion', 'media_motion'): req['nodes'][0][special] = {'scale': 1}
    elif special.endswith('_image'): req['nodes'][0]['video'][special] = 'data:image/png;base64,x'
    else: req[special] = True
    seed(cache, req)
    assert cache.restore(req) is None


def test_budget_and_single_entry_replacement(tmp_path):
    cache = PaintCache(max_bytes=1)
    req = request(tmp_path/'first'); seed(cache, req)
    assert cache.restore(req) is None
    cache = PaintCache()
    seed(cache, req)
    different = request(tmp_path/'second', scene_id='different'); seed(cache, different)
    assert cache.restore(req) is None
    assert cache.restore(different) is not None
    assert cache.retained_bytes <= cache.max_bytes


def test_errors_and_oversized_entries_drop_prior_paint(tmp_path):
    cache = PaintCache(); req = request(tmp_path/'first'); seed(cache, req)
    result = {'video_planes': [], 'diagnostics': [{'severity': 'error'}]}
    cache.store(req, result)
    assert cache.restore(req) is None


def test_css_declaration_order_is_part_of_identity(tmp_path):
    cache = PaintCache(); first = request(tmp_path/'first')
    first['root_style'] = {'padding': '0px', 'padding-left': '20px'}
    seed(cache, first)
    other = deepcopy(first)
    other['root_style'] = {'padding-left': '20px', 'padding': '0px'}
    assert cache.restore(other) is None


@pytest.mark.parametrize('canvas', [{'width': 320, 'height': 180}, {'width': 180, 'height': 320}])
def test_real_frames_reuse_paint_but_keep_fresh_video(project, tmp_path, canvas):
    import av
    from moviestar.spatial_layout import browser_available
    from moviestar.spatial_render import render_spatial_frame
    from moviestar.spatial_session import BrowserSession
    from tests.test_spatial_render import _frame
    if not browser_available():
        pytest.skip('Chrome and Node required')
    spec, metadata = project
    def rgb(path):
        with av.open(str(path)) as source:
            return next(source.decode(video=0)).to_ndarray(format='rgb24').tobytes()
    pixels = []
    from moviestar import spatial_composite
    original = spatial_composite._read_png
    reads = []
    def read(path):
        reads.append(path.name)
        return original(path)
    # Count cached rendering separately from the independent reference session.
    from unittest.mock import patch
    with BrowserSession() as session, BrowserSession() as reference:
        reference._paint_cache.max_bytes = 0
        for index, at in enumerate([.05, .15, .25]):
            frame = _frame(spec, metadata, at)
            before, after = tmp_path/f'old-{index}.png', tmp_path/f'new-{index}.png'
            old = render_spatial_frame(spec, frame, canvas, project_root=tmp_path,
                output_path=before, browser_session=reference)
            reads.clear()
            with patch.object(spatial_composite, '_read_png', read):
                new = render_spatial_frame(spec, frame, canvas, project_root=tmp_path,
                    output_path=after, browser_session=session)
            paint_reads = [name for name in reads if name == 'base.png' or name.endswith('-white.png')]
            assert len(paint_reads) == (1 + len(new['media']) if index == 0 else 0)
            assert rgb(before) == rgb(after)
            pixels.append(rgb(after))
            assert new['boxes'] == old['boxes'] and new['media'] == old['media']
        assert session._counter == 1
        assert reference._counter == 3
        assert len(set(pixels)) == 3, 'Reuse must never freeze source frames'
    assert session._paint_cache.retained_bytes == 0
    assert session._paint_cache.decoded_bytes == 0


def test_active_word_changes_repaint_and_keep_current_reports(tmp_path):
    from moviestar.spatial_layout import browser_available
    from moviestar.spatial_session import BrowserSession
    if not browser_available():
        pytest.skip('Chrome and Node required')
    with BrowserSession() as session, BrowserSession() as reference:
        reference._paint_cache.max_bytes = 0
        for index, active in enumerate([0, 0, 1]):
            folder = tmp_path/f'cached-{index}';folder.mkdir()
            req = {'scene_id': 'caption', 'canvas': {'width': 320, 'height': 180},
                'root_style': {'background-color': '#000000'}, 'scene_layout': {},
                'scene_time_s': index/24, 'paint_dir': str(folder),
                'nodes': [{'kind': 'caption', 'path': ['caption','words'], 'content': '',
                    'style': {'font-size': '24px', 'color': '#ffffff'}, 'children': [],
                    'caption': {'track': 'test', 'text': 'two words', 'cues': [{
                        'id': 'cue', 'text': 'two words', 'highlight_mode': 'spoken-word',
                        'word_spans': [{'start': 0, 'end': 3}, {'start': 4, 'end': 9}],
                        'active_word': active, 'highlight_color': '#ff0000'}]}}]}
            actual = session.run(req)
            before = tmp_path/f'uncached-{index}';before.mkdir()
            expected = reference.run({**req, 'paint_dir': str(before)})
            assert actual == expected
            assert (folder/'base.png').read_bytes() == (before/'base.png').read_bytes()
            assert [w['active'] for w in actual['boxes'][0]['caption_words']] == [active==0,active==1]
        assert session._counter == 2 and reference._counter == 3
