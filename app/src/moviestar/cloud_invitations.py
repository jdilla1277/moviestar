"""Pending recipient invitations across projects and account assets."""
import json
import re
from datetime import datetime
from urllib.parse import urlencode
import click
from moviestar import cloud as service


def _entry(raw):
    if not isinstance(raw, dict) or raw.get('kind') not in ('asset', 'project') or raw.get('state') != 'pending':
        return None
    if any(not isinstance(raw.get(k), str) or not service._UUID.fullmatch(raw[k]) for k in ('id', 'resource_id')):
        return None
    if (not isinstance(raw.get('title'), str) or not isinstance(raw.get('shared_by'), str) or not raw['shared_by']
            or not isinstance(raw.get('role'), str) or not raw['role']
            or not isinstance(raw.get('permissions'), list) or not all(isinstance(v, str) for v in raw['permissions'])
            or raw.get('includes_assets') is not None and type(raw['includes_assets']) is not bool):
        return None
    for field in ('created_at', 'expires_at'):
        try:
            if not isinstance(raw.get(field), str) or datetime.fromisoformat(raw[field].replace('Z', '+00:00')).tzinfo is None:
                return None
        except ValueError:
            return None
    path = f'/account/received-invitations?kind={raw["kind"]}&invitation={raw["id"]}'
    if raw.get('review_path') != path:
        return None
    return {k: raw[k] for k in ('id', 'kind', 'resource_id', 'title', 'shared_by', 'state', 'role', 'permissions', 'created_at', 'expires_at', 'review_path')} | {'includes_assets': raw.get('includes_assets')}


