"""Caption elements use existing result-time cues and browser text layout."""

from copy import deepcopy
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available, measure_spatial_layout
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_render import SpatialRenderError, render_spatial_frame
from moviestar.spec import SpecValidationError, empty_spec, save_spec, validate_spec


def _cue(identity, start, end, text, track='captions'):
    return {'id': identity, 'kind': 'caption', 'track': track, 'from': start,
            'to': end, 'text': text, 'position': {'preset': 'bottom'},
            'style': {'preset': 'plain'}}


@pytest.fixture
def caption_project():
    spec = empty_spec([{'id': 'camera', 'path': '/tmp/spatial-caption-camera.mp4'}])
    spec['composition_canvas'] = {'preset': None, 'width': 640, 'height': 360, 'aspect_ratio': '16:9'}
    spec['composition'] = [
        {'id': name, 'name': name, 'layout': {'preset': 'single'},
         'slots': [{'slot': 'main', 'source': 'camera',
                    'source_from': '0:00:00.000', 'source_to': '0:00:02.000'}]}
        for name in ('first', 'second')]
    spec['spatial'] = {'definitions': {'subtitles': {
        'name': 'Subtitles', 'inputs': [{'name': 'track', 'type': 'caption', 'required': True}],
        'children': [{'id': 'words', 'type': 'caption', 'content': {'input': 'track'},
                      'layout': {'width': '100%', 'height': 88, 'padding': 8},
                      'style': {'font_size': 24, 'line_height': 1.4, 'font_family': 'monospace',
                                'color': '#ffffff', 'background_color': '#182838'}}]}},
        'scenes': {name: {'layout': {'background_color': '#07111d', 'children': [
            {'instance_id': 'subtitles', 'definition_id': 'subtitles',
             'bindings': {'track': {'track': 'captions'}},
             'layout': {'position': 'absolute', 'left': 16, 'right': 16, 'bottom': 16}}]}}
                   for name in ('first', 'second')}}
    spec['overlays'] = [
        _cue('one', '0:00:00.250', '0:00:01.000', 'First line'),
        _cue('two', '0:00:01.000', '0:00:01.750', 'Second line\nwith two rows'),
        _cue('three', '0:00:02.000', '0:00:04.000', 'Next scene'),
        _cue('translation', '0:00:00.000', '0:00:04.000', 'Another language', 'translated')]
    project = {'sources': [{**spec['sources'][0], 'duration': {'seconds': 4},
                            'fps': 10, 'width': 640, 'height': 360, 'has_audio': False}]}
    return spec, project


def _frame(spec, project, at):
    return compile_spatial_frame(spec, resolve_project(project, spec), at)


def _caption(frame):
    return frame['children'][0]['children'][0]


@pytest.mark.parametrize('at,ids,text', [
    (0, [], ''), (0.25, ['one'], 'First line'), (0.999, ['one'], 'First line'),
    (1, ['two'], 'Second line\nwith two rows'), (1.75, [], ''),
    (2, ['three'], 'Next scene'), (4, ['three'], 'Next scene')])
def test_caption_elements_select_half_open_result_time_cues(caption_project, at, ids, text):
    spec, project = caption_project
    before = deepcopy(spec)
    validate_spec(spec)
    frame = _frame(spec, project, at)
    caption = _caption(frame)['caption']
    assert caption['track'] == 'captions'
    assert [cue['id'] for cue in caption['cues']] == ids
    assert caption['text'] == text
    assert caption['visible'] is bool(ids)
    assert frame['diagnostics'] == []
    assert spec == before


def test_caption_track_binding_and_overlapping_cues_are_explicit(caption_project):
    spec, project = caption_project
    spec['spatial']['scenes']['second']['layout']['children'][0]['bindings']['track'] = {'track': 'translated'}
    assert _caption(_frame(spec, project, 2.2))['caption']['text'] == 'Another language'
    spec['overlays'].insert(0, _cue('overlap', '0:00:00.500', '0:00:01.000', 'Other speaker'))
    caption = _caption(_frame(spec, project, 0.6))['caption']
    assert [cue['id'] for cue in caption['cues']] == ['one', 'overlap']
    assert caption['text'] == 'First line\nOther speaker'


