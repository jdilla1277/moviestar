// Real CLI save -> fresh visible browser revision; no completed video export.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {Browser,sleep} from './browser.mjs';
import {DIAGNOSTICS_SCHEMA,startCommand,observeEdit,previewProbeExpression} from './diagnostics.mjs';

const [url,root,out,python,chrome]=process.argv.slice(2);
if(!chrome)throw Error('usage: benchmark.mjs URL PROJECT OUT PYTHON CHROME');
await mkdir(out,{recursive:true});
const result={diagnostics_schema:DIAGNOSTICS_SCHEMA,cold:{},edits:[],edit_attempts:[],commands:[],checks:[],problems:[],browser_exceptions:[]};
const check=(condition,message)=>{result.checks.push({check:message,passed:Boolean(condition)});if(!condition)result.problems.push(message);};
const persist=()=>writeFile(join(out,'browser-report.json'),JSON.stringify(result,null,2));
let commandNumber=0;
function launchCli(args,label='command-'+String(++commandNumber).padStart(2,'0')){
  const operation=startCommand(python,['-m','moviestar.cli',...args],{cwd:root,out,label});
  result.commands.push(operation.record);return operation;
}
async function cli(args){
  const operation=launchCli(args);await operation.done;await operation.flush();
  if(operation.record.status!=='succeeded')throw Error(`${operation.record.label}: ${operation.record.status}; see its command/stdout/stderr files`);
  return operation.value;
}
await persist();
const browser=await Browser.launch(chrome,out);
try{
  const fixture=JSON.parse(await readFile(join(out,'case.json'),'utf8'));
  const real=fixture.media_kind==='real',segmented=Boolean(fixture.segments);
  const segmentAt=at=>segmented?fixture.segments.findLast(segment=>segment.offset<=at+1e-8):{offset:0,source_start:fixture.source_start};
  const expectedSource=at=>{const segment=segmentAt(at);return segment.source_start+at-segment.offset;};
  const barcode=at=>Math.round(at*fixture.fps)%fixture.barcode_period_frames;
  const requests=new Map();
  await browser.send('Network.enable');
  browser.socket.addEventListener('message',event=>{
    const value=JSON.parse(event.data),p=value.params;
    if(value.method==='Runtime.exceptionThrown')result.browser_exceptions.push(p.exceptionDetails);
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
    if(segmented)check(actual.boxes.length===expected.boxes.length,`all canonical nodes at ${expected.at}s`);
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
      sourcePixels=await browser.evaluate(`(async()=>{const root=document.querySelector('iframe').contentDocument,child=[...root.querySelectorAll('iframe')].find(f=>f.style.opacity==='1'),doc=child?child.contentDocument:root,v=doc.querySelector('video'),c=document.createElement('canvas');c.width=v.videoWidth;c.height=v.videoHeight;const context=c.getContext('2d');context.drawImage(v,0,0);const hash=await crypto.subtle.digest('SHA-256',context.getImageData(0,0,c.width,c.height).data);return{time:v.currentTime,sha256:[...new Uint8Array(hash)].map(b=>b.toString(16).padStart(2,'0')).join('')};})()`);
      check(Math.abs(sourcePixels.time-expectedSource(expected.at))<=1e-3,`real source clock at ${expected.at}s`);
      if(fixture.reference_hashes)check(sourcePixels.sha256===fixture.reference_hashes[String(expected.at)],`decoded source pixels match baseline at ${expected.at}s`);
    }else{
      const source=barcode(expectedSource(expected.at));
      check(actual.source.number===source,`source barcode ${source} at ${expected.at}s`);
    }
    check(geometry<=.2&&glyph<=1&&opacity<=.001,`canonical geometry/motion/opacity/glyph bounds at ${expected.at}s`);
    result.parity.push({at:expected.at,seek_ms:seekMs,max_geometry_px:geometry,max_glyph_px:glyph,max_opacity_error:opacity,source_frame:real?undefined:actual.source.number,source_pixels:sourcePixels,
      caption_lines:actual.boxes.filter(box=>box.kind==='caption').map(box=>box.text_lines.length)});
  }
  await browser.evaluate('window.moviestarLive.stage.seek(0)');
  const specPath=join(root,'moviestar/spec.json'),original=JSON.parse(await readFile(specPath,'utf8')).spatial;
  const initial=await browser.evaluate('window.moviestarLive.current');
  let journalBefore=fixture.initial_history;
  if(journalBefore!==undefined){
    const saved=JSON.parse(await readFile(specPath,'utf8'));
    check(saved.revisions.length===journalBefore,'requested starting history depth is exact');
  }
  for(let i=0;i<20;i++){
    const model=structuredClone(original),title='CLI edit '+(i+1);
    for(const scene of segmented?fixture.segments.map(segment=>segment.scene_id):['review']){
      const children=model.scenes[scene].layout.children;
      children.find(node=>node.id==='title').content.text=title;
      children.find(node=>node.id==='title').style.color=i%2?'#ffb81c':'#ffffff';
      children.find(node=>node.instance_id==='speaker').layout.left=50+(i%2?0:20);
    }
    const before=await browser.evaluate('window.moviestarLive.current.request_id');
    const label='edit-'+String(i+1).padStart(2,'0'),file=join(out,label+'.input.json');
    const attempt=await observeEdit({specPath,priorRequest:before,
      prepare:()=>writeFile(file,JSON.stringify(model)),
      start:()=>launchCli(['composition','model','apply',file],label),
      serverProbe:async()=>{const response=await fetch(url+'state',{signal:AbortSignal.timeout(1000)});if(!response.ok)throw Error('Server state HTTP '+response.status);return response.json();},
      probe:()=>browser.evaluate(previewProbeExpression(title))});
    attempt.trace.edit=i+1;attempt.trace.input_file=label+'.input.json';result.edit_attempts.push(attempt.trace);
    await writeFile(join(out,label+'.trace.json'),JSON.stringify(attempt.trace,null,2));await persist();
    if(attempt.trace.outcome!=='succeeded')throw Error(`${label}: ${attempt.trace.outcome}; see ${label}.trace.json and command/stdout/stderr files`);
    const started=attempt.trace.started_at,event=attempt.preview.visible_event;
    if(event?.request_id!==attempt.preview.current.request_id)throw Error('Visible event does not match the confirmed preview');
    const current={item:attempt.preview.current,prepared_at:attempt.preview.frames.find(frame=>frame.request_id===event.request_id)?.prepared_at};
    const entry={edit:i+1,cli_completion_ms:attempt.trace.cli_completion_ms,visible_ms:event.ready_at-started,cli_and_visible_ms:attempt.trace.measurement_ms,
      detected_after_ms:Math.round(current.item.requested_at*1000-started),generation_ms:current.item.generation_ms,
      generation_to_stage_prepared_ms:Math.round(current.prepared_at-current.item.finished_at*1000),
      request_id:event.request_id,state_id:event.state_id};
    if(journalBefore!==undefined){
      const raw=await readFile(specPath,'utf8'),saved=JSON.parse(raw);
      Object.assign(entry,{journal_before:journalBefore,journal_after:saved.revisions.length,spec_bytes:Buffer.byteLength(raw)});
      check(saved.revisions.length===Math.min(journalBefore+1,fixture.revision_limit),`edit ${i+1} preserves the journal and FIFO cap`);
      journalBefore=saved.revisions.length;
    }
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
    const cues=segmented?Math.round(fixture.range.to-fixture.range.from):5;
    check(metrics.flashes.length===cues&&metrics.beeps.length===cues,`${cues} native audio and visible flash cues`);
    check(metrics.beeps.every(beep=>beep.timestamp_available)&&result.playback.phase_ms.every(value=>value!==null&&Math.abs(value)<=50),'browser-estimated audio/display phase within 50ms');
  }else{
    result.playback.audio_state=await browser.evaluate('window.moviestarLive.stage.audioState');
    check(result.playback.audio_state==='running','real-media audio output is running');
    // Speech is not the generated flash/beep fixture. Physical lip-sync remains
    // a human check; do not reinterpret threshold crossings as sync evidence.
    delete result.playback.phase_ms;
  }
  check(metrics.stalls===0&&metrics.quality.droppedVideoFrames===0,'no playback stalls or dropped frames');
  if(segmented){
    check(metrics.cuts.length===fixture.segments.length-1,'every native scene cut is performed');
    check(metrics.cuts.every(cut=>Math.abs(cut.switched_at-cut.expected_at)<=50),'cut handoffs stay within 50ms');
    check(Math.abs(metrics.end_result_time-fixture.range.to)<=.05,'result window stops within 50ms');
    const first=metrics.frames[0],epoch=first.display_epoch-(first.result_time-fixture.range.from)*1000;
    result.playback.elapsed_from_first_source_ms=metrics.finished_at-epoch;
    check(Math.abs(result.playback.elapsed_from_first_source_ms-(fixture.range.to-fixture.range.from)*1000)<=50,'program clock stays within 50ms across cuts');
    for(const [i,segment] of fixture.segments.entries()){
      const frames=metrics.frames.filter(frame=>frame.segment===i),initial=metrics.segments_shown.find(frame=>frame.segment===i);
      check(frames.every(frame=>frame.media_time>=segment.source_start-1e-6&&frame.media_time<segment.source_end-1e-6),'segment '+i+' never shows frames beyond its cut');
      if(!real)check(initial.source.number===barcode(segment.source_start)&&frames.every(frame=>frame.number===barcode(frame.media_time)),'segment '+i+' source frame IDs agree');
      const shown=new Set([Math.round(initial.media_time*fixture.fps),...frames.map(frame=>Math.round(frame.media_time*fixture.fps))]);
      check(shown.size===Math.round((segment.source_end-segment.source_start)*fixture.fps),'all expected frames in segment '+i);
    }
    if(fixture.continuous_pilot){
      const blocks=metrics.audio_blocks,first=blocks.findIndex(block=>block.rms>.01),last=blocks.findLastIndex(block=>block.rms>.01);
      let gap=0,maxGap=0;for(let i=Math.max(0,first);i<=last;i++){gap=blocks[i].rms<.001?gap+blocks[i].duration:0;maxGap=Math.max(maxGap,gap);}
      result.playback.max_silent_gap_ms=maxGap*1000;
      check(last>first&&maxGap<=Math.max(...blocks.map(block=>block.duration))+1e-6,'continuous output pilot has no gap longer than one audio quantum');
    }
  }else{
    check(Math.abs(metrics.end_time-fixture.source_end)<=.05,'reference window stops within 50ms');
    if(!real)check(metrics.initial_barcode===barcode(fixture.source_start)&&metrics.frames.every(frame=>frame.number===barcode(frame.media_time)&&frame.media_time>=fixture.source_start-1e-6&&frame.media_time<fixture.source_end),'source frames and source offset agree throughout playback');
    else check(metrics.frames.every(frame=>frame.media_time>=fixture.source_start-1e-6&&frame.media_time<fixture.source_end),'real source frames stay within the requested window');
    const shown=real?new Set([Math.round(fixture.source_start*fixture.fps),...metrics.frames.map(frame=>Math.round(frame.media_time*fixture.fps))]):new Set([metrics.initial_barcode,...metrics.frames.map(frame=>frame.number)]);
    const expectedFrames=Math.round((fixture.source_end-fixture.source_start)*fixture.fps);
    check(shown.size===expectedFrames,`all ${expectedFrames} expected reference source frames displayed`);
  }
  check(result.browser_exceptions.length===0,'no uncaught browser exceptions');
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
