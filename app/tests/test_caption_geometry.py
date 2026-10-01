"""Caption geometry: canvas-relative defaults and PiP-style placement controls.

Caption size and placement resolve against the render canvas, so the same
recipe reads correctly on 1920x1080, a 1080x1920 Short, and a 480p source.
`captions placement` mirrors `scenes geometry`: named or exact sizes, nine
anchors, exact normalized centers, margins, per-scene/per-layout scope,
retained values, --reset, and --dry-run.
"""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.overlays import length_px, plan_overlays
from tests.conftest import (
    assert_error_envelope,
    inject_synthetic_transcript,
    make_segment,
    make_word,
)

# Default caption geometry in 1080-line reference pixels: the generate
# default style (social-bold, 72px) and the bottom preset margin (180px).
DEFAULT_FONT_AT_1080 = 72
DEFAULT_BOTTOM_MARGIN_FRACTION = 180 / 1080


@pytest.fixture
def runner():
    return CliRunner()


def _words():
    return [
        make_word("intro", 0.0, 0.3),
        make_word("setup", 0.4, 0.7),
        make_word("this", 0.8, 0.9),
        make_word("is", 1.0, 1.05),
        make_word("where", 1.1, 1.2),
        make_word("you", 1.25, 1.3),
        make_word("get", 1.35, 1.4),
        make_word("to", 1.45, 1.5),
        make_word("that", 1.55, 1.65),
        make_word("pain", 1.7, 1.95),
        make_word("demo", 2.1, 2.4),
        make_word("time", 2.5, 2.9),
        make_word("now", 3.0, 3.4),
    ]


def _segments():
    return [
        make_segment("intro setup", 0.0, 0.7),
        make_segment("this is where you get to that pain", 0.8, 1.95),
        make_segment("demo time now", 2.1, 3.4),
    ]


def _video(tmp_path: Path, size: str = "320x240") -> str:
    path = tmp_path / f"src-{size}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=duration=4:size={size}:rate=30",
            "-f", "lavfi", "-i", "sine=duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )
    return str(path)


def _invoke(runner, args, *, ok=True):
    result = runner.invoke(cli, args)
    if ok:
        assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def _project(runner, tmp_path, monkeypatch, *, canvas="short", scenes=1, size="320x240"):
    """Loaded, transcribed project with a derived caption track.

    ``canvas=None`` skips the scene composition (source-sized render).
    ``scenes=2`` builds intro (single) + demo (two-up) scenes.
    """
    video = _video(tmp_path, size)
    monkeypatch.chdir(tmp_path)
    _invoke(runner, ["load", video, "--as", "src_0", "--no-transcribe", "--no-frames"])
    inject_synthetic_transcript(tmp_path, _words(), _segments())
    if canvas is not None:
        args = [
            "scenes", "set", "--canvas", canvas,
            "--scene", "intro=single",
            "--slot", "intro:main=src_0", "--from", "0", "--to", "2",
            "--audio-from", "intro=src_0",
        ]
        if scenes == 2:
            args += [
                "--scene", "demo=two-up",
                "--slot", "demo:top=src_0", "--from", "2", "--to", "4",
                "--slot", "demo:bottom=src_0", "--from", "2", "--to", "4",
                "--audio-from", "demo=src_0",
            ]
        _invoke(runner, args)
        _invoke(runner, ["captions", "generate"])
    else:
        _invoke(runner, ["captions", "generate", "--source", "src_0"])


def _render_items(runner):
    """Caption overlay plans exactly as export would render them."""
    data = _invoke(runner, ["export", "--dry-run"])
    return [
        item for item in data["overlays"]["items"] if item["track"] == "captions"
    ]


def _bottom(bounds):
    return bounds["y"] + bounds["height"]


def _center(bounds):
    return (bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)


