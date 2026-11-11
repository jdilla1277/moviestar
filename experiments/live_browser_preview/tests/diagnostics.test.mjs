import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startCommand, observeEdit} from '../diagnostics.mjs';

async function fixture(t){
  const out=await mkdtemp(join(tmpdir(),'moviestar-diagnostics-'));
  t.after(()=>rm(out,{recursive:true,force:true}));
  const spec=join(out,'spec.json');await writeFile(spec,JSON.stringify({spatial:{text:'original'}}));
  return {out,spec};
}
function command(out,code,options={}){
  return startCommand(process.execPath,['-e',code],{cwd:out,out,label:'edit',...options});
}
const prior='previous';
const stale=()=>({is_current:true,current:{request_id:prior},expected_text_visible:false,requests:[],frames:[]});
const fresh=()=>({is_current:true,current:{request_id:'new'},expected_text_visible:true,
  requests:[{request_id:'new',requested_at:1,started_at:2,finished_at:3,status:'ready'}],
  frames:[{request_id:'new',prepared_at:4000}],visible_event:{request_id:'new',ready_at:5000}});

test('captures nonzero exit, complete stderr/stdout and signal metadata',async t=>{
  const {out}=await fixture(t);
  const op=command(out,`process.stdout.write('partial output');process.stderr.write('validation rejected');process.exitCode=7;`);
  const result=await op.done;assert.equal(result.status,'exit_error');assert.equal(result.exit_code,7);
  assert.equal(result.signal,null);await op.flush();
  assert.equal(await readFile(join(out,result.stdout_file),'utf8'),'partial output');
  assert.equal(await readFile(join(out,result.stderr_file),'utf8'),'validation rejected');
});

test('records spawn failures and invalid JSON without silently succeeding',async t=>{
  const {out}=await fixture(t);
  const missing=startCommand(join(out,'missing'),[],{cwd:out,out,label:'missing'});
  assert.equal((await missing.done).status,'spawn_error');assert.match(missing.record.error,/ENOENT/);
  await missing.flush();
  const invalid=command(out,`process.stdout.write('not JSON')`);
  assert.equal((await invalid.done).status,'output_error');await invalid.flush();
});

test('waits for pipe close, retaining split UTF-8 and trailing output',async t=>{
  const {out}=await fixture(t);
  const op=command(out,`const b=Buffer.from(JSON.stringify({text:'café'}));process.stdout.write(b.subarray(0,b.length-3));setTimeout(()=>process.stdout.end(b.subarray(b.length-3)),15)`);
  assert.equal((await op.done).status,'succeeded');assert.deepEqual(op.value,{text:'café'});await op.flush();
  assert.equal(JSON.parse(await readFile(join(out,op.record.stdout_file),'utf8')).text,'café');
});

test('command failure is retained when the browser stays on the old revision',async t=>{
  const {out,spec}=await fixture(t);
  const result=await observeEdit({start:()=>command(out,`process.stderr.write('bad model');process.exitCode=9`),
    probe:async()=>stale(),specPath:spec,priorRequest:prior,readyTimeoutMs:500});
  assert.equal(result.trace.outcome,'command_failed');assert.equal(result.trace.command.exit_code,9);
  assert.equal(result.trace.preview_at_failure.current.request_id,prior);
  assert.equal(result.trace.visible_at_ms,null);
  assert.equal(await readFile(join(out,result.trace.command.stderr_file),'utf8'),'bad model');
});

test('readiness timeout retains a later CLI failure and does not become a success',async t=>{
  const {out,spec}=await fixture(t);
  const result=await observeEdit({start:()=>command(out,`setTimeout(()=>{process.stderr.write('late failure');process.exitCode=6},120)`),
    probe:async()=>stale(),specPath:spec,priorRequest:prior,readyTimeoutMs:40});
  assert.equal(result.trace.outcome,'readiness_timeout');assert.equal(result.trace.command.exit_code,6);
  assert.ok(result.trace.command_finished_after_failure);assert.equal(result.trace.visible_at_ms,null);
  assert.match(await readFile(join(out,result.trace.command.stderr_file),'utf8'),/late failure/);
});

test('readiness timeout retains a later successful save, with an atomic file observation',async t=>{
  const {out,spec}=await fixture(t);
  const code=`const fs=require('node:fs');setTimeout(()=>{fs.writeFileSync('spec.tmp',JSON.stringify({spatial:{text:'changed'}}));fs.renameSync('spec.tmp','spec.json');console.log('{}')},120)`;
  const result=await observeEdit({start:()=>command(out,code),probe:async()=>stale(),
    specPath:spec,priorRequest:prior,readyTimeoutMs:40});
  assert.equal(result.trace.outcome,'readiness_timeout');assert.equal(result.trace.command.status,'succeeded');
  assert.ok(result.trace.command_finished_after_failure);assert.ok(result.trace.spec_changes.length>0);
  assert.notEqual(result.trace.spec_before.sha256,result.trace.spec_after_command.sha256);
  assert.equal(result.trace.spec_at_failure.sha256,result.trace.spec_before.sha256);
});

