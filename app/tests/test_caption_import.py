"""Import existing caption files as the transcript (issue #29).

Long public-meeting footage usually ships with captions: YouTube
auto-captions as WebVTT with per-word timing tags, or a broadcaster's SRT.
``load --captions`` and ``retranscribe --captions`` turn those cues into the
same transcript shape Whisper produces, in seconds instead of hours, so
``find``, ``skim``, and ``captions generate`` work on the source directly.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.caption_import import (
    CAPTIONS_BACKEND,
    CaptionImportError,
    import_caption_transcript,
    parse_caption_file,
)
from moviestar.cli import cli
from moviestar.find import search_transcript
from tests.conftest import assert_error_envelope


# YouTube auto-captions roll: each cue repeats the previous line, then adds
# a new line whose words carry <timestamp><c> tags. A 10 ms cue in between
# repeats the finished line with no tags. The \x20 lines are the
# single-space lines YouTube emits.
YOUTUBE_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:01.000 align:start position:0%
\x20
call<00:00:00.300><c> the</c><00:00:00.500><c> roll</c>

00:00:01.000 --> 00:00:01.010 align:start position:0%
call the roll
\x20

00:00:01.010 --> 00:00:02.000 align:start position:0%
call the roll
all<00:00:01.200><c> in</c><00:00:01.400><c> favor</c>

00:00:02.000 --> 00:00:02.010 align:start position:0%
all in favor
\x20

00:00:02.010 --> 00:00:03.000 align:start position:0%
all in favor
&gt;&gt; aye<00:00:02.500><c> aye</c>
"""

BROADCAST_SRT = """1
00:00:00,000 --> 00:00:01,000
<i>Call the roll.</i>

2
00:00:01,000 --> 00:00:02,000
All in favor?

3
00:00:02,000 --> 00:00:03,000
Motion &amp; second.
"""


