# Create a private account asset

Upload a video, audio file, image, caption file or transcript to your account
without creating a project or share link. The original bytes are preserved.

## Remote CLI

Use a CLI build containing `cloud assets create` and the existing human-approved
account connection. This command needs the base package only: no FFmpeg,
Whisper, transcription model or local editing workspace.

```sh
moviestar account connect you@example.com
# Open the approval email, then confirm the connection:
moviestar account status
moviestar cloud assets create ./photo.png
```

If already connected, skip the connection steps. The upload uses the same
approved installation and service origin; you do not need to create an API key.
See [account connection](accounts.md) for service configuration and revocation.

The CLI reserves an upload, transfers the original privately, and asks the
service to validate it. Success returns one JSON object on stdout with
`status: "ready"`, `asset.id`, `asset.kind`, `asset.format`, and `upload_id`.
The asset is account-owned. Creating it does not attach it to a project, expose
it publicly, or modify a local workspace. The older `moviestar upload` command
remains the separate finished-MP4 hosting workflow.

### Recover an interrupted upload

A generated idempotency key appears on stderr before the first request. Save
it, or supply your own from the start:

```sh
moviestar cloud assets create ./photo.png --idempotency-key photo-original-1
```

Retry the **same unchanged original and key** after a lost reply or transfer
failure. The service reuses the same asset and upload, without charging another
slot. A reused key is scoped to the account, not just one CLI installation.
Changing the declaration with that key returns `idempotency_conflict`; the key
does not compare file hashes or authorize replacing an original.

If the transfer succeeded but completion/validation failed, use the returned
`upload_id` (not `asset.id`) to retry without transferring again:

```sh
moviestar cloud assets create ./photo.png --resume UPLOAD_ID
```

Commands do not automatically retry writes. Errors return structured JSON with
a stable `status`, recovery `hint`, and the retry key/upload ID when known.
Unsupported formats, unavailable service operations, quota limits, invalid
content and revoked credentials fail without claiming the asset is ready.
Keep retry identifiers when an outcome is uncertain. A genuinely invalid file
must be corrected and submitted with a new key.

## Starter formats and limits

| Kind | Formats |
| --- | --- |
| Video | MP4/MOV with H.264 or HEVC; WebM with VP8 or VP9 |
| Audio | MP3, WAV, M4A, FLAC |
| Image | JPEG (`.jpg`/`.jpeg`), PNG, WebP, GIF including animated GIF |
| Caption/transcript | UTF-8 SRT, WebVTT (`.vtt`), plain text (`.txt`) |

Extensions declare the format; the server checks the actual content. Renaming
an unsupported file does not convert it. Files must be nonempty. Format ceilings
are 1 GB for video/audio, 32 MiB for images and 256 KiB for captions/transcripts;
account limits may be lower. Media admission is bounded; acceptance is not a
full inspection or an editing compatibility guarantee.

All kinds consume one slot in the same account allowance. Free allows 100
assets, 1 GB total and 500 MB per file; Plus allows 1,000 assets, 25 GB total and
1 GB per file (GB/MB decimal). Existing hosted media also consumes that
allowance. Pending uploads reserve capacity. The service checks live capacity
on reservation/completion; the CLI does not bypass it.

## HTTP API

Developers can create a personal API key at `/account/api-keys` on the website
for the target service. Use it as `Authorization: Bearer ...` on API requests:

1. `POST /api/v1/assets/uploads` with JSON `filename`, `size_bytes` and an
   `Idempotency-Key` header. Save `asset.id`, `upload_id`, and the key.
2. If `upload` is non-null, PUT the unchanged original to `upload.url`, using
   exactly `upload.headers`, including `If-None-Match: *`. Never send your API
   credential to storage. A 412 on a replay means an original is already there;
   completion still checks it against the reservation.
3. `POST /api/v1/assets/uploads/UPLOAD_ID/complete`. Only `asset.state: "ready"`
   means validation succeeded. Preserve retry identifiers after a 503.
4. `GET /api/v1/assets/ASSET_ID` reads metadata;
   `GET /api/v1/assets/ASSET_ID/download` returns the ready original.

The CLI uses these same endpoints with its approved installation bearer and
`x-moviestar-client: moviestar-cli-v1`. Every operation rechecks live credentials,
ownership and account consent. Browser cookies are not authentication for these
endpoints. Create keys and connect the CLI on the same service you are testing;
Preview and Production credentials are separate.

## List owned assets and assets shared with you

```sh
moviestar cloud assets list
moviestar cloud assets list --shared
moviestar cloud assets list --shared --limit 20 --cursor NEXT_CURSOR
```

The default collection contains your ready originals. `--shared` contains ready
originals available through accepted asset invitations or project invitations
that include assets. Each asset appears once even when several paths grant
access. `shared_via` identifies one current path: project provenance includes
the project title and reference ID; direct provenance has `kind: "asset"`, an
invitation ID and the sender's handle. Open that project or use `cloud assets get`
to see or download the original. This does not grant permission to edit or
reshare it.

Follow `next_cursor` unchanged, keeping it with the same collection. Revoked
invitations, removed references, deleted projects, nonready assets and
metadata-only invitations do not grant catalog access. Every read rechecks
current credentials and account consent. A service without this capability
returns `unsupported_operation`, rather than an empty or owned collection.

