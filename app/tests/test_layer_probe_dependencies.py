"""A report tap processes only its audible dependency closure."""
import subprocess

import pytest

from moviestar.ffmpeg import build_layer_loudness_probe_command, _parse_loudnorm_json
from tests.test_ffmpeg import _resolved_mix, _resolved_track


def test_independent_probe_omits_unrelated_files_and_source():
    mix = _resolved_mix(tracks=[_resolved_track('target.wav'),
        _resolved_track('unrelated.wav', id='other')])
    cmd = build_layer_loudness_probe_command('base.mp4', mix, 2,
        source_audio_available=True, layer_id='music')
    assert [cmd[i+1] for i,arg in enumerate(cmd) if arg=='-i'] == ['base.mp4','target.wav']
    graph = cmd[cmd.index('-filter_complex')+1]
    assert '[0:a]' not in graph and 'other' not in graph


@pytest.mark.parametrize('mode', ['independent','shared','nested','muted','outside','source','collision'])
@pytest.mark.parametrize('window', [(0,2),(.7,.6)])
def test_pruned_loudness_matches_full_graph(test_video,audio_only_file,mode,window):
    def duck(under):
        return {'under':[under],'preset':'speech','resolved':{'threshold_db':-30,
            'ratio':8,'attack_ms':20,'release_ms':250}}
    voice = _resolved_track(audio_only_file,id='voice')
    bed = _resolved_track(audio_only_file,id='bed',loop=True,gain_db=-8,
        placed_range={'from':{'seconds':.2},'to':{'seconds':1.8},'duration':{'seconds':1.6}})
    other = _resolved_track(audio_only_file,id='unrelated',ducking=duck('voice'))
    if mode != 'independent':bed['ducking']=duck('voice')
    if mode=='nested':voice['ducking']=duck('source')
    if mode=='muted':voice['muted']=True
    if mode=='outside':
        voice['placed_range']={'from':{'seconds':3},'to':{'seconds':4},'duration':{'seconds':1}}
    if mode=='collision':
        voice['id']='bed-voice';bed['id']='bed_voice'
        bed['ducking']=other['ducking']=duck('bed-voice')
    mix=_resolved_mix(tracks=[voice,bed,other])
    target='source' if mode=='source' else bed['id']
    commands=[build_layer_loudness_probe_command(test_video,mix,2,
        source_audio_available=True,layer_id=target,window_start=window[0],
        window_duration=window[1],prune_unrelated=prune) for prune in [False,True]]
    metrics=[]
    for cmd in commands:
        measured=subprocess.run(cmd,capture_output=True,text=True,check=True,timeout=15)
        raw=_parse_loudnorm_json(measured.stderr)
        metrics.append({key:raw[key] for key in ['input_i','input_tp','input_lra','input_thresh']})
    assert metrics[0]==metrics[1]


def test_pruning_does_not_hide_invalid_unrelated_ducking_cycles():
    duck=lambda under: {'under':[under],'resolved':{
        'threshold_db':-30,'ratio':8,'attack_ms':20,'release_ms':250}}
    mix=_resolved_mix(tracks=[_resolved_track('target.wav'),
        _resolved_track('a.wav',id='a',ducking=duck('b')),
        _resolved_track('b.wav',id='b',ducking=duck('a'))])
    with pytest.raises(ValueError,match='ducking cycle'):
        build_layer_loudness_probe_command('base.mp4',mix,2,
            source_audio_available=True,layer_id='music')