def test_caption_default_track_empty_track_and_missing_track_diagnostics(caption_project):
    spec, project = caption_project
    spec['spatial']['scenes']['first']['layout']['children'] = [{'id': 'words', 'type': 'caption'}]
    assert _frame(spec, project, 0.5)['children'][0]['caption']['text'] == 'First line'
    spec['overlays'] = []
    missing = _frame(spec, project, 0.5)
    assert missing['diagnostics'] == [{'code': 'unknown_caption_track', 'path': ['first', 'words'],
                                       'track': 'captions'}]
    spec['captions'] = [{'track': 'captions'}]  # A known recipe may have no cues.
    assert _frame(spec, project, 0.5)['diagnostics'] == []


@pytest.mark.parametrize('content', [{'track': ''}, {'track': 7}, {'track': 'captions', 'text': 'oops'}])
def test_caption_binding_rejects_malformed_track(caption_project, content):
    spec, _ = caption_project
    spec['spatial']['scenes']['first']['layout']['children'][0]['bindings']['track'] = content
    with pytest.raises(SpecValidationError, match='caption.*track'):
        validate_spec(spec)


def test_scene_without_caption_element_never_injects_captions(caption_project):
    spec, project = caption_project
    spec['spatial']['scenes']['first']['layout']['children'] = []
    frame = _frame(spec, project, 0.5)
    assert frame['children'] == []
    assert frame['diagnostics'] == []


def test_caption_empty_binding_and_forwarded_default(caption_project):
    spec, project = caption_project
    instance = spec['spatial']['scenes']['first']['layout']['children'][0]
    instance['bindings']['track'] = {'empty': True}
    definition = spec['spatial']['definitions']['subtitles']
    definition['inputs'][0]['required'] = False
    assert _caption(_frame(spec, project, 0.5))['caption'] == {
        'track': None, 'visible': False, 'text': '', 'cues': []}
    definition['inputs'][0]['default'] = {'track': 'translated'}
    instance['bindings'] = {}
    assert _caption(_frame(spec, project, 0.5))['caption']['text'] == 'Another language'
    spec['spatial']['definitions']['wrapper'] = {
        'name': 'Wrapper', 'inputs': [{'name': 'captions', 'type': 'caption',
                                     'default': {'track': 'translated'}}],
        'children': [{'instance_id': 'nested', 'definition_id': 'subtitles',
                      'bindings': {'track': {'forward': 'captions'}}}]}
    instance['definition_id'] = 'wrapper'
    validate_spec(spec)
    nested = _frame(spec, project, 0.5)['children'][0]['children'][0]['children'][0]
    assert nested['caption']['text'] == 'Another language'
    assert nested['occurrence_path'] == ['first', 'subtitles', 'nested', 'words']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('canvas', [{'width': 640, 'height': 360}, {'width': 360, 'height': 640}])
def test_caption_render_and_layout_share_wrapping_and_geometry(caption_project, tmp_path, canvas):
    spec, project = caption_project
    boxes_at_times = []
    for at, lines in [(0, 0), (0.5, 1), (1, 2)]:
        frame = _frame(spec, project, at)
        rendered = render_spatial_frame(spec, frame, canvas, output_path=tmp_path / f'{at}.png',
                                       layout_output_path=tmp_path / f'{at}-boxes.png',
                                       reference_path=tmp_path / f'{at}-reference.png')
        measured = measure_spatial_layout(frame, canvas)
        assert rendered['boxes'] == measured['boxes']
        box = rendered['boxes'][1]
        assert box['kind'] == 'caption'
        assert box['caption'] == _caption(frame)['caption']
        assert len(box['text_lines']) == lines
        assert box['overflow'] is False
        assert rendered['diagnostics'] == []
        boxes_at_times.append(box['rect'])
    assert boxes_at_times[0] == boxes_at_times[1] == boxes_at_times[2]
    assert (tmp_path / '0.5.png').read_bytes() != (tmp_path / '1.png').read_bytes()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_caption_overflow_is_inspectable_and_does_not_replace_preview(caption_project, tmp_path):
    spec, project = caption_project
    node = spec['spatial']['definitions']['subtitles']['children'][0]
    node['layout'].update({'width': 100, 'height': 30, 'overflow': 'hidden'})
    frame = _frame(spec, project, 1)
    measured = measure_spatial_layout(frame, {'width': 640, 'height': 360})
    assert measured['boxes'][1]['overflow'] is True
    assert measured['diagnostics'][0]['code'] == 'caption_overflow'
    assert measured['diagnostics'][0]['path'] == ['first', 'subtitles', 'words']
    output = tmp_path / 'previous.png'
    output.write_bytes(b'previous preview')
    with pytest.raises(SpatialRenderError, match='caption_overflow.*first/subtitles/words'):
        render_spatial_frame(spec, frame, {'width': 640, 'height': 360}, output_path=output)
    assert output.read_bytes() == b'previous preview'


