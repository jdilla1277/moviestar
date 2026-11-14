"""File-clock activity uses the same detector as the edited local surface."""
import json
import subprocess
import pytest
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar.activity_file import analyze_file_activity, ActivityError, normalize_options

@pytest.fixture
def video(tmp_path):
    path = tmp_path / 'silent.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x120:rate=10:duration=2','-an','-c:v','libx264',str(path)], check=True)
    return str(path)

def test_matches_local_activity_on_identical_file(video, loaded_project):
    loaded_project(video, frames=False)
    local = CliRunner().invoke(cli, ['activity','--interval','0.5'])
    assert local.exit_code == 0, local.output
    expected = json.loads(local.stdout)
    result = analyze_file_activity(video, {'interval_seconds':0.5})
    for name in ('samples','active_ranges','idle_ranges','no_new_frame_ranges','summary','analysis_dimensions','settings'):
        assert result[name] == expected[name]
    assert result['range']['clock'] == 'asset'
    assert 'commands' not in result and 'source' not in result
    assert result['file_size_bytes'] > 0

@pytest.mark.parametrize('options', [{'interval_seconds':0},{'threshold':float('nan')},{'merge_gap_seconds':-1},{'from_seconds':2,'to_seconds':1},{'extra':True},{'interval_seconds':True},{'to_seconds':86401}])
def test_invalid_options_before_probing(options, monkeypatch):
    monkeypatch.setattr('moviestar.activity_file.run_ffprobe', lambda _: pytest.fail('must not probe'))
    with pytest.raises(ActivityError, match='invalid_request'):
        analyze_file_activity('unused', options)

def test_count_and_actual_range_limits(video):
    for options, code in [({'interval_seconds':2},'sample_limit'),({'interval_seconds':0.0001},'sample_limit'),({'to_seconds':3},'invalid_range')]:
        with pytest.raises(ActivityError, match=code): analyze_file_activity(video, options)

def test_defaults_are_explicit():
    assert normalize_options({}) == {'from_seconds':0.0,'interval_seconds':0.25,'threshold':0.005,'merge_gap_seconds':0.5}


def test_subnormal_interval_is_a_structured_sample_limit(video):
    with pytest.raises(ActivityError, match='sample_limit'):
        analyze_file_activity(video, {'interval_seconds':1e-320})
    with pytest.raises(ActivityError, match='sample_limit'):
        normalize_options({'interval_seconds':1e-320,'to_seconds':1})
