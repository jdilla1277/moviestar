"""Isolated design rehearsal. Never registered in the production CLI.

Run python examples/cloud_delivery_mock.py --help. All data is synthetic;
fixture completion is a premise, not rendering, verification or file delivery.
"""
import json
import os
from pathlib import Path
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import click


def emit(**body):
    click.echo(json.dumps({'mock': True, 'media_delivered': False, **body}, indent=2))


def fail(code, hint, **fields):
    emit(error={'code': code, 'hint': hint}, **fields)
    raise click.exceptions.Exit(1)


def identity(value):
    try:
        return str(UUID(value))
    except ValueError:
        raise click.BadParameter('Use a UUID.') from None


def load(ctx):
    state = ctx.obj['state']
    if not state.exists():
        return {'jobs': {}, 'requests': {}, 'artifacts': {}}
    return json.loads(state.read_text())


def save(ctx, data):
    state = ctx.obj['state']
    temporary = state.with_name(state.name + '.' + str(uuid4()) + '.tmp')
    try:
        temporary.write_text(json.dumps(data, indent=2))
        os.replace(temporary, state)
    finally:
        temporary.unlink(missing_ok=True)


def authorize(ctx):
    if ctx.obj['actor'] == 'revoked':
        fail('credential_invalid', 'Reconnect as the same currently authorized account; retain IDs and keys.')
    if ctx.obj['actor'] == 'foreign':
        fail('not_found', 'Resource is absent or inaccessible.')


def resource(ctx, collection, project, resource_id):
    authorize(ctx)
    item = load(ctx)[collection].get(identity(resource_id))
    if not item or item['project_id'] != identity(project):
        fail('not_found', 'Resource is absent or inaccessible.')
    return item


@click.group()
@click.option('--state', required=True, type=click.Path(path_type=Path),
              help='Explicit disposable mock journal; no account credentials or media.')
@click.option('--actor', type=click.Choice(['owner', 'foreign', 'revoked']), default='owner',
              help='Synthetic authorization premise, not real authentication.')
@click.pass_context
def cli(ctx, state, actor):
    """MOCK ONLY: rehearse proposed cloud renders/artifacts commands.

    Separate invocations reuse --state to model a fresh client. No network,
    worker, engine, billing or real media is used. Run fixture complete only
    when the rehearsal asks you to assume successful independent verification.
    Rendering never shares. Artifact reads/downloads never render again.
    """
    ctx.obj = {'state': state, 'actor': actor}


@cli.command()
def capabilities():
    """List proposed support separately from live availability."""
    emit(contract_version='cloud-delivery-v1', render_submission_available=False,
         composition_inspection_available=False, clip_batch_available=False,
         mock_operations=['renders submit/show/cancel', 'artifacts list/show/download', 'review'],
         transition_limits={'internal_export': True, 'edge_fades': False,
                            'watch_inspect_screenshot': False})


@cli.group()
def renders():
    """Submit exact revisions; poll/reopen/cancel retained jobs."""


@renders.command()
@click.argument('project')
@click.option('--revision', required=True, help='Exact saved revision UUID; never latest.')
@click.option('--kind', required=True, type=click.Choice(['preview', 'final']))
@click.option('--idempotency-key', required=True, help='Generate and retain before submission. Same key/body recovers this job.')
@click.option('--lose-reply', is_flag=True, help='MOCK injection: retain the job but hide the submission reply.')
@click.pass_context
def submit(ctx, project, revision, kind, idempotency_key, lose_reply):
    """Request a private render pinned to a saved revision.

    A later edit does not retarget this job. Retry the same key/revision/kind
    after an uncertain reply or credential renewal. New keys mean new work.
    """
    authorize(ctx)
    project, revision = identity(project), identity(revision)
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', idempotency_key):
        fail('invalid_request', 'Use a 1–128 character alphanumeric, underscore or dash key.')
    data = load(ctx)
    request_id = 'mock-owner:render:' + idempotency_key
    payload = {'project_id': project, 'revision_id': revision, 'kind': kind}
    previous = data['requests'].get(request_id)
    if previous and previous['payload'] != payload:
        fail('idempotency_conflict', 'Recover the original body/key; use a new key only for deliberately different work.')
    job_id = str(uuid5(NAMESPACE_URL, request_id))
    if not previous:
        data['jobs'][job_id] = {'id': job_id, 'project_id': project, **payload,
                               'state': 'queued', 'phase': 'queued', 'attempt_number': 0,
                               'artifact_id': None, 'failure_code': None}
        data['requests'][request_id] = {'payload': payload, 'job_id': job_id}
        save(ctx, data)
    if lose_reply:
        fail('submission_unconfirmed', 'Retry the identical submission with this key; do not assume rollback.',
             idempotency_key=idempotency_key, request=payload, project_id=project)
    emit(status='accepted', job=data['jobs'][job_id], idempotency_key=idempotency_key,
         replayed=bool(previous), hint=f'renders show {project} {job_id}')


@renders.command()
@click.argument('project')
@click.argument('job_id')
@click.pass_context
def show(ctx, project, job_id):
    """Read this same job after reconnecting; never executes work."""
    job = resource(ctx, 'jobs', project, job_id)
    hint = (f"artifacts show {project} {job['artifact_id']}; "
            f"artifacts download {project} {job['artifact_id']} --out NEW_FILE"
            if job['artifact_id'] else f'renders show {project} {job_id}')
    emit(job=job, hint=hint)


