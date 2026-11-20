"""Sparse frame callbacks must not invent a missing cue or hide real AV drift."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
@pytest.mark.parametrize('case', ['sparse', 'late_audio', 'no_clock', 'missing_frame', 'missing_audio', 'dropped_frame'])
def test_generated_cue_clock_uses_presented_frame_times_without_relaxing_limits(case):
    module = (Path(__file__).parents[2]/'experiments/live_browser_preview/cue_clock.mjs').as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {measureCueClock}=await import(MODULE);
const metrics={frames:[],flashes:[],beeps:[],video_quality:[{dropped_frames:0}]};
for(let frame=0;frame<150;frame++){
 const time=frame/30;
 // Omit BOTH white callbacks at the middle cue. The decoder can still
 // present them: rVFC does not promise one callback per presentation.
 if(frame!==66&&frame!==67)metrics.frames.push({media_time:1+time,display_time:1000+time*1000});
}
for(let i=0;i<5;i++){
 if(i!==2)metrics.flashes.push({media_time:1+i+.2,display_time:1200+i*1000});
 metrics.beeps.push({display_time:1220+i*1000,timestamp_available:true});
}
if(CASE==='late_audio')metrics.beeps[2].display_time+=31; // 51ms must fail.
if(CASE==='no_clock')metrics.beeps[2].timestamp_available=false;
if(CASE==='missing_frame')metrics.frames=metrics.frames.filter(f=>Math.abs(f.media_time-3.2)>.1);
if(CASE==='missing_audio')metrics.beeps.pop();
if(CASE==='dropped_frame')metrics.video_quality[0].dropped_frames=1;
const result=measureCueClock(metrics,{fps:30,sourceStart:1,count:5});
assert.equal(result.observed_flashes,4,'Keep the observation gap explicit');
if(CASE==='sparse'){
 assert.deepEqual(result.problems,[]);assert.equal(result.cues.length,5);
 assert.ok(result.cues.every(c=>Math.abs(c.phase_ms-20)<1e-6));
 assert.ok(result.cues[2].nearest_frame_distance_ms<=1000/30+1e-6);
}else assert.ok(result.problems.length>0,CASE+' must not pass');
'''
    result = subprocess.run(['node', '--input-type=module', '-e', f'const MODULE={json.dumps(module)},CASE={json.dumps(case)};\n'+script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr


@pytest.mark.parametrize('rate', [1.25,1.5])
@pytest.mark.parametrize('late', [False,True])
def test_uniform_speed_cue_instants_keep_the_existing_presentation_gate(rate, late):
    module=(Path(__file__).parents[2]/'experiments/live_browser_preview/cue_clock.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {measureCueClock}=await import(MODULE);
const cueTimes=[.2,1.2,2.2].map(t=>t/RATE);
const metrics={frames:[],flashes:[{},{}],beeps:[],video_quality:[{dropped_frames:0}]};
for(let i=0;i<60*RATE;i++){
 if(i===6||i===7)continue; // Keep the known sparse observation explicit.
 const local=i/(30*RATE);metrics.frames.push({media_time:1+i/30,result_time:local,display_time:1000+local*1000});
}
for(const [i,time] of cueTimes.entries())metrics.beeps.push({display_time:1000+time*1000+20+(LATE&&i===1?31:0),timestamp_available:true});
const result=measureCueClock(metrics,{fps:30*RATE,count:3,cueTimes});
assert.equal(result.observed_flashes,2);
if(LATE)assert.ok(result.problems.some(p=>p.includes('exceeds 50')),'A real 51ms error still fails');
else{assert.deepEqual(result.problems,[]);assert.equal(result.cues.length,3);assert.ok(result.cues.every(c=>Math.abs(c.phase_ms-20)<1e-6));}
'''
    result=subprocess.run(['node','--input-type=module','-e',f'const MODULE={json.dumps(module)},RATE={rate},LATE={json.dumps(late)};\n'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
