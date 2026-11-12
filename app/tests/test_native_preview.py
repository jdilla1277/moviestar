"""Installed native preview: capability errors, isolated workers and scoped assets."""
import json
from pathlib import Path
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar import local_preview
from moviestar.spec import save_spec
from tests.test_live_browser_preview import live_project


def test_native_mode_uses_the_existing_project_session_lock(live_project, monkeypatch):
    root=live_project[2];monkeypatch.chdir(root)
    def inside(*args, **kwargs):
        assert kwargs['mode']=='native'
        with pytest.raises(ValueError, match='already running'):
            local_preview.serve(root, 0, 5, port=0, open_browser=False, ready=lambda _:None, mode='rendered')
        kwargs['ready']({'status':'serving','mode':kwargs['mode']})
    monkeypatch.setattr(local_preview,'_serve_session',inside)
    result=CliRunner().invoke(cli,['preview','--mode','native','--from','0','--to','5','--no-open'])
    assert result.exit_code==0,result.output
    assert json.loads(result.stdout)['mode']=='native'


def test_unsupported_native_mode_fails_before_writing_or_opening_a_port(live_project,monkeypatch):
    spec,_,root=live_project;monkeypatch.chdir(root)
    spec['audio_mix']['source_audio']['gain_db']=-3;save_spec(spec,cwd=root)
    monkeypatch.setattr(local_preview,'_serve_session',lambda *a,**k:pytest.fail('unsupported native must not start a server or fallback render'))
    before=sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    result=CliRunner().invoke(cli,['preview','--mode','native','--from','0','--to','5','--no-open'])
    assert result.exit_code==1
    data=json.loads(result.stdout)
    assert data['code']=='unsupported_native_preview'
    assert data['fallback_mode']=='rendered'
    assert '--mode rendered' in data['error']
    assert before==sorted(str(p.relative_to(root)) for p in root.rglob('*'))


def test_native_worker_does_not_launch_ffmpeg_or_probe(live_project,tmp_path,monkeypatch):
    from moviestar.native_preview import build
    request=local_preview.read_snapshot(live_project[2],0,5)
    request.update(request_id='a'*24,requested_at=1)
    path=tmp_path/'request.json';path.write_text(json.dumps(request))
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('native worker must not decode or encode'))
    build(path)
    packet=json.loads((tmp_path/'preview.json').read_text())
    assets=json.loads((tmp_path/'assets.json').read_text())
    assert packet['request_id']==request['request_id']
    assert str(live_project[2]) not in json.dumps(packet)
    assert len(packet['samples'])==150 and len(assets)==1


def _complete(session):
    from moviestar.native_preview import build
    session.tick()
    folder=session.directory/session.active['request_id']
    build(folder/'request.json');session.process.returncode=0
    session.tick()


def _fake_workers(monkeypatch):
    class Process:
        pid=20000;returncode=None
        def __init__(self,args,**kwargs):
            assert args[2]=='moviestar.native_preview'
            assert kwargs['start_new_session']
        def poll(self):return self.returncode
    monkeypatch.setattr(local_preview.subprocess,'Popen',Process)
    monkeypatch.setattr(local_preview.PreviewSession,'_signal',lambda *a:None)


def test_native_worker_supersession_keeps_late_packets_out(live_project,tmp_path,monkeypatch):
    from moviestar.native_preview import build
    spec,_,root=live_project;folder=tmp_path/'packets';folder.mkdir()
    _fake_workers(monkeypatch)
    session=local_preview.PreviewSession(root,0,5,folder,mode='native')
    session.tick();old=session.active['request_id'];build(folder/old/'request.json')
    spec['spatial']['scenes']['scene']['layout']['children'][1]['content']['text']='New title';save_spec(spec,cwd=root)
    session.process.returncode=0;session.tick()
    assert session.queue.state()['displayed'] is None
    assert not (folder/old).exists()
    build(folder/session.active['request_id']/'request.json');session.process.returncode=0;session.tick()
    assert session.queue.state()['is_current']
    assert session.queue.state()['displayed']['request_id']!=old


