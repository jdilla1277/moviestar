"""Force native iframe readiness to arrive late or fail in the shipped experiment."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='Node is required')


def run_page(scenario):
    page = (Path(__file__).parents[2] / 'experiments/live_browser_preview/runtime.mjs').read_text()
    harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
let nextPoll,responseState;const frames=[],elements={};
class Element{
 constructor(){this.listeners={};this.children=[];this.style={};this.clientWidth=640;}
 addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
 emit(name){for(const fn of this.listeners[name]??[])fn();}
 replaceChildren(...values){this.children=values;}
 add(value){this.children.push(value);}
 append(value){this.children.push(value);}
 remove(){this.removed=true;}
}
class Frame extends Element{
 constructor(){super();let resolve,reject;const ready=new Promise((a,b)=>{resolve=a;reject=b;});ready.catch(()=>{});
  this.resolve=resolve;this.reject=reject;
  this.contentWindow={moviestarLiveStage:{ready,metrics:{},time:0,pause(){},seek:async()=>{},error:null,
   dispose(){},packet:{canvas:{width:640,height:360},range:{from:0,to:5}}}};
 }
 set src(value){const id=new URL(value,'http://localhost').searchParams.get('request');Object.assign(this.contentWindow.moviestarLiveStage.packet,{request_id:id,state_id:'sha256:'+id});}
}
const context={window:{},document:{getElementById:id=>elements[id]??=new Element(),createElement:name=>{assert.equal(name,'iframe');const f=new Frame();frames.push(f);return f;}},
 location:{pathname:'/projects/unit/',search:''},URL,URLSearchParams,Date,Map,AbortSignal,performance:{timeOrigin:Date.now()-1000},
 ResizeObserver:class{observe(){}},Option:class{constructor(text,value){this.text=text;this.value=value;}},
 fetch:async()=>({ok:true,json:async()=>structuredClone(responseState)}),
 setTimeout:(fn,ms)=>{if(ms===100)nextPoll=fn;return 1;},clearTimeout(){},requestAnimationFrame:fn=>queueMicrotask(fn)};
const item=id=>({request_id:id,state_id:'sha256:'+id,requested_at:Date.now()/1000,status:'ready'});
const a=item('A'),b=item('B'),c=item('C');
const state=(latest,history)=>({latest,displayed:latest,history});
responseState=state(a,[a]);vm.runInNewContext(PAGE,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function poll(value){responseState=value;await nextPoll();await flush();}
(async()=>{await flush();
 frames[0].emit('load');frames[0].resolve(true);await flush();
 assert.equal(context.window.moviestarLive.current.request_id,'A');
''' + scenario + r'''
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([shutil.which('node'), '-e', 'const PAGE='+json.dumps(page)+';\n'+harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout+result.stderr


def test_late_native_ready_cannot_replace_the_latest_edit():
    run_page(r'''
 await poll(state(b,[a,b]));const old=frames.at(-1);old.emit('load');
 await poll(state(c,[a,b,c]));const fresh=frames.at(-1);fresh.emit('load');fresh.resolve(true);await flush();
 assert.equal(context.window.moviestarLive.current.request_id,'C');
 old.resolve(true);await flush();
 assert.equal(context.window.moviestarLive.current.request_id,'C');
 assert.equal(context.window.moviestarLive.events.length,2);
 assert.equal(old.removed,true);
''')


def test_native_failure_clears_after_a_later_success():
    run_page(r'''
 await poll(state(b,[a,b]));const failed=frames.at(-1);failed.emit('load');failed.reject(Error('Media failed'));await flush();
 assert.equal(context.window.moviestarLive.current.request_id,'A');
 assert.match(elements.error.textContent,/could not load/);
 const count=frames.length;await poll(state(b,[a,b]));assert.equal(frames.length,count);
 await poll(state(c,[a,b,c]));const fresh=frames.at(-1);fresh.emit('load');fresh.resolve(true);await flush();
 assert.equal(context.window.moviestarLive.current.request_id,'C');assert.equal(elements.error.textContent,'');
''')
