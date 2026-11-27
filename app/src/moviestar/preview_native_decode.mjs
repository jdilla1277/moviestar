// Explicit ownership of a bounded decoded-picture queue. Source packet bytes
// and timestamps are preserved; this module never encodes a preview movie.
export function createFrameQueue(index,{fetchPacket,maxFrames=8,maxBytes=128*1024*1024,
 decoderFactory=callbacks=>new VideoDecoder(callbacks),chunkFactory=value=>new EncodedVideoChunk(value)}={}){
 if(!Number.isInteger(maxFrames)||maxFrames<4||maxFrames>16)throw Error('Invalid decoded frame bound');
 const bytesPerFrame=index.config.codedWidth*index.config.codedHeight*4;
 if(!Number.isFinite(bytesPerFrame)||bytesPerFrame<=0||bytesPerFrame*maxFrames>maxBytes)throw Error('Native decoded frame budget exceeded');
 // Packet PTS and durations are independently rounded to integer microseconds.
 // Adjacent original timestamps define disjoint intervals without epsilon gaps
 // or showing a future picture early. Decoder output remains in display order.
 const timeline=[...new Set(index.packets.map(p=>p.timestamp))].sort((a,b)=>a-b);
 const ends=new Map(timeline.slice(0,-1).map((stamp,i)=>[stamp,timeline[i+1]]));
 const endOf=frame=>ends.get(frame.timestamp)??frame.timestamp+(frame.duration??1e6/index.fps);
 let disposed=false,generation=0,decoder,controller,frames=[],pending=0,cursor=0,error,operation,target=0,flushed=false;
 const metrics={decoded:0,closed:0,peak_frames:0,peak_reserved:0};
 const close=frame=>{frame.close();metrics.closed++;};
 function clear(){for(const frame of frames)close(frame);frames=[];pending=0;if(decoder&&decoder.state!=='closed')decoder.close();controller?.abort();}
 const find=at=>frames.find(f=>f.timestamp<=at&&at<endOf(f));
 function discard(at){frames=frames.filter(f=>{if(endOf(f)<=at){close(f);return false;}return true;});}
 function check(attempt){if(disposed||attempt!==generation)return false;if(error)throw error;return true;}
 async function fill(at,attempt){
  const deadline=performance.now()+5000;
  while(check(attempt)){
   discard(at);
   if(frames.length+pending>=maxFrames){
    if(find(at))return true;
    if(performance.now()>deadline)throw Error('Native decoder did not produce the required frame within its bound');
    await new Promise(r=>setTimeout(r,0));continue;
   }
   if(cursor>=index.packets.length){
    if(!flushed){flushed=true;await decoder.flush();if(!check(attempt))return false;}
    if(!find(at))throw Error('Native source frame is unavailable');return true;
   }
   const packet=index.packets[cursor++];
   const data=await fetchPacket(packet,controller.signal);if(!check(attempt))return false;
   pending++;metrics.peak_reserved=Math.max(metrics.peak_reserved,frames.length+pending);
   decoder.decode(chunkFactory({type:packet.key?'key':'delta',timestamp:packet.timestamp,duration:packet.duration,data}));
  }
  return false;
 }
 const api={metrics,
  async seek(at){
   if(disposed)return false;
   const attempt=++generation;clear();error=null;flushed=false;target=Math.round(at*1e6);controller=new AbortController();
   cursor=0;for(let i=0;i<index.packets.length;i++)if(index.packets[i].key&&index.packets[i].timestamp<=target)cursor=i;
   const owned=decoderFactory({output(frame){
    if(disposed||attempt!==generation||error){close(frame);return;}
    pending--;metrics.decoded++;
    const actualBytes=(frame.codedWidth??index.config.codedWidth)*(frame.codedHeight??index.config.codedHeight)*4;
    if((frame.displayWidth!==undefined&&(frame.displayWidth!==index.config.codedWidth||frame.displayHeight!==index.config.codedHeight))
       ||actualBytes*maxFrames>maxBytes){
      close(frame);error=Error('Native decoded picture dimensions or frame budget changed; use --mode rendered');
      if(decoder.state!=='closed')decoder.close();return;
    }
    if(endOf(frame)<=target||frame.timestamp>=index.end*1e6){close(frame);return;}
    frames.push(frame);frames.sort((a,b)=>a.timestamp-b.timestamp);metrics.peak_frames=Math.max(metrics.peak_frames,frames.length);
    if(frames.length>maxFrames)error=Error('Native decoder exceeded the frame bound');
   },error(cause){if(attempt===generation&&!error)error=cause;}});decoder=owned;
   decoder.configure({...index.config,optimizeForLatency:true});
   const work=fill(target,attempt);operation=work;
   try{return await work;}catch(cause){if(disposed||attempt!==generation)return false;throw cause;}finally{if(operation===work)operation=null;}
  },
  async ensure(at){
   if(disposed)return false;
   const attempt=generation;
   while(operation){
    try{await operation;}catch(cause){if(disposed||attempt!==generation)return false;throw cause;}
    if(!check(attempt))return false;
   }
   if(!check(attempt))return false;
   target=Math.round(at*1e6);const work=fill(target,attempt);operation=work;
   try{return await work;}catch(cause){if(disposed||attempt!==generation)return false;throw cause;}finally{if(operation===work)operation=null;}
  },
  frameAt(at){if(disposed)throw Error('Native frame queue is disposed');if(error)throw error;const frame=find(Math.round(at*1e6));if(!frame)throw Error('Native source frame is not ready: '+JSON.stringify({at,queued:frames.map(f=>f.timestamp),pending,cursor}));return frame;},
  dispose(){if(disposed)return;disposed=true;++generation;clear();}
 };
 return api;
}
