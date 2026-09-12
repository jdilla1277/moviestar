"""Pure M29 transition-boundary and media-handle resolution."""

import pytest

from moviestar.resolved import (
    ResolvedAudio,
    ResolvedProject,
    ResolvedSlot,
    ResolvedSpan,
)
from moviestar.transitions import (
    resolve_edge_transition,
    resolve_internal_transition,
)


def _slot(
    name,
    source,
    source_from,
    source_to,
    *,
    speed=1.0,
    held=False,
    slot_id=None,
):
    return ResolvedSlot(
        name=name,
        source_id=source,
        source_from_s=source_from,
        source_to_s=source_to,
        speed=speed,
        held=held,
        slot_id=slot_id,
    )


def _span(index, name, scene_id, start, end, slots):
    return ResolvedSpan(
        index=index,
        scene_index=index,
        scene_name=name,
        scene_id=scene_id,
        start_s=start,
        end_s=end,
        scene_start_s=start,
        scene_end_s=end,
        scene_local_from_s=0.0,
        scene_local_to_s=end - start,
        slots=tuple(slots),
        audio=ResolvedAudio(source_id=None, routing=None),
    )


def _project(outgoing_slots, incoming_slots, *, scene_duration=0.6):
    return ResolvedProject(
        shape="scenes",
        duration_s=scene_duration * 2,
        spans=(
            _span(
                0,
                "intro",
                "scene_0001",
                0.0,
                scene_duration,
                outgoing_slots,
            ),
            _span(
                1,
                "demo",
                "scene_0002",
                scene_duration,
                scene_duration * 2,
                incoming_slots,
            ),
        ),
    )


def test_dissolve_resolves_real_source_handles_and_window():
    project = _project(
        [_slot("main", "camera", 0.2, 0.8, slot_id="slot_0001")],
        [_slot("main", "camera", 1.0, 1.6, slot_id="slot_0002")],
    )

    resolved = resolve_internal_transition(
        project,
        {"camera": 2.0},
        incoming_scene_index=1,
        transition_type="dissolve",
        duration_s=0.5,
    )

    assert resolved.cut_s == pytest.approx(0.6)
    assert resolved.window_from_s == pytest.approx(0.35)
    assert resolved.window_to_s == pytest.approx(0.85)
    assert resolved.maximum_duration_s == pytest.approx(1.2)
    assert resolved.sufficient is True
    [outgoing] = resolved.outgoing_handles
    assert outgoing.scene_id == "scene_0001"
    assert outgoing.slot_id == "slot_0001"
    assert outgoing.boundary_source_s == pytest.approx(0.8)
    assert outgoing.required_source_s == pytest.approx(0.25)
    assert outgoing.available_source_s == pytest.approx(1.2)
    assert outgoing.available_result_s == pytest.approx(1.2)
    assert outgoing.sufficient is True
    [incoming] = resolved.incoming_handles
    assert incoming.boundary_source_s == pytest.approx(1.0)
    assert incoming.required_source_s == pytest.approx(0.25)
    assert incoming.available_source_s == pytest.approx(1.0)
    assert incoming.available_result_s == pytest.approx(1.0)

    assert [sample.incoming_weight for sample in resolved.visual_samples] == [
        0.0,
        0.5,
        1.0,
    ]
    assert [sample.outgoing_weight for sample in resolved.visual_samples] == [
        1.0,
        0.5,
        0.0,
    ]
    assert all(sample.color_weight == 0 for sample in resolved.visual_samples)


