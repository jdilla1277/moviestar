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
  return 202,{'job':{'id':J,'asset_id':A,'at_ms':83450,'state':'queued'},'idempotency_key':kwargs['key']}
 monkeypatch.setattr(cloud,'_request',request)
 runner=CliRunner()
 first=runner.invoke(cli,['cloud','screenshot',A,'--at','1:23.450'])
 assert first.exit_code==1
 second=runner.invoke(cli,['cloud','screenshot',A,'--at','83.45'])
 assert second.exit_code==0,second.output
 assert calls[0]['key']==calls[1]['key']
 assert calls[1]['payload']=={'at_ms':83450}
 assert json.loads(second.output)['job']['id']==J


def test_bad_timestamps_do_not_connect(monkeypatch):
 def connection(_):
  raise AssertionError('must validate before connecting')
 monkeypatch.setattr(cloud,'_connection',connection)
 for at in ['nan','inf','-1','86401','0.0001']:
  result=CliRunner().invoke(cli,['cloud','screenshot',A,'--at',at])
  assert result.exit_code==1,result.output
  assert json.loads(result.output)['status']=='invalid_request'


def test_reopen_downloads_private_jpeg_without_overwriting(monkeypatch,tmp_path):
 from email.message import Message
 from moviestar import cloud_screenshot as screenshots
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','mvs_synthetic'))
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':{'id':J,'asset_id':A,'at_ms':1234,'state':'succeeded'}}))
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
   assert request.full_url==f'https://service.invalid/api/v1/assets/{A}/screenshots/{J}/image'
   return Response()
 monkeypatch.setattr(screenshots,'build_opener',lambda *_:Opener())
 out=tmp_path/'frame.jpg'
 args=['cloud','screenshot-show',A,J,'--out',str(out)]
 first=CliRunner().invoke(cli,args)
 assert first.exit_code==0,first.output
 assert out.read_bytes()==b'\xff\xd8\xff\xd9'
 out.write_bytes(b'keep')
 second=CliRunner().invoke(cli,args)
 assert second.exit_code==1
 assert out.read_bytes()==b'keep'
