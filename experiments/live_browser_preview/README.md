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

- Up to three scene spans joined by frame-aligned hard cuts, each with one or
  two visible videos and one selected embedded source-audio route. Both pipelines
  share an audio output clock; a listener gain of zero makes it inaudible. Static
  `single`, `two-up` and `picture-in-picture` presets use the export planner. The
  project can have other spatial
  or preset scenes, and pacing/transition behavior outside the review window.
- A frame-aligned window of at most ten seconds, at 24–60 fps, matching the indexed source rate (the installed profile excludes
  low rates whose early last-frame stop would exceed the reference timing budget).
- Native-compatible MP4; the portable benchmark uses H.264/AAC at 30 fps.
- Reusable nested components, shapes, text, images, placed caption tracks,
  static canonical CSS layout, and canonical motion/opacity samples.
- Relative/absolute media wrappers without padding, border widths or children.

Source edit operations on the chosen video, ranges crossing in-scene pacing boundaries
or overlapping transitions, more than two visible videos per scene, external
soundtracks, gain/fades/mixing, holds and speed changes are not implemented here.
Other codecs, VFR, HDR/color-management parity, network
sources and cloud/phone delivery remain unverified. Private camera evaluation
does not establish acceptance for arbitrary recordings. Unsupported browser codecs
fail visibly. Backend descriptors are bounded by the requested duration/frame
rate, not a general-purpose incremental scene compiler.

Native clipping of this test window is approximate: it stops at the last expected
source frame, within 50 ms in the reference check. This is **not** sample-accurate
audio trimming or a substitute for export timing. Media fragments alone proved
too coarse at the end, so a video-frame callback schedules the pause. A production
player needs broader timing and seek tests before it can own general timelines.

## Measure

For authored preset captions and spoken-word highlighting, use:

```sh
python experiments/live_browser_preview/caption_benchmark.py --out /tmp/caption-results
python experiments/live_browser_preview/caption_benchmark.py --canvas 1080x1920 --out /tmp/caption-hd-results
```

Each run creates generated H.264/AAC PiP media and portable word-timed transcripts,
then times actual caption suppression, resizing, placement, highlight-color and
four individual undo commands at histories 0/20/50. A measurement ends only after
command success and the correct fresh, decoded caption image is visibly placed
at the review time. Every edit must stay below 3,000 ms; actual playback must
have zero decoder drops/stalls and audio/display phase within 50 ms. Word-plane
selection and application lateness are also checked on presented frames. The
driver retains all attempts, exact inputs, process outputs, producer/package
hashes and runtime versions. Reusing an output folder is forbidden.
Each caption-only edit must also preserve the existing iframe/decoders. Separate
browser checks hold an obsolete preparation while a newer edit completes,
verify that it never publishes, play again after editing a finished preview, and
require geometry changes to rebuild the player. Source fingerprint changes
invalidate decoder reuse too.

Caption glyphs reuse export's drawtext/ASS painting over synthetic mattes;
source footage is not decoded and no preview movie is encoded. Transparent
planes stream through one matte-pair buffer, are cropped, preloaded,
request-scoped and pruned with history. Unit
checks compare their composition with independent opaque export-filter output
(maximum channel error 5), including outlines, opacity and plain background
boxes. A clipped-word regression compares the transient overlapping ASS paint
stack and later frames after it ends; raster deduplication keeps the complete
ordered active-event stack even when the final active word is unchanged.
Preset highlighted captions and ordinary spatial live-caption text have
separate canonical placement paths; this does not add highlighting to spatial
caption elements. See [the installed profile](../../docs/local-preview.md).

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

## Native hard-cut sequences

```sh
python experiments/live_browser_preview/cuts.py --out /tmp/native-cut-project
moviestar preview --workspace /tmp/native-cut-project --mode native --from 0 --to 9
python experiments/live_browser_preview/cut_benchmark.py --out /tmp/native-cut-results
python experiments/live_browser_preview/cut_benchmark.py --profile long-source --out /tmp/native-long-cut-results
```

The first fixture is a nine-second A → B → A sequence, three seconds per scene,
with frozen captions, reusable components and scene layouts. The second source
starts at source time zero; A returns at a different offset. The generated left
audio channel contains clocked flash/beep cues and the right channel a continuous
pilot. Both are original generated test data. No stitched preview MP4 is made.

