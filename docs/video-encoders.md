# H.264 video encoders

`export`, `watch`, `clip`, and `batch` accept
`--video-encoder auto|x264|videotoolbox`. This is a per-invocation choice;
it does not change `spec.json`.

```bash
moviestar doctor
moviestar export --video-encoder videotoolbox --dry-run --out final.mp4
moviestar export --video-encoder videotoolbox --out final.mp4
moviestar clip --from 0 --to 10 --video-encoder auto
moviestar watch --from 0 --to 10 --precise --video-encoder videotoolbox
moviestar batch clips.json --video-encoder videotoolbox --jobs 2
```

| Request | Re-encoding behavior |
| --- | --- |
| `auto` (default) | Prefer `libx264`. If absent, use `h264_videotoolbox` on macOS. |
| `x264` | Require `libx264`; fail if absent. |
| `videotoolbox` | Require macOS and advertised `h264_videotoolbox` support. |

If no supported encoder is available, the command fails before rendering with
`ffmpeg_no_supported_h264_encoder` and a `doctor` hint. An explicit selection
never substitutes another encoder. Once rendering starts, an encoder failure
returns the FFmpeg error and selected `video_encoder`; it does not retry.

`doctor` lists both candidates, reports the effective `auto` selection as
`video_encoder`, and distinguishes advertised support from actual hardware/session
initialization. A VideoToolbox-capable Mac does not need x264. Other missing
capabilities, such as text filters or AAC, are reported independently.

For actual stream-copy plans, `auto` reports `video_encoder: null` and does not
probe encoders. An explicit selection fails with
`video_encoder_requires_reencode`: drop `--fast` for clip/export, or add
`--precise` for a source watch. A scene composition or filtered/multi-segment
export still re-encodes even with `--fast`, so explicit selection works there.

Every render and dry-run reports `video_encoder`. Automatic VideoToolbox
selection adds a `note` explaining that x264 is absent. Batch clips include the
choice individually, and all scene clips and the final composition use the
same encoder. The `ffmpeg_command`, `scene_render_commands`, and `concat_command`
fields show the actual argv.

## Dry-run and quality

Re-encoding dry-runs may run the read-only `ffmpeg -hide_banner -encoders`
listing, once per invocation. They do not encode/decode media, create files,
or overwrite output. A failed listing produces `ffmpeg_capability_probe_failed`
instead of guessing. Under the same capabilities and project state, the
dry-run command matches the real render command.

x264 retains `-preset fast -crf 18` for full renders and
`-preset ultrafast -crf 28` for 480p previews. VideoToolbox uses an independent
average bitrate target: **12 Mbit/s full**, **2 Mbit/s preview**, with H.264 High
profile and automatic level selection. These are targets, not fixed file sizes
or CRF equivalents. Detail, motion, resolution, frame rate, and Apple hardware
affect quality and size. In scene previews, each scene uses the preview target;
the final concat retains the full target to limit additional encoding loss.

Both paths emit H.264 with `yuv420p` and even dimensions; routed audio continues
to use native AAC. Preview height remains 480 pixels. Review detailed screen
content and high-resolution/high-motion footage before delivery; VideoToolbox
is not a promise of equivalent quality or a universal speedup.

Moviestar still requires user-installed FFmpeg/ffprobe. No encoder, binary,
or new runtime dependency is bundled. VideoToolbox uses Apple's system
framework on macOS; OpenH264 and other platform encoders are not supported.
