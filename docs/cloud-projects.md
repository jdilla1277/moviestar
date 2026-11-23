# Personal Cloud projects

To upload a private original without a project, use
[`moviestar cloud assets create FILE`](account-assets.md). The asset upload
workflow is separate from project metadata and uses the same approved account.

Use the explicit `cloud` command group to create and reopen projects owned by
your connected account, or discover projects shared with it when supported. Your service must have personal Cloud project access
enabled. The client supports project metadata and, when advertised, existing
account-asset references; inspect capabilities
for the operations your service currently advertises.

For initial timeline reads and trim dry runs, see [Cloud edit checks](cloud-edits.md).
Those commands are available only when the service advertises the editing bridge.

## Install

Use the base package for account and Cloud metadata commands:

```sh
python -m pip install moviestar
```

It does not install Whisper, PyAV, or transcription models and does not require
FFmpeg. Local speech recognition is available through the `transcribe` extra;
see [installation profiles](installation.md).

## Connect and inspect

Use the existing human-approved account connection:

```sh
moviestar account status
moviestar cloud capabilities
```

If disconnected, follow [account connection](accounts.md). Cloud commands use
the same installation credential and service origin as that connection. They
do not request another credential, accept terms, or approve an installation.
Never supply a partner key as a personal login.

If an older saved connection has no service URL, Cloud commands stop before
loading its credential or contacting a service. Set
`MOVIESTAR_ACCOUNT_INSTALLATION_URL` to the installation endpoint of the
service that approved it (for example,
`https://your-service.example/api/v1/account/installation`), then retry.

## Create once, then reopen

Give your project a title. The CLI generates and saves its retry key for you:

```sh
moviestar cloud projects create "Highlights"
moviestar cloud projects list
moviestar cloud projects show PROJECT_ID
```

Replace `PROJECT_ID` with `project.id` returned by create. Reads return the
project metadata or `{items, next_cursor}` directly. Create returns a
confirmation containing `status`, `project`, the generated `idempotency_key`,
and a next-step hint. The project ID persists across CLI processes and the
account website.
You can open **My projects** on the same service's account page and select it,
or bookmark `/account/projects?project=PROJECT_ID` on that service.

Each project reports `id`, `title`, `state`, `owner_id`, and
`current_revision`. List results include `access.role`; detail reads preserve
the service’s `owner` or `viewer` role when supplied. Missing source, job, or result collections are unavailable
through this metadata contract; they do not establish that the project has no
media. Local editing commands retain their current behavior and operate on
local workspaces. Cloud metadata commands require no local FFmpeg execution or
transcription and never upload media or select a local workspace. The package's
base installation omits the optional local transcription stack.

## Rename the same project

When capabilities advertise `projects:rename`, change its title with:

```sh
moviestar cloud projects rename PROJECT_ID "Tournament highlights"
moviestar cloud projects show PROJECT_ID
```

The CLI reads the current title first and sends one conditional update. The
project keeps its ID, assets and access grants. Shared viewer access does not
permit renaming. Local projects and filenames are unchanged.

If the project was renamed elsewhere, `project_changed` stops the update.
Read its current title and review your intended change before trying again.
If the reply is lost, the JSON error retains `title`, `project_id` and
`expected_title`. Inspect the project, or explicitly retry the same request:

```sh
moviestar cloud projects rename PROJECT_ID "Tournament highlights" \
  --expected-title "Previous title"
```

Preserve the original previous title after uncertainty. Replacing it with a
newer title means deliberately submitting a new change. An already-matching
desired title confirms success without another update. A title precondition
does not lock unrelated project metadata.

The Web project page offers **Rename project**. It keeps a conflicting draft
and offers **Load latest title** before another save. A lost reply preserves the
same pending rename for an explicit retry after reopening in that browser tab.

The HTTP equivalent uses a personal API key and exactly two fields:

```sh
curl --fail-with-body https://trymoviestar.com/api/v1/account/cloud/projects/PROJECT_ID \
  -X PATCH \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"title":"Tournament highlights","expected_title":"Previous title"}'
```

