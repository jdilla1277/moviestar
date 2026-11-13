// A bounded scene's decoders share transport operations and cancellation.
export function createVideoGroup(entries){
  let generation=0,seekGeneration=0,disposed=false,pendingSeek;
  const cancels=new Set();
  const stop=()=>entries.forEach(({video})=>{video.pause();if(video.playbackRate!==1)video.playbackRate=1;});
  const api={
    async play(){
      const attempt=++generation;
      if(pendingSeek)await pendingSeek;
      if(disposed||attempt!==generation)return;
      try{
        // Start both transports together. Audio-bearing elements feed the shared
        // output clock; genuinely silent pictures retain group synchronization.
        await Promise.all(entries.map(({video})=>video.play()));
        if(disposed||attempt!==generation)return;
        return true;
      }catch(error){if(!disposed&&attempt===generation){api.pause();throw error;}}
    },
    pause(){++generation;stop();},
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
    async seek(at,{presented=false}={}){
      api.pause();const attempt=++seekGeneration;
      for(const cancel of cancels)cancel();
      if(disposed)return false;
      const operation=Promise.all(entries.map(({video,start,fps})=>{
        const target=start+at;
        if(!video.seeking&&Math.abs(video.currentTime-target)<1e-6)return true;
        return new Promise((resolve,reject)=>{
          let settled=false,timer,frameCallback,seeked=false,frameReady=!presented;
          const finish=(value,error)=>{
            if(settled)return;settled=true;clearTimeout(timer);cancels.delete(cancel);
            video.removeEventListener('seeked',done);video.removeEventListener('error',errorDone);
            if(frameCallback!==undefined)video.cancelVideoFrameCallback(frameCallback);
            error?reject(error):resolve(value);
          };
          const cancel=()=>finish(false),done=()=>{seeked=true;if(frameReady)finish(true);},errorDone=()=>finish(false,Error('Native source seek failed'));
          const onFrame=(now,frame)=>{
            if(settled)return;
            if(Math.abs(frame.mediaTime-target)<1/(2*fps)+1e-6){
              frameReady=true;if(seeked)finish(true);
            }else frameCallback=video.requestVideoFrameCallback(onFrame);
          };
          cancels.add(cancel);video.addEventListener('seeked',done);video.addEventListener('error',errorDone);
          timer=setTimeout(()=>finish(false,Error('Native seek timed out')),5000);
          // seeked updates the media clock before the compositor necessarily
          // supplies destination pixels. Held cut canvases need both signals.
          if(presented)frameCallback=video.requestVideoFrameCallback(onFrame);
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
