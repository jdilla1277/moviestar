# Read, check, save and undo a Cloud trim

Services can separately advertise [saved-mix inspection and audio dry runs](cloud-saved-audio.md)
through the same commands. Audio patches remain validation-only; the one-video
trim/undo save path is described below. The initial timeline journey below
continues to use null saved revision and trim-only validation.

These commands require a service that advertises `editing` with
`contract_version: cloud-editing-v1`. They use your existing approved personal
account connection and do not select a local project, download media or require
FFmpeg/Whisper on the client. An older or unconfigured service reports that
editing is unavailable.

The first supported journey is an **unsaved baseline** for an owned personal
project whose active inputs are ready video account-asset references owned by
the same account. This initial-state path does not accept caller-supplied saved specs,
project-source uploads, borrowed inputs or other asset types; it never silently
drops state. Saved audio-mix reads require the separately advertised capability
above. Create a project and attach existing assets
through [Cloud projects](cloud-projects.md).

```sh
moviestar cloud edits capabilities
moviestar cloud edits show PROJECT_ID
```

Expect `status: no_saved_edit`, `current_revision: null` and `revision: null`.
`baseline.spec` contains the existing engine's portable initial spec;
`baseline.resolved.sources` identifies each source's selected original ranges
and result duration in integer milliseconds. Multiple sources do not imply a
composition, scene order, camera or dialogue choice. No media is decoded.
`source_id`, `asset_id` and `reference_id` are separate identities; use the
returned source ID in operations. `asset:UUID` is a symbolic reference, not a
caller-supplied path or download URL. Unknown content hashes remain null;
structural metadata is not verified execution input.

For a source at least 40 seconds long, save this as `trim.json`, replacing
`SOURCE_ID_FROM_SHOW` with its actual ID:

```json
{
  "contract_version": "cloud-editing-v1",
  "expected_base_revision": null,
  "operation": {
    "type": "trim",
    "source_id": "SOURCE_ID_FROM_SHOW",
    "time_space": "source_result",
    "from_ms": 10000,
    "to_ms": 40000
  }
}
```

```sh
moviestar cloud edits validate PROJECT_ID --request trim.json
moviestar cloud edits show PROJECT_ID
```

Expect `status: would_apply` and a 30,000 ms candidate result for that source.
Times describe the current edited source clock, initially the original source.
The existing engine owns bounds/tolerance and source-time mapping. Inspect
`projected_resolved` and `candidate` as proposed state;
`current_undo_remaining` still describes the unchanged baseline. A projected
journal entry was computed in memory, never saved. A second `show` returns the
unchanged baseline. Requests are limited to 4096 bytes and 16 sources; unsupported
operations fail explicitly. A non-null expected revision conflicts with an
unsaved project. Inputs changing during validation require a fresh read.

The same service resources are available through:

- API: `GET /api/v1/account/cloud/projects/P/edit` and
  `POST /api/v1/account/cloud/projects/P/edit/validate` with the JSON body above.
- MCP: `moviestar_edit_capabilities`, `moviestar_get_edit({project_id})` and
  `moviestar_validate_edit({project_id,request})`.

API installation requests need `Authorization: Bearer …` and
`x-moviestar-client: moviestar-cli-v1`; the CLI handles both. Browser POSTs
require the same origin. API keys and hosted MCP use their existing authenticated
personal-account adapters. The service checks current project/input access
before engine work and again before returning its result. Service failures use
`error: {code,message,details,retryable}` and `request_id`; CLI failures exit
nonzero, MCP failures set `isError: true`. Local configuration/request errors
have no server request ID.

This journey creates no saved revision, Activity event, processing job or usage
reservation, and leaves the original assets unchanged. It does not provide
apply, saving, history, undo, preview or final rendering. A successful dry
run is neither a save nor a render grant. Hosted deployment and real-account
acceptance depend on the service's configured, pinned metadata engine bridge.

A missing audio codec in historical ready-video metadata remains unreported,
not inferred silence. Timeline reads/trim validation need only measured duration
and dimensions; they do not establish audio-track presence or routing.

## Save one trim and undo