An enabled hosted MCP connection offers `moviestar_rename_project`. First call
`moviestar_get_project`, then pass its exact title as `expected_title` alongside
the new `title` and `project_id`. Its response includes the same project's Web
link. After a conflict, read again and review the requested change; do not
automatically substitute a new precondition. These operations change no assets,
editing state, rendering or sharing rights.

## Owned and shared discovery

Owned projects and accepted shared projects are separate collections:

```sh
moviestar cloud projects list
moviestar cloud projects list --shared
moviestar cloud projects show PROJECT_ID
```

Call both lists to find all accessible projects. Owned items report
`access.role: "owner"`; shared items report `access.role: "viewer"` and
`shared_by`. Opening a shared ID uses the same `show` command and rechecks
current access. New whole-project viewer invitations include current and future
ready project assets and original downloads. Legacy invitations retain their
metadata-only scope. Neither grants editing or resharing rights.

The service must advertise the `recipient-projects-v1` sharing contract in
`cloud capabilities`. An older or sharing-disabled service returns an explicit
`unsupported_operation` for `--shared`; that does not mean the shared list is
empty. Owned commands continue to work. The client uses the connected service's
fixed routes and never follows a foreign discovery URL from capabilities.

## Pagination and recovery

```sh
moviestar cloud projects list --limit 20
moviestar cloud projects list --limit 20 --cursor NEXT_CURSOR
```

Pass the previous response's opaque `next_cursor` unchanged; stop when it is
null. Keep a cursor with the same collection: use `--shared --cursor NEXT_CURSOR`
for a shared page. Project IDs are explicit, so you can reopen an ID outside
the first page. Titles can repeat; reopen by ID rather than guessing from a title.

After a lost create response, rerun the same command with the **exact original
title**. The CLI saves the key before sending the request and reuses it across
process restarts until success is confirmed. After a confirmed success, running
the command again creates a new project, even with the same title.

Retry records live under `cloud-project-requests` beside your account profile,
outside local editing projects. They are isolated by service, installation
credential, and exact title and contain no credentials. A different connection
cannot recover the old connection's request automatically; check the project's
account before starting a replacement request.

You can still supply your own key, recover a request using its returned key,
or deliberately create a separate same-title project while another request is
unresolved:

```sh
moviestar cloud projects create "Highlights" --idempotency-key highlights-1
```

If that key already exists, the CLI returns the existing project for the
original title, or `idempotency_conflict` for a different title. In both cases,
it returns a `suggested_idempotency_key` and a hint for creating an additional
project. It never automatically creates another project on key reuse. A later
rename does not change the original creation key or payload: replay may return
the project's current title.

An explicit new key does not overwrite an earlier unresolved retry record;
the no-key command still recovers that earlier request. If the explicit request
also loses its response, retry its exact title and explicit key.

If retry state cannot be read or saved, creation stops before sending a new
request. Check `moviestar cloud projects list` and preserve the original
title/key before repairing the recovery file. Restore a damaged file from a
backup, or move it aside after identifying the original project; do not discard
an unresolved request just to retry with another generated key. If a confirmed
creation cannot update local state, the response includes `recovery_warning`;
another no-key invocation may recover that project again.

Errors are structured JSON with a stable `status` and recovery `hint`.
An unknown creation outcome includes the original key; a new key could create
a second project. There is no automatic write retry. Invalid/revoked credentials
direct you to `account status`; account setup/consent failures require the human
to finish the existing account flow. Missing/inaccessible projects share the
same unavailable response. A disabled service returns
`personal_cloud_unavailable`; signing in again does not enable that service.

Local files and account credentials are preserved on all these failures.

## Create through the HTTP API

On a service with personal Cloud projects enabled, use a
[website-issued personal API key](api-keys.md). Create the key on the same
environment's website and store it as `MOVIESTAR_API_KEY` in your shell or
secret manager. Complete the account's handle and terms setup first.

```sh
curl --fail-with-body https://trymoviestar.com/api/v1/account/cloud/projects \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{"title":"Highlights"}'
```

Expected result: HTTP 201 with `status: "created"`, `project` metadata,
and an automatically generated `idempotency_key`. The project is private,
owned by the key's account, and initially contains no uploaded media. No local
workspace is created. Open `/account/projects?project=PROJECT_ID` on the same
service's website to see it, or read it through the API:

