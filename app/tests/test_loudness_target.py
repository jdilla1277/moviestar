"""Issue #352 — --loudness-target must hit the requested integrated loudness.

Field report: exports using --loudness-target finished 2-4 LU below the
requested target. Root cause: the render paths applied single-pass ffmpeg
loudnorm, which runs in *dynamic* mode — on dynamic content it misses the
integrated target by LUs in either direction and can overshoot the
true-peak ceiling (measured +2.1 dBTP against a -1.5 limit on the loud
fixture below) while the envelope silently claimed success.

The contract under test: a measured linear-gain post-pass on the rendered
artifact.

- Achievable target -> rendered integrated loudness within
  ``tolerance_lu`` of the request, true peak at or under the ceiling.
- Unachievable target (true-peak-limited) -> honest envelope: measured
  result, delta, limiting constraint, and a structured warning.
- Dry-run reports the requested target but marks achieved loudness as
  unknown until rendered.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import plan_loudness_normalization, run_loudness_probe

TOLERANCE_LU = 1.0
TRUE_PEAK_LIMIT_DBTP = -1.5
# AAC encoding can overshoot true peak slightly; assert against the
# ceiling plus a small codec margin, not the exact limit.
TRUE_PEAK_ASSERT_DBTP = -1.0


@pytest.fixture
def runner():
    return CliRunner()


def _invoke_json(runner: CliRunner, args: list[str]):
    result = runner.invoke(cli, args)
    data = json.loads(result.stdout)
    return result, data


def _measure(path: str) -> dict:
    metrics, _ = run_loudness_probe(str(path))
    return metrics


@pytest.fixture(scope="session")
def burst_video(tmp_path_factory) -> str:
    """10s sine-burst clip: speech-like loudness range, low crest factor.

    2s tone bursts (amp .25) over 3s near-silence (amp .025) measure
    ~-34.5 LUFS integrated. True peak is encoder-dependent: the source
    measures ~-25.6 dBTP, but a second AAC generation (what export
    produces) can bloom to ~-21 dBTP on some FFmpeg builds (#514
    measured 9.0.1 at -21.1 where 6.x export chains gave -25.7, while
    plain 6.x re-encodes bloomed too - second-generation AAC true peak
    on tonal bursts swings ~5 dB between builds). Achievability
    assertions must leave margin for that swing.
    """
    path = tmp_path_factory.mktemp("fixtures") / "burst.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=10:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=10",
            "-filter_complex",
            "[1:a]volume='if(lt(mod(t,5),2),0.25,0.025)':eval=frame[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.fixture(scope="session")
def loud_noise_video(tmp_path_factory) -> str:
    """8s steady loud pink noise (fixed seed): the undershoot repro.

    Measures ~-15.7 LUFS integrated with ~-2.4 dBTP true peak. A -5
    target needs +10.7 dB but only ~0.9 dB of true-peak headroom exists,
    so the target is physically unachievable under the -1.5 dBTP
    ceiling. The old single-pass loudnorm rendered this at ~-13.7 LUFS
    (8.7 LU under the request) with a +2.1 dBTP true peak while the
    envelope claimed the target was applied.
    """
    path = tmp_path_factory.mktemp("fixtures") / "loud_noise.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=8:size=320x240:rate=30",
            "-f", "lavfi",
            "-i", "anoisesrc=color=pink:duration=8:amplitude=0.9:seed=352",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.fixture(scope="session")
def quiet_music_wav(tmp_path_factory) -> str:
    """10s quiet pink-noise bed (fixed seed) for multi-track mixes."""
    path = tmp_path_factory.mktemp("fixtures") / "quiet_music.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "anoisesrc=color=pink:duration=10:amplitude=0.03:seed=353",
            str(path),
        ],
        check=True,
    )
    return str(path)


class TestLoudnessTargetAccuracy:
    def test_source_export_meets_achievable_target(
        self, runner, loaded_project, burst_video, tmp_path
    ):
        loaded_project(burst_video, frames=False)
        out = tmp_path / "normalized.mp4"
        # -18 rather than -14: the export's second AAC generation can
        # bloom this fixture's true peak by ~5 dB on some FFmpeg builds
        # (see the fixture docstring), and a -14 target leaves no margin
        # for that swing - the gain plan would brush the -1.5 dBTP
        # ceiling and flag a cap on a scenario this test defines as
        # comfortably achievable. -18 keeps ~2.4 dB of margin on the
        # worst observed build while exercising the same contract.
        result, data = _invoke_json(
            runner,
            ["export", "--out", str(out), "--loudness-target", "-18"],
        )
        assert result.exit_code == 0, result.output

        measured = _measure(out)
        assert measured["integrated_lufs"] == pytest.approx(
            -18.0, abs=TOLERANCE_LU
        )
        assert measured["true_peak_dbtp"] <= TRUE_PEAK_ASSERT_DBTP

        env = data["loudness_normalization"]
        assert env["target_lufs"] == -18.0
        assert env["tolerance_lu"] == TOLERANCE_LU
        assert env["target_met"] is True
        assert env["gain_limited_by_true_peak"] is False
        assert env["measured"]["integrated_lufs"] == pytest.approx(
            measured["integrated_lufs"], abs=0.2
        )
        assert abs(env["delta_lu"]) <= TOLERANCE_LU
        assert "--loudness" in env["hint"]
        assert not any(
            w["code"] == "loudness_target_missed"
            for w in data.get("warnings", [])
        )

    def test_unachievable_target_reports_measured_delta_and_warning(
        self, runner, loaded_project, loud_noise_video, tmp_path
    ):
        loaded_project(loud_noise_video, frames=False)
        out = tmp_path / "too_loud.mp4"
        result, data = _invoke_json(
            runner,
            ["export", "--out", str(out), "--loudness-target", "-5"],
        )
        assert result.exit_code == 0, result.output

        measured = _measure(out)
        # The true-peak ceiling must hold even though the target can't.
        assert measured["true_peak_dbtp"] <= TRUE_PEAK_ASSERT_DBTP

        env = data["loudness_normalization"]
        assert env["target_lufs"] == -5.0
        assert env["target_met"] is False
        assert env["gain_limited_by_true_peak"] is True
        assert env["limiting_constraint"] == "true_peak_limit"
        # Material miss, honestly reported: measured tracks the artifact
        # and the delta is the real shortfall, not a rounding error.
        assert env["measured"]["integrated_lufs"] == pytest.approx(
            measured["integrated_lufs"], abs=0.2
        )
        assert env["delta_lu"] == pytest.approx(
            measured["integrated_lufs"] - (-5.0), abs=0.2
        )
        assert env["delta_lu"] < -2.0

        # A capped gain lands the artifact at the ceiling, so the
        # reported max achievable target should match what was measured
        # — an agent can retarget from this field without arithmetic.
        assert env["max_achievable_lufs"] == pytest.approx(
            measured["integrated_lufs"], abs=0.5
        )

        missed = [
            w for w in data["warnings"]
            if w["code"] == "loudness_target_missed"
        ]
        assert len(missed) == 1
        assert missed[0]["target_lufs"] == -5.0
        assert missed[0]["limiting_constraint"] == "true_peak_limit"
        assert missed[0]["max_achievable_lufs"] == env["max_achievable_lufs"]
        assert missed[0]["measured_lufs"] == pytest.approx(
            measured["integrated_lufs"], abs=0.2
        )

    def test_ducked_multitrack_mix_meets_achievable_target(
        self, runner, loaded_project, burst_video, quiet_music_wav, tmp_path
    ):
        loaded_project(burst_video, frames=False)
        added = runner.invoke(
            cli,
            [
                "audio", "add", quiet_music_wav, "--as", "music",
                "--kind", "music", "--duck-under", "source",
            ],
        )
        assert added.exit_code == 0, added.output

        out = tmp_path / "mixed_normalized.mp4"
        result, data = _invoke_json(
            runner,
            ["export", "--out", str(out), "--loudness-target", "-16"],
        )
        assert result.exit_code == 0, result.output
        assert data["audio_mix_applied_to_ffmpeg_command"] is True

        measured = _measure(out)
        assert measured["integrated_lufs"] == pytest.approx(
            -16.0, abs=TOLERANCE_LU
        )
        assert measured["true_peak_dbtp"] <= TRUE_PEAK_ASSERT_DBTP

        env = data["loudness_normalization"]
        assert env["target_met"] is True
        # Normalization measures the finished mix (post ducking/limiting),
        # not the base render, so it runs as its own stage after the mix
        # remux rather than inside the mix filtergraph.
        mix_graph = data["audio_mix_command"][
            data["audio_mix_command"].index("-filter_complex") + 1
        ]
        assert "loudnorm" not in mix_graph

    def test_dry_run_distinguishes_target_from_unmeasured_result(
        self, runner, loaded_project, burst_video
    ):
        loaded_project(burst_video, frames=False)
        result, data = _invoke_json(
            runner,
            ["export", "--dry-run", "--loudness-target", "-14"],
        )
        assert result.exit_code == 0, result.output

        env = data["loudness_normalization"]
        assert env["target_lufs"] == -14.0
        assert env["measured"] is None
        assert "target_met" not in env
        # The render command no longer carries a single-pass loudnorm;
        # normalization is a measured post-pass a dry run cannot predict.
        assert "loudnorm" not in " ".join(data["ffmpeg_command"])


class TestLoudnessNormalizationPlan:
    def test_exact_gain_when_headroom_is_ample(self):
        plan = plan_loudness_normalization(
            target_lufs=-14.0, measured_i=-20.0, measured_tp=-10.0
        )
        assert plan["gain_db"] == pytest.approx(6.0)
        assert plan["limited_by_true_peak"] is False
        assert plan["predicted_lufs"] == pytest.approx(-14.0)

    def test_gain_caps_at_true_peak_headroom(self):
        plan = plan_loudness_normalization(
            target_lufs=-5.0, measured_i=-15.69, measured_tp=-2.43
        )
        assert plan["gain_db"] == pytest.approx(0.93)
        assert plan["limited_by_true_peak"] is True
        assert plan["predicted_lufs"] == pytest.approx(-14.76)

    def test_downward_gain_is_never_limited(self):
        plan = plan_loudness_normalization(
            target_lufs=-20.0, measured_i=-14.0, measured_tp=-1.0
        )
        assert plan["gain_db"] == pytest.approx(-6.0)
        assert plan["limited_by_true_peak"] is False

    def test_unmeasurable_input_yields_no_gain(self):
        for measured_i in (None, -70.0):
            plan = plan_loudness_normalization(
                target_lufs=-14.0, measured_i=measured_i, measured_tp=None
            )
            assert plan["gain_db"] is None
            assert plan["limited_by_true_peak"] is False
