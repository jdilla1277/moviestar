# Read, validate and save Cloud dialogue controls

On a service advertising `saved_revision` read/validation support, the existing edit commands
can inspect a persisted personal revision and check source gain/mute changes.
These read/validate commands do not save, render, download media or analyze loudness.
The separate [one-video trim/undo path](cloud-edits.md#save-one-trim-and-undo)
can produce saved revisions. Source gain/mute saves require the separate
capability and account enrollment described below.

```sh
moviestar cloud edits capabilities
moviestar cloud edits show PROJECT_ID
```

For saved state, expect `status: opened`, the exact `current_revision` and
`revision.spec.audio_mix`. `revision.resolved` reports source routing, normalized
tracks, placement/trim/fades/loop/ducking and undo depth. Unreported source audio
metadata stays unknown; configured controls are not evidence of an audible
track or achieved loudness. Multiple uncomposed sources cannot be interpreted
as a single mix.

## Read the saved dialogue settings

Save the complete response, then use this optional view to answer audio questions:

```sh
moviestar cloud edits show PROJECT_ID > saved-mix.json
jq '.revision | {revision_id: .id,
  source_audio: .resolved.audio_mix.source_audio,
  dialogue_routing: .resolved.routing,
  source_audio_availability: .resolved.source_audio_availability,
  tracks: .resolved.audio_mix.tracks,
  loudness_status: .resolved.loudness_status}' saved-mix.json
```

This is a view of the existing response, not a second audio document or endpoint.
`source_audio.gain_db` is relative gain: 0 means unchanged, negative values turn
it down; `muted` is the saved mute setting. `fade_in`/`fade_out` are seconds.
These controls affect the already-routed dialogue. They do not select a camera
or microphone. An empty `tracks` list means no independently added layers.

`routing` identifies stable `source_id`, scene and result-time ranges, with
`source_slices_ms` on the original source clock. Map a source ID to its separate
asset/reference IDs in `revision.dependency_manifest`. A null routing source is
not a hidden default camera choice. Audio availability distinguishes:

- `stream_reported`: authorized metadata reports an audio stream; speech or
  audible/non-silent content has not been verified.
- `no_stream_in_metadata`: the metadata explicitly reports no audio stream.
- `unreported`: historical metadata has not reported audio presence; it is unknown.

Loudness remains null/`not_measured`. This read performs no listening, loudness
analysis or rendering. If the project returns `no_saved_edit`, there is no saved
mix to report; do not invent default saved settings.

To reopen the exact settings later:

```sh
REVISION=$(jq -r '.revision.id' saved-mix.json)
moviestar cloud edits show PROJECT_ID --revision "$REVISION"
```

`revision.id` is the opened version; `current_revision` separately identifies
where the project is now. Opening history never moves it. The CLI rejects missing
or contradictory saved controls/routing/track evidence as `invalid_response`;
it does not fill missing controls with defaults.

The same read is `GET /api/v1/account/cloud/projects/P/edit` or
`GET /api/v1/account/cloud/projects/P/revisions/R`, and MCP
`moviestar_get_edit({project_id,revision_id?})`. Returned complete revision data
agrees across these surfaces. For a local project, `moviestar audio source`
without options reads the same source controls and `moviestar audio dump`
exports the editable mix; local file paths and Cloud asset references differ.

## Check a proposed change (separate from reading)

Copy `current_revision` from the read into this request file:

```json
{
  "contract_version": "cloud-editing-v1",
  "expected_base_revision": "REVISION_UUID_FROM_SHOW",
  "operation": {"type": "audio_source", "gain_db": -6}
}
```

```sh
moviestar cloud edits validate PROJECT_ID --request audio.json
moviestar cloud edits show PROJECT_ID
```

Expect `would_apply` and a candidate mix, followed by the same original saved
revision/mix on the second read. Candidate gain affects already-routed source
audio, not camera selection. Only supplied gain/mute controls change; track and
fade settings and routing remain. Gain must be a finite number from -60 through
+24 dB; `muted` is strictly boolean. Supply at least one of them. Unknown fields,
stale revisions, unavailable dependencies and unsupported states fail clearly.
A no-op reports `changes: false`, no changed fields and unchanged undo depth.

The API and hosted MCP use the same project edit read/validate boundary. No
separate audio endpoint, local project selection, processing charge or saved
Activity event is created. The service reads authoritative persisted state;
the request cannot supply its own spec or dependency manifest. Validation
never saves the candidate; saving requires the separate apply step below. There
is no spec-import path here. An existing admitted personal saved revision and
a capable configured engine bridge are prerequisites. Hosted activation,
rendering and listening acceptance are separate.

For a saved dialogue proposal, compare `projected_resolved.audio_mix.source_audio`
with the current read; keep `current_undo_remaining` separate from projected undo
depth. A real proposed change has `changes:true`/`changed_fields:["audio_mix"]`;
reasserting existing controls is a no-op with no extra projected journal entry.
`candidate.spec` is unsaved. `effects` must report zero saved revisions, Activity
events, processing jobs and customer usage. Reopen the project after checking:
the current revision and controls must still match the first read. The CLI
rejects missing/mismatched candidate evidence instead of confirming it.

## Save source gain/mute and undo

When `editing.write_operations` includes `audio_source`, and the account is
separately enrolled, the same apply command saves source-layer controls for one
owned saved video with no independently added tracks. The initial unsaved
baseline still supports trim only. No source selector, fades or track editing
is added. Omitted controls and existing fades/routing/timing stay unchanged.

```sh
moviestar cloud edits show PROJECT_ID
moviestar cloud edits validate PROJECT_ID --request audio.json
moviestar cloud edits apply PROJECT_ID --request audio.json
moviestar cloud edits show PROJECT_ID
```

Use the actual current revision in `audio.json` with operation
`{"type":"audio_source","gain_db":-6}` or `{"type":"audio_source","muted":true}`;
`muted:false` unmutes. `committed` returns the new immutable revision, saved spec,
resolved mix, read-back verification and one unmetered saved-edit Activity event.
A no-op returns `unchanged`, retains a retry receipt and creates no new revision
or event. Existing originals and historical versions stay intact.

Reopen with `show --revision REVISION_ID`. Undo with a new request using the
current base and `operation:{"type":"undo"}`: the whole previous command is
restored, including the complete previous mix. Undo creates a new immutable
saved version; it does not rewrite earlier specs or rendered outputs.

The existing CLI durable retry mechanism applies unchanged. After a lost reply,
use `cloud edits apply PROJECT_ID --recover`; retain the original request/key.
Never silently refresh its base or use a new key to resolve uncertainty. Replays
recover the original revision plus the separately reported current pointer,
with no duplicate saved-edit event or usage. Stale new writes reject safely.

**Saving changes instructions; rendering produces the audible video.**
[Render the exact saved revision](cloud-renders.md) through separately enrolled
qualified workers to apply source gain/mute in preview or full-quality output.
Saving schedules no processing job and reserves no Cloud processing/output
allowance. Unsupported mixes are rejected rather than silently dropping controls.
Previously generated videos keep their original settings and URLs.
