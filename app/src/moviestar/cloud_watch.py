"""Durable private Cloud watch excerpts; local watch stays account-free."""
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


def _job(body, asset, job_id=None, from_ms=None, to_ms=None):
    job = body.get('job')
    if (not isinstance(job, dict) or not isinstance(job.get('id'), str)
            or not service._UUID.fullmatch(job['id']) or job.get('asset_id') != asset
            or job.get('state') not in ('queued', 'running', 'succeeded', 'failed', 'cancelled')
            or type(job.get('from_ms')) is not int or type(job.get('to_ms')) is not int
            or not 0 <= job['from_ms'] < job['to_ms'] <= 86400000 or job['to_ms']-job['from_ms']>30000
            or (job_id is not None and job['id'] != job_id)
            or (from_ms is not None and (job['from_ms'], job['to_ms']) != (from_ms, to_ms))):
        service._fail('cloud watch', 'invalid_response', 'Preserve the original request key and retry; the watch outcome is uncertain.')
    return job


def _check(status, body, **fields):
    if status not in (200, 202):
        error = body.get('error', {})
        code = error.get('code', 'watch_unavailable') if isinstance(error, dict) else 'watch_unavailable'
        allowed = {'credential_invalid', 'not_found', 'invalid_request', 'idempotency_conflict', 'queue_busy', 'asset_not_ready', 'timestamp_out_of_range', 'unsupported_asset', 'watch_limit', 'watch_unavailable', 'artifact_unavailable', 'account_consent_required', 'account_setup_required'}
        service._fail('cloud watch', code if code in allowed else 'watch_unavailable',
                      'Retry with the same asset, range and request key. Reconnect if credentials expired.', **fields)


def register_watch_commands(cloud):
    @cloud.command('watch')
    @click.argument('asset_id')
    @click.option('--from', 'from_tc', required=True, help='Source time: seconds, M:SS, or HH:MM:SS.mmm.')
    @click.option('--to', 'to_tc', required=True, help='Exclusive end; at most 30 seconds after --from.')
    @click.option('--idempotency-key', help='Retain this key on retries; generated and saved if omitted.')
    def watch(asset_id, from_tc, to_tc, idempotency_key):
        """Request a bounded excerpt of a ready Cloud video, including available audio; returns a durable job ID.

        Retry the identical command after an uncertain reply. Use watch-show
        to poll/reopen the job and save its private MP4 after reconnecting.
        """
        command = 'cloud watch'
        try:
            times = [parse_timecode(t) for t in (from_tc, to_tc)]
            if not all(math.isfinite(t) and 0 <= t <= 86400 for t in times):
                raise ValueError('Use finite timestamps between 0 and 86400 seconds.')
            from_ms, to_ms = [round(t*1000) for t in times]
            if any(abs(t*1000-ms)>0.000001 for t, ms in zip(times, (from_ms, to_ms))):
                raise ValueError('Use millisecond precision.')
            if not 0 < to_ms-from_ms <= 30000:
                raise ValueError('Use an increasing range of at most 30 seconds.')
        except ValueError as error:
            service._fail(command, 'invalid_request', str(error))
        if not service._UUID.fullmatch(asset_id) or (idempotency_key is not None and not service._KEY.fullmatch(idempotency_key)):
            service._fail(command, 'invalid_request', 'Use an asset UUID and a valid retry key.')
        asset_id = asset_id.lower()
        endpoint, credential = service._connection(command)
        url = endpoint.removesuffix('/account/cloud') + f'/assets/{asset_id}/watch'
        try:
            with creation_request(url, credential, f"{from_ms}:{to_ms}", idempotency_key) as pending:
                pending.save()
                status, body = service._request(url, credential, payload={'from_ms': from_ms, 'to_ms': to_ms}, key=pending.key)
                _check(status, body, idempotency_key=pending.key)
                job = _job(body, asset_id, from_ms=from_ms,to_ms=to_ms)
                if body.get('idempotency_key') != pending.key:
                    service._fail(command, 'invalid_response', 'Retry the same command to recover the watch.', idempotency_key=pending.key)
                # Keep the saved key even after success: repeated inspection reopens
                # this execution. A new key explicitly requests a fresh execution.
                click.echo(json.dumps({'command': command, 'job': job, 'idempotency_key': pending.key}, indent=2))
        except (CreationBusy, CreationStateError):
            service._fail(command, 'request_state_unavailable', 'Wait for an active request or repair local retry state while retaining its key.')

    @cloud.command('watch-show')
    @click.argument('asset_id')
    @click.argument('job_id')
    @click.option('--out', type=click.Path(path_type=Path), help='Save the completed private MP4; never overwrites an existing file.')
    def show(asset_id, job_id, out):
        """Reopen status/media using durable IDs without creating work."""
        command = 'cloud watch-show'
        if not all(service._UUID.fullmatch(i) for i in (asset_id, job_id)):
            service._fail(command, 'invalid_request', 'Use the original asset and job UUIDs.')
        asset_id, job_id = asset_id.lower(), job_id.lower()
        endpoint, credential = service._connection(command)
        url = endpoint.removesuffix('/account/cloud') + f'/assets/{asset_id}/watches/{job_id}'
        status, body = service._request(url, credential)
        _check(status, body)
        job = _job(body, asset_id, job_id)
        result = {'command': command, 'job': job}
        if out is not None:
            if job['state'] != 'succeeded':
                service._fail(command, 'result_not_ready', 'Poll these same IDs until the watch succeeds.', job=job)
            if job.get('artifact_available') is False:
                service._fail(command, 'artifact_unavailable', 'The saved result remains, but its private media is unavailable. Reopening never extracts again; use a new request key only to request new work.', job=job)
            req = Request(url+'/media', headers={'Authorization': 'Bearer '+credential, 'x-moviestar-client': _CLIENT_MARKER, 'Accept': 'video/mp4'})
            try:
                with build_opener(_NoRedirectHandler()).open(req, timeout=30) as response:
                    if response.status != 200 or response.headers.get_content_type() != 'video/mp4':
                        raise ValueError('Invalid MP4 response')
                    media = response.read(2097153)
                if len(media)>2097152 or len(media)<12 or media[4:8]!=b'ftyp':
                    raise ValueError('Invalid MP4')
                with out.open('xb') as output:
                    output.write(media)
                result['out'] = str(out.resolve())
            except HTTPError as error:
                service._fail(command, 'artifact_unavailable' if error.code == 410 else 'media_unavailable', 'Reopen these same IDs with current credentials; no new extraction is scheduled. Expired retained media needs an explicit new request key.')
            except (URLError, HTTPException, OSError, ValueError):
                service._fail(command, 'media_unavailable', 'Choose a new output filename and retry these same IDs; the server result is retained.')
        click.echo(json.dumps(result, indent=2))
