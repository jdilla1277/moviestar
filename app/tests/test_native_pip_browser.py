"""Native PiP must show both source frames, route one voice and seek together."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

import pytest
import av
import numpy as np

from moviestar.spatial_layout import browser_available, _chrome_path


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
@pytest.mark.parametrize('spatial,cuts,silent_listener,omit_end_callbacks,pixel_audit', [
    (False, False, False, False, True), (True, False, False, False, True),
    (False, True, False, False, True), (True, True, False, False, True),
    (False, False, True, False, True), (False, True, False, True, True),
    (True, False, False, False, False), (False, True, False, False, False)])
def test_actual_pip_prepares_both_videos_and_keeps_clocks_and_routing(tmp_path, spatial, cuts, silent_listener, omit_end_callbacks, pixel_audit):
    repo = Path(__file__).parents[2]; root = tmp_path/'project'
    subprocess.run([sys.executable, str(repo/'experiments/live_browser_preview/pip.py'), '--out', str(root),
        *(['--spatial'] if spatial else []), *(['--cuts'] if cuts else []),
        *(['--silent-listener'] if silent_listener else [])], check=True, capture_output=True, text=True, timeout=40)
    chrome = os.environ.get('MOVIESTAR_CHROME_PATH') or str(_chrome_path())
    with (tmp_path/'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root),
            '--mode', 'native', '--from', '0', '--to', '5', '--port', '0', '--no-open'], stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                assert selector.select(15), 'Native server did not start'
                line = server.stdout.readline(); ready = json.loads(line)
                assert ready.get('url'), line
            script = r'''
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const {measureCueClock}=await import(CUE_HELPER);
const {Browser}=await import(HELPER),b=await Browser.launch(CHROME,OUT);
try{
 await b.send('Emulation.setDeviceMetricsOverride',{width:1280,height:1800,deviceScaleFactor:1,mobile:false});
 if(OMIT_END_CALLBACKS)await b.send('Page.addScriptToEvaluateOnNewDocument',{source:`
  const request=HTMLVideoElement.prototype.requestVideoFrameCallback;
  HTMLVideoElement.prototype.requestVideoFrameCallback=function(callback){
   const video=this;
   return request.call(video,function deliver(now,frame){
    if(!video.paused&&Math.abs(frame.mediaTime-Math.floor(frame.mediaTime)-29/30)<1e-5){
     const path=window.moviestarScene.entries.find(e=>e.element===video.parentElement).node.path;
     (window.omittedEndCallbacks??=[]).push({path,media_time:frame.mediaTime});
     request.call(video,deliver);
    }else callback(now,frame);
   });
  };
 `});
 await b.send('Page.navigate',{url:URL+(PIXEL_AUDIT?'?audit=1':'')});
 await b.wait('window.movieStarPreview?.is_current',20000);
 // Use the authored 360x640 raster at 1x for software-headless playback.
 await b.evaluate(`(async()=>{const s=document.getElementById('stage');s.style.maxWidth='none';s.style.width=(360+s.offsetWidth-s.clientWidth)+'px';await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()`);
 for(const at of [0,1,2,3,4]){
  const s=await b.evaluate(`(async()=>{const api=window.movieStarPreview.stage;await api.seek(${at});return api.snapshot();})()`);
  assert.equal(s.videos.length,2);
  assert.equal(s.videos.filter(v=>v.audio_gain===1).length,1);
  assert.equal(s.videos.filter(v=>v.audio_gain===0).length,1);
  assert.deepEqual(s.videos.map(v=>v.source.number),CUTS&&at>=1&&at<3?[(2+at)*30,(1+at)*30]:[(1+at)*30,(2+at)*30]);
  assert.ok(s.videos.every(v=>v.ready_state>=2&&!v.seeking&&v.paused));
 }
 await b.evaluate('window.movieStarPreview.stage.seek(0)');await b.click('#play');
 await b.wait('window.movieStarPreview.stage.metrics.finished||window.movieStarPreview.stage.error',12000);
 const m=await b.evaluate('window.movieStarPreview.stage.metrics');
 await writeFile(OUT+'/playback.json',JSON.stringify(m,null,2));
 assert.equal(m.error,null);assert.equal(m.stalls,0);
 if(OMIT_END_CALLBACKS){
  const omitted=await b.evaluate(`[...document.querySelector('iframe:not(.pending)').contentDocument.querySelectorAll('iframe')].flatMap(f=>f.contentWindow.omittedEndCallbacks??[])`);
  assert.ok(omitted.some(f=>f.path.includes('main'))&&omitted.some(f=>f.path.includes('inset')),
   'Suppress callbacks for both video roles; natural callback counts are not frame counts');
 }
 if(CUTS){
  const first=m.frames[0],last=m.frames.at(-1);
  const start=first.display_epoch-first.result_time*1000,end=last.display_epoch-last.result_time*1000;
  assert.ok(Math.abs(end-start)<=50,`Cuts preserve the program clock within 50ms: ${end-start}ms`);
 }
 const videoFrames=CUTS?(await b.evaluate(`[...document.querySelector('iframe:not(.pending)').contentDocument.querySelectorAll('iframe')].flatMap(frame=>frame.contentWindow.moviestarLiveStage.metrics.video_frames??[])`)):(m.video_frames??[]);
 const qualities=CUTS?(await b.evaluate(`[...document.querySelector('iframe:not(.pending)').contentDocument.querySelectorAll('iframe')].flatMap(frame=>frame.contentWindow.moviestarLiveStage.metrics.video_quality??[])`)):m.video_quality;
 const qualityEvents=CUTS?(await b.evaluate(`[...document.querySelector('iframe:not(.pending)').contentDocument.querySelectorAll('iframe')].flatMap(frame=>frame.contentWindow.moviestarLiveStage.metrics.quality_events??[])`)):m.quality_events;
 const firstSamples=Object.fromEntries([...new Set(videoFrames.map(f=>JSON.stringify(f.path)))].map(path=>[path,videoFrames.filter(f=>JSON.stringify(f.path)===path).slice(0,25)]));
 await writeFile(OUT+'/video-frames.json',JSON.stringify({videoFrames,qualities,qualityEvents,firstSamples},null,2));
 assert.equal(qualities.length,CUTS?6:2);
 assert.ok(qualities.every(q=>q.dropped_frames===0),'Neither decoder drops frames');
 if(!CUTS)assert.ok(qualities.every(q=>q.total_frames>=140),'Both decoders continue through the complete clip');
 if(PIXEL_AUDIT){
 const cueClock=measureCueClock(m,{fps:30,sourceStart:1,count:5,quality:qualities});
 await writeFile(OUT+'/cue-clock.json',JSON.stringify(cueClock,null,2));
 assert.deepEqual(cueClock.problems,[],'Selected voice follows the presented-frame clock within 50ms');
 const paths=[...new Set(videoFrames.map(f=>JSON.stringify(f.path)))];assert.equal(paths.length,CUTS?6:2);
 const frames=paths.map(path=>videoFrames.filter(f=>JSON.stringify(f.path)===path));
 frames.forEach((list,i)=>{
  // rVFC can omit callbacks even when getVideoPlaybackQuality reports no dropped
  // presentations. Sampled pixels may advance one frame while JS reads them.
  assert.ok(list.length>=20,'Both sources keep producing frame observations');
  assert.ok(list.every(f=>Math.min(Math.abs(f.number-Math.round(f.media_time*30)%240),240-Math.abs(f.number-Math.round(f.media_time*30)%240))<=1),'Sampled source pixels stay within one frame of the callback clock');
 });
 const zero=frames.map(list=>list[0].display_time-(list[0].media_time-(list[0].path.includes('inset')?2:1))*1000);
 if(!CUTS)assert.ok(Math.abs(zero[0]-zero[1])<=50+1e-6,'Both displayed video clocks start within 50ms');
 }else{
  assert.equal(m.beeps.length,5,'Normal playback still produces all audio cues');
  assert.ok(m.frames.length>=20,'Normal playback keeps presenting source frames');
 }
 const final=await b.evaluate('window.movieStarPreview.stage.snapshot()');
 assert.deepEqual(final.videos.map(v=>v.source.number),[179,209],'Both final frames are held');
 await b.click('#restart');await b.click('#play');await b.click('#pause');
 const paused=await b.evaluate('window.movieStarPreview.stage.snapshot()');assert.ok(paused.videos.every(v=>v.paused));
 // Capture after timing: CDP screenshots can perturb headless frame scheduling.
 await b.evaluate(`(async()=>{await window.movieStarPreview.stage.seek(0);const stage=document.getElementById('stage');stage.style.maxWidth='none';stage.style.width=(360+stage.offsetWidth-stage.clientWidth)+'px';await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()`);
 const clip=await b.evaluate(`(()=>{const r=document.querySelector('iframe:not(.pending)').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,scale:1};})()`);
 const screenshot=await b.send('Page.captureScreenshot',{format:'png',clip,captureBeyondViewport:true});
 await writeFile(OUT+'/native.png',Buffer.from(screenshot.data,'base64'));
}finally{await b.close();}
'''
            values = {'HELPER': (repo/'experiments/live_browser_preview/browser.mjs').as_uri(), 'CHROME': chrome,
                      'OUT': str(tmp_path/'browser'), 'URL': ready['url'], 'CUTS': cuts,
                      'CUE_HELPER': (repo/'experiments/live_browser_preview/cue_clock.mjs').as_uri(),
                      'OMIT_END_CALLBACKS': omit_end_callbacks, 'PIXEL_AUDIT': pixel_audit}
            prefix = ''.join('const '+key+'='+json.dumps(value)+';\n' for key, value in values.items())
            result = subprocess.run(['node', '--input-type=module', '-e', prefix+script], capture_output=True, text=True, timeout=40)
            diagnostics = {}
            for name in ('playback.json', 'video-frames.json', 'cue-clock.json'):
                path = tmp_path/'browser'/name
                if path.exists():
                    data = json.loads(path.read_text())
                    if name == 'playback.json':
                        data['first_frames'] = data.get('frames', [])[:25]
                        data = {key: data.get(key) for key in ('first_frames', 'error', 'stalls', 'waiting_events', 'cuts', 'quality_events', 'flashes', 'beeps', 'video_quality', 'paused_quality', 'settled_quality', 'initial_presented', 'audio_device')}
                    if name == 'video-frames.json': data = {key: data.get(key) for key in ('qualities', 'qualityEvents', 'firstSamples')}
                    diagnostics[name] = data
            assert result.returncode == 0, result.stdout+result.stderr+'\n'+json.dumps(diagnostics, indent=2)
            if not spatial and not cuts:
                reference = tmp_path/'export-frame.png'
                subprocess.run([sys.executable, '-m', 'moviestar.cli', 'screenshot', '--at', '0', '--out', str(reference)],
                    cwd=root, capture_output=True, check=True, timeout=30)
                def image(path):
                    with av.open(str(path)) as container:
                        return next(container.decode(video=0)).to_ndarray(format='rgb24')
                actual, expected = image(tmp_path/'browser/native.png'), image(reference)
                assert actual.shape == expected.shape == (640, 360, 3)
                # Interior picture, content edges, border and rounded-mask corners.
                # Exclude AA boundary pixels: Chrome and FFmpeg rasterize those differently.
                points = [(180, 320), (20, 100), (245, 100), (300, 60), (344, 16),
                          (234, 30), (240, 12), (250, 10), (300, 10), (350, 100)]
                for x, y in points:
                    assert np.max(np.abs(actual[y, x].astype(int)-expected[y, x].astype(int))) <= 8, (x, y)
        finally:
            server.terminate()
            try: server.wait(timeout=9)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            server.stdout.close()
