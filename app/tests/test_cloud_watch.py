import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
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
 assert json.loads(second.output)['job']['clock']=='source'


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
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':{'id':J,'asset_id':A,'from_ms':1234,'to_ms':2345,'state':'succeeded','result':{'file_size_bytes':24}}}))
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
 assert json.loads(second.output)['status']=='output_exists'
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


@pytest.mark.parametrize(('expected', 'declared', 'payload', 'succeeds'), [
 (1000, '1000', 24, False),  # Premature EOF after a valid MP4 header.
 (1000, None, 24, False),  # Job metadata also protects length-less responses.
 (24, '1000', 24, False),  # Matching job length cannot excuse HTTP truncation.
 (1000, '24', 24, False),  # A complete HTTP body can still truncate the artifact.
 (24, 'invalid', 24, False),
 (24, '-1', 24, False),
 (None, '24', 24, False),
 (True, '24', 24, False),
 (2097153, '24', 24, False),
 (24, '25', 25, False),  # Reject extra bytes, not only short responses.
 (24, '24', 24, True),
 (24, None, 24, True),
])
def test_real_http_download_checks_artifact_and_response_lengths(
 monkeypatch, tmp_path, expected, declared, payload, succeeds,
):
 prefix = bytes.fromhex('000000186674797069736f6d0000000069736f6d6d703431')
 media = prefix + b'\0' * (payload - len(prefix))
 job = {'id': J, 'asset_id': A, 'from_ms': 0, 'to_ms': 1000,
        'state': 'succeeded', 'result': {'file_size_bytes': expected}}
 paths = []

 class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
   paths.append(self.path)
   assert self.headers['Authorization'] == 'Bearer mvs_synthetic'
   assert self.headers['x-moviestar-client'] == 'moviestar-cli-v1'
   if self.path.endswith('/media'):
    body, content_type, length = media, 'video/mp4', declared
   else:
    body = json.dumps({'job': job}).encode()
    content_type, length = 'application/json', str(len(body))
   self.send_response(200)
   self.send_header('Content-Type', content_type)
   self.send_header('Connection', 'close')
   if length is not None:
    self.send_header('Content-Length', length)
   self.end_headers()
   self.wfile.write(body)
   self.close_connection = True

  def log_message(self, *args):
   pass

 server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
 thread = threading.Thread(target=server.serve_forever, daemon=True)
 thread.start()
 endpoint = f'http://127.0.0.1:{server.server_port}/api/v1/account/cloud'
 monkeypatch.setattr(cloud, '_connection', lambda _: (endpoint, 'mvs_synthetic'))
 out = tmp_path / 'excerpt.mp4'
 try:
  result = CliRunner().invoke(cli, ['cloud', 'watch-show', A, J, '--out', str(out)])
 finally:
  server.shutdown()
  server.server_close()
  thread.join()
 if succeeds:
  assert result.exit_code == 0, result.output
  assert out.read_bytes() == media
 else:
  assert result.exit_code == 1, result.output
  assert json.loads(result.output)['status'] in {'media_unavailable', 'invalid_response'}
  assert not out.exists()
 assert paths[0] == f'/api/v1/assets/{A}/watches/{J}'


@pytest.mark.parametrize('state', ['queued','running','succeeded','failed','cancelled'])
@pytest.mark.parametrize('clock', [None,'source'])
def test_saved_watch_discloses_asset_clock_even_from_older_service(monkeypatch,state,clock):
 job={'id':J,'asset_id':A,'from_ms':1000,'to_ms':2000,'state':state}
 if clock is not None:job['clock']=clock
 snapshot=json.dumps(job)
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 calls=[]
 def request(*args,**kwargs):
  calls.append((args,kwargs));return 200,{'job':job}
 monkeypatch.setattr(cloud,'_request',request)
 result=CliRunner().invoke(cli,['cloud','watch-show',A,J])
 assert result.exit_code==0,result.output
 assert json.loads(result.output)['job']['clock']=='source'
 assert json.loads(result.output)['job']['asset_id']==A
 assert json.dumps(job)==snapshot
 assert len(calls)==1 and calls[0][1]=={}


def test_remote_watch_rejects_a_conflicting_clock(monkeypatch):
 monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
 monkeypatch.setattr(cloud,'_request',lambda *a,**kw:(200,{'job':{'id':J,'asset_id':A,'from_ms':0,'to_ms':1000,'state':'queued','clock':'result'}}))
 result=CliRunner().invoke(cli,['cloud','watch-show',A,J])
 assert result.exit_code==1
 assert json.loads(result.output)['status']=='invalid_response'
