"""Transcribe only a time range of a long source (issue #29).

``load --transcribe-range`` and ``retranscribe --range`` cut each window
out with FFmpeg and run Whisper on that window alone, so a 20-minute window
of a 4.6-hour meeting costs 20 minutes of Whisper, not the whole file.
Word times come back on the source clock, and a ranged run merges into the
existing transcript instead of replacing it.
"""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.transcribe import (
    TranscriptionError,
    merge_range_transcript,
    normalize_transcribe_ranges,
    transcribe_ranges,
)
from tests.conftest import (
    assert_error_envelope,
    inject_synthetic_transcript,
    make_segment,
    make_word,
)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


class _FakeWhisper:
    """Answers every window with the same window-relative words."""

    words: list[tuple[str, float, float]] = []
    windows: list[float] = []

    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio, **kwargs):
        seconds = len(audio) / 16000
        type(self).windows.append(seconds)

        class _Word:
            def __init__(self, text, start, end):
                self.word, self.start, self.end, self.probability = text, start, end, 0.9

        class _Segment:
            def __init__(self, words):
                self.words = [_Word(*w) for w in words]
                self.text = " ".join(w[0] for w in words)
                self.start = words[0][1]
                self.end = words[-1][2]

        class _Info:
            duration = seconds
            language = "en"

        return iter([_Segment(self.words)] if self.words else []), _Info()


@pytest.fixture
def fake_whisper(monkeypatch):
    import faster_whisper

    _FakeWhisper.words = [("hello", 0.5, 0.9), ("there", 1.0, 1.4)]
    _FakeWhisper.windows = []
    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeWhisper)
    monkeypatch.setattr("moviestar.transcribe.resolve_cached_model", lambda m: None)
    monkeypatch.setattr("moviestar.transcribe.is_model_cached", lambda m: True)
    return _FakeWhisper


@pytest.fixture
def tone(tmp_path) -> str:
    """12 seconds of stereo tone: audio everywhere, so windows are cut
    from real decoded media."""
    path = str(tmp_path / "tone.wav")
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=300:duration=12:sample_rate=48000",
        "-ac", "2", path,
    )
    return path


def _words(transcript: dict) -> list[tuple[str, float]]:
    return [(w["text"], w["start"]["seconds"]) for w in transcript["words"]]


class TestNormalizeRanges:
    def test_sorts_merges_overlaps_and_clamps_to_the_source(self):
        assert normalize_transcribe_ranges(
            [(8.0, 20.0), (1.0, 3.0), (2.0, 4.0)], source_duration=12.0
        ) == [(1.0, 4.0), (8.0, 12.0)]

    @pytest.mark.parametrize(
        ("ranges", "message"),
        [
            ([(5.0, 5.0)], "must end after it starts"),
            ([(6.0, 2.0)], "must end after it starts"),
            ([(12.0, 14.0)], "starts at or after the source ends"),
        ],
    )
    def test_rejects_empty_or_out_of_bounds_ranges(self, ranges, message):
        with pytest.raises(TranscriptionError, match=message):
            normalize_transcribe_ranges(ranges, source_duration=12.0)


class TestTranscribeRanges:
    def test_whisper_sees_only_the_window_and_times_are_on_the_source_clock(
        self, tone, fake_whisper
    ):
        result = transcribe_ranges(
            tone, "src_0", [(4.0, 8.0)], source_duration=12.0,
            model="tiny", quiet=True, speech_check=False,
        )

        assert fake_whisper.windows == [pytest.approx(4.0, abs=0.05)]
        assert _words(result) == [("hello", 4.5), ("there", 5.0)]
        assert result["segments"][0]["start"]["seconds"] == pytest.approx(4.5)
        assert result["duration"]["seconds"] == 12.0
        assert result["coverage"] == "ranges"
        [window] = result["transcribed_ranges"]
        assert window["from"]["seconds"] == 4.0
        assert window["to"]["seconds"] == 8.0
        assert window["model"] == "tiny"
        assert window["audio"]["channel"]["requested"] == "auto"
        assert result["source_path"] == str(Path(tone).resolve())
        assert result["backend"] == "faster-whisper"

    def test_multiple_ranges_merge_in_source_order(self, tone, fake_whisper):
        result = transcribe_ranges(
            tone, "src_0", [(9.0, 11.0), (1.0, 3.0)], source_duration=12.0,
            model="tiny", quiet=True, speech_check=False,
        )

        assert len(fake_whisper.windows) == 2
        assert _words(result) == [
            ("hello", 1.5), ("there", 2.0), ("hello", 9.5), ("there", 10.0),
        ]
        assert result["text"] == "hello there hello there"
        assert [r["from"]["seconds"] for r in result["transcribed_ranges"]] == [
            1.0, 9.0,
        ]


