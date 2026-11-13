"""Authenticated project originals downloaded to a new, non-overwriting folder."""
from __future__ import annotations

import hashlib
import json
import os
import re
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import click

from moviestar import cloud as service
from moviestar.account import _CLIENT_MARKER, _NoRedirectHandler

CONTRACT = 'project-download-v1'
COMMAND = 'cloud projects download'


def _page(body, project_id):
    project = service._project(body.get('project'))
    items, cursor = body.get('items'), body.get('next_cursor', False)
    if (body.get('contract_version') != CONTRACT or project is None
            or project['id'].lower() != project_id or not isinstance(items, list) or len(items) > 100
            or not (cursor is None or isinstance(cursor, str) and service._CURSOR.fullmatch(cursor))):
        return None
    parsed = []
    for item in items:
        if not isinstance(item, dict):
            return None
        ref, asset_id = item.get('reference_id'), item.get('asset_id')
        if (not isinstance(ref, str) or not service._UUID.fullmatch(ref)
                or not isinstance(asset_id, str) or not service._UUID.fullmatch(asset_id)
                or item.get('kind') not in ('video', 'audio', 'image', 'transcript')
                or not isinstance(item.get('filename'), str) or not 1 <= len(item['filename']) <= 240
                or not isinstance(item.get('content_type'), str) or not item['content_type']
                or type(item.get('size_bytes')) is not int or not 1 <= item['size_bytes'] <= 2**53 - 1):
            return None
        path = f'/api/v1/account/cloud/projects/{project_id}/asset-references/{ref.lower()}/download'
        if item.get('download_path') != path:
            return None
        parsed.append({name: item[name] for name in ('reference_id', 'asset_id', 'filename', 'kind', 'content_type', 'size_bytes', 'download_path')})
    return project, parsed, cursor


def _manifest(endpoint, credential, project_id):
    items, cursors, refs, project = [], set(), set(), None
    cursor = None
    for _ in range(1000):
        url = f'{endpoint}/projects/{project_id}/download-manifest?limit=100'
        if cursor:
            url += '&cursor=' + cursor
        status, body = service._request(url, credential)
        if status != 200:
            service._failure(COMMAND, status, body, project_id=project_id)
        parsed = _page(body, project_id)
        if parsed is None:
            break
        page_project, page_items, next_cursor = parsed
        if project is not None and page_project['owner_id'] != project['owner_id']:
            break
        project = project or page_project
        if (len({item['reference_id'].lower() for item in page_items}) != len(page_items)
                or any(item['reference_id'].lower() in refs for item in page_items)):
            break
        for item in page_items:
            refs.add(item['reference_id'].lower())
            items.append(item)
        if next_cursor is None:
            return project, items
        if not page_items or next_cursor in cursors:
            break
        cursors.add(next_cursor)
        cursor = next_cursor
    service._fail(COMMAND, 'invalid_response', 'The service returned an invalid or incomplete download manifest. No local download was started.', project_id=project_id)


def _filename(name):
    # Never use a remote path, header filename, device name or hidden basename.
    basename = re.split(r'[/\\]', name)[-1]
    basename = re.sub(r'[^A-Za-z0-9._-]', '_', basename).strip(' .')[:120]
    if not basename or basename in ('.', '..') or basename.startswith('.'):
        basename = 'asset'
    if basename.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        basename = 'asset_' + basename
    return basename


