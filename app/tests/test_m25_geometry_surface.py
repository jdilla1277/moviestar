"""M25 slot-geometry authoring surface."""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from tests.conftest import assert_error_envelope, assert_no_project_envelope


@pytest.fixture
def runner():
    return CliRunner()


def _pip_project(runner, loaded_project, test_video, *, duration=1):
    loaded_project(
        [test_video, test_video],
        names=["screen", "speaker"],
        frames=False,
    )
    result = runner.invoke(
        cli,
        [
            "scenes",
            "set",
            "--canvas",
            "short",
            "--scene",
            "demo=picture-in-picture",
            "--slot",
            "demo:main=screen",
            "--from",
            "0",
            "--to",
            str(duration),
            "--slot",
            "demo:inset=speaker",
            "--from",
            "0",
            "--to",
            str(duration),
            "--audio-from",
            "demo=screen",
        ],
    )
    assert result.exit_code == 0, result.stdout
    # M26 materializes a default circular shape at authoring time; these
    # tests pin the M25 geometry surface against a preset-baseline scene,
    # so model a pre-default spec by stripping the materialized block.
    spec_path = Path("moviestar/spec.json")
    spec = json.loads(spec_path.read_text())
    for scene in spec["composition"]:
        scene["layout"].pop("geometry", None)
    spec_path.write_text(json.dumps(spec, indent=2))


