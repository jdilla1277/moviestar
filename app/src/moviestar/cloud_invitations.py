"""Pending recipient invitations across projects and account assets."""
import json
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

        Open an item's review_url while signed in to review and explicitly
        accept. Listing sends no email, accepts nothing and grants no access.
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
                               'hint': 'Open review_url to review and accept in your signed-in browser. Listing does not grant access.'}, indent=2))
