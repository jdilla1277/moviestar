// Benchmark-only evidence. No package behavior, edit semantics or budgets change.
import {spawn} from 'node:child_process';
import {watch} from 'node:fs';
import {readFile, writeFile, stat} from 'node:fs/promises';
import {basename, dirname, join} from 'node:path';
import {loadavg} from 'node:os';
import {createHash} from 'node:crypto';

const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const errorText=error=>String(error?.stack??error);
const clock=()=>({epoch_ms:Date.now(),monotonic_ms:performance.now()});
export const DIAGNOSTICS_SCHEMA=1;

export function startCommand(executable,args,{cwd,out,label,timeoutMs=15000}){
  const started=clock(),stdout=[],stderr=[];
  const record={label,status:'running',started_at:started.epoch_ms,finished_at:null,duration_ms:null,
    exit_code:null,signal:null,timeout_ms:timeoutMs,stdout_file:label+'.stdout.txt',stderr_file:label+'.stderr.txt'};
  let child,timer,drainTimer,settled=false,resolveDone;
  const operation={record,value:undefined,done:new Promise(resolve=>{resolveDone=resolve;}),
    async flush(){
      await operation.done;
      await Promise.all([writeFile(join(out,record.stdout_file),Buffer.concat(stdout)),
        writeFile(join(out,record.stderr_file),Buffer.concat(stderr))]);
      await writeFile(join(out,label+'.command.json'),JSON.stringify(record,null,2));
    }};
  function finish(code,signal){
    if(settled)return;settled=true;clearTimeout(timer);clearTimeout(drainTimer);
    record.closed_at=Date.now();
    record.exit_code=code;record.signal=signal;
    record.stdout_bytes=stdout.reduce((size,chunk)=>size+chunk.length,0);
    record.stderr_bytes=stderr.reduce((size,chunk)=>size+chunk.length,0);
    if(record.status==='running'){
      if(code!==0){record.status='exit_error';record.error=`CLI exit ${code}, signal ${signal??'none'}`;}
      else try{operation.value=JSON.parse(Buffer.concat(stdout).toString('utf8'));record.status='succeeded';}
      catch(error){record.status='output_error';record.error=errorText(error);}
    }
    record.finished_at=Date.now();record.finished_monotonic_ms=performance.now();
    if(record.status!=='succeeded')record.failure_monotonic_ms??=record.finished_monotonic_ms;
    record.duration_ms=record.finished_monotonic_ms-started.monotonic_ms;
    process.removeListener('SIGINT',interrupt);process.removeListener('SIGTERM',interrupt);
    resolveDone(record);
  }
  function terminate(status,message){
    if(settled)return;clearTimeout(timer);clearTimeout(drainTimer);
    record.status=status;record.error=message;record.failure_monotonic_ms=performance.now();
    try{if(process.platform==='win32')child.kill('SIGKILL');else process.kill(-child.pid,'SIGKILL');}
    catch(error){if(error.code!=='ESRCH')record.kill_error=errorText(error);}
    drainTimer=setTimeout(()=>{
      record.output_capture_incomplete=true;child.stdout.destroy();child.stderr.destroy();
      finish(child.exitCode,child.signalCode);
    },1000);
  }
  const interrupt=()=>terminate('interrupted','Benchmark interrupted while CLI was running');
  try{
    child=spawn(executable,args,{cwd,stdio:['ignore','pipe','pipe'],detached:process.platform!=='win32'});
    child.stdout.on('data',chunk=>stdout.push(chunk));child.stderr.on('data',chunk=>stderr.push(chunk));
    child.once('error',error=>{record.status='spawn_error';record.error=errorText(error);record.failure_monotonic_ms=performance.now();});
    // close, rather than exit, also waits for trailing stdout/stderr bytes.
    child.once('close',finish);
    process.once('SIGINT',interrupt);process.once('SIGTERM',interrupt);
    timer=setTimeout(()=>terminate('timed_out',`CLI exceeded ${timeoutMs}ms`),timeoutMs);
  }catch(error){record.status='spawn_error';record.error=errorText(error);finish(null,null);}
  return operation;
}

