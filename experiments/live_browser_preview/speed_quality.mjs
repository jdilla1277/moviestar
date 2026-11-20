import assert from 'node:assert/strict';
// Preparation includes deliberate muted play/seek work. Preserve its raw
// counters, and require zero new drops throughout the authored review interval.
export function assertReviewQuality(metrics){
  assert.equal(metrics.stalls,0,'No stalls during review');
  assert.ok(metrics.initial_presented?.length>0,'Retain the actual playback baseline');
  const before=metrics.initial_presented.reduce((sum,item)=>sum+item.quality.droppedVideoFrames,0);
  assert.ok(Number.isInteger(before)&&before>=0,'Valid raw preparation baseline');
  const delta=metrics.quality.droppedVideoFrames-before;
  assert.ok(Number.isInteger(delta)&&delta>=0,'Counters must never reset');
  assert.equal(delta,0,'Zero decoder drops during authored playback');
  assert.equal(metrics.video_quality.length,metrics.initial_presented.length);
  for(const video of metrics.video_quality)assert.equal(video.dropped_frames,0,'Every review decoder retains its frames');
  return{raw_before_review:before,raw_after_review:metrics.quality.droppedVideoFrames,playback_drops:delta};
}
