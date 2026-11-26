"""The documented experiment entry points follow the installed render API."""
import json
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from moviestar.spatial_layout import browser_available
from tests.test_preview_benchmark_diagnostics import benchmark_module as foreign_benchmark


EXPERIMENT = Path(__file__).parents[2] / 'experiments/spatial_preview_performance'


@pytest.fixture
def synthetic_project(tmp_path, monkeypatch, foreign_benchmark):
    if not browser_available():
        pytest.skip('Chrome and Node required')
    # Reproduce the full-suite import order: the live-browser experiment was
    # loaded first. It must neither be reused nor overwritten by these tests.
    monkeypatch.setitem(sys.modules, 'benchmark', foreign_benchmark)
    monkeypatch.syspath_prepend(str(EXPERIMENT))
    entry = importlib.util.spec_from_file_location('spatial_preview_benchmark', EXPERIMENT/'benchmark.py')
    benchmark = importlib.util.module_from_spec(entry)
    entry.loader.exec_module(benchmark)
    from moviestar.spec import save_spec
    spec, metadata = benchmark.fixture(tmp_path)
    spec['composition_canvas'].update(width=320, height=180)
    save_spec(spec, cwd=tmp_path)
    assert sys.modules['benchmark'] is foreign_benchmark
    return benchmark, spec, metadata


def test_benchmark_capture_accepts_session_paint_cache(tmp_path, synthetic_project):
    benchmark, spec, metadata = synthetic_project
    from moviestar import spatial_render
    original = spatial_render._compose
    captured = benchmark.capture(tmp_path, 'capture', spec, metadata)
    assert (captured/'reference.png').is_file()
    assert json.loads((captured/'inputs.json').read_text())['planes']
    assert spatial_render._compose is original


@pytest.mark.parametrize('variant', ['production', 'baseline'])
def test_preview_profiler_entry_point_keeps_production_cache(tmp_path, synthetic_project, variant):
    output = tmp_path/variant
    result = subprocess.run([sys.executable, str(EXPERIMENT/'profile_preview.py'),
        '--project', str(tmp_path), '--out', str(output), '--variant', variant,
        '--from', '0', '--to', '.2'], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    report = json.loads((output/'profile.json').read_text())
    assert report['exit_code'] == 0 and report['spec_unchanged']
    assert int(report['video']['nb_frames']) == 6
    assert report['counts']['browser_paint_reuses'] == 5
    if variant == 'production':
        assert report['counts']['decoded_paint_peak_retained_bytes'] > 0
        # Six frames need only the base and one mask decoded once, not twelve
        # decodes. This catches adapters that accept but discard paint_cache.
        if tuple(map(int, report['runtime_versions']['av'].split('.')[:2])) >= (18, 1):
            assert report['counts']['av_open.png'] == 2
    else:
        assert report['counts']['decoded_paint_peak_retained_bytes'] == 0