The API equivalent is
`GET /api/v1/account/cloud/assets?collection=shared&limit=20`, authenticated with
your personal API key or approved installation. Responses include
`collection: "shared"`, `items`, `next_cursor` and
`contract_version: "project-asset-references-v1"`.

Hosted MCP uses `moviestar_list_assets` with `collection: "shared"` (or `"owned"`,
the default), plus optional `limit` and `cursor`. The website exposes the same
collection under **Assets → Shared with me**. See the direct sharing journey
below for individual asset invitations.

## Rename an asset's display name

```sh
moviestar cloud assets rename ASSET_ID "Interview opening"
```

The command reads the current owned asset's name before writing. Its ID,
original filename, bytes, project references and sharing permissions stay the
same. All project and shared-list views show the new `display_name`.
Existing assets default to their original filename. Names are trimmed,
1–240 Unicode characters, and cannot contain control or bidirectional
formatting characters. Viewers cannot rename an asset.

If the result is uncertain, preserve `expected_display_name` from the response:

```sh
moviestar cloud assets rename ASSET_ID "Interview opening" --expected-name "Previous name"
```

Retry the same target and expected names. An `asset_changed` conflict means a
different name is current; read it and review the change before replacing it.
The CLI does not automatically retry writes. Older services fail with
`unsupported_operation` before sending a rename.

API: `PATCH /api/v1/account/cloud/assets/ASSET_ID`, using your API key or approved
installation, with JSON `{"display_name":"Interview opening","expected_display_name":"Previous name"}`.
Success returns `status: "renamed"`, `changed` and the updated `asset`.
Repeating the request confirms success if the desired name is already current;
it cannot overwrite a different name saved since the first request.

MCP: `moviestar_rename_asset` takes `asset_id`, `display_name` and
`expected_display_name`. Read `display_name` from an asset listing first (falling
back to `filename` for older responses). The website offers **Rename asset** in
an owned asset's detail view and keeps the original filename visible there.

## Share one asset with a viewer

An owner can invite a viewer to one ready original without creating a project.
The recipient signs in with the invited email verified, explicitly accepts, and
then finds the original under **Assets → Shared with me**. Accepted access is
bound to that account; forwarding an invitation does not grant another account
access. A viewer can read and download, but cannot rename or reshare the asset.

```sh
moviestar cloud assets share ASSET_ID recipient@example.com
moviestar cloud assets access ASSET_ID
moviestar cloud assets list --shared
moviestar cloud assets get ASSET_ID
moviestar cloud assets resend-invitation INVITATION_ID
moviestar cloud assets unshare INVITATION_ID --confirm
```

Share and resend save a retry key before sending. Repeat the same command after
an uncertain response, or retain `--idempotency-key KEY`; do not create another
request simply because the response was lost. `queued` records an email request,
not inbox delivery or recipient acceptance. `access` lists up to 50 invitations
per page; pass its unchanged UUID `--cursor` to continue. Old services fail with
`unsupported_operation` before a sharing mutation.

`get` returns metadata and a private `download_url`. Open it while signed in to
the recipient account, or GET it using your own API credential. This command
does not write a local file. Downloaded copies cannot be recalled. Removing a
direct invitation does not remove independent access through a shared project.
Recipient leave, editor permissions, and editor resharing are separate work.

### API and hosted MCP

Use an active personal API key as `Authorization: Bearer YOUR_KEY`. Browser
writes use same-origin signed-in requests. Remote CLI installations use their
approved installation credential; credentials and account IDs are never request
body fields.

| Operation | Endpoint |
| --- | --- |
| Invite | `POST /api/v1/account/cloud/assets/:id/invitations` |
| List invitations/access | `GET /api/v1/account/cloud/assets/:id/invitations?cursor=UUID` |
| Resend | `POST /api/v1/account/asset-invitations/:invitation_id/resend` |
| Revoke | `POST /api/v1/account/asset-invitations/:invitation_id/revoke` |
| Read accessible original | `GET /api/v1/account/cloud/assets/:id` |
| Download accessible original | `GET /api/v1/account/cloud/assets/:id/download` |

Invite accepts exactly `{"email":"recipient@example.com"}`. Resend and revoke
accept `{}`. Invite/resend require an `Idempotency-Key` header. Recipient
review/acceptance stays in the browser; a developer credential cannot assert a
recipient's verified email or accept on their behalf. Downloads recheck current
access and stream the original without exposing storage keys or signed storage
URLs.

Hosted MCP exposes `moviestar_invite_asset_viewer`,
`moviestar_list_asset_access`, `moviestar_resend_asset_invitation`,
`moviestar_revoke_asset_access`, and `moviestar_get_asset`. Share/resend take an
optional `idempotency_key`; agents should generate and retain a UUID before the
first call. Revocation requires the selected ID in both `invitation_id` and
`confirm_invitation_id`. Only send invitations or remove access on the owner's
explicit request. Both OAuth account connections and personal API keys use the
same resource permission checks.

The shared catalog retains existing project provenance. A directly shared item
instead has `shared_via: {kind: "asset", invitation_id, shared_by}`; it has no
fabricated project or reference ID. Each asset appears once, even if several
paths grant access. Follow `next_cursor` unchanged. Capabilities advertise
`asset_sharing.management.contract_version: native-asset-sharing-v1` and
`project_assets.operations` includes `assets:read` for direct detail/download.
