"""Source byte reuse stays bounded and validates even a completely hot range."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node 22+ is required')
@pytest.mark.parametrize('case', ['reuse', 'stale', 'expired', 'eviction', 'cancel', 'consumer_cancel', 'invalid_range_reply', 'short_body', 'long_body', 'range_forms', 'default_fetch', 'same_origin_range', 'origin_no_store', 'withdraw_permission'])
def test_source_buffer_contract(case):
    module = (Path(__file__).parents[1] / 'src/moviestar/preview_native_buffer.mjs').as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {SourceBuffer}=await import(MODULE);
const url='http://127.0.0.1:8765/projects/test/assets/'+'a'.repeat(24)+'?native_video=1';
let valid=true,headStatus=409,badReply=false,gets=[],heads=0,hold=false,release,noStore=false,bodyDelta=0;
const bytes=Uint8Array.from({length:128},(_,i)=>i);
const fetcher=async(request)=>{
 if(request.method==='HEAD'){
  heads++;return valid?new Response(null,{headers:{'Content-Type':'video/mp4','Content-Length':bytes.length,'Cache-Control':noStore?'private, NO-STORE':'private, no-cache'}}):new Response(null,{status:headStatus});
 }
 const [_,left,right]=request.headers.get('Range').match(/^bytes=(\d+)-(\d+)$/);
 const a=Number(left),b=Number(right);gets.push([a,b]);
 if(hold)await new Promise((resolve,reject)=>{
  release=resolve;request.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true});
 });
 return new Response(bytes.slice(a,b+1+bodyDelta),{status:badReply?200:206,headers:{'Content-Type':'video/mp4','Content-Length':b-a+1,'Content-Range':`bytes ${a}-${b}/${bytes.length}`,'Cache-Control':noStore?'no-store':'private, no-cache'}});
};
const cache=new SourceBuffer({fetcher,blockBytes:16,maxBytes:32,maxEntries:2,maxConcurrent:1});
const request=(range,signal)=>new Request(url,{headers:{Range:range},signal});
const read=async(range,signal)=>new Uint8Array(await(await cache.respond(request(range,signal))).arrayBuffer());
if(CASE==='reuse'){
 const first=await read('bytes=3-20');assert.deepEqual(first,bytes.slice(3,21));first.fill(255);
 const before=gets.length;
 assert.deepEqual(await read('bytes=4-18'),bytes.slice(4,19));
 assert.equal(gets.length,before,'Hot bytes must not fetch the source body again');
 assert.equal(heads,2,'Every media request validates the source, even on a full hit');
 assert.equal(cache.stats.cached_bytes,32);
}else if(CASE==='stale'||CASE==='expired'){
 await read('bytes=0-15');valid=false;headStatus=CASE==='stale'?409:404;
 const response=await cache.respond(request('bytes=0-15'));
 assert.equal(response.status,headStatus);
 assert.equal(gets.length,1,'Rejected validation cannot read or serve cached bytes');
 assert.equal(cache.stats.cached_bytes,0,'Rejected assets are evicted');
}else if(CASE==='eviction'){
 for(const range of ['bytes=0-15','bytes=16-31','bytes=32-47'])await read(range);
 assert.equal(cache.stats.cached_bytes,32);assert.equal(cache.stats.entries,2);
 const before=gets.length;await read('bytes=0-15');assert.equal(gets.length,before+1);
}else if(CASE==='cancel'){
 hold=true;const first=new AbortController(),second=new AbortController();
 const a=read('bytes=0-15',first.signal);a.catch(()=>{});
 while(!release)await new Promise(resolve=>setImmediate(resolve));
 const b=read('bytes=32-47',second.signal);b.catch(()=>{});
 await new Promise(resolve=>setImmediate(resolve));
 second.abort();await assert.rejects(b,{name:'AbortError'});
 first.abort();await assert.rejects(a,{name:'AbortError'});
 assert.equal(cache.stats.inflight,0);assert.equal(cache.stats.queued,0);assert.equal( gets.length,1);
 hold=false;assert.deepEqual(await read('bytes=0-15'),bytes.slice(0,16));
}else if(CASE==='invalid_range_reply'){
 badReply=true;await assert.rejects(read('bytes=0-15'),/range/i);
 assert.equal(cache.stats.cached_bytes,0,'A full response must not be buffered as a chunk');
}else if(CASE==='short_body'||CASE==='long_body'){
 bodyDelta=CASE==='short_body'?-1:1;
 await assert.rejects(read('bytes=0-15'),CASE==='short_body'?/ended early/:/exceeds/);
 assert.equal(cache.stats.cached_bytes,0);assert.equal(cache.stats.inflight,0);
}else if(CASE==='consumer_cancel'){
 hold=true;const response=await cache.respond(request('bytes=0-31'));
 const reader=response.body.getReader(),pending=reader.read();
 while(!release)await new Promise(resolve=>setImmediate(resolve));
 await reader.cancel();await pending;await new Promise(resolve=>setImmediate(resolve));
 assert.equal(cache.stats.inflight,0);assert.equal(cache.stats.queued,0);assert.equal(cache.stats.cached_bytes,0);
}else if(CASE==='origin_no_store'){
 noStore=true;await read('bytes=0-15');await read('bytes=0-15');
 assert.equal(gets.length,2);assert.equal(cache.stats.cached_bytes,0);
}else if(CASE==='withdraw_permission'){
 await read('bytes=0-15');assert.equal(cache.stats.cached_bytes,16);
 noStore=true;await read('bytes=0-15');assert.equal(cache.stats.cached_bytes,0);
 assert.equal(gets.length,2,'Withdrawing storage permission bypasses even a hot entry');
}else if(CASE==='same_origin_range'){
 const checked=new SourceBuffer({fetcher:async r=>{assert.equal(r.mode,'same-origin','Internal range headers must not inherit the media no-cors guard');return fetcher(r);},blockBytes:16});
 const mediaRequest=new Request(url,{mode:'no-cors',headers:{Range:'bytes=1-5'}});
 assert.deepEqual(new Uint8Array(await(await checked.respond(mediaRequest)).arrayBuffer()),bytes.slice(1,6));
}else if(CASE==='default_fetch'){
 globalThis.fetch=function(request){assert.ok(this===undefined||this===globalThis,'Browser fetch must keep its global receiver');return fetcher(request);};
 const defaults=new SourceBuffer({blockBytes:16,maxBytes:32,maxEntries:2,maxConcurrent:1});
 assert.deepEqual(new Uint8Array(await(await defaults.respond(request('bytes=0-15'))).arrayBuffer()),bytes.slice(0,16));
}else if(CASE==='range_forms'){
 assert.deepEqual(await read('bytes=-5'),bytes.slice(-5));
 assert.deepEqual(await read('bytes=120-'),bytes.slice(120));
 const response=await cache.respond(request('bytes=999-'));
 assert.equal(response.status,416);assert.equal(response.headers.get('Content-Range'),'bytes */128');
 assert.equal(response.headers.get('Cache-Control'),'no-store');
}
'''
    prefix = 'const MODULE=' + json.dumps(module) + ';const CASE=' + json.dumps(case) + ';\n'
    result = subprocess.run(['node', '--input-type=module', '-e', prefix + script],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node 22+ is required')
def test_worker_intercepts_only_its_project_native_video_requests():
    worker = (Path(__file__).parents[1] / 'src/moviestar/preview_native_worker.mjs').read_text().split('\n', 1)[1]
    script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict'),listeners={},calls=[];
const scope='http://127.0.0.1:8765/projects/owned/';
class SourceBuffer{stats={cached_bytes:12};async respond(request){calls.push(request.url);return new Response('bytes');}}
const self={registration:{scope},addEventListener:(name,fn)=>listeners[name]=fn,
 skipWaiting:async()=>calls.push('install'),clients:{claim:async()=>calls.push('activate')}};
vm.runInNewContext(WORKER,{self,SourceBuffer,URL,Response,console});
(async()=>{
 for(const name of ['install','activate'])await new Promise(resolve=>listeners[name]({waitUntil:p=>p.then(resolve)}));
 assert.deepEqual(calls.splice(0),['install','activate']);
 const asset='assets/'+'a'.repeat(24);
 for(const [url,method] of [[scope+asset,'GET'],[scope+'state?native_video=1','GET'],
  [scope+asset+'?native_video=1','HEAD'],[scope+asset+'?native_video=1','POST'],
  [scope.replace('/owned/','/foreign/')+asset+'?native_video=1','GET'],
  [scope.replace('127.0.0.1','foreign.example')+asset+'?native_video=1','GET']]){
   listeners.fetch({request:{url,method},respondWith(){throw Error('Out-of-scope request intercepted');}});
 }
 let response;listeners.fetch({request:{url:scope+asset+'?native_video=1',method:'GET'},respondWith:p=>response=p});
 assert.equal(await(await response).text(),'bytes');assert.equal(calls.length,1);
 let stats;listeners.message({data:'native-source-buffer-stats',ports:[{postMessage:value=>stats=value}]});
 assert.equal(stats.cached_bytes,12);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(['node', '-e', 'const WORKER=' + json.dumps(worker) + ';\n' + script],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node 22+ is required')
@pytest.mark.parametrize('case', ['controlled', 'unavailable', 'rejected', 'timeout', 'abort'])
def test_source_buffer_activation_has_bounded_fallback_and_cleans_up(case):
    from tests.native_stage_harness import stage_script
    stage = stage_script()
    script = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const service=new EventTarget(),signal=new AbortController();let timeout,cleared=false;
const href='http://127.0.0.1:8765/projects/owned/stage?request=test';
service.register=async(url,options)=>{
 assert.equal(url,new URL('preview_native_worker.mjs',href).href);
 assert.equal(options.scope,'./');assert.equal(options.type,'module');
 if(CASE==='rejected')throw Error('disabled');
 if(CASE==='controlled')service.controller={scriptURL:url};
 if(['timeout','abort'].includes(CASE))return new Promise(()=>{});
 return {};
};
const context={window:{moviestarScene:{entries:[],applyMotion(){}}},
 document:{createElement:()=>({getContext:()=>({})})},location:{href,search:'?request=test'},
 navigator:CASE==='unavailable'?{}:{serviceWorker:service},signal:signal.signal,
 URL,URLSearchParams,AbortController,performance:{now:()=>1},Date,
 fetch:()=>new Promise(()=>{}),setTimeout:fn=>{timeout=fn;return 1;},clearTimeout:()=>cleared=true,
 requestAnimationFrame:fn=>fn(),console};
vm.createContext(context);vm.runInContext(STAGE,context);
(async()=>{
 const pending=vm.runInContext('enableSourceBuffering(signal)',context);
 if(CASE==='timeout')timeout();if(CASE==='abort')signal.abort();
 assert.equal(await pending,CASE==='controlled');
 if(CASE!=='unavailable')assert.equal(cleared,true,'Always remove the activation timeout');
 // A delayed controller event after fallback/disposal cannot change the result.
 service.dispatchEvent(new Event('controllerchange'));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    prefix = 'const STAGE=' + json.dumps(stage) + ';const CASE=' + json.dumps(case) + ';\n'
    result = subprocess.run(['node', '-e', prefix + script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
