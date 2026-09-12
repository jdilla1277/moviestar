"""Transcript search for `moviestar find`.

Pure module — no I/O, no Click. Takes a transcript dict (the on-disk
shape from ``transcribe.py``) and a query string; returns a list of
matches with ``text``, ``score``, and ``source_range``. The CLI layer
handles transcript loading, scope filtering, and result-range
translation.

Two modes:

- **Fuzzy (default).** rapidfuzz ``partial_ratio`` over each Whisper
  segment plus adjacent-segment pairs (catches cross-boundary matches).
  Within the top-scoring spans, slide variable-length word windows and
  pick the best by ``ratio``. Always returns up to ``limit`` matches —
  never empty in fuzzy mode (D2).
- **Exact.** Substring search over the concatenated word text. Returns
  empty when the literal phrase isn't in the transcript (the meaningful
  empty case from D2).

Match shape (D3): each match is a contiguous span of real transcript
text, with the actual spoken words in ``text`` (not the query echoed
back). Multiple matches = different spans in the transcript that scored
above noise.

These mode and result-shape guarantees are the public search contract.
"""

from __future__ import annotations

import string

from rapidfuzz import fuzz


def _normalize_token(token: str) -> str:
    """Lowercase + strip surrounding punctuation for verbatim comparison."""
    return token.lower().strip(string.punctuation)


def _longest_contiguous_query_run(query: str, window_text: str) -> dict | None:
    """Longest run of consecutive query tokens appearing verbatim in
    ``window_text``, as ``{"text", "tokens", "query_coverage"}`` — or None
    when no query token lands verbatim (issue #118).

    Comparison is case- and surrounding-punctuation-insensitive. ``text`` is
    drawn from the window's original casing; ``query_coverage`` is the run's
    token count over the total query-token count, so an agent can tell at a
    glance how much of a long query actually landed (a stable signal that
    survives a borderline fuzzy score).
    """
    q_tokens = [t for t in (_normalize_token(t) for t in query.split()) if t]
    w_raw = window_text.split()
    w_tokens = [_normalize_token(t) for t in w_raw]
    if not q_tokens or not w_tokens:
        return None

    # Longest common contiguous run (token-level LCS-substring), tracking the
    # window position so we can reconstruct the original-cased text.
    best_len = 0
    best_w_start = 0
    # dp[j] = length of run ending at query[i], window[j]
    prev = [0] * (len(w_tokens) + 1)
    for i in range(len(q_tokens)):
        cur = [0] * (len(w_tokens) + 1)
        for j in range(len(w_tokens)):
            if q_tokens[i] == w_tokens[j]:
                cur[j + 1] = prev[j] + 1
                if cur[j + 1] > best_len:
                    best_len = cur[j + 1]
                    best_w_start = j + 1 - cur[j + 1]
        prev = cur

    if best_len == 0:
        return None
    return {
        "text": " ".join(w_raw[best_w_start : best_w_start + best_len]),
        "tokens": best_len,
        "query_coverage": round(best_len / len(q_tokens), 4),
    }


# Score thresholds (D7): warn-only, never filter.
WARNING_THRESHOLD = 70
STRONG_WARNING_THRESHOLD = 50

# Issue #118: long literal queries can score lower than a tighter
# contiguous phrase at the same timecode. Keep the suggestion conservative
# so it is a confidence signal, not another fuzzy guess.
SUGGESTION_MAX_SCORE = 89
SUGGESTION_MIN_QUERY_WORDS = 5
SUGGESTION_MIN_PHRASE_WORDS = 3
SUGGESTION_MIN_SCORE = 95
SUGGESTION_MIN_IMPROVEMENT = 10


def search_transcript(
    transcript: dict,
    query: str,
    *,
    exact: bool = False,
    limit: int = 5,
) -> list[dict]:
    """Search the transcript for ``query``. Returns a list of matches.

    Each match: ``{"text": str, "score": int, "source_range": (from, to),
    "segment_range": (from, to), "contiguous_match": dict | None}``.
    ``score`` is 0–100 (rapidfuzz convention). ``source_range`` is the
    word-span range in seconds on the source timeline; ``segment_range``
    widens it to the enclosing transcript segment(s) for sentence-level clip
    boundaries (issue #176). ``contiguous_match`` reports the longest verbatim
    run of the query landing in the match — a stable "right spot" signal that
    survives a borderline fuzzy score (issue #118), or None when nothing
    landed verbatim.

    Fuzzy mode always returns up to ``limit`` matches sorted by score
    descending; never empty unless the transcript itself is empty.
    Exact mode returns 0+ matches (empty when the literal phrase
    isn't present).
    """
    query = (query or "").strip()
    if not query:
        return []

    words = transcript.get("words") or []
    if not words:
        return []

    if exact:
        matches = _search_exact(words, query, limit)
    else:
        matches = _search_fuzzy(transcript, words, query, limit)

    # Issue #176: widen every match to its enclosing transcript segment(s)
    # so the clip/batch fan-cut path has a sentence-level boundary to copy,
    # not just the word-span-narrow source_range. Read-only and additive.
    _attach_segment_ranges(matches, transcript.get("segments") or [])
    return matches


