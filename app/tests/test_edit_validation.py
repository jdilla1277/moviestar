"""Real engine read/dry-run checks; no media, ASR, files or saved state."""
import copy

import pytest

from moviestar.edit_validation import EditValidationError, prepare_initial_edit
from moviestar.spec import append_trim, empty_spec, resolve_source

ASSET = "00000000-0000-4000-8000-000000000021"
SOURCE = "source_00000000000040008000000000000031"
SOURCES = [{"source_id": SOURCE, "asset_id": ASSET,
            "media": {"duration_ms": 60000, "width": 1920, "height": 1080, "audio_codec": "aac"}}]
OP = {"type": "trim", "source_id": SOURCE, "time_space": "source_result", "from_ms": 10000, "to_ms": 40000}


def test_read_is_explicit_unsaved_baseline_and_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sources = copy.deepcopy(SOURCES)
    result = prepare_initial_edit(sources)
    assert sources == SOURCES
    assert list(tmp_path.iterdir()) == []
    assert result["origin"] == "unsaved_baseline"
    assert result["spec"]["sources"] == [{"id": SOURCE, "path": f"asset:{ASSET}", "operations": []}]
    assert result["spec"]["composition"] is None
    assert result["spec"]["revisions"] == []
    assert result["resolved"]["sources"][0]["result_duration_ms"] == 60000
    assert result["resolved"]["undo_remaining"] == 0


def test_trim_dry_run_matches_existing_local_engine_without_saving(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = prepare_initial_edit(SOURCES, OP)
    baseline = empty_spec([{"id": SOURCE, "path": f"asset:{ASSET}"}])
    expected = append_trim(baseline, SOURCE, 10, 40, 60)
    timeline = resolve_source(expected, SOURCE, 60)
    assert result["changes"] is True
    assert result["changed_fields"] == ["sources"]
    assert result["current_undo_remaining"] == 0
    assert result["projected_resolved"]["undo_remaining"] == 1
    projected = result["projected_resolved"]["sources"][0]
    assert projected["source_segments_ms"] == [[int(a * 1000), int(b * 1000)] for a, b in timeline.source_segments]
    assert projected["result_duration_ms"] == int(timeline.effective_duration * 1000)
    assert result["spec"]["sources"] == baseline["sources"]
    assert result["candidate_spec"]["sources"] == expected["sources"]
    assert result["candidate_spec"]["revisions"][0]["command"] == "trim"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change", [
    {"from_ms": -1}, {"from_ms": True}, {"to_ms": 61000}, {"to_ms": 10000},
    {"time_space": "source"}, {"type": "cut"}, {"path": "/tmp/secret"}, {"source_id": "foreign"},
])
def test_invalid_or_unavailable_edits_do_not_touch_baseline(change):
    sources = copy.deepcopy(SOURCES)
    with pytest.raises(EditValidationError):
        prepare_initial_edit(sources, dict(OP, **change))
    assert sources == SOURCES


def test_multiple_sources_do_not_imply_a_composition_or_choose_a_camera():
    other = dict(SOURCES[0], source_id="other", asset_id="00000000-0000-4000-8000-000000000022")
    result = prepare_initial_edit([*SOURCES, other])
    assert result["spec"]["composition"] is None
    assert result["resolved"]["result_duration_ms"] is None
    assert len(result["resolved"]["sources"]) == 2


@pytest.mark.parametrize("sources", [[], SOURCES * 2, [{**SOURCES[0], "asset_id": "https://foreign"}]])
def test_source_context_must_be_bounded_unique_and_portable(sources):
    with pytest.raises(EditValidationError):
        prepare_initial_edit(sources)


def test_unreported_audio_codec_stays_unknown_and_does_not_block_timeline():
    sources=copy.deepcopy(SOURCES)
    del sources[0]['media']['audio_codec']
    original=copy.deepcopy(sources)
    result=prepare_initial_edit(sources,OP)
    assert result['projected_resolved']['result_duration_ms']==30000
    assert sources==original
