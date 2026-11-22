"""A speaker and reusable ListenerPair retain three independent source clocks."""
from copy import deepcopy

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.native_preview import compile_snapshot, NativePreviewUnsupported
from moviestar.project import save_project
from moviestar.spec import save_spec
from tests.test_live_browser_preview import live_project


@pytest.fixture
def interview_project(live_project):
    spec, project, root = live_project
    for identity in ('bob', 'casey'):
        path = root / f'{identity}.mp4'; path.write_bytes(b'packet fixture only')
        spec['sources'].append({'id': identity, 'path': str(path), 'operations': []})
        meta = deepcopy(project['sources'][0]); meta.update(id=identity, path=str(path))
        project['sources'].append(meta)
    spec['spatial']['definitions']['listeners'] = {
        'name': 'ListenerPair',
        'inputs': [{'name': name, 'type': 'video', 'required': True} for name in ('left', 'right')],
        'children': [{'instance_id': name, 'definition_id': 'card',
            'bindings': {'clip': {'forward': name}},
            'layout': {'position': 'absolute', 'left': index * 90, 'top': 0, 'width': 80, 'height': 220}}
            for index, name in enumerate(('left', 'right'))]}
    children = spec['spatial']['scenes']['scene']['layout']['children']
    children[0].pop('motion', None)
    children.append({'instance_id': 'listeners', 'definition_id': 'listeners',
        'layout': {'position': 'absolute', 'left': 440, 'top': 20, 'width': 180, 'height': 220},
        'bindings': {'left': {'asset_id': 'bob', 'source': {'start': 2, 'end': 7}},
                     'right': {'asset_id': 'casey', 'source': {'start': 3, 'end': 8}}}})
    save_spec(spec, cwd=root); save_project(project, cwd=root)
    return spec, project, root


@pytest.mark.parametrize('speaker,origin,path', [
    ('camera', 1, ['scene', 'card', 'video']),
    ('bob', 2, ['scene', 'listeners', 'left', 'video']),
    ('casey', 3, ['scene', 'listeners', 'right', 'video']),
])
def test_three_person_packet_selects_any_visible_speaker_without_flattening(interview_project, speaker, origin, path):
    spec, _, root = interview_project
    spec['composition'][0]['audio_from'] = speaker
    spec['composition'][0]['slots'][0].update(source=speaker,
        source_from=f'0:00:0{origin}.000', source_to=f'0:00:0{origin + 5}.000')
    save_spec(spec, cwd=root)
    before = (root / 'moviestar/spec.json').read_bytes()
    packet, assets = compile_snapshot(read_snapshot(root, 1, 4))
    videos = [m for m in packet['media'] if m['kind'] == 'video']
    assert len(videos) == 3 and len(assets) == 4
    assert [(m['source_start'], m['source_end']) for m in videos] == [(2, 5), (3, 6), (4, 7)]
    assert packet['prepared_audio'] is True
    # The real embedded audio pipeline of the routed speaker supplies the
    # picture clock, even if an earlier listener in DOM order is silent.
    assert packet['video_path'] == path
    assert (packet['source_start'], packet['source_end']) == (origin + 1, origin + 4)
    job = assets[packet['audio_plane']['job']]
    assert [(p['source_id'], p['source_start']) for p in job['source_pieces']] == [(speaker, origin + 1)]
    assert path in [m['path'] for m in videos]
    assert [m['path'] for m in videos] == [
        ['scene', 'card', 'video'], ['scene', 'listeners', 'left', 'video'],
        ['scene', 'listeners', 'right', 'video']]
    assert (root / 'moviestar/spec.json').read_bytes() == before


def test_four_visible_videos_remain_an_explicit_capability_error(interview_project):
    spec, _, root = interview_project
    child = deepcopy(spec['spatial']['scenes']['scene']['layout']['children'][0])
    child['instance_id'] = 'fourth'
    spec['spatial']['scenes']['scene']['layout']['children'].append(child)
    save_spec(spec, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='three'):
        compile_snapshot(read_snapshot(root, 0, 5))


def test_three_person_scene_allows_listeners_without_embedded_audio(interview_project):
    spec, project, root = interview_project
    for source in project['sources']:
        if source['id'] in ('bob', 'casey'):
            source['audio_codec'] = None
    save_project(project, cwd=root)
    packet, _ = compile_snapshot(read_snapshot(root, 0, 5))
    assert [m['has_audio'] for m in packet['media']] == [True, False, False]
    assert packet['prepared_audio'] is True


