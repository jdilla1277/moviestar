// Plain functions also run inside the browser probe; no hidden mutable state.
export function semanticPacket(p){
 if(p.segments){const r={segments:p.segments.map(s=>({offset:s.offset,packet:semanticPacket(s.packet)})),range:p.range,canvas:p.canvas,fps:p.fps};if(p.audio_plane)r.audio_plane={source_audio:p.audio_plane.source_audio,tracks:p.audio_plane.tracks};return r;}
 const r=Object.fromEntries(['range','canvas','fps','initial','media','samples'].map(k=>[k,p[k]]));
 r.initial=JSON.parse(JSON.stringify(p.initial,(k,v)=>['motion','content','caption'].includes(k)?undefined:v));
 if(p.caption_plane)r.caption_plane={frames:p.caption_plane.frames,states:p.caption_plane.states.map(s=>({frame:s.frame,cues:s.cues}))};
 if(p.audio_plane)r.audio_plane={source_audio:p.audio_plane.source_audio,tracks:p.audio_plane.tracks};
 return r;
}
export function checkSnapshot(p,s,at){
 const t=at-(p.range.from-(s.window_start??p.range.from));
 const near=(a,b)=>Number.isFinite(a)&&Math.abs(a-b)<=1;
 const pose=p.pose_boxes?.[Math.round(t*p.fps)];
 if(pose){
  for(const expected of pose){
   const actual=s.boxes.find(b=>JSON.stringify(b.path)===JSON.stringify(expected.path));
   if(!actual||!['x','y','width','height'].every(k=>near(actual.rect[k],expected.rect[k])))return false;
   if(expected.media_box){
    const a=actual.media_box,e=expected.media_box;
    if(!a||a.object_fit!==e.object_fit||a.object_position!==e.object_position||!['x','y','width','height'].every(k=>near(a.content_rect[k],e.content_rect[k])))return false;
   }
   if(expected.caption_words){
    if(actual.text!==expected.text||actual.caption_words?.length!==expected.caption_words.length)return false;
    for(let i=0;i<expected.caption_words.length;i++){
     const a=actual.caption_words[i],e=expected.caption_words[i];
     if(['cue_id','word_index','text','active','color'].some(k=>a[k]!==e[k])||!['x','y','width','height'].every(k=>near(a.rect[k],e.rect[k])))return false;
    }
    if(actual.text_lines?.length!==expected.text_lines.length)return false;
   }
  }
 }
 for(const n of pose?[]:p.initial.nodes){
  const values=['left','top','width','height'].map(k=>n.style?.[k]);
  if(!values.every(v=>typeof v==='string'&&/^-?[\d.]+px$/.test(v)))continue;
  const box=s.boxes.find(b=>JSON.stringify(b.path)===JSON.stringify(n.path));
  if(!box||!['x','y','width','height'].every((k,i)=>near(box.rect[k],parseFloat(values[i]))))return false;
 }
 const videos=p.media.filter(m=>m.kind==='video'||m.kind===undefined);
 if(s.videos.length!==videos.length)return false;
 for(let i=0;i<videos.length;i++){
  const v=videos[i],actual=s.videos[i],style=s.video_styles[i];
  if(actual.asset!==v.asset||actual.seeking||actual.ready_state<2||Math.abs(actual.media_time-(v.source_start+t))>1/p.fps+.001
   ||style?.object_fit!==v.object_fit||style?.object_position!==(style?.expected_object_position??v.object_position))return false;
 }
 const images=p.media.filter(m=>m.kind==='image');
 if(images.length){
  if(s.images?.length!==images.length)return false;
  for(const image of images){const actual=s.images.find(i=>JSON.stringify(i.path)===JSON.stringify(image.path));
   if(!actual?.ready||actual.asset!==image.asset||actual.object_fit!==image.object_fit||actual.object_position!==(actual.expected_object_position??image.object_position))return false;
  }
 }
 const frame=Math.max(0,Math.min((p.samples?.length??1)-1,Math.round(t*p.fps)));
 const walk=function*(nodes){for(const n of nodes??[]){yield n;yield*walk(n.children);}};
 const kinds=new Map([...walk(p.initial.nodes)].map(n=>[JSON.stringify(n.path),n.kind]));
 for(const n of walk(p.samples?.[frame]?.nodes)){
  const box=s.boxes.find(b=>JSON.stringify(b.path)===JSON.stringify(n.path));
  if(['text','caption'].includes(kinds.get(JSON.stringify(n.path)))&&typeof n.content==='string'&&box?.text!==n.content)return false;
 }
 const plane=p.caption_plane;
 if(plane){
  const index=plane.frames[Math.max(0,Math.min(plane.frames.length-1,Math.round(t*p.fps)))],state=plane.states[index];
  const raster=plane.rasters?.[index];
  if(raster&&(s.caption_sha256!==raster.sha256||JSON.stringify(s.caption_rect)!==JSON.stringify(raster.rect)))return false;
  if(!s.caption_image_ready||s.captions.length!==state.cues.length)return false;
  for(let i=0;i<state.cues.length;i++)for(const k of ['text','active_word','font_size','highlight_color','estimated_bounds'])
   if(JSON.stringify(state.cues[i][k])!==JSON.stringify(s.captions[i][k]))return false;
 }
 return true;
}

export function completeVideoQuality(packet,quality){
 const packets=packet.segments?packet.segments.map(s=>s.packet):[packet];
 const wanted=packets.flatMap(p=>p.media.filter(m=>m.kind==='video'||m.kind===undefined).map(m=>JSON.stringify(m.path))).sort();
 const observed=quality.map(q=>JSON.stringify(q.path)).sort();
 return JSON.stringify(wanted)===JSON.stringify(observed)&&quality.every(q=>q.dropped_frames===0);
}
