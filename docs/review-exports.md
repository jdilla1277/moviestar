# Review exports

Use `--review` to produce a 720p review copy with more detail than the existing
480p export preview. It selects a shared profile for `export` and `watch`:

```bash
moviestar export --review --out review.mp4
moviestar watch --from 12 --to 20 --review --out review-section.mp4
moviestar export --review --dry-run
```

The profile fits landscape output inside 1280×720, portrait output inside
720×1280, and square output inside 720×720. Other aspect ratios fit inside the
same orientation-aware box. Smaller videos retain their size. Odd dimensions
receive the same padding of at most one pixel used by normal H.264 exports.

Composition, framing, camera motion, text layout, and transitions supported by
the command are rendered at the authored canvas size. The assembled video is
then resized before the final encode. Caption font sizes, wrapping, and project
settings stay unchanged; a 72px caption on a 1080p canvas becomes proportionally
smaller in the review image. Scene intermediates retain their native canvas
and full-quality settings.

## Encoding and output

| Selected encoder | Review video settings |
| --- | --- |
| x264 | `libx264`, `fast` preset, CRF 23, `yuv420p` |
| VideoToolbox | `h264_videotoolbox`, 4 Mb/s target bitrate, High profile, `yuv420p` |

`--video-encoder` and automatic encoder selection work as usual. Audio routing,
fades, mixing, and requested loudness normalization retain the command's usual
behavior. Final measurements include subsequent audio muxing and normalization.

The response adds `render_profile` with the profile name `review-720p`, maximum
long/short edges, selected video encoding settings, resize stage, and actual
measured output dimensions. `file_size_bytes` reports the delivered file's size.
A dry run reports the settings and maximum dimensions with
`actual_dimensions: null`; it does not encode video or create output directories.

With no `--out`, export writes `moviestar-clips/review-720p.mp4`. Watch adds
`_review-720p` to its usual automatic filename. Existing automatic names receive
numeric suffixes. An explicit `--out` retains the normal overwrite behavior.

Review the full-resolution export when judging details that 720p cannot preserve.

## Maximum file size

Add `--max-bytes N` to request a maximum final MP4 size. It requires `--review`
and accepts a positive integer byte count, not an `MB` string or a service name:

```bash
moviestar export --review --max-bytes 20000000 --out review.mp4
moviestar watch --from 12 --to 20 --review --max-bytes 5000000
moviestar export --review --max-bytes 20000000 --dry-run
```

MovieStar checks the completed file after all audio mixing and normalization.
If it fits, it keeps that rendition without another encode. Otherwise it may
try at most two compression retries using the selected encoder: two-pass
target-bitrate encoding for x264, or target-bitrate encoding for VideoToolbox.
Each retry reads the original completed review, never a previous retry. This
adds a video compression generation when needed, without rendering the scene
composition again. Encoded audio is copied unchanged. Resolution, duration,
frame count, authored text, and layout remain fixed; there is no truncation,
automatic downscaling, or audio bitrate reduction.

The initial bitrate estimate reserves actual audio packet payload plus the
larger of 4,096 bytes or 3% of the budget for container overhead, with additional
headroom for rate-control variation. Estimates do not establish compliance:
only the final measured file size does. Retry targets stay at or above 300 kb/s;
this is a compression policy, not proof that typography remains readable.
Review visual quality separately. An initial review already under the limit is
kept even if its bitrate is below that retry floor.

The JSON `size_budget` reports `max_bytes`, final `actual_bytes`, nullable `met`,
the bounded retry policy, attempts and their commands/settings, the selected
candidate, and `stop_reason`. `render_profile.video_encoding` describes the
selected output's settings, including a retry's target bitrate when applicable.
The original render commands remain in their usual fields; compression retry
commands are under `size_budget.attempts`. Temporary candidate files and pass
logs are removed after execution.

Success requires `size_budget.met: true`. If the limit is still unmet, the
command exits with code 1 and `status: "size_limit_unmet"`, reports `met: false`,
and preserves the smallest complete, verified candidate at `out`. This file
is **not suitable for the requested size-limited route**. Request a shorter clip
or a larger limit. A failed retry preserves the last verified candidate, never
its partial output; its error is included in the attempt. Higher-quality files
at other paths and the source project remain untouched. Explicit `--out` still
uses normal overwrite semantics, so use a separate path for a review copy.

Dry runs report the retry policy with `met: null`, `actual_bytes: null`, and no
attempts. They do not encode media, inspect output bytes, predict a compliant
bitrate, or write temporary files. The profile without `--max-bytes` retains its
usual settings and makes no attachment-size guarantee.

## Choosing a command

`export --review` renders the current edit, including a source-only project's
cuts and text. `watch --review` retains watch's existing clock and routing:
without `--source`, scene compositions use composition result-time; raw source
extraction uses source-time and excludes the edit's visual overlays. Use export
to review the complete edited source when there is no scene composition.

`--review` re-encodes and gives watch frame-accurate boundaries. Export rejects
combining it with `--fast` or `--preview`. Normal full exports, 480p previews, and
watch's default extraction mode keep their existing defaults.
