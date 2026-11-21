"""Exercise the documented interview edits through the CLI, not spec mutations."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.native_preview import compile_snapshot
from moviestar.spatial_model import expand_spatial_scene, spatial_state_id
from moviestar.spec import load_spec


def _videos(nodes):
    for node in nodes:
        if node.get('type') == 'video':
            yield node
        yield from _videos(node.get('children', []))


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_interview_cli_reuses_components_preserves_local_edits_and_undo(tmp_path, monkeypatch):
    repo = Path(__file__).parents[2]
    monkeypatch.syspath_prepend(str(repo / 'experiments/live_browser_preview'))
    from interview import prepare_interview
    root = tmp_path / 'interview'
    prepare_interview(root)
    original = load_spec(root); original_id = spatial_state_id(original)
    initial_depth = len(original['revisions'])
    def command(*args):
        result = subprocess.run([sys.executable, '-m', 'moviestar.cli', *args], cwd=root,
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    def colors():
        spec = load_spec(root)
        return [node['style']['border_color'] for scene in ('intro', 'reply')
            for node in _videos(expand_spatial_scene(spec, scene)['children'])]
    assert colors() == ['#ffb81c'] * 6
    assert all(original['spatial']['scenes'][scene]['layout']['children'][2]['definition_id'] == 'listener_pair'
               for scene in ('intro', 'reply'))
    for start in (0, 3):
        packet, _ = compile_snapshot(read_snapshot(root, start, start + 3))
        assert len(packet['media']) == 3
    before = (root / 'moviestar/spec.json').read_bytes()
    command('composition', 'model', 'edit', 'edit-local.json', '--dry-run')
    assert (root / 'moviestar/spec.json').read_bytes() == before
    command('composition', 'model', 'edit', 'edit-local.json')
    assert colors() == ['#ffb81c', '#00d4ff', '#ffb81c', '#ffb81c', '#ffb81c', '#ffb81c']
    command('composition', 'model', 'edit', 'edit-shared.json')
    assert colors() == ['#c084fc', '#00d4ff', '#c084fc', '#c084fc', '#c084fc', '#c084fc']
    command('composition', 'model', 'edit', 'edit-reset.json')
    assert colors() == ['#c084fc'] * 6
    assert len(load_spec(root)['revisions']) == initial_depth + 3
    command('undo')
    assert colors()[1] == '#00d4ff'
    command('undo')
    assert colors() == ['#ffb81c', '#00d4ff', '#ffb81c', '#ffb81c', '#ffb81c', '#ffb81c']
    command('undo')
    assert spatial_state_id(load_spec(root)) == original_id
    command('scenes', 'set', 'scenes-640x360.json')
    landscape = load_spec(root)
    assert landscape['composition_canvas']['width'] == 640
    assert landscape['composition_canvas']['height'] == 360
    assert landscape['spatial'] == original['spatial'], 'Canvas changes preserve the same shared arrangement'
    command('undo')
    assert spatial_state_id(load_spec(root)) == original_id
    assert len(load_spec(root)['revisions']) == initial_depth
