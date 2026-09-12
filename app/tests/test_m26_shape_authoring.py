"""M26 step 3 — shape schema + authoring.

Shapes stop being a design preview: ``scenes geometry --shape/--radius``
persists into ``layout.geometry.inset.shape``, the schema validates the
block (including the fit-framing and square-circle rules), and render
plans attach the shape so export and screenshot actually draw it.
Borders persist since step 4, and since step 5 new picture-in-picture
scenes carry the materialized circle-with-white-border default.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.spec import SpecValidationError, validate_spec
from moviestar.cli import cli
from tests.conftest import assert_error_envelope
from tests.test_m26_shape_surface import _pip_project, _shape, _size_inset


@pytest.fixture
def runner():
    return CliRunner()


def _stored_shape():
    spec = json.loads(Path("moviestar/spec.json").read_text())
    scene = next(
        item for item in spec["composition"] if item.get("name") == "demo"
    )
    geometry = (scene["layout"].get("geometry") or {}).get("inset") or {}
    return geometry.get("shape")


class TestShapePersistence:
    def test_shape_only_invocation_writes_spec(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)

        result, data = _shape(runner, "--shape", "circle")

        assert result.exit_code == 0, result.stdout
        assert data["writes_spec"] is True
        assert data["status"] == "set_slot_geometry"
        assert "design_preview" not in data
        assert data["shape"]["kind"] == "circle"
        assert _stored_shape() == {
            "kind": "circle",
            "border": {"width": 4, "color": "white"},
        }

    def test_mixed_invocation_persists_both_halves(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result, data = _shape(
            runner, "--size", "small", "--at", "top-left", "--shape", "circle"
        )

        assert result.exit_code == 0, result.stdout
        assert data["writes_spec"] is True
        assert "design_preview" not in data
        assert data["stored_geometry"]["shape"]["kind"] == "circle"
        assert data["stored_geometry"]["size"] == 0.22
        assert _stored_shape()["kind"] == "circle"

    def test_rounded_persists_radius(self, runner, loaded_project, test_video):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)

        result, data = _shape(runner, "--shape", "rounded", "--radius", "0.2")

        assert result.exit_code == 0, result.stdout
        assert _stored_shape() == {
            "kind": "rounded",
            "radius": 0.2,
            "border": {"width": 4, "color": "white"},
        }

    def test_rect_opt_out_is_recorded(self, runner, loaded_project, test_video):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)

        result, data = _shape(runner, "--shape", "rect")

        assert result.exit_code == 0, result.stdout
        assert _stored_shape() == {"kind": "rect"}

    def test_masks_and_preview_still_reported(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)

        result, data = _shape(runner, "--shape", "circle")

        assert result.exit_code == 0, result.stdout
        preview = data["shape_preview"]
        assert Path(preview["preview"]).exists()
        assert Path(preview["masks"]["content"]).exists()

    def test_dry_run_resolves_without_writing(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        before = Path("moviestar/spec.json").read_bytes()

        result, data = _shape(runner, "--shape", "circle", "--dry-run")

        assert result.exit_code == 0, result.stdout
        assert data["status"] == "would_set_slot_geometry"
        assert data["writes_spec"] is False
        assert Path("moviestar/spec.json").read_bytes() == before

    def test_later_geometry_edit_retains_stored_shape(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "circle")

        result, data = _shape(runner, "--at", "top-left")

        assert result.exit_code == 0, result.stdout
        assert _stored_shape() == {
            "kind": "circle",
            "border": {"width": 4, "color": "white"},
        }

    def test_reset_restores_the_default_shape(
        self, runner, loaded_project, test_video
    ):
        """#498: reset restores the preset default, so the stored shape
        becomes the default circle rather than disappearing."""
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "rect", "--border", "none")

        result, data = _shape(runner, "--reset")

        assert result.exit_code == 0, result.stdout
        assert _stored_shape() == {
            "kind": "circle",
            "border": {"width": 4, "color": "white"},
        }


class TestBorderPersistence:
    def test_border_persists_with_the_shape(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)

        result, data = _shape(
            runner, "--shape", "circle", "--border", "4px white"
        )

        assert result.exit_code == 0, result.stdout
        assert _stored_shape() == {
            "kind": "circle",
            "border": {"width": 4, "color": "white"},
        }
        assert Path(data["shape_preview"]["masks"]["border_disc"]).exists()

    def test_bare_color_border_persists_default_width(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)

        result, data = _shape(runner, "--border", "white")

        assert result.exit_code == 0, result.stdout
        assert _stored_shape()["border"] == {"width": 4, "color": "white"}

    def test_border_none_removes_a_stored_border(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "circle", "--border", "4px white")

        result, data = _shape(runner, "--border", "none")

        assert result.exit_code == 0, result.stdout
        assert _stored_shape() == {"kind": "circle"}

    def test_geometry_edit_retains_stored_border(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "circle", "--border", "6px black")

        result, data = _shape(runner, "--at", "top-left")

        assert result.exit_code == 0, result.stdout
        assert _stored_shape()["border"] == {"width": 6, "color": "black"}

    def test_shape_restatement_retains_stored_border(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "circle", "--border", "4px white")

        result, data = _shape(runner, "--shape", "circle")

        assert result.exit_code == 0, result.stdout
        assert _stored_shape()["border"] == {"width": 4, "color": "white"}

    def test_border_alone_retains_stored_rounded_kind(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "rounded", "--radius", "0.2")

        result, data = _shape(runner, "--border", "white")

        assert result.exit_code == 0, result.stdout
        assert _stored_shape() == {
            "kind": "rounded",
            "radius": 0.2,
            "border": {"width": 4, "color": "white"},
        }

    def test_rect_with_border_persists_and_previews(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)

        result, data = _shape(
            runner, "--shape", "rect", "--border", "4px white"
        )

        assert result.exit_code == 0, result.stdout
        assert _stored_shape() == {
            "kind": "rect",
            "border": {"width": 4, "color": "white"},
        }
        assert "masks" not in data["shape_preview"]
        preview = Path(data["shape_preview"]["preview"]).read_text()
        assert 'width="354" height="354"' in preview
        assert 'fill="white"' in preview

    def test_switching_to_rect_drops_a_stored_border(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "circle", "--border", "4px white")

        result, data = _shape(runner, "--shape", "rect")

        assert result.exit_code == 0, result.stdout
        assert _stored_shape() == {"kind": "rect"}

    def test_export_dry_run_composites_the_border_disc(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "circle", "--border", "4px white")

        result = runner.invoke(cli, ["export", "--dry-run", "--out", "out.mp4"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        command = data["scene_render_commands"][0]["command"]
        graph = command[command.index("-filter_complex") + 1]
        assert "alphamerge[disc" in graph
        mask_inputs = [arg for arg in command if arg.endswith(".png")]
        assert len(mask_inputs) == 2

    def test_export_dry_run_composites_a_rect_border_without_masks(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "rect", "--border", "4px black")

        result = runner.invoke(cli, ["export", "--dry-run", "--out", "out.mp4"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        command = data["scene_render_commands"][0]["command"]
        graph = command[command.index("-filter_complex") + 1]
        assert "color=c=black@1.0:s=354x354" in graph
        assert "[bcol1][rectc1]overlay=4:4[slotv1]" in graph
        assert not any(arg.endswith(".png") for arg in command)


class TestFramingFlag:
    def test_framing_alone_persists_slot_framing(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video, framing="fit")

        result, data = _shape(runner, "--framing", "fill:center")

        assert result.exit_code == 0, result.stdout
        assert data["writes_spec"] is True
        spec = json.loads(Path("moviestar/spec.json").read_text())
        scene = next(
            item for item in spec["composition"] if item.get("name") == "demo"
        )
        inset_slot = next(
            slot for slot in scene["slots"] if slot["slot"] == "inset"
        )
        assert inset_slot["framing"] == {"mode": "fill", "anchor": "center"}

    def test_shape_and_framing_fix_fit_in_one_command(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video, framing="fit")
        _size_inset(runner)

        result, data = _shape(
            runner, "--shape", "circle", "--framing", "fill:center"
        )

        assert result.exit_code == 0, result.stdout
        assert _stored_shape() == {"kind": "circle"}

    def test_fit_error_hint_names_the_framing_flag(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video, framing="fit")
        _size_inset(runner)

        result, data = _shape(runner, "--shape", "circle")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")
        assert "--framing" in data["hint"]

    def test_invalid_framing_value_is_rejected(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result, data = _shape(runner, "--framing", "sideways")

        assert result.exit_code != 0
        assert_error_envelope(data, command="scenes geometry")


def _scene_spec(*, shape=None, framing=None, size=0.32):
    inset_geometry = {}
    if size is not None:
        inset_geometry["size"] = size
    if shape is not None:
        inset_geometry["shape"] = shape
    return {
        "version": "0.2",
        "revisions": [],
        "sources": [
            {"id": "screen", "path": "/tmp/screen.mp4", "operations": []},
            {"id": "speaker", "path": "/tmp/speaker.mp4", "operations": []},
        ],
        "composition_canvas": {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        },
        "composition": [
            {
                "name": "demo",
                "layout": {
                    "preset": "picture-in-picture",
                    "orientation": "vertical",
                    "geometry": {"inset": inset_geometry},
                },
                "slots": [
                    {
                        "slot": "main",
                        "source": "screen",
                        "source_from": "0:00:00.000",
                        "source_to": "0:00:01.000",
                    },
                    {
                        "slot": "inset",
                        "source": "speaker",
                        "source_from": "0:00:00.000",
                        "source_to": "0:00:01.000",
                        "framing": framing
                        or {"mode": "fill", "anchor": "center"},
                    },
                ],
            }
        ],
    }


class TestShapeSchema:
    def test_valid_circle_shape_passes(self):
        validate_spec(_scene_spec(shape={"kind": "circle"}))

    def test_valid_rounded_shape_with_radius_passes(self):
        validate_spec(_scene_spec(shape={"kind": "rounded", "radius": 0.15}))

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(SpecValidationError, match="kind"):
            validate_spec(_scene_spec(shape={"kind": "hexagon"}))

    def test_missing_kind_is_rejected(self):
        with pytest.raises(SpecValidationError, match="kind"):
            validate_spec(_scene_spec(shape={"radius": 0.15}))

    def test_unknown_shape_field_is_rejected(self):
        with pytest.raises(SpecValidationError, match="glow"):
            validate_spec(
                _scene_spec(shape={"kind": "circle", "glow": True})
            )

    def test_radius_on_circle_is_rejected(self):
        with pytest.raises(SpecValidationError, match="radius"):
            validate_spec(
                _scene_spec(shape={"kind": "circle", "radius": 0.2})
            )

    def test_radius_out_of_range_is_rejected(self):
        with pytest.raises(SpecValidationError, match="radius"):
            validate_spec(
                _scene_spec(shape={"kind": "rounded", "radius": 0.7})
            )

    def test_circle_on_non_square_region_is_rejected(self):
        with pytest.raises(SpecValidationError, match="square"):
            validate_spec(_scene_spec(shape={"kind": "circle"}, size=None))

    def test_fit_framing_under_curved_shape_is_rejected(self):
        with pytest.raises(SpecValidationError, match="fit"):
            validate_spec(
                _scene_spec(
                    shape={"kind": "circle"},
                    framing={"mode": "fit", "anchor": "center"},
                )
            )

    def test_valid_border_passes(self):
        validate_spec(
            _scene_spec(
                shape={
                    "kind": "circle",
                    "border": {"width": 4, "color": "white"},
                }
            )
        )

    def test_border_width_must_be_positive_int(self):
        with pytest.raises(SpecValidationError, match="width"):
            validate_spec(
                _scene_spec(
                    shape={
                        "kind": "circle",
                        "border": {"width": 0, "color": "white"},
                    }
                )
            )

    def test_border_color_is_validated(self):
        with pytest.raises(SpecValidationError, match="color"):
            validate_spec(
                _scene_spec(
                    shape={
                        "kind": "circle",
                        "border": {"width": 4, "color": "not a color!"},
                    }
                )
            )

    def test_border_unknown_field_is_rejected(self):
        with pytest.raises(SpecValidationError, match="glow"):
            validate_spec(
                _scene_spec(
                    shape={
                        "kind": "circle",
                        "border": {"width": 4, "color": "white", "glow": 2},
                    }
                )
            )

    def test_border_on_rect_passes(self):
        validate_spec(
            _scene_spec(
                shape={
                    "kind": "rect",
                    "border": {"width": 4, "color": "white"},
                }
            )
        )

    def test_fit_framing_under_rect_shape_passes(self):
        validate_spec(
            _scene_spec(
                shape={"kind": "rect"},
                framing={"mode": "fit", "anchor": "center"},
            )
        )


class TestShapeRenders:
    def test_export_dry_run_command_alphamerges_the_mask(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "circle")

        result = runner.invoke(cli, ["export", "--dry-run", "--out", "out.mp4"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        command = data["scene_render_commands"][0]["command"]
        graph = command[command.index("-filter_complex") + 1]
        assert "alphamerge" in graph
        # Content mask plus the default border's disc mask.
        mask_inputs = [arg for arg in command if arg.endswith(".png")]
        assert len(mask_inputs) == 2
        assert all(Path(mask) .exists() for mask in mask_inputs)

    def test_rect_shape_export_emits_unshaped_graph(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "rect")

        result = runner.invoke(cli, ["export", "--dry-run", "--out", "out.mp4"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        command = data["scene_render_commands"][0]["command"]
        graph = command[command.index("-filter_complex") + 1]
        assert "alphamerge" not in graph

    def test_export_renders_the_circle_for_real(
        self, runner, loaded_project, tmp_path_factory
    ):
        import subprocess

        clips = tmp_path_factory.mktemp("shape-clips")

        def _solid(name, color):
            path = clips / f"{name}.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi",
                    "-i", f"color=c={color}:s=320x240:d=2:r=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(path),
                ],
                check=True,
            )
            return str(path)

        loaded_project(
            [_solid("screen", "green"), _solid("speaker", "red")],
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
        _size_inset(runner)
        result, data = _shape(runner, "--shape", "circle", "--at", "top-left")
        assert result.exit_code == 0, result.stdout
        region = data["region"]

        result = runner.invoke(cli, ["export", "--out", "shaped.mp4"])

        assert result.exit_code == 0, result.stdout
        raw = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", "shaped.mp4",
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        spec = json.loads(Path("moviestar/spec.json").read_text())
        canvas_w = spec["composition_canvas"]["width"]

        def px(x, y):
            i = (y * canvas_w + x) * 3
            return raw[i], raw[i + 1], raw[i + 2]

        # The circle drops the inset's corner pixels, so the green main
        # shows through; the inset's center keeps the red speaker.
        corner = px(region["x"] + 2, region["y"] + 2)
        assert corner[1] > 120 and corner[0] < 100
        center = px(
            region["x"] + region["width"] // 2,
            region["y"] + region["height"] // 2,
        )
        assert center[0] > 120 and center[1] < 100

    def test_screenshot_command_includes_the_mask(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "circle")

        result = runner.invoke(
            cli,
            ["screenshot", "--at", "0.5", "--out", "shot.jpg", "--dry-run"],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        command = data["ffmpeg_command"]
        graph = command[command.index("-filter_complex") + 1]
        assert "alphamerge" in graph

    def test_screenshot_slot_report_includes_shape(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        _shape(runner, "--shape", "circle")

        result = runner.invoke(
            cli,
            ["screenshot", "--at", "0.5", "--out", "shot.jpg", "--dry-run"],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        inset = next(
            slot
            for slot in data["active_scene"]["slots"]
            if slot["slot"] == "inset"
        )
        assert inset["shape"]["kind"] == "circle"
