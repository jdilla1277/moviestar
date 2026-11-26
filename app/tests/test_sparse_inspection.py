"""Cloud skim can select a bounded set from the same prepared sampling grid."""
from pathlib import Path
import subprocess
import pytest
from moviestar.ffmpeg import extract_inspection_frames

@pytest.mark.parametrize('indices',[[0],[1,3,7],[0,2,5,7]])
def test_sparse_sampling_keeps_full_index_pixels_and_source_times(tmp_path,indices):
    video=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
        "nullsrc=s=64x64:r=30:d=4,geq=lum='16+N':cb=128:cr=128",'-c:v','libx264','-crf','0',str(video)],check=True)
    all_paths,all_times,_=extract_inspection_frames(str(video),.5,tmp_path/'all','all',64,0,4)
    paths,times,cmd=extract_inspection_frames(str(video),.5,tmp_path/'selected','selected',64,0,4,sample_indices=indices)
    assert times==[all_times[i] for i in indices]
    assert [p.read_bytes() for p in paths]==[all_paths[i].read_bytes() for i in indices]
    assert len(list((tmp_path/'selected').glob('*.jpg')))==len(indices)
    assert cmd[cmd.index('-frames:v')+1]==str(len(indices))

@pytest.mark.parametrize('indices',[[],[-1],[2,1],[1,1],[8],[True],[.5]])
def test_bad_sparse_indices_fail_before_writing(tmp_path,indices):
    video=tmp_path/'source.mp4';video.touch()
    with pytest.raises(ValueError):
        extract_inspection_frames(str(video),.5,tmp_path/'out','selected',64,0,4,sample_indices=indices)
    assert not (tmp_path/'out').exists()

@pytest.mark.parametrize('rate,vfr,offset',[('30',False,0),('30000/1001',False,0),('30',True,0),('24',False,5)])
def test_sparse_fast_seek_preserves_global_sampling_grid(tmp_path,rate,vfr,offset):
    video=tmp_path/'source.mp4'
    vf=f"nullsrc=s=64x64:r={rate}:d=4,geq=lum='16+N':cb=128:cr=128"
    if vfr:vf+=",select='eq(n,0)+eq(n,3)+eq(n,12)+eq(n,42)+eq(n,57)+eq(n,69)+eq(n,117)'"
    if offset:vf+=f',setpts=PTS+{offset}/TB'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',vf,'-fps_mode','vfr','-c:v','libx264','-g','30','-crf','0',str(video)],check=True)
    paths,times,_=extract_inspection_frames(str(video),.25,tmp_path/'all','all',64,0,4)
    for i in (1,5,11,15):
        selected,actual,cmd=extract_inspection_frames(str(video),.25,tmp_path/f'one-{i}','one',64,0,4,sample_indices=[i],decode_seek_time=i*.25)
        assert actual==[times[i]]
        assert selected[0].read_bytes()==paths[i].read_bytes()
        assert cmd[cmd.index('-ss')+1]==str(i*.25)


def test_verbose_long_source_timing_does_not_require_log_sized_memory(tmp_path,monkeypatch):
    import tracemalloc
    from types import SimpleNamespace
    video=tmp_path/'source.mp4';video.touch()
    out=tmp_path/'out';out.mkdir()
    for i in (1,2):(out/f'frame_{i:06d}.jpg').write_bytes(b'jpeg')
    def records():
        for label in ('source','sample','output'):
            yield f'[showinfo@{label} @ 0x0] config in time_base: 1/10000\n'
            for n in range(2 if label=='output' else 50000):
                pts=n*5000 if label=='output' else n
                yield f'[showinfo@{label} @ 0x0] n: {n} pts: {pts} pts_time:unused\n'
    def fake(*args,**kwargs):
        target=kwargs.get('stderr')
        if target is not None:
            for line in records():target.write(line)
            return SimpleNamespace(returncode=0,stdout='',stderr=None)
        return SimpleNamespace(returncode=0,stdout='',stderr=''.join(records()))
    monkeypatch.setattr(subprocess,'run',fake)
    tracemalloc.start()
    try:
        paths,times,_=extract_inspection_frames(str(video),.5,out,'frame',64,0,1)
        _,peak=tracemalloc.get_traced_memory()
    finally:tracemalloc.stop()
    assert len(paths)==2 and times==[0,.5]
    assert peak<8_000_000,peak
