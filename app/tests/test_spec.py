"""Tests for moviestar.spec — v0.2 schema, per-source resolver, mutations.

M13a removed v0.1; v0.2 is the only on-disk format. The v0.1
"reload-the-project" friendly error path is still tested (via
``is_v01_spec``) so anyone with a stale v0.1 spec on disk gets a
good message.
"""

import json
from pathlib import Path

import pytest

from moviestar.project import MOVIESTAR_DIR
from moviestar.spec import (
    CANVAS_PRESETS,
    SCHEMA_VERSION,
    SPEC_FILE,
    EffectiveTimeline,
    SceneValidationError,
    SpecValidationError,
    TimelineStep,
    append_cut,
    append_overlay,
    append_revision,
    append_trim,
    empty_spec,
    ensure_scene_motion_identities,
    get_source,
    is_v01_spec,
    layout_orientation,
    layout_regions,
    layout_slots,
    load_spec,
    parse_timecode_string,
    pop_last_op,
    pop_revision,
    resolve_all_sources,
    resolve_source,
    resolve_source_history,
    reconcile_motion_lifecycle,
    result_to_source_time,
    save_spec,
    set_composition,
    set_audio_mix,
    set_layout_composition,
    set_motion,
    set_overlays,
    set_scene_composition,
    slice_audio_from_anchor,
    source_ids,
    source_range_in_result,
    sync_project_sources,
    validate_spec,
)


def _spec(sources: list[dict]) -> dict:
    """Build a v0.2 spec for tests. ``sources`` is a list of dicts with
    {id, path, operations} or just {id, path} (operations defaults empty)."""
    normalized = []
    for s in sources:
        normalized.append(
            {
                "id": s["id"],
                "path": s.get("path", f"/tmp/{s['id']}.mp4"),
                "operations": s.get("operations", []),
            }
        )
    return {
        "version": SCHEMA_VERSION,
        "sources": normalized,
        "composition": None,
        "revisions": [],
    }


def _trim_op(from_text: str, to_text: str) -> dict:
    return {"type": "trim", "from": from_text, "to": to_text}


def _cut_op(from_text: str, to_text: str) -> dict:
    return {"type": "cut", "from": from_text, "to": to_text}


def _overlay(overlay_id: str = "manual_0001", text: str = "Title") -> dict:
    return {
        "id": overlay_id,
        "track": "titles",
        "kind": "manual",
        "timing": {
            "space": "result",
            "from": "0:00:00.000",
            "to": "0:00:01.000",
        },
        "text": text,
        "z_index": 200,
        "position": {"preset": "top"},
        "style": {"preset": "title"},
    }


def _audio_mix(*, gain_db: float = 0.0, tracks: list[dict] | None = None) -> dict:
    return {
        "source_audio": {
            "muted": False,
            "gain_db": gain_db,
            "fade_in": 0.0,
            "fade_out": 0.0,
        },
        "tracks": [] if tracks is None else tracks,
    }


def _audio_track(
    track_id: str = "music",
    *,
    duck_under: list[str] | None = None,
) -> dict:
    return {
        "id": track_id,
        "kind": "music",
        "path": f"/tmp/{track_id}.wav",
        "at": "0:00:00.000",
        "source_from": "0:00:00.000",
        "source_to": None,
        "gain_db": -18.0,
        "muted": False,
        "loop": True,
        "fade_in": 1.0,
        "fade_out": 2.0,
        "ducking": (
            None
            if duck_under is None
            else {"under": duck_under, "preset": "speech"}
        ),
    }


class TestParseTimecodeString:
    def test_parses_zero(self):
        assert parse_timecode_string("0:00:00.000", "where") == 0.0

    def test_parses_simple_seconds(self):
        assert parse_timecode_string("0:00:10.500", "where") == 10.5

    def test_parses_minutes(self):
        assert parse_timecode_string("0:01:30.000", "where") == 90.0

    def test_parses_hours(self):
        assert parse_timecode_string("1:30:45.250", "where") == 5445.25

    def test_raises_on_malformed(self):
        with pytest.raises(SpecValidationError, match="malformed"):
            parse_timecode_string("not-a-timecode", "where")


class TestValidateSpec:
    def test_valid_minimal_spec(self):
        validate_spec(_spec([{"id": "src_0", "path": "/x.mp4"}]))

    def test_valid_multi_source_spec(self):
        validate_spec(
            _spec(
                [
                    {"id": "holden", "path": "/h.mp4"},
                    {"id": "jdilla", "path": "/j.mp4"},
                ]
            )
        )

    def test_valid_with_trim_ops(self):
        spec = _spec(
            [
                {
                    "id": "holden",
                    "path": "/h.mp4",
                    "operations": [_trim_op("0:00:10.000", "0:00:30.000")],
                }
            ]
        )
        validate_spec(spec)

    def test_rejects_wrong_version(self):
        spec = _spec([{"id": "src_0", "path": "/x.mp4"}])
        spec["version"] = "0.1"
        with pytest.raises(SpecValidationError, match="version"):
            validate_spec(spec)

    def test_rejects_empty_sources(self):
        spec = {"version": SCHEMA_VERSION, "sources": [], "composition": None}
        with pytest.raises(SpecValidationError, match="at least one source"):
            validate_spec(spec)

    def test_rejects_duplicate_source_ids(self):
        spec = _spec(
            [
                {"id": "holden", "path": "/h.mp4"},
                {"id": "holden", "path": "/h2.mp4"},
            ]
        )
        with pytest.raises(SpecValidationError, match="duplicate"):
            validate_spec(spec)

    def test_rejects_missing_source_id(self):
        spec = {
            "version": SCHEMA_VERSION,
            "sources": [{"path": "/x.mp4", "operations": []}],
            "composition": None,
        }
        with pytest.raises(SpecValidationError, match="id"):
            validate_spec(spec)

    def test_rejects_dict_timecode(self):
        """v0.2 timecodes are strings on disk; the v0.1 {text, seconds}
        dict shape is rejected."""
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [
                        {
                            "type": "trim",
                            "from": {"text": "0:00:10.000", "seconds": 10.0},
                            "to": {"text": "0:00:30.000", "seconds": 30.0},
                        }
                    ],
                }
            ]
        )
        with pytest.raises(SpecValidationError, match="timecode"):
            validate_spec(spec)

    def test_valid_with_cut_op(self):
        spec = _spec(
            [
                {
                    "id": "holden",
                    "path": "/h.mp4",
                    "operations": [_cut_op("0:00:10.000", "0:00:30.000")],
                }
            ]
        )
        validate_spec(spec)

    def test_valid_with_mixed_trim_and_cut(self):
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [
                        _trim_op("0:00:00.000", "0:01:00.000"),
                        _cut_op("0:00:10.000", "0:00:30.000"),
                    ],
                }
            ]
        )
        validate_spec(spec)

    def test_rejects_inverted_cut(self):
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [_cut_op("0:00:30.000", "0:00:10.000")],
                }
            ]
        )
        with pytest.raises(SpecValidationError, match="before"):
            validate_spec(spec)

    def test_rejects_inverted_trim(self):
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [_trim_op("0:00:30.000", "0:00:10.000")],
                }
            ]
        )
        with pytest.raises(SpecValidationError, match="before"):
            validate_spec(spec)

    def test_rejects_unknown_op_type(self):
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [
                        {
                            "type": "frobnicate",
                            "from": "0:00:10.000",
                            "to": "0:00:30.000",
                        }
                    ],
                }
            ]
        )
        with pytest.raises(SpecValidationError, match="unknown type"):
            validate_spec(spec)

    def test_accepts_populated_composition_array(self):
        """M13b step 4: composition is now an array of segments OR null.
        Each segment names a source plus a snapshotted source-time range."""
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "source": "holden",
                "source_from": "0:00:01.000",
                "source_to": "0:00:02.000",
            }
        ]
        validate_spec(spec)

    def test_accepts_composition_canvas(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition_canvas"] = {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }
        validate_spec(spec)

    def test_rejects_odd_composition_canvas_dimension(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition_canvas"] = {
            "preset": None,
            "width": 1079,
            "height": 1920,
            "aspect_ratio": "1079:1920",
        }
        with pytest.raises(SpecValidationError, match="width"):
            validate_spec(spec)

    def test_accepts_composition_segment_framing(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "source": "holden",
                "source_from": "0:00:01.000",
                "source_to": "0:00:02.000",
                "framing": {"mode": "fill", "anchor": "left"},
            }
        ]
        validate_spec(spec)

    def test_accepts_numeric_fill_framing_anchor(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "source": "holden",
                "source_from": "0:00:01.000",
                "source_to": "0:00:02.000",
                "framing": {"mode": "fill", "x": 0.67, "y": 0.5},
            }
        ]
        validate_spec(spec)

    def test_accepts_global_layout_composition(self):
        spec = _spec(
            [
                {"id": "holden", "path": "/h.mp4"},
                {"id": "jdilla", "path": "/j.mp4"},
            ]
        )
        spec["composition_canvas"] = {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }
        spec["composition"] = [
            {
                "layout": {"preset": "two-up", "orientation": "vertical"},
                "slots": [
                    {
                        "slot": "top",
                        "source": "holden",
                        "source_from": "0:00:00.000",
                        "source_to": "0:00:01.000",
                    },
                    {
                        "slot": "bottom",
                        "source": "jdilla",
                        "source_from": "0:00:00.000",
                        "source_to": "0:00:01.000",
                        "framing": {"mode": "fill", "anchor": "center"},
                    },
                ],
            }
        ]
        validate_spec(spec)

    def test_rejects_global_layout_without_canvas(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "layout": {"preset": "single"},
                "slots": [
                    {
                        "slot": "main",
                        "source": "holden",
                        "source_from": "0:00:00.000",
                        "source_to": "0:00:01.000",
                    }
                ],
            }
        ]
        with pytest.raises(SpecValidationError, match="composition_canvas"):
            validate_spec(spec)

    def test_rejects_global_layout_missing_required_slot(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition_canvas"] = {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }
        spec["composition"] = [
            {
                "layout": {"preset": "two-up", "orientation": "vertical"},
                "slots": [
                    {
                        "slot": "top",
                        "source": "holden",
                        "source_from": "0:00:00.000",
                        "source_to": "0:00:01.000",
                    }
                ],
            }
        ]
        with pytest.raises(SpecValidationError, match="missing required slot"):
            validate_spec(spec)

    def test_rejects_layout_mixed_with_flat_segments(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "source": "holden",
                "source_from": "0:00:00.000",
                "source_to": "0:00:01.000",
            },
            {
                "layout": {"preset": "single"},
                "slots": [],
            },
        ]
        with pytest.raises(SpecValidationError, match="cannot mix"):
            validate_spec(spec)

    def test_rejects_unknown_framing_anchor(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "source": "holden",
                "source_from": "0:00:01.000",
                "source_to": "0:00:02.000",
                "framing": {"mode": "fill", "anchor": "nearby"},
            }
        ]
        with pytest.raises(SpecValidationError, match="anchor"):
            validate_spec(spec)

    def test_rejects_out_of_range_numeric_framing_anchor(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "source": "holden",
                "source_from": "0:00:01.000",
                "source_to": "0:00:02.000",
                "framing": {"mode": "fill", "x": 1.2},
            }
        ]
        with pytest.raises(SpecValidationError, match="normalized"):
            validate_spec(spec)

    def test_rejects_numeric_framing_anchor_for_fit(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "source": "holden",
                "source_from": "0:00:01.000",
                "source_to": "0:00:02.000",
                "framing": {"mode": "fit", "x": 0.67},
            }
        ]
        with pytest.raises(SpecValidationError, match="fill mode"):
            validate_spec(spec)

    def test_rejects_composition_segment_unknown_source(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "source": "missing",
                "source_from": "0:00:01.000",
                "source_to": "0:00:02.000",
            }
        ]
        with pytest.raises(SpecValidationError, match="unknown source"):
            validate_spec(spec)

    def test_rejects_composition_segment_missing_field(self):
        spec = _spec([{"id": "h", "path": "/h.mp4"}])
        spec["composition"] = [
            {"source": "h", "source_from": "0:00:01.000"}  # missing source_to
        ]
        with pytest.raises(SpecValidationError, match="source_to"):
            validate_spec(spec)

    def test_rejects_composition_segment_inverted(self):
        spec = _spec([{"id": "h", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "source": "h",
                "source_from": "0:00:02.000",
                "source_to": "0:00:01.000",
            }
        ]
        with pytest.raises(SpecValidationError, match="before"):
            validate_spec(spec)


class TestHelpers:
    def test_get_source_returns_source(self):
        spec = _spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )
        assert get_source(spec, "holden")["id"] == "holden"
        assert get_source(spec, "jdilla")["path"] == "/j.mp4"

    def test_get_source_raises_on_unknown(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        with pytest.raises(SpecValidationError, match="unknown source_id"):
            get_source(spec, "nope")

    def test_get_source_error_lists_available(self):
        spec = _spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )
        with pytest.raises(SpecValidationError) as excinfo:
            get_source(spec, "missing")
        assert "holden" in str(excinfo.value)
        assert "jdilla" in str(excinfo.value)

    def test_source_ids_preserves_order(self):
        spec = _spec(
            [
                {"id": "holden", "path": "/h.mp4"},
                {"id": "jdilla", "path": "/j.mp4"},
                {"id": "screenshare", "path": "/s.mp4"},
            ]
        )
        assert source_ids(spec) == ["holden", "jdilla", "screenshare"]


