# Spatial preview generation: compositor experiment

These tools compare the installed spatial compositor with a frozen FFmpeg
reference and earlier experiments, using the real local preview worker.
The installed renderer now uses in-process PyAV placement and row-block NumPy
mask arithmetic. The other candidates remain experiments; no new public
profiling command, alternate project model or render cache is introduced.

## What the experiment isolates

The frozen reference launches FFmpeg once per video plane to scale/place it,
then again to combine the Chrome-painted base and visibility masks. All these
operations happen again for every output frame.

Compare five implementations using exactly the same decoded media and Chrome
captures:

1. **Baseline:** `ffmpeg_reference`, copied unchanged from the pre-optimization
   `_compose` at `f01b858`. It stays fixed when the installed renderer changes.
2. **Production:** the installed `_compose`, with row-block integer arithmetic
   and opaque RGB placement after the same bilinear scaling.
3. **Fused FFmpeg:** the same operations in one FFmpeg filter graph/process.
4. **In-process filters:** equivalent single-threaded filter graphs through PyAV.
5. **Vectorized masks:** PyAV scaling/placement and NumPy operations for the
   existing 16-bit, per-channel visibility-mask arithmetic.

Each candidate retains the 8-bit media-placement boundary and 16-bit mask math.
The maximum multiplication, `65535 * 65535`, fits in `uint32`; subtraction uses
signed intermediates and accumulation saturates before conversion. PNG output
encoding uses one thread, matching the baseline. Candidates are experimental
functions here, not production flags, caches, or alternate project models.

## Reproduce

Use the normal source-checkout environment, including local rendering
dependencies, Node.js, FFmpeg/ffprobe, and a pinned Chrome/headless-shell binary.
NumPy is now an explicit direct package dependency (`numpy>=1.24.4,<3`), rather
than depending on its presence through transcription dependencies. PyAV and
NumPy redistribution terms are covered by the repository's
[dependency license policy](../../app/tests/dependency-license-policy.json);
run `bin/check-dependency-licenses` in the environment before distributing it.
A future lightweight-install/local-render dependency split must retain these
requirements in the local-render installation.

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
  --project /tmp/spatial-compositor-run --out /tmp/preview-production \
  --variant production

python experiments/spatial_preview_performance/resource_probe.py \
  --out /tmp/compositor-4k-resources --frames 20
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
PNG captures, and PNG byte sums observed around composition. The parent also
samples the owned process group's summed RSS every 100 ms and request-folder
disk usage every second. Shared pages may count twice, and sampling can miss
brief peaks; those maxima are observations rather than continuous resource
bounds. Parent/agent memory and other project directories are excluded. It
checks that review output is 1280×720, 150 frames at 30 fps, with five seconds of
audio. It does not time an agent editing command, inspection, publication, player
startup, or human reaction. Interpreter/import startup is outside the child
stage timer but inside the parent generation timer. Inclusive stage timings must
not be added together; decoder/PTS work is nested within asset preparation.

## Historical observations, November 6, 2026

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

## Historical decision

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
These historical runs did not measure combined child RSS or peak disk.
Real-footage reproduction and large-project acceptance remain open.


## Production path

`spatial_render._compose` now calls `spatial_composite.compose` for measured
video planes; graphics-only and moving-video browser paint keep the existing
copy path. Chrome's geometry, opacity, clipping and stacking model is unchanged.
Source videos are already validated opaque, so copying the scaled RGB rectangle
into a black canvas matches the previous opaque overlay, including off-screen
clipping. PyAV retains the same bilinear scaling and final RGB conversion.

Mask subtraction uses signed 32-bit integers, clamps negative differences, then
uses unsigned multiplication/division and saturating accumulation before storing
16-bit values. Arithmetic operates on 64-row blocks over the native frame
buffers. It does not build full-frame 32-bit arrays for each plane or retain
plane buffers across frames. PNG codecs use one thread; placement needs no
filter graph or compositor subprocess. Errors remain `SpatialRenderError`;
publication still occurs only after a complete staged frame. Interrupts propagate
through the existing request-owned worker cancellation/cleanup path.

