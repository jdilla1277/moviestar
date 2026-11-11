// Native hard cuts: bounded prepared scenes, one shared running audio context.
export function createCutPlayback({packet,stages,audio,metrics,show,audit=false,cleanup=()=>{}}){
  let index=0,playing=false,disposed=false,generation=0,seekGeneration=0,pendingSeek,timer,handoffTimer;
  const duration=packet.range.to-packet.range.from;
  const length=i=>packet.segments[i].packet.range.to-packet.segments[i].packet.range.from;
  Object.assign(metrics,{frames:[],flashes:[],beeps:[],cuts:[],segments_shown:[],audio_blocks:[],stalls:0,finished:false});
  function outputs(active,at=audio.currentTime){
    stages.forEach((stage,i)=>{stage.gain.gain.cancelScheduledValues(audio.currentTime);stage.gain.gain.setValueAtTime(i===active?1:0,at);});
  }
  function reveal(i,selectAudio=true){
    show(i);if(selectAudio)outputs(playing?i:-1);
    metrics.segments_shown.push({segment:i,result_time:packet.range.from+packet.segments[i].offset,
      media_time:stages[i].api.packet.source_start+stages[i].api.time,
      source:audit?stages[i].api.snapshot().source:undefined,shown_at:Date.now()});
  }
  function fail(error){metrics.error=String(error.message??error);if(!metrics.error.includes('--mode rendered'))metrics.error+=' Use --mode rendered.';api.pause();}
  function finish(){
    metrics.end_result_time=packet.range.from+api.time;metrics.finished_at=Date.now();
    metrics.finished=true;
    metrics.stalls=stages.reduce((total,s)=>total+s.api.metrics.stalls,0);
    metrics.quality={totalVideoFrames:stages.reduce((n,s)=>n+(s.api.metrics.quality?.totalVideoFrames??0),0),
      droppedVideoFrames:stages.reduce((n,s)=>n+(s.api.metrics.quality?.droppedVideoFrames??0),0)};
    api.pause();
  }
  async function advance(i,attempt,expectedAt){
    if(disposed||!playing||attempt!==generation||index!==i)return;
    clearTimeout(timer);
    if(i===stages.length-1){
      try{
        stages[i].api.pause();
        if(stages[i].api.settleEnd)await stages[i].api.settleEnd();
        if(!disposed&&playing&&attempt===generation)finish();
      }catch(error){if(attempt===generation)fail(error);}
      return;
    }
    index++;reveal(index,false);
    metrics.cuts.push({from:i,to:index,result_time:packet.range.from+packet.segments[index].offset,
      expected_at:expectedAt,switched_at:Date.now()});
    try{
      await stages[index].api.play();
      if(!disposed&&playing&&attempt===generation){
        // Keep the outgoing source audible until the prepared incoming media
        // pipeline is running. Switch gains together on the next shared audio
        // render quantum. Extra future padding here can outlive an outgoing
        // source's EOF and introduce silence even though the next clip runs.
        outputs(index);
        handoffTimer=setTimeout(()=>{if(!disposed&&playing&&attempt===generation)stages[i].api.pause();},1);
        arm(attempt);
      }
    }
    catch(error){if(attempt===generation)fail(error);}
  }
  function arm(attempt){
    clearTimeout(timer);const i=index;
    const check=()=>{
      if(disposed||!playing||attempt!==generation||index!==i)return;
      const remaining=length(i)-stages[i].api.time;
      if(remaining>.002){timer=setTimeout(check,Math.max(1,remaining*1000));return;}
      advance(i,attempt,Date.now());
    };
    timer=setTimeout(check,Math.max(0,(length(i)-stages[i].api.time)*1000));
  }
  stages.forEach((stage,i)=>{
    stage.api.onFailure=fail;
    stage.api.onSegmentEnd=expectedAt=>advance(i,generation,expectedAt);
    stage.api.onPresented=frame=>{
      if(!playing||index!==i)return;
      const mapped={...frame,segment:i,result_time:packet.range.from+packet.segments[i].offset+frame.media_time-stage.api.packet.source_start,
        display_time:frame.display_epoch-performance.timeOrigin};
      metrics.first_presented_at??=frame.display_epoch;
      metrics.frames.push(mapped);
      if(frame.white&&!metrics.previous_white)metrics.flashes.push(mapped);
      metrics.previous_white=frame.white;
    };
  });
  const api={
    get time(){return packet.segments[index].offset+stages[index].api.time;},
    get paused(){return !playing;},get audioState(){return audio.state;},
    get sourceBuffering(){return stages.every(stage=>stage.api.metrics.source_buffering===true);},
    async play(){
      // Do not invalidate the destination while its child seeks are pending.
      // Pause, replacement seeks and disposal still cancel this queued Play.
      if(pendingSeek){
        const attempt=generation;
        await pendingSeek;
        if(disposed||attempt!==generation)return;
      }
      api.pause();if(disposed)return;
      let attempt=generation;
      if(metrics.finished||api.time>=duration-1/packet.fps-1e-6){const pending=api.seek(0);attempt=generation;await pending;}
      if(disposed||attempt!==generation)return;
      playing=true;outputs(index);
      Object.assign(metrics,{frames:[],flashes:[],beeps:[],cuts:[],segments_shown:[],audio_blocks:[],stalls:0,finished:false,previous_white:false,play_at:Date.now(),play_context_time:audio.currentTime});
      delete metrics.first_presented_at;reveal(index);
      try{
        await stages[index].api.play();
        if(!disposed&&playing&&attempt===generation)arm(attempt);
      }catch(error){if(attempt===generation){fail(error);throw error;}}
    },
    pause(){generation++;playing=false;clearTimeout(timer);clearTimeout(handoffTimer);stages.forEach(s=>s.api.pause());outputs(-1);},
    async seek(at){
      api.pause();if(disposed)return;
      const attempt=++seekGeneration,target=Math.max(0,Math.min(duration-1/packet.fps,Math.round(at*packet.fps)/packet.fps));
      const selected=packet.segments.findLastIndex(segment=>segment.offset<=target+1e-8);
      // A Pause cancels playback, not the chosen seek position. Only a newer
      // seek or disposal can prevent this destination from being committed.
      const operation=Promise.all(stages.map((stage,i)=>stage.api.seek(i===selected?target-packet.segments[i].offset:0))).then(()=>{
        if(disposed||attempt!==seekGeneration)return;
        index=selected;metrics.finished=false;reveal(index);
      });
      pendingSeek=operation;
      try{await operation;}
      catch(error){if(!disposed&&attempt===seekGeneration)fail(error);throw error;}
      finally{if(pendingSeek===operation)pendingSeek=null;}
    },
    snapshot(){return{...stages[index].api.snapshot(),time:api.time,segment:index};},
    dispose(){if(disposed)return;api.pause();disposed=true;cleanup();},
    get playing(){return playing;},
  };
  reveal(0);
  return api;
}

