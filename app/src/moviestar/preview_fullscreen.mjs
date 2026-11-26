// Presentation only: the fullscreen root owns every player and its controls.
const preview=document.getElementById('preview'),stage=document.getElementById('stage');
const button=document.getElementById('fullscreen'),error=document.getElementById('fullscreen-error');
function fit(){
  if(document.body.dataset.previewMode==='native')return;
  if(document.fullscreenElement!==preview){
    stage.style.removeProperty('width');stage.style.removeProperty('height');return;
  }
  const video=stage.querySelector('video:not(.pending)'),area=stage.parentElement;
  if(!video?.videoWidth||!video.videoHeight)return;
  const scale=Math.min(area.clientWidth/video.videoWidth,area.clientHeight/video.videoHeight);
  stage.style.width=video.videoWidth*scale+'px';stage.style.height=video.videoHeight*scale+'px';
}
function sync(){
  const active=document.fullscreenElement===preview;
  button.textContent=active?'Exit fullscreen':'Fullscreen';
  button.setAttribute('aria-pressed',String(active));fit();
}
if(!document.fullscreenEnabled||!preview.requestFullscreen){
  button.disabled=true;button.title='Fullscreen is unavailable in this browser.';
}
button.addEventListener('click',async()=>{
  error.textContent='';
  try{
    if(document.fullscreenElement===preview)await document.exitFullscreen();
    else await preview.requestFullscreen();
  }catch{error.textContent='Fullscreen could not open. Try the Fullscreen button again.';}
  finally{sync();}
});
document.addEventListener('fullscreenchange',sync);
new ResizeObserver(fit).observe(stage.parentElement);
new MutationObserver(fit).observe(stage,{childList:true,subtree:true,attributes:true,attributeFilter:['class']});
window.addEventListener('resize',fit);
sync();
