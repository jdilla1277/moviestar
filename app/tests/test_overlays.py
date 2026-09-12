"""Overlay render planning (M19 renderer slice)."""

import pytest

from moviestar.overlays import ffmpeg_color, overlay_plan_summary, plan_overlays

CANVAS = (1080, 1920)


def _record(**kwargs) -> dict:
    record = {
        "id": "manual_0001",
        "track": "titles",
        "kind": "manual",
        "from": "0:00:01.000",
        "to": "0:00:04.000",
        "text": "PM in the age of AI",
        "z_index": 200,
        "position": {
            "preset": "top",
            "coordinate_space": "canvas",
            "safe_area": True,
            "anchor": "top-center",
            "margin_y": 160,
        },
        "style": {
            "preset": "title",
            "css": None,
            "resolved": {
                "font_family": "Inter",
                "font_size": 96,
                "font_weight": "bold",
                "color": "#ffffff",
                "stroke_color": "#000000",
                "stroke_width": 3,
                "text_align": "center",
                "max_width": "90%",
            },
        },
    }
    record.update(kwargs)
    return record


class TestFfmpegColor:
    def test_hex_color_with_full_alpha(self):
        assert ffmpeg_color("#ffe94a") == "0xffe94a@1.0"

    def test_short_hex_expands(self):
        assert ffmpeg_color("#fff") == "0xffffff@1.0"

    def test_named_color_passes_through(self):
        assert ffmpeg_color("white", 0.5) == "white@0.5"

    def test_rgba_converts_and_multiplies_alpha(self):
        assert ffmpeg_color("rgba(255, 0, 80, 0.5)", 0.5) == "0xff0050@0.25"

    def test_eight_digit_hex_folds_alpha(self):
        assert ffmpeg_color("#ff005080") == "0xff0050@0.502"


class TestWindowFiltering:
    def test_timing_is_half_open(self):
        overlays = [_record(**{"from": "0:00:01.000", "to": "0:00:02.000"})]
        # Overlay ends exactly at window start -> inactive.
        assert plan_overlays(overlays, CANVAS, 2.0, 3.0)["plans"] == []
        # Overlay starts exactly at window end -> inactive.
        assert plan_overlays(overlays, CANVAS, 0.0, 1.0)["plans"] == []
        # Overlap -> active.
        assert len(plan_overlays(overlays, CANVAS, 1.5, 3.0)["plans"]) == 1

    def test_enable_times_are_window_local(self):
        overlays = [_record(**{"from": "0:00:03.500", "to": "0:00:05.000"})]
        [plan] = plan_overlays(overlays, CANVAS, 3.0, 6.0)["plans"]
        assert plan["enable_from"] == pytest.approx(0.5)
        assert plan["enable_to"] == pytest.approx(2.0)

    def test_plans_sorted_by_z_index_then_track(self):
        overlays = [
            _record(id="manual_0001", z_index=300, track="titles"),
            _record(id="manual_0002", z_index=100, track="labels"),
        ]
        plans = plan_overlays(overlays, CANVAS, 0.0, 6.0)["plans"]
        assert [p["id"] for p in plans] == ["manual_0002", "manual_0001"]


class TestTextLayout:
    def test_wraps_to_max_width(self):
        [plan] = plan_overlays([_record()], CANVAS, 0.0, 6.0)["plans"]
        # 19 chars at 96px * 0.55em exceeds 90% of 1080 -> two lines.
        assert len(plan["lines"]) == 2
        assert " ".join(plan["lines"]) == "PM in the age of AI"

    def test_manual_line_breaks_always_honored(self):
        record = _record(text="one\ntwo")
        record["style"]["resolved"]["max_width"] = None
        [plan] = plan_overlays([record], CANVAS, 0.0, 6.0)["plans"]
        assert plan["lines"] == ["one", "two"]

    def test_text_transform_uppercase(self):
        record = _record(text="loud")
        record["style"]["resolved"]["text_transform"] = "uppercase"
        [plan] = plan_overlays([record], CANVAS, 0.0, 6.0)["plans"]
        assert plan["text"] == "LOUD"

    def test_scale_transform_multiplies_font_size(self):
        record = _record()
        record["style"]["resolved"]["transform"] = "rotate(-8deg) scale(1.5)"
        [plan] = plan_overlays([record], CANVAS, 0.0, 6.0)["plans"]
        assert plan["font_size"] == 144
        assert plan["rotate_deg"] == pytest.approx(-8.0)