class TestResolveSource:
    def test_empty_source_returns_full_duration(self):
        spec = _spec([{"id": "src_0", "path": "/x.mp4"}])
        timeline = resolve_source(spec, "src_0", source_duration=100.0)
        assert isinstance(timeline, EffectiveTimeline)
        assert timeline.source_range == (0.0, 100.0)
        assert timeline.effective_duration == 100.0
        assert timeline.operations_applied == 0
        assert timeline.source_id == "src_0"

    def test_single_trim(self):
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [_trim_op("0:00:10.000", "0:00:30.000")],
                }
            ]
        )
        timeline = resolve_source(spec, "src_0", source_duration=100.0)
        assert timeline.source_range == (10.0, 30.0)
        assert timeline.effective_duration == 20.0
        assert timeline.operations_applied == 1

    def test_stacked_trims_compose(self):
        """The prototype's #1 bug regression — stacked trims must
        compose against the result, not the source."""
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [
                        # First trim: 10-50 of source. Result is 0-40.
                        _trim_op("0:00:10.000", "0:00:50.000"),
                        # Second trim: 5-20 of result. Result is 15-30 of source.
                        _trim_op("0:00:05.000", "0:00:20.000"),
                    ],
                }
            ]
        )
        timeline = resolve_source(spec, "src_0", source_duration=100.0)
        assert timeline.source_range == (15.0, 30.0)
        assert timeline.effective_duration == 15.0
        assert timeline.operations_applied == 2

    def test_per_source_isolation(self):
        """Each source's operations are independent. Editing holden
        doesn't change jdilla's resolved range."""
        spec = _spec(
            [
                {
                    "id": "holden",
                    "path": "/h.mp4",
                    "operations": [_trim_op("0:00:10.000", "0:00:30.000")],
                },
                {"id": "jdilla", "path": "/j.mp4", "operations": []},
            ]
        )
        h = resolve_source(spec, "holden", source_duration=100.0)
        j = resolve_source(spec, "jdilla", source_duration=100.0)
        assert h.source_range == (10.0, 30.0)
        assert h.operations_applied == 1
        assert j.source_range == (0.0, 100.0)
        assert j.operations_applied == 0

    def test_unknown_source_raises(self):
        spec = _spec([{"id": "holden", "path": "/h.mp4"}])
        with pytest.raises(SpecValidationError, match="unknown source_id"):
            resolve_source(spec, "missing", source_duration=100.0)

    def test_trim_only_has_single_segment(self):
        """Trim-only results stay single-segment; source_segments mirrors
        source_range. Callers naïve to cuts keep working."""
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [_trim_op("0:00:10.000", "0:00:30.000")],
                }
            ]
        )
        timeline = resolve_source(spec, "src_0", source_duration=100.0)
        assert timeline.source_segments == ((10.0, 30.0),)

    def test_single_cut_splits_into_two_segments(self):
        """A cut removes a range from the middle, producing two segments
        stitched together — agent sees one contiguous result, but the
        resolver tracks both segments for accurate rendering."""
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [_cut_op("0:00:10.000", "0:00:30.000")],
                }
            ]
        )
        timeline = resolve_source(spec, "src_0", source_duration=100.0)
        assert timeline.source_segments == ((0.0, 10.0), (30.0, 100.0))
        assert timeline.effective_duration == 80.0
        assert timeline.operations_applied == 1

    def test_source_range_is_outer_envelope_for_multi_segment(self):
        """source_range stays a single (from, to) tuple for backward-compat
        with trim-only callers; for multi-segment results it's the outer
        envelope. Precise rendering uses source_segments."""
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [_cut_op("0:00:10.000", "0:00:30.000")],
                }
            ]
        )
        timeline = resolve_source(spec, "src_0", source_duration=100.0)
        assert timeline.source_range == (0.0, 100.0)

    def test_cut_after_trim_composes_in_result_time(self):
        """A cut after a trim operates in the trimmed result-time, not
        source-time. The trim's window narrows what 'result-time' means."""
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [
                        # Trim keeps source 10..50 — result is 40s long.
                        _trim_op("0:00:10.000", "0:00:50.000"),
                        # Cut result-time 5..15 = source 15..25.
                        _cut_op("0:00:05.000", "0:00:15.000"),
                    ],
                }
            ]
        )
        timeline = resolve_source(spec, "src_0", source_duration=100.0)
        assert timeline.source_segments == ((10.0, 15.0), (25.0, 50.0))
        assert timeline.effective_duration == 30.0
        assert timeline.operations_applied == 2

    def test_trim_after_cut_narrows_multi_segment_result(self):
        """A trim after a cut walks the cut's stitched segments and
        keeps only the trim's result-time window — segments outside
        the window are dropped."""
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [
                        # Cut 20..40 of source. Segments: (0,20) + (40,100). Result 80s.
                        _cut_op("0:00:20.000", "0:00:40.000"),
                        # Trim result-time 10..50:
                        #   result 10..20 = seg1 last 10 = source 10..20
                        #   result 20..50 = seg2 first 30 = source 40..70
                        _trim_op("0:00:10.000", "0:00:50.000"),
                    ],
                }
            ]
        )
        timeline = resolve_source(spec, "src_0", source_duration=100.0)
        assert timeline.source_segments == ((10.0, 20.0), (40.0, 70.0))
        assert timeline.effective_duration == 40.0
        assert timeline.operations_applied == 2

    def test_cut_after_cut_in_result_time(self):
        """A second cut's coordinates are in the post-first-cut result-time
        (D1). Agent sees the seam as one contiguous timeline; the resolver
        handles the internal splitting (D2)."""
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [
                        # First cut: source 30..50. Segments: (0,30) + (50,100). Result 80s.
                        _cut_op("0:00:30.000", "0:00:50.000"),
                        # Second cut: result-time 20..40 — spans the seam at result 30.
                        #   Removes seg1's [20..30] (source 20..30) and seg2's [30..40]
                        #   in result-time = (50..60) in source.
                        _cut_op("0:00:20.000", "0:00:40.000"),
                    ],
                }
            ]
        )
        timeline = resolve_source(spec, "src_0", source_duration=100.0)
        assert timeline.source_segments == ((0.0, 20.0), (60.0, 100.0))
        assert timeline.effective_duration == 60.0
        assert timeline.operations_applied == 2


class TestResolveAllSources:
    def test_returns_dict_keyed_by_source_id(self):
        spec = _spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )
        timelines = resolve_all_sources(spec, {"holden": 60.0, "jdilla": 30.0})
        assert set(timelines.keys()) == {"holden", "jdilla"}
        assert timelines["holden"].effective_duration == 60.0
        assert timelines["jdilla"].effective_duration == 30.0


class TestEmptySpec:
    def test_minimal_single_source(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        assert spec["version"] == SCHEMA_VERSION
        assert len(spec["sources"]) == 1
        assert spec["sources"][0]["operations"] == []
        assert spec["composition"] is None

    def test_multi_source(self):
        spec = empty_spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )
        assert source_ids(spec) == ["holden", "jdilla"]

    def test_sync_project_sources_appends_missing_sources(self):
        spec = empty_spec([{"id": "holden", "path": "/h.mp4"}])
        synced = sync_project_sources(
            spec,
            [
                {"id": "holden", "path": "/h.mp4"},
                {"id": "jdilla", "path": "/j.mp4"},
            ],
        )

        assert source_ids(synced) == ["holden", "jdilla"]
        assert get_source(synced, "jdilla")["operations"] == []
        assert source_ids(spec) == ["holden"]

    def test_sync_project_sources_updates_revision_source_snapshots(self):
        before = empty_spec([{"id": "holden", "path": "/h.mp4"}])
        trimmed = append_trim(
            before, "holden", 0.0, 1.0, source_duration=10.0
        )
        spec = append_revision(before, trimmed, "trim")

        synced = sync_project_sources(
            spec,
            [
                {"id": "holden", "path": "/h.mp4"},
                {"id": "jdilla", "path": "/j.mp4"},
            ],
        )
        restored, _ = pop_revision(synced)

        assert source_ids(restored) == ["holden", "jdilla"]
        assert get_source(restored, "holden")["operations"] == []
        assert get_source(restored, "jdilla")["operations"] == []


