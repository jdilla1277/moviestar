"""A real frame and its inspected layout share browser geometry and media time."""

import json
from pathlib import Path
import shutil
import struct
import subprocess
import zlib

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available, measure_spatial_layout
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_render import SpatialRenderError, _decode, render_spatial_frame
from moviestar.spec import empty_spec, load_spec, save_spec


def _png(path, width, height):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + b''.join(bytes((255, 255, 0, 0 if x < width // 2 else 255))
                                  for x in range(width)) for _ in range(height))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


@pytest.fixture
def project(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg is required')
    video = tmp_path / 'numbered.mkv'
    # Every source frame has a unique RGB value, retained losslessly by FFV1.
    frames = b''.join(bytes((20 + 8 * number, 40, 60)) * (160 * 90) for number in range(20))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                    '-video_size', '160x90', '-framerate', '10', '-i', 'pipe:0',
                    '-c:v', 'ffv1', '-pix_fmt', 'bgr0', str(video)], input=frames, check=True)
    _png(tmp_path / 'logo.png', 32, 32)
    spec = empty_spec([{'id': 'camera', 'path': str(video)}])
    spec['composition_canvas'] = {'preset': None, 'width': 320, 'height': 180, 'aspect_ratio': '16:9'}
    spec['composition'] = [{'id': 'interview', 'name': 'Interview', 'layout': {'preset': 'single'},
                            'slots': [{'slot': 'main', 'source': 'camera',
                                       'source_from': '0:00:00.000', 'source_to': '0:00:01.500'}]}]
    spec['spatial'] = {
        'assets': {'logo': {'type': 'image', 'path': 'logo.png'}},
        'definitions': {
            'card': {'name': 'SpeakerCard', 'inputs': [{'name': 'clip', 'type': 'video', 'required': True}],
                     'children': [{'id': 'panel', 'type': 'shape', 'content': {},
                         'layout': {'width': '100%', 'height': '100%', 'overflow': 'hidden'},
                         'style': {'background_color': '#ff6600', 'border_radius': 18},
                         'children': [
                             {'id': 'footage', 'type': 'video', 'content': {'input': 'clip'},
                              'layout': {'position': 'absolute', 'left': '10%', 'top': '20%',
                                         'width': '85%', 'height': '70%'},
                              'style': {'object_fit': 'cover', 'border_radius': 10}},
                             {'id': 'badge', 'type': 'text', 'content': {'text': 'SPEAKER'},
                              'layout': {'position': 'absolute', 'left': '35%', 'top': '30%',
                                         'width': '65%', 'height': 24, 'z_index': 3},
                              'style': {'background_color': '#ffe066', 'font_size': 14, 'opacity': 0.7}},
                         ]}]},
            'pair': {'name': 'ListenerPair', 'inputs': [
                {'name': 'left', 'type': 'video', 'required': True},
                {'name': 'right', 'type': 'video', 'required': True}],
                'children': [{'instance_id': name, 'definition_id': 'card',
                              'bindings': {'clip': {'forward': name}},
                              'layout': {'width': '100%', 'height': '100%'}} for name in ('left', 'right')]},
        },
        'scenes': {'interview': {'layout': {
            'display': 'grid', 'columns': '2fr 1fr', 'gap': 12, 'padding': 12,
            'background_color': '#102030', 'children': [
                {'instance_id': 'speaker', 'definition_id': 'card',
                 'bindings': {'clip': {'asset_id': 'camera', 'source': {'start': 0, 'end': 1.2}}}},
                {'instance_id': 'listeners', 'definition_id': 'pair',
                 'layout': {'display': 'grid', 'rows': '1fr 1fr', 'gap': 8},
                 'bindings': {'left': {'asset_id': 'camera', 'source': {'start': 0.2, 'end': 1.4}},
                              'right': {'asset_id': 'camera', 'source': {'start': 0.4, 'end': 1.6}}}},
                {'id': 'logo', 'type': 'image', 'content': {'asset_id': 'logo'},
                 'layout': {'position': 'absolute', 'left': 8, 'top': 8, 'width': 32, 'height': 32, 'z_index': 5}},
                {'id': 'title', 'type': 'text', 'content': {'text': 'An interview\nwith our guests'},
                 'layout': {'position': 'absolute', 'left': '15%', 'top': 12, 'width': '45%', 'z_index': 4},
                 'style': {'font_size': 16, 'color': '#ffffff'}},
            ]}}},
    }
    metadata = {'sources': [{**spec['sources'][0], 'duration': {'seconds': 2}, 'fps': 10,
                             'width': 160, 'height': 90, 'has_audio': False}]}
    return spec, metadata