class TestBoundsAndWarnings:
    def test_top_preset_bounds_anchor_math(self):
        record = _record(text="Hi")
        record["style"]["resolved"]["max_width"] = None
        [plan] = plan_overlays([record], CANVAS, 0.0, 6.0)["plans"]
        bounds = plan["estimated_bounds"]
        assert bounds["estimate"] is True
        assert bounds["y"] == 160
        # Centered: x = (1080 - est_w) / 2.
        assert bounds["x"] == pytest.approx(
            (1080 - bounds["width"]) / 2, abs=1
        )

    def test_normalized_xy_centers_on_target(self):
        record = _record(
            position={
                "coordinate_space": "canvas",
                "x": 0.5,
                "y": 0.5,
                "anchor": "center",
            }
        )
        record["text"] = "Hi"
        record["style"]["resolved"]["max_width"] = None
        [plan] = plan_overlays([record], CANVAS, 0.0, 6.0)["plans"]
        bounds = plan["estimated_bounds"]
        assert bounds["x"] + bounds["width"] / 2 == pytest.approx(540, abs=1)
        assert bounds["y"] + bounds["height"] / 2 == pytest.approx(960, abs=1)

    def test_overflow_produces_warning(self):
        record = _record(text="an extremely long single headline")
        record["style"]["resolved"]["max_width"] = None
        record["style"]["resolved"]["font_size"] = 200
        planned = plan_overlays([record], (320, 240), 0.0, 6.0)
        assert any("overflow" in w for w in planned["warnings"])

    def test_letter_spacing_warns(self):
        record = _record()
        record["style"]["resolved"]["letter_spacing"] = "2px"
        planned = plan_overlays([record], CANVAS, 0.0, 6.0)
        assert any("letter-spacing" in w for w in planned["warnings"])

    @pytest.mark.parametrize(
        "tracks",
        [("captions", "captions-2"), ("captions", "captions")],
    )
    def test_temporally_overlapping_bounds_warn_with_both_overlay_ids(
        self, tracks
    ):
        first = _record(id="cap_0001", track=tracks[0])
        second = _record(
            id="cap_0002",
            track=tracks[1],
            **{"from": "0:00:02.000", "to": "0:00:05.000"},
        )

        planned = plan_overlays([first, second], CANVAS, 0.0, 6.0)

        [collision] = planned["collisions"]
        assert collision["overlay_ids"] == ["cap_0001", "cap_0002"]
        assert collision["from"]["seconds"] == pytest.approx(2.0)
        assert collision["to"]["seconds"] == pytest.approx(4.0)
        assert collision["estimated_intersection"]["width"] > 0
        assert collision["estimated_intersection"]["height"] > 0
        [warning] = planned["warnings"]
        assert "cap_0001" in warning
        assert "cap_0002" in warning
        assert "estimated bounds overlap" in warning

    def test_same_bounds_at_adjacent_half_open_times_do_not_warn(self):
        first = _record(**{"from": "0:00:01.000", "to": "0:00:02.000"})
        second = _record(
            id="manual_0002",
            **{"from": "0:00:02.000", "to": "0:00:03.000"},
        )

        planned = plan_overlays([first, second], CANVAS, 0.0, 6.0)

        assert planned["collisions"] == []
        assert planned["warnings"] == []

    def test_overlapping_times_with_disjoint_bounds_do_not_warn(self):
        first = _record()
        second = _record(
            id="manual_0002",
            position={
                "preset": "bottom",
                "coordinate_space": "canvas",
                "safe_area": True,
                "anchor": "bottom-center",
                "margin_y": 160,
            },
        )

        planned = plan_overlays([first, second], CANVAS, 0.0, 6.0)

        assert planned["collisions"] == []
        assert planned["warnings"] == []


