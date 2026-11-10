// A disposable source-byte buffer, never rendered clips or durable storage.
const permitsMemory=response=>{
  const directives=new Set((response.headers.get('Cache-Control')??'').toLowerCase().split(',').map(value=>value.trim().split('=')[0]));
  return directives.has('private')&&directives.has('no-cache')&&!directives.has('no-store');
};
export class SourceBuffer {
  constructor({fetcher=request=>fetch(request),blockBytes=256*1024,maxBytes=64*1024*1024,maxEntries=256,maxConcurrent=4}={}){
    if(![blockBytes,maxBytes,maxEntries,maxConcurrent].every(n=>Number.isSafeInteger(n)&&n>0))throw RangeError('Invalid source buffer limits');
    Object.assign(this,{fetcher,blockBytes,maxBytes,maxEntries,maxConcurrent});
    this.blocks=new Map();this.bytes=0;this.active=0;this.waiters=[];
  }
  get stats(){return{cached_bytes:this.bytes,entries:this.blocks.size,inflight:this.active,queued:this.waiters.length,max_bytes:this.maxBytes};}
  purge(url){for(const [key,entry] of this.blocks)if(entry.url===url){this.bytes-=entry.data.byteLength;this.blocks.delete(key);}}
  async take(signal){
    signal.throwIfAborted();
    if(this.active<this.maxConcurrent){this.active++;return;}
    await new Promise((resolve,reject)=>{
      const waiter={resolve,signal,abort:()=>{const i=this.waiters.indexOf(waiter);if(i>=0)this.waiters.splice(i,1);reject(signal.reason);}};
      this.waiters.push(waiter);signal.addEventListener('abort',waiter.abort,{once:true});
      if(signal.aborted)waiter.abort();
    });
  }
  release(){
    const next=this.waiters.shift();
    if(next){next.signal.removeEventListener('abort',next.abort);next.resolve();}
    else this.active--;
  }
  remember(key,entry){
    if(entry.data.byteLength>this.maxBytes)return;
    const prior=this.blocks.get(key);
    if(prior){this.bytes-=prior.data.byteLength;this.blocks.delete(key);}
    while(this.bytes+entry.data.byteLength>this.maxBytes||this.blocks.size>=this.maxEntries){
      const oldest=this.blocks.keys().next().value;
      this.bytes-=this.blocks.get(oldest).data.byteLength;this.blocks.delete(oldest);
    }
    this.blocks.set(key,entry);this.bytes+=entry.data.byteLength;
  }
  async block(request,url,size,start,signal){
    signal.throwIfAborted();
    const key=url+'\n'+size+':'+start,hit=this.blocks.get(key);
    if(hit){this.blocks.delete(key);this.blocks.set(key,hit);return hit.data;}
    await this.take(signal);
    try{
      signal.throwIfAborted();
      const end=Math.min(size-1,start+this.blockBytes-1),length=end-start+1;
      const headers=new Headers(request.headers);headers.set('Range',`bytes=${start}-${end}`);
      headers.set('X-Moviestar-Source-Buffer','memory');
      const response=await this.fetcher(new Request(request,{headers,mode:'same-origin',cache:'no-store',signal}));
      if(response.status!==206||response.headers.get('Content-Range')!==`bytes ${start}-${end}/${size}`||Number(response.headers.get('Content-Length'))!==length){
        await response.body?.cancel();throw Error(`Source byte range response is invalid (${response.status})`);
      }
      // Read into one fixed-size block; never arrayBuffer() an unchecked body.
      const data=new Uint8Array(length),reader=response.body.getReader();let at=0;
      try{
        while(true){
          const {done,value}=await reader.read();if(done)break;
          if(at+value.byteLength>length){await reader.cancel();throw Error('Source byte range exceeds its declared length');}
          data.set(value,at);at+=value.byteLength;
        }
      }finally{reader.releaseLock();}
      signal.throwIfAborted();
      if(at!==length)throw Error('Source byte range ended early');
      if(permitsMemory(response))this.remember(key,{url,data});
      return data;
    }finally{this.release();}
  }
  async respond(request){
    if(request.method!=='GET'||['If-Range','If-None-Match','If-Match'].some(name=>request.headers.has(name)))return this.fetcher(request);
    const url=new URL(request.url);url.hash='';const identity=url.href;
    const headers=new Headers(request.headers);headers.delete('Range');
    headers.set('X-Moviestar-Source-Buffer','memory');
    // The origin checks both registry membership and current file identity on
    // every request, including complete memory hits and historical reviews.
    const head=await this.fetcher(new Request(request,{method:'HEAD',headers,mode:'same-origin',cache:'no-store'}));
    if(!head.ok){this.purge(identity);return head;}
    if(!permitsMemory(head)){this.purge(identity);return this.fetcher(request);}
    const size=Number(head.headers.get('Content-Length'));
    if(!Number.isSafeInteger(size)||size<=0||!head.headers.get('Content-Type')?.startsWith('video/'))return this.fetcher(request);
    const range=request.headers.get('Range');let start=0,end=size-1;
    if(range){
      const match=/^bytes=(\d*)-(\d*)$/.exec(range);
      if(!match||!match[1]&&!match[2])return this.fetcher(request);
      if(match[1]){start=Number(match[1]);if(match[2])end=Math.min(end,Number(match[2]));}
      else start=Math.max(0,size-Number(match[2]));
      if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||start>end||start>=size)
        return new Response(null,{status:416,headers:{'Content-Range':`bytes */${size}`,'Cache-Control':'no-store'}});
    }
    const controller=new AbortController(),abort=()=>controller.abort(request.signal.reason);
    request.signal.addEventListener('abort',abort,{once:true});if(request.signal.aborted)abort();
    const cleanup=()=>request.signal.removeEventListener('abort',abort);
    let at=start;
    const stream=new ReadableStream({
      pull:async output=>{
        try{
          controller.signal.throwIfAborted();
          const offset=Math.floor(at/this.blockBytes)*this.blockBytes;
          const data=await this.block(request,identity,size,offset,controller.signal);
          const length=Math.min(data.byteLength-(at-offset),end-at+1);
          // Give the consumer a copy, never the mutable cached backing store.
          output.enqueue(data.slice(at-offset,at-offset+length));at+=length;
          if(at>end){output.close();cleanup();}
        }catch(error){
          if(!controller.signal.aborted){this.purge(identity);console.error('Native source byte buffering failed',error);}
          cleanup();output.error(error);
        }
      },
      cancel:()=>{controller.abort();cleanup();},
    });
    const resultHeaders=new Headers(head.headers);resultHeaders.set('Content-Length',end-start+1);
    resultHeaders.set('Cache-Control','no-store');resultHeaders.set('Accept-Ranges','bytes');
    if(range)resultHeaders.set('Content-Range',`bytes ${start}-${end}/${size}`);
    return new Response(stream,{status:range?206:200,headers:resultHeaders});
  }
}
