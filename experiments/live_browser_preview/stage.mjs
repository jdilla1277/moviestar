import {render} from './spatial_dom.mjs';

let resolveReady,rejectReady,packet,video,audio,source,meter,frameCallback,stopTimer,disposed=false;
const controller=new AbortController(),audit=new URLSearchParams(location.search).has('audit');
let lastIndex=-1, playing=false, readyDone=false, metadataReady=false, imageReady=false;
const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
// Parent attaches after iframe load; keep early failures observable through ready.
ready.catch(()=>{});
const metrics={frames:[],flashes:[],beeps:[],stalls:0,error:null,finished:false};
const sampleCanvas=document.createElement('canvas');sampleCanvas.width=640;sampleCanvas.height=360;
const pixels=sampleCanvas.getContext('2d',{willReadFrequently:true});
const walk=function*(nodes){for(const node of nodes){yield node;yield*walk(node.children??[]);}};
const key=path=>JSON.stringify(path);
function fail(error){metrics.error=String(error.message??error);video?.pause();rejectReady(error);}
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
function sourceFrame(){
  pixels.drawImage(video,0,0,640,360);
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
  apply(local);
  if(!playing&&metrics.initial_frame===undefined&&Math.abs(local)<1/packet.fps)metrics.initial_frame=metadata.mediaTime;
  if(playing){
    if(metrics.first_presented_at===undefined)metrics.first_presented_at=performance.timeOrigin+metadata.expectedDisplayTime;
    const sampled=audit?sourceFrame():{};
    metrics.frames.push({media_time:metadata.mediaTime,number:sampled.number,display_time:metadata.expectedDisplayTime});
    if(sampled.white&&!metrics.previous_white)metrics.flashes.push({media_time:metadata.mediaTime,display_time:metadata.expectedDisplayTime});
    metrics.previous_white=sampled.white;
    if(metadata.mediaTime>=packet.source_end-1/packet.fps-1e-6&&!stopTimer){
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
  const request=new URLSearchParams(location.search).get('request');
  const response=await fetch('packet?request='+encodeURIComponent(request),{cache:'no-store',signal:controller.signal});
  if(!response.ok)throw Error('The requested live packet is no longer retained.');
  packet=await response.json();if(disposed)return;
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
      video=media;video.preload='auto';video.playsInline=true;
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
      video.src=`assets/${asset.asset}#t=${packet.source_start},${packet.source_end}`;
      frameCallback=video.requestVideoFrameCallback(onFrame);
    }
  }
  audio=new AudioContext();
  await audio.audioWorklet.addModule('meter.mjs');
  if(disposed){await audio.close();return;}
  source=audio.createMediaElementSource(video);meter=new AudioWorkletNode(audio,'cue-meter');
  source.connect(meter);meter.connect(audio.destination);
  meter.port.onmessage=event=>{
    if(!playing)return;
    const stamp=audio.getOutputTimestamp();
    metrics.beeps.push({context_time:event.data.context_time,
      display_time:stamp.performanceTime+(event.data.context_time-stamp.contextTime)*1000,
      timestamp_available:stamp.performanceTime>0&&stamp.contextTime>0,rms:event.data.rms});
  };
  await Promise.all(images);imageReady=true;checkReady();
}
window.moviestarLiveStage={ready,metrics,
  get packet(){return packet;},
  get time(){return video?Math.max(0,video.currentTime-packet.source_start):0;},
  get paused(){return video?.paused??true;},
  get audioState(){return audio?.state;},
  get error(){return metrics.error;},
  async play(){
    if(metrics.finished)await this.seek(0);
    metrics.frames=[];metrics.flashes=[];metrics.beeps=[];metrics.stalls=0;metrics.finished=false;metrics.previous_white=false;delete metrics.first_presented_at;
    clearTimeout(stopTimer);stopTimer=null;metrics.play_at=Date.now();
    // Finish audio-device startup before starting the shared native media clock.
    await audio.resume();
    // resume() can resolve before the physical output clock has started. Wait
    // for a usable timestamp while the worklet outputs silence, before video.
    const deviceDeadline=performance.now()+2000;
    while(audio.getOutputTimestamp().contextTime<=0||audio.getOutputTimestamp().performanceTime<=0){
      if(performance.now()>deviceDeadline)throw Error('Audio output clock did not start');
      await new Promise(resolve=>setTimeout(resolve,10));
    }
    metrics.device_ready_at=Date.now();playing=true;
    await video.play();
  },
  pause(){video?.pause();playing=false;clearTimeout(stopTimer);stopTimer=null;},
  async seek(at){
    await ready;
    this.pause();
    const local=Math.max(0,Math.min((packet.samples.length-1)/packet.fps,Math.round(at*packet.fps)/packet.fps));
    const target=packet.source_start+local;
    if(Math.abs(video.currentTime-target)<1e-6){apply(local);return;}
    await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(Error('Native seek timed out')),5000);
      video.addEventListener('seeked',()=>{clearTimeout(timer);apply(local);requestAnimationFrame(()=>requestAnimationFrame(resolve));},{once:true});
      video.currentTime=target;
    });
  },
  snapshot(){
    return {request_id:packet.request_id,state_id:packet.state_id,time:this.time,source:sourceFrame(),
      boxes:window.moviestarScene.entries.map(({node,element})=>{
        const r=element.getBoundingClientRect();let opacity=1;
        for(let ancestor=element;ancestor;ancestor=ancestor.parentElement)opacity*=Number(getComputedStyle(ancestor).opacity);
        return {path:node.path,kind:node.kind,text:element.textContent,rect:{x:r.x,y:r.y,width:r.width,height:r.height},opacity,text_lines:['text','caption'].includes(node.kind)?window.moviestarScene.textLines(element):undefined};
      })};
  },
  dispose(){
    if(disposed)return;disposed=true;controller.abort();video?.pause();
    if(frameCallback)video?.cancelVideoFrameCallback(frameCallback);
    if(video){video.removeAttribute('src');video.load();}clearTimeout(stopTimer);
    if(audio&&audio.state!=='closed')audio.close().catch(()=>{});
    rejectReady(Error('Superseded'));
  }
};
initialize().catch(fail);
