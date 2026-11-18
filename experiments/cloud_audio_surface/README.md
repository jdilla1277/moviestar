# Cloud audio surface experiment

This is a runnable **canned design mock**, outside the distributed package.
It does not install commands, contact Cloud, save edits, authenticate, validate
with the engine, render, measure loudness or play media. Each invocation is
independent. Responses expose `mock: true` and the fixture limits. The proposed
wire contract and operation name require integration with the shared editing
service before they can become supported public commands.

The first slice adjusts the **already-routed source-audio layer**, matching local
`moviestar audio source`. It does not choose a camera. Scene routing resolves
dialogue first; gain/mute controls that finished stream. Independent music,
voiceover, placement, trimming, fades, loops, ducking and loudness execution are
later slices. Source controls preserve existing tracks and routing.

Run from the repository root after normal development setup:

```bash
app/.venv/bin/python experiments/cloud_audio_surface/surface.py --help
app/.venv/bin/python experiments/cloud_audio_surface/surface.py show
app/.venv/bin/python experiments/cloud_audio_surface/surface.py validate --help
app/.venv/bin/python experiments/cloud_audio_surface/surface.py apply --help
```

The proposed installed CLI flow is `moviestar cloud edits show/validate/apply`.
API and MCP are the same shared edit boundary, not separate audio endpoints:

| Task | Personal API (under `/api/v1/account/cloud`) | Hosted MCP |
|---|---|---|
| Read current edit | `GET /projects/{p}/edit` | `moviestar_get_edit` |
| Reopen exact revision | `GET /projects/{p}/revisions/{r}` | `moviestar_get_edit` with `revision_id` |
| Validate candidate | `POST /projects/{p}/edit/validate` | `moviestar_validate_edit` |
| Save/undo | `POST /projects/{p}/edit/mutations` | `moviestar_apply_edit` |

`--surface api|mcp` prints the proposed transport; it does not exercise HTTP or
the MCP protocol. `spec` in fixture reads is an audio-only excerpt. Production
reads must expose the entire portable edit and pinned dependencies. Stable
engine `source_id` is distinct from `account_asset` `asset_id` and project
`reference_id`; the fixture includes both camera dependencies.

## Canonical journey

For readable commands, define these shell variables:

```bash
audio_python=app/.venv/bin/python
audio_mock=experiments/cloud_audio_surface/surface.py
audio_requests=experiments/cloud_audio_surface/requests
```

Fixture project: `00000000-0000-4000-8000-000000000010`.

| Revision ending | Intent | Undo entries |
|---|---|---|
| 101 | Source gain 0 dB, unmuted | 0 |
| 102 | Source gain -6 dB, unmuted | 1 |
| 103 | Source gain -6 dB, muted | 2 |
| 104 | Undo mute; source gain -6 dB, unmuted | 1 |

The fixture current pointer is always 103 for `show`, including historical
reads. Apply/undo outputs project their own canned resulting pointer. That
fixed pointer is a mock limitation, not a persistent lifecycle simulation.

```bash
"$audio_python" "$audio_mock" show --revision 00000000-0000-4000-8000-000000000101
"$audio_python" "$audio_mock" --surface api validate --request "$audio_requests/gain.json"
"$audio_python" "$audio_mock" --surface api apply --request "$audio_requests/gain.json" --idempotency-key retained-gain
"$audio_python" "$audio_mock" show --revision 00000000-0000-4000-8000-000000000102
"$audio_python" "$audio_mock" --surface mcp apply --request "$audio_requests/mute.json" --idempotency-key retained-mute
"$audio_python" "$audio_mock" apply --request "$audio_requests/undo.json" --idempotency-key retained-undo
```

Gain/mute operations carry only supplied fields; absent fields remain unchanged.
An explicit `muted: false` un-mutes. Gain is relative dB, finite and within local
-60 through +24 dB bounds. At least one supported change is required. Production
must reject unknown properties, invalid booleans/numbers and unsupported options
before saving. The mock accepts only the three fixture requests; fixture
matching is **not** implementation of that validator. `undo` uses the shared
atomic command journal and creates a new immutable revision.

Validation is a dry run with zero save, journal, Activity, processing and usage
effects. `current_undo_remaining` reports current undo depth;
`projected_resolved` describes the candidate only after apply. Apply creates one
saved revision and one unmetered `saved_edit` event;
it starts no render. Historical reads and retained-key replay create neither.
These counters are expected contract behavior printed by the mock, not database
or billing evidence.

## Retained-key recovery and failures

Retain the exact request/body/base/key before sending. After an uncertain reply,
retry them unchanged; do not automatically fetch a newer base. Replay returns
the original saved revision plus a separately named current pointer, which may
already have advanced.

```bash
"$audio_python" "$audio_mock" --scenario lost-response apply --request "$audio_requests/gain.json" --idempotency-key retained-gain
"$audio_python" "$audio_mock" --surface mcp --scenario retry apply --request "$audio_requests/gain.json" --idempotency-key retained-gain
"$audio_python" "$audio_mock" --scenario stale apply --request "$audio_requests/gain.json" --idempotency-key another-edit
```

The lost-response fixture exits nonzero, reports unknown client outcome and
`effects: null`. Separate `fixture_expected_effects` models one committed save
from the mock's omniscient perspective; it is not client knowledge. Retry
recovers 102 while current is 103, with zero new
effects. `stale`, `changed-key`, `unauthorized` and `disabled` are canned failures
with nonzero exits and actionable hints. Disabled admission still permits saved
reads. None of these scenarios establishes actual concurrency or authorization.

## Verification boundary

Run `app/.venv/bin/python -m pytest experiments/cloud_audio_surface/test_surface.py -q`.
Tests assert envelope/transport parity, dry-run effects, exact reads, mute undo,
replay and explicit fixture limits. Agent friction testing should use only
command help, fixture request inputs and command outputs.

Actual acceptance still needs the shared personal-account revision service and
delivery path: API create, CLI reopen, MCP revise, fresh-session exact reads,
lost committed response, stale-write and access rejection, matching Web Activity,
and real saved preview/final files made with the same inputs as local. Verify
dialogue routing and joins, then listen to continuity, gain/mute and sync.
Measure achieved loudness and true peak on the identified output independently.
`loudness: null` / `not_measured` in these fixtures is intentional. Metadata,
configured gain or a render URL cannot establish listening acceptance.
