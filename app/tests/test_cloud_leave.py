import json
import pytest
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import cloud
ID='123e4567-e89b-42d3-a456-426614174001'
@pytest.fixture
def remote(monkeypatch,tmp_path):
 monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 state={'calls':[],'supported':True,'status':200,'receipt':None}
 monkeypatch.setattr(cloud,'_capabilities',lambda *_:{'recipient_leave':{'contract_version':'recipient-leave-v1','path':'/api/v1/account/cloud/shared-access/:kind/:id/leave'}} if state['supported'] else {})
 def request(url,credential,**kwargs):
  state['calls'].append((url,kwargs))
  kind='asset' if '/asset/' in url else 'project'
  return state['status'],state['receipt'] or {'contract_version':'recipient-leave-v1','kind':kind,'resource_id':ID,'status':'left','changed':True,'remaining_access':False,'replayed':False}
 monkeypatch.setattr(cloud,'_request',request);return state

def invoke(kind):
 result=CliRunner().invoke(cli,['cloud','assets' if kind=='asset' else 'projects','leave',ID]);return result,json.loads(result.stdout)

@pytest.mark.parametrize('kind',['asset','project'])
def test_leave_is_explicit_and_preserves_retry_key_after_uncertain_reply(remote,kind):
 remote['status']=0
 result,body=invoke(kind);assert result.exit_code==1
 assert body['status']=='leave_unconfirmed' and body['resource_id']==ID and body['kind']==kind
 key=body['idempotency_key'];assert remote['calls']==[(f'https://service.invalid/api/v1/account/cloud/shared-access/{kind}/{ID}/leave',{'payload':{},'key':key})]
 remote['status']=200
 result,body=invoke(kind);assert result.exit_code==0,body
 assert body['idempotency_key']==key and len(remote['calls'])==2
 assert remote['calls'][-1][1]['key']==key

@pytest.mark.parametrize('kind',['asset','project'])
def test_old_services_fail_before_sending_leave(remote,kind):
 remote['supported']=False;result,body=invoke(kind)
 assert result.exit_code==1 and body['status']=='unsupported_operation' and remote['calls']==[]
