"""Real CLI/browser cut playback: visible frames, shared-clock cues and continuity."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

import pytest

from moviestar.spatial_layout import browser_available, _chrome_path


@pytest.mark.skipif(not browser_available(),reason='Chrome and Node required')
@pytest.mark.parametrize('offsets,surround',[((1,0,4),False),((5,5,4),False),((1,0,4),True)])
def test_actual_native_cuts_keep_source_frames_and_audio_continuity(tmp_path,offsets,surround):
    repo=Path(__file__).parents[2]
    root=tmp_path/'project'
    subprocess.run([sys.executable,str(repo/'experiments/live_browser_preview/cuts.py'),'--out',str(root),'--offsets',*(str(x) for x in offsets)],check=True,capture_output=True,text=True,timeout=40)
    if surround:
        # Put the original cue signal in FC and original pilot in FR before
        # the final AAC encode. Re-encoding the already-lossy stereo mix can
        # notch its first cue into two threshold onsets after center downmix.
        # A stereo bus must still downmix speakers, not drop channels 2+.
        from moviestar.spec import load_spec, save_spec
        spec=load_spec(root); replacement=root/'surround.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(root/'source-b.mp4'),'-i',str(root/'cues.mp4'),
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=8','-filter_complex',
            '[1:a][2:a]amerge=inputs=2[signals];[signals]pan=5.1|FL=0*c0|FR=c1|FC=c0|LFE=0*c0|BL=0*c0|BR=0*c0[a]',
            '-map','0:v','-map','[a]','-c:v','copy','-c:a','aac','-movflags','+faststart',str(replacement)],check=True,timeout=30)
        replacement.replace(root/'source-b.mp4')
        from tests.native_cue_reference import assert_surround_cue_reference
        assert_surround_cue_reference(root/'source-b.mp4')
        subprocess.run([sys.executable,'-m','moviestar.cli','load',str(root/'source-a.mp4'),str(root/'source-b.mp4'),
            '--as','camera','--as','other','--force','--no-frames','--no-transcribe'],cwd=root,check=True,capture_output=True,text=True,timeout=30)
        save_spec(spec,cwd=root)
    chrome=os.environ.get('MOVIESTAR_CHROME_PATH') or str(_chrome_path())
    with (tmp_path/'server.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-m','moviestar.cli','preview','--workspace',str(root),'--mode','native','--from','0','--to','9','--port','0','--no-open'],stdout=subprocess.PIPE,stderr=log,text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout,selectors.EVENT_READ)
                assert selector.select(15),'Server did not start'
                url=json.loads(server.stdout.readline())['url']
            script=r'''
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const {Browser}=await import(HELPER),b=await Browser.launch(CHROME,OUT);
const {assertPresentedSegment}=await import(FRAME_AUDIT);
const exceptions=[];b.socket.addEventListener('message',event=>{const message=JSON.parse(event.data);if(message.method==='Runtime.exceptionThrown')exceptions.push(message.params.exceptionDetails);});
try{
 await b.send('Page.navigate',{url:URL+'?audit=1'});
 await b.wait('window.movieStarPreview?.is_current',20000);
 for(const [at,segment,barcode] of [[0,0,OFFSETS[0]*30],[3-1/30,0,(OFFSETS[0]+3)*30-1],[3,1,OFFSETS[1]*30],[3+1/30,1,OFFSETS[1]*30+1],[6-1/30,1,(OFFSETS[1]+3)*30-1],[6,2,120],[6+1/30,2,121]]){
  const sample=await b.evaluate(`(async()=>{const api=window.movieStarPreview.stage;await api.seek(${at});return api.snapshot();})()`);
  assert.equal(sample.segment,segment);assert.equal(sample.source.number,barcode,`Source frame at ${at}s`);
 }
 await b.evaluate('window.movieStarPreview.stage.seek(0)');await b.click('#play');
 await b.wait('window.movieStarPreview.stage.metrics.finished',15000);
 const m=await b.evaluate(`(()=>{
  const root=document.querySelector('iframe:not(.pending)').contentDocument;
  return {...window.movieStarPreview.stage.metrics,segment_initial_presented:[...root.querySelectorAll('iframe')].map(frame=>{
   const api=frame.contentWindow.moviestarLiveStage;
   return api.metrics.initial_presented.find(item=>JSON.stringify(item.path)===JSON.stringify(api.packet.video_path));
  })};
 })()`);
 await writeFile(OUT+'/playback.json',JSON.stringify(m));
 assert.deepEqual(exceptions,[],'No uncaught browser exceptions');
 assert.equal(m.cuts.length,2);assert.ok(Math.abs(m.end_result_time-9)<=.05);
 const first=m.frames[0],zero=first.display_epoch-first.result_time*1000;
 assert.ok(Math.abs(m.finished_at-zero-9000)<=50,'Program clock stays aligned across cuts');
 assert.equal(m.stalls,0);assert.equal(m.quality.droppedVideoFrames,0);
 for(const [i,start] of OFFSETS.entries()){
  const frames=m.frames.filter(f=>f.segment===i);
  assert.ok(frames.every(f=>f.media_time>=start-1e-6&&f.media_time<start+3-1e-6),'No outgoing frame beyond its cut');
  const mismatches=frames.filter(f=>f.number!==Math.round(f.media_time*30)%240);
  assert.deepEqual(mismatches,[],`Segment ${i}: barcode pixels must match each source timestamp`);
  const shown=m.segments_shown.find(s=>s.segment===i);
  assert.equal(shown.source.number,start*30);
  // Keep the 90-frame requirement. rVFC can omit an observation while its
  // compositor counter advances; do not mistake callbacks for submissions.
  m.segment_presentations??=[];
  m.segment_presentations.push(assertPresentedSegment({baseline:m.segment_initial_presented[i],frames,shown,start,fps:30,count:90}));
 }
 await writeFile(OUT+'/playback.json',JSON.stringify(m));
 assert.equal(m.flashes.length,9);assert.equal(m.beeps.length,9);
 m.flashes.forEach((f,i)=>assert.ok(Math.abs(m.beeps[i].display_time-f.display_time)<=50,'Cue sync survives the cut'));
 const blocks=m.audio_blocks,firstBlock=blocks.findIndex(x=>x.rms>.01),last=blocks.findLastIndex(x=>x.rms>.01);
 assert.ok(last>firstBlock,'Pilot tone reaches the shared output');
 let gap=0,maxGap=0;for(let i=firstBlock;i<=last;i++){gap=blocks[i].rms<.001?gap+blocks[i].duration:0;maxGap=Math.max(maxGap,gap);}
 assert.ok(maxGap<=Math.max(...blocks.map(block=>block.duration))+1e-6,`No silent gap longer than one audio quantum: ${maxGap*1000}ms`);
 const final=await b.evaluate('window.movieStarPreview.stage.snapshot()');assert.equal(final.source.number,209,'Hold the final expected frame');
 // Restart after completion and pause immediately; no late handoff may restart audio.
 await b.click('#restart');await b.click('#play');await b.click('#pause');
 assert.equal(await b.evaluate('window.movieStarPreview.stage.paused'),true);
 // Audio is activated. Seek across a cut and press Play before seeks settle.
 const pendingPlay=await b.evaluate(`(async()=>{const api=window.movieStarPreview.stage;await api.seek(0);const seek=api.seek(4);await api.play();await seek;return{segment:api.snapshot().segment,time:api.time,paused:api.paused};})()`);
 assert.equal(pendingPlay.segment,1,'Play preserves the pending cross-cut destination');
 assert.ok(Math.abs(pendingPlay.time-4)<.1);assert.equal(pendingPlay.paused,false);
 await b.wait('window.movieStarPreview.stage.metrics.frames.some(frame=>frame.segment===1&&frame.result_time>=4)',2000);
 await b.click('#pause');

}finally{await b.close();}
'''
            values={'FRAME_AUDIT':(repo/'experiments/live_browser_preview/presented_frames.mjs').as_uri(),'HELPER':(repo/'experiments/live_browser_preview/browser.mjs').as_uri(),'CHROME':chrome,'OFFSETS':list(offsets),'OUT':str(tmp_path/'browser'),'URL':url}
            prefix=''.join('const '+key+'='+json.dumps(value)+';\n' for key,value in values.items())
            result=subprocess.run(['node','--input-type=module','-e',prefix+script],capture_output=True,text=True,timeout=45)
            assert result.returncode==0,result.stdout+result.stderr
        finally:
            server.terminate()
            try:server.wait(timeout=9)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            server.stdout.close()
