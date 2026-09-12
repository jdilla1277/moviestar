"""Issue #489: deterministic visual-activity evidence for silent video."""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.activity import _active_ranges
from moviestar.cli import cli
from moviestar.timecodes import format_timecode
from tests.conftest import assert_error_envelope, assert_no_project_envelope


@pytest.fixture
def activity_video(tmp_path) -> str:
    """Four seconds: idle black, changing test pattern, idle black."""
    path = tmp_path / "activity.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=black:duration=1:size=160x120:rate=10",
            "-f", "lavfi", "-i", "testsrc2=duration=2:size=160x120:rate=10",
            "-f", "lavfi", "-i", "color=black:duration=1:size=160x120:rate=10",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
            "-an", str(path),
        ],
        check=True,
    )
    return str(path)


def _invoke_json(runner: CliRunner, args: list[str]):
    result = runner.invoke(cli, args)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"not JSON (exit {result.exit_code}):\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
    return result, data


class TestActivitySurface:
    def test_help_teaches_evidence_settings_and_clocks(self):
        result = CliRunner().invoke(cli, ["activity", "--help"])

        assert result.exit_code == 0, result.stdout
        for token in (
            "--from", "--to", "--interval", "--threshold",
            "--merge-gap", "--dry-run", "--source",
            "result-time", "source-time", "active_ranges", "idle_ranges",
        ):
            assert token in result.stdout

    def test_no_project_uses_the_canonical_error_envelope(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result, data = _invoke_json(CliRunner(), ["activity"])

        assert result.exit_code == 1
        assert_no_project_envelope(data, command="activity")

    def test_dry_run_reports_exact_cost_without_decoding(
        self, loaded_project, activity_video, monkeypatch
    ):
        loaded_project(activity_video, frames=False)

        def fail_decode(*_args, **_kwargs):
            pytest.fail("dry-run must not decode or probe frames")

        monkeypatch.setattr("moviestar.cli.analyze_visual_activity", fail_decode)
        result, data = _invoke_json(
            CliRunner(),
            [
                "activity", "--from", "0.5", "--to", "3.5",
                "--interval", "0.25", "--threshold", "0.03", "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert data["dry_run"] is True
        assert data["status"] == "would_analyze"
        assert data["would_analyze_frames_count"] == 12
        assert data["would_compare_frame_pairs_count"] == 11
        assert data["range"]["clock"] == "result"
        assert data["range"]["from"]["seconds"] == 0.5
        assert data["range"]["to"]["seconds"] == 3.5
        assert data["settings"]["interval_seconds"] == 0.25
        assert data["settings"]["active_threshold"] == 0.03
        assert data["settings"]["metric"] == "mean_absolute_luma_difference"
        assert "samples" not in data
        assert "active_ranges" not in data

    def test_invalid_range_and_settings_are_actionable(self, loaded_project, activity_video):
        loaded_project(activity_video, frames=False)
        runner = CliRunner()

        for args in (
            ["activity", "--from", "3", "--to", "2"],
            ["activity", "--interval", "0"],
            ["activity", "--interval", "nan"],
            ["activity", "--threshold", "1.1"],
            ["activity", "--threshold", "nan"],
            ["activity", "--merge-gap", "-1"],
            ["activity", "--merge-gap", "inf"],
            ["activity", "--from", "nan"],
        ):
            result, data = _invoke_json(runner, args)
            assert result.exit_code != 0
            assert_error_envelope(data, command="activity")

    def test_transcriptless_skim_routes_to_visual_activity(
        self, loaded_project, activity_video
    ):
        loaded_project(activity_video)

        result, data = _invoke_json(CliRunner(), ["skim"])

        assert result.exit_code == 0, result.stdout
        assert data["transcript"] is None
        assert "moviestar activity" in data["hint"]

        inspect_result, inspect_data = _invoke_json(
            CliRunner(),
            [
                "inspect", "--from", "0", "--to", "1",
                "--interval", "0.25", "--dry-run",
            ],
        )
        assert inspect_result.exit_code == 0, inspect_result.stdout
        assert inspect_data["transcript"] is None
        assert "moviestar activity" in inspect_data["hint"]


class TestActivityAnalysis:
    def test_range_coalescing_pins_gap_boundary_and_evidence(self):
        samples = []
        for index, (seconds, classification, score) in enumerate((
            (0.0, "baseline", None),
            (0.5, "active", 0.05),
            (1.0, "idle", 0.0),
            (1.5, "active", 0.05),
            (2.0, "idle", 0.0),
        )):
            samples.append({
                "index": index,
                "clock": {
                    "result": format_timecode(seconds),
                    "source": format_timecode(seconds),
                },
                "classification": classification,
                "visual_change": (
                    None if score is None else {
                        "mean_absolute_luma_difference": score,
                        "changed_pixel_ratio": score,
                    }
                ),
                "frame_state": "baseline" if index == 0 else "new",
            })

        split = _active_ranges(samples, threshold=0.02, merge_gap=0.499)
        merged = _active_ranges(samples, threshold=0.02, merge_gap=0.5)

        assert len(split) == 2
        assert len(merged) == 1
        assert merged[0]["from"]["seconds"] == 0.0
        assert merged[0]["to"]["seconds"] == 1.5
        assert merged[0]["evidence"]["bridged_idle_seconds"] == 0.5
        assert merged[0]["evidence"]["sample_indexes"] == [1, 3]

    def test_reports_ordered_samples_and_known_active_idle_windows(
        self, loaded_project, activity_video
    ):
        loaded_project(activity_video, frames=False)
        result, data = _invoke_json(
            CliRunner(),
            [
                "activity", "--from", "0", "--to", "4",
                "--interval", "0.5", "--threshold", "0.01",
                "--merge-gap", "0.5",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert data["status"] == "analyzed"
        assert data["source"]["id"] == "src_0"
        assert data["summary"]["sample_count"] == 8
        assert data["summary"]["comparison_count"] == 7
        assert [s["index"] for s in data["samples"]] == list(range(8))
        assert [
            s["clock"]["result"]["seconds"] for s in data["samples"]
        ] == sorted(s["clock"]["result"]["seconds"] for s in data["samples"])

        baseline = data["samples"][0]
        assert baseline["classification"] == "baseline"
        assert baseline["visual_change"] is None
        for sample in data["samples"]:
            assert sample["clock"]["source"]["seconds"] == sample["clock"]["result"]["seconds"]
            assert sample["decoded_frame"]["source_time"] is not None
            assert sample["frame_state"] in {
                "baseline", "new", "held", "decoded_unchanged", "unavailable"
            }

        active = data["active_ranges"]
        assert len(active) == 1, active
        assert active[0]["from"]["seconds"] == pytest.approx(0.5, abs=0.01)
        assert active[0]["to"]["seconds"] == pytest.approx(3.0, abs=0.01)
        assert active[0]["confidence"] > 0
        assert active[0]["evidence"]["active_sample_count"] >= 4
        assert active[0]["evidence"]["max_change"] >= data["settings"]["active_threshold"]

        idle = data["idle_ranges"]
        assert [(r["from"]["seconds"], r["to"]["seconds"]) for r in idle] == [
            (0.0, 0.5), (3.0, 4.0)
        ]
        assert all("confidence" in item and "evidence" in item for item in idle)
        assert data["summary"]["active_duration_seconds"] == pytest.approx(2.5)
        assert data["summary"]["idle_duration_seconds"] == pytest.approx(1.5)

    def test_vfr_void_marks_held_samples_and_no_newly_decoded_ranges(
        self, loaded_project, vfr_gap_video
    ):
        loaded_project(vfr_gap_video, frames=False)
        result, data = _invoke_json(
            CliRunner(),
            [
                "activity", "--from", "2", "--to", "7",
                "--interval", "1", "--threshold", "0.01",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert data["active_ranges"] == []
        assert data["summary"]["held_sample_count"] >= 4
        assert data["summary"]["no_new_frame_range_count"] == 1
        held = data["no_new_frame_ranges"][0]
        assert held["from"]["seconds"] <= 2.0
        assert held["to"]["seconds"] >= 6.0
        assert held["evidence"]["held_sample_count"] >= 4
        decoded_times = {
            sample["decoded_frame"]["source_time"]["seconds"]
            for sample in data["samples"]
            if sample["decoded_frame"]["source_time"] is not None
        }
        assert len(decoded_times) == 1

    def test_result_clock_maps_across_an_edit_seam(
        self, loaded_project, activity_video
    ):
        loaded_project(activity_video, frames=False)
        runner = CliRunner()
        cut = runner.invoke(cli, ["cut", "--from", "1", "--to", "2"])
        assert cut.exit_code == 0, cut.stdout

        result, data = _invoke_json(
            runner,
            [
                "activity", "--from", "0", "--to", "3",
                "--interval", "0.5", "--threshold", "0.01",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert data["range"]["clock"] == "result"
        assert data["source_segments"] == [
            {
                "result_range": {"from": {"text": "0:00:00.000", "seconds": 0.0}, "to": {"text": "0:00:01.000", "seconds": 1.0}, "duration": {"text": "0:00:01.000", "seconds": 1.0}},
                "source_range": {"from": {"text": "0:00:00.000", "seconds": 0.0}, "to": {"text": "0:00:01.000", "seconds": 1.0}, "duration": {"text": "0:00:01.000", "seconds": 1.0}},
            },
            {
                "result_range": {"from": {"text": "0:00:01.000", "seconds": 1.0}, "to": {"text": "0:00:03.000", "seconds": 3.0}, "duration": {"text": "0:00:02.000", "seconds": 2.0}},
                "source_range": {"from": {"text": "0:00:02.000", "seconds": 2.0}, "to": {"text": "0:00:04.000", "seconds": 4.0}, "duration": {"text": "0:00:02.000", "seconds": 2.0}},
            },
        ]
        sample_at_seam = next(
            sample for sample in data["samples"]
            if sample["clock"]["result"]["seconds"] == 1.0
        )
        assert sample_at_seam["clock"]["source"]["seconds"] == 2.0


class TestActivityUnsupportedMedia:
    def test_source_without_video_metadata_is_a_stable_error(
        self, loaded_project, activity_video, monkeypatch
    ):
        loaded_project(activity_video, frames=False)
        from moviestar import cli as cli_module

        project = cli_module.load_project()
        project["sources"][0]["width"] = None
        project["sources"][0]["height"] = None
        monkeypatch.setattr("moviestar.cli.load_project", lambda: project)

        result, data = _invoke_json(CliRunner(), ["activity"])

        assert result.exit_code == 1
        assert_error_envelope(data, command="activity")
        assert data["code"] == "activity_no_video_stream"
        assert "video" in data["error"].lower()
