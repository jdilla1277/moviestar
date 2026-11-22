// Follow the public interview workflow and retain each first timing/playback result.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {Browser} from './browser.mjs';
import {measureCueClock} from './cue_clock.mjs';
const [url,root,out,python,chrome]=process.argv.slice(2),run=promisify(execFile);
const fixture=JSON.parse(await readFile(out+'/case.json','utf8'));
const b=await Browser.launch(chrome,out),api='window.movieStarPreview.stage';
const report={edits:[],playback:[],captures:[],problems:[],browser_exceptions:[]};
const persist=()=>writeFile(out+'/report.json',JSON.stringify(report,null,2));
const rgb=hex=>`rgb(${[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(', ')})`;
function sceneProbe(){
 return `(()=>{const outer=document.querySelector('iframe:not(.pending)');if(!outer)return null;
  const children=[outer];
  return children.map((frame,index)=>{const w=frame.contentWindow,stage=w.moviestarLiveStage;
   if(!stage)return null;const scene=${JSON.stringify(fixture.scene)};
   const paths=[[scene,'speaker','video'],[scene,'listeners','left','video'],[scene,'listeners','right','video']];
   return{snapshot:stage.snapshot(),styles:paths.map(path=>{const entry=w.moviestarScene.entries.find(e=>JSON.stringify(e.node.path)===JSON.stringify(path));
    if(!entry)return null;const css=w.getComputedStyle(entry.element);return{path,color:css.borderColor,width:css.borderTopWidth,style:css.borderTopStyle};})};});})()`;
}
async function setReferenceWidth(size){
 await b.evaluate(`(()=>{const stage=document.getElementById('stage');document.documentElement.style.setProperty('--interview-width',(${size[0]}+stage.offsetWidth-stage.clientWidth)+'px');})()`);
}
async function checkState(wanted){
 const value=await b.evaluate(`(()=>{const live=window.movieStarPreview;if(!live?.is_current)return null;
  const packet=live.stage.packet,r=document.querySelector('iframe:not(.pending)').getBoundingClientRect();
  return{prepared:live.stage.snapshot().audio?.prepared,canvas:packet.canvas,raster:{width:r.width,height:r.height},scenes:${sceneProbe()}};})()`);
 if(!value||!value.prepared||value.canvas.width!==wanted.canvas[0]||value.canvas.height!==wanted.canvas[1])return false;
 if(value.raster.width!==wanted.canvas[0]||value.raster.height!==wanted.canvas[1])return false;
 if(value.scenes?.length!==1||value.scenes.some(s=>!s||s.snapshot.videos.length!==3))return false;
 const styles=value.scenes.flatMap(s=>s.styles);
 if(styles.some((s,i)=>!s||s.color!==rgb(wanted.colors[i+(fixture.scene==='intro'?0:3)])||s.width!=='2px'||s.style!=='solid'))return false;
 for(const [i,s]of value.scenes.entries()){
  const starts=fixture.scene==='intro'?[1,2,3]:[2,3,4];
  if(s.snapshot.videos.some((v,k)=>v.source.number!==starts[k]*30||v.ready_state<2||v.seeking||!v.paused||v.audio_gain!==0))return false;
  const scene=fixture.scene,box=path=>s.snapshot.boxes.find(b=>b.path.join('/')===scene+'/'+path);
  const speaker=box('speaker/video')?.rect,left=box('listeners/left/video')?.rect,right=box('listeners/right/video')?.rect;
  if(!speaker||!left||!right)return false;
  for(const rect of [speaker,left,right])if(rect.width<=0||rect.height<=0||rect.x<0||rect.y<0||rect.x+rect.width>wanted.canvas[0]+.2||rect.y+rect.height>wanted.canvas[1]+.2)return false;
  if(speaker.width*speaker.height<2*left.width*left.height)return false;
  if(wanted.canvas[0]<wanted.canvas[1]){
   if(speaker.y+speaker.height>left.y||left.x+left.width>right.x||Math.abs(left.y-right.y)>.2)return false;
  }else if(speaker.x+speaker.width>left.x||left.y+left.height>right.y||Math.abs(left.x-right.x)>.2)return false;
  const caption=box('captions'),heading=box('heading');
  if(!caption||caption.rect.y<Math.max(...[speaker,left,right].map(r=>r.y+r.height))||heading.rect.y+heading.rect.height>speaker.y)return false;
  if(caption.text!==(fixture.scene==='intro'?'Alice opens the discussion.':'Bob takes the lead.'))return false;
 }
 return value;
}
async function waitState(wanted){
 const deadline=performance.now()+12000;let value;
 while(performance.now()<deadline){value=await checkState(wanted);if(value)return value;await new Promise(r=>setTimeout(r,20));}
 throw Error('Expected interview arrangement not ready: '+JSON.stringify(await b.evaluate(`({error:document.getElementById('error').textContent,scenes:${sceneProbe()},current:window.movieStarPreview?.is_current})`)));
}
async function playback(label){
 // First playback is from published readiness, without a preparatory transport seek.
 await b.click('#play');await b.wait(api+'.metrics.finished||'+api+'.error',12000);
 const m=await b.evaluate(api+'.metrics');
 const routing=await b.evaluate(api+'.snapshot().videos');
 assert.ok(routing.every(v=>v.audio_connected&&v.audio_gain===0),'Pictures share the device clock without doubling prepared PCM');
 const children=[m];
 const item={label,metrics:m,children};report.playback.push(item);await persist();
 assert.equal(m.error,null);assert.equal(m.stalls,0);
 const quality=children.flatMap(s=>s.video_quality??[]);assert.equal(quality.length,3);
 assert.ok(quality.every(q=>q.dropped_frames===0),'Every interview decoder retains every review frame');
 assert.ok(quality.every(q=>q.total_frames>=80),'All three pictures keep moving in the selected scene');
 const videoFrames=children.flatMap(s=>s.video_frames??[]),paths=[...new Set(videoFrames.map(f=>JSON.stringify(f.path)))];
 assert.equal(paths.length,3);
 for(const path of paths){const frames=videoFrames.filter(f=>JSON.stringify(f.path)===path);assert.ok(frames.length>=20);
  assert.ok(frames.every(f=>Math.abs(f.number-Math.round(f.media_time*30)%240)<=1),'Source identity retains the existing one-frame live-readback bound');}
 if(fixture.media_kind==='generated'){
  const sourceStart=fixture.scene==='intro'?1:2;
  item.cue_clock=measureCueClock({...m,frames:m.frames.map(f=>({...f,result_time:f.media_time-sourceStart}))},{fps:30,count:3,quality});assert.deepEqual(item.cue_clock.problems,[],'Selected speaker stays within 50ms');
  // Cue-onset RMS depends on which part of the first quantum contains sound.
  // Compare full interior quanta; PCM capture separately checks exact samples.
  const start=m.prepared_audio.started_context_time;
  item.voice_peaks=Array.from({length:3},(_,i)=>Math.max(...m.audio_blocks.filter(block=>
    block.context_time-start>=i+.21&&block.context_time-start<=i+.25).map(block=>block.rms)));
  const [low,high]=fixture.scene==='intro'?[.14,.22]:[.07,.12];
  assert.ok(item.voice_peaks.every(rms=>rms>low&&rms<high),'Only the selected speaker is audible');
 }
 const first=m.frames[0],last=m.frames.at(-1);
 item.program_drift_ms=(last.display_time-last.media_time*1000)-(first.display_time-first.media_time*1000);
 assert.ok(Math.abs(item.program_drift_ms)<=50,'Interview program clock stays within 50ms');
 const final=await b.evaluate(api+'.snapshot()');
 assert.deepEqual(final.videos.map(v=>v.source.number),fixture.scene==='intro'?[119,149,179]:[149,179,209],'All three canonical final pictures hold');
 if(fixture.capture_pcm){
  const asset=await b.evaluate(api+'.packet.audio_plane.asset');
  await writeFile(out+'/'+label+'-audio-reference.wav',Buffer.from(await(await fetch(url+'assets/'+asset)).arrayBuffer()));
 }
 await persist();
}
async function capture(label,at){
 const snapshot=await b.evaluate(`(async()=>{await ${api}.seek(${at});return ${api}.snapshot();})()`);
 const clip=await b.evaluate(`(()=>{const r=document.querySelector('iframe:not(.pending)').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,scale:1};})()`);
 const png=await b.send('Page.captureScreenshot',{format:'png',clip,captureBeyondViewport:true});
 await writeFile(out+'/'+label+'.png',Buffer.from(png.data,'base64'));
 report.captures.push({label,at,snapshot,canvas:await b.evaluate(api+'.packet.canvas')});await persist();
}
try{
 b.socket.addEventListener('message',event=>{const item=JSON.parse(event.data);if(item.method==='Runtime.exceptionThrown')report.browser_exceptions.push(item.params.exceptionDetails);});
 await b.send('Emulation.setDeviceMetricsOverride',{width:3200,height:2600,deviceScaleFactor:1,mobile:false});
 await b.send('Page.navigate',{url:url+'?audit=1'+(fixture.capture_pcm?'&audit_pcm=1':'')});
 await b.wait('window.movieStarPreview?.is_current',20000);
 await b.evaluate(`(()=>{const style=document.createElement('style');style.textContent='#stage{max-width:none!important;width:var(--interview-width)!important}';document.head.append(style);})()`);
 await setReferenceWidth(fixture.initial.canvas);await waitState(fixture.initial);
 const initial=await b.evaluate('window.movieStarPreview.current');
 await playback('first-play');
 await b.evaluate(api+'.seek(0)');
 for(const [index,action]of fixture.actions.entries()){
  await b.evaluate(api+'.seek(0)');await setReferenceWidth(action.canvas);
  const prior=await b.evaluate('window.movieStarPreview.current.request_id'),started=performance.now();
  const result=await run(python,['-m','moviestar.cli',...action.args],{cwd:root,timeout:30000});
  const cliElapsed=performance.now()-started;await writeFile(out+`/command-${index}.json`,result.stdout);
  await b.wait('window.movieStarPreview?.is_current&&window.movieStarPreview.current.request_id!=='+JSON.stringify(prior),12000);
  await waitState(action);
  const elapsed=performance.now()-started;
  report.edits.push({index,name:action.name,elapsed_ms:elapsed,cli_ms:cliElapsed,request:await b.evaluate('window.movieStarPreview.current.request_id'),canvas:action.canvas});
  if(elapsed>=3000)report.problems.push(`Edit ${index} ${action.name}: ${elapsed}ms`);
  await persist();
 }
 const final=await b.evaluate('window.movieStarPreview.current');
 const saved=JSON.parse(await readFile(root+'/moviestar/spec.json','utf8'));
 assert.equal(initial.state_id,final.state_id,'Individual undos restore the same editable content');
 assert.equal(saved.revisions.length,fixture.actions.length?Math.min(fixture.initial_history+10,50)-10:fixture.initial_history);
 if(fixture.actions.length){assert.notEqual(initial.request_id,final.request_id);await playback('after-undo');}
 for(const at of [.5,1.5,2.5]){
  const state=await b.evaluate(`(async()=>{await ${api}.seek(${at});return ${api}.snapshot();})()`),local=at,starts=fixture.scene==='intro'?[1,2,3]:[2,3,4];
  assert.deepEqual(state.videos.map(v=>v.source.number),starts.map(start=>Math.round((start+local)*30)));
  assert.ok(state.videos.every(v=>v.paused&&!v.seeking));
 }
 await b.evaluate(api+'.seek(.5)');await b.click('#play');await b.click('#pause');
 await new Promise(r=>setTimeout(r,100));assert.equal(await b.evaluate(api+'.snapshot().audio.playing'),false,'Pause cancels pending PCM');
 assert.ok((await b.evaluate(api+'.snapshot()')).videos.every(v=>v.paused));
 await b.click('#restart');await waitState(fixture.initial);const reset=await b.evaluate(api+'.snapshot()');assert.deepEqual(reset.videos.map(v=>v.source.number),fixture.scene==='intro'?[30,60,90]:[60,90,120]);
 // Capture after playback measurements; screenshots can perturb compositor timing.
 await capture(fixture.scene,.5);await b.evaluate(api+'.seek(0)');
 assert.deepEqual(report.browser_exceptions,[],'No uncaught browser errors');
}catch(error){report.problems.push(String(error.stack??error));report.failure_state=await b.evaluate(`({error:document.getElementById('error').textContent,scenes:${sceneProbe()}})`).catch(()=>null);}
finally{await persist();await b.close();}
console.log(JSON.stringify({edits:report.edits.length,reviews:report.playback.length,max_ms:Math.max(0,...report.edits.map(e=>e.elapsed_ms)),problems:report.problems}));
if(report.problems.length)process.exitCode=1;
