"""Import existing caption files (WebVTT or SRT) as a transcript (issue #29).

Long public footage usually ships with usable captions: YouTube
auto-captions as WebVTT with per-word ``<timestamp><c>`` tags, or a
broadcaster's SRT. Importing them takes seconds where Whisper takes hours,
and produces the same on-disk transcript shape, so ``find``, ``skim``, and
``captions generate`` read it unchanged.

Word timing comes from inline timestamp tags when a cue has them;
otherwise the cue's words are spread across the cue in proportion to their
length. Rolling captions repeat the previous cue's line before adding a
new one, so a cue's leading lines that repeat the previous cue's trailing
lines are dropped.

Pure parsing plus one file read — no FFmpeg, no Click.
"""

from __future__ import annotations

import html
import os
import re

from moviestar.timecodes import format_timecode

CAPTIONS_BACKEND = "imported-captions"

_TIME = r"(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}"
_TIMING_RE = re.compile(rf"^\s*({_TIME})\s*-->\s*({_TIME})")
_INLINE_TIME_RE = re.compile(r"<((?:\d+:)?\d{1,2}:\d{2}\.\d{1,3})>")
_TAG_RE = re.compile(r"<[^>]*>")
# Rolling captions start each cue where the previous one ended. A cue that
# only repeats the previous text is a roll-up hold when it flashes by, like
# YouTube's 10 ms cues; held longer, or after a gap, it is repeated speech.
ROLL_UP_MAX_GAP_SECONDS = 0.05
ROLL_UP_HOLD_MAX_SECONDS = 0.1
# Speaker-change and music markers carry no speech.
_MARKER_RE = re.compile(r"^[>\-\u2013\u2014\u266a\u266b~*#]+$")


class CaptionImportError(ValueError):
    """Raised when a caption file cannot be read as WebVTT or SRT."""


def _parse_time(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2])
    hours = int(parts[-3]) if len(parts) == 3 else 0
    return round(hours * 3600 + minutes * 60 + seconds, 3)


def _plain(line: str) -> str:
    """Caption line without markup or entities, whitespace collapsed."""
    return " ".join(html.unescape(_TAG_RE.sub("", line)).split())


def parse_caption_file(path: str) -> dict:
    """Read a WebVTT or SRT file into ``{"format", "language", "cues"}``.

    Each cue is ``{"start", "end", "lines"}`` with raw payload lines (inline
    tags intact). Raises CaptionImportError when the file is missing,
    unreadable, or has no timed cues.
    """
    if not os.path.isfile(path):
        raise CaptionImportError(f"Caption file not found: {path}")
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        raise CaptionImportError(f"Could not read caption file {path}: {exc}") from exc

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    is_vtt = bool(lines) and lines[0].startswith("WEBVTT")
    language = None
    if is_vtt:
        for line in lines[1:]:
            if not line.strip():
                break
            key, sep, value = line.partition(":")
            if sep and key.strip().lower() == "language":
                language = value.strip() or None

    cues: list[dict] = []
    current: dict | None = None
    for line in lines:
        timing = _TIMING_RE.match(line)
        if timing:
            if current is not None:
                # No empty line before this timing line: a trailing bare
                # number is the next SRT cue's index, not caption text.
                if current["lines"] and current["lines"][-1].strip().isdigit():
                    current["lines"].pop()
                cues.append(current)
            current = {
                "start": _parse_time(timing.group(1)),
                "end": _parse_time(timing.group(2)),
                "lines": [],
            }
        elif current is not None:
            # Only a truly empty line after some payload ends a cue. YouTube
            # cues open with a single-space line, which some tools strip.
            if line == "" and current["lines"]:
                cues.append(current)
                current = None
            elif line:
                current["lines"].append(line)
    if current is not None:
        cues.append(current)

    if not cues:
        raise CaptionImportError(
            f"No caption cues found in {path}; expected WebVTT or SRT with "
            "'HH:MM:SS.mmm --> HH:MM:SS.mmm' timing lines."
        )
    cues.sort(key=lambda cue: cue["start"])
    return {"format": "vtt" if is_vtt else "srt", "language": language, "cues": cues}


def _new_lines(cues: list[dict]) -> list[tuple[dict, list[str]]]:
    """Each cue with only the lines it adds over the previous cue.

    Leading lines are treated as a roll-up repeat only when the cue starts
    where the previous one ended, so independent repeats ("Aye." at
    several votes) stay in the transcript.
    """
    kept: list[tuple[dict, list[str]]] = []
    previous: list[str] = []
    previous_end: float | None = None
    for cue in cues:
        raw = [line for line in cue["lines"] if _plain(line)]
        plain = [_plain(line) for line in raw]
        overlap = 0
        if (
            previous_end is not None
            and cue["start"] - previous_end <= ROLL_UP_MAX_GAP_SECONDS
        ):
            for k in range(min(len(plain), len(previous)), 0, -1):
                if plain[:k] == previous[-k:]:
                    overlap = k
                    break
            if (
                overlap == len(plain)
                and cue["end"] - cue["start"] > ROLL_UP_HOLD_MAX_SECONDS
            ):
                overlap = 0
        previous = plain
        previous_end = cue["end"]
        if raw[overlap:]:
            kept.append((cue, raw[overlap:]))
    return kept


