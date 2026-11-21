"""Remote initial-edit reads and engine-backed trim dry runs; never save."""
from __future__ import annotations

import json
from pathlib import Path

import click

_CONTRACT = 'cloud-editing-v1'
_HINTS = {
    'invalid_request': 'Use a project UUID and a JSON request with contract_version, expected_base_revision and operation only (at most 4096 bytes).',
    'edits_unavailable': 'Inspect cloud edits capabilities; this service has no available edit validation bridge.',
    'invalid_response': 'The service returned an incompatible edit response. Update the CLI or retry later.',
    'cloud_unavailable': 'The read or dry run could not be confirmed. No save was requested; retry the same read or validation.',
}


def _fail(code, message=None):
    click.echo(json.dumps({'error': {'code': code, 'message': message or _HINTS[code], 'details': {},
                                   'retryable': code in ('edits_unavailable', 'cloud_unavailable')}}, indent=2))
    raise click.exceptions.Exit(1)


def _capability(command, endpoint, credential, action):
    from moviestar.cloud import _request, _CONTRACT as projects_contract
    status, body = _request(endpoint + '/capabilities', credential)
    if status != 200:
        _service_error(body)
    if (body.get('contract_version') != projects_contract or body.get('project_access') != 'owner_only'
            or not isinstance(body.get('operations'), list) or not all(isinstance(x, str) for x in body['operations'])):
        _fail('invalid_response')
    value = body.get('editing')
    if not isinstance(value, dict) or value.get('contract_version') != _CONTRACT:
        _fail('edits_unavailable')
    if action and value.get(action + '_available') is not True:
        _fail('edits_unavailable')
    return value


def _service_error(body):
    error = body.get('error')
    allowed = {'edits_unavailable', 'personal_cloud_unavailable', 'engine_unavailable', 'cloud_unavailable', 'credential_invalid',
               'account_setup_required', 'account_consent_required', 'not_found', 'invalid_request', 'request_too_large',
               'invalid_edit', 'unsupported_feature', 'unsupported_edit_state', 'unsupported_edit_inputs',
               'edit_limit_exceeded', 'source_metadata_unavailable', 'revision_conflict', 'edit_inputs_changed'}
    if (isinstance(error, dict) and set(error) == {'code','message','details','retryable'} and error['code'] in allowed
            and isinstance(error['message'], str) and isinstance(error['details'], dict) and type(error['retryable']) is bool):
        # Preserve the service's common error envelope across CLI/API/MCP.
        click.echo(json.dumps({**({'request_id':body['request_id']} if isinstance(body.get('request_id'),str) else {}), 'error':error}, indent=2))
        raise click.exceptions.Exit(1)
    _fail('cloud_unavailable')


def _response(status, body, project, validate):
    from moviestar.cloud import _UUID
    if status != 200:
        _service_error(body)
    current = body.get('current_revision', 'missing')
    saved = isinstance(current, str) and bool(_UUID.fullmatch(current))
    if (body.get('contract_version') != _CONTRACT or body.get('project_id') != project
            or (current is not None and not saved)
            or body.get('status') != ('would_apply' if validate else 'opened' if saved else 'no_saved_edit')):
        _fail('invalid_response')
    if validate:
        depth = body.get('current_undo_remaining')
        if type(body.get('changes')) is not bool or type(depth) is not int or depth < 0 or (not saved and depth != 0) or not isinstance(body.get('projected_resolved'),dict) or 'revision' in body:
            _fail('invalid_response')
    elif saved:
        revision = body.get('revision')
        if not isinstance(revision, dict) or revision.get('id') != current or not isinstance(revision.get('spec'), dict):
            _fail('invalid_response')
    elif body.get('revision', 'missing') is not None or 'baseline' not in body:
        _fail('invalid_response')
    click.echo(json.dumps(body, indent=2))


def register_edits(cloud):
    @cloud.group('edits')
    def edits():
        """Read a Cloud edit and validate supported changes without saving.

        Owned personal projects with ready owned video references only. An
        unsaved baseline is labelled explicitly; it is not a saved revision.
        Saved audio mixes support gain/mute dry runs. Apply, history, undo and
        rendering are not available; an existing saved revision is required.
        These commands do not select or modify a local project.
        """

    @edits.command('capabilities')
    def capabilities():
        """Read supported edit state, operation and availability limits."""
        from moviestar.cloud import _connection
        endpoint, credential = _connection('cloud edits capabilities')
        click.echo(json.dumps(_capability('cloud edits capabilities',endpoint,credential,None),indent=2))

    def run(project, request=None):
        from moviestar.cloud import _connection, _request, _UUID
        if not _UUID.fullmatch(project):
            _fail('invalid_request')
        project = project.lower()
        command = 'cloud edits validate' if request is not None else 'cloud edits show'
        endpoint, credential = _connection(command)
        _capability(command,endpoint,credential,'validation' if request is not None else 'read')
        status, body = _request(endpoint+'/projects/'+project+'/edit'+('/validate' if request is not None else ''),credential,
                                **({'payload':request} if request is not None else {}))
        _response(status,body,project,request is not None)

    @edits.command('show')
    @click.argument('project_id')
    def show(project_id):
        """Read PROJECT_ID's timeline or authoritative saved mix.

        no_saved_edit and null revision mean nothing has been saved. Multiple
        sources do not imply a composition or dialogue/camera selection.
        opened returns revision.spec and revision.resolved.audio_mix/routing for
        a saved mix; reading does not create or update a revision.
        """
        run(project_id)

    @edits.command('validate')
    @click.argument('project_id')
    @click.option('--request','request_file',required=True,type=click.Path(path_type=Path),help='JSON body, at most 4096 bytes; use source IDs from edits show.')
    def validate(project_id,request_file):
        """Check a proposed trim or saved-mix gain/mute without saving.

        Body: {"contract_version":"cloud-editing-v1",
        "expected_base_revision":null,"operation":{"type":"trim",
        "source_id":"ID_FROM_SHOW","time_space":"source_result",
        "from_ms":10000,"to_ms":40000}}.

        Times are integer milliseconds on the edited source clock, initially
        the full source. projected_resolved is a candidate; current_undo_remaining
        describes the unchanged current edit. No revision or Activity is saved.
        For a saved mix, use its current_revision as expected_base_revision and
        operation {"type":"audio_source","gain_db":-6} or muted:true/false.
        Source controls affect routed dialogue, not camera selection; omitted
        controls, tracks, fades and timing are preserved. Inspect candidate
        audio_mix and routing, then show again to confirm unchanged saved state.
        """
        try:
            with request_file.open('rb') as stream:
                raw = stream.read(4097)
            if len(raw)>4096:
                raise ValueError()
            request=json.loads(raw.decode('utf8'))
            if (not isinstance(request,dict) or set(request)!={'contract_version','expected_base_revision','operation'}
                    or request['contract_version']!=_CONTRACT or not isinstance(request['operation'],dict)
                    or (request['expected_base_revision'] is not None and not isinstance(request['expected_base_revision'],str))):
                raise ValueError()
        except (OSError,ValueError,UnicodeError):
            _fail('invalid_request')
        run(project_id,request)