class TestFontsAndSummary:
    def test_fonts_deduped_across_plans(self):
        overlays = [
            _record(id="manual_0001"),
            _record(id="manual_0002"),
        ]
        planned = plan_overlays(overlays, CANVAS, 0.0, 6.0)
        [font] = planned["fonts"]
        assert font["family"] == "Inter"
        assert font["source"] == "bundled"

    def test_summary_shape(self):
        [plan] = plan_overlays([_record()], CANVAS, 0.0, 6.0)["plans"]
        summary = overlay_plan_summary(plan)
        assert summary["id"] == "manual_0001"
        assert summary["from"]["seconds"] == pytest.approx(1.0)
        assert summary["font"]["source"] == "bundled"
        assert "path" not in summary["font"]
        assert summary["estimated_bounds"]["estimate"] is True


def _caption_record(**kwargs) -> dict:
    record = {
        "id": "cap_0001",
        "track": "captions",
        "kind": "caption",
        "from": "0:00:00.200",
        "to": "0:00:02.000",
        "text": "hello world again",
        "z_index": 100,
        "position": {
            "preset": "bottom",
            "coordinate_space": "canvas",
            "safe_area": True,
            "anchor": "bottom-center",
            "margin_y": 180,
        },
        "style": {
            "preset": "social-bold",
            "css": None,
            "resolved": {
                "font_family": "Inter",
                "font_size": 72,
                "font_weight": "bold",
                "color": "#ffffff",
                "stroke_color": "#000000",
                "stroke_width": 5,
                "text_align": "center",
                "max_width": "88%",
            },
        },
        "highlight": {"mode": "spoken-word", "color": "#ffe94a"},
        "tokens": [
            {"text": "hello", "from": "0:00:00.200", "to": "0:00:00.600"},
            {"text": "world", "from": "0:00:00.600", "to": "0:00:01.100"},
            {"text": "again", "from": "0:00:01.400", "to": "0:00:01.900"},
        ],
    }
    record.update(kwargs)
    return record


class TestSpokenWordHighlightPlanning:
    def test_highlight_plan_carries_local_token_times(self):
        planned = plan_overlays([_caption_record()], CANVAS, 0.0, 2.0)
        [plan] = planned["plans"]
        highlight = plan["highlight"]
        assert highlight["mode"] == "spoken-word"
        assert highlight["color"] == "#ffe94a"
        assert [t["word_index"] for t in highlight["tokens"]] == [0, 1, 2]
        assert highlight["tokens"][0]["from_local"] == pytest.approx(0.2)
        assert not any(
            "not rendered" in w for w in planned["warnings"]
        )

    def test_missing_tokens_raise_with_fix(self):
        record = _caption_record(tokens=None)
        record.pop("tokens")
        with pytest.raises(ValueError) as exc:
            plan_overlays([record], CANVAS, 0.0, 2.0)
        assert "no word tokens" in str(exc.value)
        assert "captions generate" in str(exc.value)

    def test_token_text_drift_falls_back_with_warning(self):
        record = _caption_record(text="edited text no longer matching")
        planned = plan_overlays([record], CANVAS, 0.0, 2.0)
        [plan] = planned["plans"]
        assert plan["highlight"] is None
        assert any("token(s)" in w for w in planned["warnings"])

    def test_zero_duration_tokens_become_gaps(self):
        record = _caption_record()
        record["tokens"][1]["to"] = record["tokens"][1]["from"]
        planned = plan_overlays([record], CANVAS, 0.0, 2.0)
        [plan] = planned["plans"]
        assert [t["word_index"] for t in plan["highlight"]["tokens"]] == [0, 2]

    def test_highlight_drops_box_and_rotation_with_warnings(self):
        record = _caption_record()
        record["style"]["resolved"]["background_color"] = "#000000"
        record["style"]["resolved"]["transform"] = "rotate(-8deg)"
        planned = plan_overlays([record], CANVAS, 0.0, 2.0)
        [plan] = planned["plans"]
        assert plan["box"] is None
        assert plan["rotate_deg"] == 0.0
        assert any("background boxes" in w for w in planned["warnings"])
        assert any("transforms" in w for w in planned["warnings"])

    def test_summary_reports_rendered_highlight(self):
        planned = plan_overlays([_caption_record()], CANVAS, 0.0, 2.0)
        summary = overlay_plan_summary(planned["plans"][0])
        assert summary["highlight"]["rendered"] is True
        assert summary["highlight"]["tokens_count"] == 3