```sh
curl --fail-with-body \
  https://trymoviestar.com/api/v1/account/cloud/projects/PROJECT_ID \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY"
```

The service also accepts `GET /api/v1/account/cloud/projects` and
`GET /api/v1/account/cloud/capabilities` with that key. Without an accepted grant, another account cannot
read a private project just by knowing its ID; missing and inaccessible
projects return HTTP 404. Revoking the key ends its API and MCP access while
the owner can continue using the website.

You may supply an `Idempotency-Key` header. Repeating the original title/key
returns HTTP 200 with the same project ID and `replayed: true`; using that key
for a different title returns HTTP 409 `idempotency_conflict`. Both responses
suggest a fresh `suggested_idempotency_key` for an additional project. The
service never automatically sends another creation on key reuse.

A missing key is generated for that HTTP request and returned in the response
body and `Idempotency-Key` response header. If your client must recover a
completely lost response, generate and save a key before sending and reuse it
on retry. If no key was retained, check My projects before creating again;
matching titles do not establish that two requests are the same creation.

Project creation requires the service's Cloud access to be enabled separately
from account/key access. HTTP 503 `personal_cloud_unavailable` means projects
are unavailable on that environment; signing in or replacing the key does not
enable them. HTTP 403 account setup/consent errors require a human to complete
the website flow. These metadata permissions do not grant source upload,
editing, rendering, or sharing permissions.

## Create through hosted MCP

