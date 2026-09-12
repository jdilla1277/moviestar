"""M26 step 5 — the circular default, materialized on write.

New picture-in-picture scenes record a square medium inset with a
white-bordered circle at authoring time. Existing looks never change
silently: re-authoring a scene that had no stored shape materializes
today's rectangle, and specs that predate the default render rect and
surface a structured warning on status and export.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from tests.test_m26_shape_surface import _pip_project, _shape


@pytest.fixture
def runner():
    return CliRunner()


M26_DEFAULT_GEOMETRY = {
    "size": 0.32,
    "shape": {
        "kind": "circle",
        "border": {"width": 4, "color": "white"},
    },
}


def _stored_inset_geometry(scene="demo"):
    spec = json.loads(Path("moviestar/spec.json").read_text())
    entry = next(
        item for item in spec["composition"] if item.get("name") == scene
    )
    return (entry["layout"].get("geometry") or {}).get("inset")


def _strip_geometry(scene="demo"):
    """Rewrite spec.json as a legacy spec: bare pip layout, no shape."""
    path = Path("moviestar/spec.json")
    spec = json.loads(path.read_text())
    entry = next(
        item for item in spec["composition"] if item.get("name") == scene
    )
    entry["layout"].pop("geometry", None)
    path.write_text(json.dumps(spec, indent=2))


def _export_graph(runner):
    result = runner.invoke(cli, ["export", "--dry-run", "--out", "out.mp4"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    command = data["scene_render_commands"][0]["command"]
    return data, command[command.index("-filter_complex") + 1]


class TestCircleDefault:
    def test_new_pip_scene_materializes_the_circle_default(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        assert _stored_inset_geometry() == M26_DEFAULT_GEOMETRY

    def test_new_pip_scene_renders_the_circle(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        _data, graph = _export_graph(runner)

        assert "alphamerge" in graph
        assert "alphamerge[disc" in graph

    def test_fit_framed_inset_materializes_rect_instead(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video, framing="fit")

        assert _stored_inset_geometry() == {"shape": {"kind": "rect"}}
        _data, graph = _export_graph(runner)
        assert "alphamerge" not in graph

    def test_reauthoring_a_legacy_scene_materializes_rect(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _strip_geometry()

        _pip_project(runner, loaded_project, test_video, load=False)

        assert _stored_inset_geometry() == {"shape": {"kind": "rect"}}
        _data, graph = _export_graph(runner)
        assert "alphamerge" not in graph

    def test_reauthoring_carries_a_stored_circle(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        _pip_project(runner, loaded_project, test_video, load=False)

        assert _stored_inset_geometry() == M26_DEFAULT_GEOMETRY

    def test_single_layout_scenes_get_no_geometry(
        self, runner, loaded_project, test_video
    ):
        loaded_project([test_video], names=["screen"], frames=False)
        result = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "solo=single",
                "--slot", "solo:main=screen", "--from", "0", "--to", "1",
            ],
        )
        assert result.exit_code == 0, result.stdout
        spec = json.loads(Path("moviestar/spec.json").read_text())
        entry = spec["composition"][0]
        assert "geometry" not in entry["layout"]


class TestLegacyWarning:
    def _legacy_project(self, runner, loaded_project, test_video):
        _pip_project(runner, loaded_project, test_video)
        _strip_geometry()

    def _find_warning(self, data):
        return next(
            (
                item
                for item in data.get("warnings", [])
                if item["code"] == "legacy_rect_inset"
            ),
            None,
        )

    def test_status_warns_on_legacy_pip_scenes(
        self, runner, loaded_project, test_video
    ):
        self._legacy_project(runner, loaded_project, test_video)

        result = runner.invoke(cli, ["status"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        warning = self._find_warning(data)
        assert warning is not None
        assert warning["severity"] == "info"
        assert "demo" in warning["message"]
        assert "scenes geometry demo:inset --shape circle" in (
            warning["smallest_fix"]
        )

    def test_export_dry_run_warns_on_legacy_pip_scenes(
        self, runner, loaded_project, test_video
    ):
        self._legacy_project(runner, loaded_project, test_video)

        data, graph = _export_graph(runner)

        assert self._find_warning(data) is not None
        assert "alphamerge" not in graph

    def test_export_real_run_warns_on_legacy_pip_scenes(
        self, runner, loaded_project, test_video
    ):
        self._legacy_project(runner, loaded_project, test_video)

        result = runner.invoke(cli, ["export", "--out", "legacy.mp4"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert self._find_warning(data) is not None

    def test_authored_circle_does_not_warn(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(cli, ["status"])

        data = json.loads(result.stdout)
        assert self._find_warning(data) is None

    def test_authored_rect_does_not_warn(
        self, runner, loaded_project, test_video
    ):
        self._legacy_project(runner, loaded_project, test_video)
        _shape(runner, "--shape", "rect")

        result = runner.invoke(cli, ["status"])

        data = json.loads(result.stdout)
        assert self._find_warning(data) is None

    def test_non_pip_scenes_do_not_warn(
        self, runner, loaded_project, test_video
    ):
        loaded_project([test_video], names=["screen"], frames=False)
        result = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "solo=single",
                "--slot", "solo:main=screen", "--from", "0", "--to", "1",
            ],
        )
        assert result.exit_code == 0, result.stdout

        result = runner.invoke(cli, ["status"])

        data = json.loads(result.stdout)
        assert self._find_warning(data) is None


class TestResetRestoresTheDefault:
    """#498: --reset restores the current preset default (the
    materialized circle), matching layouts preview and new scenes,
    instead of stripping the slot into the legacy-warning state."""

    def test_reset_materializes_the_preset_default(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _shape(runner, "--size", "small", "--at", "top-left",
               "--shape", "rect", "--border", "none")

        result, data = _shape(runner, "--reset")

        assert result.exit_code == 0, result.stdout
        assert data["status"] == "reset_slot_geometry"
        assert data["stored_geometry"] == M26_DEFAULT_GEOMETRY
        assert data["region"]["width"] == data["region"]["height"]
        assert _stored_inset_geometry() == M26_DEFAULT_GEOMETRY

    def test_reset_hint_names_the_plain_rectangle_path(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result, data = _shape(runner, "--reset")

        assert result.exit_code == 0, result.stdout
        assert "--shape rect --border none" in data["hint"]

    def test_reset_scene_does_not_fire_the_legacy_warning(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _shape(runner, "--reset")

        result = runner.invoke(cli, ["status"])

        data = json.loads(result.stdout)
        codes = {w["code"] for w in data.get("warnings", [])}
        assert "legacy_rect_inset" not in codes

    def test_reset_adopts_the_default_on_a_legacy_scene(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _strip_geometry()

        result, data = _shape(runner, "--reset")

        assert result.exit_code == 0, result.stdout
        assert _stored_inset_geometry() == M26_DEFAULT_GEOMETRY

    def test_reset_dry_run_leaves_the_spec_alone(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _shape(runner, "--shape", "rect", "--border", "none")
        before = Path("moviestar/spec.json").read_bytes()

        result, data = _shape(runner, "--reset", "--dry-run")

        assert result.exit_code == 0, result.stdout
        assert data["status"] == "would_reset_slot_geometry"
        assert Path("moviestar/spec.json").read_bytes() == before

    def test_export_after_reset_renders_the_circle(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _shape(runner, "--shape", "rect", "--border", "none")
        _shape(runner, "--reset")

        _data, graph = _export_graph(runner)

        assert "alphamerge" in graph