class TestMergeRangeTranscript:
    def _base(self, backend: str = "imported-captions") -> dict:
        return {
            "source_id": "src_0",
            "model": None,
            "backend": backend,
            "language": "en",
            "text": "one two three four",
            "duration": {"text": "", "seconds": 12.0},
            "words": [
                make_word("one", 1.0, 1.5),
                make_word("two", 4.5, 5.0),
                make_word("three", 6.0, 6.5),
                make_word("four", 10.0, 10.5),
            ],
            "segments": [
                make_segment("one", 1.0, 1.5),
                make_segment("two three", 4.5, 6.5),
                make_segment("four", 10.0, 10.5),
            ],
        }

    def _ranged(self) -> dict:
        return {
            "source_id": "src_0",
            "model": "tiny",
            "backend": "faster-whisper",
            "language": "en",
            "duration": {"text": "", "seconds": 12.0},
            "words": [make_word("TWO", 4.6, 5.0)],
            "segments": [make_segment("TWO", 4.6, 5.0)],
            "coverage": "ranges",
            "transcribed_ranges": [
                {
                    "from": {"text": "", "seconds": 4.0},
                    "to": {"text": "", "seconds": 5.5},
                    "model": "tiny",
                }
            ],
            "vocabulary": [],
        }

    def test_replaces_words_inside_ranges_and_keeps_the_rest(self):
        merged = merge_range_transcript(self._base(), self._ranged())

        assert [w["text"] for w in merged["words"]] == ["one", "TWO", "three", "four"]
        # The segment straddling the range keeps only its outside part.
        assert [
            (s["text"], s["start"]["seconds"], s["end"]["seconds"])
            for s in merged["segments"]
        ] == [
            ("one", 1.0, 1.5),
            ("TWO", 4.6, 5.0),
            ("three", 5.5, 6.5),
            ("four", 10.0, 10.5),
        ]
        assert merged["text"] == "one TWO three four"
        assert merged["backend"] == "imported-captions"
        assert merged["coverage"] == "full"
        assert len(merged["transcribed_ranges"]) == 1

    def test_without_a_base_the_ranged_transcript_stands_alone(self):
        ranged = self._ranged()
        assert merge_range_transcript(None, ranged) == ranged

    def test_ranges_accumulate_on_a_ranges_only_transcript(self):
        first = self._ranged()
        second = self._ranged()
        second["words"] = [make_word("later", 9.2, 9.6)]
        second["segments"] = [make_segment("later", 9.2, 9.6)]
        second["transcribed_ranges"] = [
            {
                "from": {"text": "", "seconds": 9.0},
                "to": {"text": "", "seconds": 10.0},
                "model": "base",
            }
        ]

        merged = merge_range_transcript(first, second)

        assert [w["text"] for w in merged["words"]] == ["TWO", "later"]
        assert merged["coverage"] == "ranges"
        assert [
            (r["from"]["seconds"], r["to"]["seconds"], r["model"])
            for r in merged["transcribed_ranges"]
        ] == [(4.0, 5.5, "tiny"), (9.0, 10.0, "base")]

    def test_a_redone_window_replaces_the_overlapping_old_window(self):
        first = self._ranged()
        second = self._ranged()
        second["transcribed_ranges"] = [
            {
                "from": {"text": "", "seconds": 5.0},
                "to": {"text": "", "seconds": 6.0},
                "model": "base",
            }
        ]
        second["words"] = [make_word("redo", 5.2, 5.4)]
        second["segments"] = [make_segment("redo", 5.2, 5.4)]

        merged = merge_range_transcript(first, second)

        assert [
            (r["from"]["seconds"], r["to"]["seconds"], r["model"])
            for r in merged["transcribed_ranges"]
        ] == [(4.0, 5.0, "tiny"), (5.0, 6.0, "base")]
        assert [w["text"] for w in merged["words"]] == ["TWO", "redo"]


