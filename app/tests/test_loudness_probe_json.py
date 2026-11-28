"""Unavailable loudness must remain useful, strict JSON (#329)."""
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from moviestar.ffmpeg import normalize_loudness, run_loudness_probe

FIELDS = {
    "input_i": "integrated_lufs", "input_tp": "true_peak_dbtp", "input_lra": "lra_lu",
    "input_thresh": "threshold_lufs", "target_offset": "target_offset_lu",
}


def strict_json(text):
    def invalid(value):
        pytest.fail(f"Non-standard JSON constant: {value}")
    return json.loads(text, parse_constant=invalid)


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", ["-inf", "+inf", "NaN", None, "unavailable"])
def test_probe_helper_returns_only_finite_or_null_metrics(tmp_path, monkeypatch, field, value):
    path = tmp_path / "source.wav"
    path.write_bytes(b"stub")
    raw = {name: "-20" for name in FIELDS}
    raw[field] = value
    monkeypatch.setattr("moviestar.ffmpeg.check_ffmpeg_available", lambda: None)
    monkeypatch.setattr("moviestar.ffmpeg.subprocess.run", lambda cmd, **kw:
                        subprocess.CompletedProcess(cmd, 0, "", json.dumps(raw)))
    metrics, _ = run_loudness_probe(str(path))
    assert metrics[FIELDS[field]] is None
    for other, key in FIELDS.items():
        if other != field:
            assert metrics[key] == -20
    json.dumps(metrics, allow_nan=False)


@pytest.fixture(params=["short", "silence", "near_eof", "tone"])
def probe_media(tmp_path, request):
    path = tmp_path / "source.wav"
    duration = "0.1" if request.param == "short" else "1"
    source = "anullsrc=r=48000:cl=mono" if request.param == "silence" else "sine=frequency=440:sample_rate=48000"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source,
                    "-t", duration, "-c:a", "pcm_s16le", str(path)], check=True)
    return path, request.param


def test_real_process_probe_is_strict_json_with_actionable_unknowns(probe_media):
    path, mode = probe_media
    before = path.read_bytes()
    args = [str(Path(sys.executable).with_name("moviestar")), "probe", str(path), "--loudness"]
    if mode == "near_eof":
        args += ["--from", ".9", "--to", "1"]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    data = strict_json(result.stdout)
    loudness = data["loudness"]
    warnings = [w for w in data.get("warnings", []) if w["code"] == "loudness_metrics_unavailable"]
    if mode == "tone":
        assert math.isfinite(loudness["integrated_lufs"])
        assert not warnings
    else:
        assert loudness["integrated_lufs"] is None
        assert len(warnings) == 1
        assert "longer" in warnings[0]["message"].lower()
        assert set(warnings[0]["unavailable_metrics"]) == {key for key in FIELDS.values() if loudness[key] is None}
        if mode != "silence":
            assert math.isfinite(loudness["true_peak_dbtp"])
    if mode == "near_eof":
        assert loudness["range"]["from"]["seconds"] == .9
        assert loudness["range"]["duration"]["seconds"] == .1
    assert path.read_bytes() == before


@pytest.mark.parametrize("mode", ["short", "silence"])
def test_normalization_keeps_unmeasurable_input_unchanged(tmp_path, mode):
    path = tmp_path / "source.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=mono" if mode == "silence" else "sine=frequency=440:sample_rate=48000",
                    "-t", "0.1" if mode == "short" else "1", "-c:a", "pcm_s16le", str(path)], check=True)
    before = path.read_bytes()
    info = normalize_loudness(str(path), -14)
    assert info["plan"]["reason"] == "input_unmeasurable"
    assert info["plan"]["gain_db"] is None
    assert info["apply_command"] is None
    assert info["pre"]["integrated_lufs"] is None
    assert info["measured"]["integrated_lufs"] is None
    assert path.read_bytes() == before
    json.dumps(info, allow_nan=False)
