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


def test_border_color_revision_reuses_only_matching_geometry_and_commits_atomically():
    module=(Path(__file__).parents[1]/'src/moviestar/preview_native_captions.mjs').as_uri()
    script=r'''
import assert from 'node:assert/strict';const {playbackKey,prepareBorderColors}=await import(MODULE);
const initial={nodes:[{path:['speaker'],kind:'video',style:{'border-color':'red','border-width':'2px'},rule_sources:{'border-color':{layer:'base'},'border-width':{layer:'base'}},children:[]}]};
const base={initial,range:{from:0,to:3},fps:30,media:[{asset:'original'}],samples:[{nodes:[]}]};
const next=structuredClone(base);next.initial.nodes[0].style['border-color']='blue';next.initial.nodes[0].rule_sources['border-color']={layer:'local_customization'};
assert.equal(playbackKey(base),playbackKey(next),'A border color must not rebuild source decoders');
for(const change of [p=>p.initial.nodes[0].style['border-width']='3px',p=>p.media[0].asset='changed',p=>p.initial.nodes[0].rule_sources['border-width'].layer='local',p=>p.initial.nodes[0].kind='text',p=>p.initial.nodes[0].path=['other']]){
 const incompatible=structuredClone(next);change(incompatible);assert.notEqual(playbackKey(base),playbackKey(incompatible));
}
globalThis.CSS={supports:(name,value)=>name==='border-color'&&['red','blue'].includes(value)};
const calls=[],entry={node:structuredClone(initial.nodes[0]),element:{style:{setProperty:(k,v)=>calls.push([k,v]),removeProperty:k=>calls.push([k])}}};
const signal=new AbortController(),commit=prepareBorderColors([entry],next.initial,signal.signal);
assert.deepEqual(calls,[],'Preparation is non-mutating');commit();assert.deepEqual(calls,[['border-color','blue']]);
assert.equal(entry.node.style['border-color'],'blue');assert.deepEqual(entry.node.rule_sources['border-color'],{layer:'local_customization'});
const reset=structuredClone(next.initial);delete reset.nodes[0].style['border-color'];prepareBorderColors([entry],reset,signal.signal)();assert.deepEqual(calls.at(-1),['border-color']);
const canceled=new AbortController(),stale=prepareBorderColors([entry],next.initial,canceled.signal);canceled.abort();assert.throws(stale);assert.equal(calls.length,2);
const invalid=structuredClone(next.initial);invalid.nodes[0].style['border-color']='invalid';assert.throws(()=>prepareBorderColors([entry],invalid,signal.signal));assert.equal(calls.length,2);
'''.replace('MODULE',repr(module))
    result=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
