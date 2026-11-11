import {render} from './spatial_dom.mjs';

let resolveReady,rejectReady,packet,video,audio,meter,frameCallback,stopTimer,cuts,shared,frozenFrame,disposed=false;
const controller=new AbortController(),audit=new URLSearchParams(location.search).has('audit');
const pendingSeeks=new Set();
let lastIndex=-1, playing=false, readyDone=false, metadataReady=false, imageReady=false,finishing=false,playGeneration=0;
let videoGroup;
const videos=[];
const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
// Parent attaches after iframe load; keep early failures observable through ready.
ready.catch(()=>{});
const metrics={frames:[],flashes:[],beeps:[],stalls:0,error:null,finished:false};
const sampleCanvas=document.createElement('canvas');sampleCanvas.width=128;sampleCanvas.height=17;
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
function fail(error){if(disposed)return;window.moviestarLiveStage?.onFailure?.(error);window.moviestarLiveStage?.pause();playing=false;metrics.error=String(error.message??error);if(!metrics.error.includes('--mode rendered'))metrics.error+=' Use --mode rendered if native playback is unsupported.';video?.pause();rejectReady(error);}
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
function clearFrozenFrame(){if(frozenFrame){frozenFrame.remove();frozenFrame.width=frozenFrame.height=0;frozenFrame=null;}for(const item of videos){if(item.frozen){item.frozen.remove();item.frozen.width=item.frozen.height=0;item.frozen=null;}}}
function freezeLastFrame(){
  if(frozenFrame)return;
  frozenFrame=document.createElement('canvas');frozenFrame.width=video.videoWidth;frozenFrame.height=video.videoHeight;
  frozenFrame.style.cssText=video.style.cssText;
  frozenFrame.getContext('2d').drawImage(videos.find(item=>item.primary)?.endFrame??video,0,0);
  video.parentElement.append(frozenFrame);
}
function freezeFollower(item){
  if(item.frozen)return;
  const media=item.video,canvas=document.createElement('canvas');canvas.width=media.videoWidth;canvas.height=media.videoHeight;
  canvas.style.cssText=media.style.cssText;canvas.getContext('2d').drawImage(item.endFrame??media,0,0);media.parentElement.append(canvas);item.frozen=canvas;
}
function sourceFrame(item){
  const image=item?(item.primary?(frozenFrame??item.video):(item.frozen??item.video)):(frozenFrame??video);
  const x=(image.videoWidth??image.width)/640,y=(image.videoHeight??image.height)/360;
  // Audit only needs the barcode strip and center cue. Read back one small
  // tile, rather than a full frame plus nine separate GPU readbacks per video.
  pixels.drawImage(image,0,0,128*x,16*y,0,0,128,16);
  pixels.drawImage(image,320*x,180*y,x,y,0,16,1,1);
  const data=pixels.getImageData(0,0,128,17).data;
  let number=0;
  for(let bit=0;bit<8;bit++)if(data[(8*128+bit*16+8)*4]>128)number|=1<<bit;
  return {number,white:data[16*128*4]>230};
}
async function checkReady(){
  if(readyDone||disposed||!metadataReady||!imageReady||video.readyState<2||video.seeking||Math.abs(video.currentTime-packet.source_start)>1/packet.fps)return;
  if(videos.some(item=>!item.metadata||!item.firstFrame||item.video.readyState<2||item.video.seeking||Math.abs(item.video.currentTime-item.start)>1/packet.fps))return;
  readyDone=true;
  try{
    // rVFC may omit the last valid presentation even with zero decoder drops.
    // Prepare its pixels before publishing a cut scene, then return both
    // decoders to their first frames. No output clip or persistent cache.
    if(packet.cut_segment){
      if(!await videoGroup.seek((packet.samples.length-1)/packet.fps)||disposed)return;
      for(const item of videos){
        const frame=document.createElement('canvas');frame.width=item.video.videoWidth;frame.height=item.video.videoHeight;
        frame.getContext('2d').drawImage(item.video,0,0);item.endFrame=frame;
      }
      if(!await videoGroup.seek(0)||disposed)return;
    }
    apply(0);
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    if(disposed)return;
    metrics.prepared_at=Date.now();metrics.initial_barcode=sourceFrame().number;resolveReady(true);
  }catch(error){fail(error);}
}
function onFrame(now,metadata){
  if(disposed)return;
  const running=playing&&!video.paused;
  const primary=videos.find(item=>item.primary);
  if(primary){primary.firstFrame=true;primary.frame={media_time:metadata.mediaTime,display_time:metadata.expectedDisplayTime,presented_frames:metadata.presentedFrames};}
  if(running&&primary)recordVideoQuality(primary,'frame');
  if(playing&&videoGroup){try{videoGroup.synchronize(videos.find(item=>item.primary));}catch(error){fail(error);return;}}
  const local=metadata.mediaTime-packet.source_start;
  if(packet.cut_segment&&running&&!stopTimer){
    // Schedule from the first presentation's clock, independently of whether
    // JS receives the final-frame callback. The prepared final pixels cover
    // an outgoing decoder that advances while the next scene is revealed.
    const boundary=performance.timeOrigin+metadata.expectedDisplayTime+(packet.source_end-metadata.mediaTime)*1000;
    const attempt=playGeneration;
    stopTimer=setTimeout(()=>{if(!disposed&&playing&&attempt===playGeneration)window.moviestarLiveStage.onSegmentEnd?.(boundary);},Math.max(0,boundary-performance.timeOrigin-performance.now()-CUT_PAINT_LEAD_MS));
  }
  if(packet.cut_segment&&running&&metadata.mediaTime>=packet.source_end-1e-6){
    // The media keeps its audio clock until the cut; the overlaid last valid
    // frame prevents a decoded frame beyond the edit from becoming visible.
    freezeLastFrame();
    frameCallback=video.requestVideoFrameCallback(onFrame);return;
  }
  if(packet.cut_segment&&running&&metadata.mediaTime>=packet.source_end-1/packet.fps-1e-6)freezeLastFrame();
  apply(local);
  if(!running&&metrics.initial_frame===undefined&&Math.abs(local)<1/packet.fps)metrics.initial_frame=metadata.mediaTime;
  if(running){
    if(metrics.first_presented_at===undefined)metrics.first_presented_at=performance.timeOrigin+metadata.expectedDisplayTime;
    const sampled=audit?sourceFrame():{};
    if(audit&&primary){metrics.video_frames??=[];metrics.video_frames.push({path:primary.path,...primary.frame,number:sampled.number});}
    metrics.frames.push({media_time:metadata.mediaTime,number:sampled.number,display_time:metadata.expectedDisplayTime,presented_frames:metadata.presentedFrames});
    if(sampled.white&&!metrics.previous_white)metrics.flashes.push({media_time:metadata.mediaTime,display_time:metadata.expectedDisplayTime,presented_frames:metadata.presentedFrames});
    metrics.previous_white=sampled.white;
    window.moviestarLiveStage.onPresented?.({...metrics.frames.at(-1),white:sampled.white,display_epoch:performance.timeOrigin+metadata.expectedDisplayTime});
    if(!packet.cut_segment&&metadata.mediaTime>=packet.source_end-1/packet.fps-1e-6&&!stopTimer){
      stopTimer=setTimeout(()=>finishScene().catch(fail),Math.max(0,metadata.expectedDisplayTime-performance.now()));
    }
  }
  checkReady();
  frameCallback=video.requestVideoFrameCallback(onFrame);
}
async function finishScene(){
  if(finishing||disposed||metrics.finished)return;
  // Pausing/seeking can discard decoded read-ahead frames. Measure playback
  // before that control work, while retaining the raw post-settle counters.
  videos.forEach(item=>recordVideoQuality(item,'before-finish'));
  recordQuality();
  finishing=true;const attempt=playGeneration;playing=false;videoGroup?.pause();video.pause();
  metrics.paused_quality=quality();
  try{
    if(videos.length>1)await seekTo((packet.samples.length-1)/packet.fps);
    if(disposed||attempt!==playGeneration)return;
    metrics.settled_quality=quality();metrics.end_time=video.currentTime;metrics.finished=true;
  }finally{finishing=false;}
}
function complete(){
  if(!playing||video.currentTime<packet.source_end-1/packet.fps)return;
  if(packet.cut_segment)window.moviestarLiveStage.onSegmentEnd?.(Date.now());
  else finishScene().catch(fail);
}
function quality(){return videos.reduce((sum,item)=>{const q=item.video.getVideoPlaybackQuality();return{totalVideoFrames:sum.totalVideoFrames+q.totalVideoFrames,droppedVideoFrames:sum.droppedVideoFrames+q.droppedVideoFrames};},{totalVideoFrames:0,droppedVideoFrames:0});}
function recordQuality(){
  metrics.quality=quality();metrics.video_quality=videos.map(item=>{const q=item.video.getVideoPlaybackQuality();return{
    path:item.path,total_frames:q.totalVideoFrames-(item.beforePlay?.totalVideoFrames??0),
    dropped_frames:q.droppedVideoFrames-(item.beforePlay?.droppedVideoFrames??0)};});
}
function recordVideoQuality(item,phase){
  if(!audit||!item.beforePlay)return;
  const q=item.video.getVideoPlaybackQuality(),drops=q.droppedVideoFrames-item.beforePlay.droppedVideoFrames;
  if(phase==='frame'&&drops===item.reportedDrops)return;
  item.reportedDrops=drops;
  (metrics.quality_events??=[]).push({path:item.path,phase,at:Date.now(),media_time:item.video.currentTime,
    callback_media_time:item.frame?.media_time,source_end:item.end,paused:item.video.paused,seeking:item.video.seeking,
    rate:item.video.playbackRate,dropped_frames:drops,total_frames:q.totalVideoFrames-item.beforePlay.totalVideoFrames});
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
    Object.assign(media.style,{position:'absolute',left:'0',top:'0',width:'100%',height:'100%',objectFit:asset.object_fit,objectPosition:asset.object_position??'50% 50%',borderRadius:computed.borderRadius});
    element.append(media);
    if(asset.kind==='image'){
      media.src='assets/'+asset.asset;images.push(media.decode());
    }else{
      const primary=key(asset.path)===key(packet.video_path),item={video:media,path:asset.path,start:asset.source_start??packet.source_start,end:asset.source_end??packet.source_end,fps:packet.fps,primary};
      videos.push(item);if(primary)video=media;
      media.preload=packet.media.filter(item=>item.kind==='video').length>1?'auto':'metadata';media.playsInline=true;
      media.addEventListener('error',()=>fail(Error('Source video could not load.')));
      media.addEventListener('canplay',checkReady);media.addEventListener('seeked',checkReady);
      media.addEventListener('loadedmetadata',()=>{
        item.metadata=true;if(primary)metadataReady=true;
        if(!media.videoWidth||!media.videoHeight)fail(Error('Source has no decodable video.'));
        checkReady();
      });
      media.addEventListener('waiting',()=>{
        if(!playing)return;
        metrics.stalls++;
        if(audit)(metrics.waiting_events??=[]).push({path:item.path,media_time:media.currentTime,
          source_end:item.end,ready_state:media.readyState,paused:media.paused,at:Date.now()});
      });
      if(primary){media.addEventListener('pause',complete);media.addEventListener('ended',complete);}
      media.src=`assets/${asset.asset}?native_video=1#t=${item.start}${packet.cut_segment?'':','+item.end}`;
      if(typeof media.requestVideoFrameCallback!=='function')throw Error('Native preview needs video-frame callbacks; use --mode rendered.');
      const observe=(now,frame)=>{
        if(disposed)return;
        item.firstFrame=true;item.frame={media_time:frame.mediaTime,display_time:frame.expectedDisplayTime,presented_frames:frame.presentedFrames};
        const running=playing&&!video.paused;
        if(packet.cut_segment&&running&&frame.mediaTime>=item.end-1e-6){
          freezeFollower(item);
          item.callback=media.requestVideoFrameCallback(observe);return;
        }
        if(running&&audit){
          recordVideoQuality(item,'frame');
          metrics.video_frames??=[];metrics.video_frames.push({path:item.path,...item.frame,number:sourceFrame(item).number});
        }
        if(packet.cut_segment&&running&&frame.mediaTime>=item.end-1/packet.fps-1e-6)freezeFollower(item);
        checkReady();item.callback=media.requestVideoFrameCallback(observe);
      };
      if(primary)frameCallback=media.requestVideoFrameCallback(onFrame);
      else item.callback=media.requestVideoFrameCallback(observe);
    }
  }
  if(!video)throw Error('Native preview audio carrier is missing');
  const {createVideoGroup}=await import('./preview_native_videos.mjs');if(disposed)return;
  videoGroup=createVideoGroup(videos);
  shared=packet.cut_segment?window.parent.moviestarCutShared:null;
  if(packet.cut_segment&&!shared)throw Error('Native cut audio context is unavailable');
  // Use the export audio rate for multiple media clocks, rather than a
  // device-preferred rate that can vary across playback hosts.
  audio=shared?.audio??new AudioContext(videos.length>1?{sampleRate:48000}:{});
  metrics.audio_device={latency_hint:'interactive',sample_rate:audio.sampleRate,base_latency:audio.baseLatency,output_latency:audio.outputLatency};
  if(!audio.audioWorklet||typeof audio.getOutputTimestamp!=='function')throw Error('Native preview needs the browser audio output clock; use --mode rendered.');
  if(!shared)await audio.audioWorklet.addModule('preview_native_meter.mjs');
  if(disposed){if(!shared)await audio.close();return;}
  meter=new AudioWorkletNode(audio,'cue-meter',{channelCount:2,channelCountMode:'explicit',
    channelInterpretation:'speakers',outputChannelCount:[2]});
  for(const item of videos){
    // Both media pipelines use the same audio output clock. Silence the
    // listener in the graph: element.muted would select a different video
    // scheduling path and require startup priming at every scene handoff.
    const node=audio.createMediaElementSource(item.video),gain=audio.createGain();
    gain.channelCount=2;gain.channelCountMode='explicit';gain.channelInterpretation='speakers';
    gain.gain.value=item.primary?1:0;node.connect(gain);gain.connect(meter);
    item.audioSource=node;item.audioGain=gain;
  }
  meter.connect(shared?shared.destinations[Number(new URLSearchParams(location.search).get('segment'))]:audio.destination);
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
  if(videoGroup){
    if(!await videoGroup.seek(local)||disposed)return false;
    clearFrozenFrame();apply(local);
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    return true;
  }
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
  async settleEnd(){
    if(videos.length>1){this.pause();await seekTo((packet.samples.length-1)/packet.fps);}
  },
  async play(){
    if(cuts)return cuts.play();
    this.pause();
    const attempt=playGeneration;
    const active=()=>!disposed&&attempt===playGeneration;
    try{
      if(!packet.cut_segment&&(metrics.finished||this.time>=(packet.samples.length-1)/packet.fps-1e-6))await seekTo(0);
      if(!active())return;
      metrics.frames=[];metrics.flashes=[];metrics.beeps=[];metrics.video_frames=[];metrics.waiting_events=[];metrics.quality_events=[];metrics.stalls=0;metrics.finished=false;metrics.previous_white=false;delete metrics.first_presented_at;
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
      metrics.device_ready_at=Date.now();if(packet.cut_segment&&packet.samples.length===1){freezeLastFrame();videos.filter(item=>!item.primary).forEach(freezeFollower);}
      for(const item of videos){item.beforePlay=item.video.getVideoPlaybackQuality();delete item.reportedDrops;}
      metrics.initial_presented=videos.map(item=>({path:item.path,media_time:item.video.currentTime,...item.frame,quality:{totalVideoFrames:item.beforePlay.totalVideoFrames,droppedVideoFrames:item.beforePlay.droppedVideoFrames}}));playing=true;
      if(videoGroup)await videoGroup.play();else await video.play();
    }catch(error){if(active()){fail(error);throw error;}}
  },
  pause(){if(cuts){cuts.pause();return;}if(playing&&videos.length){videos.forEach(item=>recordVideoQuality(item,'before-pause'));recordQuality();}++playGeneration;playing=false;clearTimeout(stopTimer);stopTimer=null;videoGroup?.pause();video?.pause();},
  async seek(at){if(cuts)return cuts.seek(at);this.pause();try{if(await seekTo(at))metrics.finished=false;}catch(error){fail(error);throw error;}},
  snapshot(){
    if(cuts)return cuts.snapshot();
    return {request_id:packet.request_id,state_id:packet.state_id,time:this.time,source:sourceFrame(),
      videos:videos.map(item=>({path:item.path,asset:item.video.currentSrc.match(/\/assets\/([a-f0-9]{24})(?:[?#]|$)/)?.[1]??null,audio_gain:item.audioGain?.gain.value,source:sourceFrame(item),media_time:item.video.currentTime,paused:item.video.paused,seeking:item.video.seeking,ready_state:item.video.readyState})),
      boxes:window.moviestarScene.entries.map(({node,element})=>{
        const r=element.getBoundingClientRect();let opacity=1;
        for(let ancestor=element;ancestor;ancestor=ancestor.parentElement)opacity*=Number(getComputedStyle(ancestor).opacity);
        return {path:node.path,kind:node.kind,text:element.textContent,rect:{x:r.x,y:r.y,width:r.width,height:r.height},opacity,text_lines:['text','caption'].includes(node.kind)?window.moviestarScene.textLines(element):undefined};
      })};
  },
  dispose(){
    if(disposed)return;this.pause();disposed=true;cuts?.dispose();for(const cancel of pendingSeeks)cancel();controller.abort();
    if(frameCallback)video?.cancelVideoFrameCallback(frameCallback);
    for(const item of videos)if(item.callback)item.video.cancelVideoFrameCallback(item.callback);
    if(videoGroup)videoGroup.dispose();else if(videos.length){for(const item of videos){item.video.removeAttribute('src');item.video.load();}}else if(video){video.removeAttribute('src');video.load();}clearTimeout(stopTimer);
    clearFrozenFrame();for(const item of videos){item.audioSource?.disconnect();item.audioGain?.disconnect();}meter?.disconnect();meter?.port.close();
    for(const item of videos)if(item.endFrame){item.endFrame.width=item.endFrame.height=0;item.endFrame=null;}
    if(audio&&!shared&&audio.state!=='closed')audio.close().catch(()=>{});
    rejectReady(Error('Superseded'));
  }
};
initialize().catch(fail);