def _frame(spec, project, at=0.5):
    return compile_spatial_frame(spec, resolve_project(project, spec), at, source_durations={'camera': 2})


def _rgb(path):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-frames:v', '1',
                                    '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('canvas,opacity', [({'width': 320, 'height': 180}, 1),
                                           ({'width': 321, 'height': 181}, 1),
                                           ({'width': 180, 'height': 320}, 0.65)])
def test_hybrid_frame_matches_browser_pixels_and_measured_nested_boxes(project, tmp_path, canvas, opacity):
    spec, metadata = project
    spec['spatial']['scenes']['interview']['layout']['children'][1]['style'] = {'opacity': opacity}
    frame = _frame(spec, metadata)
    output, reference, labels = (tmp_path / name for name in ('rendered.png', 'reference.png', 'layout.png'))
    result = render_spatial_frame(spec, frame, canvas, output_path=output,
                                  project_root=tmp_path, layout_output_path=labels, reference_path=reference)
    actual, expected = _rgb(output), _rgb(reference)
    assert len(actual) == canvas['width'] * canvas['height'] * 3
    assert max(abs(a - b) for a, b in zip(actual, expected)) <= 3
    measured = measure_spatial_layout(frame, canvas)
    assert [(box['path'], box['rect']) for box in result['boxes']] == [
        (box['path'], box['rect']) for box in measured['boxes']]
    assert sorted(item['source_time_s'] for item in result['media']) == [0.5, 0.7, 0.9]
    if opacity == 1:
        for source_frame in (5, 7, 9):
            assert bytes((20 + 8 * source_frame, 40, 60)) * 8 in actual
    logo_pixel = (16 * canvas['width'] + 32) * 3
    assert actual[logo_pixel:logo_pixel + 3] == bytes((255, 255, 0))
    assert result['artifact'] == str(output)
    assert labels.read_bytes().startswith(b'\x89PNG')


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_overlapping_video_stacking_and_group_opacity_match_browser(project, tmp_path):
    spec, metadata = project
    children = spec['spatial']['scenes']['interview']['layout']['children']
    speaker, listeners = children[:2]
    listeners['layout'].update({'position': 'absolute', 'left': 30, 'top': 35,
                                'width': 180, 'height': 130, 'z_index': 8})
    listeners['style'] = {'opacity': 0.55}
    speaker['layout'] = {'z_index': 1}
    children[:2] = [listeners, speaker]  # Paint order must differ from DOM order.
    output, reference = tmp_path / 'overlap.png', tmp_path / 'overlap-reference.png'
    render_spatial_frame(spec, _frame(spec, metadata), {'width': 320, 'height': 180},
                         output_path=output, project_root=tmp_path, reference_path=reference)
    assert max(abs(a - b) for a, b in zip(_rgb(output), _rgb(reference))) <= 3


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_patterned_native_video_matches_browser_through_graphics_and_opacity(project, tmp_path):
    spec, metadata = project
    video = tmp_path / 'patterned.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi',
                    '-i', 'testsrc2=size=160x90:rate=10:duration=2', '-c:v', 'ffv1', str(video)], check=True)
    spec['sources'][0]['path'] = str(video)
    spec['spatial']['scenes']['interview']['layout'] = {'background_color': '#102030', 'children': [{
        'id': 'panel', 'type': 'shape', 'content': {},
        'layout': {'position': 'absolute', 'left': 20, 'top': 20, 'width': 180, 'height': 110,
                   'overflow': 'hidden'},
        'style': {'background_color': '#ff6600', 'border_radius': 24, 'opacity': 0.65},
        'children': [
            {'id': 'footage', 'type': 'video', 'content': {'asset_id': 'camera'},
             'layout': {'position': 'absolute', 'left': 10, 'top': 10, 'width': 160, 'height': 90}},
            {'id': 'badge', 'type': 'text', 'content': {'text': 'FOREGROUND'},
             'layout': {'position': 'absolute', 'left': 25, 'top': 40, 'z_index': 2},
             'style': {'background_color': '#ffffff', 'opacity': 0.6}},
        ],
    }]}
    output, reference = tmp_path / 'patterned.png', tmp_path / 'patterned-reference.png'
    render_spatial_frame(spec, _frame(spec, metadata), {'width': 320, 'height': 180},
                         output_path=output, project_root=tmp_path, reference_path=reference)
    assert max(abs(a - b) for a, b in zip(_rgb(output), _rgb(reference))) <= 3


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('fit,expected', [('contain', {'x': 20, 'y': 42, 'width': 100, 'height': 56}),
                                        ('cover', {'x': -19, 'y': 20, 'width': 178, 'height': 100})])
