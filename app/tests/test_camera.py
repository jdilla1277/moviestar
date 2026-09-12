"""Pure M20 camera primitive contracts."""

import pytest

from moviestar.camera import (
    CameraResolutionError,
    fill_visible_rect,
    rebase_camera_ranges,
    resolve_camera,
    resolve_selection_crop,
)
from moviestar.motion import resolve_pacing


CANVAS = {
    "preset": None,
    "width": 320,
    "height": 240,
    "aspect_ratio": "4:3",
}


def _scene(slots=("main",), duration=10.0):
    return {
        "name": "demo",
        "layout": {
            "preset": "single" if len(slots) == 1 else "picture-in-picture",
            "orientation": "horizontal",
        },
        "slots": [
            {
                "slot": name,
                "source": f"src_{name}",
                "source_from": "0:00:10.000",
                "source_to": f"0:00:{10 + duration:06.3f}",
            }
            for name in slots
        ],
    }


def _state(target):
    return {"target": target}


def _rect(x, y, w, h, units="pixels"):
    return {
        "space": "source",
        "units": units,
        "rect": {"x": x, "y": y, "w": w, "h": h},
    }


def _move(move_id, start, end, target, *, from_target=None, ease="linear"):
    move = {
        "id": move_id,
        "range": {
            "from": f"0:00:{start:06.3f}",
            "to": f"0:00:{end:06.3f}",
            "space": "result-local",
        },
        "to": _state(target),
        "ease": ease,
    }
    if from_target is not None:
        move["from"] = _state(from_target)
    return move


def _motion(*camera, slot="main", pacing=()):
    return {
        "version": 1,
        "scenes": [
            {
                "scene": "demo",
                "slots": [
                    {
                        "slot": slot,
                        "pacing": list(pacing),
                        "camera": list(camera),
                    }
                ],
            }
        ],
    }


def _resolve(motion, scene=None, dimensions=None):
    composition = [scene or _scene()]
    pacing = resolve_pacing(composition, motion)
    return resolve_camera(
        composition,
        motion,
        pacing,
        dimensions
        or {"src_main": (320, 180), "src_inset": (640, 360)},
        CANVAS,
    )


def test_normalized_target_reports_both_units_and_derived_zoom():
    plan = _resolve(_motion(_move("zoom", 1, 3, _rect(.25, .25, .5, .5, "normalized"))))

    [move] = plan.moves
    assert move.to_state.authored_units == "normalized"
    assert move.to_state.rect_normalized == pytest.approx(
        {"x": .25, "y": .25, "w": .5, "h": .5}
    )
    assert move.to_state.rect_pixels == pytest.approx(
        {"x": 80, "y": 45, "w": 160, "h": 90}
    )
    assert move.to_state.zoom == pytest.approx(2.0)


def test_resolved_crop_matches_slot_aspect_and_shifts_inside_source():
    plan = _resolve(_motion(_move("edge", 1, 3, _rect(240, 90, 80, 90))))

    [move] = plan.moves
    crop = move.to_state.resolved_crop_pixels
    assert crop["w"] / crop["h"] == pytest.approx(4 / 3)
    assert crop["x"] + crop["w"] <= 320
    assert crop["y"] + crop["h"] <= 180
    assert move.to_state.adjustments


def test_missing_from_uses_previous_terminal_state_and_persists_between_moves():
    first = _move("first", 1, 2, _rect(0, 0, 160, 90))
    second = _move("second", 4, 6, _rect(160, 90, 160, 90))
    plan = _resolve(_motion(first, second))

    assert plan.moves[0].from_state_source == "previous_state"
    assert plan.moves[0].from_state.target == "full"
    assert plan.moves[1].from_state_source == "previous_state"
    assert plan.moves[1].from_state.rect_pixels == plan.moves[0].to_state.rect_pixels
    assert plan.state_at("demo", "main", 3.0).rect_pixels \
        == plan.moves[0].to_state.rect_pixels
    assert plan.state_at("demo", "main", 8.0).rect_pixels \
        == plan.moves[1].to_state.rect_pixels


