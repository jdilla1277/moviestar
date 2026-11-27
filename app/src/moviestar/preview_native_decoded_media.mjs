import {createFrameQueue} from './preview_native_decode.mjs';

// Canvas-backed media transport for packet-indexed three-picture scenes. The PCM
// output clock determines the source image; no independently starting media
// element clock participates in this route.
export function createDecodedMedia(asset,{offset,clock,signal,onFailure}){
 const canvas=document.createElement('canvas'),index={...asset.video_decode,
  config:{...asset.video_decode.config,description:new Uint8Array(asset.video_decode.config.description)}};
 const last=index.last??index.end-1/index.fps;
 canvas.width=index.config.codedWidth;canvas.height=index.config.codedHeight;
 let paused=true,seeking=false,at=index.start,readyState=0,src='',disposed=false,generation=0,playGeneration=0,frameId=0,
  callbackId=0,total=0,drops=0,lastTimestamp,shown,seekWork,cached,lastTick,uncertainty=0,refilling;
 const callbacks=new Map();
 const queue=createFrameQueue(index,{maxFrames:Math.min(8,Math.floor(128*1024*1024/(canvas.width*canvas.height*4))),fetchPacket:async(packet,cancel)=>{
  if(!cached||packet.offset<cached.left||packet.offset+packet.size>cached.right){
   const left=packet.offset,right=left+Math.max(131072,packet.size);
   const response=await fetch(src.split('#')[0],{headers:{Range:`bytes=${left}-${right-1}`},signal:cancel});
   if(response.status!==206)throw Error('Native packet range is unavailable');
   cached={left,right,data:new Uint8Array(await response.arrayBuffer())};
  }
  return cached.data.subarray(packet.offset-cached.left,packet.offset-cached.left+packet.size);
 }});
 const localTime=()=>{
  if(paused)return at;
  const time=clock();return Number.isFinite(time)?Math.max(index.start,Math.min(index.end,index.start+time-offset)):at;
 };
 function notify(frame){
  total++;shown={mediaTime:frame.timestamp/1e6,presentedFrames:total,expectedDisplayTime:performance.now(),presentationTime:performance.now()};
  const pending=[...callbacks.values()];callbacks.clear();for(const callback of pending)callback(performance.now(),shown);
 }
 function draw(target){
  const frame=queue.frameAt(target);
  if(frame.timestamp===lastTimestamp)return;
  const previous=lastTimestamp;
  if(!paused&&previous!==undefined&&frame.timestamp-previous>1e6/index.fps+3){
   drops+=Math.max(1,Math.round((frame.timestamp-previous)*index.fps/1e6)-1);
   throw Error('Native decoded picture missed its presentation deadline');
  }
  canvas.getContext('2d').drawImage(frame,0,0);lastTimestamp=frame.timestamp;notify(frame);
 }
 function fail(error){if(disposed)return;onFailure(error);}
 async function seek(target){
  const attempt=++generation;++playGeneration;refilling=null;target=Math.max(index.start,Math.min(last,target));paused=true;cancelAnimationFrame(frameId);seeking=true;at=target;lastTimestamp=undefined;
  try{
   if(!await queue.seek(target)||disposed||attempt!==generation)return;
   seeking=false;readyState=4;draw(target);canvas.dispatchEvent(new Event('seeked'));canvas.dispatchEvent(new Event('canplay'));
  }catch(error){if(!disposed&&attempt===generation)fail(error);}
 }
 function refill(target,attempt){
  if(refilling)return;
  const work=queue.ensure(target);refilling=work;
  work.catch(error=>{if(!paused&&attempt===playGeneration)fail(error);}).finally(()=>{if(refilling===work)refilling=null;});
 }
 function tick(stamp){
  if(disposed||paused)return;
  const attempt=playGeneration;
  if(lastTick!==undefined)uncertainty=Math.max(uncertainty,stamp-lastTick);lastTick=stamp;
  try{
   const target=Math.min(last,localTime());
   at=localTime();draw(target);
   if(disposed||paused||attempt!==playGeneration)return;
   if(localTime()>=index.end){paused=true;at=index.end;canvas.dispatchEvent(new Event('ended'));return;}
   refill(target,attempt);frameId=requestAnimationFrame(tick);
  }catch(error){if(!paused&&attempt===playGeneration)fail(error);}
 }
 Object.defineProperties(canvas,{
  videoWidth:{get:()=>canvas.width},videoHeight:{get:()=>canvas.height},
  currentTime:{get:localTime,set:value=>{seekWork=seek(value);}},
  paused:{get:()=>paused},seeking:{get:()=>seeking},readyState:{get:()=>readyState},
  currentSrc:{get:()=>src?new URL(src,location.href).href:''},src:{get:()=>src,set:value=>{
   src=value;queueMicrotask(()=>{if(disposed)return;readyState=1;canvas.dispatchEvent(new Event('loadedmetadata'));seekWork=seek(at);});
  }},
 });
 canvas.playbackRate=1;canvas.muted=true;
 canvas.play=async()=>{const attempt=playGeneration;if(seekWork)await seekWork;if(disposed||attempt!==playGeneration)return;paused=false;lastTick=undefined;uncertainty=0;frameId=requestAnimationFrame(tick);};
 canvas.pause=()=>{++playGeneration;if(paused)return;at=localTime();paused=true;cancelAnimationFrame(frameId);canvas.dispatchEvent(new Event('pause'));};
 canvas.requestVideoFrameCallback=callback=>{const id=++callbackId;callbacks.set(id,callback);return id;};
 canvas.cancelVideoFrameCallback=id=>callbacks.delete(id);
 // Internal media-controller compatibility: these are presentation counts
 // and explicit deadline misses, not browser video-element quality counters.
 canvas.getVideoPlaybackQuality=()=>({totalVideoFrames:total,droppedVideoFrames:drops,display_uncertainty_ms:uncertainty,presentation_source:'animation-frame-submission'});
 canvas.load=()=>{disposed=true;++generation;cancelAnimationFrame(frameId);callbacks.clear();queue.dispose();cached=null;};
 canvas.decodedMetrics=queue.metrics;
 signal.addEventListener('abort',canvas.load,{once:true});
 return canvas;
}
