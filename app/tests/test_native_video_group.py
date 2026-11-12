"""Real JS coordinator cancellation tests, independent of browser timing."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
@pytest.mark.parametrize('action', ['play', 'pause', 'seek', 'dispose', 'failure'])
def test_two_video_playback_waits_for_both_seeks_and_cancels_owned_work(action):
    module = (Path(__file__).parents[1]/'src/moviestar/preview_native_videos.mjs').as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {createVideoGroup}=await import(MODULE);
function video(){return {currentTime:0,readyState:4,seeking:false,paused:true,plays:0,listeners:new Map(),
 addEventListener(k,f){this.listeners.set(k,f);},removeEventListener(k){this.listeners.delete(k);},
 play(){this.plays++;this.paused=false;return Promise.resolve();},pause(){this.paused=true;},
 removeAttribute(){this.removed=true;},load(){},cancelVideoFrameCallback(){}};}
const a=video(),b=video();a.currentTime=1;b.currentTime=4;
const group=createVideoGroup([{video:a,start:1,end:6},{video:b,start:4,end:9}]);
const seeking=group.seek(2),play=group.play();await Promise.resolve();
assert.equal(a.plays+b.plays,0,'Both first frames must be sought before playback');
assert.equal(a.currentTime,3.00001);assert.equal(b.currentTime,6.00001);
if(ACTION==='pause')group.pause();
if(ACTION==='dispose')group.dispose();
if(ACTION==='seek'){
 const next=group.seek(1);a.listeners.get('seeked')?.();b.listeners.get('seeked')?.();await next;
}
if(ACTION==='failure'){
 a.play=()=>Promise.reject(Error('decoder failed'));
}
a.listeners.get('seeked')?.();b.listeners.get('seeked')?.();
await seeking;
if(ACTION==='failure'){await assert.rejects(play,/decoder failed/);assert.equal(a.paused,true);assert.equal(b.paused,true);}
else await play;
if(ACTION==='play')assert.equal(a.plays+b.plays,2);
else assert.equal(a.paused&&b.paused,true);
assert.equal(a.listeners.size+b.listeners.size,0,'No abandoned seek listeners');
if(ACTION!=='dispose'){group.pause();group.dispose();}
assert.equal(a.removed&&b.removed,true,'Both decoders are released');
'''
    prefix = f'const MODULE={json.dumps(module)},ACTION={json.dumps(action)};\n'
    result = subprocess.run(['node', '--input-type=module', '-e', prefix+script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
@pytest.mark.parametrize('action', ['play', 'pause', 'dispose', 'seek'])
@pytest.mark.parametrize('delayed', ['a', 'b'])
def test_both_audio_clock_play_promises_are_owned_and_cancelable(action, delayed):
    module = (Path(__file__).parents[1]/'src/moviestar/preview_native_videos.mjs').as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {createVideoGroup}=await import(MODULE);let release;
const make=()=>({currentTime:1,playbackRate:1,plays:0,paused:true,
 play(){this.plays++;this.paused=false;return Promise.resolve();},pause(){this.paused=true;},removeAttribute(){},load(){}});
const a=make(),b=make(),slow=DELAYED==='a'?a:b;
slow.play=function(){this.plays++;this.paused=false;return new Promise(r=>release=r);};
const group=createVideoGroup([{video:a,start:1,end:6,primary:true},{video:b,start:1,end:6}]);
const play=group.play();await new Promise(r=>setImmediate(r));
assert.equal(a.plays,1);assert.equal(b.plays,1,'Both audio-clock pipelines start together');
if(ACTION==='pause')group.pause();
if(ACTION==='dispose')group.dispose();
if(ACTION==='seek')await group.seek(0);
release();await play;
assert.equal(a.paused&&b.paused,ACTION!=='play','Canceled startup cannot restart either video');
group.dispose();
'''
    prefix = f'const MODULE={json.dumps(module)},ACTION={json.dumps(action)},DELAYED={json.dumps(delayed)};\n'
    result = subprocess.run(['node', '--input-type=module', '-e', prefix+script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
def test_startup_does_not_report_drift_against_a_paused_voice_clock():
    module = (Path(__file__).parents[1]/'src/moviestar/preview_native_videos.mjs').as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {createVideoGroup}=await import(MODULE);
const clock={start:1,video:{currentTime:1,paused:true}},follower={start:2,video:{currentTime:2.2,paused:false,seeking:false}};
const group=createVideoGroup([clock,follower]);
assert.doesNotThrow(()=>group.synchronize(clock),'Do not compare running time against a paused clock');
clock.video.paused=false;assert.throws(()=>group.synchronize(clock),/lost synchronization/,'Real running drift still fails');
'''
    result = subprocess.run(['node', '--input-type=module', '-e', f'const MODULE={json.dumps(module)};\n'+script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr
