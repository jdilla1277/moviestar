"""Unit tests for the caption compiler module (pure logic)."""

import pytest

from moviestar.captions import (
    CUE_GAP_SECONDS,
    MAX_CUE_CHARS,
    CaptionParseError,
    apply_caption_rules,
    caption_text_matches_tokens,
    group_words_into_cues,
    map_words_through_windows,
    normalize_cues,
    parse_caption_rule_expression,
    parse_srt,
    parse_vtt,
    validate_caption_overlay_content,
)


def _word(text, start, end):
    return {"text": text, "start_s": start, "end_s": end}


class TestGroupWordsIntoCues:
    def test_short_phrase_becomes_one_cue_with_tokens(self):
        words = [
            _word("Here's", 0.2, 0.5),
            _word("what", 0.5, 0.7),
            _word("I", 0.7, 0.8),
            _word("think.", 0.8, 1.2),
        ]
        cues = group_words_into_cues(words)
        assert len(cues) == 1
        assert cues[0]["text"] == "Here's what I think."
        assert cues[0]["from_s"] == 0.2
        assert cues[0]["to_s"] == 1.2
        assert [t["text"] for t in cues[0]["tokens"]] == [
            "Here's", "what", "I", "think.",
        ]

    def test_char_cap_starts_a_new_cue(self):
        words = [
            _word("word" + str(i), i * 0.3, i * 0.3 + 0.2) for i in range(20)
        ]
        cues = group_words_into_cues(words)
        assert len(cues) > 1
        assert all(len(cue["text"]) <= MAX_CUE_CHARS for cue in cues)

    def test_silence_gap_starts_a_new_cue(self):
        words = [
            _word("before", 0.0, 0.4),
            _word("after", 0.4 + CUE_GAP_SECONDS + 0.1, 2.0),
        ]
        cues = group_words_into_cues(words)
        assert [cue["text"] for cue in cues] == ["before", "after"]

    def test_long_cue_duration_splits(self):
        words = [_word(f"w{i}", i * 1.0, i * 1.0 + 0.9) for i in range(6)]
        cues = group_words_into_cues(words)
        assert len(cues) >= 2

    def test_empty_words_are_skipped(self):
        assert group_words_into_cues([_word("", 0.0, 1.0)]) == []


