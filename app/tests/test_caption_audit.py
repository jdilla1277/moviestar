"""Visibility-state auditing uses authored timing, not aesthetic approval."""

import copy

import pytest

from moviestar.caption_audit import audit_caption_states, plan_caption_proofs


def record(identity, text, start, end, *, track="captions", kind="caption", **extra):
    return {
        "id": identity, "track": track, "kind": kind, "text": text,
        "from": str(start), "to": str(end), "z_index": 100,
        "position": {"anchor": "center", "x": 0.5, "y": 0.5},
        "style": {"resolved": {"font_family": "Inter", "font_size": 32}},
        **extra,
    }


def audit(records, duration=2, **options):
    return audit_caption_states(records, duration, canvas=(320, 240), **options)


def codes(report):
    return [warning["code"] for warning in report["warnings"]]


def test_states_cover_the_full_timeline_with_half_open_boundaries_and_gaps():
    report = audit([
        record("a", "First", 0.25, 0.75),
        record("b", "and", 0.75, 0.85),
        record("c", "last", 1.25, 2),
    ], language="en")
    states = report["states"]
    assert [state["active_ids"] for state in states] == [[], ["a"], ["b"], [], ["c"]]
    assert [state["range"]["from"]["seconds"] for state in states] == [
        0, 0.25, 0.75, 0.85, 1.25,
    ]
    assert sum(state["range"]["duration"]["seconds"] for state in states) == pytest.approx(2)
    assert "caption_short_component" in codes(report)
    assert "caption_short_state" in codes(report)
    assert "caption_connector_only_state" in codes(report)
    finding = next(w for w in report["warnings"]
                   if w["code"] == "caption_connector_only_state")
    assert finding["overlay_ids"] == ["b"]
    assert finding["range"]["from"]["seconds"] == 0.75
    assert finding["severity"] == "review"


def test_overlaps_are_reported_as_simultaneously_visible_text():
    report = audit([
        record("long", "Make readable captions", 0, 2),
        record("brief", "and", 0.75, 0.85),
    ], language="en")
    middle = report["states"][1]
    assert middle["active_ids"] == ["brief", "long"]
    assert {item["text"] for item in middle["text_components"]} == {
        "Make readable captions", "and",
    }
    assert "caption_connector_only_state" not in codes(report)
    assert "caption_short_component" in codes(report)


def test_tracks_are_separate_unless_explicitly_combined():
    records = [
        record("heading", "Make good edits", 0, 2, track="headlines", kind="manual"),
        record("joiner", "and", 0.5, 0.6, track="connectors", kind="manual"),
    ]
    assert audit(records)["states"] == []  # manual text is opt-in
    separate = audit(records, tracks=("headlines", "connectors"), language="en")
    assert "caption_connector_only_state" in codes(separate)
    combined = audit(records, tracks=("headlines", "connectors"),
                     combine_tracks=True, language="en")
    assert "caption_connector_only_state" not in codes(combined)
    assert any(s["active_ids"] == ["joiner", "heading"] for s in combined["states"])
    assert combined["grouping"] == "combined_tracks"


def test_default_language_does_not_assume_english():
    records = [record("one", "AND!", 0, 1)]
    assert "caption_connector_only_state" not in codes(audit(records))
    assert "caption_connector_only_state" in codes(audit(records, language="en"))
    custom = audit([record("one", "y", 0, 1)], connector_words=("Y",))
    assert "caption_connector_only_state" in codes(custom)


@pytest.mark.parametrize("text", ["and 2026", "and something", "...", "anders"])
def test_numbers_punctuation_and_partial_words_do_not_become_connector_only(text):
    assert "caption_connector_only_state" not in codes(
        audit([record("one", text, 0, 1)], language="en")
    )


def test_duration_threshold_is_configurable_and_zero_disables_short_warnings():
    records = [record("one", "brief", 0.5, 0.6)]
    assert "caption_short_component" in codes(audit(records))
    assert "caption_short_component" not in codes(audit(records, min_duration=0.05))
    assert not codes(audit(records, min_duration=0))


