"""Native PiP must show both source frames, route one voice and seek together."""
import base64
import gzip
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


def _exercise_pip(tmp_path, spatial, cuts, silent_listener, omit_end_callbacks, pixel_audit, *, independent_audio=False, unmute_source=False, mute_dialogue=False, fades=False):
    repo = Path(__file__).parents[2]; root = tmp_path/'project'
    subprocess.run([sys.executable, str(repo/'experiments/live_browser_preview/pip.py'), '--out', str(root),
        *(['--spatial'] if spatial else []), *(['--cuts'] if cuts else []),
        *(['--silent-listener'] if silent_listener else [])], check=True, capture_output=True, text=True, timeout=40)
    if independent_audio:
        subprocess.run([sys.executable,str(repo/'experiments/live_browser_preview/audio.py'),'--out',str(root),'--existing'],check=True,capture_output=True,timeout=30)
        if unmute_source:
            subprocess.run([sys.executable,'-m','moviestar.cli','audio','source','--workspace',str(root),'--unmute','--gain-db','-6'],check=True,capture_output=True,timeout=30)
    if independent_audio:
        from moviestar.spec import load_spec
        from moviestar.audio_surface import editable_audio_document
        document=editable_audio_document(load_spec(root)['audio_mix'])
        document['audio_mix']['tracks'][0].update(gain_db=-9,muted=mute_dialogue)
        if fades:
            document['audio_mix']['tracks'][0].update(fade_in=.2,fade_out=.2)
            document['audio_mix']['source_audio'].update(fade_in=.2,fade_out=.2)
        mix_path=root/'edited-mix.json';mix_path.write_text(json.dumps(document))
        subprocess.run([sys.executable,'-m','moviestar.cli','audio','set',str(mix_path),'--workspace',str(root)],check=True,capture_output=True,timeout=30)
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
 const mediaEvents=[];b.socket.addEventListener('message',event=>{const data=JSON.parse(event.data);if(data.method?.startsWith('Media.'))mediaEvents.push(data);});
 await b.send('Media.enable');
 let traceComplete;
 const traceDone=new Promise(resolve=>traceComplete=resolve);
 if(TRACE){
  b.socket.addEventListener('message',event=>{const data=JSON.parse(event.data);if(data.method==='Tracing.tracingComplete')traceComplete(data.params.stream);});
  await b.send('Tracing.start',{categories:'media,webaudio,toplevel',transferMode:'ReturnAsStream'});
 }

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
 await b.send('Page.navigate',{url:URL+(PIXEL_AUDIT?'?audit=1':'')+(INDEPENDENT?'&audit_pcm=1':'')});
 await b.wait('window.movieStarPreview?.is_current',20000);
 // Use the authored 360x640 raster at 1x for software-headless playback.
 await b.evaluate(`(async()=>{const s=document.getElementById('stage'),style=document.createElement('style');style.textContent='#stage{max-width:none!important;width:'+(360+s.offsetWidth-s.clientWidth)+'px!important}';document.head.append(style);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()`);
 const checkRaster=async()=>{
  const box=await b.evaluate(`(()=>{const r=document.querySelector('iframe:not(.pending)').getBoundingClientRect();return{width:r.width,height:r.height};})()`);
  assert.deepEqual(box,{width:360,height:640},'Fidelity/playback workload stays at the authored 1x canvas');
 };
 await checkRaster();
 for(const at of [0,1,2,3,4]){
  const s=await b.evaluate(`(async()=>{const api=window.movieStarPreview.stage;await api.seek(${at});return api.snapshot();})()`);
  assert.equal(s.videos.length,2);
  assert.deepEqual(s.videos.map(v=>v.audio_connected),SILENT_LISTENER?[true,false]:[true,true],'Only actual audio streams join the shared output graph');
  assert.equal(s.videos.filter(v=>v.audio_gain===1).length,INDEPENDENT?0:1);
  assert.equal(s.videos.filter(v=>v.audio_gain===0).length,INDEPENDENT?2:1);
  if(INDEPENDENT){assert.equal(s.audio.prepared,true);assert.equal(s.audio.tracks[0].id,'dialogue');}
  assert.deepEqual(s.videos.map(v=>v.source.number),CUTS&&at>=1&&at<3?[(2+at)*30,(1+at)*30]:[(1+at)*30,(2+at)*30]);
  assert.ok(s.videos.every(v=>v.ready_state>=2&&!v.seeking&&v.paused));
 }
 await b.evaluate('window.movieStarPreview.stage.seek(0)');
 await b.click('#play');
 await b.wait('window.movieStarPreview.stage.metrics.finished||window.movieStarPreview.stage.error',12000);
 if(TRACE){
  await b.send('Tracing.end');const stream=await traceDone,chunks=[];
  while(true){const part=await b.send('IO.read',{handle:stream});chunks.push(Buffer.from(part.data,part.base64Encoded?'base64':'utf8'));if(part.eof)break;}
  await b.send('IO.close',{handle:stream});await writeFile(OUT+'/trace.json',Buffer.concat(chunks));
 }
 await checkRaster();
 const m=await b.evaluate('window.movieStarPreview.stage.metrics');
 await writeFile(OUT+'/playback.json',JSON.stringify(m,null,2));
 if(INDEPENDENT){
  const asset=await b.evaluate('window.movieStarPreview.stage.packet.audio_plane.asset');
  await writeFile(OUT+'/audio-reference.wav',Buffer.from(await(await fetch(URL+'assets/'+asset)).arrayBuffer()));
 }

 await writeFile(OUT+'/media-events.json',JSON.stringify(mediaEvents,null,2));
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
 assert.ok(qualities.every(q=>q.dropped_frames===0),'Neither decoder drops frames: '+JSON.stringify(mediaEvents));
 if(!CUTS)assert.ok(qualities.every(q=>q.total_frames>=140),'Both decoders continue through the complete clip');
 if(PIXEL_AUDIT){
 const cueClock=TRACK_MUTED?{problems:m.beeps.length?['Muted dialogue produced audio cues']:[]}:measureCueClock(m,{fps:30,sourceStart:1,count:5,quality:qualities});
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
 if(FADES){
  const fadeAt=await b.evaluate('(async()=>{const api=window.movieStarPreview.stage;await api.seek(.1);return api.time;})()');
  assert.ok(Math.abs(fadeAt-.1)<1/30,'Seek lands within the existing frame tolerance');await b.click('#play');
  await b.wait('window.movieStarPreview.stage.time>.35',3000);await b.click('#pause');
  const seeked=await b.evaluate('window.movieStarPreview.stage.snapshot()');
  assert.ok(Math.abs(seeked.audio.started_offset-fadeAt)<1e-6,'Seek inside fade preserves its position');
  assert.equal(seeked.audio.playing,false);
  await b.evaluate('window.movieStarPreview.stage.seek(5-1/30)');
 }
 const final=await b.evaluate('window.movieStarPreview.stage.snapshot()');
 assert.deepEqual(final.videos.map(v=>v.source.number),[179,209],'Both final frames are held');
 await b.click('#restart');await b.click('#play');await b.click('#pause');
 const paused=await b.evaluate('window.movieStarPreview.stage.snapshot()');assert.ok(paused.videos.every(v=>v.paused));
 if(INDEPENDENT){assert.equal(paused.audio.playing,false);await b.evaluate('new Promise(resolve=>setTimeout(resolve,100))');assert.equal(await b.evaluate('window.movieStarPreview.stage.snapshot().audio.playing'),false,'Pause cancels pending audio startup');}
 // Capture after timing: CDP screenshots can perturb headless frame scheduling.
 await b.evaluate('window.movieStarPreview.stage.seek(0)');
 await checkRaster();
 const clip=await b.evaluate(`(()=>{const r=document.querySelector('iframe:not(.pending)').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,scale:1};})()`);
 const screenshot=await b.send('Page.captureScreenshot',{format:'png',clip,captureBeyondViewport:true});
 await writeFile(OUT+'/native.png',Buffer.from(screenshot.data,'base64'));
 await checkRaster();
}finally{await b.close();}
'''
            values = {'FADES':fades,'TRACK_MUTED':mute_dialogue,'INDEPENDENT':independent_audio,'HELPER': (repo/'experiments/live_browser_preview/browser.mjs').as_uri(), 'CHROME': chrome,
                      'OUT': str(tmp_path/'browser'), 'URL': ready['url'], 'CUTS': cuts,
                      'CUE_HELPER': (repo/'experiments/live_browser_preview/cue_clock.mjs').as_uri(),
                      'OMIT_END_CALLBACKS': omit_end_callbacks, 'PIXEL_AUDIT': pixel_audit, 'SILENT_LISTENER': silent_listener, 'TRACE': os.environ.get('MOVIESTAR_NATIVE_TRACE') == '1'}
            prefix = ''.join('const '+key+'='+json.dumps(value)+';\n' for key, value in values.items())
            result = subprocess.run(['node', '--input-type=module', '-e', prefix+script], capture_output=True, text=True, timeout=40)
            # Diagnostic-only CI reruns preserve a compressed trace in job logs;
            # the original acceptance run remains failed. Fixtures are generated.
            trace = tmp_path/'browser/trace.json'
            if os.environ.get('MOVIESTAR_NATIVE_TRACE') == '1' and trace.exists():
                encoded = base64.b64encode(gzip.compress(trace.read_bytes())).decode('ascii')
                print('NATIVE_TRACE_BEGIN '+tmp_path.name, flush=True)
                for offset in range(0, len(encoded), 5000):
                    print('NATIVE_TRACE_DATA '+encoded[offset:offset+5000], flush=True)
                print('NATIVE_TRACE_END', flush=True)
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


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
@pytest.mark.parametrize('spatial,cuts,silent_listener,omit_end_callbacks,pixel_audit', [
    (False, False, False, False, True), (True, False, False, False, True),
    (False, True, False, False, True), (True, True, False, False, True),
    (False, False, True, False, True), (False, True, False, True, True),
    (True, False, False, False, False), (False, True, False, False, False)])
def test_actual_pip_prepares_both_videos_and_keeps_clocks_and_routing(tmp_path, spatial, cuts, silent_listener, omit_end_callbacks, pixel_audit):
    return _exercise_pip(tmp_path,spatial,cuts,silent_listener,omit_end_callbacks,pixel_audit)


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
@pytest.mark.parametrize('cuts,unmute,muted,fades', [(False,False,False,False),(True,False,False,False),(True,True,False,False),(True,False,True,False),(True,False,False,True),(True,True,False,True)])
def test_independent_audio_matches_actual_output_across_picture_cuts(tmp_path,cuts,unmute,muted,fades):
    _exercise_pip(tmp_path,False,cuts,False,False,True,independent_audio=True,unmute_source=unmute,mute_dialogue=muted,fades=fades)
    import io
    m=json.loads((tmp_path/'browser/playback.json').read_text());a=m['prepared_audio']
    with av.open(str(tmp_path/'browser/audio-reference.wav')) as container:
        resampler=av.AudioResampler(format='fltp',layout='stereo',rate=48000)
        expected=np.concatenate([f.to_ndarray() for src in container.decode(audio=0) for f in resampler.resample(src)],axis=1)
    capture=m['audio_pcm'];pcm=np.asarray(capture['samples'])
    index=round((capture['start_context_time']-a['started_context_time']+a['started_offset'])*48000)
    # Device scheduling quantizes clock timestamps to a sample frame.
    candidates=[]
    for shift in (-1,0,1):
        begin=index+shift;left=max(0,-begin)
        # Native end/Pause is explicitly approximate, unlike export trim.
        # Compare the stable playing interval; one output quantum brackets
        # the stop request. Internal camera cuts remain fully compared.
        stop=round((m['pause_context_time']-capture['start_context_time'])*48000)
        right=min(pcm.shape[1],expected.shape[1]-begin,stop-128)
        candidates.append((pcm[:,left:right]-expected[:,begin+left:begin+right]).ravel())
    error=min(candidates,key=lambda e:np.sqrt(np.mean(e*e)))
    assert len(error)>4.8*48000*2
    assert abs(error).max()<1e-5, (abs(error).max(),np.sqrt(np.mean(error*error)))
    end=round((m['pause_context_time']-a['started_context_time']+a['started_offset'])*48000)
    assert abs(min(end,expected.shape[1])-expected.shape[1])<=.05*48000
    tail=round((m['pause_context_time']-capture['start_context_time'])*48000)+128
    if tail<pcm.shape[1]:assert abs(pcm[:,tail:]).max()<1e-7