class TestCaptionRules:
    def test_parses_exact_merge_expression(self):
        rule = parse_caption_rule_expression(
            "ground -truthing=ground-truthing", "merge"
        )
        assert rule == {
            "type": "merge",
            "match": ["ground", "-truthing"],
            "replacement": "ground-truthing",
        }

    def test_rejects_merge_with_only_one_match_token(self):
        with pytest.raises(ValueError, match="at least two"):
            parse_caption_rule_expression("mispelled=misspelled", "merge")

    def test_parses_same_cardinality_phrase_replacement(self):
        rule = parse_caption_rule_expression(
            "Worse with OpenClaw=works with OpenClaw", "replace"
        )
        assert rule == {
            "type": "replace",
            "match": ["Worse", "with", "OpenClaw"],
            "replacement": "works with OpenClaw",
        }

    def test_parses_single_token_split_expression(self):
        rule = parse_caption_rule_expression("code=coding agent", "split")
        assert rule == {
            "type": "split",
            "match": ["code"],
            "replacement": "coding agent",
        }

    @pytest.mark.parametrize(
        "expression",
        ["one two=three four five", "one=two"],
    )
    def test_split_requires_one_match_and_multiple_replacement_tokens(
        self, expression
    ):
        with pytest.raises(ValueError, match="one token into two or more"):
            parse_caption_rule_expression(expression, "split")

    def test_rejects_phrase_replacement_with_different_cardinality(self):
        with pytest.raises(ValueError, match="same number of tokens"):
            parse_caption_rule_expression("one two=three four five", "replace")

    def test_merge_replaces_tokens_and_spans_their_timing(self):
        words = [
            {**_word("ground", 0.2, 0.4), "source": "a"},
            {**_word("-truthing", 0.4, 0.7), "source": "a"},
            {**_word("works", 0.8, 1.0), "source": "a"},
        ]
        rules = [
            {
                "id": "caption_rule_0001",
                "type": "merge",
                "match": ["ground", "-truthing"],
                "replacement": "ground-truthing",
            }
        ]

        transformed, report = apply_caption_rules(words, rules)

        assert [word["text"] for word in transformed] == [
            "ground-truthing",
            "works",
        ]
        assert transformed[0]["start_s"] == pytest.approx(0.2)
        assert transformed[0]["end_s"] == pytest.approx(0.7)
        assert report == {
            "configured_count": 1,
            "applied_rules_count": 1,
            "applications_count": 1,
            "applied": [
                {"id": "caption_rule_0001", "applications_count": 1}
            ],
        }

    def test_replace_is_exact_and_counts_repeated_applications(self):
        words = [
            _word("mispelled", 0.0, 0.2),
            _word("mispelled.", 0.3, 0.5),
            _word("mispelled", 0.6, 0.8),
        ]
        rules = [
            {
                "id": "caption_rule_0001",
                "type": "replace",
                "match": ["mispelled"],
                "replacement": "misspelled",
            }
        ]

        transformed, report = apply_caption_rules(words, rules)

        assert [word["text"] for word in transformed] == [
            "misspelled",
            "mispelled.",
            "misspelled",
        ]
        assert report["applied_rules_count"] == 1
        assert report["applications_count"] == 2

    def test_split_interpolates_result_and_source_timing(self):
        words = [
            {
                **_word("code", 1.0, 1.6),
                "source_start_s": 10.0,
                "source_end_s": 10.6,
                "source": "camera_a",
                "scene": "intro",
                "speaker": "host",
                "confidence": 0.87,
            }
        ]
        rules = [
            {
                "id": "caption_rule_0001",
                "type": "split",
                "match": ["code"],
                "replacement": "coding agent",
            }
        ]

        transformed, report = apply_caption_rules(words, rules)

        assert [word["text"] for word in transformed] == ["coding", "agent"]
        assert [
            (word["start_s"], word["end_s"])
            for word in transformed
        ] == pytest.approx([(1.0, 1.3), (1.3, 1.6)])
        assert [word["source_start_s"] for word in transformed] == pytest.approx(
            [10.0, 10.3]
        )
        assert [word["source_end_s"] for word in transformed] == pytest.approx(
            [10.3, 10.6]
        )
        assert [word["source_token_start_s"] for word in transformed] == [
            10.0,
            10.0,
        ]
        for word in transformed:
            assert word["source"] == "camera_a"
            assert word["scene"] == "intro"
            assert word["speaker"] == "host"
            assert word["confidence"] == pytest.approx(0.87)
        assert report["applied"] == [
            {"id": "caption_rule_0001", "applications_count": 1}
        ]

        retimed_words = [{**words[0], "start_s": 3.0, "end_s": 3.3}]
        retimed, _retimed_report = apply_caption_rules(retimed_words, rules)
        assert [word["source_start_s"] for word in retimed] == pytest.approx(
            [10.0, 10.3]
        )
        assert [
            (word["start_s"], word["end_s"]) for word in retimed
        ] == pytest.approx([(3.0, 3.15), (3.15, 3.3)])

    def test_split_children_have_distinct_break_anchors(self):
        from moviestar.captions import apply_break_edits

        words, _report = apply_caption_rules(
            [
                {
                    **_word("code", 1.0, 1.6),
                    "source_start_s": 10.0,
                    "source_end_s": 10.6,
                    "source": "camera_a",
                }
            ],
            [
                {
                    "id": "caption_rule_0001",
                    "type": "split",
                    "match": ["code"],
                    "replacement": "coding agent",
                }
            ],
        )
        cues = group_words_into_cues(words)

        split = apply_break_edits(
            cues,
            [{"op": "break", "source": "camera_a", "source_time_s": 10.3}],
        )

        assert [cue["text"] for cue in split] == ["coding", "agent"]

    @pytest.mark.parametrize(
        ("match", "replacement"),
        [
            (["Worse", "with", "OpenClaw"], "works with OpenClaw"),
            (["your", "code", "engagement"], "your coding agent"),
            (["build", "my", "hand"], "built by hand"),
        ],
    )
    def test_phrase_replace_changes_only_corresponding_token_text(
        self, match, replacement
    ):
        words = [
            {
                **_word(text, index * 0.2, index * 0.2 + 0.15),
                "source_start_s": 10.0 + index * 0.2,
                "source": "camera_a",
                "scene": "intro",
                "speaker": "host",
                "confidence": 0.9 - index * 0.1,
            }
            for index, text in enumerate(match)
        ]
        rules = [
            {
                "id": "caption_rule_0001",
                "type": "replace",
                "match": match,
                "replacement": replacement,
            }
        ]

        transformed, report = apply_caption_rules(words, rules)

        assert [word["text"] for word in transformed] == replacement.split()
        for original, replaced in zip(words, transformed):
            assert {
                key: value
                for key, value in replaced.items()
                if key != "text"
            } == {
                key: value
                for key, value in original.items()
                if key != "text"
            }
        assert report["applications_count"] == 1

    def test_rule_does_not_cross_source_boundary(self):
        words = [
            {**_word("ground", 0.0, 0.2), "source": "a"},
            {**_word("-truthing", 0.2, 0.4), "source": "b"},
        ]
        rules = [
            {
                "id": "caption_rule_0001",
                "type": "merge",
                "match": ["ground", "-truthing"],
                "replacement": "ground-truthing",
            }
        ]

        transformed, report = apply_caption_rules(words, rules)

        assert [word["text"] for word in transformed] == [
            "ground",
            "-truthing",
        ]
        assert report["applications_count"] == 0

    @pytest.mark.parametrize("boundary", ["source", "speaker"])
    def test_phrase_replace_does_not_cross_hard_boundary(self, boundary):
        words = [
            {**_word("your", 0.0, 0.2), "source": "a", "speaker": "host"},
            {**_word("code", 0.2, 0.4), "source": "a", "speaker": "host"},
            {**_word("engagement", 0.4, 0.6), "source": "a", "speaker": "host"},
        ]
        words[1][boundary] = "other"
        rules = [
            {
                "id": "caption_rule_0001",
                "type": "replace",
                "match": ["your", "code", "engagement"],
                "replacement": "your coding agent",
            }
        ]

        transformed, report = apply_caption_rules(words, rules)

        assert [word["text"] for word in transformed] == [
            "your", "code", "engagement"
        ]
        assert report["applications_count"] == 0


