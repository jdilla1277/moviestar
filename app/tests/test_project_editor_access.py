import json
import pytest
from click.testing import CliRunner
from moviestar import cloud
from moviestar.cli import cli
from moviestar.cloud_project_sharing import _invitation
P='123e4567-e89b-42d3-a456-426614174001';I='123e4567-e89b-42d3-a456-426614174002'
def invitation():
 return dict(id=I,email='recipient@example.invalid',includes_assets=True,state='pending',created_at='2026-11-16T10:00:00Z',expires_at='2026-11-23T10:00:00Z',accepted_at=None,revoked_at=None,last_requested_at='2026-11-16T10:00:00Z',resend_available_at='2026-11-16T10:01:00Z',delivery='queued')
@pytest.fixture
def service(monkeypatch,tmp_path):
 monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
 state={'calls':[],'role':'editor','operations':['invitations:create','invitations:list','invitations:role'],'recipient_roles':['viewer','editor']}
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 def request(url,credential,**kwargs):
  state['calls'].append((url,kwargs))
  if url.endswith('/capabilities'):return 200,{'contract_version':'m33.3-personal-projects-1','project_access':'owner_only','operations':['projects:read','projects:rename'],'project_sharing':{'management':{'contract_version':'native-project-sharing-v1','operations':state['operations'],'recipient_roles':state['recipient_roles']}}}
  item=invitation();item['role']=state['role']
  return (200 if url.endswith('/role') else 202),{'status':'role_updated' if url.endswith('/role') else 'invitation_requested','invitation':item,'delivery':'queued'}
 monkeypatch.setattr(cloud,'_request',request);return state

def test_invites_editor_with_retained_role_and_default_viewer_compatibility(service):
 r=CliRunner().invoke(cli,['cloud','projects','share',P,'recipient@example.invalid','--role','editor']);assert r.exit_code==0,r.stdout
 b=json.loads(r.stdout);assert b['invitation']['role']=='editor';assert service['calls'][-1][1]['payload']=={'email':'recipient@example.invalid','role':'editor'}
def test_old_service_refuses_editor_before_any_invitation(service):
 service['recipient_roles']=['viewer'];r=CliRunner().invoke(cli,['cloud','projects','share',P,'recipient@example.invalid','--role','editor']);assert r.exit_code==1;assert json.loads(r.stdout)['status']=='unsupported_operation';assert len(service['calls'])==1

def test_role_change_keeps_precondition_and_confirms_exact_result(service):
 r=CliRunner().invoke(cli,['cloud','projects','set-access-role',I,'editor','--expected-role','viewer']);assert r.exit_code==0,r.stdout
 assert service['calls'][-1][1]=={'payload':{'role':'editor','expected_role':'viewer'}}
 assert service['calls'][-1][0]==f'https://service.invalid/api/v1/account/project-invitations/{I}/role'
def test_parses_editor_project_and_rejects_manager():
 raw={'id':P,'owner_id':I,'title':'Project','state':'active','current_revision':None,'access':{'role':'editor'}};assert cloud._project(raw)['access']['role']=='editor'
 raw['access']['role']='manager';assert cloud._project(raw) is None
 raw=invitation();raw['role']='manager';assert _invitation(raw) is None
