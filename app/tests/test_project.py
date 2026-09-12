"""Tests for moviestar.project."""

import json
from pathlib import Path

import pytest

from moviestar.project import (
    FRAMES_DIR,
    MOVIESTAR_DIR,
    PROJECT_FILE,
    SCHEMA_VERSION,
    TRANSCRIPTS_DIR,
    create_workspace,
    filter_frames_by_range,
    find_project_dir,
    get_project_dir,
    is_loaded,
    list_frames,
    load_project,
    load_transcript,
    reset_workspace,
    save_project,
    save_transcript,
    select_load_frame_interval,
    slice_transcript,
    subsample_frames,
)


def _make_workspace(at: "Path", source_id: str = "src_0") -> None:
    """Create a minimal moviestar/project.json under ``at``."""
    ms = at / MOVIESTAR_DIR
    ms.mkdir(parents=True, exist_ok=True)
    (ms / PROJECT_FILE).write_text(
        json.dumps({
            "version": SCHEMA_VERSION,
            "sources": [{"id": source_id}],
        })
    )


class TestGetProjectDir:
    def test_returns_moviestar_path_in_cwd(self, tmp_path):
        p = get_project_dir(tmp_path)
        assert p == tmp_path / MOVIESTAR_DIR

    def test_walks_up_to_find_existing_workspace(self, tmp_path):
        """Issue #41: from a subdir of a project, get_project_dir
        returns the ancestor's moviestar/, not subdir/moviestar/.
        Walk-up is the default for read commands."""
        _make_workspace(tmp_path)
        sub = tmp_path / "Desktop" / "exports"
        sub.mkdir(parents=True)
        assert get_project_dir(sub) == tmp_path / MOVIESTAR_DIR

    def test_walk_false_returns_cwd_only(self, tmp_path):
        """Destructive / create operations need cwd-only resolution
        regardless of any ancestor workspace — load creates here,
        reset wipes here, neither should reach up to the parent."""
        _make_workspace(tmp_path)
        sub = tmp_path / "subdir"
        sub.mkdir()
        assert get_project_dir(sub, walk=False) == sub / MOVIESTAR_DIR

    def test_no_ancestor_falls_back_to_cwd(self, tmp_path):
        """When no workspace exists anywhere up to root, return
        cwd/moviestar (where load would write a fresh project)."""
        sub = tmp_path / "nope"
        sub.mkdir()
        assert get_project_dir(sub) == sub / MOVIESTAR_DIR


class TestFindProjectDir:
    def test_returns_none_when_no_workspace_anywhere(self, tmp_path):
        sub = tmp_path / "deep" / "nested"
        sub.mkdir(parents=True)
        assert find_project_dir(sub) is None

    def test_finds_workspace_in_self(self, tmp_path):
        _make_workspace(tmp_path)
        assert find_project_dir(tmp_path) == tmp_path / MOVIESTAR_DIR

    def test_walks_up_one_level(self, tmp_path):
        _make_workspace(tmp_path)
        sub = tmp_path / "subdir"
        sub.mkdir()
        assert find_project_dir(sub) == tmp_path / MOVIESTAR_DIR

    def test_walks_up_multiple_levels(self, tmp_path):
        _make_workspace(tmp_path)
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        assert find_project_dir(deep) == tmp_path / MOVIESTAR_DIR

    def test_nearest_ancestor_wins_with_nested_workspaces(self, tmp_path):
        """Two nested workspaces — the closer one to cwd wins (the
        same rule git applies to .git ancestor lookup)."""
        _make_workspace(tmp_path, source_id="outer")
        inner = tmp_path / "inner"
        inner.mkdir()
        _make_workspace(inner, source_id="inner")
        sub = inner / "subdir"
        sub.mkdir()
        assert find_project_dir(sub) == inner / MOVIESTAR_DIR

    def test_ignores_workspace_dir_without_project_json(self, tmp_path):
        """A bare moviestar/ directory without project.json is not a
        valid workspace — walk-up should keep going."""
        (tmp_path / MOVIESTAR_DIR).mkdir()  # no project.json
        # Real workspace one level up.
        outer = tmp_path.parent
        # Skip if we don't own the parent (sandbox should always
        # let us — tmp_path comes from pytest tmp_path_factory).
        # Build a sibling structure: tmp_path is empty-ish workspace,
        # tmp_path/sub uses walk-up; should NOT find tmp_path's bare dir.
        sub = tmp_path / "sub"
        sub.mkdir()
        # No real workspace anywhere = None.
        assert find_project_dir(sub) is None


