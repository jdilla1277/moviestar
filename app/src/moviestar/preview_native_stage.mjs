import {render} from './spatial_dom.mjs';

let resolveReady,rejectReady,packet,video,audio,source,meter,frameCallback,stopTimer,cuts,shared,frozenFrame,disposed=false;
const controller=new AbortController(),audit=new URLSearchParams(location.search).has('audit');
const pendingSeeks=new Set();
let lastIndex=-1, playing=false, readyDone=false, metadataReady=false, imageReady=false,playGeneration=0;
const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
// Parent attaches after iframe load; keep early failures observable through ready.
ready.catch(()=>{});
const metrics={frames:[],flashes:[],beeps:[],stalls:0,error:null,finished:false};
const sampleCanvas=document.createElement('canvas');sampleCanvas.width=640;sampleCanvas.height=360;
const pixels=sampleCanvas.getContext('2d',{willReadFrequently:true});
const walk=function*(nodes){for(const node of nodes){yield node;yield*walk(node.children??[]);}};
const key=path=>JSON.stringify(path);
// Give the compositor time to swap prepared scenes before the cut's paint.
const CUT_PAINT_LEAD_MS=8;
async function enableSourceBuffering(signal){
  const service=globalThis.navigator?.serviceWorker;
  if(!service||signal.aborted)return false;
  const worker=new URL('preview_native_worker.mjs',location.href).href;
  let timer,changed,canceled;
  try{
    const controlled=()=>service.controller?.scriptURL===worker;
    const available=new Promise(resolve=>{
      changed=()=>{if(controlled())resolve(true);};
      canceled=()=>resolve(false);
      service.addEventListener('controllerchange',changed);signal.addEventListener('abort',canceled,{once:true});
      timer=setTimeout(canceled,1500);
    });
    const registered=service.register(worker,{scope:'./',type:'module',updateViaCache:'none'})
      .then(()=>controlled()?true:available).catch(()=>false);
    return await Promise.race([registered,available]);
  }finally{
    clearTimeout(timer);service.removeEventListener('controllerchange',changed);signal.removeEventListener('abort',canceled);
  }
}
function fail(error){if(disposed)return;window.moviestarLiveStage?.onFailure?.(error);playing=false;metrics.error=String(error.message??error);if(!metrics.error.includes('--mode rendered'))metrics.error+=' Use --mode rendered if native playback is unsupported.';video?.pause();rejectReady(error);}
function apply(at){
  if(!packet)return;
  const index=Math.max(0,Math.min(packet.samples.length-1,Math.round(at*packet.fps)));
  if(index===lastIndex)return;
  lastIndex=index;
  const next=new Map([...walk(packet.samples[index].nodes)].map(node=>[key(node.path),node]));
  for(const entry of window.moviestarScene.entries){
    const node=next.get(key(entry.node.path));
    entry.node.motion=node.motion;
    if(['text','caption'].includes(entry.node.kind)){
      if(entry.element.textContent!==node.content)entry.element.textContent=node.content;
      entry.node.caption=node.caption;
    }
  }
  window.moviestarScene.applyMotion();
}
function clearFrozenFrame(){if(frozenFrame){frozenFrame.remove();frozenFrame.width=frozenFrame.height=0;frozenFrame=null;}}
function freezeLastFrame(){
  if(frozenFrame)return;
  frozenFrame=document.createElement('canvas');frozenFrame.width=video.videoWidth;frozenFrame.height=video.videoHeight;
  frozenFrame.style.cssText=video.style.cssText;
  frozenFrame.getContext('2d').drawImage(video,0,0);
  video.parentElement.append(frozenFrame);
}
function sourceFrame(){
  pixels.drawImage(frozenFrame??video,0,0,640,360);
  let number=0;
  for(let bit=0;bit<8;bit++)if(pixels.getImageData(bit*16+8,8,1,1).data[0]>128)number|=1<<bit;
  return {number,white:pixels.getImageData(320,180,1,1).data[0]>230};
}
async function checkReady(){
  if(readyDone||disposed||!metadataReady||!imageReady||video.readyState<2||video.seeking||Math.abs(video.currentTime-packet.source_start)>1/packet.fps)return;
  readyDone=true;
  apply(0);
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  metrics.prepared_at=Date.now();metrics.initial_barcode=sourceFrame().number;resolveReady(true);
}
function onFrame(now,metadata){
  if(disposed)return;
  const local=metadata.mediaTime-packet.source_start;
  if(packet.cut_segment&&playing&&metadata.mediaTime>=packet.source_end-1e-6){
    // The media keeps its audio clock until the cut; the overlaid last valid
    // frame prevents a decoded frame beyond the edit from becoming visible.
    if(!frozenFrame){fail(Error('Native cut missed its last expected frame'));return;}
    frameCallback=video.requestVideoFrameCallback(onFrame);return;
  }
  if(packet.cut_segment&&playing&&metadata.mediaTime>=packet.source_end-1/packet.fps-1e-6)freezeLastFrame();
  apply(local);
  if(!playing&&metrics.initial_frame===undefined&&Math.abs(local)<1/packet.fps)metrics.initial_frame=metadata.mediaTime;
  if(playing){
    if(metrics.first_presented_at===undefined)metrics.first_presented_at=performance.timeOrigin+metadata.expectedDisplayTime;
    const sampled=audit?sourceFrame():{};
    metrics.frames.push({media_time:metadata.mediaTime,number:sampled.number,display_time:metadata.expectedDisplayTime});
    if(sampled.white&&!metrics.previous_white)metrics.flashes.push({media_time:metadata.mediaTime,display_time:metadata.expectedDisplayTime});
    metrics.previous_white=sampled.white;
    window.moviestarLiveStage.onPresented?.({...metrics.frames.at(-1),white:sampled.white,display_epoch:performance.timeOrigin+metadata.expectedDisplayTime});
    if(metadata.mediaTime>=packet.source_end-1/packet.fps-1e-6&&!stopTimer){
      if(packet.cut_segment){
        const boundary=performance.timeOrigin+metadata.expectedDisplayTime+1000/packet.fps;
        const attempt=playGeneration;
        stopTimer=setTimeout(()=>{if(!disposed&&playing&&attempt===playGeneration)window.moviestarLiveStage.onSegmentEnd?.(boundary);},Math.max(0,boundary-performance.timeOrigin-performance.now()-CUT_PAINT_LEAD_MS));
        frameCallback=video.requestVideoFrameCallback(onFrame);return;
      }
      stopTimer=setTimeout(()=>{
        metrics.finished=true;metrics.end_time=video.currentTime;
        video.pause();playing=false;
        metrics.quality={totalVideoFrames:video.getVideoPlaybackQuality().totalVideoFrames,droppedVideoFrames:video.getVideoPlaybackQuality().droppedVideoFrames};

      },Math.max(0,metadata.expectedDisplayTime-performance.now()));
    }
  }
  checkReady();
  frameCallback=video.requestVideoFrameCallback(onFrame);
}
function complete(){
  if(video.currentTime>=packet.source_end-1/packet.fps){
    metrics.finished=true;metrics.quality=video.getVideoPlaybackQuality().toJSON?.()??{
      totalVideoFrames:video.getVideoPlaybackQuality().totalVideoFrames,droppedVideoFrames:video.getVideoPlaybackQuality().droppedVideoFrames};
    metrics.end_time=video.currentTime;playing=false;
  }
}
async function initialize(){
  const params=new URLSearchParams(location.search),request=params.get('request');
  const response=await fetch('packet?request='+encodeURIComponent(request)+(params.has('segment')?'&segment='+encodeURIComponent(params.get('segment')):''),{cache:'no-store',signal:controller.signal});
  if(!response.ok)throw Error('The requested live packet is no longer retained.');
  packet=await response.json();if(disposed)return;
  if(packet.segments){
    Object.assign(window.frameElement.style,{width:packet.canvas.width+'px',height:packet.canvas.height+'px'});
    const {mountCuts}=await import('./preview_native_cuts.mjs');
    cuts=await mountCuts(packet,controller.signal,metrics);if(disposed){cuts.dispose();return;}
    metrics.source_buffering=cuts.sourceBuffering;metrics.prepared_at=Date.now();resolveReady(true);return;
  }
  metrics.source_buffering=await enableSourceBuffering(controller.signal);if(disposed)return;
  Object.assign(window.frameElement.style,{width:packet.canvas.width+'px',height:packet.canvas.height+'px'});
  const measured=await render({...packet.initial,paint_dir:'native',live_dom:true});
  if(disposed)return;
  const bad=measured.diagnostics.find(item=>item.severity==='error');
  if(bad)throw Error(bad.code);
  const entries=new Map(window.moviestarScene.entries.map(entry=>[key(entry.node.path),entry]));
  const images=[];
  for(const asset of packet.media){
    const {element}=entries.get(key(asset.path));
    const computed=getComputedStyle(element);
    if(!['relative','absolute'].includes(computed.position)||['inline','contents','none'].includes(computed.display))throw Error('Unsupported native media containing block.');
    const media=document.createElement(asset.kind==='video'?'video':'img');
    Object.assign(media.style,{position:'absolute',left:'0',top:'0',width:'100%',height:'100%',objectFit:asset.object_fit,borderRadius:computed.borderRadius});
    element.append(media);
    if(asset.kind==='image'){
      media.src='assets/'+asset.asset;images.push(media.decode());
    }else{
      video=media;video.preload='metadata';video.playsInline=true;
      video.addEventListener('error',()=>fail(Error('Source video could not load.')));
      video.addEventListener('canplay',checkReady);video.addEventListener('seeked',checkReady);
      video.addEventListener('loadedmetadata',()=>{
        metadataReady=true;
        if(!video.videoWidth||!video.videoHeight)fail(Error('Source has no decodable video.'));
        checkReady();
      });
      video.addEventListener('waiting',()=>{if(playing)metrics.stalls++;});
      video.addEventListener('pause',complete);
      video.addEventListener('ended',complete);
      video.src=`assets/${asset.asset}?native_video=1#t=${packet.source_start}${packet.cut_segment?'':','+packet.source_end}`;
      if(typeof video.requestVideoFrameCallback!=='function')throw Error('Native preview needs video-frame callbacks; use --mode rendered.');
      frameCallback=video.requestVideoFrameCallback(onFrame);
    }
  }
  shared=packet.cut_segment?window.parent.moviestarCutShared:null;
  if(packet.cut_segment&&!shared)throw Error('Native cut audio context is unavailable');
  audio=shared?.audio??new AudioContext();
  if(!audio.audioWorklet||typeof audio.getOutputTimestamp!=='function')throw Error('Native preview needs the browser audio output clock; use --mode rendered.');
  if(!shared)await audio.audioWorklet.addModule('preview_native_meter.mjs');
  if(disposed){if(!shared)await audio.close();return;}
  source=audio.createMediaElementSource(video);meter=new AudioWorkletNode(audio,'cue-meter');
  source.connect(meter);meter.connect(shared?shared.destinations[Number(new URLSearchParams(location.search).get('segment'))]:audio.destination);
  meter.port.onmessage=event=>{
    if(!playing)return;
    const stamp=audio.getOutputTimestamp();
    metrics.beeps.push({context_time:event.data.context_time,
      display_time:stamp.performanceTime+(shared?window.parent.performance.timeOrigin-performance.timeOrigin:0)+(event.data.context_time-stamp.contextTime)*1000,
      timestamp_available:stamp.performanceTime>0&&stamp.contextTime>0,rms:event.data.rms});
  };
  if(metadataReady&&Math.abs(video.currentTime-packet.source_start)>1e-6)video.currentTime=packet.source_start;
  await Promise.all(images);imageReady=true;checkReady();
}
async function seekTo(at){
  await ready;
  if(disposed)return false;
  for(const cancel of pendingSeeks)cancel();
  const local=Math.max(0,Math.min((packet.samples.length-1)/packet.fps,Math.round(at*packet.fps)/packet.fps));
  const target=packet.source_start+local;
  if(Math.abs(video.currentTime-target)<1e-6){clearFrozenFrame();apply(local);return true;}
  return await new Promise((resolve,reject)=>{
    let settled=false,timer;
    const finish=(applied,error)=>{
      if(settled)return;settled=true;clearTimeout(timer);
      video.removeEventListener('seeked',done);pendingSeeks.delete(cancel);
      error?reject(error):resolve(applied);
    };
    const cancel=()=>finish(false);
    const done=()=>{
      clearTimeout(timer);video.removeEventListener('seeked',done);
      if(disposed){finish(false);return;}
      clearFrozenFrame();apply(local);
      requestAnimationFrame(()=>requestAnimationFrame(()=>finish(true)));
    };
    pendingSeeks.add(cancel);
    timer=setTimeout(()=>finish(false,Error('Native seek timed out')),5000);
    // Land just after the output-grid instant: container PTS rounded to
    // microseconds can otherwise select the previous frame at an exact cut.
    video.addEventListener('seeked',done,{once:true});video.currentTime=target+1e-5;
  });
}
window.moviestarLiveStage={ready,metrics,
  get packet(){return packet;},
  get time(){if(cuts)return cuts.time;return video?Math.max(0,video.currentTime-packet.source_start):0;},
  get paused(){return cuts?cuts.paused:video?.paused??true;},
  get audioState(){return cuts?cuts.audioState:audio?.state;},
  get error(){return metrics.error;},
  async play(){
    if(cuts)return cuts.play();
    this.pause();
    const attempt=playGeneration;
    const active=()=>!disposed&&attempt===playGeneration;
    try{
      if(!packet.cut_segment&&(metrics.finished||this.time>=(packet.samples.length-1)/packet.fps-1e-6))await seekTo(0);
      if(!active())return;
      metrics.frames=[];metrics.flashes=[];metrics.beeps=[];metrics.stalls=0;metrics.finished=false;metrics.previous_white=false;delete metrics.first_presented_at;
      metrics.play_at=Date.now();
      await audio.resume();
      if(!active())return;
      // resume() may precede output-device startup. Pause/restart/revision
      // replacement must cancel this wait as well as the resume() promise.
      const deviceDeadline=performance.now()+2000;
      while(audio.getOutputTimestamp().contextTime<=0||audio.getOutputTimestamp().performanceTime<=0){
        if(!active())return;
        if(performance.now()>deviceDeadline)throw Error('Audio output clock did not start');
        await new Promise(resolve=>setTimeout(resolve,10));
      }
      if(!active())return;
      metrics.device_ready_at=Date.now();if(packet.cut_segment&&packet.samples.length===1)freezeLastFrame();playing=true;
      await video.play();
    }catch(error){if(active()){fail(error);throw error;}}
  },
  pause(){if(cuts){cuts.pause();return;}++playGeneration;playing=false;clearTimeout(stopTimer);stopTimer=null;video?.pause();},
  async seek(at){if(cuts)return cuts.seek(at);this.pause();try{if(await seekTo(at))metrics.finished=false;}catch(error){fail(error);throw error;}},
  snapshot(){
    if(cuts)return cuts.snapshot();
    return {request_id:packet.request_id,state_id:packet.state_id,time:this.time,source:sourceFrame(),
      boxes:window.moviestarScene.entries.map(({node,element})=>{
        const r=element.getBoundingClientRect();let opacity=1;
        for(let ancestor=element;ancestor;ancestor=ancestor.parentElement)opacity*=Number(getComputedStyle(ancestor).opacity);
        return {path:node.path,kind:node.kind,text:element.textContent,rect:{x:r.x,y:r.y,width:r.width,height:r.height},opacity,text_lines:['text','caption'].includes(node.kind)?window.moviestarScene.textLines(element):undefined};
      })};
  },
  dispose(){
    if(disposed)return;this.pause();disposed=true;cuts?.dispose();for(const cancel of pendingSeeks)cancel();controller.abort();
    if(frameCallback)video?.cancelVideoFrameCallback(frameCallback);
    if(video){video.removeAttribute('src');video.load();}clearTimeout(stopTimer);
    clearFrozenFrame();source?.disconnect();meter?.disconnect();meter?.port.close();
    if(audio&&!shared&&audio.state!=='closed')audio.close().catch(()=>{});
    rejectReady(Error('Superseded'));
  }
};
initialize().catch(fail);