export async function mountCuts(packet,signal,metrics){
  const stages=[],audio=new AudioContext(),frames=[];
  let delegate,closed=false;
  const cleanup=()=>{
    if(closed)return;closed=true;
    for(const frame of frames){frame.contentWindow?.moviestarLiveStage?.dispose();frame.remove();}
    if(window.moviestarCutShared?.audio===audio)delete window.moviestarCutShared;
    if(audio.state!=='closed')audio.close().catch(()=>{});
    signal.removeEventListener('abort',abort);
  };
  let rejectAbort;
  const aborted=new Promise((_,reject)=>rejectAbort=reject);aborted.catch(()=>{});
  const abort=()=>{delegate?.pause();cleanup();rejectAbort(signal.reason??Error('Superseded'));};
  signal.addEventListener('abort',abort,{once:true});
  try{
    signal.throwIfAborted();
    await Promise.race([audio.audioWorklet.addModule('preview_native_meter.mjs'),aborted]);
    await Promise.race([audio.audioWorklet.addModule('preview_native_cut_meter.mjs'),aborted]);
    signal.throwIfAborted();
    const audit=new URLSearchParams(location.search).has('audit');
    // Let the browser downmix speakers (including center-channel dialogue)
    // before the stereo processor; selecting channels 0/1 would discard it.
    const output=new AudioWorkletNode(audio,'cut-output-meter',{channelCount:2,channelCountMode:'explicit',
      channelInterpretation:'speakers',outputChannelCount:[2],processorOptions:{audit}});
    output.connect(audio.destination);
    output.port.onmessage=event=>{
      const data=event.data;
      if(!delegate?.playing||data.context_time<metrics.play_context_time-1e-6)return;
      if(data.kind==='block'){metrics.audio_blocks.push(data);return;}
      const stamp=audio.getOutputTimestamp();
      metrics.beeps.push({...data,display_time:stamp.performanceTime+(data.context_time-stamp.contextTime)*1000,
        timestamp_available:stamp.performanceTime>0&&stamp.contextTime>0});
    };
    const destinations=packet.segments.map(()=>{const gain=audio.createGain();gain.gain.value=0;gain.connect(output);return gain;});
    window.moviestarCutShared={audio,destinations};
    document.documentElement.style.cssText='margin:0;width:100%;height:100%';
    document.body.style.cssText='margin:0;position:relative;width:100%;height:100%;overflow:hidden';
    document.body.replaceChildren();
    const loaded=packet.segments.map((segment,i)=>new Promise((resolve,reject)=>{
      const frame=document.createElement('iframe');frames.push(frame);
      frame.title='Native scene '+(i+1);
      frame.style.cssText=`position:absolute;left:0;top:0;border:0;width:${packet.canvas.width}px;height:${packet.canvas.height}px;opacity:0;pointer-events:none`;
      frame.addEventListener('load',async()=>{
        try{
          const api=frame.contentWindow.moviestarLiveStage;
          if(!api)throw Error('Native cut segment did not initialize');
          await Promise.race([api.ready,aborted]);signal.throwIfAborted();
          if(api.packet.request_id!==packet.request_id||api.packet.state_id!==packet.state_id||!api.packet.cut_segment)
            throw Error('Wrong native cut revision');
          stages[i]={api,frame,gain:destinations[i]};resolve();
        }catch(error){reject(error);}
      },{once:true});
      frame.src='stage?request='+encodeURIComponent(packet.request_id)+'&segment='+i+(audit?'&audit=1':'');
      document.body.append(frame);
    }));
    await Promise.race([Promise.all(loaded),aborted]);signal.throwIfAborted();
    delegate=createCutPlayback({packet,stages,audio,metrics,audit,cleanup,show:active=>frames.forEach((frame,i)=>frame.style.opacity=i===active?'1':'0')});
    return delegate;
  }catch(error){cleanup();throw error;}
}
