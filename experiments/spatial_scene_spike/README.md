# Reproducing the spatial renderer spike

Run from the repository root on macOS with FFmpeg/ffprobe, Python 3.10+,
Node 24+, and a local Chrome or Chromium binary. The measured FFmpeg build
included `libx264`. Install the MovieStar package in the Python environment
or set `PYTHONPATH=app/src`. Set `MOVIESTAR_CHROME_PATH` if Chrome is not at
`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.

The scripts create synthetic test videos, a transparent logo, HTML pages,
browser masks, PNG frames, and encoded results in this directory. Those
artifacts are ignored by Git. They do not read project or customer media.

Use `experiments/spatial_scene_spike` as the command argument prefix below:

```sh
python3 experiments/spatial_scene_spike/gen_assets.py
python3 experiments/spatial_scene_spike/make_pages.py
python3 -m unittest discover -s experiments/spatial_scene_spike -p 'test_*.py'
node experiments/spatial_scene_spike/browser_probe.mjs --reuse-only
node experiments/spatial_scene_spike/browser_probe.mjs --export
python3 experiments/spatial_scene_spike/make_masks.py
node experiments/spatial_scene_spike/browser_probe.mjs --generate-masks
node experiments/spatial_scene_spike/browser_probe.mjs --calibration
python3 experiments/spatial_scene_spike/hybrid_probe.py
python3 experiments/spatial_scene_spike/compare.py
python3 experiments/spatial_scene_spike/clip_calibration.py
python3 experiments/spatial_scene_spike/ffmpeg_text_probe.py
python3 experiments/spatial_scene_spike/motion_probe.py
python3 experiments/spatial_scene_spike/element_motion_probe.py
python3 experiments/spatial_scene_spike/text_parity_probe.py
python3 experiments/spatial_scene_spike/transition_probe.py
python3 experiments/spatial_scene_spike/singlepass_probe.py --gop
python3 experiments/spatial_scene_spike/singlepass_probe.py
python3 experiments/spatial_scene_spike/codec_compare.py
python3 experiments/spatial_scene_spike/motion_compare.py
python3 experiments/spatial_scene_spike/layer_order_probe.py
python3 experiments/spatial_scene_spike/frame_contract_probe.py
python3 experiments/spatial_scene_spike/cold_hybrid_probe.py
python3 experiments/spatial_scene_spike/temporal_gate.py --verify
```

`browser_probe.mjs --export` captures 180 full browser frames and takes about
30 seconds including the other compositions. Use `browser_probe.mjs` without
`--export` for the shorter geometry/text run. Each run writes ignored JSON
records, PNGs, and clips that can be inspected locally. The bounded renderer
decision and measurements are in [the design record](../../docs/design/spatial-render-spike.md).
The cold probe uses the frame contract fixture, regenerates a measured JSON
description in a fresh browser process for every run, and passes that exact
description to `singleframe_preview.py`. The focused layer probe tests paint
order inside the media region and ancestor clipping. The frame contract probe
tests preview/export geometry on two canvases and result times. The temporal
gate generates numbered source clips and verifies nonzero trims, both source
positions through a transition, the exact caption cue frame, and
browser/encoded-frame parity in portrait and landscape. It also times fresh and
warm preview plus complete export and checks peak process-tree memory. It needs
no customer footage. The generated videos, page, screenshots, descriptions,
and exports stay ignored by Git.

This is a disposable benchmark, not a supported MovieStar command. Earlier
probes use the existing FFmpeg layout builder as a convenient media compositor
and patch its generated graph to exercise browser masks, RGB composition, and
motion. The temporal gate uses a direct FFmpeg graph. Product integration should
make those operations explicit.
