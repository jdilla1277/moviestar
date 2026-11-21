"""Recipient self-service leave; does not delete or reshare originals."""
import json
import click
from moviestar import cloud as service
from moviestar.cloud_creation_state import CreationBusy,CreationStateError,creation_request


def register_leave(cloud):
    for kind,group in [('asset',cloud.commands['assets']),('project',cloud.commands['projects'])]:
        def register(kind,group):
            @group.command('leave')
            @click.argument('resource_id')
            @click.option('--idempotency-key',help='Reuse this key after uncertainty; generated and saved when omitted.')
            def leave(resource_id,idempotency_key):
                """Leave your accepted share without deleting the object or others' access.

                For assets this ends direct shares only. Independent project access
                remains: leave that project to remove its path. For projects this
                ends your accepted project grants and inherited asset access through
                them. Independent asset/other-project access survives.
                Retry the same command after uncertainty to recover its saved key.
                """
                command=f'cloud {"assets" if kind=="asset" else "projects"} leave'
                if not service._UUID.fullmatch(resource_id) or idempotency_key is not None and not service._KEY.fullmatch(idempotency_key):
                    service._fail(command,'invalid_request','Use an asset/project ID and the original retry key.')
                resource_id=resource_id.lower()
                endpoint,credential=service._connection(command)
                capability=service._capabilities(command,endpoint,credential).get('recipient_leave')
                if not isinstance(capability,dict) or capability.get('contract_version')!='recipient-leave-v1' or capability.get('path')!='/api/v1/account/cloud/shared-access/:kind/:id/leave':
                    service._fail(command,'unsupported_operation','This service does not support recipient leave yet. No access was changed.')
                context=json.dumps([kind,resource_id],separators=(',',':'));fields={'kind':kind,'resource_id':resource_id}
                try:
                    with creation_request(endpoint,credential,context,idempotency_key,recovery_directory='cloud-recipient-leave-requests') as pending:
                        fields['idempotency_key']=pending.key;pending.save()
                        status,body=service._request(f'{endpoint}/shared-access/{kind}/{resource_id}/leave',credential,payload={},key=pending.key)
                        if status==0 or status>=500:
                            service._fail(command,'leave_unconfirmed','The reply is uncertain. Retry with this same key; no automatic retry was sent.',**fields)
                        if status!=200:
                            if 400<=status<500 and not pending.recovering:pending.resolve()
                            if body.get('error',{}).get('code')=='project_access_only':service._fail(command,'project_access_only','This asset is available through a project, with no direct share to leave. Leave that shared project to remove its access path.',**fields)
                            if body.get('error',{}).get('code')=='owner_cannot_leave':service._fail(command,'owner_cannot_leave','You own this object. Leave only applies to shares received from others.',**fields)
                            service._failure(command,status,body,**fields)
                        if (body.get('contract_version')!='recipient-leave-v1' or body.get('kind')!=kind or body.get('resource_id')!=resource_id or body.get('status') not in ('left','already_left')
                                or any(type(body.get(k)) is not bool for k in ('changed','remaining_access','replayed'))):
                            service._fail(command,'leave_unconfirmed','Could not confirm the leave receipt. Retry with this same key.',**fields)
                        try:pending.resolve()
                        except CreationStateError:fields['recovery_warning']='Leave confirmed, but local retry state could not be cleared. Preserve the returned key.'
                except CreationBusy:service._fail(command,'leave_in_progress','This leave request is already running. Wait and retry.',**fields)
                except CreationStateError:service._fail(command,'leave_state_unavailable','Could not read/save leave recovery state. Preserve the original key before repairing it.',**fields)
                click.echo(json.dumps({'command':command,**fields,**{k:body[k] for k in ('status','changed','remaining_access','replayed')},
                    'hint':('The original leave request is recorded. Access remains through another grant, possibly a newer invitation. Review current access before starting a new leave request.' if body['replayed'] else 'Direct share left. You still have access through a project. Leave that project to remove its path.') if body['remaining_access'] else 'You have left this share. The original and other recipients were preserved.'},indent=2))
        register(kind,group)