@renders.command()
@click.argument('project')
@click.argument('job_id')
@click.pass_context
def cancel(ctx, project, job_id):
    """Cancel unfinished work; a terminal job stays terminal.

    Production must distinguish cancellation requested from terminal cleanup.
    The mock has no worker and transitions queued work directly to cancelled.
    """
    job = resource(ctx, 'jobs', project, job_id)
    if job['state'] not in ('succeeded', 'failed', 'cancelled'):
        job.update(state='cancelled', phase='cancelled')
        data = load(ctx)
        data['jobs'][job['id']] = job
        save(ctx, data)
    emit(status='cancelled' if job['state'] == 'cancelled' else 'terminal', job=job)


@cli.group()
def artifacts():
    """Discover exact verified-result identities, distinct from candidate IDs."""


@artifacts.command('list')
@click.argument('project')
@click.option('--revision', help='Filter by exact revision; older artifacts stay bound to their original edit.')
@click.pass_context
def list_artifacts(ctx, project, revision):
    """List private artifact metadata; does not make outputs current or shareable."""
    authorize(ctx)
    project = identity(project)
    revision = identity(revision) if revision else None
    emit(items=[a for a in load(ctx)['artifacts'].values()
                if a['project_id'] == project and (not revision or a['revision_id'] == revision)],
         next_cursor=None)


@artifacts.command('show')
@click.argument('project')
@click.argument('artifact_id')
@click.pass_context
def show_artifact(ctx, project, artifact_id):
    """Read exact retained metadata; unavailable media is never rendered again."""
    emit(artifact=resource(ctx, 'artifacts', project, artifact_id),
         hint=f'artifacts download {project} {artifact_id} --out NEW_FILE')


@artifacts.command()
@click.argument('project')
@click.argument('artifact_id')
@click.option('--out', required=True, type=click.Path(path_type=Path), help='Intended new destination. This mock saves no file.')
@click.pass_context
def download(ctx, project, artifact_id, out):
    """Preview authorized private delivery; MOCK DOES NOT DOWNLOAD.

    Production saves without overwrite and verifies byte size/SHA-256 before
    reporting out. Refreshing a grant never schedules processing. An expired
    artifact fails explicitly; it cannot silently rerender.
    """
    artifact = resource(ctx, 'artifacts', project, artifact_id)
    if not artifact['available']:
        fail('artifact_unavailable', 'Retained metadata exists but media is unavailable. Reopening does not rerender.')
    emit(artifact=artifact, would_save_to=str(out.resolve()), file_saved=False,
         hint='Mock only. No private download URL or real media exists.')


@cli.command()
@click.argument('project')
@click.argument('artifact_id')
@click.option('--at-ms', type=click.IntRange(min=0), required=True, help='Result time in this artifact, not source time.')
@click.pass_context
def review(ctx, project, artifact_id, at_ms):
    """Show an artifact-bound review target; no perception/approval is recorded."""
    artifact = resource(ctx, 'artifacts', project, artifact_id)
    if not artifact['available']:
        fail('artifact_unavailable', 'Do not transfer review to a different artifact.')
    if at_ms >= artifact['media']['duration_ms']:
        fail('timestamp_out_of_range', 'Use result time before artifact duration.')
    emit(review_target={k: artifact[k] for k in ('project_id', 'revision_id', 'job_id', 'sha256', 'kind')}
         | {'artifact_id': artifact['id'], 'at_ms': at_ms, 'time_space': 'result'},
         review_recorded=False, agent_perception=False, human_display=False)


@cli.group()
def fixture():
    """MOCK controls: inject premises; never call these production operations."""


@fixture.command()
@click.argument('project')
@click.argument('job_id')
@click.pass_context
def complete(ctx, project, job_id):
    """Assume verification/promotion succeeded; creates only synthetic metadata."""
    job = resource(ctx, 'jobs', project, job_id)
    if job['state'] in ('cancelled', 'failed'):
        fail('terminal_job', 'A stale worker cannot publish a terminal job.')
    data = load(ctx)
    artifact_id = str(uuid5(NAMESPACE_URL, job['id'] + ':artifact'))
    artifact = data['artifacts'].get(artifact_id) or {
        'id': artifact_id, 'project_id': job['project_id'], 'revision_id': job['revision_id'],
        'job_id': job['id'], 'kind': job['kind'], 'available': True,
        'verification': 'synthetic_fixture_only', 'sha256': ('a' if job['kind'] == 'preview' else 'd')*64, 'size_bytes': 12345,
        'runtime_manifest_sha256': 'b'*64, 'output_profile_sha256': ('c' if job['kind'] == 'preview' else 'e')*64,
        'media': {'duration_ms': 10000, 'width': 854 if job['kind'] == 'preview' else 1920,
                  'height': 480 if job['kind'] == 'preview' else 1080,
                  'container': 'mp4', 'video_codec': 'avc', 'audio_codec': 'aac'}}
    job.update(state='succeeded', phase='succeeded', attempt_number=1, artifact_id=artifact_id)
    data['jobs'][job['id']] = job
    data['artifacts'][artifact_id] = artifact
    save(ctx, data)
    emit(job=job, artifact=artifact)


@fixture.command()
@click.argument('project')
@click.argument('artifact_id')
@click.pass_context
def expire(ctx, project, artifact_id):
    """Assume media retention ended; keep the job/artifact identity."""
    artifact = resource(ctx, 'artifacts', project, artifact_id)
    data = load(ctx)
    artifact['available'] = False
    data['artifacts'][artifact['id']] = artifact
    save(ctx, data)
    emit(artifact=artifact)


if __name__ == '__main__':
    cli()
