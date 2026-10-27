"""Email handoff preserves publication and delivery outcomes independently."""
import json
import pytest
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import sharing

VIDEO='123e4567-e89b-42d3-a456-426614174001'
LINK='123e4567-e89b-42d3-a456-426614174002'
ID='123e4567-e89b-42d3-a456-426614174003'
ORIGIN='https://preview.example'
TO='editor@example.com'
URL=ORIGIN+'/v/'+'s'*43
CREDENTIAL='mvs_'+'a'*43
QUOTA={'before':{'used_videos':1,'used_bytes':100},'after':{'used_videos':1,'used_bytes':100},'changed':False}

def receipt(state='accepted'):
    return {'id':ID,'link_id':LINK,'to':TO,'status':state,'created_at':'2026-10-27T12:00:00Z','accepted_at':'2026-10-27T12:00:01Z' if state=='accepted' else None}

def publication():
    return {'status':'shared','video_id':VIDEO,'quota':QUOTA,'link':{'id':LINK,'video_id':VIDEO,'url':URL,'access':'unlisted','forwardable':True,'downloadable':True,'created_at':'2026-10-27T12:00:00Z','revoked_at':None}}

@pytest.fixture
def connected(tmp_path,monkeypatch):
    state=tmp_path/'account.json'
    state.write_text(json.dumps({'installation_url':ORIGIN+'/api/v1/account/installation','connection':{'handle':'@editor','email':'e***@example.com','credential_storage':'private_file','credential_fallback':CREDENTIAL}}))
    monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(state))
    monkeypatch.delenv('MOVIESTAR_ACCOUNT_INSTALLATION_URL',raising=False)
    monkeypatch.delenv('MOVIESTAR_WATCH_ORIGIN',raising=False)

def invoke(*args):
    r=CliRunner().invoke(cli,list(args));assert r.stdout,repr(r.exception)
    return r,json.loads(r.stdout)

def test_share_to_uploads_publishes_and_requests_exactly_one_recipient(connected,tmp_path,monkeypatch):
    file=tmp_path/'cut.mp4';file.write_bytes(b'video');calls=[]
    monkeypatch.setattr(sharing,'upload_video',lambda *a,**k:{'status':'ready','video_id':VIDEO,'upload_performed':True})
    def request(url,credential,payload=None,*,method='POST'):
        calls.append((url,credential,payload))
        if url.endswith('/shares'):return 201,publication()
        return 200,{'status':'accepted','email_sent':True,'delivery':receipt()}
    monkeypatch.setattr(sharing,'_json_request',request)
    result,body=invoke('share',str(file),'--to',TO,'--idempotency-key','handoff')
    assert result.exit_code==0,result.output
    assert body['url']==URL and body['email_sent'] is True and body['upload_performed'] is True
    assert body['publication_outcome']=='shared' and body['delivery']['status']=='accepted'
    assert calls[-1]==(f'{ORIGIN}/api/v1/shares/{LINK}/deliveries',CREDENTIAL,{'to':TO,'idempotency_key':'handoff'})
    assert 'inbox' in body['hint'].lower()

@pytest.mark.parametrize('code,status,state,sent',[(202,'delivery_pending','pending',None),(409,'delivery_unknown','unknown',None),(502,'email_failed','failed',False)])
def test_delivery_problem_keeps_the_known_link_and_retry_key(connected,monkeypatch,code,status,state,sent):
    def request(url,*a,**k):
        return (201,publication()) if url.endswith('/shares') else (code,{'status':status,'email_sent':sent,'delivery':receipt(state),'hint':'private server text'})
    monkeypatch.setattr(sharing,'_json_request',request)
    result,body=invoke('share',VIDEO,'--to',TO,'--idempotency-key','same')
    assert result.exit_code==1 and body['status']==status
    assert body['url']==URL and body['link_id']==LINK and body['publication_outcome']=='shared'
    assert body['email_sent'] is sent and body['delivery']['status']==state
    assert body['idempotency_key']=='same' and 'private server text' not in result.output

@pytest.mark.parametrize('code,reply',[(0,{}),(200,{'status':'accepted','email_sent':True,'delivery':{'id':'bad'}}),(200,{'status':'accepted','email_sent':True,'delivery':receipt()|{'to':'wrong@example.com'}})])
def test_untrusted_or_lost_email_ack_never_claims_email_was_not_sent(connected,monkeypatch,code,reply):
    monkeypatch.setattr(sharing,'_json_request',lambda url,*a,**k:(201,publication()) if url.endswith('/shares') else (code,reply))
    result,body=invoke('share',VIDEO,'--to',TO,'--idempotency-key','same')
    assert result.exit_code==1 and body['email_sent'] is None and body['status']=='delivery_pending'
    assert body['url']==URL and 'same' in body['hint']

def test_existing_link_can_be_emailed_and_history_is_paginated(connected,monkeypatch):
    calls=[]
    def request(url,*a,**k):
        calls.append((url,k))
        if k.get('method')=='GET':return 200,{'status':'ok','deliveries':[receipt()],'next_cursor':ID}
        return 200,{'status':'already_accepted','email_sent':True,'delivery':receipt()}
    monkeypatch.setattr(sharing,'_json_request',request)
    result,body=invoke('shares','email',LINK,'--to',TO,'--idempotency-key','resend-one')
    assert result.exit_code==0 and body['status']=='already_accepted' and body['delivery']['to']==TO
    result,body=invoke('shares','deliveries',LINK,'--cursor',ID)
    assert result.exit_code==0 and body['deliveries']==[receipt()] and body['next_cursor']==ID
    assert calls[-1][0].endswith(f'/deliveries?cursor={ID}')

@pytest.mark.parametrize('address',['Name <a@example.com>','a@example.com,b@example.com','a@example.com~','a@example.com\r\nBcc:b@example.com'])
def test_bad_recipient_rejected_before_upload_or_credential_read(tmp_path,monkeypatch,address):
    file=tmp_path/'cut.mp4';file.write_bytes(b'video')
    monkeypatch.setattr(sharing,'_credential_for_connection',lambda *a:pytest.fail('credential read'))
    monkeypatch.setattr(sharing,'upload_video',lambda *a,**k:pytest.fail('upload'))
    result,body=invoke('share',str(file),'--to',address)
    assert result.exit_code==1 and body['email_sent'] is False

def test_email_existing_link_dry_run_never_connects(connected,monkeypatch):
    monkeypatch.setattr(sharing,'_json_request',lambda *a,**k:pytest.fail('network'))
    result,body=invoke('shares','email',LINK,'--to',TO,'--dry-run')
    assert result.exit_code==0 and body['status']=='would_email' and body['email_sent'] is False
