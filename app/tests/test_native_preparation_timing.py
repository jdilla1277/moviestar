"""Opt-in phase evidence must explain a real owned worker without exposing inputs."""
import json
from pathlib import Path
import time

import pytest

from moviestar import local_preview
from tests.test_live_browser_preview import live_project


def complete(session):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        session.tick()
        latest=session.queue.state()['latest']
        if latest and latest['status'] in {'ready','failed'}:
            return latest
        time.sleep(.01)
    pytest.fail('owned native worker did not complete')


def test_real_worker_phase_totals_survive_publication_without_exposing_inputs(live_project,tmp_path,monkeypatch):
    monkeypatch.setenv('MOVIESTAR_PREVIEW_TIMING','1')
    folder=tmp_path/'requests';folder.mkdir()
    session=local_preview.PreviewSession(live_project[2],0,5,folder,mode='native')
    try:
        latest=complete(session)
        assert latest['status']=='ready',latest
        report=latest['preparation_timing']
        names=[p['name'] for p in report['phases']]
        assert names==['snapshot_validation','queue_dispatch','worker_startup','module_imports',
                       'request_read','descriptor_compile','descriptor_publish','worker_exit_and_publication']
        assert report['completed'] is True
        assert all(p['duration_ms']>=0 and p['end_ms']>=p['start_ms'] for p in report['phases'])
        assert all(a['end_ms']<=b['start_ms']+.001 for a,b in zip(report['phases'],report['phases'][1:]))
        assert sum(p['duration_ms'] for p in report['phases'])+report['unattributed_ms']==pytest.approx(report['elapsed_ms'])
        assert latest['generation_ms']==pytest.approx(report['elapsed_ms']-report['generation_started_ms'],abs=2)
        assert str(live_project[2]) not in json.dumps(report)
        assert 'Camera' not in json.dumps(report)
        before=session.queue.state();session.refresh()
        assert session.queue.state()==before,'Timing must never create a new edit identity'
    finally:
        session.stopped.set();session.run()


def test_worker_timing_is_off_by_default(live_project,tmp_path,monkeypatch):
    monkeypatch.delenv('MOVIESTAR_PREVIEW_TIMING',raising=False)
    folder=tmp_path/'requests';folder.mkdir()
    session=local_preview.PreviewSession(live_project[2],0,5,folder,mode='native')
    try:
        latest=complete(session)
        assert latest['status']=='ready',latest
        assert 'preparation_timing' not in latest
        assert not list(folder.rglob('timing.json'))
    finally:
        session.stopped.set();session.run()


def test_failed_worker_retains_incomplete_phase_evidence(live_project,tmp_path,monkeypatch):
    monkeypatch.setenv('MOVIESTAR_PREVIEW_TIMING','1')
    original=local_preview.subprocess.Popen
    def launch(args,**kwargs):
        request=Path(args[-1]);data=json.loads(request.read_text())
        data['range']['to']=20  # Actual child must reject this unsupported window.
        request.write_text(json.dumps(data))
        return original(args,**kwargs)
    monkeypatch.setattr(local_preview.subprocess,'Popen',launch)
    folder=tmp_path/'requests';folder.mkdir()
    session=local_preview.PreviewSession(live_project[2],0,5,folder,mode='native')
    try:
        latest=complete(session)
        assert latest['status']=='failed'
        report=latest['preparation_timing']
        assert report['completed'] is False
        assert 'descriptor_compile' in [p['name'] for p in report['phases']]
        assert 'descriptor_publish' not in [p['name'] for p in report['phases']]
        assert list(folder.iterdir())==[],'Failed worker must keep its existing cleanup boundary'
    finally:
        session.stopped.set();session.run()


def test_corrupt_timing_does_not_reject_a_valid_preview(live_project,tmp_path,monkeypatch):
    monkeypatch.setenv('MOVIESTAR_PREVIEW_TIMING','1')
    def reject(*args,**kwargs):raise ValueError('corrupt diagnostic')
    monkeypatch.setattr(local_preview,'published_timing',reject)
    folder=tmp_path/'requests';folder.mkdir()
    session=local_preview.PreviewSession(live_project[2],0,5,folder,mode='native')
    try:
        latest=complete(session)
        assert latest['status']=='ready'
        assert latest['preparation_timing']=={'unavailable':True}
    finally:
        session.stopped.set();session.run()


@pytest.mark.parametrize('corrupt', ['nan','overlap','oversized','private_text'])
def test_public_timing_checks_schema_and_never_copies_unknown_fields(corrupt):
    from moviestar.preview_timing import PreparationTiming,published_timing
    timer=PreparationTiming({'schema_version':1,'origin_perf':1,'started_at':1000,'phases':[],
        'generation_started_perf':1.1,'worker_started_perf':1.2,'worker_finished_perf':2,'completed':True})
    timer.add('snapshot_validation',1,1.1)
    timer.document['private_path']='/private/recording.mp4'
    if corrupt=='nan':timer.document['phases'][0]['end_ms']=float('nan')
    elif corrupt=='overlap':timer.add('descriptor_compile',1.05,1.2)
    elif corrupt=='oversized':timer.document['phases']*=33
    if corrupt!='private_text':
        with pytest.raises(ValueError):published_timing(timer.document,end=2.1)
    else:
        report=published_timing(timer.document,end=2.1)
        assert 'private' not in json.dumps(report)


def test_pending_request_does_not_expose_internal_timing_origin():
    queue=local_preview.PreviewQueue()
    queue.submit({'state_id':'first','preparation_timing':{'origin_perf':123,'phases':[]}})
    assert 'preparation_timing' not in queue.state()['latest']
    internal=queue.take()
    assert internal['preparation_timing']['origin_perf']==123


def test_launch_failure_explicitly_reports_unavailable_timing(live_project,tmp_path,monkeypatch):
    monkeypatch.setenv('MOVIESTAR_PREVIEW_TIMING','1')
    def fail(*args,**kwargs):raise OSError('cannot start worker')
    monkeypatch.setattr(local_preview.subprocess,'Popen',fail)
    folder=tmp_path/'requests';folder.mkdir()
    session=local_preview.PreviewSession(live_project[2],0,5,folder,mode='native')
    session.tick()
    latest=session.queue.state()['latest']
    assert latest['status']=='failed'
    assert latest['preparation_timing']=={'unavailable':True}
    assert list(folder.iterdir())==[]
