// Timed canonical DOM scenes need no dummy video or encoded still-image clip.
export function createSceneClock({packet,metrics,apply,present=()=>{},end=()=>{},
 onError=()=>{},audio=null,resume=async()=>{},drain=async()=>{},contextTime=()=>undefined,
 now=()=>performance.now(),request=fn=>requestAnimationFrame(fn),cancel=id=>cancelAnimationFrame(id)}){
 const duration=packet.range.to-packet.range.from,last=(packet.samples.length-1)/packet.fps;
 let offset=0,started=0,playing=false,disposed=false,generation=0,callback,lastFrame=-1;
 const prepared=()=>audio?.snapshot().prepared;
 function paint(at,stamp){
  const index=Math.min(packet.samples.length-1,Math.max(0,Math.floor(at*packet.fps+1e-7)));
  if(index===lastFrame)return;
  lastFrame=index;apply(index/packet.fps);
  const frame={clock:'scene',media_time:index/packet.fps,result_time:packet.range.from+index/packet.fps,display_time:stamp,
   display_epoch:performance.timeOrigin+stamp,white:false};
  metrics.first_presented_at??=frame.display_epoch;metrics.frames.push(frame);present(frame);
 }
 async function finish(){
  api.pause();const stopped=generation;
  apply(last);await drain();
  if(disposed||generation!==stopped)return;
  metrics.finished=true;metrics.finished_at=Date.now();end();
 }
 function tick(attempt){
  if(disposed||!playing||attempt!==generation)return;
  try{
   const at=api.time;paint(at,now());
   if(at>=duration){finish().catch(fail);return;}
   callback=request(()=>tick(attempt));
  }catch(error){fail(error);}
 }
 function fail(error){api.pause();metrics.error=String(error.message??error);onError(error);}
 const api={
  get time(){
   if(!playing)return offset;
   const audible=prepared()?audio.outputTimeAt(performance.timeOrigin+now()):null;
   if(prepared()&&!Number.isFinite(audible)){
    if(audio.snapshot().time>=duration-1/48000)return duration;
    throw Error('Scene audio output clock is unavailable');
   }
   const at=Number.isFinite(audible)?audible:offset+(now()-started)/1000;
   return Math.max(0,Math.min(duration,at));
  },
  get paused(){return !playing;},
  async play(){
   if(disposed)return;
   api.pause();if(!packet.cut_segment&&(metrics.finished||offset>=last-1e-6)){offset=0;apply(0);}
   const attempt=generation;
   try{await resume();
   if(disposed||attempt!==generation)return;
   await audio?.play(offset);
   if(disposed||attempt!==generation)return;
   started=now();playing=true;lastFrame=-1;
   Object.assign(metrics,{frames:[],audio_blocks:[],beeps:[],stalls:0,finished:false,play_at:Date.now(),play_context_time:contextTime()});
   delete metrics.pause_context_time;delete metrics.first_presented_at;
   if(prepared())metrics.prepared_audio=audio.snapshot();else delete metrics.prepared_audio;
   tick(attempt);
   }catch(error){if(!disposed&&attempt===generation)fail(error);throw error;}
  },
  pause(){
   if(playing){try{offset=api.time;}catch(error){metrics.error=String(error.message??error);}
    metrics.pause_context_time=contextTime();}
   ++generation;playing=false;audio?.pause();
   if(callback!==undefined)cancel(callback);callback=undefined;
  },
  async seek(at){
   if(disposed)return;
   api.pause();offset=Math.max(0,Math.min(last,Math.round(at*packet.fps)/packet.fps));
   audio?.seek(offset);apply(offset);lastFrame=-1;metrics.finished=false;
  },
  holdEnd(){if(disposed)return;api.pause();apply(last);},
  dispose(){if(disposed)return;api.pause();disposed=true;}
 };
 return api;
}
