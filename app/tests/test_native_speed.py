"""Uniform spatial pacing must share export clocks without changing saved intent."""
import json
from pathlib import Path
import os
import selectors
import subprocess
import sys

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.native_preview import compile_snapshot, NativePreviewUnsupported
from moviestar.spec import save_spec
from tests.test_live_browser_preview import live_project
from moviestar.spatial_layout import browser_available, _chrome_path


def paced(spec, speed):
    spec['motion'] = {'version': 1, 'scenes': [{'scene': 'Scene', 'slots': [
        {'slot': 'main', 'camera': [], 'pacing': [{'id': 'pace', 'mode': 'speed',
            'range': {'from': '0:00:00.000', 'to': '0:00:05.000', 'space': 'source-local'},
            'speed': speed}]}]}]}


@pytest.mark.parametrize('speed', [1.25, 1.5])
def test_uniform_speed_packet_maps_source_and_result_clocks(live_project, speed):
    spec, _, root = live_project
    paced(spec, speed); save_spec(spec, cwd=root)
    before = (root/'moviestar/spec.json').read_bytes()
    packet, jobs = compile_snapshot(read_snapshot(root, .5, 2))
    assert packet['playback_rate'] == speed
    media, = [m for m in packet['media'] if m['kind'] == 'video']
    assert media['playback_rate'] == speed
    assert media['source_start'] == 1 + .5*speed
    assert media['source_end'] == 1 + 2*speed
    assert packet['source_end'] == media['source_end']
    job = jobs[packet['audio_plane']['job']]
    piece, = job['source_pieces']
    assert piece['speed'] == speed
    assert piece['source_start'] == media['source_start']
    assert piece['duration'] == 1.5
    # Caption boundary and whole-component motion stay on result time.
    assert packet['samples'][15]['nodes'][2]['content'] == 'Second\nline'
    assert packet['samples'][45-1]['nodes'][0]['motion']['translate_x'] == '39.33334px'
    assert (root/'moviestar/spec.json').read_bytes() == before


@pytest.mark.parametrize('speed', [.25, .5, 2, 4])
def test_unqualified_rates_remain_explicit(live_project, speed):
    spec, _, root = live_project
    paced(spec, speed); save_spec(spec, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='1.*1.5'):
        compile_snapshot(read_snapshot(root, 0, 1))


def test_speed_rejects_a_presentation_cadence_above_sixty(live_project):
    from moviestar.project import save_project
    spec, project, root = live_project
    paced(spec, 1.5); project['sources'][0]['fps'] = 60
    save_spec(spec, cwd=root); save_project(project, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='cadence'):
        compile_snapshot(read_snapshot(root, 0, 1))


def test_source_only_audio_planning_does_not_import_the_command_tree(live_project, monkeypatch):
    import builtins
    from moviestar.native_audio import compile_audio
    from moviestar.native_preview import fail
    from moviestar.resolved import resolve_project
    snapshot = read_snapshot(live_project[2], 0, 1)
    timeline = resolve_project(snapshot['project'], snapshot['spec'])
    original = builtins.__import__
    def bounded_import(name, *args, **kwargs):
        if name == 'moviestar.cli':
            raise AssertionError('Source-only preparation must not load every CLI command')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', bounded_import)
    plane, _ = compile_audio(snapshot, timeline, 0, 1, fail)
    assert plane['tracks'] == []


def test_duration_authored_uniform_map_uses_the_same_native_clock(live_project):
    spec, _, root = live_project
    paced(spec, 1.25)
    entry = spec['motion']['scenes'][0]['slots'][0]['pacing'][0]
    entry.pop('speed'); entry.update(mode='duration', duration='0:00:04.000')
    save_spec(spec, cwd=root)
    packet, _ = compile_snapshot(read_snapshot(root, 0, 2))
    assert packet['playback_rate'] == 1.25
    assert packet['source_end'] == 3.5


def test_speed_audio_does_not_decode_an_unbounded_span(live_project):
    from moviestar.project import save_project
    spec, project, root = live_project
    spec['composition'][0]['slots'][0]['source_to'] = '0:00:21.000'
    spec['spatial']['scenes']['scene']['layout']['children'][0]['bindings']['clip']['source']['end'] = 21
    project['sources'][0]['duration']['seconds'] = 30
    paced(spec, 1.25)
    spec['motion']['scenes'][0]['slots'][0]['pacing'][0]['range']['to'] = '0:00:20.000'
    save_spec(spec, cwd=root); save_project(project, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='at most ten seconds'):
        compile_snapshot(read_snapshot(root, 0, 1))