def test_linear_and_in_out_interpolation_are_deterministic():
    target = _rect(80, 45, 160, 90)
    linear = _resolve(_motion(_move("linear", 0, 2, target, ease="linear")))
    eased = _resolve(_motion(_move("ease", 0, 2, target, ease="in-out")))

    assert linear.state_at("demo", "main", .5).progress == pytest.approx(.25)
    assert linear.state_at("demo", "main", .5).eased_progress == pytest.approx(.25)
    assert eased.state_at("demo", "main", .5).eased_progress == pytest.approx(.15625)


def test_cut_changes_state_immediately_at_authored_time():
    move = {
        "id": "cut",
        "at": "0:00:02.000",
        "to": _state(_rect(80, 45, 160, 90)),
        "ease": "cut",
    }
    plan = _resolve(_motion(move))

    assert plan.state_at("demo", "main", 1.999).target == "full"
    assert plan.state_at("demo", "main", 2.0).target != "full"


def test_camera_state_is_independent_per_slot():
    scene = _scene(slots=("main", "inset"))
    motion = _motion(_move("zoom", 0, 2, _rect(80, 45, 160, 90)))
    plan = _resolve(motion, scene=scene)

    assert plan.state_at("demo", "main", 2.5).target != "full"
    assert plan.state_at("demo", "inset", 2.5).target == "full"


def test_legacy_name_addressed_camera_survives_new_slot_identities():
    scene = _scene()
    scene["id"] = "scene_0001"
    scene["slots"][0]["id"] = "slot_0001"
    motion = _motion(_move("legacy", 0, 1, _rect(80, 45, 160, 90)))

    plan = _resolve(motion, scene=scene)

    assert plan.state_at("demo", "main", 0.5).active_move_id == "legacy"


def test_source_equivalent_range_follows_pacing_and_holds():
    pacing = (
        {
            "id": "fast",
            "mode": "speed",
            "range": {
                "from": "0:00:00.000",
                "to": "0:00:04.000",
                "space": "source-local",
            },
            "speed": 2.0,
        },
        {
            "id": "hold",
            "mode": "hold",
            "at": "0:00:04.000",
            "space": "source-local",
            "duration": "0:00:02.000",
        },
    )
    camera = _move("during-fast", 1, 2, _rect(80, 45, 160, 90))
    plan = _resolve(_motion(camera, pacing=pacing))

    [move] = plan.moves
    assert move.source_equivalent_range == pytest.approx((2.0, 4.0))
    held = plan.source_anchor_at("demo", "main", 3.0)
    assert held.kind == "hold"
    assert held.source_local_s == pytest.approx(4.0)
    assert held.hold_offset_s == pytest.approx(1.0)


def test_out_of_bounds_target_and_overlapping_moves_fail():
    with pytest.raises(CameraResolutionError, match="outside"):
        _resolve(_motion(_move("bad", 0, 1, _rect(300, 10, 40, 40))))

    with pytest.raises(CameraResolutionError, match="overlap"):
        _resolve(
            _motion(
                _move("first", 0, 2, "full"),
                _move("second", 1, 3, "full"),
            )
        )


def test_unchanged_camera_range_rebases_to_same_source_content_after_pacing_edit():
    camera = _move("attached", 4, 6, _rect(80, 45, 160, 90))
    old_motion = _motion(camera)
    fast = {
        "id": "fast",
        "mode": "speed",
        "range": {
            "from": "0:00:00.000",
            "to": "0:00:04.000",
            "space": "source-local",
        },
        "speed": 2.0,
    }
    new_motion = _motion(camera, pacing=(fast,))
    composition = [_scene()]

    rebased, changes = rebase_camera_ranges(
        old_motion,
        new_motion,
        resolve_pacing(composition, old_motion),
        resolve_pacing(composition, new_motion),
    )

    [record] = rebased["scenes"][0]["slots"][0]["camera"]
    assert record["range"] == {
        "from": "0:00:02.000",
        "to": "0:00:04.000",
        "space": "result-local",
    }
    assert changes[0]["id"] == "attached"


