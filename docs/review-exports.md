# Review exports

Use `--review` to produce a 720p review copy with more detail than the existing
480p export preview. It selects a shared profile for `export` and `watch`:

```bash
moviestar export --review --out review.mp4
moviestar watch --from 12 --to 20 --review --out review-section.mp4
moviestar export --review --dry-run
```

The profile fits landscape output inside 1280×720, portrait output inside
720×1280, and square output inside 720×720. Other aspect ratios fit inside the
same orientation-aware box. Smaller videos retain their size. Odd dimensions
receive the same padding of at most one pixel used by normal H.264 exports.

Composition, framing, camera motion, text layout, and transitions supported by
the command are rendered at the authored canvas size. The assembled video is
then resized before the final encode. Caption font sizes, wrapping, and project
settings stay unchanged; a 72px caption on a 1080p canvas becomes proportionally
smaller in the review image. Scene intermediates retain their native canvas
and full-quality settings.

## Encoding and output

| Selected encoder | Review video settings |
| --- | --- |
| x264 | `libx264`, `fast` preset, CRF 23, `yuv420p` |
| VideoToolbox | `h264_videotoolbox`, 4 Mb/s target bitrate, High profile, `yuv420p` |

`--video-encoder` and automatic encoder selection work as usual. Audio routing,
fades, mixing, and requested loudness normalization retain the command's usual
behavior. Final measurements include subsequent audio muxing and normalization.

The response adds `render_profile` with the profile name `review-720p`, maximum
long/short edges, selected video encoding settings, resize stage, and actual
measured output dimensions. `file_size_bytes` reports the delivered file's size.
A dry run reports the settings and maximum dimensions with
`actual_dimensions: null`; it does not encode video or create output directories.

With no `--out`, export writes `moviestar-clips/review-720p.mp4`. Watch adds
`_review-720p` to its usual automatic filename. Existing automatic names receive
numeric suffixes. An explicit `--out` retains the normal overwrite behavior.

The 720p profile controls rendition size and encoding settings. A maximum byte
budget is a separate feature; inspect the measured file size before choosing a
delivery route. Review the full-resolution export when judging details that
720p cannot preserve.

## Choosing a command

`export --review` renders the current edit, including a source-only project's
cuts and text. `watch --review` retains watch's existing clock and routing:
without `--source`, scene compositions use composition result-time; raw source
extraction uses source-time and excludes the edit's visual overlays. Use export
to review the complete edited source when there is no scene composition.

`--review` re-encodes and gives watch frame-accurate boundaries. Export rejects
combining it with `--fast` or `--preview`. Normal full exports, 480p previews, and
watch's default extraction mode keep their existing defaults.
