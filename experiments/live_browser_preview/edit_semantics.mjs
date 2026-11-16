// Plain functions also run inside the browser probe; no hidden mutable state.
export function semanticPacket(p){
 if(p.segments){const r={segments:p.segments.map(s=>({offset:s.offset,packet:semanticPacket(s.packet)})),range:p.range,canvas:p.canvas,fps:p.fps};if(p.audio_plane)r.audio_plane={source_audio:p.audio_plane.source_audio,tracks:p.audio_plane.tracks};return r;}
 const r=Object.fromEntries(['range','canvas','fps','initial','media','samples'].map(k=>[k,p[k]]));
 if(p.caption_plane)r.caption_plane={frames:p.caption_plane.frames,states:p.caption_plane.states.map(s=>({frame:s.frame,cues:s.cues}))};
 if(p.audio_plane)r.audio_plane={source_audio:p.audio_plane.source_audio,tracks:p.audio_plane.tracks};
 return r;
}
export function checkSnapshot(p,s,at){
 const t=at-(p.range.from-(s.window_start??p.range.from));
 const near=(a,b)=>Number.isFinite(a)&&Math.abs(a-b)<=1;
 for(const n of p.initial.nodes){
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
   ||style?.object_fit!==v.object_fit||style?.object_position!==v.object_position)return false;
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
