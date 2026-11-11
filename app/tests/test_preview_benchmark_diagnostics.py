"""Exercise real child-process failures and edit-timeout evidence without Chrome."""
from pathlib import Path
import importlib.util
import json
import shutil
import subprocess

import pytest


def test_benchmark_diagnostics_preserve_command_and_preview_failures():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for the browser benchmark diagnostics')
    suite = Path(__file__).parents[2] / 'experiments/live_browser_preview/tests/diagnostics.test.mjs'
    result = subprocess.run([node, '--test', str(suite)], capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def benchmark_module(monkeypatch):
    folder = Path(__file__).parents[2] / 'experiments/live_browser_preview'
    monkeypatch.syspath_prepend(str(folder))
    entry = importlib.util.spec_from_file_location('diagnostic_benchmark', folder / 'benchmark.py')
    module = importlib.util.module_from_spec(entry)
    entry.loader.exec_module(module)
    return module


def test_outer_driver_timeout_retains_completed_and_inflight_evidence(tmp_path, benchmark_module):
    retained = {'cold': {'navigation_to_visible_ms': 400}, 'edits': [{'edit': 1}],
                'edit_attempts': [{'edit': 2, 'outcome': 'readiness_timeout'}],
                'commands': [{'exit_code': 7}], 'problems': ['earlier failure']}
    path = tmp_path / 'browser-report.json'
    path.write_text(json.dumps(retained))
    original = path.read_bytes()
    result = benchmark_module.driver_failure_result(tmp_path, {'range': [0, 5]}, 'outer deadline')
    assert result['edits'] == retained['edits']
    assert result['edit_attempts'] == retained['edit_attempts']
    assert result['commands'] == retained['commands']
    assert result['problems'] == ['earlier failure', 'outer deadline']
    assert result['fixture'] == {'range': [0, 5]}
    assert path.read_bytes() == original


@pytest.mark.parametrize('raw', [b'{incomplete report', b'null', b'[]'])
def test_outer_failure_keeps_corrupt_raw_report_for_investigation(tmp_path, benchmark_module, raw):
    path = tmp_path / 'browser-report.json'
    path.write_bytes(raw)
    result = benchmark_module.driver_failure_result(tmp_path, {}, 'driver killed')
    assert result['problems'] == ['driver killed']
    assert path.read_bytes() == raw
