# Cloud delivery CLI design rehearsal

This **mock only** surface explores proposed `moviestar cloud renders` and
`moviestar cloud artifacts` commands. It is outside the distributed package,
is not registered in `moviestar`, and makes no network or media-processing calls.
No credentials are needed. Production availability is not established here.

The merged initial-edit commands from public #260 provide real unsaved timeline
reads and trim validation through the existing Cloud connection. They do not
save a revision, render the candidate or download media. Future Delivery commands
must reuse that connection/transport foundation and consume an authoritative saved
revision once available. This standalone mock remains synthetic and is not wired
into those real commands.

```sh
app/.venv/bin/python examples/cloud_delivery_mock.py --help
app/.venv/bin/python examples/cloud_delivery_mock.py --state /tmp/delivery-design.json capabilities
```

Choose a new disposable `--state` path per rehearsal. It stores synthetic
jobs/keys/artifacts, never credentials or media. Keep it across fresh CLI
invocations to rehearse reconnect. Use one process at a time; this journal is
not a concurrent/transactional service. Remove it when done.

Suppose project `00000000-0000-4000-8000-000000000001` has two ready personal
inputs and saved trim/join revision `00000000-0000-4000-8000-000000000101`:

```sh
app/.venv/bin/python examples/cloud_delivery_mock.py --state /tmp/delivery-design.json \
  renders submit 00000000-0000-4000-8000-000000000001 \
  --revision 00000000-0000-4000-8000-000000000101 \
  --kind preview --idempotency-key preview-retained-key --lose-reply
```

Retry without `--lose-reply` with identical project/revision/kind/key. The key
is scoped to this synthetic account and render family; changed payload conflicts.
The job's revision never follows latest. Follow its `renders show` hint. Use
`fixture complete PROJECT JOB` to **assume** independent verification and
immutable promotion; the mock does neither. Terminal reads lead to
`artifacts show` and `artifacts download`.

Fixture completion binds a synthetic producing `attempt_id` UUID to both the job
and artifact. Queued jobs/cancellation have no started attempt (`attempt_id: null`,
ordinal zero). Canonical preview/final keys yield attempt UUIDs
`1879251c-77b6-54fb-bbc7-6789df0c34fb` and
`9de1117a-3db2-57da-b61f-82f32d692cac`, matching API/MCP fixtures. These identities
do not imply measured work, usage, price or provider costs. Use a fresh disposable
journal after fixture-schema changes; older mock journals are not migrated.

Job/artifact `engine_version` explicitly names the checked-in worker commit as a
synthetic reference, not an installed runtime measurement. Artifact
`retention: {status: "unfrozen", expires_at: null}` leaves retention unknown; it
does not promise unlimited storage. Full fixture equality is not production
schema completeness or media acceptance.

Application failures retain canonical
`{error:{code,message,details,retryable},request_id}` fields matching API/MCP
fixtures. A separate top-level `hint` and retained key/request explain CLI
recovery. `submission_unconfirmed` is a client transport-uncertainty outcome,
not a server rollback receipt. Mock request IDs are stable synthetic labels;
Click syntax/argument errors remain normal CLI usage diagnostics.

For the revised edit, use revision `00000000-0000-4000-8000-000000000102`, kind
`final` and a new retained key. Old preview identities remain unchanged.
`artifacts list PROJECT --revision REVISION` discovers that revision's outputs.
`review PROJECT ARTIFACT --at-ms 500` shows an artifact-bound **result-time**
target without approval, frame display or perception.

Download returns `would_save_to`, `file_saved: false`, `media_delivered: false`;
no file or URL exists. Production must save without overwrite, verify size and
SHA-256, then return `out`. Fixture hashes are labels, not media checksums.

`--actor revoked` and `--actor foreign` inject denial even for replay.
`renders cancel` cancels queued work; subsequent `fixture complete` fails.
Running cancellation/cleanup/lease recovery are not simulated.
`fixture expire PROJECT ARTIFACT` makes download fail without another job.
Real authorization, grants, workers, quota, Activity and realistic media need
separate acceptance. Capabilities keep live rendering, composition inspection
and clip/batch unavailable. Ready-asset inspection is not unrendered edit
inspection. Baseline internal transitions render in export; edge fades and
transitions in watch/inspect/screenshot remain unavailable.

Run `app/.venv/bin/python -m pytest app/tests/test_cloud_delivery_mock.py -q`.
Tests prove mock identity/replay/error semantics only, not service delivery.
