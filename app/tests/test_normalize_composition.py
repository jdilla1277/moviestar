"""Stage 3 B1: flat concat compositions normalize to scene compositions.

The pure transform (`normalize_composition_storage`) rewrites a stored
flat concat as one single-slot full-frame scene per segment, carrying
`composition_authored_as: "concat"` so read surfaces keep reporting the
vocabulary the agent authored. Canvasless concats get a canvas inferred
from the first segment's source dimensions (even-ized), matching the
current canvasless render rule.

Applied in memory at spec load; the normalized shape persists on the
next write.
"""

import pytest

from moviestar.spec import (
    composition_shape,
    empty_spec,
    normalize_composition_storage,
    validate_spec,
)

DIMS = {"holden": (320, 240), "jdilla": (854, 480)}


def _flat_spec(*, canvas=False, audio_from=None, framing=False):
    spec = empty_spec(
        [
            {"id": "holden", "path": "/h.mp4"},
            {"id": "jdilla", "path": "/j.mp4"},
        ]
    )
    seg = {
        "source": "holden",
        "source_from": "0:00:00.000",
        "source_to": "0:00:01.000",
    }
    seg2 = {
        "source": "jdilla",
        "source_from": "0:00:00.500",
        "source_to": "0:00:01.500",
    }
    if framing:
        seg["framing"] = {"mode": "fill", "anchor": "left"}
    spec["composition"] = [seg, seg2]
    if canvas:
        spec["composition_canvas"] = {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }
    if audio_from:
        spec["composition_audio_from"] = audio_from
    validate_spec(spec)
    return spec


class TestNormalizeFlatConcat:
    def test_segments_become_single_slot_scenes(self):
        spec = normalize_composition_storage(_flat_spec(), DIMS)
        validate_spec(spec)
        assert composition_shape(spec) == "scenes"
        assert spec["composition_authored_as"] == "concat"
        scenes = spec["composition"]
        assert [scene["name"] for scene in scenes] == [
            "segment_1", "segment_2",
        ]
        for scene in scenes:
            assert scene["layout"] == {"preset": "single"}
            assert len(scene["slots"]) == 1
            assert scene["slots"][0]["slot"] == "main"
            assert "audio_from" not in scene
        assert scenes[0]["slots"][0]["source"] == "holden"
        assert scenes[0]["slots"][0]["source_from"] == "0:00:00.000"
        assert scenes[1]["slots"][0]["source"] == "jdilla"
        assert scenes[1]["slots"][0]["source_to"] == "0:00:01.500"

    def test_canvas_inferred_from_first_source_dims(self):
        spec = normalize_composition_storage(_flat_spec(), DIMS)
        canvas = spec["composition_canvas"]
        assert canvas["preset"] is None
        assert (canvas["width"], canvas["height"]) == (320, 240)
        assert canvas["aspect_ratio"] == "4:3"

    def test_inferred_canvas_dimensions_are_even_ized(self):
        dims = {"holden": (853, 481), "jdilla": (854, 480)}
        spec = normalize_composition_storage(_flat_spec(), dims)
        canvas = spec["composition_canvas"]
        assert (canvas["width"], canvas["height"]) == (852, 480)
        validate_spec(spec)

    def test_existing_canvas_and_framing_are_preserved(self):
        spec = normalize_composition_storage(
            _flat_spec(canvas=True, framing=True), DIMS
        )
        assert spec["composition_canvas"]["preset"] == "short"
        slot = spec["composition"][0]["slots"][0]
        assert slot["framing"] == {"mode": "fill", "anchor": "left"}
        assert "framing" not in spec["composition"][1]["slots"][0]

    def test_composition_audio_from_survives(self):
        spec = normalize_composition_storage(
            _flat_spec(audio_from="holden"), DIMS
        )
        assert spec["composition_audio_from"] == "holden"

    def test_normalization_is_idempotent(self):
        once = normalize_composition_storage(_flat_spec(), DIMS)
        twice = normalize_composition_storage(once, DIMS)
        assert twice == once

    def test_scene_authored_spec_passes_through(self):
        spec = empty_spec([{"id": "holden", "path": "/h.mp4"}])
        spec["composition"] = [
            {
                "name": "intro",
                "layout": {"preset": "single"},
                "slots": [
                    {
                        "slot": "main",
                        "source": "holden",
                        "source_from": "0:00:00.000",
                        "source_to": "0:00:01.000",
                    },
                ],
                "audio_from": "holden",
            },
        ]
        spec["composition_canvas"] = {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }
        validate_spec(spec)
        out = normalize_composition_storage(spec, DIMS)
        assert out == spec

    def test_no_composition_passes_through(self):
        spec = empty_spec([{"id": "holden", "path": "/h.mp4"}])
        out = normalize_composition_storage(spec, DIMS)
        assert out == spec

    def test_unknown_dims_fall_back_to_a_default_canvas(self):
        spec = normalize_composition_storage(_flat_spec(), {"holden": None, "jdilla": None})
        canvas = spec["composition_canvas"]
        assert (canvas["width"], canvas["height"]) == (1920, 1080)
        validate_spec(spec)