class TestMapWordsThroughWindows:
    def test_maps_source_time_to_result_time(self):
        windows = [
            {"source": "a", "source_from": 10.0, "source_to": 12.0,
             "result_from": 0.0},
        ]
        mapped, unmapped = map_words_through_windows(
            {"a": [_word("hello", 10.5, 11.0)]}, windows
        )
        assert mapped == [
            {
                "text": "hello", "start_s": 0.5, "end_s": 1.0,
                "source_start_s": 10.5, "source_end_s": 11.0, "source": "a",
            }
        ]
        assert unmapped == []

    def test_word_outside_windows_is_reported_unmapped(self):
        windows = [
            {"source": "a", "source_from": 0.0, "source_to": 1.0,
             "result_from": 0.0},
        ]
        mapped, unmapped = map_words_through_windows(
            {"a": [_word("in", 0.2, 0.6), _word("out", 5.0, 5.5)]}, windows
        )
        assert [w["text"] for w in mapped] == ["in"]
        assert unmapped[0]["source"] == "a"
        assert unmapped[0]["words"] == 1

    def test_midpoint_rule_assigns_boundary_word_once(self):
        windows = [
            {"source": "a", "source_from": 0.0, "source_to": 1.0,
             "result_from": 0.0},
            {"source": "a", "source_from": 1.0, "source_to": 2.0,
             "result_from": 1.0},
        ]
        mapped, unmapped = map_words_through_windows(
            {"a": [_word("edge", 0.9, 1.1)]}, windows
        )
        assert len(mapped) == 1
        assert unmapped == []

    def test_second_window_offsets_result_time(self):
        windows = [
            {"source": "a", "source_from": 0.0, "source_to": 2.0,
             "result_from": 0.0},
            {"source": "b", "source_from": 30.0, "source_to": 32.0,
             "result_from": 2.0},
        ]
        mapped, _ = map_words_through_windows(
            {"b": [_word("later", 30.5, 31.0)]}, windows
        )
        assert mapped == [
            {
                "text": "later", "start_s": 2.5, "end_s": 3.0,
                "source_start_s": 30.5, "source_end_s": 31.0, "source": "b",
            }
        ]

    def test_paced_window_scales_word_times(self):
        windows = [
            {
                "source": "a",
                "source_from": 10.0,
                "source_to": 11.0,
                "result_from": 2.0,
                "speed": 5.0,
            },
        ]
        mapped, unmapped = map_words_through_windows(
            {"a": [_word("fast", 10.25, 10.75)]}, windows
        )
        assert mapped == [
            {
                "text": "fast", "start_s": 2.05, "end_s": 2.15,
                "source_start_s": 10.25, "source_end_s": 10.75,
                "source": "a",
            }
        ]
        assert unmapped == []

    def test_source_change_starts_new_cue(self):
        words = [
            {"text": "holden", "start_s": 0.0, "end_s": 0.3, "source": "holden"},
            {"text": "jdilla", "start_s": 0.3, "end_s": 0.6, "source": "jdilla"},
        ]
        cues = group_words_into_cues(words)
        assert [cue["text"] for cue in cues] == ["holden", "jdilla"]
        assert [cue["source"] for cue in cues] == ["holden", "jdilla"]
        assert cues[0]["tokens"][0]["source"] == "holden"