def test_camera_offsets_inside_hold_survive_earlier_pacing_change():
    hold = {
        "id": "hold",
        "mode": "hold",
        "at": "0:00:04.000",
        "space": "source-local",
        "duration": "0:00:02.000",
    }
    camera = _move("in-hold", 4.5, 5.5, "full")
    old_motion = _motion(camera, pacing=(hold,))
    fast = {
        "id": "fast",
        "mode": "speed",
        "range": {
            "from": "0:00:00.000",
            "to": "0:00:04.000",
            "space": "source-local",
        },
        "speed": 2.0,
    }
    new_motion = _motion(camera, pacing=(fast, hold))
    composition = [_scene()]

    rebased, _changes = rebase_camera_ranges(
        old_motion,
        new_motion,
        resolve_pacing(composition, old_motion),
        resolve_pacing(composition, new_motion),
    )
    [record] = rebased["scenes"][0]["slots"][0]["camera"]
    assert record["range"]["from"] == "0:00:02.500"
    assert record["range"]["to"] == "0:00:03.500"


# --- selection → crop resolution (agent-friendly zooming) ---

SOURCE = (1920.0, 1080.0)
WIDE_REGION = {"x": 0, "y": 0, "width": 1280, "height": 720}
TALL_REGION = {"x": 0, "y": 0, "width": 720, "height": 1280}


def test_box_selection_matches_region_aspect_and_centers_box():
    crop = resolve_selection_crop(
        source=SOURCE,
        region=WIDE_REGION,
        box_pixels={"x": 900, "y": 220, "w": 480, "h": 300},
    )
    assert crop.kind == "box"
    assert crop.crop_pixels["w"] == pytest.approx(533.3333, abs=0.001)
    assert crop.crop_pixels["h"] == pytest.approx(300.0)
    # Centered on the box center (1140, 370).
    assert crop.crop_pixels["x"] == pytest.approx(873.3333, abs=0.001)
    assert crop.crop_pixels["y"] == pytest.approx(220.0)
    assert "aspect_matched_to_slot" in crop.adjustments
    assert crop.zoom == pytest.approx(3.6, abs=0.001)
    assert crop.effective_placement == (0.5, 0.5)


def test_box_padding_expands_the_crop_outside_the_box():
    tight = resolve_selection_crop(
        source=SOURCE,
        region=WIDE_REGION,
        box_pixels={"x": 900, "y": 220, "w": 480, "h": 300},
    )
    padded = resolve_selection_crop(
        source=SOURCE,
        region=WIDE_REGION,
        box_pixels={"x": 900, "y": 220, "w": 480, "h": 300},
        padding=0.15,
    )
    assert padded.padded_pixels["w"] == pytest.approx(624.0)
    assert padded.padded_pixels["h"] == pytest.approx(390.0)
    assert padded.crop_pixels["w"] > tight.crop_pixels["w"]
    assert padded.crop_pixels["h"] == pytest.approx(390.0)
    assert padded.zoom < tight.zoom


def test_box_near_edge_shifts_crop_and_keeps_box_visible():
    crop = resolve_selection_crop(
        source=SOURCE,
        region=WIDE_REGION,
        box_pixels={"x": 1800, "y": 50, "w": 100, "h": 100},
    )
    assert "shifted_inside_source_bounds" in crop.adjustments
    rect = crop.crop_pixels
    assert rect["x"] + rect["w"] <= 1920.0 + 0.001
    assert rect["x"] <= 1800.0
    assert rect["x"] + rect["w"] >= 1900.0


