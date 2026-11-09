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

- One spatial scene and one timeline span, with exactly one visible video and
  its embedded source audio on the same unit-speed clock.
- A frame-aligned window of at most ten seconds, at 24–60 fps, matching the indexed source rate (the installed profile excludes
  low rates whose early last-frame stop would exceed the reference timing budget).
- Native-compatible MP4; the portable benchmark uses H.264/AAC at 30 fps.
- Reusable nested components, shapes, text, images, manual/frozen captions,
  static canonical CSS layout, and canonical motion/opacity samples.
- Relative/absolute media wrappers without padding, border widths or children.

Source edit operations, cuts, transitions, multiple video clocks, live caption derivation, external
soundtracks, gain/fades/mixing, holds and speed changes are not implemented here.
Other codecs, VFR, HDR/color-management parity, long/gigabyte sources, network
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
