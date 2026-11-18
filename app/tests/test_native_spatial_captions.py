"""Later caption glyph failures cannot replace the current playable revision."""
import json
from pathlib import Path
import selectors
import subprocess
import sys

import pytest
from moviestar.spec import load_spec
from moviestar.spatial_layout import browser_available, _chrome_path
from tests.test_native_edit_benchmark import benchmark


@pytest.mark.parametrize('initial_scale',[None,.01,.001])
def test_later_cue_overflow_keeps_previous_preview(tmp_path,benchmark,initial_scale):
    if not browser_available():pytest.skip('Chrome and Node required')
    from spatial_caption_benchmark import prepare
    root=tmp_path/'project';prepare(root,(360,640),'still')
    model=load_spec(root)
    # Initial cue fits; only the last cue overflows. All-cue validation must run
    # before the new stage is published, even if the viewer is paused at zero.
    model['overlays'][-1]['text']='A very long caption\n'*30
    model['overlays'][-1].pop('tokens');model['overlays'][-1].pop('highlight')
    if initial_scale is not None:
        for cue in model['overlays']:
            cue['text']='A very long caption\n'*4
            cue.pop('tokens',None);cue.pop('highlight',None)
        group=model['spatial']['scenes']['title']['layout']['children'][-1]
        group['motion']={'scale':[{'at':0,'value':initial_scale},{'at':1,'value':1}]}
    bad=tmp_path/'bad.json';bad.write_text(json.dumps(model))
    with (tmp_path/'server.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-m','moviestar.cli','preview','--workspace',str(root),
            '--mode','native','--from','0','--to','5','--port','0','--no-open'],
            stdout=subprocess.PIPE,stderr=log,text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout,selectors.EVENT_READ)
                assert selector.select(20),'Server did not start'
                ready=json.loads(server.stdout.readline())
            constants={'MODULE':(Path(__file__).parents[2]/'experiments/live_browser_preview/browser.mjs').as_uri(),
                'CHROME':str(_chrome_path()),'OUT':str(tmp_path/'browser'),'URL':ready['url'],
                'PYTHON':sys.executable,'ROOT':str(root),'BAD':str(bad)}
            script=r'''
import assert from 'node:assert/strict';
const {Browser}=await import(MODULE),b=await Browser.launch(CHROME,OUT);
try{
 await b.send('Page.navigate',{url:URL});await b.wait('window.moviestarLive?.is_current',20000);
 const prior=await b.evaluate('window.moviestarLive.current.request_id');
 const {spawnSync}=await import('node:child_process');
 const run=args=>{const r=spawnSync(PYTHON,['-m','moviestar.cli',...args],{cwd:ROOT,encoding:'utf8'});assert.equal(r.status,0,r.stdout+r.stderr);};
 run(['spec','--edit',BAD]);
 await b.wait('document.getElementById("error").textContent.includes("caption_overflow")',15000);
 assert.equal(await b.evaluate('window.moviestarLive.current.request_id'),prior);
 assert.equal(await b.evaluate('window.moviestarLive.is_current'),false);
 assert.match(await b.evaluate('document.getElementById("error").textContent'),/title\/captions\/words\/text/);
 await b.evaluate('window.moviestarLive.stage.seek(.2)');
 assert.equal(await b.evaluate('window.moviestarLive.stage.snapshot().boxes.find(b=>b.kind==="caption").text'),'First second');
 run(['undo']);await b.wait('window.moviestarLive.is_current&&window.moviestarLive.current.request_id!=='+JSON.stringify(prior),15000);
 assert.equal(await b.evaluate('document.getElementById("error").textContent'),'');
}finally{await b.close();}
'''
            script=''.join('const '+k+'='+json.dumps(v)+';' for k,v in constants.items())+script
            result=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=65)
            assert result.returncode==0,result.stdout+result.stderr
        finally:
            server.terminate()
            try:server.wait(timeout=9)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            server.stdout.close()
