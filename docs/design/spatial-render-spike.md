# Spatial composition renderer spike (milestone 0.2)

**Decision for the tested layout subset:** use a constrained CSS-style layout
vocabulary in the JSON project document. Resolve layout and rasterize text, shapes, and images
with a browser; place source video and encode final frames with FFmpeg. The
temporal parity and integrated performance gate now passes on the reference
machine. General browser paint-order compilation and production integration
remain separate implementation work.
The [reproducible experiment](../../experiments/spatial_scene_spike/README.md)
contains synthetic assets and measurement scripts. It does not implement a
production schema or renderer.

## Authoring and frame boundary

A scene owns a layout tree. Its nodes are elements or component instances;
definitions can contain either, including a built-in `Stack` and a user-defined
`ListenerPair`. A component instance supplies bindings and local choices. The
prototype expands both component origins through one function. It renders the
same `ListenerPair` in two podcast scenes with swapped Alice/Bob video bindings;
no preset or renderer code changes between those scenes.

The authoring syntax for milestone 1 should be JSON with identified nodes,
bindings, and a `style` object whose property names follow CSS spelling. Only
layout, paint, text, and clipping properties in a documented allowlist are
accepted. Format rules are explicit conditions on the output canvas rather
than free-form CSS selectors. Motion is a separate scene-time track for
translation and opacity, so the same values can drive browser inspection and
FFmpeg media/layer placement. An illustrative fragment is:

```json
{
  "kind": "component",
  "id": "inst_listeners_a",
  "definition": "ListenerPair",
  "bindings": {"left": {"asset": "alice"}, "right": {"asset": "bob"}},
  "style": {
    "grid-column": "2", "min-width": "0", "overflow": "hidden"
  },
  "format_rules": [
    {"when": {"orientation": "portrait"}, "style": {"grid-column": "1"}}
  ],
  "motion": {"translate-y": [{"at": 0, "px": 0}, {"at": 3, "px": 18}, {"at": 6, "px": 0}]}
}
```

This fragment chooses names and shape for the spike; milestone 1 must validate
types, allowed CSS values, stable IDs, overrides, and draft diagnostics before
publishing a persistence schema. CSS Grid, flex, positioned layers, intrinsic
text sizing, percent sizing, min/max constraints, aspect ratio, overflow, and
draw order remain the intended vocabulary. Arbitrary stylesheets, selectors,
remote URLs, scripts, and CSS animations are outside the initial authoring
surface. Movement of an element or component subtree is included.

At a requested canvas and result time, the browser computes boxes, text lines,
paint order, and raster layers for text, images, shapes, and caption treatment.
It can replace asset pixels with labeled placeholders without changing layout.
For each video node, browser paint must be split at that node: ancestor and
earlier-sibling graphics behind it, then clipped video pixels, then later
siblings and foreground graphics. A parent background cannot be flattened over
its own video child. FFmpeg should consume measured boxes, clip masks, source
positions, and scene-time motion from the same frame description as preview and
inspection. Watch and export must consume this description too. A transition
frame needs outgoing and incoming trees. Audio routing remains the timeline's
decision, independent of visual placement.

## Measurement

The budgets were [recorded before rendering](../../experiments/spatial_scene_spike/criteria.md).
The [codec amendment](../../experiments/spatial_scene_spike/criteria-amendment.md)
was recorded after the original encoded-edge criterion failed and before the
candidate was retested. It separates lossless clipping accuracy from H.264
color variation; geometry, timing, throughput, and memory limits did not move.

Measured on Apple M4 arm64 with 24 GiB RAM, macOS, Chrome 155.0.8059.40,
FFmpeg 9.0.1 (`ffmpeg-full`, GPL/x264 enabled), Node 24.5.0, and the repository's
Inter Regular/Bold fonts. Six-second samples used 30 fps and 1280x720 or
720x1280 canvases. Figures below are local wall time; they are not a cross-device
performance guarantee.

