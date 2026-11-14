"""Caption reuse must preserve canonical pixels, request ownership and fencing."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from moviestar import local_preview, native_captions
from moviestar.native_preview import build
from moviestar.spec import load_spec, save_spec
from tests.test_native_captions import preset, cue
from tests.test_native_pip import pip_project
from tests.test_live_browser_preview import live_project
from tests.test_native_preview import _fake_workers, loaded_native_project


def finish(session, monkeypatch):
    with monkeypatch.context() as fake:
        _fake_workers(fake); session.tick()
    request=session.directory/session.active['request_id']/'request.json'
    build(request)
    with monkeypatch.context() as fake:
        _fake_workers(fake);session.process.returncode=0;session.tick()
    packet=json.loads((request.parent/'preview.json').read_text())
    images=[Path(session.assets[s['asset']]['path']) for s in packet['caption_plane']['states']]
    return request,images


@pytest.fixture
def caption_session(pip_project,tmp_path,monkeypatch):
    from moviestar import native_caption_reuse
    monkeypatch.setattr(native_caption_reuse,'environment_fingerprint',lambda jobs:'a'*64)
    root=preset(pip_project);monkeypatch.chdir(root)
    spec=load_spec(root);spec['audio_mix']['source_audio']['muted']=True;save_spec(spec,cwd=root)
    folder=tmp_path/'session';folder.mkdir()
    return local_preview.PreviewSession(root,0,5,folder,mode='native')


def audio_edit(session):
    spec=load_spec(session.root);spec['audio_mix']['source_audio']['gain_db']-=3
    save_spec(spec,cwd=session.root)


def test_audio_edit_copies_exact_captions_without_painting(caption_session,monkeypatch):
    session=caption_session;old,images=finish(session,monkeypatch)
    hashes=[sha256(p.read_bytes()).hexdigest()for p in images]
    audio_edit(session)
    monkeypatch.setattr(native_captions,'paint_plane',lambda *a:pytest.fail('unchanged captions were repainted'))
    new,copied=finish(session,monkeypatch)
    assert session.queue.state()['displayed']['caption_reuse']['hits']==1
    assert [sha256(p.read_bytes()).hexdigest()for p in copied]==hashes
    assert all(p.is_relative_to(new.parent)for p in copied)
    assert not set(copied)&set(images)
    shutil.rmtree(old.parent)
    assert all(p.is_file()for p in copied),'Each new request owns its copied images'


@pytest.mark.parametrize('change',['text','timing','style','layout','canvas','range','source','font_environment'])
def test_changed_caption_inputs_paint_again(caption_session,monkeypatch,change):
    from moviestar import native_caption_reuse
    session=caption_session;finish(session,monkeypatch)
    spec=load_spec(session.root)
    if change=='text':
        spec['overlays'][0]['text']='Third second';spec['overlays'][0]['tokens'][0]['text']='Third'
    elif change=='timing':spec['overlays'][0]['tokens'][0]['to']='0:00:00.400'
    elif change=='style':spec['overlays'][0]['highlight']['color']='#00ff00'
    elif change=='layout':spec['composition'][0]['slots'][0]['framing']={'mode':'fill','x':.6,'y':.5}
    elif change=='canvas':spec['composition_canvas'].update(width=800,height=450)
    elif change=='range':session.start,session.end=1,4
    elif change=='source':(session.root/'camera.mp4').write_bytes(b'replacement source identity')
    elif change=='font_environment':monkeypatch.setattr(native_caption_reuse,'environment_fingerprint',lambda jobs:'b'*64)
    spec['audio_mix']['source_audio']['gain_db']-=3;save_spec(spec,cwd=session.root)
    calls=[];paint=native_captions.paint_plane
    def checked(*args):calls.append(True);return paint(*args)
    monkeypatch.setattr(native_captions,'paint_plane',checked)
    finish(session,monkeypatch)
    assert calls
    assert session.queue.state()['displayed']['caption_reuse']['hits']==0


@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_missing_or_corrupt_candidate_is_a_safe_miss(caption_session,monkeypatch,damage):
    session=caption_session;_,images=finish(session,monkeypatch)
    expected=[sha256(p.read_bytes()).hexdigest()for p in images]
    if damage=='missing':images[0].unlink()
    else:images[0].write_bytes(b'incomplete PNG')
    audio_edit(session);_,fresh=finish(session,monkeypatch)
    assert session.queue.state()['displayed']['caption_reuse']['hits']==0
    assert [sha256(p.read_bytes()).hexdigest()for p in fresh]==expected


def test_environment_changes_during_copy_fall_back_to_paint(caption_session,monkeypatch):
    from moviestar import native_caption_reuse as reuse
    session=caption_session;finish(session,monkeypatch);audio_edit(session)
    values=iter(['a'*64,'b'*64])
    monkeypatch.setattr(reuse,'environment_fingerprint',lambda jobs:next(values))
    calls=[];paint=native_captions.paint_plane
    def checked(*args):calls.append(True);return paint(*args)
    monkeypatch.setattr(native_captions,'paint_plane',checked)
    finish(session,monkeypatch)
    assert calls and session.queue.state()['displayed']['caption_reuse']['hits']==0
    assert session.queue.state()['displayed']['caption_reuse']['eligible_jobs']==0


def test_pruning_original_request_keeps_new_copied_assets(caption_session,monkeypatch):
    session=caption_session;monkeypatch.setattr(local_preview,'RETAINED_REQUESTS',1)
    old,_=finish(session,monkeypatch);audio_edit(session);new,images=finish(session,monkeypatch)
    assert not old.parent.exists()
    assert all(p.is_file()and p.is_relative_to(new.parent)for p in images)
    public=json.dumps(session.queue.state())
    assert str(session.directory)not in public
    assert 'caption_candidates'not in public


def test_obsolete_worker_cannot_seed_candidates(caption_session,monkeypatch):
    session=caption_session
    with monkeypatch.context()as fake:
        _fake_workers(fake);session.tick()
    old=session.directory/session.active['request_id']
    build(old/'request.json')
    audio_edit(session)
    with monkeypatch.context()as fake:
        _fake_workers(fake);session.process.returncode=0;session.tick()
    assert session.caption_candidates.snapshot()=={}
    assert not old.exists()
    assert json.loads((session.directory/session.active['request_id']/'request.json').read_text())['caption_candidates']=={}


def test_undo_reuses_matching_validated_caption_output(caption_session,monkeypatch):
    from moviestar.spec import pop_revision
    session=caption_session;_,first=finish(session,monkeypatch)
    hashes=[sha256(p.read_bytes()).hexdigest()for p in first]
    spec=load_spec(session.root);spec['overlays'][0]['highlight']['color']='#00ff00';save_spec(spec,cwd=session.root,command='caption style')
    finish(session,monkeypatch)
    restored,_=pop_revision(load_spec(session.root));save_spec(restored,cwd=session.root,record=False)
    monkeypatch.setattr(native_captions,'paint_plane',lambda *a:pytest.fail('matching undo captions should reuse'))
    _,images=finish(session,monkeypatch)
    assert [sha256(p.read_bytes()).hexdigest()for p in images]==hashes
    assert session.queue.state()['displayed']['caption_reuse']['hits']==1


def test_unavailable_font_inventory_paints_normally(caption_session,monkeypatch):
    from moviestar import native_caption_reuse as reuse
    session=caption_session;finish(session,monkeypatch);audio_edit(session)
    monkeypatch.setattr(reuse,'environment_fingerprint',lambda jobs:None)
    _,images=finish(session,monkeypatch)
    assert all(p.is_file()for p in images)
    assert session.queue.state()['displayed']['caption_reuse']['eligible_jobs']==0


@pytest.mark.parametrize('target',['font','config','renderer','version'])
def test_environment_identity_detects_same_path_replacement(tmp_path,monkeypatch,target):
    import os
    from types import SimpleNamespace
    from moviestar import native_caption_reuse as reuse
    font=tmp_path/'font.ttf';font.write_bytes(b'first')
    config=tmp_path/'fonts.conf';config.write_bytes(b'first')
    tool=tmp_path/'ffmpeg';tool.write_bytes(b'first')
    module=tmp_path/'source'/'paint.py';module.parent.mkdir();module.write_bytes(b'first')
    monkeypatch.setattr(reuse,'__file__',str(module))
    monkeypatch.setattr(reuse,'_font_dependencies',lambda jobs,deadline=None:{font,config})
    monkeypatch.setattr(reuse.shutil,'which',lambda name:str(tool))
    version=['renderer v1']
    monkeypatch.setattr(reuse.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=version[0]))
    before=reuse.environment_fingerprint([]);assert before is not None
    if target=='version':version[0]='renderer v2'
    else:
        path={'font':font,'config':config,'renderer':module}[target];stamp=path.stat()
        path.write_bytes(b'other');os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    assert reuse.environment_fingerprint([])!=before


def candidate(tmp_path,index,bytes_count=32):
    from moviestar.native_caption_reuse import describe
    directory=tmp_path/('captions-'+f'{index:024x}');directory.mkdir()
    path=directory/'state-001.png';path.write_bytes(b'\x89PNG\r\n\x1a\n'+b'x'*bytes_count)
    return describe([(path,{'x':0,'y':0,'width':1,'height':1})])


def test_candidate_limits_evict_references_without_deleting_owned_files(tmp_path,monkeypatch):
    from moviestar import native_caption_reuse as reuse
    monkeypatch.setattr(reuse,'MAX_JOBS',2);monkeypatch.setattr(reuse,'MAX_BYTES',100)
    catalog=reuse.CaptionCandidates()
    for i in range(3):catalog.accept({f'{i:064x}':candidate(tmp_path,i)},tmp_path)
    assert len(catalog.snapshot())==2
    assert sum(r['bytes']for r in catalog.snapshot().values())<=100
    assert len(list(tmp_path.glob('captions-*/*.png')))==3
    first=next(iter(catalog.entries.values()));Path(first['files'][0]['path']).unlink()
    assert len(catalog.snapshot())==1


def test_candidate_catalog_rejects_symlinks_outside_owned_request(tmp_path):
    from moviestar.native_caption_reuse import CaptionCandidates,describe
    outside=tmp_path/'foreign';outside.mkdir();file=outside/'state-001.png';file.write_bytes(b'\x89PNG\r\n\x1a\nforeign')
    request=tmp_path/'request';request.mkdir();alias=request/('captions-'+'a'*24);alias.symlink_to(outside,target_is_directory=True)
    record=describe([(alias/file.name,{'x':0,'y':0,'width':1,'height':1})])
    catalog=CaptionCandidates();catalog.accept({'a'*64:record},request)
    assert catalog.snapshot()=={}


def test_real_discovered_font_environment_reuses_exact_pixels(pip_project,tmp_path,monkeypatch):
    from moviestar.native_caption_reuse import environment_fingerprint
    if environment_fingerprint([]) is None:pytest.skip('Complete host font inventory unavailable')
    root=preset(pip_project);monkeypatch.chdir(root)
    spec=load_spec(root);spec['audio_mix']['source_audio']['muted']=True;save_spec(spec,cwd=root)
    folder=tmp_path/'session';folder.mkdir();session=local_preview.PreviewSession(root,0,5,folder,mode='native')
    _,original=finish(session,monkeypatch)
    hashes=[sha256(p.read_bytes()).hexdigest()for p in original]
    assert session.queue.state()['displayed']['caption_reuse']['eligible_jobs']==1
    audio_edit(session)
    monkeypatch.setattr(native_captions,'paint_plane',lambda *a:pytest.fail('real caption reuse did not occur'))
    _,images=finish(session,monkeypatch)
    assert session.queue.state()['displayed']['caption_reuse']['hits']==1
    assert [sha256(p.read_bytes()).hexdigest()for p in images]==hashes


def test_separate_real_workers_reuse_captions(loaded_native_project,tmp_path,monkeypatch):
    from moviestar.native_caption_reuse import environment_fingerprint
    from tests.test_native_preparation_timing import complete
    if environment_fingerprint([]) is None:pytest.skip('Complete host font inventory unavailable')
    spec,_,root=loaded_native_project
    spec.pop('spatial');spec['overlays']=[cue()]
    spec['audio_mix']['source_audio']['muted']=True;save_spec(spec,cwd=root)
    folder=tmp_path/'real-session';folder.mkdir();session=local_preview.PreviewSession(root,0,5,folder,mode='native')
    try:
        first=complete(session);assert first['status']=='ready',first
        first_images=[p for p in session.assets.values()if p['kind']=='image']
        hashes=sorted(sha256(Path(p['path']).read_bytes()).hexdigest()for p in first_images)
        assert first['caption_reuse']['eligible_jobs']==1
        audio_edit(session)
        second=complete(session);assert second['request_id']!=first['request_id']
        assert second['caption_reuse']['hits']==1,second
        packet=json.loads(Path(session.queue.get(second['request_id'])['media']).read_text())
        copied=[Path(session.assets[s['asset']]['path'])for s in packet['caption_plane']['states']]
        assert sorted(sha256(p.read_bytes()).hexdigest()for p in copied)==hashes
    finally:
        session.stopped.set();session.run()


def test_unavailable_inventory_tools_disable_only_reuse(monkeypatch):
    from moviestar import native_caption_reuse as reuse
    monkeypatch.setattr(reuse.shutil,'which',lambda name:None)
    assert reuse.environment_fingerprint([]) is None


def test_oversized_candidate_is_not_copied(tmp_path,monkeypatch):
    from moviestar import native_caption_reuse as reuse
    record=candidate(tmp_path,1)
    monkeypatch.setattr(reuse,'MAX_BYTES',16)
    destination=tmp_path/'copy'
    assert reuse.copy_candidate(record,destination,1) is None
    assert not destination.exists()
    assert Path(record['files'][0]['path']).is_file()