class TestViewportLengthUnits:
    @pytest.mark.parametrize(
        ("value", "axis", "expected"),
        [
            (12, "x", 12.0),
            ("12px", "y", 12.0),
            ("50%", "x", 540.0),
            ("5vw", "x", 54.0),
            ("10vh", "y", 192.0),
            ("6vmin", "min", 64.8),
            ("5vmax", "min", 96.0),
        ],
    )
    def test_lengths_resolve_against_canvas(self, value, axis, expected):
        assert length_px(value, (1080, 1920), axis, "test") == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("800 px", 800.0),
            ("+800px", 800.0),
            ("8e2px", 800.0),
            ("800PX", 800.0),
            (" 50 % ", 540.0),
            ("1e2", 100.0),
            ("6 vmin", 64.8),
        ],
    )
    def test_previously_accepted_length_spellings_still_resolve(
        self, value, expected
    ):
        # The pre-viewport parser used float() after stripping px/%, so
        # spaces, signs, and exponents were accepted and may be stored.
        axis = "x" if "%" in value else "min"
        assert length_px(value, (1080, 1920), axis, "t") == pytest.approx(expected)

    def test_unknown_unit_is_rejected(self):
        with pytest.raises(ValueError, match="vmin"):
            length_px("3em", (1080, 1920), "min", "test")

    def test_plan_resolves_viewport_font_and_margin(self):
        record = {
            "id": "caption_1",
            "track": "captions",
            "kind": "caption",
            "text": "hello",
            "from": "0",
            "to": "1",
            "position": {
                "anchor": "bottom-center",
                "margin_y": "16.6667vh",
                "coordinate_space": "canvas",
            },
            "style": {
                "resolved": {
                    "font_family": "Inter",
                    "font_weight": "bold",
                    "font_size": "6.6667vmin",
                    "stroke_width": "0.463vmin",
                    "max_width": "88%",
                }
            },
        }
        plan = plan_overlays([record], (1080, 1920), 0.0, 1.0)["plans"][0]
        assert plan["font_size"] == 72
        assert plan["stroke"]["width"] == 5
        assert _bottom(plan["estimated_bounds"]) == 1920 - 320


