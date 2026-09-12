"""Pure-function tests for moviestar.find.

These exercise the search algorithm against synthetic transcripts so
they stay fast (no Whisper). The CLI integration tests in test_cli.py
cover the load → find → trim flow end-to-end.
"""

import pytest

from moviestar.find import (
    _enclosing_segment_range,
    _longest_contiguous_query_run,
    search_transcript,
    snap_boundaries_to_words,
)
from tests.conftest import make_segment, make_word


def _transcript(words: list[dict], segments: list[dict]) -> dict:
    """Wrap word/segment lists in the on-disk transcript shape for
    pure-function tests. CLI integration tests use
    ``inject_synthetic_transcript`` from conftest instead."""
    return {
        "source_id": "src_0",
        "source_path": "/tmp/test.mp4",
        "model": "tiny",
        "backend": "faster-whisper",
        "language": "en",
        "text": " ".join(w["text"] for w in words),
        "duration": {"text": "", "seconds": 60.0},
        "words": words,
        "segments": segments,
    }


def _agent_phrase_transcript() -> dict:
    words = [
        make_word("tool", 0.4, 0.5),
        make_word("where", 0.6, 0.7),
        make_word("the", 0.8, 0.85),
        make_word("agent", 0.9, 1.05),
        make_word("is", 1.1, 1.15),
        make_word("going", 1.2, 1.35),
    ]
    segments = [
        make_segment("tool where the agent is going", 0.4, 1.35),
    ]
    return _transcript(words, segments)


@pytest.fixture
def pain_transcript() -> dict:
    """Synthetic transcript covering the canonical 'find a quote' example."""
    words = [
        make_word("intro", 0.0, 0.5),
        make_word("setup", 0.6, 1.0),
        make_word("this", 1.2, 1.4),
        make_word("is", 1.5, 1.6),
        make_word("where", 1.7, 1.9),
        make_word("you", 2.0, 2.1),
        make_word("get", 2.2, 2.4),
        make_word("to", 2.5, 2.6),
        make_word("that", 2.7, 2.9),
        make_word("pain", 3.0, 3.4),
        make_word("aside", 4.0, 4.5),
        make_word("this", 5.0, 5.2),
        make_word("gets", 5.3, 5.5),
        make_word("painful", 5.6, 6.2),
        make_word("later", 7.0, 7.4),
        make_word("it", 8.0, 8.1),
        make_word("can", 8.2, 8.3),
        make_word("be", 8.4, 8.5),
        make_word("painful", 8.6, 9.2),
    ]
    segments = [
        make_segment("intro setup", 0.0, 1.0),
        make_segment("this is where you get to that pain", 1.2, 3.4),
        make_segment("aside", 4.0, 4.5),
        make_segment("this gets painful later", 5.0, 7.4),
        make_segment("it can be painful", 8.0, 9.2),
    ]
    return _transcript(words, segments)


# ---------- fuzzy ----------