The player prepares up to three scene players, shares one output audio context,
and switches gains only once the incoming media pipeline is running. Each
outgoing last valid frame covers the handoff; native video/audio continue to use
the source, with a 50 ms timing budget rather than export-grade sample trimming.
Single-span playback and explicit rendered-mode fallback remain available.

The cut benchmark retains twenty actual CLI edits, undo/restoration, canonical
geometry/glyph/opacity checks, source frame IDs and trusted Play. It additionally
samples immediately before/at/after each cut, checks every expected frame, cues
measured after the final audio gain switch, cut timing and total program-clock
drift within 50 ms. The continuous-pilot fixture rejects a silent gap longer than
one actual output audio quantum. The long-source profile uses 123-scene hour-long
projects, beginning/middle/end positions in the 90-minute synthetic A source and
a short B source; it retains the existing cue/phase checks but has no continuous
pilot across its mono A segments. It does not claim a pilot-gap pass.

Private-media example (keep the entire output directory private):

```sh
python experiments/live_browser_preview/cut_benchmark.py \
  --source /private/camera-a.mp4 --source /private/camera-b.mp4 \
  --offsets 120 300 600 --srt /private/captions.srt \
  --out /private/new-cut-results
```

Supplied recordings are referenced in place and must fit the native profile.
Each selected three-second excerpt must contain the intended picture/audio;
a quiet listening track is unsuitable for a human speech/lip-sync check.
Captions are clipped on each source clock and placed on the selected scene’s
result clock. SRT input must already match each selected recording's clock and
speaker. Clipping a cue's time range preserves its whole text; it does not align
words, shorten a sentence to the excerpt, or separate speakers from a combined
transcript. For physical caption review, prepare accurate short cues for the
selected speech. Canonical caption parity checks verify the authored cues, not
their transcription accuracy. Real speech is not a synthetic cue or pilot: these cases check
running output, source seeks, canonical visuals, frame counts and timing while
physical per-cut listening/viewing remains required. Hashes record selected
native-source pixels; a before/after pixel-parity claim needs an explicit matching
baseline. The private-media runner creates a 123-scene hour-long project and
keeps the same three-second edit gate. All failures remain in its report.

Crossfades, in-scene pacing changes, simultaneous visible videos, independent
continuous soundtracks, mixing/gain/fades and holds/speed changes remain outside
this initial hard-cut profile. Do not silently fall back to a render or relax
budgets to accept a sequence.

### Installed hard-cut results — November 10, 2026

