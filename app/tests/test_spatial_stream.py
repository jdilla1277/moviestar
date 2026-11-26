"""Bounded raw-frame handoff preserves spatial pixels, clocks and cleanup."""
from pathlib import Path
import subprocess
import sys

import av
import pytest

from moviestar.spatial_media import MediaSession
from moviestar.spatial_stream import encode_frames
from moviestar.spatial_render import _decode
from tests.test_spatial_render import project
from tests.test_spatial_video import video_project

FORWARD_PIXELS = tuple(map(int, av.__version__.split('.')[:2])) >= (18, 1)


def rgb(path):
    with av.open(str(path)) as source:
        return next(source.decode(video=0)).to_ndarray(format='rgb24').tobytes()


@pytest.mark.skipif(not FORWARD_PIXELS, reason='PyAV <18.1 uses reference PNG decode')
def test_source_pixels_match_png_without_encoding_intermediates(project, tmp_path, monkeypatch):
    import moviestar.spatial_render as render
    source = Path(project[0]['sources'][0]['path'])
    expected = []
    for i, at in enumerate([.05,.15,.55,.25]):
        out=tmp_path/f'{i}.png';_decode(source,out,source_time=at);expected.append(rgb(out))
    def unexpected(*a, **k):raise AssertionError('ordinary source encoded as PNG')
    monkeypatch.setattr(render,'_decode',unexpected)
    with MediaSession() as media:
        for at,pixels in zip([.05,.15,.55,.25],expected):
            frame=media.pixels(source,source_time=at,occurrence=('camera',))
            assert frame.to_ndarray(format='rgb24').tobytes()==pixels


@pytest.mark.skipif(not FORWARD_PIXELS, reason='PyAV <18.1 uses reference PNG decode')
def test_five_active_occurrences_do_not_reopen_every_frame(project, monkeypatch):
    source=Path(project[0]['sources'][0]['path']);original=av.open;opened=[]
    def opening(path,*a,**k):
        if str(path)==str(source):opened.append(path)
        return original(path,*a,**k)
    monkeypatch.setattr(av,'open',opening)
    with MediaSession() as media:
        for index in range(4):
            for occurrence in range(5):
                media.pixels(source,source_time=.05+index*.1+occurrence*.1,occurrence=(str(occurrence),))
        assert len(opened)==5
        assert len(media._decoders)==5
    assert not media._decoders


def frame(width=320,height=180):
    import numpy as np
    return av.VideoFrame.from_ndarray(np.zeros((height,width,3),dtype='uint8'),format='rgb24')


def test_raw_frames_encode_identically_to_png_sequence(tmp_path):
    from fractions import Fraction
    source=tmp_path/'frames';source.mkdir()
    frames=[]
    import numpy as np
    for i in range(6):
        f=av.VideoFrame.from_ndarray(np.full((90,160,3),20+i*10,dtype='uint8'),format='rgb24')
        frames.append(f)
        e=av.CodecContext.create('png','w');e.width=160;e.height=90;e.pix_fmt='rgb24';e.time_base=Fraction(1,24)
        f.pts=i;f.time_base=Fraction(1,24)
        (source/f'{i:03}.png').write_bytes(b''.join(bytes(p) for p in [*e.encode(f),*e.encode(None)]))
    suffix=['-vf','trim=duration=0.25,setpts=PTS-STARTPTS,setsar=1','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p']
    before,after=tmp_path/'old.mp4',tmp_path/'new.mp4'
    subprocess.run(['ffmpeg','-v','error','-framerate','24','-i',str(source/'%03d.png'),*suffix,str(before)],check=True)
    command=['ffmpeg','-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size','160x90','-framerate','24','-thread_queue_size','1','-i','pipe:0',*suffix,str(after)]
    encode_frames(command,iter(frames),(160,90))
    assert before.read_bytes()==after.read_bytes()


def test_stalled_encoder_applies_backpressure_and_reaps_group(tmp_path,monkeypatch):
    import moviestar.spatial_stream as stream
    monkeypatch.setattr(stream,'_IO_TIMEOUT',.5)
    produced=[]
    def frames():
        for i in range(20):
            produced.append(i);yield frame(1024,512)
    with pytest.raises(RuntimeError,match='timed out'):
        encode_frames([sys.executable,'-c','import time;time.sleep(60)'],frames(),(1024,512))
    assert produced==[0], 'Never generate another frame while the current write is blocked'


def test_stderr_is_drained_and_failure_is_reported(tmp_path):
    script="import sys;sys.stderr.write('x'*200000+'\\nencoder exploded\\n');sys.stderr.flush();sys.exit(1)"
    with pytest.raises(RuntimeError,match='encoder exploded'):
        encode_frames([sys.executable,'-c',script],iter([frame()]),(320,180))


