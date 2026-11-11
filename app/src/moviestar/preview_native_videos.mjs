// A bounded scene's decoders share transport operations and cancellation.
export function createVideoGroup(entries){
  let generation=0,seekGeneration=0,disposed=false,pendingSeek;
  const cancels=new Set(),priming=new Set();
  const stop=()=>entries.forEach(({video})=>{video.pause();if(video.playbackRate!==1)video.playbackRate=1;});
  const api={
    async play(){
      const attempt=++generation;
      if(pendingSeek)await pendingSeek;
      if(disposed||attempt!==generation)return;
      try{
        const clock=entries.find(item=>item.primary)??entries[0],followers=entries.filter(item=>item!==clock);
        const starts=followers.map(item=>item.video.currentTime);
        await Promise.all(followers.map(({video})=>video.play()));
        if(disposed||attempt!==generation)return;
        // A successful play() does not mean a muted picture has reached the
        // compositor. Prime its first moving frame before starting the voice.
        const prepared=await Promise.all(followers.map((item,i)=>{
          if(typeof item.video.requestVideoFrameCallback!=='function'||item.end-item.start<=1/(item.fps??30)+1e-6)return true;
          return new Promise(resolve=>{
            let callback,timer,settled=false;
            const finish=value=>{if(settled)return;settled=true;clearTimeout(timer);item.video.cancelVideoFrameCallback(callback);priming.delete(cancel);resolve(value);};
            const cancel=()=>finish(false);priming.add(cancel);
            const frame=(now,metadata)=>{
              if(settled)return;
              if(metadata.mediaTime>starts[i]+1e-6)finish(true);
              else callback=item.video.requestVideoFrameCallback(frame);
            };
            callback=item.video.requestVideoFrameCallback(frame);
            timer=setTimeout(()=>finish(false),1000);
          });
        }));
        if(disposed||attempt!==generation)return;
        if(prepared.some(value=>!value))throw Error('Native inset did not start; use --mode rendered');
        await clock.video.play();
        if(disposed||attempt!==generation)return;
        return true;
      }catch(error){if(!disposed&&attempt===generation){api.pause();throw error;}}
    },
    pause(){++generation;for(const cancel of priming)cancel();stop();},
    synchronize(clock){
      if(clock.video.paused)return;
      const at=clock.video.currentTime-clock.start;
      for(const item of entries){
        if(item===clock||item.video.paused||item.video.seeking)continue;
        const delta=at-(item.video.currentTime-item.start);
        // Gentle correction keeps muted pictures on the audible source clock.
        // A stalled decoder is a visible failure, never a silently drifting PiP.
        if(Math.abs(delta)>.1)throw Error('Native video clocks lost synchronization; use --mode rendered');
        // Hysteresis avoids resetting the browser's frame scheduler for normal
        // one-frame callback phase differences, or assigning an unchanged rate.
        const rate=Math.abs(delta)>.05?1+Math.max(-.04,Math.min(.04,delta)):
          Math.abs(delta)<.015?1:item.video.playbackRate;
        if(Math.abs(item.video.playbackRate-rate)>1e-6)item.video.playbackRate=rate;
      }
    },
    async seek(at){
      api.pause();const attempt=++seekGeneration;
      for(const cancel of cancels)cancel();
      if(disposed)return false;
      const operation=Promise.all(entries.map(({video,start})=>{
        const target=start+at;
        if(!video.seeking&&Math.abs(video.currentTime-target)<1e-6)return true;
        return new Promise((resolve,reject)=>{
          let settled=false,timer;
          const finish=(value,error)=>{
            if(settled)return;settled=true;clearTimeout(timer);cancels.delete(cancel);
            video.removeEventListener('seeked',done);video.removeEventListener('error',errorDone);
            error?reject(error):resolve(value);
          };
          const cancel=()=>finish(false),done=()=>finish(true),errorDone=()=>finish(false,Error('Native source seek failed'));
          cancels.add(cancel);video.addEventListener('seeked',done);video.addEventListener('error',errorDone);
          timer=setTimeout(()=>finish(false,Error('Native seek timed out')),5000);
          video.currentTime=target+1e-5;
        });
      })).then(values=>!disposed&&attempt===seekGeneration&&values.every(Boolean));
      pendingSeek=operation;
      try{return await operation;}finally{if(pendingSeek===operation)pendingSeek=null;}
    },
    dispose(){
      if(disposed)return;api.pause();disposed=true;++seekGeneration;
      for(const cancel of cancels)cancel();
      for(const {video} of entries){video.removeAttribute('src');video.load();}
    }
  };
  return api;
}