def test_point_zoom_sizes_crop_from_base_view():
    crop = resolve_selection_crop(
        source=SOURCE,
        region=WIDE_REGION,
        point_pixels=(960, 540),
        zoom=2.0,
        place=(0.5, 0.35),
    )
    assert crop.kind == "point"
    assert crop.crop_pixels["w"] == pytest.approx(960.0)
    assert crop.crop_pixels["h"] == pytest.approx(540.0)
    assert crop.crop_pixels["x"] == pytest.approx(480.0)
    assert crop.crop_pixels["y"] == pytest.approx(540.0 - 0.35 * 540.0)
    assert crop.zoom == pytest.approx(2.0)
    assert crop.requested_zoom == pytest.approx(2.0)
    assert crop.effective_placement == (0.5, 0.35)


def test_selection_larger_than_slot_view_is_flagged():
    crop = resolve_selection_crop(
        source=SOURCE,
        region=TALL_REGION,
        box_pixels={"x": 200, "y": 100, "w": 800, "h": 300},
    )
    assert "selection_exceeds_slot_view" in crop.adjustments
    assert crop.crop_pixels["w"] == pytest.approx(607.5)
    assert crop.zoom == pytest.approx(1.0)


def test_placement_is_limited_to_keep_selection_inside_crop():
    crop = resolve_selection_crop(
        source=SOURCE,
        region=WIDE_REGION,
        box_pixels={"x": 700, "y": 220, "w": 500, "h": 300},
        place=(0.1, 0.5),
    )
    assert "placement_limited_to_keep_selection_visible" in crop.adjustments
    rect = crop.crop_pixels
    assert rect["x"] <= 700.0 + 0.001
    assert rect["x"] + rect["w"] >= 1200.0 - 0.001


def test_selection_maps_into_region_pixels():
    crop = resolve_selection_crop(
        source=SOURCE,
        region=WIDE_REGION,
        box_pixels={"x": 900, "y": 220, "w": 480, "h": 300},
    )
    mapped = crop.selection_in_region
    assert mapped["x"] == pytest.approx(64.0, abs=0.01)
    assert mapped["y"] == pytest.approx(0.0, abs=0.01)
    assert mapped["w"] == pytest.approx(1152.0, abs=0.01)
    assert mapped["h"] == pytest.approx(720.0, abs=0.01)
    assert crop.upscale_factor == pytest.approx(2.4, abs=0.001)


def test_selection_crop_reports_normalized_rect():
    crop = resolve_selection_crop(
        source=SOURCE,
        region=WIDE_REGION,
        point_pixels=(960, 540),
        zoom=2.0,
    )
    assert crop.crop_normalized["w"] == pytest.approx(0.5)
    assert crop.crop_normalized["h"] == pytest.approx(0.5)
    assert crop.crop_normalized["x"] == pytest.approx(0.25)
    assert crop.crop_normalized["y"] == pytest.approx(0.25)


def test_selection_crop_input_validation():
    with pytest.raises(CameraResolutionError):
        resolve_selection_crop(source=SOURCE, region=WIDE_REGION)
    with pytest.raises(CameraResolutionError):
        resolve_selection_crop(
            source=SOURCE, region=WIDE_REGION, point_pixels=(10, 10)
        )
    with pytest.raises(CameraResolutionError):
        resolve_selection_crop(
            source=SOURCE,
            region=WIDE_REGION,
            box_pixels={"x": 1800, "y": 0, "w": 400, "h": 100},
        )
    with pytest.raises(CameraResolutionError):
        resolve_selection_crop(
            source=SOURCE,
            region=WIDE_REGION,
            box_pixels={"x": 0, "y": 0, "w": 100, "h": 100},
            zoom=2.0,
        )
    with pytest.raises(CameraResolutionError):
        resolve_selection_crop(
            source=SOURCE,
            region=WIDE_REGION,
            point_pixels=(10, 10),
            zoom=2.0,
            place=(1.5, 0.5),
        )


