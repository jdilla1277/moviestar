"""Installed CLI caption edits must become fresh, visible and playable."""
import json
from pathlib import Path
import subprocess
import sys
import selectors

import pytest

from moviestar.spatial_layout import browser_available, _chrome_path


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
def test_authored_captions_and_individual_undos_in_the_actual_browser(tmp_path):
    repo = Path(__file__).parents[2]
    out = tmp_path/'evidence'
    result = subprocess.run([sys.executable, str(repo/'experiments/live_browser_preview/caption_benchmark.py'),
        '--history', '0', '--out', str(out)], capture_output=True, text=True, timeout=180)
    report_path = out/'history-0/browser-report.json'
    assert result.returncode == 0, (result.stdout, result.stderr,
        report_path.read_text() if report_path.exists() else 'No browser report')
    report = json.loads(report_path.read_text())
    assert len(report['edits']) == 8
    assert report['restored']['same_state'] and report['restored']['fresh_request']
    assert not report['problems'] and not report['browser_exceptions']


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node required')
def test_caption_reuse_fences_superseded_updates_and_replays_after_edit(tmp_path):
    repo = Path(__file__).parents[2]; root = tmp_path/'project'
    helper = repo/'experiments/live_browser_preview'
    subprocess.run([sys.executable, '-c',
        'import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from caption_benchmark import prepare;prepare(Path(sys.argv[2]))',
        str(helper), str(root)], check=True, capture_output=True, timeout=40)
    with (tmp_path/'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, '-m', 'moviestar.cli', 'preview', '--workspace', str(root),
            '--mode', 'native', '--from', '0', '--to', '5', '--port', '0', '--no-open'],
            stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ); assert selector.select(15)
                ready = json.loads(server.stdout.readline()); assert ready.get('url'), ready
            script = r'''
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
const {Browser}=await import(HELPER),b=await Browser.launch(CHROME,OUT);
const cli=args=>{const p=spawnSync(PYTHON,['-m','moviestar.cli',...args],{cwd:ROOT,encoding:'utf8'});assert.equal(p.status,0,p.stdout+p.stderr);};
try{
 const exceptions=[];b.socket.addEventListener('message',e=>{if(JSON.parse(e.data).method==='Runtime.exceptionThrown')exceptions.push(e.data);});
 await b.send('Page.navigate',{url:URL+'?audit=1'});await b.wait('window.moviestarLive?.is_current',20000);
 await b.evaluate(`(async()=>{await window.moviestarLive.stage.seek(.2);window.savedCaptionFrame=document.querySelector('iframe:not(.pending)');
 const api=window.moviestarLive.stage,prepare=api.prepareCaptionUpdate.bind(api);window.holdCaptionUpdate=true;
 api.prepareCaptionUpdate=async(...args)=>{const tx=await prepare(...args);if(window.holdCaptionUpdate){window.captionUpdatePrepared=true;await new Promise(r=>window.releaseCaptionUpdate=r);}return tx;};})()`);
 const original=await b.evaluate('window.moviestarLive.current.request_id');
 cli(['captions','placement','--size','32px']);await b.wait('window.captionUpdatePrepared',10000);
 const obsolete=await b.evaluate('window.moviestarLive.pending.request_id');
 assert.equal(await b.evaluate('window.moviestarLive.current.request_id'),original,'Prepared images do not change the displayed revision');
 assert.equal(await b.evaluate('window.moviestarLive.stage.snapshot().captions[0].font_size'),24);
 await b.evaluate('window.holdCaptionUpdate=false');cli(['captions','placement','--size','36px']);
 await b.wait('window.moviestarLive.is_current&&window.moviestarLive.stage.snapshot().captions[0].font_size===36',15000);
 await b.evaluate('(async()=>{window.releaseCaptionUpdate();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})()');
 assert.equal(await b.evaluate('document.querySelector("iframe:not(.pending)")===window.savedCaptionFrame'),true);
 assert.notEqual(await b.evaluate('window.moviestarLive.current.request_id'),obsolete);
 assert.equal(await b.evaluate(`window.moviestarLive.events.some(e=>e.request_id===${JSON.stringify(obsolete)})`),false,'Superseded captions never become current');
 await b.evaluate('window.moviestarLive.stage.seek(0)');await b.click('#play');
 await b.wait('window.moviestarLive.stage.metrics.finished||window.moviestarLive.stage.error',12000);
 cli(['captions','placement','--size','40px']);
 await b.wait('window.moviestarLive.is_current&&window.moviestarLive.stage.snapshot().captions[0]?.font_size===40',15000);
 assert.equal(await b.evaluate('document.querySelector("iframe:not(.pending)")===window.savedCaptionFrame'),true);
 assert.equal(await b.evaluate('window.moviestarLive.stage.metrics.frames.length'),0,'New revision cannot inherit old playback evidence');
 assert.ok(await b.evaluate('window.moviestarLive.stage.time<.001&&!window.moviestarLive.stage.metrics.finished'));
 await b.click('#play');await b.wait('window.moviestarLive.stage.metrics.finished||window.moviestarLive.stage.error',12000);
 const metrics=await b.evaluate('window.moviestarLive.stage.metrics');assert.equal(metrics.error,null);assert.equal(metrics.stalls,0);
 assert.ok(metrics.video_quality.every(q=>q.dropped_frames===0));assert.equal(metrics.beeps.length,5);
 const prior=await b.evaluate('window.moviestarLive.current.request_id');cli(['scenes','geometry','Review:inset','--at','bottom-right']);
 await b.wait(`window.moviestarLive.is_current&&window.moviestarLive.current.request_id!==${JSON.stringify(prior)}`,15000);
 assert.equal(await b.evaluate('document.querySelector("iframe:not(.pending)")===window.savedCaptionFrame'),false,'Geometry changes must rebuild the canonical player');
 assert.deepEqual(exceptions,[]);
}finally{await b.close();}
'''
            for key, value in {'HELPER': (helper/'browser.mjs').as_uri(), 'CHROME': str(_chrome_path()),
                               'OUT': str(tmp_path/'browser'), 'PYTHON': sys.executable, 'ROOT': str(root), 'URL': ready['url']}.items():
                script = script.replace(key, json.dumps(value))
            result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=120)
            assert result.returncode == 0, result.stdout+result.stderr
        finally:
            server.terminate()
            try: server.wait(timeout=9)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            server.stdout.close()
