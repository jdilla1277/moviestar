"""Final encoded true peak is independent of integrated-LUFS success (#99)."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli, _add_loudness_result, _finalize_loudness_normalization
from moviestar.ffmpeg import run_loudness_probe


def finalize(tmp_path, monkeypatch, integrated, peak):
    artifact = tmp_path / "encoded.mp4"
    artifact.write_bytes(b"already rendered artifact")
    metrics = {"integrated_lufs": integrated, "true_peak_dbtp": peak, "lra_lu": 0.0}
    monkeypatch.setattr("moviestar.cli.normalize_loudness", lambda *a, **kw: {
        "pre": {"integrated_lufs": -20.0, "true_peak_dbtp": -8.0, "lra_lu": 0.0},
        "plan": {"gain_db": 6.0, "limited_by_true_peak": False, "reason": None},
        "measured": metrics,
        "apply_command": ["ffmpeg", "postpass"],
    })
    result = {"warnings": [{"code": "existing_warning"}], "warning_count": 1,
              "audio_loudness": {"final_mix": {"loudness": {}}}}
    _add_loudness_result(result, -14.0)
    _finalize_loudness_normalization("export", result, -14.0, str(artifact))
    assert artifact.read_bytes() == b"already rendered artifact"
    assert result["file_size_bytes"] == artifact.stat().st_size
    assert result["warning_count"] == len(result["warnings"])
    json.dumps(result, allow_nan=False)
    return result


@pytest.mark.parametrize("integrated,peak,lufs_met,peak_met", [
    (-14.0, -1.1, True, False),
    (-16.0, -1.8, False, True),
    (-14.0, -1.8, True, True),
    (-16.0, -1.1, False, False),
])
def test_loudness_and_peak_compliance_are_independent(
    tmp_path, monkeypatch, integrated, peak, lufs_met, peak_met
):
    result = finalize(tmp_path, monkeypatch, integrated, peak)
    env = result["loudness_normalization"]
    assert env["target_met"] is lufs_met  # preserve the existing LUFS contract
    assert env["true_peak_limit_met"] is peak_met
    assert env["true_peak_status"] == ("compliant" if peak_met else "exceeded")
    assert env["true_peak_tolerance_db"] == 0.1
    assert env["true_peak_overshoot_db"] == pytest.approx(max(0, peak + 1.5))
    assert env["measured"]["true_peak_dbtp"] == peak
    assert result["audio_loudness"]["final_mix"]["loudness"]["true_peak_dbtp"] == peak
    codes = [w["code"] for w in result["warnings"]]
    assert codes[0] == "existing_warning"
    assert ("loudness_target_missed" in codes) is not lufs_met
    assert ("true_peak_limit_exceeded" in codes) is not peak_met
    if not peak_met:
        warning = next(w for w in result["warnings"]
                       if w["code"] == "true_peak_limit_exceeded")
        assert warning["measured_true_peak_dbtp"] == peak
        assert warning["true_peak_limit_dbtp"] == -1.5
        assert warning["true_peak_tolerance_db"] == 0.1
        assert warning["overshoot_db"] == pytest.approx(peak + 1.5)


@pytest.mark.parametrize("peak,met", [
    (-1.5, True), (-1.4, True), (-1.399, False), (-2.0, True),
])
def test_peak_tolerance_boundary_uses_unrounded_measurement(tmp_path, monkeypatch, peak, met):
    env = finalize(tmp_path, monkeypatch, -14.0, peak)["loudness_normalization"]
    assert env["true_peak_limit_met"] is met


@pytest.mark.parametrize("peak", [None, float("nan"), float("inf"), -float("inf")])
def test_unmeasurable_peak_never_claims_compliance(tmp_path, monkeypatch, peak):
    result = finalize(tmp_path, monkeypatch, -14.0, peak)
    env = result["loudness_normalization"]
    assert env["target_met"] is True
    assert env["true_peak_limit_met"] is None
    assert env["true_peak_status"] == "unmeasurable"
    assert env["true_peak_overshoot_db"] is None
    assert env["measured"]["true_peak_dbtp"] is None
    codes = [w["code"] for w in result["warnings"]]
    assert "true_peak_unmeasurable" in codes
    assert "true_peak_limit_exceeded" not in codes


@pytest.mark.parametrize("integrated", [None, float("nan"), -float("inf")])
def test_unmeasurable_loudness_does_not_hide_valid_peak(tmp_path, monkeypatch, integrated):
    result = finalize(tmp_path, monkeypatch, integrated, -1.1)
    env = result["loudness_normalization"]
    assert env["target_met"] is False
    assert env["delta_lu"] is None
    assert env["measured"]["integrated_lufs"] is None
    assert env["true_peak_limit_met"] is False


def test_dry_run_does_not_claim_peak_was_measured():
    result = {}
    _add_loudness_result(result, -14.0, dry_run=True)
    env = result["loudness_normalization"]
    assert env["true_peak_status"] == "not_measured"
    assert env["true_peak_limit_met"] is None
    assert env["true_peak_overshoot_db"] is None
    assert env["measured"] is None
    assert "target_met" not in env
    assert "warnings" not in result


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("composition", [False, True], ids=["source", "scene"])
def test_export_peak_report_matches_finished_aac_artifact(
    tmp_path, loaded_project, composition
):
    source = tmp_path / "square.mp4"
    # Abrupt square-wave edges stress AAC round trips. Choose the target from
    # an unnormalized render's measured headroom so LUFS remains achievable.
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=black:s=160x120:r=30:d=3", "-f", "lavfi", "-i",
        "aevalsrc=0.5*sgn(sin(2*PI*330*t)):s=48000:d=3",
        "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac",
        "-b:a", "192k", "-shortest", str(source),
    ], check=True)
    loaded_project(source, frames=False)
    if composition:
        invoke("scenes", "set", "--canvas", "160x120", "--scene", "test=single",
               "--slot", "test:main=src_0", "--from", "0", "--to", "3")
    spec = tmp_path / "moviestar" / "spec.json"
    before = spec.read_bytes() if spec.exists() else None
    base = tmp_path / "base.mp4"
    invoke("export", "--out", str(base))
    pre, _ = run_loudness_probe(str(base))
    target = round(pre["integrated_lufs"] - 1.5 - pre["true_peak_dbtp"], 2)
    out = tmp_path / "normalized.mp4"
    result = invoke("export", "--out", str(out), "--loudness-target", str(target),
                    "--loudness-report")
    # Probe the delivered artifact again, independently of the JSON result.
    measured, _ = run_loudness_probe(str(out))
    assert abs(measured["integrated_lufs"] - target) <= 1.0
    peak_met = measured["true_peak_dbtp"] <= -1.4
    env = result["loudness_normalization"]
    assert env["target_met"] is True
    assert env["true_peak_limit_met"] is peak_met
    assert env["true_peak_status"] == ("compliant" if peak_met else "exceeded")
    assert env["measured"]["true_peak_dbtp"] == measured["true_peak_dbtp"]
    assert result["audio_loudness"]["final_mix"]["loudness"]["true_peak_dbtp"] == measured["true_peak_dbtp"]
    warnings = result.get("warnings", [])
    exceeded = [w for w in warnings if w["code"] == "true_peak_limit_exceeded"]
    assert bool(exceeded) is not peak_met
    if exceeded:
        assert exceeded[0]["measured_true_peak_dbtp"] == measured["true_peak_dbtp"]
    assert not any(w["code"] == "loudness_target_missed" for w in warnings)
    assert result["file_size_bytes"] == out.stat().st_size
    assert (spec.read_bytes() if spec.exists() else None) == before
    json.dumps(result, allow_nan=False)


def test_silent_encoded_audio_reports_unknown_peak(tmp_path, loaded_project):
    source = tmp_path / "silence.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=black:s=160x120:r=30:d=1", "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=mono", "-t", "1", "-c:v", "mpeg4",
        "-q:v", "5", "-c:a", "aac", str(source),
    ], check=True)
    loaded_project(source, frames=False)
    result = invoke("export", "--out", str(tmp_path / "silent-out.mp4"),
                    "--loudness-target", "-14")
    env = result["loudness_normalization"]
    assert env["target_met"] is False
    assert env["true_peak_limit_met"] is None
    assert env["true_peak_status"] == "unmeasurable"
    assert env["measured"]["true_peak_dbtp"] is None
    assert env["limiting_constraint"] == "input_unmeasurable"
    json.dumps(result, allow_nan=False)


def test_real_aac_roundtrip_warns_even_when_lufs_is_met(tmp_path):
    # Normalize an actual encoded artifact. Unlike a source export, this
    # isolates the final AAC pass from earlier, encoder-dependent generations.
    artifact = tmp_path / "square.m4a"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "aevalsrc=0.5*sgn(sin(2*PI*330*t)):s=48000:d=3",
        "-c:a", "aac", "-b:a", "192k", str(artifact),
    ], check=True)
    pre, _ = run_loudness_probe(str(artifact))
    target = round(pre["integrated_lufs"] - 1.5 - pre["true_peak_dbtp"], 2)
    result = {}
    _add_loudness_result(result, target)
    _finalize_loudness_normalization("export", result, target, str(artifact))
    measured, _ = run_loudness_probe(str(artifact))
    assert abs(measured["integrated_lufs"] - target) <= 1.0
    assert measured["true_peak_dbtp"] > -1.4
    env = result["loudness_normalization"]
    assert env["target_met"] is True
    assert env["true_peak_limit_met"] is False
    assert env["true_peak_status"] == "exceeded"
    assert env["measured"]["true_peak_dbtp"] == measured["true_peak_dbtp"]
    warning = next(w for w in result["warnings"] if w["code"] == "true_peak_limit_exceeded")
    assert warning["measured_true_peak_dbtp"] == measured["true_peak_dbtp"]
    assert result["file_size_bytes"] == artifact.stat().st_size
