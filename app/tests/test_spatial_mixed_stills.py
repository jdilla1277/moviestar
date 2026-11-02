"""Preset transition participants retain their existing rendering semantics."""
from copy import deepcopy
from pathlib import Path

import pytest

from moviestar.resolved import resolve_project
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_layout import browser_available
from moviestar.spatial_render import render_spatial_frame
from tests.test_spatial_render import project, _rgb
from tests.test_spatial_video import _transition_project, _pixels
from tests.test_spatial_inspection import _save, _invoke


@pytest.mark.parametrize('incoming,outgoing', [('legacy','structured'), ('structured','legacy')])
def test_mixed_transition_compiles_both_owning_clocks_without_source_probes(project, monkeypatch, incoming, outgoing):
    import moviestar.cli as c
    spec, metadata = project
    _transition_project(spec, metadata, incoming=incoming, outgoing=outgoing)
    def unexpected(*args, **kwargs):
        raise AssertionError('frame compilation probed source media')
    monkeypatch.setattr(c, 'probe_last_video_packet_timestamp', unexpected)
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .35,
                                  source_durations={'camera': 2})
    assert frame['kind'] == 'transition'
    assert [layer['weight'] for layer in frame['layers']] == [.75, .25]
    preset = next(layer['frame'] for layer in frame['layers'] if layer['frame'].get('kind') == 'preset')
    assert preset['children'] == []
    assert preset['scene_id'] == ('second' if incoming == 'legacy' else 'first')
    slot = preset['preset_plan']['render_slots'][0]
    assert slot['source_from'] == pytest.approx(.75 if incoming == 'legacy' else .55)
    assert preset['preset_plan']['canvas_tuple'] == (160, 90)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('incoming,outgoing', [('legacy','structured'), ('structured','legacy')])
@pytest.mark.parametrize('kind', ['dissolve', 'dip-black', 'dip-white'])
def test_mixed_screenshot_preview_layout_and_export_agree(project, tmp_path, monkeypatch, incoming, outgoing, kind):
    spec, metadata = project
    _transition_project(spec, metadata, incoming=incoming, outgoing=outgoing, kind=kind)
    _save(spec, metadata, tmp_path, monkeypatch)
    before = Path('moviestar/spec.json').read_bytes()
    _invoke(['export', '--out', 'movie.mp4', '--quiet', '--video-encoder', 'x264'])
    for at, index in [('.3', 3), ('.4', 4)]:
        shot = _invoke(['screenshot', '--at', at, '--out', 'shot.png'])
        preview = _invoke(['composition', 'model', 'preview', '--at', f'0:00:00{at}00',
            '--out', 'preview.png', '--layout-out', 'labels.png'])
        labels = _invoke(['composition', 'model', 'layout', '--at', f'0:00:00{at}00', '--out', 'layout.png'])
        assert Path('shot.png').read_bytes() == Path('preview.png').read_bytes()
        assert Path('labels.png').read_bytes() == Path('layout.png').read_bytes()
        assert shot['spatial_preview']['boxes'] == preview['preview']['boxes'] == labels['layout']['boxes']
        assert {box['scene_id'] for box in labels['layout']['boxes']} == {'first', 'second'}
        actual, expected = _rgb(Path('shot.png')), _pixels('movie.mp4', index)
        for x, y in [(10,10), (100,70)]:
            offset = (y*160+x)*3
            assert max(abs(a-b) for a,b in zip(actual[offset:offset+3], expected[offset:offset+3])) <= 12
    assert Path('moviestar/spec.json').read_bytes() == before


