"""Parity matrix for the project compiler (``moviestar.resolved``).

Stage 1 of the resolved-project-compiler proposal: every project shape
(source-only, flat concat, global layout, scene composition) resolves
through ``resolve_project()`` into one canonical ordered span list, and
that model must agree with what the existing per-shape paths report.

These tests are the guardrail the proposal requires to land *with* the
compiler, not after it: each asserts agreement between the compiler and
the plan builders that status / watch / export / screenshot / inspect
already consume, so later stages that move surfaces onto the compiler
are checked from day one.
"""

import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.resolved import (
    ResolvedAudio,
    ResolvedSpan,
    ResolvedProject,
    ResolvedProjectError,
    resolve_overlays,
    resolve_project,
)
from moviestar.spec import parse_timecode_string


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner, *args) -> dict:
    result = runner.invoke(cli, list(args))
    assert result.exit_code == 0, f"{args}: {result.stdout}"
    return json.loads(result.stdout)


def _read_state(tmp_path) -> tuple[dict, dict]:
    """Read (project, spec) from the workspace the CLI wrote.

    A freshly loaded project has no spec.json yet; mirror the CLI's
    init path with an empty spec over the loaded sources.
    """
    from moviestar.spec import empty_spec

    workspace = tmp_path / "moviestar"
    project = json.loads((workspace / "project.json").read_text())
    spec_path = workspace / "spec.json"
    if spec_path.exists():
        spec = json.loads(spec_path.read_text())
    else:
        spec = empty_spec(
            [{"id": s["id"], "path": s["path"]} for s in project["sources"]]
        )
    return project, spec


def _tc(value) -> float:
    """Seconds from an envelope timecode (dict with 'seconds') or string."""
    if isinstance(value, dict):
        return float(value["seconds"])
    return parse_timecode_string(value, "test")


def _overlay_timing_project() -> ResolvedProject:
    def span(index, name, scene_id, start, end):
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
            slots=(),
            audio=ResolvedAudio(source_id=None, routing=None),
        )

    return ResolvedProject(
        shape="scenes",
        duration_s=5.0,
        spans=(
            span(0, "intro", "scene_0001", 0.0, 2.0),
            span(1, "chapter", "scene_0002", 2.0, 5.0),
        ),
        overlays_burnable=True,
    )


class TestCompositionOutputFps:
    """Issue #366: one compiler cadence drives every scene clip."""

    @staticmethod
    def _project_and_spec():
        from moviestar.spec import empty_spec

        project = {
            "sources": [
                {
                    "id": "cam30",
                    "path": "/cam30.mp4",
                    "duration": {"seconds": 2.0},
                    "fps": 30.0,
                    "width": 320,
                    "height": 240,
                },
                {
                    "id": "screen60",
                    "path": "/screen60.mp4",
                    "duration": {"seconds": 2.0},
                    "fps": 60.0,
                    "width": 320,
                    "height": 240,
                },
                {
                    "id": "unused120",
                    "path": "/unused120.mp4",
                    "duration": {"seconds": 2.0},
                    "fps": 120.0,
                    "width": 320,
                    "height": 240,
                },
            ],
        }
        spec = empty_spec(
            [{"id": source["id"], "path": source["path"]}
             for source in project["sources"]]
        )
        spec["composition"] = [
            {
                "name": "camera",
                "layout": {"preset": "single"},
                "slots": [{
                    "slot": "main",
                    "source": "cam30",
                    "source_from": "0:00:00.000",
                    "source_to": "0:00:01.000",
                }],
            },
            {
                "name": "screen",
                "layout": {"preset": "single"},
                "slots": [{
                    "slot": "main",
                    "source": "screen60",
                    "source_from": "0:00:00.000",
                    "source_to": "0:00:01.000",
                }],
            },
        ]
        spec["composition_canvas"] = {
            "preset": "custom",
            "width": 320,
            "height": 240,
            "aspect_ratio": "4:3",
        }
        return project, spec

    def test_compiler_uses_highest_active_rate_for_every_span(self):
        project, spec = self._project_and_spec()

        resolved = resolve_project(project, spec)

        assert resolved.output_fps == pytest.approx(60.0)
        assert [span.output_fps for span in resolved.spans] == [60.0, 60.0]

    def test_partial_render_plan_keeps_whole_composition_rate(self):
        from moviestar.cli import _scene_render_plans_for_range

        project, spec = self._project_and_spec()
        _plan, render_plans = _scene_render_plans_for_range(
            project=project,
            spec=spec,
            composition=spec["composition"],
            start_s=0.0,
            end_s=1.0,
            command="watch",
        )

        assert len(render_plans) == 1
        assert render_plans[0]["output_fps"] == pytest.approx(60.0)

    def test_single_rate_composition_keeps_its_source_rate(self):
        project, spec = self._project_and_spec()
        spec["composition"] = spec["composition"][:1]

        resolved = resolve_project(project, spec)

        assert resolved.output_fps == pytest.approx(30.0)
        assert [span.output_fps for span in resolved.spans] == [30.0]


