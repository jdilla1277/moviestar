# Render verification

Verification should test the requested outcome, not merely that FFmpeg exited
successfully.

## Before the final render

For custom scene designs, `composition model layouts` lists editable starting
points (`single`, `two-up`, `pip`, `text-video`). `layouts show ID` returns typed
inputs and ordinary component trees. Use `layouts apply ID --scene SCENE_ID
--bindings FILE --dry-run` before applying; the scene must already exist.
Bindings use typed values such as `{"video":{"asset_id":"camera"}}` and
`{"title":{"text":"A new chapter"}}`. Missing inputs are saveable drafts;
optional image/caption inputs start empty. Captions need an explicit track
binding. Applying captures project-owned components, preserving existing shared
edits on later applications. Replacing a spatial tree requires `--replace`;
review the response and use `undo` if needed. Existing scene timing/audio stay
in place, but legacy visual overlays/framing are not migrated. Verify the new
design in both rendered and labeled views, including portrait where relevant.

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
   For transitions in spatial projects (including preset participants), use `screenshot --at TIMECODE` or
   `composition model preview --at TIMECODE --out frame.png --layout-out layout.png`
   to inspect both named scene layers and weights at one moment. Read `frame.kind`:
   a transition frame has `layers` rather than root `children`. The labeled view
   keeps both layers visible with role/weight labels. A preset layer has
   `frame.kind: preset` and uses the native FFmpeg plan; preset text bounds and
   labels explicitly say `estimated`, while spatial glyph geometry is measured.
   Overlapping internal windows still require `watch` or `export`.
   For persistent local human review of spatial scenes, run `moviestar preview
   --from 0 --to 5 --no-open` in a persistent terminal and return its browser URL.
   It follows saved edits and CLI undo; historical viewing does not undo the
   project. Keep the process running until review is finished, then use Ctrl-C
   to stop it. This first viewer generates completed clips with the existing
   renderer and can take minutes; wait for playable media rather than treating
   the returned link as readiness. No account or upload is required. The link
   works only on the same machine; remote agents need a separately verified
   forwarding/delivery route.
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
