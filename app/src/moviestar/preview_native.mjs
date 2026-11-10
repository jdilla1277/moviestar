const $=id=>document.getElementById(id), base=location.pathname, selected=$('version');
let state,current,pending,selection=new URLSearchParams(location.search).get('request')||'',connected=false,failed=null,failureText='',seeking=false,seekGeneration=0;
$('native-controls').hidden=false;
const events=[];
const target=()=>selection?state?.history.find(item=>item.request_id===selection):state?.displayed;
const allowed=item=>target()?.request_id===item.request_id&&(selection||state.latest.request_id===item.request_id);
function discard(){
  if(!pending)return;
  clearTimeout(pending.timer);pending.cancel(Error('Superseded'));pending.frame.contentWindow?.moviestarLiveStage?.dispose();pending.frame.remove();pending=null;
}
function describe(){
  const upToDate=current&&current.item.request_id===state?.latest?.request_id&&!selection;
  $('status').textContent=!connected?'Disconnected — showing the last loaded preview.':selection?(current?.item.request_id===selection?'Historical preview — project unchanged.':'Loading historical preview — project unchanged.'):current?.api.error?'Native playback failed — showing its last frame.':state?.latest?.status==='failed'?'Latest native preview failed. Any preview shown is from an older edit.':failed?'The requested native preview could not load. Any preview shown is from an older edit.':upToDate?'Current native preview · Play to review video and sound.':current?'Updating — showing an older preview.':'Preparing native preview…';
  $('play').disabled=Boolean(pending||!current||current.api.error);
  $('error').textContent=selection&&!state?.history.some(item=>item.request_id===selection)?'That historical preview is no longer retained in this session.':state?.latest?.status==='failed'?state.latest.error:failed?(failureText||'The requested native preview could not load. Use --mode rendered.'):current?.api.error??'';
  $('restart').disabled=$('pause').disabled=$('seek').disabled=Boolean(pending||!current||current?.api.error);
  if(current){const p=current.api.packet;$('seek').max=p.range.to-p.range.from;$('seek').step=1/p.fps;if(!seeking)$('seek').value=current.api.time;}
  if(current){$('clock').textContent=current.api.time.toFixed(1)+' s';const p=current.api.packet;$('details').textContent=`Version ${p.state_id.slice(7,19)} · ${p.range.from}–${p.range.to}s · ${p.canvas.width}×${p.canvas.height} · Native browser playback`;$('timing').textContent=`Navigation / detected edit to ready: ${Math.round(current.ready_at-Math.max(performance.timeOrigin,current.item.requested_at*1000))} ms`;}
}
function fit(){
  if(!current)return;
  const p=current.api.packet;$('stage').style.aspectRatio=`${p.canvas.width}/${p.canvas.height}`;
  Object.assign(current.frame.style,{width:p.canvas.width+'px',height:p.canvas.height+'px',transform:`scale(${$('stage').clientWidth/p.canvas.width})`});
}
function load(item){
  discard();current?.api.pause();
  const frame=document.createElement('iframe');frame.className='pending';frame.title='Live spatial scene';
  let cancel;const canceled=new Promise((_,reject)=>{cancel=reject;});canceled.catch(()=>{});
  const candidate={item,frame,cancel,timer:setTimeout(()=>finishError(Error('Native preview timed out')),30000)};
  pending=candidate;
  function finishError(error){if(pending!==candidate)return;failed=item.request_id;failureText='The requested native preview could not load: '+error.message+' Use --mode rendered if this browser or project is unsupported.';discard();describe();}
  frame.addEventListener('load',async()=>{
    try{
      const api=frame.contentWindow.moviestarLiveStage;
      if(!api)throw Error('Native stage did not initialize.');
      await Promise.race([api.ready,canceled]);
      if(pending!==candidate||!allowed(item))return;
      if(api.packet.request_id!==item.request_id||api.packet.state_id!==item.state_id)throw Error('Wrong native preview revision.');
      const old=current;
      if(old&&!old.api.metrics.finished&&old.api.time>0)await Promise.race([api.seek(old.api.time),canceled]);
      if(pending!==candidate||!allowed(item))return;
      frame.className='';
      $('stage').style.aspectRatio=`${api.packet.canvas.width}/${api.packet.canvas.height}`;
      Object.assign(frame.style,{transform:`scale(${$('stage').clientWidth/api.packet.canvas.width})`});
      await Promise.race([new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))),canceled]);
      if(pending!==candidate||!allowed(item))return;
      clearTimeout(candidate.timer);pending=null;current={...candidate,api,ready_at:Date.now()};failed=null;failureText='';seeking=false;++seekGeneration;
      if(old){old.api.dispose();old.frame.remove();}
      $('empty').hidden=true;fit();
      events.push({request_id:item.request_id,state_id:item.state_id,ready_at:current.ready_at,requested_at:item.requested_at*1000});if(events.length>64)events.shift();
      describe();
    }catch(error){finishError(error);}
  },{once:true});
  frame.src=base+'stage?request='+item.request_id+(new URLSearchParams(location.search).has('audit')?'&audit=1':'');$('stage').append(frame);
}
function update(){
  if(pending&&!allowed(pending.item))discard();
  const item=target();if(item&&allowed(item)&&item.request_id!==current?.item.request_id&&item.request_id!==pending?.item.request_id&&item.request_id!==failed)load(item);
  describe();
}
selected.addEventListener('change',()=>{selection=selected.value;failed=null;history.replaceState(null,'',base+(selection?'?request='+encodeURIComponent(selection):''));update();});
$('play').addEventListener('click',()=>{if(current)current.api.play().catch(error=>{$('error').textContent=error.message;});});
$('pause').addEventListener('click',()=>current?.api.pause());
$('restart').addEventListener('click',()=>current?.api.seek(0).catch(error=>{$('error').textContent=error.message;}));
$('seek').addEventListener('input',()=>{seeking=true;++seekGeneration;});
$('seek').addEventListener('change',async()=>{const attempt=++seekGeneration;seeking=true;try{await current?.api.seek(Number($('seek').value));}catch(error){$('error').textContent=error.message;}finally{if(attempt===seekGeneration)seeking=false;}});
$('seek').addEventListener('blur',()=>{seeking=false;});
new ResizeObserver(fit).observe($('stage'));
async function poll(){
  try{
    const response=await fetch(base+'state',{cache:'no-store',signal:AbortSignal.timeout(3000)});if(!response.ok)throw Error('Disconnected');
    state=await response.json();if(state.mode&&state.mode!=='native'){location.reload();return;}connected=true;selected.replaceChildren(new Option('Follow latest edit',''));
    for(const item of state.history)selected.add(new Option(`${item.state_id.slice(7,19)} · ${item.request_id.slice(0,6)}`,item.request_id));
    if(selection&&!state.history.some(item=>item.request_id===selection))selected.add(new Option('Historical preview expired',selection));
    selected.value=selection;update();
  }catch{connected=false;describe();}
  setTimeout(poll,100);
}
window.movieStarPreview=window.moviestarLive={mode:'native',events,
  get displayed(){return current?.item;},
  get measurements(){return Object.fromEntries(events.map(item=>[item.request_id,{playable_ready_ms:Math.round(item.ready_at-item.requested_at),stalls:current?.item.request_id===item.request_id?current.api.metrics.stalls:undefined}]));},
  get state(){return state;},get current(){return current?.item;},
  get is_current(){return Boolean(!selection&&current&&allowed(current.item)&&!current.api.error);},
  get stage(){return current?.api;},get pending(){return pending?.item;}};
poll();
