"""Prepared audio must never leak a superseded or cancelled playback attempt."""
from pathlib import Path
import json
import subprocess

import pytest


def test_native_audio_buffer_transport_and_atomic_replacement():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_audio.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {createPreparedAudio}=await import(MODULE);
const starts=[],stops=[];let resumed=Promise.resolve(),gate;
const context={sampleRate:48000,currentTime:2,
 resume:()=>resumed,getOutputTimestamp:()=>({contextTime:2,performanceTime:1}),
 decodeAudioData:async bytes=>({numberOfChannels:2,sampleRate:48000,duration:5,id:new Uint8Array(bytes)[0]}),
 createBufferSource:()=>({connect(){},disconnect(){},start(...args){starts.push({args,buffer:this.buffer.id});},stop(){stops.push(true);}})};
globalThis.fetch=async(url,{signal})=>({ok:true,arrayBuffer:async()=>Uint8Array.of(Number(url.split('/').at(-1))).buffer});
const controller=new AbortController(),transport=createPreparedAudio(context,{},controller.signal);
const plane=id=>({asset:String(id),duration:5,sample_rate:48000,channels:2,source_audio:{muted:true},tracks:[]});
(await transport.prepare(plane(1),controller.signal)).commit();
await transport.play(1.5);assert.deepEqual(starts[0],{args:[2,1.5],buffer:1});
assert.ok(Math.abs(transport.outputTimeAt(performance.timeOrigin+501)-2)<1e-6);
transport.pause();assert.equal(transport.outputTimeAt(performance.timeOrigin+501),null);assert.equal(stops.length,1);assert.equal(transport.snapshot().playing,false);
// Delayed resume cannot start after Pause, seek or disposal.
resumed=new Promise(resolve=>gate=resolve);const late=transport.play(0);transport.pause();gate();await late;
assert.equal(starts.length,1);
resumed=Promise.resolve();transport.seek(2.5);assert.equal(transport.snapshot().time,2.5);
const next=await transport.prepare(plane(2),controller.signal);
assert.equal(transport.snapshot().asset,'1');next.commit();await transport.play(0);
assert.equal(starts.at(-1).buffer,2);
const aborted=new AbortController(),stale=await transport.prepare(plane(3),aborted.signal);aborted.abort();
assert.throws(()=>stale.commit());assert.equal(transport.snapshot().asset,'2');stale.cancel();
(await transport.prepare(null,controller.signal)).commit();await transport.play(0);assert.equal(starts.length,2);
const disposed=await transport.prepare(plane(4),controller.signal);transport.dispose();assert.throws(()=>disposed.commit());
'''.replace('MODULE',repr(module))
    result=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


def test_final_audio_meter_keeps_late_batches_but_excludes_paused_output():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_audio.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {createMixedOutput}=await import(MODULE);
let messages=[];globalThis.AudioWorkletNode=class{
 constructor(){this.port={postMessage:m=>messages.push(m),close(){}};}
 connect(){}disconnect(){}
};
const controller=new AbortController(),metrics={play_context_time:1,pause_context_time:2,audio_blocks:[],beeps:[]};
const context={destination:{},audioWorklet:{addModule:async()=>{}},getOutputTimestamp:()=>({contextTime:1,performanceTime:100})};
const output=await createMixedOutput(context,metrics,controller.signal,{audit:true,capturePcm:true});
output.node.port.onmessage({data:{kind:'blocks',blocks:[.9,1.1,2.1].map(t=>({kind:'block',context_time:t,samples:[[.1],[.2]]}))}});
assert.equal(metrics.audio_blocks.length,1);assert.deepEqual(metrics.audio_blocks[0].samples,[[.1],[.2]]);
output.node.port.onmessage({data:{kind:'cue',context_time:1.2,rms:.1}});assert.equal(metrics.beeps.length,1);
assert.deepEqual(metrics.beeps[0].output_timestamp,{contextTime:1,performanceTime:100});
assert.ok(Number.isFinite(metrics.beeps[0].timestamp_sampled_at));
const pending=output.drain();output.node.port.onmessage({data:{kind:'flushed',id:messages.at(-1).id}});await pending;
const cancelled=output.drain();controller.abort();await assert.rejects(cancelled,/Superseded/);
'''.replace('MODULE',repr(module))
    result=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('fps', [24,30,60])
def test_picture_launch_reserves_half_source_frame_before_audible_output(fps):
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_audio.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {createPreparedAudio}=await import(MODULE);
let output=2,started,stops=0;
const context={sampleRate:48000,currentTime:2.04,resume:async()=>{},
 getOutputTimestamp(){output+=.001;return{contextTime:output,performanceTime:1};},
 decodeAudioData:async()=>({numberOfChannels:2,sampleRate:48000,duration:5}),
 createBufferSource:()=>({connect(){},disconnect(){},start(at,offset){started={at,offset};},stop(){stops++;}})};
globalThis.fetch=async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(1)});
const transport=createPreparedAudio(context,{},new AbortController().signal);
(await transport.prepare({asset:'test',duration:5,sample_rate:48000,channels:2})).commit();
await transport.play(1.5,{pictureLead:1/(2*FPS)});
assert.deepEqual(started,{at:2.04,offset:1.5},'Keep every authored sample and its offset');
assert.ok(output<started.at,'Launch pictures before the audible edge, not a frame late');
assert.ok(output+1/(2*FPS)>=2.04-1/48000,'Do not launch earlier than the single-frame allowance');
transport.pause();assert.equal(stops,1);
'''
    result=subprocess.run(['node','--input-type=module','-e','const MODULE='+json.dumps(module)+';const FPS='+str(fps)+';'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
