# Native browser preview feasibility

This experiment tests a different way to meet the preview latency target: the
browser plays a source MP4 directly while the **same canonical scene DOM** used
by inspection and export places components, text, images and captions around it.
No output video is generated before the human presses Play. Export continues to
use the existing renderer. The measured player now ships as opt-in `moviestar preview --mode native`.
The existing rendered transport remains the default; the historical results below
come from the earlier standalone experiment. Current benchmarks exercise the
real CLI and its isolated descriptor workers.

The loop is local and requires no account or upload. The page reuses the product
viewer's dark branding, offline fonts, site links and current/history controls.
The compatibility launcher now opens the product viewer. Its account link does not
establish or recognize a website login session.

## Run

Use Python 3.10+, Node 22+ (with built-in WebSocket), FFmpeg with libx264/AAC, and
Chrome/Chrome for Testing. Install the package and its declared dependencies:

```sh
python3 -m venv app/.venv
. app/.venv/bin/activate
python -m pip install -e app
export MOVIESTAR_CHROME_PATH=/absolute/path/to/chrome-headless-shell
python experiments/live_browser_preview/fixtures.py --out /tmp/live-scene
python experiments/live_browser_preview/serve.py --project /tmp/live-scene
```

Open the loopback URL printed as JSON, then press Play. Each white flash should
have an aligned beep. Play requires a real user gesture; the experiment does
not bypass the browser's audio policy. Use another port with `--port` if needed.
Assets remain on disk and must remain unchanged during playback. Only exact
registered asset IDs are served; requests support ranges and HEAD. Replacing an
asset changes the request identity, and a stat mismatch rejects a stale asset
request. This is not an immutable snapshot of a file being modified in place.

A real edit reloads the latest compiled descriptor in a new, initially hidden
iframe. Only after source pixels, images, fonts and canonical layout are ready,
and the page has had a visible paint opportunity, does that revision become
current. Superseded requests cannot replace newer revisions. Errors keep the
last loaded version visible and label it. History is read-only; selecting an old
preview does not undo the saved project. History and descriptor retention use
the existing bounded preview queue. URLs work on the same machine only; exposing
one remotely requires a separate delivery/security integration.

Edit the generated project's spatial model using the actual CLI (run commands
from the project directory), or use a coding agent. Undo uses the existing CLI.

## Supported subset

The probe fails explicitly outside this subset:

- One selected spatial span, with exactly one visible video and its embedded
  source audio on the same unit-speed clock. The project can have other spatial
  or preset scenes, and pacing/transition behavior outside the review window.
- A frame-aligned window of at most ten seconds, at 24–60 fps, matching the indexed source rate (the installed profile excludes
  low rates whose early last-frame stop would exceed the reference timing budget).
- Native-compatible MP4; the portable benchmark uses H.264/AAC at 30 fps.
- Reusable nested components, shapes, text, images, manual/frozen captions,
  static canonical CSS layout, and canonical motion/opacity samples.
- Relative/absolute media wrappers without padding, border widths or children.

Source edit operations on the chosen video, ranges crossing cuts/pacing boundaries
or overlapping transitions, multiple video clocks, live caption derivation on placed tracks, external
soundtracks, gain/fades/mixing, holds and speed changes are not implemented here.
Other codecs, VFR, HDR/color-management parity, representative gigabyte camera sources, network
sources and cloud/phone delivery remain unverified. Unsupported browser codecs
fail visibly. Backend descriptors are bounded by the requested duration/frame
rate, not a general-purpose incremental scene compiler.

Native clipping of this test window is approximate: it stops at the last expected
source frame, within 50 ms in the reference check. This is **not** sample-accurate
audio trimming or a substitute for export timing. Media fragments alone proved
too coarse at the end, so a video-frame callback schedules the pause. A production
player needs broader timing and seek tests before it can own general timelines.

## Measure

```sh
python experiments/live_browser_preview/benchmark.py --out /tmp/live-results
```

