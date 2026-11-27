"""Decoded frame queues own their frames and cannot hide missing pictures."""
import json
from pathlib import Path
import subprocess

def test_rounded_packet_timestamps_cover_every_instant_without_gaps_or_early_frames():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_decode.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';const {createFrameQueue}=await import(MODULE);
const packets=Array.from({length:90},(_,i)=>({timestamp:Math.round(i*1e6/30),duration:33333,key:i%30===0,offset:i,size:1}));
const decoderFactory=c=>({state:'configured',configure(){},decode(chunk){c.output({timestamp:chunk.timestamp,duration:33333,close(){}});},close(){this.state='closed';},flush:async()=>{}});
const q=createFrameQueue({config:{codedWidth:16,codedHeight:16},packets,start:0,end:3,fps:30},
 {decoderFactory,chunkFactory:x=>x,fetchPacket:async()=>new Uint8Array([0]),maxFrames:4});
// This exact clock instant failed standalone HD review: the preceding frame
// was discarded 2us early while the next rounded PTS was still in the future.
await q.seek(1.866664);assert.equal(q.frameAt(1.866664).timestamp,1833333);
for(let i=57;i<75;i++)for(const delta of [-3,-2,-1,0,1,2,3]){
 const at=packets[i].timestamp+delta;await q.ensure(at/1e6);
 assert.equal(q.frameAt(at/1e6).timestamp,packets[delta<0?i-1:i].timestamp,'Select the exact original packet interval');
}
assert.throws(()=>q.frameAt(2.8),/not ready/,'Never extend an old picture into a missing next packet');q.dispose();
'''
    result=subprocess.run(['node','--input-type=module','-e',f'const MODULE={json.dumps(module)};'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr

def test_frame_queue_is_bounded_and_cancellation_closes_every_frame():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_decode.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {createFrameQueue}=await import(MODULE);
let live=0,peak=0;
class Frame {constructor(timestamp){this.timestamp=timestamp;this.duration=33333;live++;peak=Math.max(peak,live);}close(){assert.ok(!this.closed);this.closed=true;live--;}}
class Decoder {constructor(callbacks){this.callbacks=callbacks;this.decodeQueueSize=0;this.state='unconfigured';}configure(){this.state='configured';}decode(chunk){this.callbacks.output(new Frame(chunk.timestamp));}async flush(){}close(){this.state='closed';}}
const packets=Array.from({length:60},(_,i)=>({timestamp:i*33333,duration:33333,key:i%30===0,offset:i,size:1}));
const q=createFrameQueue({config:{codec:'test',codedWidth:16,codedHeight:16},packets,start:0,end:2},
 {maxFrames:4,fetchPacket:async()=>new Uint8Array([0]),decoderFactory:c=>new Decoder(c),chunkFactory:x=>x});
await q.seek(0);assert.equal(q.frameAt(0).timestamp,0);
for(let i=0;i<50;i++){
 await q.ensure(i*33333/1e6);assert.equal(q.frameAt(i*33333/1e6).timestamp,i*33333);
 assert.ok(live<=4,'Decoded pictures must stay within the explicit frame bound');
}
assert.throws(()=>q.frameAt(1.99),/not ready/,'Never conceal starvation with an older frame');
await q.seek(.99999);assert.equal(q.frameAt(.99999).timestamp,999990);
q.dispose();assert.equal(live,0);assert.ok(peak<=4);
let release;
const pending=createFrameQueue({config:{codec:'test',codedWidth:16,codedHeight:16},packets,start:0,end:2},
 {maxFrames:4,fetchPacket:()=>new Promise(r=>release=r),decoderFactory:c=>new Decoder(c),chunkFactory:x=>x});
const preparing=pending.seek(0);await new Promise(r=>setImmediate(r));pending.dispose();release(new Uint8Array([0]));
assert.equal(await preparing,false);assert.equal(live,0,'Canceled fetch cannot resurrect a decoder or frame');
'''
    result=subprocess.run(['node','--input-type=module','-e',f'const MODULE={json.dumps(module)};'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr

def test_replacement_seek_cancels_pending_decoder_flush_without_failing_new_seek():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_decode.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {createFrameQueue}=await import(MODULE);let number=0,rejectFlush;
class Decoder {constructor(cb){this.cb=cb;this.id=++number;this.state='configured';}configure(){}decode(c){this.cb.output({timestamp:c.timestamp,duration:33333,close(){}});}flush(){return this.id===1?new Promise((r,j)=>rejectFlush=j):Promise.resolve();}close(){this.state='closed';if(this.id===1)rejectFlush(Error('Aborted due to close()'));}}
const packets=[{timestamp:0,duration:33333,key:true,offset:0,size:1}];
const q=createFrameQueue({config:{codedWidth:16,codedHeight:16},packets,start:0,end:.1},
 {fetchPacket:async()=>new Uint8Array([0]),decoderFactory:c=>new Decoder(c),chunkFactory:x=>x,maxFrames:4});
const old=q.seek(0);old.catch(()=>{});await new Promise(r=>setImmediate(r));
assert.ok(rejectFlush);assert.equal(await q.seek(0),true);assert.equal(await old,false);
assert.equal(q.frameAt(0).timestamp,0);q.dispose();
'''
    result=subprocess.run(['node','--input-type=module','-e',f'const MODULE={json.dumps(module)};'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr

def test_canvas_cue_gate_accounts_for_paint_uncertainty_without_widening_fifty_ms():
    helper=(Path(__file__).parents[2]/'experiments/live_browser_preview/cue_clock.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';import {measureCueClock} from MODULE;
const metrics={frames:[{media_time:1.2,display_time:1000}],flashes:[{}],beeps:[{timestamp_available:true,display_time:1040}],video_quality:[{dropped_frames:0,display_uncertainty_ms:17}]};
assert.ok(measureCueClock(metrics,{fps:30,sourceStart:1,count:1}).problems.length,'40ms estimate plus 17ms paint uncertainty must fail the existing 50ms gate');
metrics.beeps[0].display_time=1030;assert.deepEqual(measureCueClock(metrics,{fps:30,sourceStart:1,count:1}).problems,[]);
'''.replace('from MODULE','from '+json.dumps(helper))
    result=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


def test_decoder_output_cannot_exceed_declared_picture_or_queue_budget():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_decode.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';const {createFrameQueue}=await import(MODULE);
for(const mode of ['dimensions','budget']){
 let closed=0;
 const frame={timestamp:0,duration:33333,displayWidth:mode==='dimensions'?32:16,displayHeight:16,codedWidth:mode==='budget'?128:32,codedHeight:16,close(){closed++;}};
 const decoderFactory=c=>({state:'configured',configure(){},decode(){c.output(frame);},close(){this.state='closed';},flush:async()=>{}});
 const q=createFrameQueue({config:{codedWidth:16,codedHeight:16},packets:[{timestamp:0,duration:33333,key:true,size:1,offset:0}],start:0,end:1},
  {decoderFactory,chunkFactory:x=>x,fetchPacket:async()=>new Uint8Array([0]),maxFrames:4,maxBytes:4096});
 await assert.rejects(q.seek(0),/dimensions|budget/);q.dispose();assert.equal(closed,1,'Reject and close an unexpected output surface without retaining it');
}
'''
    result=subprocess.run(['node','--input-type=module','-e',f'const MODULE={json.dumps(module)};'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


def test_concurrent_refill_requests_serialize_and_keep_the_frame_reservation_bound():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_decode.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';const {createFrameQueue}=await import(MODULE);
let fetching=0,peak=0;
const decoderFactory=c=>({state:'configured',configure(){},decode(coded){c.output({timestamp:coded.timestamp,duration:33333,close(){}});},close(){this.state='closed';},flush:async()=>{}});
const packets=Array.from({length:60},(_,i)=>({timestamp:i*33333,duration:33333,key:i===0,offset:i,size:1}));
const q=createFrameQueue({config:{codedWidth:16,codedHeight:16},packets,start:0,end:2},
 {decoderFactory,chunkFactory:x=>x,maxFrames:4,fetchPacket:async()=>{fetching++;peak=Math.max(peak,fetching);await new Promise(r=>setImmediate(r));fetching--;return new Uint8Array([0]);}});
await q.seek(0);
await Promise.all([q.ensure(.033333),q.ensure(.066666),q.ensure(.099999)]);
assert.equal(peak,1,'Several waiters must not start overlapping fills after the same old operation');
assert.ok(q.metrics.peak_reserved<=4);assert.ok(q.metrics.peak_frames<=4);q.dispose();
'''
    result=subprocess.run(['node','--input-type=module','-e',f'const MODULE={json.dumps(module)};'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
