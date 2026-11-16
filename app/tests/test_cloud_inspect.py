import json
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import cloud
A='123e4567-e89b-42d3-a456-426614174002'
J='123e4567-e89b-42d3-a456-426614174003'

def test_request_recovers_same_key_and_normalizes_timecodes(monkeypatch,tmp_path):
 monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE',str(tmp_path/'account.json'))
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 calls=[]
 def request(url,credential,**kwargs):
  calls.append(kwargs)
  if len(calls)==1:return 0,{}
  return 202,{'job':{'id':J,'asset_id':A,'state':'queued','options':kwargs['payload']},'idempotency_key':kwargs['key']}
 monkeypatch.setattr(cloud,'_request',request)
 runner=CliRunner()
 first=runner.invoke(cli,['cloud','inspect',A,'--from','0:05','--to','0:15'])
 assert first.exit_code==1,first.output
 second=runner.invoke(cli,['cloud','inspect',A,'--from','5','--to','15'])
 assert second.exit_code==0,second.output
 assert calls[0]['key']==calls[1]['key']
 assert calls[1]['payload']=={'from_ms':5000,'to_ms':15000,'interval_ms':500,'width':640}
 assert json.loads(second.output)['job']['id']==J

def test_invalid_inspection_options_do_not_authenticate(monkeypatch):
 def connection(_):raise AssertionError('validate before connecting')
 monkeypatch.setattr(cloud,'_connection',connection)
 for args in [['--from','0','--to','121'],['--from','1','--to','1'],['--from','0','--to','10','--interval','0.001'],['--from','nan','--to','10']]:
  result=CliRunner().invoke(cli,['cloud','inspect',A,*args])
  assert result.exit_code==1,result.output
  assert json.loads(result.output)['status']=='invalid_request'

def test_reopen_creates_no_job(monkeypatch):
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 def request(url,credential,**kwargs):
  assert not kwargs
  assert url.endswith(f'/inspections/{J}')
  return 200,{'job':{'id':J,'asset_id':A,'state':'running','options':{'from_ms':0,'to_ms':1000,'interval_ms':500,'width':640}}}
 monkeypatch.setattr(cloud,'_request',request)
 result=CliRunner().invoke(cli,['cloud','inspect-show',A,J])
 assert result.exit_code==0,result.output

def test_download_resumes_identical_files_and_preserves_conflicting_files(monkeypatch,tmp_path):
 from email.message import Message
 from moviestar import cloud_inspect as inspections
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 image=b'\xff\xd8\xff\xd9'
 frame={'index':0,'at_ms':0,'width':640,'file_size_bytes':4,'mime_type':'image/jpeg'}
 job={'id':J,'asset_id':A,'state':'succeeded','options':{'from_ms':0,'to_ms':500,'interval_ms':500,'width':640},'result':{'mode':'visual_inspect','range':{'from_ms':0,'to_ms':500},'sampling':{'returned_frames':1,'interval_ms':500,'scale_width':640},'thumbnails':[frame]}}
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':job}))
 class Response:
  status=200
  headers=Message()
  headers['Content-Type']='image/jpeg'
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def read(self,limit):return image
 class Opener:
  def open(self,request,**kwargs):
   assert request.full_url==f'https://service.invalid/api/v1/assets/{A}/inspections/{J}/images/0'
   assert request.get_header('X-moviestar-client')=='moviestar-cli-v1'
   return Response()
 monkeypatch.setattr(inspections,'build_opener',lambda *_:Opener())
 args=['cloud','inspect-show',A,J,'--out-dir',str(tmp_path/'frames')]
 first=CliRunner().invoke(cli,args);assert first.exit_code==0,first.output
 again=CliRunner().invoke(cli,args);assert again.exit_code==0,again.output
 output=tmp_path/'frames'/'inspect_0000_0ms.jpg'
 assert output.read_bytes()==image
 output.write_bytes(b'keep')
 conflict=CliRunner().invoke(cli,args)
 assert conflict.exit_code==1
 assert output.read_bytes()==b'keep'
