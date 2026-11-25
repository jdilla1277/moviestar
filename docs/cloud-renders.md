# Preview and deliver an exact saved Cloud edit

Local `export` and `export --preview` stay local. Remote renders explicitly use
one immutable saved version with the same engine and x264 encoder.
Saving or undoing instructions does not automatically render.

The initial Cloud profile supports one owned ready MP4, trim-only instructions,
at most 30 seconds of result and a 64 MiB input. Layouts, captions and altered
audio are not admitted yet. Two output kinds are supported when enrolled:

| Kind | Local equivalent | Output and processing bounds |
| --- | --- | --- |
| `preview` | `export --preview --video-encoder x264` | 480px high, 16 MiB, 4 minutes |
| `final` | `export --video-encoder x264` | Full source dimensions, padded to even pixels, maximum 1920px edge; 64 MiB, 8 minutes |

Final keeps the engine's full-quality x264 profile (fast, CRF 18), not the
preview's reduced resolution/quality. Bounds are admission/execution limits,
not promises that every input will fit. Oversized or failed outputs publish no
artifact; no lower-quality fallback is substituted. Final requires separate
account enrollment and a qualified worker; support does not imply availability.
Use
`moviestar cloud capabilities` for the connected service's availability/bounds.

```sh
moviestar cloud edits show "$PROJECT"
moviestar cloud renders submit "$PROJECT" --revision "$REVISION" \
  --kind preview --idempotency-key "$RETAINED_KEY"
moviestar cloud renders show "$PROJECT" "$JOB"
moviestar cloud artifacts list "$PROJECT" --revision "$REVISION"
moviestar cloud artifacts show "$PROJECT" "$ARTIFACT"
moviestar cloud artifacts download "$PROJECT" "$ARTIFACT" --out preview.mp4

# After reviewing the preview, request the SAME revision at full quality.
# FINAL_KEY is a different retained key; each new render reserves allowance.
moviestar cloud renders submit "$PROJECT" --revision "$REVISION" \
  --kind final --idempotency-key "$FINAL_KEY"
# Poll the returned final JOB, then download its returned ARTIFACT.
moviestar cloud artifacts download "$PROJECT" "$FINAL_ARTIFACT" --out final.mp4
```

The revision is required: the CLI never silently selects the latest edit.
Generate a unique retry key before the first submission. If omitted, the CLI
saves one before sending. Retain the key, exact request and returned job ID.
Repeating that submission recovers the same job, even after later edits. A new
key deliberately requests another render. Switching preview to final requires
a different key; both outputs can belong to the same revision. Changed intent with the same key
fails. After `submission_unconfirmed`, use the returned recovery command;
do not infer rollback or generate a replacement key.

`accepted` means queued. Poll the same job through `queued`, `running` and
`verifying`. Only `succeeded` identifies an independently downloaded/decoded,
hash/size/media-verified artifact. Failure or cancellation publishes none.
Use `cloud renders cancel PROJECT JOB` to fence unfinished work. A completed
output is unchanged by cancellation, editing or undo.

Downloads create a new file without overwrite. They verify the advertised size
and SHA-256 before reporting `file_saved: true`, and send no account credential
to storage. An expired transfer can be retried for the same artifact without
rendering again. The private grant is short-lived and is not a sharing link.
The downloader permits HTTPS storage grants, plus same-origin loopback HTTP
only when connected to a loopback development service.

API paths below `/api/v1/account/cloud` are project-scoped:

| Action | Route |
| --- | --- |
| Submit | `POST /projects/P/render-jobs`, body `{revision_id,kind:"preview"}` or `{revision_id,kind:"final"}`, `Idempotency-Key` required |
| Read / cancel | `GET /projects/P/render-jobs/J`, `POST /projects/P/render-jobs/J/cancel` |
| List / inspect outputs | `GET /projects/P/artifacts?revision_id=R`, `GET /projects/P/artifacts/A` |
| Authorize download | `POST /projects/P/artifacts/A/download` returns a JSON GET grant, not bytes |

MCP exposes `moviestar_render_project`, `moviestar_get_project_render`,
`moviestar_cancel_project_render`, `moviestar_list_render_artifacts`,
`moviestar_get_render_artifact` and `moviestar_download_render_artifact` when
enabled. API, CLI and MCP use the same canonical job/artifact identities.
MCP returning a grant does not establish saving, playback or perception.

The project page supports viewing/downloading outputs under their generating
version. It has no editing or render controls. Outputs are not imported into the
source asset library. Initially up to 20 render jobs are retained per project;
output bytes and in-flight storage holds count against storage capacity. No
automatic cleanup or unlimited retention is promised. Existing Activity and
Cloud allowance track one execution; reads, replay, download and saved edits
do not create another render debit. Provider costs remain separate.

Independent decoding proves file/media integrity, not that a person watched,
heard or approved the result. Inspect the actual preview before final delivery.