@pytest.mark.parametrize('variant',['yuv','full','high','rotated','vfr'])
def test_pixels_preserve_reference_for_color_formats_and_timing(tmp_path,variant):
    source=tmp_path/'source.mp4'
    command=['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=24:d=2',
             '-c:v','libx264','-bf','2','-g','24']
    if variant=='full':command+=['-color_range','pc']
    if variant=='high':command+=['-pix_fmt','yuv420p10le']
    if variant=='vfr':command+=['-vf','setpts=PTS+gte(N\\,12)/TB','-fps_mode','vfr']
    subprocess.run([*command,str(source)],check=True)
    if variant=='rotated':
        rotated=tmp_path/'rotated.mp4'
        subprocess.run(['ffmpeg','-v','error','-i',str(source),'-c','copy','-metadata:s:v:0','rotate=90',str(rotated)],check=True)
        source=rotated
    with MediaSession() as media:
        for index,at in enumerate([.05,.15,1.5,.25]):
            reference=tmp_path/f'{index}.png';_decode(source,reference,source_time=at)
            actual=media.pixels(source,source_time=at)
            assert actual.to_ndarray(format='rgb24').tobytes()==rgb(reference)


@pytest.mark.parametrize('canvas',[(320,180),(321,181),(180,320)])
def test_direct_pixels_match_real_png_frame(project,tmp_path,canvas,monkeypatch):
    from moviestar.spatial_layout import browser_available
    from moviestar.spatial_render import render_spatial_frame,render_spatial_pixels
    from moviestar.spatial_session import BrowserSession
    from tests.test_spatial_render import _frame
    if not browser_available():pytest.skip('Chrome required')
    spec,metadata=project
    with BrowserSession() as browser,MediaSession() as media:
        for index,at in enumerate([.05,.15,.55,.25]):
            model=_frame(spec,metadata,at);size={'width':canvas[0],'height':canvas[1]}
            path=tmp_path/f'{index}.png'
            render_spatial_frame(spec,model,size,output_path=path,project_root=tmp_path,browser_session=browser,media_session=media)
            before=rgb(path)
            picture=render_spatial_pixels(spec,model,size,project_root=tmp_path,browser_session=browser,media_session=media)
            assert picture.to_ndarray(format='rgb24').tobytes()==before


def test_encoder_error_or_cancel_reaps_threads_and_generator(tmp_path,monkeypatch):
    import moviestar.spatial_stream as stream
    original=stream.FrameEncoder;encoders=[];closed=[]
    class Tracked(original):
        def __init__(self,*a,**k):super().__init__(*a,**k);encoders.append(self)
    monkeypatch.setattr(stream,'FrameEncoder',Tracked)
    def frames():
        try:
            yield frame()
            raise KeyboardInterrupt()
        finally:closed.append(True)
    with pytest.raises(KeyboardInterrupt):
        encode_frames([sys.executable,'-c','import sys;sys.stdin.buffer.read()'],frames(),(320,180))
    assert closed and all(e.closed and e.process.poll() is not None for e in encoders)
    assert all(not e._writer.is_alive() and not e._reader.is_alive() for e in encoders)


def test_encoder_startup_failure_reaps_started_process(monkeypatch):
    import moviestar.spatial_stream as stream
    start=stream.Thread.start;count=[0]
    def failing(thread):
        count[0]+=1
        if count[0]==2:raise RuntimeError('writer startup failed')
        return start(thread)
    monkeypatch.setattr(stream.Thread,'start',failing)
    encoder=stream.FrameEncoder([sys.executable,'-c','import time;time.sleep(60)'])
    try:
        with pytest.raises(RuntimeError,match='writer startup failed'):
            with encoder:pass
        assert encoder.process.poll() is not None and not encoder._reader.is_alive()
    finally:
        if encoder.process.poll() is None:encoder.process.kill();encoder.process.wait()


def test_finish_timeout_requires_advancing_progress(monkeypatch):
    import moviestar.spatial_stream as stream
    monkeypatch.setattr(stream,'_IO_TIMEOUT',.3)
    code="import sys,time;sys.stdin.buffer.read();exec(\"while True:\\n sys.stderr.write('frame=0\\\\n');sys.stderr.flush();time.sleep(.05)\")"
    with pytest.raises(RuntimeError,match='finish timed out'):
        encode_frames([sys.executable,'-c',code],iter([frame()]),(320,180))


@pytest.mark.skipif(sys.platform=='win32',reason='POSIX process-group evidence')
def test_stalled_writer_reaps_encoder_descendants(tmp_path,monkeypatch):
    import moviestar.spatial_stream as stream
    import json
    monkeypatch.setattr(stream,'_IO_TIMEOUT',.4)
    marker=tmp_path/'pid'
    code="import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"
    with pytest.raises(RuntimeError,match='timed out'):
        encode_frames([sys.executable,'-c',code,str(marker)],iter([frame(1024,512)]),(1024,512))
    pid=int(marker.read_text());state=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
    assert not state or state.startswith('Z')


