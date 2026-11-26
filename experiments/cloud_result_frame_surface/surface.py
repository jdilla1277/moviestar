"""Runnable design fixture. No HTTP, authentication, jobs, extraction or billing."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

import click

PROJECT = '00000000-0000-4000-8000-000000000001'
ARTIFACT = '00000000-0000-4000-8000-000000000002'
REVISION = '00000000-0000-4000-8000-000000000003'
RENDER = '00000000-0000-4000-8000-000000000004'
JOB = '00000000-0000-4000-8000-000000000005'
IMAGE = Path(__file__).resolve().parents[2] / '.context/cloud-result-frame-mock/mock-frame.jpg'

def fixture_bytes():
    # Generated test media stays ignored; it is never committed or uploaded.
    if not IMAGE.exists():
        IMAGE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=0x102030:s=64x112', '-vf',
                        'drawtext=text=MOCK:fontcolor=white:fontsize=12:x=10:y=35,'
                        'drawtext=text=ONLY:fontcolor=white:fontsize=12:x=10:y=55',
                        '-frames:v', '1', '-q:v', '2', '-n', str(IMAGE)],
                       stdin=subprocess.DEVNULL, capture_output=True, check=True)
        IMAGE.chmod(0o600)
    return IMAGE.read_bytes()
# Explicit canned presentation times, NOT a frame-rate calculation or decoder.
SAMPLES = {0: 0, 10000: 10000, 10017: 10000, 29999: 29967}
JOBS = {10000: JOB, 0: '00000000-0000-4000-8000-000000000006',
        10017: '00000000-0000-4000-8000-000000000007',
        29999: '00000000-0000-4000-8000-000000000008'}
JOB_TIMES = {job: at for at, job in JOBS.items()}
SCENARIOS = ['ready', 'replay', 'lost-response', 'missing', 'foreign', 'revoked',
             'disabled', 'conflict', 'pending', 'failed']


def failure(code, message, status, retryable=False, details=None):
    return status, {'error': {'code': code, 'message': message,
                             'details': details or {}, 'retryable': retryable},
                    'request_id': 'mock-result-frame'}


def resource(at_ms=10000, state='succeeded'):
    image = fixture_bytes()
    return {'id': JOBS[at_ms], 'project_id': PROJECT, 'artifact_id': ARTIFACT,
            'revision_id': REVISION, 'render_job_id': RENDER,
            'artifact_sha256': 'a' * 64, 'clock': 'rendered_result',
            'state': state, 'request': {'at_ms': at_ms},
            'failure_code': 'frame_extraction_failed' if state == 'failed' else None,
            'result': None if state != 'succeeded' else {
                'requested_at_ms': at_ms, 'at_ms': SAMPLES[at_ms],
                'selection': 'frame_at_or_before',
                'extractor': {'engine_version': 'b' * 40, 'runtime_sha256': 'c' * 64},
                'image': {'mime_type': 'image/jpeg', 'width': 64, 'height': 112,
                          'size_bytes': len(image),
                          'sha256': hashlib.sha256(image).hexdigest()}}}


def respond(command, scenario, project, artifact, job, at_ms, key):
    try:
        for value in [project, artifact] + ([job] if job is not None else []):
            uuid.UUID(value)
    except (ValueError, TypeError):
        return failure('invalid_request', 'Use the exact project/artifact/job UUIDs.', 400)
    if command == 'screenshot':
        import re
        if not key or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key) is None:
            return failure('invalid_request', 'Retain a key of 1–128 ASCII letters, digits, underscores or hyphens.', 400)
        if at_ms is None:
            return failure('invalid_request', 'Use integer result-time milliseconds, for example --at-ms 10000.', 400)
        if not 0 <= at_ms < 30000:
            return failure('range_out_of_bounds', 'Use a time within the 30-second rendered file; its end is excluded.', 422)
    if scenario in ('missing', 'foreign', 'revoked') or project != PROJECT or artifact != ARTIFACT or job not in (None, *JOB_TIMES):
        return failure('not_found', 'The rendered artifact or frame job is unavailable to this account.', 404)
    if scenario == 'disabled' and command == 'screenshot':
        return failure('inspection_unavailable', 'New frame requests are unavailable. Reopen existing jobs or retry the same request/key.', 503, True)
    if scenario == 'conflict' and command == 'screenshot':
        return failure('idempotency_conflict', 'This key belongs to different frame instructions. Recover the original; use a new key only for deliberately different work.', 409)
    if scenario == 'lost-response' and command == 'screenshot':
        return failure('submission_unconfirmed', 'The reply was lost; the job may exist. Retry the exact project, artifact, timestamp and retained key.', 503, True,
                       {'recovery': {'project_id': project, 'artifact_id': artifact,
                                     'at_ms': at_ms, 'idempotency_key': key}})
    selected = at_ms if command == 'screenshot' else JOB_TIMES[job]
    if selected not in SAMPLES:
        return failure('mock_fixture_only', 'This mock models 0, 10000, 10017 and 29999ms; this is not a production restriction.', 400)
    state = 'failed' if scenario == 'failed' else 'queued' if scenario == 'pending' or (command == 'screenshot' and scenario != 'replay') else 'succeeded'
    payload = {'job': resource(selected, state)}
    if command == 'screenshot':
        payload.update(replayed=scenario == 'replay', idempotency_key=key)
    return (202 if state == 'queued' else 200), payload


@click.group(help='DESIGN MOCK ONLY. Proposed remote artifact-frame commands; no live service calls. All IDs, jobs and pixels are synthetic.')
@click.option('--surface', type=click.Choice(['cli', 'api', 'mcp']), default='cli', show_default=True)
@click.option('--scenario', type=click.Choice(SCENARIOS), default='ready', show_default=True)
@click.pass_context
def app(ctx, surface, scenario):
    ctx.obj = (surface, scenario)


def invoke(ctx, command, project, artifact, job=None, at_ms=None, key=None, out=None):
    surface, scenario = ctx.obj
    status, response = respond(command, scenario, project, artifact, job, at_ms, key)
    create = command == 'screenshot'
    route = f'/projects/{project}/artifacts/{artifact}/screenshots' + ('' if create else f'/{job}')
    transport = {'method': 'POST' if create else 'GET', 'path': route}
    if create:
        transport.update(body={'at_ms': at_ms}, headers={'Idempotency-Key': key})
    if surface == 'cli':
        transport = {'command': 'moviestar cloud artifacts ' + command,
                     'project_id': project, 'artifact_id': artifact,
                     **({'at_ms': at_ms, 'idempotency_key': key} if create else {'job_id': job})}
    elif surface == 'mcp':
        transport = {'tool': 'moviestar_screenshot_render_artifact' if create else 'moviestar_get_render_artifact_screenshot',
                     'arguments': {'project_id': project, 'artifact_id': artifact,
                                   **({'at_ms': at_ms, 'idempotency_key': key} if create else {'job_id': job})},
                     'isError': status >= 400}
    saved = False
    if out is not None and status < 400:
        if response['job']['state'] == 'failed':
            status, response = failure('frame_extraction_failed', 'This saved job failed. Reopening does not retry extraction; review the failure before deliberately requesting new work.', 422,
                                       details={'job_id': job, 'state': 'failed', 'failure_code': response['job']['failure_code']})
        elif response['job']['state'] != 'succeeded':
            status, response = failure('frame_not_ready', 'Poll this same job before saving its image.', 409)
        elif out.exists():
            status, response = failure('output_exists', 'Choose a new output filename; existing files are never overwritten.', 409)
        else:
            with out.open('xb') as target, IMAGE.open('rb') as source:
                shutil.copyfileobj(source, target)
            saved = True
    if surface == 'mcp':
        transport['isError'] = status >= 400
    envelope = {'mock': True,
                'fixture_limit': 'Canned jobs and synthetic pixels. No server, authority, persistence, customer processing or usage evidence.',
                'transport': transport, 'http_status': status, 'response': response,
                'effects': None if scenario == 'lost-response' and create else {'jobs_created': 0, 'usage_reserved': 0, 'edit_writes': 0},
                'file_saved': saved}
    if saved:
        envelope['out'] = str(out.resolve())
    if surface == 'mcp':
        envelope['mcp_result'] = {'isError': status >= 400, 'structuredContent': response,
                                  'content': [{'type': 'text', 'text': json.dumps(response)}]}
    if surface == 'mcp' and status < 400 and response['job']['state'] == 'succeeded':
        envelope['mcp_result']['content'].append({'type': 'image', 'mimeType': 'image/jpeg',
                                                 'data': base64.b64encode(IMAGE.read_bytes()).decode('ascii')})
        envelope['proposed_image_delivery'] = {'content_type': 'image', 'mime_type': 'image/jpeg',
                                              'fixture_file': str(IMAGE), 'native_perception_verified': False}
    click.echo(json.dumps(envelope, indent=2))
    if status >= 400:
        raise click.exceptions.Exit(1)


@app.command(help='Propose one frame of this exact verified output, in RESULT time. Requires an existing artifact; never renders, imports an asset or changes the edit. Retain the same request/key after a lost reply.')
@click.argument('project')
@click.argument('artifact')
@click.option('--at-ms', type=str, required=True, help='Integer result-time milliseconds within this rendered file, excluding its end.')
@click.option('--idempotency-key', 'key', required=True)
@click.pass_context
def screenshot(ctx, project, artifact, at_ms, key):
    try:
        at_ms = int(at_ms)
    except ValueError:
        at_ms = None
    invoke(ctx, 'screenshot', project, artifact, at_ms=at_ms, key=key)


@app.command('screenshot-show', help='Reopen the same project/artifact/frame-job IDs. Proposed MCP returns a native JPEG; CLI --out saves a new file. Here --out copies ONLY a labeled synthetic fixture. Reads do not schedule work.')
@click.argument('project')
@click.argument('artifact')
@click.argument('job')
@click.option('--out', type=click.Path(path_type=Path))
@click.pass_context
def screenshot_show(ctx, project, artifact, job, out):
    invoke(ctx, 'screenshot-show', project, artifact, job=job, out=out)


if __name__ == '__main__':
    app()
