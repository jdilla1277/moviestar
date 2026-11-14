# Measure visual activity in a Cloud video

Use an existing, ready video asset owned by your connected Moviestar account.
A rendered output can be analyzed once it is available as a ready account asset.
Each file uses its own timeline. Unrendered compositions are outside this
operation's scope. No local media, FFmpeg, transcript or audio track is needed.

```bash
moviestar cloud activity ASSET_ID --from 00:00 --to 00:10 --no-wait
moviestar cloud activity ASSET_ID --job-id JOB_ID
```

The first command returns a durable job and its retry key. The second polls or
reopens that saved job, without scheduling another execution. The default wait
limit is 300 seconds; `--timeout SECONDS` changes polling, and `--no-wait`
returns immediately. Disconnecting or a polling timeout does not cancel work.
Failed/cancelled jobs exit unsuccessfully with the saved job and failure code.

The command saves a retry key before submission. Repeating the same asset and
controls recovers that request, **including after completion**. Use a new
`--idempotency-key KEY` only to deliberately request another analysis. Changing
parameters under the same key returns `idempotency_conflict`. Retry records live
in private `cloud-activity-requests` beside your account profile and are scoped
to the service and installation credential. After reconnecting with a different
credential, reopen with the original job ID or explicitly reuse the original key.

## Controls and measurements

| Control | Default | Accepted values |
| --- | --- | --- |
| `--from` | 0 | Asset time in seconds, M:SS or HH:MM:SS.mmm |
| `--to` | File duration | Increasing endpoint within that file |
| `--interval` | 0.25 seconds | Finite positive sampling interval, at most 86400 |
| `--threshold` | 0.005 | Normalized mean luma difference, 0 through 1 |
| `--merge-gap` | 0.5 seconds | Finite gap from 0 through 86400 seconds |

A request must produce 2–5000 samples. The result contains `settings`,
`analysis_dimensions`, timestamped `samples`, `active_ranges`, `idle_ranges`,
`no_new_frame_ranges`, and a `summary`. Scores include mean absolute luma
difference and the changed-pixel ratio. Frame states distinguish held VFR
frames from newly decoded frames whose pixels are unchanged. Both the `source`
and `result` sample clocks refer to the asset file; `range.clock` is `asset`.
Timecode evidence and the default duration retain the local detector's
millisecond precision. Validation, timestamps and decoded pixels all use the
first video stream, even if another track is marked default. The first video
stream must be usable video; attached cover art is rejected rather than treated
as moving footage.

Visual change is a heuristic. It does not determine editorial importance,
speech, or semantic meaning. Ranges are candidates to inspect further. Silent
footage works without transcripts or images, and no media is changed.

The hosted analyzer reuses the local `moviestar activity` detector: 160-pixel
wide grayscale analysis, pixel-change delta 16, and the same thresholds and gap
bridging. Local `activity` can map edits across source clocks; Cloud analyzes
only the actual file. Hosted results omit local paths and diagnostic commands.
Local `--dry-run` and source selection remain local project controls.

Cloud adds bounded processing: at most 1 GiB input, 24-hour endpoints, a
3,500,000-byte compact result, source dimensions at most 16384 on either axis,
scaled height at most 640, and a 128,000,000-byte decoded-frame budget. A worker
may impose smaller byte/time limits. Reduce the range or increase the interval
if `sample_limit` or `resource_limit` is returned. Media without usable video
fails with `no_video`; invalid actual media ranges fail with `invalid_range`.
A short clip can work with a smaller interval if it yields at least two samples.

## API and hosted MCP

Submit with a personal API key and a retained `Idempotency-Key`:

```http
POST /api/v1/assets/ASSET_ID/activity
Content-Type: application/json
Authorization: Bearer YOUR_PERSONAL_API_KEY
Idempotency-Key: RETAINED_REQUEST_KEY

{"from_seconds":0,"to_seconds":10,"interval_seconds":0.25,"threshold":0.005,"merge_gap_seconds":0.5}
```

Poll/reopen `GET /api/v1/assets/ASSET_ID/activities/JOB_ID` using the same account.
A pending submission returns HTTP 202; terminal jobs and stored reads return
200. API errors include safe codes and retained request identity. Inaccessible
assets cannot launch work or reveal saved results. Project viewer access does
not by itself authorize this owner-scoped inspection operation.

Hosted MCP offers `moviestar_activity_asset` with `asset_id`, a generated and
retained `idempotency_key`, and the same optional numeric controls. Reopen with
`moviestar_get_asset_activity` using `asset_id` and `job_id`. Full measurements
are in `structuredContent.job.result`; the text content summarizes job state
without duplicating the entire evidence payload. A request submitted through
one surface can be read through another using the same durable IDs.

Ask an authenticated agent:

> Analyze visual change from 0 to 10 seconds in my existing Moviestar asset
> ASSET_ID. Generate and retain a retry key, use a 0.25-second interval, and poll
> the same job. Show the candidate active, idle and held-frame ranges and scores.
> Give me its asset ID and job ID so I can reopen it from the CLI. Explain the
> limits of visual-change evidence.

`moviestar cloud capabilities` reports `asset_activity`, including limits and
whether submission is configured. A matching verified worker must also be
available; configuration alone does not prove live execution. Rollout depends
on the connected service. If unavailable, preserve your retry key and retry.

The account website's `/activity` is the Cloud job ledger, a separate meaning of
activity. It records the analysis job and each execution attempt, elapsed time
and existing cost status. Unknown actual provider cost remains unknown, not zero.
