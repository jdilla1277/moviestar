"""Installed CLI caption edits must become fresh, visible and playable."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from moviestar.spatial_layout import browser_available


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
def test_authored_captions_and_individual_undos_in_the_actual_browser(tmp_path):
    repo = Path(__file__).parents[2]
    out = tmp_path/'evidence'
    result = subprocess.run([sys.executable, str(repo/'experiments/live_browser_preview/caption_benchmark.py'),
        '--history', '0', '--out', str(out)], capture_output=True, text=True, timeout=180)
    report_path = out/'history-0/browser-report.json'
    assert result.returncode == 0, (result.stdout, result.stderr,
        report_path.read_text() if report_path.exists() else 'No browser report')
    report = json.loads(report_path.read_text())
    assert len(report['edits']) == 8
    assert report['restored']['same_state'] and report['restored']['fresh_request']
    assert not report['problems'] and not report['browser_exceptions']