class TestParseSrt:
    def test_parses_cues_and_preserves_line_breaks(self):
        cues = parse_srt(
            "1\n00:00:00,200 --> 00:00:01,100\nWelcome back.\n\n"
            "2\n00:00:01,300 --> 00:00:02,000\nTwo\nlines.\n"
        )
        assert len(cues) == 2
        assert cues[0]["from_s"] == pytest.approx(0.2)
        assert cues[0]["to_s"] == pytest.approx(1.1)
        assert cues[1]["text"] == "Two\nlines."

    def test_accepts_dot_millisecond_separator(self):
        cues = parse_srt("1\n00:00:00.200 --> 00:00:01.100\nHi.\n")
        assert cues[0]["from_s"] == pytest.approx(0.2)

    def test_block_without_timing_line_raises(self):
        with pytest.raises(CaptionParseError):
            parse_srt("garbage without any timing")

    def test_empty_content_raises(self):
        with pytest.raises(CaptionParseError):
            parse_srt("")


class TestParseVtt:
    def test_requires_webvtt_header(self):
        with pytest.raises(CaptionParseError):
            parse_vtt("1\n00:00:00.200 --> 00:00:01.100\nHi.\n")

    def test_parses_cues_and_strips_tags(self):
        cues = parse_vtt(
            "WEBVTT\n\n"
            "NOTE this is a comment\n\n"
            "intro\n00:00.200 --> 00:01.100 position:50%\n"
            "<c.yellow>Hello</c> <b>there</b>.\n"
        )
        assert len(cues) == 1
        assert cues[0]["text"] == "Hello there."
        assert cues[0]["from_s"] == pytest.approx(0.2)

    def test_accepts_hour_timestamps(self):
        cues = parse_vtt(
            "WEBVTT\n\n01:00:00.000 --> 01:00:01.000\nLate cue.\n"
        )
        assert cues[0]["from_s"] == pytest.approx(3600.0)


class TestNormalizeCues:
    def test_reversed_cue_timing_raises(self):
        with pytest.raises(CaptionParseError):
            normalize_cues([{"from_s": 2.0, "to_s": 1.0, "text": "bad"}])

    def test_out_of_order_cues_resorted_with_note(self):
        cues, notes = normalize_cues(
            [
                {"from_s": 2.0, "to_s": 3.0, "text": "b"},
                {"from_s": 0.0, "to_s": 1.0, "text": "a"},
            ]
        )
        assert [c["text"] for c in cues] == ["a", "b"]
        assert notes and "re-sorted" in notes[0]

    def test_ordered_cues_pass_without_notes(self):
        cues, notes = normalize_cues(
            [
                {"from_s": 0.0, "to_s": 1.0, "text": "a"},
                {"from_s": 1.0, "to_s": 2.0, "text": "b"},
            ]
        )
        assert len(cues) == 2
        assert notes == []


