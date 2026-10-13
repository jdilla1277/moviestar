"""Compare browser boxes with encoded moving component and element edges."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
records = json.loads((root / "browser_results.json").read_text())["scenes"]


def decoded_frame(video, number):
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(video),
        "-vf", f"select=eq(n\\,{number})", "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ])


results = []
for scene, node, video in [
    ("podcast", "listener-pair", "singlepass_gop.mp4"),
    ("civic", "tag", "civic_tag_motion.mp4"),
]:
    record = records[f"{scene}-1280x720"]
    for time, frame_number in [(0, 0), (3, 90), (5.9, 177)]:
        boxes = record["boxes"] if time == 3 else record["motion_boxes"][str(time)]
        box = next(box for box in boxes if box["id"] == node)
        pixels = decoded_frame(root / video, frame_number)
        if scene == "podcast":
            x = 950
            measured = next(y for y in range(90, 160) if max(
                abs(pixels[(y * 1280 + x) * 3 + channel] - color)
                for channel, color in enumerate((16, 27, 43))
            ) > 60)
            expected = box["y"]
            edge = "top"
        else:
            y = 41
            measured = next(x for x in range(0, 220) if (
                (lambda color: color[0] > 150 and color[1] > 120 and color[2] < 120)(
                    pixels[(y * 1280 + x) * 3:(y * 1280 + x) * 3 + 3]
                )
            ))
            expected = box["x"]
            edge = "left"
        results.append({"scene": scene, "time_s": time, "edge": edge,
                        "browser_px": round(expected, 2), "export_px": measured,
                        "absolute_error_px": round(abs(expected - measured), 2)})

print(json.dumps(results))
assert max(result["absolute_error_px"] for result in results) <= 1