def register_invitations(cloud):
    @cloud.group('invitations')
    def invitations():
        """Discover invitations received by your verified account."""

    @invitations.command('list')
    @click.option('--kind', type=click.Choice(['all', 'asset', 'project']), default='all', show_default=True)
    @click.option('--limit', type=click.IntRange(1, 50), default=20, show_default=True)
    @click.option('--cursor', help='Unchanged next_cursor from the same collection/filter.')
    def list_invitations(kind, limit, cursor):
        """List pending asset/project invitations without finding an email.

        Use invitations review and accept to finish here; review_url is a
        browser fallback. Listing sends no email, accepts nothing and grants no access.
        Accepted invitations leave this list and appear in shared collections.
        """
        command = 'cloud invitations list'
        if cursor is not None and (len(cursor) > 128 or not service._CURSOR.fullmatch(cursor)):
            service._fail(command, 'invalid_request', 'Follow the unchanged next_cursor with the same --kind filter.')
        endpoint, credential = service._connection(command)
        cap = service._capabilities(command, endpoint, credential).get('received_invitations')
        if (not isinstance(cap, dict) or cap.get('contract_version') != 'received-invitations-v1'
                or cap.get('kinds') != ['asset', 'project'] or cap.get('path') != '/api/v1/account/cloud/invitations'):
            service._fail(command, 'unsupported_operation', 'This service does not support received invitations yet. No invitation was accepted.')
        status, body = service._request(endpoint + '/invitations?' + urlencode({'kind': kind, 'limit': limit, **({'cursor': cursor} if cursor else {})}), credential)
        if status != 200:
            service._failure(command, status, body)
        raw, next_cursor = body.get('items'), body.get('next_cursor', 'missing')
        items = [_entry(i) for i in raw] if isinstance(raw, list) and len(raw) <= limit else None
        if (body.get('contract_version') != 'received-invitations-v1' or items is None or any(i is None or kind != 'all' and i['kind'] != kind for i in items)
                or len({(i['kind'], i['id'].lower()) for i in items}) != len(items)
                or next_cursor is not None and (not isinstance(next_cursor, str) or len(next_cursor) > 128 or not service._CURSOR.fullmatch(next_cursor))):
            service._fail(command, 'invalid_response', 'Could not validate pending invitations. No invitation was accepted.')
        for item in items:
            item['review_url'] = endpoint.removesuffix('/api/v1/account/cloud') + item['review_path']
        click.echo(json.dumps({'command': command, 'contract_version': 'received-invitations-v1', 'items': items, 'next_cursor': next_cursor,
                               'hint': 'Use cloud invitations review, then accept with its revision; review_url is a browser fallback. Listing does not grant access.'}, indent=2))

    def native_action(command, kind, invitation_id, revision=None):
        if not service._UUID.fullmatch(invitation_id):
            service._fail(command, 'invalid_request', 'Use the invitation ID and kind from the received inbox.')
        endpoint, credential = service._connection(command)
        cap = service._capabilities(command, endpoint, credential).get('received_invitations', {})
        actions = cap.get('native_actions') if isinstance(cap, dict) else None
        if actions != {'contract_version': 'received-invitation-actions-v1', 'path': '/api/v1/account/cloud/invitations/:kind/:id'}:
            service._fail(command, 'unsupported_operation', 'This service does not support native invitation actions. Use the inbox review_url in your browser.')
        url = f'{endpoint}/invitations/{kind}/{invitation_id}'
        recovery_context = {'invitation_id': invitation_id, 'kind': kind, **({'expected_revision': revision} if revision else {})}
        status, body = service._request(url + ('/accept' if revision else ''), credential, **({'payload': {'expected_revision': revision}} if revision else {}))
        if status != 200:
            if revision and (status == 0 or status >= 500):
                service._fail(command, 'acceptance_unconfirmed', 'The reply is uncertain. Review this invitation to recover, or retry with the same expected_revision. Do not send another invitation.', **recovery_context)
            code = body.get('error', {}).get('code', '')
            if code == 'invitation_changed':
                service._fail(command, code, 'Review the invitation again and explicitly approve its current permissions before accepting.')
            service._failure(command, status, body)
        allowed = ('accepted', 'already_accepted') if revision else ('acceptance_required', 'accepted', 'already_accepted')
        obj = body.get(kind)
        permissions = body.get('permissions')
        if (body.get('status') not in allowed or body.get('kind') != kind or body.get('id') != invitation_id
                or not isinstance(body.get('revision'), str) or not re.fullmatch(r'[a-f0-9]{64}', body['revision'])
                or not isinstance(body.get('role'), str) or not body['role']
                or not isinstance(permissions, list) or not all(isinstance(v, str) for v in permissions)
                or not isinstance(obj, dict) or not isinstance(obj.get('id'), str) or not service._UUID.fullmatch(obj['id'])
                or kind == 'asset' and (not isinstance(obj.get('filename'), str) or 'display_name' in obj and not isinstance(obj['display_name'], str))
                or kind == 'project' and (not isinstance(obj.get('title'), str) or type(body.get('includes_assets')) is not bool)):
            service._fail(command, 'acceptance_unconfirmed' if revision else 'invalid_response', 'Review the same invitation to check current access. Do not send another invitation.', **recovery_context)
        receipt = {key: body[key] for key in ('status', 'kind', 'id', 'revision', 'role', 'permissions')}
        receipt[kind] = {key: obj[key] for key in ('id', 'filename', 'display_name') if key in obj} if kind == 'asset' else {'id': obj['id'], 'title': obj['title']}
        if kind == 'project':
            receipt['includes_assets'] = body['includes_assets']
        click.echo(json.dumps({'command': command, 'status': body['status'], 'invitation': receipt,
                               'hint': 'Use the reviewed revision to explicitly accept; listing and review do not grant access.' if not revision else 'Access is accepted. The invitation leaves pending and appears in shared collections.'}, indent=2))

    @invitations.command('review')
    @click.argument('invitation_id')
    @click.option('--kind', type=click.Choice(['asset', 'project']), required=True)
    def review_invitation(invitation_id, kind):
        """Review the current recipient, object role and permissions without accepting.

        Retain revision from this result for an explicit accept. No email is sent.
        """
        native_action('cloud invitations review', kind, invitation_id)

    @invitations.command('accept')
    @click.argument('invitation_id')
    @click.option('--kind', type=click.Choice(['asset', 'project']), required=True)
    @click.option('--expected-revision', required=True, help='Exact revision from review; preserve it on retries.')
    def accept_invitation(invitation_id, kind, expected_revision):
        """Explicitly accept reviewed permissions as the connected recipient.

        Review first, then pass its exact revision. Changed invitations require
        a fresh review and approval. Retry the same command after an uncertain
        response; an already accepted invitation returns existing access.
        """
        if not re.fullmatch(r'[a-f0-9]{64}', expected_revision):
            service._fail('cloud invitations accept', 'invalid_request', 'Use the exact revision from cloud invitations review.')
        native_action('cloud invitations accept', kind, invitation_id, expected_revision)
