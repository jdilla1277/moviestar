import json
import pytest
from click.testing import CliRunner
from moviestar import cloud
from moviestar.cli import cli
P='123e4567-e89b-42d3-a456-426614174001';I='123e4567-e89b-42d3-a456-426614174002'
def invitation(**changes):
 return dict(id=I,email='recipient@example.invalid',state='pending',created_at='2026-11-16T10:00:00+00:00',expires_at='2026-11-23T10:00:00+00:00',accepted_at=None,revoked_at=None,last_requested_at='2026-11-16T10:00:00+00:00',resend_available_at='2026-11-16T10:01:00+00:00',delivery='queued',**changes)
@pytest.fixture
def remote(monkeypatch,tmp_path):
 monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
 state={'calls':[],'status':202,'body':{'status':'invitation_requested','invitation':invitation(),'delivery':'queued'},'supported':True}
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 def request(url,credential,**kwargs):
  state['calls'].append((url,kwargs))
  if url.endswith('/capabilities'):return 200,{'contract_version':'m33.3-personal-projects-1','project_access':'owner_only','operations':['projects:read'],'asset_sharing':{'management':{'contract_version':'native-asset-sharing-v1','operations':['invitations:list','invitations:create','invitations:revoke','invitations:resend'] if state['supported'] else []}}}
  return state['status'],state['body']
 monkeypatch.setattr(cloud,'_request',request);return state

def invoke(*args):
 r=CliRunner().invoke(cli,['cloud','assets',*args]);return r,json.loads(r.stdout)
def test_share_sends_one_native_invitation_and_generates_key(remote):
 r,b=invoke('share',P,'recipient@example.invalid');assert r.exit_code==0,b
 assert b['status']=='invitation_requested' and b['delivery']=='queued' and b['idempotency_key'];assert 'url' not in b
 assert remote['calls'][-1]==(f'https://service.invalid/api/v1/account/cloud/assets/{P}/invitations',{'payload':{'email':'recipient@example.invalid'},'key':b['idempotency_key']})
def test_unknown_invitation_reuses_durable_key_without_auto_retry(remote):
 remote.update(status=0,body={});r,b=invoke('share',P,'recipient@example.invalid');assert r.exit_code==1 and b['sharing_outcome']=='unknown';key=b['idempotency_key'];assert len(remote['calls'])==2
 remote.update(status=202,body={'status':'invitation_requested','invitation':invitation(),'delivery':'queued'})
 r,b=invoke('share',P,'recipient@example.invalid');assert r.exit_code==0 and b['idempotency_key']==key
 assert remote['calls'][-1][1]['key']==key
 r,b=invoke('share',P,'recipient@example.invalid');assert r.exit_code==0 and b['idempotency_key']!=key

def test_access_uses_cursor_and_does_not_send_email(remote):
 remote.update(status=200,body={'status':'ok','invitations':[invitation()],'next_cursor':I})
 r,b=invoke('access',P,'--cursor',I);assert r.exit_code==0 and b['next_cursor']==I
 assert remote['calls'][-1]==(f'https://service.invalid/api/v1/account/cloud/assets/{P}/invitations?cursor={I}',{})
def test_revoke_needs_confirmation_and_is_idempotent(remote):
 r,b=invoke('unshare',I);assert r.exit_code==1 and b['status']=='confirmation_required';assert remote['calls']==[]
 item=invitation();item.update(state='revoked',revoked_at='2026-11-16T10:00:00+00:00',delivery='cancelled')
 remote.update(status=200,body={'status':'revoked','invitation':item})
 for _ in range(2):
  r,b=invoke('unshare',I,'--confirm');assert r.exit_code==0 and b['status']=='revoked'
 assert remote['calls'][-1]==(f'https://service.invalid/api/v1/account/asset-invitations/{I}/revoke',{'payload':{}})
def test_resend_keeps_scope_and_key(remote):
 remote['body']['status']='invitation_resent';remote['body']['invitation']['token_hash']='private'
 r,b=invoke('resend-invitation',I,'--idempotency-key','retained');assert r.exit_code==0 and b['idempotency_key']=='retained';assert 'token_hash' not in b['invitation']
@pytest.mark.parametrize('status,body',[(401,{'status':'credential_invalid'}),(404,{'status':'not_found'}),(429,{'status':'rate_limited'}),(503,{'status':'sending_paused'})])
def test_safe_errors_never_claim_sent_or_expose_provider_errors(remote,status,body):
 remote.update(status=status,body={**body,'secret':'private-provider-error'});r,b=invoke('share',P,'recipient@example.invalid');assert r.exit_code==1;assert b['status']==body['status'];assert 'private-provider-error' not in r.stdout

def test_unsupported_or_invalid_inputs_send_no_write(remote):
 remote['supported']=False;r,b=invoke('share',P,'recipient@example.invalid');assert r.exit_code==1 and b['status']=='unsupported_operation';assert len(remote['calls'])==1
 remote['calls'].clear();r,b=invoke('share',P,'bad\nrecipient@example.invalid');assert r.exit_code==1 and b['status']=='invalid_request';assert remote['calls']==[]

def test_wrong_recipient_and_wrong_revoke_id_are_unconfirmed(remote):
 remote['body']['invitation']['email']='other@example.invalid';r,b=invoke('share',P,'recipient@example.invalid');assert r.exit_code==1 and b['sharing_outcome']=='unknown'
 remote.update(status=200,body={'status':'revoked','invitation':invitation()});remote['body']['invitation']['id']=P
 r,b=invoke('unshare',I,'--confirm');assert r.exit_code==1 and b['sharing_outcome']=='unknown'

@pytest.mark.parametrize('next_cursor',['https://foreign.invalid',5])
def test_access_rejects_untrusted_pagination(remote,next_cursor):
 remote.update(status=200,body={'status':'ok','invitations':[invitation()],'next_cursor':next_cursor})
 r,b=invoke('access',P);assert r.exit_code==1 and b['status']=='invalid_response'

def test_access_rejects_duplicate_ids_and_strips_private_fields(remote):
 item=invitation();item['token_hash']='server-only';remote.update(status=200,body={'status':'ok','invitations':[item],'next_cursor':None,'secret':'server-only'})
 r,b=invoke('access',P);assert r.exit_code==0 and 'server-only' not in r.stdout
 remote['body']['invitations'].append(item);r,b=invoke('access',P);assert r.exit_code==1 and b['status']=='invalid_response'

def test_unknown_revoke_keeps_id_and_sends_no_automatic_retry(remote):
 remote.update(status=0,body={});r,b=invoke('unshare',I,'--confirm');assert r.exit_code==1 and b['sharing_outcome']=='unknown' and b['invitation_id']==I;assert len(remote['calls'])==2

def test_unknown_resend_reuses_its_key(remote):
 remote.update(status=0,body={});r,b=invoke('resend-invitation',I);assert r.exit_code==1;key=b['idempotency_key']
 remote.update(status=202,body={'status':'invitation_resent','invitation':invitation(),'delivery':'queued'});r,b=invoke('resend-invitation',I);assert r.exit_code==0 and b['idempotency_key']==key