def _write(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def _texts(transcript: dict) -> list[str]:
    return [w["text"] for w in transcript["words"]]


def _starts(transcript: dict) -> list[float]:
    return [w["start"]["seconds"] for w in transcript["words"]]


class TestParseCaptionFile:
    def test_srt_cues_strip_markup_and_entities(self, tmp_path):
        parsed = parse_caption_file(_write(tmp_path, "a.srt", BROADCAST_SRT))
        assert parsed["format"] == "srt"
        assert [c["start"] for c in parsed["cues"]] == [0.0, 1.0, 2.0]
        assert parsed["cues"][0]["end"] == 1.0

    def test_vtt_reads_language_header(self, tmp_path):
        parsed = parse_caption_file(_write(tmp_path, "a.vtt", YOUTUBE_VTT))
        assert parsed["format"] == "vtt"
        assert parsed["language"] == "en"

    def test_crlf_and_bom_are_tolerated(self, tmp_path):
        path = tmp_path / "a.srt"
        path.write_bytes(("﻿" + BROADCAST_SRT).replace("\n", "\r\n").encode())
        parsed = parse_caption_file(str(path))
        assert len(parsed["cues"]) == 3

    def test_short_vtt_timestamps_without_hours(self, tmp_path):
        text = "WEBVTT\n\n01:02.500 --> 01:04.000\nhello there\n"
        parsed = parse_caption_file(_write(tmp_path, "a.vtt", text))
        assert parsed["cues"][0]["start"] == pytest.approx(62.5)

    def test_whitespace_separated_srt_does_not_leak_index_numbers(self, tmp_path):
        text = (
            "1\n00:00:00,000 --> 00:00:01,000\nfirst line\n \n"
            "2\n00:00:01,000 --> 00:00:02,000\nsecond line\n"
        )
        transcript = import_caption_transcript(
            _write(tmp_path, "a.srt", text), "src_0", source_duration=2.0
        )
        assert _texts(transcript) == ["first", "line", "second", "line"]

    def test_file_without_cues_is_an_error(self, tmp_path):
        with pytest.raises(CaptionImportError, match="WebVTT or SRT"):
            parse_caption_file(_write(tmp_path, "a.vtt", "WEBVTT\n\nNOTE hi\n"))

    def test_missing_file_is_an_error(self, tmp_path):
        with pytest.raises(CaptionImportError, match="not found"):
            parse_caption_file(str(tmp_path / "missing.vtt"))


class TestImportCaptionTranscript:
    def test_youtube_word_tags_time_each_word_without_rolling_duplicates(
        self, tmp_path
    ):
        transcript = import_caption_transcript(
            _write(tmp_path, "a.vtt", YOUTUBE_VTT), "src_0", source_duration=3.0
        )
        assert _texts(transcript) == [
            "call", "the", "roll", "all", "in", "favor", "aye", "aye",
        ]
        assert _starts(transcript) == pytest.approx(
            [0.0, 0.3, 0.5, 1.01, 1.2, 1.4, 2.01, 2.5]
        )
        ends = [w["end"]["seconds"] for w in transcript["words"]]
        # Each tagged word ends where the next one starts, and the last
        # word in a cue ends with the cue.
        assert ends[:3] == pytest.approx([0.3, 0.5, 1.0])
        assert transcript["captions"]["cues_with_word_timing"] == 3

    def test_youtube_cues_survive_stripped_space_lines(self, tmp_path):
        # Editors and some downloaders strip YouTube's single-space lines,
        # leaving an empty line between a timing line and its text.
        stripped = YOUTUBE_VTT.replace("\n \n", "\n\n")
        transcript = import_caption_transcript(
            _write(tmp_path, "a.vtt", stripped), "src_0", source_duration=3.0
        )
        assert _texts(transcript)[:3] == ["call", "the", "roll"]
        assert len(transcript["words"]) == 8

    def test_untagged_cues_distribute_words_across_the_cue(self, tmp_path):
        transcript = import_caption_transcript(
            _write(tmp_path, "a.srt", BROADCAST_SRT), "src_0", source_duration=3.0
        )
        assert _texts(transcript) == [
            "Call", "the", "roll.", "All", "in", "favor?", "Motion", "&", "second.",
        ]
        words = transcript["words"]
        assert words[0]["start"]["seconds"] == 0.0
        assert words[2]["end"]["seconds"] == pytest.approx(1.0)
        for word in words:
            assert word["end"]["seconds"] > word["start"]["seconds"]
        assert _starts(transcript) == sorted(_starts(transcript))
        assert transcript["captions"]["cues_with_word_timing"] == 0

    def test_transcript_shape_matches_whisper_and_names_its_backend(
        self, tmp_path
    ):
        caption_path = _write(tmp_path, "a.vtt", YOUTUBE_VTT)
        transcript = import_caption_transcript(
            caption_path, "src_0", source_duration=3.0
        )
        assert transcript["backend"] == CAPTIONS_BACKEND == "imported-captions"
        assert transcript["source_id"] == "src_0"
        assert transcript["model"] is None
        assert transcript["language"] == "en"
        assert transcript["duration"]["seconds"] == 3.0
        assert transcript["text"] == "call the roll all in favor aye aye"
        assert [s["text"] for s in transcript["segments"]] == [
            "call the roll", "all in favor", "aye aye",
        ]
        assert transcript["segments"][1]["start"]["seconds"] == 1.01
        word = transcript["words"][0]
        assert set(word) >= {"text", "start", "end", "speaker"}
        assert transcript["captions"]["path"] == str(Path(caption_path).resolve())
        assert transcript["captions"]["format"] == "vtt"
        assert transcript["captions"]["cues"] == 3
        assert transcript["no_speech_detected"] is False
        assert "warnings" not in transcript

    def test_cues_past_the_source_end_are_dropped_with_a_warning(self, tmp_path):
        transcript = import_caption_transcript(
            _write(tmp_path, "a.srt", BROADCAST_SRT), "src_0", source_duration=1.5
        )
        assert _texts(transcript)[-1] == "favor?"
        assert transcript["words"][-1]["end"]["seconds"] <= 1.5
        [warning] = transcript["warnings"]
        assert warning["code"] == "captions_extend_past_source"
        assert warning["cues_dropped"] == 1

    def test_find_returns_cue_accurate_hits(self, tmp_path):
        transcript = import_caption_transcript(
            _write(tmp_path, "a.vtt", YOUTUBE_VTT), "src_0", source_duration=3.0
        )
        [match] = search_transcript(transcript, "all in favor", exact=True)
        assert match["source_range"] == pytest.approx((1.01, 2.0))


class TestCli:
    @pytest.fixture
    def no_whisper(self, monkeypatch):
        def _fail(*args, **kwargs):
            raise AssertionError("Whisper must not run for imported captions")

        monkeypatch.setattr("moviestar.project.transcribe_file", _fail)
        monkeypatch.setattr("moviestar.cli.transcribe_file", _fail)

    def _load(self, runner, *args):
        return runner.invoke(cli, ["load", *args])

    def test_load_with_captions_skips_whisper(
        self, test_video, tmp_path, monkeypatch, no_whisper
    ):
        monkeypatch.chdir(tmp_path)
        captions = _write(tmp_path, "meeting.en.vtt", YOUTUBE_VTT)
        runner = CliRunner()

        result = self._load(
            runner, test_video, "--as", "src_0", "--no-frames",
            "--captions", captions,
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        [source] = data["sources"]
        assert source["transcript"]["backend"] == "imported-captions"
        assert source["transcript"]["model"] is None
        assert source["transcript"]["word_count"] == 6
        on_disk = json.loads(
            (tmp_path / "moviestar" / "transcripts" / "src_0.json").read_text()
        )
        assert on_disk["backend"] == "imported-captions"
        # Captions past the 2 s test video are dropped and reported.
        assert [w["code"] for w in data["warnings"]] == [
            "captions_extend_past_source"
        ]

        found = runner.invoke(cli, ["find", "all in favor", "--exact"])
        assert found.exit_code == 0, found.stdout
        [match] = json.loads(found.stdout)["matches"]
        assert match["source_range"]["from"]["seconds"] == pytest.approx(1.01)

        status = json.loads(runner.invoke(cli, ["status"]).stdout)
        assert status["sources"][0]["transcript"]["backend"] == "imported-captions"

    def test_captions_generate_uses_imported_word_timings(
        self, test_video, tmp_path, monkeypatch, no_whisper
    ):
        monkeypatch.chdir(tmp_path)
        captions = _write(tmp_path, "meeting.en.vtt", YOUTUBE_VTT)
        runner = CliRunner()
        loaded = self._load(
            runner, test_video, "--as", "src_0", "--no-frames",
            "--captions", captions,
        )
        assert loaded.exit_code == 0, loaded.stdout

        result = runner.invoke(cli, ["captions", "generate", "--source", "src_0"])

        assert result.exit_code == 0, result.stdout
        spec = json.loads((tmp_path / "moviestar" / "spec.json").read_text())
        caption_words = [
            word
            for overlay in spec["overlays"]
            if overlay["kind"] == "caption"
            for word in overlay.get("tokens", [])
        ]
        assert [w["text"] for w in caption_words][:3] == ["call", "the", "roll"]

    def test_captions_with_no_transcribe_is_rejected(
        self, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        captions = _write(tmp_path, "a.vtt", YOUTUBE_VTT)
        result = self._load(
            CliRunner(), test_video, "--captions", captions, "--no-transcribe"
        )
        assert result.exit_code == 1
        assert_error_envelope(json.loads(result.stdout), command="load")
        assert not (tmp_path / "moviestar").exists()

    def test_unreadable_captions_fail_before_the_workspace_changes(
        self, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        bad = _write(tmp_path, "bad.vtt", "not captions at all\n")
        result = self._load(CliRunner(), test_video, "--captions", bad)
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="load")
        assert "WebVTT or SRT" in data["error"]
        assert not (tmp_path / "moviestar").exists()

    def test_captions_count_must_match_videos(
        self, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        captions = _write(tmp_path, "a.vtt", YOUTUBE_VTT)
        result = self._load(
            CliRunner(), test_video, test_video, "--captions", captions
        )
        assert result.exit_code == 1
        assert "--captions" in json.loads(result.stdout)["error"]

    def test_retranscribe_with_captions_replaces_the_transcript(
        self, loaded_project, test_video, tmp_path, no_whisper
    ):
        loaded_project(test_video, frames=False)
        captions = _write(tmp_path, "a.srt", BROADCAST_SRT)

        result = CliRunner().invoke(
            cli, ["retranscribe", "--captions", captions, "--quiet"]
        )

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["transcript"]["backend"] == "imported-captions"
        assert data["transcript"]["word_count"] == 6
        project = json.loads((tmp_path / "moviestar" / "project.json").read_text())
        assert project["sources"][0]["transcript"]["backend"] == "imported-captions"
        assert "transcription_skipped_reason" not in project["sources"][0]

    def test_dry_run_reports_caption_import_without_whisper(
        self, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        captions = _write(tmp_path, "a.vtt", YOUTUBE_VTT)
        result = self._load(
            CliRunner(), test_video, "--captions", captions, "--dry-run"
        )
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["would_import_captions"] == str(Path(captions).resolve())
        assert data["would_transcribe"] is False
        assert "would_use_model" not in data
        assert not (tmp_path / "moviestar").exists()
