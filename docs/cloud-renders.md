# Preview an exact saved Cloud edit

Local `export --preview` stays local. Remote previews explicitly render one
immutable saved version using that engine's preview mode and x264 encoder.
Saving or undoing instructions does not automatically render.

The initial Cloud profile supports one owned ready MP4, trim-only instructions,
at most 30 seconds of result, a 64 MiB input and a 16 MiB output. Layouts,
captions, altered audio and full-quality rendering are not admitted yet. Use
`moviestar cloud capabilities` for the connected service's availability/bounds.

```sh
moviestar cloud edits show "$PROJECT"
moviestar cloud renders submit "$PROJECT" --revision "$REVISION" \
  --kind preview --idempotency-key "$RETAINED_KEY"
moviestar cloud renders show "$PROJECT" "$JOB"
moviestar cloud artifacts list "$PROJECT" --revision "$REVISION"
moviestar cloud artifacts show "$PROJECT" "$ARTIFACT"
moviestar cloud artifacts download "$PROJECT" "$ARTIFACT" --out preview.mp4
```

The revision is required: the CLI never silently selects the latest edit.
Generate a unique retry key before the first submission. If omitted, the CLI
saves one before sending. Retain the key, exact request and returned job ID.
Repeating that submission recovers the same job, even after later edits. A new
key deliberately requests another render. Changed intent with the same key
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
| Submit | `POST /projects/P/render-jobs`, body `{revision_id,kind:"preview"}`, `Idempotency-Key` required |
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