def test_video_fit_keeps_source_orientation_and_letterboxes_transparent(project, tmp_path, fit, expected):
    spec, metadata = project
    video = tmp_path / 'two-tone.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=80x90:r=10:d=2',
                    '-f', 'lavfi', '-i', 'color=c=blue:s=80x90:r=10:d=2',
                    '-filter_complex', 'hstack', '-c:v', 'ffv1', str(video)], check=True)
    spec['sources'][0]['path'] = str(video)
    spec['spatial']['scenes']['interview']['layout'] = {'background_color': '#00ff00', 'children': [{
        'id': 'footage', 'type': 'video', 'content': {'asset_id': 'camera', 'source': {'start': 0, 'end': 1.5}},
        'layout': {'position': 'absolute', 'left': 20, 'top': 20, 'width': 100, 'height': 100},
        'style': {'object_fit': fit},
    }]}
    output = tmp_path / f'{fit}.png'
    result = render_spatial_frame(spec, _frame(spec, metadata), {'width': 320, 'height': 180},
                                  output_path=output, project_root=tmp_path)
    assert result['media'][0]['rect'] == expected
    pixels = _rgb(output)
    def pixel(x, y):
        return pixels[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3]
    assert pixel(40, 60)[0] > 240 and pixel(40, 60)[2] < 10
    assert pixel(100, 60)[2] > 240 and pixel(100, 60)[0] < 10
    if fit == 'contain':
        assert pixel(30, 30) == bytes((0, 255, 0))


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_ended_videos_are_transparent_and_do_not_loop(project, tmp_path):
    spec, metadata = project
    output, reference = tmp_path / 'ended.png', tmp_path / 'ended-reference.png'
    result = render_spatial_frame(spec, _frame(spec, metadata, 1.3),
                                  {'width': 320, 'height': 180}, output_path=output,
                                  project_root=tmp_path, reference_path=reference)
    assert result['media'] == []
    assert _rgb(output) == _rgb(reference)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('at,expected_frame', [(0.05, 6), (1.35, 19)])
