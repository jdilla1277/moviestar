# Inspect a section of a Cloud video

Use dense visual inspection when an overview has identified a moment that needs
a closer look. The source can be an uploaded original or a finished video saved
as an asset. Times use that asset's original clock; unrendered edits are not
applied.

Connect your installation with `moviestar account connect`, then request a job:

```bash
moviestar cloud inspect ASSET_ID --from 00:05 --to 00:15
```

The command returns a durable job ID immediately. Reopen that exact job until
its state is `succeeded`, then save the private JPEGs:

```bash
moviestar cloud inspect-show ASSET_ID JOB_ID
moviestar cloud inspect-show ASSET_ID JOB_ID --out-dir ./inspection-frames
```

Downloads include frame indices and source milliseconds in filenames. Repeating
the download preserves identical existing files; differing files are never
overwritten. Retain the job ID if a transfer is interrupted or you reconnect.
Reading a result does not start another execution.

The defaults match local visual inspection: a 0.5-second interval and 640-pixel
width. Use `--interval SECONDS` and `--width PIXELS` to change them. Cloud limits
are 120 seconds per section, 240 frames, width 16–640 pixels, 256 KiB per JPEG,
and 16 MiB of images per job. Input is limited to 1 GiB. Increase the interval,
reduce the width, or choose a shorter section if the job exceeds a limit.

Sampling uses a half-open range: a 5–15 second section at a 0.5-second interval
has nominal samples at 5.0, 5.5, …, 14.5 seconds. New results use the same held-frame sampler as local inspection: `at_ms` and
`timecode` identify the decoded source frame (rounded to milliseconds), while
`requested_at_ms` identifies the nominal sample request. Sampling reports
`time_basis: decoded_source_time` and `strategy: source_frame_at_or_before`.
Actual frame times may precede the requested start or repeat across VFR holds.
The engine observes the actual sampling time base chosen by FFmpeg; it does not
reconstruct frame identity from the requested decimal interval.
Retained older jobs still report `nominal_source_time`; their times are nominal
samples and must not be treated as decoded PTS. Reopening does not relabel or
reprocess them. Downloads preserve the job's time basis and requested time
when available. Actual video coverage
can produce fewer frames; an empty result explicitly reports
`no_frames_in_range`.

The new local and Cloud fields carry the same meaning:

| Meaning | Local `inspect` | Cloud inspection |
| --- | --- | --- |
| Requested source sample | `requested_timecode.seconds` | `requested_at_ms / 1000` |
| Displayed source frame | `timecode.seconds` | `at_ms / 1000` |
| Displayed frame label | `timecode.text` | `timecode` |

No transcript is generated or inferred. Results explicitly report
`transcript: null` and `transcript_status: unavailable`. Local inspection can
include a loaded transcript; transcript association is not available in this
Cloud journey. This command measures existing media and does not edit it.

The CLI generates and privately saves a retry key before submission. Repeat the
same request after an uncertain response to recover its job. To request a new
execution intentionally, supply a new `--idempotency-key KEY`. Reusing a key
with a different asset, range, interval, or width returns a conflict.

## API and hosted MCP

REST clients use a personal API key and a retained `Idempotency-Key`:

```text
POST /api/v1/assets/ASSET_ID/inspect
{"from_ms":5000,"to_ms":15000,"interval_ms":500,"width":640}

GET /api/v1/assets/ASSET_ID/inspections/JOB_ID
GET /api/v1/assets/ASSET_ID/inspections/JOB_ID/images/FRAME_INDEX
```

Each image request requires authorization. There are no public image URLs.
Hosted MCP exposes `moviestar_inspect_asset` and
`moviestar_get_asset_inspect`. A completed read returns timestamped native image
blocks, eight at a time. Follow `image_page.next_index` as `start_index` on the
same job to see the remaining frames. Polling and pagination reuse the retained
execution.

Job and attempt outcomes and elapsed time appear in account Activity. Missing
actual cost data means unknown, not zero. Availability depends on an enabled,
verified service worker; unavailable requests retain their retry information.
The remote CLI needs neither local FFmpeg nor a local copy of the asset.
