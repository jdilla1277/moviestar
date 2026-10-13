import {spawn, execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, existsSync, writeFileSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {performance} from 'node:perf_hooks';

const root = resolve(import.meta.dirname);
const out = join(root, 'frames'); mkdirSync(out,{recursive:true});
const profile = mkdtempSync(join(tmpdir(),'spatial-chrome-'));
const processStart = performance.now();
const chrome = spawn(process.env.MOVIESTAR_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--headless','--no-sandbox','--disable-gpu','--hide-scrollbars','--force-device-scale-factor=1',
  '--autoplay-policy=no-user-gesture-required','--remote-allow-origins=*','--remote-debugging-port=0',
  `--user-data-dir=${profile}`,'about:blank'
],{stdio:['ignore','ignore','ignore']});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function waitPort(){for(let i=0;i<100;i++){const p=join(profile,'DevToolsActivePort');if(existsSync(p))return Number(readFileSync(p,'utf8').split('\n')[0]);await sleep(50)}throw new Error('Chrome did not open remote debugging port')}
let ws; let id=0;const waiting=new Map();
function send(method,params={}){return new Promise((resolve,reject)=>{const k=++id;waiting.set(k,{resolve,reject});ws.send(JSON.stringify({id:k,method,params}))})}
async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.error||r.result?.exceptionDetails)throw new Error(JSON.stringify(r));return r.result.result.value}
async function navigate(file,width,height){await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});await send('Page.navigate',{url:pathToFileURL(join(root,`${file}.html`)).href});for(let i=0;i<100;i++){try{if(await evaluate('document.readyState')){if(await evaluate('document.readyState')==='complete')break}}catch{}await sleep(30)}await evaluate('document.fonts.ready.then(()=>true)')}
async function frame(scene,width,height,time,view,save){const t=performance.now();const call=scene.startsWith('transition_')?'setTransition':'setMoment';const boxes=await evaluate(`window.${call}(${time},${JSON.stringify(view)})`);const transparent=['hybrid','hybrid-static','tag-only'].includes(view);if(transparent)await send('Emulation.setDefaultBackgroundColorOverride',{color:{r:0,g:0,b:0,a:0}});const s=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false,fromSurface:true});if(transparent)await send('Emulation.setDefaultBackgroundColorOverride',{});if(save)writeFileSync(save,Buffer.from(s.result.data,'base64'));return {latency_ms:performance.now()-t,boxes}}
const results={machine:'Apple M4, 24 GiB',chrome:'155.0.8059.40',scenes:{}};
try{
 const port=await waitPort();let pages;for(let i=0;i<100;i++){pages=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();if(pages.find(p=>p.type==='page'))break;await sleep(50)}
 ws=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);await new Promise((r,j)=>{ws.addEventListener('open',r,{once:true});ws.addEventListener('error',j,{once:true})});ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id&&waiting.has(m.id)){const p=waiting.get(m.id);waiting.delete(m.id);m.error?p.reject(new Error(JSON.stringify(m.error))):p.resolve(m)}});
 await send('Page.enable');await send('Runtime.enable');
 if(process.argv.includes('--warm-hybrid')){
  await navigate('podcast',1280,720);
  await frame('podcast',1280,720,3,'hybrid',join(out,'podcast-1280x720-hybrid.png'));
  const times=[];
  for(let i=0;i<20;i++){
   const started=performance.now();
   await frame('podcast',1280,720,3,'hybrid',join(out,'podcast-1280x720-hybrid.png'));
   execFileSync(process.env.MOVIESTAR_PYTHON || 'python3',[join(root,'singleframe_preview.py')],{stdio:'ignore'});
   times.push(performance.now()-started);
  }
  times.sort((a,b)=>a-b);
  console.log(JSON.stringify({warm_hybrid_p95_ms:Math.round(times[18]),min_ms:Math.round(times[0]),max_ms:Math.round(times[19])}));
 }else if(process.argv.includes('--cold-hybrid')){
  await navigate('podcast',1280,720);
  const result=await frame('podcast',1280,720,3,'hybrid',join(out,'podcast-1280x720-hybrid.png'));
  writeFileSync(join(root,'cold_hybrid_boxes.json'),JSON.stringify(result.boxes));
  console.log(JSON.stringify({cold_hybrid_ms:performance.now()-processStart,frame_ms:result.latency_ms}));
 }else if(process.argv.includes('--cold-only')){
  await navigate('podcast',1280,720);
  await frame('podcast',1280,720,3,'rendered',null);
  console.log(JSON.stringify({cold_process_ms:performance.now()-processStart}));
 }else if(process.argv.includes('--calibration')){
  await navigate('calibration',1280,720);
  const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false,fromSurface:true});
  writeFileSync(join(out,'calibration-browser.png'),Buffer.from(shot.result.data,'base64'));
  console.log('calibration-browser.png');
 }else if(process.argv.includes('--reuse-only')){
  const reused={};
  for(const scene of ['podcast','podcast_alt']){
   await navigate(scene,1280,720);
   reused[scene]=(await frame(scene,1280,720,3,'rendered',join(out,`${scene}-reuse.png`))).boxes;
  }
  writeFileSync(join(root,'reuse_results.json'),JSON.stringify(reused,null,2));
  console.log(JSON.stringify({scenes:Object.keys(reused),pair_count:Object.values(reused).map(x=>x.filter(b=>b.id==='listener-pair').length)}));
 }else if(process.argv.includes('--mask-only')){
  await navigate('mask_400x300_r16',400,300);
  const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false,fromSurface:true});
  writeFileSync(join(root,'masks/browser_400x300_r16.png'),Buffer.from(shot.result.data,'base64'));
 }else if(process.argv.includes('--generate-masks')){
  const manifest=JSON.parse(readFileSync(join(root,'mask_manifest.json'),'utf8'));
  for(const item of manifest){await navigate(item.name,item.width,item.height);const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false,fromSurface:true});writeFileSync(join(root,'masks',`${item.name}.png`),Buffer.from(shot.result.data,'base64'))}
  console.log('masks',manifest.length);
 }else{
 for(const [width,height] of [[1280,720],[720,1280]])for(const scene of ['podcast','civic','explainer','static']){
  const key=`${scene}-${width}x${height}`;const coldStart=performance.now();await navigate(scene,width,height);const rendered=await frame(scene,width,height,3,'rendered',join(out,`${key}-rendered.png`));const cold=performance.now()-coldStart;const layout=await frame(scene,width,height,3,'layout',join(out,`${key}-layout.png`));const hybrid=await frame(scene,width,height,3,'hybrid',join(out,`${key}-hybrid.png`));const motionBoxes={};if(scene==='podcast'||scene==='civic')for(const t of [0,5.9])motionBoxes[t]=(await frame(scene,width,height,t,'rendered',join(out,`${key}-t${t}-rendered.png`))).boxes;if(scene==='civic'){await frame(scene,width,height,0,'hybrid-static',join(out,`${key}-static-overlay.png`));await frame(scene,width,height,0,'tag-only',join(out,`${key}-tag-only.png`))}const times=[];for(let n=0;n<20;n++)times.push((await frame(scene,width,height,0.1+n*0.27,'rendered',null)).latency_ms);times.sort((a,b)=>a-b);results.scenes[key]={cold_ms:cold,first_frame_ms:rendered.latency_ms,warm_p95_ms:times[Math.ceil(times.length*.95)-1],layout_ms:layout.latency_ms,hybrid_ms:hybrid.latency_ms,boxes:rendered.boxes,layout_boxes:layout.boxes,motion_boxes:motionBoxes};console.log(key,Math.round(cold),Math.round(results.scenes[key].warm_p95_ms));
 }
 for(const scene of ['transition_structured','transition_legacy']){
  await navigate(scene,1280,720);const rendered=await frame(scene,1280,720,5.75,'rendered',join(out,`${scene}-rendered.png`));const layout=await frame(scene,1280,720,5.75,'layout',join(out,`${scene}-layout.png`));results.scenes[scene]={warm_ms:rendered.latency_ms,layout_ms:layout.latency_ms,boxes:rendered.boxes};console.log(scene,Math.round(rendered.latency_ms));
 }
 if(process.argv.includes('--export')){
  await navigate('podcast',1280,720);
  let peakRSS=0;
  const rssTimer=setInterval(()=>{try{const lines=execFileSync('ps',['-axo','pid=,ppid=,rss='],{encoding:'utf8'}).trim().split('\n').map(x=>x.trim().split(/\s+/).map(Number));const descendants=new Set([chrome.pid]);let changed=true;while(changed){changed=false;for(const [pid,ppid] of lines)if(descendants.has(ppid)&&!descendants.has(pid)){descendants.add(pid);changed=true}}const rss=lines.filter(([pid])=>descendants.has(pid)).reduce((sum,row)=>sum+row[2],0);peakRSS=Math.max(peakRSS,rss)}catch{}},100);
  const start=performance.now();
  for(let n=0;n<180;n++)await frame('podcast',1280,720,n/30,'rendered',join(out,`export-${String(n).padStart(3,'0')}.png`));
  clearInterval(rssTimer);
  results.export={capture_ms:performance.now()-start,peak_browser_rss_kib:peakRSS,frame_count:180};
  console.log('export capture',Math.round(results.export.capture_ms),'ms; peak',Math.round(peakRSS/1024),'MiB');
 }
 writeFileSync(join(root,'browser_results.json'),JSON.stringify(results,null,2));
 }
}finally{try{ws?.close()}catch{}chrome.kill('SIGTERM');await sleep(250);rmSync(profile,{recursive:true,force:true})}
