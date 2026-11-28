"""Remote initial-edit reads and engine-backed trim dry runs; never save."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
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


def _service_error(body, **extra):
    error = body.get('error')
    allowed = {'edits_unavailable', 'personal_cloud_unavailable', 'engine_unavailable', 'cloud_unavailable', 'credential_invalid',
               'account_setup_required', 'account_consent_required', 'not_found', 'invalid_request', 'request_too_large',
               'invalid_edit', 'unsupported_feature', 'unsupported_edit_state', 'unsupported_edit_inputs',
               'edit_limit_exceeded', 'source_metadata_unavailable', 'revision_conflict', 'edit_inputs_changed', 'nothing_to_undo', 'idempotency_conflict', 'capacity_exceeded', 'edit_authority_withdrawn', 'engine_version_unavailable'}
    if (isinstance(error, dict) and set(error) == {'code','message','details','retryable'} and error['code'] in allowed
            and isinstance(error['message'], str) and isinstance(error['details'], dict) and type(error['retryable']) is bool):
        # Preserve the service's common error envelope across CLI/API/MCP.
        click.echo(json.dumps({**({'request_id':body['request_id']} if isinstance(body.get('request_id'),str) else {}), 'error':error,**extra}, indent=2))
        raise click.exceptions.Exit(1)
    _fail('cloud_unavailable')


def _saved_mix_read_valid(revision):
    """Check read evidence, without resolving audio or inventing defaults."""
    try:
        spec, resolved = revision['spec'], revision['resolved']
        saved, mix = spec['audio_mix'], resolved['audio_mix']
        controls = mix['source_audio']
        if (controls != saved['source_audio'] or any(type(c['muted']) is not bool
                or any(type(c[k]) not in (int, float) or not math.isfinite(c[k])
                       for k in ('gain_db', 'fade_in', 'fade_out'))
                for c in (controls, saved['source_audio']))):
            return False
        tracks, original = mix['tracks'], saved['tracks']
        identities = [t['id'] for t in tracks]
        available = resolved['source_audio_availability']
        return (isinstance(tracks, list) and isinstance(original, list)
                and all(isinstance(i, str) and i for i in identities)
                and len(set(identities)) == len(identities)
                and identities == [t['id'] for t in original]
                and type(resolved['track_count']) is int and resolved['track_count'] == len(tracks)
                and isinstance(available, dict)
                and all(v in ('stream_reported', 'no_stream_in_metadata', 'unreported') for v in available.values())
                and isinstance(resolved['routing'], list)
                and all(r['source_id'] is None or r['source_id'] in available for r in resolved['routing'])
                and resolved['loudness'] is None and resolved['loudness_status'] == 'not_measured')
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _audio_projection_valid(body, request):
    try:
        spec=body['candidate']['spec'];resolved=body['projected_resolved']
        changes=body['changes'];depth=body['current_undo_remaining']
        return (body['normalized_request']==request and body['current_revision']==request['expected_base_revision']
                and set(body['effects'])=={'saved_revisions','activity_events','processing_jobs','customer_usage'}
                and all(type(v)is int and v==0 for v in body['effects'].values())
                and _saved_mix_read_valid({'spec':spec,'resolved':resolved})
                and all(type(spec['audio_mix']['source_audio'][k]) is type(v) and spec['audio_mix']['source_audio'][k]==v
                        or type(v)in(int,float) and type(spec['audio_mix']['source_audio'][k])in(int,float) and spec['audio_mix']['source_audio'][k]==v
                        for k,v in request['operation'].items() if k!='type')
                and body['changed_fields']==(['audio_mix']if changes else [])
                and type(resolved['undo_remaining'])is int and resolved['undo_remaining']==depth+int(changes)
                and isinstance(spec['revisions'],list) and len(spec['revisions'])==depth+int(changes))
    except (KeyError,TypeError,ValueError):return False


def _response(status, body, project, validate, selected=None, request=None):
    from moviestar.cloud import _UUID
    if status != 200:
        _service_error(body)
    current = body.get('current_revision', 'missing')
    saved = isinstance(current, str) and bool(_UUID.fullmatch(current))
    if (body.get('contract_version') != _CONTRACT or body.get('project_id') != project
            or (current is not None and not saved)
            or body.get('status') != ('would_apply' if validate else 'opened' if saved else 'no_saved_edit')):
        _fail('invalid_response')
    if validate and request and request['operation'].get('type')=='audio_source' and not _audio_projection_valid(body,request):
        _fail('invalid_response')
    if validate:
        depth = body.get('current_undo_remaining')
        if type(body.get('changes')) is not bool or type(depth) is not int or depth < 0 or (not saved and depth != 0) or not isinstance(body.get('projected_resolved'),dict) or 'revision' in body:
            _fail('invalid_response')
    elif saved:
        revision = body.get('revision')
        if not isinstance(revision, dict) or revision.get('id') != (selected or current) or not _saved_mix_read_valid(revision):
            _fail('invalid_response')
    elif body.get('revision', 'missing') is not None or 'baseline' not in body:
        _fail('invalid_response')
    click.echo(json.dumps(body, indent=2))


def _confirmed_mutation(body, project, key, proposal):
    """Do not acknowledge/forget a write on a partial or unrelated HTTP 200."""
    from moviestar.cloud import _UUID
    revision, current = body.get('revision'), body.get('current_revision')
    status, proof, effects = body.get('status'), body.get('verification'), body.get('effects')
    normalized = {**proposal, 'expected_base_revision': (proposal['expected_base_revision'].lower()
                  if isinstance(proposal['expected_base_revision'],str) else None)}
    if ('revision' not in body or 'current_revision' not in body
        or body.get('contract_version') != _CONTRACT or body.get('project_id') != project
        or status not in ('committed','unchanged') or body.get('idempotency_key') != key
        or type(body.get('replayed')) is not bool or body.get('changes') is not (status=='committed')
        or body.get('normalized_request') != normalized
        or not isinstance(body.get('request_sha256'),str) or not re.fullmatch(r'[a-f0-9]{64}',body['request_sha256'])
        or body.get('canonical_format') != 'postgres-jsonb-text-v1'
        or (current is not None and (not isinstance(current,str) or not _UUID.fullmatch(current)))
        or not isinstance(proof,dict) or proof.get('format') != 'cloud-edit-state-proof-1'
        or proof.get('saved_state') != ('read_back_match' if status=='committed' else 'unchanged')
        or not isinstance(effects,dict) or set(effects)!={'saved_revisions','activity_events','processing_jobs','customer_usage'}):
        return False
    count = 1 if status=='committed' and not body['replayed'] else 0
    if any(type(effects[k]) is not int or effects[k] != v for k,v in
           {'saved_revisions':count,'activity_events':count,'processing_jobs':0,'customer_usage':0}.items()):
        return False
    if revision is None:
        return status=='unchanged'
    return (isinstance(revision,dict) and isinstance(revision.get('id'),str) and bool(_UUID.fullmatch(revision['id']))
            and isinstance(revision.get('spec'),dict) and isinstance(revision.get('resolved'),dict)
            and (body['replayed'] or revision['id']==current))


def register_edits(cloud):
    @cloud.group('edits')
    def edits():
        """Read, validate, save and undo a Cloud trim.

        Owned personal projects with ready owned video references only. An
        unsaved baseline is labelled explicitly; it is not a saved revision.
        Saved audio mixes support gain/mute dry runs. Use apply for one-source trim/undo and show --revision for exact older state.
        Saved instructions are verified by read-back; rendering is separate.
        These commands do not select or modify a local project.
        """

    @edits.command('capabilities')
    def capabilities():
        """Read supported edit state, operation and availability limits."""
        from moviestar.cloud import _connection
        endpoint, credential = _connection('cloud edits capabilities')
        click.echo(json.dumps(_capability('cloud edits capabilities',endpoint,credential,None),indent=2))

    def run(project, request=None, selected=None):
        from moviestar.cloud import _connection, _request, _UUID
        if not _UUID.fullmatch(project):
            _fail('invalid_request')
        project = project.lower()
        command = 'cloud edits validate' if request is not None else 'cloud edits show'
        endpoint, credential = _connection(command)
        _capability(command,endpoint,credential,'validation' if request is not None else 'read')
        status, body = _request(endpoint+'/projects/'+project+('/revisions/'+selected if selected else '/edit'+('/validate' if request is not None else '')),credential,
                                **({'payload':request} if request is not None else {}))
        _response(status,body,project,request is not None,selected,request)

    @edits.command('show')
    @click.argument('project_id')
    @click.option('--revision',help='Read this exact immutable version; current_revision remains the current pointer.')
    def show(project_id,revision):
        """Read PROJECT_ID's timeline or authoritative saved mix.

        no_saved_edit and null revision mean nothing has been saved. Multiple
        sources do not imply a composition or dialogue/camera selection.
        opened returns revision.spec and revision.resolved.audio_mix/routing for
        a saved mix; reading does not create or update a revision.
        Read source_audio.gain_db and muted for saved dialogue controls; routing
        identifies its source and result-time ranges in milliseconds.
        fade_in/fade_out are seconds. Join source_id through
        revision.dependency_manifest to separate asset_id and reference_id.
        source_audio_availability
        reports stream metadata, not proof of audible speech. Loudness is not
        measured by this read. --revision opens old settings without moving the
        current pointer. No media is downloaded or analyzed.
        """
        from moviestar.cloud import _UUID
        if revision and not _UUID.fullmatch(revision):_fail('invalid_request')
        run(project_id,selected=revision.lower() if revision else None)

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
        saved_candidate means derived from saved state, not saved itself.
        would_apply with changes:false is a no-op: no extra projected undo entry.
        For a saved mix, use its current_revision as expected_base_revision and
        operation {"type":"audio_source","gain_db":-6} or muted:true/false.
        Source controls affect routed dialogue, not camera selection; omitted
        controls, tracks, fades and timing are preserved. Inspect candidate
        audio_mix and routing, then show again to confirm unchanged saved state.
        """
        run(project_id,_read_request(request_file))

    @edits.command('apply')
    @click.argument('project_id')
    @click.option('--request','request_file',type=click.Path(path_type=Path),help='Exact cloud-editing-v1 body; trim or undo only.')
    @click.option('--recover',is_flag=True,help='Resend the exact pending request and retained key after an uncertain response.')
    @click.option('--idempotency-key',help='Optional explicit retained key; normally generated and saved before sending.')
    @click.option('--abandon-pending',is_flag=True,help='Archive the pending key/body and stop automatic retry locally. Does not undo/cancel a possible server commit.')
    def apply(project_id,request_file,recover,idempotency_key,abandon_pending):
        """Save one trim, or undo one complete command, without rendering.

        Read and validate first. Use the current revision as expected_base_revision,
        or null before the first save. Undo body operation is {"type":"undo"}.
        A second trim uses the current edited source clock. On a lost response,
        run apply PROJECT_ID --recover; do not refresh the base or invent a key.
        A confirmed command reports saved ranges and exact revision. Its
        verification.output remains not_checked until a separate render check.
        Another normal invocation after success is a NEW command.
        If a rejected request cannot be corrected while pending, explicitly run
        apply PROJECT_ID --abandon-pending. This archives its exact body/key and
        stops local retries only; a previous server save is NOT cancelled or
        undone. Inspect the current edit, then submit a corrected request/new
        base as a new command. The returned recovery command can recover the
        archived original, after resolving any later pending command.
        """
        from moviestar.cloud import _connection,_request,_UUID
        from moviestar.cloud_creation_state import creation_request,CreationStateError,_read,_write
        if (not _UUID.fullmatch(project_id) or (idempotency_key is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',idempotency_key))
                or (abandon_pending and (recover or request_file is not None or idempotency_key is not None))
                or (recover and request_file is not None) or (not recover and not abandon_pending and request_file is None)):
            _fail('invalid_request')
        project=project_id.lower()
        proposal=None if recover or abandon_pending else _read_request(request_file)
        endpoint,credential=_connection('cloud edits apply')
        # Availability must not block recovery: the service checks receipts before
        # writer enrollment/engine availability and before new-write base checks.
        if not abandon_pending:
            _capability('cloud edits apply',endpoint,credential,None)
        try:
            with creation_request(endpoint,credential,project,idempotency_key,recovery_directory='cloud-edit-requests') as pending:
                if not pending.managed:
                    raise CreationStateError('An earlier edit still has a pending retry record. Run apply PROJECT_ID --recover with its original key, or deliberately use --abandon-pending to archive it before submitting a new command.')
                previous=_read(pending.path,project)
                if abandon_pending:
                    if not previous or previous['state']!='pending' or not isinstance(previous.get('request'),dict):
                        raise CreationStateError('No pending edit to abandon.')
                    # Archive durably before changing the active record. Never
                    # interpret a rejection as proof an earlier call rolled back.
                    try:directory=Path(tempfile.mkdtemp(prefix='abandoned-',dir=pending.path.parent))
                    except OSError as error:raise CreationStateError('Could not create a private archive; the pending request is unchanged.') from error
                    record_file=directory/'record.json';request_path=directory/'request.json'
                    _write(record_file,previous);_write(request_path,previous['request'])
                    # Persist the new archive directory's parent entry BEFORE
                    # marking the active request resolved (not just its files).
                    if os.name!='nt':
                        try:
                            fd=os.open(directory.parent,os.O_RDONLY)
                            try:os.fsync(fd)
                            finally:os.close(fd)
                        except OSError as error:raise CreationStateError('Could not durably archive the request; the pending record is unchanged.') from error
                    _write(pending.path,{**previous,'state':'resolved','abandoned':True,'archive_directory':str(directory)})
                    click.echo(json.dumps({'status':'pending_abandoned','project_id':project,'server_outcome':'unknown',
                        'message':'Stopped local retry only. This does not cancel or undo any server save. Read the current edit before submitting a new command.',
                        'recovery':{'idempotency_key':previous['idempotency_key'],'request_file':str(request_path),'record_file':str(record_file),
                            'command':['moviestar','cloud','edits','apply',project,'--request',str(request_path),'--idempotency-key',previous['idempotency_key']]}},indent=2))
                    return
                original=previous.get('request') if pending.recovering and previous else None
                if recover:
                    if not original:raise CreationStateError('No pending edit to recover.')
                    proposal=original
                elif original is not None and original!=proposal:
                    raise CreationStateError('A different edit already has a pending retry record. Run apply PROJECT_ID --recover before submitting another request, or explicitly archive it with --abandon-pending.')
                pending.record['request']=proposal
                pending.save()  # Includes exact body; fsync precedes network IO.
                status,body=_request(endpoint+'/projects/'+project+'/edit/mutations',credential,payload=proposal,key=pending.key)
                valid=status==200 and _confirmed_mutation(body,project,pending.key,proposal)
                if not valid:
                    if status in (400,401,403,404,409,413,422,429,503) and isinstance(body.get('error'),dict):
                        # Retain pending state on rejection too: retry may have
                        # discovered withdrawal after an earlier successful commit.
                        _service_error(body,recovery={'idempotency_key':pending.key,'request_retained':True,'command':['moviestar','cloud','edits','apply',project,'--recover']})
                    click.echo(json.dumps({'error':{'code':'mutation_unconfirmed','message':'Save outcome is unknown. Retry the exact retained request with --recover; do not change its base or key.',
                        'details':{'idempotency_key':pending.key,'recovery_command':['moviestar','cloud','edits','apply',project,'--recover']},'retryable':True}},indent=2))
                    raise click.exceptions.Exit(1)
                try:pending.resolve()
                except CreationStateError:
                    body={**body,'recovery_warning':'Saved result confirmed; local pending state remains. --recover safely replays this result.'}
                click.echo(json.dumps(body,indent=2))
        except CreationStateError as error:
            _fail('invalid_request',str(error))


def _read_request(request_file):
    try:
        with request_file.open('rb') as stream:raw=stream.read(4097)
        if len(raw)>4096:raise ValueError()
        request=json.loads(raw.decode('utf8'))
        if (not isinstance(request,dict) or set(request)!={'contract_version','expected_base_revision','operation'}
            or request['contract_version']!=_CONTRACT or not isinstance(request['operation'],dict)
            or (request['expected_base_revision'] is not None and not isinstance(request['expected_base_revision'],str))):raise ValueError()
        return request
    except (OSError,ValueError,UnicodeError):_fail('invalid_request')
