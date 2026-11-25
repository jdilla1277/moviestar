"""Save one authenticated original without overwriting a local file."""
from __future__ import annotations

import json
import os
import tempfile
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError

import click

from moviestar import cloud as service
from moviestar.cloud_assets import _asset, _connection, _fail, _ids
from moviestar.cloud_project_download import _transfer

COMMAND = 'cloud assets download'


def register_asset_download(assets):
    @assets.command('download')
    @click.argument('asset_id')
    @click.option('--out', required=True, type=click.Path(path_type=Path), help='Destination file. Existing files, directories and symlinks are never overwritten.')
    def download_asset(asset_id, out):
        """Save an owned or accepted shared original to a local file.

        Downloads the original bytes, not a preview, edit or re-encode. Uses
        this installation's credential or explicit personal API-key mode.
        The service rechecks access on the download request. A private staging
        folder holds incomplete bytes; only a completed transfer is published.
        """
        _ids(COMMAND, asset_id)
        asset_id = asset_id.lower()
        destination = out.expanduser().absolute()
        if os.path.lexists(destination):
            service._fail(COMMAND, 'destination_exists', 'Choose a new --out file; existing files, directories and symlinks are never overwritten.', asset_id=asset_id)
        if not destination.parent.is_dir():
            service._fail(COMMAND, 'invalid_destination', 'Create the parent directory first, then choose a new --out file.', asset_id=asset_id)
        endpoint, credential = _connection(COMMAND, 'assets:read')
        status, body = service._request(f'{endpoint}/assets/{asset_id}', credential)
        if status != 200:
            _fail(COMMAND, status, body, asset_id=asset_id)
        asset = _asset(body.get('asset'))
        path = f'/api/v1/account/cloud/assets/{asset_id}/download'
        if (asset is None or asset['id'].lower() != asset_id or asset['size_bytes'] > 2**53 - 1
                or body.get('download_path') != path):
            service._fail(COMMAND, 'invalid_response', 'Could not confirm the original download metadata. No file was saved.', asset_id=asset_id)
        try:
            # Stage on the destination filesystem so publication is atomic.
            # The existing project transfer validates size/type, hashes bytes
            # and refuses redirects; the remote filename never chooses a path.
            with tempfile.TemporaryDirectory(prefix='.moviestar-download-', dir=destination.parent, ignore_cleanup_errors=True) as folder:
                staged = Path(folder) / 'original'
                digest = _transfer(endpoint.removesuffix('/api/v1/account/cloud'), credential,
                                   {**asset, 'download_path': path}, staged)
                os.link(staged, destination)
        except FileExistsError:
            service._fail(COMMAND, 'destination_exists', 'A destination appeared during transfer. It was preserved; choose a new --out file.', asset_id=asset_id)
        except HTTPError as error:
            # Only retain allowlisted service error codes, never response text
            # or provider URLs. Redirects remain rejected by the transfer helper.
            with error:
                try:
                    raw = error.read(4097)
                    failure = json.loads(raw) if len(raw) <= 4096 else {}
                except (HTTPException, OSError, ValueError):
                    failure = {}
            _fail(COMMAND, error.code, failure if isinstance(failure, dict) else {}, asset_id=asset_id)
        except (URLError, HTTPException, OSError, ValueError):
            service._fail(COMMAND, 'download_incomplete', 'The download could not be completed. No new destination file was published. Check access and storage, then retry the same command.', asset_id=asset_id)
        click.echo(json.dumps({'command': COMMAND, 'status': 'downloaded', 'asset_id': asset_id,
                              'file': str(destination), 'filename': asset['filename'], 'kind': asset['kind'],
                              'size_bytes': asset['size_bytes'], 'sha256': digest}, indent=2))