def test_mixed_dry_run_and_missing_media_layout_need_no_source_decode(project, tmp_path, monkeypatch):
    import moviestar.cli as c
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy')
    _save(spec, metadata, tmp_path, monkeypatch)
    def unexpected(*args, **kwargs):
        raise AssertionError('dry-run probed source media')
    monkeypatch.setattr(c, 'probe_last_video_packet_timestamp', unexpected)
    result = _invoke(['screenshot', '--at', '.35', '--out', 'shot.png', '--dry-run'])
    assert result['spatial_frame']['layers'][1]['frame']['kind'] == 'preset'
    assert not Path('shot.png').exists()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_mixed_missing_media_layout_still_shows_preset_geometry(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy')
    Path(spec['sources'][0]['path']).unlink()
    _save(spec, metadata, tmp_path, monkeypatch)
    result = _invoke(['composition', 'model', 'layout', '--at', '0:00:00.350', '--out', 'draft.png'])
    assert result['layout']['layers'][1]['renderer'] == 'preset_ffmpeg'
    assert result['layout']['boxes'][-1]['rect'] == {'x': 0, 'y': 0, 'width': 160, 'height': 90}
    assert Path('draft.png').is_file()
    pixels = _rgb(Path('draft.png'))
    offset = (70*160+100)*3
    assert tuple(pixels[offset:offset+3]) == (16, 32, 48)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('edge', ['opening_transition', 'closing_transition'])
@pytest.mark.parametrize('kind', ['dip-black', 'dip-white'])
def test_preset_edge_fades_in_spatial_projects_match_export(project, tmp_path, monkeypatch, edge, kind):
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy' if edge.startswith('closing') else 'structured',
        outgoing='legacy' if edge.startswith('opening') else 'structured')
    spec['composition'][1].pop('transition_in')
    spec[edge] = {'type': kind, 'duration': .2}
    _save(spec, metadata, tmp_path, monkeypatch)
    at, index = ('.1', 1) if edge.startswith('opening') else ('.8', 7)
    result = _invoke(['screenshot', '--at', at, '--out', 'edge.png'])
    _invoke(['export', '--out', 'edge.mp4', '--quiet', '--video-encoder', 'x264'])
    assert result['spatial_frame']['layers'][0]['frame']['kind'] == 'preset'
    offset = (70*160+100)*3
    actual, expected = _rgb(Path('edge.png')), _pixels('edge.mp4', index)
    assert max(abs(a-b) for a,b in zip(actual[offset:offset+3], expected[offset:offset+3])) <= 8


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_preset_only_transition_participants_inside_a_spatial_project(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy', outgoing='legacy')
    spec['composition'].append({'id': 'title', 'name': 'Title', 'duration': .2,
        'layout': {'preset': 'single'}, 'slots': []})
    spec['spatial']['scenes']['title'] = {'layout': {'children': []}}
    _save(spec, metadata, tmp_path, monkeypatch)
    shot = _invoke(['screenshot', '--at', '.4', '--out', 'shot.png'])
    assert [layer['frame']['kind'] for layer in shot['spatial_frame']['layers']] == ['preset', 'preset']
    _invoke(['export', '--out', 'movie.mp4', '--quiet', '--video-encoder', 'x264'])
    actual, expected = _rgb(Path('shot.png')), _pixels('movie.mp4', 4)
    assert max(abs(a-b) for a,b in zip(actual, expected)) <= 12


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_mixed_inspect_borrows_one_browser_for_layers_and_samples(project, tmp_path, monkeypatch):
    import moviestar.spatial_inspection as inspection
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy')
    _save(spec, metadata, tmp_path, monkeypatch)
    sessions = []
    original = inspection.BrowserSession
    class Tracked(original):
        def __init__(self):
            super().__init__()
            sessions.append(self)
    monkeypatch.setattr(inspection, 'BrowserSession', Tracked)
    result = _invoke(['inspect', '--from', '.3', '--to', '.5', '--interval', '.05'])
    assert len(sessions) == 1 and sessions[0]._process.poll() is not None
    assert not Path(sessions[0]._profile.name).exists()
    for thumbnail in result['thumbnails']:
        report = thumbnail['spatial_preview']
        assert report['canvas'] == {'width': 160, 'height': 90}
        assert report['image_dimensions'] == {'width': 640, 'height': 360}
        assert {layer['renderer'] for layer in report['layers']} == {'spatial', 'preset_ffmpeg'}


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('failure', ['missing_asset', 'shutdown'])
def test_mixed_layer_failures_preserve_all_prior_outputs(project, tmp_path, monkeypatch, failure):
    import moviestar.spatial_session as worker
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy')
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .35,
                                  source_durations={'camera': 2}, project=metadata)
    if failure == 'missing_asset':
        frame['layers'][1]['frame']['preset_plan']['render_slots'][0]['path'] = 'unavailable.mp4'
    sessions = []
    original = worker.BrowserSession
    class Tracked(original):
        def __init__(self):
            super().__init__()
            sessions.append(self)
        def close(self, *, abort=False):
            super().close(abort=abort)
            if failure == 'shutdown' and not abort:
                raise RuntimeError('mixed shutdown failed')
    monkeypatch.setattr(worker, 'BrowserSession', Tracked)
    out, labels, reference = (tmp_path / name for name in ('prior.png', 'labels.png', 'reference.png'))
    for path in (out, labels, reference):
        path.write_bytes(b'prior')
    with pytest.raises((ValueError, RuntimeError), match='asset|shutdown'):
        render_spatial_frame(spec, frame, {'width': 160, 'height': 90}, output_path=out,
            layout_output_path=labels, reference_path=reference, project_root=tmp_path)
    assert all(path.read_bytes() == b'prior' for path in (out, labels, reference))
    assert len(sessions) == 1 and sessions[0]._process.poll() is not None
    assert not Path(sessions[0]._profile.name).exists()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_mixed_canvas_override_resolves_preset_regions_with_spatial_layout(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy')
    second = spec['composition'][1]
    second['layout'] = {'preset': 'two-up'}
    second['slots'] = [{**deepcopy(second['slots'][0]), 'slot': role} for role in ('left', 'right')]
    _save(spec, metadata, tmp_path, monkeypatch)
    result = _invoke(['composition', 'model', 'layout', '--at', '0:00:00.350', '--canvas', '90x160'])
    preset = [box for box in result['layout']['boxes'] if box.get('geometry_source') == 'preset_renderer']
    assert [box['rect'] for box in preset] == [
        {'x': 0, 'y': 0, 'width': 90, 'height': 80}, {'x': 0, 'y': 80, 'width': 90, 'height': 80}]
    assert [box['path'][-1] for box in preset] == ['left', 'right']
    preview = _invoke(['composition', 'model', 'preview', '--at', '0:00:00.350',
        '--canvas', '90x160', '--out', 'portrait.png'])
    assert preview['preview']['boxes'] == result['layout']['boxes']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_preset_pip_shape_motion_and_camera_keep_native_export_behavior(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy')
    second = spec['composition'][1]
    second['layout'] = {'preset': 'picture-in-picture', 'geometry': {'inset': {
        'size': .32, 'anchor': 'bottom-right', 'motion': {'preset': 'bounce', 'speed': 'medium'},
        'shape': {'kind': 'circle', 'border': {'width': 2, 'color': 'white'}}}}}
    second['slots'].append({**deepcopy(second['slots'][0]), 'slot': 'inset',
        'source_from': '0:00:00.300', 'source_to': '0:00:00.700'})
    spec['motion'] = {'version': 1, 'scenes': [{'scene_id': 'second', 'scene': 'second', 'slots': [{'slot': 'main',
        'camera': [{'id': 'crop', 'at': '0:00:00.000', 'ease': 'cut', 'to': {'target': {
            'space': 'source', 'units': 'normalized', 'rect': {'x': .2, 'y': .1, 'w': .6, 'h': .8}}}}]}]}]}
    _save(spec, metadata, tmp_path, monkeypatch)
    _invoke(['export', '--out', 'movie.mp4', '--quiet', '--video-encoder', 'x264'])
    regions = []
    for at, index in [('.3', 3), ('.4', 4)]:
        shot = _invoke(['screenshot', '--at', at, '--out', 'shot.png'])
        preset = shot['spatial_frame']['layers'][1]['frame']
        assert (preset['preset_plan']['camera_states']['main']['target'] == 'full') is (index == 3)
        boxes = [box for box in shot['spatial_preview']['boxes'] if box['scene_id'] == 'second']
        inset = next(box for box in boxes if box['path'][-1] == 'inset')
        assert inset['shape']['kind'] == 'circle'
        rect = inset['rect']; regions.append(rect)
        actual, expected = _rgb(Path('shot.png')), _pixels('movie.mp4', index)
        for x,y in [(100,70), (int(rect['x']+rect['width']/2), int(rect['y']+rect['height']/2))]:
            offset = (y*160+x)*3
            assert max(abs(a-b) for a,b in zip(actual[offset:offset+3], expected[offset:offset+3])) <= 12
    assert regions[0] != regions[1]


def _legacy_caption(spec):
    from tests.test_overlays import _caption_record
    cue = _caption_record()
    cue.update({'from': '0:00:00.000', 'to': '0:00:00.800', 'text': 'hello world',
        'position': {'anchor': 'center', 'coordinate_space': 'canvas', 'x': .5, 'y': .5},
        'tokens': [{'text': 'hello', 'from': '0:00:00.000', 'to': '0:00:00.400'},
                   {'text': 'world', 'from': '0:00:00.400', 'to': '0:00:00.800'}]})
    cue['style']['resolved'].update({'font_size': 12, 'stroke_width': 0})
    spec['overlays'] = [cue]


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_legacy_caption_estimates_and_highlight_render_without_project_writes(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata, outgoing='legacy')
    _legacy_caption(spec)
    _save(spec, metadata, tmp_path, monkeypatch)
    before = Path('moviestar/spec.json').read_bytes()
    shot = _invoke(['screenshot', '--at', '.35', '--out', 'caption.png'])
    report = shot['spatial_preview']
    caption = next(box for box in report['boxes'] if box['kind'] == 'caption')
    assert caption['geometry_source'] == 'preset_overlay_estimate' and caption['geometry_is'] == 'estimated'
    assert 'text_lines' not in caption
    assert any(d['code'] == 'preset_overlay_estimated_bounds' for d in report['diagnostics'])
    pixels = _rgb(Path('caption.png'))
    # Native ASS paints white text and the yellow active word in the preset
    # layer. The structured scene has no declared caption element.
    assert any(g > 100 and b > 100 for r,g,b in zip(pixels[::3], pixels[1::3], pixels[2::3]))
    assert any(g > 100 and b < 160 and r > 150 for r,g,b in zip(pixels[::3], pixels[1::3], pixels[2::3]))
    assert not (tmp_path / 'moviestar' / 'highlight-ass').exists()
    assert Path('moviestar/spec.json').read_bytes() == before
    _invoke(['screenshot', '--at', '.3', '--out', 'caption.png'])
    _invoke(['export', '--out', 'caption.mp4', '--quiet', '--video-encoder', 'x264'])
    def bounds(pixels):
        positions = [(i%160, i//160) for i, green in enumerate(pixels[1::3]) if green > 100]
        assert positions
        return [min(p[axis] for p in positions) for axis in (0,1)] + [max(p[axis] for p in positions) for axis in (0,1)]
    still, exported = bounds(_rgb(Path('caption.png'))), bounds(_pixels('caption.mp4', 3))
    assert max(abs(a-b) for a,b in zip(still, exported)) <= 1


def test_preset_caption_refresh_and_staleness_use_only_active_tracks(project, monkeypatch):
    import moviestar.cli as c
    from moviestar.spatial_inspection import spatial_frame_context
    spec, metadata = project
    _transition_project(spec, metadata, outgoing='legacy')
    _legacy_caption(spec)
    spec['captions'] = [{'track': 'captions', 'frozen': False}]
    calls = []
    def refresh(project, document, *, tracks):
        calls.append(tracks)
        return document
    monkeypatch.setattr(c, '_refresh_caption_tracks', refresh)
    monkeypatch.setattr(c, '_caption_staleness', lambda *args: True)
    _, frame, _ = spatial_frame_context(metadata, spec, resolve_project(metadata, spec), .35, {'width': 160, 'height': 90})
    assert calls == [{'captions'}]
    stale = [d for d in frame['diagnostics'] if d['code'] == 'stale_caption_track']
    assert [d['path'] for d in stale] == [['first', 'overlays', 'cap_0001']]


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('incoming,outgoing', [('legacy','structured'), ('structured','legacy')])
@pytest.mark.parametrize('kind', ['dissolve', 'dip-black', 'dip-white'])
def test_mixed_fractional_cut_stills_keep_export_weights(project, tmp_path, monkeypatch, incoming, outgoing, kind):
    spec, metadata = project
    _transition_project(spec, metadata, incoming=incoming, outgoing=outgoing, kind=kind)
    spec['composition'][0]['slots'][0]['source_to'] = '0:00:00.550'
    if outgoing == 'structured':
        spec['spatial']['scenes']['first']['layout']['children'][0]['content']['source']['end'] = .55
    _save(spec, metadata, tmp_path, monkeypatch)
    _invoke(['export', '--out', 'movie.mp4', '--quiet', '--video-encoder', 'x264'])
    for index in (3, 4):
        _invoke(['screenshot', '--at', str(index/10), '--out', 'shot.png'])
        actual, expected = _rgb(Path('shot.png')), _pixels('movie.mp4', index)
        for x,y in [(10,10), (100,70)]:
            offset = (y*160+x)*3
            assert max(abs(a-b) for a,b in zip(actual[offset:offset+3], expected[offset:offset+3])) <= 12


@pytest.mark.parametrize('held', [False, True])
def test_preset_transition_handles_reuse_edge_pacing(project, held):
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy')
    pacing = ([{'id': 'hold', 'mode': 'hold', 'at': '0:00:00.000', 'space': 'source-local', 'duration': '0:00:00.200'}]
        if held else [{'id': 'speed', 'mode': 'speed',
            'range': {'from': '0:00:00.000', 'to': '0:00:00.400', 'space': 'source-local'}, 'speed': 2}])
    spec['motion'] = {'version': 1, 'scenes': [{'scene_id': 'second', 'scene': 'second',
        'slots': [{'slot': 'main', 'pacing': pacing}]}]}
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .35,
        source_durations={'camera': 2}, project=metadata)
    slot = frame['layers'][1]['frame']['preset_plan']['render_slots'][0]
    assert slot['held'] is held
    assert slot['source_from'] == pytest.approx(.8 if held else .7)
    assert slot['speed'] == (None if held else 2)


def test_shaped_preset_dry_run_does_not_materialize_mask_files(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy')
    second = spec['composition'][1]
    second['layout'] = {'preset': 'picture-in-picture', 'geometry': {'inset': {
        'size': .32, 'shape': {'kind': 'circle', 'border': {'width': 2, 'color': 'white'}}}}}
    second['slots'].append({**deepcopy(second['slots'][0]), 'slot': 'inset'})
    _save(spec, metadata, tmp_path, monkeypatch)
    before = {str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')}
    _invoke(['screenshot', '--at', '.35', '--out', 'shot.png', '--dry-run'])
    _invoke(['inspect', '--from', '.3', '--to', '.5', '--dry-run'])
    assert {str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')} == before


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_preset_only_inspect_window_in_spatial_project_stages_masks_and_stays_fresh(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata, incoming='legacy')
    second = spec['composition'][1]
    second['layout'] = {'preset': 'picture-in-picture', 'geometry': {'inset': {
        'size': .32, 'shape': {'kind': 'circle', 'border': {'width': 2, 'color': 'white'}}}}}
    second['slots'].append({**deepcopy(second['slots'][0]), 'slot': 'inset'})
    _save(spec, metadata, tmp_path, monkeypatch)
    first = _invoke(['inspect', '--from', '.6', '--to', '.8', '--interval', '.1'])
    second = _invoke(['inspect', '--from', '.6', '--to', '.8', '--interval', '.1'])
    assert first['extraction']['cached'] is second['extraction']['cached'] is False
    assert first['thumbnails'][0]['path'] != second['thumbnails'][0]['path']
    assert all(Path(item['path']).is_file() for item in first['thumbnails'])
    assert not (tmp_path / 'moviestar' / 'masks').exists()
    assert not list(Path(first['extraction']['frames_dir']).rglob('*.png'))
