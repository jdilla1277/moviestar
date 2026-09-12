"""Caption compilers: transcript words and SRT/VTT cues into caption
overlay records.

Captions are generated timed overlays. This module owns the
pure logic: grouping transcript words into readable cues, mapping
source-time words into composition result-time through audio windows,
and parsing caption files. The CLI layer builds the windows (it knows
the composition shapes) and persists the resulting overlay records.

Cue policy (deterministic, documented in envelopes): a new cue starts
when adding the next word would exceed ``MAX_CUE_CHARS`` characters,
the silence gap since the previous word exceeds ``CUE_GAP_SECONDS``,
or the cue would run longer than ``MAX_CUE_SECONDS``.
"""

from __future__ import annotations

import copy
import re

from .timecodes import parse_timecode

MAX_CUE_CHARS = 42
MAX_CUE_SECONDS = 3.5
CUE_GAP_SECONDS = 0.8

CUE_POLICY = {
    "max_chars": MAX_CUE_CHARS,
    "max_seconds": MAX_CUE_SECONDS,
    "gap_seconds": CUE_GAP_SECONDS,
}

CAPTION_RULE_TYPES = {"merge", "replace", "split"}


def parse_caption_rule_expression(expression: str, rule_type: str) -> dict:
    """Parse ``MATCH=REPLACEMENT`` into one exact caption token rule.

    Replacements preserve cardinality so each new word inherits exactly one
    matched token's timing and provenance. Merges produce one token spanning
    the full matched range. Splits divide one token into two or more tokens.
    """
    if rule_type not in CAPTION_RULE_TYPES:
        raise ValueError(
            f"Unknown caption rule type {rule_type!r}; expected merge, replace, "
            "or split."
        )
    match_text, separator, replacement = str(expression).partition("=")
    match = match_text.split()
    replacement_tokens = replacement.split()
    if not separator or not match or not replacement_tokens:
        raise ValueError(
            "Caption rules use MATCH=REPLACEMENT with non-empty text on "
            "both sides."
        )
    if rule_type == "merge":
        if len(match) < 2:
            raise ValueError("A merge rule must match at least two tokens.")
        if len(replacement_tokens) != 1:
            raise ValueError(
                "A merge rule must produce exactly one replacement token."
            )
    elif rule_type == "split":
        if len(match) != 1 or len(replacement_tokens) < 2:
            raise ValueError(
                "A split rule must expand one token into two or more "
                "replacement tokens."
            )
    elif len(match) != len(replacement_tokens):
        raise ValueError(
            "A replace rule must have the same number of tokens on both "
            f"sides (got {len(match)} match and "
            f"{len(replacement_tokens)} replacement tokens)."
        )
    return {
        "type": rule_type,
        "match": match,
        "replacement": " ".join(replacement_tokens),
    }


def validate_caption_rules(rules) -> None:
    """Validate the optional project-level caption rule array."""
    if not isinstance(rules, list):
        raise ValueError("project.caption_rules must be an array.")
    seen_ids: set[str] = set()
    for index, rule in enumerate(rules):
        where = f"project.caption_rules[{index}]"
        if not isinstance(rule, dict):
            raise ValueError(f"{where} must be an object.")
        for field in ("id", "type", "match", "replacement"):
            if field not in rule:
                raise ValueError(f"{where} is missing required field {field!r}.")
        rule_id = rule["id"]
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError(f"{where}.id must be a non-empty string.")
        if rule_id in seen_ids:
            raise ValueError(f"project.caption_rules has duplicate id {rule_id!r}.")
        seen_ids.add(rule_id)
        rule_type = rule["type"]
        if rule_type not in CAPTION_RULE_TYPES:
            raise ValueError(f"{where}.type must be merge, replace, or split.")
        match = rule["match"]
        if (
            not isinstance(match, list)
            or not match
            or any(not isinstance(token, str) or not token for token in match)
        ):
            raise ValueError(f"{where}.match must be a non-empty string array.")
        replacement = rule["replacement"]
        replacement_tokens = (
            replacement.split() if isinstance(replacement, str) else []
        )
        if not replacement_tokens:
            raise ValueError(f"{where}.replacement must contain text.")
        if rule_type == "merge":
            if len(match) < 2:
                raise ValueError(
                    f"{where}: a merge rule must match at least two tokens."
                )
            if len(replacement_tokens) != 1:
                raise ValueError(
                    f"{where}: a merge rule must produce exactly one token."
                )
        elif rule_type == "split":
            if len(match) != 1 or len(replacement_tokens) < 2:
                raise ValueError(
                    f"{where}: a split rule must expand one token into two "
                    "or more replacement tokens."
                )
        elif len(match) != len(replacement_tokens):
            raise ValueError(
                f"{where}: a replace rule must have the same number of "
                "tokens on both sides."
            )