def test_caption_frame_cli_reports_cues_without_writing_project(caption_project, tmp_path, monkeypatch):
    spec, project = caption_project
    save_spec(spec, cwd=tmp_path)
    save_project(project, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / 'moviestar' / 'spec.json'
    before = path.read_bytes()
    result = CliRunner().invoke(cli, ['composition', 'model', 'frame', '--at', '0:00:01.000'])
    assert result.exit_code == 0, result.output
    assert _caption(json.loads(result.output)['frame'])['caption']['text'] == 'Second line\nwith two rows'
    assert path.read_bytes() == before


@pytest.mark.parametrize('command', ['frame', 'layout', 'preview'])
def test_caption_cli_refreshes_live_cues_after_scene_reorder(
        caption_project, tmp_path, monkeypatch, command):
    if command != 'frame' and not browser_available():
        pytest.skip('Chrome and Node are required')
    from tests.conftest import inject_synthetic_transcript, make_segment, make_word
    from moviestar.spec import load_spec

    spec, project = caption_project
    project['sources'][0]['has_audio'] = True
    for scene in spec['composition']:
        scene['audio_from'] = 'camera'
    spec['composition'][1]['slots'][0].update({'source_from': '0:00:02.000', 'source_to': '0:00:04.000'})
    save_spec(spec, cwd=tmp_path)
    save_project(project, cwd=tmp_path)
    inject_synthetic_transcript(tmp_path, [make_word('Opening', 0.2, 0.8), make_word('Closing', 2.2, 2.8)],
                                [make_segment('Opening', 0.2, 0.8), make_segment('Closing', 2.2, 2.8)])
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    generated = runner.invoke(cli, ['captions', 'generate', '--highlight', 'none'])
    assert generated.exit_code == 0, generated.output
    spec = load_spec(cwd=tmp_path)
    spec['composition'].reverse()
    save_spec(spec, cwd=tmp_path)
    path = tmp_path / 'moviestar' / 'spec.json'
    before = path.read_bytes()
    args = ['composition', 'model', command, '--at', '0:00:00.500']
    if command == 'preview':
        args += ['--out', str(tmp_path / 'preview.png')]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    frame = json.loads(result.output)['frame']
    assert frame['scene_id'] == 'second'
    assert _caption(frame)['caption']['text'] == 'Closing'
    assert frame['diagnostics'] == []
    assert path.read_bytes() == before


def test_caption_cli_reports_failed_refresh_only_for_placed_track(caption_project, tmp_path, monkeypatch):
    spec, project = caption_project
    spec['captions'] = [{'track': 'captions', 'style': 'social-bold',
                         'highlight': {'mode': 'none'}, 'cache_fingerprint': 'old'}]
    save_spec(spec, cwd=tmp_path)
    save_project(project, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    args = ['composition', 'model', 'frame', '--at', '0:00:00.500']
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    frame = json.loads(result.output)['frame']
    assert frame['diagnostics'] == [{'code': 'stale_caption_track',
        'path': ['first', 'subtitles', 'words'], 'track': 'captions',
        'reason': 'live caption cues could not be refreshed; check the transcript and audio routing'}]
    spec['spatial']['scenes']['first']['layout']['children'] = []
    save_spec(spec, cwd=tmp_path)
    import moviestar.cli as cli_module

    def unexpected_refresh(*args, **kwargs):
        raise AssertionError('an unplaced caption track must not be derived')

    monkeypatch.setattr(cli_module, '_derive_caption_records', unexpected_refresh)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['frame']['diagnostics'] == []
