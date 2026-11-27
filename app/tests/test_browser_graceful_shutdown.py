"""Profile cleanup follows graceful Chrome shutdown, with bounded fallback."""
import json
from pathlib import Path
import subprocess
import pytest


def test_graceful_browser_exit_finishes_profile_writes_before_removal():
    module=(Path(__file__).parents[1]/'src/moviestar/spatial_browser_lifecycle.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {stopBrowser}=await import(MODULE);
let finish,writing=true,requested=false;const signals=[];
const child={exitCode:null,signalCode:null,kill(signal){signals.push(signal);this.signalCode=signal;finish();}};
const exited=new Promise(r=>finish=r);
const error=await stopBrowser(child,exited,'owned-profile',()=>{
 assert.equal(writing,false,'Profile removal must follow the graceful writer drain');
},()=>{
 requested=true;setTimeout(()=>{writing=false;child.exitCode=0;finish();},30);
});
assert.equal(error,null);assert.equal(requested,true);assert.deepEqual(signals,[]);
'''
    result=subprocess.run(['node','--input-type=module','-e','const MODULE='+json.dumps(module)+';'+script],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('mode', ['reject', 'hang'])
def test_broken_graceful_close_forces_exit_and_preserves_cleanup_errors(mode):
    module=(Path(__file__).parents[1]/'src/moviestar/spatial_browser_lifecycle.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';const {stopBrowser}=await import(MODULE);
let finish;const events=[];
const child={exitCode:null,signalCode:null,kill(signal){events.push(signal);this.signalCode=signal;finish();}};
const exited=new Promise(r=>finish=r),failure=Object.assign(new Error('profile denied'),{code:'EACCES'});
const error=await stopBrowser(child,exited,'owned-profile',()=>{
 events.push('remove');assert.equal(child.signalCode,'SIGTERM');throw failure;
},()=>{events.push('request');return MODE==='reject'?Promise.reject(Error('CDP closed')):new Promise(()=>{});});
assert.equal(error,failure);assert.deepEqual(events,['request','SIGTERM','remove']);
'''
    result=subprocess.run(['node','--input-type=module','-e','const MODULE='+json.dumps(module)+';const MODE='+json.dumps(mode)+';'+script],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stdout+result.stderr
