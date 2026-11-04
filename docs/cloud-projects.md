# Personal Cloud projects

Use the explicit `cloud` command group to create and reopen projects owned by
your connected account. Your service must have personal Cloud project access
enabled. This initial client supports project metadata; inspect capabilities
for the operations your service currently advertises.

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

Choose a new key for a deliberate new project. Keep the key and exact title so
you can recover the result if the connection drops:

```sh
moviestar cloud projects create "Highlights" --idempotency-key highlights-1
moviestar cloud projects list
moviestar cloud projects show PROJECT_ID
```

Replace `PROJECT_ID` with `project.id` returned by create. Reads return the
project metadata or `{items, next_cursor}` directly. Create returns a
confirmation containing `status`, `project`, `idempotency_key`, and a next-step
hint. The project ID persists across CLI processes and the account website.
You can open **My projects** on the same service's account page and select it,
or bookmark `/account/projects?project=PROJECT_ID` on that service.

Each project currently reports `id`, `title`, `state`, `owner_id`, and
`current_revision`. Missing source, job, or result collections are unavailable
through this metadata contract; they do not establish that the project has no
media. Local editing commands retain their current behavior and operate on
local workspaces. Cloud metadata commands require no local FFmpeg execution or
transcription and never upload media or select a local workspace. The package's
standard installation dependencies are unchanged by this client.

## Pagination and recovery

```sh
moviestar cloud projects list --limit 20
moviestar cloud projects list --limit 20 --cursor NEXT_CURSOR
```

Pass the previous response's opaque `next_cursor` unchanged; stop when it is
null. Project IDs are explicit, so you can reopen an ID outside the first page.

After a lost create response, repeat the **exact original title and key**.
The service returns the same project. A different title with that key produces
`idempotency_conflict`. A later project rename does not change the original
creation key or payload: replay may return the project's current title.

Errors are structured JSON with a stable `status` and recovery `hint`.
An unknown creation outcome includes the original key; a new key could create
a second project. There is no automatic write retry. Invalid/revoked credentials
direct you to `account status`; account setup/consent failures require the human
to finish the existing account flow. Missing/inaccessible projects share the
same unavailable response. A disabled service returns
`personal_cloud_unavailable`; signing in again does not enable that service.

Local files and account credentials are preserved on all these failures.
