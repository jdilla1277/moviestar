"""Local video-body accounting never changes range or asset-identity semantics."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from moviestar.local_preview import PreviewQueue, PreviewServer, PreviewSession
from moviestar.local_preview import read_snapshot
from moviestar.native_preview import asset_identity
from moviestar.spec import save_spec, load_spec
from moviestar.spatial_model import spatial_state_id
from tests.test_live_browser_preview import live_project
from tests.test_native_preview import _complete, _fake_workers


@pytest.fixture
def native_service(live_project, tmp_path, monkeypatch):
    root = live_project[2]
    folder = tmp_path / 'packets'; folder.mkdir()
    _fake_workers(monkeypatch)
    session = PreviewSession(root, 0, 5, folder, mode='native'); _complete(session)
    server = PreviewServer(session.queue, token='a' * 48, port=0, mode='native', session=session)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield server, session, root
    finally:
        server.shutdown(); server.server_close(); thread.join()


def transfers(server, *, bytes_written=None):
    deadline = time.monotonic() + 2
    while True:
        with urlopen(server.url + 'state', timeout=2) as response:
            state = json.load(response)['native_video_delivery']
        if not state['active_requests'] and (bytes_written is None or state['bytes_written'] == bytes_written):
            return state
        assert time.monotonic() < deadline, state
        time.sleep(.01)


def test_video_accounting_matches_completed_range_bodies_and_ignores_heads(native_service):
    server, session, root = native_service
    asset = next(iter(session.assets))
    assert transfers(server) == {'requests': 0, 'active_requests': 0, 'bytes_written': 0, 'validated_heads': 0}
    with urlopen(Request(server.url + 'assets/' + asset, method='HEAD', headers={'Range': 'bytes=2-5'})) as response:
        assert response.status == 206 and response.read() == b''
        assert response.headers['Content-Length'] == '4'
    assert transfers(server)['requests'] == 0
    assert transfers(server)['validated_heads'] == 1
    for requested, expected in [('bytes=2-5', (root / 'camera.mp4').read_bytes()[2:6]),
                                ('bytes=-3', (root / 'camera.mp4').read_bytes()[-3:])]:
        with urlopen(Request(server.url + 'assets/' + asset, headers={'Range': requested})) as response:
            assert response.status == 206 and response.read() == expected
            assert response.headers['Cache-Control'] == 'no-store'
    assert transfers(server, bytes_written=7) == {'requests': 2, 'active_requests': 0, 'bytes_written': 7, 'validated_heads': 1}


def test_video_accounting_is_concurrent_and_excludes_rejected_or_image_requests(native_service):
    server, session, root = native_service
    asset = next(iter(session.assets))
    image = root / 'logo.png'; image.write_bytes(b'image response')
    session.assets['b' * 24] = {'path': str(image), 'identity': asset_identity(image), 'kind': 'image'}
    with urlopen(server.url + 'assets/' + 'b' * 24) as response:
        assert response.read() == b'image response'
    def read_range(_):
        with urlopen(Request(server.url + 'assets/' + asset, headers={'Range': 'bytes=1-4'})) as response:
            return response.read()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(list(pool.map(read_range, range(8)))) == 8
    assert transfers(server, bytes_written=32) == {'requests': 8, 'active_requests': 0, 'bytes_written': 32, 'validated_heads': 0}
    for route, headers, status in [('assets/' + asset, {'Range': 'bytes=99999-'}, 416),
                                  ('assets/' + asset, {'Origin': 'https://example.com'}, 403),
                                  ('assets/' + 'c' * 24, {}, 404)]:
        with pytest.raises(HTTPError) as error:
            urlopen(Request(server.url + route, headers=headers))
        assert error.value.code == status
    (root / 'camera.mp4').write_bytes(b'changed source')
    with pytest.raises(HTTPError) as error:
        urlopen(server.url + 'assets/' + asset)
    assert error.value.code == 409
    assert transfers(server, bytes_written=32)['requests'] == 8


def test_rendered_mode_does_not_report_native_video_accounting():
    server = PreviewServer(PreviewQueue(), token='a' * 48, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with urlopen(server.url + 'state') as response:
            assert 'native_video_delivery' not in json.load(response)
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_abandoned_video_request_releases_active_accounting(native_service):
    server, session, root = native_service
    asset = next(iter(session.assets))
    source = root / 'camera.mp4'
    source.write_bytes(b'\0' * (16 * 1024 ** 2))
    session.assets[asset]['identity'] = asset_identity(source)
    response = urlopen(server.url + 'assets/' + asset, timeout=2)
    try:
        deadline = time.monotonic() + 2
        while True:
            with urlopen(server.url + 'state') as state_response:
                state = json.load(state_response)['native_video_delivery']
            if state['active_requests']:
                break
            assert time.monotonic() < deadline, state
            time.sleep(.01)
        # Let the producer fill the unread connection. Kernel socket-buffer
        # sizes vary by host; only cancellation/accounting cleanup is asserted.
        time.sleep(.1)
    finally:
        response.close()
    state = transfers(server)
    assert state['requests'] == 1
    assert 0 < state['bytes_written'] < source.stat().st_size


def test_only_explicit_source_buffer_reads_allow_private_revalidated_storage(native_service):
    server, session, root = native_service
    asset = next(iter(session.assets))
    headers = {'Range': 'bytes=2-5', 'X-Moviestar-Source-Buffer': 'memory'}
    with urlopen(Request(server.url + 'assets/' + asset, headers=headers)) as response:
        assert response.read() == (root / 'camera.mp4').read_bytes()[2:6]
        assert response.headers['Cache-Control'] == 'private, no-cache'
        assert response.headers['Vary'] == 'X-Moviestar-Source-Buffer'
    with urlopen(Request(server.url + 'assets/' + asset, method='HEAD', headers=headers)) as response:
        assert response.headers['Cache-Control'] == 'private, no-cache'
    (root / 'camera.mp4').write_bytes(b'replaced source')
    with pytest.raises(HTTPError) as error:
        urlopen(Request(server.url + 'assets/' + asset, method='HEAD', headers=headers))
    assert error.value.code == 409
    assert error.value.headers['Cache-Control'] == 'no-store'


def test_preview_snapshot_omits_undo_payload_without_changing_project_or_version(live_project):
    spec, _, root = live_project
    for index in range(12):
        spec['spatial']['scenes']['scene']['layout']['children'][1]['content']['text'] = f'Edit {index}'
        spec = save_spec(spec, cwd=root, command='composition model edit')
    path = root / 'moviestar/spec.json'
    before = path.read_bytes()
    assert len(load_spec(root)['revisions']) == 12
    snapshot = read_snapshot(root, 0, 5)
    assert snapshot['spec']['revisions'] == []
    assert snapshot['state_id'] == spatial_state_id(load_spec(root))
    assert snapshot['spec']['spatial'] == load_spec(root)['spatial']
    assert path.read_bytes() == before


def test_idle_watcher_does_not_reparse_but_detects_edits_assets_and_errors(live_project, monkeypatch):
    import moviestar.local_preview as module
    spec, _, root = live_project
    read, calls = module.read_snapshot, []
    def counted(*args):
        calls.append(None)
        return read(*args)
    monkeypatch.setattr(module, 'read_snapshot', counted)
    session = module.PreviewSession(root, 0, 5, root / 'previews', mode='native')
    session.refresh()
    for _ in range(5):
        session.refresh()
    assert len(calls) == 1, 'Idle polls should only stat the pinned input files'
    initial = session.queue.state()['latest']
    (root / 'camera.mp4').touch()
    session.refresh()
    assert len(calls) == 2
    assert session.queue.state()['latest']['request_id'] != initial['request_id']
    spec['spatial']['scenes']['scene']['layout']['background_color'] = '#123456'
    save_spec(spec, cwd=root, command='composition model edit')
    session.refresh()
    assert len(calls) == 3
    edited = session.queue.state()['latest']
    assert edited['state_id'] != initial['state_id']
    path = root / 'moviestar/project.json'
    original = path.read_bytes()
    path.write_text('{')
    session.refresh()
    assert session.queue.state()['latest']['status'] == 'failed'
    path.write_bytes(original)
    session.refresh()
    assert session.queue.state()['latest']['state_id'] == edited['state_id']
    assert session.queue.state()['latest']['status'] == 'queued'


def test_edit_during_snapshot_read_is_not_hidden_by_idle_check(live_project, monkeypatch):
    import moviestar.local_preview as module
    spec, _, root = live_project
    read = module.read_snapshot
    def racing(*args):
        snapshot = read(*args)
        spec['spatial']['scenes']['scene']['layout']['background_color'] = '#123456'
        save_spec(spec, cwd=root, command='composition model edit')
        monkeypatch.setattr(module, 'read_snapshot', read)
        return snapshot
    monkeypatch.setattr(module, 'read_snapshot', racing)
    session = module.PreviewSession(root, 0, 5, root / 'previews', mode='native')
    session.refresh()
    initial = session.queue.state()['latest']
    session.refresh()
    assert session.queue.state()['latest']['state_id'] != initial['state_id']


def test_idle_stat_error_recovery_reloads_even_when_metadata_is_unchanged(live_project, monkeypatch):
    import moviestar.local_preview as module
    root = live_project[2]
    session = module.PreviewSession(root, 0, 5, root / 'previews', mode='native')
    session.refresh()
    with monkeypatch.context() as fault:
        def unavailable(*_):
            raise OSError('temporarily unavailable')
        fault.setattr(module, '_file_stamps', unavailable)
        session.refresh()
        assert session.queue.state()['latest']['status'] == 'failed'
    session.refresh()
    assert session.queue.state()['latest']['status'] == 'queued'


def test_idle_watcher_detects_retargeted_source_symlink(live_project):
    from moviestar.project import load_project, save_project
    spec, _, root = live_project
    link = root / 'linked.mp4'
    link.symlink_to(root / 'camera.mp4')
    spec['sources'][0]['path'] = str(link)
    save_spec(spec, cwd=root)
    project = load_project(root)
    project['sources'][0]['path'] = str(link)
    save_project(project, cwd=root)
    session = PreviewSession(root, 0, 5, root / 'previews', mode='native')
    session.refresh()
    before = session.queue.state()['latest']['request_id']
    replacement = root / 'replacement.mp4'
    replacement.write_bytes((root / 'camera.mp4').read_bytes())
    link.unlink(); link.symlink_to(replacement)
    session.refresh()
    assert session.queue.state()['latest']['request_id'] != before
