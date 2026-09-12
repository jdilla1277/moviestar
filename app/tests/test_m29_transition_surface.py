"""M29 scene-transition authoring and resolver surface."""

import json
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from tests.conftest import assert_error_envelope, assert_no_project_envelope


@pytest.fixture
def runner():
    return CliRunner()


def _two_scene_project(runner, loaded_project, test_video):
    loaded_project(test_video, frames=False)
    result = runner.invoke(
        cli,
        [
            "scenes",
            "set",
            "--canvas",
            "short",
            "--scene",
            "intro=single",
            "--slot",
            "intro:main=src_0",
            "--from",
            "0.2",
            "--to",
            "0.8",
            "--scene",
            "demo=single",
            "--slot",
            "demo:main=src_0",
            "--from",
            "1.0",
            "--to",
            "1.6",
        ],
    )
    assert result.exit_code == 0, result.stdout


class TestM29TransitionDiscoverySurface:
    def test_root_and_scenes_help_expose_transition_authoring(self, runner):
        root = runner.invoke(cli, ["--help"])
        scenes = runner.invoke(cli, ["scenes", "--help"])

        assert root.exit_code == 0
        assert scenes.exit_code == 0
        assert "scenes transition" in root.output
        assert "transition" in scenes.output
        assert "visual transitions at scene boundaries" in " ".join(
            scenes.output.split()
        )

    def test_transition_help_teaches_targeting_timing_handles_and_audio(self, runner):
        result = runner.invoke(cli, ["scenes", "transition", "--help"])

        assert result.exit_code == 0
        compact = " ".join(result.output.split())
        for token in (
            "--before SCENE",
            "--opening",
            "--closing",
            "--all",
            "--type",
            "dissolve",
            "dip-black",
            "dip-white",
            "--duration",
            "0.5 seconds",
            "--remove",
            "--dry-run",
            "centered on the cut",
            "source handles",
            "available handles",
            "Every visible slot",
            "playback speed",
            "result duration does not change",
            "Audio is unchanged",
        ):
            assert token in compact

    def test_bare_transition_command_returns_catalog_without_a_project(self, runner):
        result = runner.invoke(cli, ["scenes", "transition"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["status"] == "scene_transition_authoring"
        assert data["writes_spec"] is False
        assert data["default_duration"]["seconds"] == pytest.approx(0.5)
        assert [item["type"] for item in data["transition_types"]] == [
            "dissolve",
            "dip-black",
            "dip-white",
        ]
        assert data["default_boundary_behavior"] == "cut"
        assert data["audio_behavior"] == "unchanged"
        assert data["scene_file_shape"] == {
            "internal_boundary": {
                "location": "incoming scene.transition_in",
                "example": {"type": "dissolve", "duration": 0.5},
            },
            "project_edges": {
                "opening": "opening_transition",
                "closing": "closing_transition",
            },
        }
        assert "--before" in data["hint"]


class TestM29TransitionResolverEnvelope:
    def test_targeted_transition_resolves_boundary_and_writes_spec(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        spec_path = Path("moviestar/spec.json")
        before = spec_path.read_bytes()

        result = runner.invoke(
            cli,
            [
                "scenes",
                "transition",
                "--before",
                "demo",
                "--type",
                "dissolve",
                "--duration",
                "0.5",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "set_scene_transition"
        assert data["writes_spec"] is True
        assert data["requested_dry_run"] is False
        assert "mocked_surface_note" not in data
        assert data["resolution_note"].startswith(
            "Boundary, timing, and source-handle validation are real"
        )
        assert data["ownership"] == "incoming_scene"
        assert data["selector"] == {"before": "demo"}
        assert data["boundary"]["outgoing"]["scene"] == "intro"
        assert data["boundary"]["incoming"]["scene"] == "demo"
        assert data["boundary"]["outgoing"]["scene_id"] == "scene_0001"
        assert data["boundary"]["incoming"]["scene_id"] == "scene_0002"
        assert data["boundary"]["result_cut_at"]["seconds"] == pytest.approx(0.6)
        assert data["transition"]["type"] == "dissolve"
        assert data["transition"]["duration"]["seconds"] == pytest.approx(0.5)
        assert data["transition"]["alignment"] == "center"
        assert data["transition"]["result_window"]["from"]["seconds"] \
            == pytest.approx(0.35)
        assert data["transition"]["result_window"]["to"]["seconds"] \
            == pytest.approx(0.85)
        assert data["transition"]["result_duration_change"]["seconds"] == 0
        assert data["source_handles"]["policy"] == "half_duration_each_side"
        assert data["source_handles"]["required_each_side"]["seconds"] \
            == pytest.approx(0.25)
        assert data["source_handles"]["maximum_duration"]["seconds"] \
            == pytest.approx(1.2)
        assert data["source_handles"]["sufficient"] is True
        [outgoing] = data["source_handles"]["outgoing"]
        assert outgoing["scene_id"] == "scene_0001"
        assert outgoing["slot_id"] == "slot_0001"
        assert outgoing["source"] == "src_0"
        assert outgoing["boundary_source_time"]["seconds"] == pytest.approx(0.8)
        assert outgoing["required"]["seconds"] == pytest.approx(0.25)
        assert outgoing["available"]["seconds"] == pytest.approx(1.2)
        assert outgoing["available_in_result_time"]["seconds"] \
            == pytest.approx(1.2)
        assert outgoing["sufficient"] is True
        [incoming] = data["source_handles"]["incoming"]
        assert incoming["scene_id"] == "scene_0002"
        assert incoming["boundary_source_time"]["seconds"] == pytest.approx(1.0)
        assert incoming["available"]["seconds"] == pytest.approx(1.0)
        progress = data["boundary"]["visual_progress"]
        assert [sample["position"] for sample in progress] == [
            "start",
            "cut",
            "end",
        ]
        assert progress[1]["outgoing_weight"] == pytest.approx(0.5)
        assert progress[1]["incoming_weight"] == pytest.approx(0.5)
        assert progress[1]["color_weight"] == 0
        assert "source_handles" not in data["boundary"]
        assert data["audio"] == {
            "behavior": "unchanged",
            "crossfade": False,
            "timeline_shift": False,
        }
        assert spec_path.read_bytes() != before

    def test_cli_handle_math_uses_resolved_boundary_playback_speed(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        spec_path = Path("moviestar/spec.json")
        spec = json.loads(spec_path.read_text())
        spec["motion"] = {
            "version": 1,
            "scenes": [
                {
                    "scene": "intro",
                    "slots": [
                        {
                            "slot": "main",
                            "pacing": [
                                {
                                    "id": "fast-edge",
                                    "mode": "speed",
                                    "range": {
                                        "from": "0:00:00.300",
                                        "to": "0:00:00.600",
                                        "space": "source-local",
                                    },
                                    "speed": 2.0,
                                }
                            ],
                            "camera": [],
                        }
                    ],
                }
            ],
        }
        spec_path.write_text(json.dumps(spec, indent=2))
        before = spec_path.read_bytes()

        result = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dissolve", "--duration", "0.5", "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["boundary"]["result_cut_at"]["seconds"] \
            == pytest.approx(0.45)
        [outgoing] = data["source_handles"]["outgoing"]
        assert outgoing["edge_speed"] == pytest.approx(2.0)
        assert outgoing["required"]["seconds"] == pytest.approx(0.5)
        assert outgoing["available_in_result_time"]["seconds"] \
            == pytest.approx(0.6)
        assert spec_path.read_bytes() == before

    def test_insufficient_handle_error_names_limit_and_exact_remedies(
        self, runner, loaded_project, test_video
    ):
        loaded_project(test_video, frames=False)
        set_result = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "intro=single",
                "--slot", "intro:main=src_0", "--from", "0.2", "--to", "0.8",
                "--scene", "demo=single",
                "--slot", "demo:main=src_0", "--from", "0.05", "--to", "0.65",
            ],
        )
        assert set_result.exit_code == 0, set_result.stdout

        result = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dissolve", "--duration", "0.5",
            ],
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes transition")
        assert data["code"] == "insufficient_transition_media"
        assert data["requested_duration"]["seconds"] == pytest.approx(0.5)
        assert data["maximum_duration"]["seconds"] == pytest.approx(0.1)
        assert data["limit_reason"] == "source_handles"
        [limiting] = data["limiting_handles"]
        assert limiting["scene"] == "demo"
        assert limiting["slot"] == "main"
        assert limiting["side"] == "incoming"
        assert limiting["required"]["seconds"] == pytest.approx(0.25)
        assert limiting["available"]["seconds"] == pytest.approx(0.05)
        assert "--duration 0.1" in data["hint"]
        assert "retrim" in data["hint"]

    def test_multi_slot_error_identifies_only_the_short_slot(
        self, runner, loaded_project, test_video
    ):
        loaded_project(
            [test_video, test_video],
            names=["screen", "speaker"],
            frames=False,
        )
        set_result = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "intro=picture-in-picture",
                "--slot", "intro:main=screen", "--from", "0.2", "--to", "0.8",
                "--slot", "intro:inset=speaker", "--from", "0.2", "--to", "0.8",
                "--audio-from", "intro=screen",
                "--scene", "demo=picture-in-picture",
                "--slot", "demo:main=screen", "--from", "0.5", "--to", "1.1",
                "--slot", "demo:inset=speaker", "--from", "0.05", "--to", "0.65",
                "--audio-from", "demo=screen",
            ],
        )
        assert set_result.exit_code == 0, set_result.stdout

        result = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dissolve", "--duration", "0.5",
            ],
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["maximum_duration"]["seconds"] == pytest.approx(0.1)
        assert [(item["source"], item["slot"]) for item in data["limiting_handles"]] \
            == [("speaker", "inset")]
        assert len(data["source_handles"]["incoming"]) == 2
        by_slot = {
            item["slot"]: item for item in data["source_handles"]["incoming"]
        }
        assert by_slot["main"]["sufficient"] is True
        assert by_slot["inset"]["sufficient"] is False

    def test_bulk_validation_reports_only_failing_boundaries(
        self, runner, loaded_project, test_video
    ):
        loaded_project(test_video, frames=False)
        set_result = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "intro=single",
                "--slot", "intro:main=src_0", "--from", "0.2", "--to", "0.8",
                "--scene", "demo=single",
                "--slot", "demo:main=src_0", "--from", "0.05", "--to", "0.65",
                "--scene", "ending=single",
                "--slot", "ending:main=src_0", "--from", "1.0", "--to", "1.6",
            ],
        )
        assert set_result.exit_code == 0, set_result.stdout

        result = runner.invoke(
            cli,
            [
                "scenes", "transition", "--all",
                "--type", "dissolve", "--duration", "0.5",
            ],
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["code"] == "insufficient_transition_media"
        assert len(data["failed_boundaries"]) == 1
        [failed] = data["failed_boundaries"]
        assert failed["outgoing"]["scene"] == "intro"
        assert failed["incoming"]["scene"] == "demo"
        assert "source_handles" not in failed
        assert data["source_handles"]["sufficient"] is False
        assert data["maximum_duration"]["seconds"] == pytest.approx(0.1)

    def test_bulk_on_one_scene_is_an_explicit_noop(
        self, runner, loaded_project, test_video
    ):
        loaded_project(test_video, frames=False)
        set_result = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "only=single",
                "--slot", "only:main=src_0", "--from", "0.2", "--to", "0.8",
            ],
        )
        assert set_result.exit_code == 0, set_result.stdout

        result = runner.invoke(
            cli,
            [
                "scenes", "transition", "--all",
                "--type", "dissolve", "--duration", "0.5",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "no_internal_transition_boundaries"
        assert data["writes_spec"] is False
        assert data["boundaries_count"] == 0
        assert data["boundaries"] == []
        assert "second scene" in data["hint"]

    def test_default_duration_and_explicit_dry_run_are_visible(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            [
                "scenes",
                "transition",
                "--before",
                "scene_0002",
                "--type",
                "dip-black",
                "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["requested_dry_run"] is True
        assert data["transition"]["duration"]["seconds"] == pytest.approx(0.5)
        assert data["source_handles"] == {
            "policy": "not_required",
            "reason": "dip transitions use visible scene content",
        }
        assert data["transition"]["maximum_duration"]["seconds"] \
            == pytest.approx(1.2)

    def test_remove_reports_when_boundary_is_already_the_default_cut(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            ["scenes", "transition", "--before", "demo", "--remove"],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "scene_transition_already_cut"
        assert data["writes_spec"] is False
        assert data["state_changed"] is False
        assert data["boundary"]["incoming"]["scene"] == "demo"
        assert data["resulting_boundary_behavior"] == "cut"
        assert "already a cut" in data["hint"]

    def test_targeting_without_project_uses_standard_error_envelope(self, runner):
        result = runner.invoke(
            cli,
            [
                "scenes",
                "transition",
                "--before",
                "demo",
                "--type",
                "dissolve",
            ],
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_no_project_envelope(data, command="scenes transition")

    def test_unknown_scene_error_teaches_stable_available_targets(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            [
                "scenes",
                "transition",
                "--before",
                "missing",
                "--type",
                "dissolve",
            ],
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes transition")
        assert data["available_scenes"] == [
            {"scene": "intro", "scene_id": "scene_0001"},
            {"scene": "demo", "scene_id": "scene_0002"},
        ]
        assert "--before" in data["hint"]

    def test_first_scene_error_points_to_opening_fade(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            [
                "scenes",
                "transition",
                "--before",
                "intro",
                "--type",
                "dip-black",
            ],
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes transition")
        assert "first scene" in data["error"]
        assert "--opening" in data["hint"]

    @pytest.mark.parametrize("edge", ["--opening", "--closing"])
    def test_project_edges_require_a_color_fade(
        self, runner, loaded_project, test_video, edge
    ):
        _two_scene_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            ["scenes", "transition", edge, "--type", "dissolve"],
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes transition")
        assert "project edge" in data["error"]
        assert "dip-black" in data["hint"]
        assert "dip-white" in data["hint"]

    def test_edge_fade_rejects_duration_beyond_project_instead_of_clamping(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            [
                "scenes", "transition", "--opening",
                "--type", "dip-black", "--duration", "2",
            ],
        )

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="scenes transition")
        assert data["code"] == "transition_window_too_long"
        assert data["maximum_duration"]["seconds"] == pytest.approx(1.2)
        assert "--duration 1.2" in data["hint"]

    def test_transition_hint_states_rendering_limit_once(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)

        result = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dip-white",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["hint"].count("render in export") == 1


class TestM29TransitionPersistence:
    def test_targeted_set_persists_and_scenes_list_reads_it_back(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        spec_path = Path("moviestar/spec.json")
        before = json.loads(spec_path.read_text())

        result = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dissolve", "--duration", "0.5",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "set_scene_transition"
        assert data["writes_spec"] is True
        assert data["state_changed"] is True
        stored = json.loads(spec_path.read_text())
        assert stored["composition"][1]["transition_in"] == {
            "type": "dissolve",
            "duration": 0.5,
        }
        assert stored["revisions"][-1]["command"] == "scenes transition"
        assert stored["revisions"][-1]["changed"]["composition"] \
            == before["composition"]

        listed = runner.invoke(cli, ["scenes", "list"])
        assert listed.exit_code == 0, listed.stdout
        listed_data = json.loads(listed.stdout)
        assert listed_data["scenes"][1]["transition_in"]["type"] == "dissolve"
        assert listed_data["scenes"][1]["transition_in"]["duration"]["seconds"] \
            == pytest.approx(0.5)
        assert any(
            warning["code"] == "authored_transitions_not_rendered"
            for warning in listed_data["warnings"]
        )

        catalog = runner.invoke(cli, ["scenes", "transition"])
        assert catalog.exit_code == 0, catalog.stdout
        catalog_data = json.loads(catalog.stdout)
        assert catalog_data["authored_transitions"]["internal"] == [
            {
                "incoming": {"scene": "demo", "scene_id": "scene_0002"},
                "transition": {
                    "type": "dissolve",
                    "duration": {"text": "0:00:00.500", "seconds": 0.5},
                },
            }
        ]

    def test_dry_run_resolves_same_mutation_without_writing(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        spec_path = Path("moviestar/spec.json")
        before = spec_path.read_bytes()

        result = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dip-black", "--duration", "0.4", "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "would_set_scene_transition"
        assert data["writes_spec"] is False
        assert data["state_changed"] is True
        assert data["requested_dry_run"] is True
        assert spec_path.read_bytes() == before
        assert "Re-run without --dry-run" in data["hint"]

    def test_replacement_is_one_revision_and_undo_restores_previous_value(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        first = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dip-black", "--duration", "0.4",
            ],
        )
        assert first.exit_code == 0, first.stdout
        after_first = json.loads(Path("moviestar/spec.json").read_text())

        replacement = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "scene_0002",
                "--type", "dip-white", "--duration", "0.3",
            ],
        )

        assert replacement.exit_code == 0, replacement.stdout
        stored = json.loads(Path("moviestar/spec.json").read_text())
        assert stored["composition"][1]["transition_in"] == {
            "type": "dip-white",
            "duration": 0.3,
        }
        assert len(stored["revisions"]) == len(after_first["revisions"]) + 1

        undone = runner.invoke(cli, ["undo", "--composition"])
        assert undone.exit_code == 0, undone.stdout
        restored = json.loads(Path("moviestar/spec.json").read_text())
        assert restored["composition"][1]["transition_in"] == {
            "type": "dip-black",
            "duration": 0.4,
        }

    def test_remove_persists_cut_and_undo_restores_transition(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        set_result = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dip-black", "--duration", "0.4",
            ],
        )
        assert set_result.exit_code == 0, set_result.stdout

        removed = runner.invoke(
            cli, ["scenes", "transition", "--before", "demo", "--remove"]
        )

        assert removed.exit_code == 0, removed.stdout
        removed_data = json.loads(removed.stdout)
        assert removed_data["status"] == "removed_scene_transition"
        assert removed_data["writes_spec"] is True
        assert removed_data["state_changed"] is True
        assert (
            removed_data["boundary"]["result_window"]["to"]["seconds"]
            - removed_data["boundary"]["result_window"]["from"]["seconds"]
        ) == pytest.approx(0.4)
        stored = json.loads(Path("moviestar/spec.json").read_text())
        assert "transition_in" not in stored["composition"][1]

        undone = runner.invoke(cli, ["undo", "--composition"])
        assert undone.exit_code == 0, undone.stdout
        restored = json.loads(Path("moviestar/spec.json").read_text())
        assert restored["composition"][1]["transition_in"]["type"] \
            == "dip-black"

    def test_repeating_identical_set_is_noop_without_revision(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        command = [
            "scenes", "transition", "--before", "demo",
            "--type", "dip-black", "--duration", "0.4",
        ]
        first = runner.invoke(cli, command)
        assert first.exit_code == 0, first.stdout
        spec_path = Path("moviestar/spec.json")
        before = spec_path.read_bytes()
        revisions_before = len(json.loads(before)["revisions"])

        repeated = runner.invoke(cli, command)

        assert repeated.exit_code == 0, repeated.stdout
        data = json.loads(repeated.stdout)
        assert data["status"] == "scene_transition_unchanged"
        assert data["writes_spec"] is False
        assert data["state_changed"] is False
        assert spec_path.read_bytes() == before
        assert len(json.loads(spec_path.read_text())["revisions"]) \
            == revisions_before

    def test_bulk_set_is_atomic_and_records_one_revision(
        self, runner, loaded_project, test_video
    ):
        loaded_project(test_video, frames=False)
        set_result = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "intro=single",
                "--slot", "intro:main=src_0", "--from", "0.2", "--to", "0.8",
                "--scene", "demo=single",
                "--slot", "demo:main=src_0", "--from", "1.0", "--to", "1.6",
                "--scene", "end=single",
                "--slot", "end:main=src_0", "--from", "0.5", "--to", "1.1",
            ],
        )
        assert set_result.exit_code == 0, set_result.stdout
        before = json.loads(Path("moviestar/spec.json").read_text())

        bulk = runner.invoke(
            cli,
            [
                "scenes", "transition", "--all",
                "--type", "dip-black", "--duration", "0.4",
            ],
        )

        assert bulk.exit_code == 0, bulk.stdout
        data = json.loads(bulk.stdout)
        assert data["status"] == "set_scene_transitions"
        assert data["writes_spec"] is True
        stored = json.loads(Path("moviestar/spec.json").read_text())
        assert "transition_in" not in stored["composition"][0]
        assert [scene["transition_in"] for scene in stored["composition"][1:]] \
            == [
                {"type": "dip-black", "duration": 0.4},
                {"type": "dip-black", "duration": 0.4},
            ]
        assert len(stored["revisions"]) == len(before["revisions"]) + 1
        assert set(stored["revisions"][-1]["changed"]) == {"composition"}

    def test_opening_and_closing_fades_persist_and_guarded_undo_works(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        opening = runner.invoke(
            cli,
            [
                "scenes", "transition", "--opening",
                "--type", "dip-black", "--duration", "0.4",
            ],
        )
        assert opening.exit_code == 0, opening.stdout
        closing = runner.invoke(
            cli,
            [
                "scenes", "transition", "--closing",
                "--type", "dip-white", "--duration", "0.3",
            ],
        )
        assert closing.exit_code == 0, closing.stdout
        stored = json.loads(Path("moviestar/spec.json").read_text())
        assert stored["opening_transition"] == {
            "type": "dip-black", "duration": 0.4,
        }
        assert stored["closing_transition"] == {
            "type": "dip-white", "duration": 0.3,
        }
        assert set(stored["revisions"][-1]["changed"]) \
            == {"closing_transition"}

        undone = runner.invoke(cli, ["undo", "--composition"])
        assert undone.exit_code == 0, undone.stdout
        restored = json.loads(Path("moviestar/spec.json").read_text())
        assert restored["opening_transition"]["type"] == "dip-black"
        assert restored["closing_transition"] is None

    def test_reauthoring_same_scene_preserves_attached_transition(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        authored = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dip-black", "--duration", "0.4",
            ],
        )
        assert authored.exit_code == 0, authored.stdout

        reset = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "short",
                "--scene", "intro=single",
                "--slot", "intro:main=src_0", "--from", "0.2", "--to", "0.8",
                "--scene", "demo=single",
                "--slot", "demo:main=src_0", "--from", "1.0", "--to", "1.6",
            ],
        )

        assert reset.exit_code == 0, reset.stdout
        stored = json.loads(Path("moviestar/spec.json").read_text())
        assert stored["composition"][1]["transition_in"] == {
            "type": "dip-black", "duration": 0.4,
        }

    def test_export_dry_run_renders_authored_internal_transition(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        authored = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dip-black", "--duration", "0.4",
            ],
        )
        assert authored.exit_code == 0, authored.stdout

        preview = runner.invoke(cli, ["export", "--dry-run"])

        assert preview.exit_code == 0, preview.stdout
        data = json.loads(preview.stdout)
        assert data["transition_render_status"] == "active"
        [transition] = data["transition_render_plan"]
        assert transition["type"] == "dip-black"
        assert transition["duration"]["seconds"] == pytest.approx(0.4)
        assert transition["result_window"]["from"]["seconds"] \
            == pytest.approx(0.4)
        assert transition["result_window"]["to"]["seconds"] \
            == pytest.approx(0.8)
        assert transition["outgoing"]["scene"] == "intro"
        assert transition["incoming"]["scene"] == "demo"
        graph = data["concat_command"][
            data["concat_command"].index("-filter_complex") + 1
        ]
        assert "fade=t=out:st=0.4:d=0.2:color=black" in graph
        assert "fade=t=in:st=0:d=0.2:color=black" in graph
        assert "xfade" not in graph
        assert not any(
            item["code"] == "authored_transitions_not_rendered"
            for item in data.get("warnings", [])
        )
        first, second = data["scene_render_commands"]
        assert first["video_duration"]["seconds"] == pytest.approx(0.6)
        assert second["video_duration"]["seconds"] == pytest.approx(0.6)

    def test_export_still_warns_for_unrendered_project_edge_transition(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        authored = runner.invoke(
            cli,
            [
                "scenes", "transition", "--opening",
                "--type", "dip-black", "--duration", "0.4",
            ],
        )
        assert authored.exit_code == 0, authored.stdout

        preview = runner.invoke(cli, ["export", "--dry-run"])

        assert preview.exit_code == 0, preview.stdout
        data = json.loads(preview.stdout)
        warning = next(
            item
            for item in data["warnings"]
            if item["code"] == "authored_transitions_not_rendered"
        )
        assert warning["authored_transitions_count"] == 1
        assert warning["render_status"] == "not_implemented"
        assert warning["output_behavior"] == "hard_cuts"

    def test_export_dissolve_consumes_real_handles_on_scene_clips(
        self, runner, loaded_project, test_video
    ):
        _two_scene_project(runner, loaded_project, test_video)
        authored = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dissolve", "--duration", "0.4",
            ],
        )
        assert authored.exit_code == 0, authored.stdout

        preview = runner.invoke(cli, ["export", "--dry-run"])

        assert preview.exit_code == 0, preview.stdout
        data = json.loads(preview.stdout)
        [transition] = data["transition_render_plan"]
        assert transition["source_handles"]["policy"] \
            == "half_duration_each_side"
        assert transition["source_handles"]["sufficient"] is True
        first, second = data["scene_render_commands"]
        assert first["transition_postroll"]["seconds"] == pytest.approx(0.2)
        assert second["transition_preroll"]["seconds"] == pytest.approx(0.2)
        assert first["video_duration"]["seconds"] == pytest.approx(0.8)
        assert second["video_duration"]["seconds"] == pytest.approx(0.8)
        incoming_graph = second["command"][
            second["command"].index("-filter_complex") + 1
        ]
        assert "start_duration" not in incoming_graph


