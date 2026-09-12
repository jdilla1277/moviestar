"""Stage 7 CLI contract: undo undoes the last command.

The revision journal replaces the four per-family history stacks whose
independence produced the arc's original footgun — bare undo after
`scenes motion set` popping the composition while leaving the new
pacing in place. These tests lock the new contract end to end.
"""

import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from tests.conftest import assert_error_envelope


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner, *args) -> dict:
    result = runner.invoke(cli, list(args))
    assert result.exit_code == 0, f"{args}: {result.stdout}"
    return json.loads(result.stdout)


def _scene_project(runner, test_video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _invoke(
        runner,
        "load", test_video, test_video,
        "--as", "holden", "--as", "jdilla",
        "--interval", "1.0", "--no-transcribe",
    )
    _invoke(
        runner,
        "scenes", "set", "--canvas", "short",
        "--scene", "intro=single",
        "--slot", "intro:main=holden", "--from", "0", "--to", "1",
        "--audio-from", "intro=holden",
    )


def _set_pacing(runner, tmp_path):
    motion_file = tmp_path / "motion.json"
    motion_file.write_text(json.dumps({
        "version": 1,
        "scenes": [{
            "scene": "intro",
            "slots": [{
                "slot": "main",
                "pacing": [{
                    "id": "rush", "mode": "speed",
                    "range": {
                        "from": "0.2", "to": "0.8",
                        "space": "source-local",
                    },
                    "speed": 2.0,
                }],
            }],
        }],
    }))
    return _invoke(runner, "scenes", "motion", "set", str(motion_file))


def _spec(tmp_path):
    return json.loads((tmp_path / "moviestar" / "spec.json").read_text())


class TestBareUndoIsLastCommand:
    def test_bare_undo_after_motion_set_undoes_the_motion(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        """The arc's original footgun, finally correct: bare undo after
        a motion edit undoes the motion — not the composition."""
        _scene_project(runner, test_video, tmp_path, monkeypatch)
        _set_pacing(runner, tmp_path)

        data = _invoke(runner, "undo")
        assert data["status"] == "command_undone"
        assert data["undone"]["command"] == "scenes motion set"
        assert "motion" in data["undone"]["fields"]

        spec = _spec(tmp_path)
        assert spec["motion"]["scenes"] == []
        assert spec["composition"] is not None

    def test_undo_walks_commands_in_reverse_order(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _scene_project(runner, test_video, tmp_path, monkeypatch)
        _invoke(
            runner,
            "overlays", "add", "--text", "T",
            "--from", "0", "--to", "0.5",
        )
        first = _invoke(runner, "undo")
        assert first["undone"]["command"] == "overlays add"
        second = _invoke(runner, "undo")
        assert second["undone"]["command"] == "scenes set"
        spec = _spec(tmp_path)
        assert spec["composition"] is None
        assert spec["overlays"] == []

    def test_caption_edit_undo_restores_cues_and_recipe_together(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        from tests.conftest import (
            inject_synthetic_transcript,
            make_segment,
            make_word,
        )

        monkeypatch.chdir(tmp_path)
        _invoke(
            runner,
            "load", test_video, "--as", "src_0",
            "--interval", "1.0", "--no-transcribe",
        )
        words = [
            make_word("this", 0.1, 0.3), make_word("is", 0.4, 0.5),
            make_word("where", 0.6, 0.8), make_word("it", 0.9, 1.0),
            make_word("lands", 1.1, 1.4),
        ]
        inject_synthetic_transcript(
            tmp_path, words, [make_segment("this is where it lands", 0.1, 1.4)]
        )
        _invoke(
            runner,
            "scenes", "set", "--canvas", "short",
            "--scene", "intro=single",
            "--slot", "intro:main=src_0", "--from", "0", "--to", "2",
            "--audio-from", "intro=src_0",
        )
        _invoke(runner, "captions", "generate")
        before = _invoke(runner, "captions", "dump")
        _invoke(runner, "captions", "break", "--at-word", "where")

        data = _invoke(runner, "undo")
        assert data["undone"]["command"] == "captions break"
        assert set(data["undone"]["fields"]) >= {"captions", "overlays"}
        after = _invoke(runner, "captions", "dump")
        assert [c["text"] for c in after["cues"]] == [
            c["text"] for c in before["cues"]
        ]
        assert after["recipe"]["edits"] == []

    def test_trim_after_overlay_is_undone_before_overlay(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _invoke(
            runner,
            "load", test_video, "--as", "src_0",
            "--interval", "1.0", "--no-transcribe",
        )
        _invoke(
            runner,
            "overlays", "add", "--text", "T",
            "--from", "0", "--to", "0.5",
        )
        _invoke(runner, "trim", "--from", "0", "--to", "1")

        first = _invoke(runner, "undo")
        assert first["undone"]["command"] == "trim"
        assert first["undone"]["type"] == "trim"
        spec = _spec(tmp_path)
        assert spec["sources"][0]["operations"] == []
        assert len(spec["overlays"]) == 1

        second = _invoke(runner, "undo")
        assert second["undone"]["command"] == "overlays add"
        assert _spec(tmp_path)["overlays"] == []

    def test_bare_undo_disambiguates_latest_trim_in_multi_source_project(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _invoke(
            runner,
            "load", test_video, test_video,
            "--as", "left", "--as", "right",
            "--interval", "1.0", "--no-transcribe",
        )
        _invoke(
            runner,
            "trim", "--source", "right", "--from", "0", "--to", "1",
        )

        data = _invoke(runner, "undo")

        assert data["undone"]["command"] == "trim"
        assert data["source"]["id"] == "right"
        assert _spec(tmp_path)["sources"][1]["operations"] == []


class TestGuardedFamilyFlags:
    def test_wrong_family_guard_names_the_last_command(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _scene_project(runner, test_video, tmp_path, monkeypatch)
        _invoke(
            runner,
            "overlays", "add", "--text", "T",
            "--from", "0", "--to", "0.5",
        )
        result = runner.invoke(cli, ["undo", "--audio"])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="undo")
        assert "overlays add" in data["error"]

    def test_matching_family_guard_pops_normally(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _scene_project(runner, test_video, tmp_path, monkeypatch)
        _invoke(
            runner,
            "overlays", "add", "--text", "T",
            "--from", "0", "--to", "0.5",
        )
        data = _invoke(runner, "undo", "--overlays")
        assert data["undone"]["command"] == "overlays add"

    def test_composition_guard_covers_motion(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _scene_project(runner, test_video, tmp_path, monkeypatch)
        _set_pacing(runner, tmp_path)
        data = _invoke(runner, "undo", "--composition")
        assert data["undone"]["command"] == "scenes motion set"

    def test_source_guard_never_skips_a_newer_global_command(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _invoke(
            runner,
            "load", test_video, "--as", "src_0",
            "--interval", "1.0", "--no-transcribe",
        )
        _invoke(runner, "trim", "--from", "0", "--to", "1")
        _invoke(
            runner,
            "overlays", "add", "--text", "T",
            "--from", "0", "--to", "0.5",
        )

        result = runner.invoke(cli, ["undo", "--source", "src_0"])

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="undo")
        assert "overlays add" in data["error"]


class TestEmptyJournal:
    def test_empty_revision_journal_never_falls_back_to_old_histories(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _scene_project(runner, test_video, tmp_path, monkeypatch)
        spec_path = tmp_path / "moviestar" / "spec.json"
        spec = json.loads(spec_path.read_text())
        spec["revisions"] = []
        spec_path.write_text(json.dumps(spec))

        result = runner.invoke(cli, ["undo", "--composition"])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="undo")
        assert "revision" in data["error"].lower()
        assert _spec(tmp_path)["composition"] is not None

    def test_empty_revision_journal_never_falls_back_to_source_operations(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _invoke(
            runner,
            "load", test_video, "--as", "src_0",
            "--interval", "1.0", "--no-transcribe",
        )
        _invoke(runner, "trim", "--from", "0", "--to", "1")
        spec_path = tmp_path / "moviestar" / "spec.json"
        spec = json.loads(spec_path.read_text())
        spec["revisions"] = []
        spec_path.write_text(json.dumps(spec))

        result = runner.invoke(cli, ["undo", "--source", "src_0"])

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="undo")
        assert "revision" in data["error"].lower()
        assert len(_spec(tmp_path)["sources"][0]["operations"]) == 1

    def test_help_warns_how_to_port_old_specs(self, runner):
        result = runner.invoke(cli, ["undo", "--help"])
        assert result.exit_code == 0
        assert "Legacy spec files" in result.output
        assert '"revisions": []' in result.output
        assert "old undo order" in result.output
        assert "cannot undo edits made before the port" in result.output

    def test_spec_help_describes_replacement_as_one_atomic_revision(self, runner):
        result = runner.invoke(cli, ["spec", "--help"])

        assert result.exit_code == 0
        normalized = " ".join(result.output.split())
        assert "one atomic revision" in normalized
        assert "wipes history" not in result.output

    def test_cli_stops_after_retained_revision_limit(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _invoke(
            runner,
            "load", test_video, "--as", "src_0",
            "--interval", "1.0", "--no-transcribe",
        )
        for index in range(51):
            _invoke(
                runner,
                "overlays", "add", "--text", f"T{index}",
                "--from", "0", "--to", "0.5",
            )

        for _ in range(50):
            _invoke(runner, "undo")
        result = runner.invoke(cli, ["undo"])

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="undo")
        assert "no command revision" in data["error"].lower()
        assert len(_spec(tmp_path)["overlays"]) == 1

    def test_cli_error_includes_port_template_for_old_spec(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        _invoke(
            runner,
            "load", test_video, "--as", "src_0",
            "--interval", "1.0", "--no-transcribe",
        )
        _invoke(
            runner,
            "overlays", "add", "--text", "Keep me",
            "--from", "0", "--to", "0.5",
        )
        spec_path = tmp_path / "moviestar" / "spec.json"
        spec = json.loads(spec_path.read_text())
        spec["overlays_history"] = [[]]
        spec_path.write_text(json.dumps(spec))

        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["command"] == "status"
        assert "current edit is portable" in data["error"]
        assert "spec.pre-stage7.json" in data["error"]
        assert "mv moviestar/spec.stage7.json moviestar/spec.json" in data["error"]
