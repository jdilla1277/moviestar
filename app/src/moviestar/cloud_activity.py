"""Canonical asset probing over the approved installation's remote service."""
from __future__ import annotations

import json
import math
import re
import time
import click
from moviestar.cloud_creation_state import CreationBusy, CreationStateError, creation_request


def _result(raw):
    # Strict path-free result shape. Reject unknown fields at every level before
    # printing server data. Strings occur only in known enumerations/timecodes.
    from moviestar.activity_file import normalize_options
    def shape(value, keys): return isinstance(value, dict) and set(value) == set(keys.split())
    def num(value, lo=0, hi=86400): return type(value) in (int,float) and lo <= value <= hi and math.isfinite(value)
    def tc(value): return shape(value,'seconds text') and num(value['seconds']) and isinstance(value['text'],str) and re.fullmatch(r'\d+:\d{2}:\d{2}\.\d{3}',value['text'])
    if not shape(raw,'file_size_bytes range settings analysis_dimensions samples active_ranges idle_ranges no_new_frame_ranges summary hint'): return None
    if type(raw['file_size_bytes']) is not int or not 1 <= raw['file_size_bytes'] <= 1073741824: return None
    window=raw['range'];settings=raw['settings'];summary=raw['summary']
    if not shape(window,'clock from to duration') or window['clock']!='asset' or not all(tc(window[k]) for k in ('from','to','duration')): return None
    if not shape(settings,'interval_seconds active_threshold merge_gap_seconds metric metric_range changed_pixel_min_luma_delta analysis_width'): return None
    if settings['metric']!='mean_absolute_luma_difference' or settings['metric_range']!=[0,1] or settings['changed_pixel_min_luma_delta']!=16 or settings['analysis_width']!=160: return None
    try: normalize_options({'interval_seconds':settings['interval_seconds'],'threshold':settings['active_threshold'],'merge_gap_seconds':settings['merge_gap_seconds']})
    except ValueError: return None
    if not shape(raw['analysis_dimensions'],'width height') or raw['analysis_dimensions']['width']!=160 or not num(raw['analysis_dimensions']['height'],2,640): return None
    if not shape(summary,'sample_count comparison_count active_range_count idle_range_count held_sample_count no_new_frame_range_count active_duration_seconds idle_duration_seconds') or not all(num(v) for v in summary.values()): return None
    samples=raw['samples']
    if not isinstance(samples,list) or not 2<=len(samples)<=5000 or summary['sample_count']!=len(samples) or summary['comparison_count']!=len(samples)-1: return None
    for i,s in enumerate(samples):
        if not shape(s,'index clock decoded_frame frame_state visual_change classification') or s['index']!=i: return None
        if not shape(s['clock'],'result source') or not all(tc(v) for v in s['clock'].values()): return None
        d=s['decoded_frame']
        if not shape(d,'source_time newly_decoded_since_previous_sample') or (d['source_time'] is not None and not tc(d['source_time'])) or d['newly_decoded_since_previous_sample'] not in (True,False,None): return None
        if s['frame_state'] not in ('baseline','new','held','decoded_unchanged','unavailable') or s['classification'] not in ('baseline','active','idle'): return None
        if s['visual_change'] is not None and (not shape(s['visual_change'],'mean_absolute_luma_difference changed_pixel_ratio') or not all(num(v,0,1) for v in s['visual_change'].values())): return None
    for name in ('active_ranges','idle_ranges','no_new_frame_ranges'):
        ranges=raw[name]
        if not isinstance(ranges,list) or len(ranges)>5000: return None
        for span in ranges:
            if not isinstance(span,dict) or set(span)-{'from','to','duration','confidence','evidence'} or not {'from','to','duration','evidence'}<=set(span): return None
            if not all(tc(span[k]) for k in ('from','to','duration')) or ('confidence' in span and not num(span['confidence'],0,1)): return None
            e=span['evidence']
            if not isinstance(e,dict) or set(e)-{'active_sample_count','comparison_sample_count','sample_indexes','max_change','mean_change','bridged_idle_seconds','held_sample_count','unavailable_sample_count'}: return None
            for k,v in e.items():
                if k=='sample_indexes':
                    if not isinstance(v,list) or len(v)>5000 or not all(type(x) is int and 0<=x<len(samples) for x in v): return None
                elif not num(v): return None
    from moviestar.activity_file import ACTIVITY_HINT
    if raw['hint']!=ACTIVITY_HINT: return None
    return raw


def _job(raw, asset, expected=None):
    from moviestar.cloud import _UUID
    if (not isinstance(raw, dict) or not isinstance(raw.get('id'), str) or not _UUID.fullmatch(raw['id'])
            or (expected is not None and raw['id'] != expected) or raw.get('asset_id') != asset
            or raw.get('state') not in ('queued', 'running', 'succeeded', 'failed', 'cancelled')):
        return None
    runtime = raw.get('runtime_sha256')
    if not isinstance(runtime,str) or not re.fullmatch(r'[a-f0-9]{64}',runtime): return None
    from moviestar.activity_file import normalize_options
    try:
        if normalize_options(raw.get('options')) != raw.get('options'): return None
    except ValueError:
        return None
    result = _result(raw.get('result')) if raw['state'] == 'succeeded' else None
    if raw['state'] != 'succeeded' and raw.get('result') is not None: return None
    if raw['state'] == 'succeeded' and result is None:
        return None
    failure = raw.get('failure_code')
    if failure is not None and failure not in ('download_failed', 'input_changed', 'activity_failed', 'resource_limit',
                                              'access_ended', 'job_timeout', 'retry_exhausted', 'no_video', 'invalid_range', 'sample_limit'):
        return None
    return {'id': raw['id'], 'asset_id': asset, 'state': raw['state'], 'failure_code': failure, 'result': result, 'options': raw.get('options'), 'runtime_sha256': runtime}


