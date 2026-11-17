import test from 'node:test';import assert from 'node:assert/strict';
import {checkSnapshot,completeVideoQuality}from '../edit_semantics.mjs';
const p={range:{from:0,to:5},fps:30,initial:{nodes:[{path:['inset'],style:{left:'10px',top:'20px',width:'30px',height:'40px'}}]},media:[{path:['main'],asset:'camera',source_start:1,object_fit:'cover',object_position:'25% 50%'}],caption_plane:{frames:[0],states:[{cues:[{text:'New',active_word:0}]}]}};
const s={boxes:[{path:['inset'],rect:{x:10,y:20,width:30,height:40}}],videos:[{path:['main'],asset:'camera',media_time:1,ready_state:4,seeking:false}],video_styles:[{object_fit:'cover',object_position:'25% 50%'}],captions:[{text:'New',active_word:0}],caption_image_ready:true};
test('reject stale pixels and layout even when a new version is declared',()=>{
 assert.equal(checkSnapshot(p,s,0),true);
 for(const edit of [x=>x.captions[0].text='Old',x=>x.boxes[0].rect.x=100,x=>x.videos[0].asset='other',x=>x.video_styles[0].object_position='50% 50%',x=>x.caption_image_ready=false]){
  const next=structuredClone(s);edit(next);assert.equal(checkSnapshot(p,next,0),false);
 }
});

test('reject a loaded image with stale caption pixels',()=>{
 const wanted=structuredClone(p);wanted.caption_plane.rasters=[{sha256:'fresh',rect:{x:0,y:0,width:1,height:1}}];
 const actual=structuredClone(s);actual.caption_sha256='stale';actual.caption_rect={x:0,y:0,width:1,height:1};
 assert.equal(checkSnapshot(wanted,actual,0),false);
 actual.caption_sha256='fresh';assert.equal(checkSnapshot(wanted,actual,0),true);
 actual.caption_rect.width=2;assert.equal(checkSnapshot(wanted,actual,0),false);
});

test('require complete quality observations for every video in every scene',()=>{
 const wanted={segments:[{packet:p},{packet:{...p,media:[{...p.media[0],path:['second']} ]}}]};
 const quality=[{path:['main'],dropped_frames:0},{path:['second'],dropped_frames:0}];
 assert.equal(completeVideoQuality(wanted,quality),true);
 assert.equal(completeVideoQuality(wanted,quality.slice(0,1)),false);
 assert.equal(completeVideoQuality(wanted,[quality[0],{...quality[1],dropped_frames:1}]),false);
 assert.equal(completeVideoQuality(wanted,[quality[0],quality[0]]),false);
});

test('still-image quality means no video decoders, not missing video measurements',()=>{
 assert.equal(completeVideoQuality({media:[{kind:'image',path:['background']}]},[]),true);
 assert.equal(completeVideoQuality(p,[]),false);
});
test('reject stale, unloaded or incorrectly cropped image layers',()=>{
 const image={kind:'image',path:['background'],asset:'fresh',object_fit:'cover',object_position:'50% 50%'};
 const wanted={...p,media:[image]},actual={...s,videos:[],images:[{...image,ready:true}]};
 assert.equal(checkSnapshot(wanted,actual,0),true);
 for(const edit of [i=>i.asset='stale',i=>i.ready=false,i=>i.object_fit='contain']){
  const next=structuredClone(actual);edit(next.images[0]);assert.equal(checkSnapshot(wanted,next,0),false);
 }
});

test('compare immutable samples while live DOM initial nodes acquire sampled fields',async()=>{
 const {semanticPacket}=await import('../edit_semantics.mjs');
 const packet={...p,initial:{nodes:[{path:['title'],kind:'text',content:'First',style:{}}]},samples:[{nodes:[{path:['title'],content:'First'}]}]};
 const live=structuredClone(packet);live.initial.nodes[0].content='Later';live.initial.nodes[0].motion=null;live.initial.nodes[0].caption=null;
 assert.deepEqual(semanticPacket(live),semanticPacket(packet));
 live.samples[0].nodes[0].content='Stale';assert.notDeepEqual(semanticPacket(live),semanticPacket(packet));
 const wanted={...packet,media:[],caption_plane:undefined};
 assert.equal(checkSnapshot(wanted,{...s,videos:[],boxes:[{path:['title'],kind:'text',text:'Wrong',rect:{}}]},0),false);
});

test('spatial captions require canonical glyph pose, color and active word',()=>{
 const word={cue_id:'cue',word_index:0,text:'Hello',active:true,color:'rgb(0, 255, 0)',rect:{x:12,y:22,width:50,height:25}};
 const box={path:['group','words'],rect:{x:10,y:20,width:100,height:50},text:'Hello',caption_words:[word],text_lines:[{x:12,y:22,width:50,height:25}]};
 const wanted={range:{from:0,to:5},fps:30,initial:{nodes:[]},media:[],pose_boxes:{6:[box]}};
 const actual={boxes:[structuredClone(box)],videos:[]};
 assert.equal(checkSnapshot(wanted,actual,.2),true);
 for(const edit of [w=>w.active=false,w=>w.color='rgb(255, 255, 255)',w=>w.rect.x+=2,w=>w.text='Stale']){
  const next=structuredClone(actual);edit(next.boxes[0].caption_words[0]);assert.equal(checkSnapshot(wanted,next,.2),false);
 }
 const next=structuredClone(actual);next.boxes[0].rect.x+=2;assert.equal(checkSnapshot(wanted,next,.2),false);
});
