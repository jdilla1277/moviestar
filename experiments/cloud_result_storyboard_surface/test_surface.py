import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from click.testing import CliRunner
import pytest
spec=importlib.util.spec_from_file_location('sheet_surface',Path(__file__).with_name('surface.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def run(surface='cli',scenario='ready',command='storyboard-show',extra=()):
 args=['--surface',surface,'--scenario',scenario,command,m.PROJECT,m.ARTIFACT]
 args+=['--idempotency-key','retained'] if command=='storyboard' else [m.JOB]
 r=CliRunner().invoke(m.app,[*args,*extra]);return r,json.loads(r.output)

def test_three_surfaces_same_sheet_identity_and_sampling():
 jobs=[run(surface=s)[1]['response']['job']for s in ['cli','api','mcp']]
 assert jobs[0]==jobs[1]==jobs[2]
 j=jobs[0];assert j['clock']=='rendered_result' and j['revision_id']==m.REVISION
 assert len(j['result']['frames'])==j['sampling']['frame_count']==15
 assert j['sampling']['requested_times_ms'][-1]<j['sampling']['to_ms']==30000
 assert all(f['at_ms']<=f['requested_at_ms'] for f in j['result']['frames'])

def test_transport_does_not_invent_mcp_request_argument():
 _,api=run('api',command='storyboard');assert api['transport']['body']=={}
 _,tool=run('mcp',command='storyboard')
 assert set(tool['transport']['arguments'])=={'project_id','artifact_id','idempotency_key'}

@pytest.mark.parametrize('scenario',['replay','paused-replay'])
def test_retained_replay_recovers_original_plan_even_when_admission_paused(scenario):
 r,d=run(scenario=scenario,command='storyboard');assert r.exit_code==0
 assert d['response']['replayed'] and d['response']['job']==m.resource()

def test_unknown_reply_preserves_exact_retry_identity_without_claiming_zero_effects():
 r,d=run(scenario='lost-response',command='storyboard');assert r.exit_code!=0
 assert d['effects']is None and d['response']['error']['details']['recovery']['request']=={}

@pytest.mark.parametrize('scenario',['missing','foreign','revoked'])
def test_no_disclosure(scenario):
 r,d=run(scenario=scenario);assert r.exit_code!=0 and d['http_status']==404
 assert d['response']['error']['code']=='not_found'

def test_paused_new_work_keeps_saved_sheet_readable():
 assert run(scenario='disabled',command='storyboard')[1]['http_status']==503
 assert run(scenario='disabled')[0].exit_code==0

def test_mcp_native_image_matches_manifest():
 _,d=run('mcp');image=next(c for c in d['mcp_result']['content']if c['type']=='image')
 b=base64.b64decode(image['data']);i=d['response']['job']['result']['image']
 assert hashlib.sha256(b).hexdigest()==i['sha256'] and len(b)==i['size_bytes']
 assert not d['proposed_image_delivery']['native_perception_verified']

def test_save_and_no_overwrite(tmp_path):
 out=tmp_path/'sheet.jpg';r,d=run(extra=['--out',str(out)])
 assert r.exit_code==0 and d['file_saved'] and out.read_bytes()==m.fixture_bytes()
 out.write_bytes(b'preserve');r,d=run(extra=['--out',str(out)])
 assert r.exit_code!=0 and out.read_bytes()==b'preserve'

@pytest.mark.parametrize('scenario',['pending','failed'])
def test_no_image_for_unfinished_or_terminal_failure(tmp_path,scenario):
 out=tmp_path/'never.jpg';r,d=run(scenario=scenario,extra=['--out',str(out)])
 assert r.exit_code!=0 and not out.exists()
 assert d['response']['error']['code']==('labels_unavailable'if scenario=='failed'else'storyboard_not_ready')

@pytest.mark.parametrize('key',['','x'*129,'not valid','é'])
def test_key_validation(key):
 r=CliRunner().invoke(m.app,['storyboard',m.PROJECT,m.ARTIFACT,'--idempotency-key',key])
 assert r.exit_code!=0 and json.loads(r.output)['response']['error']['code']=='invalid_request'

def test_pending_save_is_retryable_and_help_explains_key_and_end_boundary(tmp_path):
    _,d=run(scenario='pending',extra=['--out',str(tmp_path/'wait.jpg')])
    assert d['response']['error']['retryable'] is True
    text=' '.join(CliRunner().invoke(m.app,['storyboard','--help']).output.split())
    assert '1–128 ASCII' in text and 'end is excluded' in text

@pytest.mark.parametrize('key',['a','a'*128])
def test_key_boundaries_are_accepted(key):
    r=CliRunner().invoke(m.app,['storyboard',m.PROJECT,m.ARTIFACT,'--idempotency-key',key])
    assert r.exit_code==0 and json.loads(r.output)['response']['idempotency_key']==key

def test_decoded_timestamp_example_and_actionable_terminal_failure(tmp_path):
    _,d=run();f=d['response']['job']['result']['frames'][1]
    assert f=={'index':1,'requested_at_ms':2000,'at_ms':1967,'timecode':'00:00:01.967'}
    _,d=run(scenario='failed',extra=['--out',str(tmp_path/'failed.jpg')])
    assert d['response']['error']['code']=='labels_unavailable'
    assert d['response']['error']['details']['job_id']==m.JOB
