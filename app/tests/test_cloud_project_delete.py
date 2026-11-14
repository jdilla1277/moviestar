import json
import pytest
from click.testing import CliRunner
from moviestar import cloud
from moviestar.cli import cli
P='123e4567-e89b-42d3-a456-426614174001';D='123e4567-e89b-42d3-a456-426614174002'
@pytest.fixture
def remote(monkeypatch):
 state={'calls':[],'status':202,'body':{'status':'deleted','project_id':P,'deletion':{'id':D,'state':'pending'},'original_assets_preserved':True},'supported':True}
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 def request(url,credential,**kwargs):
  state['calls'].append((url,kwargs))
  if url.endswith('/capabilities'):return 200,{'contract_version':'m33.3-personal-projects-1','project_access':'owner_only','operations':['projects:read']+(['projects:delete'] if state['supported'] else [])}
  return state['status'],state['body']
 monkeypatch.setattr(cloud,'_request',request);return state

def invoke(*args):
 r=CliRunner().invoke(cli,['cloud','projects','delete',P,*args]);return r,json.loads(r.stdout)
def test_requires_explicit_confirmation_before_any_remote_access(remote):
 r,b=invoke();assert r.exit_code==1 and b['status']=='confirmation_required';assert remote['calls']==[]
def test_confirmed_delete_and_retry_use_one_identical_delete_without_live_project_read(remote):
 for _ in range(2):
  r,b=invoke('--confirm');assert r.exit_code==0 and b['status']=='deleted';assert b['deletion']['state']=='pending';assert b['original_assets_preserved'] is True
 writes=[c for c in remote['calls'] if not c[0].endswith('/capabilities')];assert writes==[(f'https://service.invalid/api/v1/account/cloud/projects/{P}',{'method':'DELETE','payload':{'confirm_project_id':P}})]*2
 assert all('/projects?' not in u for u,_ in remote['calls'])
@pytest.mark.parametrize('status',[0,500,503])
def test_unknown_reply_keeps_same_project_identity_without_auto_retry(remote,status):
 remote['status']=status;remote['body']={};r,b=invoke('--confirm');assert r.exit_code==1 and b['deletion_outcome']=='unknown' and b['project_id']==P;assert len(remote['calls'])==2
def test_other_project_or_malformed_receipt_is_not_success(remote):
 remote['body']['project_id']=D;r,b=invoke('--confirm');assert r.exit_code==1 and b['deletion_outcome']=='unknown'
def test_unsupported_service_sends_no_mutation(remote):
 remote['supported']=False;r,b=invoke('--confirm');assert r.exit_code==1 and b['status']=='unsupported_operation';assert len(remote['calls'])==1
def test_viewer_or_foreign_project_rejection_is_preserved(remote):
 remote['status']=404;remote['body']={'error':{'code':'not_found'}};r,b=invoke('--confirm');assert r.exit_code==1 and b['deletion_outcome']=='rejected'
