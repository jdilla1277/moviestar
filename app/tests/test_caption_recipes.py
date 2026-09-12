"""Stage 4: caption recipes — live derivation against the timeline.

The recipe (spec["captions"]) is authoritative for a derived track;
stored cue overlays are a cache keyed by a fingerprint of the compiled
timeline + recipe + rules, refreshed at spec load. The product
contract under test: change the timeline, and captions just work —
no regeneration, no stale warning, edits survive.

The tests below freeze the behavior validated by the original design work.
"""

import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from tests.conftest import (
    assert_error_envelope,
    inject_synthetic_transcript,
    make_segment,
    make_word,
)


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
    ]


def _segments():
    return [
        make_segment("intro setup", 0.0, 0.7),
        make_segment("this is where you get to that pain", 0.8, 1.95),
    ]


def _spec_on_disk(tmp_path) -> dict:
    return json.loads((tmp_path / "moviestar" / "spec.json").read_text())


def _invoke(runner, *args) -> dict:
    result = runner.invoke(cli, list(args))
    assert result.exit_code == 0, f"{args}: {result.stdout}"
    return json.loads(result.stdout)


def _project(runner, test_video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _invoke(
        runner,
        "load", test_video, "--as", "src_0",
        "--interval", "1.0", "--no-transcribe",
    )
    inject_synthetic_transcript(tmp_path, _words(), _segments())
    _invoke(
        runner,
        "scenes", "set", "--canvas", "short",
        "--scene", "intro=single",
        "--slot", "intro:main=src_0", "--from", "0", "--to", "2",
        "--audio-from", "intro=src_0",
    )
    return _invoke(runner, "captions", "generate")


def _apply_pacing(runner, tmp_path):
    """2x speed on source-local 0.2-0.8 of intro: 2.0s -> 1.7s, and
    every word after source 0.8 moves 0.3s earlier on the result clock.
    """
    motion_file = tmp_path / "motion.json"
    motion_file.write_text(json.dumps({
        "version": 1,
        "scenes": [{
            "scene": "intro",
            "slots": [{
                "slot": "main",
                "pacing": [{
                    "id": "rush",
                    "mode": "speed",
                    "range": {
                        "from": "0.2", "to": "0.8",
                        "space": "source-local",
                    },
                    "speed": 2.0,
                }],
            }],
        }],
    }))
    result = runner.invoke(
        cli, ["scenes", "motion", "set", str(motion_file)]
    )
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


PACED_DURATION = 1.7


class TestRecipePersistence:
    def test_generate_persists_the_recipe(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        spec = _spec_on_disk(tmp_path)
        recipes = spec["captions"]
        assert len(recipes) == 1
        assert recipes[0]["track"] == "captions"
        assert recipes[0]["cache_fingerprint"]
        # The cue cache is stored alongside, as before.
        assert any(
            overlay["kind"] == "caption" for overlay in spec["overlays"]
        )

    def test_regenerate_replaces_recipe_not_duplicates(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "generate", "--position", "top")
        spec = _spec_on_disk(tmp_path)
        assert len(spec["captions"]) == 1
        assert spec["captions"][0]["position"] == "top"


class TestLiveDerivation:
    def test_cues_rederive_after_a_pacing_change(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        """The product moment: pacing changes and the captions follow,
        with nothing regenerated and nothing stale."""
        _project(runner, test_video, tmp_path, monkeypatch)
        _apply_pacing(runner, tmp_path)

        dump = _invoke(runner, "captions", "dump")
        assert dump["caption_model"] == "derived"
        last_to = max(
            cue["result_range"]["to"]["seconds"] for cue in dump["cues"]
        )
        assert last_to <= PACED_DURATION + 0.011
        # 'pain' (source 1.7-1.95) sits at result 1.4-1.65 on the paced
        # clock — 0.3s earlier than the unpaced timeline.
        pain = next(
            cue for cue in dump["cues"] if "pain" in cue["text"]
        )
        assert pain["result_range"]["to"]["seconds"] == pytest.approx(
            1.65, abs=0.02
        )

    def test_read_surfaces_see_fresh_cues_without_writing(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _apply_pacing(runner, tmp_path)
        on_disk_before = _spec_on_disk(tmp_path)

        status = _invoke(runner, "status")
        summary = status["overlays"]["track_summaries"][0]
        assert summary["caption_model"] == "derived"
        assert summary["coverage"]["to"]["seconds"] <= PACED_DURATION + 0.011

        # status is a read command: the on-disk cache may stay stale
        # until the next write persists it.
        assert _spec_on_disk(tmp_path) == on_disk_before

    def test_write_commands_persist_the_refreshed_cache(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _apply_pacing(runner, tmp_path)
        _invoke(
            runner,
            "overlays", "add", "--text", "Title",
            "--from", "0", "--to", "0.4",
        )
        spec = _spec_on_disk(tmp_path)
        caption_tos = [
            float(overlay["to"].split(":")[-1])
            for overlay in spec["overlays"]
            if overlay["kind"] == "caption"
        ]
        assert caption_tos and max(caption_tos) <= PACED_DURATION + 0.011


class TestStaleWarningExclusion:
    def test_motion_set_does_not_warn_about_derived_tracks(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        data = _apply_pacing(runner, tmp_path)
        stale = [
            w
            for w in data.get("warnings", [])
            if w["code"] == "motion_result_clock_changed_overlays_stale"
        ]
        assert stale == []

    def test_manual_overlays_still_warn(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(
            runner,
            "overlays", "add", "--text", "Chapter",
            "--from", "0.1", "--to", "0.6",
        )
        data = _apply_pacing(runner, tmp_path)
        stale = next(
            w
            for w in data.get("warnings", [])
            if w["code"] == "motion_result_clock_changed_overlays_stale"
        )
        assert stale["affected_overlays_count"] == 1
        assert stale["caption_cues_count"] == 0
        assert stale["manual_overlays_count"] == 1
        assert stale["affected_tracks"] == ["titles"]
        # Friction (implementation run): the hint must not tell agents
        # to regenerate captions when no caption cues are stale.
        assert "captions generate" not in stale["hint"]
        assert "overlays dump" in stale["hint"]


class TestPlacement:
    def test_placement_persists_and_rederives(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        data = _invoke(
            runner,
            "captions", "placement", "--for", "scene:intro=top",
        )
        assert data["writes_spec"] is True
        assert "design_preview" not in data

        dump = _invoke(runner, "captions", "dump")
        for cue in dump["cues"]:
            assert cue["placement"]["position"] == "top"
            assert cue["placement"]["rule"] == "scene:intro"
        spec = _spec_on_disk(tmp_path)
        assert spec["captions"][0]["placement"]["overrides"]


class TestMaterialize:
    def test_materialize_freezes_the_track(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        data = _invoke(runner, "captions", "materialize")
        assert data["writes_spec"] is True
        assert data["caption_model"] == "materialized"
        assert "design_preview" not in data

        spec = _spec_on_disk(tmp_path)
        # #412: the recipe is kept, frozen — staleness stays queryable.
        assert spec["captions"][0]["frozen"] is True
        assert any(o["kind"] == "caption" for o in spec["overlays"])

        # Frozen tracks go back to warning on pacing changes.
        motion = _apply_pacing(runner, tmp_path)
        stale = next(
            w
            for w in motion.get("warnings", [])
            if w["code"] == "motion_result_clock_changed_overlays_stale"
        )
        assert stale["caption_cues_count"] > 0
        assert "captions generate" in stale["hint"]

        dump = _invoke(runner, "captions", "dump")
        assert dump["caption_model"] == "materialized"
        assert dump["follows_timeline_edits"] is False

        status = _invoke(runner, "status")
        summary = status["overlays"]["track_summaries"][0]
        assert summary["caption_model"] == "materialized"
        assert summary["follows_timeline_edits"] is False


class TestOverlaysSetGuard:
    def test_omitting_derived_cues_explains_complete_state_requirement(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        overlay_file = tmp_path / "overlays.json"
        overlay_file.write_text(json.dumps({"overlays": [{
            "text": "Chapter",
            "from": "0",
            "to": "1",
        }]}))

        result = runner.invoke(cli, ["overlays", "set", str(overlay_file)])

        assert result.exit_code == 1, result.stdout
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="overlays set")
        assert data["reason"] == "derived_caption_cues_omitted"
        assert data["omitted_tracks"] == ["captions"]
        assert "complete overlay state" in data["error"]
        assert "overlays dump" in data["hint"]
        assert "materialize" not in data["hint"]

    def test_full_state_can_edit_manual_overlay_with_derived_cues_present(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(
            runner,
            "overlays", "add", "--text", "Chapter",
            "--from", "0", "--to", "1",
        )
        overlay_file = tmp_path / "overlays.json"
        _invoke(runner, "overlays", "dump", "--out", str(overlay_file))
        state = json.loads(overlay_file.read_text())
        manual = next(
            overlay for overlay in state["overlays"]
            if overlay["kind"] == "manual"
        )
        manual["text"] = "Corrected chapter"
        overlay_file.write_text(json.dumps(state))

        result = runner.invoke(cli, ["overlays", "set", str(overlay_file)])

        assert result.exit_code == 0, result.stdout
        stored = _spec_on_disk(tmp_path)["overlays"]
        assert next(o for o in stored if o["kind"] == "manual")["text"] == (
            "Corrected chapter"
        )
        assert any(o["kind"] == "caption" for o in stored)

    def test_hand_editing_derived_cues_is_rejected(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        spec = _spec_on_disk(tmp_path)
        records = spec["overlays"]
        overlay_file = tmp_path / "overlays.json"
        records[0]["text"] = "hand edit"
        overlay_file.write_text(json.dumps({"overlays": records}))
        result = runner.invoke(
            cli, ["overlays", "set", str(overlay_file)]
        )
        assert result.exit_code == 1, result.stdout
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="overlays set")
        assert data["reason"] == "derived_caption_cues_modified"
        assert "materialize" in data["hint"]

    def test_nested_manual_record_cannot_bypass_derived_cue_guard(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        records = _spec_on_disk(tmp_path)["overlays"]
        records[0]["text"] = "hand edit"
        records.append(
            {
                "text": "Chapter",
                "timing": {"space": "result", "from": "0", "to": "1"},
            }
        )
        overlay_file = tmp_path / "overlays.json"
        overlay_file.write_text(json.dumps({"overlays": records}))

        result = runner.invoke(cli, ["overlays", "set", str(overlay_file)])

        assert result.exit_code == 1, result.stdout
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="overlays set")
        assert "materialize" in data["hint"]


class TestSceneBoundaryCues:
    """#351: cues never cross scene boundaries, even when adjacent
    scenes route the same audio source — the original provenance-loss
    repro behind the compiler proposal."""

    def test_same_source_adjacent_scenes_split_cues_at_the_boundary(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _invoke(
            runner,
            "load", test_video, "--as", "src_0",
            "--interval", "1.0", "--no-transcribe",
        )
        inject_synthetic_transcript(tmp_path, _words(), _segments())
        _invoke(
            runner,
            "scenes", "set", "--canvas", "short",
            "--scene", "one=single",
            "--slot", "one:main=src_0", "--from", "0", "--to", "1",
            "--audio-from", "one=src_0",
            "--scene", "two=single",
            "--slot", "two:main=src_0", "--from", "1", "--to", "2",
            "--audio-from", "two=src_0",
        )
        _invoke(runner, "captions", "generate")
        dump = _invoke(runner, "captions", "dump")
        boundary = 1.0
        for cue in dump["cues"]:
            from_s = cue["result_range"]["from"]["seconds"]
            to_s = cue["result_range"]["to"]["seconds"]
            assert not (from_s < boundary - 0.01 and to_s > boundary + 0.01), (
                f"cue {cue['text']!r} ({from_s}-{to_s}) crosses the scene "
                f"boundary at {boundary}"
            )
        scenes = {cue["scene"] for cue in dump["cues"]}
        assert scenes == {"one", "two"}


class TestQueryableStaleness:
    """#412: staleness is a queryable state on dump/status, not just a
    motion-set event. Materialize keeps the recipe with frozen: true —
    preserving the fingerprint for staleness detection and retaining
    edits dormant instead of discarding them."""

    def test_fresh_frozen_track_is_not_stale(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "materialize")
        dump = _invoke(runner, "captions", "dump")
        assert dump["caption_model"] == "materialized"
        assert dump["stale"] is False

    def test_stale_frozen_track_reports_clocks_in_dump_and_status(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "materialize")
        _apply_pacing(runner, tmp_path)

        dump = _invoke(runner, "captions", "dump")
        assert dump["stale"] is True
        assert dump["frozen_clock"]["seconds"] == pytest.approx(
            2.0, abs=0.01
        )
        assert dump["current_clock"]["seconds"] == pytest.approx(
            1.7, abs=0.01
        )

        status = _invoke(runner, "status")
        summary = next(
            s
            for s in status["overlays"]["track_summaries"]
            if s["track"] == "captions"
        )
        assert summary["caption_model"] == "materialized"
        assert summary["stale"] is True

    def test_derived_track_is_never_stale(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _apply_pacing(runner, tmp_path)
        dump = _invoke(runner, "captions", "dump")
        assert dump["caption_model"] == "derived"
        assert dump["stale"] is False

    def test_imported_track_is_stale_queryable(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        srt = tmp_path / "cues.srt"
        srt.write_text(
            "1\n00:00:00,100 --> 00:00:01,000\nimported line\n",
        )
        _invoke(runner, "captions", "import", str(srt))
        dump = _invoke(runner, "captions", "dump")
        assert dump["caption_model"] == "materialized"
        assert dump["stale"] is False

        _apply_pacing(runner, tmp_path)
        dump = _invoke(runner, "captions", "dump")
        assert dump["stale"] is True

    def test_materialize_retains_edits_dormant(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "break", "--at-word", "where")
        data = _invoke(runner, "captions", "materialize")
        assert data["recipe_frozen"] is True
        assert data["retained_edits_count"] == 1
        assert data["retained_edits"][0]["op"] == "break"
        # The frozen recipe still lists the dormant edit.
        dump = _invoke(runner, "captions", "dump")
        assert dump["recipe"]["edits"][0]["op"] == "break"

    def test_hand_edited_frozen_cues_survive_refresh(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "materialize")
        spec_path = tmp_path / "moviestar" / "spec.json"
        spec = json.loads(spec_path.read_text())
        cue = next(
            o for o in spec["overlays"] if o.get("kind") == "caption"
        )
        overlay_file = tmp_path / "overlays.json"
        edited = [dict(o) for o in spec["overlays"]]
        for o in edited:
            if o["id"] == cue["id"]:
                o["text"] = "HAND EDITED"
        overlay_file.write_text(json.dumps({"overlays": edited}))
        set_result = runner.invoke(
            cli, ["overlays", "set", str(overlay_file)]
        )
        assert set_result.exit_code == 0, set_result.stdout

        # A timeline change must NOT re-derive the frozen track and
        # clobber the hand edit.
        _apply_pacing(runner, tmp_path)
        dump = _invoke(runner, "captions", "dump")
        assert any(c["text"] == "HAND EDITED" for c in dump["cues"])

    def test_edit_verbs_still_reject_frozen_tracks(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "materialize")
        result = runner.invoke(
            cli, ["captions", "break", "--at-word", "where"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="captions break")
        assert "frozen" in data["error"]