def test_fill_visible_rect_matches_canvas_filter_semantics():
    full = {"x": 0.0, "y": 0.0, "w": 1920.0, "h": 1080.0}
    visible = fill_visible_rect(full, TALL_REGION)
    assert visible["w"] == pytest.approx(607.5)
    assert visible["h"] == pytest.approx(1080.0)
    assert visible["x"] == pytest.approx(656.25)
    assert visible["y"] == pytest.approx(0.0)

    left = fill_visible_rect(
        full, TALL_REGION, {"mode": "fill", "anchor": "left"}
    )
    assert left["x"] == pytest.approx(0.0)

    fit = fill_visible_rect(full, TALL_REGION, {"mode": "fit"})
    assert fit == full


# --- kind:"zoom" record expansion ---


def _zoom_record(**overrides):
    record = {
        "id": "zoom-button",
        "kind": "zoom",
        "at": "0:00:04.000",
        "timing": {"move_in": 1.0, "hold": 2.0, "move_out": 0.5},
        "ease": "in-out",
        "to": _state(_rect(80, 45, 160, 90)),
    }
    record.update(overrides)
    return record


def test_zoom_record_expands_into_move_in_hold_and_return():
    plan = _resolve(_motion(_zoom_record()))

    move_in, move_out = plan.moves
    assert move_in.id == "zoom-button"
    assert move_in.result_local_from_s == pytest.approx(3.0)
    assert move_in.result_local_to_s == pytest.approx(4.0)
    assert move_out.id == "zoom-button:return"
    assert move_out.result_local_from_s == pytest.approx(6.0)
    assert move_out.result_local_to_s == pytest.approx(6.5)
    # Hold keeps the zoomed framing; the return lands back on full.
    assert plan.state_at("demo", "main", 5.0).rect_pixels == \
        move_in.to_state.rect_pixels
    assert plan.state_at("demo", "main", 7.0).target == "full"


def test_zoom_record_returns_to_stored_pre_zoom_state():
    record = _zoom_record(
        return_to=_state(_rect(0, 0, 160, 90)),
    )
    plan = _resolve(_motion(record))

    _move_in, move_out = plan.moves
    assert move_out.to_state.rect_pixels == pytest.approx(
        {"x": 0, "y": 0, "w": 160, "h": 90}
    )


def test_zoom_record_rejects_cut_ease_and_bad_timing():
    with pytest.raises(CameraResolutionError, match="cut"):
        _resolve(_motion(_zoom_record(ease="cut")))
    with pytest.raises(CameraResolutionError, match="greater than 0"):
        _resolve(
            _motion(
                _zoom_record(
                    timing={"move_in": 0, "hold": 2.0, "move_out": 0.5}
                )
            )
        )


def test_zoom_record_rebases_arrival_frame_and_keeps_durations():
    zoom = _zoom_record()
    old_motion = _motion(zoom)
    fast = {
        "id": "fast",
        "mode": "speed",
        "range": {
            "from": "0:00:00.000",
            "to": "0:00:04.000",
            "space": "source-local",
        },
        "speed": 2.0,
    }
    new_motion = _motion(zoom, pacing=(fast,))
    composition = [_scene()]

    rebased, changes = rebase_camera_ranges(
        old_motion,
        new_motion,
        resolve_pacing(composition, old_motion),
        resolve_pacing(composition, new_motion),
    )
    [record] = rebased["scenes"][0]["slots"][0]["camera"]
    # Arrival frame source-local 4.0 now lands at result-local 2.0;
    # the viewer-facing move/hold durations are untouched.
    assert record["at"] == "0:00:02.000"
    assert record["timing"] == {"move_in": 1.0, "hold": 2.0, "move_out": 0.5}
    assert changes and changes[0]["id"] == "zoom-button"
