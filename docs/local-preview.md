# Local preview viewer

Keep one browser page open while a coding agent edits a scene project.
Choose rendered MP4 review or the opt-in native player. Both follow saved edits,
including `moviestar undo`, and need no account, upload or cloud render.

| Mode | How review works | Use it for |
| --- | --- | --- |
| `--mode native` | The browser plays source media directly with the canonical scene layout. No preview MP4 is encoded. | Fast review of the supported one/two-video subset described below. |
| `--mode rendered` (default) | The shared spatial renderer produces a completed short MP4 with audio. | Projects outside the native subset, or review through the export renderer. Generation remains slow. |

The viewer uses moviestar's gold star, serif wordmark and blue palette in a
dark interface by default, including when the operating system prefers light
mode. Docs, guided feedback and account links open the main site in another
tab without sending your local project or media. Local review requires no
account. Fonts are bundled, so branding does not require a network connection.

## Start the viewer

From a project containing saved scenes, in a persistent terminal:

```bash
moviestar preview --mode native --from 0 --to 5
```

The command prints a JSON response with a `url`, opens that page in your local
browser, and keeps running until Ctrl-C. Edit with your agent in another terminal.
Use `--no-open` to return the link without opening a browser. The default port is
8765; `--port 0` selects an available temporary port. `--workspace PROJECT_ROOT`
selects an existing workspace explicitly. Ranges use composition result time and
are limited to ten seconds; restart the viewer to change the range.

macOS and Linux are supported. Native descriptor preparation does not launch
headless Chrome or Node. The human's browser decodes videos and images directly;
FFmpeg prepares bounded audio/caption planes when needed. Loading
and indexing new sources still uses the normal project-loading tools. Rendered
mode requires Chrome/headless Chrome, Node.js 22+, FFmpeg and ffprobe, as for
spatial rendering. Native playback needs a browser with H.264/AAC, video-frame
callbacks, AudioWorklet and an audio output clock; Chrome is the tested browser.
Press Play to hear sound: updates remain paused rather than
unexpectedly restarting audio. The service runs in the foreground so agents
should use their environment's persistent terminal/background-process facility.

The URL uses `127.0.0.1` and works on that machine only. On a remote coding-agent
VM, it requires the host's explicit port-forwarding capability; merely returning
the URL does not make it accessible on a phone. Hosted sharing is a separate
workflow. The default project link survives session restarts while its token and
port remain unchanged. Two sessions for the same project are rejected; an
occupied port is an error, never a silent switch to another URL.

## Native profile and explicit fallback

Native mode supports a frame-aligned window of at most ten seconds covering
up to **three scene spans joined by hard cuts**. Each cut must land on
the output frame grid. The project can contain other spatial or preset
scenes before and after it; `--from`/`--to` stay on the full composition's result
clock. For example, `--from 1801 --to 1806` reviews that section without preparing
the preceding thirty minutes. Restart the viewer to select another range.

Each selected span can have **zero, one or two visible MP4s**. In scenes with
video, one visible video carries the resolved speaker audio; the other stays muted. Each video keeps its
own source offset. Play, Pause, seek and revision replacement operate on both
pictures. Both first frames must be prepared before the version becomes current.
Both video elements feed the same Web Audio clock. The selected speaker's gain
is one and the listener's gain is zero; both transport starts are submitted
together. Pause, seek and replacement cancel pending startup too.
A cut switches the composition and its selected speaker audio together. A → B → A can return to
the same recording at a different source position, with a different layout,
caption state or component arrangement in each scene.
A scene containing only text, images, shapes and components uses the scene's
resolved duration and output cadence, without a dummy video or encoded still
clip. Image backgrounds are ordinary image elements placed behind other children;
fit/fill, crop position, stacking, transparent logos and text stay editable.
Play, Pause, Restart, seek, sampled motion, saved edits and undo all work on that
scene clock. Images and fonts load before the version becomes current.

