"""#497 — custom WxH (non-square) inset sizes from the M26 spec table.

``--size WxH`` stores ``{"width": f, "height": f}`` (each a fraction of
the canvas's shorter dimension), resolves per-dimension even pixels,
opts out of circle into rounded with a ``shape_note``, and errors when
combined with an explicit circle. This also restores an aspect-changing
CLI edit, so the camera selection-cut contract is CLI-triggerable again.
"""

import json
from importlib.resources import files
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.spec import (
    SpecValidationError,
    resolve_inset_geometry,
    validate_spec,
)
from moviestar.cli import cli
from tests.conftest import assert_error_envelope
from tests.test_m26_shape_authoring import _scene_spec
from tests.test_m26_shape_surface import _pip_project, _shape


@pytest.fixture
def runner():
    return CliRunner()


CANVAS = {"width": 1080, "height": 1920}


class TestWxhSchema:
    def test_object_size_passes_with_rounded_shape(self):
        validate_spec(
            _scene_spec(
                shape={"kind": "rounded", "radius": 0.15},
                size={"width": 0.4, "height": 0.3},
            )
        )

    def test_object_size_passes_without_shape(self):
        validate_spec(_scene_spec(shape=None, size={"width": 0.4, "height": 0.3}))

    def test_one_third_by_two_thirds_size_passes(self):
        validate_spec(
            _scene_spec(
                shape=None,
                size={"width": 1 / 3, "height": 2 / 3},
            )
        )

    def test_circle_rejects_a_non_square_size(self):
        with pytest.raises(SpecValidationError, match="square"):
            validate_spec(
                _scene_spec(
                    shape={"kind": "circle"},
                    size={"width": 0.4, "height": 0.3},
                )
            )

    def test_object_size_requires_both_dimensions(self):
        with pytest.raises(SpecValidationError, match="height"):
            validate_spec(_scene_spec(shape=None, size={"width": 0.4}))

    def test_object_size_rejects_unknown_fields(self):
        with pytest.raises(SpecValidationError, match="depth"):
            validate_spec(
                _scene_spec(
                    shape=None,
                    size={"width": 0.4, "height": 0.3, "depth": 1},
                )
            )

    def test_object_size_dimensions_are_range_checked(self):
        with pytest.raises(SpecValidationError, match="height"):
            validate_spec(
                _scene_spec(shape=None, size={"width": 0.4, "height": 1.01})
            )

    def test_agent_readable_schema_matches_wxh_runtime_range(self):
        schema_path = files("moviestar.schema").joinpath("spec-0.2.json")
        schema = json.loads(schema_path.read_text())
        size_schema = schema["$defs"]["layout"]["properties"]["geometry"][
            "properties"
        ]["inset"]["properties"]["size"]

        number_schema, object_schema = size_schema["oneOf"]
        assert number_schema["maximum"] == 1
        assert object_schema["properties"]["width"]["maximum"] == 1
        assert object_schema["properties"]["height"]["maximum"] == 1

    def test_resolution_rounds_each_dimension_to_even_pixels(self):
        region = resolve_inset_geometry(
            {"size": {"width": 0.4, "height": 0.3}}, CANVAS
        )
        assert (region["width"], region["height"]) == (432, 324)


class TestWxhAuthoring:
    def test_wxh_size_persists_and_defaults_to_rounded(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result, data = _shape(runner, "--size", "0.40x0.30")

        assert result.exit_code == 0, result.stdout
        assert data["geometry"]["size"]["value"] == "custom"
        assert data["geometry"]["size"]["width"] == 432
        assert data["geometry"]["size"]["height"] == 324
        stored = data["stored_geometry"]
        assert stored["size"] == {"width": 0.4, "height": 0.3}
        # Non-square opts out of circle: the stored default becomes
        # rounded and the envelope says why.
        assert stored["shape"]["kind"] == "rounded"
        assert "square" in data["shape_note"]

    def test_percent_form_parses(self, runner, loaded_project, test_video):
        _pip_project(runner, loaded_project, test_video)

        result, data = _shape(runner, "--size", "40%x30%")

        assert result.exit_code == 0, result.stdout
        assert data["stored_geometry"]["size"] == {"width": 0.4, "height": 0.3}

    def test_one_third_by_two_thirds_percent_form_parses(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result, data = _shape(runner, "--size", "33.333%x66.667%")

        assert result.exit_code == 0, result.stdout
        assert data["stored_geometry"]["size"] == {
            "width": pytest.approx(1 / 3, abs=0.00001),
            "height": pytest.approx(2 / 3, abs=0.00001),
        }
        assert data["geometry"]["size"]["width"] == 360
        assert data["geometry"]["size"]["height"] == 720

    def test_explicit_circle_with_wxh_errors_naming_the_size(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result, data = _shape(
            runner, "--size", "0.40x0.30", "--shape", "circle"
        )

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "square" in data["error"]
        assert "--size" in data["hint"]

    @pytest.mark.parametrize("value", ["0.4x", "x0.3", "0.4x0.3x0.2", "axb"])
    def test_malformed_wxh_teaches_the_form(
        self, runner, loaded_project, test_video, value
    ):
        _pip_project(runner, loaded_project, test_video)

        result, data = _shape(runner, "--size", value)

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "0.40x0.30" in data["error"]

    def test_wxh_out_of_range_dimension_is_rejected(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result, data = _shape(runner, "--size", "0.40x1.01")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "1.00" in data["error"]

    def test_geometry_overview_advertises_full_short_side(self, runner):
        result = runner.invoke(cli, ["scenes", "geometry"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["sizes"]["custom"] == (
            "0.05..1.00 of the canvas shorter dimension"
        )

    def test_layouts_preview_accepts_wxh(
        self, runner, loaded_project, test_video
    ):
        loaded_project([test_video], names=["screen"], frames=False)

        result = runner.invoke(
            cli,
            [
                "layouts", "preview", "picture-in-picture",
                "--slot-size", "inset=0.40x0.30",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        region = data["layout"]["regions"]["inset"]
        assert (region["width"], region["height"]) == (432, 324)

    def test_wxh_export_renders_the_rounded_shape(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _shape(runner, "--size", "0.40x0.30")

        result = runner.invoke(cli, ["export", "--dry-run", "--out", "o.mp4"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        command = data["scene_render_commands"][0]["command"]
        graph = command[command.index("-filter_complex") + 1]
        assert "alphamerge" in graph
        masks = [arg for arg in command if arg.endswith(".png")]
        assert any("rounded" in mask for mask in masks)

    def test_square_sizes_still_store_a_single_fraction(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result, data = _shape(runner, "--size", "small")

        assert result.exit_code == 0, result.stdout
        assert data["stored_geometry"]["size"] == 0.22
