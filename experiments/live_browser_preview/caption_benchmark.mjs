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
    try{
      const live=window.moviestarLive,api=live.stage,snapshot=api.snapshot(),wanted=${JSON.stringify(expected)};
      const frame=document.querySelector('iframe:not(.pending)'),stage=frame.contentWindow,raster=frame.getBoundingClientRect();
      const plane=api.packet.caption_plane,index=plane.frames[Math.round(api.time*api.packet.fps)],state=plane.states[index];
      const image=[...stage.document.images].find(i=>i.src.includes('/assets/'+state.asset));
      observation.expected_text_visible=Boolean(raster.width===${fixture.canvas[0]}&&raster.height===${fixture.canvas[1]}&&Math.abs(api.time-.2)<1e-3&&
        image?.isConnected&&image.complete&&image.naturalWidth===state.rect.width&&state.rect.width>1&&
        snapshot.captions.length===wanted.length&&wanted.every((v,i)=>{
          const cue=snapshot.captions[i];return cue.text===v.text&&cue.font_size===v.font_size&&
            cue.highlight_color===v.highlight_color&&cue.estimated_bounds.x===v.estimated_bounds.x&&cue.estimated_bounds.y===v.estimated_bounds.y;
        }));
      observation.semantic_snapshot=snapshot;
    }catch(error){observation.semantic_error=String(error);observation.expected_text_visible=false;}
    return observation;})()`;
}
try{
  browser.socket.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.method==='Runtime.exceptionThrown')result.browser_exceptions.push(m.params.exceptionDetails);});
  await persist();await browser.send('Page.navigate',{url:url+'?audit=1'});
  await browser.wait('window.moviestarLive?.is_current',20000);
  await browser.send('Emulation.setDeviceMetricsOverride',{width:Math.max(1280,fixture.canvas[0]+100),height:Math.max(1000,fixture.canvas[1]+500),deviceScaleFactor:1,mobile:false});
  await browser.evaluate(`(async()=>{const s=document.getElementById('stage'),style=document.createElement('style');style.textContent='#stage{max-width:none!important;width:'+(${fixture.canvas[0]}+s.offsetWidth-s.clientWidth)+'px!important}';document.head.append(style);await window.moviestarLive.stage.seek(.2);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()`);
  await browser.wait(`(${probe(fixture.initial)}).expected_text_visible`,5000);
  const initial=await browser.evaluate('window.moviestarLive.current');
  for(const [i,action] of fixture.actions.entries()){
    const prior=await browser.evaluate('window.moviestarLive.current.request_id'),label=`edit-${i+1}-${action.name}`;
    const attempt=await observeEdit({specPath:join(root,'moviestar/spec.json'),priorRequest:prior,
      start:()=>startCommand(python,['-m','moviestar.cli',...action.args],{cwd:root,out,label}),
      serverProbe:async()=>(await fetch(url+'state',{signal:AbortSignal.timeout(1000)})).json(),
      probe:()=>browser.evaluate(probe(action.expected))});
    result.edit_attempts.push(attempt.trace);await writeFile(join(out,label+'.trace.json'),JSON.stringify(attempt.trace,null,2));await persist();
    if(attempt.trace.outcome!=='succeeded')throw Error(`${label}: ${attempt.trace.outcome}`);
    result.edits.push({name:action.name,ms:attempt.trace.measurement_ms,cli_ms:attempt.trace.cli_completion_ms,
      request_id:attempt.preview.current.request_id,state_id:attempt.preview.current.state_id});
    if(attempt.trace.measurement_ms>=3000)result.problems.push(`${label} exceeds 3000ms`);
    await persist();
  }
  const final=await browser.evaluate('window.moviestarLive.current');
  const saved=JSON.parse(await readFile(join(root,'moviestar/spec.json'),'utf8'));
  result.restored={same_state:initial.state_id===final.state_id,fresh_request:initial.request_id!==final.request_id,history:saved.revisions.length};
  if(!result.restored.same_state||!result.restored.fresh_request||saved.revisions.length!==Math.min(fixture.initial_history+4,50)-4)
    result.problems.push('Undo did not restore original captions with expected FIFO history');
  await browser.evaluate('window.moviestarLive.stage.seek(0)');await browser.click('#play');
  await browser.wait('window.moviestarLive.stage.metrics.finished||window.moviestarLive.stage.error',12000);
  result.playback=await browser.evaluate('window.moviestarLive.stage.metrics');
  if(result.playback.error||result.playback.stalls||result.playback.video_quality?.some(q=>q.dropped_frames))result.problems.push('Caption playback failed/stalled/dropped');
  result.cue_clock=measureCueClock(result.playback,{fps:30,sourceStart:1,count:5});result.problems.push(...result.cue_clock.problems);
  const plane=await browser.evaluate('window.moviestarLive.stage.packet.caption_plane');
  result.caption_playback={observations:0,max_apply_lateness_ms:0,states:[]};
  for(const frame of result.playback.frames){
    const index=Math.max(0,Math.min(plane.frames.length-1,Math.round((frame.media_time-1)*30))),expected=plane.frames[index];
    result.caption_playback.observations++;
    if(frame.caption_state!==expected||frame.caption_asset!=='assets/'+plane.states[expected].asset)result.problems.push('Wrong visible caption plane on presented source frame');
    result.caption_playback.max_apply_lateness_ms=Math.max(result.caption_playback.max_apply_lateness_ms,frame.caption_applied_at-frame.display_time);
    if(!result.caption_playback.states.includes(frame.caption_state))result.caption_playback.states.push(frame.caption_state);
  }
  if(!result.caption_playback.observations||result.caption_playback.states.length<3||result.caption_playback.max_apply_lateness_ms>50)result.problems.push('Caption playback clock missing, static, or more than 50ms late');
  if(result.browser_exceptions.length)result.problems.push('Uncaught browser exception');
}catch(error){result.problems.push(String(error.stack??error));}
finally{await persist();await browser.close();}
process.exitCode=result.problems.length?1:0;
