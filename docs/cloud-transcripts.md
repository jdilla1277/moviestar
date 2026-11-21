# Supplied transcripts in Cloud projects

Attach an SRT/VTT transcript to one existing media reference in your owned
Moviestar project. This does not transcribe audio or consume transcription
minutes. Retained transcript bytes count toward storage. Importing does not
select a transcript in a saved edit, generate captions or render video.

This requires a compatible service advertising `project_transcripts` with
`contract_version: project-transcripts-v1`, account enrollment and ready owned
video/audio media in an owned project. Older services and unenrolled accounts
cannot import. Shared/borrowed media is outside this first workflow.

```sh
moviestar cloud capabilities
moviestar cloud projects assets PROJECT_ID
```

Use the media entry's **reference_id**, not its asset ID or filename. Caption
cues must use the original source clock and fit within its duration. Supply
UTF-8 `.srt` or `.vtt`; plain text and native provider JSON need a different
import/alignment path. Cue-interpolated timing is not verified word alignment.

```sh
moviestar cloud projects transcripts import PROJECT_ID \
  --reference REFERENCE_ID --captions original.vtt \
  --idempotency-key original-1
```

Retain the returned `association`, `transcript.id` and SHA-256. Keep the exact
project/reference/format/file bytes/key for retry. The CLI saves a private input
snapshot before POST; an uncertain reply reports its location as `out` and keeps
the request key. Snapshots contain transcript text, so keep them private. Do not
change the file or generate a new key merely to recover a lost reply.

```sh
moviestar cloud projects transcripts show PROJECT_ID \
  --reference REFERENCE_ID --version ORIGINAL_TRANSCRIPT_UUID

# An intentional correction gets a NEW key and version.
moviestar cloud projects transcripts import PROJECT_ID \
  --reference REFERENCE_ID --captions corrected.vtt \
  --idempotency-key correction-1

# The original exact read still returns the original words.
moviestar cloud projects transcripts show PROJECT_ID \
  --reference REFERENCE_ID --version ORIGINAL_TRANSCRIPT_UUID
moviestar cloud projects transcripts list PROJECT_ID \
  --reference REFERENCE_ID --limit 20
```

Follow `next_cursor` unchanged with `--cursor` until null. Pass `items[].id` to
`show --version`; `version` is the ordinal. There is no default-latest read or
automatic edit selection. Reads and accepted replay remain available when new
intake is disabled, subject to current permissions and deletion rules. Removed
memberships and missing/deleted/inaccessible versions fail without substituting
another version or membership.

API clients use the same account credentials and
`/api/v1/account/cloud/projects/PROJECT_ID/asset-references/REFERENCE_ID/transcripts`:
POST JSON `{format, text}` with `Idempotency-Key`, GET paginated metadata, and
GET `/TRANSCRIPT_VERSION_ID` for exact original text/hash/timing provenance.
Preserve BOM/newlines when constructing the JSON text value.

Hosted MCP tools are `moviestar_import_project_transcript`,
`moviestar_list_project_transcripts` and `moviestar_get_project_transcript`.
They use `project_id` and `reference_id`; import also takes `format`, `text` and
`idempotency_key`; exact read requires `transcript_version_id`. Agents should
retain their request/key before invocation and recover the same version across
surfaces. Tool discovery alone does not prove account enrollment or live import
acceptance. No credentials belong in chat or transcript files.