def _transfer(origin, credential, item, destination):
    request = Request(origin + item['download_path'], headers={
        'Authorization': 'Bearer ' + credential, 'x-moviestar-client': _CLIENT_MARKER,
        'Accept': item['content_type'], 'Accept-Encoding': 'identity',
    })
    part = destination.with_name(destination.name + '.part')
    digest, size = hashlib.sha256(), 0
    with build_opener(_NoRedirectHandler()).open(request, timeout=60) as response:
        if (response.status != 200 or response.headers.get_content_type() != item['content_type'].split(';')[0].strip().lower()
                or response.headers.get('Content-Encoding', 'identity').lower() != 'identity'):
            raise ValueError('invalid_download')
        length = response.headers.get('Content-Length')
        if length is not None and (not length.isdecimal() or int(length) != item['size_bytes']):
            raise ValueError('invalid_download')
        with part.open('xb') as output:
            while chunk := response.read(65536):
                size += len(chunk)
                if size > item['size_bytes']:
                    raise ValueError('invalid_download')
                output.write(chunk)
                digest.update(chunk)
            if size != item['size_bytes']:
                raise ValueError('invalid_download')
            output.flush()
            os.fsync(output.fileno())
    # A link publishes without overwriting a destination that appeared meanwhile.
    os.link(part, destination)
    part.unlink()
    return digest.hexdigest()


def register_project_download(projects):
    @projects.command('download')
    @click.argument('project_id')
    @click.option('--out', required=True, type=click.Path(path_type=Path), help='New destination folder. Existing paths are never overwritten.')
    def download_project(project_id, out):
        """Download ready project originals and a manifest without a browser.

        Supports owners and accepted asset viewers. Requires the service's
        project-download-v1 manifest. No local loading, editing or media tools
        run; use the downloaded originals with local editing commands afterward.
        """
        if not service._UUID.fullmatch(project_id):
            service._fail(COMMAND, 'invalid_request', 'Use a project UUID.')
        project_id = project_id.lower()
        destination = out.expanduser().absolute()
        if os.path.lexists(destination):
            service._fail(COMMAND, 'destination_exists', 'Choose a new --out folder; existing files and symlinks are never overwritten.', project_id=project_id)
        if not destination.parent.is_dir():
            service._fail(COMMAND, 'invalid_destination', 'Create the parent directory first, then choose a new --out folder.', project_id=project_id)
        endpoint, credential = service._connection(COMMAND)
        project, items = _manifest(endpoint, credential, project_id)
        completed = []
        try:
            destination.mkdir(mode=0o700)
        except OSError:
            service._fail(COMMAND, 'invalid_destination', 'Could not create a new destination folder. Existing work was preserved.', project_id=project_id)
        try:
            incomplete = destination / '.incomplete.json'
            with incomplete.open('x') as output:
                json.dump({'contract_version': CONTRACT, 'status': 'incomplete', 'project_id': project_id}, output)
            for item in items:
                relative = Path('assets') / item['reference_id'].lower() / _filename(item['filename'])
                path = destination / relative
                path.parent.mkdir(parents=True, exist_ok=False)
                digest = _transfer(endpoint.removesuffix('/api/v1/account/cloud'), credential, item, path)
                completed.append({**{name: value for name, value in item.items() if name != 'download_path'}, 'local_path': relative.as_posix(), 'sha256': digest})
            with (destination / 'cloud-project.json').open('x') as output:
                json.dump({'contract_version': CONTRACT, 'status': 'complete', 'project': project, 'items': completed,
                           'contents': 'ready_originals_only', 'cloud_edits_included': False}, output, indent=2)
                output.write('\n')
            incomplete.unlink()
        except (HTTPError, URLError, HTTPException, OSError, ValueError):
            service._fail(COMMAND, 'download_incomplete', 'Download stopped. Completed files and .part files remain in this incomplete folder; no overwrite or automatic retry occurred. Check account/project access, then retry into a new --out folder.',
                          project_id=project_id, directory=str(destination), completed_assets=len(completed))
        click.echo(json.dumps({'command': COMMAND, 'status': 'downloaded', 'project_id': project_id,
            'directory': str(destination), 'manifest': str(destination / 'cloud-project.json'), 'asset_count': len(completed),
            'hint': 'Ready originals are saved locally. Use them with local editing commands; Cloud edits, renders and local workspace initialization are not included.'}, indent=2))
