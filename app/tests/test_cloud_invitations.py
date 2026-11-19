import json
import pytest
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import cloud
ID='123e4567-e89b-42d3-a456-426614174001'
@pytest.fixture
def remote(monkeypatch):
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 state={'calls':[],'supported':True,'body':{'contract_version':'received-invitations-v1','items':[{'id':ID,'kind':'asset','resource_id':ID,'title':'image.png','shared_by':'sender','state':'pending','role':'viewer','permissions':['assets:read','assets:download'],'includes_assets':None,'created_at':'2026-11-19T10:00:00Z','expires_at':'2026-11-26T10:00:00Z','review_path':f'/account/received-invitations?kind=asset&invitation={ID}'}],'next_cursor':None}}
 def request(url,credential,**kwargs):
  state['calls'].append((url,kwargs))
  if url.endswith('/capabilities'):return 200,{'contract_version':'m33.3-personal-projects-1','project_access':'owner_only','operations':[],**({'received_invitations':{'contract_version':'received-invitations-v1','kinds':['asset','project'],'path':'/api/v1/account/cloud/invitations','acceptance':'verified_browser_review'}} if state['supported'] else {})}
  return 200,state['body']
 monkeypatch.setattr(cloud,'_request',request);return state

def invoke(*args):
 r=CliRunner().invoke(cli,['cloud','invitations','list',*args]);return r,json.loads(r.stdout)

def test_lists_received_assets_and_projects_using_one_contract_without_accepting(remote):
 for kind in ['asset','project']:
  item=remote['body']['items'][0];item.update(kind=kind,review_path=f'/account/received-invitations?kind={kind}&invitation={ID}')
  r,b=invoke('--kind',kind,'--cursor','YWJj');assert r.exit_code==0,b
  assert b['items'][0]['review_url']==f'https://service.invalid/account/received-invitations?kind={kind}&invitation={ID}'
  assert f'kind={kind}' in remote['calls'][-1][0] and 'cursor=YWJj' in remote['calls'][-1][0]
  assert all(not kwargs for _,kwargs in remote['calls'])

def test_old_service_fails_gracefully(remote):
 remote['supported']=False;r,b=invoke();assert r.exit_code==1 and b['status']=='unsupported_operation';assert len(remote['calls'])==1

@pytest.mark.parametrize('change',[{'review_path':'https://evil.invalid/token'},{'state':'accepted'},{'kind':'video'},{'id':'not-a-uuid'}])
def test_rejects_untrusted_or_nonpending_entries(remote,change):
 remote['body']['items'][0].update(change);r,b=invoke();assert r.exit_code==1 and b['status']=='invalid_response'

def test_strips_unexpected_private_fields(remote):
 remote['body']['items'][0]['token_hash']='private';r,b=invoke();assert r.exit_code==0 and 'private' not in r.stdout

def test_preserves_project_editor_permissions_and_browser_review_link(remote):
 permissions=['projects:read','projects:rename','references:attach','references:detach']
 remote['body']['items'][0].update(kind='project',role='editor',permissions=permissions,includes_assets=True,
                                 review_path=f'/account/received-invitations?kind=project&invitation={ID}')
 result,body=invoke('--kind','project')
 assert result.exit_code==0,body
 assert body['items'][0]['role']=='editor' and body['items'][0]['permissions']==permissions
 assert body['items'][0]['includes_assets'] is True
 assert body['items'][0]['review_url']==f'https://service.invalid/account/received-invitations?kind=project&invitation={ID}'
 assert all(not kwargs for _,kwargs in remote['calls'])
