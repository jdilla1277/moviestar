// A bounded scene's decoders share transport operations and cancellation.
export function createVideoGroup(entries){
  let generation=0,seekGeneration=0,disposed=false,pendingSeek,preparingCancel;
  const cancels=new Set();
  const stop=()=>entries.forEach(({video,rate=1})=>{video.pause();if(video.playbackRate!==rate)video.playbackRate=rate;});
  const api={
    async measureStartup(){
      if(entries.length!==1)throw Error('Picture startup measurement needs one video');
      const {video,start,rate=1,fps}=entries[0],requested=performance.now();
      const before=video.getVideoPlaybackQuality();let cancel;
      const moving=new Promise((resolve,reject)=>{
        let callback,timer,settled=false;
        const finish=(point,error)=>{
          if(settled)return;settled=true;clearTimeout(timer);cancels.delete(cancel);
          if(preparingCancel===cancel)preparingCancel=null;
          if(callback!==undefined)video.cancelVideoFrameCallback(callback);
          error?reject(error):resolve(point);
        };
        cancel=()=>finish(null);preparingCancel=cancel;cancels.add(cancel);
        const observe=(now,frame)=>{
          const delta=frame.mediaTime-start;
          if(delta>1e-6&&delta<4/fps+1e-6)finish(frame);
          else callback=video.requestVideoFrameCallback(observe);
        };
        callback=video.requestVideoFrameCallback(observe);
        timer=setTimeout(()=>finish(null,Error('Native picture startup measurement timed out')),2000);
      });
      moving.catch(()=>{});
      try{
        const starting=api.play(),attempt=generation;
        if(!await starting){cancel();return null;}
        const frame=await moving;
        if(!frame||disposed||attempt!==generation)return null;
        const lead=Math.max(0,(frame.expectedDisplayTime-requested-(frame.mediaTime-start)/rate*1000)/1000);
        if(!Number.isFinite(lead)||lead>.25)throw Error('Native picture startup exceeds its bounded preparation profile');
        const after=video.getVideoPlaybackQuality();
        api.pause();if(!await api.seek(0,{presented:true}))return null;
        return{lead,requested_at:performance.timeOrigin+requested,media_time:frame.mediaTime,
          display_time:frame.expectedDisplayTime,preparation_frames:after.totalVideoFrames-before.totalVideoFrames,
          preparation_drops:after.droppedVideoFrames-before.droppedVideoFrames};
      }catch(error){cancel();api.pause();throw error;}
    },
    async play(){
      const attempt=++generation;
      if(pendingSeek)await pendingSeek;
      if(disposed||attempt!==generation)return;
      try{
        for(const {video,rate=1} of entries)if(video.playbackRate!==rate)video.playbackRate=rate;
        // Start both transports together. Audio-bearing elements feed the shared
        // output clock; genuinely silent pictures retain group synchronization.
        await Promise.all(entries.map(({video})=>video.play()));
        if(disposed||attempt!==generation)return;
        return true;
      }catch(error){if(!disposed&&attempt===generation){api.pause();throw error;}}
    },
    pause(){++generation;preparingCancel?.();stop();},
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
      const replacingSeek=Boolean(pendingSeek);
      api.pause();const attempt=++seekGeneration;
      for(const cancel of cancels)cancel();
      if(disposed)return false;
      const operation=Promise.all(entries.map(({video,start,fps,rate=1})=>{
        const target=start+at*rate,seekTarget=target+1e-5;
        if((!presented||!replacingSeek)&&!video.seeking&&(Math.abs(video.currentTime-target)<1e-6||Math.abs(video.currentTime-seekTarget)<1e-6))return true;
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
            // Source trims can begin between frame timestamps. Wait for
            // the frame containing the actual seek time, not its neighbor.
            const delta=seekTarget-frame.mediaTime;
            if(delta>=-1e-6&&delta<1/fps-1e-6){
              frameReady=true;if(seeked)finish(true);
            }else frameCallback=video.requestVideoFrameCallback(onFrame);
          };
          cancels.add(cancel);video.addEventListener('seeked',done);video.addEventListener('error',errorDone);
          timer=setTimeout(()=>finish(false,Error('Native seek timed out')),5000);
          // seeked updates the media clock before the compositor necessarily
          // supplies destination pixels. Held cut canvases need both signals.
          if(presented)frameCallback=video.requestVideoFrameCallback(onFrame);
          video.currentTime=seekTarget;
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
