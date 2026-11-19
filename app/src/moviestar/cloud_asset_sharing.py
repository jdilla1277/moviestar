"""Native owner-managed asset viewer invitations with durable retries."""
import json
import re
from datetime import datetime
from urllib.parse import urlencode

import click

from moviestar import cloud as service
from moviestar.cloud_creation_state import CreationBusy, CreationStateError, creation_request

_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9.-]+[.][A-Za-z]{2,63}$")
_HINTS = {
    'credential_invalid': service._HINTS['credential_invalid'],
    'account_setup_required': service._HINTS['account_setup_required'],
    'account_consent_required': service._HINTS['account_consent_required'],
    'account_action_required': 'Complete account setup and review current terms on the website.',
    'not_found': 'This asset or invitation is unavailable to the connected owner. Viewers cannot manage sharing.',
    'rate_limited': 'Invitation email limits were reached. Wait before retrying with the same request key.',
    'sending_paused': 'New invitation emails are paused. Existing access can still be listed or revoked.',
    'resend_not_ready': 'An email was recently requested or is still processing. Wait before retrying with the same key.',
    'grant_exists': 'This recipient already accepted access. No additional email was requested.',
    'invitation_revoked': 'This invitation was revoked. To deliberately share again, send a new invitation.',
    'asset_not_ready': 'Only a ready asset can be shared.',
    'idempotency_conflict': 'Use the original asset, recipient and request key; use a new key only for a deliberate new request.',
    'invalid_request': 'Check the documented asset/invitation ID, recipient, cursor and request key.',
    'invalid_cursor': 'Use the unchanged next_cursor from the previous access page.',
    'unavailable': 'Sharing is unavailable. Preserve the same request key and invitation ID when retrying.',
}


def _check(command, status, body, **fields):
    code = body.get('status')
    if status == 401:
        code = 'credential_invalid'
    safe = code if isinstance(code, str) and code in _HINTS else 'unavailable'
    service._fail(command, safe, _HINTS[safe], **fields)


def _supported(command, endpoint, credential, operation):
    sharing = service._capabilities(command, endpoint, credential).get('asset_sharing')
    management = sharing.get('management') if isinstance(sharing, dict) else None
    if (not isinstance(management, dict) or management.get('contract_version') != 'native-asset-sharing-v1'
            or not isinstance(management.get('operations'), list)
            or operation not in management['operations']):
        service._fail(command, 'unsupported_operation', 'Native asset sharing is not available on this service. No invitation was sent.')


def _invitation(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('id'), str) or not service._UUID.fullmatch(raw['id']):
        return None
    if (not isinstance(raw.get('email'), str) or len(raw['email']) > 254 or not _EMAIL.fullmatch(raw['email'])
            or raw.get('state') not in ('pending', 'accepted', 'revoked', 'expired')
            or raw.get('delivery') not in ('queued', 'pending', 'accepted', 'failed', 'unknown', 'cancelled')):
        return None
    for field in ('created_at', 'expires_at', 'accepted_at', 'revoked_at', 'last_requested_at', 'resend_available_at'):
        value = raw.get(field)
        if value is None and field not in ('created_at', 'expires_at'):
            continue
        try:
            if not isinstance(value, str) or datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
                return None
        except ValueError:
            return None
    return {field: raw.get(field) for field in ('id', 'email', 'state', 'created_at', 'expires_at', 'accepted_at', 'revoked_at', 'last_requested_at', 'resend_available_at', 'delivery')}


def _id(command, value):
    if not service._UUID.fullmatch(value):
        service._fail(command, 'invalid_request', _HINTS['invalid_request'])
    return value.lower()


def _send(command, resource_id, email, explicit_key):
    resource_id = _id(command, resource_id)
    if (email is not None and (len(email) > 254 or not _EMAIL.fullmatch(email))
            or explicit_key is not None and not service._KEY.fullmatch(explicit_key)):
        service._fail(command, 'invalid_request', _HINTS['invalid_request'])
    email = email.lower() if email else None
    endpoint, credential = service._connection(command)
    operation = 'create' if email else 'resend'
    _supported(command, endpoint, credential, f'invitations:{operation}')
    url = (f'{endpoint}/assets/{resource_id}/invitations' if email else
           endpoint.removesuffix('/cloud') + f'/asset-invitations/{resource_id}/resend')
    fields = {}
    try:
        context = json.dumps([operation, resource_id, email], separators=(',', ':'))
        with creation_request(endpoint, credential, context, explicit_key, recovery_directory='cloud-asset-sharing-requests') as pending:
            fields['idempotency_key'] = pending.key
            pending.save()
            status, body = service._request(url, credential, payload={'email': email} if email else {}, key=pending.key)
            expected = ('invitation_requested', 'invitation_exists') if email else ('invitation_resent',)
            if status not in (200, 202):
                rejected = 400 <= status < 500 and not pending.recovering
                if rejected:
                    pending.resolve()
                _check(command, status, body, sharing_outcome='rejected' if rejected else 'unknown', **fields)
            invitation = _invitation(body.get('invitation'))
            if (body.get('status') not in expected or invitation is None
                    or (email is not None and invitation['email'] != email)
                    or (email is None and invitation['id'].lower() != resource_id)
                    or body.get('delivery') != ('not_requested' if body.get('status') == 'invitation_exists' else 'queued')):
                service._fail(command, 'sharing_unconfirmed', 'The request may have succeeded. Rerun the same command to recover its key, or list asset access. No automatic retry was sent.', sharing_outcome='unknown', **fields)
            try:
                pending.resolve()
            except CreationStateError:
                fields['recovery_warning'] = 'Request confirmed, but retry state could not be cleared. Preserve this key before repairing local state.'
    except CreationBusy:
        service._fail(command, 'sharing_in_progress', 'The same invitation request is already running. Wait and retry.')
    except CreationStateError:
        service._fail(command, 'sharing_state_unavailable', 'Could not read or save invitation recovery state. Check asset access before repairing local retry files; preserve any returned key.', **fields)
    click.echo(json.dumps({'command': command, 'status': body['status'], 'invitation': invitation, 'delivery': body['delivery'], **fields,
                           'hint': 'This receipt records an email request queued at creation time, not current delivery. Check access for live status. The recipient must verify the invited email and accept.' if body['delivery'] == 'queued' else 'An invitation already exists; no additional email was requested.'}, indent=2))


