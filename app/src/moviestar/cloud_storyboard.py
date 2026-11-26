"""Durable Cloud asset storyboards; local storyboard stays account-free."""
import json
import math
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from http.client import HTTPException

import click
from moviestar import cloud as service
from moviestar.account import _CLIENT_MARKER, _NoRedirectHandler
from moviestar.cloud_creation_state import creation_request, CreationBusy, CreationStateError
from moviestar.timecodes import parse_timecode


def _job(body, asset, job_id=None, options=None):
    job = body.get('job')
    if (not isinstance(job, dict) or not isinstance(job.get('id'), str)
            or not service._UUID.fullmatch(job['id']) or job.get('asset_id') != asset
            or job.get('state') not in ('queued', 'running', 'succeeded', 'failed', 'cancelled')
            or not isinstance(job.get('options'), dict) or not isinstance(job.get('sampling'), dict)
            or (job_id is not None and job['id'] != job_id)
            or (options is not None and job['options'] != options)):
        service._fail('cloud storyboard', 'invalid_response', 'Preserve the original request key and retry; the storyboard outcome is uncertain.')
    return job


def _check(status, body, **fields):
    if status not in (200, 202):
        error = body.get('error', {})
        code = error.get('code', 'storyboard_unavailable') if isinstance(error, dict) else 'storyboard_unavailable'
        allowed = {'credential_invalid', 'not_found', 'invalid_request', 'idempotency_conflict', 'queue_busy', 'asset_not_ready', 'timestamp_out_of_range', 'unsupported_asset', 'storyboard_limit', 'storyboard_unavailable'}
        service._fail('cloud storyboard', code if code in allowed else 'storyboard_unavailable',
                      'Retry with the same asset, range, interval, columns and request key. Reconnect if credentials expired.', **fields)


def register_storyboard_commands(cloud):
    @cloud.command('storyboard')
    @click.argument('asset_id')
    @click.option('--from', 'from_', default='0', help='Range start; seconds, M:SS, or HH:MM:SS.mmm.')
    @click.option('--to', help='Exclusive range end; omitted means the video end.')
    @click.option('--interval', help='Sample spacing of at least 10 milliseconds; omitted uses automatic sampling.')
    @click.option('--columns', type=int, help='Grid columns, from 1 to 12.')
    @click.option('--idempotency-key', help='Retain this key on retries; generated and saved if omitted.')
    def storyboard(asset_id, from_, to, interval, columns, idempotency_key):
        """Request a labeled contact sheet of a ready Cloud video.

        At most 60 frames from a half-open range of at least 100 milliseconds.
        Retry the identical command after an uncertain reply. Use storyboard-show
        to poll/reopen and download the private JPEG after reconnecting.
        """
        command = 'cloud storyboard'
        options = {}
        try:
            for field, value in [('from_ms', from_), ('to_ms', to), ('interval_ms', interval)]:
                if value is None:
                    continue
                seconds = parse_timecode(value)
                if not math.isfinite(seconds) or seconds < 0 or seconds > 86400:
                    raise ValueError('Times must be between 0 and 86400 seconds.')
                milliseconds = round(seconds * 1000)
                if abs(seconds*1000-milliseconds) > 0.000001:
                    raise ValueError('Use millisecond precision.')
                options[field] = milliseconds
            if options.get('interval_ms', 10) < 10:
                raise ValueError('The interval must be at least 10 milliseconds.')
            if to is not None and options['to_ms']-options['from_ms'] < 100:
                raise ValueError('The sampled range must be at least 100 milliseconds.')
            if to is not None and options.get('interval_ms', 0) >= options['to_ms']-options['from_ms']:
                raise ValueError('The interval must be smaller than the range.')
            if columns is not None:
                if not 1 <= columns <= 12:
                    raise ValueError('Columns must be between 1 and 12.')
                options['columns'] = columns
        except ValueError as error:
            service._fail(command, 'invalid_request', str(error))
        if not service._UUID.fullmatch(asset_id) or (idempotency_key is not None and not service._KEY.fullmatch(idempotency_key)):
            service._fail(command, 'invalid_request', 'Use an asset UUID and a valid retry key.')
        asset_id = asset_id.lower()
        endpoint, credential = service._connection(command)
        url = endpoint.removesuffix('/account/cloud') + f'/assets/{asset_id}/storyboard'
        try:
            with creation_request(url, credential, json.dumps(options, sort_keys=True, separators=(',', ':')), idempotency_key) as pending:
                pending.save()
                status, body = service._request(url, credential, payload=options, key=pending.key)
                _check(status, body, idempotency_key=pending.key)
                job = _job(body, asset_id, options=options)
                if body.get('idempotency_key') != pending.key:
                    service._fail(command, 'invalid_response', 'Retry the same command to recover the storyboard.', idempotency_key=pending.key)
                # Keep the saved key even after success: repeated inspection reopens
                # this execution. A new key explicitly requests a fresh execution.
                click.echo(json.dumps({'command': command, 'job': job, 'idempotency_key': pending.key}, indent=2))
        except (CreationBusy, CreationStateError):
            service._fail(command, 'request_state_unavailable', 'Wait for an active request or repair local retry state while retaining its key.')

    @cloud.command('storyboard-show')
    @click.argument('asset_id')
    @click.argument('job_id')
    @click.option('--out', type=click.Path(path_type=Path), help='Save the completed private JPEG; never overwrites an existing file.')
    def show(asset_id, job_id, out):
        """Reopen status/image using durable IDs without creating work."""
        command = 'cloud storyboard-show'
        if not all(service._UUID.fullmatch(i) for i in (asset_id, job_id)):
            service._fail(command, 'invalid_request', 'Use the original asset and job UUIDs.')
        asset_id, job_id = asset_id.lower(), job_id.lower()
        endpoint, credential = service._connection(command)
        url = endpoint.removesuffix('/account/cloud') + f'/assets/{asset_id}/storyboards/{job_id}'
        status, body = service._request(url, credential)
        _check(status, body)
        job = _job(body, asset_id, job_id)
        result = {'command': command, 'job': job}
        if out is not None:
            if job['state'] != 'succeeded':
                service._fail(command, 'result_not_ready', 'Poll these same IDs until the storyboard succeeds.', job=job)
            req = Request(url+'/image', headers={'Authorization': 'Bearer '+credential, 'x-moviestar-client': _CLIENT_MARKER, 'Accept': 'image/jpeg'})
            try:
                with build_opener(_NoRedirectHandler()).open(req, timeout=30) as response:
                    if response.status != 200 or response.headers.get_content_type() != 'image/jpeg':
                        raise ValueError('Invalid image response')
                    image = response.read(2097153)
                if len(image)>2097152 or not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
                    raise ValueError('Invalid image')
                with out.open('xb') as output:
                    output.write(image)
                result['out'] = str(out.resolve())
            except (HTTPError, URLError, HTTPException, OSError, ValueError):
                service._fail(command, 'image_unavailable', 'Choose a new output filename and retry these same IDs; the server result is retained.')
        click.echo(json.dumps(result, indent=2))
