import json
from pathlib import Path
import subprocess

def test_static_three_picture_cut_completion_uses_the_master_output_clock():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_cuts.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {createCutPlayback}=await import(MODULE);
let at=0,id=0;const timers=new Map();
globalThis.setTimeout=(fn,ms)=>{timers.set(++id,{fn,due:at+ms/1000});return id;};globalThis.clearTimeout=id=>timers.delete(id);
async function tick(t){at=t;for(const[id,v]of[...timers])if(v.due<=t+1e-8){timers.delete(id);v.fn();}await new Promise(r=>setImmediate(r));}
const packet={fps:30,range:{from:0,to:2/30},segments:[0,1/30].map(offset=>({offset,packet:{range:{from:0,to:1/30},source_start:1,media:Array(3).fill({kind:'video'})}}))};
const stages=packet.segments.map(s=>({gain:{gain:{cancelScheduledValues(){},setValueAtTime(){}}},api:{packet:s.packet,time:0,
 metrics:{stalls:0,picture_startup:{prepared_frame_only:true,lead:0}},pause(){},async play(){},async prepareOutputClock(){return true;},async prepareCut(){return true;},snapshot(){return{};},holdEnd(){}}}));
const metrics={},api=createCutPlayback({packet,stages,metrics,audio:{sampleRate:48000,currentTime:0},show(){},
 reviewAudio:{snapshot:()=>({prepared:true}),async play(){},pause(){},outputTimeAt:()=>at}});
await api.play();await tick(1/30);await tick(2/30);
assert.equal(metrics.finished,true,'Static sources never advance media.currentTime; the continuous mix owns the end');assert.equal(api.paused,true);api.dispose();
'''
    result=subprocess.run(['node','--input-type=module','-e',f'const MODULE={json.dumps(module)};'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr

