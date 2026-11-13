"""Native fades keep project/track time, including windows inside a fade."""
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

from moviestar.audio_surface import default_audio_mix, resolve_audio_mix
from moviestar.cli import _audio_probe_file
from moviestar.ffmpeg import _build_mix_layer_graph, _finish_mix_graph
from moviestar.local_preview import read_snapshot
from moviestar.native_audio import prepare_audio
from moviestar.native_preview import compile_snapshot
from moviestar.spec import save_spec
from tests.test_live_browser_preview import live_project
from tests.test_native_audio import dialogue_project
from tests.test_native_pip import pip_project


def test_source_and_track_fades_keep_unclipped_intent_in_inspection(pip_project):
    spec,_,root=dialogue_project(pip_project)
    spec['audio_mix']['source_audio'].update(muted=False,fade_in=2,fade_out=1)
    spec['audio_mix']['tracks'][0].update(fade_in=1.5,fade_out=1.5)
    save_spec(spec,cwd=root);before=(root/'moviestar/spec.json').read_bytes()
    packet,_=compile_snapshot(read_snapshot(root,1,3));plane=packet['audio_plane']
    assert plane['source_audio']['fade_in']==2
    assert plane['result_duration']==5
    track=plane['tracks'][0]
    assert track['fade_in']==track['fade_out']==1.5
    assert track['placed_range']=={'from':.5,'to':4.5}
    assert track['result_range']=={'from':1,'to':3}
    assert (root/'moviestar/spec.json').read_bytes()==before


@pytest.mark.parametrize('layer', ['source','track','both'])
def test_selected_window_matches_slice_of_full_export_fades(tmp_path,layer):
    source,track=tmp_path/'source.wav',tmp_path/'track.wav'
    for path,freq in ((source,220),(track,880)):
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'sine=frequency={freq}:sample_rate=48000:duration=6',
            '-ac','2','-c:a','pcm_f32le',str(path)],check=True,timeout=10)
    intent=default_audio_mix();intent['source_audio'].update(muted=layer=='track',gain_db=-6,
        fade_in=2 if layer!='track' else 0,fade_out=2 if layer!='track' else 0)
    if layer!='source':intent['tracks']=[{'id':'dialogue','kind':'voiceover','path':str(track),
        'at':.5,'source_from':.25,'source_to':5.75,'gain_db':-9,'fade_in':2,'fade_out':2}]
    mix=resolve_audio_mix(intent,result_duration=6,base_dir=tmp_path,probe_audio=_audio_probe_file)
    start,duration=1.125,4.5
    job={'mix':mix,'result_duration':6,'window_start':start,'duration':duration,
        'source_pieces':[{'path':str(source),'source_start':start,'duration':duration}]}
    actual_path=prepare_audio(job,tmp_path/'native')
    graph=_build_mix_layer_graph(mix,6,source_audio_available=True)
    parts,label=_finish_mix_graph(graph)
    inputs=['-i',str(source)]+[arg for p in graph['track_inputs'] for arg in ('-i',p)]
    full=subprocess.check_output(['ffmpeg','-v','error',*inputs,'-filter_complex',';'.join(parts),
        '-map',f'[{label}]','-ar','48000','-ac','2','-f','f32le','-'],timeout=10)
    (tmp_path/'full-reference.f32').write_bytes(full)
    native=subprocess.check_output(['ffmpeg','-v','error','-i',str(actual_path),'-f','f32le','-'],timeout=10)
    expected=np.frombuffer(full,dtype='<f4').reshape(-1,2)[round(start*48000):round((start+duration)*48000)]
    actual=np.frombuffer(native,dtype='<f4').reshape(-1,2)
    assert actual.shape==expected.shape
    assert abs(actual-expected).max()<1e-5


def test_partial_fade_matches_analytic_stereo_envelope(tmp_path):
    track=tmp_path/'constant.wav'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        'aevalsrc=0.1|0.2:s=48000:d=6','-c:a','pcm_f32le',str(track)],check=True,timeout=10)
    intent=default_audio_mix();intent['source_audio']['muted']=True
    intent['tracks']=[{'id':'dialogue','kind':'voiceover','path':str(track),'at':.5,
        'source_from':.25,'source_to':5.75,'gain_db':-6,'fade_in':2,'fade_out':2}]
    mix=resolve_audio_mix(intent,result_duration=6,base_dir=tmp_path,probe_audio=_audio_probe_file)
    window_start,duration=1.125,4.5
    actual_path=prepare_audio({'mix':mix,'result_duration':6,'window_start':window_start,'duration':duration,
        'source_pieces':[{'path':None,'source_start':0,'duration':duration}]},tmp_path/'native')
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(actual_path),'-f','f32le','-'],timeout=10)
    actual=np.frombuffer(raw,dtype='<f4').reshape(-1,2)
    track_time=window_start+np.arange(len(actual))/48000-.5
    envelope=10**(-6/20)*np.minimum(1,track_time/2)*np.minimum(1,(5.5-track_time)/2)
    expected=envelope[:,None]*np.array([.1,.2])
    assert abs(actual-expected).max()<1e-5