def test_native_assets_and_scripts_are_packaged_and_scoped(live_project,tmp_path,monkeypatch):
    _,_,root=live_project;folder=tmp_path/'packets';folder.mkdir()
    _fake_workers(monkeypatch)
    session=local_preview.PreviewSession(root,0,5,folder,mode='native');_complete(session)
    service=local_preview.PreviewServer(session.queue,token='a'*48,port=0,mode='native',session=session)
    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
    try:
        packet=json.loads(urlopen(service.url+'packet?request='+session.queue.state()['displayed']['request_id']).read())
        assert str(root) not in json.dumps(packet)
        asset=packet['media'][0]['asset']
        with urlopen(Request(service.url+'assets/'+asset,headers={'Range':'bytes=2-5'})) as response:
            assert response.status==206 and response.headers['Content-Type']=='video/mp4'
            assert response.read()==(root/'camera.mp4').read_bytes()[2:6]
        with urlopen(service.url) as response:
            page=response.read().decode();assert 'data-preview-mode="native"' in page
            assert 'LIVE PREVIEW EXPERIMENT' not in page and 'id="seek"' in page
        for name in ['preview_native.mjs','preview_native_stage.mjs','preview_native_meter.mjs','spatial_dom.mjs',
                     'preview_native_worker.mjs','preview_native_buffer.mjs','preview_native_videos.mjs']:
            with urlopen(service.url+name) as response:
                assert response.headers['Content-Type']=='text/javascript'
                assert response.read()
        for route,headers,code in [('assets/unknown',{},404),('packet?request=unknown',{},404),
                                   ('assets/'+asset,{'Origin':'https://example.com'},403)]:
            with pytest.raises(HTTPError) as error:urlopen(Request(service.url+route,headers=headers))
            assert error.value.code==code
        (root/'camera.mp4').write_bytes(b'new source')
        with pytest.raises(HTTPError) as error:urlopen(service.url+'assets/'+asset)
        assert error.value.code==409
    finally:
        service.shutdown();service.server_close();thread.join()


def test_native_history_prunes_private_asset_registrations(live_project,tmp_path,monkeypatch):
    root=live_project[2];folder=tmp_path/'packets';folder.mkdir();_fake_workers(monkeypatch)
    session=local_preview.PreviewSession(root,0,5,folder,mode='native')
    for index in range(12):
        (root/'camera.mp4').write_bytes(b'x'*(index+1))
        _complete(session)
        assert len(session.queue.requests)<=8
        assert len(session.assets)<=8
    assert len(session.assets)==8


@pytest.fixture
def loaded_native_project(live_project, monkeypatch):
    """Index real media through load; keep the authored supported scene."""
    from moviestar.project import load_project
    spec, _, root = live_project
    source = root / 'camera.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=blue:s=96x64:r=30', '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
        '-t', '8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)],
        check=True, timeout=20)
    monkeypatch.chdir(root)
    loaded = CliRunner().invoke(cli, ['load', str(source), '--as', 'camera', '--force',
        '--no-transcribe', '--no-frames'])
    assert loaded.exit_code == 0, loaded.output
    project = load_project(root)
    assert project['sources'][0]['audio_codec'] == 'aac'
    assert 'has_audio' not in project['sources'][0]
    save_spec(spec, cwd=root)
    return spec, project, root


@pytest.mark.skipif(sys.platform=='win32',reason='POSIX foreground service')
@pytest.mark.parametrize('route', ['explicit', 'inferred', 'global'])
def test_real_native_cli_recovers_after_unsupported_edit_and_cleans_up(loaded_native_project, route):
    import selectors
    import time
    spec,_,root=loaded_native_project
    if route != 'explicit':
        spec['composition'][0]['audio_from'] = None
    if route == 'global':
        spec['composition_audio_from'] = 'camera'
    save_spec(spec,cwd=root)
    process=subprocess.Popen([sys.executable,'-m','moviestar.cli','preview','--workspace',str(root),
        '--mode','native','--from','0','--to','5','--port','0','--no-open'],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def wait_for(url,predicate):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            with urlopen(url+'state') as response:state=json.load(response)
            if predicate(state):return state
            time.sleep(.02)
        pytest.fail('Native CLI state timeout: '+str(state))
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            assert selector.select(8),'CLI did not print its ready response'
            ready=json.loads(process.stdout.readline())
        assert ready['mode']=='native' and ready['transport']=='native-browser'
        assert ready['profile']['name']=='native' and ready['profile']['width']==640
        initial=wait_for(ready['url'],lambda state:state['is_current'])
        original=initial['displayed']['request_id']
        spec['audio_mix']['source_audio']['gain_db']=-3;save_spec(spec,cwd=root)
        failed=wait_for(ready['url'],lambda state:state['latest']['status']=='failed')
        assert failed['displayed']['request_id']==original and not failed['is_current']
        assert '--mode rendered' in failed['latest']['error']
        spec['audio_mix']['source_audio']['gain_db']=0.0;save_spec(spec,cwd=root)
        restored=wait_for(ready['url'],lambda state:state['is_current'] and state['displayed']['request_id']!=original)
        assert restored['displayed']['state_id']==initial['displayed']['state_id']
    finally:
        process.terminate()
        try:process.wait(timeout=9)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        process.stdout.close();process.stderr.close()
    assert process.returncode==0
    assert not list((root/'moviestar/local-preview').glob('session-*'))


def test_native_worker_start_failure_removes_its_owned_folder(live_project,tmp_path,monkeypatch):
    root=live_project[2];folder=tmp_path/'packets';folder.mkdir()
    session=local_preview.PreviewSession(root,0,5,folder,mode='native')
    def failed(*args,**kwargs):raise OSError('worker could not start')
    monkeypatch.setattr(local_preview.subprocess,'Popen',failed)
    for index in range(10):
        (root/'camera.mp4').write_bytes(b'x'*(index+1));session.tick()
        assert session.queue.state()['latest']['status']=='failed'
        assert session.process is None and session.active is None
        assert list(folder.iterdir())==[]