def test_preview_samples_displayed_source_frame_between_timestamps(project, tmp_path, at, expected_frame):
    spec, metadata = project
    spec['spatial']['scenes']['interview']['layout'] = {'children': [{
        'id': 'footage', 'type': 'video',
        'content': {'asset_id': 'camera', 'source': {'start': 0.6, 'end': 2}},
        'layout': {'width': 160, 'height': 90},
    }]}
    output = tmp_path / 'between.png'
    frame = _frame(spec, metadata, at)
    assert frame['children'][0]['media']['visible'] is True
    result = render_spatial_frame(spec, frame, {'width': 160, 'height': 90},
                                  output_path=output, project_root=tmp_path)
    assert result['media'][0]['source_time_s'] == pytest.approx(0.6 + at)
    expected = bytes((20 + 8 * expected_frame, 40, 60)) * (160 * 90)
    assert max(abs(a - b) for a, b in zip(_rgb(output), expected)) <= 3


@pytest.mark.parametrize('offset', [0, 5])
@pytest.mark.parametrize('at,expected_frame', [(0.05, 1), (0.65, 19)])
def test_decode_uses_presentation_timestamps_for_fractional_rate_and_reordered_frames(
        project, tmp_path, offset, at, expected_frame):
    video = tmp_path / 'fractional.mp4'
    # Encode 20 distinct frames on an exact fractional-rate clock. Lossy x264
    # enables real B-frames; compare against an independent full decode below.
    frames = b''.join(bytes((20 + 8 * number, 40, 60)) * (160 * 90) for number in range(20))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                    '-video_size', '160x90', '-framerate', '30000/1001', '-i', 'pipe:0',
                    '-c:v', 'libx264rgb', '-crf', '12', '-bf', '2', '-g', '20',
                    '-output_ts_offset', str(offset), str(video)], input=frames, check=True)
    picture_types = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                      '-show_entries', 'frame=pict_type', '-of', 'csv=p=0', str(video)])
    assert b'B' in picture_types
    reference = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(video),
                      '-fps_mode', 'vfr', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
    frame_bytes = 160 * 90 * 3
    assert len(reference) == 20 * frame_bytes
    output = tmp_path / 'fractional.png'
    _decode(video, output, source_time=at)
    assert _rgb(output) == reference[expected_frame * frame_bytes:(expected_frame + 1) * frame_bytes]


def test_decode_holds_preceding_vfr_frame_in_a_timestamp_gap(project, tmp_path):
    spec, _ = project
    video = tmp_path / 'vfr.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', spec['sources'][0]['path'],
                    '-vf', 'setpts=PTS+gte(N\\,10)/TB', '-fps_mode', 'vfr',
                    '-c:v', 'ffv1', '-pix_fmt', 'bgr0', str(video)], check=True)
    output = tmp_path / 'held.png'
    _decode(video, output, source_time=1.5)
    assert _rgb(output) == bytes((20 + 8 * 9, 40, 60)) * (160 * 90)


@pytest.mark.parametrize('at,expected_frame', [(0.05, 0), (0.5, 5), (1.5, 15),
                                             (1.95, 19), (2.1, None)])
def test_decode_recovers_displayed_mpegts_frame_when_backward_seek_overshoots(
        project, tmp_path, at, expected_frame):
    video = tmp_path / 'reordered.ts'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi',
                    '-i', 'testsrc2=s=160x90:r=10:d=2', '-c:v', 'libx264',
                    '-g', '10', '-bf', '2', str(video)], check=True)
    reference = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(video),
                      '-fps_mode', 'vfr', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
    frame_bytes = 160 * 90 * 3
    assert len(reference) == 20 * frame_bytes
    output = tmp_path / 'transport.png'
    if expected_frame is None:
        with pytest.raises(SpatialRenderError, match='no source frame at 2.1s'):
            _decode(video, output, source_time=at)
        assert not output.exists()
        return
    _decode(video, output, source_time=at)
    assert _rgb(output) == reference[expected_frame * frame_bytes:(expected_frame + 1) * frame_bytes]


def test_decode_does_not_extend_final_frame_past_source_end(project, tmp_path):
    spec, _ = project
    with pytest.raises(SpatialRenderError, match='no source frame at 2.1s'):
        _decode(Path(spec['sources'][0]['path']), tmp_path / 'past-end.png', source_time=2.1)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('kind', ['video', 'image'])
