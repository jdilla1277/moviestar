"""The edit benchmark must reject stale content and preserve rule/undo scope."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parents[2] / 'experiments/live_browser_preview'


@pytest.fixture
def benchmark():
    before = set(sys.modules)
    sys.path.insert(0, str(HERE))
    try:
        spec = importlib.util.spec_from_file_location('visual_benchmark', HERE / 'edit_benchmark.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(HERE))
        for name in set(sys.modules) - before:
            path = getattr(sys.modules[name], '__file__', None)
            if path and Path(path).resolve().parent == HERE:
                del sys.modules[name]


def test_semantics_ignore_transport_but_preserve_visual_content(benchmark):
    base = {'range': {'from': 0, 'to': 5}, 'canvas': {'width': 100, 'height': 200},
        'fps': 30, 'initial': {}, 'media': [], 'samples': [], 'state_id': 'old',
        'caption_plane': {'frames': [0], 'states': [
            {'frame': 0, 'cues': [{'text': 'old'}], 'asset': 'a', 'rect': {}}]}}
    changed = deepcopy(base)
    changed['state_id'] = 'new'
    changed['caption_plane']['states'][0]['asset'] = 'b'
    assert benchmark.semantic_packet(base) == benchmark.semantic_packet(changed)
    changed['caption_plane']['states'][0]['cues'][0]['text'] = 'new'
    assert benchmark.semantic_packet(base) != benchmark.semantic_packet(changed)


def test_visible_edit_oracle():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node required for browser edit oracle')
    subprocess.run([node, '--test', str(HERE / 'tests/edit_semantics.test.mjs')],
        check=True, capture_output=True, text=True, timeout=15)


@pytest.mark.slow
def test_setup_restores_project_and_uses_rule_inverse(tmp_path, benchmark):
    from moviestar.spec import load_spec
    root = tmp_path / 'project'
    benchmark.prepare_generated(root, (360, 640))
    project = (root / 'moviestar/project.json').read_bytes()
    case = benchmark.prepare_case(root, tmp_path, depth=50)
    assert (root / 'moviestar/project.json').read_bytes() == project
    assert (root / 'moviestar/spec.json').read_bytes() == (tmp_path / 'initial-spec.json').read_bytes()
    assert len(load_spec(root)['revisions']) == 50
    assert len(case['actions']) == 10
    assert [a['args'][0] for a in case['actions'][-5:]] == ['undo'] * 4 + ['captions']
    assert case['actions'][-1]['expected'] == case['initial']
    assert case['expected_history'] == 46
