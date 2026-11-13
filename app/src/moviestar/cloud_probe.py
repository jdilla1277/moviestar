"""Canonical asset probing over the approved installation's remote service."""
from __future__ import annotations

import json
import math
import re
import time
import click
from moviestar.cloud_creation_state import CreationBusy, CreationStateError, creation_request


def _media(raw):
    if not isinstance(raw, dict) or set(raw) != {'duration', 'file_size_bytes', 'format', 'bitrate', 'video', 'audio'}:
        return None
    def number(value, minimum=0):
        return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= 2**53 - 1
    duration = raw['duration']
    if (not isinstance(duration, dict) or set(duration) != {'seconds', 'text'}
            or not number(duration['seconds']) or not isinstance(duration['text'], str)
            or not re.fullmatch(r'\d+:\d{2}:\d{2}\.\d{3}', duration['text'])):
        return None
    if (type(raw['file_size_bytes']) is not int or not number(raw['file_size_bytes'], 1)
            or type(raw['bitrate']) is not int or not number(raw['bitrate'])
            or not isinstance(raw['format'], str) or not re.fullmatch(r'[A-Za-z0-9_,.-]{1,256}', raw['format'])):
        return None
    for field, integers, words in [('video', ('width', 'height'), ('codec', 'pixel_format')),
                                   ('audio', ('sample_rate', 'channels'), ('codec',))]:
        stream = raw[field]
        if stream is None:
            continue
        expected = set(integers + words) | ({'fps'} if field == 'video' else set())
        if not isinstance(stream, dict) or set(stream) != expected:
            return None
        if any(type(stream[k]) is not int or not number(stream[k], 1) for k in integers):
            return None
        if any(not isinstance(stream[k], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', stream[k]) for k in words):
            return None
        if field == 'video' and not number(stream['fps']):
            return None
    return raw


def _job(raw, asset, expected=None):
    from moviestar.cloud import _UUID
    if (not isinstance(raw, dict) or not isinstance(raw.get('id'), str) or not _UUID.fullmatch(raw['id'])
            or (expected is not None and raw['id'] != expected) or raw.get('asset_id') != asset
            or raw.get('state') not in ('queued', 'running', 'succeeded', 'failed', 'cancelled')):
        return None
    result = _media(raw.get('result')) if raw['state'] == 'succeeded' else None
    if raw['state'] == 'succeeded' and result is None:
        return None
    failure = raw.get('failure_code')
    if failure is not None and failure not in ('download_failed', 'input_changed', 'probe_failed', 'resource_limit',
                                              'access_ended', 'job_timeout', 'retry_exhausted'):
        return None
    return {'id': raw['id'], 'asset_id': asset, 'state': raw['state'], 'failure_code': failure, 'result': result}


def register_probe(cloud):
    @cloud.command('probe')
    @click.argument('asset_id')
    @click.option('--idempotency-key', help='Reuse this key after an uncertain reply. Saved automatically when omitted.')
    @click.option('--job-id', help='Read/reopen an existing job without scheduling any work.')
    @click.option('--wait/--no-wait', default=True, help='Wait for a result (default), or return the durable job immediately.')
    @click.option('--timeout', type=click.IntRange(0, 900), default=300, show_default=True, help='Maximum polling seconds; expiry does not cancel the job.')
    def probe(asset_id, idempotency_key, job_id, wait, timeout):
        """Inspect a ready Cloud ASSET_ID using Cloud compute.

        No local media or FFmpeg is needed. Retry an interrupted invocation to
        recover the same request; use --job-id to reopen across sessions without
        queuing a new job. A new invocation after a completed request starts a new
        probe. Local 'moviestar probe FILE' remains local and needs no account.
        """
        from moviestar.cloud import _UUID, _KEY, _connection, _request, _fail
        command = 'cloud probe'
        if (not _UUID.fullmatch(asset_id) or (job_id is not None and not _UUID.fullmatch(job_id))
                or (idempotency_key is not None and not _KEY.fullmatch(idempotency_key))
                or (job_id is not None and idempotency_key is not None)):
            _fail(command, 'invalid_request', 'Use an asset UUID and either --job-id or --idempotency-key.')
        asset_id = asset_id.lower()
        job_id = job_id.lower() if job_id else None
        endpoint, credential = _connection(command)
        origin = endpoint.removesuffix('/api/v1/account/cloud')
        base = f'{origin}/api/v1/assets/{asset_id}'
        common = {}
        def read(status, body, expected=None):
            if status not in (200, 202):
                code = body.get('error', {}).get('code') if isinstance(body.get('error'), dict) else None
                safe = code if code in ('credential_invalid', 'account_consent_required', 'account_setup_required',
                    'not_found', 'asset_not_ready', 'idempotency_conflict', 'queue_busy', 'unsupported_asset', 'probe_limit') else 'probe_unavailable'
                _fail(command, safe, 'Preserve the asset ID and request key. Retry the same request; check account access if denied.', **common)
            parsed = _job(body.get('job'), asset_id, expected)
            if parsed is None:
                _fail(command, 'invalid_response', 'Preserve the request key and retry to recover the same probe.', **common)
            return parsed
        def poll(current):
            deadline = time.monotonic() + timeout
            while wait and current['state'] in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
                current = read(*_request(f"{base}/probes/{current['id']}", credential), expected=current['id'])
            return current
        try:
            if job_id:
                current = poll(read(*_request(f'{base}/probes/{job_id}', credential), expected=job_id))
            else:
                with creation_request(base, credential, asset_id, idempotency_key, recovery_directory='cloud-probe-requests') as pending:
                    common['idempotency_key'] = pending.key
                    pending.save()
                    current = read(*_request(base + '/probe', credential, payload={}, key=pending.key))
                    common['job_id'] = current['id']
                    current = poll(current)
                    if current['state'] not in ('queued', 'running'):
                        pending.resolve()
        except KeyboardInterrupt:
            _fail(command, 'interrupted', 'The Cloud job may still be running. Retry the same asset/key, or reopen the returned job ID.', **common)
        except CreationBusy:
            _fail(command, 'probe_in_progress', 'Another probe invocation for this asset is active. Wait, then retry.', **common)
        except CreationStateError:
            _fail(command, 'recovery_state_unavailable', 'Could not preserve probe retry state. Keep the original key/job ID; inspect account profile permissions before retrying.', **common)
        click.echo(json.dumps({'command': command, 'status': current['state'], 'job': current, **common,
            'hint': f"Reopen without new work: moviestar cloud probe {asset_id} --job-id {current['id']}"}, indent=2))
        if current['state'] in ('failed', 'cancelled'):
            raise click.exceptions.Exit(1)
