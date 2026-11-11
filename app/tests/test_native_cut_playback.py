"""Cut controls retain cancellation and frame-boundary seeking across scenes."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
@pytest.mark.parametrize('case',['initial_play','sequence','seek','pause_startup','dispose_startup','cancel_restart_seek','failure','source_eof_audio','buffer_fallback','settle_failure','settle_cancel','drain_finish','drain_cancel','drain_failure'])
def test_cut_playback_controls(case):
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_cuts.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {createCutPlayback}=await import(MODULE);
let resolvePlay,resolveSeek,resolveDrain,rejectDrain,delayDrain=false,delayPlay=false,delaySeek=false,cleaned=0,shown=[];
const metrics={},audio={currentTime:1,state:'running'},packet={range:{from:0,to:9},fps:30,
 segments:[0,3,6].map((offset,i)=>({offset,packet:{range:{from:offset,to:offset+3}}}))};
const stages=packet.segments.map((segment,i)=>{
 const api={time:0,paused:true,plays:0,metrics:{stalls:0,source_buffering:true,quality:{totalVideoFrames:90,droppedVideoFrames:0}},packet:{source_start:10+i},
  pause(){this.paused=true;},async play(){if(delayPlay)await new Promise(resolve=>resolvePlay=resolve);this.plays++;this.paused=false;},
  async seek(at){if(delaySeek)await new Promise(resolve=>resolveSeek=resolve);this.time=at;},snapshot(){return{source:{number:i},boxes:[]};}};
 return{api,gain:{value:0,gain:{cancelScheduledValues(){},setValueAtTime(value,at){this.value=value;this.at=at;}}}};
});
const api=createCutPlayback({packet,stages,audio,metrics,show:i=>shown.push(i),cleanup:()=>cleaned++,drain:async()=>{if(delayDrain)await new Promise((resolve,reject)=>{resolveDrain=resolve;rejectDrain=reject;});}});
const gains=()=>stages.map(stage=>stage.gain.gain.value),flush=()=>new Promise(resolve=>setImmediate(resolve));
if(CASE==='initial_play'){
 await api.play();await new Promise(resolve=>setTimeout(resolve,15));assert.equal(api.paused,false);assert.equal(metrics.cuts.length,0);
}else if(CASE==='sequence'){
 await api.play();assert.deepEqual(gains(),[1,0,0]);
 for(let i=0;i<3;i++){
  stages[i].api.time=3;await stages[i].api.onSegmentEnd(Date.now());
  if(i<2){assert.equal(api.time,(i+1)*3);assert.equal(api.snapshot().segment,i+1);assert.equal(gains()[i+1],1);}
 }
 assert.equal(metrics.finished,true);assert.equal(metrics.end_result_time,9);
 assert.equal(metrics.cuts.length,2);assert.equal(api.paused,true);assert.deepEqual(gains(),[0,0,0]);
}else if(CASE==='source_eof_audio'){
 await api.play();audio.currentTime=4;stages[0].api.time=3;
 await stages[0].api.onSegmentEnd(Date.now());
 assert.ok(stages[1].gain.gain.at<=audio.currentTime,'An EOF handoff must not add silence by deferring the running incoming source');
}else if(CASE==='seek'){
 for(const [at,segment,local] of [[3-1/30,0,3-1/30],[3,1,0],[3+1/30,1,1/30],[6,2,0]]){
  await api.seek(at);assert.equal(api.snapshot().segment,segment);assert.ok(Math.abs(stages[segment].api.time-local)<1e-6);assert.equal(api.paused,true);
 }
}else if(CASE==='pause_startup'||CASE==='dispose_startup'){
 delayPlay=true;const pending=api.play();await flush();
 CASE==='pause_startup'?api.pause():api.dispose();resolvePlay();await pending;
 assert.equal(api.paused,true);assert.deepEqual(gains(),[0,0,0]);
 // A late old-segment notification cannot restart a paused/disposed sequence.
 await stages[0].api.onSegmentEnd(Date.now());assert.equal(stages[1].api.plays,0);
}else if(CASE==='cancel_restart_seek'){
 // Return a deferred seek only for the active scene, preserving other readiness.
 const normal=stages[0].api.seek;stages[0].api.seek=async function(at){await new Promise(resolve=>resolveSeek=resolve);this.time=at;};
 metrics.finished=true;const pending=api.play();await flush();api.pause();resolveSeek();await pending;
 assert.equal(stages.reduce((n,s)=>n+s.api.plays,0),0,'Pause during restart seek must cancel Play');
}else if(CASE==='settle_failure'||CASE==='settle_cancel'){
 await api.seek(6);await api.play();
 let release;
 stages[2].api.settleEnd=CASE==='settle_failure'?async()=>{throw Error('final frame failed');}:()=>new Promise(r=>release=r);
 stages[2].api.time=3;const pending=stages[2].api.onSegmentEnd(Date.now());
 if(CASE==='settle_cancel'){await flush();api.pause();release();}
 await pending;assert.equal(api.paused,true);assert.deepEqual(gains(),[0,0,0]);
 assert.equal(metrics.finished,false,'Failed/canceled final seek must not claim completion');
 if(CASE==='settle_failure')assert.match(metrics.error,/final frame failed/);
}else if(CASE.startsWith('drain_')){
 await api.seek(6);await api.play();delayDrain=true;stages[2].api.time=3;
 const pending=stages[2].api.onSegmentEnd(Date.now());await flush();
 assert.equal(metrics.finished,false,'Do not publish completion before the final audio measurements arrive');
 if(CASE==='drain_cancel')api.pause();
 if(CASE==='drain_failure')rejectDrain(Error('meter failed'));else resolveDrain();
 await pending;
 assert.equal(metrics.finished,CASE==='drain_finish');
 assert.equal(api.paused,true);
 if(CASE==='drain_failure')assert.match(metrics.error,/meter failed/);
}else if(CASE==='failure'){
 await api.play();stages[0].api.onFailure(Error('source expired'));
 assert.equal(api.paused,true);assert.match(metrics.error,/source expired/);assert.deepEqual(gains(),[0,0,0]);
}else{
 assert.equal(api.sourceBuffering,true);stages[1].api.metrics.source_buffering=false;assert.equal(api.sourceBuffering,false);
}
api.dispose();api.dispose();assert.equal(cleaned,1);
'''
    result=subprocess.run(['node','--input-type=module','-e','const MODULE='+json.dumps(module)+';const CASE='+json.dumps(case)+';\n'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
@pytest.mark.parametrize('action',['play','pause','dispose','new_seek','failure','repeat_play'])
def test_play_waits_for_pending_cross_cut_seek(action):
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_cuts.mjs').as_uri()
    script=r"""