The regression oracle compares actual decoded RGB values, not compressed PNG
bytes. The direct tests include odd and padded rasters, portrait, row-block
boundaries, negative/partial/fully off-screen rectangles, multiple planes and
mask saturation. Browser fidelity and full video/audio tests cover the canonical
rendering path. Python 3.10 coverage checks the older compatible PyAV/NumPy
versions as well as the full Python 3.12 suite.


## Production measurements, November 7, 2026

Same Apple M4/macOS 15.6 environment, Python 3.12.12, PyAV 18.1.0,
NumPy 2.5.4, FFmpeg 9.0.1 and pinned Chrome 156.0.8078.73. The production
module SHA-256 is `920b128dd4ced2ae0bab9c5d0e60edb337c69f1f61d8ac1724c95f67cc227ca1`;
the wrapper hash is `1acbbb05081e1d27efc111dab0017678004898588981bfce00e69b183d388893`.
Runners retain exact source hashes, samples, versions and load observations.

Twenty randomized trials per case compared all five methods: **400 exact RGB
matches**, including 80 installed-production comparisons. Composition medians:

| Case | Frozen FFmpeg | Installed production | Paired median speedup |
| --- | ---: | ---: | ---: |
| Landscape, one plane | 921.8 ms | 141.7 ms | 6.49× |
| Portrait, one plane | 553.5 ms | 76.0 ms | 7.57× |
| Landscape, two planes | 2,065.0 ms | 276.8 ms | 8.00× |

The moving-video control still copies the browser-painted frame; its
sub-millisecond measurements are not a meaningful speed improvement.

One real five-second completed-preview run per method, same saved project,
review profile and source bytes:

| Measurement | Frozen FFmpeg | Installed production |
| --- | ---: | ---: |
| Complete worker generation | 410.58 s | 160.05 s |
| Composition | 306.64 s | 46.82 s |
| FFmpeg launches | 454 | 154 |
| Sampled summed worker-group RSS | 951.8 MiB | 1,098.0 MiB |
| Sampled request-folder disk | 29.35 MiB | 29.47 MiB |

Both full clips have the same decoded video/audio hashes recorded in the
historical table, 150 frames at 30 fps, five seconds of 48 kHz mono AAC audio,
and unchanged saved project state. Source decoder opens (150), probes (153),
browser requests (150) and browser PNG captures (300) are unchanged. Production
reads 450 PNG inputs in process; no rendered clip or source decoder is cached.

**These are shared-host observations.** Load averages ranged from roughly 27
to 43 during the complete-worker pair. The observed overall ratio is 2.57×,
not the stage-only 6–8×; neither ratio establishes edit-to-preview percentiles,
controlled-host throughput, long-project performance or the three-second gate.
The common renderer now uses this path for spatial stills, preview and export,
but the whole workflow remains too slow for interactive review.

The independent 4K probe captured two video planes at 3840×2160, then composed
that same captured frame 20 times in a fresh process. Its first/last decoded
pixels match the frozen reference. Worker high-water RSS was **463.2 MiB**;
post-frame RSS stabilized around 456 MiB after warm-up, with the final hash
verification included in the reported maximum. This probe excludes browser
capture/setup and includes hash checking, so it is not a combined 4K renderer
memory result or a throughput benchmark. The full 1080p worker observation above
includes its browser/FFmpeg group and stayed below the earlier 2 GiB renderer
budget, with a higher sampled maximum than the reference in this pair.

The Python 3.10 checks also passed with PyAV 17.1.0 and both NumPy 1.24.4 and
2.2.6. The license policy records separately reviewed fingerprints and upstream
wheel/bundled-license evidence for those metadata variants; unknown changes
still fail closed. Linux CI checks the lowest NumPy version with Python 3.10,
and runs the complete browser/video suite with Python 3.12.

Next measure request-owned source probing/decoder reuse and repeated browser
work, then the full edit-to-human-player path. Native/progressive preview remains
a separate delivery option to evaluate; unchanged-scene reuse, persistent cache
invalidation and account-session integration remain outside this change.
