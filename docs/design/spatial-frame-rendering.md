# Spatial frame rendering

This slice connects the persisted visual tree and canonical media clock to a
real still-frame preview. It advances milestone 2; release-throughput gates
remain open. Scene-relative element and
component motion, watch, and export now use this same frame path.

## Measured layout reservations

Scene layouts may declare `reserved_regions`, each with a stable ID, a relative
occurrence target, and optional allowed occurrence subtrees. The temporal
compiler retains the declarations and diagnoses unresolved references without
preventing draft persistence. Shared edits cannot silently remove previously
referenced targets. Existing scene-root property edits and undo cover the list;
there is no separate region registry or timing model.

For scenes using reservations, Chrome's `IntersectionObserver` supplies clipped,
post-motion border rectangles. This follows actual containing-block and overflow
relationships, including absolute children that escape static overflow ancestors.
The pure `spatial_regions.py` checker compares unrelated boxes with each target,
exempting its descendants/ancestors and explicitly allowed subtrees. It returns
addressed blocking diagnostics and a region report. Occupancy is rectangular
and conservative; no pixel masks, OCR, stacking-occlusion analysis, or general
collision heuristic is introduced. Completely transparent intruders are ignored;
a reserved target remains reserved regardless of content/opacity.

Layout inspection includes diagnostics and dashed guides for repair. Rendering
runs the same checker before publishing assets. Single-frame views check the
requested sample; video checks every rendered output sample, including positions
between motion keyframes, and retains the command's atomic publication boundary.
Dry-runs do not claim measured collision readiness. Without declarations, the
browser performs no intersection-observer work and layering remains unchanged.
Active-transition still inspection and real-content acceptance remain separate
gates; no rendered-clip reuse or cache invalidation is added.

## Explicit scene time without carrier footage

A named composition scene may now store `duration` seconds with `slots: []`.
It must reference a scene-owned spatial tree and use the `single` compatibility
layout envelope. `scenes set` JSON creates its blank tree and stable identity
atomically, preserving an existing tree when its scene ID survives a rename or
duration edit. Undo includes the duration and tree. File parsing, spec validation,
and the published schema reject duration on legacy scenes with footage slots;
explicit-duration precedence over slot pacing is outside this slice.

The pacing resolver uses the explicit duration to create an ordinary canonical
segment with no slots. No fake source, resolved video slot, or second clock is
inserted. The spatial compiler uses scene time directly as its source-local
clock, so non-video component motion and optional bound videos use the same
frame path as footage-backed scenes. Bound media can end early transparently
or be clipped at the scene end, without changing scene duration or adding audio.
The existing 30 fps fallback applies when the composition has no timed slots.

Transitions need no footage handles for a static tree. Existing timeline
transition-duration limits still apply; a bound video in a slot-free tree must
still supply its own media handles. Video export retains atomic staging and
the existing audio routing/mix. Static scenes have no implicit source audio;
per-scene audio_from is invalid without a slot. Tests cover static-to-preset
and preset-to-static dissolve/dip pixels and silence beside routed footage audio.

The model frame/layout/preview commands and standard screenshot/thumbnail inspect
render these scenes through the same compiler and renderer. Entirely source-free project
initialization and active-transition frame inspection are separate
work; a loaded project's unused source need not be available to render a static
scene. No rendered-clip reuse or cache invalidation is introduced.

