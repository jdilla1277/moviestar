"""Durable Cloud asset screenshots; local screenshot stays account-free."""
import json
import math
import os
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from http.client import HTTPException

import click
from moviestar import cloud as service
from moviestar.account import _CLIENT_MARKER, _NoRedirectHandler
from moviestar.cloud_creation_state import creation_request, CreationBusy, CreationStateError
from moviestar.timecodes import parse_timecode


def _job(body, asset, job_id=None, at_ms=None):
    job = body.get('job')
    if (not isinstance(job, dict) or not isinstance(job.get('id'), str)
            or not service._UUID.fullmatch(job['id']) or job.get('asset_id') != asset
            or job.get('state') not in ('queued', 'running', 'succeeded', 'failed', 'cancelled')
            or type(job.get('at_ms')) is not int or not 0 <= job['at_ms'] <= 86400000
            or (job_id is not None and job['id'] != job_id)
            or (at_ms is not None and job['at_ms'] != at_ms)):
        service._fail('cloud screenshot', 'invalid_response', 'Preserve the original request key and retry; the screenshot outcome is uncertain.')
    return job


def _check(status, body, **fields):
    if status not in (200, 202):
        error = body.get('error', {})
        code = error.get('code', 'screenshot_unavailable') if isinstance(error, dict) else 'screenshot_unavailable'
        allowed = {'credential_invalid', 'not_found', 'invalid_request', 'idempotency_conflict', 'queue_busy', 'asset_not_ready', 'timestamp_out_of_range', 'unsupported_asset', 'screenshot_limit', 'screenshot_unavailable'}
        service._fail('cloud screenshot', code if code in allowed else 'screenshot_unavailable',
                      'Retry with the same asset, timestamp and request key. Reconnect if credentials expired.', **fields)


def register_screenshot_commands(cloud):
    @cloud.command('screenshot')
    @click.argument('asset_id')
    @click.option('--at', required=True, help='Seconds, M:SS, or HH:MM:SS.mmm; millisecond precision.')
    @click.option('--idempotency-key', help='Retain this key on retries; generated and saved if omitted.')
    def screenshot(asset_id, at, idempotency_key):
        """Request a frame of a ready Cloud video; returns a durable job ID.

        Retry the identical command after an uncertain reply. Use screenshot-show
        to poll/reopen the job and save its private JPEG after reconnecting.
        """
        command = 'cloud screenshot'
        try:
            seconds = parse_timecode(at)
            if not math.isfinite(seconds) or seconds < 0 or seconds > 86400:
                raise ValueError('Timestamp must be between 0 and 86400 seconds.')
            at_ms = round(seconds * 1000)
            if abs(seconds*1000-at_ms) > 0.000001:
                raise ValueError('Use millisecond precision.')
        except ValueError as error:
            service._fail(command, 'invalid_request', str(error))
        if not service._UUID.fullmatch(asset_id) or (idempotency_key is not None and not service._KEY.fullmatch(idempotency_key)):
            service._fail(command, 'invalid_request', 'Use an asset UUID and a valid retry key.')
        asset_id = asset_id.lower()
        endpoint, credential = service._connection(command)
        url = endpoint.removesuffix('/account/cloud') + f'/assets/{asset_id}/screenshot'
        try:
            with creation_request(url, credential, str(at_ms), idempotency_key) as pending:
                pending.save()
                status, body = service._request(url, credential, payload={'at_ms': at_ms}, key=pending.key)
                _check(status, body, idempotency_key=pending.key)
                job = _job(body, asset_id, at_ms=at_ms)
                if body.get('idempotency_key') != pending.key:
                    service._fail(command, 'invalid_response', 'Retry the same command to recover the screenshot.', idempotency_key=pending.key)
                # Keep the saved key even after success: repeated inspection reopens
                # this execution. A new key explicitly requests a fresh execution.
                click.echo(json.dumps({'command': command, 'job': job, 'idempotency_key': pending.key}, indent=2))
        except (CreationBusy, CreationStateError):
            service._fail(command, 'request_state_unavailable', 'Wait for an active request or repair local retry state while retaining its key.')

    @cloud.command('screenshot-show')
    @click.argument('asset_id')
    @click.argument('job_id')
    @click.option('--out', type=click.Path(path_type=Path), help='Save the completed private JPEG; never overwrites an existing file.')
    def show(asset_id, job_id, out):
        """Reopen status/image using durable IDs without creating work."""
        command = 'cloud screenshot-show'
        if not all(service._UUID.fullmatch(i) for i in (asset_id, job_id)):
            service._fail(command, 'invalid_request', 'Use the original asset and job UUIDs.')
        asset_id, job_id = asset_id.lower(), job_id.lower()
        endpoint, credential = service._connection(command)
        url = endpoint.removesuffix('/account/cloud') + f'/assets/{asset_id}/screenshots/{job_id}'
        status, body = service._request(url, credential)
        _check(status, body)
        job = _job(body, asset_id, job_id)
        result = {'command': command, 'job': job}
        if out is not None:
            if job['state'] != 'succeeded':
                service._fail(command, 'result_not_ready', 'Poll these same IDs until the screenshot succeeds.', job=job)
            req = Request(url+'/image', headers={'Authorization': 'Bearer '+credential, 'x-moviestar-client': _CLIENT_MARKER, 'Accept': 'image/jpeg'})
            try:
                with build_opener(_NoRedirectHandler()).open(req, timeout=30) as response:
                    if response.status != 200 or response.headers.get_content_type() != 'image/jpeg':
                        raise ValueError('Invalid image response')
                    image = response.read(1048577)
                if len(image)>1048576 or not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
                    raise ValueError('Invalid image')
                with out.open('xb') as output:
                    output.write(image)
                result['out'] = str(out.resolve())
            except (HTTPError, URLError, HTTPException, OSError, ValueError):
                service._fail(command, 'image_unavailable', 'Choose a new output filename and retry these same IDs; the server result is retained.')
        click.echo(json.dumps(result, indent=2))
