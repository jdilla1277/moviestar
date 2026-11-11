# Personal Cloud projects

Use the explicit `cloud` command group to create and reopen projects owned by
your connected account, or discover projects shared with it when supported. Your service must have personal Cloud project access
enabled. The client supports project metadata and, when advertised, existing
account-asset references; inspect capabilities
for the operations your service currently advertises.

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
current access. Viewer invitations currently permit project metadata only;
they do not grant access to assets, edits, rendering, downloads or resharing.

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
pages return `{reference_id, asset}` entries. Legacy project-source uploads and
assets owned by other people are not supported in this initial catalog. An
accepted viewer project invitation does not grant asset access or membership
changes. Unsupported services fail clearly before sending a mutation.

`add-asset` generates and saves a retry key before sending. Rerun the same command
after a lost response, or provide the original `--idempotency-key`. Its retry
state is isolated by service, credential, project and asset. A `reference_removed`
response means that attempt's membership remains removed; only use the suggested
new key if deliberately adding it again. No fresh-key write happens
automatically. If recovery storage fails, preserve the returned key and inspect
the project's assets before repairing retry files beside your account profile.

In the website, open an owned project, choose **Add existing assets**, select a
ready original, and reopen the other project to add it there too. **Remove from
project** removes only that membership. The account picker is separate from
**Earlier project uploads**, which remain under their existing source contract.

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