Linux CI installs Chrome for Testing headless shell 156.0.8078.73 from Google's
[official distribution](https://github.com/GoogleChromeLabs/chrome-for-testing),
reports its version, and sets `MOVIESTAR_CHROME_PATH` explicitly. This is an
external test prerequisite, not an asset or binary distributed with MovieStar.
Local runs still discover installed Chrome or headless shell as before. The
pin makes the CI renderer explicit rather than depending on the runner image's
desktop browser; it does not change startup, request, or fidelity budgets.
Intermittent cold desktop Chrome startup failures observed before frame
measurement are tracked separately in [#86](https://github.com/jdilla1277/moviestar/issues/86).

## Standard still-image surfaces

`spatial_inspection.py` adapts the shared spatial frame to screenshot and mixed
preset/spatial thumbnail batches. Model views and standard frames share one
in-memory live-caption refresh helper. The compiler and dispatcher share exact
end-of-project sample selection, while inspect schedules retain canonical
microsecond boundaries rather than reading rounded display timecodes.

The adapter measures at the authored canvas and converts/resizes only the final
raster. Geometry metadata therefore stays in canvas coordinates. Screenshot
supports PNG/JPEG and optional platform annotation; the latter lints actual
browser text/caption rectangles through the existing platform policy. Dry-run
only validates/plans and does not claim browser-measured geometry.

Spatial inspection deliberately disables thumbnail reuse and allocates a fresh
output directory, including when asset contents change at the same path. It
borrows one command-owned browser across frames and stages the complete mixed
batch before publishing its directory. Preset-only paths retain their existing
renderer/cache. This adds no asset fingerprinting or rendered-clip reuse.
Failures during conversion, annotation, later frames, or browser shutdown cannot
publish an incomplete batch or replace a prior screenshot.

Active transitions still fail before publishing a single-tree frame, including
when the scene active at that time is a preset in a mixed project. Full blended
frame inspection remains follow-on work; watch/export remain the complete
transition path. Camera preview and source-specific commands retain their
existing behavior.

## Shared boundary

`compile_spatial_frame` selects the scene, expands linked components, and resolves
media clocks. `prepare_layout_frame` validates and translates the supported CSS
rules and samples scene-relative visual tracks. One browser page measures the result, paints graphics, and can also draw
the labeled layout view. Media background paint never participates in flow, so
adding asset pixels leaves measured element boxes and text lines unchanged.
Each asset is a content-box background layer on its own element; an empty final
layer keeps the authored background color over the padding and border box.
Static positioning needs no extra containing block or positioned wrapper, and
ancestor clips and stacking keep their normal CSS behavior.

FFmpeg decodes each distinct asset/time pair once, normalizing display aspect
ratio and orientation into a PNG. Local images become RGBA PNGs painted by the
browser. Stationary videos remain opaque RGB frames composed by FFmpeg; videos
in moving subtrees are decoded RGB stills painted by Chrome. This avoids browser
video codec and seek differences. The frame response records the source times
and pixel-aligned sampling rectangles used by the selected paint path.

For video, the existing PyAV dependency seeks to the preceding keyframe and
walks decoded presentation timestamps until the first frame after the requested
media position. The selected frame is therefore the one already displayed at
that time, including a VFR hold or the final frame interval. FFmpeg preserves
source timestamps and selects that integer PTS without accurate-seek discarding
the preceding frame. Integer ticks avoid rounding fractional-rate frame times
forward; source positions remain on the canonical timeline. The timestamp walk
has a 60-second deadline and stops after passing the requested position.
Some demuxers, notably H.264 MPEG-TS, can decode only frames after the target or
no frames after a backward seek. In that case the probe reopens the source and
walks from the beginning under the same deadline. If FFmpeg's own seek produces
no PNG for the chosen PTS, it also retries without seeking, preserving source
timestamps and the exact selected frame. This fallback can require more decoding
on poorly indexed sources; it never substitutes a later frame or extends a
frame past source end.

The alpha guard checks stream pixel format and metadata before decoding RGB.
VP9/WebM can carry alpha while the default decoder reports `yuv420p`;
`alpha_mode=1` rejects that source rather than silently flattening it to opaque.

## Browser paint and video visibility

A flattened graphics image cannot simply be overlaid on every video: it can put
a parent's background over its own child footage. DOM order also does not account
for stacking contexts, clipping, or group opacity.

The supported paint vocabulary uses normal source-over composition. For opaque
video, its contribution to each output channel is linear in its source pixels:

`result = base + sum(video_pixels[i] * visibility[i])`

Chrome first captures the graphics with all video surfaces black. It then captures
each video surface white, leaving other video surfaces black. Subtracting the base
from that capture measures the surface's visibility through every foreground and
ancestor opacity/clip. These are RGB contribution masks, including antialiased
text edges. FFmpeg scales each source frame into its measured sampling rectangle,
multiplies it by its mask, and adds it to the base. Sixteen-bit intermediate planes
retain fractional contributions until the final RGB PNG conversion.
The temporary full-canvas video layers use RGB backgrounds, preserving odd
canvas dimensions on older FFmpeg builds whose
[`color` source rounds subsampled dimensions down](https://github.com/FFmpeg/FFmpeg/blob/n6.1.1/libavfilter/vsrc_testsrc.c#L232-L246).

Calibration surfaces use opaque PNG textures with the source's intrinsic size,
matching the browser reference's image path. Every capture forces a complete
repaint: partial repaint can reuse rounded-clip edge pixels from a previous
calibration. CSS filters, blend modes, arbitrary transforms, and user HTML/scripts
are outside the accepted vocabulary. Authored visual motion uses only the
validated translation/uniform-scale/opacity tracks below. Video with alpha is rejected because its pixels
would change the visibility of underlying paint; images with alpha are browser
graphics and are supported.

This boundary can supply a future export compositor with static graphics and
masks while FFmpeg supplies changing video frames. The current preview regenerates
everything and removes its temporary directory. Reusing previously rendered scene
clips and cache invalidation remain a separate project.

## Verification

### Scene-relative visual motion

`spatial_motion.py` validates independent tracks for translation x/y, uniform
scale, and opacity. Each track has strictly increasing scene-relative result
seconds, linear interpolation, and endpoint holds. Pixel or own-box percentage
translation resolves after responsive base layout. Local track overrides use the
same stable paths, shared edit conflicts, reset, variation baking, and undo as
existing visual properties. They do not change source pacing, media ranges,
caption cue selection, or audio.

The browser records every `base_rect`, then applies top-left-origin transforms
and multiplicative opacity without changing flow or wrapping. `rect` and text
line bounds use the transformed coordinates; `effective_opacity` includes
ancestors. Rectangles describe geometric boxes, not unclipped pixel occupancy.
A translated/scaled static target or fixed descendant would acquire a different
containing block. Those cases fail with an addressed error; the normal relative
positioning default and absolute children are supported. Opacity-only motion
does not introduce a transform and can fade a static target. Computed display
must provide a transformable box for geometric motion: non-replaced inline,
contents, table-column, and table-column-group targets fail with a hint to use
block or inline-block. Opacity-only tracks remain valid on inline text.

Cumulative scale is calculated from the ancestor motion factors, independently
of measured width. A zero-width caption may still have glyph fragments; its
content-box bounds stay finite, overflow stays inspectable, and preview rejects
it before replacing artifacts. Regression cases cover an unanimated caption
and shrinking/growing ancestors.

Sharp scaled patterns exposed the existing FFmpeg/Chrome interpolation
difference under motion. Rather than change the 3/255 paint budget, videos with
motion on themselves or an ancestor use a decoded PNG as browser background
paint. This path is selected by authored tracks, consistently before, during,
and after the move. It retains exact FFmpeg frame selection while sharing the
browser's transformed sampling, clips, and group opacity. Other videos retain
the mask compositor: moving video paint is included in the base and calibration
captures, so it correctly occludes stationary footage. The renderer returns
all media rectangles independently of which paint path owns their pixels.

`test_spatial_motion.py` covers before/middle/after samples in portrait and
landscape, nested element/group motion, fractional scales and translation,
patterned footage, transparent images, and overlap between moving browser paint
and stationary mask-composited footage. All retain the existing maximum 3/255
paint comparison. Geometry checks independently calculate ancestor/child
transforms from the base boxes. Source frame/color checks and clock tests cover
speed changes, holds, scene offsets, and the final output-grid sample. Caption
checks retain cues, wrapping, and scaled content-box overflow measurement.
File-based CLI tests cover dry-run/save/read-only preview, equal rendered and
labeled boxes, and revision undo. Unsupported containing-block changes preserve
prior artifacts. This is still-frame evidence; interval-wide collision checks,
single-frame transition inspection, release-throughput verification, custom
easing, and render caching remain separate gates.

### Responsive canvas rules

The temporal compiler preserves `format_rules` on scene layouts, elements, and
component instances. Layout preparation matches those predicates against the
requested output canvas, then resolves base properties, highest-priority matching
rules, and local occurrence customizations. CSS aliases and overlapping gap/grid
shorthands are expanded to effective longhands before checking conflicts.
Equal-priority conflicting final values produce an addressed
diagnostic; inspection shows provisional base geometry, and preview rejects it
before publishing. Matching rules cannot change bindings, timing, or audio.

The shared browser request contains the resolved CSS and its property provenance.
Both preview and labeled measurement return `scene_layout` and per-box
`matched_format_rules`/`rule_sources`. Responsive text uses the same browser line
measurement and caption overflow checks. Scene-local edits remain local, reset
restores the cascade, and saving a separate component preserves unconditional
customizations while retaining responsive choices for other properties.
Variation baking uses that same expansion to remove only overridden longhand
assignments; an unmodified column gap or grid boundary stays responsive even
when another part of its original shorthand becomes an unconditional local value.

Tests cover portrait/landscape/square, inclusive numeric bounds, nested component
rules, explicit priorities, conflicting and identical ties, local overrides,
reset/variation, save/reload, and read-only canvas selection. Real video cases
retain source frames 5/7/9 while changing the arrangement, and pass the existing
3/255 browser paint comparison in both canvas orientations.
Review regressions compare actual geometry before/after variation save in both
formats, check conflicting/compatible/ranked gap rules in both array orders, and
compare expanded grid placements with Chrome's original shorthand parser.

### Timed caption elements

Caption elements select existing result-time cue records by track. They do not
derive speech or introduce an independent media clock. The CLI refreshes live
recipes through the existing caption pipeline in memory; a failed refresh blocks
only placed uses of that track. Imported/frozen tracks retain their stored cues.
No caption element means no generated caption layer in a structured scene.
Legacy preset scene rendering remains on its existing path.

Chrome lays out active cue text in the caption's authored box using the same
text path as regular text. A component can accept a typed caption-track binding
and forward it through nested instances. Line rectangles and active cue IDs,
text, and bounds are inspection data; the labeled view hides text after measuring
it. Glyph-line bounds outside the caption content box produce `caption_overflow`.
Inspection still returns that diagnostic; preview rejects it before publishing
an artifact. Empty intervals retain the reserved region and its authored paint.
The element owns typography and placement; legacy cue style/position and
spoken-word highlighting are not part of this whole-cue rendering slice.

The caption tests exercise exact cue boundaries, gaps, overlapping cues,
component bindings/defaults/forwarding, portrait and landscape, one/two-line
regions, overflow, unknown tracks, and read-only CLI behavior. A real generated
track follows a scene reorder on all three spatial views. Caption text over three
nested video uses also passes the existing 3/255 browser paint parity budget.

### Media and compositing

`app/tests/test_spatial_render.py` generates a lossless 10 fps clip whose source
frames have distinct RGB values. Three uses inside nested reusable components
sample source times 0.5, 0.7, and 0.9 seconds. Their actual pixels identify frames
5, 7, and 9. A separate two-color source checks orientation and contain/cover
placement, including transparent letterboxes.

The tests compare every output channel against a browser reference using the
same decoded source PNGs, with a maximum error budget of 3/255 declared in the
tests before implementation. This budget checks paint composition on flat-color
footage and patterned footage at native sampling size. They cover landscape and
portrait, long text,
transparent images, parent backgrounds, foreground badges, intersecting rounded
clips, group opacity, and overlapping video uses whose paint order differs from
DOM order. Layout and rendered views must report identical element rectangles.
Ended clips become transparent; missing bindings/assets and unsupported
alpha video fail explicitly. CLI coverage checks JSON errors, unchanged project
state, and preservation of the previous artifact when an asset disappears.
Regression cases also check between-frame requests at source times 0.65 and
1.95 seconds, fractional rates with real B-frames and nonzero timestamp origins,
VFR gaps, rejection past source end, transparent VP9/WebM, and static-positioned
video/images in padded flex layouts. Those media tests compare actual pixels
with the measured content box and preserve the authored padding/background.
The MPEG-TS regression compares the first, middle, and final displayed frames
against an independent full decode, including the reported 0.5/1.5-second
positions and rejection after source end.

This establishes still-frame geometry and paint parity on synthetic content.
It is not evidence for export throughput or every source decoder.
Real-content watch/export friction tests and single-frame transition inspection
remain separate delivery gates.

The browser reference is a paint check, not a promise that FFmpeg's video scaler
matches Chrome's image scaler. A larger manual `testsrc2` example measured mean
channel differences below 0.4/255, but individual sharp-edge pixels differed by
69/255 in landscape and 21/255 in portrait when resampled. Those cases do not
meet the 3/255 paint budget and are not counted as passing that gate. Stationary footage in the product
preview uses FFmpeg's bilinear video sampling; moving subtrees use the browser
still-image path described above. Future export must preserve the same path
selection and sampling. Native-size patterned coverage isolates paint fidelity from this
known resampling difference. Do not claim arbitrary scaled footage is pixel
identical to browser image rendering. This manual check used a 320×180, 10 fps
source sampled at 0.5/0.7/0.9 seconds on 960×540 and 540×960 canvases, macOS
arm64, Chrome 155.0.8059.85, FFmpeg 9.0.1, and Node 24.5.0.

The initial Ubuntu CI run passed every renderer case but hit the previous
15-second Chrome startup limit in two layout tests. Startup now allows 30 seconds,
with a 60-second overall browser-process timeout. These are bounded failure
deadlines, not changes to the renderer spike's performance acceptance budgets.

Page discovery now retries transient connection failures, non-success HTTP
responses, invalid/incomplete JSON, and missing targets for at most 5 seconds
after the port becomes available. Each request is bounded to 500 ms (or the
remaining deadline), and Chrome exit aborts discovery. Permanent failures retain
the last discovery error and Chrome stderr. This does not retry rendering or
change the 30-second port / 60-second process deadlines. A subprocess regression
injects connection refusal, HTTP 503, incomplete JSON, a stalled request, and an
empty target list before connecting to real Chrome and measuring geometry.
Another keeps returning HTTP 503 and verifies bounded failure with the prior
artifact preserved. These tests cover the transient discovery failure mode;
they do not establish the exact cause of the original CI `fetch failed` result.

## Spatial video integration

`spatial_video.py` selects the shared spatial frame renderer for structured
scene clips and the existing layout renderer for preset clips. PNG sequences
feed the existing video encoder through `frame_sequence`; routed source audio
uses the existing slice/tempo filters once, independently of visual reuse.
The video filter limits each PNG sequence without an output-level duration cap
on audio. Concatenation decodes zero-origin scene clips without seeking past AAC
priming, preserving the full authored audio samples independently of quantized
video frame counts.
Both structured and preset clips in a spatial project use the global frame
count, including a watch containing only preset scenes. The preset FFmpeg path
phases source timestamps before CFR sampling, offsets camera/layout motion, and
anchors overlays to its first global sample. This prevents fractional cuts from
adding frames or shifting footage relative to audio in mixed compositions.
Preset sampling uses a nanosecond working clock with a one-tick rescaling guard
before retiming, so recurring timestamps such as 1/30 do not delay aligned
frames. Preset-only projects retain their existing path. Empty output-grid windows are
rejected.

The existing source-handle planner and FFmpeg transition compositor blend both
structured and mixed scene clips. `compile_spatial_scene_frame` is an explicit
owned-layer compiler for handles; normal `compile_spatial_frame` retains its
active-transition guard. Handle footage follows edge pacing; component motion
and caption cues hold scene endpoint state. Placed media handles are checked
against asset duration in addition to the legacy carrier-slot checks. Watches
expand to cover intersecting transition windows, align the render origin to the
global output frame grid, and crop by frame indices after compositing. Exact
internal sample origins are kept separate from rounded timecode reports; audio
is cropped to the requested times without video-grid quantization.
Project-edge fades apply to the complete video before crop. Audio mixing and
mastering apply to the requested result-time window after this visual stage.

The destination is replaced only after frame generation, encoding, mixing, and
mastering succeed. Intermediate files are temporary and removed on failure.
Dry-run plans do not launch Chrome or decode assets; runtime glyph/asset failures
remain possible and preserve previous output. This introduces no clip cache.
A watch/export owns one lazy browser session across all its structured scenes.
Per-frame source decoding/composition and PNG storage remain throughput
limitations; decoder reuse and streaming remain follow-on work.

`test_spatial_video.py` uses real encoded clips and decoded frames to verify
portrait/landscape motion placement, audio RMS without duplicated routing,
authored gain, global clocks, mixed transitions, watch windows, edge fades,
caption declaration policy, non-aligned scene cuts, fractional-cut audio duration
and tone alignment, fractional-transition watch/export frame-count and blend
parity at 10 and 30 fps, alternating preset/spatial fractional cuts in both
orders with exact sampled footage and audio windows, insufficient media handles,
and preservation of prior output after late caption/media failure. These are
MP4 correctness checks with lossy-encoding patch tolerances; the existing PNG
paint-parity budget is unchanged. No release-throughput claim is made.

The owned Chrome process is now awaited before deleting its temporary profile.
Shutdown allows two seconds for SIGTERM, then one second after SIGKILL; profile
removal has bounded retries. Completed shutdown timers are cancelled so they do
not delay normal frame completion. Cleanup returns errors to the driver and
never replaces an earlier startup/render error with a Node filesystem stack.
This fixes a reproduced `ENOTEMPTY` race that could otherwise report only the
Node version. Deterministic lifecycle tests cover delayed graceful shutdown,
forced shutdown, and removal failure. Port/discovery/process deadlines and
fidelity/performance acceptance budgets are unchanged.

The Python driver also owns the profile and, on POSIX, a separate process group.
If Node times out or crashes, the driver terminates that owned group and removes
the profile instead of leaving browser descendants behind. The existing
60-second rendering deadline is unchanged. A real subprocess-tree regression
verifies descendant termination and profile removal after a shortened test-only
deadline; no renderer deadline or performance budget is relaxed.


## Command-owned browser sessions

`spatial_session.py` keeps one Node/Chrome worker alive for a video render and
passes sequential, numbered JSON-line requests through the same page renderer
used by standalone frames. The Node input iterator is installed before browser
startup so the first request cannot be lost during the startup handshake.
Every request reapplies the viewport, rebuilds the DOM, resolves current text,
media and motion, and installs fresh paint/label callbacks. Rendered or labeled
views from an earlier request cannot persist into a later frame. Standalone
measurement and PNG commands retain the single-request protocol.

The Python context lazily starts the worker, owns its temporary profile and
POSIX process group, drains bounded stderr, and bounds response size. A request's
60-second deadline includes blocked pipe writes and waiting for a complete
response. Node independently bounds startup and render operations so a stalled
CDP wait can terminate even if the Python caller disappears. Its deadline timers
are cancelled on completion. Invalid JSON, wrong response IDs, driver exit, render errors, or a
request timeout close the session permanently. Frames are never retried or
replayed. Normal shutdown allows four seconds for EOF-driven browser/profile
cleanup, then terminates the owned group if needed; shutdown failures abort the
movie before publication. A primary rendering error survives cleanup.

Video export shares the session across scene clips and closes it before concat.
Preset rendering uses the existing FFmpeg path and a preset-only watch does not
start Chrome. Dry-run starts no worker. No reusable clip cache or new dependency
is introduced. Source decoding and PNG inputs remain per frame, so browser reuse
alone does not close the release performance gate.

`test_spatial_session.py` checks real browser geometry/PNG parity across viewport,
tree, media, motion and labeled-view changes. Controlled subprocess workers
exercise stderr backpressure, malformed/truncated replies, driver death,
request/blocked-write timeouts, descendant termination, profile cleanup, and
shutdown/primary-error behavior. Video tests count one worker across two scenes
and verify that late caption failures and failed shutdown preserve prior output.

### Browser exit and inherited stderr

Browser teardown waits for the Chrome parent process's `exit` event. Node's
`ChildProcess.close` also waits for inherited stdio descriptors, so a surviving
Chrome helper can hold it open after Chrome and its debugging socket have exited.
A desktop Chrome trace reproduced that case: Chrome exited about 70 ms after
SIGTERM, but an inherited stderr pipe kept the worker alive until Python's
four-second shutdown deadline. Teardown now releases the captured stderr stream
after confirmed process exit. Both one-shot and session Python owners reap their
remaining process group; normal completion still requires successful cleanup.

The existing two-second TERM grace, one-second KILL grace, four-second Python
worker-exit deadline, and sixty-second request deadlines remain unchanged. A
browser that never confirms exit produces a cleanup error without removing its
profile. Profile removal errors and genuine worker-shutdown failures still fail
publication, and an earlier rendering error keeps priority. No frames are retried
or replayed. A deterministic regression uses a fake Chrome parent with a helper
that inherits stderr; both one-shot measurement and a reused session must exit,
reap the helper, and remove the temporary profile.