def test_static_media_paints_inside_its_measured_content_box(project, tmp_path, kind):
    spec, metadata = project
    spec['spatial']['scenes']['interview']['layout'] = {
        'display': 'flex', 'padding': 20, 'background_color': '#00ff00', 'children': [
            {'id': 'spacer', 'type': 'shape', 'layout': {'width': 50, 'height': 90}},
            {'id': 'media', 'type': kind,
             'content': {'asset_id': 'camera' if kind == 'video' else 'logo'},
             'layout': {'position': 'static', 'width': 160, 'height': 90,
                        'padding': 4},
             'style': {'border_width': 2, 'border_style': 'solid', 'border_color': '#ffffff',
                       'background_color': '#102030'}},
        ]}
    frame, canvas = _frame(spec, metadata), {'width': 320, 'height': 180}
    result = render_spatial_frame(spec, frame, canvas, output_path=tmp_path / 'static.png',
                                  project_root=tmp_path)
    measured = measure_spatial_layout(frame, canvas)
    assert [(box['path'], box['rect']) for box in result['boxes']] == [
        (box['path'], box['rect']) for box in measured['boxes']]
    assert result['boxes'][1]['rect'] == {'x': 70, 'y': 20, 'width': 160, 'height': 90}
    pixels = _rgb(tmp_path / 'static.png')
    def pixel(x, y):
        return pixels[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3]
    assert pixel(10, 10) == bytes((0, 255, 0))
    assert pixel(190, 50) == (bytes((60, 40, 60)) if kind == 'video' else bytes((255, 255, 0)))
    assert pixel(72, 50) == bytes((16, 32, 48))  # authored background fills the padding
    if kind == 'image':
        assert pixel(90, 50) == bytes((16, 32, 48))  # alpha exposes the media element's background
    if kind == 'video':
        assert result['media'][0]['rect'] == {'x': 76, 'y': 26, 'width': 148, 'height': 78}


def test_unready_frame_leaves_existing_output_untouched(project, tmp_path):
    spec, metadata = project
    del spec['spatial']['scenes']['interview']['layout']['children'][0]['bindings']['clip']
    output = tmp_path / 'previous.png'
    output.write_bytes(b'previous result')
    with pytest.raises(SpatialRenderError, match='missing_binding.*interview/speaker'):
        render_spatial_frame(spec, _frame(spec, metadata), {'width': 320, 'height': 180},
                              output_path=output, project_root=tmp_path)
    assert output.read_bytes() == b'previous result'


def test_preview_rejects_missing_caption_track_and_asset_output_paths(project, tmp_path):
    spec, metadata = project
    frame = _frame(spec, metadata)
    with pytest.raises(SpatialRenderError, match='cannot overwrite a project asset'):
        render_spatial_frame(spec, frame, {'width': 320, 'height': 180},
                              output_path=tmp_path / 'logo.png', project_root=tmp_path)
    spec['spatial']['scenes']['interview']['layout']['children'].append({'id': 'captions', 'type': 'caption'})
    with pytest.raises(SpatialRenderError, match='unknown_caption_track.*interview/captions'):
        render_spatial_frame(spec, _frame(spec, metadata), {'width': 320, 'height': 180},
                              output_path=tmp_path / 'captions.png', project_root=tmp_path)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('at,text', [(0.5, 'First caption'), (1, 'Two lines\nof captions')])
