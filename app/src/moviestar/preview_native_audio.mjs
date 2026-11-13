// One lossless, bounded result-window mix. Picture cuts never restart it.
export function createPreparedAudio(context,destination,signal,duration){
  let buffer=null,plane=null,source=null,generation=0,disposed=false,offset=0,startedAt=0,lastStart=null;
  const api={
    async prepare(next,requestSignal){
      requestSignal?.throwIfAborted();if(disposed)throw Error('Superseded');
      let prepared=null,settled=false;
      if(next){
        if(!(next.duration>0&&next.duration<=10)||(duration!==undefined&&Math.abs(next.duration-duration)>1e-6)||next.sample_rate!==48000||next.channels!==2)
          throw Error('Unsupported native audio window');
        const response=await fetch('assets/'+next.asset,{cache:'no-store',signal:requestSignal});
        if(!response.ok)throw Error('Prepared audio is no longer retained');
        const bytes=await response.arrayBuffer();
        if(bytes.byteLength>next.duration*48000*8+4096)throw Error('Native audio exceeds its bounded window');
        prepared=await context.decodeAudioData(bytes);
        if(prepared.numberOfChannels!==2||prepared.sampleRate!==48000
            ||Math.abs(prepared.duration-next.duration)>1/48000+1e-6)
          throw Error('Prepared audio does not match the result window');
      }
      requestSignal?.throwIfAborted();
      return{
        commit(){
          requestSignal?.throwIfAborted();if(disposed||settled)throw Error('Superseded');
          api.pause();buffer=prepared;prepared=null;plane=next;offset=0;lastStart=null;settled=true;
        },
        cancel(){if(!settled){prepared=null;settled=true;}}
      };
    },
    async play(at){
      api.pause();if(!buffer||disposed)return;
      const attempt=generation,active=()=>!disposed&&!signal?.aborted&&attempt===generation;
      await context.resume();if(!active())return;
      const deadline=performance.now()+2000;
      while(context.getOutputTimestamp().contextTime<=0||context.getOutputTimestamp().performanceTime<=0){
        if(!active())return;if(performance.now()>deadline)throw Error('Audio output clock did not start');
        await new Promise(resolve=>setTimeout(resolve,10));
      }
      if(!active())return;
      offset=Math.max(0,Math.min(buffer.duration,at));if(offset>=buffer.duration)return;
      source=context.createBufferSource();source.buffer=buffer;source.connect(destination);
      const playing=source;source.onended=()=>{if(source===playing){source.disconnect();source=null;offset=buffer.duration;}};
      destination.port?.postMessage({kind:'capture'});
      startedAt=context.currentTime;lastStart={context_time:startedAt,offset};
      source.start(startedAt,offset);
      // Media-element pictures can advance before queued Web Audio reaches
      // the device. Start picture transport on this actual output-clock edge.
      while(context.getOutputTimestamp().contextTime+1/context.sampleRate<startedAt){
        if(!active())return;
        if(performance.now()>deadline)throw Error('Prepared audio did not reach the output clock');
        await new Promise(resolve=>setTimeout(resolve,1));
      }
    },
    pause(){
      ++generation;
      if(source){offset=Math.min(buffer.duration,offset+Math.max(0,context.currentTime-startedAt));
        source.onended=null;source.stop();source.disconnect();source=null;}
    },
    seek(at){api.pause();offset=buffer?Math.max(0,Math.min(buffer.duration,at)):0;lastStart=null;},
    snapshot(){return{prepared:Boolean(buffer),playing:Boolean(source),asset:plane?.asset,
      duration:buffer?.duration,sample_rate:buffer?.sampleRate,channels:buffer?.numberOfChannels,
      time:source?Math.min(buffer.duration,offset+Math.max(0,context.currentTime-startedAt)):offset,
      started_context_time:lastStart?.context_time,started_offset:lastStart?.offset,
      source_audio:plane?.source_audio,tracks:plane?.tracks,source_ranges:plane?.source_ranges};},
    dispose(){if(disposed)return;api.pause();disposed=true;buffer=null;plane=null;signal?.removeEventListener('abort',abort);}
  };
  const abort=()=>api.dispose();signal?.addEventListener('abort',abort,{once:true});
  if(signal?.aborted)api.dispose();
  return api;
}

// Meter the actual final output. Late diagnostic batches keep their original
// audio-clock times; a Pause must not discard samples produced before it.
export async function createMixedOutput(context,metrics,signal,{audit=false,capturePcm=false}={}){
  await context.audioWorklet.addModule('preview_native_cut_meter.mjs');signal.throwIfAborted();
  const node=new AudioWorkletNode(context,'cut-output-meter',{channelCount:2,channelCountMode:'explicit',
    channelInterpretation:'speakers',outputChannelCount:[2],processorOptions:{audit,capturePcm}});
  node.connect(context.destination);
  let nextId=0,disposed=false;const drains=new Map();
  const api={node,
    drain(){return new Promise((resolve,reject)=>{
      if(disposed){reject(Error('Superseded'));return;}
      const id=++nextId,timer=setTimeout(()=>{drains.delete(id);reject(Error('Audio diagnostics did not drain'));},1000);
      drains.set(id,{resolve,reject,timer});node.port.postMessage({kind:'flush',id});
    });},
    dispose(){
      if(disposed)return;disposed=true;
      for(const {reject,timer} of drains.values()){clearTimeout(timer);reject(Error('Superseded'));}drains.clear();
      node.disconnect();node.port.close();signal.removeEventListener('abort',abort);
    }
  };
  node.port.onmessage=({data})=>{
    if(disposed)return;
    if(data.kind==='pcm'){
      const pcm=new Float32Array(data.data);
      metrics.audio_pcm={start_context_time:data.start_context_time,sample_rate:data.sample_rate,
        samples:[Array.from(pcm.subarray(0,data.frames)),Array.from(pcm.subarray(data.capacity,data.capacity+data.frames))]};return;
    }
    if(data.kind==='flushed'){
      const waiting=drains.get(data.id);if(waiting){clearTimeout(waiting.timer);drains.delete(data.id);waiting.resolve();}return;
    }
    for(const item of data.kind==='blocks'?data.blocks:[data]){
      if(!Number.isFinite(metrics.play_context_time)||item.context_time<metrics.play_context_time-1e-6
          ||item.context_time>=(metrics.pause_context_time??Infinity))continue;
      if(item.kind==='block'){(metrics.audio_blocks??=[]).push(item);continue;}
      const stamp=context.getOutputTimestamp();
      metrics.beeps.push({...item,display_time:stamp.performanceTime+(item.context_time-stamp.contextTime)*1000,
        timestamp_available:stamp.performanceTime>0&&stamp.contextTime>0});
    }
  };
  const abort=()=>api.dispose();signal.addEventListener('abort',abort,{once:true});
  if(signal.aborted)api.dispose();
  return api;
}
