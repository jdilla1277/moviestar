# Find invitations without email

The recipient's **Invitations** inbox lists pending invitations to both account
assets and projects. Open it from either Web library. Filter **All**, **Projects**
or **Assets**, review an invitation, and explicitly accept. The email is an
optional entry point; its private link is not needed for this journey.

Listing or reviewing does not grant access. After acceptance, the invitation
leaves pending and the object appears in **Shared with me**. A project invitation
keeps its original permissions: a legacy details-only invitation does not become
an asset-download invitation.

## Invitations and notification email

Project and asset sharing creates a pending invitation. Email is a notification,
not a requirement for discovering or accepting that invitation.

When the service's notification allowance is exhausted, sharing still succeeds:
`status` is `invitation_requested`, `delivery` is `not_requested`, and
`email_suppressed_reason` is `rate_limited`. No notification is queued for later.
The invitation appears in the recipient's Web inbox and in
`moviestar cloud invitations list` / `moviestar_list_invitations` once they have
an account with the invited email verified. Access still requires explicit
acceptance; an invitation does not create an account or grant access by itself.

The owner can list access to distinguish invitation state from email delivery.
Retrying the same request key returns the original receipt, even after the email
allowance resets, and does not send another email. An explicit resend still
respects email limits; a rejected resend leaves the current invitation and
acceptance link unchanged.

## Remote CLI

```sh
moviestar cloud invitations list
moviestar cloud invitations list --kind asset
moviestar cloud invitations list --kind project
moviestar cloud invitations list --limit 20 --cursor NEXT_CURSOR
```

Use your approved recipient installation. Each entry includes its object kind,
title, sender handle, invitation ID, role, expiry and `review_url`. Open that URL
while signed in to the recipient account to review and accept. The CLI never accepts automatically or sends an email; native acceptance is an explicit command after review. Old services fail gracefully with
`unsupported_operation`.

Keep each `next_cursor` with its original `--kind` filter. Received invitations
are different from the owner's `cloud assets access` / `cloud projects access`
lists, which manage who the owner has invited.

## Hosted MCP

Call `moviestar_list_invitations` with optional `kind` (`all`, `asset`, `project`),
`limit` (1–50) and `cursor`. The same pending inbox works with an OAuth connection
or personal API key. Use the native review/accept tools below, or give the recipient `review_url` as a browser fallback. Merely listing is read-only and grants no
access. Do not ask the user to find the email or paste its private token.

## API

```http
GET /api/v1/account/cloud/invitations?kind=all&limit=20
Authorization: Bearer YOUR_PERSONAL_API_KEY
```

Responses use `contract_version: "received-invitations-v1"`, `items` and
`next_cursor`. Each item includes `id`, `kind`, `resource_id`, `title`,
`shared_by`, `state: "pending"`, `role`, `permissions`, `includes_assets`
(null for assets), `created_at`, `expires_at` and a relative `review_path`.
CLI and MCP add an absolute `review_url` on the same service origin. A listing
URL does not contain an email token or serve as authorization on its own.

Discovery uses the authenticated account's current verified email addresses.
Unverified addresses, wrong accounts, expired/revoked offers, removed objects,
and unavailable owners must not reveal an actionable pending invitation.
Acceptance always rechecks current identity and permissions; cookie-authenticated browser writes additionally require the same origin. If an invitation changes after review, the
browser requires another review before accepting. A lost acceptance response
can be recovered by reopening the same review URL; do not send another invite.

## Native review and acceptance

For either object kind, the recipient can complete acceptance without a browser
or emailed token. Use the received invitation ID, not the asset/project ID:

```sh
moviestar cloud invitations review INVITATION_ID --kind project
moviestar cloud invitations accept INVITATION_ID --kind project --expected-revision REVIEW_REVISION
```

Use `--kind asset` for an asset. Read the review's object, role, permissions and
project asset scope, then explicitly approve them. `REVIEW_REVISION` is the exact
`invitation.revision` returned by review. If the invitation changed, review again
and approve its current permissions. Preserve the revision on uncertain retries;
reopening an accepted invitation recovers existing access without another grant.
A lost or invalid acceptance receipt is unconfirmed, never proof of access.

Hosted MCP offers `moviestar_review_invitation` with `kind` and `invitation_id`,
then `moviestar_accept_invitation` with those fields plus `expected_revision`.
Present the review before asking for approval. The accept tool is a write and
must only be called on explicit user intent. OAuth and personal API keys use the
same resource authorization rules. Listing/reviewing never accepts.

API: `GET /api/v1/account/cloud/invitations/:kind/:id` reviews;
`POST` to the same path plus `/accept` with JSON `{expected_revision: "..."}`
accepts. Approved CLI installations and personal API keys use their existing
Bearer credentials; browser cookie writes remain same-origin. Current verified
recipient addresses, credential validity, consent, expiry and revocation are
rechecked for each action. No caller-supplied recipient email is accepted.
Services advertise `received_invitations.native_actions` with
`contract_version: received-invitation-actions-v1`; older services retain browser
review links and return `unsupported_operation` for native commands.

To remove accepted access, see [Leave a received share](leaving-shares.md).
