// Actual geometry/scenes/undo commands with semantic, freshly visible readiness.
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {Browser} from './browser.mjs';
import {startCommand,observeEdit,previewProbeExpression} from './diagnostics.mjs';
import {measureCueClock} from './cue_clock.mjs';
const [url,root,out,python,chrome]=process.argv.slice(2),fixture=JSON.parse(await readFile(join(out,'case.json'),'utf8'));
const result={edits:[],edit_attempts:[],problems:[],browser_exceptions:[]};
const persist=()=>writeFile(join(out,'browser-report.json'),JSON.stringify(result,null,2));
const browser=await Browser.launch(chrome,out);
function probe(expected){
  return `(()=>{const observation=${previewProbeExpression('')};
    const live=window.moviestarLive,api=live?.stage,wanted=${JSON.stringify(expected)};
    try{
      const packet=api.packet,snapshot=api.snapshot(),videos=packet.media.filter(m=>m.kind==='video');
      const stage=document.querySelector('iframe:not(.pending)')?.contentWindow;
      const selected=videos.find(m=>JSON.stringify(m.path)===JSON.stringify(packet.video_path));
      observation.expected_text_visible=Boolean(selected?.asset===wanted.speaker_asset&&videos.length===2&&wanted.videos.every(v=>{
        const media=videos.find(m=>JSON.stringify(m.path)===JSON.stringify(v.path)),box=snapshot.boxes.find(b=>JSON.stringify(b.path)===JSON.stringify(v.path));
        const decoded=snapshot.videos.find(m=>JSON.stringify(m.path)===JSON.stringify(v.path));
        const wrapper=stage?.moviestarScene?.entries.find(e=>JSON.stringify(e.node.path)===JSON.stringify(v.path))?.element;
        const element=wrapper?.querySelector('video');
        return media?.asset===v.asset&&Math.abs(media.source_start-v.start)<1e-6&&media.object_position===v.object_position&&box&&
          element&&stage.getComputedStyle(element).objectPosition===v.object_position&&
          decoded.audio_gain===(JSON.stringify(v.path)===JSON.stringify(packet.video_path)?1:0)&&
          decoded?.asset===v.asset&&decoded.ready_state>=2&&!decoded.seeking&&Math.abs(decoded.media_time-v.start)<1e-3&&
          ['x','y','width','height'].every(k=>Math.abs(box.rect[k]-v.rect[k])<=.2);
      }));
      observation.semantic_snapshot=snapshot;
    }catch(error){observation.semantic_error=String(error);observation.expected_text_visible=false;}
    return observation;})()`;
}
try{
  browser.socket.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.method==='Runtime.exceptionThrown')result.browser_exceptions.push(m.params.exceptionDetails);});
  await persist();await browser.send('Page.navigate',{url:url+'?audit=1'});
  await browser.wait(`(${probe(fixture.initial)}).is_current&&(${probe(fixture.initial)}).expected_text_visible`,20000);
  // Match the authored 360x640 reference raster in software-headless Chrome.
  // UI upscaling/viewport behavior is recorded separately from this benchmark.
  await browser.evaluate(`(async()=>{const s=document.getElementById('stage');s.style.maxWidth='none';s.style.width=(360+s.offsetWidth-s.clientWidth)+'px';await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()`);
  result.reference_view={width:360,height:640,scale:1};
  const initial=await browser.evaluate('window.moviestarLive.current');
  result.source_buffering=await browser.evaluate('window.moviestarLive.stage.metrics.source_buffering');
  for(const [i,action] of fixture.actions.entries()){
    const prior=await browser.evaluate('window.moviestarLive.current.request_id'),label=`edit-${String(i+1).padStart(2,'0')}-${action.name}`;
    const attempt=await observeEdit({specPath:join(root,'moviestar/spec.json'),priorRequest:prior,
      start:()=>startCommand(python,['-m','moviestar.cli',...action.args],{cwd:root,out,label}),
      serverProbe:async()=>(await fetch(url+'state',{signal:AbortSignal.timeout(1000)})).json(),
      probe:()=>browser.evaluate(probe(action.expected))});
    attempt.trace.logical_edit=action.name;result.edit_attempts.push(attempt.trace);
    await writeFile(join(out,label+'.trace.json'),JSON.stringify(attempt.trace,null,2));await persist();
    if(attempt.trace.outcome!=='succeeded')throw Error(`${label}: ${attempt.trace.outcome}`);
    const saved=JSON.parse(await readFile(join(root,'moviestar/spec.json'),'utf8'));
    result.edits.push({name:action.name,ms:attempt.trace.measurement_ms,cli_ms:attempt.trace.cli_completion_ms,
      request_id:attempt.preview.current.request_id,state_id:attempt.preview.current.state_id,history:saved.revisions.length,
      source_delivery:(await(await fetch(url+'state')).json()).native_video_delivery});
    if(attempt.trace.measurement_ms>=3000)result.problems.push(`${label} exceeds 3000ms`);
    if(i===3){
      await browser.click('#play');await browser.wait('window.moviestarLive.stage.metrics.finished||window.moviestarLive.stage.error',12000);
      result.swapped_playback=await browser.evaluate('window.moviestarLive.stage.metrics');
      if(result.swapped_playback.error)result.problems.push(result.swapped_playback.error);
      if(result.swapped_playback.stalls||result.swapped_playback.video_quality?.some(q=>q.dropped_frames))
        result.problems.push('Swapped playback stalled or dropped frames');
      if(fixture.media_kind==='generated'&&(!result.swapped_playback.beeps.length||result.swapped_playback.beeps.some(b=>b.rms>.07)))
        result.problems.push('Swapped speaker audio does not match the quieter source; listener must remain muted');
      if(fixture.media_kind==='generated'){
        const m=result.swapped_playback;
        const sourceStart=await browser.evaluate('window.moviestarLive.stage.packet.source_start');
        result.swapped_cue_clock=measureCueClock(m,{fps:30,sourceStart,count:5});
        result.problems.push(...result.swapped_cue_clock.problems);
      }
      await browser.evaluate('window.moviestarLive.stage.seek(0)');
    }
    await persist();
  }
  const final=await browser.evaluate('window.moviestarLive.current');
  const saved=JSON.parse(await readFile(join(root,'moviestar/spec.json'),'utf8'));
  result.restored={same_state:initial.state_id===final.state_id,fresh_request:initial.request_id!==final.request_id,
    initial_history:fixture.initial_history,final_history:saved.revisions.length};
  // With a full FIFO journal, four edits evict four oldest entries before undo.
  if(!result.restored.same_state||!result.restored.fresh_request||saved.revisions.length!==Math.min(fixture.initial_history+4,50)-4)
    result.problems.push('Four individual undos must restore current content with the expected FIFO history');
  if(result.browser_exceptions.length)result.problems.push('Uncaught browser exception');
}catch(error){result.problems.push(String(error.stack??error));}
finally{await persist();await browser.close();}
process.exitCode=result.problems.length?1:0;
