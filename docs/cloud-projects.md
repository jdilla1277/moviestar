# Personal Cloud projects

Use the explicit `cloud` command group to create and reopen projects owned by
your connected account. Your service must have personal Cloud project access
enabled. This initial client supports project metadata; inspect capabilities
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

Each project currently reports `id`, `title`, `state`, `owner_id`, and
`current_revision`. Missing source, job, or result collections are unavailable
through this metadata contract; they do not establish that the project has no
media. Local editing commands retain their current behavior and operate on
local workspaces. Cloud metadata commands require no local FFmpeg execution or
transcription and never upload media or select a local workspace. The package's
base installation omits the optional local transcription stack.

## Pagination and recovery

```sh
moviestar cloud projects list --limit 20
moviestar cloud projects list --limit 20 --cursor NEXT_CURSOR
```

Pass the previous response's opaque `next_cursor` unchanged; stop when it is
null. Project IDs are explicit, so you can reopen an ID outside the first page.

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
`GET /api/v1/account/cloud/capabilities` with that key. Another account cannot
read an owned private project by knowing its ID; missing and inaccessible
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