class TestIsLoaded:
    def test_false_when_no_workspace(self, tmp_path):
        assert is_loaded(tmp_path) is False

    def test_true_when_project_json_exists(self, tmp_path):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        (tmp_path / MOVIESTAR_DIR / PROJECT_FILE).write_text("{}")
        assert is_loaded(tmp_path) is True

    def test_false_when_dir_exists_but_no_project_json(self, tmp_path):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        assert is_loaded(tmp_path) is False

    def test_walks_up_by_default(self, tmp_path):
        """Issue #41: is_loaded checks ancestors too, so a read command
        run from inside a project subdir reports the project as loaded."""
        _make_workspace(tmp_path)
        sub = tmp_path / "Desktop"
        sub.mkdir()
        assert is_loaded(sub) is True

    def test_walk_false_cwd_only(self, tmp_path):
        """load uses walk=False so an ancestor workspace doesn't
        block creating a nested workspace in cwd."""
        _make_workspace(tmp_path)
        sub = tmp_path / "subdir"
        sub.mkdir()
        assert is_loaded(sub, walk=False) is False


class TestSaveLoadProject:
    def test_save_writes_project_json(self, tmp_path):
        data = {"version": SCHEMA_VERSION, "sources": []}
        (tmp_path / MOVIESTAR_DIR).mkdir()
        save_project(data, tmp_path)
        saved = json.loads((tmp_path / MOVIESTAR_DIR / PROJECT_FILE).read_text())
        assert saved == data

    def test_load_reads_saved_data(self, tmp_path):
        data = {"version": SCHEMA_VERSION, "sources": [{"id": "src_0"}]}
        (tmp_path / MOVIESTAR_DIR).mkdir()
        save_project(data, tmp_path)
        loaded = load_project(tmp_path)
        assert loaded == data

    def test_roundtrip(self, tmp_path):
        data = {
            "version": SCHEMA_VERSION,
            "loaded_at": "2026-04-20T12:00:00Z",
            "sources": [
                {
                    "id": "src_0",
                    "path": "/abs/path.mp4",
                    "duration": {"text": "0:00:02.000", "seconds": 2.0},
                    "frame_interval": 5.0,
                    "frames_extracted": 12,
                }
            ],
        }
        (tmp_path / MOVIESTAR_DIR).mkdir()
        save_project(data, tmp_path)
        assert load_project(tmp_path) == data


class TestSelectLoadFrameInterval:
    @pytest.mark.parametrize(
        ("duration", "expected"),
        [
            (16.9, 1.0),
            (90.0, 5.0),
            (2.0, 0.2),
            (0.05, 0.025),
            (0.0, 0.001),
        ],
    )
    def test_omitted_interval_targets_a_useful_bounded_overview(
        self, duration, expected
    ):
        assert select_load_frame_interval(duration, None) == expected

    def test_explicit_interval_is_exact(self):
        assert select_load_frame_interval(16.9, 5.0) == 5.0