test('a stuck command is killed and recorded even if the preview became visible',async t=>{
  const {out,spec}=await fixture(t);
  const result=await observeEdit({start:()=>command(out,`console.log('{}');setInterval(()=>{},1000)`,{timeoutMs:150}),
    probe:async()=>fresh(),specPath:spec,priorRequest:prior,readyTimeoutMs:500});
  assert.equal(result.trace.outcome,'command_failed');assert.equal(result.trace.command.status,'timed_out');
  assert.equal(result.trace.command.signal,'SIGKILL');assert.notEqual(result.trace.visible_at_ms,null);
});

test('command timeout closes inherited pipes owned by descendant processes',async t=>{
  const {out}=await fixture(t);
  const op=command(out,`require('node:child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'inherit'});setInterval(()=>{},1000)`,{timeoutMs:150});
  const result=await op.done;
  assert.equal(result.status,'timed_out');assert.equal(result.signal,'SIGKILL');
  assert.notEqual(result.output_capture_incomplete,true);await op.flush();
});

test('failure distinguishes a stale client from independently fetched server readiness',async t=>{
  const {out,spec}=await fixture(t);
  const result=await observeEdit({start:()=>command(out,`console.log('{}')`),probe:async()=>stale(),
    serverProbe:async()=>({latest:{request_id:'new',status:'ready'}}),specPath:spec,priorRequest:prior,readyTimeoutMs:70});
  assert.equal(result.trace.outcome,'readiness_timeout');
  assert.equal(result.trace.preview_at_failure.current.request_id,prior);
  assert.equal(result.trace.server_at_failure.state.latest.request_id,'new');
});

test('fresh ready identity with the wrong visible content still fails',async t=>{
  const {out,spec}=await fixture(t);
  const result=await observeEdit({start:()=>command(out,`console.log('{}')`),
    probe:async()=>({...fresh(),expected_text_visible:false}),specPath:spec,priorRequest:prior,readyTimeoutMs:70});
  assert.equal(result.trace.outcome,'readiness_timeout');assert.equal(result.trace.visible_at_ms,null);
});

test('hung browser probing cannot hide command completion or extend the readiness deadline',async t=>{
  const {out,spec}=await fixture(t);
  const result=await observeEdit({start:()=>command(out,`console.log('{}')`),
    probe:()=>new Promise(()=>{}),specPath:spec,priorRequest:prior,readyTimeoutMs:60});
  assert.equal(result.trace.outcome,'readiness_timeout');assert.equal(result.trace.command.status,'succeeded');
  assert.equal(result.trace.preview_at_failure,null);assert.ok(result.trace.failure_at_ms<500);
});

test('a probe returning after a blocked event loop cannot retroactively pass readiness',async t=>{
  const {out,spec}=await fixture(t);
  const result=await observeEdit({start:()=>command(out,`console.log('{}')`),
    probe:async()=>{const end=performance.now()+80;while(performance.now()<end){}return fresh();},
    specPath:spec,priorRequest:prior,readyTimeoutMs:30});
  assert.equal(result.trace.outcome,'readiness_timeout');assert.equal(result.trace.visible_at_ms,null);
});

test('success requires fresh identity, expected content AND successful CLI; stages retain request IDs',async t=>{
  const {out,spec}=await fixture(t);let polls=0;
  const result=await observeEdit({start:()=>command(out,`console.log('{}')`),
    probe:async()=>++polls<3?{...fresh(),current:{request_id:prior}}:fresh(),
    specPath:spec,priorRequest:prior,readyTimeoutMs:500});
  assert.equal(result.trace.outcome,'succeeded');assert.ok(polls>=3);
  assert.equal(result.trace.command.status,'succeeded');assert.equal(result.preview.current.request_id,'new');
  assert.equal(result.trace.requests[0].request_id,'new');
  assert.equal(result.trace.requests[0].requested_at,1);
  assert.equal(result.trace.spec_after_command.sha256,result.trace.spec_before.sha256);
});

test('completed-edit duration stays valid when the wall clock moves backwards',async t=>{
  const {out,spec}=await fixture(t),dateNow=Date.now;
  try{
    const result=await observeEdit({start:()=>command(out,`setTimeout(()=>console.log('{}'),80)`),
      probe:async()=>{Date.now=()=>dateNow()-60000;return fresh();},
      specPath:spec,priorRequest:prior,readyTimeoutMs:500});
    assert.equal(result.trace.outcome,'succeeded');
    assert.ok(result.trace.measurement_ms>=result.trace.command.duration_ms);
    assert.ok(result.trace.cli_completion_ms>=80);
  }finally{Date.now=dateNow;}
});