Connect ChatGPT to `https://trymoviestar.com/mcp` using its normal OAuth flow,
or configure a [developer MCP client with a personal key](api-keys.md#authenticate-a-remote-mcp-client-with-your-key).
On a service advertising `moviestar_create_project`, ask:

> Create a private project called Highlights.

Expected result: a persistent project ID, title, retry key, and a link to open
that project on the account website. Creation adds no media and publishes
nothing. When Cloud access is disabled, the tool returns an explicit
unavailable error rather than claiming that a project was created.

Developer clients can call the tool using their existing MCP client:

```javascript
import { randomUUID } from "node:crypto";

const retryKey = randomUUID(); // Persist before sending if you need restart recovery.
const result = await client.callTool({
  name: "moviestar_create_project",
  arguments: { title: "Highlights", idempotency_key: retryKey },
});
console.log(result.structuredContent);
```

The retry key is optional and generated automatically if omitted. Agents
should generate and retain it themselves rather than asking the user to invent
one. Retry the original title/key after an uncertain outcome; never silently
substitute a new key. Reuse returns the existing project or a conflict with a
fresh-key suggestion. A discovered tool does not establish that the environment
has enabled Cloud access, and an older service may not advertise the tool yet.


## List and reopen through HTTP or hosted MCP

HTTP API clients use the same service origin and active personal key:

```sh
curl --fail-with-body 'https://trymoviestar.com/api/v1/account/cloud/projects?limit=20' \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY"
curl --fail-with-body 'https://trymoviestar.com/api/v1/account/cloud/shared-projects?limit=20' \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY"
```

Owned pages contain `{items: [PROJECT], next_cursor}`. When sharing is enabled,
shared pages use `contract_version: "recipient-projects-v1"` and items containing
`{project: PROJECT, shared_by, role: "viewer", permissions: ["projects:read"]}`.
Read either project using `GET /api/v1/account/cloud/projects/PROJECT_ID`;
with sharing enabled, detail includes `access.role`. Check `/capabilities` for
sharing support before interpreting a missing discovery route. A missing route
on a sharing-disabled service returns 404; it is not an empty shared collection.

On an MCP service advertising `moviestar_list_projects` and
`moviestar_get_project`, ask:

> List my owned projects and projects shared with me. Show which is which.
> Open project PROJECT_ID and give me its website link.

Developer MCP calls:

```javascript
const owned = await client.callTool({
  name: "moviestar_list_projects",
  arguments: { collection: "owned", limit: 20 },
});
const shared = await client.callTool({
  name: "moviestar_list_projects",
  arguments: { collection: "shared", limit: 20 },
});
const opened = await client.callTool({
  name: "moviestar_get_project",
  arguments: { project_id: "PROJECT_ID" },
});
```

Each list returns `{collection, items: [{project, url, shared_by?}], next_cursor}`.
Follow each collection's cursor unchanged until null. Get returns `{project, url}`;
`project.access.role` distinguishes owner and viewer. The URL points to the same
project in the account website, using the shared-project page for a viewer.
The same ID works after reconnecting or when moving between Web, API, CLI and MCP.

Shared MCP discovery disabled by the service returns
`project_sharing_unavailable`, separately from a successful empty page. Revoked
or inaccessible project IDs return the same safe unavailable error as missing
IDs. A previous list, an old URL, or a cached role never authorizes a later read.
These operations read metadata only and do not upload, publish, select a local
workspace or create another project.

## Reuse existing assets across projects

On a service advertising `project_assets.contract_version:
"project-asset-references-v1"`, a ready account asset can belong to several owned
projects. Supported kinds are video, audio, image, and transcript/captions.
These commands work with existing originals; they do not upload or copy files,
create local editing workspaces, or require FFmpeg. Use a CLI version whose
`cloud projects --help` includes `add-asset` and `remove-asset`; older installed
versions need an update once that release is available.

Start with a ready original from the account asset API. Creating that original
is currently an API journey; CLI, MCP, and Web reference an existing asset:

1. With a same-environment personal key, send
   `POST /api/v1/assets/uploads` with JSON
   `{"filename":"image.png","size_bytes":1234}` using the file's actual length.
   Save the returned `asset.id`, distinct `upload_id`, and `idempotency_key`.
   Supply a saved `Idempotency-Key` header before the first request if you need
   recovery from a completely lost reservation response.
2. PUT the original bytes to `upload.url` using the returned upload headers.
   **Do not send your Moviestar API key to storage.** The create-only transfer
   uses `If-None-Match: *`; after an uncertain PUT, check completion instead of
   replacing the original with another file.
3. Call `POST /api/v1/assets/uploads/UPLOAD_ID/complete` with your personal key.
   Continue with the returned ready `asset.id`. After a temporary failure,
   retry that same upload ID rather than reserving another original.
4. Read `GET /api/v1/assets/ASSET_ID` or download
   `GET /api/v1/assets/ASSET_ID/download` with your personal key. The download
   preserves the original bytes; comparing hashes before and after project
   changes verifies that the asset survived unchanged.

Starter originals include MP4/MOV and WebM video, MP3/WAV/M4A/FLAC audio,
JPEG/PNG/WebP/GIF images, and UTF-8 SRT/VTT/TXT captions/transcripts. Video
admission supports H.264/HEVC in MP4/MOV and VP8/VP9 in WebM; it does not promise
browser playback or a full-file decode. Format and account-plan size limits
apply. Asset creation requires no project, publication, or separate provisioning.

```sh
moviestar cloud assets list
moviestar cloud projects add-asset PROJECT_A ASSET_ID
moviestar cloud projects add-asset PROJECT_B ASSET_ID
moviestar cloud projects assets PROJECT_A
moviestar cloud projects assets PROJECT_B
moviestar cloud projects remove-asset PROJECT_A REFERENCE_ID_FROM_A
```

Replace the placeholders with service-returned UUIDs. Each project membership
has its own `reference_id`, distinct from the original `asset.id`. Remove uses
that reference ID, so retrying an old removal cannot remove a later membership.
List the second project afterward: the same original asset remains there and
in your account. References add no storage or asset-slot charge; removing one
does not free the original's charge or delete it. Deleting a project likewise
preserves account originals.

All three list commands support `--limit` and `--cursor`. Follow `next_cursor`
unchanged until null. Account asset pages return ready originals; project asset
pages return `{reference_id, asset}` entries, with authenticated download links
on updated services. Legacy project-source uploads and account-wide discovery
of other people’s assets remain unsupported. A whole-project viewer invitation
grants read/download access to its current and future asset references, but never
membership changes. Older metadata-only invitations retain their original rights
until the owner removes and replaces them. Unsupported services fail clearly
before sending a mutation.

`add-asset` generates and saves a retry key before sending. Rerun the same command
after a lost response, or provide the original `--idempotency-key`. Its retry
state is isolated by service, credential, project and asset. A `reference_removed`
response means that attempt's membership remains removed; only use the suggested
new key if deliberately adding it again. No fresh-key write happens
automatically. If recovery storage fails, preserve the returned key and inspect
the project's assets before repairing retry files beside your account profile.

In the website, open an owned project, choose **Add existing assets**, select a
ready original, and reopen the other project to add it there too. **Remove from
project** removes only that membership. Legacy project uploads remain under
their existing source contract; this catalog does not migrate or copy them.

### HTTP reference operations

Use the same environment's personal API key. First discover ready originals:

```sh
curl --fail-with-body 'https://trymoviestar.com/api/v1/account/cloud/assets?limit=20' \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY"
curl --fail-with-body \
  https://trymoviestar.com/api/v1/account/cloud/projects/PROJECT_A/asset-references \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: add-original-to-project-a" \
  --data '{"asset_id":"ASSET_ID"}'
```

Attach returns HTTP 201 for a new membership, or 200 for an existing active
membership, with `{status: "attached", reference, idempotency_key, replayed}` and
`contract_version`. Omitted keys are generated and returned, but clients that
need to recover a completely lost response should save a key before sending.
The same key is bound to the same actor, project and asset. After removal, an
old attach key returns HTTP 409 `reference_removed` rather than resurrecting it.

```sh
curl --fail-with-body \
  'https://trymoviestar.com/api/v1/account/cloud/projects/PROJECT_A/assets?limit=20' \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY"
curl --fail-with-body -X DELETE \
  https://trymoviestar.com/api/v1/account/cloud/projects/PROJECT_A/asset-references/REFERENCE_ID_FROM_A \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY"
```

Removal returns HTTP 204, including an authorized replay. It never deletes the
original, removes another membership, or releases storage. Current credentials,
consent, project ownership and asset readiness/access are checked by the service
on every operation. Missing and inaccessible IDs share HTTP 404.

### Hosted MCP reference operations

On a connection advertising the asset-reference tools, ask:

> List my ready account assets. Add asset ASSET_ID to projects PROJECT_A and
> PROJECT_B. Reopen both and confirm the asset ID matches. Remove only its
> reference from PROJECT_A, then verify PROJECT_B still contains the same asset.

Tools are `moviestar_list_assets`, `moviestar_list_project_assets`,
`moviestar_add_project_asset`, and `moviestar_remove_project_asset`. Add accepts
`project_id`, `asset_id`, and an optional `idempotency_key`; remove accepts
`project_id` and `reference_id`. Agents generate and retain retry keys themselves.
Listing returns metadata, not a download, analysis of contents, or a sharing
grant. Reconnect and list by the same project ID to reopen its memberships.


## Read private developer preview guides

When the connected service enables developer documentation for your account:

```sh
moviestar cloud docs list
moviestar cloud docs show quickstart
moviestar cloud docs show asset-lifecycle
moviestar cloud docs show quickstart --json
```

`list` returns page slugs and availability summaries as JSON. `show` writes the
full page as Markdown by default, ready for an agent to read or save to a file.
Use `--json` for the page, title, Markdown and website path in a JSON object.
The existing `--markdown` flag still works; combining it with `--json` is an
error. Service errors remain structured JSON in either format. These commands
use the same approved installation and service origin as your account connection.
They do not need a personal API key, FFmpeg, or local media. The service checks
current access and preview enrollment on each read; private guides are not
bundled in the package or cached locally.

Version 0.8.3 returns JSON by default and supports `--markdown`; the Markdown
default and `--json` require a newer CLI or a source install containing this
change.

The matching hosted MCP tool is `moviestar_developer_docs`. Omit `page` to list
guides, or pass a returned slug to read one. Personal API clients can read the
same index at `GET /api/v1/account/developer-docs` and each guide at
`GET /api/v1/account/developer-docs/SLUG?format=markdown`, authenticated with a
personal key on that service. Website readers can open the developer docs link
from API key settings when enrolled and copy each page as Markdown.

A 401 requires a current installation connection. A 404 may mean that the
service has not deployed the docs or the account is not enrolled; ask your
preview contact rather than replacing credentials. Content labels distinguish
working journeys from pending rollouts and illustrative remote edit/inspect
examples. Reading a guide does not enable the operations it describes. An older
installed CLI may lack `cloud docs`; client release and website deployment are
separate.

## Delete a Cloud project

On a service advertising `projects:delete`, review its project ID and explicitly
confirm deletion:

```sh
moviestar cloud projects show PROJECT_ID
moviestar cloud projects delete PROJECT_ID --confirm
```

Only the owner can delete. The project disappears from owned/shared lists and
saved project links stop working. Accepted invitations are revoked. Account-owned
original assets and references in other projects remain intact; local files are
unchanged. This is not an asset-deletion or project-download command.

The JSON result has `status: "deleted"`, the same `project_id`,
`original_assets_preserved: true`, and `deletion: {id, state}`. Project access
ends immediately, while legacy project-media cleanup can be `pending`,
`running`, `succeeded` or `failed`. Do not interpret the receipt as immediate
storage release or cancellation of transfers already started.

After a lost reply, repeat the **same project ID** with `--confirm`; the owner
gets the same durable cleanup receipt even though ordinary project reads now
return unavailable. Every retry still checks the current credential, account
and consent. A missing/foreign project is never treated as successful deletion.
Do not automatically create another project to replace a deleted one.

The HTTP equivalent uses the same environment's personal key:

```sh
curl --fail-with-body -X DELETE \
  https://trymoviestar.com/api/v1/account/cloud/projects/PROJECT_ID \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"confirm_project_id":"PROJECT_ID"}'
```

Replace both placeholders with the exact same project UUID. Success returns
HTTP 202 with the receipt above. Hosted MCP exposes `moviestar_delete_project`
with `project_id` and an exactly matching `confirm_project_id`. Use it only for
the user's explicit deletion request, retain the same IDs after uncertainty,
and report cleanup state separately. Web deletion controls are not part of
this slice.

## Share a project with a viewer

Owners can manage viewer access natively when `cloud capabilities` advertises
`project_sharing.management.contract_version: native-project-sharing-v1`.

```bash
moviestar cloud projects share PROJECT_ID recipient@example.com
moviestar cloud projects access PROJECT_ID
moviestar cloud projects access PROJECT_ID --cursor NEXT_CURSOR
moviestar cloud projects resend-invitation INVITATION_ID
moviestar cloud projects unshare INVITATION_ID --confirm
```

`share` creates an invitation and requests notification email within the service
allowance. If email is rate-limited, the invitation still appears in the
recipient inbox with `delivery: not_requested` and
`email_suppressed_reason: rate_limited`; no email is queued for later. See
[Invitations](invitations.md#invitations-and-notification-email). Its retry key is
generated and saved before sending; rerun the same command after a lost reply to recover the same
request. `--idempotency-key KEY` can recover an explicit request. Resends have
separate saved retry keys and rotate the acceptance link. Do not automatically
resend or generate a new key after an uncertain reply. `queued` means waiting
for email delivery; delivery `accepted` means accepted by the email provider,
not confirmed in the inbox or accepted by the recipient.

`access` returns up to 50 invitations with a raw UUID `next_cursor`; follow it
unchanged until null. Invitation `state: accepted` indicates recipient access.
Revoked and expired history remains visible. Use an invitation ID from this
list for `unshare`; repeating the same revocation is safe. No browser is opened
for these owner operations. Initial account connection and recipient acceptance
still use their normal account flows.

New invitations grant viewer access to the project's current and future assets
until revoked or the project ends. Recipients must sign in with the invited
email verified and explicitly accept. Viewers cannot edit or reshare. Resending
preserves the invitation's original scope, including legacy metadata-only
invitations. Revocation denies later reads/downloads through that invitation;
files already downloaded cannot be recalled, and independent access survives.

The API uses the same service with a personal API key. Keep keys in environment
variables and out of conversation or URLs:

```bash
# Generate once; retain this value when retrying this invitation request.
INVITATION_REQUEST_KEY="$(python3 -c 'from uuid import uuid4; print(uuid4())')"
curl --fail-with-body \
  "https://trymoviestar.com/api/v1/account/cloud/projects/$PROJECT_ID/invitations" \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $INVITATION_REQUEST_KEY" \
  --data '{"email":"recipient@example.com"}'

curl --fail-with-body \
  "https://trymoviestar.com/api/v1/account/cloud/projects/$PROJECT_ID/invitations" \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY"

curl --fail-with-body \
  "https://trymoviestar.com/api/v1/account/project-invitations/$INVITATION_ID/revoke" \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY" \
  -H 'Content-Type: application/json' --data '{}'
```

Create accepts only `{email}`. Resend uses `POST
/api/v1/account/project-invitations/INVITATION_ID/resend` with `{}` and its own
`Idempotency-Key`. List/revoke do not need a key. Every operation checks current
credentials, consent and ownership; account scopes never allow a viewer to
manage someone else's project. Recipient token review/acceptance remains a
signed-in browser flow. API error bodies use `{status, hint?}` for these routes.

Hosted MCP offers `moviestar_invite_project_viewer`,
`moviestar_list_project_access`, `moviestar_resend_project_invitation`, and
`moviestar_revoke_project_access`. Ask:

> Share project PROJECT_ID with recipient@example.com as a viewer, then show
> its access list.

Then, on an explicit revocation request, the agent reads access and passes the
selected `invitation_id` in both `invitation_id` and `confirm_invitation_id`.
Agents generate and retain send/resend keys before the first call and reuse
them after uncertainty. Owner management requires no website handoff; recipient
verification/acceptance remains separate. Older services report unsupported
native sharing rather than silently sending the user to a browser.

### After accepting a project invitation

Recipients can reopen the same ID through each supported surface:

```sh
moviestar cloud projects list --shared
moviestar cloud projects show PROJECT_ID
moviestar cloud projects assets PROJECT_ID
```

Each asset entry can include `download_url` on an updated service. Open it in a
browser signed in to the recipient account, or GET it using that account's
personal API key or approved CLI installation proof. Keep credentials out of
URLs and do not send them to another origin. Older services may omit download
links. The HTTP route is:

```sh
curl --fail-with-body \
  https://trymoviestar.com/api/v1/account/cloud/projects/PROJECT_ID/asset-references/REFERENCE_ID/download \
  -H "Authorization: Bearer $MOVIESTAR_API_KEY" \
  --output image.png
```

Use the service-returned reference ID and the original filename you want to
save. Downloads stream the original bytes and support Range/HEAD. MCP's
`moviestar_list_project_assets` provides the same authenticated download links;
they are browser handoffs, not public storage links or embedded credentials.

Viewer access never permits renaming, adding/removing references, deleting
originals or inviting others. Reads and downloads recheck the account, grant,
project and active reference. Removing a reference makes its saved URL stop
working; another membership has its own ID and access check. Revoking project
access ends new downloads through that project without affecting independent
access through another project. Downloaded copies and transfers already started
cannot be recalled. Account-wide asset discovery remains owner-only; sharing a
project does not expose the owner's other originals. Editor/resharing rights
and attaching another person's assets remain separate work.

## Download project originals directly

On a service supporting `project-download-v1`, download every currently ready
asset in an owned project or an accepted asset-viewer project without a browser:

```sh
moviestar cloud projects download PROJECT_ID --out ./highlights-originals
```

The destination must be a **new folder** with an existing parent. This command
uses your approved CLI installation credential, follows every manifest page,
and streams the files with no browser clicks. It never overwrites existing
paths or follows redirects carrying credentials. It needs no FFmpeg or optional
transcription dependencies.

`cloud-project.json` records the Cloud project ID/title, original asset and
reference IDs, original filenames, safe relative local paths, sizes and computed
SHA-256 hashes. Originals are listed together under `assets/` using their original filenames
where safe, including spaces and Unicode. Duplicate or case-colliding names get
a numeric suffix such as `image (2).png`; existing distinct filenames are
reserved before suffixes are assigned. Unsafe path components are sanitized,
and files cannot escape the destination or overwrite one another. Reference IDs
and original filenames stay in the manifest rather than directory names. The hashes
record the bytes saved locally; they are not a server-supplied checksum.

Only ready account originals are included. This does not export legacy project
sources, Cloud edits, render results, history or invitations, and it does not
initialize a local editing workspace. Use the downloaded paths with local
`moviestar load` and editing commands afterward; downloading itself never loads
or processes media. Images, audio and caption files remain available as original
files for the local commands that support those formats.

If any file is inaccessible, truncated or invalid, the command returns
`download_incomplete`. `.incomplete.json` and any completed or `.part` files stay
in that folder for inspection. An incomplete download has no completed
`cloud-project.json`. Check access and retry into a new folder; automatic resume
and overwrite are not supported. Downloads and pagination check current access;
revocation or reference removal may interrupt the operation. This is a collection
of originals observed during the download, not an atomic snapshot while someone
else adds/removes assets. Already downloaded copies cannot be recalled.

API clients can page through
`GET /api/v1/account/cloud/projects/PROJECT_ID/download-manifest?limit=100`,
following `next_cursor` until null. Authenticate with the same account's personal
key or approved installation. Each item contains `reference_id`, `asset_id`,
`filename`, `kind`, `content_type`, `size_bytes` and a **same-origin**
`download_path`; GET that exact path with your credential. Never send credentials
to another host or put them into a URL.

Hosted MCP exposes `moviestar_prepare_project_download` with `project_id`,
optional `limit` and the unchanged `cursor` from the previous page. It returns
the same manifest and `transfer_status: "not_started"`. Preparing a manifest
does not save files. A developer MCP client with an authenticated file-transfer
capability can fetch the originals into its own environment; clients without
that capability need a signed-in browser download or the CLI. Automatic file
import into ChatGPT and saving onto the user's computer are not established by
this tool and need separate host-specific acceptance. Never paste a credential
into chat. Older services may return unavailable for the manifest route.

For pending invitations received by your account, see [Invitations](invitations.md).

## Project editors and accessible asset references

Projects have three access levels:

| Action | Viewer | Editor | Owner |
| --- | --- | --- | --- |
| Open the project and download assets they can access | Yes | Yes | Yes |
| Add/remove asset references and rename the project | No | Yes | Yes |
| Invite people, change roles, revoke access, delete the project | No | No | Yes |

Project editing changes the collection and title. It never grants permission to
modify or delete another person's original asset. Removing a reference preserves
the original and its other project memberships. There is no manager role.

Owners can invite editors or change a member's current project role:

```bash
moviestar cloud projects share PROJECT_ID recipient@example.com --role editor
moviestar cloud projects access PROJECT_ID
moviestar cloud projects set-access-role INVITATION_ID editor --expected-role viewer
moviestar cloud projects set-access-role INVITATION_ID viewer --expected-role editor
```

`--expected-role` comes from the access list. Preserve it on retry after an
uncertain reply; stale changes cannot overwrite a different role. Revoked
invitations cannot be revived by changing role. Existing metadata-only invitations
must be revoked and replaced before assigning editor access. Invites default to
viewer. An older server without editor support rejects `--role editor` before
sending an invitation.

Editors reopen shared projects with the ordinary `list --shared` and `show`
commands, then use `rename`, `add-asset` and `remove-asset`. Renaming retains the
existing `--expected-title` precondition. Revocation or demotion takes effect on
the next operation, including retries.

You may reference an original you own or can independently access:

```bash
moviestar cloud assets list --shared
moviestar cloud projects add-asset PROJECT_ID ACCESSIBLE_ASSET_ID
```

Adding an original you own authorizes project members under that project's
sharing rules. Referencing someone else's original preserves its audience.
For example, Alice shares an original with Bob. Bob references it in a project
shared with Carol. Carol does not gain access merely from Bob's reference: she
needs an independent grant authorized by Alice. Removing the source grant or
reference ends access through that path. Other valid paths survive. No original
is copied, and borrowed references cannot create chains of new access grants.
Unavailable originals are omitted from each person's project asset list and
download manifest; their private filenames or storage paths are not disclosed.

The same rules apply to Web, API and hosted MCP. Owners invite with the API's
`{email, role}` body (viewer is the default). Owner-only role changes use
`POST /api/v1/account/project-invitations/INVITATION_ID/role` with
`{role, expected_role}`. Current invitation/list/review results include `role`.
Hosted MCP adds `moviestar_invite_project_member` with viewer/editor selection and
`moviestar_set_project_role`; the existing viewer invitation tool remains
viewer-only. Account-wide scopes never authorize access management on another
person's project.
