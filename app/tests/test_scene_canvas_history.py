"""Scene edits and format changes form one valid, reversible CLI journal."""
from copy import deepcopy
import json
import shutil
import subprocess

from click.testing import CliRunner
import pytest

from moviestar.cli import cli
from moviestar.spec import append_revision, load_spec, save_spec, validate_spec
from tests.test_live_browser_preview import live_project


@pytest.fixture(scope='module')
def camera_media(tmp_path_factory):
    path = tmp_path_factory.mktemp('canvas-media') / 'camera.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'color=blue:size=640x360:rate=30:duration=8', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=48000:duration=8', '-c:v', 'libx264',
        '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(path)], check=True)
    return path


@pytest.fixture
def canvas_project(live_project, camera_media):
    spec, project, root = live_project
    shutil.copyfile(camera_media, root / 'camera.mp4')
    return spec, project, root


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def scene_file(root, spec, canvas, *, edit=None):
    scene = spec['composition'][0]
    slot = scene['slots'][0]
    document = {'canvas': canvas, 'scenes': [{
        'id': scene['id'], 'name': 'Renamed scene' if edit == 'rename' else scene['name'],
        'layout': 'single', 'audio_from': 'camera', 'slots': [{
            'id': slot.get('id', 'main'), 'slot': 'main', 'source': 'camera',
            'from': slot['source_from'],
            'to': '0:00:05.500' if edit == 'trim' else slot['source_to'],
        }],
    }]}
    path = root / 'scenes.json'
    path.write_text(json.dumps(document))
    return str(path)


@pytest.mark.parametrize('depth', [0, 20, 50])
@pytest.mark.parametrize('canvas,other,orientation', [
    ('640x360', '360x640', 'horizontal'),
    ('360x640', '640x360', 'vertical'),
])
@pytest.mark.parametrize('edit', ['rename', 'trim'])
def test_scene_edit_then_canvas_change_and_undo(canvas_project, monkeypatch, depth, canvas, other, orientation, edit):
    spec, _, root = canvas_project
    monkeypatch.chdir(root)
    width, height = map(int, canvas.split('x'))
    spec['composition_canvas'].update(width=width, height=height,
        aspect_ratio='16:9' if width > height else '9:16')
    spec['composition'][0]['layout']['orientation'] = orientation
    spec['composition'][0]['slots'][0]['id'] = 'main'
    spec['composition_authored_as'] = None
    # A local customization and existing caption track must survive both steps.
    spec['spatial']['scenes']['scene']['layout']['children'][0]['customizations'] = [
        {'op': 'property_set', 'path': ['video'], 'property': 'style.border_color', 'value': '#00d4ff'}]
    for index in range(depth):
        after = deepcopy(spec)
        after['spatial']['scenes']['scene']['layout']['children'][1]['content']['text'] = f'Title {index}'
        spec = append_revision(spec, after, f'Title {index}')
    save_spec(spec, cwd=root, record=False)
    before = load_spec(root)
    invoke('scenes', 'set', scene_file(root, before, canvas, edit=edit))
    edited = load_spec(root)
    path = scene_file(root, edited, other)
    original_bytes = (root / 'moviestar/spec.json').read_bytes()
    invoke('scenes', 'set', path, '--dry-run')
    assert (root / 'moviestar/spec.json').read_bytes() == original_bytes
    invoke('scenes', 'set', path)
    changed = load_spec(root)
    validate_spec(changed)
    assert changed['composition_canvas']['width'] == int(other.split('x')[0])
    assert changed['spatial'] == before['spatial']
    assert changed['captions'] == before['captions']
    assert changed['overlays'] == before['overlays']
    assert len(changed['revisions']) == min(depth + 2, 50)
    assert changed['revisions'][-2] == edited['revisions'][-1]
    assert changed['revisions'][-1]['command'] == 'scenes set'
    invoke('undo')
    restored_edit = load_spec(root)
    assert {k: v for k, v in restored_edit.items() if k != 'revisions'} == {
        k: v for k, v in edited.items() if k != 'revisions'}
    invoke('undo')
    restored = load_spec(root)
    assert {k: v for k, v in restored.items() if k != 'revisions'} == {
        k: v for k, v in before.items() if k != 'revisions'}
    assert restored['revisions'] == before['revisions'][max(0, depth + 2 - 50):]


def test_invalid_canvas_change_does_not_write_or_erase_history(canvas_project, monkeypatch):
    _, _, root = canvas_project
    monkeypatch.chdir(root)
    invoke('scenes', 'set', scene_file(root, load_spec(root), '640x360', edit='rename'))
    before = (root / 'moviestar/spec.json').read_bytes()
    path = scene_file(root, load_spec(root), '0x360')
    result = CliRunner().invoke(cli, ['scenes', 'set', path])
    assert result.exit_code != 0
    assert (root / 'moviestar/spec.json').read_bytes() == before


@pytest.mark.parametrize('index', [0, 25, 49])
def test_canvas_change_rejects_corrupt_history_even_at_fifo_boundary(canvas_project, monkeypatch, index):
    spec, _, root = canvas_project
    monkeypatch.chdir(root)
    for number in range(50):
        after = deepcopy(spec)
        after['spatial']['scenes']['scene']['layout']['children'][1]['content']['text'] = str(number)
        spec = append_revision(spec, after, 'title')
    path = scene_file(root, spec, '360x640')
    spec['revisions'][index]['changed']['sources'] = [{'id': 'broken'}]
    saved = root / 'moviestar/spec.json'
    saved.write_text(json.dumps(spec))
    before = saved.read_bytes()
    result = CliRunner().invoke(cli, ['scenes', 'set', path])
    assert result.exit_code != 0
    assert 'invalid prior state' in result.output
    assert saved.read_bytes() == before