class TestM25GeometryDiscoverySurface:
    def test_scenes_help_exposes_geometry_command(self, runner):
        result = runner.invoke(cli, ["scenes", "--help"])

        assert result.exit_code == 0
        compact = " ".join(result.output.split())
        assert (
            "geometry Set a scene slot's size, placement, shape, framing, and"
            in compact
        )

    def test_geometry_help_documents_complete_vocabulary(self, runner):
        result = runner.invoke(cli, ["scenes", "geometry", "--help"])

        assert result.exit_code == 0
        compact = " ".join(result.output.split())
        for token in (
            "SCENE:SLOT",
            "--size",
            "small",
            "medium",
            "large",
            "--at",
            "--x",
            "--y",
            "--margin",
            "--motion",
            "bounce",
            "--speed",
            "--reset",
            "--dry-run",
        ):
            assert token in compact
        assert "Default: 4% of the shorter canvas dimension" in compact
        assert "4%%" not in compact

    def test_root_and_camera_motion_help_cross_link_output_geometry(self, runner):
        root_result = runner.invoke(cli, ["--help"])
        motion_result = runner.invoke(cli, ["scenes", "motion", "--help"])
        inset_result = runner.invoke(cli, ["scenes", "inset", "--help"])

        assert root_result.exit_code == 0
        assert "scenes geometry" in root_result.output
        assert motion_result.exit_code == 0
        assert "scenes geometry SCENE:SLOT" in " ".join(
            motion_result.output.split()
        )
        assert inset_result.exit_code == 0
        assert "scenes geometry SCENE:SLOT" in " ".join(
            inset_result.output.split()
        )

    def test_bare_geometry_describes_adjust_inline_and_preview_paths(self, runner):
        result = runner.invoke(cli, ["scenes", "geometry"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "slot_geometry_authoring"
        assert data["writes_spec"] is False
        assert "mocked_surface_note" not in data
        commands = json.dumps(data["commands"])
        assert "scenes geometry demo:inset" in commands
        assert "scenes set scenes.json --dry-run" in commands
        assert "layouts preview" in commands
        scene_file_command = next(
            item["command"]
            for item in data["commands"]
            if "scenes set scenes.json" in item["command"]
        )
        assert "--slot-size" not in scene_file_command
        assert "SCENE:SLOT" in data["addressing"]


class TestM25SceneFileGeometry:
    _GEOMETRY = {
        "size": 0.22,
        "anchor": "top-left",
        "margin_x": 64,
        "margin_y": 64,
    }

    def _scene_file(self, tmp_path, *, geometry="__unset__"):
        inset = {
            "slot": "inset",
            "source": "speaker",
            "from": 0,
            "to": 1,
        }
        if geometry != "__unset__":
            inset["geometry"] = geometry
        path = tmp_path / "scenes.json"
        path.write_text(
            json.dumps(
                {
                    "canvas": "short",
                    "scenes": [
                        {
                            "name": "demo",
                            "layout": "picture-in-picture",
                            "slots": [
                                {
                                    "slot": "main",
                                    "source": "screen",
                                    "from": 0,
                                    "to": 1,
                                },
                                inset,
                            ],
                            "audio_from": "screen",
                        }
                    ],
                }
            )
        )
        return path

    def test_scene_file_geometry_writes_canonical_layout_override(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(
            [test_video, test_video], names=["screen", "speaker"], frames=False
        )
        scene_file = self._scene_file(tmp_path, geometry=self._GEOMETRY)

        result = runner.invoke(cli, ["scenes", "set", str(scene_file)])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["geometry_preserved"] == 0
        assert data["scenes"][0]["layout"]["geometry"] == {
            "inset": self._GEOMETRY
        }
        assert data["scenes"][0]["layout"]["regions"]["inset"] == {
            "x": 64,
            "y": 64,
            "width": 238,
            "height": 238,
        }
        spec = json.loads((tmp_path / "moviestar" / "spec.json").read_text())
        assert spec["composition"][0]["layout"]["geometry"] == {
            "inset": self._GEOMETRY
        }

    def test_scene_file_without_geometry_round_trips_stored_override(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(
            [test_video, test_video], names=["screen", "speaker"], frames=False
        )
        with_geometry = self._scene_file(tmp_path, geometry=self._GEOMETRY)
        first = runner.invoke(cli, ["scenes", "set", str(with_geometry)])
        assert first.exit_code == 0, first.stdout

        without_geometry = self._scene_file(tmp_path)
        second = runner.invoke(cli, ["scenes", "set", str(without_geometry)])

        assert second.exit_code == 0, second.stdout
        data = json.loads(second.stdout)
        assert data["geometry_preserved"] == 1
        spec = json.loads((tmp_path / "moviestar" / "spec.json").read_text())
        assert spec["composition"][0]["layout"]["geometry"] == {
            "inset": self._GEOMETRY
        }

    def test_timing_change_dry_run_reports_preserved_geometry(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(
            [test_video, test_video], names=["screen", "speaker"], frames=False
        )
        timing_change = self._scene_file(tmp_path, geometry=self._GEOMETRY)
        document = json.loads(timing_change.read_text())
        second_scene = json.loads(json.dumps(document["scenes"][0]))
        second_scene["name"] = "outro"
        document["scenes"].append(second_scene)
        timing_change.write_text(json.dumps(document))
        first = runner.invoke(
            cli,
            ["scenes", "set", str(timing_change)],
        )
        assert first.exit_code == 0, first.stdout
        spec_path = tmp_path / "moviestar" / "spec.json"
        before = spec_path.read_text()

        for scene in document["scenes"]:
            for slot in scene["slots"]:
                slot["from"] = 0.1
                slot["to"] = 0.9
                slot.pop("geometry", None)
        timing_change.write_text(json.dumps(document))

        result = runner.invoke(
            cli, ["scenes", "set", str(timing_change), "--dry-run"]
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["geometry_preserved"] == 2
        for scene in data["scenes"]:
            assert scene["layout"]["geometry"] == {"inset": self._GEOMETRY}
        assert spec_path.read_text() == before

    def test_scene_file_null_geometry_clears_stored_override(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(
            [test_video, test_video], names=["screen", "speaker"], frames=False
        )
        first = runner.invoke(
            cli,
            ["scenes", "set", str(self._scene_file(tmp_path, geometry=self._GEOMETRY))],
        )
        assert first.exit_code == 0, first.stdout

        cleared = runner.invoke(
            cli, ["scenes", "set", str(self._scene_file(tmp_path, geometry=None))]
        )

        assert cleared.exit_code == 0, cleared.stdout
        data = json.loads(cleared.stdout)
        assert data["geometry_preserved"] == 0
        spec = json.loads((tmp_path / "moviestar" / "spec.json").read_text())
        assert "geometry" not in spec["composition"][0]["layout"]

    def test_scene_file_invalid_geometry_reports_the_slot_json_path(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(
            [test_video, test_video], names=["screen", "speaker"], frames=False
        )
        scene_file = self._scene_file(tmp_path, geometry={"size": 2})

        result = runner.invoke(
            cli, ["scenes", "set", str(scene_file), "--dry-run"]
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["errors"][0]["path"] == "scenes[0].slots[1].geometry.size"


class TestM25LayoutPreviewGeometry:
    def test_preview_help_exposes_slot_geometry_vocabulary(self, runner):
        result = runner.invoke(cli, ["layouts", "preview", "--help"])

        assert result.exit_code == 0
        assert "--slot-size" in result.output
        assert "--slot-at" in result.output
        assert "--slot-margin" in result.output
        assert "inset=small" in result.output
        assert "inset=top-left" in result.output
        assert "inset=64" in result.output

    def test_preview_resolves_geometry_and_draws_the_authored_region(
        self, runner, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            cli,
            [
                "layouts", "preview", "picture-in-picture",
                "--canvas", "short",
                "--slot-size", "inset=small",
                "--slot-at", "inset=top-left",
                "--slot-margin", "inset=64",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["layout"]["geometry"] == {
            "inset": {
                "size": 0.22,
                "anchor": "top-left",
                "margin_x": 64,
                "margin_y": 64,
            }
        }
        assert data["layout"]["regions"]["inset"] == {
            "x": 64,
            "y": 64,
            "width": 238,
            "height": 238,
        }
        assert "bottom-right" not in data["layout"]["description"]
        svg = Path(data["out"]).read_text()
        assert 'x="64" y="64" width="238" height="238"' in svg

    @pytest.mark.parametrize(
        "args",
        [
            ["single", "--slot-size", "inset=small"],
            ["single", "--slot-margin", "inset=64"],
            ["picture-in-picture", "--slot-size", "main=small"],
            ["picture-in-picture", "--slot-at", "inset=diagonal"],
            ["picture-in-picture", "--slot-margin", "main=64"],
        ],
    )
    def test_preview_rejects_geometry_outside_the_pip_inset(self, runner, args):
        result = runner.invoke(cli, ["layouts", "preview", *args])

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["command"] == "layouts"


class TestM25GeometryCommandSurface:

    def test_geometry_requires_project(self, runner, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            cli, ["scenes", "geometry", "demo:inset", "--size", "small"]
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_no_project_envelope(data, command="scenes geometry")

    def test_static_geometry_resolves_named_size_and_anchor_and_writes(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        spec_path = tmp_path / "moviestar" / "spec.json"

        result = runner.invoke(
            cli,
            [
                "scenes",
                "geometry",
                "demo:inset",
                "--size",
                "small",
                "--at",
                "top-left",
                "--margin",
                "64",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "set_slot_geometry"
        assert data["writes_spec"] is True
        assert "mocked_surface_note" not in data
        assert data["target"] == {"scene": "demo", "slot": "inset"}
        assert data["geometry"]["size"] == {
            "value": "small",
            "fraction": 0.22,
            "width": 238,
            "height": 238,
        }
        assert data["geometry"]["placement"] == {
            "mode": "anchor",
            "anchor": "top-left",
            "margin_x": 64,
            "margin_y": 64,
        }
        assert data["region"] == {
            "x": 64,
            "y": 64,
            "width": 238,
            "height": 238,
        }
        assert data["region_is"] == "instant"
        assert data["camera_changes"] == []
        stored = json.loads(spec_path.read_text())
        assert stored["composition"][0]["layout"]["geometry"] == {
            "inset": {
                "size": 0.22,
                "anchor": "top-left",
                "margin_x": 64,
                "margin_y": 64,
            }
        }

    def test_dry_run_resolves_without_writing(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        spec_path = tmp_path / "moviestar" / "spec.json"
        before = spec_path.read_bytes()

        result = runner.invoke(
            cli,
            [
                "scenes", "geometry", "demo:inset",
                "--size", "small", "--at", "top-left", "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "would_set_slot_geometry"
        assert data["dry_run"] is True
        assert data["writes_spec"] is False
        assert spec_path.read_bytes() == before

    def test_custom_size_and_normalized_position_are_resolved(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            [
                "scenes",
                "geometry",
                "demo:inset",
                "--size",
                "0.28",
                "--x",
                "0.10",
                "--y",
                "0.20",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["geometry"]["size"]["value"] == "custom"
        assert data["geometry"]["size"]["fraction"] == 0.28
        assert data["geometry"]["placement"] == {
            "mode": "normalized",
            "x": 0.1,
            "y": 0.2,
        }
        assert data["region"] == {
            "x": 108,
            "y": 384,
            "width": 302,
            "height": 302,
        }

    def test_bounce_reports_envelope_and_named_speed(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            [
                "scenes",
                "geometry",
                "demo:inset",
                "--motion",
                "bounce",
                "--speed",
                "medium",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["geometry"]["motion"] == {
            "preset": "bounce",
            "speed": "medium",
        }
        assert data["region_is"] == "envelope"
        assert data["region"] == {"x": 0, "y": 0, "width": 1080, "height": 1920}
        assert "result time" in data["motion_note"]

    def test_bounce_only_update_preserves_stored_size_and_placement(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        first = runner.invoke(
            cli,
            [
                "scenes", "geometry", "demo:inset",
                "--size", "small", "--at", "top-left", "--margin", "64",
            ],
        )
        assert first.exit_code == 0, first.stdout

        second = runner.invoke(
            cli,
            [
                "scenes", "geometry", "demo:inset",
                "--motion", "bounce", "--speed", "fast",
            ],
        )

        assert second.exit_code == 0, second.stdout
        spec = json.loads((tmp_path / "moviestar" / "spec.json").read_text())
        assert spec["composition"][0]["layout"]["geometry"]["inset"] == {
            "size": 0.22,
            "anchor": "top-left",
            "margin_x": 64,
            "margin_y": 64,
            "motion": {"preset": "bounce", "speed": "fast"},
        }

    def test_read_surfaces_share_stored_and_resolved_geometry(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        authored = runner.invoke(
            cli,
            [
                "scenes", "geometry", "demo:inset",
                "--size", "small", "--at", "top-left", "--margin", "64",
            ],
        )
        assert authored.exit_code == 0, authored.stdout

        listed = json.loads(runner.invoke(cli, ["scenes", "list"]).stdout)
        status = json.loads(runner.invoke(cli, ["status"]).stdout)
        exported = json.loads(
            runner.invoke(cli, ["export", "--dry-run"]).stdout
        )
        raw_spec = json.loads(runner.invoke(cli, ["spec"]).stdout)

        layouts = [
            listed["scenes"][0]["layout"],
            status["composition"][0]["layout"],
            exported["composition"][0]["layout"],
        ]
        expected_geometry = {
            "inset": {
                "size": 0.22,
                "anchor": "top-left",
                "margin_x": 64,
                "margin_y": 64,
            }
        }
        expected_region = {
            "x": 64, "y": 64, "width": 238, "height": 238,
        }
        assert (
            raw_spec["composition"][0]["layout"]["geometry"]
            == expected_geometry
        )
        for layout in layouts:
            assert layout["geometry"] == expected_geometry
            assert layout["regions"]["inset"] == expected_region

    @pytest.mark.parametrize(
        ("args", "hint_token"),
        [
            (["--at", "top-left", "--x", "0.1", "--y", "0.2"], "--at"),
            (["--x", "0.1", "--y", "0.2", "--margin", "64"], "--margin"),
            (["--x", "0.1"], "--x and --y"),
            (["--speed", "fast"], "--motion bounce"),
            (["--reset", "--size", "small"], "--reset"),
        ],
    )
    def test_geometry_rejects_conflicting_controls(
        self,
        runner,
        loaded_project,
        test_video,
        args,
        hint_token,
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(cli, ["scenes", "geometry", "demo:inset", *args])

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes geometry")
        assert hint_token in data["hint"]

    def test_geometry_rejects_unknown_scene_with_available_names(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli, ["scenes", "geometry", "missing:inset", "--size", "small"]
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes geometry")
        assert "missing" in data["error"]
        assert "demo" in data["hint"]

    def test_geometry_rejects_non_pip_slot_with_specific_fix(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli, ["scenes", "geometry", "demo:main", "--size", "small"]
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes geometry")
        assert "picture-in-picture inset" in data["error"]
        assert "demo:inset" in data["hint"]

    def test_reset_restores_the_preset_default(
        self, runner, loaded_project, test_video, tmp_path
    ):
        """#498: reset restores the materialized preset default (the
        M26 circle) rather than stripping to the legacy state."""
        _pip_project(runner, loaded_project, test_video)
        authored = runner.invoke(
            cli,
            ["scenes", "geometry", "demo:inset", "--size", "small"],
        )
        assert authored.exit_code == 0, authored.stdout

        result = runner.invoke(
            cli, ["scenes", "geometry", "demo:inset", "--reset"]
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "reset_slot_geometry"
        assert data["geometry_source"] == "preset"
        # Medium square default at the bottom-right anchor.
        assert data["region"]["width"] == data["region"]["height"] == 346
        assert data["resolved_start_region"] == data["region"]
        assert data["writes_spec"] is True
        spec = json.loads((tmp_path / "moviestar" / "spec.json").read_text())
        stored = spec["composition"][0]["layout"]["geometry"]["inset"]
        assert stored["size"] == 0.32
        assert stored["shape"]["kind"] == "circle"

    def test_write_migrates_legacy_inset_without_colliding(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        spec_path = tmp_path / "moviestar" / "spec.json"
        spec = json.loads(spec_path.read_text())
        spec["composition"][0]["layout"]["inset"] = {
            "corner": "top-left", "width": 0.25, "height": 0.20,
        }
        spec_path.write_text(json.dumps(spec))

        result = runner.invoke(
            cli, ["scenes", "geometry", "demo:inset", "--size", "small"]
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["migration"]["removed"] == "layout.inset"
        stored = json.loads(spec_path.read_text())["composition"][0]["layout"]
        assert "inset" not in stored
        assert stored["geometry"] == {"inset": {"size": 0.22}}


class TestM25CameraReresolution:
    def _apply_point_zoom(self, runner):
        preview = runner.invoke(
            cli,
            [
                "scenes", "motion", "camera", "preview",
                "--at", "0.5", "--slot", "inset",
                "--move-in", "0.1", "--hold", "0.1", "--move-out", "0.1",
                "--point", "160,120", "--zoom", "2",
            ],
        )
        assert preview.exit_code == 0, preview.stdout
        applied = runner.invoke(
            cli, ["scenes", "motion", "camera", "apply", "preview_0001"]
        )
        assert applied.exit_code == 0, applied.stdout

    def _apply_wide_box_zoom(self, runner):
        preview = runner.invoke(
            cli,
            [
                "scenes", "motion", "camera", "preview",
                "--at", "0.5", "--slot", "inset",
                "--move-in", "0.1", "--hold", "0.1", "--move-out", "0.1",
                "--box", "45,5,230,230", "--padding", "0",
            ],
        )
        assert preview.exit_code == 0, preview.stdout
        applied = runner.invoke(
            cli, ["scenes", "motion", "camera", "apply", "preview_0001"]
        )
        assert applied.exit_code == 0, applied.stdout

    def _set_explicit_crop(self, runner, tmp_path, rect, *, move_id):
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
                                            "id": move_id,
                                            "range": {
                                                "from": "0.1",
                                                "to": "0.3",
                                                "space": "result-local",
                                            },
                                            "to": {
                                                "target": {
                                                    "space": "source",
                                                    "units": "pixels",
                                                    "rect": rect,
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
        result = runner.invoke(cli, ["scenes", "motion", "set", str(motion_file)])
        assert result.exit_code == 0, result.stdout

    def _stored_camera(self, tmp_path):
        spec = json.loads((tmp_path / "moviestar" / "spec.json").read_text())
        return spec["motion"]["scenes"][0]["slots"][0]["camera"][0]

    def test_geometry_recomputes_authored_zoom_and_reports_crop_change(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        self._apply_point_zoom(runner)
        before = self._stored_camera(tmp_path)["to"]["target"]["rect"]

        dry_run = runner.invoke(
            cli,
            [
                "scenes", "geometry", "demo:inset",
                "--size", "small", "--dry-run",
            ],
        )
        assert dry_run.exit_code == 0, dry_run.stdout
        dry_data = json.loads(dry_run.stdout)
        assert len(dry_data["camera_changes"]) == 1
        assert self._stored_camera(tmp_path)["to"]["target"]["rect"] == before

        result = runner.invoke(
            cli,
            ["scenes", "geometry", "demo:inset", "--size", "small"],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        [change] = data["camera_changes"]
        assert change["id"] == "cam_0001"
        assert change["scene"] == "demo"
        assert change["slot"] == "inset"
        assert change["resolution"] == "authored_selection_recomputed"
        assert change["crop_pixels"]["from"] == pytest.approx(before, abs=0.01)
        after = self._stored_camera(tmp_path)["to"]["target"]["rect"]
        assert change["crop_pixels"]["to"] == after
        assert dry_data["camera_changes"] == data["camera_changes"]
        assert after != before

        same_aspect = runner.invoke(
            cli,
            ["scenes", "geometry", "demo:inset", "--size", "large"],
        )
        assert same_aspect.exit_code == 0, same_aspect.stdout
        assert json.loads(same_aspect.stdout)["camera_changes"] == []
        assert self._stored_camera(tmp_path)["to"]["target"]["rect"] == after

    def test_geometry_reports_explicit_crop_without_rewriting_target(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        target = {"x": 100, "y": 60, "w": 120, "h": 120}
        self._set_explicit_crop(
            runner, tmp_path, target, move_id="focus-controls"
        )

        result = runner.invoke(
            cli,
            ["scenes", "geometry", "demo:inset", "--size", "small"],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        [change] = data["camera_changes"]
        assert change["id"] == "focus-controls"
        assert change["resolution"] == "explicit_crop_reresolved"
        assert change["crop_pixels"]["from"] == pytest.approx(
            {"x": 100.0, "y": 40.0, "w": 120.0, "h": 160.0},
            abs=0.2,
        )
        assert change["crop_pixels"]["to"] == {
            "x": 100.0, "y": 60.0, "w": 120.0, "h": 120.0,
        }
        assert self._stored_camera(tmp_path)["to"]["target"]["rect"] == target

    def test_scene_file_geometry_uses_same_camera_reconciliation(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        self._apply_point_zoom(runner)
        before = self._stored_camera(tmp_path)["to"]["target"]["rect"]
        scene_file = TestM25SceneFileGeometry()._scene_file(
            tmp_path,
            geometry={"size": 0.22, "anchor": "top-left"},
        )

        result = runner.invoke(cli, ["scenes", "set", str(scene_file)])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        [change] = data["camera_changes"]
        assert change["id"] == "cam_0001"
        assert change["resolution"] == "authored_selection_recomputed"
        assert self._stored_camera(tmp_path)["to"]["target"]["rect"] != before

    def test_legacy_inset_resize_reports_camera_changes(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        target = {"x": 100, "y": 60, "w": 120, "h": 120}
        self._set_explicit_crop(
            runner, tmp_path, target, move_id="legacy-focus"
        )

        result = runner.invoke(
            cli,
            [
                "scenes", "inset", "--scene", "demo",
                "--width", "22%", "--height", "12.375%",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        [change] = data["camera_changes"]
        assert change["id"] == "legacy-focus"
        assert change["resolution"] == "explicit_crop_reresolved"
        assert self._stored_camera(tmp_path)["to"]["target"]["rect"] == target

    def _aspect_change(self, runner):
        """An aspect-changing CLI edit (#497 WxH sizes): square -> wide,
        the trigger #503 promised these tests would graduate back to."""
        return runner.invoke(
            cli,
            ["scenes", "geometry", "demo:inset", "--size", "0.44x0.22"],
        )

    def test_geometry_errors_with_move_id_instead_of_reducing_crop(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        square = runner.invoke(
            cli,
            ["scenes", "geometry", "demo:inset", "--size", "small"],
        )
        assert square.exit_code == 0, square.stdout
        self._set_explicit_crop(
            runner,
            tmp_path,
            {"x": 40, "y": 0, "w": 240, "h": 240},
            move_id="wide-shot",
        )

        result = self._aspect_change(runner)

        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes geometry")
        assert data["camera_id"] == "wide-shot"
        assert "wide-shot" in data["error"]

    def test_geometry_errors_when_authored_selection_would_be_cut(
        self, runner, loaded_project, test_video, tmp_path
    ):
        _pip_project(runner, loaded_project, test_video)
        square = runner.invoke(
            cli,
            ["scenes", "geometry", "demo:inset", "--size", "small"],
        )
        assert square.exit_code == 0, square.stdout
        self._apply_wide_box_zoom(runner)

        result = self._aspect_change(runner)

        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes geometry")
        assert data["camera_id"] == "cam_0001"
        assert "selection visible" in data["error"]
        # The failed reconciliation never touched the stored spec.
        spec = json.loads((tmp_path / "moviestar" / "spec.json").read_text())
        assert spec["composition"][0]["layout"]["geometry"]["inset"]["size"] == 0.22


class TestM25StaticVerificationParity:
    EXPECTED_REGION = {"x": 64, "y": 64, "width": 238, "height": 238}

    def _authored_project(self, runner, loaded_project, test_video):
        _pip_project(runner, loaded_project, test_video)
        result = runner.invoke(
            cli,
            [
                "scenes", "geometry", "demo:inset",
                "--size", "small", "--at", "top-left", "--margin", "64",
            ],
        )
        assert result.exit_code == 0, result.stdout

    def _inset_slot(self, scene):
        return next(slot for slot in scene["slots"] if slot["slot"] == "inset")

    def _main_slot(self, scene):
        return next(slot for slot in scene["slots"] if slot["slot"] == "main")

    def _filter_graph(self, command):
        return command[command.index("-filter_complex") + 1]

    def test_static_geometry_metadata_matches_every_verification_surface(
        self, runner, loaded_project, test_video
    ):
        self._authored_project(runner, loaded_project, test_video)

        listed = json.loads(runner.invoke(cli, ["scenes", "list"]).stdout)
        status = json.loads(runner.invoke(cli, ["status"]).stdout)
        screenshot = json.loads(
            runner.invoke(
                cli, ["screenshot", "--at", "0.5", "--dry-run"]
            ).stdout
        )
        inspect = json.loads(
            runner.invoke(
                cli,
                [
                    "inspect", "--from", "0", "--to", "1",
                    "--interval", "0.5", "--dry-run",
                ],
            ).stdout
        )
        watch = json.loads(
            runner.invoke(
                cli, ["watch", "--from", "0", "--to", "1", "--dry-run"]
            ).stdout
        )
        exported = json.loads(
            runner.invoke(cli, ["export", "--dry-run"]).stdout
        )

        scenes = [
            listed["scenes"][0],
            status["composition"][0],
            screenshot["active_scene"],
            inspect["scene_windows"][0],
            watch["scene_windows"][0],
            exported["composition"][0],
        ]
        for scene in scenes:
            assert scene["layout"]["regions"]["inset"] == self.EXPECTED_REGION
            slot = self._inset_slot(scene)
            assert slot["region"] == self.EXPECTED_REGION
            assert slot["region_is"] == "instant"
            assert slot["geometry_source"] == "override"
            assert "motion" not in slot

            main = self._main_slot(scene)
            assert main["region_is"] == "instant"
            assert main["geometry_source"] == "preset"
            assert "motion" not in main

    def test_static_geometry_preview_and_ffmpeg_commands_use_same_pixels(
        self, runner, loaded_project, test_video, tmp_path
    ):
        self._authored_project(runner, loaded_project, test_video)
        preview_path = tmp_path / "static-geometry.svg"

        preview_result = runner.invoke(
            cli,
            [
                "layouts", "preview", "picture-in-picture",
                "--canvas", "short",
                "--slot-size", "inset=small",
                "--slot-at", "inset=top-left",
                "--slot-margin", "inset=64",
                "--out", str(preview_path),
            ],
        )
        screenshot_result = runner.invoke(
            cli, ["screenshot", "--at", "0.5", "--dry-run"]
        )
        inspect_result = runner.invoke(
            cli,
            [
                "inspect", "--from", "0", "--to", "1",
                "--interval", "0.5", "--dry-run",
            ],
        )
        watch_result = runner.invoke(
            cli, ["watch", "--from", "0", "--to", "1", "--dry-run"]
        )
        export_result = runner.invoke(cli, ["export", "--dry-run"])
        for result in (
            preview_result,
            screenshot_result,
            inspect_result,
            watch_result,
            export_result,
        ):
            assert result.exit_code == 0, result.stdout

        preview = json.loads(preview_result.stdout)
        assert preview["layout"]["regions"]["inset"] == self.EXPECTED_REGION
        assert (
            '<rect x="64" y="64" width="238" height="238"'
            in preview_path.read_text()
        )

        screenshot = json.loads(screenshot_result.stdout)
        inspect = json.loads(inspect_result.stdout)
        watch = json.loads(watch_result.stdout)
        exported = json.loads(export_result.stdout)
        commands = [
            screenshot["ffmpeg_command"],
            *[item["command"] for item in inspect["ffmpeg_commands"]],
            *[item["command"] for item in watch["scene_render_commands"]],
            *[item["command"] for item in exported["scene_render_commands"]],
        ]
        for command in commands:
            graph = self._filter_graph(command)
            assert "overlay=x=64:y=64" in graph
            assert "overlay=x='" not in graph


class TestM25BounceExpressionEmission:
    def test_export_emits_bounce_expression_and_keeps_main_literal(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        authored = runner.invoke(
            cli,
            [
                "scenes", "geometry", "demo:inset",
                "--size", "small", "--at", "top-left", "--margin", "64",
                "--motion", "bounce", "--speed", "medium",
            ],
        )
        assert authored.exit_code == 0, authored.stdout

        result = runner.invoke(cli, ["export", "--dry-run"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        [command] = [
            item["command"] for item in data["scene_render_commands"]
        ]
        graph = command[command.index("-filter_complex") + 1]
        assert "overlay=x=0:y=0:shortest=1[lay0]" in graph
        assert (
            "overlay=x='abs(mod(600*(t+0)+906,1684)-842)':"
            "y='abs(mod(420*(t+0)+1746,3364)-1682)':shortest=1[outv]"
            in graph
        )


class TestM25BounceResultTimeParity:
    EXPECTED_ENVELOPE = {"x": 0, "y": 0, "width": 1080, "height": 1920}
    EXPECTED_START = {"x": 64, "y": 64, "width": 238, "height": 238}

    def _authored_project(self, runner, loaded_project, test_video):
        _pip_project(
            runner, loaded_project, test_video, duration=2
        )
        authored = runner.invoke(
            cli,
            [
                "scenes", "geometry", "demo:inset",
                "--size", "small", "--at", "top-left", "--margin", "64",
                "--motion", "bounce", "--speed", "medium",
            ],
        )
        assert authored.exit_code == 0, authored.stdout

    @staticmethod
    def _filter_graph(command):
        return command[command.index("-filter_complex") + 1]

    @staticmethod
    def _inset(slots):
        return next(slot for slot in slots if slot["slot"] == "inset")

    @staticmethod
    def _two_scene_project(
        runner, loaded_project, test_video, tmp_path, *, moving_scenes
    ):
        loaded_project(
            [test_video, test_video],
            names=["screen", "speaker"],
            frames=False,
        )
        scene_file = tmp_path / "two-scenes.json"
        scenes = []
        for name in ("one", "two"):
            geometry = {
                "size": 0.22,
                "anchor": "top-left",
                "margin_x": 64,
                "margin_y": 64,
            }
            if name in moving_scenes:
                geometry["motion"] = {"preset": "bounce", "speed": "medium"}
            scenes.append(
                {
                    "name": name,
                    "layout": "picture-in-picture",
                    "slots": [
                        {
                            "slot": "main",
                            "source": "screen",
                            "from": 0,
                            "to": 2,
                        },
                        {
                            "slot": "inset",
                            "source": "speaker",
                            "from": 0,
                            "to": 2,
                            "geometry": geometry,
                        },
                    ],
                    "audio_from": "screen",
                }
            )
        scene_file.write_text(json.dumps({"canvas": "short", "scenes": scenes}))
        authored = runner.invoke(cli, ["scenes", "set", str(scene_file)])
        assert authored.exit_code == 0, authored.stdout

    def test_scene_level_surfaces_report_motion_envelope(
        self, runner, loaded_project, test_video
    ):
        self._authored_project(runner, loaded_project, test_video)

        listed = json.loads(runner.invoke(cli, ["scenes", "list"]).stdout)
        watched = json.loads(
            runner.invoke(
                cli,
                ["watch", "--from", "0.5", "--to", "2", "--dry-run"],
            ).stdout
        )
        for scene in (listed["scenes"][0], watched["scene_windows"][0]):
            assert scene["layout"]["regions"]["inset"] == self.EXPECTED_ENVELOPE
            inset = self._inset(scene["slots"])
            assert inset["region"] == self.EXPECTED_ENVELOPE
            assert inset["region_is"] == "envelope"
            assert inset["geometry_source"] == "override"
            assert inset["resolved_start_region"] == self.EXPECTED_START
            assert inset["motion"] == {"preset": "bounce", "speed": "medium"}
        watched_inset = self._inset(watched["scene_windows"][0]["slots"])
        assert watched_inset["range_start_region"] == {
            "x": 364,
            "y": 274,
            "width": 238,
            "height": 238,
        }
        assert self._inset(listed["scenes"][0]["slots"])[
            "range_start_region"
        ] == self.EXPECTED_START

    def test_screenshot_reports_and_renders_instant_at_requested_time(
        self, runner, loaded_project, test_video
    ):
        self._authored_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli, ["screenshot", "--at", "1.5", "--dry-run"]
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        inset = self._inset(data["active_scene"]["slots"])
        assert inset["region"] == {
            "x": 720,
            "y": 694,
            "width": 238,
            "height": 238,
        }
        assert inset["region_is"] == "instant"
        assert inset["region_at"] == {"seconds": 1.5, "text": "0:00:01.500"}
        assert inset["geometry_source"] == "override"
        assert inset["motion"] == {"preset": "bounce", "speed": "medium"}
        assert "range_start_region" not in inset
        assert "resolved_start_region" not in inset
        graph = self._filter_graph(data["ffmpeg_command"])
        assert (
            "overlay=x='abs(mod(600*(t+1.5)+906,1684)-842)':"
            "y='abs(mod(420*(t+1.5)+1746,3364)-1682)'"
            in graph
        )

    def test_inspect_each_sample_uses_its_result_time(
        self, runner, loaded_project, test_video
    ):
        self._authored_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            [
                "inspect", "--from", "0.5", "--to", "2",
                "--interval", "1", "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert len(data["ffmpeg_commands"]) == 2
        for frame, offset, expected_region in (
            (data["ffmpeg_commands"][0], "0.5", (364, 274)),
            (data["ffmpeg_commands"][1], "1.5", (720, 694)),
        ):
            graph = self._filter_graph(frame["command"])
            assert f"600*(t+{offset})" in graph
            inset = self._inset(frame["slots"])
            assert (inset["region"]["x"], inset["region"]["y"]) == expected_region
            assert inset["region_is"] == "instant"
            assert inset["region_at"] == frame["timecode"]
            assert "range_start_region" not in inset
            assert "resolved_start_region" not in inset

    def test_watch_resumes_motion_at_clipped_range_start(
        self, runner, loaded_project, test_video
    ):
        self._authored_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            ["watch", "--from", "1.5", "--to", "2", "--dry-run"],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        [render] = data["scene_render_commands"]
        graph = self._filter_graph(render["command"])
        assert "600*(t+1.5)" in graph
        assert "420*(t+1.5)" in graph

    def test_motion_continues_across_adjacent_scenes_with_matching_geometry(
        self, runner, loaded_project, test_video, tmp_path
    ):
        self._two_scene_project(
            runner,
            loaded_project,
            test_video,
            tmp_path,
            moving_scenes={"one", "two"},
        )

        result = runner.invoke(
            cli, ["screenshot", "--at", "2.5", "--dry-run"]
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["active_scene"]["scene"] == "two"
        inset_out = self._inset(data["active_scene"]["slots"])
        assert (inset_out["region"]["x"], inset_out["region"]["y"]) == (120, 1114)
        assert inset_out["region_at"] == {
            "seconds": 2.5,
            "text": "0:00:02.500",
        }
        graph = self._filter_graph(data["ffmpeg_command"])
        assert "600*(t+2.5)" in graph
        assert "420*(t+2.5)" in graph

        export = runner.invoke(cli, ["export", "--dry-run"])
        assert export.exit_code == 0, export.stdout
        first, second = json.loads(export.stdout)["scene_render_commands"]
        first_graph = self._filter_graph(first["command"])
        second_graph = self._filter_graph(second["command"])
        assert "600*(t+0)" in first_graph
        assert "420*(t+0)" in first_graph
        assert "600*(t+2)" in second_graph
        assert "420*(t+2)" in second_graph

    def test_motion_starts_at_authored_origin_after_a_static_scene(
        self, runner, loaded_project, test_video, tmp_path
    ):
        self._two_scene_project(
            runner,
            loaded_project,
            test_video,
            tmp_path,
            moving_scenes={"two"},
        )

        result = runner.invoke(
            cli, ["screenshot", "--at", "2.5", "--dry-run"]
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        inset = self._inset(data["active_scene"]["slots"])
        assert (inset["region"]["x"], inset["region"]["y"]) == (364, 274)
        graph = self._filter_graph(data["ffmpeg_command"])
        assert "600*(t+0.5)" in graph
        assert "420*(t+0.5)" in graph

    def test_real_screenshot_matches_export_after_reflection(
        self, runner, loaded_project, tmp_path
    ):
        main = tmp_path / "main.mp4"
        inset = tmp_path / "inset.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i",
                "color=blue:size=640x360:rate=20:duration=3.2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3.2",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-shortest", str(main),
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i",
                "color=red:size=80x80:rate=20:duration=3.2",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-an", str(inset),
            ],
            check=True,
        )
        loaded_project(
            [str(main), str(inset)],
            names=["screen", "speaker"],
            frames=False,
        )
        scene = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "640x360",
                "--scene", "demo=picture-in-picture",
                "--slot", "demo:main=screen", "--from", "0", "--to", "3.2",
                "--slot", "demo:inset=speaker", "--from", "0", "--to", "3.2",
                "--audio-from", "demo=screen",
            ],
        )
        assert scene.exit_code == 0, scene.stdout
        authored = runner.invoke(
            cli,
            [
                "scenes", "geometry", "demo:inset",
                "--size", "small", "--at", "top-left", "--margin", "0",
                "--motion", "bounce", "--speed", "medium",
            ],
        )
        assert authored.exit_code == 0, authored.stdout
        exported = tmp_path / "export.mp4"
        screenshot = tmp_path / "screenshot.png"

        export_result = runner.invoke(
            cli, ["export", "--quiet", "--out", str(exported)]
        )
        screenshot_result = runner.invoke(
            cli,
            ["screenshot", "--at", "3", "--out", str(screenshot)],
        )

        assert export_result.exit_code == 0, export_result.stdout
        assert screenshot_result.exit_code == 0, screenshot_result.stdout

        def red_origin(path, *, at_s=None):
            seek = ["-ss", str(at_s)] if at_s is not None else []
            raw = subprocess.run(
                [
                    "ffmpeg", "-v", "error", *seek, "-i", str(path),
                    "-frames:v", "1", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "-",
                ],
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
            red_pixels = []
            for pixel in range(0, len(raw), 3):
                red, green, blue = raw[pixel:pixel + 3]
                if red > 180 and green < 80 and blue < 80:
                    index = pixel // 3
                    red_pixels.append((index % 640, index // 640))
            assert red_pixels
            return (
                min(x for x, _y in red_pixels),
                min(y for _x, y in red_pixels),
            )

        export_origin = red_origin(exported, at_s=3)
        screenshot_origin = red_origin(screenshot)
        assert export_origin == pytest.approx((520, 140), abs=2)
        assert screenshot_origin == pytest.approx(export_origin, abs=2)
