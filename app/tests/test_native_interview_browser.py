"""The reusable interview stays editable, audible and identical to canonical rendering."""
import json
import os
from pathlib import Path
import subprocess
import sys

import av
import numpy as np
import pytest

from moviestar.project import load_project
from moviestar.resolved import resolve_project
from moviestar.spatial_layout import browser_available, measure_spatial_layout
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_render import render_spatial_frame
from moviestar.spec import load_spec
from tests.test_native_edit_benchmark import benchmark


def _image(path):
    with av.open(str(path)) as container:
        return next(container.decode(video=0)).to_ndarray(format='rgb24').astype(int)


def _audio(path):
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=48000)
        return np.concatenate([f.to_ndarray() for src in container.decode(audio=0)
                               for f in resampler.resample(src)], axis=1)


def _assert_pcm(review, reference, *, duration=3):
    m = review['metrics']; a = m['prepared_audio']; capture = m['audio_pcm']
    actual = np.asarray(capture['samples']); expected = _audio(reference)
    index = round((capture['start_context_time'] - a['started_context_time'] + a['started_offset']) * 48000)
    stop = round((m['pause_context_time'] - capture['start_context_time']) * 48000)
    errors = []
    for shift in (-1, 0, 1):
        begin = index + shift; left = max(0, -begin)
        right = min(actual.shape[1], expected.shape[1] - begin, stop - 128)
        errors.append((actual[:, left:right] - expected[:, begin + left:begin + right]).ravel())
    error = min(errors, key=lambda e: np.sqrt(np.mean(e * e)))
    assert len(error) > (duration-.2) * 48000 * 2, 'Compare the whole review, allowing only the final stop quantum'
    assert abs(error).max() < 1e-5


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
@pytest.mark.parametrize('canvas,scene,depth', [('360x640','intro',0),('640x360','reply',0),('360x640','all',20),('640x360','all',50)])
def test_actual_interview_edits_undo_playback_and_export(tmp_path, benchmark, canvas, scene, depth):
    from interview_benchmark import run_case
    folder = tmp_path / 'browser'
    assert run_case(folder, canvas, depth, capture_pcm=True, scene=scene) == 0, (folder / 'driver.log').read_text()
    report = json.loads((folder / 'report.json').read_text())
    assert len(report['edits']) == 20 and len(report['playback']) == 2
    assert report['problems'] == []
    for shot in report['captures']:
        assert all(v['transport'] == 'webcodecs' for v in shot['snapshot']['videos'])
    for review in report['playback']:
        _assert_pcm(review, folder / (review['label'] + '-audio-reference.wav'),duration=6 if scene=='all' else 3)
    root = folder / 'project'; spec = load_spec(root)
    assert all(spec['spatial']['scenes'][scene]['layout']['children'][2]['definition_id'] == 'listener_pair'
               for scene in ('intro', 'reply'))
    width, height = map(int, canvas.split('x')); size = {'width': width, 'height': height}
    before = Path.cwd()
    try:
        os.chdir(root)
        timeline = resolve_project(load_project(root), spec)
        for shot in report['captures']:
            frame = compile_spatial_frame(spec, timeline, shot['at'] + (3 if scene == 'reply' else 0))
            boxes = measure_spatial_layout(frame, size)['boxes']
            actual_boxes = {tuple(b['path']): b for b in shot['snapshot']['boxes']}
            for box in boxes:
                actual = actual_boxes[tuple(box['path'])]
                for key in ('x', 'y', 'width', 'height'):
                    assert abs(actual['rect'][key] - box['rect'][key]) <= .2
                if box.get('text'):
                    assert actual['text'] == box['text']
            reference = folder / (shot['label'] + '-canonical.png')
            render_spatial_frame(spec, frame, size, project_root=root, output_path=reference)
            actual, expected = _image(folder / (shot['label'] + '.png')), _image(reference)
            assert actual.shape == expected.shape == (height, width, 3)
            for box in boxes:
                if not box.get('media_box'):
                    continue
                rect = box['media_box']['content_rect']
                # Interior picture colors avoid glyph/rounded-border antialiasing.
                for fx, fy in ((.2, .35), (.5, .5), (.8, .65)):
                    x, y = int(rect['x'] + fx * rect['width']), int(rect['y'] + fy * rect['height'])
                    assert abs(actual[y, x] - expected[y, x]).max() <= 8, (shot['label'], box['path'], x, y)
    finally:
        os.chdir(before)
    # Exercise both sides of the real scene cut through the canonical export
    # renderer. The full six-second owner demo uses `export`; this smaller
    # watch window keeps the real pixel/audio regression bounded in CI.
    result = subprocess.run([sys.executable, '-m', 'moviestar.cli', 'watch', '--from', '2.1', '--to', '3.3',
        '--precise', '--out', str(folder / 'interview.mp4')],
        cwd=root, capture_output=True, text=True, timeout=120)
    (folder / 'export.stdout.json').write_text(result.stdout)
    (folder / 'export.stderr.log').write_text(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    with av.open(str(folder / 'interview.mp4')) as container:
        video = container.streams.video[0]
        assert (video.width, video.height) == (width, height)
        assert abs(container.duration / av.time_base - 1.2) <= 1 / 30
        frames = list(container.decode(video=0))
        assert len(frames) == 36
        for index, at in ((0, 2.1), (30, 3.1)):
            before = Path.cwd()
            try:
                os.chdir(root)
                frame = compile_spatial_frame(spec, timeline, at)
                boxes = measure_spatial_layout(frame, size)['boxes']
                reference = folder / f'cut-{index}-canonical.png'
                render_spatial_frame(spec, frame, size, project_root=root, output_path=reference)
            finally:
                os.chdir(before)
            expected = _image(reference)
            actual = frames[index].to_ndarray(format='rgb24').astype(int)
            # H.264 introduces error at edges; compare source interiors again.
            for box in boxes:
                if box.get('media_box'):
                    rect = box['media_box']['content_rect']
                    x, y = int(rect['x'] + .5 * rect['width']), int(rect['y'] + .5 * rect['height'])
                    assert abs(actual[y, x] - expected[y, x]).max() <= 8
    audio = _audio(folder / 'interview.mp4')
    for at, low, high in ((.11, .14, .22), (1.11, .07, .12)):
        samples = audio[:, round(at*48000):round((at+.04)*48000)]
        assert low < np.sqrt(np.mean(samples*samples)) < high, 'Export selects the speaker across the cut'


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
@pytest.mark.parametrize('terminal_count', [0, 1, 3])
@pytest.mark.parametrize('window_frames', [1, 2])
def test_terminal_interview_window_is_ready_and_replayable(tmp_path, benchmark, terminal_count, window_frames):
    import selectors
    from interview import prepare_interview
    from moviestar.spec import save_spec
    from moviestar.spatial_layout import _chrome_path
    root = tmp_path / 'project'; prepare_interview(root)
    spec = load_spec(root)
    children = spec['spatial']['scenes']['intro']['layout']['children']
    expected = [119, 149, 179]
    if terminal_count:
        children[2]['bindings']['right']['source'] = {'start': 5, 'end': 8}
        expected[2] = 239
    if terminal_count == 3:
        children[1]['bindings']['clip']['source'] = {'start': 5, 'end': 8}
        children[2]['bindings']['left']['source'] = {'start': 5, 'end': 8}
        spec['composition'][0]['slots'][0].update(source_from='0:00:05.000', source_to='0:00:08.000')
        expected = [239] * 3
    expected = [n - (window_frames - 1) for n in expected]
    save_spec(spec, cwd=root)
    with (tmp_path / 'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root),
            '--mode', 'native', '--from', str((90-window_frames)/30), '--to', '3', '--port', '0', '--no-open'],
            stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                assert selector.select(20)
                ready = json.loads(server.stdout.readline()); assert ready.get('url'), ready
            script = r'''
import assert from 'node:assert/strict';import{writeFile}from'node:fs/promises';
const {Browser}=await import(HELPER),b=await Browser.launch(CHROME,OUT),api='window.movieStarPreview.stage';
try{
 await b.send('Page.navigate',{url:URL+'?audit=1'});
 await b.wait('window.movieStarPreview?.is_current||document.getElementById("error").textContent',12000);
 const error=await b.evaluate('document.getElementById("error").textContent');assert.equal(error,'');
 assert.equal(await b.evaluate('window.movieStarPreview.is_current'),true);
 const check=async(expected=EXPECTED)=>{const s=await b.evaluate(api+'.snapshot()');
  assert.deepEqual(s.videos.map(v=>v.source.number),expected);
  assert.ok(s.videos.every(v=>v.paused&&!v.seeking&&v.ready_state>=2));return s;};
 await check();assert.equal(await b.evaluate(api+'.packet.samples.length'),WINDOW_FRAMES);
 assert.equal(await b.evaluate('Boolean('+api+'.metrics.picture_startup.prepared_frame_only)'),WINDOW_FRAMES===1);
 for(let i=0;i<2;i++){
  await b.click('#play');await b.wait(api+'.metrics.finished||'+api+'.error',5000);
  assert.equal(await b.evaluate(api+'.error'),null);await check(EXPECTED.map(n=>n+WINDOW_FRAMES-1));
  if(WINDOW_FRAMES===1){const m=await b.evaluate(api+'.metrics');
   assert.ok(m.video_quality.every(q=>q.total_frames===0&&q.dropped_frames===0),'A static window must not advance beyond its selected image');
   assert.ok(m.pause_context_time-m.prepared_audio.started_context_time-1/30<=.05,'Static completion retains the existing 50ms end tolerance');}

  await b.click('#restart');await b.wait(api+'.metrics.finished===false');
  await b.evaluate(api+'.seek(0)');await check();
 }
 await writeFile(OUT+'/terminal.json',JSON.stringify({snapshot:await check(),metrics:await b.evaluate(api+'.metrics')}));
}catch(error){
 const failure=await b.evaluate(`(()=>{const stage=window.movieStarPreview?.stage??document.querySelector('iframe')?.contentWindow.moviestarLiveStage;
  let snapshot;try{snapshot=stage?.snapshot();}catch(error){snapshot={error:String(error)};}
  return{error:document.getElementById('error').textContent,current:window.movieStarPreview?.is_current,snapshot,metrics:stage?.metrics};})()`).catch(()=>null);
 await writeFile(OUT+'/failure.json',JSON.stringify({exception:String(error.stack??error),state:failure}));throw error;
}finally{await b.close();}
'''
            values = {'HELPER': (Path(__file__).parents[2]/'experiments/live_browser_preview/browser.mjs').as_uri(),
                'CHROME': str(_chrome_path()), 'OUT': str(tmp_path/'browser'), 'URL': ready['url'], 'EXPECTED': expected, 'WINDOW_FRAMES': window_frames}
            result = subprocess.run(['node', '--input-type=module', '-e',
                ''.join('const '+k+'='+json.dumps(v)+';' for k,v in values.items())+script],
                capture_output=True, text=True, timeout=35)
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            server.terminate()
            try: server.wait(timeout=9)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            server.stdout.close()


@pytest.mark.parametrize('defect', ['none', 'one_sample', 'short_capture'])
def test_full_cut_pcm_oracle_rejects_short_or_locally_corrupted_output(tmp_path, benchmark, defect):
    import wave
    from pcm_reference import verify_pcm_reference
    reference=tmp_path/'canonical.wav'
    with wave.open(str(reference),'wb') as output:
        output.setparams((2,2,48000,0,'NONE','not compressed'))
        output.writeframes(np.full((6*48000,2),4096,dtype='<i2').tobytes())
    samples=np.full((2,6*48000),.125)
    if defect=='one_sample':samples[1,100]+=.001  # RMS alone would miss this corruption.
    elif defect=='short_capture':samples=samples[:,:round(2.9*48000)]
    review={'metrics':{'prepared_audio':{'started_context_time':0,'started_offset':0},
        'pause_context_time':6+128/48000,
        'audio_pcm':{'sample_rate':48000,'start_context_time':0,'samples':samples}}}
    if defect=='none':
        result=verify_pcm_reference(review,reference,duration=6)
        assert result['compared_stereo_seconds']==6 and result['maximum_sample_error']==0
    else:
        with pytest.raises(AssertionError,match='sample error' if defect=='one_sample' else 'complete review'):
            verify_pcm_reference(review,reference,duration=6)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
def test_one_frame_three_picture_cut_at_source_eof_finishes_and_restarts(tmp_path, benchmark):
    import selectors
    from interview import prepare_interview
    from moviestar.spec import save_spec
    from moviestar.spatial_layout import _chrome_path
    root=tmp_path/'project';prepare_interview(root)
    spec=load_spec(root);children=spec['spatial']['scenes']['intro']['layout']['children']
    children[1]['bindings']['clip']['source']={'start':5,'end':8}
    for side in ('left','right'):children[2]['bindings'][side]['source']={'start':5,'end':8}
    spec['composition'][0]['slots'][0].update(source_from='0:00:05.000',source_to='0:00:08.000')
    save_spec(spec,cwd=root)
    with (tmp_path/'server.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-m','moviestar.cli','preview','--workspace',str(root),'--mode','native',
            '--from',str(89/30),'--to',str(91/30),'--port','0','--no-open'],stdout=subprocess.PIPE,stderr=log,text=True)
        try:
            with selectors.DefaultSelector() as poll:
                poll.register(server.stdout,selectors.EVENT_READ);assert poll.select(20)
                ready=json.loads(server.stdout.readline());assert ready.get('url'),ready
            script=r'''
import assert from 'node:assert/strict';import{writeFile}from'node:fs/promises';
const {Browser}=await import(HELPER),b=await Browser.launch(CHROME,OUT),api='window.movieStarPreview.stage';
const children=`(()=>{const w=document.querySelector('#stage iframe:not(.pending)').contentWindow;return [...w.document.querySelectorAll('iframe')].map(f=>({snapshot:f.contentWindow.moviestarLiveStage.snapshot(),metrics:f.contentWindow.moviestarLiveStage.metrics}));})()`;
try{
 await b.send('Page.navigate',{url:URL+'?audit=1'});await b.wait('window.movieStarPreview?.is_current',15000);
 for(let trial=0;trial<2;trial++){
  assert.deepEqual((await b.evaluate(children)).map(c=>c.snapshot.videos.map(v=>v.source.number)),[[239,239,239],[60,90,120]]);
  await b.click('#play');await b.wait(api+'.metrics.finished||'+api+'.error',6000);
  assert.equal(await b.evaluate(api+'.error'),null);
  const result=await b.evaluate(children);assert.ok(result.every(c=>c.snapshot.videos.every(v=>v.paused&&!v.seeking)));
  assert.ok(result.every(c=>c.metrics.video_quality.every(q=>q.total_frames===0&&q.dropped_frames===0)),'Neither static scene may advance beyond its one authored image');
  await writeFile(OUT+'/static-cut-'+trial+'.json',JSON.stringify({children:result,metrics:await b.evaluate(api+'.metrics')}));
  await b.click('#restart');await b.wait(api+'.metrics.finished===false');await b.evaluate(api+'.seek(0)');
 }
}finally{await b.close();}
'''
            values={'HELPER':(Path(__file__).parents[2]/'experiments/live_browser_preview/browser.mjs').as_uri(),
                'CHROME':str(_chrome_path()),'OUT':str(tmp_path/'browser'),'URL':ready['url']}
            result=subprocess.run(['node','--input-type=module','-e',''.join('const '+k+'='+json.dumps(v)+';' for k,v in values.items())+script],capture_output=True,text=True,timeout=40)
            assert result.returncode==0,result.stdout+result.stderr
        finally:
            server.terminate()
            try:server.wait(timeout=9)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            server.stdout.close()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
