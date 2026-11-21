"""Import and reopen immutable supplied transcripts; never selects edit inputs."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlencode
import click
from moviestar.cloud_creation_state import creation_request, _write, CreationStateError, CreationBusy

CONTRACT = 'project-transcripts-v1'
UUID = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', re.I)


def fail(code, message, **extra):
    click.echo(json.dumps({'error': {'code': code, 'message': message, 'details': {}, 'retryable': code == 'import_unconfirmed'}, **extra}, indent=2))
    raise click.exceptions.Exit(1)


def service_error(body):
    error = body.get('error')
    if isinstance(error, dict) and isinstance(error.get('code'), str) and isinstance(error.get('message'), str):
        click.echo(json.dumps(body, indent=2))
        raise click.exceptions.Exit(1)
    fail('cloud_unavailable', 'Service unavailable; retain the original request and key.')


def connection(project, reference):
    from moviestar.cloud import _connection, _request
    if not UUID.fullmatch(project) or not UUID.fullmatch(reference):
        fail('invalid_request', 'Use the project UUID and membership reference_id, not asset_id.')
    endpoint, credential = _connection('cloud projects transcripts')
    status, capability = _request(endpoint + '/capabilities', credential)
    if status != 200:
        service_error(capability)
    if capability.get('project_transcripts', {}).get('contract_version') != CONTRACT:
        fail('transcript_imports_unavailable', 'This service does not advertise project transcript support.')
    # Disabled intake is not a reason to block an accepted replay or a read.
    return endpoint + f'/projects/{project.lower()}/asset-references/{reference.lower()}/transcripts', credential


def metadata(value):
    return (isinstance(value, dict) and isinstance(value.get('id'), str) and UUID.fullmatch(value['id'])
            and type(value.get('version')) is int and value['version'] > 0 and value.get('format') in ('srt', 'vtt')
            and isinstance(value.get('sha256'), str) and re.fullmatch(r'[a-f0-9]{64}', value['sha256'])
            and type(value.get('size_bytes')) is int and 0 < value['size_bytes'] <= 262144
            and type(value.get('source_duration_ms')) is int and 0 < value['source_duration_ms'] <= 86400000
            and value.get('time_space') == 'source' and value.get('timing_provenance') in (
                {'kind': 'cue_interpolated', 'word_timing_verified': False},
                {'kind': 'inline_with_cue_interpolation', 'word_timing_verified': False}))


def exact_response(body, project, reference, version=None):
    association, transcript = body.get('association'), body.get('transcript')
    return (body.get('contract_version') == CONTRACT and isinstance(association, dict) and metadata(transcript)
            and association.get('project_id') == project.lower() and association.get('reference_id') == reference.lower()
            and isinstance(association.get('asset_id'), str) and UUID.fullmatch(association['asset_id'])
            and association.get('transcript_version_id') == transcript['id']
            and (version is None or transcript['id'] == version.lower()))


def register_transcripts(projects):
    @projects.group('transcripts')
    def transcripts():
        """Import supplied captions for an existing owned project media reference.

        Use reference_id from 'cloud projects assets PROJECT', not asset_id.
        No local project, ASR or saved-edit selection is changed. New intake
        requires service enrollment; retained exact reads/replays remain subject
        to current access. Cue timing is not verified word alignment.
        """

    @transcripts.command('import')
    @click.argument('project')
    @click.option('--reference', required=True)
    @click.option('--captions', required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option('--idempotency-key', required=True)
    def import_transcript(project, reference, captions, idempotency_key):
        """Retain source-time SRT/VTT and return an immutable transcript UUID.

        Retain exact project/reference/format/bytes/key before sending. The CLI
        saves a private request snapshot for recovery before POST. Repeat that
        same request/key after uncertain replies. Corrections use a new key.
        Save the returned association/hash/version for reconnects. Imports do
        not select transcripts in saved edits or consume ASR minutes. TXT and
        native provider JSON are not accepted by this interchange importer.
        """
        from moviestar.cloud import _request
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', idempotency_key):
            fail('invalid_request', 'Use 1–128 ASCII letters, digits, underscores or hyphens for the retained key.')
        fmt = captions.suffix.lower().removeprefix('.')
        try:
            with captions.open('rb') as stream:
                raw = stream.read(262145)
            if fmt not in ('srt', 'vtt') or not 0 < len(raw) <= 262144:
                raise ValueError()
            text = raw.decode('utf-8')  # Preserve BOM and CRLF exactly.
        except (ValueError, OSError):
            fail('invalid_transcript', 'Use a UTF-8 SRT/VTT file of at most 262144 bytes.')
        url, credential = connection(project, reference)
        digest = hashlib.sha256(raw).hexdigest()
        identity = json.dumps([url, fmt, digest], separators=(',', ':'))
        submitted = False
        snapshot = None
        try:
            with creation_request(url, credential, identity, idempotency_key, recovery_directory='cloud-transcript-requests') as record:
                snapshot = record.path.with_name(record.path.stem + '-' + hashlib.sha256(idempotency_key.encode()).hexdigest() + '.transcript.json')
                _write(snapshot, {'project_id': project.lower(), 'reference_id': reference.lower(), 'format': fmt,
                                  'text': text, 'idempotency_key': idempotency_key, 'sha256': digest})
                record.save()
                submitted = True
                status, body = _request(url, credential, payload={'format': fmt, 'text': text}, key=idempotency_key)
                if status not in (200, 201):
                    error = body.get('error')
                    if status >= 500 and isinstance(error, dict) and error.get('code') == 'transcript_imports_unavailable':
                        service_error(body)
                    if status == 0 or status >= 500:
                        fail('import_unconfirmed', 'Outcome may be unknown. Retry the same project/reference/format/bytes/key; recover exact bytes from out if the original file changed.',
                             import_outcome='unknown', idempotency_key=idempotency_key, out=str(snapshot))
                    service_error(body)
                if (not exact_response(body, project, reference) or body.get('status') != 'imported'
                        or body.get('idempotency_key') != idempotency_key or type(body.get('replayed')) is not bool
                        or body.get('saved_edit_changed') is not False or body.get('asr_seconds_consumed') != 0
                        or body['transcript']['sha256'] != digest or body['transcript']['size_bytes'] != len(raw)
                        or body['transcript']['format'] != fmt):
                    fail('invalid_response', 'Import could have succeeded. Retain the same request/key and recover the exact version before proceeding.',
                         import_outcome='unknown', idempotency_key=idempotency_key, out=str(snapshot))
                record.resolve()
                click.echo(json.dumps(body, indent=2))
        except (CreationStateError, CreationBusy):
            if submitted:
                fail('import_unconfirmed', 'Could not retain confirmation after submission. Retry the SAME request/key.',
                     import_outcome='unknown', idempotency_key=idempotency_key, out=str(snapshot))
            fail('recovery_unavailable', 'Could not safely retain this request, or another process holds its lock. No new request was submitted; preserve the original key.')

    @transcripts.command('show')
    @click.argument('project')
    @click.option('--reference', required=True)
    @click.option('--version', required=True, help='Exact transcript UUID from items[].id; not the ordinal. No latest fallback.')
    def show(project, reference, version):
        """Read original text/hash/timing for one exact authorized version.

        Does not change an edit or select a transcript. Inaccessible/deleted
        versions fail; current access is required even with a retained ID.
        """
        from moviestar.cloud import _request
        if not UUID.fullmatch(version):
            fail('invalid_request', 'Use the exact transcript version UUID.')
        url, credential = connection(project, reference)
        status, body = _request(url + '/' + version.lower(), credential)
        if status != 200:
            service_error(body)
        if (not exact_response(body, project, reference, version) or not isinstance(body.get('text'), str)
                or hashlib.sha256(body['text'].encode()).hexdigest() != body['transcript']['sha256']
                or len(body['text'].encode()) != body['transcript']['size_bytes']):
            fail('invalid_response', 'Service did not return the requested exact transcript bytes and identity.')
        click.echo(json.dumps(body, indent=2))

    @transcripts.command('list')
    @click.argument('project')
    @click.option('--reference', required=True)
    @click.option('--limit', type=click.IntRange(1, 100), default=20, show_default=True)
    @click.option('--cursor', help='Follow next_cursor unchanged until null.')
    def list_transcripts(project, reference, limit, cursor):
        """List retained version metadata, newest first, without transcript text.

        Use items[].id with show --version; version is the ordinal. Listing
        order does not select a version in any saved edit.
        """
        from moviestar.cloud import _request
        url, credential = connection(project, reference)
        if cursor is not None and (not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', cursor)):
            fail('invalid_request', 'Follow next_cursor unchanged.')
        status, body = _request(url + '?' + urlencode({'limit': limit, **({'cursor': cursor} if cursor is not None else {})}), credential)
        if status != 200:
            service_error(body)
        items = body.get('items')
        next_cursor = body.get('next_cursor', 'missing')
        if (body.get('contract_version') != CONTRACT or body.get('project_id') != project.lower()
                or body.get('reference_id') != reference.lower() or not isinstance(items, list) or len(items) > limit
                or any(not metadata(item) or 'text' in item for item in items)
                or not (next_cursor is None or isinstance(next_cursor, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', next_cursor))):
            fail('invalid_response', 'Service returned invalid transcript metadata. Retain the original cursor and retry.')
        click.echo(json.dumps(body, indent=2))