def test_caption_over_video_matches_browser_paint(project, tmp_path, at, text):
    spec, metadata = project
    spec['overlays'] = [
        {'id': identity, 'kind': 'caption', 'track': 'captions', 'text': words,
         'from': start, 'to': end, 'position': {}, 'style': {}}
        for identity, start, end, words in [
            ('first', '0:00:00.000', '0:00:01.000', 'First caption'),
            ('second', '0:00:01.000', '0:00:01.500', 'Two lines\nof captions')]]
    spec['spatial']['definitions']['card']['children'][0]['children'].append({
        'id': 'captions', 'type': 'caption', 'content': {'track': 'captions'},
        'layout': {'position': 'absolute', 'left': 6, 'right': 6, 'bottom': 4,
                   'height': 36, 'z_index': 8},
        'style': {'font_size': 10, 'line_height': 1.4, 'color': '#ffffff',
                  'background_color': '#102030', 'opacity': 0.8}})
    frame = _frame(spec, metadata, at)
    output, reference = tmp_path / 'captions.png', tmp_path / 'reference.png'
    result = render_spatial_frame(spec, frame, {'width': 320, 'height': 180}, output_path=output,
                                 project_root=tmp_path, reference_path=reference)
    assert max(abs(a - b) for a, b in zip(_rgb(output), _rgb(reference))) <= 3
    caption_boxes = [box for box in result['boxes'] if box['kind'] == 'caption']
    assert len(caption_boxes) == 3
    assert all(box['caption']['text'] == text for box in caption_boxes)


def test_preview_rejects_video_alpha_before_painting(project, tmp_path):
    spec, metadata = project
    alpha = tmp_path / 'alpha.mov'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi',
                    '-i', 'color=c=red@0.5:s=16x16:d=2:r=10,format=argb',
                    '-c:v', 'qtrle', str(alpha)], check=True)
    spec['sources'][0]['path'] = str(alpha)
    with pytest.raises(SpatialRenderError, match='video alpha is not supported'):
        render_spatial_frame(spec, _frame(spec, metadata), {'width': 320, 'height': 180},
                              output_path=tmp_path / 'alpha.png', project_root=tmp_path)


def test_preview_rejects_webm_alpha_metadata_before_decoding(project, tmp_path):
    spec, metadata = project
    alpha = tmp_path / 'alpha.webm'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi',
                    '-i', 'color=c=red@0:s=16x16:d=2:r=10,format=yuva420p',
                    '-c:v', 'libvpx-vp9', '-auto-alt-ref', '0', str(alpha)], check=True)
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                        '-show_entries', 'stream=pix_fmt:stream_tags', '-of', 'json', str(alpha)]))
    assert probe['streams'][0]['pix_fmt'] == 'yuv420p'
    assert {key.lower(): value for key, value in probe['streams'][0]['tags'].items()}['alpha_mode'] == '1'
    spec['sources'][0]['path'] = str(alpha)
    output = tmp_path / 'previous.png'
    output.write_bytes(b'previous result')
    with pytest.raises(SpatialRenderError, match='interview/speaker/panel/footage.*video alpha is not supported'):
        render_spatial_frame(spec, _frame(spec, metadata), {'width': 320, 'height': 180},
                              output_path=output, project_root=tmp_path)
    assert output.read_bytes() == b'previous result'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_preview_cli_writes_real_pixels_and_stays_read_only(project, tmp_path, monkeypatch):
    spec, metadata = project
    save_spec(spec, cwd=tmp_path)
    save_project(metadata, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ['composition', 'model', 'preview', '--at', '0:00:00.500',
                                      '--canvas', '320x180', '--out', 'preview.png', '--layout-out', 'boxes.png'])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data['status'] == 'rendered'
    assert data['renders_assets'] is True
    assert Path(data['preview']['artifact']).is_file()
    assert load_spec(cwd=tmp_path) == spec
    (tmp_path / 'logo.png').unlink()
    before = (tmp_path / 'preview.png').read_bytes()
    failure = CliRunner().invoke(cli, ['composition', 'model', 'preview', '--at', '0:00:00.500',
                                       '--out', 'preview.png'])
    assert failure.exit_code == 1
    assert json.loads(failure.output)['command'] == 'composition model preview'
    assert 'interview/logo' in json.loads(failure.output)['error']
    assert (tmp_path / 'preview.png').read_bytes() == before
