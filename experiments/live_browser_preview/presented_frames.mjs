// A rVFC callback is a sample, not a count of compositor submissions:
// https://wicg.github.io/video-rvfc/#dom-videoframecallbackmetadata-presentedframes
import assert from 'node:assert/strict';

export function assertPresentedSegment({baseline,frames,shown,start,fps,count,barcodePeriod=240}){
  assert.ok(baseline&&Number.isInteger(baseline.presented_frames),'Prepared compositor baseline is required');
  assert.ok(Math.abs(baseline.media_time-start)<1e-6,'Baseline must be the prepared first source frame');
  const first=Math.round(start*fps);
  assert.equal(shown.source.number,first%barcodePeriod,'Prepared first barcode pixels');
  assert.ok(frames.length>0,'Playback has source-frame observations');
  let previous=-1;
  for(const frame of frames){
    const index=Math.round((frame.media_time-start)*fps);
    assert.ok(index>=0&&index<count,'No outgoing frame beyond its cut');
    assert.ok(Math.abs((frame.media_time-start)*fps-index)<1e-4,'Timestamp remains on the source frame grid');
    assert.equal(frame.number,(first+index)%barcodePeriod,`Source barcode pixels at ${frame.media_time}`);
    assert.ok(index>previous,'Observed source frames advance monotonically');
    assert.equal(frame.presented_frames-baseline.presented_frames,index,
      `Compositor submissions must match source progression at ${frame.media_time}`);
    previous=index;
  }
  assert.equal(previous,count-1,'Observe the final expected source frame');
  const presented=frames.at(-1).presented_frames-baseline.presented_frames+1;
  assert.equal(presented,count,'Every expected segment frame was submitted');
  const observed=new Set([0,...frames.map(frame=>Math.round((frame.media_time-start)*fps))]).size;
  return{presented_frames:presented,callbacks:frames.length,observed_source_frames:observed,
    unobserved_callbacks:count-observed};
}
