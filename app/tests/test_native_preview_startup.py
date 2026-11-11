"""A user's Pause/seek/dispose must cancel a pending audio-device startup."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node is required')
@pytest.mark.parametrize('action', ['pause', 'seek', 'dispose', 'dispose_seek'])
@pytest.mark.parametrize('phase', ['resume', 'output_clock'])
def test_cancel_during_audio_startup(action, phase):
    path = Path(__file__).parents[1]/'src/moviestar/preview_native_stage.mjs'
    stage = path.read_text().split('\n', 1)[1]
    script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
let resume,clock,releaseClock;const clockWait=new Promise(resolve=>releaseClock=resolve);
const video={paused:true,plays:0,currentTime:1,readyState:4,listeners:new Map(),
 addEventListener(name,fn){this.listeners.set(name,fn);},removeEventListener(name){this.listeners.delete(name);},
 pause(){this.paused=true;},async play(){this.plays++;this.paused=false;},
 removeAttribute(){},load(){},cancelVideoFrameCallback(){}};
const audio={state:'suspended',resume:()=>new Promise(resolve=>resume=resolve),close:async()=>{},
 getOutputTimestamp:()=>clock?{contextTime:1,performanceTime:1}:{contextTime:0,performanceTime:0}};
const context={window:{moviestarScene:{entries:[],applyMotion(){}}},document:{createElement:()=>({getContext:()=>({})})},
 location:{search:'?request=unit'},URLSearchParams,AbortController,performance:{now:()=>1},Date,
 fetch:()=>new Promise(()=>{}),clearTimeout(){},setTimeout:fn=>{clockWait.then(fn);return 1;},
 requestAnimationFrame:fn=>fn(),console};
vm.createContext(context);vm.runInContext(STAGE,context);
context.testVideo=video;context.testAudio=audio;
vm.runInContext("packet={fps:30,source_start:1,samples:Array(150).fill({nodes:[]})};video=testVideo;audio=testAudio;resolveReady(true)",context);
const api=context.window.moviestarLiveStage,flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const pending=api.play();await flush();
 if(PHASE==='output_clock'){resume();await flush();}
 let canceled;
 if(ACTION==='dispose_seek'){canceled=api.seek(2);await flush();api.dispose();}
 else canceled=ACTION==='seek'?api.seek(0):api[ACTION]();
 if(PHASE==='resume')resume();clock=true;releaseClock();
 await pending;await canceled;
 assert.equal(video.plays,0,'Canceled startup must never call video.play()');
 assert.equal(video.paused,true);
 if(ACTION==='seek'){api.metrics.finished=true;await api.seek(0);assert.equal(api.metrics.finished,false,'Manual seek clears the completed marker');}
 assert.equal(video.listeners.size,0,'Disposal removes pending seek listeners');
 if(!ACTION.startsWith('dispose')){
   audio.resume=async()=>{};await api.play();assert.equal(video.plays,1,'A new Play still works');
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', 'const STAGE='+json.dumps(stage)+';const ACTION='+json.dumps(action)+';const PHASE='+json.dumps(phase)+';\n'+script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node is required')
@pytest.mark.parametrize('playback_drops', [0, 2])
def test_final_seek_discards_are_separate_from_playback_quality(playback_drops):
    path = Path(__file__).parents[1]/'src/moviestar/preview_native_stage.mjs'
    stage = path.read_text().split('\n', 1)[1]
    script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const context={window:{moviestarScene:{entries:[],applyMotion(){}}},document:{createElement:()=>({getContext:()=>({})})},
 location:{search:'?request=unit'},URLSearchParams,AbortController,performance:{now:()=>1},Date,
 fetch:()=>new Promise(()=>{}),clearTimeout(){},setTimeout(){},requestAnimationFrame:fn=>fn(),console};
vm.createContext(context);vm.runInContext(STAGE,context);context.PLAYBACK_DROPS=PLAYBACK_DROPS;
vm.runInContext(`
 const make=()=>({currentTime:5.98,paused:false,drops:PLAYBACK_DROPS,pause(){this.paused=true;},
  getVideoPlaybackQuality(){return{totalVideoFrames:150,droppedVideoFrames:this.drops};}});
 const a=make(),b=make();video=a;
 videos.push({video:a,path:['main'],primary:true,beforePlay:{totalVideoFrames:0,droppedVideoFrames:0}},
  {video:b,path:['inset'],beforePlay:{totalVideoFrames:0,droppedVideoFrames:0}});
 packet={fps:30,source_start:1,samples:Array(150).fill({nodes:[]})};
 videoGroup={pause(){a.pause();b.pause();},async seek(at){a.currentTime=b.currentTime=1+at;a.drops+=2;b.drops+=2;return true;}};
 playing=true;resolveReady(true);
`,context);
(async()=>{
 await vm.runInContext('finishScene()',context);
 const metrics=context.window.moviestarLiveStage.metrics;
 assert.equal(metrics.finished,true);
 assert.deepEqual(Array.from(metrics.video_quality,q=>q.dropped_frames),[PLAYBACK_DROPS,PLAYBACK_DROPS],
  'Only post-playback seek discards are excluded; real playback drops remain visible');
 assert.equal(metrics.settled_quality.droppedVideoFrames,2*PLAYBACK_DROPS+4,'Keep post-seek counters for diagnosis');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', f'const STAGE={json.dumps(stage)},PLAYBACK_DROPS={playback_drops};\n'+script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr
