"""M26 slot-shape authoring surface.

The shape flags on ``scenes geometry`` resolve a real contract — kind,
border, region, on-disk masks, truthful preview SVG — and persist into
``layout.geometry.inset.shape`` (shape since step 3, border since
step 4).
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from tests.conftest import assert_error_envelope, assert_no_project_envelope


@pytest.fixture
def runner():
    return CliRunner()


def _pip_project(
    runner, loaded_project, test_video, *, framing=None, duration=1,
    load=True, legacy=False
):
    if load:
        loaded_project(
            [test_video, test_video],
            names=["screen", "speaker"],
            frames=False,
        )
    framing_flags = (
        ["--framing", "fill:center", "--framing", framing]
        if framing is not None
        else []
    )
    result = runner.invoke(
        cli,
        [
            "scenes", "set", "--canvas", "short",
            "--scene", "demo=picture-in-picture",
            "--slot", "demo:main=screen", "--from", "0", "--to", str(duration),
            "--slot", "demo:inset=speaker", "--from", "0", "--to", str(duration),
            *framing_flags,
            "--audio-from", "demo=screen",
        ],
    )
    assert result.exit_code == 0, result.stdout
    if legacy:
        # Model a pre-M26 spec: strip the materialized default so the
        # scene has no stored geometry or shape.
        spec_path = Path("moviestar/spec.json")
        spec = json.loads(spec_path.read_text())
        for scene in spec["composition"]:
            scene["layout"].pop("geometry", None)
        spec_path.write_text(json.dumps(spec, indent=2))


def _shape(runner, *args):
    result = runner.invoke(cli, ["scenes", "geometry", "demo:inset", *args])
    return result, json.loads(result.stdout) if result.stdout else None


def _size_inset(runner, size="medium"):
    """Persist a square inset size so shape-only calls resolve a circle."""
    result = runner.invoke(
        cli, ["scenes", "geometry", "demo:inset", "--size", size]
    )
    assert result.exit_code == 0, result.stdout


class TestM26ShapeDiscoverySurface:
    def test_geometry_help_documents_shape_vocabulary(self, runner):
        result = runner.invoke(cli, ["scenes", "geometry", "--help"])

        assert result.exit_code == 0
        compact = " ".join(result.output.split())
        for token in (
            "--shape",
            "circle",
            "rounded",
            "rect",
            "--radius",
            "--border",
            "'4px white'",
            "fill framing",
        ):
            assert token in compact, token

    def test_overview_lists_shape_kinds(self, runner, loaded_project, test_video):
        _pip_project(runner, loaded_project, test_video)
        result = runner.invoke(cli, ["scenes", "geometry"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["shapes"]["kinds"] == ["circle", "rounded", "rect"]

    def test_shape_flags_without_target_error(self, runner):
        result = runner.invoke(cli, ["scenes", "geometry", "--shape", "circle"])

        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes geometry")
        assert "demo:inset" in data["hint"]

    def test_shape_without_project_reports_no_project(self, runner, tmp_path,
                                                      monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            cli, ["scenes", "geometry", "demo:inset", "--shape", "circle"]
        )

        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert_no_project_envelope(data, command="scenes geometry")


class TestM26ShapeResolution:
    def test_circle_resolves_with_masks_and_preview(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        result, data = _shape(runner, "--shape", "circle")

        assert result.exit_code == 0, result.stdout
        assert data["status"] == "set_slot_geometry"
        assert data["writes_spec"] is True
        assert data["shape"]["kind"] == "circle"
        assert data["shape"]["border"] == {"width": 4, "color": "white"}
        assert data["region"]["width"] == data["region"]["height"]
        assert data["region_is"] == "instant"
        assert Path(data["shape_preview"]["masks"]["content"]).exists()
        assert Path(data["shape_preview"]["masks"]["border_disc"]).exists()
        preview = Path(data["shape_preview"]["preview"])
        assert preview.exists()
        assert preview.name.endswith("-circle.svg")
        svg = preview.read_text()
        assert "<circle" in svg
        # Slot labels match the standard layout previews.
        assert ">main</text>" in svg
        assert ">inset</text>" in svg


    def test_mixed_invocation_persists_geometry_and_shape(
        self, runner, loaded_project, test_video
    ):
        """The single natural command — size + place + shape — stores
        both halves. Friction round 1 caught the step-1 behavior
        (nothing persisted, disclosed in one buried sentence) as the
        surface's one lie-by-omission."""
        _pip_project(runner, loaded_project, test_video)
        spec_path = Path("moviestar/spec.json")
        before = spec_path.read_bytes()

        result, data = _shape(
            runner, "--size", "small", "--at", "top-left",
            "--shape", "circle",
        )

        assert result.exit_code == 0, result.stdout
        assert data["status"] == "set_slot_geometry"
        assert data["writes_spec"] is True
        assert spec_path.read_bytes() != before
        assert data["geometry"]["placement"]["anchor"] == "top-left"
        assert data["shape"]["kind"] == "circle"
        assert Path(data["shape_preview"]["preview"]).exists()
        assert Path(data["shape_preview"]["masks"]["content"]).exists()
        assert "design_preview" not in data

    def test_circle_on_legacy_rectangle_names_the_size_fix(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video, legacy=True)
        result, data = _shape(runner, "--shape", "circle")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "square" in data["error"]
        assert "--size" in data["hint"]

    def test_bare_shape_flags_on_square_slot_default_to_circle(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        result, data = _shape(runner, "--border", "none")

        assert result.exit_code == 0, result.stdout
        assert data["shape"]["kind"] == "circle"
        assert "shape_note" not in data

    def test_bare_border_on_legacy_rectangle_defaults_to_rounded_with_note(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video, legacy=True)
        result, data = _shape(runner, "--border", "none")

        assert result.exit_code == 0, result.stdout
        assert data["shape"]["kind"] == "rounded"
        assert "square" in data["shape_note"]

    def test_radius_implies_rounded(self, runner, loaded_project, test_video):
        _pip_project(runner, loaded_project, test_video)
        result, data = _shape(runner, "--radius", "0.2")

        assert result.exit_code == 0, result.stdout
        assert data["shape"]["kind"] == "rounded"
        assert data["shape"]["radius"] == 0.2
        assert Path(data["shape_preview"]["masks"]["content"]).exists()

    def test_rounded_defaults_radius(self, runner, loaded_project, test_video):
        _pip_project(runner, loaded_project, test_video)
        result, data = _shape(runner, "--shape", "rounded")

        assert result.exit_code == 0, result.stdout
        assert data["shape"]["radius"] == 0.15

    def test_rect_resolves_without_masks(self, runner, loaded_project, test_video):
        _pip_project(runner, loaded_project, test_video)
        result, data = _shape(runner, "--shape", "rect")

        assert result.exit_code == 0, result.stdout
        assert data["shape"] == {"kind": "rect"}
        assert "masks" not in data

    def test_fit_framing_rejects_curved_shapes(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video, framing="fit")
        result, data = _shape(runner, "--size", "medium", "--shape", "circle")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "fit" in data["error"]
        assert "fill" in data["hint"]

    def test_fit_framing_still_allows_rect(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video, framing="fit")
        result, data = _shape(runner, "--shape", "rect")

        assert result.exit_code == 0, result.stdout
        assert data["shape"]["kind"] == "rect"

    def test_moving_slot_reports_envelope_region(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        result, data = _shape(
            runner, "--size", "small", "--shape", "circle",
            "--motion", "bounce",
        )

        assert result.exit_code == 0, result.stdout
        assert data["status"] == "set_slot_geometry"
        assert data["shape"]["kind"] == "circle"
        assert data["region_is"] == "envelope"
        assert "motion_note" in data
        start = data["resolved_start_region"]
        assert start["width"] == start["height"]


class TestM26BorderSurface:
    def test_border_shorthand_parses_width_and_color(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        result, data = _shape(runner, "--border", "4px white")

        assert result.exit_code == 0, result.stdout
        assert data["shape"]["border"] == {"width": 4, "color": "white"}
        assert Path(data["shape_preview"]["masks"]["border_disc"]).exists()

    def test_bare_color_border_defaults_the_width(
        self, runner, loaded_project, test_video
    ):
        """'Give it a white border' — the most common agent phrasing —
        should work without a width (friction round 1, agent 1)."""
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        result, data = _shape(runner, "--border", "white")

        assert result.exit_code == 0, result.stdout
        assert data["shape"]["border"] == {"width": 4, "color": "white"}

    def test_border_none_removes_the_border(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        result, data = _shape(runner, "--border", "none")

        assert result.exit_code == 0, result.stdout
        assert "border" not in data["shape"]

    @pytest.mark.parametrize("value", ["4 white", "px white", "4px", ""])
    def test_malformed_border_shows_the_example(
        self, runner, loaded_project, test_video, value
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        result, data = _shape(runner, "--border", value)

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "4px white" in data["error"]

    def test_zero_width_border_points_at_none(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        result, data = _shape(runner, "--border", "0px white")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "--border none" in data["error"]

    def test_invalid_border_color_is_rejected(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        result, data = _shape(runner, "--border", "4px wh!te")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "color" in data["error"]


class TestM26ShapeConflicts:
    def test_radius_with_circle_is_rejected(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        result, data = _shape(runner, "--shape", "circle", "--radius", "0.2")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "--radius" in data["error"]

    def test_radius_with_rect_is_rejected(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        result, data = _shape(runner, "--shape", "rect", "--radius", "0.2")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "--radius" in data["error"]

    def test_radius_out_of_range_is_rejected(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        result, data = _shape(runner, "--radius", "0.6")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "0.5" in data["error"]

    def test_reset_conflicts_with_shape_flags(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        result, data = _shape(runner, "--reset", "--shape", "circle")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "--reset" in data["error"]
