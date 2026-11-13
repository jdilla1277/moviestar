"""Real HTTP transfers exercise credentials, pagination and partial downloads."""
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner
from moviestar import cloud as service

PROJECT = '00000000-0000-4000-8000-000000000001'
OWNER = '00000000-0000-4000-8000-000000000002'
REFS = ['00000000-0000-4000-8000-000000000003', '00000000-0000-4000-8000-000000000004']
DATA = b'original bytes\x00\xff'
TOKEN = 'mvs_' + 'a' * 43


@pytest.fixture
def remote(monkeypatch):
    state = {'requests': [], 'mode': 'ok', 'pages': 1}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            state['requests'].append((self.path, self.headers.get('Authorization'), self.headers.get('x-moviestar-client')))
            if '/download-manifest' in self.path:
                index = 1 if 'cursor=' in self.path else 0
                entry = {'reference_id': REFS[index], 'asset_id': OWNER, 'filename': '../../same.png',
                         'kind': 'image', 'content_type': 'image/png', 'size_bytes': len(DATA),
                         'download_path': f'/api/v1/account/cloud/projects/{PROJECT}/asset-references/{REFS[index]}/download'}
                if state['mode'] == 'foreign_path':
                    entry['download_path'] = 'https://attacker.invalid/steal'
                body = json.dumps({'contract_version': 'project-download-v1',
                    'project': {'id': PROJECT, 'owner_id': OWNER, 'title': 'Test', 'state': 'active', 'current_revision': None, 'access': {'role': 'viewer'}},
                    'items': [entry], 'next_cursor': 'bmV4dA' if state['pages'] == 2 and index == 0 else None}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
                return
            mode = state['mode']
            if mode in ('revoked', 'redirect'):
                self.send_response(404 if mode == 'revoked' else 302)
                self.send_header('Location', '/must-not-follow'); self.end_headers(); return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html' if mode == 'html' else 'image/png')
            self.end_headers()
            self.wfile.write(DATA[:-1] if mode == 'short' else DATA + b'x' if mode == 'long' else DATA)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    endpoint = f'http://127.0.0.1:{server.server_port}/api/v1/account/cloud'
    monkeypatch.setattr(service, '_connection', lambda command: (endpoint, TOKEN))
    yield state
    server.shutdown(); thread.join(); server.server_close()


def invoke(destination):
    return CliRunner().invoke(service.cloud, ['projects', 'download', PROJECT, '--out', str(destination)])


def test_downloads_all_pages_without_clicks_and_preserves_original_names_safely(remote, tmp_path):
    remote['pages'] = 2
    target = tmp_path / 'project'
    result = invoke(target)
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output['status'] == 'downloaded' and output['asset_count'] == 2
    manifest = json.loads((target / 'cloud-project.json').read_text())
    assert manifest['status'] == 'complete' and manifest['project']['id'] == PROJECT
    assert manifest['project']['access']['role'] == 'viewer'
    for item in manifest['items']:
        path = target / item['local_path']
        assert path.is_relative_to(target) and path.read_bytes() == DATA
        assert item['filename'] == '../../same.png'
        assert item['sha256'] == hashlib.sha256(DATA).hexdigest()
    assert len({item['local_path'] for item in manifest['items']}) == 2
    assert not (target / '.incomplete.json').exists()
    assert all(auth == 'Bearer ' + TOKEN and marker == 'moviestar-cli-v1' for _, auth, marker in remote['requests'])
    assert TOKEN not in ''.join(p.read_text(errors='ignore') for p in target.rglob('*') if p.is_file())


@pytest.mark.parametrize('mode', ['revoked', 'redirect', 'short', 'long', 'html'])
def test_failed_transfer_never_claims_completion_or_follows_redirects(remote, tmp_path, mode):
    remote['mode'] = mode
    target = tmp_path / 'partial'
    result = invoke(target)
    assert result.exit_code == 1
    output = json.loads(result.output)
    assert output['status'] == 'download_incomplete'
    assert not (target / 'cloud-project.json').exists()
    assert (target / '.incomplete.json').exists()
    assert not any('/must-not-follow' in path for path, *_ in remote['requests'])
    assert not list(target.rglob('*.png'))


def test_rejects_foreign_download_paths_before_any_transfer(remote, tmp_path):
    remote['mode'] = 'foreign_path'
    target = tmp_path / 'project'
    result = invoke(target)
    assert result.exit_code == 1 and json.loads(result.output)['status'] == 'invalid_response'
    assert not target.exists()
    assert len(remote['requests']) == 1


@pytest.mark.parametrize('symlink', [False, True])
def test_existing_destination_is_preserved_before_network(remote, tmp_path, symlink):
    existing = tmp_path / 'existing'; existing.mkdir(); (existing / 'keep.txt').write_text('keep')
    destination = tmp_path / 'link' if symlink else existing
    if symlink:
        destination.symlink_to(existing, target_is_directory=True)
    result = invoke(destination)
    assert result.exit_code == 1 and not remote['requests']
    assert (existing / 'keep.txt').read_text() == 'keep'


def test_empty_project_finishes_with_explicit_empty_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(service, '_connection', lambda command: ('https://service.invalid/api/v1/account/cloud', TOKEN))
    monkeypatch.setattr(service, '_request', lambda *a, **k: (200, {'contract_version': 'project-download-v1',
        'project': {'id': PROJECT, 'owner_id': OWNER, 'title': 'Empty', 'state': 'active', 'current_revision': None},
        'items': [], 'next_cursor': None}))
    target = tmp_path / 'empty'
    result = invoke(target)
    assert result.exit_code == 0 and json.loads(result.output)['asset_count'] == 0
    assert json.loads((target / 'cloud-project.json').read_text())['items'] == []


def test_duplicate_references_and_cursor_loops_are_rejected_before_writes(monkeypatch, tmp_path):
    from moviestar import cloud_project_download as download
    monkeypatch.setattr(service, '_connection', lambda command: ('https://service.invalid/api/v1/account/cloud', TOKEN))
    project = {'id': PROJECT, 'owner_id': OWNER, 'title': 'Test', 'state': 'active', 'current_revision': None}
    entry = {'reference_id': REFS[0], 'asset_id': OWNER, 'filename': 'a.png', 'kind': 'image', 'content_type': 'image/png', 'size_bytes': 3,
             'download_path': f'/api/v1/account/cloud/projects/{PROJECT}/asset-references/{REFS[0]}/download'}
    for entries, cursor in [([entry, entry], None), ([entry], 'loop')]:
        monkeypatch.setattr(service, '_request', lambda *a, **k: (200, {'contract_version': download.CONTRACT, 'project': project, 'items': entries, 'next_cursor': cursor}))
        target = tmp_path / ('duplicate' if cursor is None else 'loop')
        result = invoke(target)
        assert result.exit_code == 1 and json.loads(result.output)['status'] == 'invalid_response'
        assert not target.exists()