def _timed_overlay(overlay_id: str, timing: dict) -> dict:
    return {
        "id": overlay_id,
        "track": "titles",
        "kind": "manual",
        "timing": timing,
        "text": overlay_id,
        "position": {},
        "style": {},
    }


class TestResolvedOverlayTiming:
    def test_result_space_stays_on_finished_video_clock(self):
        overlay = _timed_overlay(
            "result",
            {
                "space": "result",
                "from": "0:00:01.000",
                "to": "0:00:04.000",
            },
        )

        resolved = resolve_overlays(_overlay_timing_project(), [overlay])

        [record] = resolved.records
        assert _tc(record["from"]) == pytest.approx(1.0)
        assert _tc(record["to"]) == pytest.approx(4.0)
        assert record["resolved_timing"]["space"] == "result"
        assert resolved.detached == ()
        assert resolved.clipped == ()

    def test_scene_space_resolves_to_current_scene_position(self):
        overlay = _timed_overlay(
            "chapter-title",
            {
                "space": "scene",
                "scene": "scene_0002",
                "from": "0:00:00.200",
                "to": "0:00:00.800",
            },
        )

        resolved = resolve_overlays(_overlay_timing_project(), [overlay])

        [record] = resolved.records
        assert _tc(record["from"]) == pytest.approx(2.2)
        assert _tc(record["to"]) == pytest.approx(2.8)
        assert record["resolved_timing"]["scene_name"] == "chapter"

    def test_scene_space_clips_to_current_scene_duration(self):
        overlay = _timed_overlay(
            "chapter-title",
            {
                "space": "scene",
                "scene": "scene_0002",
                "from": "0:00:02.500",
                "to": "0:00:03.500",
            },
        )

        resolved = resolve_overlays(_overlay_timing_project(), [overlay])

        [record] = resolved.records
        assert _tc(record["from"]) == pytest.approx(4.5)
        assert _tc(record["to"]) == pytest.approx(5.0)
        [clipped] = resolved.clipped
        assert clipped["id"] == "chapter-title"
        assert clipped["renders"] is True
        assert clipped["visible_to_s"] == pytest.approx(3.0)

    def test_missing_scene_is_detached_and_does_not_render(self):
        overlay = _timed_overlay(
            "orphan",
            {
                "space": "scene",
                "scene": "scene_9999",
                "from": "0:00:00.000",
                "to": "0:00:01.000",
            },
        )

        resolved = resolve_overlays(_overlay_timing_project(), [overlay])

        assert resolved.records == ()
        assert resolved.clipped == ()
        assert resolved.detached == (
            {"id": "orphan", "text": "orphan", "scene": "scene_9999"},
        )


# -------------------- shape builders --------------------


def _concat_two(runner):
    return _invoke(
        runner,
        "concat",
        "--segment", "holden", "--from", "0", "--to", "1",
        "--segment", "jdilla", "--from", "0.5", "--to", "1.5",
    )


def _layout_two_up(runner):
    return _invoke(
        runner,
        "concat",
        "--layout", "two-up",
        "--canvas", "short",
        "--slot", "top=holden", "--from", "0", "--to", "1",
        "--slot", "bottom=jdilla", "--from", "0", "--to", "1",
        "--audio-from", "holden",
    )


def _scene_composition(runner):
    return _invoke(
        runner,
        "scenes", "set",
        "--canvas", "short",
        "--scene", "intro=single",
        "--slot", "intro:main=holden", "--from", "0", "--to", "1",
        "--audio-from", "intro=holden",
        "--scene", "conversation=two-up",
        "--slot", "conversation:top=holden", "--from", "1", "--to", "2",
        "--slot", "conversation:bottom=jdilla", "--from", "0", "--to", "1",
        "--audio-from", "conversation=jdilla",
    )


def _paced_motion(runner, tmp_path):
    motion_file = tmp_path / "motion.json"
    motion_file.write_text(json.dumps({
        "version": 1,
        "scenes": [
            {
                "scene": "intro",
                "slots": [
                    {
                        "slot": "main",
                        "pacing": [
                            {
                                "id": "rush",
                                "mode": "speed",
                                "range": {
                                    "from": "0.2",
                                    "to": "0.8",
                                    "space": "source-local",
                                },
                                "speed": 2.0,
                            },
                            {
                                "id": "beat",
                                "mode": "hold",
                                "at": "0.9",
                                "space": "source-local",
                                "duration": "0.4",
                            },
                        ],
                    },
                ],
            },
        ],
    }))
    return _invoke(runner, "scenes", "motion", "set", str(motion_file))