def register_activity(cloud):
    @cloud.command('activity')
    @click.argument('asset_id')
    @click.option('--interval', type=float, default=None, help='Seconds between samples (default 0.25).')
    @click.option('--threshold', type=float, default=None, help='Normalized luma-change threshold (default 0.005).')
    @click.option('--merge-gap', type=float, default=None, help='Bridge active windows across idle gaps (default 0.5 seconds).')
    @click.option('--from', 'from_tc', help='Range start on the asset file timeline: seconds, M:SS or H:MM:SS.mmm.')
    @click.option('--to', 'to_tc', help='Range end on the asset file timeline; defaults to the asset duration.')
    @click.option('--idempotency-key', help='Reuse this key after an uncertain reply. Saved automatically when omitted.')
    @click.option('--job-id', help='Read/reopen an existing job without scheduling any work.')
    @click.option('--wait/--no-wait', default=True, help='Wait for a result (default), or return the durable job immediately.')
    @click.option('--timeout', type=click.IntRange(0, 900), default=300, show_default=True, help='Maximum polling seconds; expiry does not cancel the job.')
    def activity(asset_id, interval, threshold, merge_gap, from_tc, to_tc, idempotency_key, job_id, wait, timeout):
        """Inspect a ready Cloud ASSET_ID using Cloud compute.

        No local media or FFmpeg is needed. Retry an interrupted invocation to
        recover the same request; use --job-id to reopen across sessions without
        queuing a new job. Completed requests remain saved; use a new explicit idempotency key
        to deliberately request another analysis. Local 'moviestar activity' remains project-local and needs no account. Visual change is a heuristic, not editorial importance, speech detection or semantic understanding.
        """
        from moviestar.cloud import _UUID, _KEY, _connection, _request, _fail
        command = 'cloud activity'
        if (not _UUID.fullmatch(asset_id) or (job_id is not None and not _UUID.fullmatch(job_id))
                or (idempotency_key is not None and not _KEY.fullmatch(idempotency_key))
                or (job_id is not None and idempotency_key is not None)):
            _fail(command, 'invalid_request', 'Use an asset UUID and either --job-id or --idempotency-key.')
        options = {}
        try:
            from moviestar.timecodes import parse_timecode
            from moviestar.activity_file import normalize_options
            flags = (interval, threshold, merge_gap, from_tc, to_tc)
            if job_id and any(v is not None for v in flags): raise ValueError()
            if not job_id:
                values = {k:v for k,v in [('interval_seconds',interval),('threshold',threshold),('merge_gap_seconds',merge_gap),('from_seconds',parse_timecode(from_tc) if from_tc is not None else None),('to_seconds',parse_timecode(to_tc) if to_tc is not None else None)] if v is not None}
                options = normalize_options(values)
        except ValueError:
            _fail(command, 'invalid_request', 'Use finite controls and an increasing asset range yielding 2–5000 samples. Reopen --job-id without analysis flags.')
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
                    'not_found', 'asset_not_ready', 'idempotency_conflict', 'queue_busy', 'unsupported_asset', 'activity_limit', 'invalid_range', 'no_video', 'sample_limit') else 'activity_unavailable'
                _fail(command, safe, 'Preserve the asset ID and request key. Retry the same request; check account access if denied.', **common)
            parsed = _job(body.get('job'), asset_id, expected)
            if parsed is None or (options and parsed['options'] != options):
                _fail(command, 'invalid_response', 'Preserve the request key and retry to recover the same activity.', **common)
            return parsed
        def poll(current):
            deadline = time.monotonic() + timeout
            while wait and current['state'] in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
                current = read(*_request(f"{base}/activities/{current['id']}", credential, max_response_bytes=3600000), expected=current['id'])
            return current
        try:
            if job_id:
                current = poll(read(*_request(f'{base}/activities/{job_id}', credential, max_response_bytes=3600000), expected=job_id))
            else:
                subject = json.dumps({'asset_id': asset_id, **options}, sort_keys=True) if options else asset_id
                with creation_request(base, credential, subject, idempotency_key, recovery_directory='cloud-activity-requests') as pending:
                    common['idempotency_key'] = pending.key
                    pending.save()
                    current = read(*_request(base + '/activity', credential, payload=options, key=pending.key, max_response_bytes=3600000))
                    common['job_id'] = current['id']
                    current = poll(current)
                    # Retain completed requests too; a new explicit key requests a new analysis.
        except KeyboardInterrupt:
            _fail(command, 'interrupted', 'The Cloud job may still be running. Retry the same asset/key, or reopen the returned job ID.', **common)
        except CreationBusy:
            _fail(command, 'activity_in_progress', 'Another activity invocation for this asset is active. Wait, then retry.', **common)
        except CreationStateError:
            _fail(command, 'recovery_state_unavailable', 'Could not preserve activity retry state. Keep the original key/job ID; inspect account profile permissions before retrying.', **common)
        click.echo(json.dumps({'command': command, 'status': current['state'], 'job': current, **common,
            'hint': f"Reopen without new work: moviestar cloud activity {asset_id} --job-id {current['id']}"}, indent=2))
        if current['state'] in ('failed', 'cancelled'):
            raise click.exceptions.Exit(1)