def test_cli_uses_pixels_without_final_or_source_video_pngs(tmp_path,monkeypatch,video_project):
    import moviestar.spatial_render as render
    from tests.test_spatial_video import _invoke
    original=render._compose;calls=[]
    def compose(directory,canvas,planes,videos,output,**kwargs):
        assert output is None
        assert all(isinstance(v['decoded'],av.VideoFrame) for v in videos.values())
        calls.append(1)
        return original(directory,canvas,planes,videos,output,**kwargs)
    def publish(*args):raise AssertionError('stream published a PNG')
    monkeypatch.setattr(render,'_compose',compose)
    monkeypatch.setattr(render,'_publish',publish)
    _invoke(['export','--out','stream.mp4','--video-encoder','x264','--quiet'])
    assert len(calls)==4 and (tmp_path/'stream.mp4').exists()
    assert not list(tmp_path.rglob('frame*.png'))


def test_generator_cleanup_does_not_replace_primary_error():
    class Frames:
        def __iter__(self):return self
        def __next__(self):raise ValueError('primary generator error')
        def close(self):raise RuntimeError('secondary cleanup error')
    with pytest.raises(ValueError,match='primary generator error'):
        encode_frames([sys.executable,'-c','import sys;sys.stdin.buffer.read()'],Frames(),(320,180))


@pytest.mark.parametrize('moving_first',[False,True])
def test_shared_frame_keeps_raster_and_pixel_representations_separate(tmp_path,moving_first):
    from moviestar.spatial_render import _prepare_assets
    source=tmp_path/'source';source.write_bytes(b'fixture')
    spec={'sources':[{'id':'camera','path':str(source)}]}
    nodes=[{'occurrence_path':[name],'type':'video','content':{'asset_id':'camera'},
            'media':{'visible':True,'source_time_s':.5}} for name in ['static','moving']]
    prepared={'nodes':[{'path':[node['occurrence_path'][0]],'kind':'video',
        **({'motion':{'translate_x':'1px'}} if node['occurrence_path']==['moving'] else {})}
        for node in nodes]}
    if moving_first:prepared['nodes'].reverse()
    class Media:
        def probe(self,path):return {'streams':[{'pix_fmt':'yuv420p'}]}
        def pixels(self,*args,**kwargs):return frame(160,90)
        def decode(self,path,output,**kwargs):
            from fractions import Fraction
            encoder=av.CodecContext.create('png','w')
            encoder.width,encoder.height,encoder.pix_fmt=160,90,'rgb24'
            encoder.time_base=Fraction(1,24)
            picture=frame(160,90);picture.pts=0;picture.time_base=Fraction(1,24)
            output.write_bytes(b''.join(bytes(p) for p in [*encoder.encode(picture),*encoder.encode(None)]))
    videos=_prepare_assets(spec,{'children':nodes},prepared,tmp_path,tmp_path,
        reference=False,media_session=Media(),pixels=True)
    decoded={tuple(v['path']):v['decoded'] for v in videos.values()}
    assert isinstance(decoded[('static',)],av.VideoFrame)
    assert isinstance(decoded[('moving',)],Path)
    moving=next(n for n in prepared['nodes'] if n['path']==['moving'])
    assert moving['video']['raster_image'].startswith('data:image/png;base64,')


def test_same_source_static_and_motion_match_png_frame(project,tmp_path):
    from moviestar.spatial_layout import browser_available
    from moviestar.spatial_render import render_spatial_frame,render_spatial_pixels
    from moviestar.spatial_session import BrowserSession
    from tests.test_spatial_render import _frame
    if not browser_available():pytest.skip('Chrome required')
    spec,metadata=project
    listeners=spec['spatial']['scenes']['interview']['layout']['children'][1]
    listeners['bindings']['left']['source']={'start':0,'end':1.2}
    listeners['motion']={'translate_x':[{'at':0,'value':.5}]}
    with BrowserSession() as browser,MediaSession() as media:
        for index,at in enumerate([.05,.15,.55]):
            model=_frame(spec,metadata,at);canvas={'width':320,'height':180}
            reference=tmp_path/f'reference-{index}.png'
            render_spatial_frame(spec,model,canvas,output_path=reference,project_root=tmp_path,
                browser_session=browser,media_session=media)
            direct=render_spatial_pixels(spec,model,canvas,project_root=tmp_path,
                browser_session=browser,media_session=media)
            assert direct.to_ndarray(format='rgb24').tobytes()==rgb(reference)
