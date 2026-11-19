"""Cut players use the canonical audio clock even with one picture per span."""
import json
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.skipif(not shutil.which('node'),reason='Node required')
@pytest.mark.parametrize('videos,prepared',[(1,False),(2,False),(1,True)])
def test_cut_audio_clock_does_not_depend_on_parallel_picture_count(videos,prepared):
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_cuts.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';
const {mountCuts}=await import(MODULE),opened=[];
globalThis.window={};
globalThis.AudioContext=class{
 constructor(options){opened.push(options);this.state='suspended';}
 async close(){this.state='closed';}
};
const packet={segments:[0,1,2].map(()=>({packet:{media:Array(VIDEOS).fill({kind:'video'})}})),
 ...(PREPARED?{audio_plane:{sample_rate:48000}}:{})};
const controller=new AbortController();controller.abort(Error('canceled'));
// Exercise real mounting through context creation, then cancel before DOM or
// browser dependencies. All spans share this single output context.
await assert.rejects(mountCuts(packet,controller.signal,{}),/canceled/);
assert.equal(opened.length,1);
assert.equal(opened[0]?.sampleRate,48000,'Cut audio must use the canonical rate, not a device-dependent default');
'''
    prefix=f'const MODULE={json.dumps(module)},VIDEOS={videos},PREPARED={json.dumps(prepared)};\n'
    result=subprocess.run(['node','--input-type=module','-e',prefix+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