def next_caption_rule_id(rules: list[dict]) -> str:
    """Return the next stable ``caption_rule_NNNN`` project rule ID."""
    highest = 0
    for rule in rules:
        rule_id = rule.get("id") if isinstance(rule, dict) else None
        if not isinstance(rule_id, str) or not rule_id.startswith("caption_rule_"):
            continue
        suffix = rule_id.removeprefix("caption_rule_")
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"caption_rule_{highest + 1:04d}"


def apply_caption_rules(
    words: list[dict], rules: list[dict]
) -> tuple[list[dict], dict]:
    """Apply exact project rules to result-time transcript words.

    Rules run in stored order and each rule scans left-to-right using
    non-overlapping matches. A match cannot cross a source or speaker boundary.
    Split timing is interpolated evenly in both result and source time. Each
    child keeps the parent transcript token's start as its suppression identity.
    """
    validate_caption_rules(rules)
    transformed = copy.deepcopy(words)
    applied: list[dict] = []
    applications_count = 0

    for rule in rules:
        match = rule["match"]
        match_len = len(match)
        replacement_tokens = rule["replacement"].split()
        rule_applications = 0
        output: list[dict] = []
        index = 0
        while index < len(transformed):
            candidate = transformed[index : index + match_len]
            same_boundary = bool(candidate) and all(
                word.get("source") == candidate[0].get("source")
                and word.get("speaker") == candidate[0].get("speaker")
                for word in candidate
            )
            if (
                len(candidate) == match_len
                and same_boundary
                and [word.get("text") for word in candidate] == match
            ):
                if rule["type"] == "merge":
                    replacement = copy.deepcopy(candidate[0])
                    replacement["text"] = replacement_tokens[0]
                    replacement["end_s"] = candidate[-1]["end_s"]
                    if candidate[-1].get("source_end_s") is not None:
                        replacement["source_end_s"] = candidate[-1][
                            "source_end_s"
                        ]
                    output.append(replacement)
                elif rule["type"] == "split":
                    original = candidate[0]
                    count = len(replacement_tokens)
                    result_start = float(original["start_s"])
                    result_end = float(original["end_s"])
                    source_start = original.get("source_start_s")
                    source_end = original.get("source_end_s")
                    source_token_start = original.get(
                        "source_token_start_s", source_start
                    )
                    for split_index, replacement_text in enumerate(
                        replacement_tokens
                    ):
                        replacement = copy.deepcopy(original)
                        replacement["text"] = replacement_text
                        replacement["start_s"] = result_start + (
                            (result_end - result_start) * split_index / count
                        )
                        replacement["end_s"] = result_start + (
                            (result_end - result_start)
                            * (split_index + 1)
                            / count
                        )
                        if source_start is not None and source_end is not None:
                            replacement["source_start_s"] = float(
                                source_start
                            ) + (
                                (float(source_end) - float(source_start))
                                * split_index
                                / count
                            )
                            replacement["source_end_s"] = float(source_start) + (
                                (float(source_end) - float(source_start))
                                * (split_index + 1)
                                / count
                            )
                            replacement["source_token_start_s"] = (
                                source_token_start
                            )
                        output.append(replacement)
                else:
                    for original, replacement_text in zip(
                        candidate, replacement_tokens
                    ):
                        replacement = copy.deepcopy(original)
                        replacement["text"] = replacement_text
                        output.append(replacement)
                index += match_len
                rule_applications += 1
                continue
            output.append(copy.deepcopy(transformed[index]))
            index += 1
        transformed = output
        if rule_applications:
            applied.append(
                {
                    "id": rule["id"],
                    "applications_count": rule_applications,
                }
            )
            applications_count += rule_applications

    return transformed, {
        "configured_count": len(rules),
        "applied_rules_count": len(applied),
        "applications_count": applications_count,
        "applied": applied,
    }