import assert from 'node:assert/strict';
const {createCutPlayback}=await import(MODULE);
const metrics={},packet={range:{from:0,to:9},fps:30,segments:[0,3,6].map(offset=>({offset,packet:{range:{from:offset,to:offset+3}}}))};
const stages=packet.segments.map((segment,i)=>({gain:{gain:{value:0,cancelScheduledValues(){},setValueAtTime(value){this.value=value;}}},api:{
 time:0,plays:0,metrics:{stalls:0},packet:{source_start:10+i},pause(){},async play(){this.plays++;},async seek(at){this.time=at;},snapshot(){return{};}
}}));
const api=createCutPlayback({packet,stages,audio:{currentTime:1,state:'running'},metrics,show:()=>{}});
const totalPlays=()=>stages.reduce((n,s)=>n+s.api.plays,0),flush=()=>new Promise(resolve=>setImmediate(resolve));
const normal=stages[1].api.seek;let resolveSeek,rejectSeek;
stages[1].api.seek=async function(at){await new Promise((resolve,reject)=>{resolveSeek=resolve;rejectSeek=reject;});this.time=at;};
const seeking=api.seek(4);seeking.catch(()=>{});
const play=api.play();play.catch(()=>{});await flush();
assert.equal(totalPlays(),0,'Play must wait for the pending destination, not start the old scene');
assert.deepEqual(stages.map(s=>s.gain.gain.value),[0,0,0]);
let repeated;
if(ACTION==='pause')api.pause();
if(ACTION==='dispose')api.dispose();
if(ACTION==='new_seek'){
 stages[1].api.seek=normal;await api.seek(7);
}
if(ACTION==='repeat_play'){repeated=api.play();await flush();assert.equal(totalPlays(),0);}
if(ACTION==='failure'){
 rejectSeek(Error('seek failed'));await assert.rejects(seeking,/seek failed/);await assert.rejects(play,/seek failed/);
 assert.equal(totalPlays(),0);assert.equal(api.paused,true);assert.match(metrics.error,/seek failed/);
}else{
 resolveSeek();await Promise.all([seeking,play,repeated]);
 if(ACTION==='play'||ACTION==='repeat_play'){
  assert.equal(totalPlays(),1);assert.equal(stages[1].api.plays,1);assert.equal(api.snapshot().segment,1);assert.equal(api.time,4);
 }else{
  assert.equal(totalPlays(),0);assert.equal(api.paused,true);assert.deepEqual(stages.map(s=>s.gain.gain.value),[0,0,0]);
  if(ACTION==='pause'){
   assert.equal(api.time,4,'Pause cancels queued playback while retaining the sought position');
   await api.play();assert.equal(stages[1].api.plays,1);assert.equal(api.time,4);
  }
  if(ACTION==='new_seek')assert.equal(api.time,7,'An obsolete seek cannot replace the newer destination');
 }
}
api.dispose();
"""
    result=subprocess.run(['node','--input-type=module','-e','const MODULE='+json.dumps(module)+';const ACTION='+json.dumps(action)+';\n'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


def test_final_cut_meter_passes_through_stereo_and_mono_samples():
    if not shutil.which('node'):pytest.skip('Node required')
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_cut_meter.mjs').read_text()
    script=r'''
const vm=require('node:vm'),assert=require('node:assert/strict');let Processor,messages=[];
class Base{port={postMessage:data=>messages.push(data)};}
vm.runInNewContext(SOURCE,{AudioWorkletProcessor:Base,currentFrame:128,sampleRate:48000,registerProcessor:(name,klass)=>Processor=klass});
const meter=new Processor({processorOptions:{audit:true}});
const left=Float32Array.of(.1,-.2,.3,0),right=Float32Array.of(.5,-.5,.5,-.5),output=[new Float32Array(4),new Float32Array(4)];
assert.equal(meter.process([[left,right]],[output]),true);
assert.deepEqual([...output[0]],[...left]);assert.deepEqual([...output[1]],[...right]);
meter.port.onmessage({data:{kind:'flush',id:1}});assert.equal(messages.find(x=>x.kind==='blocks').blocks[0].rms,.5);assert.equal(messages.filter(x=>x.kind==='cue').length,1);
messages=[];meter.process([[left]],[output]);assert.deepEqual([...output[1]],[...left]);
meter.process([],[output]);assert.deepEqual([...output[0]],[0,0,0,0]);assert.deepEqual([...output[1]],[0,0,0,0]);
const normal=new Processor({processorOptions:{audit:false}});messages=[];normal.process([[left,right]],[output]);
assert.equal(messages.filter(x=>x.kind==='blocks').length,0,'Normal playback emits no per-quantum audit stream');
'''
    result=subprocess.run(['node','-e','const SOURCE='+json.dumps(module)+';\n'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


def test_cut_meter_batches_without_losing_any_quantum_or_final_partial_batch():
    if not shutil.which('node'): pytest.skip('Node required')
    module = (Path(__file__).parents[1]/'src/moviestar/preview_native_cut_meter.mjs').read_text()
    script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');let Processor,messages=[];
class Base{port={postMessage:data=>messages.push(data)};}
const context={AudioWorkletProcessor:Base,currentFrame:0,sampleRate:48000,registerProcessor:(name,klass)=>Processor=klass};
vm.runInNewContext(SOURCE,context);
const meter=new Processor({processorOptions:{audit:true}}),output=[new Float32Array(128),new Float32Array(128)];
for(let i=0;i<13;i++){
 context.currentFrame=i*128;
 const value=i===7?0:.125,channel=new Float32Array(128).fill(value);
 meter.process([[channel,channel]],[output]);
 assert.deepEqual([...output[0]],[...channel]);assert.deepEqual([...output[1]],[...channel]);
}
const batches=()=>messages.filter(x=>x.kind==='blocks');
assert.ok(batches().length>0&&batches().length<13,'Send fewer IPC messages, not fewer measurements');
assert.ok(batches().flatMap(x=>x.blocks).length<13,'Retain a partial batch until explicitly drained');
meter.port.onmessage({data:{kind:'flush',id:9}});
const blocks=batches().flatMap(x=>x.blocks);
assert.equal(blocks.length,13,'Every render quantum, including the final partial batch, survives');
blocks.forEach((block,i)=>{
 assert.equal(block.kind,'block');assert.equal(block.context_time,i*128/48000);
 assert.equal(block.duration,128/48000);assert.equal(block.rms,i===7?0:.125);
});
assert.equal(messages.at(-1).kind,'flushed');assert.equal(messages.at(-1).id,9,'Acknowledge after publishing the final data');
'''
    result = subprocess.run(['node', '-e', 'const SOURCE='+json.dumps(module)+';\n'+script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr
