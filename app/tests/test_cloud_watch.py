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
  return 202,{'job':{'id':J,'asset_id':A,'from_ms':83450,'to_ms':84000,'state':'queued'},'idempotency_key':kwargs['key']}
 monkeypatch.setattr(cloud,'_request',request)
 runner=CliRunner()
 first=runner.invoke(cli,['cloud','watch',A,'--from','1:23.450','--to','84'])
 assert first.exit_code==1
 second=runner.invoke(cli,['cloud','watch',A,'--from','83.45','--to','84'])
 assert second.exit_code==0,second.output
 assert calls[0]['key']==calls[1]['key']
 assert calls[1]['payload']=={'from_ms':83450,'to_ms':84000}
 assert json.loads(second.output)['job']['id']==J


def test_bad_timestamps_do_not_connect(monkeypatch):
 def connection(_):
  raise AssertionError('must validate before connecting')
 monkeypatch.setattr(cloud,'_connection',connection)
 for at in ['nan','inf','-1','86401','0.0001']:
  result=CliRunner().invoke(cli,['cloud','watch',A,'--from',at,'--to','1'])
  assert result.exit_code==1,result.output
  assert json.loads(result.output)['status']=='invalid_request'


def test_reopen_downloads_private_mp4_without_overwriting(monkeypatch,tmp_path):
 from email.message import Message
 from moviestar import cloud_watch as watchs
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','mvs_synthetic'))
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':{'id':J,'asset_id':A,'from_ms':1234,'to_ms':2345,'state':'succeeded'}}))
 class Response:
  status=200
  headers=Message()
  headers['Content-Type']='video/mp4'
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def read(self,limit):return bytes.fromhex('000000186674797069736f6d0000000069736f6d6d703431')
 class Opener:
  def open(self,request,**kwargs):
   assert request.get_header('X-moviestar-client')=='moviestar-cli-v1'
   assert request.full_url==f'https://service.invalid/api/v1/assets/{A}/watches/{J}/media'
   return Response()
 monkeypatch.setattr(watchs,'build_opener',lambda *_:Opener())
 out=tmp_path/'excerpt.mp4'
 args=['cloud','watch-show',A,J,'--out',str(out)]
 first=CliRunner().invoke(cli,args)
 assert first.exit_code==0,first.output
 assert out.read_bytes()==bytes.fromhex('000000186674797069736f6d0000000069736f6d6d703431')
 out.write_bytes(b'keep')
 second=CliRunner().invoke(cli,args)
 assert second.exit_code==1
 assert out.read_bytes()==b'keep'


def test_reversed_and_over_budget_ranges_never_connect(monkeypatch):
 monkeypatch.setattr(cloud,'_connection',lambda _:(_ for _ in ()).throw(AssertionError('must not connect')))
 for start,end in [('1','1'),('2','1'),('0','30.001')]:
  result=CliRunner().invoke(cli,['cloud','watch',A,'--from',start,'--to',end])
  assert result.exit_code==1,result.output
  assert json.loads(result.output)['status']=='invalid_request'


def test_expired_media_reopens_metadata_without_new_execution(monkeypatch,tmp_path):
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 calls=[]
 def request(url,*args,**kwargs):
  calls.append((url,kwargs))
  return 200,{'job':{'id':J,'asset_id':A,'from_ms':0,'to_ms':1000,'state':'succeeded','artifact_available':False}}
 monkeypatch.setattr(cloud,'_request',request)
 result=CliRunner().invoke(cli,['cloud','watch-show',A,J,'--out',str(tmp_path/'expired.mp4')])
 assert result.exit_code==1,result.output
 assert json.loads(result.output)['status']=='artifact_unavailable'
 assert len(calls)==1 and not calls[0][1]
 assert not (tmp_path/'expired.mp4').exists()
