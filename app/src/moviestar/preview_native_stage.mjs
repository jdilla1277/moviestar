import {render} from './spatial_dom.mjs';
import {playbackKey,prepareCaptionImages} from './preview_native_captions.mjs';

let resolveReady,rejectReady,packet,video,audio,meter,frameCallback,stopTimer,cuts,shared,frozenFrame,disposed=false;
const controller=new AbortController(),audit=new URLSearchParams(location.search).has('audit');
const pendingSeeks=new Set();
let lastIndex=-1, playing=false, readyDone=false, metadataReady=false, imageReady=false,finishing=false,playGeneration=0;
let videoGroup,reviewAudio,outputMixer,sceneClock;
let playbackIdentity,speedFrame,pictureStartup,pendingStageSeek;
const playbackRate=()=>packet?.playback_rate??1;
const measuredPictures=()=>playbackRate()!==1||(packet?.prepared_audio&&(packet.media??[]).filter(m=>m.kind==='video').length===3);
function animateSpeed(){
  if(disposed||!playing||playbackRate()===1)return;
  const at=reviewAudio?.outputTimeAt(performance.timeOrigin+performance.now());
  const first=reviewAudio?.snapshot().started_offset??0;
  apply(Math.max(first,Number.isFinite(at)?at:(video.currentTime-packet.source_start)/playbackRate()));
  speedFrame=requestAnimationFrame(animateSpeed);
}
let captionLayer,captionImages=[],captionState=-1;
const videos=[],mediaFrames=[];
let mediaResize;
function syncMediaFrames(){
  window.moviestarScene.refreshMediaFrames();
  for(const {element,asset,frame,media,camera} of mediaFrames){
    if(camera)continue;
    const styles=window.moviestarScene.mediaStyle(element,asset);
    for(const [target,style] of [[frame,styles.frame],[media,styles.media]])
      for(const [key,value] of Object.entries(style))if(target.style[key]!==value)target.style[key]=value;
  }
}
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
  if(packet.caption_plane){
    const state=packet.caption_plane.frames[index];
    if(state!==captionState){captionState=state;captionLayer.replaceChildren(captionImages[state]);}
  }
  const next=new Map([...walk(packet.samples[index].nodes)].map(node=>[key(node.path),node]));
  let layoutChanged=false;
  for(const entry of window.moviestarScene.entries){
    const node=next.get(key(entry.node.path));
    entry.node.motion=node.motion;entry.node.media_motion=node.media_motion;
    if(['text','caption'].includes(entry.node.kind)){
      if(entry.node.kind==='caption')layoutChanged=window.moviestarScene.updateCaption(entry.element,node.caption,entry.node.path)||layoutChanged;
      else if(entry.element.textContent!==node.content){entry.element.textContent=node.content;layoutChanged=true;}
      entry.node.caption=node.caption;
    }
  }
  if(layoutChanged)window.moviestarScene.refreshLayout();else window.moviestarScene.applyMotion();
}
function captionAudit(){
  if(!audit)return {};
  const captions=window.moviestarScene.entries.filter(e=>e.node.kind==='caption');
  if(!captions.length)return {};
  return {spatial_captions:captions.map(({node,element})=>({path:node.path,text:element.textContent,
    words:[...element.querySelectorAll('[data-caption-word]')].map(span=>({cue_id:span.dataset.captionCue,
      word_index:Number(span.dataset.captionWord),text:span.textContent,active:span.dataset.active==='true'}))})),
    caption_applied_at:performance.now()};
}
async function validateCaptionSamples(){
  const scene=window.moviestarScene, captions=scene.entries.filter(e=>e.node.kind==='caption');
  if(!captions.length)return;
  const paths=new Set(captions.map(e=>key(e.node.path))),seen=new Set();
  try{
    for(let i=0;i<packet.samples.length;i++){
      const signature=JSON.stringify([...walk(packet.samples[i].nodes)].filter(n=>paths.has(key(n.path))).map(n=>[n.path,n.content]));
      if(seen.has(signature))continue;seen.add(signature);
      apply(i/packet.fps);await document.fonts.ready;if(disposed)return;
      scene.refreshLayout();
      // Resize observers can miss temporary validation poses. Check every
      // media box synchronously before the candidate revision is published.
      syncMediaFrames();
      for(const {node,element} of captions)if(scene.inspectCaption(element).overflow)
        throw Error('caption_overflow '+node.path.join('/')+': caption text exceeds its content box; enlarge the region or reduce font size');
    }
  }finally{if(!disposed){apply(0);scene.refreshLayout();syncMediaFrames();}}
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
  // Pin sped-up decoder pixels during GPU readback; drawing a live element
  // twice can otherwise sample different presentations at elevated source cadence.
  const captured=playbackRate()!==1&&image.tagName==='VIDEO'?new VideoFrame(image):null;
  const capturedTime=captured?.timestamp/1e6;
  try{
    pixels.drawImage(captured??image,0,0,128*x,16*y,0,0,128,16);
    pixels.drawImage(captured??image,320*x,180*y,x,y,0,16,1,1);
  }finally{captured?.close();}
  const data=pixels.getImageData(0,0,128,17).data;
  let number=0;
  for(let bit=0;bit<8;bit++)if(data[(8*128+bit*16+8)*4]>128)number|=1<<bit;
  return {number,white:data[16*128*4]>230,...(captured?{captured_media_time:capturedTime}:{})};
}
async function checkReady(){
  if(readyDone||disposed||!metadataReady||!imageReady||video.readyState<2||video.seeking||Math.abs(video.currentTime-packet.source_start)>1/packet.fps)return;
  if(videos.some(item=>!item.metadata||!item.firstFrame||item.video.readyState<2||item.video.seeking||Math.abs(item.video.currentTime-item.start)>1/packet.fps))return;
  readyDone=true;
  try{
    // rVFC may omit the last valid presentation even with zero decoder drops.
    // Prepare its pixels before publishing a cut scene, then return both
    // decoders to their first frames. No output clip or persistent cache.
    if(packet.cut_segment||playbackRate()!==1){
      if(!await videoGroup.seek((packet.samples.length-1)/packet.fps,{presented:true})||disposed)return;
      for(const item of videos){
        const frame=document.createElement('canvas');frame.width=item.video.videoWidth;frame.height=item.video.videoHeight;
        frame.getContext('2d').drawImage(item.video,0,0);item.endFrame=frame;
      }
      if(!await videoGroup.seek(1/packet.fps)||disposed)return;
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      if(!await videoGroup.seek(0,{presented:playbackRate()!==1})||disposed)return;
    }
    if(measuredPictures()){
      // A one-frame review already has every required picture presented.
      // A source may be on its final decoded frame, so asking it for an
      // advancing presentation would wait forever. No moving-picture startup
      // lead is needed for this static result window.
      pictureStartup=packet.samples.length===1&&playbackRate()===1
        ? {lead:0,prepared_frame_only:true}
        : await videoGroup.measureStartup();
      if(!pictureStartup||disposed)return;
      metrics.picture_startup=pictureStartup;
    }
    apply(0);
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    if(disposed)return;
    metrics.prepared_at=Date.now();metrics.initial_barcode=sourceFrame().number;resolveReady(true);
  }catch(error){fail(error);}
}
function onFrame(now,metadata){
  if(disposed)return;
  const callbackStarted=audit?performance.now():null;
  const running=playing&&!video.paused;
  // Read the callback image before motion/layout work can let the live video
  // image advance. At a cut's final frame, sample the held canonical pixels
  // after installing them below, rather than reading beyond the edit.
  const callbackPixels=running&&audit&&(!packet.cut_segment||metadata.mediaTime<packet.source_end-1/packet.fps-1e-6)?sourceFrame():null;
  let sourceReadbackFinished=audit?performance.now():null;
  const primary=videos.find(item=>item.primary);
  if(primary){primary.firstFrame=true;primary.frame={media_time:metadata.mediaTime,display_time:metadata.expectedDisplayTime,presented_frames:metadata.presentedFrames};}
  if(running&&primary)recordVideoQuality(primary,'frame');
  if(playing&&videoGroup){try{videoGroup.synchronize(videos.find(item=>item.primary));}catch(error){fail(error);return;}}
  const local=(metadata.mediaTime-packet.source_start)/playbackRate();
  if(playbackRate()!==1&&running&&!stopTimer){
    const remaining=(packet.source_end-metadata.mediaTime)/playbackRate();
    stopTimer=setTimeout(()=>finishScene().catch(fail),Math.max(0,metadata.expectedDisplayTime-performance.now()+remaining*1000));
  }
  if(playbackRate()!==1&&running&&metadata.mediaTime>packet.source_end-playbackRate()/packet.fps+1e-6){
    freezeLastFrame();frameCallback=video.requestVideoFrameCallback(onFrame);return;
  }
  if(playbackRate()!==1&&running&&metadata.mediaTime>=packet.source_end-playbackRate()/packet.fps-1e-6)freezeLastFrame();
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
  if(playbackRate()===1||!running)apply(local);
  if(!running&&metrics.initial_frame===undefined&&Math.abs(local)<1/packet.fps)metrics.initial_frame=metadata.mediaTime;
  if(running){
    if(metrics.first_presented_at===undefined)metrics.first_presented_at=performance.timeOrigin+metadata.expectedDisplayTime;
    const sampled=audit?(callbackPixels??sourceFrame()):{};
    // Final cut pixels are read only after installing the canonical hold.
    if(audit&&!callbackPixels)sourceReadbackFinished=performance.now();
    if(audit&&primary){metrics.video_frames??=[];metrics.video_frames.push({path:primary.path,...primary.frame,number:sampled.number});}
    metrics.frames.push({media_time:metadata.mediaTime,...(sampled.captured_media_time!==undefined?{captured_media_time:sampled.captured_media_time}:{}),number:sampled.number,display_time:metadata.expectedDisplayTime,presented_frames:metadata.presentedFrames,
      ...captionAudit(),
      ...(audit?{callback_time:performance.now(),current_time:video.currentTime,frozen:Boolean(frozenFrame),
        callback_started_at:callbackStarted,source_readback_ms:sourceReadbackFinished-callbackStarted,
        callback_elapsed_ms:performance.now()-callbackStarted,
        ...(Number.isFinite(metadata.presentationTime)?{presentation_time:metadata.presentationTime}:{}),
        ...(Number.isFinite(metadata.processingDuration)?{decode_processing_ms:metadata.processingDuration*1000}:{})}:{}),
      ...(audit&&packet.caption_plane?{caption_state:captionState,caption_asset:captionLayer.firstElementChild?.getAttribute('src'),caption_applied_at:performance.now()}:{} )});
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
  // Completion owns the final output pose even when rVFC omits its last
  // presentation. The prepared snapshot uses the canonical output frame grid.
  if(playbackRate()!==1){freezeLastFrame();apply((packet.samples.length-1)/packet.fps);}
  finishing=true;const attempt=playGeneration;
  if(playbackRate()!==1)metrics.paused_at=performance.timeOrigin+performance.now();
  if(playing&&outputMixer)metrics.pause_context_time=audio.currentTime;
  if(speedFrame!==undefined)cancelAnimationFrame(speedFrame);
  reviewAudio?.pause();playing=false;videoGroup?.pause();video.pause();
  metrics.paused_quality=quality();
  try{
    if(videos.length>1)await seekTo((packet.samples.length-1)/packet.fps);
    if(disposed||attempt!==playGeneration)return;
    await outputMixer?.drain();if(disposed||attempt!==playGeneration)return;
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
function connectAudioGraph(){
  // A cut's parent already meters the final selected output. Keep child
  // routing in ordinary gains instead of redundant always-running worklets.
  let destination=shared?.destinations[Number(new URLSearchParams(location.search).get('segment'))];
  if(!shared){
    if(outputMixer){meter=outputMixer.node;destination=meter;}
    else{
      meter=new AudioWorkletNode(audio,'cue-meter',{channelCount:2,channelCountMode:'explicit',
        channelInterpretation:'speakers',outputChannelCount:[2]});
      meter.connect(audio.destination);destination=meter;
      meter.port.onmessage=event=>{
        if(!playing)return;
        const stamp=audio.getOutputTimestamp();
        metrics.beeps.push({context_time:event.data.context_time,
          display_time:stamp.performanceTime+(event.data.context_time-stamp.contextTime)*1000,
          timestamp_available:stamp.performanceTime>0&&stamp.contextTime>0,rms:event.data.rms});
      };
    }
  }

  for(const item of videos){
    // Ordinary pictures share the output clock through their media sources.
    // Measured speed/three-picture groups stay muted and use prepared PCM.
    const gain=audio.createGain();
    gain.channelCount=2;gain.channelCountMode='explicit';gain.channelInterpretation='speakers';
    gain.gain.value=item.primary&&!packet.prepared_audio?1:0;
    // A silent video has no audio pipeline to join to this output clock.
    // Keep its picture on group transport without creating an empty source.
    if(item.hasAudio&&!measuredPictures()){const node=audio.createMediaElementSource(item.video);node.connect(gain);item.audioSource=node;}
    gain.connect(destination);item.audioGain=gain;
  }
}
async function initialize(){
  const params=new URLSearchParams(location.search),request=params.get('request');
  const response=await fetch('packet?request='+encodeURIComponent(request)+(params.has('segment')?'&segment='+encodeURIComponent(params.get('segment')):''),{cache:'no-store',signal:controller.signal});
  if(!response.ok)throw Error('The requested live packet is no longer retained.');
  packet=await response.json();if(disposed)return;
  // Canonical DOM entries acquire sampled motion while playing. Fingerprint
  // the immutable descriptor before render/apply touches those node objects.
  playbackIdentity=playbackKey(packet);
  if(packet.segments){
    Object.assign(window.frameElement.style,{width:packet.canvas.width+'px',height:packet.canvas.height+'px'});
    const {mountCuts}=await import('./preview_native_cuts.mjs');
    cuts=await mountCuts(packet,controller.signal,metrics);if(disposed){cuts.dispose();return;}
    metrics.source_buffering=cuts.sourceBuffering;metrics.prepared_at=Date.now();resolveReady(true);return;
  }
  metrics.source_buffering=packet.media.some(m=>m.kind==='video')?await enableSourceBuffering(controller.signal):false;if(disposed)return;
  Object.assign(window.frameElement.style,{width:packet.canvas.width+'px',height:packet.canvas.height+'px'});
  const measured=await render({...packet.initial,paint_dir:'native',live_dom:true});
  if(disposed)return;
  const bad=measured.diagnostics.find(item=>item.severity==='error');
  if(bad)throw Error(bad.code+' '+(bad.path??[]).join('/')+(bad.reason?': '+bad.reason:''));
  const entries=new Map(window.moviestarScene.entries.map(entry=>[key(entry.node.path),entry]));
  const images=[];
  if(packet.caption_plane){
    captionLayer=document.createElement('div');
    captionLayer.style.cssText='position:absolute;inset:0;pointer-events:none';
    window.moviestarScene.scene.append(captionLayer);
    for(const state of packet.caption_plane.states){
      const image=new Image(),rect=state.rect;
      Object.assign(image.style,{display:'block',position:'absolute',left:rect.x+'px',top:rect.y+'px',width:rect.width+'px',height:rect.height+'px'});
      image.src='assets/'+state.asset;captionImages.push(image);
      const decoded=image.decode();decoded.catch(()=>{});images.push(decoded);
    }
  }
  for(const asset of packet.media){
    const {element}=entries.get(key(asset.path));
    const computed=getComputedStyle(element);
    if(!['relative','absolute'].includes(computed.position)||['inline','contents','none'].includes(computed.display))throw Error('Unsupported native media containing block.');
    if(!CSS.supports('clip-path','content-box'))throw Error('Native media framing needs content-box clipping.');
    const scene=window.moviestarScene,target=scene.mediaTarget(element),camera=target!==element;
    const frame=camera?target.parentElement.parentElement:document.createElement('div');
    const media=document.createElement(asset.kind==='video'?'video':'img');
    if(camera){
      Object.assign(media.style,{position:'absolute',display:'block',left:'0px',top:'0px',width:'100%',height:'100%',
        border:'0px',padding:'0px',objectFit:asset.object_fit,objectPosition:asset.object_position});target.append(media);
    }else{
      const styles=scene.mediaStyle(element,asset);Object.assign(frame.style,styles.frame);Object.assign(media.style,styles.media);
      frame.append(media);element.append(frame);
    }
    mediaFrames.push({element,asset,frame,media,camera});
    if(asset.kind==='image'){
      media.src='assets/'+asset.asset;const decoded=media.decode().then(()=>scene.setMediaIntrinsic(element,media.naturalWidth,media.naturalHeight));decoded.catch(()=>{});images.push(decoded);
    }else{
      const primary=key(asset.path)===key(packet.video_path),item={video:media,path:asset.path,start:asset.source_start??packet.source_start,end:asset.source_end??packet.source_end,fps:packet.fps,rate:asset.playback_rate??1,primary,hasAudio:asset.has_audio!==false};
      videos.push(item);if(primary)video=media;
      media.playbackRate=item.rate;
      if(measuredPictures())media.muted=true;
      media.preload=packet.media.filter(item=>item.kind==='video').length>1?'auto':'metadata';media.playsInline=true;
      media.addEventListener('error',()=>fail(Error('Source video could not load.')));
      media.addEventListener('canplay',checkReady);media.addEventListener('seeked',checkReady);
      media.addEventListener('loadedmetadata',()=>{
        scene.setMediaIntrinsic(element,media.videoWidth,media.videoHeight);
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
  mediaResize=new ResizeObserver(()=>{if(!disposed)try{syncMediaFrames();}catch(error){fail(error);}});
  // Content width may stay constant while percentage padding and the outer
  // frame resize. Track both boxes so the inner source follows either change.
  for(const {element,frame} of mediaFrames){mediaResize.observe(element);mediaResize.observe(frame,{box:'border-box'});}
  if(!video){
    if(packet.clock!=='scene')throw Error('Native preview audio carrier is missing');
    await initializeStill(images);return;
  }
  const {createVideoGroup}=await import('./preview_native_videos.mjs');if(disposed)return;
  videoGroup=createVideoGroup(videos);
  shared=packet.cut_segment?window.parent.moviestarCutShared:null;
  if(packet.cut_segment&&!shared)throw Error('Native cut audio context is unavailable');
  // Use the export audio rate for multiple media clocks, rather than a
  // device-preferred rate that can vary across playback hosts.
  audio=shared?.audio??new AudioContext(videos.length>1||packet.audio_plane?{sampleRate:48000}:{});
  metrics.audio_device={latency_hint:'interactive',sample_rate:audio.sampleRate,base_latency:audio.baseLatency,output_latency:audio.outputLatency};
  if(!audio.audioWorklet||typeof audio.getOutputTimestamp!=='function')throw Error('Native preview needs the browser audio output clock; use --mode rendered.');
  if(!shared){
    if(packet.audio_plane){
      const {createMixedOutput}=await import('./preview_native_audio.mjs');
      outputMixer=await createMixedOutput(audio,metrics,controller.signal,{audit,
        capturePcm:new URLSearchParams(location.search).has('audit_pcm')});
    }else await audio.audioWorklet.addModule('preview_native_meter.mjs');
  }
  if(disposed){if(!shared)await audio.close();return;}
  connectAudioGraph();
  if(!shared){
    const {createPreparedAudio}=await import('./preview_native_audio.mjs');if(disposed)return;
    reviewAudio=createPreparedAudio(audio,meter,controller.signal,packet.range.to-packet.range.from);
    const prepared=await reviewAudio.prepare(packet.audio_plane,controller.signal);prepared.commit();
  }
  if(metadataReady&&Math.abs(video.currentTime-packet.source_start)>1e-6)video.currentTime=packet.source_start;
  await Promise.all(images);await validateCaptionSamples();if(disposed)return;imageReady=true;checkReady();
}
async function initializeStill(images){
  shared=packet.cut_segment?window.parent.moviestarCutShared:null;
  if(packet.cut_segment&&!shared)throw Error('Native cut audio context is unavailable');
  audio=shared?.audio??new AudioContext({sampleRate:48000});
  if(!shared){
    const {createPreparedAudio,createMixedOutput}=await import('./preview_native_audio.mjs');
    if(disposed)return;
    // Keep an output graph ready for a later audio-only revision of a silent
    // scene. Atomic audio replacement can then reuse this same still player.
    outputMixer=await createMixedOutput(audio,metrics,controller.signal,{audit,
      capturePcm:new URLSearchParams(location.search).has('audit_pcm')});
    meter=outputMixer.node;
    reviewAudio=createPreparedAudio(audio,meter,controller.signal,packet.range.to-packet.range.from);
    const prepared=await reviewAudio.prepare(packet.audio_plane,controller.signal);prepared.commit();
  }
  await Promise.all(images);await validateCaptionSamples();if(disposed)return;
  const {createSceneClock}=await import('./preview_native_scene_clock.mjs');if(disposed)return;
  sceneClock=createSceneClock({packet,metrics,apply,audio:reviewAudio,onError:fail,
    resume:()=>audio.resume(),contextTime:()=>audio.currentTime,drain:()=>outputMixer?.drain(),
    present:frame=>{Object.assign(frame,captionAudit());window.moviestarLiveStage.onPresented?.(frame);},
    end:()=>window.moviestarLiveStage.onSegmentEnd?.(Date.now())});
  apply(0);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  if(disposed)return;metrics.prepared_at=Date.now();resolveReady(true);
}
async function seekTo(at){
  await ready;
  if(disposed)return false;
  for(const cancel of pendingSeeks)cancel();
  const local=Math.max(0,Math.min((packet.samples.length-1)/packet.fps,Math.round(at*packet.fps)/packet.fps));
  if(videoGroup){
    if(!await videoGroup.seek(local,{presented:playbackRate()!==1})||disposed)return false;
    clearFrozenFrame();apply(local);reviewAudio?.seek(local);
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
  get time(){if(cuts)return cuts.time;if(sceneClock)return sceneClock.time;return video?Math.max(0,(video.currentTime-packet.source_start)/playbackRate()):0;},
  get paused(){return cuts?cuts.paused:sceneClock?sceneClock.paused:pictureStartup?.prepared_frame_only?!playing:video?.paused??true;},
  get audioState(){return cuts?cuts.audioState:audio?.state;},
  get error(){return metrics.error;},
  get audioOutputTimestamp(){return cuts?cuts.audioOutputTimestamp:audio?.getOutputTimestamp?.();},
  async prepareCaptionUpdate(next,signal){
    await ready;signal?.throwIfAborted();
    if(disposed||metrics.error||playbackIdentity!==playbackKey(next)
        ||(next.audio_plane&&!cuts&&audio.sampleRate!==48000))return null;
    const transaction=cuts?await cuts.prepareCaptionUpdate(next,signal):await prepareCaptionImages(next.caption_plane,signal);
    if(!transaction)return null;
    let audioTransaction;
    try{audioTransaction=await reviewAudio?.prepare(next.audio_plane,signal);}
    catch(error){(cuts?transaction.cancel:transaction.dispose)();throw error;}
    let committed=false;
    return{
      commit:()=>{
        signal?.throwIfAborted();if(disposed)throw Error('Superseded');
        if(cuts)transaction.commit();
        else{
          for(const image of captionImages){image.remove();image.removeAttribute('src');}
          captionLayer?.remove();captionLayer=null;captionImages=transaction.images;
          if(next.caption_plane){captionLayer=document.createElement('div');captionLayer.style.cssText='position:absolute;inset:0;pointer-events:none';window.moviestarScene.scene.append(captionLayer);}
        }
        audioTransaction?.commit();reviewAudio?.seek(window.moviestarLiveStage.time);
        for(const item of videos)item.audioGain.gain.value=item.primary&&!next.prepared_audio?1:0;
        playbackIdentity=playbackKey(next);packet=next;committed=true;lastIndex=-1;captionState=-1;
        if(!cuts)apply(window.moviestarLiveStage.time);
        Object.assign(metrics,{frames:[],flashes:[],beeps:[],video_frames:[],cuts:[],segments_shown:[],audio_blocks:[],stalls:0,finished:false,prepared_at:Date.now()});
        for(const key of ['quality','video_quality','play_context_time','pause_context_time','first_presented_at','finished_at','end_result_time','prepared_audio','audio_pcm'])delete metrics[key];
      },
      cancel:()=>{if(!committed){(cuts?transaction.cancel:transaction.dispose)();audioTransaction?.cancel();}}
    };
  },
  holdEnd(){
    if(sceneClock){sceneClock.holdEnd();return;}
    if(disposed||!packet.cut_segment||!packet.prepared_audio)return;
    // The parent calls inside the final frame's interval, only when its
    // independent mix owns the cut clock. Keep both canonical end pictures
    // visible while that mix and the incoming decoder continue unchanged.
    freezeLastFrame();videos.filter(item=>!item.primary).forEach(freezeFollower);
    apply((packet.samples.length-1)/packet.fps);this.pause();
    metrics.held_quality=quality();
  },
  async settleEnd(){
    if(sceneClock){sceneClock.pause();apply((packet.samples.length-1)/packet.fps);return;}
    if(videos.length>1){this.pause();await seekTo((packet.samples.length-1)/packet.fps);}
  },
  async play(){
    if(cuts)return cuts.play();
    if(sceneClock)return sceneClock.play();
    this.pause();
    const attempt=playGeneration;
    const active=()=>!disposed&&attempt===playGeneration;
    try{
      await pendingStageSeek;
      if(!active())return;
      if(!packet.cut_segment&&(metrics.finished||this.time>=(packet.samples.length-1)/packet.fps-1e-6))await seekTo(0);
      if(!active())return;
      metrics.frames=[];metrics.flashes=[];metrics.beeps=[];metrics.video_frames=[];metrics.waiting_events=[];metrics.quality_events=[];metrics.stalls=0;metrics.finished=false;metrics.previous_white=false;delete metrics.first_presented_at;
      metrics.play_at=Date.now();metrics.play_context_time=audio.currentTime;delete metrics.pause_context_time;metrics.audio_blocks=[];
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
      if(reviewAudio?.snapshot().prepared)await reviewAudio.play(this.time,pictureStartup?{presentationLead:pictureStartup.lead}:{pictureLead:1/(2*packet.fps)});if(!active())return;
      if(reviewAudio)metrics.prepared_audio=reviewAudio.snapshot();
      if(pictureStartup?.prepared_frame_only){
        // Keep the sole authored image visible. Advancing the decoders could
        // show an out-of-window frame or require an EOF callback to finish.
        const end=metrics.prepared_audio.started_context_time+packet.range.to-packet.range.from;
        const complete=()=>{
          if(!active())return;
          const remaining=end-audio.currentTime;
          if(remaining>1/audio.sampleRate){stopTimer=setTimeout(complete,Math.max(1,remaining*1000));return;}
          stopTimer=null;finishScene().catch(fail);
        };
        complete();return;
      }
      if(videoGroup)await videoGroup.play();else await video.play();
      if(active()&&playbackRate()!==1)animateSpeed();
    }catch(error){if(active()){fail(error);throw error;}}
  },
  pause(){if(cuts){cuts.pause();return;}if(sceneClock){sceneClock.pause();return;}if(playing&&outputMixer)metrics.pause_context_time=audio.currentTime;reviewAudio?.pause();if(playing&&videos.length){videos.forEach(item=>recordVideoQuality(item,'before-pause'));recordQuality();}++playGeneration;playing=false;if(speedFrame!==undefined)cancelAnimationFrame(speedFrame);clearTimeout(stopTimer);stopTimer=null;videoGroup?.pause();video?.pause();},
  async seek(at){if(cuts)return cuts.seek(at);if(sceneClock){await sceneClock.seek(at);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return;}this.pause();const operation=seekTo(at);pendingStageSeek=operation;try{if(await operation)metrics.finished=false;}catch(error){fail(error);throw error;}finally{if(pendingStageSeek===operation)pendingStageSeek=null;}},
  sourceSnapshot(){return cuts?cuts.sourceSnapshot():packet.clock==='scene'?null:sourceFrame();},
  snapshot(){
    if(cuts)return cuts.snapshot();
    return {request_id:packet.request_id,state_id:packet.state_id,time:this.time,source:packet.clock==='scene'?null:sourceFrame(),
      audio:reviewAudio?.snapshot(),
      captions:packet.caption_plane?.states[captionState]?.cues??[],
      videos:videos.map(item=>({path:item.path,asset:item.video.currentSrc.match(/\/assets\/([a-f0-9]{24})(?:[?#]|$)/)?.[1]??null,audio_gain:item.audioGain?.gain.value,audio_connected:Boolean(item.audioSource),source:sourceFrame(item),media_time:item.video.currentTime,paused:item.video.paused,seeking:item.video.seeking,ready_state:item.video.readyState})),
      boxes:window.moviestarScene.entries.map(({node,element})=>{
        const r=element.getBoundingClientRect();let opacity=1;
        for(let ancestor=element;ancestor;ancestor=ancestor.parentElement)opacity*=Number(getComputedStyle(ancestor).opacity);
        return {path:node.path,kind:node.kind,text:element.textContent,
          ...(node.media_motion?{media_motion:node.media_motion}:{}),...(['video','image'].includes(node.kind)?{media_box:node.media_motion?window.moviestarScene.mediaBox(element):(()=>{const m=window.moviestarScene.contentMetrics(element.querySelector('video,img')??element);return{object_fit:m.css.objectFit,object_position:m.css.objectPosition,content_rect:m.rect};})()} : {}),rect:{x:r.x,y:r.y,width:r.width,height:r.height},opacity,caption:node.kind==='caption'?node.caption:undefined,caption_words:node.kind==='caption'?window.moviestarScene.captionWords(element):undefined,text_lines:['text','caption'].includes(node.kind)?window.moviestarScene.textLines(element):undefined};
      })};
  },
  dispose(){
    if(disposed)return;this.pause();disposed=true;mediaResize?.disconnect();sceneClock?.dispose();cuts?.dispose();reviewAudio?.dispose();outputMixer?.dispose();for(const cancel of pendingSeeks)cancel();controller.abort();
    if(frameCallback)video?.cancelVideoFrameCallback(frameCallback);
    for(const item of videos)if(item.callback)item.video.cancelVideoFrameCallback(item.callback);
    if(videoGroup)videoGroup.dispose();else if(videos.length){for(const item of videos){item.video.removeAttribute('src');item.video.load();}}else if(video){video.removeAttribute('src');video.load();}clearTimeout(stopTimer);
    clearFrozenFrame();for(const item of videos){item.audioSource?.disconnect();item.audioGain?.disconnect();}meter?.disconnect();meter?.port.close();
    for(const image of captionImages)image.removeAttribute('src');captionImages=[];captionLayer?.remove();
    for(const item of videos)if(item.endFrame){item.endFrame.width=item.endFrame.height=0;item.endFrame=null;}
    if(audio&&!shared&&audio.state!=='closed')audio.close().catch(()=>{});
    rejectReady(Error('Superseded'));
  }
};
initialize().catch(fail);