def _attach_segment_ranges(matches: list[dict], segments: list[dict]) -> None:
    """Add a source-time ``segment_range`` to each match in place."""
    for m in matches:
        from_s, to_s = m["source_range"]
        m["segment_range"] = _enclosing_segment_range(from_s, to_s, segments)


def _enclosing_segment_range(
    from_s: float, to_s: float, segments: list[dict]
) -> tuple[float, float]:
    """Source-time range of the transcript segment(s) enclosing ``[from_s, to_s]``.

    Returns the span from the start of the first overlapping segment to the
    end of the last — so a match straddling two segments widens across both.
    Falls back to the input range when the transcript has no segment metadata
    (keeps the field always-present and sensible).
    """
    overlapping = [
        s
        for s in segments
        if s["start"]["seconds"] <= to_s and s["end"]["seconds"] >= from_s
    ]
    if not overlapping:
        return (from_s, to_s)
    return (
        min(s["start"]["seconds"] for s in overlapping),
        max(s["end"]["seconds"] for s in overlapping),
    )


# ---------- exact ----------


def _search_exact(words: list[dict], query: str, limit: int) -> list[dict]:
    """Substring match over the concatenated word text. Returns matches
    whose word-text concatenation contains ``query`` (case-insensitive).
    """
    query_lower = query.lower()

    # Build concatenated text + each word's start offset in that string.
    parts: list[str] = []
    offsets: list[int] = []
    cursor = 0
    for w in words:
        text = w["text"]
        if parts:
            cursor += 1  # space separator
        offsets.append(cursor)
        cursor += len(text)
        parts.append(text)
    full = " ".join(parts).lower()

    matches: list[dict] = []
    start_search = 0
    while len(matches) < limit:
        idx = full.find(query_lower, start_search)
        if idx == -1:
            break
        end_idx = idx + len(query_lower)
        # Map char span back to word indices.
        first_word = None
        last_word = None
        for i, off in enumerate(offsets):
            word_end = off + len(words[i]["text"])
            if word_end > idx and first_word is None:
                first_word = i
            if off < end_idx:
                last_word = i
        if first_word is not None and last_word is not None:
            span = words[first_word : last_word + 1]
            span_text = " ".join(w["text"] for w in span)
            matches.append(
                {
                    "text": span_text,
                    "score": 100,
                    "source_range": (
                        span[0]["start"]["seconds"],
                        span[-1]["end"]["seconds"],
                    ),
                    "contiguous_match": _longest_contiguous_query_run(
                        query, span_text
                    ),
                }
            )
        start_search = idx + 1  # allow overlapping matches
    return matches


# ---------- fuzzy ----------


def _search_fuzzy(
    transcript: dict, words: list[dict], query: str, limit: int
) -> list[dict]:
    segments = transcript.get("segments") or []
    query_word_count = max(1, len(query.split()))

    # Group words by segment for the window-search step.
    seg_word_lists: list[list[dict]] = []
    for seg in segments:
        seg_start = seg["start"]["seconds"]
        seg_end = seg["end"]["seconds"]
        # Word's start within segment range; tiny end-tolerance for
        # float-edge cases.
        seg_words = [
            w
            for w in words
            if seg_start <= w["start"]["seconds"] <= seg_end + 0.01
        ]
        if seg_words:
            seg_word_lists.append(seg_words)

    # Fall back to "all words as one segment" if Whisper didn't emit
    # segment metadata for some reason — keeps the path robust.
    if not seg_word_lists:
        seg_word_lists = [words]

    # Score every reasonable word-window against the query. We'd rather
    # spend CPU on synthetic transcripts than miss a cross-boundary
    # match. ~50 words × ~5 window sizes per segment is fine.
    span_scores: list[tuple[float, list[dict]]] = []

    def _score_windows(word_list: list[dict]) -> None:
        if not word_list:
            return
        min_len = max(1, query_word_count - 1)
        max_len = min(len(word_list), query_word_count + 3)
        for window_len in range(min_len, max_len + 1):
            for start in range(len(word_list) - window_len + 1):
                window = word_list[start : start + window_len]
                window_text = " ".join(w["text"] for w in window)
                score = fuzz.ratio(query, window_text)
                span_scores.append((score, window))

    for seg_words in seg_word_lists:
        _score_windows(seg_words)

    # Cross-segment windows: merge adjacent pairs and search again.
    for i in range(len(seg_word_lists) - 1):
        merged = seg_word_lists[i] + seg_word_lists[i + 1]
        _score_windows(merged)

    if not span_scores:
        return []

    span_scores.sort(key=lambda x: -x[0])

    # Dedupe by source-range overlap > 50%, keep highest score.
    matches: list[dict] = []
    for score, window in span_scores:
        if len(matches) >= limit:
            break
        from_s = window[0]["start"]["seconds"]
        to_s = window[-1]["end"]["seconds"]
        if _overlaps_existing(from_s, to_s, matches, threshold=0.5):
            continue
        window_text = " ".join(w["text"] for w in window)
        matches.append(
            {
                "text": window_text,
                "score": int(round(score)),
                "source_range": (from_s, to_s),
                "contiguous_match": _longest_contiguous_query_run(
                    query, window_text
                ),
            }
        )
    _attach_tighter_query_suggestions(matches, query)
    return matches


