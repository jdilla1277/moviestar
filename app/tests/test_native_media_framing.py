"""Live media content boxes agree with export and fail atomically."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

import av
import numpy as np
import pytest
from moviestar.spec import load_spec,save_spec
from moviestar.project import load_project
from moviestar.resolved import resolve_project
from moviestar.spatial_model import compile_spatial_frame
from moviestar.spatial_layout import browser_available,_chrome_path,measure_spatial_layout
from moviestar.spatial_render import render_spatial_frame
from tests.test_native_edit_benchmark import benchmark


@pytest.mark.parametrize('radius,padding', [(40,'10px'),('30% 50% / 20% 40%','6px 10px 12px 4px')])
def test_live_decorated_boxes_match_export_and_reject_empty_content(tmp_path,benchmark,radius,padding):
    if not browser_available():pytest.skip('Chrome and Node required')
    from framing_benchmark import prepare
    root=tmp_path/'project';prepare(root,(360,640),'framing');spec=load_spec(root)
    video=spec['spatial']['definitions']['speaker_card']['children'][0]
    video['layout']['padding']=padding
    video['style'].update(border_width=5,border_style='solid',border_color='#ffe000',border_radius=radius,
        object_position='right 10px bottom 5px',background_color='#183848')
    save_spec(spec,cwd=root);before=Path.cwd()
    try:
        os.chdir(root)
        frame=compile_spatial_frame(spec,resolve_project(load_project(root),spec),.5)
        boxes=measure_spatial_layout(frame,{'width':360,'height':640})['boxes']
        render_spatial_frame(spec,frame,{'width':360,'height':640},project_root=root,output_path=tmp_path/'reference.png')
    finally:os.chdir(before)
    video['layout']['width']=10;bad=tmp_path/'bad.json';bad.write_text(json.dumps(spec))
    with (tmp_path/'server.log').open('w')as log:
        server=subprocess.Popen([sys.executable,'-m','moviestar.cli','preview','--workspace',str(root),
            '--mode','native','--from','0','--to','5','--port','0','--no-open'],stdout=subprocess.PIPE,stderr=log,text=True)
        try:
            with selectors.DefaultSelector()as selector:
                selector.register(server.stdout,selectors.EVENT_READ);assert selector.select(20)
                ready=json.loads(server.stdout.readline())
            constants={'MODULE':(Path(__file__).parents[2]/'experiments/live_browser_preview/browser.mjs').as_uri(),
                'CHROME':str(_chrome_path()),'OUT':str(tmp_path/'browser'),'URL':ready['url'],
                'PYTHON':sys.executable,'ROOT':str(root),'BAD':str(bad),'BOXES':boxes,'PNG':str(tmp_path/'native.png')}
            script=r'''
import assert from 'node:assert/strict';import{writeFile}from'node:fs/promises';
const {Browser}=await import(MODULE),b=await Browser.launch(CHROME,OUT);
try{
 await b.send('Emulation.setDeviceMetricsOverride',{width:1280,height:1100,deviceScaleFactor:1,mobile:false});
 await b.send('Page.navigate',{url:URL});await b.wait('window.moviestarLive?.is_current',20000);
 await b.evaluate(`(()=>{const s=document.getElementById('stage'),style=document.createElement('style');style.textContent='#stage{max-width:none!important;width:'+(360+s.offsetWidth-s.clientWidth)+'px!important;border-radius:0!important;border-color:transparent!important;box-shadow:none!important}';document.head.append(style);})()`);
 const edges=await b.evaluate(`(()=>{const outer=document.querySelector('iframe:not(.pending)'),frame=outer.contentDocument.querySelector('iframe')??outer,doc=frame.contentDocument;return[...doc.querySelectorAll('video')].map(v=>{const c=doc.defaultView.getComputedStyle(v);return{padding:c.padding,border:c.borderWidth,clip:doc.defaultView.getComputedStyle(v.parentElement).clipPath};});})()`);
 assert.ok(edges.length===2&&edges.every(e=>e.padding==='0px'&&e.border==='0px'&&e.clip==='content-box'),'Native video stays undecorated inside the content clip: '+JSON.stringify(edges));
 await b.evaluate('window.moviestarLive.stage.seek(.5)');const snapshot=await b.evaluate('window.moviestarLive.stage.snapshot()');
 for(const box of BOXES.filter(b=>b.media_box)){
  const actual=snapshot.boxes.find(b=>JSON.stringify(b.path)===JSON.stringify(box.path));
  for(const k of ['x','y','width','height'])assert.ok(Math.abs(actual.media_box.content_rect[k]-box.media_box.content_rect[k])<=1,JSON.stringify({actual,box}));
  assert.equal(actual.media_box.object_fit,box.media_box.object_fit);assert.equal(actual.media_box.object_position,box.media_box.object_position);
 }
 const clip=await b.evaluate(`(()=>{const r=document.querySelector('iframe:not(.pending)').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,scale:1};})()`);
 const shot=await b.send('Page.captureScreenshot',{format:'png',clip,captureBeyondViewport:true});await writeFile(PNG,Buffer.from(shot.data,'base64'));
 const prior=await b.evaluate('window.moviestarLive.current.request_id');const {spawnSync}=await import('node:child_process');
 const run=args=>{const r=spawnSync(PYTHON,['-m','moviestar.cli',...args],{cwd:ROOT,encoding:'utf8'});assert.equal(r.status,0,r.stdout+r.stderr);};
 run(['spec','--edit',BAD]);await b.wait('document.getElementById("error").textContent.includes("Media content box")',15000);
 assert.match(await b.evaluate('document.getElementById("error").textContent'),/review\/main\/footage/);
 assert.equal(await b.evaluate('window.moviestarLive.current.request_id'),prior);assert.equal(await b.evaluate('window.moviestarLive.is_current'),false);
 run(['undo']);await b.wait('window.moviestarLive.is_current&&window.moviestarLive.current.request_id!=='+JSON.stringify(prior),15000);
}finally{await b.close();}
'''
            result=subprocess.run(['node','--input-type=module','-e',''.join('const '+k+'='+json.dumps(v)+';' for k,v in constants.items())+script],
                capture_output=True,text=True,timeout=65)
            assert result.returncode==0,result.stdout+result.stderr
        finally:
            server.terminate()
            try:server.wait(timeout=9)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            server.stdout.close()
    def pixels(path):
        with av.open(str(path))as c:return next(c.decode(video=0)).to_ndarray(format='rgb24').astype(int)
    expected,actual=pixels(tmp_path/'reference.png'),pixels(tmp_path/'native.png')
    assert expected.shape==actual.shape==(640,360,3)
    # Semantic regions: transparent corners, border, padding and source interiors.
    for y in (0,5,10,15,25,200,350,630):
        for x in (0,5,10,15,25,180,350,359):
            assert np.abs(expected[y,x]-actual[y,x]).max()<=8,(x,y,expected[y,x],actual[y,x])
