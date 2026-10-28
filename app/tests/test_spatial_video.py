"""Spatial scenes reach real watch/export with the canonical audio timeline."""

from copy import deepcopy
import json
import math
from pathlib import Path
import struct
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_render import render_spatial_frame
from moviestar.spec import load_spec, save_spec
from tests.test_spatial_render import project


@pytest.fixture
def video_project(project, tmp_path, monkeypatch):
    spec, metadata = project
    source = tmp_path / 'with-audio.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', spec['sources'][0]['path'],
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
                    '-c:v', 'copy', '-c:a', 'pcm_s16le', str(source)], check=True)
    spec['sources'][0]['path'] = str(source)
    metadata['sources'][0].update({'path': str(source), 'audio_codec': 'pcm_s16le', 'has_audio': True})
    spec['composition'][0]['slots'][0]['source_to'] = '0:00:00.400'
    spec['composition'][0]['audio_from'] = 'camera'
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return spec, metadata


def _invoke(arguments):
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _pixels(path, frame=0):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
        '-vf', f'select=eq(n\\,{frame})', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])


def _rms(path, *, start=0, duration=.3):
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(path),
        '-t', str(duration), '-vn', '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1'])
    values = struct.unpack('<' + 'f' * (len(raw) // 4), raw)
    return (sum(x*x for x in values) / len(values)) ** .5


@pytest.mark.parametrize('command,extra', [('export', []), ('watch', ['--from', '0', '--to', '.4'])])
def test_spatial_video_dry_run_plans_frames_and_audio_without_writing(video_project, tmp_path, command, extra):
    before = (tmp_path / 'moviestar' / 'spec.json').read_bytes()
    result = _invoke([command, *extra, '--out', 'movie.mp4', '--dry-run', '--video-encoder', 'x264'])
    assert result['mode'] == 'spatial_composition'
    assert result['render_available'] is True
    assert result['spatial_render_plan'][0]['frame_count'] == 4
    assert result['spatial_render_plan'][0]['scene_id'] == 'interview'
    assert '-framerate' in result['scene_render_commands'][0]['command']
    assert result['audio_routes'][0]['audio_from'] == 'camera'
    assert not (tmp_path / 'movie.mp4').exists()
    assert not (tmp_path / 'moviestar' / 'scene-renders').exists()
    assert (tmp_path / 'moviestar' / 'spec.json').read_bytes() == before


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('portrait', [False, True])
def test_export_uses_spatial_pixels_motion_and_one_routed_audio_track(video_project, tmp_path, portrait):
    spec, metadata = video_project
    if portrait:
        spec['composition_canvas'].update({'width': 180, 'height': 320, 'aspect_ratio': '9:16'})
        spec['audio_mix']['source_audio']['gain_db'] = -6
    # One moving component, reused source footage, a transparent logo, and text.
    spec['spatial']['scenes']['interview']['layout']['children'][0]['motion'] = {
        'translate_x': [{'at': 0, 'value': 0}, {'at': .3, 'value': 24}]}
    save_spec(spec, cwd=tmp_path)
    before = load_spec(cwd=tmp_path)
    result = _invoke(['export', '--out', 'movie.mp4', '--video-encoder', 'x264', '--quiet'])
    assert result['status'] == 'exported'
    assert result['output_fps'] == 10
    assert load_spec(cwd=tmp_path) == before
    width, height = (180, 320) if portrait else (320, 180)
    for index in (0, 2, 3):
        actual = _pixels(tmp_path / 'movie.mp4', index)
        frame = compile_spatial_frame(spec, resolve_project(metadata, spec), index / 10,
                                      source_durations={'camera': 2})
        reference = tmp_path / f'reference-{index}.png'
        measured = render_spatial_frame(spec, frame, {'width': width, 'height': height},
                                        output_path=reference, project_root=tmp_path)
        assert len(actual) == width * height * 3
        # Patch interiors independently verify footage frames and transformed
        # placement; H.264 is lossy, so this is not the PNG paint-parity budget.
        for media in measured['media']:
            rect = media['rect']
            x, y = int(rect['x'] + 4), int(rect['y'] + rect['height'] - 6)
            if not (0 <= x < width and 0 <= y < height):
                continue
            expected = _pixels(reference)[(y*width+x)*3:(y*width+x)*3+3]
            pixel = actual[(y*width+x)*3:(y*width+x)*3+3]
            assert max(abs(a-b) for a,b in zip(pixel, expected)) <= 12
        assert max(abs(actual[c] - expected) for c, expected in enumerate((16,32,48))) <= 4
    gain = 10 ** (-6 / 20) if portrait else 1
    assert _rms(tmp_path / 'movie.mp4') == pytest.approx(gain * _rms(Path(spec['sources'][0]['path'])), rel=.12)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_watch_matches_export_global_time_and_audio(video_project, tmp_path):
    _invoke(['export', '--out', 'movie.mp4', '--video-encoder', 'x264', '--quiet'])
    result = _invoke(['watch', '--from', '.1', '--to', '.4', '--out', 'watch.mp4', '--video-encoder', 'x264'])
    assert result['status'] == 'extracted'
    for index in (0, 2):
        export_frame, watch_frame = _pixels('movie.mp4', index+1), _pixels('watch.mp4', index)
        # Stable patch away from glyphs, clips and chroma boundaries.
        for point in ((70, 130), (270, 160)):
            offset = (point[1]*320+point[0])*3
            assert max(abs(a-b) for a,b in zip(export_frame[offset:offset+3], watch_frame[offset:offset+3])) <= 8
    assert _rms(tmp_path / 'watch.mp4') == pytest.approx(_rms(tmp_path / 'movie.mp4', start=.1), rel=.12)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_failed_spatial_export_preserves_prior_output(video_project, tmp_path):
    (tmp_path / 'logo.png').unlink()
    prior = tmp_path / 'movie.mp4'
    prior.write_bytes(b'prior export')
    result = CliRunner().invoke(cli, ['export', '--out', str(prior), '--video-encoder', 'x264', '--quiet'])
    assert result.exit_code == 1
    assert 'logo' in json.loads(result.stdout)['error']
    assert prior.read_bytes() == b'prior export'


def _transition_project(spec, metadata, *, incoming='structured', outgoing='structured', kind='dissolve'):
    spec['composition_canvas'].update({'width': 160, 'height': 90, 'aspect_ratio': '16:9'})
    first = spec['composition'][0]
    first['id'] = first['name'] = 'first'
    first['slots'][0].update({'source_from': '0:00:00.200', 'source_to': '0:00:00.600'})
    second = deepcopy(first)
    second['id'] = second['name'] = 'second'
    second['slots'][0].update({'source_from': '0:00:00.800', 'source_to': '0:00:01.200'})
    second['transition_in'] = {'type': kind, 'duration': .2}
    spec['composition'] = [first, second]
    spec['spatial']['scenes'] = {}
    for identity, style, color, start in [('first', outgoing, '#ff0000', .2), ('second', incoming, '#0000ff', .8)]:
        if style == 'structured':
            spec['spatial']['scenes'][identity] = {'layout': {'background_color': color, 'children': [{
                'id': 'clip', 'type': 'video', 'content': {'asset_id': 'camera', 'source': {'start': start, 'end': start+.4}},
                'layout': {'position': 'absolute', 'width': 40, 'height': 30}}]}}
    return spec


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('outgoing,incoming,kind', [
    ('structured', 'structured', 'dissolve'), ('structured', 'legacy', 'dissolve'),
    ('legacy', 'structured', 'dissolve'), ('structured', 'structured', 'dip-black'),
    ('structured', 'structured', 'dip-white')])
def test_structured_and_legacy_scene_transitions_reuse_existing_compositor(
        video_project, tmp_path, outgoing, incoming, kind):
    spec, metadata = video_project
    _transition_project(spec, metadata, incoming=incoming, outgoing=outgoing, kind=kind)
    save_spec(spec, cwd=tmp_path)
    result = _invoke(['export', '--out', 'transition.mp4', '--quiet', '--video-encoder', 'x264'])
    assert result['transition_render_plan'][0]['type'] == kind
    at_cut = _pixels('transition.mp4', 4)
    center = at_cut[(70*160+100)*3:(70*160+100)*3+3]
    if kind == 'dip-black':
        assert max(center) <= 8
    elif kind == 'dip-white':
        assert min(center) >= 245
    else:
        left = (255, 0, 0) if outgoing == 'structured' else (68, 40, 60)
        right = (0, 0, 255) if incoming == 'structured' else (84, 40, 60)
        assert max(abs(pixel - (a+b)/2) for pixel,a,b in zip(center,left,right)) <= 10
    assert _rms(tmp_path / 'transition.mp4') == pytest.approx(_rms(Path(spec['sources'][0]['path'])), rel=.12)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_watch_inside_transition_matches_export(video_project, tmp_path):
    spec, metadata = video_project
    _transition_project(spec, metadata)
    save_spec(spec, cwd=tmp_path)
    _invoke(['export', '--out', 'whole.mp4', '--quiet', '--video-encoder', 'x264'])
    _invoke(['watch', '--from', '.4', '--to', '.6', '--out', 'part.mp4', '--video-encoder', 'x264'])
    for index in (0, 1):
        whole, part = _pixels('whole.mp4', index+4), _pixels('part.mp4', index)
        offset = (70*160+100)*3
        assert max(abs(a-b) for a,b in zip(whole[offset:offset+3],part[offset:offset+3])) <= 8


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_project_edge_fades_and_partial_watch_use_the_same_window(video_project, tmp_path):
    spec, metadata = video_project
    spec['opening_transition'] = {'type': 'dip-black', 'duration': .2}
    spec['closing_transition'] = {'type': 'dip-white', 'duration': .2}
    save_spec(spec, cwd=tmp_path)
    _invoke(['export', '--out', 'edges.mp4', '--quiet', '--video-encoder', 'x264'])
    assert max(_pixels('edges.mp4', 0)[:3]) <= 8
    _invoke(['watch', '--from', '.1', '--to', '.3', '--out', 'part.mp4', '--video-encoder', 'x264'])
    for index in (0, 1):
        assert max(abs(a-b) for a,b in zip(_pixels('edges.mp4', index+1)[:3], _pixels('part.mp4', index)[:3])) <= 8


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_non_aligned_scene_boundary_keeps_global_frame_and_source_clocks(video_project, tmp_path):
    spec, metadata = video_project
    first = spec['composition'][0]
    first['id'] = first['name'] = 'first'
    first['slots'][0].update({'source_from': '0:00:00.000', 'source_to': '0:00:00.150'})
    second = deepcopy(first)
    second['id'] = second['name'] = 'second'
    second['slots'][0].update({'source_from': '0:00:00.200', 'source_to': '0:00:00.550'})
    spec['composition'] = [first, second]
    spec['spatial']['scenes'] = {name: {'layout': {'children': [{
        'id': 'clip', 'type': 'video', 'content': {'asset_id': 'camera', 'source': {'start': start, 'end': stop}},
        'layout': {'width': '100%', 'height': '100%'}}]}}
        for name,start,stop in [('first', 0, .15), ('second', .25, .6)]}
    save_spec(spec, cwd=tmp_path)
    _invoke(['export', '--out', 'grid.mp4', '--quiet', '--video-encoder', 'x264'])
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                        '-show_entries', 'stream=nb_frames', '-of', 'json', 'grid.mp4']))
    assert int(probe['streams'][0]['nb_frames']) == 5
    for index, source_frame in [(0, 0), (1, 1), (2, 3), (3, 4), (4, 5)]:
        pixels = _pixels('grid.mp4', index)
        offset = (90*320+160)*3
        assert max(abs(a-b) for a,b in zip(pixels[offset:offset+3], (20+8*source_frame,40,60))) <= 4


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_late_caption_overflow_cannot_replace_prior_movie(video_project, tmp_path):
    from tests.test_spatial_captions import _cue

    spec, metadata = video_project
    spec['overlays'] = [_cue('later', '0:00:00.200', '0:00:00.400', 'This caption is much too long')]
    spec['spatial']['scenes']['interview']['layout']['children'].append({
        'id': 'words', 'type': 'caption', 'layout': {'width': 10, 'height': 1, 'overflow': 'hidden'},
        'style': {'font_size': 24}})
    save_spec(spec, cwd=tmp_path)
    prior = tmp_path / 'prior.mp4'
    prior.write_bytes(b'previous movie')
    result = CliRunner().invoke(cli, ['export', '--out', str(prior), '--quiet', '--video-encoder', 'x264'])
    assert result.exit_code == 1
    assert 'caption_overflow' in json.loads(result.stdout)['error']
    assert prior.read_bytes() == b'previous movie'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_visual_transition_handle_failure_is_addressed_and_atomic(video_project, tmp_path):
    spec, metadata = video_project
    _transition_project(spec, metadata)
    # The timeline's legacy carrier has handles; the visual binding does not.
    spec['spatial']['scenes']['second']['layout']['children'][0]['content']['source'] = {'start': 0, 'end': .4}
    save_spec(spec, cwd=tmp_path)
    prior = tmp_path / 'prior.mp4'
    prior.write_bytes(b'previous movie')
    result = CliRunner().invoke(cli, ['export', '--out', str(prior), '--quiet', '--video-encoder', 'x264'])
    assert result.exit_code == 1
    error = json.loads(result.stdout)['error']
    assert 'insufficient_spatial_transition_handle' in error and 'second/clip' in error
    assert prior.read_bytes() == b'previous movie'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_export_only_renders_declared_caption_elements(video_project, tmp_path):
    from tests.test_spatial_captions import _cue

    spec, metadata = video_project
    spec['overlays'] = [_cue('words', '0:00:00.200', '0:00:00.400', 'CAPTION')]
    layout = spec['spatial']['scenes']['interview']['layout']
    layout['background_color'] = '#000000'
    layout['children'] = []
    save_spec(spec, cwd=tmp_path)
    _invoke(['export', '--out', 'no-captions.mp4', '--quiet', '--video-encoder', 'x264'])
    assert max(_pixels('no-captions.mp4', 2)) <= 4
    spec['spatial']['definitions']['captions'] = {'name': 'Captions', 'inputs': [], 'children': [{
        'id': 'words', 'type': 'caption', 'layout': {'width': 200, 'height': 50},
        'style': {'font_size': 20, 'color': '#ffffff'}}]}
    layout['children'] = [{'instance_id': 'captions', 'definition_id': 'captions'}]
    save_spec(spec, cwd=tmp_path)
    _invoke(['export', '--out', 'captions.mp4', '--quiet', '--video-encoder', 'x264'])
    assert max(_pixels('captions.mp4', 0)) <= 4
    assert max(_pixels('captions.mp4', 2)) >= 240


