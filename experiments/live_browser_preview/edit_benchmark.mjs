import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {Browser} from './browser.mjs';
import {startCommand,observeEdit,previewProbeExpression} from './diagnostics.mjs';
import {describeAudioClocks} from './audio_clock_report.mjs';
import {semanticPacket,checkSnapshot,completeVideoQuality}from './edit_semantics.mjs';
const [url,root,out,python,chrome]=process.argv.slice(2),fixture=JSON.parse(await readFile(join(out,'case.json'),'utf8'));
const result={edits:[],edit_attempts:[],problems:[],browser_exceptions:[]};
const persist=()=>writeFile(join(out,'browser-report.json'),JSON.stringify(result,null,2));
const browser=await Browser.launch(chrome,out);
function probe(expected,at=.2){
 return `(async()=>{const observation=${previewProbeExpression('')};
 const semanticPacket=${semanticPacket.toString()},checkSnapshot=${checkSnapshot.toString()};
 try{
  const live=window.moviestarLive,api=live.stage;
  if(Math.abs(api.time-${at})>1/api.packet.fps+.001)await api.seek(${at});
  const packet=api.packet,snapshot=api.snapshot(),wanted=${JSON.stringify(expected)};
  const outer=document.querySelector('iframe:not(.pending)'),root=outer.contentWindow;
  const active=packet.segments?packet.segments[snapshot.segment].packet:packet;
  const wantedActive=wanted.segments?wanted.segments[snapshot.segment].packet:wanted;
  const frame=packet.segments?[...root.document.querySelectorAll('iframe')].find(f=>f.contentWindow.moviestarLiveStage?.packet.range.from===active.range.from):outer;
  const doc=frame.contentDocument;
  snapshot.video_styles=[...doc.querySelectorAll('video')].map(v=>({object_fit:doc.defaultView.getComputedStyle(v).objectFit,object_position:doc.defaultView.getComputedStyle(v).objectPosition}));
  const plane=active.caption_plane;
  if(plane){const index=Math.round((snapshot.time-(active.range.from-packet.range.from))*active.fps),state=plane.states[plane.frames[Math.max(0,Math.min(plane.frames.length-1,index))]];
   const image=[...doc.images].find(i=>i.src.includes('/assets/'+state.asset)&&i.isConnected&&i.complete&&i.naturalWidth===state.rect.width&&i.naturalHeight===state.rect.height);
   snapshot.caption_image_ready=Boolean(image);snapshot.caption_rect=state.rect;
   if(image){const response=await fetch(image.src);if(!response.ok)throw Error('Caption raster unavailable');
    const digest=await crypto.subtle.digest('SHA-256',await response.arrayBuffer());
    snapshot.caption_sha256=[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');}

  }
  snapshot.window_start=packet.range.from;
  const audio=packet.audio_plane;
  const soundReady=!audio||(snapshot.audio?.prepared&&snapshot.audio.asset===audio.asset);
  observation.expected_text_visible=soundReady&&JSON.stringify(semanticPacket(packet))===JSON.stringify(semanticPacket(wanted))&&checkSnapshot(wantedActive,snapshot,snapshot.time);
  observation.semantic_snapshot=snapshot;
 }catch(error){observation.semantic_error=String(error);observation.expected_text_visible=false;}
 return observation;})()`;
}

async function checkPlayback(label,wanted){
  const report={label};
  for(const at of [0,1,2,3,4,0])await browser.evaluate('window.moviestarLive.stage.seek('+at+')');
  await browser.click('#play');
  await browser.wait('window.moviestarLive.stage.metrics.finished||window.moviestarLive.stage.error',12000);
  const playback=await browser.evaluate('window.moviestarLive.stage.metrics');
  report.child_metrics=await browser.evaluate(`[...document.querySelector('iframe:not(.pending)').contentDocument.querySelectorAll('iframe')].map(f=>f.contentWindow.moviestarLiveStage.metrics)`);
  playback.video_quality=await browser.evaluate(`[...document.querySelector('iframe:not(.pending)').contentDocument.querySelectorAll('iframe')].flatMap(f=>f.contentWindow.moviestarLiveStage.metrics.video_quality??[])`);
  if(playback.error||playback.stalls||!completeVideoQuality(wanted,playback.video_quality))result.problems.push('Audio playback failed/stalled/dropped');
  report.clock_diagnostics=describeAudioClocks(playback);
  const stamp=await browser.evaluate("document.querySelector('iframe:not(.pending)').contentWindow.moviestarCutShared.audio.getOutputTimestamp()");
  report.audio_output_stamp=stamp;const started=playback.prepared_audio;
  report.audio_display_phases_ms=playback.frames.map(f=>f.display_time-(stamp.performanceTime+(started.started_context_time+f.result_time-started.started_offset-stamp.contextTime)*1000));
  if(!report.audio_display_phases_ms.length||report.audio_display_phases_ms.some(n=>!Number.isFinite(n)||Math.abs(n)>50))result.problems.push('Prepared-audio/display output-clock phase exceeds 50ms');
  const first=playback.frames[0],last=playback.frames.at(-1);
  report.program_drift_ms=last.display_epoch-last.result_time*1000-first.display_epoch+first.result_time*1000;
  if(Math.abs(report.program_drift_ms)>50)result.problems.push('Program clock exceeds 50ms');

  report.metrics=playback;(result.playbacks??=[]).push(report);await persist();
}

