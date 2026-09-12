"""Pure M20 pacing resolver contracts."""

import pytest

from moviestar.motion import PacingResolutionError, resolve_pacing


def _scene(name="demo", duration=10.0, slots=("main",)):
    return {
        "name": name,
        "layout": {"preset": "single", "orientation": "horizontal"},
        "slots": [
            {
                "slot": slot,
                "source": f"src_{slot}",
                "source_from": "0:00:10.000",
                "source_to": f"0:00:{10 + duration:06.3f}",
            }
            for slot in slots
        ],
    }


def _motion(*slot_records, scene="demo"):
    return {"version": 1, "scenes": [{"scene": scene, "slots": list(slot_records)}]}


def _slot(slot, *pacing):
    return {"slot": slot, "pacing": list(pacing), "camera": []}


def _speed(entry_id, start, end, speed):
    return {
        "id": entry_id,
        "mode": "speed",
        "range": {
            "from": f"0:00:{start:06.3f}",
            "to": f"0:00:{end:06.3f}",
            "space": "source-local",
        },
        "speed": speed,
    }


def _duration(entry_id, start, end, duration):
    return {
        "id": entry_id,
        "mode": "duration",
        "range": {
            "from": f"0:00:{start:06.3f}",
            "to": f"0:00:{end:06.3f}",
            "space": "source-local",
        },
        "duration": f"0:00:{duration:06.3f}",
    }


def _hold(entry_id, at, duration):
    return {
        "id": entry_id,
        "mode": "hold",
        "at": f"0:00:{at:06.3f}",
        "space": "source-local",
        "duration": f"0:00:{duration:06.3f}",
    }


def test_identity_resolution_preserves_old_scene_math():
    timeline = resolve_pacing([_scene()], {"version": 1, "scenes": []})

    assert timeline.duration_s == 10.0
    [scene] = timeline.scenes
    assert (scene.start_s, scene.end_s) == (0.0, 10.0)
    [segment] = scene.segments
    assert (segment.source_local_from_s, segment.source_local_to_s) == (0.0, 10.0)
    main = segment.slot("main")
    assert (main.source_from_s, main.source_to_s) == (10.0, 20.0)
    assert main.speed == 1.0
    assert main.values == "default"


def test_speed_splits_scene_and_maps_result_back_to_source():
    timeline = resolve_pacing(
        [_scene()], _motion(_slot("main", _speed("skip", 2.0, 6.0, 2.0)))
    )

    [scene] = timeline.scenes
    assert scene.duration_s == 8.0
    assert [segment.duration_s for segment in scene.segments] == [2.0, 2.0, 4.0]
    paced = scene.segments[1]
    assert paced.slot("main").speed == 2.0
    assert paced.slot("main").values == "authored"
    assert timeline.source_time_at(3.0, "main") == 14.0
    assert timeline.source_time_at(7.0, "main") == 19.0


def test_duration_mode_derives_effective_speed():
    timeline = resolve_pacing(
        [_scene()], _motion(_slot("main", _duration("fit", 2.0, 6.0, 1.0)))
    )

    paced = timeline.scenes[0].segments[1].slot("main")
    assert paced.speed == 4.0
    assert timeline.scenes[0].duration_s == 7.0


def test_unbound_peer_gets_numeric_calculated_speed_for_every_union_segment():
    timeline = resolve_pacing(
        [_scene(slots=("main", "inset"))],
        _motion(_slot("main", _speed("skip", 2.0, 6.0, 2.0))),
    )

    paced = timeline.scenes[0].segments[1]
    assert paced.slot("main").values == "authored"
    peer = paced.slot("inset")
    assert peer.values == "calculated"
    assert peer.mode == "speed"
    assert peer.speed == 2.0
    assert (peer.source_from_s, peer.source_to_s) == (12.0, 16.0)


