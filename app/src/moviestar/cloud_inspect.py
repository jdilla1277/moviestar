"""Durable dense Cloud video inspection, without a local media dependency."""
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

COMMAND = 'cloud inspect'

def _fail(code, hint, **fields):
    service._fail(COMMAND, code, hint, **fields)

def _milliseconds(value):
    seconds = parse_timecode(value)
    if not math.isfinite(seconds) or not 0 <= seconds <= 86400:
        raise ValueError('Use a finite source time between zero and 86400 seconds.')
    ms = round(seconds * 1000)
    if abs(seconds * 1000 - ms) > 0.000001:
        raise ValueError('Use millisecond precision.')
    return ms

def _check(status, body, **fields):
    if status not in (200, 202):
        error = body.get('error', {})
        code = error.get('code') if isinstance(error, dict) else None
        allowed = {'credential_invalid', 'account_setup_required', 'account_consent_required', 'not_found', 'invalid_request', 'idempotency_conflict', 'queue_busy', 'asset_not_ready', 'range_out_of_bounds', 'unsupported_asset', 'inspect_limit', 'inspect_unavailable'}
        _fail(code if code in allowed else 'inspect_unavailable',
              'Retain the asset, range, options, and request key. Retry after service recovery; reconnect if authorization expired.', **fields)

def _job(body, asset, job_id=None, options=None):
    job = body.get('job')
    if (not isinstance(job, dict) or not isinstance(job.get('id'), str)
            or not service._UUID.fullmatch(job['id']) or job.get('asset_id') != asset
            or job.get('state') not in ('queued', 'running', 'succeeded', 'failed', 'cancelled')
            or (job_id is not None and job['id'] != job_id)
            or (options is not None and job.get('options') != options)):
        _fail('invalid_response', 'Preserve the original request key and job IDs; the inspection outcome is uncertain.')
    return job

def _frames(job):
    """Validate the entire manifest before creating files or making image reads."""
    try:
        result, options = job['result'], job['options']
        frames = result['thumbnails']
        start, end, interval, width = (options[k] for k in ('from_ms', 'to_ms', 'interval_ms', 'width'))
        if not all(type(v) is int for v in (start, end, interval, width)):
            raise ValueError()
        if not (0 <= start < end <= 86400000 and end-start <= 120000 and 1 <= interval <= end-start
                and math.ceil((end-start)/interval) <= 240 and 16 <= width <= 640):
            raise ValueError()
        if (not isinstance(frames, list) or len(frames) > 240
                or result['mode'] != 'visual_inspect' or result['range'] != {'from_ms': start, 'to_ms': end}
                or result['sampling']['returned_frames'] != len(frames)
                or result['sampling']['interval_ms'] != interval or result['sampling']['scale_width'] != width):
            raise ValueError()
        total = 0
        for index, frame in enumerate(frames):
            if (frame['index'] != index or frame['at_ms'] != start + index*interval or frame['at_ms'] >= end
                    or frame['width'] != width or frame['mime_type'] != 'image/jpeg'
                    or type(frame['file_size_bytes']) is not int or not 4 <= frame['file_size_bytes'] <= 262144):
                raise ValueError()
            total += frame['file_size_bytes']
        if total > 16777216:
            raise ValueError()
        return frames
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        _fail('invalid_response', 'Inspection image metadata is invalid. Reopen these same IDs after service recovery.')

