import assert from 'node:assert/strict';
import {readFile,writeFile,readdir,copyFile,mkdir} from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {Browser} from './browser.mjs';
const [url,root,out,python,chrome]=process.argv.slice(2),run=promisify(execFile);
const fixture=JSON.parse(await readFile(out+'/case.json','utf8'));
const report={edits:[],playback:[],problems:[]},b=await Browser.launch(chrome,out);
const api='document.querySelector("iframe:not(.pending)").contentWindow.moviestarLiveStage';
try{
 await b.send('Page.navigate',{url:url+'?audit=1'});
 await b.wait('window.movieStarPreview?.is_current',15000);
 // Preserve authored resolution at 1x, as in the existing reference workload.
 await b.evaluate(`(async()=>{const stage=document.getElementById('stage'),style=document.createElement('style');style.textContent='#stage{max-width:none!important;width:'+(${fixture.canvas[0]}+stage.offsetWidth-stage.clientWidth)+'px!important}';document.head.append(style);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()`);
 const checkRaster=async()=>assert.deepEqual(await b.evaluate(`(()=>{const r=document.querySelector('iframe:not(.pending)').getBoundingClientRect();return{width:r.width,height:r.height};})()`),{width:fixture.canvas[0],height:fixture.canvas[1]},'Authored raster stays at 1x');
 await checkRaster();
 const playback=async()=>{
  await b.evaluate(`(async()=>{await ${api}.seek(.5);await ${api}.seek(0);})()`);
  await b.click('#play');await b.wait(api+'.metrics.finished',6000);
  await checkRaster();
  const m=await b.evaluate(api+'.metrics');report.playback.push(m);
  assert.equal(m.error,null);assert.equal(m.stalls,0);assert.equal(m.quality.droppedVideoFrames,0);
  assert.equal(m.flashes.length,m.beeps.length);assert.ok(m.flashes.length>0);
  m.flashes.forEach((f,i)=>assert.ok(Math.abs(f.display_time-m.beeps[i].display_time)<=50,'Output phase <=50ms'));
  const rate=await b.evaluate(api+'.packet.playback_rate');
  const origin=await b.evaluate('document.querySelector("iframe:not(.pending)").contentWindow.performance.timeOrigin');
  const zero=origin+m.frames[0].display_time-(m.frames[0].media_time-1)/rate*1000;
  m.program_drift_ms=m.paused_at-zero-2000;
  assert.ok(Math.abs(m.program_drift_ms)<=50,'Program clock <=50ms');
  for(const f of m.frames){
   assert.equal(f.number,Math.round((f.captured_media_time??f.media_time)*30)%240,'Exact captured source identity');
   assert.ok(Math.abs((f.captured_media_time??f.media_time)-f.media_time)<=1/30+1e-6);
  }
  const final=await b.evaluate(api+'.snapshot()');
  assert.equal(final.source.number,Math.floor((1+(2-1/30)*rate)*30+1e-5),'Canonical final held frame');
 };
 for(const [i,action] of fixture.actions.entries()){
  await b.evaluate(`(async()=>{await ${api}.seek(0);})()`);
  const previous=await b.evaluate(api+'.packet.state_id'),start=performance.now();
  const result=await run(python,['-m','moviestar.cli',...action.args],{cwd:root,timeout:30000});
  const cliElapsed=performance.now()-start;
  await writeFile(out+`/command-${i}.json`,result.stdout);
  await b.wait(`window.movieStarPreview?.is_current&&${api}.packet.state_id!==${JSON.stringify(previous)}`,10000);
  const elapsed=performance.now()-start,snap=await b.evaluate(api+'.snapshot()');
  const rate=await b.evaluate(api+'.packet.playback_rate');
  report.edits.push({index:i,args:action.args,elapsed_ms:elapsed,cli_ms:cliElapsed,state_id:snap.state_id,rate});
  const state=await b.evaluate('window.movieStarPreview.state');
  await writeFile(out+`/state-${i}.json`,JSON.stringify(state));
  const request=await b.evaluate('window.movieStarPreview.displayed.request_id');
  const sessions=(await readdir(root+'/moviestar/local-preview')).filter(name=>name.startsWith('session-'));
  const evidence=out+`/preparation-${i}`;await mkdir(evidence);
  for(const name of ['request.json','timing.json','preview.json']){
   for(const session of sessions)try{await copyFile(root+'/moviestar/local-preview/'+session+'/'+request+'/'+name,evidence+'/'+name);}catch{}
  }
  await checkRaster();
  assert.equal(rate,action.rate);assert.equal(snap.source.number,30,'Correct fresh first source frame');
  if(elapsed>=3000)report.problems.push(`Edit ${i}: ${elapsed}ms`);
  // Verify the selected clock after each edit and undo as well as fresh readiness.
  const at=await b.evaluate(`(async()=>{await ${api}.seek(1);return ${api}.snapshot();})()`);
  assert.equal(at.source.number,Math.floor((1+action.rate)*30+1e-5));
  if(i===0||i===fixture.actions.length-1)await playback();
 }
}catch(error){report.failure_state=await b.evaluate('({state:window.movieStarPreview?.state,error:document.getElementById("error")?.textContent})').catch(()=>null);report.problems.push(String(error.stack??error));}
finally{
 await writeFile(out+'/report.json',JSON.stringify(report,null,2));await b.close();
}
console.log(JSON.stringify({edits:report.edits.length,max_ms:Math.max(...report.edits.map(e=>e.elapsed_ms)),problems:report.problems}));
if(report.problems.length)process.exitCode=1;
