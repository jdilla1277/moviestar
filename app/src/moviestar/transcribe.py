"""Transcription via faster-whisper.

Takes an audio or video path and returns a word-level timestamped
transcript. The CPU path uses int8 quantization, which is fast on
Apple Silicon and fine for agent-facing transcripts.

Speaker diarization is NOT done here. A later milestone will add a
diarize step that attaches a `speaker` value to each word; for now,
every word's `speaker` field is `None`.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterable
from pathlib import Path

from moviestar.ffmpeg import run_audio_energy_probe
from moviestar.timecodes import format_timecode


def _normalize_vocabulary(raw_terms: Iterable[str] | None) -> list[str]:
    """Flatten ``--vocabulary`` values into a clean, deduped term list.

    Accepts the raw values from a repeatable ``--vocabulary`` option — each
    of which may itself be a comma-separated string — and returns terms with
    surrounding whitespace stripped, empties dropped, and duplicates removed
    while preserving first-seen order (issue #136).
    """
    if not raw_terms:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for chunk in raw_terms:
        for term in (chunk or "").split(","):
            t = term.strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def _build_vocabulary_prompt(terms: list[str]) -> str | None:
    """Build a faster-whisper ``initial_prompt`` biasing the decoder toward
    the given names/terms, or None when there are no terms.

    Whisper conditions on the prompt as if it were transcript text preceding
    the audio, so a short natural-language glossary nudges it to spell unusual
    names and jargon correctly — e.g. "Amal" instead of "Emma"/"ML" (#136).
    """
    if not terms:
        return None
    return (
        "This recording mentions the following names and terms: "
        + ", ".join(terms)
        + "."
    )


def _format_elapsed(seconds: float) -> str:
    """Compact elapsed-time string: '5s' or '1m 23s'."""
    if seconds < 60:
        return f"{int(seconds)}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def _format_short_timecode(seconds: float) -> str:
    """Compact M:SS.s format for progress lines (not the verbose dict form)."""
    if seconds is None:
        return "?"
    total = round(seconds, 1)
    m, s = divmod(total, 60)
    return f"{int(m)}:{s:04.1f}"


def _log(msg: str, *, quiet: bool) -> None:
    """Print a progress line to stderr unless quiet."""
    if not quiet:
        print(msg, file=sys.stderr, flush=True)


AVAILABLE_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
DEFAULT_MODEL = "base"

# Transcript coverage QA intentionally targets large holes, not ordinary
# pauses or timestamp jitter. Audio pauses shorter than two seconds are kept
# together by the FFmpeg probe; word timestamps get another one-second buffer.
TRANSCRIPT_COVERAGE_MIN_GAP_SECONDS = 5.0
TRANSCRIPT_WORD_ALIGNMENT_TOLERANCE_SECONDS = 1.0


def _speech_energy_without_words(
    energy_spans: Iterable[tuple[float, float]],
    words: Iterable[dict],
    *,
    min_gap_seconds: float = TRANSCRIPT_COVERAGE_MIN_GAP_SECONDS,
    alignment_tolerance_seconds: float = (
        TRANSCRIPT_WORD_ALIGNMENT_TOLERANCE_SECONDS
    ),
) -> list[tuple[float, float]]:
    """Return sustained occupied-audio ranges lacking nearby words.

    Word spans are expanded on both sides to absorb normal Whisper alignment
    drift. Only remaining holes at least ``min_gap_seconds`` long survive.
    """
    word_spans: list[tuple[float, float]] = []
    for word in words:
        try:
            start = float(word["start"]["seconds"])
            end = float(word["end"]["seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        word_spans.append(
            (
                max(0.0, start - alignment_tolerance_seconds),
                max(0.0, end + alignment_tolerance_seconds),
            )
        )
    word_spans.sort()

    gaps: list[tuple[float, float]] = []
    for raw_start, raw_end in sorted(energy_spans):
        start = max(0.0, float(raw_start))
        end = max(start, float(raw_end))
        cursor = start
        for word_start, word_end in word_spans:
            if word_end <= cursor:
                continue
            if word_start >= end:
                break
            gap_end = min(end, word_start)
            if gap_end - cursor >= min_gap_seconds:
                gaps.append((round(cursor, 3), round(gap_end, 3)))
            cursor = max(cursor, min(end, word_end))
            if cursor >= end:
                break
        if end - cursor >= min_gap_seconds:
            gaps.append((round(cursor, 3), round(end, 3)))
    return gaps


def _transcript_coverage_warnings(
    source_path: str,
    source_id: str,
    duration_seconds: float,
    words: list[dict],
    *,
    quiet: bool,
) -> list[dict]:
    """Build structured warnings for sustained audio missing transcript words."""
    try:
        energy_spans = run_audio_energy_probe(source_path, duration_seconds)
    except (FileNotFoundError, RuntimeError) as exc:
        # Coverage QA is an additive guardrail. If its lightweight follow-up
        # probe fails, keep the successful transcript and make the skipped
        # check visible on stderr without turning load into a hard failure.
        _log(f"  Transcript coverage check skipped: {exc}", quiet=quiet)
        return []

    likely_cause = (
        "Whisper or VAD may have omitted speech; the interval may instead "
        "contain sustained non-speech audio."
    )
    warnings = []
    for start, end in _speech_energy_without_words(energy_spans, words):
        duration = end - start
        warnings.append(
            {
                "code": "speech_energy_without_words",
                "severity": "warning",
                "message": (
                    f"Sustained speech-like audio from "
                    f"{format_timecode(start)['text']} to "
                    f"{format_timecode(end)['text']} has no transcript "
                    "words nearby."
                ),
                "source": source_id,
                "source_id": source_id,
                "from": format_timecode(start),
                "to": format_timecode(end),
                "duration": format_timecode(duration),
                "likely_cause": likely_cause,
            }
        )
    return warnings


def _quiet_hf_hub_warnings() -> None:
    """Silence WARNING-level hf_hub logs (issue #65).

    HF Hub returns server-side advisory warnings via an
    ``X-HF-Warning`` HTTP response header — currently
    *"You are sending unauthenticated requests to the HF Hub.
    Please set a HF_TOKEN..."* — which hf_hub then re-emits via
    its logger at WARNING level when an unauthenticated client
    hits a rate-limited request. Whisper model downloads work
    fine unauthenticated, just slower under HF rate limits, so
    the warning is advisory rather than actionable for moviestar.
    Setting hf_hub's verbosity to ERROR silences advisories
    while keeping actual download failures (model-not-found,
    network errors) visible.

    Idempotent. Lazy-imports hf_hub so a misconfigured environment
    where the dependency is missing still allows moviestar's
    non-transcription commands to import.
    """
    try:
        from huggingface_hub.utils import logging as hf_logging
    except ImportError:
        return
    hf_logging.set_verbosity_error()


_quiet_hf_hub_warnings()


# Approximate on-disk sizes for the Systran/faster-whisper-* int8 builds.
# Used in the first-run download announce so an agent on a slow connection
# can predict the wait. Values are rough — listed on the HuggingFace repos
# at the time of writing — and are documentation, not a contract.
_MODEL_DOWNLOAD_SIZES: dict[str, str] = {
    "tiny": "~75 MB",
    "base": "~145 MB",
    "small": "~480 MB",
    "medium": "~1.5 GB",
    "large-v3": "~3.0 GB",
}

_MODEL_DOWNLOAD_SIZES_MB: dict[str, int] = {
    "tiny": 75,
    "base": 145,
    "small": 480,
    "medium": 1500,
    "large-v3": 3000,
}


# Rough CPU int8 realtime factors (processing seconds per audio second) for
# faster-whisper on Apple Silicon, as (low, high) pairs. Like
# _MODEL_DOWNLOAD_SIZES these are rough documentation, not a contract — real
# hardware swings the factor several-fold, which is why the ETA is surfaced as
# a wide range rather than a point estimate. Heavier models cost more per
# second, so the pairs grow monotonically with model size.
_MODEL_REALTIME_FACTORS: dict[str, tuple[float, float]] = {
    "tiny": (0.05, 0.15),
    "base": (0.10, 0.30),
    "small": (0.25, 0.60),
    "medium": (0.50, 1.20),
    "large-v3": (1.00, 2.50),
}


def _humanize_seconds(seconds: float) -> tuple[int, str]:
    """Round a second count to a friendly magnitude + unit ('seconds'/'minutes')."""
    if seconds < 60:
        # Round to the nearest 5s, floored at 5, so we never promise "0 seconds".
        return max(5, int(round(seconds / 5.0)) * 5), "seconds"
    return max(1, int(round(seconds / 60.0))), "minutes"


def _format_remaining(elapsed: float, progress: float) -> str | None:
    """Live 'time remaining' from observed throughput so far.

    Once a few segments are done we know the *actual* realtime factor for this
    machine and file, which beats any baked constant — so each progress line
    can carry a self-correcting estimate. Returns a compact string like
    ``"~2m 10s left"``, or None until there's enough signal (progress must be
    past a small floor, else the extrapolation is wildly noisy).
    """
    if progress <= 0.02 or progress >= 1.0 or elapsed <= 0:
        return None
    remaining = elapsed * (1.0 - progress) / progress
    return f"~{_format_elapsed(remaining)} left"


def _estimate_transcription_eta(model: str, duration_seconds: float) -> str | None:
    """Human-readable ETA range for transcribing `duration_seconds` of audio.

    Returns a string like ``"12-15 minutes"`` or ``"~10 seconds"``, or None
    when the model is unknown or the duration is non-positive. The range comes
    from the rough per-model realtime factors above, so callers should treat it
    as a planning hint, not a guarantee.
    """
    factors = _MODEL_REALTIME_FACTORS.get(model)
    if not factors or duration_seconds <= 0:
        return None
    low_factor, high_factor = factors
    # Express both bounds in the unit of the *upper* bound so the range reads
    # cleanly (e.g. avoid "45 seconds-2 minutes").
    _, unit = _humanize_seconds(duration_seconds * high_factor)
    if unit == "minutes":
        low = max(1, int(round(duration_seconds * low_factor / 60.0)))
        high = max(1, int(round(duration_seconds * high_factor / 60.0)))
    else:
        low = max(5, int(round(duration_seconds * low_factor / 5.0)) * 5)
        high = max(5, int(round(duration_seconds * high_factor / 5.0)) * 5)
    if low >= high:
        return f"~{high} {unit}"
    return f"{low}-{high} {unit}"


class TranscriptionError(RuntimeError):
    """Raised when transcription fails."""


def _hf_cache_root() -> Path:
    """Default HuggingFace cache location."""
    if "HF_HUB_CACHE" in os.environ:
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(
        os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")
    ) / "hub"


def _download_model(model: str, *, local_files_only: bool) -> Path:
    """Resolve or download a model through faster-whisper's public helper."""
    from faster_whisper.utils import download_model

    return Path(
        download_model(
            model,
            cache_dir=str(_hf_cache_root()),
            local_files_only=local_files_only,
        )
    )


def resolve_cached_model(model: str) -> Path | None:
    """Return a complete cached snapshot, or ``None`` without using network."""
    try:
        snapshot = _download_model(model, local_files_only=True)
    except Exception as exc:  # huggingface-hub moved this exception between releases
        if exc.__class__.__name__ == "LocalEntryNotFoundError":
            return None
        raise
    required_files = ("config.json", "model.bin", "tokenizer.json")
    if not all((snapshot / filename).is_file() for filename in required_files):
        return None
    return snapshot


def is_model_cached(model: str) -> bool:
    """True if faster-whisper can resolve a complete local model snapshot."""
    return resolve_cached_model(model) is not None


def model_download_requirement(model: str) -> dict | None:
    """Structured pre-flight metadata when ``model`` is not cached."""
    if resolve_cached_model(model) is not None:
        return None
    return {
        "model": model,
        "estimated_size_mb": _MODEL_DOWNLOAD_SIZES_MB.get(model),
        "cache_dir": str(_hf_cache_root()),
    }


def pull_model(model: str) -> Path:
    """Download ``model`` without constructing the inference model."""
    if model not in AVAILABLE_MODELS:
        raise TranscriptionError(
            f"Unknown model {model!r}. Available: {', '.join(AVAILABLE_MODELS)}"
        )
    try:
        return _download_model(model, local_files_only=False)
    except Exception as exc:  # noqa: BLE001 — preserve the upstream download detail
        raise TranscriptionError(f"Failed to download whisper model {model!r}: {exc}") from exc


def _announce_download_if_needed(model: str, *, quiet: bool = False) -> None:
    if quiet or is_model_cached(model):
        return
    size = _MODEL_DOWNLOAD_SIZES.get(model, "size unknown")
    print(
        f"Downloading faster-whisper {model!r} model (first run, {size})...",
        file=sys.stderr,
        flush=True,
    )


def transcribe_file(
    source_path: str,
    source_id: str,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    quiet: bool = False,
    vocabulary: Iterable[str] | None = None,
    allow_download: bool = True,
) -> dict:
    """Transcribe an audio or video file with faster-whisper.

    Returns a dict with source_id, source_path, model, backend, language,
    text, duration, words, segments, and vocabulary. Times use
    format_timecode() dict shape. Each word has `speaker: None`
    (diarization is a later milestone).

    Pass ``vocabulary`` (names/terms, comma-separated strings or a list) to
    bias the decoder toward unusual spellings via faster-whisper's
    ``initial_prompt`` (issue #136). The normalized term list is echoed back
    in the result under ``vocabulary``.

    Progress is reported on stderr (plain prose, one line per segment,
    with elapsed time). Pass quiet=True to suppress.

    Raises TranscriptionError on model-load or transcription failure.
    FileNotFoundError if source_path does not exist.
    """
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"File not found: {source_path}")

    if model not in AVAILABLE_MODELS:
        raise TranscriptionError(
            f"Unknown model {model!r}. Available: {', '.join(AVAILABLE_MODELS)}"
        )

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError(
            "faster-whisper is not installed. Reinstall MovieStar: pip install -e app/"
        ) from exc

    cached_model = resolve_cached_model(model)
    if not allow_download and cached_model is None:
        raise TranscriptionError(
            f"Whisper model {model!r} is not cached. Run "
            f"'moviestar models pull {model}' before retrying."
        )

    if cached_model is None:
        _announce_download_if_needed(model, quiet=quiet)

    # Print BEFORE model instantiation so the user sees activity during the
    # 3-5s model-load gap. Without this, the CLI is silent for that whole
    # window and agents can't tell they're making progress.
    _log(f"  Loading Whisper {model!r} model...", quiet=quiet)

    try:
        model_source = str(cached_model) if cached_model is not None else model
        whisper_model = WhisperModel(
            model_source,
            device="cpu",
            compute_type="int8",
            download_root=str(_hf_cache_root()),
            local_files_only=(cached_model is not None or not allow_download),
        )
    except Exception as exc:  # noqa: BLE001 — surface whisper's error verbatim
        raise TranscriptionError(f"Failed to load whisper model {model!r}: {exc}") from exc

    vocab = _normalize_vocabulary(vocabulary)
    initial_prompt = _build_vocabulary_prompt(vocab)
    if vocab:
        _log(
            f"  Biasing transcription toward {len(vocab)} vocabulary term(s): "
            f"{', '.join(vocab)}.",
            quiet=quiet,
        )

    start_time = time.time()

    try:
        segments_iter, info = whisper_model.transcribe(
            source_path,
            word_timestamps=True,
            language=language,
            initial_prompt=initial_prompt,
        )
        duration = float(getattr(info, "duration", 0.0) or 0.0)

        # Surface an upfront ETA so an agent isn't blindsided by a multi-minute
        # wait on a long file (issue #140). Duration is known here, before the
        # lazy segment loop does any heavy work.
        eta = _estimate_transcription_eta(model, duration)
        eta_suffix = f" — estimated {eta}" if eta else ""
        _log(
            f"  Transcribing {_format_short_timecode(duration)} audio "
            f"with {model!r} model{eta_suffix}...",
            quiet=quiet,
        )

        words: list[dict] = []
        segments_out: list[dict] = []
        text_parts: list[str] = []

        for seg in segments_iter:
            seg_text = (seg.text or "").strip()
            if seg_text:
                text_parts.append(seg_text)
            segments_out.append(
                {
                    "text": seg.text,
                    "start": format_timecode(seg.start),
                    "end": format_timecode(seg.end),
                }
            )
            for w in seg.words or []:
                words.append(
                    {
                        "text": (w.word or "").strip(),
                        "start": format_timecode(w.start),
                        "end": format_timecode(w.end),
                        "probability": round(float(w.probability), 4),
                        "speaker": None,
                    }
                )

            elapsed = time.time() - start_time
            progress = (seg.end / duration) if duration else 0.0
            remaining = _format_remaining(elapsed, progress)
            remaining_suffix = f" {remaining}" if remaining else ""
            _log(
                f"    [elapsed {_format_elapsed(elapsed)}] "
                f"{_format_short_timecode(seg.end)} / "
                f"{_format_short_timecode(duration)} "
                f"({progress:.0%}, {len(words)} words){remaining_suffix}",
                quiet=quiet,
            )
    except Exception as exc:  # noqa: BLE001
        raise TranscriptionError(f"Transcription failed: {exc}") from exc

    total_elapsed = time.time() - start_time
    no_speech_detected = len(words) == 0
    coverage_warnings = _transcript_coverage_warnings(
        source_path,
        source_id,
        duration,
        words,
        quiet=quiet,
    )
    if no_speech_detected:
        if coverage_warnings:
            _log(
                f"  Transcription produced 0 words despite sustained audio "
                f"in {_format_elapsed(total_elapsed)}.",
                quiet=quiet,
            )
        else:
            # Issue #199: '0 words' alone is ambiguous between "no speech in
            # this audio" (expected) and "transcription silently broke" (a
            # bug). The coverage check above distinguishes those cases.
            _log(
                f"  No speech detected in audio (0 words) in "
                f"{_format_elapsed(total_elapsed)}.",
                quiet=quiet,
            )
    else:
        _log(
            f"  Transcription done: {len(words)} words in "
            f"{_format_elapsed(total_elapsed)}.",
            quiet=quiet,
        )

    if coverage_warnings:
        _log(
            f"  Warning: {len(coverage_warnings)} sustained audio interval(s) "
            "have no transcript words nearby.",
            quiet=quiet,
        )

    result = {
        "source_id": source_id,
        "source_path": os.path.realpath(source_path),
        "model": model,
        "backend": "faster-whisper",
        "language": info.language,
        "text": " ".join(text_parts).strip(),
        "duration": format_timecode(duration),
        "words": words,
        "segments": segments_out,
        "no_speech_detected": no_speech_detected,
        "vocabulary": vocab,
    }
    if coverage_warnings:
        result["warnings"] = coverage_warnings
    return result
