"""Fullscreen keeps the whole composition, controls and player identities."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest
from moviestar.spatial_layout import browser_available, _chrome_path


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
@pytest.mark.parametrize('mode', ['native', 'rendered'])
@pytest.mark.parametrize('canvas', [(360, 640), (1280, 720)])
def test_fullscreen_preserves_composition_transport_and_revision(tmp_path, mode, canvas):
    width, height = canvas
    media = tmp_path/'media.mp4'
    if mode == 'rendered':
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
            f'color=c=blue:s={width}x{height}:r=24:d=3', '-c:v', 'libx264',
            '-threads', '1', '-pix_fmt', 'yuv420p', str(media)], check=True)
    active = ['A']
    def item(identity):
        return {'request_id': identity, 'state_id': 'sha256:'+identity*64,
            'status': 'ready', 'requested_at': 1, 'range': {'from': 0, 'to': 3},
            'profile': {'width': width, 'height': height, 'fps': 24}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            route = self.path.split('?', 1)[0].removeprefix('/projects/test/')
            kind = 'text/html'
            if route == '':
                body = files('moviestar').joinpath('preview_viewer.html').read_text().replace('data-preview-mode="rendered"', f'data-preview-mode="{mode}"').encode()
            elif route in {'preview_native.mjs', 'preview_fullscreen.mjs'}:
                body = files('moviestar').joinpath(route).read_bytes(); kind = 'text/javascript'
            elif route == 'state':
                body = json.dumps({'mode': mode, 'latest': item(active[0]), 'displayed': item(active[0]), 'history': [item('A'), item('B')]}).encode(); kind = 'application/json'
            elif route == 'advance':
                active[0] = 'B'; body = b'ok'
            elif route == 'stage':
                from urllib.parse import parse_qs, urlsplit
                identity = parse_qs(urlsplit(self.path).query)['request'][0]
                packet = {**item(identity), 'canvas': {'width': width, 'height': height}, 'fps': 24}
                body = ('<style>body{margin:0;background:blue}</style><div>Both cameras and captions</div><script>window.moviestarLiveStage={packet:'+json.dumps(packet)+',ready:Promise.resolve(),metrics:{finished:false},time:0,paused:true,plays:0,pauses:0,seeks:0,pause(){this.paused=true;this.pauses++},dispose(){},play:async function(){this.paused=false;this.plays++},seek:async function(t){this.time=t;this.seeks++}};</script>').encode()
            elif route.startswith('media/'):
                body = media.read_bytes(); kind = 'video/mp4'
                if self.headers.get('Range'):
                    bounds = self.headers['Range'].removeprefix('bytes=').split('-')
                    start, end = int(bounds[0]), int(bounds[1]) if bounds[1] else len(body)-1
                    self.send_response(206); self.send_header('Content-Type', kind)
                    self.send_header('Content-Range', f'bytes {start}-{end}/{len(body)}')
                    self.send_header('Accept-Ranges', 'bytes'); self.send_header('Content-Length', str(end-start+1))
                    self.end_headers(); self.wfile.write(body[start:end+1]); return
            elif route.startswith('fonts/'):
                body = files('moviestar').joinpath(route).read_bytes(); kind = 'font/ttf'
            else: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    script = r'''
import assert from 'node:assert/strict';
const {Browser}=await import(HELPER),b=await Browser.launch(CHROME,OUT);
try{
 await b.send('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
 await b.send('Page.navigate',{url:URL});
 await b.wait('window.movieStarPreview?.displayed?.request_id === "A"',10000);
 assert.ok(await b.evaluate('Boolean(document.getElementById("fullscreen"))'),'Whole-composition fullscreen control exists');
 await b.evaluate(`window.originalPlayer=document.querySelector('#stage iframe:not(.pending),#stage video:not(.pending)');window.transport=${MODE==='native'?'window.movieStarPreview.stage':'window.originalPlayer'};window.transportEvents=[];`);
 if(MODE==='native')await b.evaluate('transport.time=1;transport.paused=false;window.before={...transport}');
 else {
  await b.evaluate('transport.currentTime=1');await b.wait('!transport.seeking&&Math.abs(transport.currentTime-1)<.05',2000);
  await b.evaluate(`for(const name of ['play','pause','seeking'])transport.addEventListener(name,()=>transportEvents.push(name));`);
 }
 async function toggle(){await b.send('Runtime.evaluate',{expression:'document.getElementById("fullscreen").click()',userGesture:true});}
 await toggle();await b.wait('document.fullscreenElement?.id === "preview"',3000);
 await b.evaluate('document.fonts.ready');await new Promise(r=>setTimeout(r,100));
 const geometry=await b.evaluate(`(()=>{const stage=document.getElementById('stage'),rect=stage.getBoundingClientRect(),root=document.fullscreenElement;return{width:rect.width,height:rect.height,x:rect.x,y:rect.y,right:rect.right,bottom:rect.bottom,vw:innerWidth,vh:innerHeight,controls:root.contains(document.getElementById('version'))&&root.contains(document.getElementById('native-controls'))&&root.contains(document.getElementById('fullscreen')),same:originalPlayer===stage.querySelector('iframe:not(.pending),video:not(.pending)')};})()`);
 assert.ok(geometry.controls&&geometry.same,'Player and accessible controls stay inside fullscreen');
 assert.ok(geometry.x>=0&&geometry.y>=0&&geometry.right<=geometry.vw+1&&geometry.bottom<=geometry.vh+1,'Complete canvas fits viewport');
 assert.ok(Math.abs(geometry.width/geometry.height-CANVAS[0]/CANVAS[1])<.002,'Fullscreen preserves authored aspect ratio');
 if(MODE==='native')assert.ok(await b.evaluate('transport.time===1&&!transport.paused&&transport.plays===before.plays&&transport.pauses===before.pauses&&transport.seeks===before.seeks'),'Fullscreen never changes native transport');
 else {const transport=await b.evaluate('({paused:transport.paused,time:transport.currentTime,events:transportEvents})');assert.ok(transport.paused&&Math.abs(transport.time-1)<.05&&transport.events.length===0,'Fullscreen never changes rendered transport: '+JSON.stringify(transport));}
 await b.evaluate('fetch("advance")');await b.wait('window.movieStarPreview?.displayed?.request_id === "B"',5000);
 assert.ok(await b.evaluate('document.fullscreenElement?.id === "preview"'),'Edit replacement preserves fullscreen');
 await b.evaluate(`const selected=document.getElementById('version');selected.value='A';selected.dispatchEvent(new Event('change'));`);
 await b.wait('window.movieStarPreview?.displayed?.request_id === "A"',5000);
 assert.ok(await b.evaluate('document.fullscreenElement?.id === "preview"'),'Historical selection preserves fullscreen');
 await toggle();await b.wait('document.fullscreenElement === null',3000);
 assert.equal(await b.evaluate('window.movieStarPreview.displayed.request_id'),'A');
 if(MODE==='rendered'){
  await b.evaluate(`window.playingPlayer=document.querySelector('#stage video:not(.pending)');playingPlayer.currentTime=.5;`);
  await b.wait('!playingPlayer.seeking&&playingPlayer.currentTime>=.45',2000);
  await b.evaluate('playingPlayer.play()');
 }
 await toggle();await b.wait('document.fullscreenElement?.id === "preview"',3000);
 if(MODE==='rendered')assert.ok(await b.evaluate('!playingPlayer.paused&&playingPlayer===document.querySelector("#stage video:not(.pending)")'),'Playing video keeps playing through fullscreen');
 const screenshot=await b.send('Page.captureScreenshot',{format:'png'});
 await (await import('node:fs/promises')).writeFile(OUT+'/fullscreen.png',Buffer.from(screenshot.data,'base64'));
 await b.evaluate('document.exitFullscreen()');await b.wait('document.fullscreenElement === null',3000);
 await b.wait('document.getElementById("fullscreen").getAttribute("aria-pressed") === "false"',3000);
}finally{await b.close();}
'''
    try:
        helper = (Path(__file__).parents[2]/'experiments/live_browser_preview/browser.mjs').as_uri()
        values = {'HELPER': helper, 'CHROME': str(_chrome_path()), 'OUT': str(tmp_path/'browser'), 'URL': f'http://127.0.0.1:{server.server_port}/projects/test/', 'CANVAS': canvas, 'MODE': mode}
        prefix = ''.join('const '+k+'='+json.dumps(v)+';\n' for k,v in values.items())
        result = subprocess.run(['node', '--input-type=module', '-e', prefix+script], capture_output=True, text=True, timeout=40)
        assert result.returncode == 0, result.stdout+result.stderr
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
@pytest.mark.parametrize('supported', [True, False])
def test_fullscreen_rejection_and_unavailable_api_are_truthful(supported):
    source = files('moviestar').joinpath('preview_fullscreen.mjs').read_text()
    script = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const listeners={},button={addEventListener(name,fn){listeners[name]=fn;},setAttribute(name,value){this[name]=value;}},error={};
const stage={style:{removeProperty(){}},parentElement:{},querySelector(){throw Error('No player access outside fullscreen');}};
const preview={requestFullscreen:async()=>{throw Error('User gesture denied');}};
const doc={fullscreenElement:null,fullscreenEnabled:SUPPORTED,body:{dataset:{previewMode:'rendered'}},
 getElementById:id=>({preview,stage,fullscreen:button,'fullscreen-error':error})[id],addEventListener(){}};
vm.runInNewContext(SOURCE,{document:doc,window:{addEventListener(){}},ResizeObserver:class{observe(){}},MutationObserver:class{observe(){}}});
(async()=>{
 if(SUPPORTED){await listeners.click();assert.match(error.textContent,/Fullscreen/);assert.equal(doc.fullscreenElement,null);assert.equal(button['aria-pressed'],'false');}
 else{assert.equal(button.disabled,true);assert.match(button.title,/unavailable/);}
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', f'const SOURCE={json.dumps(source)},SUPPORTED={json.dumps(supported)};\n'+script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr
