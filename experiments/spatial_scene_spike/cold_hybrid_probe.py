"""Measure fresh browser description and FFmpeg single-frame preview."""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import frame_contract_probe  # prepares the synthetic asset and HTML page

root = Path(__file__).resolve().parent
description_file = root / "frame_contract_cold.json"
rows = []
for _ in range(5):
    start = time.perf_counter()
    browser = subprocess.run([
        "node", str(root / "browser_probe.mjs"), "--frame-contract",
        "--name", "cold", "--width", "1280", "--height", "720",
        "--time", "3", "--shift", "0",
    ], check=True, text=True, capture_output=True)
    description_hash = hashlib.sha256(description_file.read_bytes()).hexdigest()
    encoder = subprocess.run([
        sys.executable, str(root / "singleframe_preview.py"),
        str(description_file),
    ], check=True, text=True, capture_output=True)
    encoder_result = json.loads(encoder.stdout)
    if encoder_result["description_sha256"] != description_hash:
        raise RuntimeError("preview did not consume this run's frame description")
    rows.append({
        "end_to_end_ms": round((time.perf_counter() - start) * 1000),
        "browser": json.loads(browser.stdout),
        "encoder": encoder_result,
        "description_sha256": description_hash,
    })
times = sorted(row["end_to_end_ms"] for row in rows)
if len({row["description_sha256"] for row in rows}) != len(rows):
    raise RuntimeError("a cold run reused an older frame description")
result = {"runs": rows, "p95_ms": times[-1]}
(root / "cold_hybrid_results.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))
