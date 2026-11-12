"""A missed callback is observable without accepting a missing source frame."""
from pathlib import Path
import subprocess


def test_native_cut_compositor_counter_requires_every_source_frame():
    module = (Path(__file__).parents[2]/'experiments/live_browser_preview/presented_frames.mjs').as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {assertPresentedSegment}=await import(MODULE);
const initial={media_time:1,presented_frames:5};
const frames=Array.from({length:89},(_,i)=>({media_time:1+(i+1)/30,number:31+i,presented_frames:6+i}));
const sample={baseline:initial,frames,shown:{source:{number:30}},start:1,fps:30,count:90};
assert.equal(assertPresentedSegment(sample).presented_frames,90);
// Linux evidence: source 31/counter 6 -> source 33/counter 8. The
// compositor submitted source 32 although its callback wasn't delivered.
const skippedCallback=frames.filter(frame=>frame.number!==32);
assert.deepEqual(assertPresentedSegment({...sample,frames:skippedCallback}),{
 presented_frames:90,callbacks:88,observed_source_frames:89,unobserved_callbacks:1});
// A real missing compositor submission must still fail (counter 6 -> 7).
const missingSubmission=skippedCallback.map(frame=>({...frame,presented_frames:frame.presented_frames-(frame.number>32?1:0)}));
assert.throws(()=>assertPresentedSegment({...sample,frames:missingSubmission}),/Compositor submissions/);
const changed=()=>structuredClone(frames);
const wrongPixels=changed();wrongPixels[4].number++;
assert.throws(()=>assertPresentedSegment({...sample,frames:wrongPixels}),/barcode pixels/);
const duplicateSubmission=changed();duplicateSubmission[4].presented_frames++;
assert.throws(()=>assertPresentedSegment({...sample,frames:duplicateSubmission}),/Compositor submissions/);
assert.throws(()=>assertPresentedSegment({...sample,frames:frames.slice(0,-1)}),/final expected/);
assert.throws(()=>assertPresentedSegment({...sample,baseline:{...initial,media_time:1+1/30}}),/prepared first/);
assert.throws(()=>assertPresentedSegment({...sample,baseline:{...initial,presented_frames:6}}),/Compositor submissions/);
assert.throws(()=>assertPresentedSegment({...sample,baseline:undefined}),/baseline/);
const wrongClock=changed();wrongClock[5].media_time+=.01;
assert.throws(()=>assertPresentedSegment({...sample,frames:wrongClock}),/source frame grid/);
'''.replace('MODULE', repr(module))
    subprocess.run(['node', '--input-type=module', '-e', script], check=True, capture_output=True, timeout=10)