def _words_in(text: str) -> list[str]:
    """Caption tokens without speaker-change or music markers."""
    return [token for token in _plain(text).split() if not _MARKER_RE.match(token)]


def _spread(words: list[str], start: float, end: float) -> list[tuple[str, float, float]]:
    """Place words across [start, end] in proportion to their length."""
    weights = [len(word) + 1 for word in words]
    total = sum(weights)
    span = max(0.0, end - start)
    out = []
    cursor = 0
    for word, weight in zip(words, weights):
        word_start = start + span * cursor / total
        cursor += weight
        out.append((word, round(word_start, 3), round(start + span * cursor / total, 3)))
    return out


def _cue_words(cue: dict, lines: list[str]) -> tuple[list[tuple[str, float, float]], bool]:
    """Timed words for a cue's new lines, and whether inline tags timed them."""
    start, end = cue["start"], max(cue["start"], cue["end"])
    parts = _INLINE_TIME_RE.split(" ".join(lines))
    chunks = [(start, parts[0])] + [
        (min(end, max(start, _parse_time(parts[i]))), parts[i + 1])
        for i in range(1, len(parts), 2)
    ]
    timed = [(at, _words_in(text)) for at, text in chunks]
    timed = [(at, words) for at, words in timed if words]
    out: list[tuple[str, float, float]] = []
    for index, (at, words) in enumerate(timed):
        at = max(at, timed[index - 1][0]) if index else at
        until = timed[index + 1][0] if index + 1 < len(timed) else end
        out.extend(_spread(words, at, max(at, until)))
    return out, len(parts) > 1


def import_caption_transcript(
    caption_path: str,
    source_id: str,
    *,
    source_duration: float,
    source_path: str | None = None,
) -> dict:
    """Build a transcript dict from a caption file.

    The result matches ``transcribe_file``'s shape with ``backend:
    "imported-captions"`` and ``model: None``, plus a ``captions`` block
    naming the file, its format, and how many cues carried word timing.
    Words starting at or after ``source_duration`` are dropped, and the
    survivors' ends are clamped to it, with a ``captions_extend_past_source``
    warning.
    """
    parsed = parse_caption_file(caption_path)
    limit = source_duration if source_duration > 0 else None
    words: list[dict] = []
    segments: list[dict] = []
    tagged_cues = 0
    dropped_cues = 0
    dropped_words = 0
    for cue, lines in _new_lines(parsed["cues"]):
        cue_words, tagged = _cue_words(cue, lines)
        if limit is not None and cue["start"] >= limit:
            dropped_cues += 1
            dropped_words += len(cue_words)
            continue
        clipped = [
            (text, start, min(end, limit) if limit is not None else end)
            for text, start, end in cue_words
            if limit is None or start < limit
        ]
        dropped_words += len(cue_words) - len(clipped)
        if not clipped:
            continue
        tagged_cues += tagged
        words.extend(
            {
                "text": text,
                "start": format_timecode(start),
                "end": format_timecode(end),
                "probability": None,
                "speaker": None,
            }
            for text, start, end in clipped
        )
        segment_end = cue["end"] if limit is None else min(cue["end"], limit)
        segments.append(
            {
                "text": " ".join(text for text, _, _ in clipped),
                "start": format_timecode(cue["start"]),
                "end": format_timecode(segment_end),
            }
        )

    transcript: dict = {
        "source_id": source_id,
        "source_path": os.path.realpath(source_path) if source_path else None,
        "model": None,
        "backend": CAPTIONS_BACKEND,
        "language": parsed["language"],
        "text": " ".join(segment["text"] for segment in segments),
        "duration": format_timecode(source_duration),
        "words": words,
        "segments": segments,
        "no_speech_detected": not words,
        "vocabulary": [],
        "captions": {
            "path": os.path.realpath(caption_path),
            "format": parsed["format"],
            "cues": len(segments),
            "cues_with_word_timing": tagged_cues,
        },
    }
    if dropped_words:
        transcript["warnings"] = [
            {
                "code": "captions_extend_past_source",
                "severity": "warning",
                "message": (
                    f"Captions run past the source end at "
                    f"{format_timecode(source_duration)['text']}; "
                    f"{dropped_words} word(s) after it were dropped, "
                    f"including {dropped_cues} whole cue(s)."
                ),
                "source": source_id,
                "source_id": source_id,
                "cues_dropped": dropped_cues,
                "words_dropped": dropped_words,
                "likely_cause": (
                    "The caption file may belong to a longer or different "
                    "video, or to an untrimmed upload of this one."
                ),
            }
        ]
    return transcript
