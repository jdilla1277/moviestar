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
headless Chrome, Node or FFmpeg; the human's browser decodes the source. Loading
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

Each selected span can have **one or two visible MP4s**. One visible video
carries the resolved speaker audio; the other stays muted. Each video keeps its
own source offset. Play, Pause, seek and revision replacement operate on both
pictures. Both first frames must be prepared before the version becomes current.
Both video elements feed the same Web Audio clock. The selected speaker's gain
is one and the listener's gain is zero; both transport starts are submitted
together. Pause, seek and replacement cancel pending startup too.
A cut switches the composition and its selected speaker audio together. A → B → A can return to
the same recording at a different source position, with a different layout,
caption state or component arrangement in each scene.
Every source clock must be unit speed, matching the indexed output rate
(24–60 fps). Reusable components, text, shapes, images, manual/frozen captions and
canonical sampled translation/scale/opacity are supported. Media wrappers must
be relative/absolute, without padding, border widths or nested children.

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
`scenes set`, `scenes geometry` and `undo` appear in the viewer. Preset camera or
pacing motion and preset text/caption overlays intersecting the review window
still require rendered mode. Spatial scenes retain their existing component,
image, text, frozen-caption and motion support.

Windows containing more than three spans, crossing pacing boundaries inside
one scene, overlapping any transition, or needing more than two visible videos
at once are rejected. Transitions elsewhere in the
project do not block an otherwise supported window. The chosen sources must have
no source edit operations, and placed caption tracks must use manual/frozen
cues. Edits on unrelated sources and unplaced live caption tracks do not block
this window. Independent continuous audio across camera cuts, project-wide audio
mixing/gain/fades remain unsupported, as do
held/speed-changed media in the selected span. Ordinary unit-speed spans after
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
A disposable snapshot of the outgoing last valid
video frame covers the brief decoder/audio handoff, so a decoded frame beyond
the edit is not displayed. This is an in-memory boundary frame, not an encoded
or reusable rendered clip.

Cut and audio/display timing use a 50 ms reference tolerance. Muted pictures
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
