"""Two independently placed layers retain their canonical mix and bounded clocks."""
from copy import deepcopy
import json
import subprocess

import numpy as np
import pytest

from moviestar.audio_surface import default_audio_mix, resolve_audio_mix
from moviestar.cli import _audio_probe_file
from moviestar.ffmpeg import _build_mix_layer_graph, _finish_mix_graph
from moviestar.local_preview import read_snapshot
from moviestar.native_audio import prepare_audio
from moviestar.native_preview import compile_snapshot, NativePreviewUnsupported, PROFILE
from moviestar.spec import save_spec
from tests.test_live_browser_preview import live_project
from tests.test_native_audio import dialogue_project
from tests.test_native_pip import pip_project


def test_two_track_inspection_keeps_each_identity_placement_and_source_range(pip_project):
    spec,_,root=dialogue_project(pip_project)
    second=deepcopy(spec['audio_mix']['tracks'][0]);second.update(id='bed',kind='music',at='0:00:02.000',
        source_from='0:00:00.250',source_to='0:00:02.250',gain_db=-12,fade_in=.5,fade_out=.5)
    spec['audio_mix']['tracks'].append(second);save_spec(spec,cwd=root)
    before=(root/'moviestar/spec.json').read_bytes()
    packet,jobs=compile_snapshot(read_snapshot(root,1,3));plane=packet['audio_plane']
    assert [t['id'] for t in plane['tracks']]==['dialogue','bed']
    assert plane['tracks'][0]['source_range']=={'from':1.5,'to':3.5}
    bed=plane['tracks'][1]
    assert bed['placed_range']=={'from':2,'to':4}
    assert bed['result_range']=={'from':2,'to':3}
    assert bed['source_range']=={'from':.25,'to':1.25}
    assert bed['gain_db']==-12 and bed['fade_in']==bed['fade_out']==.5
    assert jobs[plane['job']]['duration']==2
    assert PROFILE['max_independent_audio_tracks']==2
    assert str(root) not in json.dumps(packet)
    assert (root/'moviestar/spec.json').read_bytes()==before


@pytest.mark.parametrize('third', ['audible','muted','outside'])
def test_two_track_limit_counts_only_audible_tracks_in_the_selected_window(pip_project,third):
    spec,_,root=dialogue_project(pip_project)
    for name in ('bed','extra'):
        track=deepcopy(spec['audio_mix']['tracks'][0]);track['id']=name
        spec['audio_mix']['tracks'].append(track)
    if third=='muted':spec['audio_mix']['tracks'][2]['muted']=True
    if third=='outside':spec['audio_mix']['tracks'][2]['at']='0:00:04.000'
    save_spec(spec,cwd=root)
    if third=='audible':
        with pytest.raises(NativePreviewUnsupported,match='at most two independent'):
            compile_snapshot(read_snapshot(root,1,3))
    else:
        packet,_=compile_snapshot(read_snapshot(root,1,3))
        assert [t['id'] for t in packet['audio_plane']['tracks'] if not t['muted']]==['dialogue','bed']


@pytest.mark.parametrize('source_audible', [False,True])
def test_overlapping_tracks_match_full_export_slice_and_independent_sum(tmp_path,source_audible):
    source,dialogue,bed=[tmp_path/(name+'.wav') for name in ('source','dialogue','bed')]
    for path,expression in ((source,'.01|.02'),(dialogue,'.1|.2'),(bed,'.2|-.1')):
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
            f'aevalsrc={expression}:s=48000:d=6','-c:a','pcm_f32le',str(path)],check=True,timeout=10)
    intent=default_audio_mix();intent['source_audio'].update(muted=not source_audible,gain_db=-6)
    intent['tracks']=[{'id':'dialogue','kind':'voiceover','path':str(dialogue),'at':.5,
        'source_from':.25,'source_to':5.75,'gain_db':-6,'fade_in':2,'fade_out':2},
        {'id':'bed','kind':'music','path':str(bed),'at':2,'source_from':.5,'source_to':3.5,
        'gain_db':-12,'fade_in':1,'fade_out':1}]
    mix=resolve_audio_mix(intent,result_duration=6,base_dir=tmp_path,probe_audio=_audio_probe_file)
    start,duration=1.125,4.5
    actual_path=prepare_audio({'mix':mix,'result_duration':6,'window_start':start,'duration':duration,
        'source_pieces':[{'path':str(source) if source_audible else None,'source_start':start,'duration':duration}]},tmp_path/'native')
    graph=_build_mix_layer_graph(mix,6,source_audio_available=source_audible)
    parts,label=_finish_mix_graph(graph)
    full=subprocess.check_output(['ffmpeg','-v','error','-i',str(source),'-i',str(dialogue),'-i',str(bed),
        '-filter_complex',';'.join(parts),'-map',f'[{label}]','-f','f32le','-'],timeout=10)
    (tmp_path/'full-reference.f32').write_bytes(full)
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(actual_path),'-f','f32le','-'],timeout=10)
    actual=np.frombuffer(raw,dtype='<f4').reshape(-1,2)
    expected=np.frombuffer(full,dtype='<f4').reshape(-1,2)[round(start*48000):round((start+duration)*48000)]
    assert actual.shape==expected.shape
    assert abs(actual-expected).max()<1e-5
    time=start+np.arange(len(actual))/48000
    def envelope(at,length,fade):
        clock=time-at
        return np.clip(clock/fade,0,1)*np.clip((length-clock)/fade,0,1)
    analytic=envelope(.5,5.5,2)[:,None]*10**(-6/20)*np.array([.1,.2])
    analytic+=envelope(2,3,1)[:,None]*10**(-12/20)*np.array([.2,-.1])
    if source_audible:analytic+=10**(-6/20)*np.array([.01,.02])
    # These levels stay below the canonical limiter ceiling, so the independent
    # per-sample sum detects an omitted/duplicated layer or wrong placement.
    assert abs(actual-analytic).max()<1e-5


def test_second_audio_file_change_invalidates_preview_without_a_spec_edit(pip_project):
    spec,_,root=dialogue_project(pip_project)
    bed=root/'bed.wav';bed.write_bytes((root/'dialogue.wav').read_bytes())
    second=deepcopy(spec['audio_mix']['tracks'][0]);second.update(id='bed',kind='music',path=str(bed))
    spec['audio_mix']['tracks'].append(second);save_spec(spec,cwd=root)
    before=read_snapshot(root,1,3)
    data=bed.read_bytes();bed.write_bytes(data[:-2]+b'\1\0')
    after=read_snapshot(root,1,3)
    assert after['state_id']==before['state_id']
    assert after['input_identity']!=before['input_identity']
