"""Bounded native audio uses canonical placement/mix intent, independently of picture."""
from copy import deepcopy
import json
from pathlib import Path
import wave

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.native_preview import compile_snapshot, NativePreviewUnsupported
from moviestar.spec import save_spec
from tests.test_live_browser_preview import live_project
from tests.test_native_pip import pip_project


def dialogue_project(pip_project):
    spec, project, root = pip_project
    path = root/'dialogue.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0'*48000*4*6)
    spec['audio_mix']['source_audio'].update(muted=True, gain_db=-6)
    spec['audio_mix']['tracks'] = [{'id':'dialogue','kind':'voiceover','path':str(path),
        'at':'0:00:00.500','source_from':'0:00:01.000','source_to':'0:00:05.000',
        'gain_db':-3,'muted':False,'loop':False,'fade_in':0,'fade_out':0,'ducking':None}]
    save_spec(spec,cwd=root)
    return spec, project, root


def test_independent_audio_plan_clips_only_the_selected_result_window(pip_project):
    spec, _, root = dialogue_project(pip_project)
    before=(root/'moviestar/spec.json').read_bytes()
    packet,jobs=compile_snapshot(read_snapshot(root,1,3))
    plane=packet['audio_plane']; job=jobs[plane['job']]
    assert job['kind']=='audio_job' and job['duration']==2
    assert plane['sample_rate']==48000 and plane['channels']==2
    assert plane['source_audio']['muted'] is True
    assert plane['source_ranges'][0]['source_start']==2
    track=plane['tracks'][0]
    assert track['id']=='dialogue' and track['gain_db']==-3
    assert track['result_range']=={'from':1.0,'to':3.0}
    assert track['source_range']=={'from':1.5,'to':3.5}
    assert packet['prepared_audio'] is True
    assert str(root) not in json.dumps(packet)
    assert (root/'moviestar/spec.json').read_bytes()==before


def test_source_gain_without_extra_tracks_also_uses_canonical_audio(pip_project):
    spec,_,root=pip_project
    spec['audio_mix']['source_audio']['gain_db']=-6
    save_spec(spec,cwd=root)
    packet,jobs=compile_snapshot(read_snapshot(root,0,5))
    assert packet['audio_plane']['source_audio']['gain_db']==-6
    assert not packet['audio_plane']['tracks']
    job=jobs[packet['audio_plane']['job']]
    assert job['source_pieces'][0]['source_start']==1
    assert job['source_pieces'][0]['duration']==5


@pytest.mark.parametrize('change,reason',[
    ('source-fade','fades'),('track-fade','fades'),('loop','loop'),('ducking','ducking'),('second-track','one independent')])
def test_first_audio_profile_rejects_uncovered_mix_features(pip_project,change,reason):
    spec,_,root=dialogue_project(pip_project)
    if change=='source-fade':spec['audio_mix']['source_audio']['fade_in']=.2
    if change=='track-fade':spec['audio_mix']['tracks'][0]['fade_in']=.2
    if change=='loop':spec['audio_mix']['tracks'][0]['loop']=True
    if change=='ducking':spec['audio_mix']['tracks'][0]['ducking']={'under':['source'],'preset':'speech'}
    if change=='second-track':
        second=deepcopy(spec['audio_mix']['tracks'][0]);second['id']='other'
        spec['audio_mix']['tracks'].append(second)
    save_spec(spec,cwd=root)
    with pytest.raises(NativePreviewUnsupported,match=reason):
        compile_snapshot(read_snapshot(root,0,5))


def test_muted_source_does_not_require_a_visible_embedded_audio_carrier(pip_project):
    from moviestar.project import save_project
    spec,project,root=dialogue_project(pip_project)
    for source in project['sources']:source['audio_codec']=None
    save_project(project,cwd=root)
    packet,_=compile_snapshot(read_snapshot(root,0,5))
    assert packet['audio_plane']['source_audio']['muted']
    assert all(not item['has_audio'] for item in packet['media'] if item['kind']=='video')


