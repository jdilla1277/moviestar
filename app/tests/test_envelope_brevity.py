"""#499 — static scene envelopes stop burying geometry in camera dumps.

A slot with no authored camera used to echo a ~24-line from/to
camera-state rect dump per slot in every scenes set, screenshot, and
export envelope. Idle states now collapse to {"target": "full"}; slots
with real camera moves keep the full echo.
"""

import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from tests.test_m26_shape_surface import _pip_project


@pytest.fixture
def runner():
    return CliRunner()


IDLE = {"target": "full"}


class TestStaticEnvelopesAreBrief:
    def test_scenes_set_collapses_idle_camera_states(
        self, runner, loaded_project, test_video
    ):
        loaded_project(
            [test_video, test_video],
            names=["screen", "speaker"],
            frames=False,
        )
        result = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "demo=picture-in-picture",
                "--slot", "demo:main=screen", "--from", "0", "--to", "1",
                "--slot", "demo:inset=speaker", "--from", "0", "--to", "1",
                "--audio-from", "demo=screen",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert "rect_pixels" not in result.stdout
        assert "eased_progress" not in result.stdout

    def test_screenshot_reports_idle_camera_compactly(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli, ["screenshot", "--at", "0.5", "--dry-run"]
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        for slot in data["active_scene"]["slots"]:
            assert slot["camera"] == IDLE

    def test_export_dry_run_collapses_idle_camera_states(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli, ["export", "--dry-run", "--out", "o.mp4"]
        )

        assert result.exit_code == 0, result.stdout
        assert "rect_pixels" not in result.stdout
        data = json.loads(result.stdout)
        window = data["composition"][0]
        assert window["camera_states"] == {"main": IDLE, "inset": IDLE}

    def test_authored_camera_keeps_the_full_echo(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        motion_file = tmp_path / "motion.json"
        motion_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "scenes": [
                        {
                            "scene": "demo",
                            "slots": [
                                {
                                    "slot": "inset",
                                    "pacing": [],
                                    "camera": [
                                        {
                                            "id": "closeup",
                                            "range": {
                                                "from": "0.1",
                                                "to": "0.3",
                                                "space": "result-local",
                                            },
                                            "to": {
                                                "target": {
                                                    "space": "source",
                                                    "units": "pixels",
                                                    "rect": {
                                                        "x": 40, "y": 40,
                                                        "w": 160, "h": 160,
                                                    },
                                                }
                                            },
                                            "ease": "in-out",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            )
        )
        set_result = runner.invoke(
            cli, ["scenes", "motion", "set", str(motion_file)]
        )
        assert set_result.exit_code == 0, set_result.stdout

        result = runner.invoke(
            cli, ["screenshot", "--at", "0.5", "--dry-run"]
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        inset = next(
            slot
            for slot in data["active_scene"]["slots"]
            if slot["slot"] == "inset"
        )
        assert inset["camera"]["target"] != "full"
        assert "rect_pixels" in inset["camera"]
