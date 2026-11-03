# Render verification

Verification should test the requested outcome, not merely that FFmpeg exited
successfully.

## Before the final render

Use `captions audit` to enumerate text states across the complete result clock.
Add `--language en` or explicit `--connector-word` values for connector-only
candidates; no language is assumed. `--track NAME` also selects manual text;
`--combine-tracks` groups selected tracks that form one treatment. Duration
warnings and estimated geometry changes require contextual review. Optional
`--proof-dir NEW_DIRECTORY` creates bounded project screenshots and report.json;
inspect their stated coverage and verify the final export separately. The
default audit renders nothing and never changes project state.

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
  clipping risk. For `export --loudness-target`, check both
  `loudness_normalization.target_met` (integrated LUFS only) and
  `true_peak_limit_met`. Final encoded peak has its own 0.1 dB reporting
  tolerance; an exceeded peak emits `true_peak_limit_exceeded`, and an
  unmeasurable peak remains unknown. A successful render or met LUFS target
  does not establish peak compliance.
- Compare actual duration with the intended result. A frame of container drift
  can be normal for re-encoded output; larger unexplained drift needs review.

## Handoff

Report the absolute output path, duration, dimensions, stream presence, and the
visual/audio evidence inspected. Mention unresolved warnings or subjective
choices instead of presenting them as verified facts.