Still scenes can be silent, use the existing independent audio mix, or sit
between footage scenes. A selected window mixing stills with routed source audio
prepares one canonical audio window, including the silent portions, so picture
cuts do not restart its soundtrack. Still-only scenes do not infer source audio
from an image; existing explicit audio routing and mix rules apply. Structured
scenes without footage slots still need a declared duration in a loaded project;
this does not add a new source-free project creation command. The ten-second,
three-span limit and frame-aligned boundaries remain unchanged.

Every source clock must be unit speed, matching the indexed output rate
(24–60 fps). Reusable components, text, shapes, images, placed caption tracks and
canonical sampled translation/scale/opacity are supported. Media wrappers must
be relative/absolute and have no authored children. Video/image elements support
`object_fit`, `object_position`, padding, borders, rounded corners and background
colors. Live media uses the same content box as inspection/export; transparent
letterboxing exposes the authored background. A box with no positive content
width/height fails the new revision and preserves the prior playable preview.

Audio can be explicitly selected for the scene, inferred from its single
audio-capable slot, or selected by `composition_audio_from`. These routes are
accepted when the canonical resolved audio follows one visible video's source
and clock. A scene's `audio_from: null` allows inference; it does not mute audio.
An inset without embedded audio is allowed when another visible video provides
the selected audio. Multiple audio-capable sources need an unambiguous route.

