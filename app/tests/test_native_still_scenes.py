"""Image/text scenes use their declared timeline, with no hidden video carrier."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from moviestar.local_preview import read_snapshot
from moviestar.native_preview import compile_snapshot
from moviestar.spec import save_spec
from tests.test_live_browser_preview import live_project
from tests.test_native_edit_benchmark import benchmark


def make_still(spec):
    spec['composition'][0].update(slots=[], duration=5, audio_from=None)
    spec['spatial']['scenes']['scene']['layout']['children'].pop(0)
    spec['spatial']['scenes']['scene']['layout']['children'][0]['motion'] = {
        'translate_x': [{'at': 0, 'value': 0}, {'at': 4, 'value': 80}]}
    return spec


def test_silent_title_scene_compiles_without_decode_or_fake_video(live_project, monkeypatch):
    spec, _, root = live_project
    make_still(spec); save_spec(spec, cwd=root)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No decode')))
    packet, assets = compile_snapshot(read_snapshot(root, 0, 5))
    assert packet['clock'] == 'scene'
    assert packet['video_path'] is None
    assert packet['source_start'] == 0 and packet['source_end'] == 5
    assert packet['media'] == [] and assets == {}
    assert 'audio_plane' not in packet
    assert packet['samples'][60]['nodes'][0]['motion']['translate_x'] == '40px'


def test_image_background_registers_image_only(live_project, tmp_path):
    spec, _, root = live_project; make_still(spec)
    image = root / 'background.png'; image.write_bytes(b'Image bytes are decoded only in browser tests')
    spec['spatial']['assets'] = {'background': {'type': 'image', 'path': str(image)}}
    spec['spatial']['scenes']['scene']['layout']['children'].insert(0, {
        'id': 'background', 'type': 'image', 'content': {'asset_id': 'background'},
        'layout': {'position': 'absolute', 'left': 0, 'top': 0, 'width': '100%', 'height': '100%'},
        'style': {'object_fit': 'cover'}})
    save_spec(spec, cwd=root)
    packet, assets = compile_snapshot(read_snapshot(root, 0, 5))
    assert packet['clock'] == 'scene' and [m['kind'] for m in packet['media']] == ['image']
    assert [a['kind'] for a in assets.values()] == ['image']


def test_scene_clock_controls_and_cancellation():
    module = (Path(__file__).parents[1] / 'src/moviestar/preview_native_scene_clock.mjs').as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {createSceneClock}=await import(MODULE);
let now=0,callback=null,canceled=0,resolveResume,resume=Promise.resolve(),painted=[],presented=[],ended=0;
const metrics={},packet={fps:30,range:{from:30,to:35},samples:Array(150)};
const clock=createSceneClock({packet,metrics,now:()=>now,
 request:fn=>{callback=fn;return 1;},cancel:()=>{callback=null;canceled++;},
 resume:()=>resume,apply:t=>painted.push(t),present:f=>presented.push(f),end:()=>ended++});
await clock.seek(2);assert.equal(clock.time,2);assert.equal(clock.paused,true);
resume=new Promise(r=>resolveResume=r);const late=clock.play();clock.pause();resolveResume();await late;
assert.equal(callback,null,'Pause cancels asynchronous startup');
resume=Promise.resolve();await clock.play();now=500;callback(now);
assert.equal(clock.time,2.5);assert.equal(painted.at(-1),2.5);
clock.pause();now=1000;assert.equal(clock.time,2.5);
await clock.play();now=3500;callback(now);await new Promise(r=>setImmediate(r));
assert.equal(metrics.finished,true);assert.equal(clock.time,5);assert.equal(ended,1);
assert.equal(painted.at(-1),149/30,'Hold the final canonical sample');
await clock.play();assert.equal(clock.time,0,'Restart a finished scene');
await clock.seek(1.11);assert.equal(clock.time,33/30);
await clock.seek(4.999);await clock.play();assert.equal(clock.time,0,'Play at the final sample restarts a standalone card');
clock.dispose();assert.equal(callback,null);await clock.play();assert.equal(clock.paused,true);
assert.ok(presented.every(f=>f.clock==='scene'));
'''
    result = subprocess.run(['node', '--input-type=module', '-e', 'const MODULE='+json.dumps(module)+';'+script],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_scene_clock_uses_prepared_output_clock_and_reports_loss():
    module = (Path(__file__).parents[1] / 'src/moviestar/preview_native_scene_clock.mjs').as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {createSceneClock}=await import(MODULE);
let at=0,callback,errors=[];
const metrics={},audio={snapshot:()=>({prepared:true,time:at}),outputTimeAt:()=>at,
 async play(){},pause(){},seek(t){at=t;}};
const clock=createSceneClock({packet:{fps:30,range:{from:0,to:5},samples:Array(150)},metrics,audio,
 apply(){},onError:error=>errors.push(String(error)),request:fn=>{callback=fn;return 1;},cancel(){callback=null;}});
await clock.play();at=1.5;callback();assert.equal(clock.time,1.5);
audio.outputTimeAt=()=>null;callback();
assert.equal(clock.paused,true);assert.match(metrics.error,/clock/i);assert.equal(errors.length,1);
'''
    result = subprocess.run(['node', '--input-type=module', '-e', 'const MODULE='+json.dumps(module)+';'+script],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mixed_title_and_footage_use_one_prepared_audio_timeline(live_project):
    spec, _, root = live_project
    footage = deepcopy(spec['composition'][0]); footage['id'] = 'footage'
    footage['slots'][0]['source_to'] = '0:00:03.000'
    tree = deepcopy(spec['spatial']['scenes']['scene'])
    make_still(spec); spec['composition'][0]['duration'] = 1
    spec['composition'].append(footage); spec['spatial']['scenes']['footage'] = tree
    save_spec(spec, cwd=root)
    packet, assets = compile_snapshot(read_snapshot(root, 0, 3))
    assert packet['segments'][0]['packet']['clock'] == 'scene'
    assert packet['audio_plane']['source_ranges'] == [
        {'source_id': None, 'source_start': 0, 'duration': 1, 'result_from': 0},
        {'source_id': 'camera', 'source_start': 1, 'duration': 2, 'result_from': 1}]
    assert all(s['packet']['prepared_audio'] for s in packet['segments'])
    assert any(a['kind'] == 'audio_job' for a in assets.values())


def test_still_motion_plays_on_declared_scene_clock(tmp_path, benchmark):
    from moviestar.spatial_layout import browser_available, _chrome_path
    if not browser_available():
        import pytest
        pytest.skip('Chrome and Node required')
    import selectors
    import sys
    from still_benchmark import prepare
    root = tmp_path / 'project'; prepare(root, (360, 640))
    spec = __import__('moviestar.spec', fromlist=['load_spec']).load_spec(root)
    spec['composition'] = [spec['composition'][0]]
    spec['composition'][0]['duration'] = 5
    spec['spatial']['scenes'] = {'title': spec['spatial']['scenes']['title']}
    panel = spec['spatial']['scenes']['title']['layout']['children'][0]
    panel['motion'] = {'translate_x': [{'at': 0, 'value': 0}, {'at': 2, 'value': 40}]}
    save_spec(spec, cwd=root)
    with (tmp_path / 'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root),
            '--mode', 'native', '--from', '0', '--to', '5', '--port', '0', '--no-open'],
            stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                assert selector.select(20)
                ready = json.loads(server.stdout.readline())
            module = (Path(__file__).parents[2] / 'experiments/live_browser_preview/browser.mjs').as_uri()
            script = r'''
import assert from 'node:assert/strict';
const {Browser}=await import(MODULE),b=await Browser.launch(CHROME,OUT);
try{
 await b.send('Page.navigate',{url:URL});await b.wait('window.moviestarLive?.is_current',20000);
 const snapshot=()=>b.evaluate('window.moviestarLive.stage.snapshot()');
 const initial=await snapshot();assert.equal(initial.source,null);assert.deepEqual(initial.videos,[]);
 assert.equal(await b.evaluate('document.querySelector("iframe:not(.pending)").contentDocument.querySelectorAll("video").length'),0);
 await b.evaluate('window.moviestarLive.stage.seek(1)');let state=await snapshot();
 const box=s=>s.boxes.find(b=>JSON.stringify(b.path)===JSON.stringify(['title','panel']));
 assert.ok(Math.abs(box(state).rect.x-20)<.5,'Seek applies component motion to still images and text');
 await b.evaluate('window.moviestarLive.stage.seek(0)');await b.click('#play');
 await b.wait('window.moviestarLive.stage.time>=1.05',3000);await b.click('#pause');state=await snapshot();
 assert.ok(Math.abs(box(state).rect.x-Math.floor(state.time*30)/30*20)<1.5,'Playback advances canonical motion');
 const time=state.time;await b.evaluate('new Promise(r=>setTimeout(r,100))');assert.equal((await snapshot()).time,time);
 await b.click('#restart');assert.equal((await snapshot()).time,0);
 const prior=await b.evaluate('window.moviestarLive.current.request_id'),started=performance.now();
 const {spawnSync}=await import('node:child_process');
 const edited=spawnSync(PYTHON,['-m','moviestar.cli','audio','add',SOURCE,'--as','soundtrack','--kind','music','--from','1','--to','6'],{cwd:ROOT,encoding:'utf8'});
 assert.equal(edited.status,0,edited.stdout+edited.stderr);
 await b.wait('window.moviestarLive.is_current&&window.moviestarLive.current.request_id!=='+JSON.stringify(prior),10000);
 assert.ok(performance.now()-started<3000,'Adding audio to a silent card meets the feedback budget');
 await b.click('#play');await b.wait('window.moviestarLive.stage.time>.1||window.moviestarLive.stage.error',3000);
 assert.equal(await b.evaluate('window.moviestarLive.stage.error'),null,'A silent card can acquire audio without a missing output graph');
 assert.equal((await snapshot()).audio.playing,true);assert.deepEqual((await snapshot()).videos,[]);
 await b.click('#pause');

}finally{await b.close();}
'''
            constants = {'MODULE': module, 'CHROME': str(_chrome_path()), 'OUT': str(tmp_path / 'browser'), 'URL': ready['url'], 'PYTHON': sys.executable, 'ROOT': str(root), 'SOURCE': str(root / 'cues.mp4')}
            script = ''.join('const '+k+'='+json.dumps(v)+';' for k,v in constants.items()) + script
            result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=45)
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            server.terminate()
            try: server.wait(timeout=9)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            server.stdout.close()
