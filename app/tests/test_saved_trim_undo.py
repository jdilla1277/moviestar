"""Actual engine result-clock trim and whole-command undo without IO."""
import copy
import pytest
from moviestar.spec import empty_spec, append_revision
from moviestar.saved_edit_validation import prepare_saved_edit
from moviestar.edit_validation import EditValidationError

S='speaker'; A='00000000-0000-4000-8000-000000000021'
INPUTS=[{'source_id':S,'asset_id':A,'media':{'duration_ms':109533,'width':1080,'height':1920,'audio_codec':'aac'}}]
TRIM={'type':'trim','source_id':S,'time_space':'source_result','from_ms':10000,'to_ms':40000}

def test_trim_retrim_and_undo_return_observable_source_ranges(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    baseline=empty_spec([{'id':S,'path':'asset:'+A}])
    before=copy.deepcopy(baseline)
    first=prepare_saved_edit(INPUTS,baseline,TRIM)
    assert first['projected_resolved']['sources'][0]['source_segments_ms']==[[10000,40000]]
    assert first['projected_resolved']['result_duration_ms']==30000
    assert first['current_undo_remaining']==0
    one=first['candidate_spec']
    second=prepare_saved_edit(INPUTS,one,{**TRIM,'from_ms':5000,'to_ms':20000})
    assert second['projected_resolved']['sources'][0]['source_segments_ms']==[[15000,30000]]
    assert second['projected_resolved']['result_duration_ms']==15000
    undone=prepare_saved_edit(INPUTS,second['candidate_spec'],{'type':'undo'})
    assert undone['candidate_spec']==one
    restored=prepare_saved_edit(INPUTS,undone['candidate_spec'],{'type':'undo'})
    assert restored['candidate_spec']==baseline==before
    assert restored['projected_resolved']['undo_remaining']==0
    assert list(tmp_path.iterdir())==[]

def test_undo_restores_all_journaled_fields():
    baseline=empty_spec([{'id':S,'path':'asset:'+A}])
    after=copy.deepcopy(baseline)
    after['audio_mix']['source_audio']['gain_db']=-6
    after['sources'][0]['operations']=[{'type':'trim','from':'0:00:10.000','to':'0:00:40.000'}]
    changed=append_revision(baseline,after,'coupled command')
    assert prepare_saved_edit(INPUTS,changed,{'type':'undo'})['candidate_spec']==baseline

@pytest.mark.parametrize('op',[{'type':'undo','source_id':S},{**TRIM,'from_ms':True},{**TRIM,'time_space':'source'}])
def test_strict_operations(op):
    with pytest.raises(EditValidationError):prepare_saved_edit(INPUTS,empty_spec([{'id':S,'path':'asset:'+A}]),op)

def test_empty_undo_is_actionable():
    with pytest.raises(EditValidationError) as error:prepare_saved_edit(INPUTS,empty_spec([{'id':S,'path':'asset:'+A}]),{'type':'undo'})
    assert error.value.code=='nothing_to_undo'

def test_full_current_range_is_noop_without_journal_entry():
    baseline=empty_spec([{'id':S,'path':'asset:'+A}])
    noop=prepare_saved_edit(INPUTS,baseline,{**TRIM,'from_ms':0,'to_ms':109533})
    assert noop['changes'] is False
    assert noop['candidate_spec']==baseline
    assert noop['projected_resolved']['undo_remaining']==0


def test_local_trim_and_undo_dry_runs_do_not_change_spec(test_video,tmp_path,monkeypatch):
    import json
    from click.testing import CliRunner
    from moviestar.cli import cli
    monkeypatch.chdir(tmp_path);runner=CliRunner()
    assert runner.invoke(cli,['load',test_video,'--as',S,'--no-transcribe','--no-frames']).exit_code==0
    path=tmp_path/'moviestar/spec.json'
    before=path.read_bytes() if path.exists() else None
    checked=runner.invoke(cli,['trim','--from','0.1','--to','0.8','--dry-run'])
    assert checked.exit_code==0,checked.output
    projected=json.loads(checked.output)
    assert projected['status']=='would_apply' and projected['writes_spec'] is False
    assert (path.read_bytes() if path.exists() else None)==before
    assert runner.invoke(cli,['trim','--from','0.1','--to','0.8']).exit_code==0
    saved=path.read_bytes()
    undo=runner.invoke(cli,['undo','--dry-run'])
    assert undo.exit_code==0,undo.output
    assert json.loads(undo.output)['status']=='would_undo'
    assert path.read_bytes()==saved
