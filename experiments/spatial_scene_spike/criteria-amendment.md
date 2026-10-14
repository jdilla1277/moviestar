# Spatial composition spike: codec-aware amendment

Recorded 2026-10-13 after the first preview/export comparison and before
retesting the candidate. The original criteria remain in `criteria.md`.

The original alpha/clipping budget combined two different effects: layout
compositing and lossy H.264 encoding. A red rounded rectangle composited from
a browser-generated mask with FFmpeg in RGB had alpha-edge P95 channel error
2/255 against Chrome before encoding. Encoding a one-frame preview and a
180-frame export with normal inter-frame H.264 raised P95 error to 15/255,
even though both used the same boxes and mask. All-intra output reduced the
isolated rectangle's error to 6/255, but grew the six-second podcast sample
from about 2 MB to about 7 MB. The original <=8 encoded color threshold thus
measures codec context, not just renderer parity.

Revised gate, preserving the original intent:

* Before encoding: a fixed-color fiducial clipped by the production mask has
  no pixels outside the clip in the lossless composed frame, and alpha-edge
  P95 channel difference against Chrome is <=8/255. Geometry remains <=1
  output pixel per edge. Measure live video RGB differences separately:
  independent Chrome and FFmpeg decoders do not produce identical pixels or
  necessarily seek to the same source frame, so that comparison cannot isolate
  clipping fidelity.
* After H.264 encoding: preview and export use the same resolved frame
  description. At the sampled time, P95 RGB channel difference is <=16/255
  in rounded-edge samples. Report maximum and outliers; do not claim exact
  pixel identity from independently encoded files.
* Text keeps exact line breaks and <=2-pixel glyph bounds in the lossless
  frame. Report decoded H.264 glyph differences separately.

The new threshold applies to codec color variation only. Timing, geometry,
preview latency, export throughput, and memory budgets are unchanged. If a
sample exceeds the revised edge budget, revisit the renderer strategy.
