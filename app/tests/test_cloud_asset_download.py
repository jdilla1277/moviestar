"""Single-original downloads exercise real HTTP and atomic local publication."""
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from click.testing import CliRunner
from moviestar import cloud as service

ASSET = '00000000-0000-4000-8000-000000000001'
OWNER = '00000000-0000-4000-8000-000000000002'
DATA = b'original bytes\x00\xff'
TOKEN = 'mvs_' + 'a' * 43


@pytest.fixture
def remote(monkeypatch):
    state = {'requests': [], 'mode': 'ok', 'kind': 'image', 'content_type': 'image/png', 'token': TOKEN}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def json_response(self, body, status=200):
            self.send_response(status); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            state['requests'].append((self.path, self.headers.get('Authorization'), self.headers.get('x-moviestar-client')))
            if self.path.endswith('/capabilities'):
                return self.json_response({'contract_version': 'm33.3-personal-projects-1', 'project_access': 'owner_only', 'operations': ['projects:read'],
                    'project_assets': {'contract_version': 'project-asset-references-v1', 'access': 'owned_assets_in_owned_projects',
                        'ready_only': True, 'kinds': ['video', 'audio', 'image', 'transcript'], 'operations': ['assets:read']}})
            if self.path.endswith('/assets/' + ASSET):
                if state['mode'] == 'inaccessible':
                    return self.json_response({'error': {'code': 'not_found'}}, 404)
                asset = {'id': ASSET, 'owner_account_id': OWNER, 'filename': '../../original', 'display_name': 'Renamed asset',
                    'kind': state['kind'], 'format': 'test', 'content_type': state['content_type'], 'size_bytes': len(DATA), 'state': 'ready',
                    'metadata': {}, 'created_at': '2026-11-24T10:00:00Z', 'ready_at': None}
                path = 'https://attacker.invalid/steal' if state['mode'] == 'foreign_path' else f'/api/v1/account/cloud/assets/{ASSET}/download'
                return self.json_response({'asset': asset, 'download_path': path})
            if state['mode'] in ('revoked', 'expired'):
                return self.json_response({'error': {'code': 'not_found' if state['mode'] == 'revoked' else 'credential_invalid'}}, 404 if state['mode'] == 'revoked' else 401)
            if state['mode'] == 'redirect':
                self.send_response(302); self.send_header('Location', '/must-not-follow'); self.end_headers(); return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html' if state['mode'] == 'html' else state['content_type'])
            if state['mode'] == 'wrong_length':
                self.send_header('Content-Length', str(len(DATA) + 1))
            self.end_headers()
            self.wfile.write(DATA[:-1] if state['mode'] == 'short' else DATA + b'x' if state['mode'] == 'long' else DATA)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    endpoint = f'http://127.0.0.1:{server.server_port}/api/v1/account/cloud'
    monkeypatch.setattr(service, '_connection', lambda command: (endpoint, state['token']))
    yield state
    server.shutdown(); thread.join(); server.server_close()


def invoke(path, asset_id=ASSET):
    result = CliRunner().invoke(service.cloud, ['assets', 'download', asset_id, '--out', str(path)])
    return result, json.loads(result.output)


@pytest.mark.parametrize('kind,content_type', [('video', 'video/mp4'), ('audio', 'audio/mpeg'), ('image', 'image/png'), ('transcript', 'text/vtt')])
def test_saves_exact_original_bytes_with_local_hash_and_explicit_destination(remote, tmp_path, kind, content_type):
    remote.update(kind=kind, content_type=content_type)
    path = tmp_path / 'chosen-file'
    result, body = invoke(path)
    assert result.exit_code == 0, body
    assert body['status'] == 'downloaded' and body['asset_id'] == ASSET
    assert body['file'] == str(path) and body['size_bytes'] == len(DATA)
    assert body['sha256'] == hashlib.sha256(DATA).hexdigest() and path.read_bytes() == DATA
    assert list(tmp_path.iterdir()) == [path]
    assert all(auth == 'Bearer ' + TOKEN and marker == 'moviestar-cli-v1' for _, auth, marker in remote['requests'])
    assert TOKEN not in result.output


@pytest.mark.parametrize('mode', ['revoked', 'expired', 'redirect', 'short', 'long', 'html', 'wrong_length', 'foreign_path', 'inaccessible'])
def test_failed_transfer_has_no_final_file_or_temp_residue_and_never_follows_redirects(remote, tmp_path, mode):
    remote['mode'] = mode
    path = tmp_path / 'download'
    result, body = invoke(path)
    assert result.exit_code == 1 and body['status'] != 'downloaded'
    assert list(tmp_path.iterdir()) == []
    assert not any('/must-not-follow' in request[0] for request in remote['requests'])
    assert len(remote['requests']) <= 3
    assert TOKEN not in result.output
    if mode == 'revoked':
        assert body['status'] == 'not_found'
    if mode == 'expired':
        assert body['status'] == 'credential_invalid'


@pytest.mark.parametrize('kind', ['file', 'directory', 'symlink', 'dangling_symlink'])
def test_existing_destinations_are_never_overwritten_or_sent_over_network(remote, tmp_path, kind):
    path = tmp_path / 'existing'
    if kind == 'file':
        path.write_bytes(b'keep')
    elif kind == 'directory':
        path.mkdir()
    else:
        target = tmp_path / 'target'
        if kind == 'symlink':
            target.write_bytes(b'keep')
        path.symlink_to(target)
    result, body = invoke(path)
    assert result.exit_code == 1 and body['status'] == 'destination_exists'
    assert remote['requests'] == []
    if kind == 'file':
        assert path.read_bytes() == b'keep'
    elif kind == 'symlink':
        assert (tmp_path / 'target').read_bytes() == b'keep'


def test_checks_input_and_parent_before_network(remote, tmp_path):
    for path, asset_id in [(tmp_path / 'missing' / 'file', ASSET), (tmp_path / 'file', 'not-an-id')]:
        result, body = invoke(path, asset_id)
        assert result.exit_code == 1 and body['status'] in ('invalid_destination', 'invalid_request')
    assert remote['requests'] == []


def test_api_key_download_uses_same_explicit_account_credential(remote, tmp_path):
    remote['token'] = 'mvsk_' + 'b' * 43
    result, body = invoke(tmp_path / 'key-original')
    assert result.exit_code == 0, body
    assert all(auth == 'Bearer ' + remote['token'] for _, auth, _ in remote['requests'])
    assert remote['token'] not in result.output


def test_destination_created_during_transfer_is_preserved(remote, tmp_path, monkeypatch):
    from moviestar import cloud_asset_download
    path = tmp_path / 'racing-file'
    original = cloud_asset_download._transfer

    def transfer(*args):
        digest = original(*args)
        path.write_bytes(b'created elsewhere')
        return digest

    monkeypatch.setattr(cloud_asset_download, '_transfer', transfer)
    result, body = invoke(path)
    assert result.exit_code == 1 and body['status'] == 'destination_exists'
    assert path.read_bytes() == b'created elsewhere'
    assert list(tmp_path.iterdir()) == [path]
