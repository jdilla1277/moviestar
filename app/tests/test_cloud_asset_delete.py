import json
import pytest
from click.testing import CliRunner
from moviestar import cloud
from moviestar.cli import cli
P='123e4567-e89b-42d3-a456-426614174001';D='123e4567-e89b-42d3-a456-426614174002'
@pytest.fixture
def remote(monkeypatch):
 state={'calls':[],'status':202,'body':{'status':'deleted','asset_id':P,'deletion':{'id':D,'state':'pending'},'references_removed':2,'projects_preserved':True,'storage_released':False},'supported':True}
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 def request(url,credential,**kwargs):
  state['calls'].append((url,kwargs))
  if url.endswith('/capabilities'):return 200,{'contract_version':'m33.3-personal-projects-1','project_access':'owner_only','operations':['projects:read'],'project_assets':{'contract_version':'project-asset-references-v1','access':'owned_assets_in_owned_projects','ready_only':True,'kinds':['video','audio','image','transcript'],'operations':['assets:delete'] if state['supported'] else []}}
  return state['status'],state['body']
 monkeypatch.setattr(cloud,'_request',request);return state

def invoke(*args):
 r=CliRunner().invoke(cli,['cloud','assets','delete',P,*args]);return r,json.loads(r.stdout)
def test_requires_explicit_confirmation_before_any_remote_access(remote):
 r,b=invoke();assert r.exit_code==1 and b['status']=='confirmation_required';assert remote['calls']==[]
def test_confirmed_delete_and_retry_use_one_identical_delete_without_live_asset_read(remote):
 for _ in range(2):
  r,b=invoke('--confirm');assert r.exit_code==0 and b['status']=='deleted';assert b['deletion']['state']=='pending';assert b['projects_preserved'] is True
 writes=[c for c in remote['calls'] if not c[0].endswith('/capabilities')];assert writes==[(f'https://service.invalid/api/v1/account/cloud/assets/{P}',{'method':'DELETE','payload':{'confirm_asset_id':P}})]*2
 assert all('/assets?' not in u for u,_ in remote['calls'])
@pytest.mark.parametrize('status',[0,500,503])
def test_unknown_reply_keeps_same_asset_identity_without_auto_retry(remote,status):
 remote['status']=status;remote['body']={};r,b=invoke('--confirm');assert r.exit_code==1 and b['deletion_outcome']=='unknown' and b['asset_id']==P;assert len(remote['calls'])==2
def test_other_asset_or_malformed_receipt_is_not_success(remote):
 remote['body']['asset_id']=D;r,b=invoke('--confirm');assert r.exit_code==1 and b['deletion_outcome']=='unknown'
def test_unsupported_service_sends_no_mutation(remote):
 remote['supported']=False;r,b=invoke('--confirm');assert r.exit_code==1 and b['status']=='unsupported_operation';assert len(remote['calls'])==1
def test_viewer_or_foreign_asset_rejection_is_preserved(remote):
 remote['status']=404;remote['body']={'error':{'code':'not_found'}};r,b=invoke('--confirm');assert r.exit_code==1 and b['deletion_outcome']=='rejected'

@pytest.mark.parametrize('released,status,state',[(True,200,'succeeded'),(False,202,'pending')])
def test_reports_verified_storage_release_separately(remote,released,status,state):
 remote['status']=status;remote['body']['storage_released']=released;remote['body']['deletion']['state']=state
 r,b=invoke('--confirm');assert r.exit_code==0;assert b['storage_released'] is released

@pytest.mark.parametrize('field,value',[('storage_released','yes'),('references_removed',True),('projects_preserved',False),('references_removed',-1)])
def test_rejects_malformed_receipts(remote,field,value):
 remote['body'][field]=value;r,b=invoke('--confirm');assert r.exit_code==1 and b['status']=='deletion_unconfirmed'

def test_pending_original_is_rejected_with_a_concrete_recovery_hint(remote):
 remote['status']=409;remote['body']={'error':{'code':'asset_not_ready'}}
 r,b=invoke('--confirm');assert r.exit_code==1 and b['status']=='asset_not_ready';assert b['deletion_outcome']=='rejected'
