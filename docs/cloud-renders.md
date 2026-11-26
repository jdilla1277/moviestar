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
| `final` | `export --video-encoder x264` | Square pixels (1:1), full source dimensions padded to even pixels, maximum 1920px edge; 64 MiB, 8 minutes |

Final keeps the engine's full-quality x264 profile (fast, CRF 18), not the
preview's reduced resolution/quality. Bounds are admission/execution limits,
not promises that every input will fit. Oversized or failed outputs publish no
artifact; no lower-quality fallback is substituted. Final requires separate
account enrollment and a qualified worker; support does not imply availability.
Non-square-pixel (anamorphic) input is explicitly unsupported for Cloud final
in this slice and is rejected before a job or usage reservation is created.
Preview remains supported for otherwise admitted inputs. Local full export
continues to support its native pixel-aspect-ratio behavior; Cloud does not
silently stretch or resample the source to bypass this limit.
Use
`moviestar cloud capabilities` for the connected service's availability/bounds.
Its summary edit/render flags describe configured availability; account
enrollment, exact input support and quota are checked at submission. The detailed
`editing` and `rendering` sections describe the admitted operations/profiles.

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
Successful output allowance includes the MP4 plus 8 KiB of job/receipt metadata.
Artifact `size_bytes` is the downloadable MP4 size, without that metadata overhead.

Independent decoding proves file/media integrity, not that a person watched,
heard or approved the result. Inspect the actual preview before final delivery.


## See a frame from the rendered video

When `rendering.frame_inspection.submission_available` is true, request one JPEG
from an **exact verified artifact**. This does not render again or change the
edit. Times are integer milliseconds on that rendered file, excluding its end.
Source `screenshot`/`inspect` commands still inspect original footage.

```sh
moviestar cloud artifacts screenshot "$PROJECT" "$ARTIFACT" --at-ms 10000
# Retain the returned frame JOB and retry key; poll the SAME job.
moviestar cloud artifacts screenshot-show "$PROJECT" "$ARTIFACT" "$FRAME_JOB"
moviestar cloud artifacts screenshot-show "$PROJECT" "$ARTIFACT" "$FRAME_JOB" \
  --out rendered-frame.jpg
```

An omitted key is generated and saved before submission. After
`submission_unconfirmed`, retry the returned recovery command with the same key,
artifact and timestamp. A new key requests another processing job. Reopening and
image transfer do not reserve processing again. A failed job does not restart
when read. New filenames only; `file_saved:true` follows actual JPEG size/SHA
verification and atomic file creation.

The job binds `project_id`, `revision_id`, `render_job_id`, `artifact_id` and
`artifact_sha256`. Its `clock` is `rendered_result`; `result.requested_at_ms` is
your request, while `result.at_ms` is the observed decoded timestamp of the frame
at or before it (`selection:frame_at_or_before`). Engine/runtime and actual JPEG
dimensions/size/hash are returned. A newer edit or undo does not retarget an old
frame. Preview pixels do not certify a separately rendered final artifact.

API below the same account Cloud prefix:
`POST /projects/P/artifacts/A/screenshots` with `{at_ms:10000}` and retained
`Idempotency-Key`; `GET /projects/P/artifacts/A/screenshots/J` to reopen;
`GET /projects/P/artifacts/A/screenshots/J/image` for private JPEG bytes.
MCP tools are `moviestar_screenshot_render_artifact` and
`moviestar_get_render_artifact_screenshot`; successful reads include a native
JPEG image block as well as canonical job metadata. Delivery of a block is not
proof that a client displayed or perceived it.

The bounded profile accepts verified personal render MP4s up to 64 MiB, returns
one aspect-preserving JPEG up to 640 pixels wide (no upscale) and 1 MiB, and
bounds processing at 180 seconds. Pending image storage holds and actual saved
image bytes count toward capacity. Existing Cloud allowance and screenshot
Activity track this work, including its exact rendered-output links. The compatible
Activity kind is `asset_screenshot`; `resource_name` labels the rendered frame and
`rendered_output` identifies its exact target. Metered `output_bytes` includes
the JPEG plus existing job/receipt metadata; image `size_bytes` describes only
the downloadable JPEG. Access is
rechecked for requests, worker input/publication and image reads. Account
worker enrollment is still required even when configured flags are available.
Unrendered compositions, storyboards and time windows are outside this action.

Local `inspect` uses the same observed decoded-time helper. Local `screenshot`
uses seek-based requested-time reporting; it is not an observed-PTS parity claim.
