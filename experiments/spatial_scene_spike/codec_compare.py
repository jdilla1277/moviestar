"""Check separately encoded screenshot and export at scene time three seconds."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
frames = root / "frames"


def raw(path, pixel_format="rgb24"):
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", pixel_format, "pipe:1",
    ])


preview = raw(frames / "podcast-1280x720-preview-decoded.png")
export = raw(frames / "podcast-1280x720-gop-t3.png")
mask = raw(root / "masks/mask_793x465_r16.png", "gray")
edge_errors = []
for y in range(465):
    for x in range(793):
        if 0 < mask[y * 793 + x] < 255:
            offset = ((y + 107) * 1280 + x + 34) * 3
            edge_errors.append(max(abs(preview[offset + c] - export[offset + c]) for c in range(3)))
edge_errors.sort()


def glyph_bounds(image):
    points = []
    for y in range(25, 100):
        for x in range(25, 750):
            offset = (y * 1280 + x) * 3
            color = image[offset:offset + 3]
            if min(color) > 200 and max(color) - min(color) < 25:
                points.append((x, y))
    return [min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points), max(y for _, y in points)]


print(json.dumps({
    "edge_samples": len(edge_errors),
    "preview_export_edge_p95_rgb_error": edge_errors[int(0.95 * (len(edge_errors) - 1))],
    "preview_export_edge_max_rgb_error": edge_errors[-1],
    "preview_title_glyph_bounds": glyph_bounds(preview),
    "export_title_glyph_bounds": glyph_bounds(export),
}))
