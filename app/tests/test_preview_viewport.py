"""The shipped native viewer must fit the complete authored canvas on screen."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import subprocess
import threading

import pytest
from moviestar.spatial_layout import browser_available, _chrome_path


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
@pytest.mark.parametrize('canvas', [(360, 640), (1280, 720)])
def test_native_viewer_fits_and_centers_canvas_on_resize(tmp_path, canvas):
    width, height = canvas
    item = {'request_id': 'viewport', 'state_id': 'sha256:'+'a'*64,
            'status': 'ready', 'requested_at': 1, 'range': {'from': 0, 'to': 5}}
    packet = {**item, 'canvas': {'width': width, 'height': height}, 'fps': 30}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            route = self.path.split('?', 1)[0].removeprefix('/projects/test/')
            kind = 'text/html'
            if route == '':
                body = files('moviestar').joinpath('preview_viewer.html').read_text().replace('data-preview-mode="rendered"', 'data-preview-mode="native"').encode()
            elif route == 'preview_native.mjs':
                body = files('moviestar').joinpath(route).read_bytes(); kind = 'text/javascript'
            elif route == 'state':
                body = json.dumps({'mode': 'native', 'latest': item, 'displayed': item, 'history': [item]}).encode(); kind = 'application/json'
            elif route == 'stage':
                body = ('<style>body{margin:0;background:#16307d}</style><script>window.moviestarLiveStage={packet:'+json.dumps(packet)+',ready:Promise.resolve(),metrics:{finished:false},time:0,pause(){},dispose(){},play:async()=>{},seek:async()=>{}};</script>').encode()
            elif route.startswith('fonts/'):
                body = files('moviestar').joinpath(route).read_bytes(); kind = 'font/ttf'
            else: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        helper = (Path(__file__).parents[2]/'experiments/live_browser_preview/browser.mjs').as_uri()
        script = r'''
import assert from 'node:assert/strict';
const {Browser}=await import(HELPER),b=await Browser.launch(CHROME,OUT);
try{
 for(const [width,height] of [[1280,900],[390,844],[1440,1000]]){
  await b.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
  if(!await b.evaluate('Boolean(window.movieStarPreview)'))await b.send('Page.navigate',{url:URL});
  await b.wait('window.movieStarPreview?.is_current',10000);
  await b.evaluate('document.fonts.ready');
  await new Promise(r=>setTimeout(r,100));
  const box=await b.evaluate(`(()=>{const stage=document.getElementById('stage'),frame=stage.querySelector('iframe:not(.pending)'),s=stage.getBoundingClientRect(),f=frame.getBoundingClientRect();return{stage:{x:s.x,y:s.y,width:s.width,height:s.height,bottom:s.bottom,right:s.right},frame:{width:f.width,height:f.height},viewport:{width:innerWidth,height:innerHeight}};})()`);
  assert.ok(box.stage.bottom<=height-20,'Complete canvas fits below the controls');
  assert.ok(box.stage.x>=0&&box.stage.right<=width,'No horizontal overflow');
  assert.ok(Math.abs(box.stage.x+box.stage.width/2-width/2)<=2,'Preview is centered');
  assert.ok(Math.abs(box.frame.width/box.frame.height-CANVAS[0]/CANVAS[1])<.001,'Authored picture proportions survive resize');
  assert.ok(box.frame.height<=box.stage.height+1,'Full frame is visible');
 }
}finally{await b.close();}
'''
        prefix = ''.join('const '+k+'='+json.dumps(v)+';\n' for k,v in {'HELPER': helper, 'CHROME': str(_chrome_path()), 'OUT': str(tmp_path/'browser'), 'URL': f'http://127.0.0.1:{server.server_port}/projects/test/', 'CANVAS': canvas}.items())
        result = subprocess.run(['node', '--input-type=module', '-e', prefix+script], capture_output=True, text=True, timeout=35)
        assert result.returncode == 0, result.stdout+result.stderr
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
