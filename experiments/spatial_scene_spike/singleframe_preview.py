"""Render a preview from a freshly measured frame description."""

import json
import sys
import time
from pathlib import Path

from frame_contract_probe import render


description_file = Path(sys.argv[1])
start = time.perf_counter()
output, description_hash = render(description_file, "preview")
print(json.dumps({
    "preview_render_ms": round((time.perf_counter() - start) * 1000),
    "bytes": output.stat().st_size,
    "description_sha256": description_hash,
}))