class TestM29TransitionRendering:
    @staticmethod
    def _solid_clip(path, color, frequency):
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=30:d=2",
                "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(path),
            ],
            check=True,
        )

    @staticmethod
    def _pixel(path, at_s):
        raw = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", str(at_s), "-i", str(path),
                "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
                "-pix_fmt", "rgb24", "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        return tuple(raw[:3])

    def test_scene_export_renders_real_dissolve_pixels_without_time_shift(
        self, runner, loaded_project, tmp_path
    ):
        red = tmp_path / "red.mp4"
        blue = tmp_path / "blue.mp4"
        output = tmp_path / "scene-dissolve.mp4"
        self._solid_clip(red, "red", 440)
        self._solid_clip(blue, "blue", 880)
        loaded_project([str(red), str(blue)], names=["red", "blue"], frames=False)
        scenes = runner.invoke(
            cli,
            [
                "scenes", "set", "--canvas", "320x240",
                "--scene", "intro=single",
                "--slot", "intro:main=red", "--from", "0.2", "--to", "0.8",
                "--scene", "demo=single",
                "--slot", "demo:main=blue", "--from", "0.4", "--to", "1.0",
            ],
        )
        assert scenes.exit_code == 0, scenes.stdout
        authored = runner.invoke(
            cli,
            [
                "scenes", "transition", "--before", "demo",
                "--type", "dissolve", "--duration", "0.4",
            ],
        )
        assert authored.exit_code == 0, authored.stdout

        rendered = runner.invoke(cli, ["export", "--out", str(output), "--quiet"])

        assert rendered.exit_code == 0, rendered.stdout
        data = json.loads(rendered.stdout)
        assert data["transition_render_status"] == "active"
        assert data["result_duration"]["seconds"] == pytest.approx(1.2)
        early = self._pixel(output, 0.2)
        middle = self._pixel(output, 0.6)
        late = self._pixel(output, 1.0)
        assert early[0] > 200 and early[2] < 50
        assert middle[0] > 70 and middle[2] > 70
        assert late[2] > 200 and late[0] < 50
