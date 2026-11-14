import json
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import cloud
A='123e4567-e89b-42d3-a456-426614174002'
J='123e4567-e89b-42d3-a456-426614174003'
OPTIONS={'from_ms':1000,'to_ms':10000,'count':1}
def job(state='queued'):
 return {'id':J,'asset_id':A,'state':state,'options':OPTIONS}

def test_skim_replays_canonical_request_after_lost_reply(monkeypatch,tmp_path):
 monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 calls=[]
 def request(url,credential,**kwargs):
  calls.append(kwargs)
  assert url.endswith(f'/assets/{A}/skim')
  if len(calls)==1:return 0,{}
  return 202,{'job':job(),'idempotency_key':kwargs['key']}
 monkeypatch.setattr(cloud,'_request',request)
 runner=CliRunner()
 first=runner.invoke(cli,['cloud','skim',A,'--from','0:01','--to','10','--count','1'])
 assert first.exit_code==1
 second=runner.invoke(cli,['cloud','skim',A,'--from','1.000','--to','0:10','--count','1'])
 assert second.exit_code==0,second.output
 assert calls[0]['key']==calls[1]['key']
 assert calls[1]['payload']==OPTIONS
 assert json.loads(second.output)['job']['id']==J

def test_skim_invalid_options_do_not_connect(monkeypatch):
 monkeypatch.setattr(cloud,'_connection',lambda _:(_ for _ in ()).throw(AssertionError('must not connect')))
 for options in [['--count','0'],['--count','17'],['--from','nan'],['--from','0.0001'],['--from','10','--to','5']]:
  r=CliRunner().invoke(cli,['cloud','skim',A]+options)
  assert r.exit_code!=0

def test_skim_show_downloads_timestamped_frames_and_prevents_overwrite(monkeypatch,tmp_path):
 from email.message import Message
 from moviestar import cloud_skim as skims
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','mvs_synthetic'))
 complete=job('succeeded');complete['result']={'mode':'visual_skim','thumbnails':[{'index':0,'at_ms':5000,'file_size_bytes':4,'mime_type':'image/jpeg'}]}
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':complete}))
 calls=[]
 class Response:
  status=200
  headers=Message();headers['Content-Type']='image/jpeg'
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def read(self,limit):return b'\xff\xd8\xff\xd9'
 class Opener:
  def open(self,request,**kwargs):
   calls.append(request.full_url)
   assert request.get_header('X-moviestar-client')=='moviestar-cli-v1'
   assert request.full_url==f'https://service.invalid/api/v1/assets/{A}/skims/{J}/images/0'
   return Response()
 monkeypatch.setattr(skims,'build_opener',lambda *_:Opener())
 out=tmp_path/'skim';args=['cloud','skim-show',A,J,'--out-dir',str(out)]
 first=CliRunner().invoke(cli,args);assert first.exit_code==0,first.output
 assert (out/'frame-00-0000005000ms.jpg').read_bytes()==b'\xff\xd8\xff\xd9'
 assert json.loads((out/'skim.json').read_text())['job']['id']==J
 second=CliRunner().invoke(cli,args);assert second.exit_code==1
 assert len(calls)==1

def test_skim_show_rejects_wrong_job_identity(monkeypatch):
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 bad=job();bad['asset_id']=J
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':bad}))
 r=CliRunner().invoke(cli,['cloud','skim-show',A,J]);assert r.exit_code==1
 assert json.loads(r.output)['status']=='invalid_response'

def test_skim_show_cleans_partial_download_without_changing_existing_files(monkeypatch,tmp_path):
 from email.message import Message
 from moviestar import cloud_skim as skims
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 complete=job('succeeded');complete['options']={**OPTIONS,'count':2}
 complete['result']={'mode':'visual_skim','thumbnails':[{'index':i,'at_ms':5000+i*1000,'file_size_bytes':4,'mime_type':'image/jpeg'} for i in range(2)]}
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':complete}))
 class Response:
  status=200
  headers=Message();headers['Content-Type']='image/jpeg'
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def read(self,limit):return b'\xff\xd8\xff\xd9'
 class Opener:
  def open(self,request,**kwargs):
   if request.full_url.endswith('/1'):raise OSError('connection lost')
   return Response()
 monkeypatch.setattr(skims,'build_opener',lambda *_:Opener())
 out=tmp_path/'skim';r=CliRunner().invoke(cli,['cloud','skim-show',A,J,'--out-dir',str(out)])
 assert r.exit_code==1 and not out.exists()


def test_skim_show_empty_index_writes_manifest_without_fabricating_images(monkeypatch,tmp_path):
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 complete=job('succeeded');complete['result']={'mode':'visual_skim','thumbnails':[],'coverage_note':'no_indexed_frames_in_range'}
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':complete}))
 out=tmp_path/'skim';r=CliRunner().invoke(cli,['cloud','skim-show',A,J,'--out-dir',str(out)])
 assert r.exit_code==0,r.output
 assert json.loads(r.output)['images']==[]
 assert list(out.iterdir())==[out/'skim.json']


def test_default_skim_omits_null_wire_end_but_replays_canonical_options(monkeypatch,tmp_path):
 monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 calls=[]
 from moviestar.cloud_creation_state import creation_request
 canonical={'from_ms':0,'to_ms':None,'count':10}
 with creation_request(f'https://service.invalid/api/v1/assets/{A}/skim','synthetic',json.dumps(canonical,sort_keys=True,separators=(',',':')),None) as pending:
  pending.save();original_key=pending.key
 def request(url,credential,**kwargs):
  calls.append(kwargs)
  assert kwargs['payload']=={'from_ms':0,'count':10}
  if len(calls)==1:return 0,{}
  return 202,{'job':{'id':J,'asset_id':A,'state':'queued','options':{'from_ms':0,'to_ms':None,'count':10}},'idempotency_key':kwargs['key']}
 monkeypatch.setattr(cloud,'_request',request)
 first=CliRunner().invoke(cli,['cloud','skim',A]);assert first.exit_code==1
 second=CliRunner().invoke(cli,['cloud','skim',A]);assert second.exit_code==0,second.output
 assert calls[0]['key']==calls[1]['key']
 assert calls[0]['key']==original_key
 assert json.loads(second.output)['job']['options']['to_ms'] is None