A fresh installed wheel passed all 160 synthetic cut edits below, plus 40
single-span regression edits. Each case has twenty real CLI edits and retains
the 3,000 ms gate for **every** sample. The measured product module bytes match
[measurement commit `b7f956e`](https://github.com/jdilla1277/moviestar/commit/b7f956e76c92f7b5680162b7a75375627782ccae);
hashes, individual latencies, cold readiness, diagnostics and checks
are retained in [`cut-results.json`](cut-results.json).

| Case | Landscape p95 / maximum | Portrait p95 / maximum |
| --- | ---: | ---: |
| Nine-second A → B → A reference | 692 / 721 ms | 710 / 722 ms |
| Hour project, beginning of 90-minute A | 1,874 / 1,915 ms | 1,794 / 1,905 ms |
| Hour project, middle of 90-minute A | 1,869 / 1,888 ms | 1,867 / 1,892 ms |
| Hour project, end of 90-minute A | 1,885 / 2,104 ms | 1,871 / 1,904 ms |
| Existing five-second single-span reference | 619 / 624 ms | 642 / 644 ms |

Every cut case showed all 270 expected source frames, including the three
prepared initial frames, without an out-of-range outgoing frame. Nine output
audio cues matched nine flashes; maximum browser-estimated phase error was
33.11 ms. Cut timing, complete program-clock drift and end time stayed within
50 ms. No stalls, dropped frames or uncaught browser exceptions were recorded.
The stereo reference had no silent pilot gap; the long-source fixture has no
continuous pilot and makes no gap claim. Geometry/opacity matched canonical
inspection; maximum glyph-bound error was 0.0005 px.

Fresh-profile navigation to cut readiness was 427–948 ms after server startup;
Play-to-first-motion was 303–442 ms. Peak sampled combined process-family RSS
was 1,485.6 MiB for the long-source cases, including the runner, CLI/server,
workers, driver and owned browser. This is separate from the 64 MiB source buffer.
No additional source bodies were written between edits 1 and 20 in these cases.

Host: Apple M4 / 24 GiB, macOS 15.6; Python 3.12.12, Node 22.17.1,
Chrome for Testing 156.0.8078.73, FFmpeg 9.0.1. Runs were sequential, with no
competing benchmark or regression suite launched by this agent; the host remains
shared. This is one local series per case, not cross-platform acceptance or a
full-project playback/export claim.

Development failures are retained locally: fractional-frame seeks selected a
previous frame, decoded outgoing tails leaked past cuts, source-EOF handoffs
left audio gaps (including future gain padding caught by full preflight), and an
uncaught initial-Play timer referenced an undefined index. Regression tests now cover those failures, and the benchmark rejects
uncaught exceptions. A center-channel dialogue case also caught an incorrect
surround-to-stereo mix; the shared output now uses the browser's speaker downmix.
Incoming gain switches as soon as its media pipeline runs, without extra future
padding that could outlive the outgoing file. Five additional EOF browser runs
passed without relaxing the one-audio-quantum silence gate.
Earlier runs before these corrections are not acceptance for the final build.
Fixture integral offsets were normalized to work around the numeric state-ID
failure recorded in [#157](https://github.com/jdilla1277/moviestar/issues/157).
That convention remains for comparisons with archived runs. Production identity
now handles equivalent numbers directly, with separate unnormalized float/JSON
round-trip regressions. The earlier failed restoration report remains a failed
run; this fix does not turn it into passing benchmark evidence.

A subsequent review found that Play during a pending cross-cut seek could start
the old scene. Play now waits for the seek destination; Pause/disposal cancel
queued playback, and newer seeks fence older results. Six deterministic control
cases and the real-browser cut/EOF/surround matrix cover this race, including
actual playback at the requested destination after audio activation. The full
timing matrix above was not rerun for this control fix: its module hashes and
measurements identify `b7f956e`, rather than the later player revision.

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

## Undo-history depth comparison

`history_benchmark.py` repeats the unchanged real-CLI/browser gates with fresh
owned fixtures starting at **0, 20 and 50** undo entries. Twenty timed edits
finish at 20, 40 and 50 entries respectively; the last case exercises FIFO
rollover on every edit. Source media and the current document are held constant
across starting depths. The setup uses real revision diffs, validates the seeded
chain and returns to the same current state before timing. It resets only a new
fixture's setup journal, never a supplied project or the timed edit history.

```sh
python experiments/live_browser_preview/history_benchmark.py \
  --profile long-source --revision TESTED_COMMIT --out /tmp/history-depth-new
python experiments/live_browser_preview/history_benchmark.py \
  --profile cuts --revision TESTED_COMMIT --out /tmp/history-cuts-new
```

The long-source profile covers beginning/middle/end of a 90-minute synthetic
recording in hour-long 121-scene projects, in both orientations. The cut profile
uses the generated nine-second A → B → A reference, retaining source-frame,
caption/layout, output-cue and cut-clock checks. The package regression suite
also covers pending-seek Play/Pause/disposal, source-EOF audio and surround
center-channel dialogue; passing edit latency alone does not replace those tests.

For a before/after comparison, run this **same harness** with each version's
separately installed Python environment and identical dependencies/browser.
Supply the first run's generated `ninety-minutes.mp4` through `--source` on the
second run so both reference the same file. Record the actual product revision
through `--revision`; the report also records the imported `spec.py` hash and
harness-file hashes. Keep failed samples and version/environment boundaries.
`--orientation landscape --positions middle` selects a smaller diagnostic run;
it is not the complete matrix. `--histories` selects explicit depths.

Each edit records `cli_completion_ms`, `journal_before`, `journal_after` and
`spec_bytes`, in addition to the existing command-plus-visible timing and source
counters. Reading those diagnostic spec sizes happens after the timed result.
Every sample still needs a fresh correct visible revision and command success
within **3,000 ms**; a p95 below the limit cannot hide a slow individual edit.
Cold navigation, canonical/source seeks, undo/restoration and actual Play keep
their separate fields and unchanged gates. Profiling must run separately from
these uninstrumented series.

Private-media checks reference footage in place and put paths, caption text,
pixels and logs in the output directory: **keep the entire directory private**.

```sh
python experiments/live_browser_preview/history_benchmark.py \
  --profile real --source /private/recording.mp4 --srt /private/subtitles.srt \
  --source-offset 2 --revision TESTED_COMMIT --out /private/history-real-new
```

The selected five-second window begins one second after `--source-offset`,
inside the hour-long project. Supplied SRT cues must already fit the recording's
clock and speaker; clipping time ranges does not align words or separate speakers.
Canonical caption parity is distinct from physical speech/caption/lip-sync review.

The first #154 implementation removes copies of journals that are immediately
discarded. It preserves caller/result isolation, all historical-state validation,
the 50-entry cap and `save_spec`'s fresh on-disk reread. It does not introduce
validation caching or change the single-writer atomic-replacement contract into
a transaction/conflict-detection guarantee for concurrent writers.

The retained [synthetic comparison](history-copy-results.json) measures baseline
`a09dba6` and product candidate `6b555a3` with the same locked harness and installed
wheels on an Apple M4 / 24 GiB shared Mac. It includes all 24 cases / 480 edits per
build, actual depths/spec sizes, provenance and failures. Long-source case p95s
at 50 entries ranged 3,926–4,040 ms before and 2,871–4,138 ms after; at 20 entries
they ranged 3,079–3,199 ms before and 2,298–2,888 ms after. These are ranges of
case-specific p95s, not pooled percentiles. Every synthetic non-latency gate
passed, including all hard-cut playback checks. Misses fell from 142/480 to
14/480; the candidate maximum was 4,487 ms. **The copy reduction does not meet
the complete per-edit target or close #154.** All samples remain in the report;
no profiler timing is used for acceptance.

### Diagnosing an edit timeout

New runs retain every command outcome independently of browser readiness.
For each edit, inspect these files in its case output directory:

- `edit-01.input.json`: the exact model submitted to the real editing command.
- `edit-01.command.json`, `.stdout.txt`, `.stderr.txt`: command completion,
  exit code/signal, timeout or spawn/JSON-output error, and complete raw output.
- `edit-01.trace.json`: file fingerprints before the command, at failure and
  after completion; observed file changes; viewer/server requests and frame
  readiness; the original failure and any later command outcome.
- `browser-report.json`: `commands` and `edit_attempts` include failed attempts,
  while `edits` still contains only successfully completed measurements. An
  incomplete case does not become a passing short series.

The readiness deadline remains **10 seconds**, the command lifetime limit
**15 seconds**, and the acceptance gate **under 3,000 ms for every edit**.
If readiness times out, the trace freezes that failure, then collects the
command's eventual result within its existing lifetime limit. A late successful
save cannot turn the original timeout into a pass. Signal interruption and
command timeout terminate the owned command process group on POSIX. Raw output
is finalized on pipe closure, including trailing bytes; forced incomplete
capture is explicitly marked. Undo and restore commands retain their own
`command-01.*` artifacts.

Use request IDs to distinguish an old displayed preview from a new request.
Requests preserve the server's `requested_at`, `started_at`, `finished_at`
timestamps (epoch seconds); frame `prepared_at` and visible-event `ready_at`
are epoch milliseconds. Poll observations are offsets in monotonic milliseconds
from edit start; command/completed-edit durations also use the monotonic clock,
so wall-clock adjustments cannot produce a passing negative duration.
Missing stages remain absent. File notifications and fallback
post-command snapshots are **observations**, not precise save-completion times;
snapshot reads carry start/end timestamps. Shared-host load is recorded at the
start/end of each attempt and does not establish a cause for a timeout.

On failure, `server_at_failure` independently fetches the server's state with a
one-second limit. Compare it with `preview_at_failure`, which is the state the
browser actually observed; either observation can fail and is then marked
unknown. A driver-level failure also retains prior samples and partial report
data instead of replacing them with an empty result. An abrupt kill may leave
incomplete artifacts; missing command/stage data must never be treated as success.

Baseline fingerprints precede the edit timer; artifact writes and post-command
fingerprints follow the completed-edit timing boundary. Snapshot read timestamps
retain that extra work separately. Diagnostic observation still adds work, so these reports are
not an overhead-free product profile. New reports fingerprint all experiment
producer scripts. The archived copy comparison above used the harness at
`6b555a3`; it is unchanged. Compare product builds with the **same** diagnostic
harness before attributing a timing difference to a product change.

**Keep real-input output directories entirely private.** Input models and raw
CLI output may contain source paths, captions or other customer content. No
upload is performed; do not attach these artifacts to a public issue or PR.

### Representative picture-in-picture edits

From the repository root, use a new output directory:

```bash
app/.venv/bin/python experiments/live_browser_preview/pip_benchmark.py --out /tmp/moviestar-pip-run-1
```

This prepares a small portrait two-camera preset composition, then measures
eight actual command operations at each initial undo depth 0/20/50: move and
resize the inset, reframe the main picture, swap the cameras and speaker audio,
and undo each edit. Setup/ingestion/history seeding stays outside edit timing.
Generated source pictures have a horizontal pattern so crop changes affect
visible content. A quieter second audio source checks the swapped output and
the actual listener audio gain must stay zero. Both video elements feed a
shared Web Audio clock; muting the element itself can select a different video
scheduling path and add startup delay at cuts. The software-headless reference
view is the authored 360×640 raster at 1×; this does not certify the viewer's
default upscaling/viewport behavior. Browser tests separately compare masks/borders with
export pixels and exercise reusable components and two-video hard cuts.

Readiness requires a fresh visible revision with **both** decoded source frames,
the expected asset identities/source offsets, measured boxes, crop anchors and
audio carrier, plus command success. The diagnostic observer's legacy
`expected_text_visible` field carries this semantic predicate in the PiP adapter;
there is no title-only shortcut. The 3,000 ms every-edit gate and 10/15-second
readiness/command deadlines are unchanged. A miss is retained and makes the
runner fail. Each case preserves the command arguments/input JSON, raw outputs,
traces, package source hashes, experiment hashes, runtime versions, observed
history counts and final content/FIFO-undo checks.

Optional `--sources FIRST.mp4 SECOND.mp4` references supplied recordings in
place, selects seconds 3–8 and keeps the entire output local. These inputs must
be compatible same-rate MP4s at least eight seconds long. This checks a small
composition referencing large files; it does not replace the existing
121-scene/full-history stress case or certify general native coverage. Raw
real-input artifacts must remain private. Physical speech/lip-sync review is a
separate owner check, not established by command success or decoder counters.

Reference run at `5c1ba92` (one host, Chrome for Testing 156.0.8078.73,
generated media, authored 360×640 at 1×):

This single-span timing reference predates explicit outgoing-frame preparation
for cuts. It does not measure two-video cut-edit preparation latency. Browser
regressions separately suppress final-frame callbacks and require smooth cut
completion, both held final frames and unchanged timing/quality limits.

| Initial undo entries | Completed operations | Maximum command-to-visible-ready | ≥3,000 ms misses |
| --- | --- | --- | --- |
| 0 | 8/8 | 813 ms | 0 |
| 20 | 8/8 | 744 ms | 0 |
| 50 | 8/8 | 1,330 ms | 0 |

All three cases restored current content through four individual undos,
including the expected FIFO cap behavior. Swapped-speaker playback had no stalls
or dropped decoder frames; maximum flash/beep phase error was 24.05 ms against
the existing 50 ms reference. This small single-scene sample does not establish
general long-project, cloud or full native-feature acceptance.

Frame callbacks can omit observations without a decoder presentation drop.
The generated cue check correlates each known cue instant with a presented-frame
clock within one frame, requires all five actual output audio cues, a usable
output clock and complete zero-drop decoder counters, and keeps the 50 ms limit.
Raw observed flash counts remain in the report. Negative controls reject missing
clock/sample/audio data, 51 ms audio drift and decoder drops. Separate cut tests
also require whole-program clock drift within 50 ms. Starting a muted follower
before the speaker had introduced real delay at each cut; both pipelines now
start together on the same audio clock with independent output gains.

Earlier producer `42fb920` used Chrome 157 and a different startup path; its
generated maxima were 811 / 849 / 725 ms. A separate earlier default/upscaled-view
run met latency but reported one dropped follower frame per playback. Keep those
failures: neither is a controlled speed comparison with this reference. The
ordinary viewport/upscaling behavior remains under
[viewer follow-up #149](https://github.com/jdilla1277/moviestar/issues/149);
the current 1× result does not certify every display condition. No deadline or
fidelity budget was widened to make this run pass.


### Headless playback cadence

The browser driver disables `OnBeginFrameThrottleVideo` in Chrome for Testing.
The pinned 156.0.8078.73 Linux build can leave a gap in video-compositor frame
requests while the audio output and decoding buffers continue normally. A
captured generated-fixture trace showed a roughly 67 ms gap, followed by two
reported video drops; the renderer's main thread was not blocked for that
interval. This is a browser scheduling investigation, not evidence that every
historical timeout or drop has the same cause.

The headless reference uses display-vsync frame requests instead of per-video
throttling. Source dimensions, encoded FPS, playback speed, wall-clock audio,
raw dropped-frame counters and all existing fidelity/per-edit budgets stay the
same. Reproduction with a separately launched headless browser needs the same
`--disable-features=OnBeginFrameThrottleVideo` flag; record browser version and
flags with benchmark results. Ordinary human browsers are separate physical
playback checks, and these reference results do not certify every browser/host.

The feature is present and enabled in the [pinned Chromium source](https://chromium.googlesource.com/chromium/src/+/refs/tags/156.0.8078.73/components/viz/common/features.cc).
Failed acceptance runs remain failed. On failure, CI can collect generated-only
browser traces in a separate diagnostic run; a diagnostic rerun never replaces
the original acceptance result.

### Cut-frame observations and compositor counts

The cut regression records two different quantities: pixel observations from
`requestVideoFrameCallback` and frames submitted for composition. The
[rVFC specification](https://wicg.github.io/video-rvfc/) permits omitted callbacks;
`presentedFrames` identifies those gaps. `presented_frames.mjs` anchors that
counter to the prepared first source frame immediately before Play, requires
each observed source index to match its counter delta, and requires the final
observed frame to establish all 90 submissions per three-second/30 fps span.
It retains exact barcode checks on every observed frame, zero decoder drops and
stalls, and the existing 50 ms timing limits. Reports separately count callbacks,
observed source frames and unobserved callbacks. A counter cannot establish the
pixel contents of an unobserved frame. Negative controls reject actual missing
or duplicate submissions, wrong pixels, bad baselines and a missing final frame.
The earlier Linux barcode mismatch remains tracked in #189; omitted callbacks
do not explain a mismatching pixel sample.

### Independent audio reference

```sh
python experiments/live_browser_preview/audio_benchmark.py --out /tmp/native-audio-new
python experiments/live_browser_preview/audio_benchmark.py --canvas 1080x1920 --out /tmp/native-audio-hd-new
```

These generated five-second, three-span PiP cases time existing `audio add`,
source gain/mute/unmute and individual undo commands at histories 0/20/50.
Gain/mute edits must keep their actual decoders; adding/removing a file dependency
intentionally rebuilds under the full input fingerprint. After timed restoration,
an untimed dialogue setup supplies audible playback. The established reference
uses authored raster/1x and canonical seek probes before Play; it is not a cold
startup claim. Every measured edit must finish below 3,000 ms, with zero playback
drops/stalls and audio/display plus program drift within 50 ms. The installed
results and exact source/producer/wheel hashes are in [audio-results.json](audio-results.json).

An edited mix prepares only bounded lossless stereo 48 kHz audio with export's
layer graph and limiter. Source pictures stay native. Six actual-browser mix
cases also compare optional `audit_pcm=1` final-output samples against the prepared
canonical mix, including muted tracks. Sample capture is opt-in diagnostic work,
not an additional production recording feature. Private audio/logs stay local.
Cold fresh-play startup drops also reproduce in the prior renderer and are
tracked separately in [#199](https://github.com/jdilla1277/moviestar/issues/199).


### Source and track edge fades

```sh
python experiments/live_browser_preview/audio_benchmark.py --scenario fades --out /tmp/native-fades-new
python experiments/live_browser_preview/audio_benchmark.py --scenario fades --canvas 1080x1920 --out /tmp/native-fades-hd-new
```

This scenario times actual source fade-in/out commands, independent-track
fade-in/out model edits and four individual undos at histories 0/20/50. It starts
with the track already placed and requires every fade edit to reuse the same
iframe and video decoders. It retains the same per-edit, playback and clock
budgets as the gain scenario. Final playback reapplies the faded mix after the
measured undo checks; that setup is untimed and identified separately.

The native descriptor preserves full project duration and track placement, so a
selected window in the middle of a fade does not restart its envelope. Export,
loudness measurement and bounded native audio use the same per-sample linear
fade filters. Separate analytic-waveform, full-export slice and actual-browser
output checks cover fade timing and amplitude, including seeking into a fade.
These are the existing layer-edge fades, not a new crossfade editing API.

The first installed-wheel matrix is recorded in
[fade-results.json](fade-results.json). It failed the unchanged latency gates:
16 completed edits exceeded 3,000 ms and one later case timed out before its
first edit became ready. All failures remain part of the evidence. This shared
host was under heavy load, but that observation alone does not isolate the
cause or establish a performance pass. Performance qualification and physical
playback review for this slice remain pending.

Sequential 1080×1920/history-50 gain controls on the same busy host retained a
3,842 ms miss in the previous installed renderer, while the new renderer's gain
control completed below budget. These diagnose a host-sensitive reference; they
do not prove that the fade path has no regression and do not replace its failed
matrix. The report includes both versions' source/producer hashes and observed
load/swap conditions. Do not rerun to select passing samples or widen budgets.


The first dedicated Linux CI reference passed both rasters and histories
0/20/50: **48/48 actual fade/undo edits**, maximum **1,225.735 ms**, all six
undo/playback cases within the existing zero-drop/stall and 50 ms clock limits.
[fade-ci-results.json](fade-ci-results.json) records verified installed-wheel
runtime hashes, producer identity and per-case measurements, linked to the
[run and generated artifacts](https://github.com/jdilla1277/moviestar/actions/runs/43415641459).
This is a four-CPU Linux/FFmpeg 8.0.1/Chrome 156 reference, not a causal explanation
of the retained Mac failures or a physical listening pass. Neither failed Mac
matrix was removed or relabeled. General/cold-start acceptance remains separate.


### Fade-duration rounding regression and fresh reference

Source/track fade durations accepted within the existing millisecond tolerance
remain renderable when a frame-aligned layer duration is not an exact number of
milliseconds. An overlong fade-out uses a nonnegative `afade` range and scales
its initial amplitude to preserve the intended `(duration - t) / fade` curve;
validation and saved intent are unchanged. Boundary tests exercise real FFmpeg
at 31/30 seconds with fades rounded below/above that duration, including fade-in,
a selected window, a full export slice and an independent analytic envelope.

The corrected build's complete fresh matrix is in
[fade-rounding-results.json](fade-rounding-results.json). All 48 edits completed
and all six playback/undo cases passed their quality/clock checks, but 13 edits
still missed 3,000 ms. This matrix remains failed. The earlier failed matrix is
retained separately; neither is replaced by selecting passing cases.

The `native-audio-performance` workflow runs the same generated fade scenario
on separate Linux runners at both authored rasters and all three history depths.
It installs a fresh wheel, verifies its source hashes, retains runtime/producer
identity and all generated evidence on success or failure, and fails on any
unchanged driver gate. There is no acceptance rerun or `continue-on-error`.
Runner results are a separate environment reference and do not prove the cause
of shared-Mac failures or physical playback acceptance.
### Two independently placed audio tracks

```sh
python experiments/live_browser_preview/audio_benchmark.py --scenario two-tracks --out /tmp/native-two-tracks-new
python experiments/live_browser_preview/audio_benchmark.py --scenario two-tracks --canvas 1080x1920 --out /tmp/native-two-tracks-hd-new
```

The scenario starts with dialogue, then times adding a second music layer,
changing its gain, muting/unmuting it and four individual undos at histories
0/20/50. The second layer has its own source offset, result-time placement and
edge fades. The generated right-channel tone is distinguishable from dialogue
and leaves the left-channel flash/beep oracle unchanged. Only adding/removing
the file dependency may rebuild decoders; the other edits must reuse them.
Final playback reapplies the two-track mix after the timed undo checks.

The native profile permits at most two audible independent tracks intersecting
the selected window, alongside routed source audio. Muted/out-of-window tracks
do not consume that audio-layer limit. More tracks, looping and ducking still
fail explicitly; the canonical model and export have not acquired new limits.
Independent analytic stereo sums and full-export slices check both placed
layers, and actual-browser output checks cover source audio on/off, a muted bed,
fades, seeking and camera cuts. Clip/window/clock/quality budgets are unchanged.
The fade results above measure the earlier one-independent-track build and do
not qualify the two-track build.


The [initial two-track matrix](two-track-initial-results.json) preserves all
48 completed edits (maximum 2,590 ms) and one failed playback case: at
360×640/history-20, both final-span videos dropped three frames. No edit missed
3,000 ms; playback quality still failed. That historical wheel predates the
parent's fade-rounding correction; its exact hashes remain attached, rather
than being presented as current-head qualification. The current CI workflow
runs both fade and two-track scenarios at both rasters and all three histories
with unchanged gates and complete generated artifacts. The rounding fix is not
claimed to repair the dropped frames. Physical two-track playback remains a
separate pending check.

### Two-track CI timing failure (110a50c)

The [current two-track CI failure](two-track-ci-failure-results.json) is distinct
from the historical pre-rounding matrix. All 48 edits completed below 3,000 ms
(maximum 1,581.294 ms), all six cases restored correctly, and playback reported
zero drops/stalls. Nevertheless, 1080×1920/history-50 failed the **unchanged
50 ms** audio/display limit: cues 2/3 measured −50.020882/−50.054215 ms. Full
correctness CI passed. This scenario remains unqualified.

In the failed trace, the audio-output mapping intercept varies by about
0.084 ms. Picture presentation offset steps by 16.734 ms at the first cut,
then another 16.634 ms at result time 1.233333 **within** the next span. Program
drift is 33.27 ms. The second step prevents attributing the entire failure to
cut handoff. Cues 2/3 have exact source-instant observations, so nearest-frame
interpolation at cue 1 does not explain their failure either. These are observed
clock relationships, not proof of a particular Chromium scheduling cause.

The driver now reports these independent clock offsets/steps. Audit-mode beep
records also retain raw `getOutputTimestamp()` pairs and sampling times for the
next investigation. This adds evidence without altering playback, source quality,
frame rate, phase calculations or acceptance gates. A passing rerun does not
explain or erase the retained failure. Further work must correlate raw mapping,
startup and in-span presentation scheduling before qualifying this profile;
physical two-track listening/lip-sync remains pending.

### Prepared-audio transport alignment (#215)

The prepared mix now owns cut boundaries through its audible output clock.
Incoming pictures start hidden half a source-frame before their absolute result
boundary, then become visible on that boundary. An early/late outgoing media
clock or end callback cannot accumulate timing error into the next scene.
Initial picture launch likewise reserves a half-frame for presentation latency,
rather than waiting until audio is audible before requesting picture startup.
Audio starts at its original sample/offset and remains continuous; video rates,
source quality and authored timing are unchanged. Legacy embedded-audio cuts
keep their prior path.

Deterministic regressions cover a late outgoing clock, early end callbacks,
hidden preroll, reveal on the master clock, Pause during handoff, rewinding a
canceled preroll before resuming, unavailable output clocks, and startup at
24/30/60 fps. Browser waveform, zero-drop/stall, frame, seek/restart/replacement
and 50 ms checks remain strict. Prior failed matrices are retained; a passing
rerun alone is not the explanation for this change.

Actual-browser sample capture also exposed a startup timestamp quirk: the
worklet reported the same frame twice, then recovered by jumping two quanta.
The captured PCM matched the reference byte-for-byte, but its recorded start
was one quantum early. Capture-only timestamps now use a monotonic rendered
sample cursor; raw stamps and adjustment sizes remain in audit blocks. No
samples are inserted/removed, and forward gaps are not filled. A regression
replays the captured repeated/recovered stamp sequence with distinct known
sample blocks. **Beep timestamps, phase calculations and the 50 ms gate are
unchanged**; this cannot turn the retained phase failure into a pass.

Picture-rate feedback and source-pipeline isolation were investigated and
rejected after browser quality checks failed. The final patch does neither;
those prototype patches and failures remain in the originating workspace.
Complete generated fade/two-track matrices and physical playback remain the
qualification steps for this candidate, rather than a general long-timeline
acceptance claim.


The first complete master-clock CI reference passed **96/96 edits**, all twelve
fade/two-track cases at both rasters and histories 0/20/50. Maximum edit latency
was **1,401.553 ms**, maximum absolute cue phase **21.593 ms**, and program drift
**33.167 ms**, with zero reported drops/stalls. Exact wheel/runtime/producer
identities and per-case evidence are in
[two-track-master-clock-results.json](two-track-master-clock-results.json), linked
to [the generated run/artifacts](https://github.com/jdilla1277/moviestar/actions/runs/43586302786).
This qualifies that bounded matrix; full correctness CI and physical listening
remain separate. The earlier failed references stay retained unchanged.
