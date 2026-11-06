# Spatial preview generation: compositor experiment

This is a reproducible experiment against the local preview worker. The product
viewer is shipped by `moviestar preview`; none of these compositor candidates is
enabled in the installed command. This experiment selects the next production
work without changing preview/export behavior or adding a public profiling API.

## What the experiment isolates

The existing renderer launches FFmpeg once per video plane to scale/place it,
then again to combine the Chrome-painted base and visibility masks. All these
operations happen again for every output frame.

Compare four implementations using exactly the same decoded media and Chrome
captures:

1. **Baseline:** the installed `_compose` implementation.
2. **Fused FFmpeg:** the same operations in one FFmpeg filter graph/process.
3. **In-process filters:** equivalent single-threaded filter graphs through PyAV.
4. **Vectorized masks:** PyAV scaling/placement and NumPy operations for the
   existing 16-bit, per-channel visibility-mask arithmetic.

Each candidate retains the 8-bit media-placement boundary and 16-bit mask math.
The maximum multiplication, `65535 * 65535`, fits in `uint32`; subtraction uses
signed intermediates and accumulation saturates before conversion. PNG output
encoding uses one thread, matching the baseline. Candidates are experimental
functions here, not production flags, caches, or alternate project models.

## Reproduce

Use the normal source-checkout environment, including local rendering
dependencies, Node.js, FFmpeg/ffprobe, and a pinned Chrome/headless-shell binary.
The array prototype uses NumPy already installed by this branch's local editing
dependencies. PyAV and NumPy redistribution terms are covered by the repository's
[dependency license policy](../../app/tests/dependency-license-policy.json);
run `bin/check-dependency-licenses` in the environment before distributing it.
Production promotion must declare direct dependencies explicitly, including
their placement in any lightweight-install/local-render dependency split.

From the repository root, with its Python environment active:

```bash
export PYTHONPATH="$PWD/app/src"
export MOVIESTAR_CHROME_PATH=/absolute/path/to/chrome-headless-shell

python experiments/spatial_preview_performance/benchmark.py \
  --out /tmp/spatial-compositor-run --runs 20

python experiments/spatial_preview_performance/profile_preview.py \
  --project /tmp/spatial-compositor-run --out /tmp/preview-baseline \
  --variant baseline

python experiments/spatial_preview_performance/profile_preview.py \
  --project /tmp/spatial-compositor-run --out /tmp/preview-vectorized \
  --variant vectorized
```

Output directories must be new. The scripts generate synthetic media, saved
project state, captures, and local reports; they never upload anything. Keep
generated state, logs, and media out of Git. The five-second profiling path is
read-only against the chosen project and verifies its spec hash afterward.

`benchmark.py` captures a fresh canonical frame at 1.25 seconds for landscape,
portrait, overlapping video planes, and a moving-video control. It checks every
candidate against the reference's decoded RGB hash on every run. A pixel mismatch
fails acceptance and exits nonzero after preserving the report. Execution order
is randomized within each repetition using a fixed seed. Reports retain all
samples, per-stage p95 only when there are at least 20 samples, paired median
speedups, FFmpeg launch counts, app revision, experiment hashes, library versions,
and host load. First-call import costs are included rather than silently removed.

`profile_preview.py` invokes the real completed-preview worker with an explicitly
selected experimental compositor. It records interpreter/import-inclusive
generation time, nested stage timings, process/source-decoder counts, browser
PNG captures, and PNG byte sums observed around composition. It
checks that review output is 1280×720, 150 frames at 30 fps, with five seconds of
audio. It does not time an agent editing command, inspection, publication, player
startup, or human reaction. Interpreter/import startup is outside the child
stage timer but inside the parent generation timer. Inclusive stage timings must
not be added together; decoder/PTS work is nested within asset preparation.

## Observations, November 6, 2026

App source: `383e51e9f39a1633cb3e07e7a4b71fa216613974`. Apple M4, macOS 15.6,
Python 3.12.12, PyAV 18.1.0, NumPy 2.5.4, FFmpeg 9.0.1, headless Chrome
156.0.8078.73. Exact Node/library versions and load averages are saved by the
runner. The synthetic source is 640×360 H.264, 30 fps, with audio; the authored
landscape canvas is 1920×1080. The scene uses host-default serif text. Candidate
and reference share those captured text pixels; this is not a cross-host font
reproducibility claim.

