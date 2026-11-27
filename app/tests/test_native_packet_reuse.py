"""Packet metadata reuse stays bounded, current, and request owned."""
from copy import deepcopy
from pathlib import Path
import json
import os

import pytest
from moviestar.native_preview import asset_identity
from moviestar.native_packet_reuse import prepare_indexes, CACHE_NAME
from tests.test_live_browser_preview import live_project
from tests.test_native_interview import interview_project


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    from moviestar import native_packet_reuse as reuse
    monkeypatch.setattr(reuse,'runtime_key',lambda:'runtime-a')
    sources={}
    for name in ['a','b']:
        path=tmp_path/(name+'.mp4');path.write_bytes(b'packet bytes')
        sources[name]={'path':path,'identity':asset_identity(path),'kind':'video'}
    packets=[{'fps':30,'media':[{'kind':'video','asset':name,'source_start':start,'source_end':start+3,'source_last':start+2.966667} for name in ['a','b','a']]} for start in [0,3]]
    calls=[]
    def index(path,start,end,fps):
        calls.append((path,start,end,fps))
        return {'start':start,'end':end,'fps':fps,'config':{'codec':'avc1.test'},'packets':[{'timestamp':round(start*1e6),'offset':0,'size':1,'key':True,'duration':33333}]}
    monkeypatch.setattr('moviestar.native_decode.packet_index',index)
    session=tmp_path/'session';session.mkdir()
    return packets,sources,calls,session


def folder(session,n):
    path=session/(f'{n:024x}');path.mkdir();return path


def test_unchanged_ranges_reuse_indexes_but_keep_current_final_sample(inputs, monkeypatch):
    packets,assets,calls,session=inputs;old=folder(session,1)
    prepare_indexes(packets,assets,old,[])
    assert len(calls)==4,'Duplicate views in one request share metadata, not decoded pictures'
    current=deepcopy(packets);current[0]['media'][0]['source_last']=2.9
    calls.clear();new=folder(session,2)
    prepare_indexes(current,assets,new,[str(old/CACHE_NAME)])
    assert calls==[]
    assert current[0]['media'][0]['video_decode']['last']==2.9
    assert current[0]['media'][2]['video_decode']['last']==2.966667
    assert (new/CACHE_NAME).is_file()
    (old/CACHE_NAME).unlink()
    newest=folder(session,3);prepare_indexes(current,assets,newest,[str(new/CACHE_NAME)])
    assert calls==[],'The new request owns its metadata independently of expired history'


def test_reuse_preserves_the_serialized_playback_descriptor(inputs):
    packets,assets,_,session=inputs;old=folder(session,1);prepare_indexes(packets,assets,old,[])
    before=json.dumps(packets)
    current=deepcopy(packets);prepare_indexes(current,assets,folder(session,2),[str(old/CACHE_NAME)])
    assert json.dumps(current)==before,'Identical playback inputs must retain the browser playbackKey serialization'


def test_source_change_while_loading_a_cache_hit_is_rejected(inputs, monkeypatch):
    from moviestar import native_packet_reuse as reuse
    packets,assets,_,session=inputs;old=folder(session,1);prepare_indexes(packets,assets,old,[])
    load=reuse.candidates
    def changed(*args):
        known=load(*args);assets['a']['path'].write_bytes(b'concurrent replacement');return known
    monkeypatch.setattr(reuse,'candidates',changed)
    with pytest.raises(ValueError,match='source changed'):
        prepare_indexes(deepcopy(packets),assets,folder(session,2),[str(old/CACHE_NAME)])


@pytest.mark.parametrize('damage',['missing','checksum','version','symlink','outside','oversized'])
def test_invalid_or_unowned_candidate_is_a_fresh_index(inputs, damage):
    packets,assets,calls,session=inputs;old=folder(session,1);prepare_indexes(packets,assets,old,[])
    path=old/CACHE_NAME
    if damage=='missing':path.unlink()
    elif damage=='checksum':path.write_text(path.read_text().replace('avc1.test','avc1.bad!'))
    elif damage=='version':
        doc=json.loads(path.read_text());doc['version']=0;path.write_text(json.dumps(doc))
    elif damage=='symlink':
        target=session.parent/'candidate';target.write_bytes(path.read_bytes());path.unlink();path.symlink_to(target)
    elif damage=='outside':
        target=session.parent/CACHE_NAME;target.write_bytes(path.read_bytes());path=target
    else:
        from moviestar.native_packet_reuse import MAX_BYTES
        with path.open('wb') as stream:stream.truncate(MAX_BYTES+1)
    calls.clear();prepare_indexes(deepcopy(packets),assets,folder(session,2),[str(path)])
    assert len(calls)==4