# -------------------- source-only --------------------


class TestSourceOnly:
    def test_untouched_source_is_one_full_span(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(test_video, names=["holden"])
        project, spec = _read_state(tmp_path)
        from moviestar.spec import resolve_source

        duration = float(project["sources"][0]["duration"]["seconds"])
        timeline = resolve_source(spec, "holden", duration)

        resolved = resolve_project(project, spec)
        assert resolved.shape == "source"
        assert resolved.canvas is None
        assert resolved.duration_s == pytest.approx(
            timeline.effective_duration, abs=0.002
        )
        assert len(resolved.spans) == 1
        span = resolved.spans[0]
        assert span.start_s == pytest.approx(0.0)
        assert span.end_s == pytest.approx(resolved.duration_s)
        assert len(span.slots) == 1
        assert span.slots[0].source_id == "holden"
        assert span.slots[0].source_from_s == pytest.approx(
            timeline.source_segments[0][0]
        )
        assert span.slots[0].source_to_s == pytest.approx(
            timeline.source_segments[0][1]
        )
        assert span.audio.source_id == "holden"
        assert span.audio.slices == tuple(timeline.source_segments)

    def test_cut_source_spans_follow_surviving_segments(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project(test_video, names=["holden"])
        _invoke(runner, "cut", "--from", "0.5", "--to", "1.0")
        project, spec = _read_state(tmp_path)
        from moviestar.spec import resolve_source

        duration = float(project["sources"][0]["duration"]["seconds"])
        timeline = resolve_source(spec, "holden", duration)
        assert len(timeline.source_segments) == 2  # cut split the timeline

        resolved = resolve_project(project, spec)
        assert len(resolved.spans) == len(timeline.source_segments)
        cursor = 0.0
        for span, (seg_from, seg_to) in zip(
            resolved.spans, timeline.source_segments
        ):
            assert span.start_s == pytest.approx(cursor, abs=0.002)
            assert span.slots[0].source_from_s == pytest.approx(seg_from)
            assert span.slots[0].source_to_s == pytest.approx(seg_to)
            cursor += seg_to - seg_from
        assert resolved.duration_s == pytest.approx(
            timeline.effective_duration, abs=0.002
        )

    def test_multi_source_without_composition_is_an_error(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        project, spec = _read_state(tmp_path)
        with pytest.raises(ResolvedProjectError):
            resolve_project(project, spec)


# -------------------- flat concat --------------------


class TestFlatConcat:
    def test_spans_match_composition_segments(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        _concat_two(runner)
        project, spec = _read_state(tmp_path)
        from moviestar.cli import _format_composition_segments

        segments, total = _format_composition_segments(spec["composition"])

        resolved = resolve_project(project, spec)
        assert resolved.shape == "concat"
        assert resolved.duration_s == pytest.approx(total)
        assert len(resolved.spans) == len(segments)
        for span, seg in zip(resolved.spans, segments):
            assert span.start_s == pytest.approx(
                _tc(seg["result_range"]["from"])
            )
            assert span.end_s == pytest.approx(_tc(seg["result_range"]["to"]))
            assert span.slots[0].source_id == seg["source"]
            assert span.slots[0].source_from_s == pytest.approx(
                _tc(seg["source_range"]["from"])
            )
            assert span.slots[0].source_to_s == pytest.approx(
                _tc(seg["source_range"]["to"])
            )

    def test_status_duration_parity(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        _concat_two(runner)
        project, spec = _read_state(tmp_path)
        status = _invoke(runner, "status")

        resolved = resolve_project(project, spec)
        assert _tc(status["composition_duration"]) == pytest.approx(
            resolved.duration_s, abs=0.002
        )

    def test_flat_concat_is_not_overlay_burnable(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        _concat_two(runner)
        project, spec = _read_state(tmp_path)
        resolved = resolve_project(project, spec)
        assert resolved.overlays_burnable is False

    def test_each_span_carries_its_own_segment_audio(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        _concat_two(runner)
        project, spec = _read_state(tmp_path)
        resolved = resolve_project(project, spec)
        for span in resolved.spans:
            slot = span.slots[0]
            assert span.audio.source_id == slot.source_id
            assert span.audio.routing == "segment"
            assert span.audio.slices == (
                (slot.source_from_s, slot.source_to_s),
            )


# -------------------- global layout --------------------


class TestGlobalLayout:
    def test_span_matches_layout_render_plan(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        _layout_two_up(runner)
        project, spec = _read_state(tmp_path)
        from moviestar.spec import normalize_composition_storage

        # B3 retired the legacy layout lane; the invariant is now that
        # the compiler's raw-layout adapter agrees with the normalized
        # scene form — the shape every real spec takes at load.
        dims = {
            s["id"]: (int(s["width"]), int(s["height"]))
            for s in project["sources"]
        }
        normalized = normalize_composition_storage(spec, dims)
        via_scene = resolve_project(project, normalized)

        resolved = resolve_project(project, spec)
        assert resolved.shape == "layout"
        assert via_scene.shape == "scenes"
        assert resolved.overlays_burnable is True
        assert resolved.duration_s == pytest.approx(via_scene.duration_s)
        assert resolved.canvas == spec["composition_canvas"]
        assert len(resolved.spans) == len(via_scene.spans) == 1
        span, scene_span = resolved.spans[0], via_scene.spans[0]
        assert span.layout == scene_span.layout == "two-up"
        assert {slot.name for slot in span.slots} == {
            slot.name for slot in scene_span.slots
        }
        scene_regions = {
            slot.name: slot.region for slot in scene_span.slots
        }
        for slot in span.slots:
            assert slot.region == scene_regions[slot.name]

        # Audio parity: same routed source, same anchor, same slice list.
        assert span.audio.source_id == scene_span.audio.source_id
        assert span.audio.routing == "composition"
        assert span.audio.slices == scene_span.audio.slices
        assert span.audio.anchor_s == pytest.approx(
            scene_span.audio.anchor_s
        )

    def test_status_duration_parity(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        _layout_two_up(runner)
        project, spec = _read_state(tmp_path)
        status = _invoke(runner, "status")
        resolved = resolve_project(project, spec)
        assert _tc(status["composition_duration"]) == pytest.approx(
            resolved.duration_s, abs=0.002
        )


# -------------------- scene composition --------------------


class TestSceneComposition:
    def _build(self, runner, tmp_path, *, paced: bool):
        _scene_composition(runner)
        if paced:
            _paced_motion(runner, tmp_path)
        return _read_state(tmp_path)

    def _render_plans(self, project, spec):
        from moviestar.cli import (
            _scene_composition_plan,
            _scene_render_plans_for_range,
        )

        plan = _scene_composition_plan(
            spec=spec, composition=spec["composition"], command="test"
        )
        _, render_plans = _scene_render_plans_for_range(
            project=project,
            spec=spec,
            composition=spec["composition"],
            start_s=0.0,
            end_s=plan["composition_duration"],
            command="test",
        )
        return plan, render_plans

    @pytest.mark.parametrize("paced", [False, True], ids=["unpaced", "paced"])
    def test_spans_match_render_plans_one_to_one(
        self, runner, loaded_project, test_video, tmp_path, paced
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        project, spec = self._build(runner, tmp_path, paced=paced)
        plan, render_plans = self._render_plans(project, spec)

        resolved = resolve_project(project, spec)
        assert resolved.shape == "scenes"
        assert resolved.overlays_burnable is True
        assert resolved.duration_s == pytest.approx(
            plan["composition_duration"]
        )
        assert len(resolved.spans) == len(render_plans)
        for span, render_plan in zip(resolved.spans, render_plans):
            window = render_plan["scene_window"]
            assert span.scene_name == window["scene"]
            assert span.scene_index == window["index"]
            assert span.pacing_segment_id == window["pacing_segment_id"]
            assert span.start_s == pytest.approx(
                _tc(window["result_range"]["from"]), abs=0.002
            )
            assert span.end_s == pytest.approx(
                _tc(window["result_range"]["to"]), abs=0.002
            )
            assert span.scene_start_s == pytest.approx(
                _tc(window["scene_result_range"]["from"]), abs=0.002
            )
            assert span.held == window["held"]
            assert span.layout == window["layout"]["preset"]

            plan_slots = {slot["slot"]: slot for slot in window["slots"]}
            assert {slot.name for slot in span.slots} == set(plan_slots)
            for slot in span.slots:
                expected = plan_slots[slot.name]
                assert slot.source_id == expected["source"]
                assert slot.source_from_s == pytest.approx(
                    _tc(expected["source_range"]["from"]), abs=0.002
                )
                assert slot.source_to_s == pytest.approx(
                    _tc(expected["source_range"]["to"]), abs=0.002
                )
                if expected["effective_speed"] is None:
                    assert slot.speed is None
                else:
                    assert slot.speed == pytest.approx(
                        expected["effective_speed"], abs=0.005
                    )
                assert slot.held == expected["held"]
                assert slot.region == expected["region"]

    @pytest.mark.parametrize("paced", [False, True], ids=["unpaced", "paced"])
    def test_audio_slices_match_render_plans(
        self, runner, loaded_project, test_video, tmp_path, paced
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        project, spec = self._build(runner, tmp_path, paced=paced)
        _, render_plans = self._render_plans(project, spec)

        resolved = resolve_project(project, spec)
        for span, render_plan in zip(resolved.spans, render_plans):
            assert span.audio.source_id == render_plan["audio_from"]
            if render_plan["audio_from_input"] is None:
                assert span.audio.slices == ()
            else:
                _path, plan_slices = render_plan["audio_from_input"]
                assert len(span.audio.slices) == len(plan_slices)
                for got, expected in zip(span.audio.slices, plan_slices):
                    assert got[0] == pytest.approx(expected[0], abs=0.002)
                    assert got[1] == pytest.approx(expected[1], abs=0.002)
            assert span.audio.speed == pytest.approx(
                render_plan["audio_speed"], abs=0.005
            )

    def test_paced_duration_matches_status_resolved_duration(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        project, spec = self._build(runner, tmp_path, paced=True)
        status = _invoke(runner, "status")

        resolved = resolve_project(project, spec)
        assert _tc(status["motion"]["resolved_duration"]) == pytest.approx(
            resolved.duration_s, abs=0.002
        )
        assert _tc(status["composition_duration"]) == pytest.approx(
            resolved.duration_s, abs=0.002
        )

    def test_hold_span_carries_silence_note_and_no_slices(
        self, runner, loaded_project, test_video, tmp_path
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        project, spec = self._build(runner, tmp_path, paced=True)
        resolved = resolve_project(project, spec)
        held = [span for span in resolved.spans if span.held]
        assert held, "paced fixture should produce a hold span"
        for span in held:
            assert span.audio.slices == ()
            assert span.audio.note is not None


# -------------------- surfaces consume the compiler --------------------


class TestAudioSurfaceUsesPacedDuration:
    """Audio authoring must validate against the compiler's finished
    duration, not the unpaced scene spans.

    Before stage 2, ``_audio_surface_context`` computed scene-composition
    duration from the unpaced ``resolve_scene_spans`` while every render
    surface used ``resolve_pacing`` — a paced project had two different
    answers to "how long is the finished video?" (proposal: the
    two-durations bug).
    """

    def test_track_placement_validates_against_paced_duration(
        self, runner, loaded_project, test_video, tmp_path, audio_only_file
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        _scene_composition(runner)
        _paced_motion(runner, tmp_path)
        project, spec = _read_state(tmp_path)

        resolved = resolve_project(project, spec)
        from moviestar.motion import resolve_pacing

        # The unpaced duration is the sum of authored first-slot ranges —
        # what the retired scenemap resolver used to report.
        unpaced = sum(
            _tc(scene["slots"][0]["source_to"])
            - _tc(scene["slots"][0]["source_from"])
            for scene in spec["composition"]
        )
        paced = resolve_pacing(
            spec["composition"], spec.get("motion")
        ).duration_s
        assert paced > unpaced, (
            "fixture must make the paced timeline longer than the unpaced "
            "one (the hold adds result time) so the two durations disagree"
        )
        assert resolved.duration_s == pytest.approx(paced)

        # A placement valid on the paced clock but past the unpaced clock
        # must be accepted: the finished video really is `paced` long.
        at = round((unpaced + paced) / 2, 3)
        data = _invoke(
            runner,
            "audio", "add", audio_only_file,
            "--as", "sting", "--kind", "other",
            "--at", str(at),
        )
        assert data["writes_spec"] is True

    def test_track_past_paced_end_is_rejected_with_paced_duration(
        self, runner, loaded_project, test_video, tmp_path, audio_only_file
    ):
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
        _scene_composition(runner)
        _paced_motion(runner, tmp_path)
        project, spec = _read_state(tmp_path)
        resolved = resolve_project(project, spec)

        result = runner.invoke(
            cli,
            [
                "audio", "add", audio_only_file,
                "--as", "sting", "--kind", "other",
                "--at", str(round(resolved.duration_s + 1.0, 3)),
            ],
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        # The rejection names the *paced* result end, not the unpaced one.
        assert f"{resolved.duration_s:.3f}" in json.dumps(data["errors"])


class TestCaptionWindowsAreSliceAware:
    """Caption audio windows must come from the compiler's cut-aware
    slice lists, not a collapsed outer source range.

    Before this fix the scene branch of ``_caption_audio_windows`` used
    the render plan's ``audio_from_range`` — the outer span of the audio
    slices — so a scene whose routed audio had an internal cut would
    caption words from inside the cut hole and misplace every word
    after it (proposal: the cli.py:7045 slice-collapse bug).
    """

    def _project_and_spec(self):
        from moviestar.spec import empty_spec, validate_spec

        project = {
            "sources": [
                {
                    "id": "holden",
                    "path": "/h.mp4",
                    "duration": {"seconds": 2.0},
                    "audio_codec": "aac",
                    "fps": 30.0,
                    "width": 320,
                    "height": 240,
                },
            ],
        }
        spec = empty_spec([{"id": "holden", "path": "/h.mp4"}])
        # Cut 0.5s-1.0s out of holden's audio timeline: surviving
        # source segments are (0.0, 0.5) and (1.0, 2.0).
        spec["sources"][0]["operations"] = [
            {"type": "cut", "from": "0:00:00.500", "to": "0:00:01.000"},
        ]
        spec["composition"] = [
            {
                "name": "intro",
                "layout": {"preset": "single"},
                "slots": [
                    {
                        "slot": "main",
                        "source": "holden",
                        "source_from": "0:00:00.000",
                        "source_to": "0:00:02.000",
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
        return project, spec

    def test_windows_skip_cut_out_audio(self):
        from moviestar.cli import _caption_audio_windows

        project, spec = self._project_and_spec()
        windows, _notes, rule, warnings = _caption_audio_windows(
            project=project,
            spec=spec,
            command="captions generate",
            source_filter=None,
        )
        assert warnings == []
        assert rule == "scene audio source"
        # One window per surviving audio slice — the cut hole is not heard.
        assert [
            (w["source_from"], w["source_to"]) for w in windows
        ] == [(0.0, 0.5), (1.0, 2.0)]
        # Result time is compressed across the hole: the second slice
        # starts right where the first one ended.
        assert windows[0]["result_from"] == pytest.approx(0.0)
        assert windows[1]["result_from"] == pytest.approx(0.5)

    def test_windows_match_compiler_spans(self):
        from moviestar.cli import _caption_audio_windows

        project, spec = self._project_and_spec()
        resolved = resolve_project(project, spec)
        windows, _notes, _rule, _warnings = _caption_audio_windows(
            project=project,
            spec=spec,
            command="captions generate",
            source_filter=None,
        )
        span_slices = [
            tuple(sl) for span in resolved.spans for sl in span.audio.slices
        ]
        assert [
            (w["source_from"], w["source_to"]) for w in windows
        ] == span_slices


class TestLayoutCaptionWindowsAreSliceAware:
    """The layout caption branch must honor cuts on the routed audio
    source, exactly as the scene branch does.

    The layout *renderer* slices ``composition_audio_from`` through the
    source's edit stack (``slice_audio_from_anchor``), but the caption
    branch used the raw slot range — so words inside a cut hole were
    captioned and everything after sat on the wrong clock.
    """

    def _project_and_spec(self):
        from moviestar.spec import empty_spec, validate_spec

        project = {
            "sources": [
                {
                    "id": "holden",
                    "path": "/h.mp4",
                    "duration": {"seconds": 2.0},
                    "audio_codec": "aac",
                    "fps": 30.0,
                    "width": 320,
                    "height": 240,
                },
            ],
        }
        spec = empty_spec([{"id": "holden", "path": "/h.mp4"}])
        spec["sources"][0]["operations"] = [
            {"type": "cut", "from": "0:00:00.500", "to": "0:00:01.000"},
        ]
        # A single unnamed scene with no audio_from is the global-layout
        # shape; audio routes through composition_audio_from.
        spec["composition"] = [
            {
                "layout": {"preset": "single"},
                "slots": [
                    {
                        "slot": "main",
                        "source": "holden",
                        "source_from": "0:00:00.000",
                        "source_to": "0:00:02.000",
                    },
                ],
            },
        ]
        spec["composition_audio_from"] = "holden"
        spec["composition_canvas"] = {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }
        validate_spec(spec)
        return project, spec

    def test_windows_skip_cut_out_audio(self):
        from moviestar.cli import _caption_audio_windows
        from moviestar.spec import normalize_composition_storage

        project, spec = self._project_and_spec()
        # B3: raw layout shapes normalize at load; caption windows come
        # from the scene branch's composition-wide route.
        spec = normalize_composition_storage(spec, {"holden": (320, 240)})
        windows, _notes, rule, warnings = _caption_audio_windows(
            project=project,
            spec=spec,
            command="captions generate",
            source_filter=None,
        )
        assert warnings == []
        assert rule == "scene audio source"
        assert [
            (w["source_from"], w["source_to"]) for w in windows
        ] == [(0.0, 0.5), (1.0, 2.0)]
        assert windows[0]["result_from"] == pytest.approx(0.0)
        assert windows[1]["result_from"] == pytest.approx(0.5)

    def test_windows_match_renderer_audio_slices(self):
        from moviestar.cli import (
            _caption_audio_windows,
            _scene_render_plans_for_range,
        )
        from moviestar.spec import normalize_composition_storage

        project, spec = self._project_and_spec()
        spec = normalize_composition_storage(spec, {"holden": (320, 240)})
        resolved = resolve_project(project, spec)
        _plan, render_plans = _scene_render_plans_for_range(
            project=project,
            spec=spec,
            composition=spec["composition"],
            start_s=0.0,
            end_s=resolved.duration_s,
            command="test",
        )
        render_slices = [
            tuple(sl)
            for rp in render_plans
            if rp["audio_from_input"] is not None
            for sl in rp["audio_from_input"][1]
        ]

        windows, _notes, _rule, _warnings = _caption_audio_windows(
            project=project,
            spec=spec,
            command="captions generate",
            source_filter=None,
        )
        assert [
            (w["source_from"], w["source_to"]) for w in windows
        ] == render_slices


class TestSceneGlobalAudioRoute:
    """Stage 3 prerequisite: scene compositions support a
    composition-wide audio route (``composition_audio_from``).

    Flat concats route one source's audio across every segment —
    anchored at the source's first segment, playing linearly from
    there. Migrating concats to scene compositions requires the scene
    pipeline to express the same thing: audio anchored at the routed
    source's first slot appearance, linear at 1x in result time,
    sliced through the source's edit stack, overriding per-scene
    audio_from, and continuing through pacing holds.
    """

    def _project_and_spec(self, *, cut=False, audio_from="holden"):
        from moviestar.spec import empty_spec, validate_spec

        project = {
            "sources": [
                {
                    "id": "holden",
                    "path": "/h.mp4",
                    "duration": {"seconds": 2.0},
                    "audio_codec": "aac",
                    "fps": 30.0,
                    "width": 320,
                    "height": 240,
                },
                {
                    "id": "jdilla",
                    "path": "/j.mp4",
                    "duration": {"seconds": 2.0},
                    "audio_codec": "aac",
                    "fps": 30.0,
                    "width": 320,
                    "height": 240,
                },
            ],
        }
        spec = empty_spec(
            [
                {"id": "holden", "path": "/h.mp4"},
                {"id": "jdilla", "path": "/j.mp4"},
            ]
        )
        if cut:
            spec["sources"][0]["operations"] = [
                {"type": "cut", "from": "0:00:00.500", "to": "0:00:01.000"},
            ]

        def scene(name, source, src_from, src_to):
            return {
                "name": name,
                "layout": {"preset": "single"},
                "slots": [
                    {
                        "slot": "main",
                        "source": source,
                        "source_from": src_from,
                        "source_to": src_to,
                    },
                ],
            }

        spec["composition"] = [
            scene("seg_1", "holden", "0:00:00.000", "0:00:01.000"),
            scene("seg_2", "jdilla", "0:00:00.000", "0:00:01.000"),
        ]
        spec["composition_audio_from"] = audio_from
        spec["composition_canvas"] = {
            "preset": "short",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
        }
        validate_spec(spec)
        return project, spec

    def test_compiler_routes_one_source_across_every_span(self):
        project, spec = self._project_and_spec()
        resolved = resolve_project(project, spec)
        assert resolved.shape == "scenes"
        assert len(resolved.spans) == 2
        for span in resolved.spans:
            assert span.audio.source_id == "holden"
            assert span.audio.routing == "composition"
            assert span.audio.speed == pytest.approx(1.0)
        # Anchored at holden's first slot appearance (source-time 0),
        # playing linearly: span 2 continues where span 1 left off.
        assert resolved.spans[0].audio.slices == ((0.0, 1.0),)
        assert resolved.spans[1].audio.slices == ((1.0, 2.0),)

    def test_compiler_slices_skip_cut_holes_in_the_routed_source(self):
        project, spec = self._project_and_spec(cut=True)
        resolved = resolve_project(project, spec)
        # holden's surviving audio: (0,0.5)+(1,2). Result time 0-1 plays
        # (0,0.5)+(1,1.5); result time 1-2 plays (1.5,2) then runs out.
        assert resolved.spans[0].audio.slices == ((0.0, 0.5), (1.0, 1.5))
        assert resolved.spans[1].audio.slices == ((1.5, 2.0),)

    def test_render_plans_carry_the_same_global_slices(self):
        from moviestar.cli import _scene_render_plans_for_range

        project, spec = self._project_and_spec(cut=True)
        resolved = resolve_project(project, spec)
        _plan, render_plans = _scene_render_plans_for_range(
            project=project,
            spec=spec,
            composition=spec["composition"],
            start_s=0.0,
            end_s=resolved.duration_s,
            command="test",
        )
        assert len(render_plans) == len(resolved.spans)
        for render_plan, span in zip(render_plans, resolved.spans):
            assert render_plan["audio_from"] == "holden"
            assert render_plan["audio_from_input"] is not None
            _path, slices = render_plan["audio_from_input"]
            assert tuple(tuple(s) for s in slices) == span.audio.slices
            assert render_plan["audio_speed"] == pytest.approx(1.0)

    def test_partial_range_render_plans_stay_on_the_global_clock(self):
        from moviestar.cli import _scene_render_plans_for_range

        project, spec = self._project_and_spec()
        # Watch a window inside the second scene: its audio must pick up
        # mid-stream (holden source-time 1.2), not restart at the anchor.
        _plan, render_plans = _scene_render_plans_for_range(
            project=project,
            spec=spec,
            composition=spec["composition"],
            start_s=1.2,
            end_s=1.8,
            command="test",
        )
        assert len(render_plans) == 1
        _path, slices = render_plans[0]["audio_from_input"]
        assert tuple(tuple(s) for s in slices) == ((1.2, 1.8),)

    def test_caption_windows_follow_the_global_route(self):
        from moviestar.cli import _caption_audio_windows

        project, spec = self._project_and_spec(cut=True)
        windows, _notes, _rule, warnings = _caption_audio_windows(
            project=project,
            spec=spec,
            command="captions generate",
            source_filter=None,
        )
        assert warnings == []
        assert [w["source"] for w in windows] == ["holden"] * len(windows)
        assert [
            (w["source_from"], w["source_to"]) for w in windows
        ] == [(0.0, 0.5), (1.0, 1.5), (1.5, 2.0)]
        assert [w["result_from"] for w in windows] == [
            pytest.approx(0.0),
            pytest.approx(0.5),
            pytest.approx(1.0),
        ]

    def test_unknown_slot_source_route_is_an_error(self):
        project, spec = self._project_and_spec(audio_from="jdilla")
        # jdilla IS a slot source (scene 2) — valid. Route from a source
        # that never appears in any slot must fail loudly.
        resolved = resolve_project(project, spec)
        assert resolved.spans[0].audio.source_id == "jdilla"

        from moviestar.spec import validate_spec

        spec["composition_audio_from"] = "holden"
        spec["composition"][0]["slots"][0]["source"] = "jdilla"
        validate_spec(spec)
        with pytest.raises(ResolvedProjectError):
            resolve_project(project, spec)

    def test_anchor_is_first_slot_appearance_of_the_routed_source(self):
        project, spec = self._project_and_spec(audio_from="jdilla")
        resolved = resolve_project(project, spec)
        # jdilla first appears in scene 2 with source_from 0. Audio is
        # anchored there and plays from result 0 — matching concat's
        # "anchor at the named source's first segment" contract.
        assert resolved.spans[0].audio.slices == ((0.0, 1.0),)
        assert resolved.spans[1].audio.slices == ((1.0, 2.0),)


# -------------------- cross-shape invariants --------------------


def _build_shape(shape, runner, loaded_project, test_video, tmp_path):
    if shape == "source":
        loaded_project(test_video, names=["holden"])
    else:
        loaded_project([test_video, test_video], names=["holden", "jdilla"])
    if shape == "concat":
        _concat_two(runner)
    elif shape == "layout":
        _layout_two_up(runner)
    elif shape in ("scenes", "paced"):
        _scene_composition(runner)
        if shape == "paced":
            _paced_motion(runner, tmp_path)
    return _read_state(tmp_path)


ALL_SHAPES = ["source", "concat", "layout", "scenes", "paced"]


class TestInvariants:
    @pytest.mark.parametrize("shape", ALL_SHAPES)
    def test_spans_are_contiguous_and_cover_the_duration(
        self, runner, loaded_project, test_video, tmp_path, shape
    ):
        project, spec = _build_shape(
            shape, runner, loaded_project, test_video, tmp_path
        )
        resolved = resolve_project(project, spec)
        assert isinstance(resolved, ResolvedProject)
        assert resolved.spans, "every shape resolves to at least one span"
        assert resolved.spans[0].start_s == pytest.approx(0.0)
        for prev, cur in zip(resolved.spans, resolved.spans[1:]):
            assert cur.start_s == pytest.approx(prev.end_s, abs=0.002)
            assert cur.start_s >= prev.start_s
        assert resolved.spans[-1].end_s == pytest.approx(
            resolved.duration_s, abs=0.002
        )
        for index, span in enumerate(resolved.spans):
            assert span.index == index
            assert span.end_s > span.start_s or span.held

    @pytest.mark.parametrize("shape", ALL_SHAPES)
    def test_span_lookup_agrees_with_span_ranges(
        self, runner, loaded_project, test_video, tmp_path, shape
    ):
        project, spec = _build_shape(
            shape, runner, loaded_project, test_video, tmp_path
        )
        resolved = resolve_project(project, spec)
        for span in resolved.spans:
            midpoint = (span.start_s + span.end_s) / 2
            assert resolved.span_at(midpoint) is span
        # The exact end of the timeline resolves to the last span.
        assert resolved.span_at(resolved.duration_s) is resolved.spans[-1]

        overlapping = resolved.spans_overlapping(0.0, resolved.duration_s)
        assert overlapping == list(resolved.spans)