class TestCaptionOverlayContentValidation:
    def test_text_token_drift_compares_words_not_just_count(self):
        tokens = [
            {"text": "old", "from": "0:00:00.100", "to": "0:00:00.300"},
            {"text": "words", "from": "0:00:00.300", "to": "0:00:00.600"},
        ]
        assert caption_text_matches_tokens("old words", tokens)
        assert not caption_text_matches_tokens("new words", tokens)

    def test_reports_zero_duration_tokens_and_text_drift(self):
        issues = validate_caption_overlay_content(
            [
                {
                    "id": "cap_0001",
                    "kind": "caption",
                    "text": "new words",
                    "tokens": [
                        {
                            "text": "old",
                            "from": "0:00:00.100",
                            "to": "0:00:00.100",
                        },
                        {
                            "text": "words",
                            "from": "0:00:00.300",
                            "to": "0:00:00.600",
                        },
                    ],
                }
            ]
        )
        assert [issue["code"] for issue in issues] == [
            "captions_zero_duration_tokens",
            "captions_token_text_drift",
        ]
        assert issues[0]["token_indexes"] == [0]


class TestRecipeEditApplication:
    """Pure-layer semantics of word-anchored recipe edits, including
    the dormant contract: an edit whose anchor word is not in the
    derivation is skipped, never an error."""

    def _words(self):
        return [
            {"text": "alpha", "start_s": 0.0, "end_s": 0.4,
             "source_start_s": 0.0, "source": "s"},
            {"text": "beta", "start_s": 0.5, "end_s": 0.9,
             "source_start_s": 0.5, "source": "s"},
            {"text": "gamma", "start_s": 1.0, "end_s": 1.4,
             "source_start_s": 1.0, "source": "s"},
        ]

    def test_suppressions_drop_words_by_source_time(self):
        from moviestar.captions import apply_suppressions

        out = apply_suppressions(
            {"s": self._words()},
            [{"op": "suppress",
              "spans": [{"source": "s", "from_s": 0.45, "to_s": 0.55}]}],
        )
        assert [w["text"] for w in out["s"]] == ["alpha", "gamma"]

    def test_break_splits_cue_at_anchor(self):
        from moviestar.captions import apply_break_edits, group_words_into_cues

        cues = group_words_into_cues(self._words())
        assert len(cues) == 1
        out = apply_break_edits(
            cues, [{"op": "break", "source": "s", "source_time_s": 1.0}]
        )
        assert [c["text"] for c in out] == ["alpha beta", "gamma"]

    def test_break_with_missing_anchor_is_dormant(self):
        from moviestar.captions import apply_break_edits, group_words_into_cues

        cues = group_words_into_cues(self._words())
        out = apply_break_edits(
            cues, [{"op": "break", "source": "s", "source_time_s": 9.9}]
        )
        assert [c["text"] for c in out] == ["alpha beta gamma"]

    def test_join_respects_cue_limits(self):
        from moviestar.captions import apply_join_edits

        long_left = {
            "text": "x" * 30, "from_s": 0.0, "to_s": 1.0, "source": "s",
            "tokens": [{"text": "x" * 30, "from_s": 0.0, "to_s": 1.0,
                        "source": "s", "source_start_s": 0.0}],
        }
        long_right = {
            "text": "y" * 30, "from_s": 1.1, "to_s": 2.0, "source": "s",
            "tokens": [{"text": "y" * 30, "from_s": 1.1, "to_s": 2.0,
                        "source": "s", "source_start_s": 1.1}],
        }
        out = apply_join_edits(
            [dict(long_left), dict(long_right)],
            [{"op": "join", "source": "s", "source_time_s": 0.0}],
        )
        # 61 chars > 42: the join is skipped for this derivation.
        assert len(out) == 2