| Gate | Budget | Browser full-frame path | Browser layout + FFmpeg path |
| --- | ---: | ---: | ---: |
| Fresh single-frame preview, five-run P95 | <=3.0 s | 0.602 s browser capture | **1.298 s** integrated temporal gate |
| Warm preview, 20-run P95 | <=0.70 s | 0.050–0.122 s by composition | **0.142 s** integrated temporal gate |
| Six-second, 180-frame export | <=6 s | **15.492 s capture** before encoding | **1.361 s** from fresh browser launch through completed encode |
| Peak renderer RSS | <=2 GiB | 1,304 MiB browser process tree under export load | **1,253 MiB** complete export process tree |
| Earlier export size at 1280x720 | recorded | browser capture frames not retained in Git | **2,000,588 bytes** GOP; 6,978,410 bytes all-intra |

The cold probe regenerates a frame description in a fresh browser process for
each run at scene time 3 s, then passes that exact JSON file to the preview
renderer. Each description has a unique measurement ID, and the runner asserts
that all five IDs are distinct. The old 1.148 s number used a stale media-layout
result and is not acceptance evidence. The earlier 0.989 s GOP number measured
only an FFmpeg stage after the graphics and source clips existed. The temporal
gate below supersedes it for end-to-end export acceptance, warm preview, and
memory. Synthetic source generation remains outside the export clock because
those files stand in for existing user media.

The full-frame browser path misses the export throughput gate. An FFmpeg-only
text path misses text parity: even after manually tuning the vertical position
of a one-line Inter title, its white-glyph intersection-over-union against
Chrome was 0.8333 (6,700 versus 8,003 white glyph pixels). It also lacks CSS
flow and responsive line breaking without a second text/layout engine. The
hybrid path uses the browser's text pixels instead.

The hybrid path rendered podcast, civic recap, text-led explainer, and a
text/image-only scene in both orientations. The civic composition includes a
transparent logo, footage, title, tag, and reserved two-line caption. The
explainer places a non-video text panel beside a speaker window. The civic
title occupies one line in landscape and two in portrait. The explainer
headline wraps to three different lines in each orientation; the browser's
measured line groups are retained in the frame result. A separate second
podcast scene proves component reuse with new media bindings.

Across the eight composition/canvas pairs, the lossless assembled frame's
non-video pixels differed from the browser reference by at most 2/255 per RGB
channel; no such pixel exceeded 8/255. That comparison excluded media regions
and therefore missed a parent background painted over video. A focused
counterexample puts an orange parent background, clipped red video, and a
yellow foreground badge in one component. The flat graphics overlay differs
from the browser by 102/255 at a fiducial inside the video. Splitting browser
paint into backplate and foreground, with masked video between them, yields
2/255 inside-video error and zero error at the parent background, foreground,
and outside-clip fiducials. This validates the counterexample; arbitrary DOM
overlap still needs a general layer compiler. The fixed-color rounded-clip
calibration had edge P95 difference 2/255, maximum 4/255, and zero outside-clip pixels
above 2/255. A normal H.264 export compared to a separately encoded one-frame
preview had rounded-edge P95 difference 12/255, maximum 20/255. The title's
white-glyph bounds in both decoded outputs were exactly `(35, 42)–(674, 82)`.
The civic recap's longer title and two-line caption differed by at most one
glyph-bound pixel among the browser reference, encoded screenshot, and encoded
export; their line groups were unchanged.
Decoded video pixels can differ between Chrome and FFmpeg because of independent
decoding and frame seeking; the fixed-color calibration isolates clipping.

The fresh frame-description probe samples 1280x720 at 1.25 s and 720x1280 at
4.5 s with a 100 px layout shift. It reads responsive browser boxes and an
authored translation track from one JSON description, then renders a preview
and a multi-frame export. Both paths use the same description hash. At the
sample times, their red video edges are within 0.39 and 0.26 output pixels of
the measured box. The source range has a nonzero 1 s start, but this red clip
does not prove which source frame was decoded.

At 0, 3, and 5.9 seconds, the encoded moving `ListenerPair` top edge and civic
tag left edge differed from browser-reported positions by at most 0.94 pixel.
Both types of transition produced a combined encoded video; the fade midpoint
was visually checked at 5.75 seconds. The structured/structured and
structured/legacy transition encode steps took 0.887 s and 0.908 s
respectively for the already rendered clips. Those earlier checks did not
establish temporal parity: the source clips lacked
machine-readable frame identities, the transition probe does not assert both
source positions around the cut, and the caption probe has no cue-boundary
assertions. The following gate addresses those gaps. No unchanged-clip reuse was
attempted.