async function fileSnapshot(path){
  const read_started_at=Date.now();
  try{
    const raw=await readFile(path),metadata=await stat(path);
    return {read_started_at,read_finished_at:Date.now(),bytes:raw.length,mtime_ms:metadata.mtimeMs,inode:metadata.ino,
      sha256:createHash('sha256').update(raw).digest('hex')};
  }catch(error){return {read_started_at,read_finished_at:Date.now(),error:errorText(error)};}
}

function bounded(promise,ms){
  let timer;
  return Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Observation deadline reached')),Math.max(0,ms));})])
    .finally(()=>clearTimeout(timer));
}

export async function observeEdit({start,prepare=async()=>{},probe,serverProbe,specPath,priorRequest,readyTimeoutMs=10000}){
  const specBefore=await fileSnapshot(specPath),started=clock();
  const elapsed=()=>performance.now()-started.monotonic_ms;
  const trace={schema_version:DIAGNOSTICS_SCHEMA,started_at:started.epoch_ms,prior_request_id:priorRequest,
    readiness_timeout_ms:readyTimeoutMs,outcome:'running',failure_at_ms:null,visible_at_ms:null,ready_observed_at:null,
    spec_before:specBefore,spec_changes:[],requests:[],observations:[],probe_errors:[],load_start:loadavg(),
    preview_at_failure:null,spec_at_failure:null};
  let watcher,operation,last=null,lastObservedAt=null,visible=null,lastKey,probeErrorCount=0;
  const requestMap=new Map(),watchTasks=[];
  const stamp=value=>JSON.stringify([value.ino??value.inode,value.mtimeMs??value.mtime_ms,value.size??value.bytes]);
  let lastStamp=stamp(specBefore);
  function observe(snapshot){
    last=snapshot;lastObservedAt=elapsed();
    if(!snapshot)return;
    for(const item of snapshot.requests??[]){
      if(!item||item.request_id===priorRequest)continue;
      const old=requestMap.get(item.request_id);
      requestMap.set(item.request_id,{...old,...item,first_observed_at_ms:old?.first_observed_at_ms??elapsed(),last_observed_at_ms:elapsed()});
    }
    const key=JSON.stringify(snapshot);
    if(key!==lastKey){trace.observations.push({after_ms:elapsed(),preview:snapshot});lastKey=key;}
    // Readiness must be the correct newly visible revision, not just a ready server.
    if(elapsed()<readyTimeoutMs&&snapshot.is_current&&typeof snapshot.current?.request_id==='string'&&snapshot.current.request_id!==priorRequest&&snapshot.expected_text_visible){
      visible=snapshot;trace.visible_at_ms=elapsed();trace.ready_observed_at=Date.now();return true;
    }
    return false;
  }
  try{
    try{watcher=watch(dirname(specPath),(event,name)=>{
      if(name?.toString()!==basename(specPath))return;
      watchTasks.push(stat(specPath).then(metadata=>{
        if(stamp(metadata)===lastStamp)return;lastStamp=stamp(metadata);
        trace.spec_changes.push({after_ms:elapsed(),event,bytes:metadata.size,mtime_ms:metadata.mtimeMs,inode:metadata.ino});
      }).catch(error=>{trace.watch_error=errorText(error);}));
    });watcher.on('error',error=>{trace.watch_error=errorText(error);});}
    catch(error){trace.watch_error=errorText(error);}
    await prepare();operation=start();trace.command=operation.record;
    while(elapsed()<readyTimeoutMs){
      try{if(observe(await bounded(probe(),readyTimeoutMs-elapsed())))break;}
      catch(error){probeErrorCount++;if(trace.probe_errors.length<10)trace.probe_errors.push({after_ms:elapsed(),error:errorText(error)});}
      if(operation.record.status!=='running'&&operation.record.status!=='succeeded')break;
      await sleep(Math.min(20,Math.max(0,readyTimeoutMs-elapsed())));
    }
    if(!visible){
      const commandFailedBeforeDeadline=operation.record.status!=='running'&&operation.record.status!=='succeeded'&&
        operation.record.failure_monotonic_ms-started.monotonic_ms<readyTimeoutMs;
      trace.outcome=commandFailedBeforeDeadline?'command_failed':'readiness_timeout';
      trace.failure_at_ms=elapsed();trace.preview_at_failure=last;
      trace.preview_at_failure_observed_ms=lastObservedAt;
      // Start this read now, before waiting for any late command completion.
      const serverStarted=Date.now();
      const server=serverProbe?bounded(Promise.resolve().then(serverProbe),1000).then(state=>({read_started_at:serverStarted,read_finished_at:Date.now(),state}))
        .catch(error=>({read_started_at:serverStarted,read_finished_at:Date.now(),error:errorText(error)})):Promise.resolve(null);
      [trace.spec_at_failure,trace.server_at_failure]=await Promise.all([fileSnapshot(specPath),server]);
    }
    await operation.done;
    if(visible){
      trace.outcome=operation.record.status==='succeeded'?'succeeded':'command_failed';
      if(trace.outcome!=='succeeded'){trace.failure_at_ms=elapsed();trace.preview_at_failure=last;trace.preview_at_failure_observed_ms=lastObservedAt;}
    }
    trace.command_finished_after_failure=trace.failure_at_ms!==null&&
      operation.record.finished_monotonic_ms>started.monotonic_ms+trace.failure_at_ms;
    trace.measurement_finished_at=visible?Math.max(operation.record.finished_at,trace.ready_observed_at):null;
    trace.spec_after_command=await fileSnapshot(specPath);
    if(!trace.spec_after_command.error&&stamp(trace.spec_after_command)!==lastStamp){
      lastStamp=stamp(trace.spec_after_command);
      trace.spec_changes.push({after_ms:elapsed(),event:'post_command_snapshot',bytes:trace.spec_after_command.bytes,
        mtime_ms:trace.spec_after_command.mtime_ms,inode:trace.spec_after_command.inode});
    }
    if(trace.outcome!=='succeeded'){
      try{trace.preview_after_command=await bounded(probe(),1000);}catch(error){trace.final_probe_error=errorText(error);}
    }
  }catch(error){
    trace.outcome=trace.outcome==='running'?'observer_error':trace.outcome;
    trace.observer_error=errorText(error);trace.failure_at_ms??=elapsed();
    if(operation)await operation.done;
  }finally{
    watcher?.close();await Promise.all(watchTasks);trace.requests=[...requestMap.values()];trace.probe_error_count=probeErrorCount;
    trace.load_end=loadavg();trace.finished_at=Date.now();
    if(operation)try{await operation.flush();}catch(error){
      trace.artifact_error=errorText(error);if(trace.outcome==='succeeded')trace.outcome='artifact_error';
    }
  }
  return {trace,preview:visible,value:operation?.value};
}