def test_bounded_mix_inputs_keep_export_samples_gain_and_placement(tmp_path):
    import subprocess
    import numpy as np
    from moviestar.audio_surface import resolve_audio_mix, default_audio_mix
    from moviestar.cli import _audio_probe_file
    from moviestar.ffmpeg import build_mix_audio_window_command, _build_mix_layer_graph, _finish_mix_graph
    track=tmp_path/'tone.wav'; base=tmp_path/'source.wav'
    for path,freq in [(track,880),(base,440)]:
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'sine=frequency={freq}:sample_rate=48000:duration=6',
            '-ac','2','-c:a','pcm_f32le',str(path)],check=True,timeout=10)
    intent=default_audio_mix();intent['source_audio']['gain_db']=-6
    intent['tracks']=[{'id':'dialogue','kind':'voiceover','path':str(track),'at':.5,
        'source_from':1,'source_to':5,'gain_db':-3}]
    mix=resolve_audio_mix(intent,result_duration=5,base_dir=tmp_path,probe_audio=_audio_probe_file)
    target=tmp_path/'window.wav'
    command=build_mix_audio_window_command(str(base),str(target),mix,5,source_audio_available=True,window_start=1,window_duration=2)
    # Only the overlapping track range (1.5–3.5 s) is decoded, even when a
    # project's result/source offsets are large. No video stream is prepared.
    at=command.index(str(track));assert command[at-5:at]==['-ss','1.5','-t','2','-i']
    assert '-vn' in command and 'pcm_f32le' in command
    subprocess.run(command,check=True,capture_output=True,timeout=10)
    graph=_build_mix_layer_graph(mix,5,source_audio_available=True,window_start=1,window_duration=2)
    parts,label=_finish_mix_graph(graph)
    canonical=subprocess.run(['ffmpeg','-v','error','-i',str(base),'-i',str(track),'-filter_complex',';'.join(parts),
        '-map',f'[{label}]','-f','f32le','-'],check=True,capture_output=True,timeout=10).stdout
    bounded=subprocess.run(['ffmpeg','-v','error','-i',str(target),'-f','f32le','-'],check=True,capture_output=True,timeout=10).stdout
    original=np.frombuffer(canonical,dtype='<f4');actual=np.frombuffer(bounded,dtype='<f4')
    assert len(actual)==len(original)==2*48000*2
    assert np.max(np.abs(actual-original))<1e-5


def test_audio_asset_survives_publication_and_expires_without_deleting_sources(pip_project,tmp_path,monkeypatch):
    from moviestar import local_preview
    from moviestar.native_preview import build
    from tests.test_native_preview import _fake_workers
    _,_,root=dialogue_project(pip_project);monkeypatch.chdir(root)
    original=(root/'dialogue.wav').read_bytes()
    folder=tmp_path/'session';folder.mkdir();session=local_preview.PreviewSession(root,0,5,folder,mode='native')
    with monkeypatch.context() as fake:_fake_workers(fake);session.tick()
    request=folder/session.active['request_id']/'request.json';build(request)
    with monkeypatch.context() as fake:_fake_workers(fake);session.process.returncode=0;session.tick()
    packet=json.loads((request.parent/'preview.json').read_text());asset=packet['audio_plane']['asset']
    audio_path=Path(session.assets[asset]['path']);assert audio_path.is_file()
    monkeypatch.setattr(local_preview,'RETAINED_REQUESTS',1)
    spec=read_snapshot(root,0,5)['spec'];spec['audio_mix']['tracks'][0]['gain_db']=-9;save_spec(spec,cwd=root)
    with monkeypatch.context() as fake:_fake_workers(fake);session.tick()
    next_request=folder/session.active['request_id']/'request.json';build(next_request)
    with monkeypatch.context() as fake:_fake_workers(fake);session.process.returncode=0;session.tick()
    assert not audio_path.exists() and asset not in session.assets
    assert (root/'dialogue.wav').read_bytes()==original