On an explicitly enrolled service/account, `cloud edits apply PROJECT --request
trim.json` saves one owned video trim. Read and validate first, then use exactly
the returned current revision as `expected_base_revision` (null before any save).
`operation` remains `{ "type":"trim", "source_id":"ID_FROM_SHOW",
"time_space":"source_result", "from_ms":10000, "to_ms":40000 }`.
A second trim uses the edited source clock: 5–20 seconds after 10–40 selects
original seconds 15–30.

The CLI saves a generated retry key AND the exact request privately before
sending. If the response is lost, use `cloud edits apply PROJECT --recover` in a
fresh process using the same account profile. Do not change the base or key.
An explicit `--idempotency-key KEY` plus the original request can recover across
credential renewal for the same authorized account; automatic profile recovery
is scoped to the original credential. A changed pending request or different explicit key is rejected before sending
while the original outcome is uncertain. Recover that original request first. If it is rejected and you deliberately
want to move on, use `cloud edits apply PROJECT --abandon-pending`.

Abandonment is local and works without contacting the service. It durably
archives the original exact body/key and returns an explicit recovery command;
it stops automatic retries, **not a possible server save**. `server_outcome`
remains `unknown`. Read the current edit, then correct the bounds or deliberately
rebase with its current revision and submit a new command/key. The archived
request remains recoverable with its original key/body after resolving any
later pending request. No deletion of recovery files or credential change is
needed. Do not combine `--abandon-pending` with another apply/recover intent.

`cloud edits show PROJECT --revision REVISION` reopens that exact version;
`current_revision` separately identifies the current pointer. Undo uses the same
apply command with a JSON body whose operation is `{ "type":"undo" }` and whose
base is the current revision. It saves a new immutable version, popping one
complete engine journal entry. Repeated undo walks backwards, without toggling.
Old versions remain readable. A full-current-range trim is unchanged and adds no
journal, revision or Activity event; its retry receipt remains recoverable.

Check `revision.resolved.sources` for selected source ranges and result duration,
and `undo_remaining` for available commands. `verification.saved_state` records
server read-back of the saved instructions; document/manifest SHA-256 identifies
those instructions, **not media correctness**. `input_bytes` and `output` remain
`not_checked`, and `execution_ready` is false. This path does not render, download,
or establish verified media dependency pins. Each real change creates one
unmetered saved-edit Activity record; replay, read and validation create none.
Writes are disabled by default. Audio gain/mute remains validation-only.

Local `moviestar trim --dry-run ...` and `moviestar undo --dry-run` show proposed
ranges without writing the spec. Ordinary local commands stay local.

## Explicit personal API-key connection

For a developer client using a website-issued personal key, set both
`MOVIESTAR_API_KEY` and `MOVIESTAR_CLOUD_URL` (the exact service base, such as
`https://YOUR_PREVIEW_HOST/api/v1/account/cloud`). Load the key from your secret
manager or a private local file; never put it in command arguments or chat.
Then run the same `moviestar cloud edits` commands. The service rechecks that
key's account/project/input authority for every request and replay.

Explicit key mode does not read or modify your installation profile/keyring.
A missing/malformed key or service URL fails without installation fallback.
With neither variable set, existing installation behavior is unchanged. Use a
Preview-issued key with Preview; Production keys belong to Production.

## Origin metadata in downloaded specs

A compatible Cloud service can expose an authorized saved revision as
`spec.json`. The editing fields keep the existing local format (`version: "0.2"`).
The optional `provenance` object retains:

- `project_id` and `base_revision_id`: the Cloud project and exact saved version
  this file came from;
- `base_document_sha256`: the service's fingerprint of the original saved
  document/dependencies/engine, or `null` when unreported;
- `engine_version`: the exact recorded engine commit;
- `contract_version`: `cloud-editing-v1`.

This is origin metadata, not authorization or a claim that later local changes
were already saved in Cloud. The base fingerprint is **not** the checksum of the
JSON download with its origin metadata added. Local commands validate and
preserve provenance through editing, `spec --reset`, and undo. Older specs
without provenance remain valid. Unknown provenance fields and malformed
identifiers are rejected.

The metadata does not itself download media, create a local workspace, import
an edit or synchronize local checkpoints. Source files and the authorized asset
mapping are still required for local/Cloud continuity. No render is requested by
reading or downloading a saved spec.
