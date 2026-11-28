# Inspect a saved Cloud mix without changing it

On a service advertising `saved_revision` read/validation support, the existing edit commands
can inspect a persisted personal revision and check source gain/mute changes.
These read/validate commands do not save, render, download media or analyze loudness.
The separate [one-video trim/undo path](cloud-edits.md#save-one-trim-and-undo)
can produce saved revisions; audio patches remain validation-only.

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
the request cannot supply its own spec or dependency manifest. This capability
does not add an audio save/import path. An existing admitted personal saved
revision and a capable configured engine bridge are prerequisites. Hosted
activation, rendering and listening acceptance are separate.