try{
  browser.socket.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.method==='Runtime.exceptionThrown')result.browser_exceptions.push(m.params.exceptionDetails);});
  await persist();await browser.send('Media.enable');await browser.send('Page.navigate',{url:url+'?audit=1'});
  await browser.wait('window.moviestarLive?.is_current',20000);
  await browser.send('Emulation.setDeviceMetricsOverride',{width:Math.max(1280,fixture.canvas.width+100),height:Math.max(1000,fixture.canvas.height+500),deviceScaleFactor:1,mobile:false});
  await browser.evaluate(`(async()=>{const s=document.getElementById('stage'),style=document.createElement('style');style.textContent='#stage{max-width:none!important;width:'+(${fixture.canvas.width}+s.offsetWidth-s.clientWidth)+'px!important}';document.head.append(style);await window.moviestarLive.stage.seek(.2);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()`);
  result.initial_probe=await browser.evaluate(probe(fixture.initial));await persist();
  await browser.wait(`(${probe(fixture.initial)}).then(p=>p.expected_text_visible)`,5000);
  const initial=await browser.evaluate('window.moviestarLive.current');
  await browser.evaluate('window.captionReferenceFrame=document.querySelector("iframe:not(.pending)")');
  for(const [i,action] of fixture.actions.entries()){
    await browser.evaluate('window.captionReferenceFrame=document.querySelector("iframe:not(.pending)")');
    const prior=await browser.evaluate('window.moviestarLive.current.request_id'),label=`edit-${i+1}-${action.name}`;
    const attempt=await observeEdit({specPath:join(root,'moviestar/spec.json'),priorRequest:prior,
      start:()=>startCommand(python,['-m','moviestar.cli',...action.args],{cwd:root,out,label}),
      serverProbe:async()=>(await fetch(url+'state',{signal:AbortSignal.timeout(1000)})).json(),
      probe:()=>browser.evaluate(probe(action.expected))});
    result.edit_attempts.push(attempt.trace);await writeFile(join(out,label+'.trace.json'),JSON.stringify(attempt.trace,null,2));await persist();
    if(attempt.trace.outcome!=='succeeded')throw Error(`${label}: ${attempt.trace.outcome}`);
    result.edits.push({name:action.name,ms:attempt.trace.measurement_ms,cli_ms:attempt.trace.cli_completion_ms,
      request_id:attempt.preview.current.request_id,state_id:attempt.preview.current.state_id,
      caption_reuse:attempt.preview.current.caption_reuse??null,
      reused_decoders:await browser.evaluate('document.querySelector("iframe:not(.pending)")===window.captionReferenceFrame')});
    for(const at of [.2,1.5,3,4.5]){
      await browser.evaluate('window.moviestarLive.stage.seek('+at+')');
      await browser.wait(`(${probe(action.expected,at)}).then(p=>p.expected_text_visible)`,5000);
      (result.inspections??=[]).push({edit:action.name,at,preview:await browser.evaluate(probe(action.expected,at))});
      if(at===.2||at===3){
        const shot=await browser.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
        await writeFile(join(out,label+'-at-'+at+'.png'),Buffer.from(shot.data,'base64'));
      }
      await persist();
    }
    if(action.name==='main-picture')await checkPlayback('edited',action.expected);
    await browser.evaluate('window.moviestarLive.stage.seek(.2)');
    if(attempt.trace.measurement_ms>=3000)result.problems.push(`${label} exceeds 3000ms`);
    await persist();
  }
  const final=await browser.evaluate('window.moviestarLive.current');
  const saved=JSON.parse(await readFile(join(root,'moviestar/spec.json'),'utf8'));
  result.restored={same_state:initial.state_id===final.state_id,fresh_request:initial.request_id!==final.request_id,history:saved.revisions.length};
  if(!result.restored.same_state||!result.restored.fresh_request||saved.revisions.length!==fixture.expected_history)
    result.problems.push('Undo did not restore original visuals with expected FIFO history');
  await checkPlayback('restored',fixture.initial);
  if(result.browser_exceptions.length)result.problems.push('Uncaught browser exception');
}catch(error){result.problems.push(String(error.stack??error));}
finally{await persist();await browser.close();}
process.exitCode=result.problems.length?1:0;
