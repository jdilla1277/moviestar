"""Explicit personal-key Cloud connection never falls back to installation state."""
import json
import pytest
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import cloud
KEY='mvsk_'+'k'*43
URL='https://preview.example.invalid/api/v1/account/cloud'

@pytest.fixture
def isolated(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Installation state/keyring must not be read in API-key mode')
    monkeypatch.setattr(cloud,'_read_state',forbidden)
    monkeypatch.setattr(cloud,'_credential_for_connection',forbidden)
    monkeypatch.setenv('MOVIESTAR_API_KEY',KEY)
    monkeypatch.setenv('MOVIESTAR_CLOUD_URL',URL)


def test_explicit_api_key_connection(isolated):
    assert cloud._connection('cloud edits show')==(URL,KEY)

@pytest.mark.parametrize('value',['','mvs_'+'x'*43,KEY+'\n',' '+KEY,'mvsk_short'])
def test_invalid_explicit_key_never_falls_back(isolated,monkeypatch,value):
    monkeypatch.setenv('MOVIESTAR_API_KEY',value)
    result=CliRunner().invoke(cli,['cloud','edits','capabilities'])
    assert result.exit_code==1
    assert json.loads(result.output)['status']=='credential_invalid'
    if value:assert value not in result.output

@pytest.mark.parametrize('url',['','http://remote.invalid/api/v1/account/cloud',URL+'?q=1',URL+'#x','https://user:pass@preview.invalid/api/v1/account/cloud','https://preview.invalid/other'])
def test_api_key_requires_explicit_safe_service_url(isolated,monkeypatch,url):
    monkeypatch.setenv('MOVIESTAR_CLOUD_URL',url)
    result=CliRunner().invoke(cli,['cloud','edits','capabilities'])
    assert result.exit_code==1 and json.loads(result.output)['status']=='invalid_configuration'
    assert KEY not in result.output


def test_explicit_url_without_key_never_uses_installation(isolated,monkeypatch):
    monkeypatch.delenv('MOVIESTAR_API_KEY')
    result=CliRunner().invoke(cli,['cloud','edits','capabilities'])
    assert result.exit_code==1 and json.loads(result.output)['status']=='credential_invalid'


@pytest.mark.parametrize('redirect',[False,True])
def test_cli_uses_api_key_header_over_real_http_without_installation(isolated,monkeypatch,redirect):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from threading import Thread
    project='00000000-0000-4000-8000-000000000010'
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            seen.append((self.path,self.headers.get('Authorization')))
            if redirect:
                self.send_response(302);self.send_header('Location',f'http://localhost:{self.server.server_port}/do-not-forward');self.end_headers();return
            if self.path.endswith('/capabilities'):
                body={'contract_version':'m33.3-personal-projects-1','project_access':'owner_only','operations':['projects:read'],
                      'editing':{'contract_version':'cloud-editing-v1','read_available':True}}
            else:
                body={'contract_version':'cloud-editing-v1','project_id':project,'current_revision':None,'revision':None,'baseline':None,'status':'no_saved_edit'}
            raw=json.dumps(body).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        monkeypatch.setenv('MOVIESTAR_CLOUD_URL',f'http://127.0.0.1:{server.server_port}/api/v1/account/cloud')
        result=CliRunner().invoke(cli,['cloud','edits','show',project])
        if redirect:
            assert result.exit_code==1 and len(seen)==1
            assert KEY not in result.output
            return
        assert result.exit_code==0,result.output
        assert json.loads(result.output)['status']=='no_saved_edit'
        assert len(seen)==2 and all(token=='Bearer '+KEY for _,token in seen)
        assert KEY not in result.output
    finally:server.shutdown();server.server_close();thread.join(timeout=2)
