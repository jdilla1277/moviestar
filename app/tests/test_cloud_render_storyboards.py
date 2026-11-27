"""Cloud frame commands bind saved output identity and verify real JPEG bytes."""
import hashlib
import json
from email.message import Message
import pytest
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import cloud

P='00000000-0000-4000-8000-000000000001'
A='00000000-0000-4000-8000-000000000002'
R='00000000-0000-4000-8000-000000000003'
J='00000000-0000-4000-8000-000000000004'
V='00000000-0000-4000-8000-000000000005'
IMAGE=b'\xff\xd8\xff\xd9'

def job(state='succeeded'):
 return {'id':J,'project_id':P,'artifact_id':A,'revision_id':R,'render_job_id':V,
         'artifact_sha256':'a'*64,'clock':'rendered_result','state':state,'request':{},
         'sampling':{'from_ms':0,'to_ms':30000,'interval_ms':2000,'interval_auto':True,'columns':5,'rows':3,'frame_count':15,'requested_times_ms':list(range(0,30000,2000))},
         'failure_code':'labels_unavailable' if state=='failed' else None,
         'result':None if state!='succeeded' else {'frames':[{'index':i,'requested_at_ms':t,'at_ms':t,'timecode':f'00:00:{t//1000:02d}.000'}for i,t in enumerate(range(0,30000,2000))],
          'selection':'frame_at_or_before','labels':'decoded_result_time',
          'extractor':{'engine_version':'b'*40,'runtime_sha256':'c'*64},
          'image':{'mime_type':'image/jpeg','width':1920,'height':720,'size_bytes':len(IMAGE),'sha256':hashlib.sha256(IMAGE).hexdigest()}}}

@pytest.fixture
def remote(monkeypatch,tmp_path):
 monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 calls=[]
 def request(url,credential,**kw):
  calls.append((url,kw));return 200,{'job':job(),'idempotency_key':kw.get('key'),'replayed':False}
 monkeypatch.setattr(cloud,'_request',request)
 return calls

def test_submit_uses_exact_artifact_and_milliseconds(remote):
 result=CliRunner().invoke(cli,['cloud','artifacts','storyboard',P,A,'--idempotency-key','retained'])
 assert result.exit_code==0,result.output
 assert json.loads(result.output)['job']==job()
 assert remote==[(f'https://service.invalid/api/v1/account/cloud/projects/{P}/artifacts/{A}/storyboards',{'payload':{},'key':'retained'})]

@pytest.mark.parametrize('key',['','x'*129,'unicode-é','a\nb'])
def test_bad_keys_fail_before_connecting(monkeypatch,key):
 monkeypatch.setattr(cloud,'_connection',lambda _:pytest.fail('must not connect'))
 r=CliRunner().invoke(cli,['cloud','artifacts','storyboard',P,A,'--idempotency-key',key])
 assert r.exit_code!=0 and json.loads(r.output)['error']['code']=='invalid_request'

@pytest.mark.parametrize('change',[{'artifact_id':R},{'revision_id':'bad'},{'project_id':A},{'clock':'source'},{'request':{'from_ms':0}}])
def test_unrelated_reply_cannot_confirm_submission(remote,monkeypatch,change):
 value=job();value.update(change)
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':value,'idempotency_key':'retained','replayed':False}))
 r=CliRunner().invoke(cli,['cloud','artifacts','storyboard',P,A,'--idempotency-key','retained'])
 assert r.exit_code!=0
 assert json.loads(r.output)['error']['code']=='submission_unconfirmed'


def test_lost_reply_recovers_with_automatically_retained_key(remote,monkeypatch):
 keys=[]
 def request(url,credential,**kw):
  keys.append(kw['key'])
  if len(keys)==1:return 0,{}
  return 200,{'job':job(),'idempotency_key':kw['key'],'replayed':True}
 monkeypatch.setattr(cloud,'_request',request)
 args=['cloud','artifacts','storyboard',P,A]
 first=CliRunner().invoke(cli,args);assert first.exit_code!=0
 error=json.loads(first.output);assert error['error']['code']=='submission_unconfirmed'
 assert 'cloud artifacts storyboard' in error['recovery_command']
 second=CliRunner().invoke(cli,args);assert second.exit_code==0,second.output
 assert keys[0]==keys[1]


def test_saved_jpeg_is_bound_to_manifest_and_never_overwrites(remote,monkeypatch,tmp_path):
 from moviestar import cloud_render_frames as frames
 class Response:
  status=200
  headers=Message();headers['Content-Type']='image/jpeg'
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def read(self,n):return IMAGE
 class Opener:
  def open(self,request,**kw):
   assert request.full_url==f'https://service.invalid/api/v1/account/cloud/projects/{P}/artifacts/{A}/storyboards/{J}/image'
   return Response()
 monkeypatch.setattr(frames,'build_opener',lambda *_:Opener())
 out=tmp_path/'frame.jpg';args=['cloud','artifacts','storyboard-show',P,A,J,'--out',str(out)]
 r=CliRunner().invoke(cli,args);assert r.exit_code==0,r.output
 d=json.loads(r.output);assert d['file_saved'] and d['out']==str(out)
 assert d['job']['sampling']['to_ms']==30000 and out.read_bytes()==IMAGE
 out.write_bytes(b'keep');assert CliRunner().invoke(cli,args).exit_code!=0
 assert out.read_bytes()==b'keep'


def test_wrong_image_hash_cannot_be_saved(remote,monkeypatch,tmp_path):
 from moviestar import cloud_render_frames as frames
 value=job();value['result']['image']['sha256']='d'*64
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':value}))
 class Response:
  status=200
  headers=Message();headers['Content-Type']='image/jpeg'
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def read(self,n):return IMAGE
 monkeypatch.setattr(frames,'build_opener',lambda *_:type('O',(),{'open':lambda *a,**kw:Response()})())
 out=tmp_path/'frame.jpg';r=CliRunner().invoke(cli,['cloud','artifacts','storyboard-show',P,A,J,'--out',str(out)])
 assert r.exit_code!=0 and not out.exists()
 assert json.loads(r.output)['error']['code']=='image_unavailable'


def test_failed_result_is_terminal_and_keeps_failure(remote,monkeypatch,tmp_path):
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':job('failed')}))
 r=CliRunner().invoke(cli,['cloud','artifacts','storyboard-show',P,A,J,'--out',str(tmp_path/'bad.jpg')])
 assert r.exit_code!=0
 d=json.loads(r.output);assert d['error']['code']=='labels_unavailable'
 assert 'Poll' not in d['error']['message']
 assert 'label/font' in d['error']['message'] and d['error']['details']['job_id']==J


@pytest.mark.parametrize('field',['wrong_label','future_sample','missing_cell','oversized_image','wrong_grid'])
def test_unverifiable_sheet_manifest_fails_before_downloading(remote,monkeypatch,tmp_path,field):
 value=job()
 if field=='wrong_label':value['result']['frames'][1]['timecode']='00:00:00.000'
 if field=='future_sample':value['result']['frames'][1]['at_ms']=2001
 if field=='missing_cell':value['result']['frames'].pop()
 if field=='oversized_image':value['result']['image']['size_bytes']=2097153
 if field=='wrong_grid':value['sampling']['rows']=2
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':value}))
 out=tmp_path/'never.jpg'
 r=CliRunner().invoke(cli,['cloud','artifacts','storyboard-show',P,A,J,'--out',str(out)])
 assert r.exit_code!=0 and not out.exists() and json.loads(r.output)['error']['code']=='invalid_response'