// Uses existing read-only viewer state. Requests keep their own identities and
// epoch timestamps; missing stages stay missing rather than being inferred.
export function previewProbeExpression(title){
  return `(()=>{const live=window.moviestarLive,s=live?.state;
    const item=x=>x?Object.fromEntries(['request_id','state_id','status','requested_at','started_at','finished_at','generation_ms','error'].filter(k=>x[k]!==undefined).map(k=>[k,x[k]])):null;
    let expected=false,snapshot_error=null;
    try{expected=Boolean(live?.stage?.snapshot().boxes.some(box=>box.kind==='text'&&box.text===${JSON.stringify(title)}));}catch(e){snapshot_error=String(e);}
    return {current:item(live?.current),is_current:live?.is_current===true,expected_text_visible:expected,snapshot_error,
      status:document.getElementById('status')?.textContent,error:document.getElementById('error')?.textContent,
      requests:[s?.latest,s?.active,s?.pending,s?.displayed].filter(Boolean).map(item),
      visible_event:live?.events?.at(-1)??null,
      frames:[...document.querySelectorAll('iframe')].map(f=>{const a=f.contentWindow?.moviestarLiveStage,v=f.contentDocument?.querySelector('video');return{request_id:a?.packet?.request_id,prepared_at:a?.metrics?.prepared_at,error:a?.error??null,video:v?{ready_state:v.readyState,seeking:v.seeking,current_time:v.currentTime,paused:v.paused}:null};})};})()`;
}