def test_union_of_different_slot_boundaries_splits_longer_constraint():
    timeline = resolve_pacing(
        [_scene(slots=("main", "inset"))],
        _motion(
            _slot("main", _speed("main-fast", 2.0, 8.0, 2.0)),
            _slot("inset", _speed("inset-fast", 4.0, 6.0, 2.0)),
        ),
    )

    scene = timeline.scenes[0]
    assert [
        (segment.source_local_from_s, segment.source_local_to_s)
        for segment in scene.segments
    ] == [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0), (6.0, 8.0), (8.0, 10.0)]
    middle = scene.segments[2]
    assert middle.slot("main").speed == 2.0
    assert middle.slot("inset").speed == 2.0


def test_conflicting_explicit_slot_constraints_name_both_entries_and_interval():
    with pytest.raises(PacingResolutionError) as exc_info:
        resolve_pacing(
            [_scene(slots=("main", "inset"))],
            _motion(
                _slot("main", _speed("main-fast", 2.0, 6.0, 2.0)),
                _slot("inset", _speed("inset-faster", 2.0, 6.0, 4.0)),
            ),
        )

    message = str(exc_info.value)
    assert "main-fast" in message
    assert "inset-faster" in message
    assert "0:00:02.000-0:00:06.000" in message
    assert "Smallest fix" in message


def test_hold_adds_result_time_without_consuming_source_and_freezes_peers():
    timeline = resolve_pacing(
        [_scene(slots=("main", "inset"))],
        _motion(_slot("main", _hold("pause", 5.0, 2.0))),
    )

    scene = timeline.scenes[0]
    assert scene.duration_s == 12.0
    hold = scene.segments[1]
    assert hold.held is True
    assert hold.duration_s == 2.0
    assert hold.slot("main").values == "authored"
    assert hold.slot("inset").values == "calculated"
    assert hold.slot("inset").held is True
    assert timeline.source_time_at(5.5, "main") == 15.0
    assert timeline.source_time_at(6.999, "inset") == 15.0
    assert timeline.source_time_at(7.0, "main") == 15.0


def test_conflicting_holds_at_same_boundary_are_rejected():
    with pytest.raises(PacingResolutionError, match="hold-main.*hold-inset"):
        resolve_pacing(
            [_scene(slots=("main", "inset"))],
            _motion(
                _slot("main", _hold("hold-main", 5.0, 1.0)),
                _slot("inset", _hold("hold-inset", 5.0, 2.0)),
            ),
        )


def test_same_slot_hold_inside_speed_range_is_rejected():
    with pytest.raises(PacingResolutionError, match="hold.*inside.*fast"):
        resolve_pacing(
            [_scene()],
            _motion(
                _slot(
                    "main",
                    _speed("fast", 2.0, 8.0, 2.0),
                    _hold("hold", 5.0, 1.0),
                )
            ),
        )


def test_time_map_breakpoints_expose_duplicate_source_point_for_hold():
    timeline = resolve_pacing(
        [_scene()], _motion(_slot("main", _hold("pause", 5.0, 2.0)))
    )

    points = timeline.scenes[0].time_map("main")
    assert [(point.source_local_s, point.result_local_s) for point in points] == [
        (0.0, 0.0),
        (5.0, 5.0),
        (5.0, 7.0),
        (10.0, 12.0),
    ]
    assert points[2].held is True


def test_multiple_scene_global_spans_use_resolved_durations():
    timeline = resolve_pacing(
        [_scene("intro", 4.0), _scene("demo", 10.0)],
        _motion(_slot("main", _speed("skip", 2.0, 6.0, 2.0))),
    )

    assert [(scene.start_s, scene.end_s) for scene in timeline.scenes] == [
        (0.0, 4.0),
        (4.0, 12.0),
    ]
    assert timeline.source_time_at(7.0, "main") == 14.0


def test_clipped_segment_recalculates_source_edges():
    timeline = resolve_pacing(
        [_scene()], _motion(_slot("main", _speed("skip", 2.0, 6.0, 2.0)))
    )

    [clipped] = timeline.segments_overlapping(2.5, 3.5)
    assert clipped.duration_s == 1.0
    assert (clipped.slot("main").source_from_s, clipped.slot("main").source_to_s) == (
        13.0,
        15.0,
    )