@pytest.mark.parametrize('stall', [False, True])
def test_decoded_incoming_scene_cannot_bleed_through_transparent_current_gaps(tmp_path, benchmark, stall):
    import selectors
    from interview import prepare_interview
    from moviestar.spec import save_spec
    from moviestar.spatial_layout import _chrome_path
    root=tmp_path/'project';prepare_interview(root)
    spec=load_spec(root)
    spec['spatial']['scenes']['intro']['layout'].pop('background_color')
    spec['spatial']['scenes']['reply']['layout']['background_color']='#ff0000'
    save_spec(spec,cwd=root)
    with (tmp_path/'server.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-m','moviestar.cli','preview','--workspace',str(root),
            '--mode','native','--from','0','--to','6','--port','0','--no-open'],stdout=subprocess.PIPE,stderr=log,text=True)
        try:
            with selectors.DefaultSelector() as poll:
                poll.register(server.stdout,selectors.EVENT_READ);assert poll.select(20)
                ready=json.loads(server.stdout.readline());assert ready.get('url'),ready
            script=r'''
import assert from 'node:assert/strict';import{writeFile}from'node:fs/promises';
const {Browser}=await import(HELPER),b=await Browser.launch(CHROME,OUT),api='window.movieStarPreview.stage';
async function capture(name){
 const clip=await b.evaluate(`(()=>{const r=document.querySelector('#stage iframe:not(.pending)').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,scale:1};})()`);
 const png=await b.send('Page.captureScreenshot',{format:'png',clip,captureBeyondViewport:true});await writeFile(OUT+'/'+name+'.png',Buffer.from(png.data,'base64'));
}
try{
 await b.send('Page.navigate',{url:URL});await b.wait('window.movieStarPreview?.is_current',15000);
 const layerState=await b.evaluate(`(()=>{const w=document.querySelector('#stage iframe:not(.pending)').contentWindow;return [...w.document.querySelectorAll('iframe')].map(f=>({opacity:w.getComputedStyle(f).opacity,background:w.getComputedStyle(f.parentElement).backgroundColor}));})()`);
 assert.deepEqual(layerState.map(l=>l.opacity),['1','0'],'Only the current scene is visible; decoded pictures do not need hidden video playback');
 await capture('transparent-intro');await b.evaluate(api+'.seek(3)');await capture('red-reply');
 if(STALL){
  await b.evaluate(api+'.seek(0)');await b.click('#play');await b.wait(api+'.time > .2');
  await b.evaluate('(()=>{const until=performance.now()+200;while(performance.now()<until){}})()');
  await b.wait(api+'.error',5000);
  assert.match(await b.evaluate(api+'.error'),/presentation deadline|frame is not ready/,'A missed frame must fail visibly, never silently reuse an older picture');
 }
}finally{await b.close();}
'''
            values={'HELPER':(Path(__file__).parents[2]/'experiments/live_browser_preview/browser.mjs').as_uri(),
                'CHROME':str(_chrome_path()),'OUT':str(tmp_path/'browser'),'URL':ready['url'],'STALL':stall}
            result=subprocess.run(['node','--input-type=module','-e',''.join('const '+k+'='+json.dumps(v)+';' for k,v in values.items())+script],capture_output=True,text=True,timeout=30)
            assert result.returncode==0,result.stdout+result.stderr
        finally:
            server.terminate()
            try:server.wait(timeout=9)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            server.stdout.close()
    assert tuple(_image(tmp_path/'browser/transparent-intro.png')[30,150])==(255,255,255)
    assert tuple(_image(tmp_path/'browser/red-reply.png')[30,150])==(255,0,0)
