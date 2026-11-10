// Real CLI save -> fresh visible browser revision; no completed video export.
import {spawn} from 'node:child_process';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {Browser,sleep} from './browser.mjs';

const [url,root,out,python,chrome]=process.argv.slice(2);
if(!chrome)throw Error('usage: benchmark.mjs URL PROJECT OUT PYTHON CHROME');
await mkdir(out,{recursive:true});
const result={cold:{},edits:[],checks:[],problems:[]};
const check=(condition,message)=>{result.checks.push({check:message,passed:Boolean(condition)});if(!condition)result.problems.push(message);};
const persist=()=>writeFile(join(out,'browser-report.json'),JSON.stringify(result,null,2));
async function cli(args){
  const child=spawn(python,['-m','moviestar.cli',...args],{cwd:root,stdio:['ignore','pipe','pipe']});
  let stdout='',stderr='';child.stdout.on('data',value=>stdout+=value);child.stderr.on('data',value=>stderr+=value);
  const timer=setTimeout(()=>child.kill('SIGKILL'),15000);
  const code=await new Promise((a,b)=>{child.on('error',b);child.on('exit',a);});clearTimeout(timer);
  if(code!==0)throw Error(`CLI exit ${code}: ${stdout} ${stderr}`);
  return JSON.parse(stdout);
}
await persist();
const browser=await Browser.launch(chrome,out);
try{
  const fixture=JSON.parse(await readFile(join(out,'case.json'),'utf8'));
  const real=fixture.media_kind==='real';
  const barcode=at=>Math.round(at*fixture.fps)%fixture.barcode_period_frames;
  const requests=new Map();
  await browser.send('Network.enable');
  browser.socket.addEventListener('message',event=>{
    const value=JSON.parse(event.data),p=value.params;
    if(value.method==='Network.requestWillBeSent'&&p.request.url.includes('/assets/')){
      requests.set(p.requestId,{range:p.request.headers.Range??p.request.headers.range,encoded_bytes:0});
    }else if(value.method==='Network.responseReceived'&&requests.has(p.requestId)){
      Object.assign(requests.get(p.requestId),{status:p.response.status,mime:p.response.mimeType,
        content_range:p.response.headers['Content-Range']??p.response.headers['content-range']});
    }else if(value.method==='Network.dataReceived'&&requests.has(p.requestId)){
      requests.get(p.requestId).encoded_bytes+=p.encodedDataLength;
    }
    result.source_requests=[...requests.values()].filter(item=>item.mime==='video/mp4');
  });
  const coldAt=Date.now();await browser.send('Page.navigate',{url:url+'?audit=1'});
  await browser.wait('window.moviestarLive?.is_current',15000);
  result.cold.navigation_to_visible_ms=Date.now()-coldAt;
  result.cold.event=await browser.evaluate('window.moviestarLive.events.at(-1)');
  result.cold.source_buffering=await browser.evaluate('window.moviestarLive.stage.metrics.source_buffering===true');
  if(fixture.require_source_buffering)check(result.cold.source_buffering,'memory source buffering controls this preview');
  const reference=JSON.parse(await readFile(join(out,'canonical.json'),'utf8'));
  result.parity=[];
  for(const expected of reference){
    const seekAt=Date.now();
    const actual=await browser.evaluate(`(async()=>{await window.moviestarLive.stage.seek(${expected.at});return window.moviestarLive.stage.snapshot();})()`);
    const seekMs=Date.now()-seekAt;
    const wanted=new Map(expected.boxes.map(box=>[JSON.stringify(box.path),box]));
    let geometry=0,glyph=0,opacity=0;
    for(const box of actual.boxes){
      const base=wanted.get(JSON.stringify(box.path));
      if(!base)throw Error('Native node is absent from canonical layout');
      for(const key of ['x','y','width','height'])geometry=Math.max(geometry,Math.abs(box.rect[key]-base.rect[key]));
      opacity=Math.max(opacity,Math.abs(box.opacity-base.effective_opacity));
      if(base.text_lines){
        check(box.text_lines.length===base.text_lines.length,`caption/text line count at ${expected.at}s: ${box.path.join('/')}`);
        for(let i=0;i<Math.min(box.text_lines.length,base.text_lines.length);i++)for(const key of ['x','y','width','height'])glyph=Math.max(glyph,Math.abs(box.text_lines[i][key]-base.text_lines[i][key]));
      }
    }
    let sourcePixels;
    if(real){
      sourcePixels=await browser.evaluate(`(async()=>{const v=document.querySelector('iframe').contentDocument.querySelector('video'),c=document.createElement('canvas');c.width=v.videoWidth;c.height=v.videoHeight;const context=c.getContext('2d');context.drawImage(v,0,0);const hash=await crypto.subtle.digest('SHA-256',context.getImageData(0,0,c.width,c.height).data);return{time:v.currentTime,sha256:[...new Uint8Array(hash)].map(b=>b.toString(16).padStart(2,'0')).join('')};})()`);
      check(Math.abs(sourcePixels.time-fixture.source_start-expected.at)<=1e-3,`real source clock at ${expected.at}s`);
      if(fixture.reference_hashes)check(sourcePixels.sha256===fixture.reference_hashes[String(expected.at)],`decoded source pixels match baseline at ${expected.at}s`);
    }else{
      const source=barcode(fixture.source_start+expected.at);
      check(actual.source.number===source,`source barcode ${source} at ${expected.at}s`);
    }
    check(geometry<=.2&&glyph<=1&&opacity<=.001,`canonical geometry/motion/opacity/glyph bounds at ${expected.at}s`);
    result.parity.push({at:expected.at,seek_ms:seekMs,max_geometry_px:geometry,max_glyph_px:glyph,max_opacity_error:opacity,source_frame:real?undefined:actual.source.number,source_pixels:sourcePixels,
      caption_lines:actual.boxes.filter(box=>box.kind==='caption').map(box=>box.text_lines.length)});
  }
  await browser.evaluate('window.moviestarLive.stage.seek(0)');
  const specPath=join(root,'moviestar/spec.json'),original=JSON.parse(await readFile(specPath,'utf8')).spatial;
  const initial=await browser.evaluate('window.moviestarLive.current');
  for(let i=0;i<20;i++){
    const model=structuredClone(original),title='CLI edit '+(i+1);
    const children=model.scenes.review.layout.children;
    children.find(node=>node.id==='title').content.text=title;
    children.find(node=>node.id==='title').style.color=i%2?'#ffb81c':'#ffffff';
    children.find(node=>node.instance_id==='speaker').layout.left=50+(i%2?0:20);
    const before=await browser.evaluate('window.moviestarLive.current.request_id');
    const started=Date.now(),file=join(out,'edit.json');await writeFile(file,JSON.stringify(model));
    const operation=cli(['composition','model','apply',file]);operation.catch(()=>{});
    await browser.wait(`window.moviestarLive.is_current&&window.moviestarLive.current.request_id!==${JSON.stringify(before)}&&window.moviestarLive.stage.snapshot().boxes.some(box=>box.kind==='text'&&box.text===${JSON.stringify(title)})`,10000);
    const event=await browser.evaluate('window.moviestarLive.events.at(-1)');
    await operation;const ended=Date.now();
    const current=await browser.evaluate('({item:window.moviestarLive.current,prepared_at:window.moviestarLive.stage.metrics.prepared_at})');
    const entry={edit:i+1,visible_ms:event.ready_at-started,cli_and_visible_ms:Math.max(ended,event.ready_at)-started,
      detected_after_ms:Math.round(current.item.requested_at*1000-started),generation_ms:current.item.generation_ms,
      generation_to_stage_prepared_ms:Math.round(current.prepared_at-current.item.finished_at*1000),
      request_id:event.request_id,state_id:event.state_id};
    const delivery=await(await fetch(url+'state')).json().then(s=>s.native_video_delivery);
    if(delivery){entry.source_bytes_written=delivery.bytes_written;entry.source_validated_heads=delivery.validated_heads;}
    result.edits.push(entry);check(entry.visible_ms>=0&&entry.cli_and_visible_ms<3000,`CLI edit ${i+1} visible and command complete within 3000ms`);await persist();
  }
  // A -> B -> CLI undo -> A must be a fresh request, not a stale A publication.
  const beforeUndo=await browser.evaluate('window.moviestarLive.current.request_id');
  await cli(['undo']);
  await browser.wait(`window.moviestarLive.is_current&&window.moviestarLive.current.request_id!==${JSON.stringify(beforeUndo)}&&window.moviestarLive.stage.snapshot().boxes.some(box=>box.kind==='text'&&box.text==='CLI edit 19')`);
  result.undo={passed:true,request:await browser.evaluate('window.moviestarLive.current')};
  // Restore the generated original through the actual editing command.
  const restore=join(out,'restore.json');await writeFile(restore,JSON.stringify(original));await cli(['composition','model','apply',restore]);
  await browser.wait(`window.moviestarLive.is_current&&window.moviestarLive.stage.snapshot().boxes.some(box=>box.kind==='text'&&box.text==='Live edit A')`);
  const restored=await browser.evaluate('window.moviestarLive.current');
  check(restored.state_id===initial.state_id&&restored.request_id!==initial.request_id,'restored original state is a fresh request');
  await browser.evaluate('window.moviestarLive.stage.seek(0)');
  await browser.click('#play');await browser.wait('window.moviestarLive.stage.metrics.finished',10000);
  const metrics=await browser.evaluate('window.moviestarLive.stage.metrics');
  result.playback=metrics;
  result.playback.click_to_first_video_ms=metrics.first_presented_at-metrics.play_at;
  check(result.playback.click_to_first_video_ms>=0&&result.playback.click_to_first_video_ms<3000,'trusted Play starts visible motion within 3000ms');
  result.playback.phase_ms=metrics.flashes.map((flash,i)=>metrics.beeps[i]?metrics.beeps[i].display_time-flash.display_time:null);
  if(!real){
    check(metrics.flashes.length===5&&metrics.beeps.length===5,'five native audio and visible flash cues');
    check(metrics.beeps.every(beep=>beep.timestamp_available)&&result.playback.phase_ms.every(value=>value!==null&&Math.abs(value)<=50),'browser-estimated audio/display phase within 50ms');
  }else{
    result.playback.audio_state=await browser.evaluate('window.moviestarLive.stage.audioState');
    check(result.playback.audio_state==='running','real-media audio output is running');
    // Speech is not the generated flash/beep fixture. Physical lip-sync remains
    // a human check; do not reinterpret threshold crossings as sync evidence.
    delete result.playback.phase_ms;
  }
  check(metrics.stalls===0&&metrics.quality.droppedVideoFrames===0,'no playback stalls or dropped frames');
  check(Math.abs(metrics.end_time-fixture.source_end)<=.05,'reference window stops within 50ms');
  if(!real)check(metrics.initial_barcode===barcode(fixture.source_start)&&metrics.frames.every(frame=>frame.number===barcode(frame.media_time)&&frame.media_time>=fixture.source_start-1e-6&&frame.media_time<fixture.source_end),'source frames and source offset agree throughout playback');
  else check(metrics.frames.every(frame=>frame.media_time>=fixture.source_start-1e-6&&frame.media_time<fixture.source_end),'real source frames stay within the requested window');
  const shown=real?new Set([Math.round(fixture.source_start*fixture.fps),...metrics.frames.map(frame=>Math.round(frame.media_time*fixture.fps))]):new Set([metrics.initial_barcode,...metrics.frames.map(frame=>frame.number)]);
  const expectedFrames=Math.round((fixture.source_end-fixture.source_start)*fixture.fps);
  check(shown.size===expectedFrames,`all ${expectedFrames} expected reference source frames displayed`);
  // Server-side completed body writes include transfers that CDP omits when
  // streams are canceled or fulfilled by a service worker.
  result.delivery=await(await fetch(url+'state')).json().then(s=>s.native_video_delivery);
  if(result.cold.source_buffering){
    result.source_buffer=await browser.evaluate(`new Promise(resolve=>{const channel=new MessageChannel();channel.port1.onmessage=e=>{channel.port1.close();resolve(e.data);};navigator.serviceWorker.controller.postMessage('native-source-buffer-stats',[channel.port2]);})`);
    check(result.source_buffer.cached_bytes<=64*1024*1024&&result.source_buffer.entries<=256,'source byte buffer stays within its memory/entry budget');
    if(fixture.require_source_buffering)check(result.source_buffer.cached_bytes>0,'source responses permit the memory-only buffer');
  }
  const screenshot=await browser.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
  await writeFile(join(out,'viewer.png'),Buffer.from(screenshot.data,'base64'));
  const times=result.edits.map(item=>item.cli_and_visible_ms).sort((a,b)=>a-b);
  result.summary={warm_samples:times.length,warm_p95_ms:times[Math.ceil(.95*times.length)-1],warm_max_ms:times.at(-1),budget_ms:3000};
}catch(error){
  result.problems.push(String(error.stack??error));
  try{result.failure_state=await browser.evaluate(`({state:window.movieStarPreview?.state,status:document.getElementById('status')?.textContent,error:document.getElementById('error')?.textContent,frames:[...document.querySelectorAll('iframe')].map(frame=>{const api=frame.contentWindow?.moviestarLiveStage,v=frame.contentDocument?.querySelector('video');return{request:api?.packet?.request_id,buffered:api?.metrics?.source_buffering,error:api?.error,time:api?.time,video:v?{readyState:v.readyState,seeking:v.seeking,currentTime:v.currentTime,paused:v.paused}:null};})})`);}catch{}
}
finally{
  await browser.close();
  try{
    for(let i=0;i<25;i++){
      result.delivery=await(await fetch(url+'state',{signal:AbortSignal.timeout(1000)})).json().then(s=>s.native_video_delivery);
      if(!result.delivery?.active_requests)break;
      await sleep(20);
    }
  }catch(error){result.delivery_error=String(error);}
  await persist();
}
console.log(JSON.stringify({out:resolve(out),...result.summary,cold:result.cold.navigation_to_visible_ms,problems:result.problems}));
if(result.problems.length)process.exitCode=1;