class TestAppendTrim:
    def test_append_to_empty(self):
        spec = empty_spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )
        new_spec = append_trim(spec, "holden", 10.0, 30.0, source_duration=100.0)
        # Holden has the trim; jdilla untouched.
        assert len(get_source(new_spec, "holden")["operations"]) == 1
        assert len(get_source(new_spec, "jdilla")["operations"]) == 0

    def test_append_uses_string_timecodes_on_disk(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        new_spec = append_trim(spec, "src_0", 10.0, 30.0, source_duration=100.0)
        op = get_source(new_spec, "src_0")["operations"][0]
        assert op["from"] == "0:00:10.000"
        assert op["to"] == "0:00:30.000"

    def test_append_does_not_mutate_input(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        original = json.loads(json.dumps(spec))
        append_trim(spec, "src_0", 10.0, 30.0, source_duration=100.0)
        assert spec == original

    def test_append_validates_negative_from(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        with pytest.raises(SpecValidationError, match="negative"):
            append_trim(spec, "src_0", -1.0, 10.0, source_duration=100.0)

    def test_append_validates_inverted(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        with pytest.raises(SpecValidationError, match="before"):
            append_trim(spec, "src_0", 30.0, 10.0, source_duration=100.0)

    def test_append_validates_against_per_source_result_duration(self):
        """Stacked trim: second trim must fit within the source's
        current result, not the raw source duration."""
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        # First trim: 0-10 of a 100s source. Result is 10s.
        spec1 = append_trim(spec, "src_0", 0.0, 10.0, source_duration=100.0)
        # Second trim: 5-50 fits source (100s) but not result (10s).
        with pytest.raises(SpecValidationError, match="result duration"):
            append_trim(spec1, "src_0", 5.0, 50.0, source_duration=100.0)

    def test_append_unknown_source_raises(self):
        spec = empty_spec([{"id": "holden", "path": "/h.mp4"}])
        with pytest.raises(SpecValidationError, match="unknown source_id"):
            append_trim(spec, "missing", 10.0, 30.0, source_duration=100.0)


class TestAppendCut:
    def test_append_to_empty(self):
        spec = empty_spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )
        new_spec = append_cut(spec, "holden", 10.0, 30.0, source_duration=100.0)
        assert len(get_source(new_spec, "holden")["operations"]) == 1
        assert len(get_source(new_spec, "jdilla")["operations"]) == 0
        op = get_source(new_spec, "holden")["operations"][0]
        assert op["type"] == "cut"

    def test_append_uses_string_timecodes_on_disk(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        new_spec = append_cut(spec, "src_0", 10.0, 30.0, source_duration=100.0)
        op = get_source(new_spec, "src_0")["operations"][0]
        assert op["from"] == "0:00:10.000"
        assert op["to"] == "0:00:30.000"

    def test_append_does_not_mutate_input(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        original = json.loads(json.dumps(spec))
        append_cut(spec, "src_0", 10.0, 30.0, source_duration=100.0)
        assert spec == original

    def test_append_validates_negative_from(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        with pytest.raises(SpecValidationError, match="negative"):
            append_cut(spec, "src_0", -1.0, 10.0, source_duration=100.0)

    def test_append_validates_inverted(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        with pytest.raises(SpecValidationError, match="before"):
            append_cut(spec, "src_0", 30.0, 10.0, source_duration=100.0)

    def test_append_validates_against_per_source_result_duration(self):
        """Stacked cut: cut bounds against the source's current result,
        not the raw source duration (D1: cut works in result-time)."""
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        # First trim: 0-10 of a 100s source. Result is 10s.
        spec1 = append_trim(spec, "src_0", 0.0, 10.0, source_duration=100.0)
        # Cut 5-50 fits source (100s) but exceeds result (10s).
        with pytest.raises(SpecValidationError, match="result duration"):
            append_cut(spec1, "src_0", 5.0, 50.0, source_duration=100.0)

    def test_append_rejects_cut_that_empties_result(self):
        """A cut that would leave 0 seconds of result is rejected per D2."""
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        with pytest.raises(SpecValidationError, match="entire result"):
            append_cut(spec, "src_0", 0.0, 100.0, source_duration=100.0)

    def test_append_unknown_source_raises(self):
        spec = empty_spec([{"id": "holden", "path": "/h.mp4"}])
        with pytest.raises(SpecValidationError, match="unknown source_id"):
            append_cut(spec, "missing", 10.0, 30.0, source_duration=100.0)


class TestResolveSourceHistory:
    """Step-by-step lineage exposure. Step 3 of M13b will surface this
    via `moviestar history`; step 1 builds the resolver-side machinery
    so that wiring is a pure CLI job later."""

    def test_empty_history_returns_initial_state_only(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        history = resolve_source_history(spec, "src_0", source_duration=100.0)
        assert len(history) == 1
        step = history[0]
        assert isinstance(step, TimelineStep)
        assert step.op_index == 0
        assert step.op is None
        assert step.timeline.effective_duration == 100.0
        assert step.timeline.source_segments == ((0.0, 100.0),)

    def test_history_includes_initial_then_each_op(self):
        spec = _spec(
            [
                {
                    "id": "src_0",
                    "path": "/x.mp4",
                    "operations": [
                        _trim_op("0:00:10.000", "0:00:50.000"),
                        _cut_op("0:00:05.000", "0:00:15.000"),
                    ],
                }
            ]
        )
        history = resolve_source_history(spec, "src_0", source_duration=100.0)
        assert len(history) == 3
        # Step 0: initial state (no op).
        assert history[0].op is None
        assert history[0].timeline.effective_duration == 100.0
        assert history[0].timeline.source_segments == ((0.0, 100.0),)
        # Step 1: after the trim.
        assert history[1].op["type"] == "trim"
        assert history[1].timeline.effective_duration == 40.0
        assert history[1].timeline.source_segments == ((10.0, 50.0),)
        # Step 2: after the cut.
        assert history[2].op["type"] == "cut"
        assert history[2].timeline.effective_duration == 30.0
        assert history[2].timeline.source_segments == ((10.0, 15.0), (25.0, 50.0))

    def test_history_per_source_isolation(self):
        spec = _spec(
            [
                {
                    "id": "holden",
                    "path": "/h.mp4",
                    "operations": [_trim_op("0:00:10.000", "0:00:30.000")],
                },
                {"id": "jdilla", "path": "/j.mp4", "operations": []},
            ]
        )
        h_history = resolve_source_history(spec, "holden", source_duration=100.0)
        j_history = resolve_source_history(spec, "jdilla", source_duration=100.0)
        assert len(h_history) == 2  # initial + trim
        assert len(j_history) == 1  # initial only

    def test_history_unknown_source_raises(self):
        spec = empty_spec([{"id": "holden", "path": "/h.mp4"}])
        with pytest.raises(SpecValidationError, match="unknown source_id"):
            resolve_source_history(spec, "missing", source_duration=100.0)


class TestResultToSourceTime:
    """M13b step 3.5: map a result-time second back to source-time
    given a segment list. Used by screenshot (--at), inspect, and any
    other command that needs to translate result-time → source bytes
    correctly across a cut's seam."""

    def test_single_segment_linear_mapping(self):
        """Trim-only sources have one segment: result-time 0 maps to
        the segment's source start, and the mapping is linear within."""
        segments = ((10.0, 30.0),)  # trim 10..30 of a longer source
        assert result_to_source_time(segments, 0.0) == 10.0
        assert result_to_source_time(segments, 5.0) == 15.0
        assert result_to_source_time(segments, 20.0) == 30.0

    def test_multi_segment_walks_seam(self):
        """Post-cut: a result-time past the seam maps to source-time
        in the second segment, accounting for the missing hole."""
        # Two segments stitched: source 0..10 + source 15..30.
        # Result is 10s + 15s = 25s long.
        segments = ((0.0, 10.0), (15.0, 30.0))
        # First segment: linear within.
        assert result_to_source_time(segments, 0.0) == 0.0
        assert result_to_source_time(segments, 5.0) == 5.0
        assert result_to_source_time(segments, 9.999) == pytest.approx(9.999)
        # Past the seam: result 10.0 = second segment's start (source 15.0).
        assert result_to_source_time(segments, 10.0) == 15.0
        assert result_to_source_time(segments, 20.0) == 25.0
        # Last possible result time: cumulative duration 25s = source 30.
        assert result_to_source_time(segments, 25.0) == 30.0

    def test_negative_result_time_raises(self):
        with pytest.raises(SpecValidationError, match="negative"):
            result_to_source_time(((0.0, 10.0),), -1.0)

    def test_past_result_end_raises(self):
        with pytest.raises(SpecValidationError, match="exceeds"):
            result_to_source_time(((0.0, 10.0),), 15.0)


class TestSourceRangeInResult:
    """Inverse of result_to_source_time for ranges.

    Maps a source-time range back into result-time, but only when the
    range fits entirely inside ONE segment. Cross-seam ranges return
    None — used by find to drop matches that no longer survive as
    contiguous phrases after a cut."""

    def test_range_within_single_segment(self):
        segments = ((10.0, 30.0),)
        assert source_range_in_result(segments, 15.0, 20.0) == (5.0, 10.0)

    def test_range_inside_one_segment_of_multi(self):
        """Source range entirely within the second segment maps to a
        result-time range accounting for the dropped hole."""
        segments = ((0.0, 10.0), (15.0, 30.0))
        # source 18-22 is in segment 2 (offset 3..7 within it).
        # Cumulative result-time before segment 2 starts: 10s.
        # So result 13..17.
        assert source_range_in_result(segments, 18.0, 22.0) == (13.0, 17.0)

    def test_range_in_first_segment(self):
        segments = ((0.0, 10.0), (15.0, 30.0))
        assert source_range_in_result(segments, 3.0, 7.0) == (3.0, 7.0)

    def test_range_crossing_seam_returns_none(self):
        """A source-time range spanning the cut hole is not contiguous
        in the result. find drops these."""
        segments = ((0.0, 10.0), (15.0, 30.0))
        # source 5..20 includes the hole at 10..15 — not contiguous.
        assert source_range_in_result(segments, 5.0, 20.0) is None

    def test_range_inside_hole_returns_none(self):
        """A source-time range entirely inside a cut hole is not in
        any segment of the result."""
        segments = ((0.0, 10.0), (15.0, 30.0))
        assert source_range_in_result(segments, 11.0, 14.0) is None

    def test_range_outside_all_segments_returns_none(self):
        """Beyond the last segment (or before the first)."""
        segments = ((0.0, 10.0), (15.0, 30.0))
        assert source_range_in_result(segments, 35.0, 40.0) is None


class TestSliceAudioFromAnchor:
    """M14 step 1.5: slice walker used by both set_composition (for
    validation) and the render path. Skips segments before the anchor,
    clamps the first kept segment to start at the anchor, and stops
    when target_duration is satisfied."""

    def test_single_segment_starting_at_anchor(self):
        segs = [(0.0, 60.0)]
        result = slice_audio_from_anchor(segs, anchor=10.0, target_duration=20.0)
        assert result == [(10.0, 30.0)]

    def test_target_shorter_than_remaining_after_anchor(self):
        """Anchor falls inside the segment; target_duration runs out
        before the segment does."""
        segs = [(0.0, 60.0)]
        result = slice_audio_from_anchor(segs, anchor=20.0, target_duration=5.0)
        assert result == [(20.0, 25.0)]

    def test_anchor_at_zero_returns_first_n_seconds(self):
        """Pre-M14-step-1.5 behavior was 'anchor at af_segments[0]
        start'. Confirm slice_audio_from_anchor still gives that when
        the anchor IS at t=0."""
        segs = [(0.0, 60.0)]
        result = slice_audio_from_anchor(segs, anchor=0.0, target_duration=15.0)
        assert result == [(0.0, 15.0)]

    def test_segments_before_anchor_are_skipped(self):
        """When audio_from has internal cuts, segments entirely before
        the anchor disappear from the slice list."""
        segs = [(0.0, 5.0), (10.0, 30.0)]
        result = slice_audio_from_anchor(segs, anchor=12.0, target_duration=10.0)
        assert result == [(12.0, 22.0)]

    def test_first_kept_segment_clamps_to_anchor(self):
        """If anchor falls partway into a segment, only the post-anchor
        portion contributes."""
        segs = [(0.0, 30.0), (50.0, 80.0)]
        result = slice_audio_from_anchor(segs, anchor=20.0, target_duration=25.0)
        # 10s from first segment (20→30), 15s from second (50→65).
        assert result == [(20.0, 30.0), (50.0, 65.0)]

    def test_underflow_returns_partial_slices(self):
        """If target_duration exceeds available audio after the anchor,
        return what's available — caller compares sum to target."""
        segs = [(0.0, 60.0)]
        result = slice_audio_from_anchor(segs, anchor=50.0, target_duration=20.0)
        assert result == [(50.0, 60.0)]
        total = sum(t - f for f, t in result)
        assert total == 10.0  # caller detects underflow vs target=20.0

    def test_zero_target_returns_empty(self):
        segs = [(0.0, 60.0)]
        assert slice_audio_from_anchor(segs, anchor=10.0, target_duration=0.0) == []


class TestSetComposition:
    """M13b step 4: set_composition takes a list of result-time segment
    requests, resolves each through the source's current timeline to a
    source-time snapshot, and stores it. Revision snapshots are added at
    the save boundary."""

    def _two_sources(self):
        return empty_spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )

    def test_set_composition_writes_segments(self):
        spec = self._two_sources()
        # Source durations: holden 60s, jdilla 30s. No prior edits.
        new_spec = set_composition(
            spec,
            [
                ("holden", 0.0, 10.0),
                ("jdilla", 0.0, 5.0),
            ],
            source_durations={"holden": 60.0, "jdilla": 30.0},
        )
        comp = new_spec["composition"]
        assert len(comp) == 2
        assert comp[0]["source"] == "holden"
        assert comp[1]["source"] == "jdilla"

    def test_set_composition_persists_canvas_and_framing(self):
        spec = self._two_sources()
        canvas = {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }
        new_spec = set_composition(
            spec,
            [("holden", 0.0, 10.0), ("jdilla", 0.0, 5.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
            canvas=canvas,
            framings=[
                {"mode": "fill", "anchor": "left"},
                {"mode": "fill", "anchor": "right"},
            ],
        )
        assert new_spec["composition_canvas"] == canvas
        assert new_spec["composition"][0]["framing"] == {
            "mode": "fill",
            "anchor": "left",
        }
        assert new_spec["composition"][1]["framing"] == {
            "mode": "fill",
            "anchor": "right",
        }

    def test_set_composition_framing_count_must_match_segments(self):
        spec = self._two_sources()
        with pytest.raises(SpecValidationError, match="framing count"):
            set_composition(
                spec,
                [("holden", 0.0, 10.0), ("jdilla", 0.0, 5.0)],
                source_durations={"holden": 60.0, "jdilla": 30.0},
                framings=[{"mode": "fill", "anchor": "center"}],
            )

    def test_set_composition_snapshots_source_time_at_concat_time(self):
        """D6: composition stores SOURCE-TIME, not result-time. The
        request is in result-time but the resolver freezes the
        equivalent source-time at the moment of concat.

        After a trim on holden, the segment request 'holden 0..5'
        in result-time corresponds to a non-zero source-time slice."""
        spec = self._two_sources()
        # Trim holden to keep source 10..40 (result is 30s).
        spec = append_trim(spec, "holden", 10.0, 40.0, source_duration=60.0)
        # Now concat: 'holden 0..5' (result-time) should snapshot to source 10..15.
        new_spec = set_composition(
            spec,
            [("holden", 0.0, 5.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
        )
        seg = new_spec["composition"][0]
        assert seg["source_from"] == "0:00:10.000"
        assert seg["source_to"] == "0:00:15.000"

    def test_set_composition_replaces_prior_without_legacy_history(self):
        spec = self._two_sources()
        spec1 = set_composition(
            spec,
            [("holden", 0.0, 10.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
        )
        spec2 = set_composition(
            spec1,
            [("jdilla", 0.0, 5.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
        )
        assert spec2["composition"][0]["source"] == "jdilla"
        assert "composition_history" not in spec2

    def test_set_composition_does_not_mutate_input(self):
        spec = self._two_sources()
        original = json.loads(json.dumps(spec))
        set_composition(
            spec,
            [("holden", 0.0, 10.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
        )
        assert spec == original

    def test_set_composition_empty_segment_list_raises(self):
        spec = self._two_sources()
        with pytest.raises(SpecValidationError, match="at least one"):
            set_composition(
                spec, [], source_durations={"holden": 60.0, "jdilla": 30.0}
            )

    def test_set_composition_unknown_source_raises(self):
        spec = self._two_sources()
        with pytest.raises(SpecValidationError, match="unknown source"):
            set_composition(
                spec,
                [("missing", 0.0, 1.0)],
                source_durations={"holden": 60.0, "jdilla": 30.0},
            )

    def test_set_composition_segment_exceeds_result_duration_raises(self):
        """A result-time range past the source's effective duration is
        rejected — same bounds-check shape as append_trim."""
        spec = self._two_sources()
        # holden's effective_duration is 60s. Request 0..100s.
        with pytest.raises(SpecValidationError, match="exceeds"):
            set_composition(
                spec,
                [("holden", 0.0, 100.0)],
                source_durations={"holden": 60.0, "jdilla": 30.0},
            )

    def test_set_composition_inverted_range_raises(self):
        spec = self._two_sources()
        with pytest.raises(SpecValidationError, match="before"):
            set_composition(
                spec,
                [("holden", 5.0, 2.0)],
                source_durations={"holden": 60.0, "jdilla": 30.0},
            )

    # --- M14 step 1.4: audio_from persistence + validation ---

    def test_set_composition_persists_audio_from(self):
        """audio_from is stored top-level on the spec, alongside (not
        inside) the composition list. Read back via the spec dict."""
        spec = self._two_sources()
        new_spec = set_composition(
            spec,
            [("holden", 0.0, 10.0), ("jdilla", 0.0, 5.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
            audio_from="holden",
        )
        assert new_spec["composition_audio_from"] == "holden"

    def test_set_composition_audio_from_default_is_none(self):
        spec = self._two_sources()
        new_spec = set_composition(
            spec,
            [("holden", 0.0, 10.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
        )
        assert new_spec["composition_audio_from"] is None

    def test_set_composition_audio_from_clears_prior(self):
        """Re-running concat without --audio-from must clear a
        previously-set value. Concat's contract is REPLACE."""
        spec = self._two_sources()
        spec = set_composition(
            spec,
            [("holden", 0.0, 10.0), ("jdilla", 0.0, 5.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
            audio_from="holden",
        )
        spec = set_composition(
            spec,
            [("holden", 0.0, 10.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
            # No audio_from — should clear the prior holden value.
        )
        assert spec["composition_audio_from"] is None

    def test_set_composition_audio_from_must_be_in_segments(self):
        spec = self._two_sources()
        with pytest.raises(
            SpecValidationError, match="must name a source that appears"
        ):
            set_composition(
                spec,
                [("holden", 0.0, 10.0)],
                source_durations={"holden": 60.0, "jdilla": 30.0},
                audio_from="jdilla",
            )

    def test_set_composition_audio_from_must_cover_composition_duration(self):
        """audio_from's edited result must have enough audio AFTER the
        anchor (source-time of audio_from's first appearance in the
        composition) to cover the composition; else audio runs out
        before the video. Error mentions the anchor + remaining
        duration so the agent knows what to move."""
        spec = self._two_sources()
        # holden's effective_duration is 60s. Composition is 80s with
        # holden first at result_from=0.0 → anchor at source-time 0.0s.
        # Available audio from anchor = 60.0s < 80.0s composition.
        with pytest.raises(SpecValidationError) as exc:
            set_composition(
                spec,
                [("holden", 0.0, 60.0), ("jdilla", 0.0, 20.0)],
                source_durations={"holden": 60.0, "jdilla": 30.0},
                audio_from="holden",
            )
        msg = str(exc.value)
        # Anchor is named so the agent can correlate.
        assert "anchor" in msg
        # Available + needed durations both surfaced.
        assert "60" in msg
        assert "80" in msg

    def test_set_composition_audio_from_anchored_at_first_appearance(self):
        """M14 step 1.5: validation uses 'duration from anchor onwards',
        not 'effective_duration'. If audio_from's first appearance in
        the composition is late in source-time, audio that would be
        sufficient from t=0 may not be sufficient from the anchor."""
        spec = self._two_sources()
        # holden.effective_duration = 60s. Composition: jdilla 0-5,
        # holden 50-55 (5s). First holden segment → result_from = 5.0s
        # → source-time anchor = 5.0s (jdilla 0-5 came first, so holden
        # is the SECOND segment in result-time terms). Wait no — anchor
        # = result-time of first holden segment mapped to holden's
        # source-time. First holden segment is at result_from=5.0 (the
        # 6th-10th second of OUTPUT, which is holden 50-55 of his
        # source). Result-time 5.0 on holden's timeline = source-time
        # 5.0 (no trims on holden). Available audio from holden 5.0s
        # onwards = 55s. Composition duration = 10s. Should pass.
        new_spec = set_composition(
            spec,
            [("jdilla", 0.0, 5.0), ("holden", 50.0, 55.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
            audio_from="holden",
        )
        assert new_spec["composition_audio_from"] == "holden"

    def test_set_composition_audio_from_underflow_after_anchor(self):
        """If audio_from's first appearance is late enough that the
        remaining source duration after the anchor is shorter than the
        composition, validation must reject. Anchor at holden 55.0s,
        only 5s of audio remain, composition is 10s → underflow."""
        spec = self._two_sources()
        with pytest.raises(SpecValidationError) as exc:
            set_composition(
                spec,
                [("jdilla", 0.0, 5.0), ("holden", 55.0, 60.0)],
                source_durations={"holden": 60.0, "jdilla": 30.0},
                audio_from="holden",
            )
        msg = str(exc.value)
        # Anchor surfaced so the agent knows what to move.
        assert "anchor" in msg

class TestLayoutGeometry:
    """M25 step 2: canonical ``layout.geometry`` schema and even-pixel
    resolution for authored overrides. Absent geometry keeps legacy
    preset pixels; the legacy ``layout.inset`` shape stays valid but
    cannot coexist with ``geometry``."""

    def _canvas(self, name="short"):
        width, height, aspect_ratio = CANVAS_PRESETS[name]
        return {
            "preset": name,
            "width": width,
            "height": height,
            "aspect_ratio": aspect_ratio,
        }

    def _pip_layout(self, inset_geometry):
        return {
            "preset": "picture-in-picture",
            "geometry": {"inset": inset_geometry},
        }

    def _regions(self, inset_geometry, canvas_name="short", result_time_s=0.0):
        return layout_regions(
            self._pip_layout(inset_geometry),
            self._canvas(canvas_name),
            result_time_s=result_time_s,
        )

    @pytest.mark.parametrize("canvas_name", ["short", "square", "landscape"])
    def test_size_resolves_even_square_from_shorter_dimension(
        self, canvas_name
    ):
        # All three preset canvases share a 1080 shorter dimension:
        # 1080 * 0.22 = 237.6 -> 238, already even.
        regions = self._regions(
            {"size": 0.22, "anchor": "top-left", "margin_x": 64, "margin_y": 64},
            canvas_name,
        )
        assert regions["inset"] == {"x": 64, "y": 64, "width": 238, "height": 238}
        canvas = self._canvas(canvas_name)
        assert regions["main"] == {
            "x": 0,
            "y": 0,
            "width": canvas["width"],
            "height": canvas["height"],
        }

    def test_size_rounds_up_to_even_pixels(self):
        # 1080 * 0.31 = 334.8 -> 335, odd, bumped to 336.
        region = self._regions({"size": 0.31, "anchor": "top-left"})["inset"]
        assert region["width"] == 336
        assert region["height"] == 336

    def test_placement_defaults_to_bottom_right_with_four_percent_margin(self):
        margin = max(16, round(min(1080, 1920) * 0.04))
        region = self._regions({"size": 0.22})["inset"]
        assert region == {
            "x": 1080 - 238 - margin,
            "y": 1920 - 238 - margin,
            "width": 238,
            "height": 238,
        }

    def test_center_anchor_centers_on_both_axes(self):
        region = self._regions({"size": 0.22, "anchor": "center"})["inset"]
        assert region == {
            "x": (1080 - 238) // 2,
            "y": (1920 - 238) // 2,
            "width": 238,
            "height": 238,
        }

    def test_anchor_only_override_keeps_legacy_preset_dimensions(self):
        # No authored size: keep the exact (odd) legacy pixels — even
        # rounding applies only to authored sizes.
        margin = max(16, round(min(1080, 1920) * 0.04))
        region = self._regions({"anchor": "top-left"})["inset"]
        assert region == {"x": margin, "y": margin, "width": 346, "height": 461}

    def test_exact_normalized_position_resolves_to_pixels(self):
        # Locked to the mocked-surface envelope: 0.28 -> 302 square at
        # (round(1080*0.10), round(1920*0.20)) = (108, 384).
        region = self._regions({"size": 0.28, "x": 0.10, "y": 0.20})["inset"]
        assert region == {"x": 108, "y": 384, "width": 302, "height": 302}

    @pytest.mark.parametrize(
        ("speed", "result_time_s", "expected_xy"),
        [
            ("medium", 0.0, (0, 0)),
            ("medium", 1.5, (300, 210)),
            ("medium", 3.0, (520, 140)),
            ("medium", 4.5, (220, 70)),
            ("slow", 1.5, (150, 105)),
            ("fast", 1.5, (450, 245)),
        ],
    )
    def test_bounce_resolves_triangle_wave_at_named_speeds(
        self, speed, result_time_s, expected_xy
    ):
        canvas = {
            "preset": None,
            "width": 640,
            "height": 360,
            "aspect_ratio": "16:9",
        }
        regions = layout_regions(
            self._pip_layout(
                {
                    "size": 0.22,
                    "anchor": "top-left",
                    "margin_x": 0,
                    "margin_y": 0,
                    "motion": {"preset": "bounce", "speed": speed},
                }
            ),
            canvas,
            result_time_s=result_time_s,
        )

        assert regions["inset"] == {
            "x": expected_xy[0],
            "y": expected_xy[1],
            "width": 80,
            "height": 80,
        }

    def test_bounce_starts_at_authored_placement(self):
        canvas = {
            "preset": None,
            "width": 640,
            "height": 360,
            "aspect_ratio": "16:9",
        }
        layout = self._pip_layout(
            {
                "size": 0.22,
                "x": 0.1,
                "y": 0.2,
                "motion": {"preset": "bounce", "speed": "medium"},
            }
        )

        assert layout_regions(layout, canvas, result_time_s=0.0)["inset"] == {
            "x": 64,
            "y": 72,
            "width": 80,
            "height": 80,
        }
        assert layout_regions(layout, canvas, result_time_s=1.0)["inset"] == {
            "x": 264,
            "y": 212,
            "width": 80,
            "height": 80,
        }

    def test_bounce_defaults_to_medium_speed(self):
        canvas = {
            "preset": None,
            "width": 640,
            "height": 360,
            "aspect_ratio": "16:9",
        }
        layout = self._pip_layout(
            {
                "size": 0.22,
                "anchor": "top-left",
                "margin_x": 0,
                "margin_y": 0,
                "motion": {"preset": "bounce"},
            }
        )

        assert layout_regions(layout, canvas, result_time_s=1.5)["inset"] == {
            "x": 300,
            "y": 210,
            "width": 80,
            "height": 80,
        }

    def test_out_of_bounds_geometry_errors_instead_of_clamping(self):
        with pytest.raises(SpecValidationError, match="leaves the"):
            self._regions({"size": 0.28, "x": 0.95, "y": 0.20})
        with pytest.raises(SpecValidationError, match="leaves the"):
            self._regions(
                {"size": 0.22, "anchor": "top-left", "margin_x": 900}
            )

    def test_layout_regions_rejects_inset_alongside_geometry(self):
        layout = {
            "preset": "picture-in-picture",
            "inset": {"corner": "top-left"},
            "geometry": {"inset": {"size": 0.22}},
        }
        with pytest.raises(SpecValidationError, match="geometry"):
            layout_regions(layout, self._canvas(), result_time_s=0.0)

    def _scene_spec(self, layout, canvas_name="short"):
        spec = empty_spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )
        slots = (
            [("main", "holden"), ("inset", "jdilla")]
            if layout["preset"] == "picture-in-picture"
            else [("main", "holden")]
        )
        return {
            **spec,
            "composition": [
                {
                    "name": "demo",
                    "layout": layout,
                    "slots": [
                        {
                            "slot": slot,
                            "source": source,
                            "source_from": "0:00:00.000",
                            "source_to": "0:00:01.000",
                        }
                        for slot, source in slots
                    ],
                }
            ],
            "composition_canvas": self._canvas(canvas_name),
        }

    def test_valid_geometry_passes_spec_validation(self):
        validate_spec(
            self._scene_spec(
                self._pip_layout(
                    {
                        "size": 0.28,
                        "anchor": "bottom-left",
                        "margin_x": 24,
                        "margin_y": 24,
                        "motion": {"preset": "bounce"},
                    }
                )
            )
        )
        validate_spec(
            self._scene_spec(self._pip_layout({"size": 0.22, "x": 0.1, "y": 0.2}))
        )

    @pytest.mark.parametrize(
        "inset_geometry, token",
        [
            ({"size": 1.01}, "size"),
            ({"size": "small"}, "size"),
            ({"anchor": "middle"}, "anchor"),
            ({"size": 0.22, "x": 0.1}, "together"),
            ({"anchor": "top", "x": 0.1, "y": 0.1}, "anchor"),
            ({"x": 0.1, "y": 0.1, "margin_x": 10}, "margin"),
            ({"anchor": "top-left", "margin_x": -1}, "margin_x"),
            ({"anchor": "top-left", "margin_y": 10.5}, "margin_y"),
            ({"size": 0.22, "x": 0.1, "y": 1.5}, "y"),
            ({"size": 0.22, "corner": "top-left"}, "unknown"),
            ({}, "size"),
            ({"motion": {"preset": "spin"}}, "preset"),
            ({"motion": {"preset": "bounce", "speed": "warp"}}, "speed"),
            ({"motion": {"preset": "bounce", "loop": True}}, "unknown"),
            ({"motion": "bounce"}, "object"),
            ({"size": 0.22, "x": 0.95, "y": 0.2}, "leaves the"),
        ],
    )
    def test_bad_geometry_is_rejected_with_the_failing_field(
        self, inset_geometry, token
    ):
        with pytest.raises(SpecValidationError, match=token):
            validate_spec(self._scene_spec(self._pip_layout(inset_geometry)))

    def test_geometry_only_configures_the_inset_slot(self):
        for geometry in ({}, {"main": {"size": 0.22}}):
            layout = {"preset": "picture-in-picture", "geometry": geometry}
            with pytest.raises(SpecValidationError, match="inset"):
                validate_spec(self._scene_spec(layout))
        with pytest.raises(SpecValidationError, match="object"):
            validate_spec(
                self._scene_spec(
                    {"preset": "picture-in-picture", "geometry": "small"}
                )
            )

    def test_geometry_requires_the_picture_in_picture_layout(self):
        layout = {"preset": "single", "geometry": {"inset": {"size": 0.22}}}
        with pytest.raises(SpecValidationError, match="picture-in-picture"):
            validate_spec(self._scene_spec(layout))

    def test_legacy_inset_and_canonical_geometry_cannot_coexist(self):
        layout = {
            "preset": "picture-in-picture",
            "inset": {"corner": "top-left"},
            "geometry": {"inset": {"size": 0.22}},
        }
        with pytest.raises(SpecValidationError, match="geometry"):
            validate_spec(self._scene_spec(layout))

    def test_legacy_inset_alone_still_validates_and_resolves_unchanged(self):
        layout = {
            "preset": "picture-in-picture",
            "inset": {"corner": "top-left", "width": 0.25, "height": 0.2},
        }
        validate_spec(self._scene_spec(layout))
        margin = max(16, round(min(1080, 1920) * 0.04))
        region = layout_regions(layout, self._canvas(), result_time_s=0.0)[
            "inset"
        ]
        # Legacy resolution: independent canvas fractions, no even rounding.
        assert region == {
            "x": margin,
            "y": margin,
            "width": round(1080 * 0.25),
            "height": round(1920 * 0.2),
        }


class TestSceneCompositionGeometry:
    """M25 step 3: scene authoring sets, carries, or clears canonical
    slot geometry instead of silently resetting it."""

    _GEOMETRY = {
        "size": 0.22,
        "anchor": "top-left",
        "margin_x": 64,
        "margin_y": 64,
    }

    def _sources(self):
        return empty_spec(
            [
                {"id": "holden", "path": "/h.mp4"},
                {"id": "jdilla", "path": "/j.mp4"},
            ]
        )

    def _canvas(self):
        return {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }

    def _pip_scene_def(self, slot_geometry="__unset__", layout="picture-in-picture"):
        slots = (
            [
                ("main", "holden", 0.0, 1.0, None),
                ("inset", "jdilla", 0.0, 1.0, None),
            ]
            if layout == "picture-in-picture"
            else [("main", "holden", 0.0, 1.0, None)]
        )
        scene = {"name": "demo", "layout": layout, "slots": slots}
        if slot_geometry != "__unset__":
            scene["slot_geometry"] = slot_geometry
        return scene

    def _set(self, spec, scene_def):
        return set_scene_composition(
            spec,
            scenes=[scene_def],
            source_durations={"holden": 60.0, "jdilla": 60.0},
            canvas=self._canvas(),
        )

    def test_explicit_slot_geometry_is_stored_on_the_layout(self):
        new_spec = self._set(
            self._sources(), self._pip_scene_def({"inset": self._GEOMETRY})
        )
        layout = new_spec["composition"][0]["layout"]
        assert layout["geometry"] == {"inset": self._GEOMETRY}
        assert "inset" not in layout
        validate_spec(new_spec)

    def test_unspecified_geometry_carries_forward_on_reauthor(self):
        first = self._set(
            self._sources(), self._pip_scene_def({"inset": self._GEOMETRY})
        )
        second = self._set(first, self._pip_scene_def())
        assert second["composition"][0]["layout"]["geometry"] == {
            "inset": self._GEOMETRY
        }

    def test_legacy_inset_carries_forward_on_reauthor(self):
        first = self._set(self._sources(), self._pip_scene_def())
        # Model a pre-M26 spec: no materialized geometry, a legacy
        # inset shape instead.
        first["composition"][0]["layout"].pop("geometry", None)
        first["composition"][0]["layout"]["inset"] = {
            "corner": "top-left",
            "width": 0.25,
        }
        second = self._set(first, self._pip_scene_def())
        layout = second["composition"][0]["layout"]
        assert layout["inset"] == {"corner": "top-left", "width": 0.25}
        assert "geometry" not in layout

    def test_explicit_null_clears_stored_geometry(self):
        first = self._set(
            self._sources(), self._pip_scene_def({"inset": self._GEOMETRY})
        )
        second = self._set(first, self._pip_scene_def({"inset": None}))
        layout = second["composition"][0]["layout"]
        assert "geometry" not in layout
        assert "inset" not in layout

    def test_geometry_drops_when_scene_leaves_picture_in_picture(self):
        first = self._set(
            self._sources(), self._pip_scene_def({"inset": self._GEOMETRY})
        )
        second = self._set(first, self._pip_scene_def(layout="single"))
        assert "geometry" not in second["composition"][0]["layout"]

    def test_invalid_slot_geometry_raises_a_pathed_scene_error(self):
        with pytest.raises(SceneValidationError, match="size") as exc:
            self._set(
                self._sources(), self._pip_scene_def({"inset": {"size": 5}})
            )
        assert "geometry" in exc.value.path

    def test_out_of_bounds_slot_geometry_names_the_canvas(self):
        with pytest.raises(SceneValidationError, match="leaves the"):
            self._set(
                self._sources(),
                self._pip_scene_def(
                    {"inset": {"size": 0.22, "x": 0.95, "y": 0.9}}
                ),
            )

    def test_slot_geometry_only_supports_the_inset_slot(self):
        with pytest.raises(SceneValidationError, match="inset"):
            self._set(
                self._sources(),
                self._pip_scene_def({"main": {"size": 0.22}}),
            )

    def test_slot_geometry_requires_picture_in_picture(self):
        with pytest.raises(SceneValidationError, match="picture-in-picture"):
            self._set(
                self._sources(),
                self._pip_scene_def(
                    {"inset": {"size": 0.22}}, layout="single"
                ),
            )


class TestSetLayoutComposition:
    """M17 global layouts persist one scene with preset layout slots."""

    def _two_sources(self):
        return empty_spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )

    def _short_canvas(self):
        return {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }

    def test_layout_slot_metadata_resolves_by_canvas_orientation(self):
        canvas = self._short_canvas()
        assert layout_slots("two-up", canvas) == ["top", "bottom"]
        regions = layout_regions(
            {"preset": "two-up", "orientation": "vertical"},
            canvas,
            result_time_s=0.0,
        )
        assert regions["top"] == {"x": 0, "y": 0, "width": 1080, "height": 960}
        assert regions["bottom"] == {
            "x": 0,
            "y": 960,
            "width": 1080,
            "height": 960,
        }

    def test_pip_inset_config_moves_and_resizes_the_inset(self):
        canvas = self._short_canvas()
        margin = max(16, round(min(1080, 1920) * 0.04))
        default_layout = {
            "preset": "picture-in-picture",
            "orientation": "vertical",
        }
        default = layout_regions(
            default_layout, canvas, result_time_s=0.0
        )["inset"]
        assert default["x"] == 1080 - default["width"] - margin
        assert default["y"] == 1920 - default["height"] - margin
        configured = layout_regions(
            {
                "preset": "picture-in-picture",
                "orientation": "vertical",
                "inset": {"corner": "top-left", "width": 0.25, "height": 0.2},
            },
            canvas,
            result_time_s=0.0,
        )["inset"]
        assert configured == {
            "x": margin,
            "y": margin,
            "width": round(1080 * 0.25),
            "height": round(1920 * 0.2),
        }
        # Partial config keeps defaults for unspecified fields.
        corner_only = layout_regions(
            {
                "preset": "picture-in-picture",
                "orientation": "vertical",
                "inset": {"corner": "bottom-left"},
            },
            canvas,
            result_time_s=0.0,
        )["inset"]
        assert corner_only["x"] == margin
        assert corner_only["width"] == default["width"]

    def test_pip_inset_config_is_validated(self):
        spec = self._two_sources()
        canvas = self._short_canvas()

        def _scene(layout):
            return {
                "composition": [
                    {
                        "name": "demo",
                        "layout": layout,
                        "slots": [
                            {
                                "slot": slot,
                                "source": source,
                                "source_from": "0:00:00.000",
                                "source_to": "0:00:01.000",
                            }
                            for slot, source in (
                                [("main", "holden"), ("inset", "jdilla")]
                                if layout["preset"] == "picture-in-picture"
                                else [("main", "holden")]
                            )
                        ],
                    }
                ],
                "composition_canvas": canvas,
            }

        good = {
            **spec,
            **_scene(
                {
                    "preset": "picture-in-picture",
                    "inset": {"corner": "top-right", "width": 0.3},
                }
            ),
        }
        validate_spec(good)

        for bad_inset, token in (
            ({"corner": "middle"}, "corner"),
            ({"width": 0.9}, "width"),
            ({"height": 0.01}, "height"),
            ({"anchor": "top"}, "unknown"),
            ("top-left", "object"),
        ):
            bad = {
                **spec,
                **_scene(
                    {"preset": "picture-in-picture", "inset": bad_inset}
                ),
            }
            with pytest.raises(SpecValidationError, match=token):
                validate_spec(bad)

        non_pip = {
            **spec,
            **_scene({"preset": "single", "inset": {"corner": "top-left"}}),
        }
        with pytest.raises(SpecValidationError, match="picture-in-picture"):
            validate_spec(non_pip)

    def test_layout_regions_preserve_every_legacy_preset_region(self):
        expected = {
            "short": {
                "single": {
                    "main": {"x": 0, "y": 0, "width": 1080, "height": 1920}
                },
                "two-up": {
                    "top": {"x": 0, "y": 0, "width": 1080, "height": 960},
                    "bottom": {"x": 0, "y": 960, "width": 1080, "height": 960},
                },
                "picture-in-picture": {
                    "main": {"x": 0, "y": 0, "width": 1080, "height": 1920},
                    "inset": {"x": 691, "y": 1416, "width": 346, "height": 461},
                },
            },
            "square": {
                "single": {
                    "main": {"x": 0, "y": 0, "width": 1080, "height": 1080}
                },
                "two-up": {
                    "top": {"x": 0, "y": 0, "width": 1080, "height": 540},
                    "bottom": {"x": 0, "y": 540, "width": 1080, "height": 540},
                },
                "picture-in-picture": {
                    "main": {"x": 0, "y": 0, "width": 1080, "height": 1080},
                    "inset": {"x": 691, "y": 778, "width": 346, "height": 259},
                },
            },
            "landscape": {
                "single": {
                    "main": {"x": 0, "y": 0, "width": 1920, "height": 1080}
                },
                "two-up": {
                    "left": {"x": 0, "y": 0, "width": 960, "height": 1080},
                    "right": {"x": 960, "y": 0, "width": 960, "height": 1080},
                },
                "picture-in-picture": {
                    "main": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                    "inset": {"x": 1263, "y": 778, "width": 614, "height": 259},
                },
            },
        }

        for canvas_name, (width, height, aspect_ratio) in CANVAS_PRESETS.items():
            canvas = {
                "preset": canvas_name,
                "width": width,
                "height": height,
                "aspect_ratio": aspect_ratio,
            }
            orientation = layout_orientation(canvas)
            for preset, regions in expected[canvas_name].items():
                layout = {"preset": preset, "orientation": orientation}
                assert (
                    layout_regions(layout, canvas, result_time_s=0.0) == regions
                )

    @pytest.mark.parametrize("result_time_s", [0.0, 1.25, 86400.0])
    def test_static_layout_regions_do_not_change_with_result_time(
        self, result_time_s
    ):
        canvas = self._short_canvas()
        layout = {"preset": "picture-in-picture", "orientation": "vertical"}

        assert layout_regions(layout, canvas, result_time_s=result_time_s) == {
            "main": {"x": 0, "y": 0, "width": 1080, "height": 1920},
            "inset": {"x": 691, "y": 1416, "width": 346, "height": 461},
        }

    def test_layout_regions_requires_the_stored_layout_object(self):
        with pytest.raises(SpecValidationError, match="layout: must be an object"):
            layout_regions("two-up", self._short_canvas(), result_time_s=0.0)

    def test_set_layout_composition_writes_scene(self):
        spec = self._two_sources()
        new_spec = set_layout_composition(
            spec,
            layout="two-up",
            slots=[
                ("top", "holden", 0.0, 1.0, {"mode": "fill", "anchor": "left"}),
                ("bottom", "jdilla", 0.0, 1.0, {"mode": "fill", "anchor": "right"}),
            ],
            source_durations={"holden": 60.0, "jdilla": 30.0},
            audio_from="holden",
            canvas=self._short_canvas(),
        )
        scene = new_spec["composition"][0]
        assert scene["layout"] == {"preset": "two-up", "orientation": "vertical"}
        assert scene["slots"][0]["slot"] == "top"
        assert scene["slots"][0]["source"] == "holden"
        assert scene["slots"][0]["source_from"] == "0:00:00.000"
        assert scene["slots"][0]["source_to"] == "0:00:01.000"
        assert scene["slots"][0]["framing"] == {"mode": "fill", "anchor": "left"}
        assert new_spec["composition_audio_from"] == "holden"
        assert new_spec["composition_canvas"] == self._short_canvas()

    def test_set_layout_composition_snapshots_result_time_to_source_time(self):
        spec = self._two_sources()
        spec = append_trim(spec, "holden", 10.0, 40.0, source_duration=60.0)
        new_spec = set_layout_composition(
            spec,
            layout="single",
            slots=[("main", "holden", 0.0, 2.0, None)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
            audio_from=None,
            canvas=self._short_canvas(),
        )
        slot = new_spec["composition"][0]["slots"][0]
        assert slot["source_from"] == "0:00:10.000"
        assert slot["source_to"] == "0:00:12.000"

    def test_set_layout_composition_replaces_prior_without_legacy_history(self):
        spec = self._two_sources()
        spec = set_composition(
            spec,
            [("holden", 0.0, 1.0)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
            audio_from="holden",
            canvas=self._short_canvas(),
        )
        new_spec = set_layout_composition(
            spec,
            layout="single",
            slots=[("main", "jdilla", 0.0, 1.0, None)],
            source_durations={"holden": 60.0, "jdilla": 30.0},
            audio_from=None,
            canvas=self._short_canvas(),
        )
        assert new_spec["composition"][0]["slots"][0]["source"] == "jdilla"
        assert "composition_history" not in new_spec

    def test_set_layout_composition_rejects_missing_slot(self):
        spec = self._two_sources()
        with pytest.raises(SpecValidationError, match="missing slot"):
            set_layout_composition(
                spec,
                layout="two-up",
                slots=[("top", "holden", 0.0, 1.0, None)],
                source_durations={"holden": 60.0, "jdilla": 30.0},
                audio_from=None,
                canvas=self._short_canvas(),
            )

    def test_set_layout_composition_rejects_unequal_durations(self):
        spec = self._two_sources()
        with pytest.raises(SpecValidationError, match="same duration"):
            set_layout_composition(
                spec,
                layout="two-up",
                slots=[
                    ("top", "holden", 0.0, 1.0, None),
                    ("bottom", "jdilla", 0.0, 2.0, None),
                ],
                source_durations={"holden": 60.0, "jdilla": 30.0},
                audio_from=None,
                canvas=self._short_canvas(),
            )

    def test_set_layout_composition_audio_from_must_be_slot_source(self):
        spec = self._two_sources()
        with pytest.raises(SpecValidationError, match="assigned to a layout slot"):
            set_layout_composition(
                spec,
                layout="single",
                slots=[("main", "holden", 0.0, 1.0, None)],
                source_durations={"holden": 60.0, "jdilla": 30.0},
                audio_from="jdilla",
                canvas=self._short_canvas(),
            )


class TestSetSceneComposition:
    """M18 scene layouts persist ordered named layout scenes."""

    def _three_sources(self):
        return empty_spec(
            [
                {"id": "holden", "path": "/h.mp4"},
                {"id": "jdilla", "path": "/j.mp4"},
                {"id": "screenshare", "path": "/s.mp4"},
            ]
        )

    def _short_canvas(self):
        return {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }

    def test_set_scene_composition_writes_ordered_named_scenes(self):
        spec = self._three_sources()
        new_spec = set_scene_composition(
            spec,
            scenes=[
                {
                    "name": "intro",
                    "layout": "single",
                    "slots": [
                        (
                            "main",
                            "holden",
                            0.0,
                            1.5,
                            {"mode": "fill", "anchor": "left"},
                        )
                    ],
                    "audio_from": "holden",
                },
                {
                    "name": "conversation",
                    "layout": "two-up",
                    "slots": [
                        ("top", "holden", 10.0, 12.0, None),
                        ("bottom", "jdilla", 20.0, 22.0, None),
                    ],
                    "audio_from": "holden",
                },
            ],
            source_durations={"holden": 60.0, "jdilla": 60.0, "screenshare": 60.0},
            canvas=self._short_canvas(),
        )
        assert [scene["name"] for scene in new_spec["composition"]] == [
            "intro",
            "conversation",
        ]
        assert new_spec["composition"][0]["audio_from"] == "holden"
        assert new_spec["composition"][1]["layout"] == {
            "preset": "two-up",
            "orientation": "vertical",
        }
        assert [slot["slot"] for slot in new_spec["composition"][1]["slots"]] == [
            "top",
            "bottom",
        ]
        assert new_spec["composition_audio_from"] is None
        assert new_spec["composition_canvas"] == self._short_canvas()

    def test_set_scene_composition_snapshots_result_time_to_source_time(self):
        spec = self._three_sources()
        spec = append_trim(spec, "holden", 10.0, 40.0, source_duration=60.0)
        new_spec = set_scene_composition(
            spec,
            scenes=[
                {
                    "name": "trimmed",
                    "layout": "single",
                    "slots": [("main", "holden", 0.0, 2.0, None)],
                }
            ],
            source_durations={"holden": 60.0, "jdilla": 60.0, "screenshare": 60.0},
            canvas=self._short_canvas(),
        )
        slot = new_spec["composition"][0]["slots"][0]
        assert slot["source_from"] == "0:00:10.000"
        assert slot["source_to"] == "0:00:12.000"

    def test_set_scene_composition_invalid_slot_names_valid_alternatives(self):
        spec = self._three_sources()
        with pytest.raises(SpecValidationError, match="invalid slot"):
            set_scene_composition(
                spec,
                scenes=[
                    {
                        "name": "conversation",
                        "layout": "two-up",
                        "slots": [
                            ("left", "holden", 0.0, 1.0, None),
                            ("bottom", "jdilla", 0.0, 1.0, None),
                        ],
                    }
                ],
                source_durations={
                    "holden": 60.0,
                    "jdilla": 60.0,
                    "screenshare": 60.0,
                },
                canvas=self._short_canvas(),
            )

    def test_set_scene_composition_replaces_prior_without_legacy_history(self):
        spec = self._three_sources()
        spec = set_layout_composition(
            spec,
            layout="single",
            slots=[("main", "holden", 0.0, 1.0, None)],
            source_durations={"holden": 60.0, "jdilla": 60.0, "screenshare": 60.0},
            audio_from="holden",
            canvas=self._short_canvas(),
        )
        new_spec = set_scene_composition(
            spec,
            scenes=[
                {
                    "name": "next",
                    "layout": "single",
                    "slots": [("main", "jdilla", 0.0, 1.0, None)],
                }
            ],
            source_durations={"holden": 60.0, "jdilla": 60.0, "screenshare": 60.0},
            canvas=self._short_canvas(),
        )
        assert new_spec["composition"][0]["name"] == "next"
        assert "composition_history" not in new_spec

    def test_motion_identities_are_added_without_changing_scene_names(self):
        spec = self._three_sources()
        spec = set_scene_composition(
            spec,
            scenes=[{
                "name": "walkthrough",
                "layout": "two-up",
                "slots": [
                    ("top", "holden", 0.0, 1.0, None),
                    ("bottom", "jdilla", 0.0, 1.0, None),
                ],
            }],
            source_durations={
                "holden": 60.0, "jdilla": 60.0, "screenshare": 60.0,
            },
            canvas=self._short_canvas(),
        )

        identified, assigned = ensure_scene_motion_identities(spec)

        [scene] = identified["composition"]
        assert scene["id"] == "scene_0001"
        assert [slot["id"] for slot in scene["slots"]] == [
            "slot_0001", "slot_0002",
        ]
        assert [entry["kind"] for entry in assigned] == [
            "scene", "slot", "slot",
        ]
        assert [scene["name"], *[slot["slot"] for slot in scene["slots"]]] \
            == ["walkthrough", "top", "bottom"]

    def test_motion_lifecycle_preserves_renames_and_reports_deleted_slots(self):
        old = [{
            "id": "scene_0001",
            "name": "walkthrough",
            "layout": {"preset": "two-up"},
            "slots": [
                {"id": "slot_0001", "slot": "top"},
                {"id": "slot_0002", "slot": "bottom"},
            ],
        }]
        new = [{
            "id": "scene_0001",
            "name": "tour",
            "layout": {"preset": "single"},
            "slots": [{"id": "slot_0001", "slot": "main"}],
        }]
        motion = {
            "version": 1,
            "scenes": [{
                "scene": "walkthrough",
                "scene_id": "scene_0001",
                "slots": [
                    {
                        "slot": "top", "slot_id": "slot_0001",
                        "pacing": [], "camera": [{"id": "keep"}],
                    },
                    {
                        "slot": "bottom", "slot_id": "slot_0002",
                        "pacing": [{"id": "drop"}], "camera": [],
                    },
                ],
            }],
        }

        reconciled, report = reconcile_motion_lifecycle(old, new, motion)

        [scene] = reconciled["scenes"]
        [slot] = scene["slots"]
        assert (scene["scene"], scene["scene_id"]) == ("tour", "scene_0001")
        assert (slot["slot"], slot["slot_id"]) == ("main", "slot_0001")
        assert slot["camera"][0]["id"] == "keep"
        assert report["renamed"] == [
            {"kind": "scene", "id": "scene_0001", "from": "walkthrough", "to": "tour"},
            {"kind": "slot", "id": "slot_0001", "from": "top", "to": "main"},
        ]
        assert report["removed"][0]["slot_id"] == "slot_0002"
        assert report["removed"][0]["motion_ids"] == ["drop"]


class TestPopLastOp:
    def test_pop_returns_op_and_shrinks_stack(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        spec = append_trim(spec, "src_0", 10.0, 30.0, source_duration=100.0)
        new_spec, popped = pop_last_op(spec, "src_0")
        assert popped["type"] == "trim"
        assert popped["from"] == "0:00:10.000"
        assert get_source(new_spec, "src_0")["operations"] == []

    def test_pop_per_source(self):
        spec = empty_spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )
        spec = append_trim(spec, "holden", 10.0, 30.0, source_duration=100.0)
        spec = append_trim(spec, "jdilla", 5.0, 25.0, source_duration=100.0)
        new_spec, _ = pop_last_op(spec, "holden")
        # Popping holden doesn't touch jdilla.
        assert len(get_source(new_spec, "holden")["operations"]) == 0
        assert len(get_source(new_spec, "jdilla")["operations"]) == 1

    def test_pop_empty_source_raises(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        with pytest.raises(SpecValidationError, match="No operations"):
            pop_last_op(spec, "src_0")

    def test_pop_unknown_source_raises(self):
        spec = empty_spec([{"id": "holden", "path": "/h.mp4"}])
        with pytest.raises(SpecValidationError, match="unknown source_id"):
            pop_last_op(spec, "missing")


class TestMotionState:
    def test_set_motion_replaces_whole_document_without_legacy_history(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        first = {
            "version": 1,
            "scenes": [{
                "scene": "demo",
                "slots": [{
                    "slot": "main",
                    "pacing": [{
                        "id": "pace_0001",
                        "mode": "speed",
                        "range": {
                            "from": "0:00:01.000",
                            "to": "0:00:02.000",
                            "space": "source-local",
                        },
                        "speed": 2.0,
                    }],
                    "camera": [],
                }],
            }],
        }
        second = {"version": 1, "scenes": []}

        spec = set_motion(spec, first)
        assert spec["motion"] == first

        spec = set_motion(spec, second)
        assert spec["motion"] == second
        assert "motion_history" not in spec

    def test_validate_rejects_duplicate_motion_ids(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        duplicate = {
            "version": 1,
            "scenes": [{
                "scene": "demo",
                "slots": [{
                    "slot": "main",
                    "pacing": [
                        {
                            "id": "same",
                            "mode": "hold",
                            "at": "0:00:01.000",
                            "space": "source-local",
                            "duration": "0:00:01.000",
                        },
                        {
                            "id": "same",
                            "mode": "hold",
                            "at": "0:00:02.000",
                            "space": "source-local",
                            "duration": "0:00:01.000",
                        },
                    ],
                    "camera": [],
                }],
            }],
        }
        spec["motion"] = duplicate

        with pytest.raises(SpecValidationError, match="duplicate id 'same'"):
            validate_spec(spec)

    def test_validate_rejects_noncanonical_motion_time_space(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        spec["motion"] = {
            "version": 1,
            "scenes": [{
                "scene": "demo",
                "slots": [{
                    "slot": "main",
                    "pacing": [{
                        "id": "bad",
                        "mode": "speed",
                        "range": {
                            "from": "0:00:01.000",
                            "to": "0:00:02.000",
                            "space": "result-local",
                        },
                        "speed": 2.0,
                    }],
                    "camera": [],
                }],
            }],
        }

        with pytest.raises(SpecValidationError, match="source-local"):
            validate_spec(spec)


class TestSpecIO:
    def test_load_returns_none_when_absent(self, tmp_path: Path):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        assert load_spec(cwd=tmp_path) is None

    def test_save_then_load_roundtrip(self, tmp_path: Path):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        spec = empty_spec(
            [{"id": "holden", "path": "/h.mp4"}, {"id": "jdilla", "path": "/j.mp4"}]
        )
        spec = append_trim(spec, "holden", 10.0, 30.0, source_duration=100.0)
        save_spec(spec, cwd=tmp_path)
        loaded = load_spec(cwd=tmp_path)
        assert loaded == spec

    def test_empty_spec_seeds_motion_state(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        assert spec["motion"] == {"version": 1, "scenes": []}
        assert "motion_history" not in spec

    def test_load_migrates_pre_m20_motion_state(self, tmp_path: Path):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        del spec["motion"]
        (tmp_path / MOVIESTAR_DIR / SPEC_FILE).write_text(json.dumps(spec))

        loaded = load_spec(cwd=tmp_path)

        assert loaded["motion"] == {"version": 1, "scenes": []}

    def test_load_migrates_manual_overlay_and_revision_snapshot_timing(
        self, tmp_path: Path
    ):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        legacy = {
            **_overlay(),
            "from": "0:00:02.000",
            "to": "0:00:03.000",
        }
        legacy.pop("timing")
        spec["overlays"] = [legacy]
        spec["revisions"] = [
            {
                "command": "overlays add",
                "changed": {"overlays": [legacy]},
            }
        ]
        (tmp_path / MOVIESTAR_DIR / SPEC_FILE).write_text(json.dumps(spec))

        loaded = load_spec(cwd=tmp_path)

        expected = {
            "space": "result",
            "from": "0:00:02.000",
            "to": "0:00:03.000",
        }
        assert loaded["overlays"][0]["timing"] == expected
        assert loaded["revisions"][0]["changed"]["overlays"][0][
            "timing"
        ] == expected
        assert "from" not in loaded["overlays"][0]

    def test_validate_rejects_scene_timing_without_scene_id(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        overlay = _overlay()
        overlay["timing"] = {
            "space": "scene",
            "from": "0:00:00.000",
            "to": "0:00:01.000",
        }
        spec["overlays"] = [overlay]

        with pytest.raises(SpecValidationError, match="timing.scene"):
            validate_spec(spec)

    def test_validate_rejects_manual_overlay_with_hybrid_timing(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        overlay = _overlay()
        overlay.update({"from": "0:00:00.000", "to": "0:00:01.000"})
        spec["overlays"] = [overlay]

        with pytest.raises(SpecValidationError, match="top-level"):
            validate_spec(spec)

    def test_validate_rejects_caption_overlay_with_nested_timing(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        overlay = _overlay()
        overlay.update(
            {
                "kind": "caption",
                "from": "0:00:00.000",
                "to": "0:00:01.000",
            }
        )
        spec["overlays"] = [overlay]

        with pytest.raises(SpecValidationError, match="must not use 'timing'"):
            validate_spec(spec)

    def test_load_v01_spec_raises_with_reload_hint(self, tmp_path: Path):
        """D3: v0.1 specs error with a structured 'reload' hint —
        agents who happen to have a stale v0.1 spec on disk get a
        good message rather than a generic schema error."""
        (tmp_path / MOVIESTAR_DIR).mkdir()
        spec_path = tmp_path / MOVIESTAR_DIR / SPEC_FILE
        v01_data = {"version": "0.1", "source_id": "src_0", "operations": []}
        spec_path.write_text(json.dumps(v01_data))
        with pytest.raises(SpecValidationError, match="v0.1"):
            load_spec(cwd=tmp_path)

    def test_load_corrupted_file_raises(self, tmp_path: Path):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        spec_path = tmp_path / MOVIESTAR_DIR / SPEC_FILE
        spec_path.write_text("{not json")
        with pytest.raises(SpecValidationError, match="parse|JSON"):
            load_spec(cwd=tmp_path)

    def test_save_validates_before_writing(self, tmp_path: Path):
        """Invalid spec must not corrupt the on-disk file."""
        (tmp_path / MOVIESTAR_DIR).mkdir()
        good = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        save_spec(good, cwd=tmp_path)

        bad = json.loads(json.dumps(good))
        bad["version"] = "0.1"  # invalid for v0.2
        with pytest.raises(SpecValidationError):
            save_spec(bad, cwd=tmp_path)

        reloaded = load_spec(cwd=tmp_path)
        assert reloaded == good

    def test_is_v01_spec_detects(self):
        assert is_v01_spec({"version": "0.1", "source_id": "x", "operations": []})
        assert not is_v01_spec({"version": "0.2", "sources": []})
        assert not is_v01_spec({})
        assert not is_v01_spec("not a dict")

class TestAudioMixSpec:
    def test_empty_spec_has_audio_defaults(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        assert spec["audio_mix"] == _audio_mix()
        assert "audio_mix_history" not in spec
        validate_spec(spec)

    def test_accepts_canonical_mix(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        narration = _audio_track("narration")
        narration["kind"] = "voiceover"
        narration["loop"] = False
        narration["ducking"] = None
        music = _audio_track("music", duck_under=["source", "narration"])
        spec["audio_mix"] = _audio_mix(gain_db=-8.0, tracks=[narration, music])
        validate_spec(spec)

    @pytest.mark.parametrize(
        ("mutate", "message"),
        [
            (lambda mix: mix["source_audio"].update(gain_db=-61), "gain_db"),
            (lambda mix: mix["tracks"].append(_audio_track("source")), "reserved"),
            (
                lambda mix: mix["tracks"].extend(
                    [_audio_track("music"), _audio_track("music")]
                ),
                "duplicate",
            ),
            (
                lambda mix: mix["tracks"].append(
                    _audio_track("music", duck_under=["missing"])
                ),
                "unknown sidechain",
            ),
            (
                lambda mix: mix["tracks"].extend(
                    [
                        _audio_track("music", duck_under=["voice"]),
                        _audio_track("voice", duck_under=["music"]),
                    ]
                ),
                "cycle",
            ),
        ],
    )
    def test_rejects_invalid_audio_mix(self, mutate, message):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        mutate(spec["audio_mix"])
        with pytest.raises(SpecValidationError, match=message):
            validate_spec(spec)

    def test_set_replaces_complete_mix_without_legacy_history(self):
        spec = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        lowered = _audio_mix(gain_db=-8.0)
        changed = set_audio_mix(spec, lowered)
        assert changed["audio_mix"] == lowered
        assert "audio_mix_history" not in changed

    def test_load_migrates_pre_m21_spec_to_default_audio_state(self, tmp_path: Path):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        spec_path = tmp_path / MOVIESTAR_DIR / SPEC_FILE
        pre_m21 = empty_spec([{"id": "src_0", "path": "/x.mp4"}])
        pre_m21.pop("audio_mix")
        spec_path.write_text(json.dumps(pre_m21))

        loaded = load_spec(cwd=tmp_path)
        assert loaded["audio_mix"] == _audio_mix()
