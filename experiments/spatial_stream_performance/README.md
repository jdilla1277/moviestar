# Bounded spatial-frame pipeline

`measure.py` generates five distinct H.264/yuv420p files (3840×2160, 24 fps,
two B-frames, three reference frames), then exports four/five simultaneous
cameras at both 1920×1080 and 3840×2160. Each case runs 4s and 16s in fresh
workers. Identical synthetic pictures have independent paths, decoders and
source offsets; they do not share decoded buffers.

```bash
python experiments/spatial_stream_performance/measure.py --out /tmp/stream-memory
```

Use a new output directory, FFmpeg/ffprobe, Node 22+, a pinned headless Chrome
(`MOVIESTAR_CHROME_PATH`) and the normal package dependencies. For a quick
harness smoke test, use
`--source-width 640 --source-height 360 --short 4 --long 6 --canvas 1080p`;
that is not a 4K-input acceptance result. Outputs/logs/media stay out of Git.

The CI job runs this in a Linux Docker container with **4 GiB RAM and two CPU
cores**. Swap is disabled so the RAM limit cannot be masked by paging.
The short run includes 96 frames to fill codec
lookahead/reference buffers before comparing it with a longer run. One-second
tests do not establish a memory plateau.
CPU affinity is constrained along with CPU quota: native encoders may
otherwise size their thread pools for the host. The harness verifies the cgroup
limit and CPU affinity, records cgroup memory/peak/OOM events, and samples the
entire worker process tree (including separately grouped encoder/browser
children). Shared pages can count twice and short peaks can be missed in the
RSS samples; the container limit is independent of this sampler.

Assertions cover one source open per active camera, exact direct-compositor vs
PNG-reference pixels, output dimensions/frame count, unchanged project files,
absence of an output PNG sequence, no container OOM kills, and RAM plateau when
duration increases fourfold. The plateau check allows 256 MiB or 25% above the
short-run peak for allocator/native warm-up and sampling. These resource checks
are not a timing target or a guarantee for every codec, HDR, resolution or layer
count. All samples and failed results are retained.

The Python producer has one outstanding acknowledged frame write; FFmpeg's raw
input queue is one packet. Decoder references, five source cursors, Chrome and
encoder lookahead require additional memory. Only current-frame fallback PNGs
for compatibility/motion/preset/transitions remain. Audio/video fidelity is also
covered by the regular spatial export matrix and separate real-footage checks.

The image is a test environment, not a published runtime/dependency image.
System FFmpeg licensing depends on its build (LGPL/GPL); PyAV/NumPy terms remain
covered by the package's dependency-license policy. Chrome for Testing is fetched
for CI use as in the existing renderer jobs; it is not bundled in the package.
