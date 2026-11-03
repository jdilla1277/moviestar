"""Saving design is read-only with respect to the existing rendered scene."""

import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_render import render_spatial_frame
from moviestar.spec import load_spec, save_spec
from tests.test_spatial_captions import _cue
from tests.test_spatial_render import project
from tests.test_spatial_saved_layouts import _content_references


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
@pytest.mark.parametrize('canvas', [{'width':640,'height':360}, {'width':360,'height':640}])
def test_saving_nested_layout_preserves_pixels_geometry_motion_and_content(project, tmp_path, monkeypatch, canvas):
    spec, metadata = project
    spec['overlays'] = [_cue('words','0:00:00.000','0:00:01.500','A live caption')]
    spec['spatial']['scenes']['interview']['layout']['children'].append({
        'id':'caption','type':'caption','content':{'track':'captions'},
        'layout':{'position':'absolute','left':'10%','bottom':8,'width':'80%','height':40},
        'style':{'color':'white','font_size':12}})
    spec['spatial']['scenes']['interview']['layout']['children'][0]['motion'] = {
        'translate_y':[{'at':0,'value':0},{'at':1,'value':6}]}
    save_spec(spec, cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    before = load_spec()
    frame = compile_spatial_frame(before, resolve_project(metadata, before), .5, source_durations={'camera':2})
    rendered = render_spatial_frame(before, frame, canvas, output_path=tmp_path/'before.png',
        layout_output_path=tmp_path/'before-layout.png', project_root=tmp_path)
    # Catalog save must not launch a renderer or decode source media.
    with monkeypatch.context() as context:
        def unexpected(*args, **kwargs):
            raise AssertionError('saving a layout must not run subprocesses')
        context.setattr('subprocess.run', unexpected)
        result = CliRunner().invoke(cli, ['composition','model','layouts','save','mine','--scene','interview'])
    assert result.exit_code == 0, result.output
    after = load_spec()
    assert after['composition'] == before['composition']
    assert after['spatial']['scenes'] == before['spatial']['scenes']
    assert after['spatial']['definitions'] == before['spatial']['definitions']
    next_frame = compile_spatial_frame(after, resolve_project(metadata, after), .5, source_durations={'camera':2})
    assert next_frame == frame
    next_render = render_spatial_frame(after, next_frame, canvas, output_path=tmp_path/'after.png',
        layout_output_path=tmp_path/'after-layout.png', project_root=tmp_path)
    assert next_render['boxes'] == rendered['boxes']
    assert (tmp_path/'before.png').read_bytes() == (tmp_path/'after.png').read_bytes()
    assert (tmp_path/'before-layout.png').read_bytes() == (tmp_path/'after-layout.png').read_bytes()
    shown = CliRunner().invoke(cli, ['composition','model','layouts','show','saved:mine'])
    assert shown.exit_code == 0, shown.output
    assert not list(_content_references(json.loads(shown.output)))
