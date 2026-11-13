# Agent guide

Moviestar separates editorial judgment from deterministic execution. The agent
decides what the edit should communicate; Moviestar stores non-destructive
state, resolves time mappings, renders through FFmpeg, and returns structured
evidence.

## Project model

Run commands from a dedicated project directory. `moviestar load` creates a
`moviestar/` directory containing project metadata, transcripts, indexed
frames, and the edit specification. Source media is referenced by absolute
path and is never modified.

Use `--workspace PATH` when a command should target a project other than the
current directory or one of its parents. Use `status`, `history`, and the
various `dump`/`list` commands to inspect persisted state before changing it.

For account-owned remote project metadata, use the explicit `moviestar cloud`
group. Start with `cloud capabilities`, then `cloud projects create`, `list`,
and `show`. Keep a create's original title and idempotency key for recovery.
This does not redirect local editing commands or upload media. See
[Personal Cloud projects](cloud-projects.md).

When capabilities advertise project asset references, use `cloud assets list`
and `cloud projects assets PROJECT_ID` to discover existing originals and their
memberships. `cloud projects add-asset` reuses an original; `remove-asset` takes
the specific reference ID and preserves that original and its other projects.
Keep an uncertain attachment's retry key. These operations currently require
owned assets and owned projects and do not migrate legacy project sources.

## Structured command loop

1. Discover the relevant surface with `moviestar --help` and nested help.
2. Inspect source or current project state.
3. Preview expensive or replacing commands with `--dry-run`.
4. Apply the smallest edit that satisfies the request.
5. Read the JSON response, including paths, ranges, durations, and warnings.
6. Verify the result visually and, when relevant, audibly.

Moviestar writes JSON to stdout and progress narration to stderr. If a harness
merges both streams, use `--quiet` on commands that support it. Prefer returned
file paths over `--inline`; Base64 only helps integrations that convert it into
an actual image content block.

