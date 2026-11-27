"""Range I/O must refill ahead without stopping already-buffered pictures."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import subprocess
import threading

import pytest
from moviestar.native_decode import packet_index
from moviestar.spatial_layout import browser_available,_chrome_path

@pytest.mark.skipif(not browser_available(),reason='Chrome and Node required')
def test_slow_packet_reads_do_not_block_buffered_picture_presentation(tmp_path):
    source=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=640x360:r=30:d=3',
        '-c:v','libx264','-threads','1','-b:v','4M','-minrate','4M','-maxrate','4M','-bufsize','4M',
        '-x264-params','nal-hrd=cbr:force-cfr=1','-g','30','-bf','2',str(source)],check=True)
    data=source.read_bytes();assert len(data)>3*131072
    index=packet_index(source,0,3,30)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            name=self.path.split('?',1)[0].removeprefix('/')
            if name=='source.mp4':
                left,right=map(int,self.headers['Range'].removeprefix('bytes=').split('-'));right=min(right,len(data)-1)
                body=data[left:right+1];self.send_response(206);self.send_header('Content-Range',f'bytes {left}-{right}/{len(data)}')
            elif name=='index.json':body=json.dumps(index).encode();self.send_response(200)
            elif name in {'preview_native_decode.mjs','preview_native_decoded_media.mjs'}:
                body=files('moviestar').joinpath(name).read_bytes();self.send_response(200);self.send_header('Content-Type','text/javascript')
            else:body=b'<!doctype html><body></body>';self.send_response(200);self.send_header('Content-Type','text/html')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    script=r'''
import assert from 'node:assert/strict';
const {Browser}=await import(HELPER),b=await Browser.launch(CHROME,OUT);
try{
 await b.send('Page.navigate',{url:URL});
 await b.evaluate(`(async()=>{
  const {createDecodedMedia}=await import('/preview_native_decoded_media.mjs'),index=await(await fetch('/index.json')).json();
  const original=window.fetch;window.progress=[];
  window.fetch=async(...args)=>{
   if(window.started!==undefined&&String(args[0]).includes('source.mp4')){
    const before=player.getVideoPlaybackQuality().totalVideoFrames;
    await new Promise(r=>setTimeout(r,100));progress.push(player.getVideoPlaybackQuality().totalVideoFrames-before);
   }
   return original(...args);
  };
  window.player=createDecodedMedia({video_decode:index},{offset:0,clock:()=>started===undefined?0:(performance.now()-started)/1000,
   signal:new AbortController().signal,onFailure:error=>{window.failure=error.message;player.pause();}});
  document.body.append(player);const ready=new Promise(r=>player.addEventListener('canplay',r,{once:true}));
  player.src='/source.mp4';await ready;
 })()`);
 await b.evaluate('window.started=performance.now();player.play()');
 await b.wait('player.paused||window.failure',8000);
 const result=await b.evaluate('({error:window.failure??null,quality:player.getVideoPlaybackQuality(),progress,owned:player.decodedMetrics})');
 assert.equal(result.error,null);assert.equal(result.quality.droppedVideoFrames,0);assert.ok(result.quality.totalVideoFrames>=89);
 assert.ok(result.progress.length>=2,'Exercise several delayed range reads');
 assert.ok(result.progress.some(n=>n>=2),'Buffered pictures must continue presenting during pending range I/O');
 assert.ok(result.owned.peak_frames<=8);await b.evaluate('player.load()');
}finally{await b.close();}
'''
    values={'HELPER':(Path(__file__).parents[2]/'experiments/live_browser_preview/browser.mjs').as_uri(),
            'CHROME':str(_chrome_path()),'OUT':str(tmp_path/'browser'),'URL':f'http://127.0.0.1:{server.server_port}/'}
    try:
        result=subprocess.run(['node','--input-type=module','-e',''.join('const '+k+'='+json.dumps(v)+';' for k,v in values.items())+script],capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stdout+result.stderr
    finally:server.shutdown();server.server_close();thread.join()
