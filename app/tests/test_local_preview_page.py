"""Force media readiness to arrive out of order in the actual shipped page."""

from importlib.resources import files
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required for the page event-order test")
def test_page_rejects_stale_media_and_keeps_historical_selection_read_only():
    script = files("moviestar").joinpath("preview_viewer.html").read_text().split('<script type="module">')[1].split('</script>')[0]
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
let nextPoll, responseState;
const videos = [], elements = {};
class Element {
  constructor(){this.listeners = {}; this.currentTime = 0; this.duration = 5; this.children=[];this.paused=true;}
  addEventListener(name, callback){(this.listeners[name] ??= []).push(callback);}
  emit(name){for(const callback of this.listeners[name] ?? []) callback();}
  appendChild(el){this.children.push(el);}
  replaceChildren(...items){this.children=items;}
  add(item){this.children.push(item);}
  remove(){this.removed = true;}
  removeAttribute(name){delete this[name];}
  load(){}
  pause(){this.paused=true;}
  play(){throw Error('The page must not automatically start playback');}
}
const document = {hidden:false,getElementById:id => elements[id] ??= new Element(),
  createElement:name=>{assert.equal(name,'video');const el=new Element();videos.push(el);return el;}};
const window = {};
const context = {document,window,URLSearchParams,AbortSignal,Date,Map,Option:class{constructor(text,value){this.text=text;this.value=value;}},
  location:{pathname:'/projects/test/',search:''},history:{replaceState(){}},
  fetch:async()=>({ok:true,json:async()=>structuredClone(responseState)}),
  setTimeout:(fn,ms)=>{if(ms===200)nextPoll=fn;return 1;},clearTimeout(){}};
const item = id=>({request_id:id,state_id:'sha256:'+id,requested_at:Date.now()/1000,
  range:{from:0,to:5},profile:{name:'720p',width:1280,height:720,fps:30},generation_ms:10});
const a=item('A'), b=item('B'), c=item('C');
const state = (latest,displayed,history)=>({latest,displayed,history});
responseState=state(a,a,[a]);
vm.runInNewContext(PAGE,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function poll(value){responseState=value;await nextPoll();await flush();}
(async()=>{
  await flush();
  videos[0].emit('canplay');
  assert.equal(window.movieStarPreview.displayed.request_id,'A');
  await poll(state(b,b,[a,b]));
  const delayedB=videos.at(-1);
  await poll(state({...c,status:'rendering'},b,[a,b]));
  delayedB.emit('canplay');
  assert.equal(window.movieStarPreview.displayed.request_id,'A');
  assert.match(elements.status.textContent,/older/);
  await poll(state(c,c,[a,b,c]));
  videos.at(-1).emit('canplay');
  assert.equal(window.movieStarPreview.displayed.request_id,'C');
  elements.version.value='A';elements.version.emit('change');
  videos.at(-1).emit('canplay');
  assert.equal(window.movieStarPreview.displayed.request_id,'A');
  assert.match(elements.status.textContent,/Historical/);
  assert.equal(window.movieStarPreview.state.latest.request_id,'C');
  elements.version.value='';elements.version.emit('change');
  await poll(state({...c,status:'failed',error:'Broken edit'},a,[a]));
  assert.match(elements.status.textContent,/failed/);
  assert.match(elements.status.textContent,/older/);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([shutil.which("node"), "-e", "const PAGE=" + __import__("json").dumps(script) + ";\n" + harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
