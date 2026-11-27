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
IMAGE = Path(__file__).resolve().parents[2] / '.context/cloud-result-storyboard-mock/mock-storyboard-v2.jpg'

DECODED_TIMES_MS = [0, 1967, *range(4000, 30000, 2000)]  # Canned, not FPS-derived.

def fixture_bytes():
    # Generated test media stays ignored; it is never committed or uploaded.
    if not IMAGE.exists():
        IMAGE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        filters = ['drawgrid=w=384:h=240:t=2:c=white']
        for i in range(15):
            x, y = i % 5 * 384 + 15, i // 5 * 240
            label = IMAGE.parent / f'label-{i}.txt'
            label.write_text(f'00:00:{DECODED_TIMES_MS[i]//1000:02d}.{DECODED_TIMES_MS[i]%1000:03d}')
            filters += [f'drawtext=text=MOCK ONLY:fontcolor=white:fontsize=24:x={x}:y={y+80}',
                        f'drawtext=textfile={label}:fontcolor=white:fontsize=20:x={x}:y={y+210}']
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=0x102030:s=1920x720', '-vf', ','.join(filters),
                        '-frames:v', '1', '-q:v', '2', '-n', str(IMAGE)],
                       stdin=subprocess.DEVNULL, capture_output=True, check=True)
        IMAGE.chmod(0o600)
    return IMAGE.read_bytes()
# Fixed synthetic whole-video plan. Not a planner or decoding implementation.
SAMPLING = {'from_ms': 0, 'to_ms': 30000, 'interval_ms': 2000,
            'interval_auto': True, 'columns': 5, 'rows': 3, 'frame_count': 15,
            'requested_times_ms': list(range(0, 30000, 2000))}
SCENARIOS = ['ready', 'replay', 'lost-response', 'missing', 'foreign', 'revoked',
             'disabled', 'paused-replay', 'conflict', 'pending', 'failed']


def failure(code, message, status, retryable=False, details=None):
    return status, {'error': {'code': code, 'message': message,
                             'details': details or {}, 'retryable': retryable},
                    'request_id': 'mock-result-storyboard'}


def resource(state='succeeded'):
    image = fixture_bytes()
    return {'id': JOB, 'project_id': PROJECT, 'artifact_id': ARTIFACT,
            'revision_id': REVISION, 'render_job_id': RENDER,
            'artifact_sha256': 'a' * 64, 'clock': 'rendered_result',
            'state': state, 'request': {}, 'sampling': SAMPLING,
            'failure_code': 'labels_unavailable' if state == 'failed' else None,
            'result': None if state != 'succeeded' else {
                'selection': 'frame_at_or_before', 'labels': 'decoded_result_time',
                'frames': [{'index': i, 'requested_at_ms': t, 'at_ms': DECODED_TIMES_MS[i],
                            'timecode': f'00:00:{DECODED_TIMES_MS[i]//1000:02d}.{DECODED_TIMES_MS[i]%1000:03d}'}
                           for i, t in enumerate(SAMPLING['requested_times_ms'])],
                'extractor': {'engine_version': 'b' * 40, 'runtime_sha256': 'c' * 64},
                'image': {'mime_type': 'image/jpeg', 'width': 1920, 'height': 720,
                          'size_bytes': len(image), 'sha256': hashlib.sha256(image).hexdigest()}}}


def respond(command, scenario, project, artifact, job, key):
    try:
        for value in [project, artifact] + ([job] if job is not None else []):
            uuid.UUID(value)
    except (ValueError, TypeError):
        return failure('invalid_request', 'Use the exact project/artifact/job UUIDs.', 400)
    if command == 'storyboard':
        import re
        if not key or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key) is None:
            return failure('invalid_request', 'Retain a key of 1–128 ASCII letters, digits, underscores or hyphens.', 400)
    if scenario in ('missing', 'foreign', 'revoked') or project != PROJECT or artifact != ARTIFACT or job not in (None, JOB):
        return failure('not_found', 'The rendered artifact or storyboard job is unavailable to this account.', 404)
    if scenario == 'disabled' and command == 'storyboard':
        return failure('inspection_unavailable', 'New storyboard requests are unavailable. Reopen existing jobs or retry the same request/key.', 503, True)
    if scenario == 'conflict' and command == 'storyboard':
        return failure('idempotency_conflict', 'This key belongs to different storyboard instructions. Recover the original; use a new key only for deliberately different work.', 409)
    if scenario == 'lost-response' and command == 'storyboard':
        return failure('submission_unconfirmed', 'The reply was lost; the job may exist. Retry the exact project, artifact, whole-video request and retained key.', 503, True,
                       {'recovery': {'project_id': project, 'artifact_id': artifact,
                                     'request': {}, 'idempotency_key': key}})
    state = 'failed' if scenario == 'failed' else 'queued' if scenario == 'pending' or (command == 'storyboard' and scenario not in ('replay', 'paused-replay')) else 'succeeded'
    payload = {'job': resource(state)}
    if command == 'storyboard':
        payload.update(replayed=scenario in ('replay', 'paused-replay'), idempotency_key=key)
    return (202 if state == 'queued' else 200), payload


