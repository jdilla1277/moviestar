"""Owned, sequential browser sessions preserve frame isolation and cleanup."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from moviestar.spatial_layout import SpatialLayoutError, browser_available, prepare_layout_frame, _run_browser
from moviestar.spatial_session import BrowserSession
from tests.test_spatial_layout import _frame
from tests.test_spatial_render import project


@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    import moviestar.spatial_session as session

    script = tmp_path / 'spatial_browser.mjs'
    script.write_text('''
import json, os, subprocess, sys, time
fail_shutdown = False
for line in sys.stdin:
    message = json.loads(line)
    mode = message['request'].get('mode', 'ok')
    fail_shutdown |= mode == 'shutdown_error'
    if mode == 'hang':
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        marker = message['request'].get('marker')
        if marker:
            from pathlib import Path
            Path(marker).write_text(json.dumps({'pid': child.pid, 'profile': sys.argv[2]}))
        time.sleep(60)
    if mode == 'exit':
        sys.stderr.write('driver crashed\\n'); sys.stderr.flush()
        sys.exit(1)
    if mode == 'oversized':
        sys.stdout.write('x' * 10000); sys.stdout.flush(); time.sleep(60)
    if mode == 'bad_json':
        print('not JSON', flush=True); continue
    if mode == 'partial':
        sys.stdout.write('{'); sys.stdout.flush(); time.sleep(60)
    if mode == 'error':
        print(json.dumps({'id': message['id'], 'error': 'scene/label: invalid CSS'}), flush=True)
        sys.exit(1)
    sys.stderr.write('diagnostic ' * 12000); sys.stderr.flush()
    print(json.dumps({'id': message['id'] + (1 if mode == 'wrong_id' else 0),
                      'result': {'pid': os.getpid(), 'value': message['request'].get('value')}}), flush=True)
if fail_shutdown:
    sys.stderr.write('cleanup failed\\n'); sys.stderr.flush(); sys.exit(1)
''')
    monkeypatch.setattr(session, '_MAX_RESPONSE', 4096)
    monkeypatch.setattr(session, '_chrome_path', lambda: 'owned-chrome')
    monkeypatch.setattr(session.shutil, 'which', lambda name: sys.executable)
    monkeypatch.setattr(session, 'files', lambda package: tmp_path)
    return session


def test_session_reuses_one_worker_and_drains_stderr(fake_worker):
    with BrowserSession() as session:
        first = session.run({'value': 1})
        profile = Path(session._profile.name)
        second = session.run({'value': 2})
        assert first['pid'] == second['pid']
        assert [first['value'], second['value']] == [1, 2]
        assert len(session._stderr) <= 8192
    assert session._process.poll() == 0
    assert not profile.exists()
    with pytest.raises(SpatialLayoutError, match='closed'):
        session.run({})


@pytest.mark.parametrize('mode,reason', [('bad_json', 'JSON'), ('wrong_id', 'response'),
    ('error', 'scene/label: invalid CSS'), ('exit', 'driver crashed'), ('partial', 'timed out'),
    ('oversized', 'oversized')])
def test_protocol_failure_closes_worker_and_cannot_replay_a_frame(fake_worker, monkeypatch, mode, reason):
    monkeypatch.setattr(fake_worker, '_REQUEST_TIMEOUT', .5)
    with BrowserSession() as session:
        session.run({'value': 'before'})
        profile = Path(session._profile.name)
        with pytest.raises(SpatialLayoutError, match=reason):
            session.run({'mode': mode})
        assert session._process.poll() is not None
        assert not profile.exists()
        with pytest.raises(SpatialLayoutError, match='closed'):
            session.run({'value': 'must not retry'})


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-group ownership')
def test_later_request_timeout_kills_descendants_and_removes_profile(fake_worker, monkeypatch, tmp_path):
    monkeypatch.setattr(fake_worker, '_REQUEST_TIMEOUT', .5)
    marker = tmp_path / 'child.json'
    with BrowserSession() as session:
        session.run({})
        with pytest.raises(SpatialLayoutError, match='timed out'):
            session.run({'mode': 'hang', 'marker': str(marker)})
    child = json.loads(marker.read_text())
    assert not Path(child['profile']).exists()
    state = subprocess.run(['ps', '-o', 'stat=', '-p', str(child['pid'])], capture_output=True, text=True).stdout.strip()
    assert not state or state.startswith('Z'), state


def test_stalled_input_write_is_included_in_request_deadline(fake_worker, monkeypatch):
    monkeypatch.setattr(fake_worker, '_REQUEST_TIMEOUT', .5)
    # Stop reading before a request larger than the pipe buffer can be sent.
    script = Path(fake_worker.files('moviestar')) / 'spatial_browser.mjs'
    script.write_text('import time; time.sleep(60)')
    started = time.monotonic()
    with BrowserSession() as session:
        with pytest.raises(SpatialLayoutError, match='timed out'):
            session.run({'large': 'x' * 2_000_000})
    assert time.monotonic() - started < 3
    assert not session._writer.is_alive()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_browser_session_resets_canvas_tree_and_labeled_views(tmp_path):
    with BrowserSession() as session:
        for index, canvas in enumerate([{'width': 320, 'height': 180}, {'width': 180, 'height': 320},
                                         {'width': 320, 'height': 180}]):
            frame = _frame()
            if index == 1:
                frame['children'] = []
            prepared = prepare_layout_frame(frame, canvas)
            request = {**prepared, 'output_path': str(tmp_path / f'session-{index}.png')}
            actual = session.run(request)
            request['output_path'] = str(tmp_path / f'single-{index}.png')
            expected = _run_browser(request)
            assert actual == expected
            assert (tmp_path / f'session-{index}.png').read_bytes() == (tmp_path / f'single-{index}.png').read_bytes()


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_reused_render_session_matches_single_frames_with_motion_and_assets(project, tmp_path):
    from moviestar.resolved import resolve_project
    from moviestar.spatial_model import compile_spatial_frame
    from moviestar.spatial_render import render_spatial_frame

    spec, metadata = project
    node = spec['spatial']['scenes']['interview']['layout']['children'][0]
    node['motion'] = {'translate_x': [{'at': 0, 'value': 0}, {'at': .5, 'value': 12}],
                      'opacity': [{'at': 0, 'value': 1}, {'at': .5, 'value': .5}]}
    with BrowserSession() as session:
        for index, at in enumerate([0, .5, .2]):
            canvas = {'width': 320, 'height': 180} if index != 1 else {'width': 180, 'height': 320}
            frame = compile_spatial_frame(spec, resolve_project(metadata, spec), at, source_durations={'camera': 2})
            single, reused = (tmp_path / f'{kind}-{index}.png' for kind in ['single', 'reused'])
            expected = render_spatial_frame(spec, frame, canvas, output_path=single, project_root=tmp_path)
            actual = render_spatial_frame(spec, frame, canvas, output_path=reused, project_root=tmp_path,
                browser_session=session, layout_output_path=tmp_path / f'labels-{index}.png')
            assert actual['boxes'] == expected['boxes']
            assert actual['media'] == expected['media']
            assert actual['diagnostics'] == expected['diagnostics']
            def rgb(path):
                return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
            assert max(abs(a-b) for a,b in zip(rgb(single), rgb(reused))) <= 3



def test_failed_worker_shutdown_is_reported_and_preserves_primary_errors(fake_worker):
    session = BrowserSession()
    with pytest.raises(SpatialLayoutError, match='cleanup failed'):
        with session:
            session.run({'mode': 'shutdown_error'})
    assert not Path(session._profile.name).exists()
    with pytest.raises(ValueError, match='primary render failure'):
        with BrowserSession() as session:
            session.run({'mode': 'shutdown_error'})
            raise ValueError('primary render failure')


@pytest.mark.skipif(not browser_available(), reason='Chrome and Node are required')
def test_real_browser_error_preserves_artifact_and_closes_session(tmp_path):
    prior = tmp_path / 'prior.png'
    prior.write_bytes(b'previous preview')
    valid = prepare_layout_frame(_frame(), {'width': 320, 'height': 180})
    invalid = _frame()
    invalid['display'] = 'not-a-display'
    bad_request = {**prepare_layout_frame(invalid, {'width': 320, 'height': 180}),
                   'output_path': str(prior)}
    with BrowserSession() as session:
        session.run(valid)
        with pytest.raises(SpatialLayoutError, match='scene.layout.display.*invalid CSS'):
            session.run(bad_request)
        assert prior.read_bytes() == b'previous preview'
        assert session._process.poll() is not None
        assert not Path(session._profile.name).exists()
        with pytest.raises(SpatialLayoutError, match='closed'):
            session.run(valid)



def test_empty_session_does_not_start_a_worker(fake_worker, monkeypatch):
    def unexpected_start():
        raise AssertionError('empty session launched a worker')
    monkeypatch.setattr(fake_worker, '_chrome_path', unexpected_start)
    with BrowserSession() as session:
        assert session._process is None
    assert session._profile is None and session._closed


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-group ownership')
def test_node_worker_bounds_a_hung_cdp_request_without_a_python_watchdog(tmp_path):
    from importlib.resources import files
    from shutil import which
    import signal

    node = which('node')
    if not node:
        pytest.skip('Node is required')
    chrome = tmp_path / 'fake-chrome'
    chrome.write_text('#!' + sys.executable + '''
import sys, time
from pathlib import Path
profile = Path(next(arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--user-data-dir=')))
profile.mkdir(parents=True, exist_ok=True)
(profile / 'DevToolsActivePort').write_text('12345\\n/owned-target')
time.sleep(60)
''')
    chrome.chmod(0o755)
    preload = tmp_path / 'hung-cdp.mjs'
    preload.write_text('''
const timer = globalThis.setTimeout;
let deadlines = 0;
globalThis.setTimeout = (callback, milliseconds, ...args) =>
  timer(callback, milliseconds === 60000 && ++deadlines > 1 ? 100 : milliseconds, ...args);
globalThis.fetch = async () => Response.json([{type: 'page', webSocketDebuggerUrl: 'ws://owned-target'}]);
globalThis.WebSocket = class extends EventTarget {
  constructor() { super(); queueMicrotask(() => this.dispatchEvent(new Event('open'))); }
  send(data) {
    const {id, method} = JSON.parse(data);
    if (method === 'Runtime.evaluate') return; // Hung CDP request.
    queueMicrotask(() => this.dispatchEvent(new MessageEvent('message', {data: JSON.stringify({id, result: {}})})));
  }
  close() { this.dispatchEvent(new Event('close')); }
};
''')
    env = {**os.environ, 'NODE_OPTIONS': f'--import={preload.as_uri()}'}
    profile = tmp_path / 'profile'
    process = subprocess.Popen([node, str(files('moviestar').joinpath('spatial_browser.mjs')),
        str(chrome), str(profile), '--session'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    try:
        try:
            stdout, stderr = process.communicate(input=json.dumps({'id': 1,
                'request': {'canvas': {'width': 320, 'height': 180}}})+'\n', timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail('Node left its browser alive after a stalled render')
        assert process.returncode == 1, stderr
        response = json.loads(stdout)
        assert response['id'] == 1 and 'timed out' in response['error']
        assert not profile.exists()
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.communicate()
