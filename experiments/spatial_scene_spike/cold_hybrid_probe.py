"""Measure fresh browser layout plus FFmpeg single-frame preview five times."""

import json
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent
rows = []
for _ in range(5):
    start = time.perf_counter()
    browser = subprocess.run(
        ["node", str(root / "browser_probe.mjs"), "--cold-hybrid"],
        check=True, text=True, capture_output=True,
    )
    encoder = subprocess.run(
        [sys.executable, str(root / "singleframe_preview.py")],
        check=True, text=True, capture_output=True,
    )
    boxes = json.loads((root / "cold_hybrid_boxes.json").read_text())
    rows.append({
        "end_to_end_ms": round((time.perf_counter() - start) * 1000),
        "browser": json.loads(browser.stdout),
        "encoder": json.loads(encoder.stdout),
        "box_count": len(boxes),
    })
times = sorted(row["end_to_end_ms"] for row in rows)
result = {"runs": rows, "p95_ms": times[4]}
(root / "cold_hybrid_results.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))
