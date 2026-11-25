import json
import pytest
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import cloud

P='00000000-0000-4000-8000-000000000001'
R='00000000-0000-4000-8000-000000000002'
J='00000000-0000-4000-8000-000000000003'
A='00000000-0000-4000-8000-000000000004'

@pytest.fixture
def remote(monkeypatch,tmp_path):
    monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
    monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
    calls=[]
    value={'status':'accepted','replayed':False,'idempotency_key':'retained','job':{'id':J,'project_id':P,'revision_id':R,'kind':'preview','state':'queued','artifact_id':None}}
    def request(url,credential,**kwargs):
        calls.append((url,kwargs));return 200,value
    monkeypatch.setattr(cloud,'_request',request)
    return calls,value

def test_submit_uses_exact_revision_kind_and_retained_key(remote):
    calls,value=remote
    result=CliRunner().invoke(cli,['cloud','renders','submit',P,'--revision',R,'--kind','preview','--idempotency-key','retained'])
    assert result.exit_code==0,result.output
    assert json.loads(result.stdout)['job']==value['job']
    assert calls==[('https://service.invalid/api/v1/account/cloud/projects/'+P+'/render-jobs',{'payload':{'revision_id':R,'kind':'preview'},'key':'retained'})]

@pytest.mark.parametrize('change',[{'revision_id':A},{'project_id':A},{'kind':'final'},{'state':'succeeded','artifact_id':None}])
def test_submit_does_not_confirm_unrelated_or_incomplete_result(remote,change):
    _,value=remote;value['job'].update(change)
    result=CliRunner().invoke(cli,['cloud','renders','submit',P,'--revision',R,'--idempotency-key','retained'])
    assert result.exit_code!=0
    result=json.loads(result.stdout)
    assert result['error']['code']=='submission_unconfirmed'
    assert result['idempotency_key']=='retained'

def test_unknown_submission_retains_key_and_exact_retry(remote,monkeypatch):
    monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(0,{}))
    result=CliRunner().invoke(cli,['cloud','renders','submit',P,'--revision',R,'--idempotency-key','retained'])
    assert result.exit_code!=0
    result=json.loads(result.stdout)
    assert result['error']['code']=='submission_unconfirmed'
    assert result['idempotency_key']=='retained'
    assert R in result['recovery_command'] and 'retained' in result['recovery_command']

def test_show_does_not_submit_again(remote):
    calls,value=remote;value.pop('status');value.pop('idempotency_key');value.pop('replayed')
    result=CliRunner().invoke(cli,['cloud','renders','show',P,J])
    assert result.exit_code==0,result.output
    assert calls[-1]==('https://service.invalid/api/v1/account/cloud/projects/'+P+'/render-jobs/'+J,{})

def test_rejects_final_before_connection(remote):
    calls,_=remote
    result=CliRunner().invoke(cli,['cloud','renders','submit',P,'--revision',R,'--kind','final'])
    assert result.exit_code!=0 and calls==[]

def test_capabilities_preserve_render_profile_discovery(remote,monkeypatch):
    capability={'contract_version':'m33.3-personal-projects-1','project_access':'owner_only','operations':['projects:read'],
                'rendering':{'contract_version':'personal-preview-1','kinds':['preview'],'available':True,'max_result_ms':30000}}
    monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,capability))
    result=CliRunner().invoke(cli,['cloud','capabilities'])
    assert result.exit_code==0,result.output
    assert json.loads(result.stdout)['rendering']==capability['rendering']

@pytest.mark.parametrize('corrupt',[False,True])
def test_download_checks_actual_bytes_and_never_forwards_account_credentials(tmp_path,corrupt):
    import hashlib
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from moviestar.cloud_renders import download
    from datetime import datetime,timedelta,timezone
    import click
    content=b'\x00\x00\x00\x18ftypisom'+b'fixture-bytes'
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers));self.send_response(200)
            self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    body={'id':A,'project_id':P,'revision_id':R,'job_id':J,'attempt_id':J,'kind':'preview','engine_version':'b'*40,'content_type':'video/mp4','size_bytes':len(content),'sha256':'0'*64 if corrupt else hashlib.sha256(content).hexdigest(),
          'download':{'method':'GET','url':origin+'/file','required_headers':{},'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()}}
    out=tmp_path/'preview.mp4'
    try:
        if corrupt:
            with pytest.raises(click.exceptions.Exit):download(body,P,A,out,origin+'/api/v1/account/cloud')
            assert not out.exists()
        else:
            assert download(body,P,A,out,origin+'/api/v1/account/cloud')==str(out)
            assert out.read_bytes()==content
            with pytest.raises(click.exceptions.Exit):download(body,P,A,out,origin+'/api/v1/account/cloud')
            assert out.read_bytes()==content
        assert len(seen)==1 and not any(k.lower() in ('authorization','cookie','x-moviestar-client') for k in seen[0])
        assert not list(tmp_path.glob('*.part'))
    finally:
        server.shutdown();server.server_close();thread.join()
