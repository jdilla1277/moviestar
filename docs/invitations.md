# Find invitations without email

The recipient's **Invitations** inbox lists pending invitations to both account
assets and projects. Open it from either Web library. Filter **All**, **Projects**
or **Assets**, review an invitation, and explicitly accept. The email is an
optional entry point; its private link is not needed for this journey.

Listing or reviewing does not grant access. After acceptance, the invitation
leaves pending and the object appears in **Shared with me**. A project invitation
keeps its original permissions: a legacy details-only invitation does not become
an asset-download invitation.

## Remote CLI

```sh
moviestar cloud invitations list
moviestar cloud invitations list --kind asset
moviestar cloud invitations list --kind project
moviestar cloud invitations list --limit 20 --cursor NEXT_CURSOR
```

Use your approved recipient installation. Each entry includes its object kind,
title, sender handle, invitation ID, role, expiry and `review_url`. Open that URL
while signed in to the recipient account to review and accept. The CLI does not
accept automatically or send an email. Old services fail gracefully with
`unsupported_operation`.

Keep each `next_cursor` with its original `--kind` filter. Received invitations
are different from the owner's `cloud assets access` / `cloud projects access`
lists, which manage who the owner has invited.

## Hosted MCP

Call `moviestar_list_invitations` with optional `kind` (`all`, `asset`, `project`),
`limit` (1–50) and `cursor`. The same pending inbox works with an OAuth connection
or personal API key. Give the recipient the returned `review_url` to review and
accept in their signed-in browser. Merely listing is read-only and grants no
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
Acceptance remains a same-origin browser action after current identity and
permissions are checked again. If an invitation changes after review, the
browser requires another review before accepting. A lost acceptance response
can be recovered by reopening the same review URL; do not send another invite.