@pytest.mark.parametrize('command,extra', [('export', []), ('watch', ['--from', '0', '--to', '.4'])])
def test_explicit_source_bypasses_spatial_rendering(video_project, command, extra):
    result = _invoke([command, *extra, '--source', 'camera', '--out', 'source.mp4', '--dry-run'])
    assert 'spatial_render_plan' not in result


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_non_aligned_dissolve_keeps_canonical_blend_weights(video_project, tmp_path):
    spec, metadata = video_project
    _transition_project(spec, metadata)
    spec['composition'][0]['slots'][0]['source_to'] = '0:00:00.550'
    spec['spatial']['scenes']['first']['layout']['children'][0]['content']['source']['end'] = .55
    save_spec(spec, cwd=tmp_path)
    _invoke(['export', '--out', 'fractional.mp4', '--quiet', '--video-encoder', 'x264'])
    for index, incoming_weight in [(3, .25), (4, .75)]:
        frame = _pixels('fractional.mp4', index)
        center = frame[(70*160+100)*3:(70*160+100)*3+3]
        expected = (255*(1-incoming_weight), 0, 255*incoming_weight)
        assert max(abs(a-b) for a,b in zip(center,expected)) <= 10, (index, list(center), expected)


def test_watch_transition_limit_error_identifies_watch(video_project, tmp_path):
    spec, metadata = video_project
    _transition_project(spec, metadata)
    spec['composition'][1]['slots'][0].update({'source_from': '0:00:00.000', 'source_to': '0:00:00.400'})
    save_spec(spec, cwd=tmp_path)
    result = CliRunner().invoke(cli, ['watch', '--from', '.3', '--to', '.5', '--dry-run', '--video-encoder', 'x264'])
    assert result.exit_code == 1
    assert json.loads(result.stdout)['command'] == 'watch'