def register_inspect_commands(cloud):
    @cloud.command('inspect')
    @click.argument('asset_id')
    @click.option('--from', 'from_tc', required=True, help='Original-source start: seconds, M:SS, or H:MM:SS.mmm.')
    @click.option('--to', 'to_tc', required=True, help='Exclusive original-source end; at most 120 seconds after start.')
    @click.option('--interval', default='0.5', show_default=True, help='Seconds between frames; at most 240 frames per inspection.')
    @click.option('--width', default=640, type=click.IntRange(16, 640), show_default=True, help='Thumbnail width in pixels.')
    @click.option('--idempotency-key', help='Retain this key for retries; generated and saved when omitted.')
    def inspect(asset_id, from_tc, to_tc, interval, width, idempotency_key):
        """Request dense frames for a short section of a ready Cloud video.

        Returns a durable job immediately. Poll/reopen with inspect-show; add
        --out-dir there to save the private JPEGs. No local video, FFmpeg,
        transcription, or editing workspace is needed. Local inspect stays local.
        """
        try:
            start, end, step = map(_milliseconds, (from_tc, to_tc, interval))
            if not (0 <= start < end <= 86400000 and end-start <= 120000 and 1 <= step <= end-start and math.ceil((end-start)/step) <= 240):
                raise ValueError('Use a positive interval, a section of at most 120 seconds, and at most 240 frames. Increase --interval for fewer frames.')
        except ValueError as error:
            _fail('invalid_request', str(error))
        if not service._UUID.fullmatch(asset_id) or (idempotency_key is not None and not service._KEY.fullmatch(idempotency_key)):
            _fail('invalid_request', 'Use an asset UUID and valid retry key.')
        asset_id = asset_id.lower()
        endpoint, credential = service._connection(COMMAND)
        url = endpoint.removesuffix('/account/cloud') + f'/assets/{asset_id}/inspect'
        options = {'from_ms': start, 'to_ms': end, 'interval_ms': step, 'width': width}
        try:
            with creation_request(url, credential, json.dumps(options, sort_keys=True), idempotency_key) as pending:
                pending.save()
                status, body = service._request(url, credential, payload=options, key=pending.key)
                _check(status, body, idempotency_key=pending.key)
                job = _job(body, asset_id, options=options)
                if body.get('idempotency_key') != pending.key:
                    _fail('invalid_response', 'Retry the identical request to recover its job.', idempotency_key=pending.key)
                click.echo(json.dumps({'command': COMMAND, 'job': job, 'idempotency_key': pending.key,
                                       'hint': f"Reopen without new work: moviestar cloud inspect-show {asset_id} {job['id']}"}, indent=2))
        except (CreationBusy, CreationStateError):
            _fail('request_state_unavailable', 'Wait for the active request, retaining its retry key.')

    @cloud.command('inspect-show')
    @click.argument('asset_id')
    @click.argument('job_id')
    @click.option('--out-dir', type=click.Path(path_type=Path), help='Save private timestamped JPEGs; existing different files are never overwritten.')
    def show(asset_id, job_id, out_dir):
        """Read a retained job and optionally save all its completed frames.

        Repeat these same IDs after reconnecting. An interrupted download can
        resume in the same folder; completed identical files are preserved.
        """
        if not all(service._UUID.fullmatch(i) for i in (asset_id, job_id)):
            _fail('invalid_request', 'Use the original asset and job UUIDs.')
        asset_id, job_id = asset_id.lower(), job_id.lower()
        endpoint, credential = service._connection(COMMAND)
        url = endpoint.removesuffix('/account/cloud') + f'/assets/{asset_id}/inspections/{job_id}'
        status, body = service._request(url, credential)
        _check(status, body)
        job = _job(body, asset_id, job_id)
        result = {'command': 'cloud inspect-show', 'job': job}
        if out_dir is not None:
            if job['state'] != 'succeeded':
                _fail('result_not_ready', 'Poll the same IDs until the inspection succeeds.', job=job)
            frames = _frames(job)
            saved = []
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                for frame in frames:
                    request = Request(url + f"/images/{frame['index']}", headers={'Authorization': 'Bearer '+credential, 'x-moviestar-client': _CLIENT_MARKER, 'Accept': 'image/jpeg'})
                    with build_opener(_NoRedirectHandler()).open(request, timeout=30) as response:
                        if response.status != 200 or response.headers.get_content_type() != 'image/jpeg':
                            raise ValueError('Invalid image response')
                        image = response.read(262145)
                    if len(image) != frame['file_size_bytes'] or not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
                        raise ValueError('Invalid image bytes')
                    path = out_dir / f"inspect_{frame['index']:04d}_{frame['at_ms']}ms.jpg"
                    if path.exists() or path.is_symlink():
                        if path.is_symlink() or path.stat().st_size != len(image) or path.read_bytes() != image:
                            raise ValueError('Existing output differs')
                    else:
                        with path.open('xb') as output:
                            output.write(image)
                    saved.append({'index': frame['index'], 'at_ms': frame['at_ms'], 'path': str(path.resolve())})
            except (HTTPError, URLError, HTTPException, OSError, ValueError):
                _fail('images_unavailable', 'Retained server result and completed local files are safe. Retry these IDs; use a new folder if existing files differ.', job=job, saved=saved)
            result['saved'] = saved
        click.echo(json.dumps(result, indent=2))