def _layout_spec(*, audio_from=None, slot_id=False):
    spec = empty_spec(
        [
            {"id": "holden", "path": "/h.mp4"},
            {"id": "jdilla", "path": "/j.mp4"},
        ]
    )
    slot_top = {
        "slot": "top",
        "source": "holden",
        "source_from": "0:00:00.000",
        "source_to": "0:00:01.000",
    }
    if slot_id:
        slot_top["id"] = "slot_0001"
    scene = {
        "layout": {"preset": "two-up"},
        "slots": [
            slot_top,
            {
                "slot": "bottom",
                "source": "jdilla",
                "source_from": "0:00:00.000",
                "source_to": "0:00:01.000",
                "framing": {"mode": "fill", "anchor": "left"},
            },
        ],
    }
    spec["composition"] = [scene]
    spec["composition_canvas"] = {
        "preset": "short",
        "width": 1080,
        "height": 1920,
        "aspect_ratio": "9:16",
    }
    if audio_from:
        spec["composition_audio_from"] = audio_from
    validate_spec(spec)
    return spec


class TestNormalizeGlobalLayout:
    """Stage 3 B2: unnamed single-scene layouts become named scene
    compositions with ``composition_authored_as: "layout"``."""

    def test_unnamed_layout_becomes_named_scene(self):
        spec = normalize_composition_storage(_layout_spec(), DIMS)
        validate_spec(spec)
        assert composition_shape(spec) == "scenes"
        assert spec["composition_authored_as"] == "layout"
        scenes = spec["composition"]
        assert len(scenes) == 1
        assert scenes[0]["name"] == "scene_1"
        assert scenes[0]["layout"] == {"preset": "two-up"}
        assert "audio_from" not in scenes[0]

    def test_slots_framing_and_ids_are_preserved_exactly(self):
        spec = normalize_composition_storage(_layout_spec(slot_id=True), DIMS)
        slots = spec["composition"][0]["slots"]
        assert slots[0]["id"] == "slot_0001"
        assert slots[1]["framing"] == {"mode": "fill", "anchor": "left"}
        assert [slot["slot"] for slot in slots] == ["top", "bottom"]

    def test_canvas_and_composition_audio_from_survive(self):
        spec = normalize_composition_storage(
            _layout_spec(audio_from="holden"), DIMS
        )
        assert spec["composition_canvas"]["preset"] == "short"
        assert spec["composition_audio_from"] == "holden"

    def test_normalization_is_idempotent(self):
        once = normalize_composition_storage(_layout_spec(), DIMS)
        twice = normalize_composition_storage(once, DIMS)
        assert twice == once

    def test_named_scene_composition_is_not_treated_as_layout(self):
        spec = _layout_spec()
        spec["composition"][0]["name"] = "conversation"
        validate_spec(spec)
        out = normalize_composition_storage(spec, DIMS)
        assert out == spec


class TestConcatAudioPolicyNote:
    """The stage-3 policy change is surfaced, not silent: a
    concat-authored composition mixing silent and audible segments now
    keeps per-segment audio (the scene pipeline), where flat concat
    exports used to drop audio entirely."""

    def test_mixed_silent_and_audible_concat_carries_policy_note(
        self, tmp_path, monkeypatch, test_video, silent_video
    ):
        import json

        from click.testing import CliRunner

        from moviestar.cli import cli

        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        load = runner.invoke(
            cli,
            [
                "load", test_video, silent_video,
                "--as", "cam", "--as", "screen",
                "--interval", "1.0", "--no-transcribe",
            ],
        )
        assert load.exit_code == 0, load.stdout
        concat = runner.invoke(
            cli,
            [
                "concat",
                "--segment", "cam", "--from", "0", "--to", "0.5",
                "--segment", "screen", "--from", "0", "--to", "0.5",
            ],
        )
        assert concat.exit_code == 0, concat.stdout

        export = runner.invoke(cli, ["export", "--dry-run"])
        assert export.exit_code == 0, export.stdout
        data = json.loads(export.stdout)
        assert "audio_policy_note" in data
        assert "--audio-from" in data["audio_policy_note"]

    def test_all_audible_concat_has_no_policy_note(
        self, tmp_path, monkeypatch, test_video
    ):
        import json

        from click.testing import CliRunner

        from moviestar.cli import cli

        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        load = runner.invoke(
            cli,
            [
                "load", test_video, test_video,
                "--as", "cam_a", "--as", "cam_b",
                "--interval", "1.0", "--no-transcribe",
            ],
        )
        assert load.exit_code == 0, load.stdout
        concat = runner.invoke(
            cli,
            [
                "concat",
                "--segment", "cam_a", "--from", "0", "--to", "0.5",
                "--segment", "cam_b", "--from", "0", "--to", "0.5",
            ],
        )
        assert concat.exit_code == 0, concat.stdout

        export = runner.invoke(cli, ["export", "--dry-run"])
        assert export.exit_code == 0, export.stdout
        data = json.loads(export.stdout)
        assert "audio_policy_note" not in data
