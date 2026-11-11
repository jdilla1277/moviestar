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