def test_dissolve_reports_the_one_limiting_slot_in_a_multi_slot_scene():
    project = _project(
        [
            _slot("main", "screen", 0.2, 0.8, slot_id="slot_0001"),
            _slot("inset", "speaker", 0.2, 0.8, slot_id="slot_0002"),
        ],
        [
            _slot("main", "screen", 0.5, 1.1, slot_id="slot_0003"),
            _slot("inset", "speaker", 0.05, 0.65, slot_id="slot_0004"),
        ],
    )

    resolved = resolve_internal_transition(
        project,
        {"screen": 2.0, "speaker": 2.0},
        incoming_scene_index=1,
        transition_type="dissolve",
        duration_s=0.5,
    )

    assert resolved.sufficient is False
    assert resolved.maximum_duration_s == pytest.approx(0.1)
    assert [(item.scene_id, item.slot_name) for item in resolved.limiting_handles] \
        == [("scene_0002", "inset")]
    limiting = resolved.limiting_handles[0]
    assert limiting.required_source_s == pytest.approx(0.25)
    assert limiting.available_source_s == pytest.approx(0.05)
    assert limiting.sufficient is False


def test_boundary_speed_converts_result_handle_to_source_time():
    project = _project(
        [_slot("main", "camera", 0.0, 0.8, speed=2.0)],
        [_slot("main", "camera", 0.5, 1.1)],
        scene_duration=1.0,
    )

    resolved = resolve_internal_transition(
        project,
        {"camera": 2.0},
        incoming_scene_index=1,
        transition_type="dissolve",
        duration_s=0.5,
    )

    [outgoing] = resolved.outgoing_handles
    assert outgoing.edge_speed == pytest.approx(2.0)
    assert outgoing.required_source_s == pytest.approx(0.5)
    assert outgoing.available_source_s == pytest.approx(1.2)
    assert outgoing.available_result_s == pytest.approx(0.6)
    assert resolved.maximum_duration_s == pytest.approx(1.0)


def test_authored_edge_hold_needs_no_new_source_frames():
    project = _project(
        [_slot("main", "camera", 0.8, 0.8, speed=None, held=True)],
        [_slot("main", "camera", 0.5, 1.1)],
        scene_duration=1.0,
    )

    resolved = resolve_internal_transition(
        project,
        {"camera": 2.0},
        incoming_scene_index=1,
        transition_type="dissolve",
        duration_s=0.5,
    )

    [outgoing] = resolved.outgoing_handles
    assert outgoing.edge_behavior == "authored_hold"
    assert outgoing.required_source_s == 0
    assert outgoing.available_result_s is None
    assert outgoing.sufficient is True


def test_dip_uses_visible_content_without_unused_source_handles():
    project = _project(
        [_slot("main", "camera", 1.4, 2.0)],
        [_slot("main", "camera", 0.0, 0.6)],
    )

    resolved = resolve_internal_transition(
        project,
        {"camera": 2.0},
        incoming_scene_index=1,
        transition_type="dip-black",
        duration_s=0.5,
    )

    assert resolved.sufficient is True
    assert resolved.outgoing_handles == ()
    assert resolved.incoming_handles == ()
    assert [sample.color_weight for sample in resolved.visual_samples] == [
        0.0,
        1.0,
        0.0,
    ]
    assert resolved.maximum_duration_s == pytest.approx(1.2)


def test_internal_window_cannot_extend_beyond_its_two_scenes():
    project = _project(
        [_slot("main", "camera", 0.2, 0.4)],
        [_slot("main", "camera", 0.6, 0.8)],
        scene_duration=0.2,
    )

    resolved = resolve_internal_transition(
        project,
        {"camera": 2.0},
        incoming_scene_index=1,
        transition_type="dip-white",
        duration_s=0.5,
    )

    assert resolved.sufficient is False
    assert resolved.limit_reason == "scene_window"
    assert resolved.maximum_duration_s == pytest.approx(0.4)


@pytest.mark.parametrize("edge", ["opening", "closing"])
def test_edge_fade_rejects_duration_beyond_the_project(edge):
    project = _project(
        [_slot("main", "camera", 0.2, 0.8)],
        [_slot("main", "camera", 1.0, 1.6)],
    )

    resolved = resolve_edge_transition(project, edge=edge, duration_s=1.5)

    assert resolved.sufficient is False
    assert resolved.maximum_duration_s == pytest.approx(1.2)
    assert resolved.limit_reason == "project_window"