def test_repeated_presented_seek_keeps_already_prepared_pixels():
    module = (Path(__file__).parents[1]/'src/moviestar/preview_native_videos.mjs').as_uri()
    script = '''
import assert from 'node:assert/strict';
const {createVideoGroup}=await import(MODULE);
const video={currentTime:1.00001,paused:true,playbackRate:1.5,seeking:false,pause(){},
 addEventListener(){throw Error('Same destination must not wait for a new presentation');}};
const group=createVideoGroup([{video,start:1,rate:1.5,fps:30}]);
assert.equal(await group.seek(0,{presented:true}),true);
'''
    result = subprocess.run(['node','--input-type=module','-e',f'const MODULE={json.dumps(module)};\n'+script],
        capture_output=True,text=True,timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr


def test_replaced_same_destination_seek_still_waits_for_pixels():
    module = (Path(__file__).parents[1]/'src/moviestar/preview_native_videos.mjs').as_uri()
    script = '''
import assert from 'node:assert/strict';
const {createVideoGroup}=await import(MODULE);
let callback,next=0;
const video={currentTime:1,paused:true,playbackRate:1.5,seeking:false,listeners:new Map(),pause(){},
 addEventListener(k,f){this.listeners.set(k,f);},removeEventListener(k){this.listeners.delete(k);},
 requestVideoFrameCallback(fn){callback=fn;return ++next;},cancelVideoFrameCallback(){callback=null;}};
const group=createVideoGroup([{video,start:1,rate:1.5,fps:30}]);
const first=group.seek(1,{presented:true});video.listeners.get('seeked')();
let settled=false;
const replacement=group.seek(1,{presented:true}).then(ok=>{settled=true;return ok;});
await new Promise(r=>setImmediate(r));assert.equal(settled,false,'seeked alone did not establish destination pixels');
video.listeners.get('seeked')();callback(0,{mediaTime:2.5});
assert.equal(await replacement,true);assert.equal(await first,false);
'''
    result = subprocess.run(['node','--input-type=module','-e',f'const MODULE={json.dumps(module)};\n'+script],
        capture_output=True,text=True,timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr


@pytest.mark.parametrize('speed', [1.25, 1.5])
def test_speed_transport_seeks_and_restarts_at_authored_rate(speed):
    module = (Path(__file__).parents[1]/'src/moviestar/preview_native_videos.mjs').as_uri()
    script = '''
import assert from 'node:assert/strict';
const {createVideoGroup}=await import(MODULE);
const v={currentTime:1,playbackRate:1,paused:true,listeners:new Map(),
 play(){this.paused=false;return Promise.resolve();},pause(){this.paused=true;},
 addEventListener(k,f){this.listeners.set(k,f);},removeEventListener(k){this.listeners.delete(k);},
 removeAttribute(){},load(){}};
const g=createVideoGroup([{video:v,start:1,rate:RATE,fps:30}]);
await g.play();assert.equal(v.playbackRate,RATE);
g.pause();assert.equal(v.playbackRate,RATE);
const seeking=g.seek(1);assert.ok(Math.abs(v.currentTime-(1+RATE+1e-5))<1e-9);
const pending=g.play();g.pause();v.listeners.get('seeked')();await seeking;await pending;
assert.equal(v.paused,true);await g.play();assert.equal(v.playbackRate,RATE);g.dispose();
'''
    result = subprocess.run(['node', '--input-type=module', '-e',
        f'const MODULE={json.dumps(module)},RATE={speed};\n'+script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr


@pytest.mark.parametrize('speed', [1.25, 1.5])
@pytest.mark.parametrize('edited_mix', [False, True])
def test_selected_speed_audio_matches_full_span_export_graph(live_project, tmp_path, speed, edited_mix):
    import numpy as np
    from moviestar.native_audio import prepare_audio
    from moviestar.ffmpeg import _audio_from_filter_parts, build_mix_audio_window_command
    spec, _, root = live_project
    source = root/'camera.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=s=640x360:r=30:d=8',
        '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=8',
        '-c:v','libx264','-c:a','aac','-ac','2',str(source)], check=True, timeout=20)
    paced(spec, speed)
    if edited_mix:
        spec['audio_mix']['source_audio'].update(gain_db=-6, fade_in=.2, fade_out=.2)
        spec['audio_mix']['tracks'] = [{'id':'bed','kind':'music','path':str(source),
            'at':'0:00:00.500','source_from':'0:00:00.000','source_to':'0:00:01.500','gain_db':-9,'fade_in':.1,'fade_out':.1,'muted':False,'loop':False,'ducking':None}]
    save_spec(spec, cwd=root)
    packet, jobs = compile_snapshot(read_snapshot(root, .5, 2))
    job = jobs[packet['audio_plane']['job']]
    actual = prepare_audio(job, tmp_path/'prepared')
    # Export's full resolved span is retimed before the window is selected.
    parts, label = _audio_from_filter_parts(start_index=0, audio_from_input=('',[(1,6)]),
        audio_speed=speed, audio_format='aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo')
    parts.append(f'{label}apad,atrim=duration={5/speed},atrim=start=0.5:duration=1.5,asetpts=PTS-STARTPTS[out]')
    reference = tmp_path/'reference.wav'
    subprocess.run(['ffmpeg','-v','error','-filter_complex_threads','1','-ss','1','-t','5','-i',str(source),
        '-filter_complex',';'.join(parts),'-map','[out]','-c:a','pcm_f32le',str(reference)], check=True, timeout=15)
    if edited_mix:
        mixed = tmp_path/'mixed.wav'
        subprocess.run(build_mix_audio_window_command(str(reference),str(mixed),job['mix'],job['result_duration'],
            source_audio_available=True,window_start=.5,window_duration=1.5),check=True,capture_output=True,timeout=15)
        reference = mixed
    def samples(path):
        return np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-f','f32le','-']),dtype='<f4')
    observed, expected = samples(actual), samples(reference)
    assert len(observed) == len(expected) == 1.5*48000*2
    assert np.max(np.abs(observed-expected)) < 1e-5


@pytest.mark.parametrize('change', ['hold', 'boundary', 'preset', 'two_videos'])
def test_speed_profile_does_not_admit_unqualified_pacing(live_project, change):
    from copy import deepcopy
    spec, _, root = live_project
    paced(spec, 1.5)
    if change == 'hold':
        spec['motion']['scenes'][0]['slots'][0]['pacing'] = [
            {'id':'hold','mode':'hold','at':'0:00:01.000','space':'source-local','duration':'0:00:01.000'}]
    elif change == 'boundary':
        spec['motion']['scenes'][0]['slots'][0]['pacing'][0]['range']['to'] = '0:00:01.000'
    elif change == 'preset':
        spec.pop('spatial')
    else:
        child = deepcopy(spec['spatial']['scenes']['scene']['layout']['children'][0])
        child['instance_id'] = 'second'
        spec['spatial']['scenes']['scene']['layout']['children'].append(child)
    save_spec(spec, cwd=root)
    with pytest.raises(NativePreviewUnsupported):
        compile_snapshot(read_snapshot(root, 0, 2))


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
@pytest.mark.parametrize('speed', [1.25, 1.5])
def test_actual_speed_preview_frames_sound_seek_and_restart(tmp_path, speed):
    repo = Path(__file__).parents[2]; root = tmp_path/'project'
    subprocess.run([sys.executable, str(repo/'experiments/live_browser_preview/fixtures.py'),
        '--out', str(root), '--portrait'], check=True, capture_output=True, timeout=40)
    from moviestar.spec import load_spec
    spec = load_spec(root)
    spec['composition_canvas'] = {'preset': None, 'width': 360, 'height': 640, 'aspect_ratio': '9:16'}
    children = spec['spatial']['scenes']['review']['layout']['children']
    children[:] = [children[0]]
    children[0].pop('motion', None)
    children[0]['layout'] = {'position':'absolute', 'left':0, 'top':0, 'width':360, 'height':640}
    media = spec['spatial']['definitions']['speaker_card']['children'][0]
    media['style'] = {'object_fit':'fill'}
    paced(spec, speed); spec['motion']['scenes'][0]['scene'] = 'Live review'
    save_spec(spec, cwd=root)
    with (tmp_path/'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root),
            '--mode', 'native', '--from', '0', '--to', '2', '--port', '0', '--no-open'],
            stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                assert selector.select(15), 'Native server did not start'
                ready = json.loads(server.stdout.readline()); assert ready.get('url'), ready
            script = r'''
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const {Browser}=await import(HELPER),{assertReviewQuality}=await import(QUALITY_HELPER),{measureCueClock}=await import(CUE_HELPER),b=await Browser.launch(CHROME,OUT);
try{
 await b.send('Page.navigate',{url:URL+'?audit=1&audit_pcm=1'});
 await b.wait('window.movieStarPreview?.is_current',15000);
 const expr='window.movieStarPreview.stage';
 for(const at of [0,.5,1,1.5,0]){
  const snap=await b.evaluate(`(async()=>{const api=${expr};await api.seek(${at});return api.snapshot();})()`);
  assert.ok(Math.abs(snap.time-at)<1e-3);
  assert.equal(snap.source.number,Math.floor((1+at*RATE)*30+1e-5)%240);
 }
 await b.click('#play');await b.wait(expr+'.metrics.finished',6000);
 const m=await b.evaluate(expr+'.metrics');await writeFile(OUT+'/playback.json',JSON.stringify(m));
 assert.equal(m.error,null);assertReviewQuality(m);
 assert.ok(m.frames.length>10);
 const cueTimes=[.2,1.2,2.2].map(t=>t/RATE);
 const clock=measureCueClock({...m,frames:m.frames.map(f=>({...f,result_time:(f.media_time-1)/RATE}))},{fps:30*RATE,count:cueTimes.length,cueTimes});
 assert.deepEqual(clock.problems,[]);await writeFile(OUT+'/cue-clock.json',JSON.stringify(clock));
 for(const f of m.frames){assert.equal(f.number,Math.round((f.captured_media_time??f.media_time)*30)%240,'Captured source pixels match their independent frame timestamp');assert.ok(Math.abs((f.captured_media_time??f.media_time)-f.media_time)<=1/30+1e-6,'Capture stays within one source presentation of callback');}
 const origin=await b.evaluate('document.querySelector("iframe:not(.pending)").contentWindow.performance.timeOrigin');
 const zero=origin+m.frames[0].display_time-(m.frames[0].media_time-1)/RATE*1000;
 assert.ok(Math.abs(m.paused_at-zero-2000)<=50,'Program clock <=50ms');
 // Resume from a result-time seek, retaining the same measured picture clock.
 await b.evaluate(`(async()=>{await ${expr}.seek(.5);})()`);
 await b.click('#play');await b.wait(expr+'.metrics.finished',6000);
 const resumed=await b.evaluate(expr+'.metrics');
 await writeFile(OUT+'/resume-playback.json',JSON.stringify(resumed));assertReviewQuality(resumed);
 const remaining=cueTimes.filter(t=>t>=.5);
 const resumedClock=measureCueClock({...resumed,frames:resumed.frames.map(f=>({...f,result_time:(f.media_time-1)/RATE}))},
  {fps:30*RATE,count:remaining.length,cueTimes:remaining});
 assert.deepEqual(resumedClock.problems,[]);await writeFile(OUT+'/resume-cue-clock.json',JSON.stringify(resumedClock));
 await b.click('#restart');await b.click('#play');await b.click('#pause');
 assert.equal(await b.evaluate(expr+'.paused'),true);
}finally{await b.close();}
'''
            values = {'HELPER':(repo/'experiments/live_browser_preview/browser.mjs').as_uri(),
                'CUE_HELPER':(repo/'experiments/live_browser_preview/cue_clock.mjs').as_uri(),
                'QUALITY_HELPER':(repo/'experiments/live_browser_preview/speed_quality.mjs').as_uri(),
                'CHROME':os.environ.get('MOVIESTAR_CHROME_PATH') or str(_chrome_path()),
                'OUT':str(tmp_path/'browser'), 'URL':ready['url'], 'RATE':speed}
            prefix = ''.join('const '+key+'='+json.dumps(value)+';\n' for key,value in values.items())
            result = subprocess.run(['node','--input-type=module','-e',prefix+script], capture_output=True, text=True, timeout=45)
            assert result.returncode == 0, result.stdout+result.stderr
        finally:
            server.terminate()
            try:server.wait(timeout=9)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            server.stdout.close()


def test_speed_completion_installs_the_canonical_hold_without_a_final_callback():
    from tests.native_stage_harness import stage_script
    script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const makeCanvas=()=>{const canvas={width:0,height:0,style:{},remove(){}};
 canvas.getContext=()=>({drawImage(image){canvas.copied=image;}});return canvas;};
const context={window:{moviestarScene:{entries:[],applyMotion(){}}},document:{createElement:makeCanvas},
 location:{search:''},URLSearchParams,AbortController,performance:{timeOrigin:1000,now:()=>1},Date,
 fetch:()=>new Promise(()=>{}),setTimeout,clearTimeout,console};
vm.createContext(context);vm.runInContext(STAGE,context);
vm.runInContext(`
 const media={currentTime:3.99,videoWidth:640,videoHeight:360,style:{cssText:''},
  parentElement:{append(){}},pause(){},getVideoPlaybackQuality(){return{totalVideoFrames:90,droppedVideoFrames:0};}};
 video=media;videos.push({video:media,path:['main'],primary:true,endFrame:{number:118}});
 packet={playback_rate:1.5,fps:30,source_start:1,source_end:4,samples:Array.from({length:60},()=>({nodes:[]}))};
 playing=true;videoGroup={pause(){}};
`,context);
(async()=>{await vm.runInContext('finishScene()',context);
 assert.equal(vm.runInContext('frozenFrame?.copied.number',context),118,'Completion owns the canonical hold even without the final callback');
 assert.equal(vm.runInContext('lastIndex',context),59,'The final canonical layout/caption pose is applied');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node','-e','const STAGE='+json.dumps(stage_script())+';\n'+script],
        capture_output=True,text=True,timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr


def test_speed_picture_uses_only_the_prepared_pcm_audio_pipeline():
    from tests.native_stage_harness import stage_script
    script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const context={window:{},document:{createElement:()=>({getContext:()=>({})})},location:{search:''},
 URLSearchParams,AbortController,performance:{now:()=>1},Date,fetch:()=>new Promise(()=>{}),setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(STAGE,context);
vm.runInContext(`packet={playback_rate:1.5,prepared_audio:true};shared={destinations:[{}]};
 audio={createGain:()=>({gain:{},connect(){}}),createMediaElementSource(){throw Error('Prepared speed sound must not start a second media-audio pipeline');}};
 videos.push({video:{},primary:true,hasAudio:true});connectAudioGraph();`,context);
assert.equal(vm.runInContext('videos[0].audioGain.gain.value',context),0);
'''
    result = subprocess.run(['node','-e','const STAGE='+json.dumps(stage_script())+';\n'+script],
        capture_output=True,text=True,timeout=10)
    assert result.returncode == 0, result.stdout+result.stderr


def test_prepared_audio_launch_uses_measured_picture_latency_without_skipping_samples():
    module = (Path(__file__).parents[1]/'src/moviestar/preview_native_audio.mjs').as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {createPreparedAudio}=await import(MODULE);let output=2,started;
const context={sampleRate:48000,currentTime:2.04,resume:async()=>{},
 getOutputTimestamp(){output+=.001;return{contextTime:output,performanceTime:1};},
 decodeAudioData:async()=>({numberOfChannels:2,sampleRate:48000,duration:2}),
 createBufferSource:()=>({connect(){},disconnect(){},start(at,offset){started={at,offset};},stop(){}})};
globalThis.fetch=async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(1)});
const api=createPreparedAudio(context,{},new AbortController().signal);
(await api.prepare({asset:'test',duration:2,sample_rate:48000,channels:2})).commit();
await api.play(0,{presentationLead:.08});
assert.equal(started.offset,0,'No authored audio is skipped');
assert.ok(output+.08>=started.at-1/48000,'Dispatch matches measured picture startup');
assert.ok(output+.079<started.at,'Do not substitute a fixed half-frame lead');
api.dispose();
'''
    result=subprocess.run(['node','--input-type=module','-e','const MODULE='+json.dumps(module)+';\n'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


def test_speed_review_quality_rejects_real_drops_and_counter_resets():
    module=(Path(__file__).parents[2]/'experiments/live_browser_preview/speed_quality.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {assertReviewQuality}=await import(MODULE);
const sample=(before,after,delta)=>({stalls:0,initial_presented:[{quality:{droppedVideoFrames:before}}],
 quality:{droppedVideoFrames:after},video_quality:[{dropped_frames:delta}]});
assert.deepEqual(assertReviewQuality(sample(1,1,0)),{raw_before_review:1,raw_after_review:1,playback_drops:0});
assert.throws(()=>assertReviewQuality(sample(0,1,1)),/Zero decoder drops/,'The first Linux failure still fails');
assert.throws(()=>assertReviewQuality(sample(2,3,1)),/Zero decoder drops/,'Preparation cannot hide a later dropped frame');
assert.throws(()=>assertReviewQuality(sample(2,1,0)),/never reset/);
assert.throws(()=>assertReviewQuality(sample(0,0,1)),/Every review decoder/);
'''
    result=subprocess.run(['node','--input-type=module','-e','const MODULE='+json.dumps(module)+';\n'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr



@pytest.mark.parametrize('action', ['complete', 'pause', 'dispose'])
def test_picture_startup_measurement_is_owned_and_returns_to_first_pixels(action):
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_videos.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {createVideoGroup}=await import(MODULE);
Object.defineProperty(globalThis,'performance',{value:{timeOrigin:1000,now:()=>100}});
const callbacks=new Map(),listeners=new Map();let next=0,frames=0;
const video={currentTime:1,paused:true,playbackRate:1.5,seeking:false,
 getVideoPlaybackQuality:()=>({totalVideoFrames:frames,droppedVideoFrames:0}),
 play(){this.paused=false;return Promise.resolve();},pause(){this.paused=true;},
 requestVideoFrameCallback(fn){callbacks.set(++next,fn);return next;},cancelVideoFrameCallback(id){callbacks.delete(id);},
 addEventListener(k,fn){listeners.set(k,fn);},removeEventListener(k){listeners.delete(k);},removeAttribute(){},load(){}};
const group=createVideoGroup([{video,start:1,rate:1.5,fps:30}]);
let settled=false;const measured=group.measureStartup().then(value=>{settled=true;return value;});
await new Promise(r=>setImmediate(r));
if(ACTION!=='complete'){
 group[ACTION]();assert.equal(await measured,null);assert.equal(video.paused,true);assert.equal(callbacks.size,0);
}else{
 const emit=point=>{const [id,fn]=callbacks.entries().next().value;callbacks.delete(id);frames++;fn(0,point);};
 video.currentTime=1+1/30;
 emit({mediaTime:1+1/30,expectedDisplayTime:100+70+1000/45});
 await new Promise(r=>setImmediate(r));assert.equal(settled,false,'Reset to authored first pixels before readiness');
 listeners.get('seeked')();emit({mediaTime:1,expectedDisplayTime:250});
 const point=await measured;assert.ok(Math.abs(point.lead-.07)<1e-9,'Measured from presentation and source/result slope');
 assert.equal(point.preparation_frames,1);assert.equal(point.preparation_drops,0);
 assert.ok(Math.abs(video.currentTime-1)<.0001);assert.equal(video.paused,true);assert.equal(callbacks.size,0);
 group.dispose();
}
'''
    result=subprocess.run(['node','--input-type=module','-e','const MODULE='+json.dumps(module)+';const ACTION='+json.dumps(action)+';\n'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


def test_speed_dom_uses_one_result_clock_instead_of_alternating_video_callbacks():
    from tests.native_stage_harness import stage_script
    script=r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const context={window:{moviestarScene:{entries:[],applyMotion(){}}},document:{createElement:()=>({getContext:()=>({})})},
 location:{search:''},URLSearchParams,AbortController,performance:{timeOrigin:1000,now:()=>100},Date,
 fetch:()=>new Promise(()=>{}),setTimeout:()=>1,clearTimeout(){},requestAnimationFrame:()=>1};
vm.createContext(context);vm.runInContext(STAGE,context);
vm.runInContext(`packet={playback_rate:1.25,fps:30,source_start:1,source_end:3.5,samples:Array.from({length:60},()=>({nodes:[]}))};
 video={currentTime:2.25,paused:false,requestVideoFrameCallback:()=>1};playing=true;
 reviewAudio={outputTimeAt:()=>.5,snapshot:()=>({started_offset:0})};animateSpeed();`,context);
assert.equal(vm.runInContext('lastIndex',context),15,'Caption and camera poses follow the audible result clock');
vm.runInContext('onFrame(100,{mediaTime:1.2,expectedDisplayTime:100,presentedFrames:1})',context);
assert.equal(vm.runInContext('lastIndex',context),15,'A source callback cannot rewind the result pose');
vm.runInContext('reviewAudio={outputTimeAt:()=>.9,snapshot:()=>({started_offset:1})};animateSpeed()',context);
assert.equal(vm.runInContext('lastIndex',context),30,'A seek position holds until its audio start reaches output');
'''
    result=subprocess.run(['node','-e','const STAGE='+json.dumps(stage_script())+';\n'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
