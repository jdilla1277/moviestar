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
@pytest.mark.parametrize('spatial', [False, True])
@pytest.mark.parametrize('cuts', [False, True])
def test_actual_pip_prepares_both_videos_and_keeps_clocks_and_routing(tmp_path, spatial, cuts):
    repo = Path(__file__).parents[2]; root = tmp_path/'project'
    subprocess.run([sys.executable, str(repo/'experiments/live_browser_preview/pip.py'), '--out', str(root),
        *(['--spatial'] if spatial else []), *(['--cuts'] if cuts else [])], check=True, capture_output=True, text=True, timeout=40)
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
const {Browser}=await import(HELPER),b=await Browser.launch(CHROME,OUT);
try{
 await b.send('Emulation.setDeviceMetricsOverride',{width:1280,height:1800,deviceScaleFactor:1,mobile:false});
 await b.send('Page.navigate',{url:URL+'?audit=1'});
 await b.wait('window.movieStarPreview?.is_current',20000);
 for(const at of [0,1,2,3,4]){
  const s=await b.evaluate(`(async()=>{const api=window.movieStarPreview.stage;await api.seek(${at});return api.snapshot();})()`);
  assert.equal(s.videos.length,2);
  assert.deepEqual(s.videos.map(v=>v.source.number),CUTS&&at>=1&&at<3?[(2+at)*30,(1+at)*30]:[(1+at)*30,(2+at)*30]);
  assert.ok(s.videos.every(v=>v.ready_state>=2&&!v.seeking&&v.paused));
 }
 await b.evaluate('window.movieStarPreview.stage.seek(0)');await b.click('#play');
 await b.wait('window.movieStarPreview.stage.metrics.finished||window.movieStarPreview.stage.error',12000);
 const m=await b.evaluate('window.movieStarPreview.stage.metrics');
 await writeFile(OUT+'/playback.json',JSON.stringify(m,null,2));
 assert.equal(m.error,null);assert.equal(m.stalls,0);assert.equal(m.flashes.length,5);assert.equal(m.beeps.length,5);
 m.flashes.forEach((f,i)=>assert.ok(Math.abs(m.beeps[i].display_time-f.display_time)<=50,'Selected voice stays synchronized'));
 const videoFrames=CUTS?(await b.evaluate(`[...document.querySelector('iframe:not(.pending)').contentDocument.querySelectorAll('iframe')].flatMap(frame=>frame.contentWindow.moviestarLiveStage.metrics.video_frames??[])`)):m.video_frames;
 const qualities=CUTS?(await b.evaluate(`[...document.querySelector('iframe:not(.pending)').contentDocument.querySelectorAll('iframe')].flatMap(frame=>frame.contentWindow.moviestarLiveStage.metrics.video_quality??[])`)):m.video_quality;
 await writeFile(OUT+'/video-frames.json',JSON.stringify({videoFrames,qualities},null,2));
 assert.equal(qualities.length,CUTS?6:2);
 assert.ok(qualities.every(q=>q.dropped_frames===0),'Neither decoder drops frames');
 if(!CUTS)assert.ok(qualities.every(q=>q.total_frames>=140),'Both decoders continue through the complete clip');
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
                      'OUT': str(tmp_path/'browser'), 'URL': ready['url'], 'CUTS': cuts}
            prefix = ''.join('const '+key+'='+json.dumps(value)+';\n' for key, value in values.items())
            result = subprocess.run(['node', '--input-type=module', '-e', prefix+script], capture_output=True, text=True, timeout=40)
            assert result.returncode == 0, result.stdout+result.stderr
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
