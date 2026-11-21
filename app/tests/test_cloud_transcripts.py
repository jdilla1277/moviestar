"""Supplied Cloud transcripts: retained retry input and exact resource identity."""
import hashlib
import json
from click.testing import CliRunner
import pytest
from moviestar import cloud
from moviestar.cli import cli
P='00000000-0000-4000-8000-000000000010'
R='00000000-0000-4000-8000-000000000031'
V='00000000-0000-4000-8000-000000000201'
A='00000000-0000-4000-8000-000000000021'
TEXT='\ufeffWEBVTT\r\n\r\n00:00:00.100 --> 00:00:01.900\r\nHolden\r\n'
@pytest.fixture
def remote(monkeypatch,tmp_path):
    monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'config/account.json'))
    monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
    calls=[]
    body={'contract_version':'project-transcripts-v1','status':'imported','association':{'project_id':P,'reference_id':R,'asset_id':A,'transcript_version_id':V},
          'transcript':{'id':V,'version':1,'format':'vtt','sha256':hashlib.sha256(TEXT.encode()).hexdigest(),'size_bytes':len(TEXT.encode()),'source_duration_ms':5000,'time_space':'source','timing_provenance':{'kind':'cue_interpolated','word_timing_verified':False}},
          'replayed':False,'saved_edit_changed':False,'asr_seconds_consumed':0,'idempotency_key':'kept'}
    def request(url,credential,**kwargs):
        calls.append((url,kwargs))
        if url.endswith('/capabilities'):return 200,{'project_transcripts':{'contract_version':'project-transcripts-v1','imports_enabled':True}}
        if kwargs.get('payload') is not None:return 201,body
        return 200,{k:v for k,v in body.items() if k in ('contract_version','association','transcript')}|{'text':TEXT}
    monkeypatch.setattr(cloud,'_request',request)
    file=tmp_path/'input.vtt';file.write_bytes(TEXT.encode())
    return calls,body,file,request

def invoke(*args):return CliRunner().invoke(cli,['cloud','projects','transcripts',*args])

def test_import_retains_exact_bytes_before_post_and_reads_exact_version(remote,tmp_path,monkeypatch):
    calls,body,file,request=remote
    def checked(url,credential,**kwargs):
        if kwargs.get('payload'):
            snapshots=list((tmp_path/'config').rglob('*.transcript.json'))
            assert len(snapshots)==1
            assert json.loads(snapshots[0].read_text())['text']==TEXT
        return request(url,credential,**kwargs)
    monkeypatch.setattr(cloud,'_request',checked)
    imported=invoke('import',P,'--reference',R,'--captions',str(file),'--idempotency-key','kept')
    assert imported.exit_code==0,imported.output
    assert calls[-1][1]=={'payload':{'format':'vtt','text':TEXT},'key':'kept'}
    read=invoke('show',P,'--reference',R,'--version',V)
    assert read.exit_code==0,read.output
    assert json.loads(read.output)['text']==TEXT
    assert calls[-1][0].endswith(f'/asset-references/{R}/transcripts/{V}')

def test_uncertain_response_keeps_key_and_snapshot_for_retry(remote,monkeypatch,tmp_path):
    calls,body,file,request=remote
    def uncertain(url,*args,**kwargs):
        if kwargs.get('payload'):return 0,{}
        return request(url,*args,**kwargs)
    monkeypatch.setattr(cloud,'_request',uncertain)
    first=invoke('import',P,'--reference',R,'--captions',str(file),'--idempotency-key','kept')
    assert first.exit_code==1
    assert json.loads(first.output)['import_outcome']=='unknown'
    assert list((tmp_path/'config').rglob('*.transcript.json'))
    monkeypatch.setattr(cloud,'_request',request)
    body['replayed']=True
    second=invoke('import',P,'--reference',R,'--captions',str(file),'--idempotency-key','kept')
    assert second.exit_code==0,second.output
    assert calls[-1][1]['key']=='kept'

@pytest.mark.parametrize('field,value',[('reference_id',A),('project_id',A),('transcript_version_id',A)])
def test_wrong_receipt_identity_is_unknown_not_confirmed(remote,field,value):
    _,body,file,_=remote
    body['association'][field]=value
    result=invoke('import',P,'--reference',R,'--captions',str(file),'--idempotency-key','kept')
    assert result.exit_code==1
    assert json.loads(result.output)['import_outcome']=='unknown'

def test_missing_exact_version_and_invalid_file_do_not_contact_service(remote,tmp_path):
    calls,_,_,_=remote
    assert invoke('show',P,'--reference',R).exit_code!=0
    file=tmp_path/'provider.json';file.write_text('{}')
    assert invoke('import',P,'--reference',R,'--captions',str(file),'--idempotency-key','kept').exit_code!=0
    assert calls==[]


def test_confirmation_write_failure_does_not_claim_nothing_was_submitted(remote,monkeypatch):
    from moviestar import cloud_creation_state
    _,_,file,_=remote
    write=cloud_creation_state._write
    def fail_confirmation(path,record):
        if record.get('state')=='resolved':
            raise cloud_creation_state.CreationStateError('Synthetic disk error')
        write(path,record)
    monkeypatch.setattr(cloud_creation_state,'_write',fail_confirmation)
    result=invoke('import',P,'--reference',R,'--captions',str(file),'--idempotency-key','kept')
    assert result.exit_code==1
    assert json.loads(result.output)['import_outcome']=='unknown'
    assert 'after submission' in result.output


def test_list_metadata_and_cursor_are_forwarded(remote,monkeypatch):
    calls,body,_,request=remote
    def listing(url,*a,**kw):
        if url.endswith('/capabilities'):return request(url,*a,**kw)
        return 200,{'contract_version':'project-transcripts-v1','project_id':P,'reference_id':R,
                    'asset_id':A,'items':[body['transcript']],'next_cursor':'MQ'}
    monkeypatch.setattr(cloud,'_request',listing)
    result=invoke('list',P,'--reference',R,'--limit','1','--cursor','Mg')
    assert result.exit_code==0,result.output
    assert json.loads(result.output)['next_cursor']=='MQ'


def test_cloud_capabilities_preserves_transcript_admission_projection(remote,monkeypatch):
    _,_,_,_=remote
    projection={'contract_version':'project-transcripts-v1','imports_enabled':True,'formats':['srt','vtt'],'bounds':{'text_bytes':10000}}
    monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{
        'contract_version':'m33.3-personal-projects-1','project_access':'owner_only',
        'operations':['projects:read'],'project_transcripts':projection,
    }))
    result=CliRunner().invoke(cli,['cloud','capabilities'])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)['project_transcripts']==projection