Twenty randomized repetitions per case, composition stage only:

| Case | Baseline median | Fused FFmpeg | In-process filters | Vectorized masks | Paired vectorized speedup |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1920×1080, one video plane | 471.5 ms | 370.5 ms | 321.2 ms | 104.8 ms | 4.45× |
| 720×1280, one video plane | 258.2 ms | 187.8 ms | 140.7 ms | 49.7 ms | 5.19× |
| 1920×1080, two overlapping planes | 810.9 ms | 626.9 ms | 570.1 ms | 160.6 ms | 5.06× |

All compared RGB pixels matched exactly, including cropped/rounded geometry,
opacity and overlapping text/video. The moving-video control has no compositor
planes: all four methods copy the already-painted browser frame and match. Its
sub-millisecond timing does not establish a useful speedup.

One instrumented five-second worker run per variant, same project state and
review profile:

| Measurement | Baseline | Vectorized experiment |
| --- | ---: | ---: |
| Complete local preview generation | 110.64 s | 60.30 s |
| Composition | 68.75 s | 16.09 s |
| Asset preparation, including decode/PTS | 19.51 s | 20.55 s |
| Browser requests, including initial startup | 19.40 s | 19.31 s |
| FFmpeg process launches | 454 | 154 |
| ffprobe launches | 153 | 153 |
| Source decoder opens | 150 | 150 |

The full review clips contain identical decoded video and audio samples:
video SHA-256 `b7089e1e3e18034a67c44f8f54e2057a8dc2bf50f4727c910bc76e65303a4782`,
audio SHA-256 `599d77fcf4890e98c03d26a28d0cd2f76f64da2f023c7448056e5d18cba2d7b2`.
These are FFmpeg decoded-stream hashes, not compressed-file hashes. Recheck with
`ffmpeg -v error -i FILE -map 0:v:0 -f hash -hash sha256 -` and separately
`-map 0:a:0`. Both outputs retain the same version, review raster, frame count,
frame rate and audio duration. The array path also opens 450 PNG inputs through
PyAV; it does not reduce the 150 source-media opens.

A supplemental run with capture/file counters recorded 150 browser requests and
300 PNG captures for both variants. Summed PNG sizes observed before composition
were 14,754,108 bytes for both; after composition they were 69,903,033 bytes for
the baseline and 42,802,474 bytes for the vectorized candidate. These sums are
file observations, not total physical I/O or peak disk. Supplemental timings
were excluded from the comparison because host load was much higher.

## Decision and remaining work

Vectorized composition is the strongest candidate for a production parity and
memory-validation slice. Fusing the same expression graphs saves process startup
but retains most of the per-pixel expression cost. Reusing in-process filter
graphs improves it further, while array arithmetic yields the larger stage gain.

The measured whole-worker improvement is **1.83×**, not 4–5×. Even eliminating
baseline composition entirely leaves about **41.9 seconds** in this sample.
The three-second interactive-preview target therefore requires changes to the
remaining pipeline and evaluation of playback readiness/production rate, beyond
this compositor substitution. Preview-specific progressive or browser playback
can continue using the product viewer; this experiment does not select or claim
those capabilities.

Next production work must validate the candidate on the full spatial fidelity
matrix, Linux/browser/library versions, combined peak memory, cancellation and
failure cleanup. Then address request-owned probing/decoder reuse and repeated
browser work, measuring the complete edit-to-human-player path. A progressive
path must also produce frames quickly enough to sustain playback.

The stage trials use fixed predecoded inputs, not a rendered-clip cache. The
whole-worker profiles still use the current source preparation. No unchanged
scene reuse or persistent cache invalidation is introduced. The full profiles
are single observations on a shared host, not edit-to-preview p95, an export
throughput result, a large-source/project test, or a performance acceptance pass.
Combined child RSS, peak disk and real-footage reproduction remain unmeasured.
