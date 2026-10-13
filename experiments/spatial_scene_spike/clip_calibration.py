"""Compare a fixed-color rounded crop in Chrome and FFmpeg before encoding."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
frames = root / "frames"
mask = root / "masks/mask_793x465_r16.png"
output = frames / "calibration-ffmpeg.png"

cmd = [
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "color=c=0x101b2b:s=1280x720:d=0.1",
    "-f", "lavfi", "-i", "color=c=0xf23e45:s=793x465:d=0.1",
    "-i", str(mask),
    "-filter_complex",
    "[1:v]format=rgba[red];[2:v]format=gray[mask];"
    "[red][mask]alphamerge[clipped];"
    "[0:v][clipped]overlay=x=34:y=107:format=rgb[out]",
    "-map", "[out]", "-frames:v", "1", str(output),
]
subprocess.run(cmd, check=True)


def raw(path, pixel_format):
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", pixel_format, "pipe:1",
    ])


reference = raw(frames / "calibration-browser.png", "rgb24")
actual = raw(output, "rgb24")
alpha = raw(mask, "gray")
edge_errors = []
leaked = 0
for y in range(720):
    for x in range(1280):
        mask_value = alpha[(y - 107) * 793 + (x - 34)] if 34 <= x < 827 and 107 <= y < 572 else 0
        offset = (y * 1280 + x) * 3
        channel_error = max(abs(reference[offset + channel] - actual[offset + channel]) for channel in range(3))
        if 0 < mask_value < 255:
            edge_errors.append(channel_error)
        elif mask_value == 0 and channel_error > 2:
            leaked += 1
edge_errors.sort()
print(json.dumps({
    "edge_samples": len(edge_errors),
    "edge_p95_rgb_error": edge_errors[int(0.95 * (len(edge_errors) - 1))],
    "edge_max_rgb_error": edge_errors[-1],
    "outside_clip_pixels_over_2_rgb": leaked,
}))