def _single_line_preview(text: str, limit: int = 80) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def normalized_caption_token_text(tokens: list[dict]) -> str:
    """Return the caption text implied by token text, with whitespace
    collapsed the same way generated cues do."""
    return " ".join(
        part
        for part in (
            " ".join(str(token.get("text") or "").split()) for token in tokens
        )
        if part
    )


def caption_text_matches_tokens(text: str, tokens: list[dict]) -> bool:
    """Whether stored caption text still matches its word-token text."""
    return " ".join(str(text).split()) == normalized_caption_token_text(tokens)


def validate_caption_overlay_content(records: list[dict]) -> list[dict]:
    """Find non-fatal caption/token issues in canonical overlay records.

    Returns issue dicts with ``code``, ``message``, ``overlay_id`` and
    optional details. These are warnings at authoring/import time because
    base caption rendering can still proceed, but spoken-word highlighting
    would flicker, skip words, or show stale token text.
    """
    issues: list[dict] = []
    for record in records:
        tokens = record.get("tokens")
        if not tokens or not isinstance(tokens, list):
            continue
        oid = record.get("id") or "<unknown>"
        bad_indexes = []
        for index, token in enumerate(tokens):
            if not isinstance(token, dict):
                continue
            try:
                t_from = parse_timecode(token["from"])
                t_to = parse_timecode(token["to"])
            except (KeyError, TypeError, ValueError):
                continue
            if t_to <= t_from:
                bad_indexes.append(index)
        if bad_indexes:
            count = len(bad_indexes)
            issues.append(
                {
                    "code": "captions_zero_duration_tokens",
                    "overlay_id": oid,
                    "token_indexes": bad_indexes,
                    "token_count": count,
                    "message": (
                        f"{oid}: {count} word token(s) have zero or negative "
                        "duration; spoken-word highlighting may flicker or "
                        "skip those words. Regenerate captions or fix token "
                        "from/to values via overlays dump/set."
                    ),
                }
            )
        if not caption_text_matches_tokens(record.get("text", ""), tokens):
            token_text = normalized_caption_token_text(tokens)
            issues.append(
                {
                    "code": "captions_token_text_drift",
                    "overlay_id": oid,
                    "token_text": token_text,
                    "message": (
                        f"{oid}: caption text does not match its word tokens; "
                        f"tokens read {_single_line_preview(token_text)!r}. "
                        "Spoken-word highlighting may show stale words. "
                        "Regenerate captions or update tokens via overlays "
                        "dump/set."
                    ),
                }
            )
    return issues

_SRT_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)
# VTT allows MM:SS.mmm without the hour field.
_VTT_TIME_RE = re.compile(
    r"(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{3})\s*-->\s*"
    r"(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{3})"
)
_VTT_TAG_RE = re.compile(r"<[^>]*>")


class CaptionParseError(ValueError):
    """Raised when a caption file can't be parsed into cues."""


