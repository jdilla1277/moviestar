"""Decoded pictures start against display cadence without moving PCM samples."""
import json
from pathlib import Path
import subprocess

import pytest

MODULE=(Path(__file__).parents[1]/'src/moviestar/preview_native_audio.mjs').as_uri()


def node(script):
    result=subprocess.run(['node','--input-type=module','-e',f'const MODULE={json.dumps(MODULE)};'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('fps',[24,25,30,50,60])
@pytest.mark.parametrize('origin',[0,.015333333,.032])
def test_decoded_start_preserves_pcm_grid_and_reduces_worst_display_phase(fps,origin):
    node('const FPS='+str(fps)+';const ORIGIN='+str(origin)+';'+r'''
import assert from 'node:assert/strict';const {createPreparedAudio}=await import(MODULE);
const period=1000/60,work=1.25,uncertainty=2*period;
for(const phase of [0,1,2,4,6,8,10,12,14,16]){
 let now=1000+phase,started,offset,observations=0,id=0;const pending=new Map();
 Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>now,timeOrigin:0}});
 globalThis.requestAnimationFrame=callback=>{
  const key=++id;pending.set(key,true);setImmediate(()=>{if(!pending.delete(key))return;
   // A missed observation must not make a 60Hz display look like 30Hz.
   now=(Math.floor(now/period)+((++observations===2)?2:1))*period;
   const stamp=now;now+=.25;callback(stamp);
  });return key;
 };
 globalThis.cancelAnimationFrame=key=>pending.delete(key);
 globalThis.setTimeout=(callback,ms)=>setImmediate(()=>{now+=ms;callback();});
 globalThis.clearTimeout=clearImmediate;
 const context={sampleRate:48000,get currentTime(){return now/1000+.04;},resume:async()=>{},
  getOutputTimestamp:()=>({contextTime:now/1000,performanceTime:now}),
  decodeAudioData:async()=>({duration:3,numberOfChannels:2,sampleRate:48000}),
  createBufferSource:()=>({connect(){},disconnect(){},stop(){},start(at,from){started=at;offset=from;}})};
 globalThis.fetch=async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(1)});
 const audio=createPreparedAudio(context,{},new AbortController().signal);
 (await audio.prepare({asset:'mix',duration:3,channels:2,sample_rate:48000})).commit();
 await audio.play(.5+1e-5,{displayFps:FPS,displayOrigin:ORIGIN,pictureLead:1/(2*FPS)});
 assert.equal(offset,.5,'Keep the authored sample, excluding video seek epsilon');
 assert.ok(Math.abs(started*48000-Math.round(started*48000))<1e-8,'No fractional sample interpolation');
 assert.equal(audio.snapshot().started_context_time,started,'Record the actual scheduled start');
 // Use the unchanged frame-floor rule and the actual modeled draw submission.
 // One skipped idle callback makes presentation uncertainty two refreshes.
 for(let frame=Math.ceil((offset+ORIGIN)*FPS);frame/FPS-ORIGIN<2.5;frame++){
  const edge=(started+frame/FPS-offset-ORIGIN)*1000;
  const submitted=Math.ceil((edge-.25)/period)*period+work;
  assert.ok(submitted>=edge,'Never present a future source picture');
  assert.ok(submitted-edge+uncertainty<=50,`phase ${phase}, fps ${FPS}: ${submitted-edge+uncertainty}ms`);
 }
 assert.ok(observations>=4,'Measure display cadence before choosing the PCM start');audio.dispose();
}
''')


@pytest.mark.parametrize('action',['pause','seek','dispose'])
def test_pause_seek_or_disposal_cancels_display_sampling_before_creating_audio(action):
    node('const ACTION='+json.dumps(action)+';'+r'''
import assert from 'node:assert/strict';const {createPreparedAudio}=await import(MODULE);
let frame,created=0,canceled=0;
globalThis.requestAnimationFrame=callback=>{frame=callback;return 1;};
globalThis.cancelAnimationFrame=()=>canceled++;
const context={sampleRate:48000,currentTime:2,resume:async()=>{},getOutputTimestamp:()=>({contextTime:2.05,performanceTime:1}),
 decodeAudioData:async()=>({duration:3,numberOfChannels:2,sampleRate:48000}),
 createBufferSource:()=>{created++;return{connect(){},disconnect(){},start(){},stop(){}};}};
globalThis.fetch=async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(1)});
const audio=createPreparedAudio(context,{},new AbortController().signal);
(await audio.prepare({asset:'mix',duration:3,channels:2,sample_rate:48000})).commit();
const playing=audio.play(0,{displayFps:30,pictureLead:1/60});await new Promise(r=>setImmediate(r));
assert.ok(frame,'Wait for a real display observation before scheduling audio');
audio[ACTION](.5);await playing;
assert.equal(created,0,'A canceled display wait cannot create/start a PCM source');assert.ok(canceled>0);audio.dispose();
''')


def test_missing_display_callbacks_fail_within_the_existing_startup_deadline():
    node(r'''
import assert from 'node:assert/strict';const {createPreparedAudio}=await import(MODULE);
let now=0,canceled=0;
Object.defineProperty(globalThis,'performance',{value:{now:()=>now,timeOrigin:0}});
globalThis.requestAnimationFrame=()=>1;globalThis.cancelAnimationFrame=()=>canceled++;
globalThis.setTimeout=(fn,ms)=>setImmediate(()=>{now+=ms;fn();});globalThis.clearTimeout=clearImmediate;
const context={sampleRate:48000,currentTime:2,resume:async()=>{},getOutputTimestamp:()=>({contextTime:2.05,performanceTime:1}),
 decodeAudioData:async()=>({duration:3,numberOfChannels:2,sampleRate:48000}),createBufferSource:()=>{throw Error('Must not create a source');}};
globalThis.fetch=async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(1)});
const audio=createPreparedAudio(context,{},new AbortController().signal);
(await audio.prepare({asset:'mix',duration:3,channels:2,sample_rate:48000})).commit();
await assert.rejects(audio.play(0,{displayFps:30}),/display clock/i);assert.ok(now<=2010);assert.ok(canceled>0);audio.dispose();
''')


def test_audio_dispatch_margin_is_reserved_after_main_thread_source_setup():
    node(r'''
import assert from 'node:assert/strict';const {createPreparedAudio}=await import(MODULE);
let clock=2,started;
const context={sampleRate:48000,get currentTime(){return clock;},resume:async()=>{},
 getOutputTimestamp:()=>({contextTime:2.5,performanceTime:1}),
 decodeAudioData:async()=>({duration:3,numberOfChannels:2,sampleRate:48000}),
 createBufferSource:()=>({connect(){clock+=.06;},disconnect(){},stop(){},start(at){started=at;}})};
globalThis.fetch=async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(1)});
const audio=createPreparedAudio(context,{},new AbortController().signal);
(await audio.prepare({asset:'mix',duration:3,channels:2,sample_rate:48000})).commit();
await audio.play(0);assert.ok(started-clock>=.05-1/48000,'Reserve dispatch time after source creation/connection, not before');audio.dispose();
''')


def test_decoded_seek_holds_its_prepared_frame_until_scheduled_pcm_catches_up():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_decoded_media.mjs').as_uri()
    node('const MEDIA='+json.dumps(module)+';'+r'''
import assert from 'node:assert/strict';const {createDecodedMedia}=await import(MEDIA);
let raf,clock=0,failure;const submitted=[],readings=[];
globalThis.requestAnimationFrame=fn=>{raf=fn;return 1;};globalThis.cancelAnimationFrame=()=>{};
globalThis.document={createElement:()=>Object.assign(new EventTarget(),{getContext:()=>({drawImage:f=>submitted.push(f.timestamp)})})};
globalThis.location={href:'http://localhost/'};
globalThis.EncodedVideoChunk=class{constructor(value){Object.assign(this,value);}};
globalThis.VideoDecoder=class{
 constructor(callbacks){this.callbacks=callbacks;this.state='configured';}configure(){}
 decode(chunk){this.callbacks.output({timestamp:chunk.timestamp,duration:chunk.duration,close(){}});}
 flush(){return Promise.resolve();}close(){this.state='closed';}
};
globalThis.fetch=async()=>({status:206,arrayBuffer:async()=>new ArrayBuffer(131072)});
const index={start:0,end:3,fps:30,config:{codedWidth:16,codedHeight:16,description:[]},
 packets:Array.from({length:90},(_,i)=>({offset:i,size:1,timestamp:Math.round(i*1e6/30),duration:33333,key:i%30===0}))};
const player=createDecodedMedia({video_decode:index},{offset:0,clock:()=>readings.shift()??clock,signal:new AbortController().signal,onFailure:e=>{failure=e;}});
const ready=new Promise(r=>player.addEventListener('canplay',r,{once:true}));player.src='/source';await ready;
const sought=new Promise(r=>player.addEventListener('seeked',r,{once:true}));player.currentTime=.5;await sought;
clock=.5-1/60;await player.play();raf(16);
assert.equal(failure,undefined,'Do not request a frame before the selected seek while PCM is scheduled ahead');
assert.equal(player.currentTime,.5);assert.equal(submitted.at(-1),500000);
clock=.5+1/30;raf(33);assert.equal(failure,undefined);assert.equal(submitted.at(-1),533333);
// A later clock query in one tick must not become the next tick's lower
// bound: only the selected seek/resume position owns that floor.
readings.push(clock,clock+.15,clock);raf(50);clock=.5+2/30;raf(66);
assert.equal(failure,undefined);assert.equal(submitted.at(-1),566667);player.load();
''')


def test_output_clock_loss_during_display_sampling_creates_no_connected_source():
    node(r'''
import assert from 'node:assert/strict';const {createPreparedAudio}=await import(MODULE);
let frames=0,created=0;
globalThis.requestAnimationFrame=fn=>setImmediate(()=>fn(++frames*1000/60));globalThis.cancelAnimationFrame=clearImmediate;
const context={sampleRate:48000,currentTime:2,resume:async()=>{},
 getOutputTimestamp:()=>frames<4?{contextTime:2.05,performanceTime:1}:{contextTime:0,performanceTime:0},
 decodeAudioData:async()=>({duration:3,numberOfChannels:2,sampleRate:48000}),
 createBufferSource:()=>{created++;return{connect(){},disconnect(){},start(){},stop(){}};}};
globalThis.fetch=async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(1)});
const audio=createPreparedAudio(context,{},new AbortController().signal);
(await audio.prepare({asset:'mix',duration:3,channels:2,sample_rate:48000})).commit();
await assert.rejects(audio.play(0,{displayFps:30}),/output clock is unavailable/);
assert.equal(created,0,'Validate the output clock before connecting an unstarted buffer');audio.dispose();
''')


def test_cut_seek_aligns_selected_source_origin_relative_to_the_mix_window():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_cuts.mjs').as_uri()
    node('const CUTS='+json.dumps(module)+';'+r'''
import assert from 'node:assert/strict';const {createCutPlayback}=await import(CUTS);
let master=0;const calls=[];
const packet={fps:30,range:{from:10,to:16},segments:[0,3].map((offset,i)=>({offset,packet:{
 source_start:[1.015333333,2.032][i],decoded_transport:true,range:{from:10+offset,to:13+offset},media:Array(3).fill({kind:'video'})}}))};
const stages=packet.segments.map(s=>({gain:{gain:{cancelScheduledValues(){},setValueAtTime(){}}},api:{packet:s.packet,time:0,metrics:{},
 pause(){},async play(){},async seek(at){this.time=at;},snapshot(){return{};}}}));
const api=createCutPlayback({packet,stages,audio:{sampleRate:48000,currentTime:0},metrics:{},show(){},
 reviewAudio:{snapshot:()=>({prepared:true}),pause(){},seek(at){master=at;},outputTimeAt:()=>master,async play(at,options){calls.push({at,options});}}});
await api.play();assert.equal(calls[0].options.displayOrigin,1.015333333);assert.equal(calls[0].options.displayFps,30);
await api.seek(3.5);await api.play();assert.equal(calls[1].at,3.5);
assert.ok(Math.abs(calls[1].options.displayOrigin+calls[1].at-2.532)<1e-9,'Use selected source time, excluding the result-window origin and scene offset');api.dispose();
''')