def test_geometry_changes_are_evidence_without_aesthetic_warnings():
    report = audit([
        record("one", "Short", 0, 1),
        record("two", "A longer caption", 1, 2),
    ])
    assert report["summary"]["geometry_changes"] == 1
    change = report["states"][1]["geometry_change"]
    assert "width" in change["changed_fields"]
    assert change["geometry_source"] == "overlay_planner_estimate"
    assert change["before"]["estimate"] is True
    assert report["warnings"] == []
    assert all(c["estimated_bounds"]["estimate"] for c in report["components"])
    assert report["coverage"]["visual_review"] == "not_performed"
    assert "verdict" not in report


def test_clipping_and_zero_opacity_are_explicit():
    invisible = record("hidden", "and", 0, 0.1)
    invisible["style"]["resolved"]["opacity"] = 0
    report = audit([record("tail", "Tail", 1.8, 3), invisible], language="en")
    assert [item["id"] for item in report["components"]] == ["tail"]
    assert report["components"][0]["range"]["to"]["seconds"] == 2
    assert report["excluded"][0] == {"id": "hidden", "reason": "zero_opacity"}
    assert "caption_connector_only_state" not in codes(report)


def test_result_is_deterministic_and_does_not_modify_inputs():
    records = [record("b", "Second", 1, 2), record("a", "First", 0, 1)]
    before = copy.deepcopy(records)
    assert audit(records) == audit(list(reversed(records)))
    assert records == before


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_invalid_duration_is_rejected(duration):
    with pytest.raises(ValueError, match="duration"):
        audit([], duration)


@pytest.mark.parametrize("minimum", [-1, float("nan"), float("inf")])
def test_invalid_threshold_is_rejected(minimum):
    with pytest.raises(ValueError, match="min_duration"):
        audit([], min_duration=minimum)


def test_unknown_track_and_invalid_grouping_are_actionable():
    records = [record("a", "First", 0, 1)]
    with pytest.raises(ValueError, match="missing"):
        audit(records, tracks=("missing",))
    with pytest.raises(ValueError, match="two"):
        audit(records, combine_tracks=True)
    with pytest.raises(ValueError, match="connector"):
        audit(records, connector_words=("two words",))
    with pytest.raises(ValueError, match="language"):
        audit(records, language="es")


def test_bounded_proof_selection_does_not_only_sample_early_warnings():
    records = [record(f"c{i}", "and", i, i + 0.1) for i in range(30)]
    report = audit(records, 30, language="en")
    plan_caption_proofs(report, 30, max_frames=4)
    samples = report["proofs"]["samples"]
    assert len(samples) == 4
    times = [sample["at"]["seconds"] for sample in samples]
    assert times[0] < 1
    assert times[-1] >= 29
    assert any(5 <= at <= 24 for at in times)


def test_proof_plans_do_not_invent_frames_between_output_frame_times():
    report = audit([record("flash", "and", 0.001, 0.002)])
    plan_caption_proofs(report, 30)
    state = next(state for state in report["states"] if state["active_ids"])
    assert state["proof"] == {"status": "unavailable", "reason": "no_output_frame_in_state"}
    assert report["proofs"]["planned_frames"] == 0


def test_simultaneous_track_states_share_one_proof_sample():
    report = audit([
        record("a", "First", 0, 1, track="en"),
        record("b", "Deux", 0, 1, track="fr"),
    ], 1)
    plan_caption_proofs(report, 30)
    assert report["proofs"]["candidate_frames"] == 1
    assert len(report["proofs"]["samples"][0]["state_ids"]) == 2


def test_submillisecond_boundaries_do_not_collapse_or_leave_text_active():
    report = audit([record("flash", "and", 0.1001, 0.1004)], 1)
    assert [state["active_ids"] for state in report["states"]] == [[], ["flash"], []]
    assert report["components"][0]["range"]["from"]["seconds"] == 0.1001
    assert report["components"][0]["range"]["to"]["seconds"] == 0.1004
    plan_caption_proofs(report, 30)
    assert report["proofs"]["planned_frames"] == 0


def test_proof_numeric_time_retains_the_output_frame_grid():
    report = audit([record("a", "Caption", 0, 1)], 1)
    plan_caption_proofs(report, 30)
    at = report["proofs"]["samples"][0]["at"]["seconds"]
    assert abs(at * 30 - round(at * 30)) < 1e-9


def test_proof_does_not_round_a_frame_before_a_state_into_that_state():
    report = audit([record("flash", "and", 0.1 + 1e-12, 0.11)], 1)
    plan_caption_proofs(report, 30)
    assert report["proofs"]["planned_frames"] == 0
