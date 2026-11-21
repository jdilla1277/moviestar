# Inspect a saved Cloud mix without changing it

On a service advertising `saved_audio_mix` support, the existing edit commands
can inspect a persisted personal revision and check source gain/mute changes.
They do not save, render, download media or analyze loudness.

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
does not add a customer save/import path. An existing admitted personal saved
revision and a capable configured engine bridge are prerequisites. Hosted
activation, saved-edit creation, rendering and listening acceptance are separate.
