# Spatial frame rendering

This slice connects the persisted visual tree and canonical media clock to a
real still-frame preview. It advances milestone 2; it does not complete the
watch/export, caption, motion, responsive-rule, or static-scene timing gates.

## Shared boundary

`compile_spatial_frame` selects the scene, expands linked components, and resolves
media clocks. `prepare_layout_frame` validates and translates the supported CSS
rules. One browser page measures the result, paints graphics, and can also draw
the labeled layout view. Absolute media surfaces never participate in flow, so
adding asset pixels leaves measured element boxes and text lines unchanged.

FFmpeg decodes each distinct asset/time pair once, normalizing display aspect
ratio and orientation into a PNG. Local images become RGBA PNGs painted by the
browser; videos remain opaque RGB frames composed by FFmpeg. This avoids browser
video codec and seek differences. The frame response records the source times
and pixel-aligned sampling rectangles used by FFmpeg.

## Browser paint and video visibility

A flattened graphics image cannot simply be overlaid on every video: it can put
a parent's background over its own child footage. DOM order also does not account
for stacking contexts, clipping, or group opacity.

The supported paint vocabulary uses normal source-over composition. For opaque
video, its contribution to each output channel is linear in its source pixels:

`result = base + sum(video_pixels[i] * visibility[i])`

Chrome first captures the graphics with all video surfaces black. It then captures
each video surface white, leaving other video surfaces black. Subtracting the base
from that capture measures the surface's visibility through every foreground and
ancestor opacity/clip. These are RGB contribution masks, including antialiased
text edges. FFmpeg scales each source frame into its measured sampling rectangle,
multiplies it by its mask, and adds it to the base. Sixteen-bit intermediate planes
retain fractional contributions until the final RGB PNG conversion.

Calibration surfaces use opaque PNG textures with the source's intrinsic size,
matching the browser reference's image path. Every capture forces a complete
repaint: partial repaint can reuse rounded-clip edge pixels from a previous
calibration. CSS filters, blend modes, transforms, and arbitrary HTML/scripts are
outside the accepted vocabulary. Video with alpha is rejected because its pixels
would change the visibility of underlying paint; images with alpha are browser
graphics and are supported.

This boundary can supply a future export compositor with static graphics and
masks while FFmpeg supplies changing video frames. The current preview regenerates
everything and removes its temporary directory. Reusing previously rendered scene
clips and cache invalidation remain a separate project.

## Verification

`app/tests/test_spatial_render.py` generates a lossless 10 fps clip whose source
frames have distinct RGB values. Three uses inside nested reusable components
sample source times 0.5, 0.7, and 0.9 seconds. Their actual pixels identify frames
5, 7, and 9. A separate two-color source checks orientation and contain/cover
placement, including transparent letterboxes.

The tests compare every output channel against a browser reference using the
same decoded source PNGs, with a maximum error budget of 3/255 declared in the
tests before implementation. This budget checks paint composition on flat-color
footage and patterned footage at native sampling size. They cover landscape and
portrait, long text,
transparent images, parent backgrounds, foreground badges, intersecting rounded
clips, group opacity, and overlapping video uses whose paint order differs from
DOM order. Layout and rendered views must report identical element rectangles.
Ended clips become transparent; missing bindings/assets and unsupported captions
or alpha video fail explicitly. CLI coverage checks JSON errors, unchanged project
state, and preservation of the previous artifact when an asset disappears.

This establishes still-frame geometry and paint parity on synthetic content.
It is not evidence for export throughput or every source decoder. Motion, cue
changes, transition frames, and real-content watch/export friction tests remain
separate delivery gates.

The browser reference is a paint check, not a promise that FFmpeg's video scaler
matches Chrome's image scaler. A larger manual `testsrc2` example measured mean
channel differences below 0.4/255, but individual sharp-edge pixels differed by
69/255 in landscape and 21/255 in portrait when resampled. Those cases do not
meet the 3/255 paint budget and are not counted as passing that gate. The product
preview uses FFmpeg's bilinear video sampling; future export must use that same
sampling path. Native-size patterned coverage isolates paint fidelity from this
known resampling difference. Do not claim arbitrary scaled footage is pixel
identical to browser image rendering. This manual check used a 320×180, 10 fps
source sampled at 0.5/0.7/0.9 seconds on 960×540 and 540×960 canvases, macOS
arm64, Chrome 155.0.8059.85, FFmpeg 9.0.1, and Node 24.5.0.
