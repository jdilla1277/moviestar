"""Measure longer title and two-line caption glyph bounds in preview/export."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
frames = root / "frames"
preview = root / "civic_preview.mp4"
subprocess.run([
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
    "-ss", "3", "-i", str(root / "civic_video_layer.mp4"),
    "-loop", "1", "-i", str(frames / "civic-1280x720-static-overlay.png"),
    "-loop", "1", "-i", str(frames / "civic-1280x720-tag-only.png"),
    "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[v1];"
                       "[v1][2:v]overlay=x=36:y=0:format=auto[out]",
    "-map", "[out]", "-frames:v", "1", "-c:v", "libx264",
    "-pix_fmt", "yuv420p", str(preview),
], check=True)


def raw(path, frame=None):
    command = ["ffmpeg", "-v", "error", "-i", str(path)]
    if frame is not None:
        command.extend(["-vf", f"select=eq(n\\,{frame})"])
    command.extend(["-frames:v", "1", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "pipe:1"])
    return subprocess.check_output(command)


images = {
    "browser": raw(frames / "civic-1280x720-rendered.png"),
    "preview": raw(preview),
    "export": raw(root / "civic_tag_motion.mp4", 90),
}
regions = {
    "title": (36, 99, 886, 148),
    "caption": (36, 584, 1244, 684),
}
result = {}
for name, (x0, y0, x1, y1) in regions.items():
    result[name] = {}
    for source, image in images.items():
        points = []
        for y in range(y0, y1):
            for x in range(x0, x1):
                offset = (y * 1280 + x) * 3
                color = image[offset:offset + 3]
                if min(color) > 200 and max(color) - min(color) < 25:
                    points.append((x, y))
        result[name][source] = [min(x for x, _ in points),
                                min(y for _, y in points),
                                max(x for x, _ in points),
                                max(y for _, y in points)]
    browser = result[name]["browser"]
    for source in ("preview", "export"):
        assert max(abs(a - b) for a, b in zip(browser, result[name][source])) <= 2

print(json.dumps(result))