@pytest.mark.parametrize('field', ['opening_transition', 'closing_transition'])
def test_project_edge_limit_is_checked_before_video_render(video_project, tmp_path, field):
    spec, metadata = video_project
    spec[field] = {'type': 'dip-black', 'duration': 1}
    save_spec(spec, cwd=tmp_path)
    result = CliRunner().invoke(cli, ['export', '--out', 'edge.mp4', '--dry-run', '--video-encoder', 'x264'])
    assert result.exit_code == 1
    assert field in json.loads(result.stdout)['error']
    assert not (tmp_path / 'edge.mp4').exists()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_fractional_cuts_preserve_audio_duration_and_tone_alignment(video_project, tmp_path, monkeypatch):
    spec, metadata = video_project
    # Four contiguous 150 ms scenes alternate between one and two video
    # samples at 10 fps. Audio must retain every authored sample regardless.
    source = tmp_path / 'tones.mkv'
    expression = 'if(lt(t,.15),sin(2*PI*300*t),if(lt(t,.3),sin(2*PI*600*t),if(lt(t,.45),sin(2*PI*1200*t),sin(2*PI*2400*t))))*.1'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', spec['sources'][0]['path'],
        '-f', 'lavfi', '-i', f'aevalsrc={expression}:s=48000:d=2'.replace(',', r'\,'),
        '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'pcm_s16le', str(source)], check=True)
    spec['sources'][0]['path'] = str(source)
    metadata['sources'][0]['path'] = str(source)
    original = spec['composition'][0]
    spec['composition'] = []
    spec['spatial']['scenes'] = {}
    for index in range(4):
        scene = deepcopy(original)
        name = scene['id'] = scene['name'] = f'scene-{index}'
        scene['slots'][0].update({'source_from': f'0:00:00.{index*150:03d}',
                                 'source_to': f'0:00:00.{(index+1)*150:03d}'})
        spec['composition'].append(scene)
        spec['spatial']['scenes'][name] = {'layout': {'background_color': '#112233', 'children': []}}
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    from moviestar import cli as command_module
    encode = command_module.render_layout_video
    audio_durations = []
    def inspect_clip(*args, **kwargs):
        result = encode(*args, **kwargs)
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'stream=duration', '-of', 'json', args[1]]))
        audio_durations.append(float(probe['streams'][0]['duration']))
        return result
    monkeypatch.setattr(command_module, 'render_layout_video', inspect_clip)
    _invoke(['export', '--out', 'tones.mp4', '--quiet', '--video-encoder', 'x264'])
    assert audio_durations == pytest.approx([.15] * 4, abs=.001)
    _invoke(['watch', '--from', '.17', '--to', '.52', '--out', 'part.mp4', '--video-encoder', 'x264'])
    for path, duration, tones in [
        ('tones.mp4', .6, [(.12, 300), (.27, 600), (.32, 1200), (.47, 2400)]),
        ('part.mp4', .35, [(.1, 600), (.15, 1200), (.3, 2400)]),
    ]:
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'stream=duration', '-of', 'json', path]))
        assert float(probe['streams'][0]['duration']) == pytest.approx(duration, abs=.01)
        for start, frequency in tones:
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', path,
                '-t', '0.025', '-vn', '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1'])
            samples = struct.unpack('<' + 'f' * (len(raw) // 4), raw)
            def strength(hz):
                return abs(sum(value * complex(math.cos(2*math.pi*hz*i/48000),
                                                math.sin(2*math.pi*hz*i/48000))
                               for i, value in enumerate(samples)))
            assert max((300, 600, 1200, 2400), key=strength) == frequency, (path, start)



@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('fps,cut,start,end,first,count', [(10, .35, .3, .5, 3, 2), (10, .35, .31, .5, 4, 1),
                                                           (30, .38, .3, .5, 9, 6), (30, .38, .31, .5, 10, 5)])
def test_fractional_dissolve_watch_matches_export_frame_count_and_blends(
        video_project, tmp_path, fps, cut, start, end, first, count):
    spec, metadata = video_project
    metadata['sources'][0]['fps'] = fps
    save_project(metadata, cwd=tmp_path)
    _transition_project(spec, metadata)
    spec['composition'][0]['slots'][0]['source_to'] = f'0:00:{.2+cut:06.3f}'
    spec['spatial']['scenes']['first']['layout']['children'][0]['content']['source']['end'] = .2+cut
    save_spec(spec, cwd=tmp_path)
    _invoke(['export', '--out', 'whole.mp4', '--quiet', '--video-encoder', 'x264'])
    _invoke(['watch', '--from', str(start), '--to', str(end), '--out', 'part.mp4', '--video-encoder', 'x264'])
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=nb_frames', '-of', 'json', 'part.mp4']))
    assert int(probe['streams'][0]['nb_frames']) == count
    offset = (70*160+100)*3
    for index in range(count):
        whole = _pixels('whole.mp4', first+index)[offset:offset+3]
        part = _pixels('part.mp4', index)[offset:offset+3]
        weight = min(1, max(0, ((first+index)/fps - (cut-.1)) / .2))
        expected = (255*(1-weight), 0, 255*weight)
        assert max(abs(a-b) for a,b in zip(part, expected)) <= 10, (index, list(part), expected)
        assert max(abs(a-b) for a,b in zip(part, whole)) <= 8


def test_transition_context_preserves_an_exact_30fps_origin(video_project):
    from moviestar.spatial_video import _context_range

    spec, metadata = video_project
    metadata['sources'][0]['fps'] = 30
    _transition_project(spec, metadata)
    spec['composition'][0]['slots'][0]['source_to'] = '0:00:00.580'
    start, end = _context_range(spec, resolve_project(metadata, spec), .3, .5)
    assert start == 8 / 30
    assert end == .5


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('spatial_first', [True, False])
@pytest.mark.parametrize('scene_duration', [.15, .13])
def test_mixed_fractional_cuts_share_global_frame_count_and_source_clock(
        video_project, tmp_path, spatial_first, scene_duration):
    spec, metadata = video_project
    original = spec['composition'][0]
    spec['composition'] = []
    spec['spatial']['scenes'] = {}
    # Fractional windows share the global 10 fps grid. Test both orders and
    # phases so count-only trimming cannot hide incorrectly sampled footage.
    for index in range(8):
        scene = deepcopy(original)
        name = scene['id'] = scene['name'] = f'scene-{index}'
        scene['slots'][0].update({'source_from': f'0:00:{index*scene_duration:06.3f}',
                                 'source_to': f'0:00:{(index+1)*scene_duration:06.3f}'})
        spec['composition'].append(scene)
        if (index % 2 == 0) == spatial_first:
            spec['spatial']['scenes'][name] = {'layout': {'children': [{
                'id': 'clip', 'type': 'video',
                'content': {'asset_id': 'camera', 'source': {'start': index*scene_duration, 'end': (index+1)*scene_duration}},
                'layout': {'width': '100%', 'height': '100%'}}]}}
    save_spec(spec, cwd=tmp_path)
    _invoke(['export', '--out', 'mixed.mp4', '--quiet', '--video-encoder', 'x264'])
    # The watch starts off-grid and crosses both renderers. The second watch
    # contains just a preset scene within an otherwise spatial project.
    preset_start = scene_duration if spatial_first else 0
    for path, extra, first, count, duration in [
        ('mixed.mp4', None, 0, math.ceil(round(8*scene_duration*10, 9)), 8*scene_duration),
        ('part.mp4', ['--from', '.17', '--to', '.73'], 2, 6, .56),
        ('preset.mp4', ['--from', str(preset_start), '--to', str(preset_start+scene_duration)],
         math.ceil(preset_start*10), 1 if spatial_first else 2, scene_duration),
    ]:
        if extra is not None:
            _invoke(['watch', *extra, '--out', path, '--video-encoder', 'x264'])
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error',
            '-show_entries', 'stream=codec_type,nb_frames,duration', '-of', 'json', path]))
        video, audio = probe['streams']
        assert video['codec_type'] == 'video' and int(video['nb_frames']) == count
        assert float(audio['duration']) == pytest.approx(duration, abs=.01)
        for index in range(count):
            frame = _pixels(path, index)
            offset = (90*320+160)*3
            expected = (20+8*(first+index), 40, 60)
            assert max(abs(a-b) for a,b in zip(frame[offset:offset+3], expected)) <= 4, (path, index)


