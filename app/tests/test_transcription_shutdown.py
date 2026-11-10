"""Native speech dependencies must be configured before they initialize."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("configured", [None, "0", "1"])
def test_transcription_configures_runtime_before_loading_optional_dependencies(configured):
    env = os.environ.copy()
    env.pop("ORT_DISABLE_TELEMETRY", None)
    if configured is not None:
        env["ORT_DISABLE_TELEMETRY"] = configured
    result = subprocess.run(
        [sys.executable, "-c", """
import json, os, sys
import moviestar.transcribe
print(json.dumps({
    "disabled": os.environ.get("ORT_DISABLE_TELEMETRY"),
    "native_loaded": "onnxruntime" in sys.modules,
    "whisper_loaded": "faster_whisper" in sys.modules,
}))
"""],
        env=env, capture_output=True, text=True, timeout=20, check=True,
    )
    assert json.loads(result.stdout) == {
        "disabled": "1" if configured is None else configured,
        "native_loaded": False,
        "whisper_loaded": False,
    }
    assert result.stderr == ""


def test_native_speech_detection_finishes_and_exits_without_telemetry():
    env = os.environ.copy()
    env.pop("ORT_DISABLE_TELEMETRY", None)
    result = subprocess.run(
        [sys.executable, "-c", """
import json, os
from moviestar.transcribe import _detect_speech
import numpy as np
spans = _detect_speech(np.zeros(48000, dtype=np.float32))
print(json.dumps({"spans": spans, "disabled": os.environ.get("ORT_DISABLE_TELEMETRY")}))
"""],
        env=env, capture_output=True, text=True, timeout=30, check=True,
    )
    assert json.loads(result.stdout) == {"spans": [], "disabled": "1"}
    assert result.stderr == ""