@pytest.mark.parametrize('change',['source','range','fps','runtime'])
def test_changed_inputs_never_reuse_old_packet_metadata(inputs, monkeypatch, change):
    packets,assets,calls,session=inputs;old=folder(session,1);prepare_indexes(packets,assets,old,[])
    current=deepcopy(packets)
    if change=='source':
        for asset in assets.values():
            path=asset['path'];stamp=path.stat();replacement=path.with_suffix('.replacement');replacement.write_bytes(b'replaced!!!!');replacement.replace(path)
            os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns));asset['identity']=asset_identity(path)
    elif change=='range':
        for child in current:
            for item in child['media']:item['source_start']+=.5;item['source_end']+=.5
    elif change=='fps':
        for child in current:child['fps']=24
    else:monkeypatch.setattr('moviestar.native_packet_reuse.runtime_key',lambda:'runtime-b')
    calls.clear();prepare_indexes(current,assets,folder(session,2),[str(old/CACHE_NAME)])
    assert len(calls)==4


def test_source_change_during_indexing_cannot_publish_reuse(inputs, monkeypatch):
    packets,assets,calls,session=inputs
    def changed(path,*args):path.write_bytes(b'changed source');return {}
    monkeypatch.setattr('moviestar.native_decode.packet_index',changed)
    target=folder(session,1)
    with pytest.raises(ValueError,match='source changed'):prepare_indexes(packets,assets,target,[])
    assert not (target/CACHE_NAME).exists()


def test_indexer_change_during_preparation_does_not_publish_metadata(inputs, monkeypatch):
    packets,assets,_,session=inputs;versions=iter(['old-tool','new-tool'])
    monkeypatch.setattr('moviestar.native_packet_reuse.runtime_key',lambda:next(versions))
    target=folder(session,1)
    with pytest.raises(ValueError,match='indexer changed'):prepare_indexes(packets,assets,target,[])
    assert not (target/CACHE_NAME).exists()


def test_source_changed_after_an_earlier_picture_was_indexed_is_rejected(inputs, monkeypatch):
    packets,assets,_,session=inputs
    def index(path,start,end,fps):
        if path==assets['b']['path'] and start==3:assets['a']['path'].write_bytes(b'concurrent edit')
        return {'start':start,'end':end,'fps':fps,'config':{},'packets':[{}]}
    monkeypatch.setattr('moviestar.native_decode.packet_index',index)
    with pytest.raises(ValueError,match='source changed'):prepare_indexes(packets,assets,folder(session,1),[])


def test_metadata_budget_disables_storage_without_rejecting_valid_preparation(inputs, monkeypatch):
    packets,assets,calls,session=inputs
    monkeypatch.setattr('moviestar.native_packet_reuse.MAX_BYTES',1)
    target=folder(session,1);prepare_indexes(packets,assets,target,[])
    assert len(calls)==4 and not (target/CACHE_NAME).exists()
    assert all(item['video_decode'] for child in packets for item in child['media'])


def test_candidate_paths_are_private_even_while_pending():
    from moviestar.local_preview import PreviewQueue
    queue=PreviewQueue();queue.submit({'state_id':'test','packet_candidates':['/private/owned/index.json']})
    assert 'packet_candidates' not in json.dumps(queue.state())
    assert '/private/' not in json.dumps(queue.state())


def test_expiring_a_request_removes_its_packet_metadata(tmp_path):
    from moviestar.local_preview import PreviewQueue
    queue=PreviewQueue();directories=[]
    for n in range(10):
        request=queue.submit({'state_id':str(n)});queue.take()
        target=tmp_path/request['request_id'];target.mkdir();directories.append(target)
        media=target/'preview.json';media.write_text('{}');(target/CACHE_NAME).write_text('{}')
        queue.finish(request['request_id'],media=str(media))
    queue.prune()
    assert not directories[0].exists()
    assert (directories[-1]/CACHE_NAME).exists()


def test_completed_requests_retain_and_pass_metadata_to_fresh_workers(interview_project, tmp_path, monkeypatch):
    from moviestar.local_preview import PreviewSession
    from moviestar.native_preview import build
    from moviestar.spec import load_spec,save_spec
    from tests.test_native_preview import _fake_workers
    calls=[]
    def index(path,start,end,fps):
        calls.append(str(path));return {'start':start,'end':end,'fps':fps,'config':{},'packets':[{}]}
    def audio(job,directory):
        directory.mkdir();path=directory/'audio.wav';path.write_bytes(b'RIFF fixture');return path
    monkeypatch.setattr('moviestar.native_decode.packet_index',index)
    monkeypatch.setattr('moviestar.native_audio.prepare_audio',audio)
    directory=tmp_path/'requests';directory.mkdir();session=PreviewSession(interview_project[2],0,3,directory,mode='native')
    def finish():
        with monkeypatch.context() as fake:_fake_workers(fake);session.tick()
        request=directory/session.active['request_id']/'request.json';build(request)
        with monkeypatch.context() as fake:_fake_workers(fake);session.process.returncode=0;session.tick()
        return request
    first=finish();assert (first.parent/CACHE_NAME).is_file()
    spec=load_spec(session.root);spec['audio_mix']['source_audio']['gain_db']=-3;save_spec(spec,cwd=session.root)
    calls.clear();second=finish()
    assert calls==[],'New workers must reuse retained metadata after fresh snapshot/audio compilation'
    assert session.queue.state()['displayed']['packet_reuse']=={'indexed':0,'reused':3}
    assert 'packet_candidates' not in json.dumps(session.queue.state())
    assert (second.parent/CACHE_NAME).is_file()
