"""Read/validate CLI contracts; transport examples use synthetic responses."""
import json
from click.testing import CliRunner
import pytest
from moviestar import cloud
from moviestar.cli import cli

P = '00000000-0000-4000-8000-000000000010'
REQUEST = {'contract_version':'cloud-editing-v1','expected_base_revision':None,
           'operation':{'type':'trim','source_id':'speaker','time_space':'source_result','from_ms':10000,'to_ms':40000}}

@pytest.fixture
def remote(monkeypatch):
    calls=[]
    monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
    capability={'contract_version':'m33.3-personal-projects-1','project_access':'owner_only','operations':['projects:read'],
                'editing':{'contract_version':'cloud-editing-v1','read_available':True,'validation_available':True,'writes_available':False,'state_scope':'unsaved_baseline_only'}}
    response={'contract_version':'cloud-editing-v1','status':'no_saved_edit','project_id':P,'revision':None,'current_revision':None,'baseline':None}
    def request(url,credential,**kwargs):
        calls.append((url,kwargs))
        return 200, capability if url.endswith('/capabilities') else response
    monkeypatch.setattr(cloud,'_request',request)
    return calls,response,capability

def test_show_and_validate_use_same_canonical_envelopes_and_never_mutate(remote,tmp_path):
    calls,response,_=remote
    read=CliRunner().invoke(cli,['cloud','edits','show',P])
    assert read.exit_code==0,read.output
    assert json.loads(read.output)==response
    response.update(status='would_apply',changes=True,current_undo_remaining=0,projected_resolved={'undo_remaining':1})
    response.pop('revision')
    file=tmp_path/'trim.json';file.write_text(json.dumps(REQUEST))
    result=CliRunner().invoke(cli,['cloud','edits','validate',P,'--request',str(file)])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)==response
    assert calls[-1]==('https://service.invalid/api/v1/account/cloud/projects/'+P+'/edit/validate',{'payload':REQUEST})
    assert all('/mutations' not in url for url,_ in calls)


def test_saved_mix_reads_and_candidate_keep_same_revision_and_transport(remote,tmp_path):
    calls,response,_=remote
    revision='00000000-0000-4000-8000-000000000099'
    response.update(status='opened',current_revision=revision,revision={'id':revision,'spec':{'audio_mix':{'source_audio':{'gain_db':-2},'tracks':[]}}})
    read=CliRunner().invoke(cli,['cloud','edits','show',P])
    assert read.exit_code==0, read.output
    assert json.loads(read.output)==response
    response.update(status='would_apply',current_undo_remaining=3,changes=True,projected_resolved={'undo_remaining':4})
    response.pop('revision')
    request={'contract_version':'cloud-editing-v1','expected_base_revision':revision,'operation':{'type':'audio_source','gain_db':-6}}
    file=tmp_path/'audio.json';file.write_text(json.dumps(request))
    checked=CliRunner().invoke(cli,['cloud','edits','validate',P,'--request',str(file)])
    assert checked.exit_code==0,checked.output
    assert json.loads(checked.output)==response
    assert calls[-1][1]=={'payload':request}

@pytest.mark.parametrize('raw',['[]','null','{','{"owner_id":"untrusted"}','x'*4097])
def test_malformed_or_oversized_request_is_rejected_before_connection(remote,tmp_path,raw):
    calls,_,_=remote
    file=tmp_path/'request.json';file.write_text(raw)
    result=CliRunner().invoke(cli,['cloud','edits','validate',P,'--request',str(file)])
    assert result.exit_code!=0
    assert json.loads(result.output)['error']['code']=='invalid_request'
    assert calls==[]

def test_unsupported_and_foreign_responses_fail_closed(remote,monkeypatch):
    _,response,capability=remote
    capability['editing']['read_available']=False
    result=CliRunner().invoke(cli,['cloud','edits','show',P])
    assert result.exit_code!=0
    capability['editing']['read_available']=True
    response['project_id']='00000000-0000-4000-8000-000000000011'
    result=CliRunner().invoke(cli,['cloud','edits','show',P])
    assert result.exit_code!=0
    assert json.loads(result.output)['error']['code']=='invalid_response'

@pytest.mark.parametrize('status,code',[(401,'credential_invalid'),(403,'account_consent_required'),(503,'cloud_unavailable')])
@pytest.mark.parametrize('action',['show','validate','capabilities'])
def test_capability_errors_preserve_resource_error_envelope(remote,monkeypatch,tmp_path,status,code,action):
    envelope={'error':{'code':code,'message':'Service diagnostic','details':{},'retryable':status==503},'request_id':'synthetic-request'}
    monkeypatch.setattr(cloud,'_request',lambda *a,**k:(status,envelope))
    args=['cloud','edits',action]
    if action!='capabilities': args.append(P)
    if action=='validate':
        file=tmp_path/'request.json';file.write_text(json.dumps(REQUEST));args+=['--request',str(file)]
    result=CliRunner().invoke(cli,args)
    assert result.exit_code==1
    assert json.loads(result.output)==envelope
    # Legacy clients retain their existing preflight envelope.
    legacy=CliRunner().invoke(cli,['cloud','projects','show',P])
    assert json.loads(legacy.output)['status']==code

@pytest.mark.parametrize('status,code',[(401,'credential_invalid'),(403,'account_consent_required'),(503,'cloud_unavailable')])
@pytest.mark.parametrize('action',['show','validate'])
def test_resource_errors_use_same_envelope_after_successful_capabilities(remote,monkeypatch,tmp_path,status,code,action):
    _,_,capability=remote
    envelope={'error':{'code':code,'message':'Service diagnostic','details':{},'retryable':status==503},'request_id':'synthetic-resource'}
    monkeypatch.setattr(cloud,'_request',lambda url,*a,**k:(200,capability) if url.endswith('/capabilities') else (status,envelope))
    args=['cloud','edits',action,P]
    if action=='validate':
        file=tmp_path/'request.json';file.write_text(json.dumps(REQUEST));args+=['--request',str(file)]
    result=CliRunner().invoke(cli,args)
    assert result.exit_code==1
    assert json.loads(result.output)==envelope