def register_asset_sharing(assets):
    @assets.command('share')
    @click.argument('asset_id')
    @click.argument('email')
    @click.option('--idempotency-key', help='Optional retry key, generated and saved automatically when omitted.')
    def share_asset(asset_id, email, idempotency_key):
        """Invite EMAIL to view and download ASSET_ID.

        Owner only. Running this command deliberately requests an invitation
        email. Recipient verifies and accepts in their browser. Repeat the same
        command after uncertainty; its saved key prevents duplicate delivery.
        """
        _send('cloud assets share', asset_id, email, idempotency_key)

    @assets.command('access')
    @click.argument('asset_id')
    @click.option('--cursor', help='Unchanged next_cursor UUID from the previous page.')
    def access(asset_id, cursor):
        """List viewer invitations/access for an owned ASSET_ID, 50 per page.

        Includes pending, accepted, expired and revoked invitations. Email
        delivery accepted means provider acceptance, not recipient acceptance.
        """
        command = 'cloud assets access'
        asset_id = _id(command, asset_id)
        if cursor is not None:
            cursor = _id(command, cursor)
        endpoint, credential = service._connection(command)
        _supported(command, endpoint, credential, 'invitations:list')
        status, body = service._request(f'{endpoint}/assets/{asset_id}/invitations' + ('?' + urlencode({'cursor': cursor}) if cursor else ''), credential)
        if status != 200:
            _check(command, status, body)
        raw, next_cursor = body.get('invitations'), body.get('next_cursor')
        items = [_invitation(item) for item in raw] if isinstance(raw, list) and len(raw) <= 50 else None
        if (body.get('status') != 'ok' or items is None or any(item is None for item in items)
                or len({item['id'].lower() for item in items}) != len(items)
                or 'next_cursor' not in body or (next_cursor is not None and (not isinstance(next_cursor, str) or not service._UUID.fullmatch(next_cursor)))):
            service._fail(command, 'invalid_response', 'Could not confirm asset access. No mutation was sent.')
        click.echo(json.dumps({'command': command, 'status': 'ok', 'asset_id': asset_id, 'invitations': items, 'next_cursor': next_cursor}, indent=2))

    @assets.command('unshare')
    @click.argument('invitation_id')
    @click.option('--confirm', is_flag=True, help='Confirm revoking this invitation and its accepted asset access.')
    def unshare(invitation_id, confirm):
        """Revoke INVITATION_ID from an access list, including accepted access.

        Owner only. Requires --confirm; retry the same ID after uncertainty.
        Already downloaded files remain with their recipients.
        """
        command = 'cloud assets unshare'
        invitation_id = _id(command, invitation_id)
        if not confirm:
            service._fail(command, 'confirmation_required', 'Review the invitation ID from asset access, then pass --confirm to revoke it.', invitation_id=invitation_id)
        endpoint, credential = service._connection(command)
        _supported(command, endpoint, credential, 'invitations:revoke')
        status, body = service._request(endpoint.removesuffix('/cloud') + f'/asset-invitations/{invitation_id}/revoke', credential, payload={})
        if status != 200:
            _check(command, status, body, invitation_id=invitation_id, sharing_outcome='rejected' if 400 <= status < 500 else 'unknown')
        invitation = _invitation(body.get('invitation'))
        if body.get('status') != 'revoked' or invitation is None or invitation['id'].lower() != invitation_id or invitation['state'] != 'revoked':
            service._fail(command, 'sharing_unconfirmed', 'Revocation may have succeeded. List access or retry this exact invitation ID with --confirm.', invitation_id=invitation_id, sharing_outcome='unknown')
        click.echo(json.dumps({'command': command, 'status': 'revoked', 'invitation': invitation}, indent=2))

    @assets.command('resend-invitation')
    @click.argument('invitation_id')
    @click.option('--idempotency-key', help='Optional retry key, generated and saved automatically when omitted.')
    def resend(invitation_id, idempotency_key):
        """Request another email for a pending/expired INVITATION_ID.

        Owner only; deliberately sends email and rotates its acceptance link.
        Preserves original asset scope. Accepted or revoked offers cannot resend.
        """
        _send('cloud assets resend-invitation', invitation_id, None, idempotency_key)
