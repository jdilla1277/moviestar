"""Stage 4: caption edit verbs persist into the recipe.

break/join/suppress stop being design previews: each stores a
word-anchored edit record in ``recipe.edits`` that the derivation
pipeline applies on every re-derive, so the edits survive pacing
changes, reorders, and cuts. Frozen (materialized/imported) tracks
reject the verbs; ``captions dump`` exposes the recipe so edits are
never write-only. Contract frozen by the stage-4 friction rounds.
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


def _invoke(runner, *args) -> dict:
    result = runner.invoke(cli, list(args))
    assert result.exit_code == 0, f"{args}: {result.stdout}"
    return json.loads(result.stdout)


def _spec_on_disk(tmp_path) -> dict:
    return json.loads((tmp_path / "moviestar" / "spec.json").read_text())


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


def _pace(runner, tmp_path, speed=2.0):
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
                    "speed": speed,
                }],
            }],
        }],
    }))
    return _invoke(runner, "scenes", "motion", "set", str(motion_file))


def _dump(runner):
    return _invoke(runner, "captions", "dump")


class TestBreakPersists:
    def test_break_splits_and_persists_word_anchored(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        before = _dump(runner)["cues_count"]

        data = _invoke(runner, "captions", "break", "--at-word", "where")
        assert data["writes_spec"] is True
        assert "design_preview" not in data
        assert data["edit"]["id"] == "edit_0001"
        assert data["edit"]["op"] == "break"
        assert data["edit"]["anchor"]["space"] == "word"

        spec = _spec_on_disk(tmp_path)
        edits = spec["captions"][0]["edits"]
        assert len(edits) == 1
        assert edits[0]["op"] == "break"
        assert edits[0]["source"] == "src_0"

        after = _dump(runner)
        assert after["cues_count"] == before + 1
        assert any(
            cue["text"].startswith("where") for cue in after["cues"]
        )

    def test_break_survives_a_pacing_change(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "break", "--at-word", "where")
        split_count = _dump(runner)["cues_count"]

        _pace(runner, tmp_path)
        after = _dump(runner)
        assert after["cues_count"] == split_count
        where_cue = next(
            cue for cue in after["cues"] if cue["text"].startswith("where")
        )
        # Source 1.1s maps through 2x pacing of 0.2-0.8 to result 0.8s.
        assert where_cue["result_range"]["from"]["seconds"] == pytest.approx(
            0.8, abs=0.05
        )

    def test_duplicate_break_is_rejected_with_existing_id(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "break", "--at-word", "where")
        result = runner.invoke(
            cli, ["captions", "break", "--at-word", "where"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="captions break")
        assert "edit_0001" in data["error"] or "edit_0001" in data["hint"]

    def test_break_remove_restores_the_boundary(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        before = _dump(runner)["cues_count"]
        _invoke(runner, "captions", "break", "--at-word", "where")
        data = _invoke(runner, "captions", "break", "--remove", "edit_0001")
        assert data["writes_spec"] is True
        assert _dump(runner)["cues_count"] == before
        assert _spec_on_disk(tmp_path)["captions"][0]["edits"] == []


class TestJoinPersists:
    def test_join_merges_within_limits_and_survives_removal(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "break", "--at-word", "where")
        _invoke(runner, "captions", "break", "--at-word", "to")
        broken = _dump(runner)
        assert any(
            cue["text"] == "where you get" for cue in broken["cues"]
        ), broken["cues"]

        data = _invoke(runner, "captions", "join", "--after-word", "get")
        assert data["writes_spec"] is True
        assert data["edit"]["op"] == "join"
        merged = _dump(runner)
        # "pain" stays its own cue — it was a natural 42-char boundary,
        # not part of the broken pair being rejoined.
        assert any(
            cue["text"] == "where you get to that"
            for cue in merged["cues"]
        ), merged["cues"]

        _invoke(
            runner, "captions", "join", "--remove", data["edit"]["id"]
        )
        assert any(
            cue["text"] == "where you get" for cue in _dump(runner)["cues"]
        )

    def test_join_over_limits_is_still_rejected(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        cues = _dump(runner)["cues"]
        last_word = cues[0]["text"].split()[-1].strip(".,!?")
        result = runner.invoke(
            cli, ["captions", "join", "--after-word", last_word]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="captions join")
        assert "42" in data["error"]
        # Nothing persisted for a rejected join.
        assert _spec_on_disk(tmp_path)["captions"][0]["edits"] == []


class TestSuppressPersists:
    def test_suppress_drops_words_and_survives_pacing(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        data = _invoke(
            runner, "captions", "suppress", "--from", "0", "--to", "0.75"
        )
        assert data["writes_spec"] is True
        assert data["edit"]["op"] == "suppress"
        assert "intro" in data["suppressed_words"]
        assert "setup" in data["suppressed_words"]

        after = _dump(runner)
        joined = " ".join(cue["text"] for cue in after["cues"])
        assert "intro" not in joined
        assert "setup" not in joined

        _pace(runner, tmp_path)
        paced = " ".join(cue["text"] for cue in _dump(runner)["cues"])
        assert "intro" not in paced
        assert "this is where" in paced

    def test_suppress_remove_restores_words(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        data = _invoke(
            runner, "captions", "suppress", "--from", "0", "--to", "0.75"
        )
        _invoke(
            runner, "captions", "suppress", "--remove", data["edit"]["id"]
        )
        joined = " ".join(cue["text"] for cue in _dump(runner)["cues"])
        assert "intro setup" in joined

    def test_suppress_with_no_words_in_range_errors(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        result = runner.invoke(
            cli,
            ["captions", "suppress", "--from", "1.96", "--to", "1.99"],
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="captions suppress")


class TestFrozenTrackRejection:
    @pytest.mark.parametrize(
        "verb_args",
        [
            ("break", "--at-word", "where"),
            ("join", "--after-word", "is"),
            ("suppress", "--from", "0", "--to", "0.5"),
        ],
        ids=["break", "join", "suppress"],
    )
    def test_edit_verbs_reject_frozen_tracks(
        self, runner, test_video, tmp_path, monkeypatch, verb_args
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "materialize")
        result = runner.invoke(cli, ["captions", *verb_args])
        assert result.exit_code == 1, result.stdout
        data = json.loads(result.stdout)
        assert_error_envelope(data, command=f"captions {verb_args[0]}")
        assert "captions generate" in data["hint"]
        assert "overlays dump" in data["hint"]


class TestDumpRecipeBlock:
    def test_dump_exposes_the_recipe_with_edits(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "break", "--at-word", "where")
        _invoke(
            runner, "captions", "suppress", "--from", "0", "--to", "0.75"
        )
        data = _dump(runner)
        recipe = data["recipe"]
        assert recipe["style"] == "social-bold"
        assert recipe["placement"]["default"] == "bottom"
        ops = [edit["op"] for edit in recipe["edits"]]
        assert ops == ["break", "suppress"]
        assert all(edit["id"] for edit in recipe["edits"])

    def test_frozen_dump_keeps_recipe_block_with_dormant_edits(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "break", "--at-word", "where")
        _invoke(runner, "captions", "materialize")
        data = _dump(runner)
        # #412: the frozen recipe stays visible, edits dormant.
        assert data["caption_model"] == "materialized"
        assert data["recipe"]["edits"][0]["op"] == "break"


class TestFrictionFixes:
    """Regressions from the edit-verbs friction run (2026-08-12)."""

    def test_edit_ids_are_never_reused_after_removal(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "break", "--at-word", "where")
        _invoke(runner, "captions", "break", "--remove", "edit_0001")
        # A stale edit_0001 in an agent's context must never silently
        # address this new edit.
        data = _invoke(
            runner, "captions", "suppress", "--from", "0", "--to", "0.75"
        )
        assert data["edit"]["id"] == "edit_0002"
        result = runner.invoke(
            cli, ["captions", "suppress", "--remove", "edit_0001"]
        )
        assert result.exit_code == 1
        assert "edit_0001" in json.loads(result.stdout)["error"]

    def test_materialize_retains_recipe_edits_frozen(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        _invoke(runner, "captions", "break", "--at-word", "where")
        data = _invoke(runner, "captions", "materialize")
        # #412 evolves the #415 disclosure: nothing is discarded at all.
        assert data["recipe_frozen"] is True
        assert data["retained_edits_count"] == 1
        assert data["retained_edits"][0]["op"] == "break"

    def test_duplicate_suppress_names_the_existing_edit(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _project(runner, test_video, tmp_path, monkeypatch)
        first = _invoke(
            runner, "captions", "suppress", "--from", "0", "--to", "0.75"
        )
        result = runner.invoke(
            cli,
            ["captions", "suppress", "--from", "0", "--to", "0.75"],
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="captions suppress")
        assert first["edit"]["id"] in data["error"]
        assert "intro" in data["error"]
