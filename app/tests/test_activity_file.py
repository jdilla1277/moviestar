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


def test_default_range_matches_local_millisecond_duration(video, monkeypatch):
    # Loaded local source metadata rounds duration to milliseconds. A raw probe
    # value just above a sample boundary must not introduce an extra sample.
    monkeypatch.setattr('moviestar.activity_file.run_ffprobe', lambda _: {'format':{'duration':'1.0004'},'streams':[{'codec_type':'video','width':160,'height':120}]})
    planned=[]
    def analyze(*args,**kwargs):
        planned.extend(kwargs['samples']);return {'commands':[]}
    monkeypatch.setattr('moviestar.activity_file.analyze_visual_activity',analyze)
    assert analyze_file_activity(video,{})['range']['to']['seconds']==1
    assert len(planned)==4


def test_cover_art_is_not_a_usable_first_video_stream(monkeypatch):
    monkeypatch.setattr('moviestar.activity_file.run_ffprobe', lambda _: {'format':{'duration':'2'},'streams':[{'codec_type':'video','width':160,'height':120,'disposition':{'attached_pic':1}},{'codec_type':'video','width':160,'height':120}]})
    with pytest.raises(ActivityError,match='no_video'):
        analyze_file_activity('unused',{})


def test_multitrack_defaults_cannot_change_activity_stream(tmp_path, loaded_project):
    first_default=tmp_path/'first-default.mp4'
    second_default=tmp_path/'second-default.mp4'
    # Different content, dimensions and frame clocks. Changing dispositions
    # must never change which pixels correspond to the first-track timestamps.
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:size=160x120:rate=10:duration=2',
        '-f','lavfi','-i','testsrc2=size=320x240:rate=5:duration=2','-map','0:v','-map','1:v',
        '-c:v','libx264','-preset','ultrafast','-disposition:v:0','default','-disposition:v:1','0',str(first_default)],check=True)
    subprocess.run(['ffmpeg','-v','error','-i',str(first_default),'-map','0:v:0','-map','0:v:1','-c','copy',
        '-disposition:v:0','0','-disposition:v:1','default',str(second_default)],check=True)
    baseline=analyze_file_activity(str(first_default),{'interval_seconds':0.5})
    actual=analyze_file_activity(str(second_default),{'interval_seconds':0.5})
    assert actual['summary']['active_duration_seconds']==0
    assert actual['analysis_dimensions']=={'width':160,'height':120}
    assert [s['decoded_frame']['source_time']['seconds'] for s in actual['samples']]==[0,0.5,1,1.5]
    for name in ('samples','active_ranges','idle_ranges','no_new_frame_ranges','summary'):
        assert actual[name]==baseline[name]
    loaded_project(str(second_default),frames=False)
    local=CliRunner().invoke(cli,['activity','--interval','0.5'])
    assert local.exit_code==0,local.output
    assert json.loads(local.stdout)['samples']==actual['samples']