def map_words_through_windows(
    words_by_source: dict[str, list[dict]],
    windows: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Map source-time transcript words into result-time.

    ``windows`` describe what audio is heard when:
    ``{"source", "source_from", "source_to", "result_from"}`` — the
    source range plays starting at ``result_from``, half-open. Words
    are assigned to a window when the word's midpoint falls inside the
    window's source range, so boundary words land in exactly one
    window.

    Returns ``(mapped_words, unmapped_spans)``. Mapped words carry result-time
    ``start_s``/``end_s`` plus source-time ``source_start_s``/``source_end_s``
    anchors and are sorted. Unmapped spans report transcript ranges no window
    plays, per source.
    """
    mapped: list[dict] = []
    used_by_source: dict[str, list[tuple[float, float]]] = {}
    for window in windows:
        source = window["source"]
        s_from = float(window["source_from"])
        s_to = float(window["source_to"])
        r_from = float(window["result_from"])
        speed = float(window.get("speed", 1.0))
        used_by_source.setdefault(source, []).append((s_from, s_to))
        for word in words_by_source.get(source, []):
            mid = (word["start_s"] + word["end_s"]) / 2
            if not (s_from <= mid < s_to):
                continue
            start = max(word["start_s"], s_from)
            end = min(word["end_s"], s_to)
            mapped.append(
                {
                    "text": word["text"],
                    "start_s": round(r_from + (start - s_from) / speed, 3),
                    "end_s": round(r_from + (end - s_from) / speed, 3),
                    # Source-time anchor: word-anchored recipe edits
                    # (break/join/suppress) key on this, so they follow
                    # the word through every timeline change.
                    "source_start_s": round(word["start_s"], 3),
                    "source_end_s": round(word["end_s"], 3),
                    **(
                        {"scene": window["scene"]}
                        if window.get("scene") is not None
                        else {}
                    ),
                    "source": source,
                    **(
                        {"speaker": word["speaker"]}
                        if word.get("speaker") is not None
                        else {}
                    ),
                }
            )
    mapped.sort(key=lambda w: (w["start_s"], w["end_s"]))

    unmapped: list[dict] = []
    for source, words in words_by_source.items():
        ranges = sorted(used_by_source.get(source, []))
        missed = [
            w
            for w in words
            if not any(
                lo <= (w["start_s"] + w["end_s"]) / 2 < hi for lo, hi in ranges
            )
        ]
        if missed:
            unmapped.append(
                {
                    "source": source,
                    "words": len(missed),
                    "source_span": {
                        "from_s": round(missed[0]["start_s"], 3),
                        "to_s": round(missed[-1]["end_s"], 3),
                    },
                }
            )
    return mapped, unmapped


def group_words_into_cues(words: list[dict]) -> list[dict]:
    """Group result-time words into caption cues per ``CUE_POLICY``.

    Returns ``{"text", "from_s", "to_s", "tokens"}`` dicts; tokens
    preserve per-word timing for spoken-word highlighting.
    """
    cues: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        cues.append(
            {
                "text": " ".join(w["text"] for w in current),
                "from_s": current[0]["start_s"],
                "to_s": current[-1]["end_s"],
                **(
                    {"source": current[0]["source"]}
                    if current[0].get("source") is not None
                    else {}
                ),
                **(
                    {"scene": current[0]["scene"]}
                    if current[0].get("scene") is not None
                    else {}
                ),
                "tokens": [
                    {
                        "text": w["text"],
                        "from_s": w["start_s"],
                        "to_s": w["end_s"],
                        **(
                            {"source": w["source"]}
                            if w.get("source") is not None
                            else {}
                        ),
                        **(
                            {"source_start_s": w["source_start_s"]}
                            if w.get("source_start_s") is not None
                            else {}
                        ),
                        **(
                            {"source_token_start_s": w["source_token_start_s"]}
                            if w.get("source_token_start_s") is not None
                            else {}
                        ),
                        **(
                            {"scene": w["scene"]}
                            if w.get("scene") is not None
                            else {}
                        ),
                    }
                    for w in current
                ],
            }
        )
        current.clear()

    for word in words:
        if not word["text"]:
            continue
        if current:
            chars = len(" ".join(w["text"] for w in current)) + 1 + len(
                word["text"]
            )
            gap = word["start_s"] - current[-1]["end_s"]
            duration = word["end_s"] - current[0]["start_s"]
            if (
                chars > MAX_CUE_CHARS
                or gap > CUE_GAP_SECONDS
                or duration > MAX_CUE_SECONDS
                or word.get("source") != current[-1].get("source")
                # Scene and speaker changes are hard boundaries (#351):
                # a cue never spans two scenes or two speakers, even
                # when the audio source is the same.
                or word.get("scene") != current[-1].get("scene")
                or word.get("speaker") != current[-1].get("speaker")
            ):
                flush()
        current.append(word)
    flush()
    return [cue for cue in cues if cue["to_s"] > cue["from_s"]]


def segments_to_cues(segments: list[dict]) -> list[dict]:
    """Fallback when the transcript has segment timing only: one cue
    per transcript segment, no tokens (no word timing to highlight)."""
    cues = []
    for seg in segments:
        text = (seg["text"] or "").strip()
        if not text or seg["to_s"] <= seg["from_s"]:
            continue
        cues.append(
            {
                "text": text,
                "from_s": seg["from_s"],
                "to_s": seg["to_s"],
                "tokens": None,
            }
        )
    return cues


def _srt_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(content: str) -> list[dict]:
    """Parse SRT content into ``{"from_s", "to_s", "text"}`` cues.
    Line breaks inside a cue are preserved as ``\\n`` in the text."""
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip()):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        time_index = next(
            (i for i, line in enumerate(lines) if _SRT_TIME_RE.search(line)),
            None,
        )
        if time_index is None:
            raise CaptionParseError(
                f"SRT block without a 'HH:MM:SS,mmm --> HH:MM:SS,mmm' "
                f"timing line: {lines[0][:60]!r}"
            )
        match = _SRT_TIME_RE.search(lines[time_index])
        text = "\n".join(lines[time_index + 1:]).strip()
        if not text:
            continue
        cues.append(
            {
                "from_s": _srt_seconds(*match.groups()[0:4]),
                "to_s": _srt_seconds(*match.groups()[4:8]),
                "text": text,
            }
        )
    if not cues:
        raise CaptionParseError("No cues found in SRT file.")
    return cues


def parse_vtt(content: str) -> list[dict]:
    """Parse WEBVTT content into cues. NOTE/STYLE/REGION blocks are
    skipped; cue settings after the timing line and inline ``<...>``
    tags are stripped."""
    content = content.replace("\r\n", "\n").lstrip("﻿")
    if not content.lstrip().startswith("WEBVTT"):
        raise CaptionParseError(
            "Not a WEBVTT file (missing 'WEBVTT' header on line 1)."
        )
    cues: list[dict] = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks[1:] if blocks and blocks[0].startswith("WEBVTT") else blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        first = lines[0].strip()
        if first.startswith(("NOTE", "STYLE", "REGION")):
            continue
        time_index = next(
            (i for i, line in enumerate(lines) if _VTT_TIME_RE.search(line)),
            None,
        )
        if time_index is None:
            continue
        match = _VTT_TIME_RE.search(lines[time_index])
        text = "\n".join(lines[time_index + 1:]).strip()
        text = _VTT_TAG_RE.sub("", text).strip()
        if not text:
            continue
        cues.append(
            {
                "from_s": _srt_seconds(*match.groups()[0:4]),
                "to_s": _srt_seconds(*match.groups()[4:8]),
                "text": text,
            }
        )
    if not cues:
        raise CaptionParseError("No cues found in VTT file.")
    return cues


def normalize_cues(cues: list[dict]) -> tuple[list[dict], list[str]]:
    """Validate and normalize parsed cues. Returns (cues, notes).

    - a cue whose end <= start is an error (CaptionParseError)
    - cues out of order are re-sorted, reported as a normalization
    """
    for i, cue in enumerate(cues):
        if cue["to_s"] <= cue["from_s"]:
            raise CaptionParseError(
                f"Cue {i + 1} has non-increasing timing "
                f"({cue['from_s']}s --> {cue['to_s']}s)."
            )
    notes: list[str] = []
    ordered = sorted(cues, key=lambda c: (c["from_s"], c["to_s"]))
    if ordered != cues:
        notes.append(
            "Cue start times were not monotonically increasing; cues "
            "were re-sorted by start time."
        )
    return ordered, notes


# ---------- Recipe edits ----------
#
# Word-anchored edits stored in a caption recipe's `edits` array and
# applied on every derivation. Anchors are source-time word starts
# (`source_start_s`), so edits follow their words through pacing
# changes, reorders, and cuts. An edit whose anchor word is no longer
# derived (cut out, suppressed, outside the captioned range) is
# dormant, not an error: derivation skips it and it re-applies when
# the word returns.

_ANCHOR_EPSILON_S = 0.02


def _matches_anchor(token: dict, source: str | None, time_s: float) -> bool:
    anchor = token.get("source_start_s")
    if anchor is None:
        return False
    if source is not None and token.get("source") != source:
        return False
    return abs(anchor - time_s) <= _ANCHOR_EPSILON_S


def apply_suppressions(
    words_by_source: dict[str, list[dict]], edits: list[dict]
) -> dict[str, list[dict]]:
    """Drop source-time word spans covered by suppress edits.

    Applied before window mapping: a suppressed word is never heard by
    grouping, so cues re-balance around the gap on every derivation.
    Matching keys on the word's source-time start falling inside a
    span, which makes the covered word set exact and deterministic.
    """
    spans_by_source: dict[str, list[tuple[float, float]]] = {}
    for edit in edits:
        if edit.get("op") != "suppress":
            continue
        for span in edit.get("spans", []):
            spans_by_source.setdefault(span["source"], []).append(
                (float(span["from_s"]), float(span["to_s"]))
            )
    if not spans_by_source:
        return words_by_source
    out: dict[str, list[dict]] = {}
    for source, words in words_by_source.items():
        spans = spans_by_source.get(source)
        if not spans:
            out[source] = words
            continue
        out[source] = [
            word
            for word in words
            if not any(
                lo <= float(word["start_s"]) <= hi for lo, hi in spans
            )
        ]
    return out


def apply_break_edits(cues: list[dict], edits: list[dict]) -> list[dict]:
    """Split cues at word-anchored break edits.

    A break means "a new cue starts at this word". Splitting a cue
    recomputes both halves' text and ranges from their tokens; a break
    whose anchor is already a cue start (or missing) is dormant.
    """
    breaks = [edit for edit in edits if edit.get("op") == "break"]
    if not breaks:
        return cues
    for edit in breaks:
        source = edit.get("source")
        time_s = float(edit["source_time_s"])
        for i, cue in enumerate(cues):
            tokens = cue.get("tokens") or []
            split_at = next(
                (
                    j
                    for j, token in enumerate(tokens)
                    if _matches_anchor(token, source, time_s)
                ),
                None,
            )
            if split_at is None or split_at == 0:
                continue
            head, tail = tokens[:split_at], tokens[split_at:]
            def _cue_from(parts: list[dict]) -> dict:
                return {
                    "text": " ".join(t["text"] for t in parts),
                    "from_s": parts[0]["from_s"],
                    "to_s": parts[-1]["to_s"],
                    **(
                        {"source": cue["source"]}
                        if cue.get("source") is not None
                        else {}
                    ),
                    "tokens": parts,
                }
            cues[i : i + 1] = [_cue_from(head), _cue_from(tail)]
            break
    return cues


def apply_join_edits(cues: list[dict], edits: list[dict]) -> list[dict]:
    """Merge adjacent cues at word-anchored join edits.

    A join is anchored at the last word of the earlier cue. Cue limits
    apply on every derivation: a join that would exceed the character
    or duration budget is skipped for that derivation rather than
    producing an unreadable cue.
    """
    joins = [edit for edit in edits if edit.get("op") == "join"]
    if not joins:
        return cues
    for edit in joins:
        source = edit.get("source")
        time_s = float(edit["source_time_s"])
        for i in range(len(cues) - 1):
            left, right = cues[i], cues[i + 1]
            tokens = left.get("tokens") or []
            if not tokens or not _matches_anchor(
                tokens[-1], source, time_s
            ):
                continue
            merged_text = f"{left['text']} {right['text']}"
            merged_span = right["to_s"] - left["from_s"]
            if (
                len(merged_text) > MAX_CUE_CHARS
                or merged_span > MAX_CUE_SECONDS
                or left.get("source") != right.get("source")
            ):
                break
            merged = {
                "text": merged_text,
                "from_s": left["from_s"],
                "to_s": right["to_s"],
                **(
                    {"source": left["source"]}
                    if left.get("source") is not None
                    else {}
                ),
                "tokens": (left.get("tokens") or [])
                + (right.get("tokens") or []),
            }
            cues[i : i + 2] = [merged]
            break
    return cues


def rebalance_orphan_cues(cues: list[dict]) -> list[dict]:
    """Merge single-word orphan cues into the preceding cue (#351).

    Grouping's char budget can strand a lone trailing word ("you.",
    "go?") as its own cue. When the merge fits every cue budget and
    crosses no hard boundary — source, scene, speaker, or a real
    pause — the orphan joins the previous cue with its word timing
    intact. Otherwise it deterministically stays: a kept one-word cue
    is readable; a cue over budget or spanning scenes is not.
    """
    out: list[dict] = []
    for cue in cues:
        prev = out[-1] if out else None
        tokens = cue.get("tokens") or []
        if (
            prev is not None
            and len(tokens) == 1
            and prev.get("tokens")
            and prev.get("source") == cue.get("source")
            and prev.get("scene") == cue.get("scene")
            and cue["from_s"] - prev["to_s"] <= CUE_GAP_SECONDS
            and len(f"{prev['text']} {cue['text']}") <= MAX_CUE_CHARS
            and cue["to_s"] - prev["from_s"] <= MAX_CUE_SECONDS
        ):
            out[-1] = {
                **prev,
                "text": f"{prev['text']} {cue['text']}",
                "to_s": cue["to_s"],
                "tokens": (prev.get("tokens") or []) + tokens,
            }
            continue
        out.append(cue)
    return out
