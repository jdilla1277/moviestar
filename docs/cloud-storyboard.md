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
