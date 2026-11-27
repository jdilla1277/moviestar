"""One verifiable JPEG from an exact immutable Cloud render artifact."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from http.client import HTTPException
from uuid import uuid4
import click
from moviestar import cloud as service
from moviestar.account import _CLIENT_MARKER, _NoRedirectHandler
from moviestar.cloud_creation_state import creation_request, CreationBusy, CreationStateError


def fail(code, message, *, details=None, **fields):
    click.echo(json.dumps({'error': {'code': code, 'message': message, 'details': details or {},
                                    'retryable': code in ('submission_unconfirmed', 'inspection_unavailable', 'frame_not_ready','storyboard_not_ready')},
                           'request_id': str(uuid4()), **fields}, indent=2))
    raise click.exceptions.Exit(1)


def checked(status, body, **fields):
    if status in (200, 202):
        return
    error = body.get('error')
    if (isinstance(error, dict) and set(error) == {'code', 'message', 'details', 'retryable'}
            and isinstance(error['code'], str) and isinstance(error['message'], str)
            and isinstance(error['details'], dict) and type(error['retryable']) is bool):
        click.echo(json.dumps({'error': error, **({'request_id': body['request_id']} if isinstance(body.get('request_id'), str) else {}), **fields}, indent=2))
        raise click.exceptions.Exit(1)
    fail('inspection_unavailable', 'Reopen the same inspection IDs; no render or edit was requested.', **fields)


def ids(*values):
    if any(not isinstance(v, str) or not service._UUID.fullmatch(v) for v in values):
        fail('invalid_request', 'Use exact project, rendered-artifact and inspection-job UUIDs.')
    return [v.lower() for v in values]


def valid_job(j, project, artifact, job=None, at=None):
    try:
        if (not isinstance(j, dict) or j['project_id'] != project or j['artifact_id'] != artifact
                or not all(isinstance(j[k], str) and service._UUID.fullmatch(j[k]) for k in ('id', 'revision_id', 'render_job_id'))
                or job is not None and j['id'] != job
                or j['clock'] != 'rendered_result' or not re.fullmatch('[a-f0-9]{64}', j['artifact_sha256'])
                or j['state'] not in ('queued', 'running', 'succeeded', 'failed', 'cancelled')
                or type(j['request']['at_ms']) is not int or not 0 <= j['request']['at_ms'] <= 86400000
                or at is not None and j['request']['at_ms'] != at):
            return False
        if j['state'] != 'succeeded':
            return j.get('result') is None
        r, image = j['result'], j['result']['image']
        return (r['requested_at_ms'] == j['request']['at_ms'] and type(r['at_ms']) is int
                and 0 <= r['at_ms'] <= r['requested_at_ms'] and r['selection'] == 'frame_at_or_before'
                and re.fullmatch('[a-f0-9]{40}', r['extractor']['engine_version']) is not None
                and re.fullmatch('[a-f0-9]{64}', r['extractor']['runtime_sha256']) is not None
                and image['mime_type'] == 'image/jpeg'
                and all(type(image[k]) is int for k in ('width', 'height', 'size_bytes'))
                and 1 <= image['width'] <= 640 and 1 <= image['height'] <= 4096
                and 4 <= image['size_bytes'] <= 1048576 and re.fullmatch('[a-f0-9]{64}', image['sha256']) is not None)
    except (KeyError, TypeError, ValueError):
        return False


def save_image(url, credential, job, out, *, max_bytes=1048576):
    if out.exists() or out.is_symlink():
        fail('output_exists', 'Choose a new filename; existing files are never overwritten.')
    temporary = None
    try:
        request = Request(url + '/image', headers={'Authorization': 'Bearer ' + credential,
                          'x-moviestar-client': _CLIENT_MARKER, 'Accept': 'image/jpeg'})
        with build_opener(_NoRedirectHandler()).open(request, timeout=30) as response:
            if response.status != 200 or response.headers.get_content_type() != 'image/jpeg':
                raise ValueError('Invalid image response')
            image = response.read(max_bytes+1)
        expected = job['result']['image']
        if (len(image) != expected['size_bytes'] or len(image) > max_bytes
                or not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9')
                or hashlib.sha256(image).hexdigest() != expected['sha256']):
            raise ValueError('Image identity mismatch')
        with tempfile.NamedTemporaryFile(dir=out.parent, prefix='.moviestar-frame-', delete=False) as f:
            temporary = Path(f.name)
            f.write(image)
            f.flush()
            os.fsync(f.fileno())
        os.link(temporary, out)
        return str(out.resolve())
    except (HTTPError, URLError, HTTPException, OSError, ValueError):
        fail('image_unavailable', 'The image was not saved. Reopen these same IDs and use a writable new filename; do not submit another job.')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def register_frame_commands(artifacts):
    @artifacts.command('screenshot')
    @click.argument('project_id')
    @click.argument('artifact_id')
    @click.option('--at-ms', required=True, help='Integer RESULT-time milliseconds within this exact rendered video, excluding its end.')
    @click.option('--idempotency-key', help='Retained on retry; generated and saved if omitted. A new key deliberately requests new processing.')
    def screenshot(project_id, artifact_id, at_ms, idempotency_key):
        """Request one frame from an existing verified output. Never rerenders,
        imports an asset, edits the spec or silently chooses the latest output.
        Poll/reopen using screenshot-show. Actual and requested frame times differ.
        """
        project_id, artifact_id = ids(project_id, artifact_id)
        if not re.fullmatch('[0-9]+', at_ms) or not 0 <= int(at_ms) <= 86400000:
            fail('invalid_request', 'Use integer result-time milliseconds, for example --at-ms 10000.')
        at_ms = int(at_ms)
        if idempotency_key is not None and not service._KEY.fullmatch(idempotency_key):
            fail('invalid_request', 'Use a key of 1–128 ASCII letters, digits, underscores or hyphens.')
        endpoint, credential = service._connection('cloud artifacts screenshot')
        url = endpoint + f'/projects/{project_id}/artifacts/{artifact_id}/screenshots'
        try:
            with creation_request(url, credential, json.dumps({'at_ms': at_ms}, sort_keys=True), idempotency_key) as pending:
                pending.save()
                status, body = service._request(url, credential, payload={'at_ms': at_ms}, key=pending.key)
                recovery = f'moviestar cloud artifacts screenshot {project_id} {artifact_id} --at-ms {at_ms} --idempotency-key {pending.key}'
                if status == 0 or status >= 500:
                    fail('submission_unconfirmed', 'The outcome is unknown. Retry the exact artifact, timestamp and retained key.', idempotency_key=pending.key, recovery_command=recovery)
                checked(status, body)
                if (body.get('idempotency_key') != pending.key or type(body.get('replayed')) is not bool
                        or not valid_job(body.get('job'), project_id, artifact_id, at=at_ms)):
                    fail('submission_unconfirmed', 'No matching receipt was confirmed. Preserve the original request/key.', idempotency_key=pending.key, recovery_command=recovery)
                click.echo(json.dumps(body, indent=2))
        except (CreationBusy, CreationStateError):
            fail('request_state_unavailable', 'Preserve the retained key and repair local retry state before retrying.')

    @artifacts.command('screenshot-show')
    @click.argument('project_id')
    @click.argument('artifact_id')
    @click.argument('job_id')
    @click.option('--out', type=click.Path(path_type=Path), help='Save a new JPEG only after size/SHA verification; never overwrites.')
    def show(project_id, artifact_id, job_id, out):
        """Reopen the exact frame job after reconnecting. No new extraction or
        usage reservation. The result binds the rendered file hash and revision.
        """
        project_id, artifact_id, job_id = ids(project_id, artifact_id, job_id)
        endpoint, credential = service._connection('cloud artifacts screenshot-show')
        url = endpoint + f'/projects/{project_id}/artifacts/{artifact_id}/screenshots/{job_id}'
        status, body = service._request(url, credential)
        checked(status, body)
        if not valid_job(body.get('job'), project_id, artifact_id, job_id):
            fail('invalid_response', 'Frame identity or manifest did not match. Reopen the original IDs.')
        result = {**body, 'file_saved': False}
        if out is not None:
            j = body['job']
            if j['state'] in ('failed', 'cancelled'):
                fail(j.get('failure_code') or 'frame_extraction_failed', 'This saved job ended without an image. Reopening does not restart it; review the failure before deliberately requesting new work.', job=j)
            if j['state'] != 'succeeded':
                fail('frame_not_ready', 'Poll these same IDs until the frame job ends.', job=j)
            result.update(out=save_image(url, credential, j, out), file_saved=True)
        click.echo(json.dumps(result, indent=2))