@pytest.mark.parametrize('mixed', [False, True])
def test_silent_first_picture_cannot_become_interview_master_clock(interview_project, mixed):
    spec, project, root = interview_project
    project['sources'][0]['audio_codec'] = None
    spec['composition'][0]['audio_from'] = 'bob'
    spec['composition'][0]['slots'][0].update(source='bob', source_from='0:00:02.000', source_to='0:00:07.000')
    if mixed:
        spec['audio_mix']['source_audio']['gain_db'] = -6
    save_spec(spec, cwd=root); save_project(project, cwd=root)
    packet, _ = compile_snapshot(read_snapshot(root, 0, 5))
    assert packet['video_path'] == ['scene', 'listeners', 'left', 'video']
    assert packet['source_start'] == 2
    assert packet['media'][0]['has_audio'] is False


def test_three_silent_pictures_require_rendered_review_with_a_prepared_mix(interview_project):
    spec, project, root = interview_project
    for source in project['sources']:
        source['audio_codec'] = None
    spec['audio_mix']['source_audio']['muted'] = True
    save_spec(spec, cwd=root); save_project(project, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='embedded audio for the output clock'):
        compile_snapshot(read_snapshot(root, 0, 5))


def test_three_person_audio_must_match_a_visible_picture_clock(interview_project):
    spec, _, root = interview_project
    spec['composition'][0]['audio_from'] = 'bob'
    # The visible Bob starts at two seconds; this carrier starts at one.
    spec['composition'][0]['slots'][0].update(source='bob')
    save_spec(spec, cwd=root)
    before = (root / 'moviestar/spec.json').read_bytes()
    with pytest.raises(NativePreviewUnsupported, match='audio and video.*source clock'):
        compile_snapshot(read_snapshot(root, 0, 5))
    assert (root / 'moviestar/spec.json').read_bytes() == before


@pytest.mark.parametrize('scenes', [2, 3])
def test_three_picture_cut_windows_require_explicit_rendered_fallback(interview_project, scenes):
    spec, _, root = interview_project
    original = deepcopy(spec['composition'][0]); tree = deepcopy(spec['spatial']['scenes']['scene'])
    spec['composition'] = []; spec['spatial']['scenes'] = {}
    for index in range(scenes):
        identity = f'part_{index}'
        part = deepcopy(original); part.update(id=identity, name=identity)
        part['slots'][0].update(id=f'slot_{index}', source_to='0:00:03.000')
        spec['composition'].append(part); spec['spatial']['scenes'][identity] = deepcopy(tree)
    save_spec(spec, cwd=root)
    with pytest.raises(NativePreviewUnsupported, match='three-picture.*single scene'):
        compile_snapshot(read_snapshot(root, 0, 2*scenes))
    packet, _ = compile_snapshot(read_snapshot(root, 0, 2))
    assert len(packet['media']) == 3, 'Review each scene independently without flattening'


@pytest.mark.parametrize('action', ['finish', 'pause', 'seek', 'dispose'])
def test_static_interview_completion_is_owned_by_current_play(action):
    import json
    import subprocess
    from tests.native_stage_harness import stage_script
    script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
let clock=10,next=0;const timers=new Map();
const media=()=>({currentTime:1,paused:true,pause(){},getVideoPlaybackQuality:()=>({totalVideoFrames:0,droppedVideoFrames:0})});
const pictures=Array.from({length:3},(_,i)=>({video:media(),path:[String(i)],primary:i===0}));
const group={pause(){},seek:async()=>true,play(){throw Error('Static review must not advance decoders');},dispose(){}};
const audio={sampleRate:48000,state:'running',get currentTime(){return clock;},resume:async()=>{},close:async()=>{},getOutputTimestamp:()=>({contextTime:clock,performanceTime:1})};
let audible=false;const review={pause(){audible=false;},seek(){},dispose(){},async play(){audible=true;},snapshot:()=>({prepared:true,playing:audible,started_context_time:10})};
const context={window:{moviestarScene:{entries:[],applyMotion(){}}},document:{createElement:()=>({getContext:()=>({})})},
 location:{search:''},URLSearchParams,AbortController,performance:{timeOrigin:1000,now:()=>100},Date,
 fetch:()=>new Promise(()=>{}),requestAnimationFrame:fn=>{fn();return 1;},
 setTimeout(fn,ms){timers.set(++next,{fn,due:clock+ms/1000});return next;},clearTimeout:id=>timers.delete(id)};
