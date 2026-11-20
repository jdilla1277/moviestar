# Check an initial Cloud edit without saving

These commands require a service that advertises `editing` with
`contract_version: cloud-editing-v1`. They use your existing approved personal
account connection and do not select a local project, download media or require
FFmpeg/Whisper on the client. An older or unconfigured service reports that
editing is unavailable.

The first supported journey is an **unsaved baseline** for an owned personal
project whose active inputs are ready video account-asset references owned by
the same account. Saved personal edits, project-source uploads, borrowed inputs
and other asset types are not supported in this slice; the service rejects them
rather than silently dropping state. Create a project and attach existing assets
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
apply, save/reopen, history, undo, preview or final rendering. A successful dry
run is neither a save nor a render grant. Hosted deployment and real-account
acceptance depend on the service's configured, pinned metadata engine bridge.

A missing audio codec in historical ready-video metadata remains unreported,
not inferred silence. Timeline reads/trim validation need only measured duration
and dimensions; they do not establish audio-track presence or routing.
