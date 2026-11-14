// Read-only clock decomposition. This does not alter any acceptance gate.
export function describeAudioClocks(metrics){
  const finite=values=>values.filter(Number.isFinite);
  const range=values=>values.length?{min_ms:Math.min(...values),max_ms:Math.max(...values),spread_ms:Math.max(...values)-Math.min(...values)}:null;
  const audio=finite((metrics.beeps??[]).filter(b=>b.timestamp_available).map(b=>b.display_time-b.context_time*1000));
  const frames=(metrics.frames??[]).filter(f=>Number.isFinite(f.result_time)&&Number.isFinite(f.display_time));
  const points=frames.map(f=>({segment:f.segment,result_time:f.result_time,offset_ms:f.display_time-f.result_time*1000}));
  const steps=[];
  for(let i=1;i<points.length;i++){
    const delta=points[i].offset_ms-points[i-1].offset_ms;
    // A diagnostic highlight, not a pass/fail tolerance or a smoothing filter.
    if(Math.abs(delta)>5)steps.push({...points[i],previous_result_time:points[i-1].result_time,delta_ms:delta,segment_changed:points[i].segment!==points[i-1].segment});
  }
  return{audio_output_mapping:range(audio),audio_cue_count:audio.length,
    picture_clock:range(points.map(p=>p.offset_ms)),frame_observation_count:points.length,
    picture_drift_ms:points.length?points.at(-1).offset_ms-points[0].offset_ms:null,
    picture_steps:steps};
}