class TestSceneBoundaryGrouping:
    """Cues never cross scene boundaries (#351's hard requirement).

    Two adjacent scenes routed to the same audio source used to merge
    into one cue because grouping only split on source change — the
    original provenance-loss repro behind the resolved-project
    compiler proposal."""

    def _word(self, text, start, end, scene):
        return {
            "text": text, "start_s": start, "end_s": end,
            "source_start_s": start, "source": "s", "scene": scene,
        }

    def test_scene_change_is_a_hard_cue_boundary(self):
        from moviestar.captions import group_words_into_cues

        words = [
            self._word("first", 0.0, 0.4, "intro"),
            self._word("scene", 0.5, 0.9, "intro"),
            self._word("second", 1.0, 1.4, "convo"),
            self._word("scene", 1.5, 1.9, "convo"),
        ]
        cues = group_words_into_cues(words)
        assert [c["text"] for c in cues] == ["first scene", "second scene"]

    def test_speaker_change_is_a_hard_cue_boundary(self):
        from moviestar.captions import group_words_into_cues

        words = [
            {"text": "hey", "start_s": 0.0, "end_s": 0.3,
             "source_start_s": 0.0, "source": "s", "speaker": "a"},
            {"text": "there", "start_s": 0.4, "end_s": 0.7,
             "source_start_s": 0.4, "source": "s", "speaker": "b"},
        ]
        cues = group_words_into_cues(words)
        assert [c["text"] for c in cues] == ["hey", "there"]


class TestOrphanRebalance:
    """#351: single-word trailing cues rebalance into their neighbor
    when limits permit; hard boundaries and budgets keep them."""

    def _cue(self, words, scene=None, source="s"):
        tokens = [
            {"text": t, "from_s": a, "to_s": b,
             "source_start_s": a, "source": source,
             **({"scene": scene} if scene else {})}
            for t, a, b in words
        ]
        return {
            "text": " ".join(t for t, _a, _b in words),
            "from_s": words[0][1],
            "to_s": words[-1][2],
            "source": source,
            **({"scene": scene} if scene else {}),
            "tokens": tokens,
        }

    def test_short_trailing_orphan_merges_into_previous_cue(self):
        from moviestar.captions import rebalance_orphan_cues

        cues = [
            self._cue([("did", 0.0, 0.2), ("you", 0.3, 0.5)]),
            self._cue([("go?", 0.6, 0.9)]),
        ]
        out = rebalance_orphan_cues(cues)
        assert [c["text"] for c in out] == ["did you go?"]
        # Word timing is preserved through the merge.
        assert out[0]["tokens"][-1]["from_s"] == 0.6

    def test_orphan_stays_when_merge_would_exceed_char_budget(self):
        from moviestar.captions import rebalance_orphan_cues

        long_cue = self._cue([("x" * 40, 0.0, 1.0)])
        orphan = self._cue([("pain", 1.1, 1.4)])
        out = rebalance_orphan_cues([long_cue, orphan])
        assert len(out) == 2

    def test_orphan_never_merges_across_a_scene_boundary(self):
        from moviestar.captions import rebalance_orphan_cues

        cues = [
            self._cue([("did", 0.0, 0.2), ("you", 0.3, 0.5)], scene="a"),
            self._cue([("go?", 0.6, 0.9)], scene="b"),
        ]
        out = rebalance_orphan_cues(cues)
        assert len(out) == 2

    def test_orphan_never_merges_across_a_source_boundary(self):
        from moviestar.captions import rebalance_orphan_cues

        cues = [
            self._cue([("did", 0.0, 0.2), ("you", 0.3, 0.5)], source="s1"),
            self._cue([("go?", 0.6, 0.9)], source="s2"),
        ]
        out = rebalance_orphan_cues(cues)
        assert len(out) == 2

    def test_orphan_stays_when_gap_is_a_real_pause(self):
        from moviestar.captions import rebalance_orphan_cues

        cues = [
            self._cue([("did", 0.0, 0.2), ("you", 0.3, 0.5)]),
            self._cue([("go?", 2.0, 2.3)]),
        ]
        out = rebalance_orphan_cues(cues)
        assert len(out) == 2