class TestFuzzySearch:
    def test_finds_phrase_with_one_word_swap(self, pain_transcript):
        """Headline use case: human says 'the pain', transcript has
        'that pain'. partial_ratio should still rank this top."""
        matches = search_transcript(
            pain_transcript, "this is where you get to the pain"
        )
        assert matches, "fuzzy should never return empty when transcript has words"
        top = matches[0]
        assert "this is where you get to that pain" in top["text"]
        # 1-word swap → high score (rapidfuzz typically reports 90+).
        assert top["score"] >= 80, f"weak score on near-exact match: {top['score']}"

    def test_returns_top_n_sorted_by_score(self, pain_transcript):
        matches = search_transcript(pain_transcript, "pain", limit=3)
        # Multiple "pain" occurrences in the transcript.
        assert len(matches) >= 2
        scores = [m["score"] for m in matches]
        assert scores == sorted(scores, reverse=True), (
            f"matches not sorted by score desc: {scores}"
        )

    def test_never_empty_in_fuzzy_mode(self, pain_transcript):
        """D2: fuzzy default never returns empty when transcript has words."""
        matches = search_transcript(pain_transcript, "completely unrelated query")
        assert matches, "fuzzy must return SOMETHING — best-available even if weak"

    def test_dedupes_overlapping_matches(self, pain_transcript):
        """Two windows of the same span shouldn't both appear."""
        matches = search_transcript(pain_transcript, "pain", limit=10)
        ranges = [m["source_range"] for m in matches]
        # No two ranges overlap by >50%.
        for i, r1 in enumerate(ranges):
            for r2 in ranges[i + 1 :]:
                ovl = max(0.0, min(r1[1], r2[1]) - max(r1[0], r2[0]))
                d1 = r1[1] - r1[0]
                if d1 > 0:
                    assert ovl / d1 <= 0.5, f"overlapping ranges: {r1}, {r2}"

    def test_match_text_is_actual_transcript_words(self, pain_transcript):
        """D3: text field is what was actually said, not the query echoed back."""
        matches = search_transcript(
            pain_transcript, "this is where you get to the pain"
        )
        # The actual transcript span has 'that' instead of 'the' — the
        # returned text should reflect the actual words.
        top_text = matches[0]["text"]
        # Either 'that' (actual) or starts with 'this is where you get'
        # (the matched window's words).
        assert "this is where you get" in top_text

    def test_empty_query_returns_empty(self, pain_transcript):
        assert search_transcript(pain_transcript, "") == []
        assert search_transcript(pain_transcript, "   ") == []

    def test_empty_transcript_returns_empty(self):
        empty = _transcript([], [])
        assert search_transcript(empty, "anything") == []

    def test_score_is_int_zero_to_hundred(self, pain_transcript):
        matches = search_transcript(pain_transcript, "anything", limit=5)
        for m in matches:
            assert isinstance(m["score"], int)
            assert 0 <= m["score"] <= 100

    def test_source_range_is_two_floats(self, pain_transcript):
        matches = search_transcript(pain_transcript, "pain", limit=3)
        for m in matches:
            r = m["source_range"]
            assert isinstance(r, tuple)
            assert len(r) == 2
            assert isinstance(r[0], (int, float))
            assert isinstance(r[1], (int, float))
            assert r[1] > r[0]

    def test_long_query_suggests_tighter_contiguous_subquery(self):
        """Issue #118: a long literal phrase can score lower than the
        tighter phrase that identifies the same timecode."""
        matches = search_transcript(
            _agent_phrase_transcript(),
            "this is where the agent comes in",
        )
        assert matches
        top = matches[0]
        assert top["score"] < 90
        assert top["suggested_query"] == {
            "query": "where the agent",
            "score": 100,
            "matched_text": "where the agent",
        }


# ---------- exact ----------


class TestExactSearch:
    def test_exact_substring_match(self, pain_transcript):
        matches = search_transcript(
            pain_transcript, "this gets painful", exact=True
        )
        assert len(matches) == 1
        assert matches[0]["score"] == 100
        assert "this gets painful" in matches[0]["text"]

    def test_exact_returns_empty_when_phrase_absent(self, pain_transcript):
        """D2: --exact mode returns empty when the literal phrase isn't there."""
        matches = search_transcript(
            pain_transcript, "phrase that isn't literally there", exact=True
        )
        assert matches == []

    def test_exact_finds_multiple_occurrences(self, pain_transcript):
        # "painful" appears twice in the transcript.
        matches = search_transcript(pain_transcript, "painful", exact=True, limit=5)
        assert len(matches) == 2

    def test_exact_is_case_insensitive(self, pain_transcript):
        upper = search_transcript(pain_transcript, "PAINFUL", exact=True)
        lower = search_transcript(pain_transcript, "painful", exact=True)
        assert len(upper) == len(lower)


# ---------- segment-range (issue #176) ----------


