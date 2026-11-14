"""Cloud activity recovery and response validation without local media."""
import json
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import cloud as service
ASSET='123e4567-e89b-42d3-a456-426614174001'
JOB='123e4567-e89b-42d3-a456-426614174002'
OPTIONS={'from_seconds':0,'interval_seconds':0.25,'threshold':0.005,'merge_gap_seconds':0.5}

def invoke(*args):
    result=CliRunner().invoke(cli,['cloud','activity',ASSET,*args])
    return result, json.loads(result.stdout)

def setup(monkeypatch,tmp_path):
    monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
    monkeypatch.setattr(service,'_connection',lambda _:('https://example.test/api/v1/account/cloud','mvs_'+'a'*43))

def job():
    return {'runtime_sha256':'d'*64,'id':JOB,'asset_id':ASSET,'state':'queued','failure_code':None,'options':OPTIONS,'result':None}

def test_lost_reply_recovers_same_key_and_options(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path);calls=[]
    def request(url,credential,**kw):
        calls.append((url,kw));return (0,{}) if len(calls)==1 else (202,{'job':job()})
    monkeypatch.setattr(service,'_request',request)
    assert invoke('--no-wait')[0].exit_code==1
    result,body=invoke('--no-wait')
    assert result.exit_code==0
    assert calls[0][1]['key']==calls[1][1]['key']==body['idempotency_key']
    assert calls[1][1]['payload']==OPTIONS
    assert calls[1][0].endswith('/activity')

def test_reopen_never_posts(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path);calls=[]
    def request(url,credential,**kw):calls.append((url,kw));return 200,{'job':job()}
    monkeypatch.setattr(service,'_request',request)
    result,body=invoke('--job-id',JOB,'--no-wait')
    assert result.exit_code==0
    assert calls==[(f'https://example.test/api/v1/assets/{ASSET}/activities/{JOB}',{'max_response_bytes':3600000})]

def test_parameters_validate_before_connection(monkeypatch):
    monkeypatch.setattr(service,'_connection',lambda _: (_ for _ in ()).throw(AssertionError('must not connect')))
    for args in [('--interval','0'),('--threshold','nan'),('--from','2','--to','1'),('--to','0.1'),('--job-id',JOB,'--threshold','0.1')]:
        result,body=invoke(*args);assert result.exit_code==1;assert body['status']=='invalid_request'

def test_wrong_asset_and_private_fields_are_not_printed(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path)
    monkeypatch.setattr(service,'_request',lambda *a,**kw:(200,{'job':{**job(),'asset_id':JOB,'result':{'source':'/private/clip'}}}))
    result,body=invoke('--no-wait');assert body['status']=='invalid_response';assert '/private' not in result.output

def test_polling_interrupt_preserves_key_and_known_job(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path);calls=[]
    def request(url,credential,**kw):
        calls.append((url,kw))
        if len(calls)>1: raise KeyboardInterrupt()
        return 202,{'job':job()}
    monkeypatch.setattr(service,'_request',request)
    monkeypatch.setattr('moviestar.cloud_activity.time.sleep',lambda _:None)
    result,body=invoke()
    assert result.exit_code==1 and body['status']=='interrupted'
    assert body['job_id']==JOB and body['idempotency_key']==calls[0][1]['key']

def test_terminal_failure_is_retained_and_retried_without_new_identity(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path);keys=[]
    def request(url,credential,**kw):
        keys.append(kw['key']);return 200,{'job':{**job(),'state':'failed','failure_code':'no_video'}}
    monkeypatch.setattr(service,'_request',request)
    for _ in range(2):
        result,body=invoke('--no-wait');assert result.exit_code==1;assert body['job']['failure_code']=='no_video'
    assert keys[0]==keys[1]

def test_unknown_options_do_not_leak_private_data(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path)
    monkeypatch.setattr(service,'_request',lambda *a,**kw:(200,{'job':{**job(),'options':{**OPTIONS,'source':'/private/file'}}}))
    result,body=invoke('--no-wait');assert body['status']=='invalid_response';assert '/private' not in result.output