The current native player uses a project-scoped, memory-only source-byte buffer
(64 MiB / 256 blocks, four source reads at a time). Every media request still
validates the live asset, including a complete memory hit. See
[`docs/local-preview.md`](../../docs/local-preview.md#bounded-source-byte-reuse)
for lifetime, invalidation and direct-playback fallback behavior. The benchmark
requires that buffer to control the page and checks its byte/entry limits.
`--baseline` permits an older player without it; all latency and fidelity gates
remain unchanged.

Source diagnostics include the server's completed body-write counts and
successful validating HEADs at each edit. These complement DevTools counts,
which can omit canceled/service-worker transfers. Completed writes are not an
exact wire/client-consumed byte count. Compare before/after using the same
counter and fixture, not a server total against a DevTools total. The sampled
process-family RSS includes the CLI/server, workers, driver and owned Chrome;
it is not the source-byte buffer's size.

### Optional real-media evaluation

Use native-compatible local MP4s of at least 30 seconds through the same real CLI loop without copying them into the
repository. The output includes source paths, subtitle text and screenshots:
**keep it private and do not commit it**. The runner does not upload anything.

```sh
python experiments/live_browser_preview/real_benchmark.py \
  --source /private/path/recording.mp4 --srt /private/path/subtitles.srt \
  --out /private/new-real-results
```

This creates 121-scene / hour-long projects, references each recording in place,
and reviews five seconds near its beginning, middle and end in both orientations.
Optional SRT cues use source time and become clipped, frozen/manual captions in
the selected window. `--positions middle --orientation landscape` selects a
smaller run. `--baseline-results /private/prior-results/report.json` additionally
requires exact decoded source pixel hashes at 0, 2 and 4 seconds for every
matching case; missing cases are an error. Without that option, hashes are
recorded for comparison, not declared a pixel-parity pass.

Real recordings retain the three-second per-edit limit, canonical
geometry/caption/motion checks, actual source-clock seeks, expected frame count,
no-stall/no-drop playback and 50 ms end tolerance. Running audio output is
checked, but speech is not a flash/beep fixture: physical lip-sync still needs
human review. Real-media results do not replace the synthetic cue/phase gates.

The output directory must be new. The runner generates its own synthetic
media (no third-party footage or artwork) and owns/cleans up its server, browser profile and subprocesses.
It retains JSON reports, logs, model-edit inputs and screenshots. Runtime errors
and failed checks produce a nonzero exit; budgets are not relaxed. Chrome
startup and native-codec availability can vary by host; record that host.

For each of landscape 1920×1080/30 and portrait 720×1280/30 it checks:

- Cold server startup and fresh-profile navigation separately; navigation starts
  after the server has opened and can overlap its initial compilation.
- Twenty actual `composition model apply` edits, including CLI startup,
  document write, detection, canonical compilation, source access and a fresh
  visible version. Both visible latency and command-plus-visible latency are
  retained. The latter must be below 3,000 ms for every sample; p95 uses the
  nearest-rank value from 20 samples. This is local latency, excluding user Play
  delay and browser-process launch. Assets have already been ingested.
- Canonical boxes, motion, opacity, caption wrapping/glyph rectangles and source
  barcode at 0, 2 and 4 seconds, including a one-to-two-line caption boundary.
- Actual CLI undo and restoration, each producing a fresh request identity.
- Trusted Play, all 150 expected source frames, five flash/beep cues, no stalls
  or dropped frames, correct source offset and the approximate stop boundary.
- Play-to-motion below 3,000 ms and estimated audio/display phase within 50 ms.

The diagnostic `?audit=1` adds per-frame source canvas readback and frame/cue
metrics. Normal viewing avoids that readback. Both modes use the same native
video and passthrough AudioWorklet audio path. The worklet measures the audio
actually routed to the destination; its onset times are mapped through
`getOutputTimestamp()` and compared with video `expectedDisplayTime`. Audio
startup waits for a usable output clock before starting the video. These browser
clock estimates do not prove physical screen/speaker synchronization. A human
flash/beep check remains required, and native video tests are separate from the
existing raster/export fidelity matrix.

The original experiment results are recorded below. A successful small
fixture establishes a direction, not general product acceptance or a
three-second guarantee for arbitrary projects.

## Source-buffer comparison — November 10, 2026

The long-source fixture still uses 121 scenes / one hour, a five-second selected
window and a 90-minute 20,958,144-byte synthetic H.264/AAC recording. Six cases
cover both orientations and beginning/middle/end source positions, twenty real
CLI edits each. All source-frame/caption/geometry/motion, undo/restoration,
flash/beep phase, playback-frame and end-tolerance checks passed. Seven of 120
edit samples missed the unchanged 3,000 ms gate on the shared Mac host; this
series is **not** an overall performance acceptance pass. Per-case p95 ranged
from 1,799 to 3,279 ms; maximum was 3,642 ms. The retained
[`source-buffer-results.json`](source-buffer-results.json) includes the failures.

Source delivery improved independently of that remaining latency gap:

| Middle-source case | Direct stage: completed body bytes | Buffered stage: completed body bytes |
| --- | ---: | ---: |
| Landscape | 412,371,904 | 11,010,048 |
| Portrait | 408,158,208 | 11,010,048 |

Those totals include parity seeks, twenty edits, undo/restoration and Play. Each
buffered edit series reused the already-read source bodies. Buffer occupancy
remained between 6.8 and 11.1 MB across the six cases, below the 64 MiB cap.
These are socket-write diagnostics, not exact consumed/wire traffic.

This is a **delivery-only ablation**: the baseline serves the native stage from
pre-buffer #151 (`56d0092`) using the current server/counters and optimized
watcher. It therefore isolates direct/preload-auto source delivery, not the
entire earlier package. Both runs use the same generated source; synthetic
cue/fidelity checks remain unchanged. The baseline's latency series happened to
pass while some buffered cases missed under different shared-host load: these
runs do not establish a causal latency improvement or regression. Do not compare
their server totals with earlier DevTools-only totals.

Late edit cost remains under investigation in
[#154](https://github.com/jdilla1277/moviestar/issues/154), including repeated
undo-history validation and document copies. Real-media evaluations stay private
and are not substituted for the public synthetic evidence here. No persistent
rendered-scene cache or export change is part of source-byte buffering.

## Reference result — November 8, 2026

Apple M4 / 24 GiB, macOS 15.6, Chrome for Testing 156.0.8078.73,
Python 3.12.12, Node 24.5.0. The shared host was also running regression tests;
load averages at the start were 2.82 / 2.74 / 2.50. This is one 20-edit series per
orientation, not an isolated-hardware or cross-platform claim. Source footage is
8 seconds of 640×360/30 H.264/AAC, displayed inside the declared logical canvas.
It is neither a 1080p camera-source throughput test nor a large-file benchmark.

| Measurement | Landscape | Portrait |
| --- | ---: | ---: |
| Warm CLI edit → command complete and fresh visible preview, p95 / 20 | 379 ms | 366 ms |
| Warm maximum / 20 | 387 ms | 376 ms |
| Fresh-profile page navigation → visible preview, one sample | 360 ms | 260 ms |

All fixed checks passed. Geometry/opacity matched canonical inspection at each
sample; maximum glyph-bound difference was below 0.001 px (rounding). Captions
changed from one line to two at the expected boundary. CLI undo, restoration,
150 expected source frames and five audio/flash cues passed. Browser-estimated
cue phase and end-boundary errors stayed below their 50 ms budgets. Browser
process launch, server startup, cold page navigation, and user Play are separate
measurements, not hidden within the warm edit statistic. The JSON report retains
all per-edit measurements and playback timestamps.

Earlier development runs are not acceptance passes: the first benchmark driver
used an unavailable CLI option, and later runs exposed a first-cue audio startup
problem and a fixture missing canonical slot identity. Waiting for the audio
output clock and creating the fixture's slot identity fixed those failures;
the fixed-budget series above was rerun from a new fixture. A missing first cue
must not be discarded to improve a sync statistic.

The player/compiler now live in the package and are used by the actual native
preview command. The old `serve.py` command is a compatibility launcher for it.
Next: broaden supported timelines/media and verify real footage and larger
projects. Full export remains separate. Repeated source probing/decoder setup
in the existing render pipeline is tracked as a lower-priority follow-up in
[issue #142](https://github.com/jdilla1277/moviestar/issues/142).

A second full series after cleanup changes also passed: p95 377 ms landscape and
372 ms portrait, with maxima 422/375 ms and cold navigation 291/260 ms. Both
20-edit series per orientation are retained in
[`reference-results.json`](reference-results.json), including per-edit timings,
parity checks and clock estimates. Earlier failed development runs remain
explicitly described above; neither successful series substitutes for general
project acceptance.

## Installed command result — November 9, 2026

**Historical limitation discovered in review:** the three series below used
hand-authored `has_audio` metadata, which the real loader does not produce.
They measured the prepared-player path but missed a product-path rejection.
They do not establish load-to-preview integration. The corrected loader-indexed
runs below supersede them for that acceptance; the original data is retained
and labeled in `command-results.json`.

The runner now starts `moviestar preview --mode native` itself. Three 20-edit
series per orientation passed all unchanged checks (120 edits total):

| Runtime | Landscape warm p95 | Portrait warm p95 |
| --- | ---: | ---: |
| Source checkout | 726 ms | 707 ms |
| Modules from an extracted wheel | 624 ms | 764 ms |
| Wheel installed in an isolated environment | 1,163 ms | 918 ms |

The installed-wheel run used its installed Python module; the console entry
was also checked separately,
with existing third-party dependencies shared read-only; it did not import the
package from the source checkout. All 120 edits met the 3,000 ms budget. A full
regression suite was running on the shared host, so these series are not a
controlled comparison between installation methods. Cold navigation, server
startup, Play-to-motion, all per-edit timings, geometry/caption parity, cue phase
and window-end estimates are retained in
[`command-results.json`](command-results.json). Browser process startup and user
Play delay remain separate. Larger sources, general timelines and native/export
pixel fidelity remain follow-up acceptance work.

## Corrected load-to-preview validation

The fixture now loads its generated MP4 through the real CLI with
`load --as camera --force --no-transcribe --no-frames`, retains that indexed
project metadata and saves the authored spatial scene. It supplies no invented
`has_audio` field. Ingestion stays outside the repeated-edit timer. Native
preview uses indexed `audio_codec` and validates the canonical resolved audio
source, contiguous slice, speed and clock. The benchmark scene uses the
inferred single-audio-slot route; CLI integration tests also exercise explicit
and composition-wide routes, including failure/recovery after an unsupported
edit. Unit checks reject absent/different audio, discontinuous or short slices,
and mismatched source clocks.

| Loader-indexed runtime | Landscape warm p95 | Portrait warm p95 |
| --- | ---: | ---: |
| Source checkout | 706 ms | 624 ms |
| Isolated installed wheel | 916 ms | 739 ms |

All unchanged benchmark checks pass: 80 real edits, each below 3,000 ms, with
canonical caption/layout/source-frame parity, five flash/beep cues and no
reported stalls or dropped frames. These corrected series and indexed codec/rate
fields are retained in `command-results.json`. The shared-host and small-fixture
limitations still apply. The dedicated viewer UI pass is tracked separately in
[issue #149](https://github.com/jdilla1277/moviestar/issues/149).

## Selected windows in larger projects

The native command can review an interior section without requiring the whole
project to fit its single-video profile. Range eligibility uses the canonical
overlapping span and half-open transition windows, including a transition in
the unsampled tail after the last output frame. Result, scene, span and source
origins remain distinct. An ordinary span after a hold can be selected without
resetting scene-relative motion. Unplaced live caption tracks and edits on
unrelated sources do not block review; applicable global audio settings still do.

Run the extended benchmark with the same fixed gates:

```sh
python experiments/live_browser_preview/benchmark.py --profile long-source --out /tmp/native-long-new
```

It creates a 90-minute 640×360/30 H.264/AAC source and six hour-long, 121-scene
projects, each with reusable component definitions and scene-owned spatial
layouts. The selected scene begins at result 1800; review is **1801–1806**, with
source ranges **1–6**, **2705–2710**, and **5385–5390** in both orientations.
The source video repeats the eight-second barcode sequence by stream copy;
audio is generated continuously to avoid accumulating AAC priming at joins.
The barcode is modulo 240 frames, so absolute media times are checked as well.
Ingestion uses the real CLI. Setup/source generation is outside edit timing.

This is a deliberately low-bitrate synthetic duration/scene-count test, not a
gigabyte camera-file, VFR, HDR or remote-delivery acceptance test. The encoded
source is about 21 MB. Each case uses a fresh Chrome profile/server, but source
generation, indexing and canonical inspection warm the OS file cache; “cold
navigation” does not mean cold disk. Relative seeks within the five-second
window are reported separately from navigation to an initial deep-source seek.

The editable-install series passed all 120 edits: warm p95 **2408–2481 ms**,
maximum **2596 ms**, against the unchanged 3000 ms gate. All frame/caption/layout,
five flash/beep, undo/restoration, zero-stall/drop and timing checks passed.
Fresh-profile navigation took 600–827 ms; nontrivial in-window seeks took
24–35 ms. These remain one series per case on a shared M4 host, not a universal
three-second guarantee. Per-edit results and further installed-wheel runs are
retained in [`window-results.json`](window-results.json).

An independent environment installed the built wheel (no source-package imports)
and repeated the full matrix without the competing regression suite:

| Source position | Landscape warm p95 | Portrait warm p95 |
| --- | ---: | ---: |
| Beginning | 2497 ms | 2509 ms |
| Middle | 2525 ms | 2521 ms |
| End | 2518 ms | 2583 ms |

All 120 edits and unchanged correctness/playback gates passed; maximum edit
latency was **2647 ms**. The compiler file in the installed wheel matched the
checkout. This remains a shared host, not isolated hardware.

The earlier wheel series ran alongside the full regression suite and **failed**:
six edits exceeded 3000 ms (one at 3005 ms in landscape/beginning and five at the
end of portrait/end). That last case reached p95 3709 ms / maximum 3919 ms;
all its fidelity/playback checks passed. Keep this failed series rather than
presenting the later successful run as a guarantee under arbitrary load. No
budgets, sample counts, source sizes, scene counts or quality were reduced for
the rerun. All three series are retained in the results file.

The report also separates initial snapshot/timeline resolution from full
descriptor compilation (the latter includes its own resolution), and records
edit detection, worker generation and generation-to-stage readiness. These
nested timings must not be added twice. A 0.5-second sampler measures combined
RSS for the runner, service/worker, Node driver and owned Chrome processes, plus
the output tree's disk high-water mark. It is a sampled aggregate RSS sum, not
unique physical memory or an exact high-water measurement. Source HTTP byte
counts come from CDP `Network.dataReceived`, excluding headers and unreported
cache traffic, across the entire case including edits, seeks and playback.

The editable series peaked at roughly **1.2 GiB** combined RSS. Chrome read
roughly **160–249 MB** of source response bodies per case despite only reviewing
five seconds: native `preload=auto` can read ahead and revisions create new
players. This is a scaling limitation to investigate with real large files,
not evidence that delivery costs stay bounded independently of source size.
This source-delivery follow-up is tracked in
[issue #150](https://github.com/jdilla1277/moviestar/issues/150).
The selected-window change adds no proxy, persistent scene cache, new rendering
backend or viewer redesign. General native/export pixel/color parity remains
separate; the regression suite also checks a nonzero-result window against
actual watch/export output and routed audio.
