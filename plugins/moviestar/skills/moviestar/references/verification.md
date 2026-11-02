# Render verification

Verification should test the requested outcome, not merely that FFmpeg exited
successfully.

## Before the final render

1. Run `status` and inspect the resolved duration, source selection,
   composition, captions/overlays, and audio configuration.
2. Run `export --dry-run --out PATH`. Review the resolved output path, render
   status, warnings, `video_encoder`, and FFmpeg plan. `export`, `watch`, `clip`,
   and `batch` accept `--video-encoder auto|x264|videotoolbox`. Auto prefers x264
   and falls back to VideoToolbox on macOS; explicit selections never silently
   substitute. Re-encoding dry-runs may list FFmpeg encoders without encoding
   media. Stream-copy reports `video_encoder: null` and rejects explicit
   encoders; use `watch --precise` or drop `--fast` to re-encode. All scene
   stages and batch clips share one choice. Hardware initialization can still
   fail at render time; inspect the error and run `doctor` before retrying.
3. Make a quick `export --preview` or one or more short `watch --precise`
   renders when framing, motion, transitions, captions, or audio need review.
   For transitions between spatial scenes, use `screenshot --at TIMECODE` or
   `composition model preview --at TIMECODE --out frame.png --layout-out layout.png`
   to inspect both named scene layers and weights at one moment. Read `frame.kind`:
   a transition frame has `layers` rather than root `children`. The labeled view
   keeps both trees visible with role/weight labels. Mixed/preset transitions and
   overlapping internal windows still require `watch` or `export`.
4. View returned image paths with the available image tool. Read video files
   with the available multimodal tool or sample them with `screenshot` and
   `watch`; do not ingest `--inline` Base64 as ordinary text.
5. For TikTok, Instagram Reels, or YouTube Shorts delivery, capture important
   frames with `screenshot --platform PLATFORM`. Review both the annotated
   image and structured `platform_ui_occlusion` warnings. This checks only
   Moviestar-authored overlay/slot geometry; no OCR or object detection is run
   on source pixels.

## After the final render

- Run `moviestar probe FINAL.mp4` and confirm non-zero duration, expected
  dimensions, codecs, and audio/video streams.
- Capture representative first, middle, and final frames with
  `moviestar screenshot --file FINAL.mp4 --at TIMECODE`. Add boundary frames
  around important cuts, transitions, overlays, and scene changes.
- Check that captions are readable and synchronized, subjects remain framed,
  overlays are not clipped, and the final frame is intentional.
- When audio matters, inspect or listen to representative sections. Use
  `probe --loudness` or a Moviestar loudness report for measurable levels and
  clipping risk.
- Compare actual duration with the intended result. A frame of container drift
  can be normal for re-encoded output; larger unexplained drift needs review.

## Handoff

Report the absolute output path, duration, dimensions, stream presence, and the
visual/audio evidence inspected. Mention unresolved warnings or subjective
choices instead of presenting them as verified facts.