class TestSegmentRange:
    """Issue #176: every match carries a ``segment_range`` widened to the
    enclosing transcript segment(s), so the clip/batch fan-cut path has a
    sentence-level boundary to copy instead of a word-span-narrow one."""

    def test_every_fuzzy_match_has_segment_range(self, pain_transcript):
        matches = search_transcript(pain_transcript, "get to that pain")
        assert matches
        for m in matches:
            assert "segment_range" in m
            sr = m["segment_range"]
            assert isinstance(sr, tuple) and len(sr) == 2

    def test_segment_range_widens_to_enclosing_segment(self, pain_transcript):
        # "get to that pain" lives inside segment [1.2, 3.4]; the word span
        # is narrower, but segment_range should be the full segment.
        matches = search_transcript(pain_transcript, "get to that pain")
        top = matches[0]
        assert top["segment_range"] == (1.2, 3.4)
        # And it's at least as wide as the word-level source_range.
        assert top["segment_range"][0] <= top["source_range"][0]
        assert top["segment_range"][1] >= top["source_range"][1]

    def test_exact_match_has_segment_range(self, pain_transcript):
        matches = search_transcript(pain_transcript, "this gets painful", exact=True)
        assert matches[0]["segment_range"] == (5.0, 7.4)

    def test_cross_segment_match_spans_both_segments(self, pain_transcript):
        # A query straddling the [1.2,3.4] and [4.0,4.5] segments should
        # widen across both: start of the first to end of the last.
        rng = _enclosing_segment_range(3.0, 4.2, pain_transcript["segments"])
        assert rng == (1.2, 4.5)

    def test_falls_back_to_source_range_without_segments(self):
        transcript = _transcript(
            [make_word("solo", 1.0, 1.5), make_word("word", 1.6, 2.0)],
            segments=[],
        )
        matches = search_transcript(transcript, "solo word")
        assert matches
        for m in matches:
            assert m["segment_range"] == m["source_range"]


# ---------- contiguous-match signal (issue #118) ----------


class TestLongestContiguousQueryRun:
    """Issue #118: long fuzzy queries score lower than tighter sub-queries,
    so a borderline score (e.g. 72) reads as 'maybe wrong'. The
    ``contiguous_match`` signal reports the longest verbatim run of the
    query that landed in the window, giving a stable 'yes, this is the
    spot' signal independent of the fuzzy score."""

    def test_full_phrase_present_is_full_coverage(self):
        run = _longest_contiguous_query_run(
            "where the agent", "right where the agent stands"
        )
        assert run is not None
        assert run["text"] == "where the agent"
        assert run["tokens"] == 3
        assert run["query_coverage"] == 1.0

    def test_partial_run_reports_longest_verbatim_slice(self):
        # The canonical #118 case: a 7-token query whose only verbatim
        # landing is the 3-token "where the agent".
        run = _longest_contiguous_query_run(
            "this is where the agent comes in", "tool where the agent is going"
        )
        assert run is not None
        assert run["text"] == "where the agent"
        assert run["tokens"] == 3
        assert run["query_coverage"] == pytest.approx(3 / 7, abs=1e-3)

    def test_no_overlap_returns_none(self):
        assert _longest_contiguous_query_run("alpha beta", "totally other words") is None

    def test_is_case_and_punctuation_insensitive(self):
        run = _longest_contiguous_query_run(
            "Where The Agent", "... where the agent, comes in"
        )
        assert run is not None
        assert run["tokens"] == 3


class TestContiguousMatchField:
    def test_every_fuzzy_match_has_contiguous_match_key(self, pain_transcript):
        matches = search_transcript(pain_transcript, "get to that pain")
        assert matches
        for m in matches:
            assert "contiguous_match" in m  # dict or None

    def test_strong_match_reports_verbatim_run(self, pain_transcript):
        matches = search_transcript(pain_transcript, "get to that pain")
        top = matches[0]
        assert top["contiguous_match"] is not None
        assert "pain" in top["contiguous_match"]["text"]

    def test_long_query_partial_hit_surfaces_contiguous_run(self):
        # Reproduces the #118 friction: long query, the right window only
        # contains a contiguous sub-phrase verbatim.
        words = [
            make_word("tool", 0.0, 0.4),
            make_word("where", 0.5, 0.8),
            make_word("the", 0.9, 1.0),
            make_word("agent", 1.1, 1.5),
            make_word("is", 1.6, 1.7),
            make_word("going", 1.8, 2.2),
        ]
        segments = [make_segment("tool where the agent is going", 0.0, 2.2)]
        transcript = _transcript(words, segments)
        matches = search_transcript(transcript, "this is where the agent comes in")
        top = matches[0]
        assert top["contiguous_match"] is not None
        assert top["contiguous_match"]["text"] == "where the agent"
        assert top["contiguous_match"]["tokens"] == 3

    def test_exact_match_is_full_coverage(self, pain_transcript):
        matches = search_transcript(pain_transcript, "this gets painful", exact=True)
        cm = matches[0]["contiguous_match"]
        assert cm is not None
        assert cm["query_coverage"] == 1.0