For enrolled developer guides, `moviestar cloud docs show PAGE` writes Markdown
by default. Read it directly, or use `--json` when you need the page metadata
and Markdown in a JSON object. `cloud docs list` and service errors remain JSON.
See [developer preview guides](cloud-projects.md#read-private-developer-preview-guides)
for version requirements.

## Time spaces

Do not assume every timecode uses the same clock:

- `trim` uses result-time, so later trims narrow the already-edited result.
- `find` reports source, segment, and result ranges.
- `clip` and source-targeted `watch` operate on source-time without changing
  the edit.
- External audio is placed on result-time, the finished video's clock.
- Composition and scene commands document whether their ranges are source,
  per-source result, composition result, or scene-local time.

Read the command's help and use the named range from the response instead of
copying a numerically similar value from another time space.

Scene exports place each scene at its authored result-time boundary. The final
video covers the complete result on one output frame grid, so its video-stream
duration can extend by less than one frame beyond the authored duration. Audio
follows the authored duration independently of video frame rounding.

## Composition choices

Use `trim` and `cut` for one source. Use `concat` for ordered segments or one
global multicamera layout. Use `scenes set` when layouts, slots, framing, audio
routes, or source ranges change over the finished timeline. Scene motion,
geometry, transitions, captions, overlays, and audio mixing build on that
persisted composition.

Large scene, overlay, caption, and audio changes are easier to audit through
their dump/edit/set JSON workflows. Always validate replacement files with
`--dry-run` before committing them to project state.

## Verification

Add `--review-report` to export/watch to track evidence for that exact output.
All dimensions start unreviewed. Use `review record` with the actual reviewer,
method, limitations, and artifact-local ranges or sampled times; inspect
`review show --report FILE` for coverage and next steps. A visual sample cannot
approve motion/listening, and unavailable listening requires a human or capable
reviewer. Changed media/project state makes a report stale; render a new report
and review again. See [review evidence](review-evidence.md) for the opt-in workflow.

Use `export --review` for a 720p review copy of the current edit, or
`watch --from X --to Y --review` for a scene-composition window. Check the
returned `render_profile.actual_dimensions`, encoding settings, and measured
`file_size_bytes`. Add `--max-bytes N` when the review must fit a delivery limit;
share through that route only when `size_budget.met` is true. An unmet limit
returns a nonzero exit with a complete output and a hint to shorten the clip
or increase the limit. The profile preserves authored layout and text wrapping;
see [review exports](review-exports.md) for sizing, source-time watch behavior,
and the full-resolution review limits.

Use `moviestar screenshot --at TIMECODE` to check the current result. A flat
single-source project burns active stored captions and manual overlays onto
the source-size frame, including spoken-word highlighting. The response's
`overlays` block reports active IDs and fonts; `--dry-run` reports the plan
without writing an image or highlight assets. Trims and cuts still map the
result timecode to the correct source frame. Use `screenshot --file VIDEO`
to inspect raw or exported file pixels without applying project edits or text.

New manual text overlays use canvas-relative preset defaults. Font sizes,
strokes, and lower-third padding scale with the shorter canvas side, using
1080 pixels as the reference. Position margins scale with width and height
from a 1920×1080 reference. For example, a `title` resolves to 43 px on an
854×480 canvas and 96 px on either 1920×1080 or 1080×1920. Explicit CSS such
as `font-size: 27px; padding: 8px 12px` and numeric margins in editable JSON
stay absolute. Existing saved overlays retain their stored dimensions when
rendered; applying `overlays set` recomputes style defaults from preset + CSS.

`overlays dump` and `overlays set FILE --dry-run` return `resolved_geometry`
with the current canvas and each resolved overlay's pixel font size, stroke,
padding, margins, and estimated block. Editable files retain relative lengths
so subsequent canvas changes still scale. Check `available` first: projects
without a single render canvas report why geometry is unavailable. Detached
scene overlays have no rendered geometry. `export --dry-run` reports the same
planner's active overlays for the export window.

Run `moviestar captions audit --language en` to inspect caption states across
the complete result timeline. Duration and connector-word findings are advisory;
language is opt-in. Select manual text with `--track NAME`, and combine related
tracks with `--combine-tracks`. Add `--proof-dir NEW_DIRECTORY` for bounded
rendered samples and a JSON report. See the [caption audit](caption-audit.md)
for grouping, rules, geometry estimates, and proof coverage.

Use stills for framing and text, `watch` for motion and audio, and `probe` for
file structure. A final export should have a non-zero duration, expected video
dimensions, the intended audio/video streams, and representative frames that
match the request. Loudness reports help measure a mix but do not replace
listening when subjective balance matters.

When exporting with `--loudness-target`, check integrated loudness and the
final encoded true peak separately in `loudness_normalization`:

| Field | Meaning |
| --- | --- |
| `target_met` | Integrated LUFS is within `tolerance_lu` (±1 LU); this says nothing about peak compliance. |
| `true_peak_limit_dbtp` | Requested peak ceiling, currently −1.5 dBTP. |
| `true_peak_tolerance_db` | Reporting tolerance above that ceiling, currently 0.1 dB. |
| `true_peak_status` | `compliant`, `exceeded`, `unmeasurable`, or `not_measured` for a dry run. |
| `true_peak_limit_met` | `true` or `false` after measurement; `null` when unknown. |
| `true_peak_overshoot_db` | Positive excess above the ceiling, or zero when below it; `null` when unknown. |

A final peak at or below −1.4 dBTP passes the current reporting tolerance.
An exceeded peak emits `true_peak_limit_exceeded` even when `target_met` is
true. A missing or non-finite peak is reported as `null`, with
`true_peak_unmeasurable`; it does not count as a compliance pass. Dry runs
perform no measurement and emit neither warning.

The gain plan caps the pre-encode waveform's predicted peak. AAC encoding can
overshoot it, so `gain_limited_by_true_peak` and `max_achievable_lufs` describe
the gain plan rather than prove the delivered ceiling. An exceeded peak keeps
the export and its measured warning; no corrective re-encode or limiter is
applied automatically. Leave additional headroom when needed and remeasure the
finished artifact with `moviestar probe FINAL.mp4 --loudness`. Metrics do not
replace listening.

For vertical social delivery, run `moviestar screenshot --at TIMECODE
--platform tiktok|instagram-reels|youtube-shorts` at representative and
boundary frames. The returned image shows a versioned player-UI reference mask;
the JSON warns when active Moviestar-authored text or smaller layout slots
intersect it. The check is deterministic and performs no OCR or object
detection, so inspect baked-in source text and important subject matter in the
image yourself.

The distributable [Moviestar skill](install-skill.md) gives compatible agents
this workflow automatically. The [complete command guide](../app/README.md)
covers every command family.

## Hosted collaboration

When the user requests a hosted handoff, upload privately with `moviestar upload`
and publish with `moviestar share VIDEO_ID`, or use `moviestar share FILE` to do
both. Preserve the stderr retry key and read the single stdout JSON result.
`share FILE_OR_VIDEO_ID --to EMAIL` emails the forwardable link to a human-authorized
address. `shares email LINK_ID --to EMAIL --idempotency-key KEY` recovers delivery
or emails an existing link; keep the same recipient/key for retries and check
`shares deliveries LINK_ID` for provider-acceptance history. Email failure does
not undo a confirmed public link, and provider acceptance does not prove inbox
delivery. `shares` lists owned links, `unshare LINK_ID` revokes one, and `download URL
--out FILE` lets a recipient agent start another edit without an account.
`delete VIDEO_ID` permanently removes an uploaded video and all its links,
freeing quota after cleanup. Preview with `--dry-run`; retry the same ID if the
result is pending or unknown. Local files are preserved.
Unlisted links are forwardable and downloadable; email does not restrict access
to its recipient. See [sharing and download](sharing.md) for availability, safe
retries after partial success, pagination, revocation, and Preview origins.


## Recorder files without duration metadata

For Matroska/WebM, `probe` and `load` recover missing or invalid container
duration from audio
and video packet presentation endpoints. Recovery preserves timestamp origins
and gaps; it does not infer frame rate or continuous timing from a nominal rate.
The optional `duration_probe` object records the method, origin, endpoint and
packet count in probe/load/dry-run/status output and the stored source.

Recovery demuxes without decoding and stops after 60 seconds or one million
packets. Missing final packet timing, demux errors or an exceeded budget fail
with remux guidance instead of a zero-length success or a guessed duration.
Remux to a separate seekable file with duration metadata and retry; keep the
original. A failed recovery during `load --force` preserves the existing project.
Packet endpoints establish a metadata range, not full-decode or audiovisual
quality approval. Existing finite positive container durations use the normal
fast probe without this scan. Other formats, including timeless image metadata,
keep their existing probe behavior.
