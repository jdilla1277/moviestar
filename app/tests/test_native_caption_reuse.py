"""Caption-only reuse must keep all media, source identity and geometry fences."""
from pathlib import Path
import subprocess


def test_playback_key_ignores_only_caption_planes_and_revision_metadata():
    module = (Path(__file__).parents[1]/'src/moviestar/preview_native_captions.mjs').as_uri()
    script = r'''
import assert from 'node:assert/strict';
const {playbackKey}=await import(MODULE);
const base={state_id:'old',request_id:'old',requested_at:1,input_identity:'source-stamp',
 range:{from:0,to:5},fps:30,initial:{nodes:[{style:{left:0}}]},
 samples:[{nodes:[{content:'spatial text'}]}],video_path:['main'],media:[{asset:'first',source_start:1,has_audio:true}],
 caption_plane:{states:[{asset:'old-caption'}]}};
const clone=()=>structuredClone(base),next=clone();
next.state_id=next.request_id='fresh';next.requested_at=2;next.caption_plane.states[0].asset='fresh-caption';
assert.equal(playbackKey(base),playbackKey(next));
for(const change of [p=>p.input_identity='replaced-source',p=>p.media[0].asset='new-file',
 p=>p.media[0].source_start=2,p=>p.media[0].has_audio=false,p=>p.video_path=['inset'],
 p=>p.range.to=4,p=>p.fps=24,p=>p.initial.nodes[0].style.left=1,
 p=>p.samples[0].nodes[0].content='changed spatial text']){
 const changed=clone();change(changed);assert.notEqual(playbackKey(base),playbackKey(changed));
}
const cut={segments:[{offset:0,packet:base}],input_identity:'source-stamp'};
const updated={segments:[{offset:0,packet:next}],input_identity:'source-stamp'};
assert.equal(playbackKey(cut),playbackKey(updated));
updated.segments[0].offset=1;assert.notEqual(playbackKey(cut),playbackKey(updated));
'''.replace('MODULE', repr(module))
    subprocess.run(['node', '--input-type=module', '-e', script], check=True, capture_output=True, timeout=10)
