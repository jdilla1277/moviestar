"""Transition stills describe and render the participating spatial trees."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available, measure_spatial_layout, prepare_layout_frame
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_render import render_spatial_frame
from tests.test_spatial_render import project, _rgb
from tests.test_spatial_captions import caption_project
from tests.test_spatial_video import _transition_project, _pixels
from tests.test_spatial_inspection import _save, _invoke


@pytest.mark.parametrize('kind,at,weights,color', [
    ('dissolve', .3, (1, 0), (0, 0)), ('dissolve', .35, (.75, .25), (0, 0)),
    ('dissolve', .4, (.5, .5), (0, 0)), ('dissolve', .45, (.25, .75), (0, 0)),
    ('dip-black', .35, (.5, 0), (.5, 0)), ('dip-black', .4, (0, 0), (1, 0)),
    ('dip-white', .45, (0, .5), (0, .5)),
])
def test_transition_frame_keeps_scene_paths_clocks_and_weights(project, kind, at, weights, color):
    spec, metadata = project
    _transition_project(spec, metadata, kind=kind)
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), at,
                                  source_durations={'camera': 2})
    assert frame['kind'] == 'transition'
    assert [layer['scene_id'] for layer in frame['layers']] == ['first', 'second']
    assert [layer['role'] for layer in frame['layers']] == ['outgoing', 'incoming']
    assert [layer['weight'] for layer in frame['layers']] == pytest.approx(weights)
    assert [frame['color_weights'][color] for color in ('black', 'white')] == pytest.approx(color)
    for layer in frame['layers']:
        assert layer['frame']['children'][0]['occurrence_path'] == [layer['scene_id'], 'clip']
    if kind == 'dissolve':
        assert frame['layers'][0]['frame']['children'][0]['media']['source_time_s'] == pytest.approx(.2+at)
        assert frame['layers'][1]['frame']['children'][0]['media']['source_time_s'] == pytest.approx(.8+at-.4)
    prepared = prepare_layout_frame(frame, {'width': 160, 'height': 90})
    assert prepared['scene_layout']['kind'] == 'transition'
    assert prepared['diagnostics'] == []


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('kind', ['dissolve', 'dip-black', 'dip-white'])
def test_transition_screenshot_preview_and_layout_share_layers_and_export_pixels(project, tmp_path, monkeypatch, kind):
    spec, metadata = project
    _transition_project(spec, metadata, kind=kind)
    spec['composition'][1]['transition_in']['duration'] = .4
    _save(spec, metadata, tmp_path, monkeypatch)
    before = Path('moviestar/spec.json').read_bytes()
    shot = _invoke(['screenshot', '--at', '.3', '--out', 'shot.png'])
    preview = _invoke(['composition', 'model', 'preview', '--at', '0:00:00.300',
                       '--out', 'preview.png', '--layout-out', 'labels.png'])
    labeled = _invoke(['composition', 'model', 'layout', '--at', '0:00:00.300', '--out', 'layout.png'])
    assert _rgb(Path('shot.png')) == _rgb(Path('preview.png'))
    assert shot['spatial_preview']['boxes'] == preview['preview']['boxes'] == labeled['layout']['boxes']
    assert {box['scene_id'] for box in shot['spatial_preview']['boxes']} == {'first', 'second'}
    assert Path('labels.png').read_bytes() == Path('layout.png').read_bytes()
    assert preview['frame']['kind'] == 'transition'
    _invoke(['export', '--out', 'movie.mp4', '--video-encoder', 'x264', '--quiet'])
    actual, expected = _rgb(Path('shot.png')), _pixels('movie.mp4', 3)
    for x,y in [(100,70), (10,10)]:
        offset=(y*160+x)*3
        assert max(abs(a-b) for a,b in zip(actual[offset:offset+3], expected[offset:offset+3])) <= 12
    assert Path('moviestar/spec.json').read_bytes() == before


def test_transition_dry_run_never_starts_browser_or_writes_assets(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata)
    _save(spec, metadata, tmp_path, monkeypatch)
    from moviestar.spatial_session import BrowserSession
    def unexpected(*args, **kwargs):
        raise AssertionError('dry-run launched a browser')
    monkeypatch.setattr(BrowserSession, '_start', unexpected)
    result = _invoke(['screenshot', '--at', '.4', '--out', 'shot.png', '--dry-run'])
    assert result['spatial_frame']['kind'] == 'transition'
    assert not Path('shot.png').exists()
    inspected = _invoke(['inspect', '--from', '.3', '--to', '.5', '--interval', '.1', '--dry-run'])
    assert inspected['would_extract_frames_count'] >= 2
    assert not Path(inspected['would_write_to']).exists()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('field,kind,at,index', [
    ('opening_transition', 'dip-black', .1, 3),
    ('opening_transition', 'dip-white', .1, 3),
    ('closing_transition', 'dip-black', .3, 9),
    ('closing_transition', 'dip-white', .4, 11),
])
def test_edge_fades_match_export_and_exact_end_uses_last_sample(project, tmp_path, monkeypatch, field, kind, at, index):
    from tests.test_spatial_static import _static
    spec, metadata = project
    _static(spec)
    spec[field] = {'type': kind, 'duration': .2}
    _save(spec, metadata, tmp_path, monkeypatch)
    shot = _invoke(['screenshot', '--at', str(at), '--out', 'edge.png'])
    _invoke(['export', '--out', 'edge.mp4', '--quiet', '--video-encoder', 'x264'])
    actual, expected = _rgb(Path('edge.png')), _pixels('edge.mp4', index)
    offset = (175*320+310)*3
    assert max(abs(a-b) for a,b in zip(actual[offset:offset+3], expected[offset:offset+3])) <= 8
    assert shot['spatial_frame']['transitions'][0]['scope'] == field.split('_')[0]
    if at == .4:
        assert shot['spatial_frame']['sample_time_s'] == round(11/30, 6)


def test_overlapping_edge_fades_apply_after_the_internal_blend(project):
    spec, metadata = project
    _transition_project(spec, metadata)
    spec['opening_transition'] = {'type': 'dip-black', 'duration': .5}
    spec['closing_transition'] = {'type': 'dip-white', 'duration': .5}
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .4, source_durations={'camera': 2})
    assert [layer['weight'] for layer in frame['layers']] == pytest.approx([.32,.32])
    assert frame['color_weights'] == pytest.approx({'black': .16, 'white': .2})
    assert [t['scope'] for t in frame['transitions']] == ['internal', 'opening', 'closing']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_transition_layout_can_show_both_scenes_without_available_media(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata)
    Path(spec['sources'][0]['path']).unlink()
    _save(spec, metadata, tmp_path, monkeypatch)
    result = _invoke(['composition', 'model', 'layout', '--at', '0:00:00.400', '--out', 'draft.png'])
    assert {box['scene_id'] for box in result['layout']['boxes']} == {'first', 'second'}
    assert Path('draft.png').is_file()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('failure', ['second_asset', 'shutdown', 'blend'])
def test_transition_failures_preserve_both_prior_artifacts_and_close_owned_session(
        project, tmp_path, monkeypatch, failure):
    import moviestar.spatial_session as worker
    import moviestar.spatial_transition as transition
    spec, metadata = project
    _transition_project(spec, metadata)
    if failure == 'second_asset':
        spec['spatial']['scenes']['second']['layout']['children'].append({
            'id': 'logo', 'type': 'image', 'content': {'asset_id': 'logo'},
            'layout': {'width': 20, 'height': 20}})
        Path(tmp_path / 'logo.png').unlink()
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .4, source_durations={'camera': 2})
    sessions = []
    original = worker.BrowserSession
    class Tracked(original):
        def __init__(self):
            super().__init__()
            sessions.append(self)
        def close(self, *, abort=False):
            super().close(abort=abort)
            if failure == 'shutdown' and not abort:
                raise RuntimeError('late shutdown failure')
    monkeypatch.setattr(worker, 'BrowserSession', Tracked)
    if failure == 'blend':
        original_blend = transition._blend_layers
        calls = 0
        def broken(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('blend failed')
            return original_blend(*args, **kwargs)
        monkeypatch.setattr(transition, '_blend_layers', broken)
    out, labels, reference = (tmp_path / name for name in ('prior.png','labels.png','reference.png'))
    out.write_bytes(b'prior frame'); labels.write_bytes(b'prior layout'); reference.write_bytes(b'prior reference')
    with pytest.raises((ValueError, RuntimeError), match='asset|shutdown|blend'):
        render_spatial_frame(spec, frame, {'width': 160, 'height': 90}, output_path=out,
            layout_output_path=labels, reference_path=reference, project_root=tmp_path)
    assert reference.read_bytes() == b'prior reference'
    assert out.read_bytes() == b'prior frame' and labels.read_bytes() == b'prior layout'
    assert len(sessions) == 1 and sessions[0]._process.poll() is not None
    assert not Path(sessions[0]._profile.name).exists()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_transition_inspection_keeps_one_session_and_full_geometry_before_resizing(project, tmp_path, monkeypatch):
    import moviestar.spatial_inspection as inspection
    spec, metadata = project
    _transition_project(spec, metadata)
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
    for item in result['thumbnails']:
        preview = item['spatial_preview']
        assert preview['canvas'] == {'width': 160, 'height': 90}
        assert preview['image_dimensions'] == {'width': 640, 'height': 360}
        assert {box['scene_id'] for box in preview['boxes']} == {'first', 'second'}
        assert Path(item['path']).is_file()


def test_exact_end_transition_reports_layers_for_the_last_global_sample(project, tmp_path, monkeypatch):
    from tests.test_spatial_inspection import _end_sample_transition
    spec, metadata = project
    _end_sample_transition(spec, spatial_owner=True)
    _save(spec, metadata, tmp_path, monkeypatch)
    result = _invoke(['screenshot', '--at', '.14', '--out', 'end.png', '--dry-run'])
    frame = result['spatial_frame']
    assert frame['sample_time_s'] == .1
    assert [layer['weight'] for layer in frame['layers']] == [.75, .25]
    assert [layer['scene_id'] for layer in frame['layers']] == ['interview', 'title']
    assert result['active_scene']['scene_id'] == 'interview'


@pytest.mark.parametrize('incoming,outgoing', [('legacy','structured'), ('structured','legacy')])
def test_mixed_transitions_stay_guarded_from_both_sides(project, tmp_path, monkeypatch, incoming, outgoing):
    spec, metadata = project
    _transition_project(spec, metadata, incoming=incoming, outgoing=outgoing)
    _save(spec, metadata, tmp_path, monkeypatch)
    for at in ('.3', '.4', '.499'):
        result = CliRunner().invoke(cli, ['screenshot', '--at', at, '--out', 'frame.png', '--dry-run'])
        assert result.exit_code == 1
        assert 'preset scene' in json.loads(result.stdout)['error']
        assert not Path('frame.png').exists()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_transition_layers_keep_nested_caption_clocks_and_component_motion(tmp_path, monkeypatch, caption_project):
    spec, metadata = caption_project
    for scene in spec['composition']:
        scene['slots'][0].update({'source_from': '0:00:00.200', 'source_to': '0:00:02.200'})
    spec['composition'][1]['transition_in'] = {'type': 'dissolve', 'duration': .4}
    spec['overlays'][1]['to'] = '0:00:02.000'
    for scene in spec['spatial']['scenes'].values():
        scene['layout']['children'][0]['motion'] = {'translate_x': [{'at': 0, 'value': 0}, {'at': 2, 'value': 20}]}
    _save(spec, metadata, tmp_path, monkeypatch)
    before = Path('moviestar/spec.json').read_bytes()
    result = _invoke(['composition', 'model', 'preview', '--at', '0:00:01.900', '--out', 'captions.png'])
    report = result['preview']
    captions = [box for box in report['boxes'] if box['kind'] == 'caption']
    assert [box['caption']['text'] for box in captions] == ['Second line\nwith two rows', 'Next scene']
    assert [box['path'] for box in captions] == [['first','subtitles','words'], ['second','subtitles','words']]
    assert [layer['frame']['scene_time_s'] for layer in result['frame']['layers']] == [1.9, 0]
    components = [box for box in report['boxes'] if box['kind'] == 'component']
    assert [box['rect']['x']-box['base_rect']['x'] for box in components] == [19, 0]
    assert Path('moviestar/spec.json').read_bytes() == before


def test_transition_refreshes_all_placed_tracks_in_memory(tmp_path, monkeypatch, caption_project):
    import moviestar.cli as c
    from moviestar.spatial_inspection import spatial_frame_context
    spec, metadata = caption_project
    for scene in spec['composition']:
        scene['slots'][0].update({'source_from': '0:00:00.200', 'source_to': '0:00:02.200'})
    spec['composition'][1]['transition_in'] = {'type': 'dissolve', 'duration': .4}
    spec['spatial']['scenes']['second']['layout']['children'][0]['bindings']['track'] = {'track': 'translated'}
    before = deepcopy(spec)
    called = []
    def refresh(project, document, *, tracks):
        called.append(tracks)
        updated = deepcopy(document)
        for cue in updated['overlays']:
            cue['text'] = 'Refreshed ' + cue['track']
        return updated
    monkeypatch.setattr(c, '_refresh_caption_tracks', refresh)
    _, frame, _ = spatial_frame_context(metadata, spec, resolve_project(metadata, spec), 2.1, {'width': 640, 'height': 360})
    assert called == [{'captions', 'translated'}]
    assert [layer['frame']['children'][0]['children'][0]['caption']['text'] for layer in frame['layers']] == [
        '', 'Refreshed translated']  # The outgoing edge has no cue after 1.75s.
    assert spec == before


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_reservations_stay_scoped_to_each_scene_and_layout_still_shows_errors(project, tmp_path):
    spec, metadata = project
    _transition_project(spec, metadata)
    for scene in spec['spatial']['scenes'].values():
        scene['layout']['reserved_regions'] = [{'id': 'picture', 'target': ['clip']}]
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .4, source_durations={'camera': 2})
    clear = measure_spatial_layout(frame, {'width': 160, 'height': 90})
    assert clear['diagnostics'] == []
    assert [region['path'] for region in clear['reserved_regions']] == [['first','clip'], ['second','clip']]
    spec['spatial']['scenes']['first']['layout']['children'].append({'id': 'badge', 'type': 'shape',
        'content': {}, 'layout': {'position': 'absolute', 'width': 20, 'height': 20, 'left': 0, 'top': 0}})
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .4, source_durations={'camera': 2})
    report = measure_spatial_layout(frame, {'width': 160, 'height': 90}, output_path=tmp_path / 'bad-layout.png')
    collisions = [d for d in report['diagnostics'] if d['code'] == 'reserved_region_overlap']
    assert [d['path'] for d in collisions] == [['first','badge']]
    assert (tmp_path / 'bad-layout.png').is_file()
    prior = tmp_path / 'prior.png'; prior.write_bytes(b'prior')
    with pytest.raises(ValueError, match='reserved_region_overlap.*first/badge'):
        render_spatial_frame(spec, frame, {'width': 160, 'height': 90}, output_path=prior, project_root=tmp_path)
    assert prior.read_bytes() == b'prior'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('edges', [False, True])
def test_fractional_cut_and_combined_edge_fades_keep_export_blend_weights(project, tmp_path, monkeypatch, edges):
    spec, metadata = project
    _transition_project(spec, metadata)
    if edges:
        spec['opening_transition'] = {'type': 'dip-black', 'duration': .5}
        spec['closing_transition'] = {'type': 'dip-white', 'duration': .5}
        at, frame_index = '.4', 4
    else:
        spec['composition'][0]['slots'][0]['source_to'] = '0:00:00.550'
        spec['spatial']['scenes']['first']['layout']['children'][0]['content']['source']['end'] = .55
        at, frame_index = '.3', 3
    _save(spec, metadata, tmp_path, monkeypatch)
    result = _invoke(['screenshot', '--at', at, '--out', 'shot.png'])
    _invoke(['export', '--out', 'movie.mp4', '--quiet', '--video-encoder', 'x264'])
    if not edges:
        assert [layer['weight'] for layer in result['spatial_frame']['layers']] == [.75,.25]
    actual, expected = _rgb(Path('shot.png')), _pixels('movie.mp4', frame_index)
    offset = (70*160+100)*3
    assert max(abs(a-b) for a,b in zip(actual[offset:offset+3], expected[offset:offset+3])) <= 12


def test_overlapping_internal_transitions_remain_an_explicit_guard(project):
    spec, metadata = project
    _transition_project(spec, metadata)
    spec['composition'][1]['transition_in']['duration'] = .6
    third = deepcopy(spec['composition'][1]); third.update({'id': 'third', 'name': 'Third'})
    spec['composition'].append(third)
    spec['spatial']['scenes']['third'] = deepcopy(spec['spatial']['scenes']['second'])
    with pytest.raises(ValueError, match='overlapping internal transitions'):
        compile_spatial_frame(spec, resolve_project(metadata, spec), .6, source_durations={'camera': 2})


def test_transition_dry_run_reports_bound_media_handle_failures_before_rendering(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata)
    spec['spatial']['scenes']['first']['layout']['children'][0]['content']['source'] = {'start': 1.6, 'end': 2}
    _save(spec, metadata, tmp_path, monkeypatch)
    result = CliRunner().invoke(cli, ['screenshot', '--at', '.45', '--out', 'bad.png', '--dry-run'])
    assert result.exit_code == 1
    assert 'insufficient_spatial_transition_handle' in json.loads(result.stdout)['error']
    assert 'first/clip' in json.loads(result.stdout)['error']
    assert not Path('bad.png').exists()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_transition_canvas_rules_change_geometry_without_changing_layer_clocks(project, tmp_path, monkeypatch):
    spec, metadata = project
    _transition_project(spec, metadata)
    for scene in spec['spatial']['scenes'].values():
        scene['layout']['children'][0]['format_rules'] = [{'id': 'portrait', 'when': {'orientation': 'portrait'},
            'layout': {'left': 12, 'top': 30, 'width': 80, 'height': 60}}]
    _save(spec, metadata, tmp_path, monkeypatch)
    result = _invoke(['composition', 'model', 'preview', '--at', '0:00:00.400',
                       '--canvas', '180x320', '--out', 'portrait.png', '--layout-out', 'labels.png'])
    preview = result['preview']
    assert preview['canvas'] == {'width': 180, 'height': 320}
    assert [box['rect'] for box in preview['boxes']] == [{'x':12,'y':30,'width':80,'height':60}]*2
    assert [box['matched_format_rules'] for box in preview['boxes']] == [['portrait'],['portrait']]
    assert [layer['frame']['scene_time_s'] for layer in result['frame']['layers']] == [.4,0]
    assert [layer['weight'] for layer in result['frame']['layers']] == [.5,.5]


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_transition_browser_reference_shares_the_scene_composite(project, tmp_path):
    spec, metadata = project
    _transition_project(spec, metadata)
    frame = compile_spatial_frame(spec, resolve_project(metadata, spec), .4, source_durations={'camera': 2})
    output, reference = tmp_path / 'frame.png', tmp_path / 'reference.png'
    report = render_spatial_frame(spec, frame, {'width': 160, 'height': 90}, output_path=output,
                                  reference_path=reference, project_root=tmp_path)
    assert report['kind'] == 'transition'
    assert max(abs(a-b) for a,b in zip(_rgb(output), _rgb(reference))) <= 3
