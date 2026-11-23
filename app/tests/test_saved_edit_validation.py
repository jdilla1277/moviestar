"""Saved mix metadata reads/dry runs: no media, journal writes or persistence."""
import copy
import pytest
from moviestar.spec import empty_spec, set_audio_mix
from moviestar.audio_surface import default_audio_mix
from moviestar.saved_edit_validation import prepare_saved_edit
from moviestar.edit_validation import EditValidationError

A = '00000000-0000-4000-8000-000000000021'
B = '00000000-0000-4000-8000-000000000022'
SOURCES = [{'source_id':'speaker','asset_id':A,'media':{'duration_ms':10000,'width':640,'height':360,'audio_codec':'aac'}}]


def saved():
    spec = empty_spec([{'id':'speaker','path':f'asset:{A}'}])
    mix = default_audio_mix()
    mix['source_audio'].update(gain_db=-2, fade_in=0.1, fade_out=0.2)
    return set_audio_mix(spec, mix)


def test_read_saved_mix_and_validate_without_changing_spec_or_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec = saved(); before = copy.deepcopy(spec)
    read = prepare_saved_edit(SOURCES, spec)
    check = prepare_saved_edit(SOURCES, spec, {'type':'audio_source','gain_db':-6})
    assert spec == before and not list(tmp_path.iterdir())
    assert read['origin'] == 'saved_revision'
    assert read['resolved']['audio_mix']['source_audio'] == before['audio_mix']['source_audio']
    assert read['resolved']['routing'][0]['source_id'] == 'speaker'
    assert check['changes'] is True and check['changed_fields'] == ['audio_mix']
    assert check['current_undo_remaining'] == 0
    assert check['projected_resolved']['undo_remaining'] == 1
    assert check['candidate_spec']['audio_mix']['source_audio'] == {**before['audio_mix']['source_audio'],'gain_db':-6}
    assert check['spec'] == before
    assert prepare_saved_edit(SOURCES, spec) == read


def test_noop_and_mute_preserve_full_mix():
    spec = saved()
    noop = prepare_saved_edit(SOURCES, spec, {'type':'audio_source','gain_db':-2,'muted':False})
    assert noop['changes'] is False and noop['changed_fields'] == []
    assert noop['candidate_spec'] == spec
    assert noop['projected_resolved']['undo_remaining'] == 0
    muted = prepare_saved_edit(SOURCES, spec, {'type':'audio_source','muted':True})
    assert muted['candidate_spec']['audio_mix']['source_audio']['muted'] is True
    assert muted['candidate_spec']['composition_audio_from'] == spec['composition_audio_from']


def test_saved_scene_dialogue_routing_survives_patch():
    from moviestar.spec import set_scene_composition
    sources=[*SOURCES,{'source_id':'camera','asset_id':B,'media':SOURCES[0]['media']}]
    spec=empty_spec([{'id':'speaker','path':f'asset:{A}'},{'id':'camera','path':f'asset:{B}'}])
    spec=set_scene_composition(spec,scenes=[
        {'name':name,'layout':'single','audio_from':source,'slots':[('main',source,1.0,5.0,{'mode':'fit'})]}
        for name,source in [('first','speaker'),('second','camera')]],source_durations={'speaker':10,'camera':10},
        canvas={'preset':None,'width':640,'height':360,'aspect_ratio':'16:9'})
    before=prepare_saved_edit(sources,spec)
    checked=prepare_saved_edit(sources,spec,{'type':'audio_source','muted':True})
    assert checked['candidate_spec']['composition']==spec['composition']
    assert [r['source_id'] for r in before['resolved']['routing']]==['speaker','camera']
    assert checked['projected_resolved']['routing']==before['resolved']['routing']


def test_populated_track_roundtrip_uses_only_supplied_audio_metadata():
    spec = saved()
    spec['audio_mix']['tracks'] = [{'id':'music','kind':'music','path':f'asset:{B}',
        'at':'0:00:01.000','source_from':'0:00:00.500','source_to':'0:00:02.500',
        'gain_db':-18,'muted':False,'loop':True,'fade_in':0.2,'fade_out':0.3,'ducking':{'under':['source'],'preset':'speech'}}]
    audio = [{'asset_id':B,'media':{'duration_ms':3000,'audio_codec':'pcm_s16le','sample_rate':48000,'channels':2}}]
    result = prepare_saved_edit(SOURCES, spec, {'type':'audio_source','muted':True}, audio_assets=audio)
    assert result['candidate_spec']['audio_mix']['tracks'] == spec['audio_mix']['tracks']
    track = result['resolved']['audio_mix']['tracks'][0]
    assert track['id'] == 'music' and track['path'] == f'asset:{B}' and track['loop'] is True
    assert track['media']['codec'] == 'pcm_s16le'
    assert track['media']['sample_rate'] == 48000 and track['media']['channels'] == 2
    assert result['resolved']['track_count'] == 1
    with pytest.raises(EditValidationError): prepare_saved_edit(SOURCES, spec)


@pytest.mark.parametrize('op', [
 {'type':'audio_source'}, {'type':'audio_source','muted':0}, {'type':'audio_source','muted':None},
 {'type':'audio_source','gain_db':True}, {'type':'audio_source','gain_db':float('nan')},
 {'type':'audio_source','gain_db':25}, {'type':'audio_source','gain_db':-61},
 {'type':'audio_source','source_id':'speaker','muted':True}, {'type':'audio_source','fade_in':1},
])
def test_invalid_patch_does_not_change_saved_state(op):
    spec = saved(); before = copy.deepcopy(spec)
    with pytest.raises(EditValidationError): prepare_saved_edit(SOURCES, spec, op)
    assert spec == before


@pytest.mark.parametrize('path', ['/private/input.mp4','https://example.invalid/video','asset:'+B])
def test_mismatched_or_nonportable_source_is_rejected(path):
    spec = saved(); spec['sources'][0]['path'] = path
    with pytest.raises(EditValidationError): prepare_saved_edit(SOURCES, spec)
