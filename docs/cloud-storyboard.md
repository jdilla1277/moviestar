# Cloud asset storyboard

Cloud storyboard is an opt-in service capability for an existing ready video
asset. It creates one private, timecode-labeled JPEG contact sheet and structured
sampling metadata in a single bounded job. Ordinary `moviestar storyboard FILE`
continues to work locally without an account.

Connect the CLI to the intended Cloud environment first, then run:

```bash
moviestar cloud storyboard ASSET_ID --idempotency-key retained-whole-video
moviestar cloud storyboard ASSET_ID --from 20:00 --to 21:00 --interval 10 --columns 3 --idempotency-key retained-range
moviestar cloud storyboard-show ASSET_ID JOB_ID
moviestar cloud storyboard-show ASSET_ID JOB_ID --out contact-sheet.jpg
```

The request returns a durable job ID. Poll `storyboard-show` until `state` is
`succeeded`, then download with `--out`. Existing files are never overwritten.
Repeat the identical request after an uncertain reply: the CLI saves and reuses
its request key. Retain the IDs and key when reconnecting. Use a new explicit key
only when you intend another execution. Reading status or downloading a result
never starts new computation.

Ranges are half-open: `--from` is included and `--to` is excluded. Times accept
seconds, `M:SS`, or `HH:MM:SS.mmm`, with millisecond precision. The Cloud range
must be at least 100 milliseconds, within a video of at most 24 hours. Omit
`--to` for the video end. Automatic sampling uses the local storyboard interval
ladder targeting roughly 15 frames; explicit intervals must be at least 10
milliseconds and smaller than the range. At most 60 frames and 1–12 columns are
accepted. Tiles are 384 pixels wide. Output is bounded to 2 MiB, 16 megapixels,
and 16,384 pixels on either dimension; input is bounded to 1 GiB. Exceeding
resource bounds fails the job rather than silently returning an incomplete sheet.
Timecode labels are required; missing font/drawtext support fails explicitly.

The job includes `options`, `sampling` (range, interval, grid and frame times),
and, after success, `result.frames` with source timestamps and timecodes.
`/activity` records the job and attempts, elapsed time, and existing cost and
reconciliation status. Unknown or pending costs remain unknown or pending.
Result retrieval is distinct from execution.

Integrations use `POST /api/v1/assets/ASSET_ID/storyboard` with integer millisecond
fields `from_ms`, `to_ms`, `interval_ms` and optional `columns`, plus a retained
`Idempotency-Key`. Read `GET /api/v1/assets/ASSET_ID/storyboards/JOB_ID` and retrieve
the private JPEG at that URL plus `/image`. All three endpoints require current
asset authorization; the image is not a public or signed download link.

Hosted MCP exposes `moviestar_storyboard_asset` and
`moviestar_get_asset_storyboard`. The latter returns native JPEG image content
plus metadata. An SDK image payload establishes transport only. Actual client
acceptance must separately demonstrate that the agent can inspect the sheet and
that the user sees the image inline; a URL or text description alone does not
establish either. Example prompt after connecting an enabled service:

> Create a whole-video storyboard for asset ASSET_ID using request key
> retained-whole-video. Poll the same job until complete, show the returned
> contact sheet inline, and describe two visible frames with their labels.
> Then reopen that same job without creating another execution.

Enabling the service and verifying it in a production client are separate
operator steps. Merged code or a successful local command does not establish
production enablement or ChatGPT rendering.

## Storyboard a generated video

When `rendering.storyboard_inspection.submission_available` is true, use the
artifact commands for an **exact verified personal render**, rather than an
original asset. This action never renders video, imports a source asset, changes
an edit or silently selects the latest output.

```sh
moviestar cloud artifacts storyboard "$PROJECT" "$ARTIFACT"
# Keep the returned JOB and retained key; poll that same job.
moviestar cloud artifacts storyboard-show "$PROJECT" "$ARTIFACT" "$JOB"
moviestar cloud artifacts storyboard-show "$PROJECT" "$ARTIFACT" "$JOB" --out NEW-sheet.jpg
```

The first profile automatically samples the whole rendered file using the local
storyboard interval/grid planner (roughly 15 samples, at most 60). It does not
admit range, interval or column overrides. Times are rendered-result milliseconds,
with the end excluded. `sampling` is saved with admission; retries retain the
original plan even after newer edits or policy changes.

The job binds `project_id`, `revision_id`, `render_job_id`, `artifact_id` and
`artifact_sha256`. Successful `result.frames` pairs each `requested_at_ms` with
observed decoded `at_ms` and its visible `timecode`. Labels show decoded result
time, with `selection:frame_at_or_before`, not an FPS-derived or requested seek
time. VFR holds can repeat a picture/time in separate requested cells. Padding
cells are gray `(end)`, not additional samples. Original-asset/local storyboard
commands retain their existing requested-time labels; their planning/grid
primitives are reused here with the decoded-frame inspection helper.

The resulting private JPEG includes MIME/dimensions/size/SHA and exact extractor
engine/runtime pins. CLI saves only a new file after byte verification. Reopen,
image retrieval and retained request replay never start another execution or
reserve usage again. Unknown replies retain the original request/key in the
recovery command; a new key deliberately requests new work. Failed jobs are
terminal, and no incomplete/unlabeled sheet is published. Later edits and undo
cannot retarget the saved sheet.

API routes below `/api/v1/account/cloud`:

- `POST /projects/P/artifacts/A/storyboards`, strict body `{}` and retained
  `Idempotency-Key`.
- `GET /projects/P/artifacts/A/storyboards/J` to reopen.
- `GET /projects/P/artifacts/A/storyboards/J/image` for authorized JPEG bytes.

MCP exposes `moviestar_storyboard_render_artifact` and
`moviestar_get_render_artifact_storyboard`. A successful read includes one native
JPEG block and the same canonical job. Transport, agent image perception and
human viewing are separate checks.

The bounded profile accepts one verified MP4 up to 64 MiB and a whole-file
sampling duration of 100–30000 ms. Tiles are 384px wide with at least 48px height
for labels. Output is at most 2 MiB, 16 megapixels and 16384px on either edge;
processing is bounded at 180 seconds. These are limits, not success guarantees.
Exact media/labels and installed worker support must be qualified before
activation. Unsupported or oversized results fail rather than silently dropping
cells. Current project/input permissions and original withdrawal authority are
rechecked before input grants, publication and image reads.

Activity uses the existing `asset_storyboard` kind with a clear rendered-output
name and exact `rendered_output` links. One task contains its attempts and
settlement. Image bytes and pending holds count against storage; metered output
also includes job/receipt metadata, so `output_bytes` can exceed JPEG `size_bytes`.
No saved-edit or render event is created by inspection. Check capabilities and
account enrollment before submitting; support does not imply activation.
