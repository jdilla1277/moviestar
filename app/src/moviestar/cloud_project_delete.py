"""Explicit owner-only remote project deletion, preserving account originals."""
import json
import click
from moviestar import cloud as service


def register_project_delete(projects):
    @projects.command('delete')
    @click.argument('project_id')
    @click.option('--confirm', is_flag=True, help='Explicitly confirm deleting this Cloud project and ending its shared access. Originals remain in your account.')
    def delete_project(project_id, confirm):
        """Delete PROJECT_ID and its memberships, preserving account originals.

        Requires --confirm. Retry the same ID after uncertainty; an owned
        tombstone returns the same cleanup receipt. Cleanup may still be pending.
        Does not delete local files, other projects, or account-owned assets.
        """
        command = 'cloud projects delete'
        if not service._UUID.fullmatch(project_id):
            service._fail(command, 'invalid_request', 'Use the project UUID returned by the service.')
        if not confirm:
            service._fail(command, 'confirmation_required', 'Review the project ID, then pass --confirm to delete it and end its shared access. Account originals and other project memberships remain intact.', project_id=project_id.lower())
        endpoint, credential = service._connection(command)
        service._require(command, service._capabilities(command, endpoint, credential), 'projects:delete')
        status, body = service._request(endpoint+'/projects/'+project_id.lower(), credential, method='DELETE', payload={'confirm_project_id': project_id.lower()})
        fields = {'project_id': project_id.lower()}
        if 400 <= status < 500:
            service._failure(command, status, body, deletion_outcome='rejected', **fields)
        deletion = body.get('deletion')
        valid = (status == 202 and body.get('status') == 'deleted' and isinstance(body.get('project_id'), str)
                 and body['project_id'].lower() == project_id.lower() and body.get('original_assets_preserved') is True
                 and isinstance(deletion, dict) and isinstance(deletion.get('id'), str) and service._UUID.fullmatch(deletion['id'])
                 and deletion.get('state') in ('pending', 'running', 'succeeded', 'failed'))
        if not valid:
            service._fail(command, 'deletion_unconfirmed', 'Deletion may have succeeded. Retry this same project ID with --confirm to check its receipt. No automatic retry was sent.', deletion_outcome='unknown', **fields)
        click.echo(json.dumps({'command': command, 'status': 'deleted', **fields, 'deletion': {'id': deletion['id'], 'state': deletion['state']},
                               'original_assets_preserved': True, 'hint': 'Project access has ended. Legacy project-media cleanup is reported separately; account originals and their other project memberships remain intact.'}, indent=2))
