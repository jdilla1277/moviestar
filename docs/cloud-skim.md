# Visual skims of Cloud video assets

Request a small timestamped visual sample of an existing ready Cloud video.
This works without a local project or `load`, and does not upload the video,
transcribe audio, or edit anything. Connect your account first as described in
[accounts](accounts.md); the service must have Cloud visual skim enabled.

```bash
moviestar cloud skim ASSET_ID --count 10 --idempotency-key my-visual-skim
moviestar cloud skim ASSET_ID --from 0:30 --to 1:30 --count 6 --idempotency-key my-range-skim
moviestar cloud skim-show ASSET_ID JOB_ID
moviestar cloud skim-show ASSET_ID JOB_ID --out-dir ./skim-frames
```

The request returns a durable job ID. Poll `skim-show` with that asset/job pair
until `job.state` is `succeeded` or a terminal failure. Save the original pair
for later inspection, including after reconnecting. Reading a job or its images
does not schedule more computation. If the request reply is lost, repeat the
same command: the CLI saves and reuses the same request key for the same service,
credential, asset and canonical options. Keep explicit keys when moving between
installations. Changing options with a retained key produces a conflict; use a
new key for intentional new work.

`--out-dir` creates a new directory containing real private JPEG files named
with their decoded source timestamps for new jobs (nominal timestamps for retained legacy jobs) and a `skim.json` manifest. Existing directories
are never overwritten. Failed downloads remove the newly created partial
directory; retry the same job into a new directory. No public image URLs or
credentials are written into the manifest.

## Sampling and coverage

The default is 10 images; `--count` accepts 1–16. Images are 320 pixels wide.
The service reproduces the default local `load` thumbnail index's interval
selection and local `skim` subsampling rule. It chooses a nominal interval from
video duration, capped at five seconds, then filters that index inclusively by
`--from` and `--to`. With more frames than requested it retains evenly spaced
indexed entries, including the first and last; count 1 chooses the middle
indexed entry. When the range contains fewer entries it returns all of them.

New jobs use `sampling.time_basis: decoded_source_time` and
`range_filter: inclusive_requested_index_timestamps`. Each thumbnail's `at_ms`
and `timecode` identify the displayed source frame, rounded to milliseconds;
`requested_at_ms` identifies its selected prepared-grid sample. Range filtering
and subsampling use requested times, not the possibly earlier or repeated
actual frame times. Both are preserved when saving `skim.json`.

The shared sampler observes the actual rational FFmpeg output ticks. Cloud
fast-seeks each selected sample while retaining the original index grid and
keyframe preroll; it does not decode an entire long file or encode a full JPEG
index merely to return a small subset. Local load prepares the complete index
once; subsequent skim reads need no media tools. Timestamps are not inferred
from a nominal frame rate or selected output ordinal.

Retained `nominal_prepared_index` jobs keep their original images and nominal
labels. Reopening never recalculates PTS, relabels an old image as decoded time,
or starts new work. Upgrade the remote CLI before downloading new decoded-time
jobs; older CLI validators require nominal in-range increasing timestamps.
The service and installed skim worker must adopt the paired contract before
new jobs use decoded-time results. REST, native MCP labels and the viewer
preserve both clocks and identify legacy labels as nominal.

`index_interval_ms`, `available_frames`, `returned_count`, and `omitted_count`
make coverage explicit. A short range between index samples can return zero
images with `coverage_note: "no_indexed_frames_in_range"`; widen the range or use
an [asset screenshot](cloud-screenshot.md) to request a particular time. A skim
can miss events between samples. A local custom interval has different coverage.

This first Cloud mode is visual-only. It reports `transcript: null`,
`transcript_status: "unavailable"`, and
`transcript_unavailable_reason: "canonical_asset_transcript_association_unavailable"`.
There is currently no canonical authorized asset-to-transcript association, so
the service neither invents a transcript relationship nor starts transcription.

## API and MCP

`POST /api/v1/assets/{asset_id}/skim` accepts optional `from_ms` (default 0),
`to_ms` (default video duration), and `count` (default 10). Send
`Idempotency-Key` and retain it. `GET /api/v1/assets/{asset_id}/skims/{job_id}`
reopens metadata; append `/images/{index}` to fetch each private JPEG using the
same account credential. Ranges use integer milliseconds and must satisfy
`0 <= from_ms < to_ms <= duration`. The bounded first version supports video up
to 1 GiB and 24 hours, at most 16 images and 2 MiB total image output. Access is
rechecked on every read and worker transition. Images do not use public URLs.

Authenticated MCP clients use `moviestar_skim_asset` with the same options and a
retained key, then `moviestar_get_asset_skim` with the asset/job IDs. Completed
results contain native JPEG image blocks, individual timestamp context, and
structured coverage metadata. Native image transport, an agent's ability to
interpret those images, and the ChatGPT interface visibly presenting those
images to the user are separate acceptance checks. A transport test alone does
not establish ChatGPT presentation behavior.
