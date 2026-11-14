"""Read-only diagnosis separates mapped audio jitter from picture-clock steps."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
def test_clock_report_separates_cut_delay_and_later_presentation_delay():
    module=(Path(__file__).parents[2]/'experiments/live_browser_preview/audio_clock_report.mjs').as_uri()
    script='''
import assert from 'node:assert/strict';
const {describeAudioClocks}=await import(MODULE);
const metrics={beeps:[0,1,2].map(i=>({context_time:i+.2,display_time:1000+(i+.2)*1000,timestamp_available:true})),
 frames:[{segment:0,result_time:.2,display_time:1216.7},
 {segment:1,result_time:1.033333,display_time:2066.733},
 {segment:1,result_time:1.233333,display_time:2283.433}]};
const before=JSON.stringify(metrics),report=describeAudioClocks(metrics);
assert.equal(report.audio_output_mapping.spread_ms,0);
assert.ok(Math.abs(report.picture_drift_ms-33.4)<1e-6);
assert.deepEqual(report.picture_steps.map(s=>s.segment_changed),[true,false]);
assert.equal(JSON.stringify(metrics),before,'Diagnosis must not rewrite the acceptance input');
assert.equal(describeAudioClocks({}).audio_output_mapping,null);
assert.equal(describeAudioClocks({}).picture_drift_ms,null);
'''
    result=subprocess.run(['node','--input-type=module','-e','const MODULE='+json.dumps(module)+';'+script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
