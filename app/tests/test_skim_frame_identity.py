"""Prepared skim images retain decoded identity across persistence and cheap reads."""
import json
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar.project import list_frames, load_project
def gray(path):
    return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-frames:v','1',
        '-vf','scale=64:64','-pix_fmt','gray','-f','rawvideo','-'])


def decoded_source(path):
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_frames','-show_format','-show_entries','frame=best_effort_timestamp_time:format=start_time',
        '-of','json',str(path)]))
    origin=float(info['format'].get('start_time',0))
    times=[float(f['best_effort_timestamp_time'])-origin for f in info['frames']]
    pixels=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:v:0',
        '-fps_mode','passthrough','-pix_fmt','gray','-f','rawvideo','-'])
    means=[sum(pixels[i:i+4096])/4096 for i in range(0,len(pixels),4096)]
    assert len(times)==len(means)
    return times,means


def invoke(*args):
    r=CliRunner().invoke(cli,list(args))
    assert r.exit_code==0,r.output
    return json.loads(r.stdout)


@pytest.fixture
def counter(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    path=tmp_path/'counter.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        "nullsrc=s=64x64:r=30:d=4,geq=lum='16+N':cb=128:cr=128",
        '-c:v','libx264','-crf','0',str(path)],check=True)
    return path


CASES=json.loads((Path(__file__).parent/'fixtures/inspection-clock-cases.json').read_text())
@pytest.mark.parametrize('rate,vfr,start,end,interval,offset',CASES)
def test_load_persisted_index_and_skim_identify_actual_source_frames(tmp_path,monkeypatch,rate,vfr,start,end,interval,offset):
    monkeypatch.chdir(tmp_path)
    path=tmp_path/'counter.mp4'
    vf=f"nullsrc=s=64x64:r={rate}:d=4,geq=lum='16+N':cb=128:cr=128"
    if vfr:vf+=",select='eq(n,0)+eq(n,3)+eq(n,12)+eq(n,42)+eq(n,57)+eq(n,69)+eq(n,117)'"
    if offset:vf+=f',setpts=PTS+{offset}/TB'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',vf,'-fps_mode','vfr','-c:v','libx264','-crf','0',str(path)],check=True)
    times,means=decoded_source(path)
    invoke('load',str(path),'--interval',str(interval),'--thumb-width','64','--no-transcribe')
    source=load_project()['sources'][0]
    frames=list_frames(source)
    result=invoke('skim','--from',str(start),'--to',str(end),'--count','7')
    assert result['sampling']['time_basis']=='decoded_source_time'
    assert result['sampling']['range_filter']=='inclusive_requested_index_timestamps'
    assert len(frames)==source['frames_extracted']
    for frame in result['thumbnails']:
        requested=frame['requested_timecode']['seconds']
        assert start<=requested<=end
        actual=sum(gray(frame['path']))/4096
        # Match pixels to independently decoded source frames, never to the
        # sampled output's ordinal. Fractional ticks may be rationalized.
        identity=min(range(len(times)),key=lambda i:abs(times[i]-frame['timecode']['seconds']))
        assert abs(means[identity]-actual)<1
        assert frame['timecode']['seconds']==pytest.approx(round(times[identity],3),abs=.001)
    for frame in result['frames_omitted']:
        assert 'requested_timecode' in frame
    assert result['available_frames']==len(result['thumbnails'])+len(result['frames_omitted'])


def test_skim_reads_timing_without_media_tools_and_preserves_holes(counter,monkeypatch):
    invoke('load',str(counter),'--interval','0.5','--no-transcribe')
    source=load_project()['sources'][0]
    frames=list_frames(source)
    Path(frames[1]['path']).unlink()
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:(_ for _ in ()).throw(AssertionError('no media process on skim')))
    remaining=list_frames(source)
    assert remaining[1]['requested_timecode']['seconds']==1
    assert remaining[1]['timecode']['seconds']==1
    assert invoke('skim')['sampling']['time_basis']=='decoded_source_time'


def test_legacy_index_is_explicitly_nominal_without_rewriting_images(counter):
    invoke('load',str(counter),'--no-frames','--no-transcribe')
    p=Path('moviestar/project.json');data=json.loads(p.read_text());source=data['sources'][0]
    source.pop('frame_index',None);source.pop('frame_extraction_skipped_reason',None);source['frame_interval']=.5
    p.write_text(json.dumps(data))
    frame=Path('moviestar/frames/counter_0003.jpg');frame.write_bytes(b'legacy-image')
    result=invoke('skim')
    assert result['sampling']['time_basis']=='nominal_prepared_index'
    assert result['thumbnails'][0]['timecode']['seconds']==1
    assert 'requested_timecode' not in result['thumbnails'][0]
    assert 'nominal' in result['hint'].lower()
    assert frame.read_bytes()==b'legacy-image'


def test_corrupt_new_index_fails_visual_reads_but_text_reads_still_work(counter):
    invoke('load',str(counter),'--no-transcribe')
    source=load_project()['sources'][0]
    manifest=Path('moviestar')/source['frame_index']['path']
    manifest.write_text('{}')
    result=CliRunner().invoke(cli,['skim'])
    assert result.exit_code!=0
    assert 'index' in result.output.lower()
    assert invoke('skim','--text-only')['text_only'] is True
    text=CliRunner().invoke(cli,['skim','--format','text'])
    assert 'index' not in text.output.lower()


def test_load_first_skim_frame_is_source_zero(counter):
    invoke('load',str(counter),'--interval','0.5','--no-transcribe')
    frame=invoke('skim','--from','0','--to','1')['thumbnails'][0]
    actual=gray(frame['path']);expected=gray(counter)
    assert abs(sum(actual)/len(actual)-sum(expected)/len(expected))<1
    assert frame['timecode']['seconds']==0


@pytest.mark.parametrize('field,value',[('source_seconds',float('nan')),('source_seconds',-1),('source_seconds',True),('requested_seconds',999),('file','other.jpg')])
def test_invalid_new_frame_timing_never_falls_back_to_nominal(counter,field,value):
    invoke('load',str(counter),'--interval','0.5','--no-transcribe')
    source=load_project()['sources'][0]
    path=Path('moviestar')/source['frame_index']['path']
    data=json.loads(path.read_text());data['frames'][0][field]=value;path.write_text(json.dumps(data))
    with pytest.raises(ValueError):list_frames(source)


def test_legacy_large_ordinals_are_sorted_by_grid_time(tmp_path):
    directory=tmp_path/'moviestar/frames';directory.mkdir(parents=True)
    for n in (9999,10000): (directory/f'test_{n:04d}.jpg').touch()
    frames=list_frames({'id':'test','frame_interval':.5},tmp_path)
    assert [f['timecode']['seconds'] for f in frames]==[4999,4999.5]