@pytest.mark.parametrize('sample_offset', [0, .01])
@pytest.mark.parametrize('speed', [.25, .5, 1, 2, 4])
def test_preset_sampling_retains_recurring_30fps_source_timestamps(tmp_path, sample_offset, speed):
    from moviestar.ffmpeg import render_layout_video

    source = tmp_path / 'numbered.nut'
    # NUT preserves the exact 1/30 timestamps that a millisecond source clock
    # would obscure. Rounding frame 2 to 66,667 us must not delay it a frame.
    raw = b''.join(bytes((20+8*(index % 20), 40+8*(index // 20), 60))*(160*90)
                   for index in range(60))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgb24',
        '-video_size', '160x90', '-framerate', '30', '-i', 'pipe:0', '-c:v', 'ffv1',
        '-pix_fmt', 'bgr0', str(source)], input=raw, check=True)
    output = tmp_path / 'preset.mp4'
    render_layout_video([{'path': str(source), 'source_from': 0., 'source_to': .5*speed, 'speed': speed,
        'region': {'x': 0, 'y': 0, 'width': 160, 'height': 90}}], str(output), (160, 90), 12/30,
        output_fps=30, sample_offset=sample_offset, progress_cb=lambda line: None)
    for index in range(12):
        source_frame = math.floor((sample_offset*30+index)*speed + 1e-9)
        expected = (20+8*(source_frame % 20), 40+8*(source_frame // 20), 60)
        assert max(abs(a-b) for a,b in zip(_pixels(output, index)[:3], expected)) <= 4, index
