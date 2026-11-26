import json
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import cloud
A='123e4567-e89b-42d3-a456-426614174002'
J='123e4567-e89b-42d3-a456-426614174003'
def test_request_replays_saved_key_after_lost_reply(monkeypatch,tmp_path):
 monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 calls=[]
 def request(url,credential,**kwargs):
  calls.append(kwargs)
  if len(calls)==1:return 0,{}
  return 202,{'job':{'id':J,'asset_id':A,'options':{'from_ms':83450},'sampling':{},'state':'queued'},'idempotency_key':kwargs['key']}
 monkeypatch.setattr(cloud,'_request',request)
 runner=CliRunner()
 first=runner.invoke(cli,['cloud','storyboard',A,'--from','1:23.450'])
 assert first.exit_code==1
 second=runner.invoke(cli,['cloud','storyboard',A,'--from','83.45'])
 assert second.exit_code==0,second.output
 assert calls[0]['key']==calls[1]['key']
 assert calls[1]['payload']=={'from_ms':83450}
 assert json.loads(second.output)['job']['id']==J


def test_bad_timestamps_do_not_connect(monkeypatch):
 def connection(_):
  raise AssertionError('must validate before connecting')
 monkeypatch.setattr(cloud,'_connection',connection)
 for at in ['nan','inf','-1','86401','0.0001']:
  result=CliRunner().invoke(cli,['cloud','storyboard',A,'--from',at])
  assert result.exit_code==1,result.output
  assert json.loads(result.output)['status']=='invalid_request'


def test_reopen_downloads_private_jpeg_without_overwriting(monkeypatch,tmp_path):
 from email.message import Message
 from moviestar import cloud_storyboard as storyboards
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','mvs_synthetic'))
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':{'id':J,'asset_id':A,'options':{'from_ms':0},'sampling':{},'state':'succeeded'}}))
 class Response:
  status=200
  headers=Message()
  headers['Content-Type']='image/jpeg'
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def read(self,limit):return b'\xff\xd8\xff\xd9'
 class Opener:
  def open(self,request,**kwargs):
   assert request.get_header('X-moviestar-client')=='moviestar-cli-v1'
   assert request.full_url==f'https://service.invalid/api/v1/assets/{A}/storyboards/{J}/image'
   return Response()
 monkeypatch.setattr(storyboards,'build_opener',lambda *_:Opener())
 out=tmp_path/'frame.jpg'
 args=['cloud','storyboard-show',A,J,'--out',str(out)]
 first=CliRunner().invoke(cli,args)
 assert first.exit_code==0,first.output
 assert out.read_bytes()==b'\xff\xd8\xff\xd9'
 out.write_bytes(b'keep')
 second=CliRunner().invoke(cli,args)
 assert second.exit_code==1
 assert out.read_bytes()==b'keep'


def test_range_options_and_unknown_reply_preserve_request_key(monkeypatch,tmp_path):
 monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 calls=[]
 def request(url,credential,**kwargs):
  calls.append(kwargs)
  return 202,{'job':{'id':J,'asset_id':A,'options':kwargs['payload'],'sampling':{},'state':'queued'},'idempotency_key':kwargs['key']}
 monkeypatch.setattr(cloud,'_request',request)
 result=CliRunner().invoke(cli,['cloud','storyboard',A,'--from','20:00','--to','21:00','--interval','10','--columns','3','--idempotency-key','range-key'])
 assert result.exit_code==0,result.output
 assert calls==[{'payload':{'from_ms':1200000,'to_ms':1260000,'interval_ms':10000,'columns':3},'key':'range-key'}]


def test_invalid_ranges_and_grid_do_not_connect(monkeypatch):
 monkeypatch.setattr(cloud,'_connection',lambda _:(_ for _ in ()).throw(AssertionError('must validate first')))
 for args in [['--to','0.099'],['--from','2','--to','1'],['--interval','0.009'],['--columns','13'],['--to','1','--interval','1']]:
  result=CliRunner().invoke(cli,['cloud','storyboard',A,*args])
  assert result.exit_code==1,result.output
  assert json.loads(result.output)['status']=='invalid_request'


def test_reopen_never_creates_work_and_pending_does_not_download(monkeypatch,tmp_path):
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 calls=[]
 def request(url,credential,**kwargs):
  calls.append((url,kwargs))
  return 200,{'job':{'id':J,'asset_id':A,'options':{'from_ms':0},'sampling':{},'state':'running'}}
 monkeypatch.setattr(cloud,'_request',request)
 result=CliRunner().invoke(cli,['cloud','storyboard-show',A,J,'--out',str(tmp_path/'pending.jpg')])
 assert result.exit_code==1
 assert json.loads(result.output)['status']=='result_not_ready'
 assert calls==[(f'https://service.invalid/api/v1/assets/{A}/storyboards/{J}',{})]
 assert not (tmp_path/'pending.jpg').exists()
