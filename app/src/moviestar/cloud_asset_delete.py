"""Explicit owner-only deletion of a remote account original."""
import json
import click
from moviestar import cloud as service


def register_asset_delete(assets):
    @assets.command('delete')
    @click.argument('asset_id')
    @click.option('--confirm', is_flag=True, help='Permanently delete the original and all its project references and shared access. Projects and other assets stay.')
    def delete_asset(asset_id, confirm):
        """Delete an owned ASSET_ID everywhere it is referenced.

        Requires --confirm. Does not delete local files or someone else's asset.
        Retry the SAME ID after uncertainty; the deletion receipt survives normal
        reads returning not_found. Space is released only after verified cleanup.
        """
        from moviestar.cloud_assets import _connection
        command = 'cloud assets delete'
        if not service._UUID.fullmatch(asset_id):
            service._fail(command, 'invalid_request', 'Use the asset UUID returned by the service.')
        asset_id = asset_id.lower()
        if not confirm:
            service._fail(command, 'confirmation_required', 'Review this asset ID, then pass --confirm to permanently delete its original, references and shared access. Projects and other assets remain.', asset_id=asset_id)
        endpoint, credential = _connection(command, 'assets:delete')
        status, body = service._request(endpoint+'/assets/'+asset_id, credential, method='DELETE', payload={'confirm_asset_id': asset_id})
        if status == 409 and isinstance(body.get('error'), dict) and body['error'].get('code') == 'asset_not_ready':
            service._fail(command, 'asset_not_ready', 'Only ready originals can be deleted. Finish upload validation first, then retry the same asset ID.', deletion_outcome='rejected', asset_id=asset_id)
        if 400 <= status < 500:
            service._failure(command, status, body, deletion_outcome='rejected', asset_id=asset_id)
        deletion = body.get('deletion')
        valid = (status in (200, 202) and body.get('status') == 'deleted' and isinstance(body.get('asset_id'), str)
                 and body['asset_id'].lower() == asset_id and body.get('projects_preserved') is True
                 and type(body.get('references_removed')) is int and body['references_removed'] >= 0
                 and type(body.get('storage_released')) is bool and isinstance(deletion, dict)
                 and isinstance(deletion.get('id'), str) and service._UUID.fullmatch(deletion['id'])
                 and deletion.get('state') in ('pending', 'succeeded')
                 and body['storage_released'] == (deletion['state'] == 'succeeded')
                 and (status == 200) == body['storage_released'])
        if not valid:
            service._fail(command, 'deletion_unconfirmed', 'Deletion may have succeeded. Retry this SAME asset ID with --confirm. No automatic retry was sent.', deletion_outcome='unknown', asset_id=asset_id)
        click.echo(json.dumps({'command': command, 'status': 'deleted', 'asset_id': asset_id,
                               'deletion': {'id': deletion['id'], 'state': deletion['state']},
                               'references_removed': body['references_removed'], 'projects_preserved': True,
                               'storage_released': body['storage_released'],
                               'hint': 'The asset is unavailable and its references and shared access have ended. '+('Storage has been released.' if body['storage_released'] else 'Storage cleanup is pending; space remains charged until verified. Retry the same ID to check.')}, indent=2))