Ordinary static `single`, `two-up` and `picture-in-picture` preset scenes also
work, including fill/fit framing, crop anchors, rounded/circular inset masks and
outside borders. The native adapter consumes the export planner's regions and
does not rewrite the saved scene into a spatial document. Changes made with
`scenes set`, `scenes geometry` and `undo` appear in the viewer. Preset scenes
also support authored caption/text overlays, including
`captions generate --highlight spoken-word`, caption placement, suppression,
regeneration and undo. Preset camera/pacing motion still requires rendered mode.
Spatial caption elements retain their canonical DOM placement, text and motion;
live recipes now refresh through the same derivation used by export. Spoken-word
highlighting also works inside nested spatial caption components, with their own
layout, backgrounds, strokes, inherited highlight colors and parent motion.
These elements use the shared export DOM directly, without caption PNG planes
or a preview-video render. Before a revision becomes current, every distinct
caption text is checked for overflow, including later cues. Invalid word timing
or overflow fails the new revision and leaves the previous playable version
available. See [timed caption elements](spatial-model.md#timed-caption-elements)
for mode/color overrides and inspection fields.

Native video still plays from the original sources. For preset overlays, a worker
uses export's wrapping, font resolution, drawtext and ASS highlight events to
paint **only distinct caption states** over synthetic black/white mattes. It
recovers transparent, cropped PNGs and preloads them before publishing a playable
version. The video-frame clock selects the matching caption image during play,
seek and scene cuts. It does not decode source footage or encode a preview MP4.
Matte pairs stream through one bounded buffer; no full-canvas raster sequence is
written to disk. These request-owned images expire with the retained version/session; they are
not a persistent export cache. Static preset text, strokes, ordinary background
boxes, opacity and supported word-highlight colors use this same path.

Caption-only revisions preload fresh planes and atomically update the existing
video players. Reuse requires identical source-input fingerprints, media assets,
audio route, source clocks, canvas, frame rate, range, geometry and sampled layout.
Changes to any playback input rebuild the player. Superseded caption preparations
are canceled before publication; all cut scenes update together. The viewer
preserves the current paused position, or restarts at zero after a finished
preview. New revisions clear prior playback measurements. This is bounded live
player reuse, not encoded-clip caching or reuse during final export.

Native preset captions explicitly reject rotated text and combinations the
canonical renderer cannot reproduce (for example highlighted background boxes,
ignored transforms/letter spacing and mismatched word tokens). They never
silently remove these properties. A live recipe that cannot refresh fails with
`stale_caption_track`; frozen cues remain frozen. Planning does not save a new
spec, create undo entries or freeze recipes. The stage inspection snapshot
includes the current caption text, lines, font size, active word and estimated
bounds; preset glyphs themselves come from the export renderer, not browser fonts.

Windows containing more than three spans, crossing pacing boundaries inside
one scene, overlapping any transition, or needing more than two visible videos
at once are rejected. Transitions elsewhere in the
project do not block an otherwise supported window. The chosen sources must have
no source edit operations. Edits on unrelated sources and unplaced live caption tracks do not block
this window. Up to two independent audio tracks and source/track gain, mute and edge fades are
supported as described below. Looping, ducking, more than two audible
independent tracks and held/speed-changed media remain unsupported in this profile. Ordinary unit-speed spans after
a hold elsewhere in the scene can be reviewed with their original scene clock.
Other codecs, VFR, HDR/color parity and general large-project behavior have not
passed acceptance for this initial profile. The benchmark fixture uses H.264/AAC at 30 fps.

An unsupported saved state fails before a session opens, with structured JSON
`code: "unsupported_native_preview"` and `fallback_mode: "rendered"`. There is
no silent change to a slow render. Stop the native session and explicitly use:

```bash
moviestar preview --mode rendered --from 0 --to 5
```

If an edit becomes unsupported while the viewer is open, its error names this
alternative and the old displayed version stays labeled older. Fixing that edit
requests a fresh native version. Browser decode/capability failures also remain
visible with rendered-mode guidance. Native mode offers Play, Pause, Restart
and a position slider. Audio starts only after Play; Pause/Restart/replacement
cancel audio startup as well as active playback.
Play waits for an in-progress seek before starting the selected scene. Pause
cancels queued playback while retaining the seek destination; a newer seek or
disposal prevents an older destination from becoming active.

The native range end is approximate, with a 50 ms reference budget, and is not
sample-accurate export trimming. It pauses at the last expected source frame;
export still owns final frame/audio encoding. The player uses the same canonical
scene DOM as inspection/export. Its native video pixels and color handling are
separately subject to fidelity testing, rather than assumed identical to an
encoded export.

### Cuts and playback resources

The viewer prepares every selected scene’s actual first video frame, images,
fonts and canonical DOM before the version becomes current. At most three scene
players with two decoders each are prepared per version, independent of the full project’s
length. One shared audio context switches source gains at the cut; audio is
measured after that switch. The output is stereo, using the browser's speaker
downmix so center-channel dialogue in a surround source is preserved.
A disposable snapshot of each outgoing last valid video frame covers the brief
decoder/audio handoff. Cut players seek to prepare those pixels, then return to
their initial frames before the version becomes current. Cut scheduling uses
the presentation clock even when the browser omits a final-frame callback. The
boundary frames live only in memory for those prepared players and are released
on disposal; they are not encoded or reusable rendered clips.

Cut and audio/display timing use a 50 ms reference tolerance. Inaudible pictures
follow the audible video's clock with bounded rate correction; a drift beyond
100 ms stops playback with rendered-mode guidance. The program clock
is also checked across the complete sequence, not reset for each source. These
are preview tolerances; export owns delivery-quality sample/frame encoding.
The shared clock starts only on Play, and Pause, seek, revision replacement and
disposal cancel pending handoffs and scheduled gain changes. A source failure
stops the sequence and displays the existing rendered-mode guidance.

### Bounded source-byte reuse

On supported loopback browsers, native mode uses a project-scoped service worker
to keep recently read source ranges in **memory only** across saved edits and
history selections. It stores at most 64 MiB / 256 blocks, with at most four
256 KiB source reads in flight. Old blocks are evicted as new ones arrive. A
fresh browser profile starts empty; the browser may discard the worker and its
buffer at any time. This is source-byte reuse, not rendered-scene caching.

Every media request first asks the live server to validate the registered asset
and its file identity, including when the entire requested range is in memory.
A changed or expired source cannot be replayed from the buffer. Source responses
must explicitly permit private, revalidated reuse; the worker's network fetches
bypass the HTTP cache, and it writes no media to Cache Storage or IndexedDB.
Ordinary source responses remain `no-store`. Leave source files unchanged during
review, as before: this is not an immutable archive of an asset being edited.

If registration is unavailable or does not take control within 1.5 seconds, the
native player uses direct source requests. Playback remains available; byte
reuse and its performance benefit are then absent. Metadata preloading is a
hint, not readiness: a fresh version still requires the actual first decoded
frame, fonts, images and canonical layout before it becomes current.

### Independent dialogue audio and existing mix controls

Native review can play up to two independently placed audio tracks alongside routed
source audio, including overlapping dialogue and music. Use the existing `audio add`, `audio source`, `audio dump` /
`audio set` and `undo` commands. Source/track gain, mute and edge fades, selected source
ranges and result-time placement retain their export meaning. Looping,
ducking and more than two audible independent tracks in the selected window
remain unsupported and fail explicitly.

An edited mix prepares only the selected window as lossless stereo 48 kHz PCM,
using the canonical export layer graph and limiter. Pictures still play from
their original files; no preview movie is encoded. One audio buffer continues
through picture cuts. Play/Pause, seeking, restart and replacement stop or
replace that buffer; superseded preparations cannot become current. Gain/mute/fade
edits can replace the prepared mix over unchanged video players. Adding/removing
an audio-file dependency rebuilds the player because its input fingerprint
changes. Source-file changes still invalidate reuse.

The browser snapshot includes the prepared mix's source/track gain, mute and fade intent,
resolved source/result ranges, original track placement and full result duration,
and current audio asset. Fades use sample-time envelopes shared with export and
loudness reports; seeking into a fade retains its project/track position. Rendering
no longer steps fade gain at decoder packet boundaries. The original linear
curves and authoring fields are unchanged. Generated audio assets
are request-owned and expire with session history. The ten-second/three-span
profile and existing approximate clip-end limits still apply. Reference playback
checks use the authored raster and seek-before-Play workload; fresh playback
without those seeks has a separately tracked startup-drop limitation in
[#199](https://github.com/jdilla1277/moviestar/issues/199). This does not establish
general long-project, codec or cloud acceptance.

## What follows an edit

Each requested preview pins a parsed spec and project metadata, receives a fresh
request ID, and reports the spec's `state_id`, time range, authored canvas, and
review profile. Both modes read sources in place. Basic source/asset size,
modification time and inode changes cause a new request, but inputs are not copied
or frozen: leave media unchanged while reviewing. This is not a content-addressed
asset snapshot or a rendered-scene cache.

Idle polls check file metadata before reparsing a project. Saved edits, undo,
project metadata changes and changes to current asset paths still invalidate
the check. Preview-worker snapshots contain the current document without its
undo payloads; the saved revision journal remains intact. Large journals still
cost time to validate when an actual edit is loaded.

A lossless numeric JSON round trip (such as `1.0` returning as `1`) keeps the
[state ID](spatial-model.md) and does not queue another preview. Project metadata
and source/asset changes still trigger fresh requests. Editing from A to B and
then undoing restores A's content ID but always creates a new request, so a late
result from an earlier request cannot become current.

Only one generation runs per project, with at most one pending request. Rapid
edits replace the pending request with the newest one. Obsolete work receives an
interrupt; after six seconds its owned process group is killed. New generation
waits for cleanup. The same cancellation applies to preview work after undo;
independent exports are not affected. A late completion cannot select an obsolete
clip in the service or player.

Until the new preview is ready, any previous preview stays explicitly labeled older.
A failed latest request shows an error and never presents the previous clip as
current. The player loads the candidate before replacing the displayed video,
preserves a compatible playhead position, and leaves playback paused.

Use the Review menu to play an older **ready preview** without changing the
project. This is separate from `moviestar undo`, which changes the saved project
and requests a new preview. There is no redo command or Undo/Redo viewer button
in this slice. At most eight requests are retained, including the active/current
ones; historical media may expire. Rendered media and native descriptors are
session-local and removed on shutdown. Native history references the original assets and does not archive
old asset bytes; leave source files unchanged during review. The viewer is not
a permanent archive of every project revision.

## Review quality and measurements

In native mode, CSS uses the authored logical canvas, and the browser decodes
the source at its available resolution. Scaling the viewer does not rewrite the
saved layout. The ready response reports `mode: "native"`, `transport:
"native-browser"` and the native profile with its clip-end tolerance. This is a
playback descriptor, not a downloadable rendered artifact.

In rendered mode, the authored logical canvas, layout rules, timing and frame rate are preserved.
The existing spatial renderer first generates at the authored raster, then the
review encode fits within a 1280-pixel long edge and 720-pixel short edge without
upscaling. Output is H.264, yuv420p, x264 `fast`/CRF 23, optional AAC audio at
48 kHz, with MP4 fast-start metadata. This preserves geometry but does not yet
reduce the authored pixel workload. Video composition now uses PyAV scaling
and bounded NumPy mask arithmetic instead of per-plane FFmpeg subprocesses.
Chrome still supplies the layout, clips and visibility masks. NumPy is a direct
local-render dependency, with its redistribution terms recorded in the
dependency license policy. There is no segmented/progressive delivery:
the completed short MP4 must be ready before this transport can play it.

The status page identifies the displayed request, version, range and quality.
For diagnostics, open the browser console and read:

```javascript
window.movieStarPreview.state
window.movieStarPreview.measurements
```

Rendered-mode measurements include generation time, request-to-playable readiness, player
loading, time waiting for Play, Play-to-first-presented-frame, and playback stall
count. First-frame timestamps use `requestVideoFrameCallback` when available,
with a `playing` event fallback. Absolute request-to-first-frame time includes
the user's Play delay and is explicitly labeled that way. These events cannot
prove perceptual audio/video synchronization: use matched flash/beep cues and
actual listening for that acceptance check. Diagnostics stay in the local page;
there is no telemetry upload.

Native mode also exposes `window.movieStarPreview.mode`, `.displayed`,
`.events`, `.is_current` and `.stage`. Events identify the request/version and
visible readiness time; the stage exposes relative time, paused state, errors
and local playback metrics. A multi-scene packet lists `segments` with their
result ranges and individual source clocks; there is no single source clock for
the sequence. `stage.snapshot()` identifies the active segment and its canonical
node paths. Cut metrics include handoff times and `end_result_time`; per-source
review retains its existing source-time end measurement. Its optional `?audit=1` records source barcode/flash
samples for the synthetic benchmark. Normal viewing avoids per-frame source
canvas readback. The audio worklet passes through the sound sent to the output;
clock estimates still require a physical flash/beep check for acceptance.

At a camera cut with a prepared independent audio mix, the outgoing videos hold
their canonical final pictures before the incoming scene is revealed. Their
decoders stop while the independent mix keeps running, so an occluded inset
does not decode beyond its selected range. Source-audio-only cuts retain their
running transport until the handoff.

Native preview reuses completed caption images when only audio intent changes.
Every new request still validates and compiles its current saved state and prepares
its current audio. Matching captions are copied byte-for-byte into the new
request's folder, so deleting an older review cannot remove the current images.

The reuse key includes the canonical caption job, visual/range state, renderer
source identities, FFmpeg executable/version, Python/NumPy/PyAV versions, and
discovered font/configuration identities (including macOS CoreText fonts).
Same-path replacements with preserved size/mtime still invalidate via inode,
ctime and resolved destination. Changed captions, word timing, styling, canvas,
layout or source identity cause a miss. An input change during copy, missing or
corrupt candidate, unknown font inventory, or expired lookup budget falls back
to normal painting. Host `fc-list` and `fc-conflist` discovery is optional; without
it native preview still works and paints normally.

The foreground session holds at most eight candidate jobs referencing at most
32 MiB of existing images. Eviction drops references; it never deletes another
request's files. Candidates come only from successfully published requests and
expire with their existing request owners. There is no persistent/shared render
cache. The read-only request state reports numeric `caption_reuse` counters:
`hits`, `misses`, `copied_bytes` and `eligible_jobs`.

To measure native preparation, start the foreground session with
`MOVIESTAR_PREVIEW_TIMING=1`. Each completed request in the read-only `state`
endpoint then includes `preparation_timing`: snapshot/validation, queue/dispatch,
worker startup, initial module imports, descriptor compilation, audio preparation,
caption reuse checks/copies, each fresh caption paint, descriptor publication,
and worker exit/supervisor publication. Lazy imports belong to the phase that
triggers them.
Intervals use one host monotonic clock; `started_at` anchors them to epoch
milliseconds for comparison with command and browser traces. `elapsed_ms` equals
the sum of phase durations plus explicitly reported `unattributed_ms`.
`generation_started_ms` identifies the portion corresponding to the existing
`generation_ms`; it excludes earlier snapshot preparation.

The timings are off by default, contain only numeric intervals and fixed phase
names, and follow the existing eight-request retention limit. Failed workers
retain incomplete timings; unavailable diagnostics are labeled explicitly and
do not reject an otherwise valid preview. No timing creates a new version or
changes validation, quality, playback clocks or readiness deadlines. Worker
publication is not browser visibility: use the command trace and viewer's
request/version readiness event to measure the complete feedback loop. A new
worker is launched for every request; a repeat edit is not a warm Python process.

In native mode, the local `state` endpoint also reports `native_video_delivery`:
successful validated HEAD requests, video GET requests, active GETs and completed
body bytes written to sockets. The byte count excludes headers and any partial
write that raises on disconnect; it is not an exact wire/client-consumed count.
It includes source transfers that browser DevTools can omit for canceled or
service-worker responses. These counters contain no asset paths or content.

The production command benchmark runs 20 real model edits per orientation,
including CLI startup, worker startup, source readiness and a fresh visible
revision. See the reproducible runner in
[`experiments/live_browser_preview`](../experiments/live_browser_preview/README.md).
Small synthetic results do not establish three-second performance for large
projects, real camera footage or arbitrary supported browser/codec combinations.
The runner's `--profile long-source` additionally checks beginning/middle/end
windows in 121-scene hour-long projects against a 90-minute synthetic recording;
its source is intentionally low bitrate and does not represent gigabyte camera
files. See its report for the exact source size, scene counts and measurement scope.
The additional `cut_benchmark.py` runner checks A → B → A source switching,
canonical layouts/captions around both boundaries, every expected source frame,
audio cues after the final switch, and a continuous synthetic pilot for gaps.
Its long-source profile uses 123-scene hour-long projects. Private recordings
can be supplied explicitly; those reports/screenshots remain private.
Final delivery-quality export remains a separate `moviestar export` operation.

## Bundled viewer typeface

DM Serif Display Regular is bundled unmodified from
[Google Fonts, revision 7efaaeb](https://github.com/google/fonts/tree/7efaaeb60f72588557a6605b48311611937304ba/ofl/dmserifdisplay).
Its [SIL Open Font License 1.1](../app/src/moviestar/fonts/DMSerifDisplay-OFL.txt)
and copyright notice ship alongside the font and in the wheel's license files.
The font SHA-256 is `8cc3643535edf039aa5d95440a8542735e9197e4f4b8d9303e980fefbf5ab616`.
The viewer also uses the existing bundled Inter family under its existing OFL.