class TestCreateWorkspace:
    def test_creates_moviestar_dir(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        assert (tmp_path / MOVIESTAR_DIR).is_dir()

    def test_creates_frames_dir(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        assert (tmp_path / MOVIESTAR_DIR / FRAMES_DIR).is_dir()

    def test_writes_project_json(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        assert (tmp_path / MOVIESTAR_DIR / PROJECT_FILE).is_file()

    def test_project_json_has_schema_version(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        data = load_project(tmp_path)
        assert data["version"] == SCHEMA_VERSION

    def test_project_json_has_loaded_at(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        data = load_project(tmp_path)
        assert "loaded_at" in data
        # ISO-8601 UTC with Z suffix
        assert data["loaded_at"].endswith("Z")

    def test_project_json_sources_list_with_one_entry(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        data = load_project(tmp_path)
        assert isinstance(data["sources"], list)
        assert len(data["sources"]) == 1

    def test_source_has_expected_fields(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        data = load_project(tmp_path)
        source = data["sources"][0]
        for key in [
            "id",
            "path",
            "duration",
            "width",
            "height",
            "fps",
            "video_codec",
            "audio_codec",
            "frame_interval",
            "frames_extracted",
            "frames_dir",
        ]:
            assert key in source, f"missing key: {key}"

    def test_source_id_derived_from_filename(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        data = load_project(tmp_path)
        # test_video fixture is "probe_test.mp4" → first token "probe".
        assert data["sources"][0]["id"] == "probe"

    def test_source_id_derived_from_symlink_argument_basename(
        self, test_video, tmp_path
    ):
        """Issue #116: symlinked loads use the name the agent typed
        for source ID derivation, while storing the resolved target path."""
        import os as _os

        link = tmp_path / "screenshare.mp4"
        link.symlink_to(test_video)
        create_workspace(
            [str(link)], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True
        )
        data = load_project(tmp_path)
        source = data["sources"][0]
        assert source["id"] == "screenshare"
        assert source["path"] == _os.path.realpath(test_video)

    def test_source_path_is_absolute(self, test_video, tmp_path):
        import os as _os
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        data = load_project(tmp_path)
        assert _os.path.isabs(data["sources"][0]["path"])

    def test_source_not_copied(self, test_video, tmp_path):
        """Source video should stay put — we store a pointer, not a copy."""
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        ms_dir = tmp_path / MOVIESTAR_DIR
        # no video-like files should be in the workspace
        for f in ms_dir.rglob("*.mp4"):
            pytest.fail(f"Source video leaked into workspace: {f}")
        for f in ms_dir.rglob("*.mov"):
            pytest.fail(f"Source video leaked into workspace: {f}")

    def test_frames_extracted(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        frames = list((tmp_path / MOVIESTAR_DIR / FRAMES_DIR).glob("*.jpg"))
        assert len(frames) >= 1
        data = load_project(tmp_path)
        assert data["sources"][0]["frames_extracted"] == len(frames)

    def test_returns_project_dict(self, test_video, tmp_path):
        result = create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        assert result["version"] == SCHEMA_VERSION
        assert len(result["sources"]) == 1


class TestResetWorkspace:
    def test_removes_moviestar_directory(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True)
        assert (tmp_path / MOVIESTAR_DIR).exists()
        reset_workspace(tmp_path)
        assert not (tmp_path / MOVIESTAR_DIR).exists()

    def test_noop_when_not_loaded(self, tmp_path):
        # Should not raise
        reset_workspace(tmp_path)

    def test_never_walks_up_to_ancestor(self, tmp_path):
        """Issue #41: even though is_loaded/get_project_dir walk up by
        default, reset_workspace must NEVER walk up — destructive
        operations always target cwd. Otherwise running 'load --force'
        from a subdir would wipe the parent project's workspace.
        """
        _make_workspace(tmp_path)
        sub = tmp_path / "subdir"
        sub.mkdir()
        # No workspace in subdir; reset from subdir must NOT touch
        # the parent's workspace.
        reset_workspace(sub)
        assert (tmp_path / MOVIESTAR_DIR / PROJECT_FILE).is_file(), (
            "reset_workspace from subdir wiped parent's workspace"
        )


class TestSaveTranscript:
    def test_writes_transcripts_file(self, tmp_path):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        transcript = {"source_id": "src_0", "text": "hello", "words": []}
        path = save_transcript(transcript, "src_0", cwd=tmp_path)
        assert path.exists()
        assert path.name == "src_0.json"
        assert path.parent.name == TRANSCRIPTS_DIR

    def test_transcripts_directory_created(self, tmp_path):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        save_transcript({"source_id": "src_0"}, "src_0", cwd=tmp_path)
        assert (tmp_path / MOVIESTAR_DIR / TRANSCRIPTS_DIR).is_dir()


class TestCreateWorkspaceWithTranscription:
    def test_transcribes_by_default(self, speech_video, tmp_path):
        create_workspace(
            [speech_video], interval=1.0, cwd=tmp_path, model="tiny", quiet=True,
            names=["src_0"],
        )
        transcript_path = (
            tmp_path / MOVIESTAR_DIR / TRANSCRIPTS_DIR / "src_0.json"
        )
        assert transcript_path.exists()

    def test_adds_transcript_field_to_source(self, speech_video, tmp_path):
        create_workspace(
            [speech_video], interval=1.0, cwd=tmp_path, model="tiny", quiet=True,
            names=["src_0"],
        )
        data = load_project(tmp_path)
        source = data["sources"][0]
        assert "transcript" in source
        assert source["transcript"]["source"] == "whisper:tiny"
        assert source["transcript"]["path"] == f"{TRANSCRIPTS_DIR}/src_0.json"

    def test_no_transcribe_omits_transcript(self, speech_video, tmp_path):
        """Issue #33: stable enum value, not a flag-literal string."""
        create_workspace(
            [speech_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True
        )
        data = load_project(tmp_path)
        source = data["sources"][0]
        assert source.get("transcript") is None
        assert source.get("transcription_skipped_reason") == "flag"

    def test_silent_video_skips_transcription(self, silent_video, tmp_path):
        """Issue #33: stable enum value, not prose."""
        create_workspace(
            [silent_video], interval=0.5, cwd=tmp_path, model="tiny", quiet=True
        )
        data = load_project(tmp_path)
        source = data["sources"][0]
        assert source.get("transcript") is None
        assert source.get("transcription_skipped_reason") == "no_audio"


# ==========================================================================
# M6: Skim slicing utilities
# ==========================================================================


def _make_source(frame_interval: float = 1.0, frames_extracted: int = 5) -> dict:
    """Build a minimal source dict for slicing tests."""
    return {
        "id": "src_0",
        "path": "/abs/path/video.mp4",
        "frame_interval": frame_interval,
        "frames_extracted": frames_extracted,
        "frames_dir": FRAMES_DIR,
    }


class TestListFrames:
    def test_returns_sorted_frames_with_timecodes(self, test_video, tmp_path):
        create_workspace([test_video], interval=0.5, cwd=tmp_path, transcribe=False)
        data = load_project(tmp_path)
        frames = list_frames(data["sources"][0], cwd=tmp_path)
        assert len(frames) >= 2
        # ordered by timecode
        seconds = [f["timecode"]["seconds"] for f in frames]
        assert seconds == sorted(seconds)

    def test_frame_path_is_absolute(self, test_video, tmp_path):
        import os as _os
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False)
        data = load_project(tmp_path)
        frames = list_frames(data["sources"][0], cwd=tmp_path)
        for f in frames:
            assert _os.path.isabs(f["path"])

    def test_timecode_matches_interval(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False)
        data = load_project(tmp_path)
        frames = list_frames(data["sources"][0], cwd=tmp_path)
        # frame N (1-indexed) has timecode (N-1) * interval
        for i, frame in enumerate(frames):
            assert frame["timecode"]["seconds"] == pytest.approx(i * 1.0)

    def test_timecode_has_text_field(self, test_video, tmp_path):
        create_workspace([test_video], interval=1.0, cwd=tmp_path, transcribe=False)
        data = load_project(tmp_path)
        frames = list_frames(data["sources"][0], cwd=tmp_path)
        assert "text" in frames[0]["timecode"]


class TestFilterFramesByRange:
    def _frames(self):
        return [
            {"path": "f1", "timecode": {"text": "0:00:00.000", "seconds": 0.0}},
            {"path": "f2", "timecode": {"text": "0:00:01.000", "seconds": 1.0}},
            {"path": "f3", "timecode": {"text": "0:00:02.000", "seconds": 2.0}},
            {"path": "f4", "timecode": {"text": "0:00:03.000", "seconds": 3.0}},
            {"path": "f5", "timecode": {"text": "0:00:04.000", "seconds": 4.0}},
        ]

    def test_filter_whole_range(self):
        frames = self._frames()
        assert filter_frames_by_range(frames, 0.0, 4.0) == frames

    def test_filter_middle_range(self):
        frames = self._frames()
        result = filter_frames_by_range(frames, 1.0, 3.0)
        assert [f["timecode"]["seconds"] for f in result] == [1.0, 2.0, 3.0]

    def test_filter_boundaries_are_inclusive(self):
        frames = self._frames()
        result = filter_frames_by_range(frames, 2.0, 2.0)
        assert len(result) == 1
        assert result[0]["timecode"]["seconds"] == 2.0

    def test_filter_excludes_frames_outside(self):
        frames = self._frames()
        result = filter_frames_by_range(frames, 10.0, 20.0)
        assert result == []


class TestSubsampleFrames:
    def _n_frames(self, n):
        return [{"i": i, "timecode": {"seconds": float(i)}} for i in range(n)]

    def test_returns_all_when_count_exceeds_available(self):
        frames = self._n_frames(5)
        result = subsample_frames(frames, 10)
        assert len(result) == 5

    def test_returns_exact_count_when_possible(self):
        frames = self._n_frames(100)
        result = subsample_frames(frames, 10)
        assert len(result) == 10

    def test_includes_first_and_last(self):
        frames = self._n_frames(100)
        result = subsample_frames(frames, 10)
        assert result[0] is frames[0]
        assert result[-1] is frames[-1]

    def test_count_one_returns_middle(self):
        frames = self._n_frames(10)
        result = subsample_frames(frames, 1)
        assert len(result) == 1
        assert result[0] is frames[5]

    def test_count_zero_returns_empty(self):
        frames = self._n_frames(10)
        assert subsample_frames(frames, 0) == []

    def test_evenly_distributed(self):
        frames = self._n_frames(11)  # indices 0..10
        result = subsample_frames(frames, 3)
        assert [f["i"] for f in result] == [0, 5, 10]

    def test_no_duplicates_when_count_close_to_available(self):
        """With count=5 and available=6, we should return 5 distinct frames."""
        frames = self._n_frames(6)
        result = subsample_frames(frames, 5)
        indices = [f["i"] for f in result]
        assert len(indices) == len(set(indices))


class TestLoadTranscript:
    def test_reads_transcript_file(self, tmp_path):
        (tmp_path / MOVIESTAR_DIR).mkdir()
        fake_transcript = {"source_id": "src_0", "text": "hi", "words": []}
        save_transcript(fake_transcript, "src_0", cwd=tmp_path)
        source = {
            "id": "src_0",
            "transcript": {
                "source": "whisper:tiny",
                "path": f"{TRANSCRIPTS_DIR}/src_0.json",
            },
        }
        loaded = load_transcript(source, cwd=tmp_path)
        assert loaded == fake_transcript

    def test_returns_none_when_no_transcript_field(self, tmp_path):
        source = {"id": "src_0", "transcript": None}
        assert load_transcript(source, cwd=tmp_path) is None

    def test_returns_none_when_transcript_key_absent(self, tmp_path):
        source = {"id": "src_0"}
        assert load_transcript(source, cwd=tmp_path) is None


class TestSliceTranscript:
    def _transcript(self):
        return {
            "source_id": "src_0",
            "model": "tiny",
            "backend": "faster-whisper",
            "language": "en",
            "text": "One. Two. Three.",
            "duration": {"text": "0:00:03.000", "seconds": 3.0},
            "words": [
                {
                    "text": "One.",
                    "start": {"text": "0:00:00.000", "seconds": 0.0},
                    "end": {"text": "0:00:00.400", "seconds": 0.4},
                    "probability": 0.9,
                    "speaker": None,
                },
                {
                    "text": "Two.",
                    "start": {"text": "0:00:01.000", "seconds": 1.0},
                    "end": {"text": "0:00:01.400", "seconds": 1.4},
                    "probability": 0.9,
                    "speaker": None,
                },
                {
                    "text": "Three.",
                    "start": {"text": "0:00:02.000", "seconds": 2.0},
                    "end": {"text": "0:00:02.500", "seconds": 2.5},
                    "probability": 0.9,
                    "speaker": None,
                },
            ],
            "segments": [
                {
                    "text": " One.",
                    "start": {"text": "0:00:00.000", "seconds": 0.0},
                    "end": {"text": "0:00:00.400", "seconds": 0.4},
                },
                {
                    "text": " Two.",
                    "start": {"text": "0:00:01.000", "seconds": 1.0},
                    "end": {"text": "0:00:01.400", "seconds": 1.4},
                },
                {
                    "text": " Three.",
                    "start": {"text": "0:00:02.000", "seconds": 2.0},
                    "end": {"text": "0:00:02.500", "seconds": 2.5},
                },
            ],
        }

    def test_includes_words_within_range(self):
        sliced = slice_transcript(self._transcript(), 0.5, 1.5)
        word_texts = [w["text"] for w in sliced["words"]]
        assert word_texts == ["Two."]

    def test_excludes_words_outside_range(self):
        sliced = slice_transcript(self._transcript(), 5.0, 10.0)
        assert sliced["words"] == []
        assert sliced["segments"] == []

    def test_excludes_words_crossing_boundary(self):
        """Words that start OR end outside the range are excluded (both endpoints must be in)."""
        sliced = slice_transcript(self._transcript(), 0.2, 10.0)
        # "One." starts at 0.0, outside 0.2 — excluded.
        texts = [w["text"] for w in sliced["words"]]
        assert "One." not in texts
        assert "Two." in texts
        assert "Three." in texts

    def test_whole_range_returns_all_words(self):
        sliced = slice_transcript(self._transcript(), 0.0, 3.0)
        assert len(sliced["words"]) == 3

    def test_text_rebuilt_from_sliced_segments(self):
        sliced = slice_transcript(self._transcript(), 0.5, 1.5)
        assert "Two." in sliced["text"]
        assert "Three." not in sliced["text"]

    def test_preserves_metadata(self):
        sliced = slice_transcript(self._transcript(), 0.0, 3.0)
        assert sliced["model"] == "tiny"
        assert sliced["language"] == "en"
        assert sliced["backend"] == "faster-whisper"


# ============================================================================
# M13a — multi-source workspaces.
# ============================================================================


from moviestar.project import (
    WorkspaceConflictError,
    derive_source_id,
)


class TestDeriveSourceId:
    def test_simple_filename(self):
        assert derive_source_id("/abs/holden.mp4") == "holden"

    def test_first_token_before_hyphen(self):
        # The Riverside-style filename
        assert derive_source_id("/abs/holden-2026-5-1__11-41-19-CFR.mp4") == "holden"

    def test_first_token_before_underscore(self):
        assert (
            derive_source_id("/abs/jdilla_2026_5_1__13_41_19_cfr.mp4") == "jdilla"
        )

    def test_lowercased(self):
        assert derive_source_id("/abs/HOLDEN.mp4") == "holden"

    def test_collision_disambiguates_with_suffix(self):
        taken = {"holden"}
        assert derive_source_id("/abs/holden-cam2.mp4", taken) == "holden_2"

    def test_collision_chain(self):
        taken = {"holden", "holden_2"}
        assert derive_source_id("/abs/holden-cam3.mp4", taken) == "holden_3"

    def test_unparseable_falls_back_to_src_n(self):
        # Leading non-alphanumeric → no token → fall back.
        assert derive_source_id("/abs/_weird.mp4") == "src_0"

    def test_unparseable_with_taken_increments(self):
        taken = {"src_0", "src_1"}
        assert derive_source_id("/abs/_weird.mp4", taken) == "src_2"

    def test_riverside_screenshare_token(self):
        # "riverside_screenshare_..." → "riverside" (first token)
        path = "/abs/riverside_screenshare_hd_synced-video_screenshare_xyz.mp4"
        assert derive_source_id(path) == "riverside"


class TestCreateWorkspaceMultiSource:
    """Integration tests for the multi-source/--add/--as paths of
    create_workspace. Requires the test_video fixture (small synthetic
    clip from conftest)."""

    def test_single_path_produces_one_source(
        self, test_video: str, tmp_path: Path
    ):
        """Single-path call produces a project with one source."""
        project = create_workspace(
            [test_video],
            interval=1.0,
            cwd=tmp_path,
            transcribe=False,
            quiet=True,
        )
        assert project["version"] == "0.1"  # M13a doesn't bump project.json's version
        assert len(project["sources"]) == 1
        source = project["sources"][0]
        assert "id" in source
        assert "path" in source
        assert "duration" in source
        assert source["frames_extracted"] > 0

    def test_two_paths_produces_two_sources(
        self, test_video: str, silent_video: str, tmp_path: Path
    ):
        project = create_workspace(
            [test_video, silent_video],
            interval=1.0,
            cwd=tmp_path,
            transcribe=False,
            quiet=True,
        )
        assert len(project["sources"]) == 2
        ids = [s["id"] for s in project["sources"]]
        # IDs are unique.
        assert len(set(ids)) == 2

    def test_explicit_names_via_names_param(
        self, test_video: str, silent_video: str, tmp_path: Path
    ):
        project = create_workspace(
            [test_video, silent_video],
            interval=1.0,
            cwd=tmp_path,
            transcribe=False,
            quiet=True,
            names=["cam1", "cam2"],
        )
        ids = [s["id"] for s in project["sources"]]
        assert ids == ["cam1", "cam2"]

    def test_partial_names_with_some_none(
        self, test_video: str, silent_video: str, tmp_path: Path
    ):
        """names=['cam1', None] → first source named cam1, second auto-derived."""
        project = create_workspace(
            [test_video, silent_video],
            interval=1.0,
            cwd=tmp_path,
            transcribe=False,
            quiet=True,
            names=["cam1", None],
        )
        assert project["sources"][0]["id"] == "cam1"
        # Second is auto-derived from filename.
        assert project["sources"][1]["id"] != "cam1"

    def test_duplicate_explicit_names_raises(
        self, test_video: str, silent_video: str, tmp_path: Path
    ):
        with pytest.raises(WorkspaceConflictError, match="Duplicate"):
            create_workspace(
                [test_video, silent_video],
                interval=1.0,
                cwd=tmp_path,
                transcribe=False,
                quiet=True,
                names=["same", "same"],
            )

    def test_too_many_names_raises(self, test_video: str, tmp_path: Path):
        with pytest.raises(WorkspaceConflictError, match="--as"):
            create_workspace(
                [test_video],
                interval=1.0,
                cwd=tmp_path,
                transcribe=False,
                quiet=True,
                names=["a", "b"],  # one path, two names
            )

    def test_empty_paths_raises(self, tmp_path: Path):
        with pytest.raises(ValueError):
            create_workspace(
                [], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True
            )

    def test_add_extends_existing_project(
        self, test_video: str, silent_video: str, tmp_path: Path
    ):
        # First load (one source).
        create_workspace(
            [test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True
        )
        # Add a second source.
        project = create_workspace(
            [silent_video],
            interval=1.0,
            cwd=tmp_path,
            transcribe=False,
            quiet=True,
            add=True,
        )
        assert len(project["sources"]) == 2

    def test_add_preserves_caption_rules(
        self, test_video: str, silent_video: str, tmp_path: Path
    ):
        create_workspace(
            [test_video], interval=1.0, cwd=tmp_path, transcribe=False, quiet=True
        )
        project = load_project(tmp_path, walk=False)
        project["caption_rules"] = [
            {
                "id": "caption_rule_0001",
                "type": "replace",
                "match": ["mispelled"],
                "replacement": "misspelled",
            }
        ]
        save_project(project, tmp_path, walk=False)

        extended = create_workspace(
            [silent_video],
            interval=1.0,
            cwd=tmp_path,
            transcribe=False,
            quiet=True,
            add=True,
        )

        assert extended["caption_rules"] == project["caption_rules"]

    def test_add_collision_with_existing_source_raises(
        self, test_video: str, tmp_path: Path
    ):
        create_workspace(
            [test_video],
            interval=1.0,
            cwd=tmp_path,
            transcribe=False,
            quiet=True,
            names=["holden"],
        )
        with pytest.raises(WorkspaceConflictError, match="collides"):
            create_workspace(
                [test_video],
                interval=1.0,
                cwd=tmp_path,
                transcribe=False,
                quiet=True,
                names=["holden"],  # collides with existing
                add=True,
            )

    def test_add_without_existing_project_raises(
        self, test_video: str, tmp_path: Path
    ):
        with pytest.raises(WorkspaceConflictError, match="--add requires"):
            create_workspace(
                [test_video],
                interval=1.0,
                cwd=tmp_path,
                transcribe=False,
                quiet=True,
                add=True,
            )

    def test_filename_collision_auto_disambiguates(
        self, test_video: str, tmp_path: Path
    ):
        """Two paths with same first-token → second gets _2 suffix."""
        # Use the same file twice — same derived ID, expect collision handling.
        project = create_workspace(
            [test_video, test_video],
            interval=1.0,
            cwd=tmp_path,
            transcribe=False,
            quiet=True,
        )
        ids = [s["id"] for s in project["sources"]]
        assert ids[0] != ids[1]
        assert ids[1].endswith("_2")