vm.createContext(context);vm.runInContext(STAGE,context);Object.assign(context,{pictures,group,audio,review});
vm.runInContext(`packet={fps:30,range:{from:0,to:1/30},source_start:1,source_end:1+1/30,samples:[{nodes:[]}]};
 video=pictures[0].video;videos.push(...pictures);videoGroup=group;reviewAudio=review;pictureStartup={prepared_frame_only:true,lead:0};resolveReady(true);`,context);
// Assign the module's lexical audio separately from the fixture's global name.
context.audioFixture=audio;vm.runInContext('audio=audioFixture;',context);
const api=context.window.moviestarLiveStage,flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
 await api.play();assert.equal(api.paused,false);assert.equal(audible,true);assert.equal(timers.size,1);
 const stale=[...timers.values()][0].fn;
 if(ACTION==='finish'){
  clock+=1/30;for(const[id,t]of [...timers])if(t.due<=clock+1e-8){timers.delete(id);t.fn();}
  await flush();assert.equal(api.metrics.finished,true);assert.equal(api.paused,true);assert.equal(audible,false);
 }else{
  if(ACTION==='seek')await api.seek(0);else api[ACTION]();
  assert.equal(timers.size,0);clock+=1;stale();await flush();
  assert.equal(api.metrics.finished,false,'A stale timer cannot finish paused, sought or disposed playback');
  assert.equal(api.paused,true);assert.equal(audible,false);
 }
 if(ACTION!=='dispose')api.dispose();
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', 'const STAGE='+json.dumps(stage_script())+';const ACTION='+json.dumps(action)+';'+script],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('silent_listener', [False, True])
def test_moving_interview_joins_output_clock_on_play_without_doubling_pcm(silent_listener):
    import json
    import subprocess
    from tests.native_stage_harness import stage_script
    script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const sources=[],gains=[];
const audio={sampleRate:48000,currentTime:10,state:'running',resume:async()=>{},
 getOutputTimestamp:()=>({contextTime:10,performanceTime:1}),
 createGain(){const node={gain:{value:1},connect(){}};gains.push(node);return node;},
 createMediaElementSource(video){assert.equal(video.paused,true,'Join while paused');
  assert.equal(video.muted,false,'A muted element cannot supply the audio clock');
  const node={video,connect(gain){assert.equal(gain.gain.value,0,'Only prepared PCM may be audible');}};
  sources.push(node);return node;}};
const pictures=Array.from({length:3},(_,i)=>({path:[String(i)],primary:i===0,hasAudio:i!==2||!SILENT,
 video:{muted:true,paused:true,currentTime:i+1,playbackRate:1,pause(){this.paused=true;},
  getVideoPlaybackQuality:()=>({totalVideoFrames:0,droppedVideoFrames:0})}}));
const review={pause(){},snapshot:()=>({prepared:true}),async play(at,options){
 assert.equal(options.presentationLead,undefined,'Muted preparation cannot calibrate the device-clock pipeline');
 assert.equal(options.pictureLead,1/60,'Use the established half-frame audio-clock launch');}};
const group={pause(){pictures.forEach(p=>p.video.pause());},async play(){
 assert.equal(sources.length,SILENT?2:3,'Real audio streams share the output clock before motion');
 pictures.forEach(p=>p.video.paused=false);return true;}};
const context={window:{moviestarScene:{entries:[],applyMotion(){}}},document:{createElement:()=>({getContext:()=>({})})},
 location:{search:'?request=unit'},URLSearchParams,AbortController,performance:{now:()=>1},Date,
 fetch:()=>new Promise(()=>{}),setTimeout,clearTimeout,requestAnimationFrame:()=>1,console,
 pictures,testAudio:audio,review,group};
vm.createContext(context);vm.runInContext(STAGE,context);
vm.runInContext(`packet={fps:30,source_start:1,prepared_audio:true,media:Array(3).fill({kind:'video'}),samples:Array(90).fill({nodes:[]})};
 audio=testAudio;outputMixer={node:{}};video=pictures[0].video;videos.push(...pictures);
 videoGroup=group;reviewAudio=review;pictureStartup={lead:.032};connectAudioGraph();resolveReady(true);`,context);
assert.equal(sources.length,0,'Readiness must not wait on an autoplay-blocked AudioContext');
(async()=>{const api=context.window.moviestarLiveStage;
 await api.play();api.pause();await api.play();api.pause();
 assert.equal(sources.length,SILENT?2:3,'Restart reuses each MediaElementAudioSourceNode');
 assert.ok(gains.every(g=>g.gain.value===0),'Embedded tracks never double the canonical PCM');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', 'const STAGE='+json.dumps(stage_script())+';const SILENT='+json.dumps(silent_listener)+';'+script],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
