"""M26 step 6 — capability preflight, truthful previews, parity.

Shaped renders fail before the render when the FFmpeg build lacks
alphamerge/geq; the layout catalog previews draw the real default
shapes at the standard scaled display size (#482); every verification
surface reports the shape block; and the scenes set pairing error
teaches the --slot/--from/--to rule (#481).
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar import ffmpeg as ffmpeg_module
from moviestar.cli import cli
from moviestar.ffmpeg import (
    FFmpegCapabilityError,
    preflight_shape_render_capabilities,
)
from tests.conftest import assert_error_envelope
from tests.test_m26_shape_surface import _pip_project, _shape, _size_inset


@pytest.fixture
def runner():
    return CliRunner()


def _shaped_slots():
    return [
        {
            "path": "/tmp/holden.mp4",
            "source_from": 0.0,
            "source_to": 1.0,
            "region": {"x": 0, "y": 0, "width": 120, "height": 120},
            "framing": {"mode": "fill", "anchor": "center"},
            "shape": {"kind": "circle", "content_mask": "/tmp/mask.png"},
        }
    ]


class TestShapeCapabilityPreflight:
    def test_missing_alphamerge_fails_before_the_render(self, monkeypatch):
        monkeypatch.setattr(
            ffmpeg_module, "_filter_names_cache", {"geq", "overlay"}
        )
        with pytest.raises(FFmpegCapabilityError, match="alphamerge"):
            preflight_shape_render_capabilities(_shaped_slots())

    def test_missing_geq_fails_before_the_render(self, monkeypatch):
        monkeypatch.setattr(
            ffmpeg_module, "_filter_names_cache", {"alphamerge", "overlay"}
        )
        with pytest.raises(FFmpegCapabilityError, match="geq"):
            preflight_shape_render_capabilities(_shaped_slots())

    def test_unshaped_slots_skip_the_probe(self, monkeypatch):
        def _explode(*args, **kwargs):  # pragma: no cover - guard
            raise AssertionError("unshaped slots must not probe filters")

        monkeypatch.setattr(ffmpeg_module, "ffmpeg_filter_available", _explode)
        slots = _shaped_slots()
        del slots[0]["shape"]
        preflight_shape_render_capabilities(slots)

    def _touch_slot_paths(self, tmp_path, slots):
        for slot in slots:
            path = tmp_path / Path(slot["path"]).name
            path.write_bytes(b"")
            slot["path"] = str(path)
        return slots

    def test_render_layout_video_preflights_shapes(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            ffmpeg_module, "_filter_names_cache", {"overlay"}
        )
        slots = self._touch_slot_paths(tmp_path, _shaped_slots())
        with pytest.raises(FFmpegCapabilityError):
            ffmpeg_module.render_layout_video(
                slots, "/tmp/out.mp4", (320, 240), 1.0
            )

    def test_render_layout_frame_preflights_shapes(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            ffmpeg_module, "_filter_names_cache", {"overlay"}
        )
        slots = self._touch_slot_paths(tmp_path, _shaped_slots())
        with pytest.raises(FFmpegCapabilityError):
            ffmpeg_module.render_layout_frame(
                slots, "/tmp/out.jpg", (320, 240)
            )


class TestTruthfulCatalogPreview:
    def test_pip_preview_draws_the_default_circle(
        self, runner, loaded_project, test_video
    ):
        loaded_project([test_video], names=["screen"], frames=False)
        result = runner.invoke(
            cli, ["layouts", "preview", "picture-in-picture"]
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        geometry = data["layout"]["geometry"]["inset"]
        assert geometry["shape"]["kind"] == "circle"
        svg = Path(data["out"]).read_text()
        assert "<circle" in svg
        assert ">inset</text>" in svg

    def test_pip_preview_region_is_square_default(
        self, runner, loaded_project, test_video
    ):
        loaded_project([test_video], names=["screen"], frames=False)
        result = runner.invoke(
            cli, ["layouts", "preview", "picture-in-picture"]
        )

        data = json.loads(result.stdout)
        region = data["layout"]["regions"]["inset"]
        assert region["width"] == region["height"]

    def test_single_preview_still_draws_rects(
        self, runner, loaded_project, test_video
    ):
        loaded_project([test_video], names=["screen"], frames=False)
        result = runner.invoke(cli, ["layouts", "preview", "single"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        svg = Path(data["out"]).read_text()
        assert "<circle" not in svg

    def test_shape_preview_uses_scaled_display_size(
        self, runner, loaded_project, test_video
    ):
        """#482: the shape preview from scenes geometry uses the same
        scaled display attrs as the standard catalog previews."""
        _pip_project(runner, loaded_project, test_video)
        _size_inset(runner)
        result, data = _shape(runner, "--shape", "circle")

        assert result.exit_code == 0, result.stdout
        svg = Path(data["shape_preview"]["preview"]).read_text()
        first_line = svg.splitlines()[0]
        assert 'width="405"' in first_line
        assert 'height="720"' in first_line
        assert 'viewBox="0 0 1080 1920"' in first_line


class TestVerificationParity:
    def test_scenes_list_reports_the_shape(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(cli, ["scenes", "list"])

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        scene = data["scenes"][0]
        shape = scene["layout"]["geometry"]["inset"]["shape"]
        assert shape["kind"] == "circle"

    def test_watch_dry_run_reports_the_shape(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            ["watch", "--from", "0", "--to", "1", "--dry-run",
             "--out", "w.mp4"],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        graph_command = data["scene_render_commands"][0]["command"]
        graph = graph_command[graph_command.index("-filter_complex") + 1]
        assert "alphamerge" in graph

    def test_inspect_scene_slots_report_the_shape(
        self, runner, loaded_project, test_video
    ):
        _pip_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            ["inspect", "--from", "0", "--to", "1", "--dry-run"],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        text = json.dumps(data)
        assert '"kind": "circle"' in text


class TestScenesSetPairingHint:
    def test_count_mismatch_teaches_the_pairing_rule(
        self, runner, loaded_project, test_video
    ):
        """#481: the count-mismatch error shows the --from/--to
        pairing-by-position rule with an example."""
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
                "--slot", "demo:main=screen",
                "--slot", "demo:inset=speaker",
                "--from", "0", "--to", "1",
            ],
        )

        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes set")
        assert "--from" in data["hint"] and "--to" in data["hint"]
        assert "pair" in data["hint"].lower()