### Temporal and integrated gate

Run `python3 experiments/spatial_scene_spike/temporal_gate.py --verify` from
the repository root. It generates two numbered, machine-readable source clips,
trims them at source frames 30 and 15, and tests scene frames 44–46 around a
caption cue change and 74–76, 82, 89–90 around a 15-frame dissolve. Browser
inspection records both source times and caption line positions; screenshots
capture the actual media and text; the same frame description supplies browser
graphics and media positions to FFmpeg preview and export. Encoded frame markers
are compared with browser screenshots, including the blended transition. The
one-line caption becomes two lines at frame 45 with its reserved box unchanged.
At frame 44, the gate also compares the FFmpeg single-frame preview with the
encoded export for source marker pixels and caption glyph bounds.

| Measure | 1280×720 | 720×1280 | Budget |
| --- | ---: | ---: | ---: |
| Maximum source-frame error, inspection and pixels | 0 frames | 0 frames | 1 frame |
| Transition barcode pixel error | 2/255 | 2/255 | 20/255 |
| Transition color error | 3/255 | 3/255 | 20/255 |
| Media edge error | 0.20 px | 0.19 px | 1 px |
| Caption glyph-bound error | 0 px | 0 px | 2 px |
| Caption region P95 channel error | 1/255 | 2/255 | 20/255 |
| Caption lines before/at/after cue | 1/2/2 | 1/2/2 | exact |
| Caption box movement | 0 px | 0 px | 0 px |

Measured on Apple M4 arm64, 24 GiB RAM, Chrome 155.0.8059.40, FFmpeg 9.0.1,
Node 24.5.0, Inter Bold, 30 fps, six-second H.264 exports. The 1280×720 export
was 1,232,426 bytes and the 720×1280 export was 1,199,280 bytes. Fresh preview
P95 was 1.298 s over five browser launches; warm preview P95 was 0.142 s over
20 browser-description-plus-FFmpeg frames; export took 1.361 s from browser
launch through encoded file completion; peak process-tree RSS was 1,253 MiB.
These are local measurements, not cross-device guarantees. The automated
unittest reruns the assertions rather than relying on these recorded values.

## Delivery implications

Milestone 1 can implement the JSON tree and compiler without baking preset
names into the schema. Milestone 2 should produce a stable frame description
with box, clip, line-break, draw-order, binding, rule-source, and occurrence-path
data, then make preview, labeled inspection, watch, and export consume it.
The focused probes demonstrate this shared description for one video box,
motion track, caption cue, transition, and two canvases. The broader composition
set was checked with earlier probes; a general paint-order compiler is still
needed for arbitrary overlapping element and component trees. The first product
renderer should only accept a documented subset that its compiler can place
faithfully, then expand that subset with parity tests.
Browser raster layers should be cacheable when content is static within a
scene; declarative translate/opacity tracks can move a layer or media subtree
without capturing every frame in Chrome. Reflowing text or arbitrary effects
during motion require a separately measured fallback before they are offered.
Production must also verify browser discovery/installation and memory on the
supported platforms. Keeping the browser and FFmpeg stages sequential avoids
assuming their peak memory footprints can be added safely.

The spike uses an installed Chrome binary and Node's built-in WebSocket only as
test tools. A product renderer should use an explicitly chosen Chromium
distribution or a validated installed browser, with dependency terms reviewed
at integration. Chromium's source is under a [BSD-style license](https://chromium.googlesource.com/chromium/src/+/main/LICENSE);
[FFmpeg's license](https://ffmpeg.org/legal.html) depends on build options and
the measured Homebrew build enables GPL and x264. The bundled Inter fonts are
under the [SIL Open Font License](https://github.com/google/fonts/blob/main/ofl/inter/OFL.txt).
The spike's Node runtime is [MIT licensed](https://github.com/nodejs/node/blob/main/LICENSE).
No browser, Node runtime, generated media, or new package dependency is added
by this report.