class TestCanvasRelativeDefaults:
    def test_short_canvas_default_clears_platform_ui(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        items = _render_items(runner)
        assert items
        for item in items:
            assert item["font_size"] == DEFAULT_FONT_AT_1080
            # Shorts/Reels/TikTok chrome covers roughly y > 1630 on 1920.
            assert _bottom(item["estimated_bounds"]) == round(
                1920 * (1 - DEFAULT_BOTTOM_MARGIN_FRACTION)
            )
            assert _bottom(item["estimated_bounds"]) < 1630

    def test_1080p_landscape_default_is_unchanged(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="1920x1080")
        for item in _render_items(runner):
            assert item["font_size"] == DEFAULT_FONT_AT_1080
            assert _bottom(item["estimated_bounds"]) == 1080 - 180

    def test_small_source_default_scales_down(self, runner, tmp_path, monkeypatch):
        _project(runner, tmp_path, monkeypatch, canvas=None, size="854x480")
        items = _render_items(runner)
        assert items
        for item in items:
            assert item["font_size"] == 32  # 72 * 480 / 1080
            assert _bottom(item["estimated_bounds"]) == 480 - 80

    def test_explicit_css_pixels_stay_absolute(self, runner, tmp_path, monkeypatch):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        _invoke(
            runner,
            ["captions", "generate", "--css", "font-size: 40px"],
        )
        for item in _render_items(runner):
            assert item["font_size"] == 40


class TestPlacementControls:
    def test_at_size_margin_width_resolve_in_render(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        data = _invoke(
            runner,
            [
                "captions", "placement",
                "--at", "top", "--size", "small",
                "--margin", "0.1", "--width", "0.6",
            ],
        )
        assert data["status"] == "caption_placement_set"
        assert data["writes_spec"] is True
        assert data["placement"]["default"] == {
            "at": "top", "size": "small", "margin": 0.1, "width": 0.6,
        }
        for item in _render_items(runner):
            assert item["font_size"] == 54  # small = 0.05 of 1080
            assert item["anchor"] == "top-center"
            assert item["estimated_bounds"]["y"] == 192  # 0.1 * 1920
            assert item["estimated_bounds"]["width"] <= 0.6 * 1080

    @pytest.mark.parametrize(
        ("size", "expected"), [("0.04", 43), ("48px", 48), ("large", 90)]
    )
    def test_size_accepts_fraction_pixels_and_names(
        self, runner, tmp_path, monkeypatch, size, expected
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        _invoke(runner, ["captions", "placement", "--size", size])
        assert {item["font_size"] for item in _render_items(runner)} == {expected}

    def test_x_y_place_the_block_center(self, runner, tmp_path, monkeypatch):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        data = _invoke(runner, ["captions", "placement", "--x", "0.5", "--y", "0.6"])
        assert data["placement"]["default"] == {"x": 0.5, "y": 0.6}
        for item in _render_items(runner):
            cx, cy = _center(item["estimated_bounds"])
            assert cx == pytest.approx(540, abs=1)
            assert cy == pytest.approx(1152, abs=1)

    @pytest.mark.parametrize(
        ("at", "anchor"),
        [
            ("top-left", "top-left"),
            ("left", "left"),
            ("right", "right"),
            ("bottom-right", "bottom-right"),
            ("lower-third-left", "bottom-left"),
        ],
    )
    def test_nine_anchors_and_legacy_aliases(
        self, runner, tmp_path, monkeypatch, at, anchor
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        _invoke(runner, ["captions", "placement", "--at", at])
        assert {item["anchor"] for item in _render_items(runner)} == {anchor}

    def test_unspecified_controls_retain_stored_values(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        _invoke(runner, ["captions", "placement", "--size", "small"])
        data = _invoke(runner, ["captions", "placement", "--at", "top"])
        assert data["placement"]["default"] == {"size": "small", "at": "top"}
        # --x/--y replaces the anchor placement but keeps size.
        data = _invoke(runner, ["captions", "placement", "--x", "0.5", "--y", "0.5"])
        assert data["placement"]["default"] == {"size": "small", "x": 0.5, "y": 0.5}
        data = _invoke(runner, ["captions", "placement", "--at", "bottom"])
        assert data["placement"]["default"] == {"size": "small", "at": "bottom"}

    def test_scene_override_inherits_default_fields(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short", scenes=2)
        _invoke(runner, ["captions", "placement", "--size", "large"])
        data = _invoke(
            runner, ["captions", "placement", "--scene", "demo", "--at", "top"]
        )
        assert data["placement"]["overrides"] == [
            {"selector": {"scene": "demo"}, "at": "top"}
        ]
        by_scene = {entry["scene"]: entry for entry in data["resolved_preview"]}
        assert by_scene["intro"]["rule"] == "default"
        assert by_scene["intro"]["resolved"]["anchor"] == "bottom-center"
        assert by_scene["demo"]["rule"] == "scene:demo"
        assert by_scene["demo"]["resolved"]["anchor"] == "top-center"
        for entry in by_scene.values():
            assert entry["resolved"]["font_size_px"] == 90
        # A later scene-scoped call edits that override in place.
        data = _invoke(
            runner, ["captions", "placement", "--scene", "demo", "--size", "small"]
        )
        assert data["placement"]["overrides"] == [
            {"selector": {"scene": "demo"}, "at": "top", "size": "small"}
        ]

    def test_scene_beats_layout_regardless_of_order(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short", scenes=2)
        _invoke(runner, ["captions", "placement", "--scene", "demo", "--at", "top"])
        data = _invoke(
            runner, ["captions", "placement", "--layout", "two-up", "--at", "center"]
        )
        demo = next(e for e in data["resolved_preview"] if e["scene"] == "demo")
        assert demo["rule"] == "scene:demo"
        assert demo["resolved"]["anchor"] == "top-center"
        demo_items = [
            item for item in _render_items(runner)
            if item["from"]["seconds"] >= 2.0
        ]
        assert demo_items
        assert {item["anchor"] for item in demo_items} == {"top-center"}

    def test_preview_block_matches_render(self, runner, tmp_path, monkeypatch):
        _project(runner, tmp_path, monkeypatch, canvas="short", scenes=2)
        data = _invoke(
            runner,
            ["captions", "placement", "--layout", "two-up", "--at", "center",
             "--size", "0.05"],
        )
        items = _render_items(runner)
        for entry in data["resolved_preview"]:
            block = entry["resolved"]["block"]
            match = [
                item for item in items if item["text"] == entry["resolved"]["sample_text"]
            ]
            assert match, entry
            assert {k: block[k] for k in ("x", "y", "width", "height")} == {
                k: match[0]["estimated_bounds"][k]
                for k in ("x", "y", "width", "height")
            }
            assert entry["estimated_baseline"]["y_px"] == _bottom(block)

    def test_reset_scene_then_everything(self, runner, tmp_path, monkeypatch):
        _project(runner, tmp_path, monkeypatch, canvas="short", scenes=2)
        _invoke(runner, ["captions", "placement", "--size", "small"])
        _invoke(runner, ["captions", "placement", "--scene", "demo", "--at", "top"])
        data = _invoke(runner, ["captions", "placement", "--scene", "demo", "--reset"])
        assert data["placement"] == {"default": {"size": "small"}, "overrides": []}
        data = _invoke(runner, ["captions", "placement", "--reset"])
        assert data["placement"] == {"default": {}, "overrides": []}
        for item in _render_items(runner):
            assert item["font_size"] == DEFAULT_FONT_AT_1080
            assert item["anchor"] == "bottom-center"

    def test_dry_run_resolves_without_writing(self, runner, tmp_path, monkeypatch):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        before = (tmp_path / "moviestar" / "spec.json").read_text()
        data = _invoke(
            runner, ["captions", "placement", "--at", "top", "--dry-run"]
        )
        assert data["status"] == "would_set_caption_placement"
        assert data["dry_run"] is True
        assert data["writes_spec"] is False
        assert data["resolved_preview"][0]["resolved"]["anchor"] == "top-center"
        assert (tmp_path / "moviestar" / "spec.json").read_text() == before

    def test_no_controls_reports_current_placement(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        data = _invoke(runner, ["captions", "placement"])
        assert data["status"] == "caption_placement"
        assert data["writes_spec"] is False
        entry = data["resolved_preview"][0]
        assert entry["rule"] == "default"
        assert entry["resolved"]["font_size_px"] == DEFAULT_FONT_AT_1080
        assert entry["resolved"]["canvas"] == {"width": 1080, "height": 1920}

    def test_report_without_composition_uses_source_canvas(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas=None, size="854x480")
        data = _invoke(runner, ["captions", "placement"])
        entry = data["resolved_preview"][0]
        assert entry["scene"] is None
        assert entry["resolved"]["canvas"] == {"width": 854, "height": 480}
        assert entry["resolved"]["font_size_px"] == 32

    def test_legacy_default_and_for_flags_still_work(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short", scenes=2)
        data = _invoke(
            runner,
            ["captions", "placement", "--default", "top",
             "--for", "scene:demo=center"],
        )
        assert data["placement"] == {
            "default": {"at": "top"},
            "overrides": [{"selector": {"scene": "demo"}, "at": "center"}],
        }

    def test_frozen_track_is_rejected_and_left_untouched(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        _invoke(runner, ["captions", "materialize"])
        spec_path = tmp_path / "moviestar" / "spec.json"
        spec = json.loads(spec_path.read_text())
        for overlay in spec["overlays"]:
            if overlay["track"] == "captions":
                overlay["text"] = "HAND EDITED"
        spec_path.write_text(json.dumps(spec, indent=2))

        data = _invoke(runner, ["captions", "placement", "--at", "top"], ok=False)

        assert_error_envelope(data, command="captions placement")
        assert "frozen" in data["error"]
        spec = json.loads(spec_path.read_text())
        assert {
            o["text"] for o in spec["overlays"] if o["track"] == "captions"
        } == {"HAND EDITED"}

    def test_overlays_dump_set_round_trip_keeps_geometry(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        _invoke(
            runner,
            ["captions", "placement", "--at", "top-left", "--size", "small",
             "--margin", "0.05,0.08", "--width", "0.5"],
        )
        before = _render_items(runner)
        out = tmp_path / "overlays.json"
        _invoke(runner, ["overlays", "dump", "--out", str(out)])
        _invoke(runner, ["overlays", "set", str(out)])
        assert _render_items(runner) == before

    @pytest.mark.parametrize(
        ("args", "needle"),
        [
            (["--at", "top", "--x", "0.5", "--y", "0.5"], "--at"),
            (["--x", "0.5"], "--y"),
            (["--margin", "0.1", "--x", "0.5", "--y", "0.5"], "--margin"),
            (["--x", "1.5", "--y", "0.5"], "0..1"),
            (["--size", "huge"], "small"),
            (["--size", "0.9"], "--size"),
            (["--width", "0"], "--width"),
            (["--margin", "0.9"], "--margin"),
            (["--at", "middle"], "top-left"),
            (["--reset", "--at", "top"], "--reset"),
            (["--scene", "intro", "--layout", "single", "--at", "top"], "--scene"),
            (["--scene", "outro", "--at", "top"], "intro"),
            (["--default", "top", "--at", "bottom"], "--default"),
        ],
    )
    def test_invalid_controls_explain_the_fix(
        self, runner, tmp_path, monkeypatch, args, needle
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        data = _invoke(runner, ["captions", "placement", *args], ok=False)
        assert_error_envelope(data, command="captions placement")
        assert needle in data["error"] + " " + data.get("hint", "")


class TestManualOverlayCanvasUnits:
    def test_overlays_add_accepts_vmin_and_corner_presets(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        _invoke(
            runner,
            [
                "overlays", "add", "--track", "titles", "--text", "Hello",
                "--from", "0", "--to", "1", "--position", "top-left",
                "--css", "font-size: 6vmin",
            ],
        )
        data = _invoke(runner, ["export", "--dry-run"])
        title = next(
            item for item in data["overlays"]["items"] if item["track"] == "titles"
        )
        assert title["font_size"] == 65  # 6% of 1080
        assert title["anchor"] == "top-left"

    def test_bad_viewport_size_explains_units(self, runner, tmp_path, monkeypatch):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        data = _invoke(
            runner,
            [
                "overlays", "add", "--text", "Hello", "--from", "0", "--to", "1",
                "--css", "font-size: bigvmin",
            ],
            ok=False,
        )
        assert data["command"] == "overlays add"
        assert "vmin" in data["error"]


class TestReviewRegressions:
    def test_spaced_px_max_width_still_exports(self, runner, tmp_path, monkeypatch):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        _invoke(
            runner,
            [
                "overlays", "add", "--track", "titles", "--text", "Example",
                "--from", "0", "--to", "1", "--css", "max-width: 800 px",
            ],
        )
        data = _invoke(runner, ["export", "--dry-run"])
        assert any(item["track"] == "titles" for item in data["overlays"]["items"])

    @pytest.mark.parametrize("command", ["generate", "import"])
    def test_unknown_caption_position_returns_error_envelope(
        self, runner, tmp_path, monkeypatch, command
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        if command == "generate":
            args = ["captions", "generate", "--position", "middle"]
        else:
            srt = tmp_path / "cues.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
            args = [
                "captions", "import", str(srt), "--track", "imported",
                "--position", "middle",
            ]
        result = runner.invoke(cli, args)
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["command"] == f"captions {command}"
        assert "'middle'" in data["error"]
        assert "bottom" in data["error"] and "top-left" in data["error"]


class TestRegenerateKeepsPlacement:
    """#27: re-running `captions generate` must not silently discard
    placement tuned with `captions placement`."""

    def _placement(self, runner):
        return _invoke(runner, ["captions", "placement"])["placement"]

    def test_regenerate_keeps_placement_and_says_so(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short", scenes=2)
        _invoke(runner, ["captions", "placement", "--size", "large", "--width", "0.7"])
        _invoke(runner, ["captions", "placement", "--scene", "demo", "--at", "top"])
        before = self._placement(runner)

        data = _invoke(runner, ["captions", "generate", "--style", "caption-default"])

        assert data["placement"]["status"] == "kept"
        assert data["placement"]["policy"] == before
        assert self._placement(runner) == before
        items = _render_items(runner)
        assert {item["font_size"] for item in items} == {90}
        demo = [item for item in items if item["from"]["seconds"] >= 2.0]
        assert demo and {item["anchor"] for item in demo} == {"top-center"}

    def test_explicit_position_layers_over_kept_placement(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short", scenes=2)
        _invoke(runner, ["captions", "placement", "--x", "0.5", "--y", "0.5",
                         "--size", "small"])
        _invoke(runner, ["captions", "placement", "--scene", "demo", "--at", "top"])

        data = _invoke(runner, ["captions", "generate", "--position", "bottom"])

        assert data["placement"]["status"] == "kept"
        assert self._placement(runner) == {
            "default": {"size": "small", "at": "bottom"},
            "overrides": [{"selector": {"scene": "demo"}, "at": "top"}],
        }

    def test_reset_placement_starts_fresh(self, runner, tmp_path, monkeypatch):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        _invoke(runner, ["captions", "placement", "--size", "small"])

        data = _invoke(runner, ["captions", "generate", "--reset-placement"])

        assert data["placement"]["status"] == "reset"
        assert self._placement(runner) == {"default": {}, "overrides": []}
        assert {item["font_size"] for item in _render_items(runner)} == {
            DEFAULT_FONT_AT_1080
        }

    def test_first_generate_reports_default_placement(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        data = _invoke(runner, ["captions", "generate", "--track", "second"])
        assert data["placement"] == {
            "status": "default",
            "policy": {"default": {}, "overrides": []},
        }

    def test_overrides_for_removed_scenes_are_reported_and_removable(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short", scenes=2)
        _invoke(runner, ["captions", "placement", "--scene", "demo", "--at", "top"])
        _invoke(
            runner,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "intro=single",
                "--slot", "intro:main=src_0", "--from", "0", "--to", "4",
                "--audio-from", "intro=src_0",
            ],
        )

        data = _invoke(runner, ["captions", "generate"])

        [warning] = [
            w for w in data["warnings"]
            if w["code"] == "caption_placement_override_unmatched"
        ]
        assert warning["selectors"] == [{"scene": "demo"}]
        assert "--scene demo --reset" in warning["hint"]
        assert data["placement"]["policy"]["overrides"] == [
            {"selector": {"scene": "demo"}, "at": "top"}
        ]

        cleared = _invoke(
            runner, ["captions", "placement", "--scene", "demo", "--reset"]
        )
        assert cleared["placement"]["overrides"] == []

    def test_stale_override_hint_targets_the_regenerated_track(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short", scenes=2)
        _invoke(runner, ["captions", "generate", "--track", "translation"])
        for track in ("captions", "translation"):
            _invoke(
                runner,
                ["captions", "placement", "--track", track,
                 "--scene", "demo", "--at", "top"],
            )
        _invoke(
            runner,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "intro=single",
                "--slot", "intro:main=src_0", "--from", "0", "--to", "4",
                "--audio-from", "intro=src_0",
            ],
        )

        data = _invoke(runner, ["captions", "generate", "--track", "translation"])
        [warning] = [
            w for w in data["warnings"]
            if w["code"] == "caption_placement_override_unmatched"
        ]
        command = warning["hint"].split("'")[1]
        assert "--track translation" in command

        _invoke(runner, command.split()[1:])
        remaining = {
            track: _invoke(runner, ["captions", "placement", "--track", track])[
                "placement"
            ]["overrides"]
            for track in ("captions", "translation")
        }
        assert remaining["translation"] == []
        assert remaining["captions"] == [
            {"selector": {"scene": "demo"}, "at": "top"}
        ]

    def test_invalid_position_is_rejected_even_when_overrides_cover_every_cue(
        self, runner, tmp_path, monkeypatch
    ):
        _project(runner, tmp_path, monkeypatch, canvas="short")
        _invoke(runner, ["captions", "placement", "--scene", "intro", "--at", "top"])
        before = self._placement(runner)

        result = runner.invoke(cli, ["captions", "generate", "--position", "middle"])

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["command"] == "captions generate"
        assert "'middle'" in data["error"] and "top-left" in data["error"]
        assert self._placement(runner) == before