# ---------- snap-to-words ----------


class TestSnapToWords:
    """D6 cases — locks the snap-out semantic."""

    def setup_method(self):
        # word_42 [38.2-38.4], word_43 [38.6-38.9], word_47 [41.0-41.3], word_48 [41.5-41.7]
        self.words = [
            make_word("a", 38.2, 38.4),
            make_word("b", 38.6, 38.9),
            make_word("c", 41.0, 41.3),
            make_word("d", 41.5, 41.7),
        ]

    def test_from_mid_word_snaps_to_word_start(self):
        """--from 38.7 (mid 'b') → 38.6 (b.start)."""
        from_s, _ = snap_boundaries_to_words(38.7, 50.0, self.words)
        assert from_s == pytest.approx(38.6)

    def test_to_mid_word_snaps_to_word_end(self):
        """--to 41.1 (mid 'c') → 41.3 (c.end)."""
        _, to_s = snap_boundaries_to_words(0.0, 41.1, self.words)
        assert to_s == pytest.approx(41.3)

    def test_from_in_gap_unchanged(self):
        """--from 38.5 (gap between a and b) → 38.5 (unchanged)."""
        from_s, _ = snap_boundaries_to_words(38.5, 50.0, self.words)
        assert from_s == pytest.approx(38.5)

    def test_to_in_gap_unchanged(self):
        """--to 41.35 (gap between c and d) → 41.35 (unchanged)."""
        _, to_s = snap_boundaries_to_words(0.0, 41.35, self.words)
        assert to_s == pytest.approx(41.35)

    def test_from_at_exact_word_start_unchanged(self):
        """--from 38.6 (exactly b.start) → 38.6 (no snap; not strictly inside)."""
        from_s, _ = snap_boundaries_to_words(38.6, 50.0, self.words)
        assert from_s == pytest.approx(38.6)

    def test_to_at_exact_word_end_unchanged(self):
        """--to 41.3 (exactly c.end) → 41.3 (no snap; not strictly inside)."""
        _, to_s = snap_boundaries_to_words(0.0, 41.3, self.words)
        assert to_s == pytest.approx(41.3)

    def test_both_mid_word_snaps_outward(self):
        """--from 38.7 (mid b), --to 41.1 (mid c) → 38.6, 41.3."""
        from_s, to_s = snap_boundaries_to_words(38.7, 41.1, self.words)
        assert from_s == pytest.approx(38.6)
        assert to_s == pytest.approx(41.3)

    def test_pre_first_word_silence_unchanged(self):
        """--from 0.0 with no word at 0.0 → unchanged."""
        from_s, _ = snap_boundaries_to_words(0.0, 50.0, self.words)
        assert from_s == 0.0

    def test_post_last_word_silence_unchanged(self):
        """--to 50.0 past last word → unchanged."""
        _, to_s = snap_boundaries_to_words(0.0, 50.0, self.words)
        assert to_s == 50.0

    def test_single_word_trim_collapses_to_word_span(self):
        """If --from and --to both fall inside the same word, snap to
        that word's full span."""
        from_s, to_s = snap_boundaries_to_words(38.7, 38.8, self.words)
        # Both inside word b [38.6, 38.9]. From snaps left to 38.6;
        # to snaps right to 38.9.
        assert from_s == pytest.approx(38.6)
        assert to_s == pytest.approx(38.9)
