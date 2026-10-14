# Spatial composition spike: preregistered criteria

Recorded before rendering candidate frames on 2026-10-13. Disposable prototype; no production engine selected.

Machine: Apple arm64, 24 GiB RAM; exact CPU, Chrome, FFmpeg, fonts, and package versions belong in results. Test at 1280x720 and 720x1280, 30 fps, six-second clips unless a composition requires a longer text interval. Synthetic clips use visible time markers so wrong seeks are detectable.

Acceptance budgets for each candidate:

| Measure | Budget | Method |
| --- | --- | --- |
| Cold single-frame preview | <= 3.0 s | Fresh renderer process, local assets, frame at scene time 3 s; P95 of five runs. |
| Warm single-frame preview | <= 0.70 s | Same renderer process after warm-up; P95 of 20 frames. |
| Export throughput | >= 1.0x real time | 6 s at 30 fps in <= 6 s wall time, excluding first process launch. |
| Peak renderer memory | <= 2 GiB RSS | Include browser/encoder child processes. |
| Geometry parity | <= 1 output pixel per edge | Compare reported boxes to alpha/colored fiducial edges in preview and export at same time. |
| Text parity | exact line breaks; <= 2 px glyph-bound difference | Compare real-font headline and one/two-line captions in preview/export, including longer copy. |
| Alpha/clipping | zero leaked pixels outside clip; <= 8/255 channel difference at 95% of tested alpha-edge pixels | Compare representative transparent logo and rounded crop. |
| Motion timing | <= 1 frame at 30 fps | Element and component moves, including mid-transition samples. |

Candidates:
A. Browser resolves and rasterizes full frames; FFmpeg encodes the resulting frames. Browser frame description drives both rendered and labeled views.
B. Browser resolves geometry and text; FFmpeg composites media and text from the frame description.
C. FFmpeg-only spatial subset with a custom layout resolver, using drawtext/libass for text.

All three must cover podcast host plus reusable two-video ListenerPair, civic recap with transparent logo and reserved captions, text-led explainer with a non-video TextPanel, and a text/image-only scene. Test landscape and portrait, short/long text, one/two-line captions, clipping, alpha, source video, element and component motion, structured-to-structured and structured-to-legacy transitions. Built-in and custom components must expand through one path. Compare real screenshots, measured boxes, and encoded frames. If none passes, no schema decision follows.