@click.group(help='DESIGN MOCK ONLY. Proposed remote artifact-storyboard commands; no live service calls. All IDs, jobs and pixels are synthetic.')
@click.option('--surface', type=click.Choice(['cli', 'api', 'mcp']), default='cli', show_default=True)
@click.option('--scenario', type=click.Choice(SCENARIOS), default='ready', show_default=True)
@click.pass_context
def app(ctx, surface, scenario):
    ctx.obj = (surface, scenario)


def invoke(ctx, command, project, artifact, job=None, key=None, out=None):
    surface, scenario = ctx.obj
    status, response = respond(command, scenario, project, artifact, job, key)
    create = command == 'storyboard'
    route = f'/projects/{project}/artifacts/{artifact}/storyboards' + ('' if create else f'/{job}')
    transport = {'method': 'POST' if create else 'GET', 'path': route}
    if create:
        transport.update(body={}, headers={'Idempotency-Key': key})
    if surface == 'cli':
        transport = {'command': 'moviestar cloud artifacts ' + command,
                     'project_id': project, 'artifact_id': artifact,
                     **({'request': {}, 'idempotency_key': key} if create else {'job_id': job})}
    elif surface == 'mcp':
        transport = {'tool': 'moviestar_storyboard_render_artifact' if create else 'moviestar_get_render_artifact_storyboard',
                     'arguments': {'project_id': project, 'artifact_id': artifact,
                                   **({'idempotency_key': key} if create else {'job_id': job})},
                     'isError': status >= 400}
    saved = False
    if out is not None and status < 400:
        if response['job']['state'] == 'failed':
            status, response = failure(response['job']['failure_code'], 'Timecode labels could not be produced. Reopen this job for its failure context; resolve label/font support before deliberately requesting new work. Reads do not retry extraction.', 422,
                                       details={'job_id': job, 'state': 'failed', 'failure_code': response['job']['failure_code']})
        elif response['job']['state'] != 'succeeded':
            status, response = failure('storyboard_not_ready', 'Poll this same job before saving its sheet.', 409, True)
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


@app.command(help='Request one whole-video contact sheet of this EXACT verified output. RESULT-time labels and requested/decoded sample times are returned; the end is excluded. Never renders, imports an asset or changes the edit. Reuse the same key after an uncertain reply. This first slice uses automatic sampling only (up to 60 frames); no range/interval/grid controls.')
@click.argument('project')
@click.argument('artifact')
@click.option('--idempotency-key', 'key', required=True, help='Retain 1–128 ASCII letters, digits, underscores or hyphens. Mock requires an explicit key. Production CLI will generate/save one if omitted; MCP/API retain one explicitly.')
@click.pass_context
def storyboard(ctx, project, artifact, key):
    invoke(ctx, 'storyboard', project, artifact, key=key)


@app.command('storyboard-show', help='Reopen the exact project/artifact/storyboard-job IDs. A succeeded result has one native JPEG contact sheet in MCP; CLI --out saves a NEW file after size/SHA verification. Failed jobs are terminal. Reads never schedule work. This mock copies only a visibly labeled synthetic sheet, not video evidence.')
@click.argument('project')
@click.argument('artifact')
@click.argument('job')
@click.option('--out', type=click.Path(path_type=Path))
@click.pass_context
def storyboard_show(ctx, project, artifact, job, out):
    invoke(ctx, 'storyboard-show', project, artifact, job=job, out=out)


if __name__ == '__main__':
    app()