class TestCli:
    def _load(self, *args):
        return CliRunner().invoke(cli, ["load", *args])

    def test_load_transcribes_only_the_requested_window(
        self, test_video, tmp_path, monkeypatch, fake_whisper
    ):
        monkeypatch.chdir(tmp_path)
        _FakeWhisper.words = [("hello", 0.1, 0.3)]

        result = self._load(
            test_video, "--as", "src_0", "--no-frames", "--model", "tiny",
            "--no-speech-check", "--transcribe-range", "0.5", "00:01.5",
        )

        assert result.exit_code == 0, result.stdout
        assert fake_whisper.windows == [pytest.approx(1.0, abs=0.05)]
        [source] = json.loads(result.stdout)["sources"]
        assert source["transcript"]["word_count"] == 1
        assert source["transcript"]["coverage"] == "ranges"
        [window] = source["transcript"]["transcribed_ranges"]
        assert window["from"]["seconds"] == 0.5
        on_disk = json.loads(
            (tmp_path / "moviestar" / "transcripts" / "src_0.json").read_text()
        )
        assert _words(on_disk) == [("hello", pytest.approx(0.6))]

    def test_range_past_the_end_fails_before_the_workspace_changes(
        self, test_video, tmp_path, monkeypatch, fake_whisper
    ):
        monkeypatch.chdir(tmp_path)
        result = self._load(test_video, "--transcribe-range", "5", "6")
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="load")
        assert "after the source ends" in data["error"]
        assert not (tmp_path / "moviestar").exists()
        assert fake_whisper.windows == []

    def test_invalid_range_timecode_is_rejected(self, test_video, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = self._load(test_video, "--transcribe-range", "soon", "1")
        assert result.exit_code == 1
        assert_error_envelope(json.loads(result.stdout), command="load")

    def test_range_with_multiple_videos_is_rejected(
        self, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        result = self._load(test_video, test_video, "--transcribe-range", "0", "1")
        assert result.exit_code == 1
        assert "--transcribe-range" in json.loads(result.stdout)["error"]

    def test_range_with_no_transcribe_is_rejected(
        self, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        result = self._load(
            test_video, "--no-transcribe", "--transcribe-range", "0", "1"
        )
        assert result.exit_code == 1
        assert not (tmp_path / "moviestar").exists()

    def test_captions_outside_and_whisper_inside_the_range(
        self, test_video, tmp_path, monkeypatch, fake_whisper
    ):
        monkeypatch.chdir(tmp_path)
        _FakeWhisper.words = [("whisper", 0.1, 0.4)]
        captions = tmp_path / "a.srt"
        captions.write_text(
            "1\n00:00:00,000 --> 00:00:00,800\nbefore\n\n"
            "2\n00:00:01,000 --> 00:00:01,400\ninside\n\n"
            "3\n00:00:01,600 --> 00:00:02,000\nafter\n"
        )

        result = self._load(
            test_video, "--as", "src_0", "--no-frames", "--no-speech-check",
            "--model", "tiny", "--captions", str(captions),
            "--transcribe-range", "0.9", "1.5",
        )

        assert result.exit_code == 0, result.stdout
        on_disk = json.loads(
            (tmp_path / "moviestar" / "transcripts" / "src_0.json").read_text()
        )
        assert [w["text"] for w in on_disk["words"]] == ["before", "whisper", "after"]
        assert on_disk["backend"] == "imported-captions"
        assert on_disk["coverage"] == "full"

    def test_retranscribe_range_merges_into_the_existing_transcript(
        self, loaded_project, test_video, tmp_path, fake_whisper
    ):
        loaded_project(test_video, frames=False)
        inject_synthetic_transcript(
            tmp_path,
            [make_word("keep", 0.1, 0.3), make_word("old", 1.1, 1.3)],
            [make_segment("keep", 0.1, 0.3), make_segment("old", 1.1, 1.3)],
        )
        _FakeWhisper.words = [("new", 0.1, 0.3)]

        result = CliRunner().invoke(
            cli,
            [
                "retranscribe", "--model", "tiny", "--no-speech-check",
                "--range", "1", "1.5", "--quiet",
            ],
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["transcript"]["word_count"] == 2
        assert [r["from"]["seconds"] for r in data["transcript"]["transcribed_ranges"]] == [1.0]
        on_disk = json.loads(
            (tmp_path / "moviestar" / "transcripts" / "src_0.json").read_text()
        )
        assert _words(on_disk) == [("keep", 0.1), ("new", pytest.approx(1.1))]


class TestRealWhisper:
    def test_tiny_model_finds_speech_inside_a_late_window(self, speech_wav, tmp_path):
        delayed = str(tmp_path / "delayed.wav")
        _ffmpeg("-i", speech_wav, "-af", "adelay=6000,apad=whole_dur=10", delayed)

        result = transcribe_ranges(
            delayed, "src_0", [(5.0, 9.5)], source_duration=10.0,
            model="tiny", quiet=True,
        )

        glad = [w for w in result["words"] if "glad" in w["text"].lower()]
        assert glad, result["text"]
        assert 6.0 < glad[0]["start"]["seconds"] < 9.0