def _attach_tighter_query_suggestions(matches: list[dict], query: str) -> None:
    """Annotate weak long-query matches with a better contained sub-query.

    The suggestion only fires when a contiguous subphrase from the query
    scores very highly against words inside the returned match. That keeps
    the signal tied to the same timecode the agent is already inspecting.
    """
    query_tokens = query.split()
    if len(query_tokens) < SUGGESTION_MIN_QUERY_WORDS:
        return

    for match in matches:
        if match["score"] > SUGGESTION_MAX_SCORE:
            continue
        match_tokens = match["text"].split()
        if len(match_tokens) < SUGGESTION_MIN_PHRASE_WORDS:
            continue

        best: dict | None = None
        max_phrase_len = min(len(query_tokens) - 1, len(match_tokens))
        for phrase_len in range(SUGGESTION_MIN_PHRASE_WORDS, max_phrase_len + 1):
            for query_start in range(len(query_tokens) - phrase_len + 1):
                subquery = " ".join(query_tokens[query_start : query_start + phrase_len])
                for match_start in range(len(match_tokens) - phrase_len + 1):
                    candidate_text = " ".join(
                        match_tokens[match_start : match_start + phrase_len]
                    )
                    score = int(round(fuzz.ratio(subquery, candidate_text)))
                    if score < SUGGESTION_MIN_SCORE:
                        continue
                    if score < match["score"] + SUGGESTION_MIN_IMPROVEMENT:
                        continue
                    candidate = {
                        "query": subquery,
                        "score": score,
                        "matched_text": candidate_text,
                    }
                    if (
                        best is None
                        or candidate["score"] > best["score"]
                        or (
                            candidate["score"] == best["score"]
                            and len(candidate["query"].split())
                            > len(best["query"].split())
                        )
                    ):
                        best = candidate

        if best is not None:
            match["suggested_query"] = best


def _overlaps_existing(
    from_s: float, to_s: float, matches: list[dict], threshold: float
) -> bool:
    """Return True if the candidate overlaps an existing match by more
    than ``threshold`` of *the smaller of the two* ranges' durations.

    Comparing against the smaller duration catches both subsumes — when
    the candidate is mostly inside an existing match AND when it
    mostly contains an existing match. Either case means the two
    represent the same chunk of content.
    """
    duration = to_s - from_s
    if duration <= 0:
        return False
    for m in matches:
        m_from, m_to = m["source_range"]
        m_duration = m_to - m_from
        if m_duration <= 0:
            continue
        ovl = max(0.0, min(to_s, m_to) - max(from_s, m_from))
        smaller = min(duration, m_duration)
        if ovl / smaller > threshold:
            return True
    return False


# ---------- snap-to-words ----------


def snap_boundaries_to_words(
    from_s: float, to_s: float, words: list[dict]
) -> tuple[float, float]:
    """D6: snap-out for any *started* word.

    - ``from_s`` mid-word (within ``[word.start, word.end)``) → snap
      *earlier* to that word's ``start``.
    - ``to_s`` mid-word → snap *later* to that word's ``end``.
    - Boundaries in gaps between words are unchanged.

    Both inputs and outputs are in the same time domain (the caller
    must convert between source-time and result-time as needed).
    """
    snapped_from = from_s
    snapped_to = to_s
    for w in words:
        w_start = w["start"]["seconds"]
        w_end = w["end"]["seconds"]
        if w_start < from_s < w_end:
            snapped_from = w_start
        if w_start < to_s < w_end:
            snapped_to = w_end
    return snapped_from, snapped_to
