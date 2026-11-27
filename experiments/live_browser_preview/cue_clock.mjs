// Generated-fixture AV timing. Retain sparse pixel observations; correlate the
// known cue instant with the nearest presented-frame clock, within one frame.
// Zero dropped frames are required before interpreting an unsampled white
// interval against this clock. No timing limit is widened here.
export function measureCueClock(metrics,{fps,sourceStart=0,resultStart=0,count,toleranceMs=50,quality=metrics.video_quality,cueTimes,presentationUncertaintyMs=Math.max(0,...(quality??[]).map(q=>q.display_uncertainty_ms??0))}){
  const local=frame=>frame.result_time!==undefined?frame.result_time-resultStart:frame.media_time-sourceStart;
  const frames=(metrics.frames??[]).filter(f=>Number.isFinite(local(f))&&Number.isFinite(f.display_time));
  if(!Number.isFinite(presentationUncertaintyMs)||presentationUncertaintyMs<0)throw Error('Invalid presentation uncertainty');
  const result={observed_flashes:metrics.flashes?.length??0,observed_beeps:metrics.beeps?.length??0,cues:[],problems:[]};
  if(!quality?.length||quality.some(q=>q.dropped_frames!==0))result.problems.push('A complete zero-drop decoder measurement is required');
  if(!result.observed_flashes)result.problems.push('No white pixel cue was observed');
  if(result.observed_beeps!==count)result.problems.push(`Expected ${count} actual output audio cues, observed ${result.observed_beeps}`);
  const instants=cueTimes??Array.from({length:count},(_,i)=>i+.2);
  if(instants.length!==count||instants.some(t=>!Number.isFinite(t)))throw Error('Invalid generated cue instants');
  for(let i=0;i<count;i++){
    const cue=instants[i],frame=frames.reduce((best,f)=>!best||Math.abs(local(f)-cue)<Math.abs(local(best)-cue)?f:best,null);
    const beep=metrics.beeps?.[i],distance=frame?Math.abs(local(frame)-cue)*1000:null;
    if(!frame||distance>1000/fps+1e-6){result.problems.push(`Cue ${i} has no video-clock observation within one frame`);continue;}
    if(!beep?.timestamp_available||!Number.isFinite(beep.display_time)){result.problems.push(`Cue ${i} has no audio output clock`);continue;}
    const predicted=frame.display_time+(cue-local(frame))*1000,phase=beep.display_time-predicted;
    result.cues.push({cue:i,result_time:cue,video_display_time:predicted,audio_display_time:beep.display_time,
      phase_ms:phase,presentation_uncertainty_ms:presentationUncertaintyMs,phase_bound_ms:Math.abs(phase)+presentationUncertaintyMs,nearest_frame_distance_ms:distance,sampled_media_time:frame.media_time});
    if(Math.abs(phase)+presentationUncertaintyMs>toleranceMs)result.problems.push(`Cue ${i} audio/display phase ${phase}ms plus ${presentationUncertaintyMs}ms presentation uncertainty exceeds ${toleranceMs}ms`);
  }
  return result;
}
