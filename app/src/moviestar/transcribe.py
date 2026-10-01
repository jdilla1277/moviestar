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
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

from moviestar.ffmpeg import (
    extract_audio_window,
    run_audio_energy_probe,
    run_stereo_phase_probe,
)
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
# Audio checks before transcription (issues #18, #19).
TRANSCRIPTION_CHANNELS = ("auto", "mix", "left", "right")
WHISPER_SAMPLE_RATE = 16000
# At or below this left/right correlation a mono mixdown cancels most of
# what the channels carry, so auto transcribes the louder channel alone.
OUT_OF_PHASE_CORRELATION = -0.5
# Whisper is skipped when detected speech is under both of these: on
# non-speech audio it invents fluent, plausible words instead of returning
# nothing. The absolute floor ignores stray VAD blips in long recordings;
# the fraction keeps a short clip that is mostly speech.
NO_SPEECH_MAX_SECONDS = 0.5
NO_SPEECH_MAX_FRACTION = 0.5
# Flag a transcript when at least this many words exist and more than
# this share of them fall outside detected speech.
OUTSIDE_SPEECH_MIN_WORDS = 10
OUTSIDE_SPEECH_MAX_FRACTION = 0.5
SPEECH_SPAN_TOLERANCE_SECONDS = 0.5

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
    offset_seconds: float = 0.0,
) -> list[dict]:
    """Build structured warnings for sustained audio missing transcript words.

    ``offset_seconds`` places a cut-out window's audio on the source clock.
    """
    try:
        energy_spans = [
            (start + offset_seconds, end + offset_seconds)
            for start, end in run_audio_energy_probe(source_path, duration_seconds)
        ]
    except (FileNotFoundError, RuntimeError) as exc:
        # Coverage QA is an additive guardrail. If its lightweight follow-up
        # probe fails, keep the successful transcript and make the skipped
        # check visible on stderr without turning load into a hard failure.
        _log(f"  Transcript coverage check skipped: {exc}", quiet=quiet)
        return []

    return [
        _coverage_warning(source_id, start, end)
        for start, end in _speech_energy_without_words(energy_spans, words)
    ]


def _coverage_warning(source_id: str, start: float, end: float) -> dict:
    return {
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
        "duration": format_timecode(end - start),
        "likely_cause": (
            "Whisper or VAD may have omitted speech; the interval may instead "
            "contain sustained non-speech audio."
        ),
    }


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


def _choose_transcription_channel(requested: str, phase: dict | None) -> str:
    """The channel to transcribe. ``auto`` keeps the usual mono mixdown
    unless the channels are out of phase, then takes the louder one."""
    if requested != "auto":
        return requested
    if phase is None or phase["correlation"] > OUT_OF_PHASE_CORRELATION:
        return "mix"
    return "right" if phase["right_rms_db"] > phase["left_rms_db"] else "left"


def _load_audio(source_path: str, channel: str):
    """Decode 16 kHz audio for Whisper exactly as faster-whisper would,
    optionally keeping one channel of a stereo source."""
    from faster_whisper.audio import decode_audio

    if channel in ("left", "right"):
        left, right = decode_audio(
            source_path, sampling_rate=WHISPER_SAMPLE_RATE, split_stereo=True
        )
        return left if channel == "left" else right
    return decode_audio(source_path, sampling_rate=WHISPER_SAMPLE_RATE)


def _detect_speech(audio) -> list[tuple[float, float]]:
    """Voice-activity spans in seconds, from faster-whisper's Silero VAD."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    return [
        (span["start"] / WHISPER_SAMPLE_RATE, span["end"] / WHISPER_SAMPLE_RATE)
        for span in get_speech_timestamps(
            audio, VadOptions(), sampling_rate=WHISPER_SAMPLE_RATE
        )
    ]


def _words_outside_speech(
    words: list[dict], speech_spans: list[tuple[float, float]]
) -> int:
    tolerance = SPEECH_SPAN_TOLERANCE_SECONDS
    outside = 0
    for word in words:
        middle = (word["start"]["seconds"] + word["end"]["seconds"]) / 2
        if not any(
            start - tolerance <= middle <= end + tolerance
            for start, end in speech_spans
        ):
            outside += 1
    return outside


def _out_of_phase_warning(source_id: str, phase: dict, channel: str) -> dict:
    return {
        "code": "audio_channels_out_of_phase",
        "severity": "warning",
        "message": (
            "The left and right channels are out of phase (correlation "
            f"{phase['correlation']:+.2f}), so a mono mix cancels the speech. "
            f"Transcribed the {channel} channel instead."
        ),
        "source": source_id,
        "source_id": source_id,
        "stereo_correlation": phase["correlation"],
        "channel_used": channel,
        "remedy": (
            "Override with 'moviestar retranscribe --channel left|right|mix'. "
            "Mono playback of this source (phone speakers) will cancel too."
        ),
    }


def _no_speech_warning(source_id: str, duration: float, speech: float) -> dict:
    return {
        "code": "no_speech_in_audio",
        "severity": "warning",
        "message": (
            "Voice activity detection found no speech in "
            f"{format_timecode(duration)['text']} of audio, so Whisper was "
            "skipped: on non-speech audio it invents plausible words."
        ),
        "source": source_id,
        "source_id": source_id,
        "detected_speech": format_timecode(speech),
        "remedy": (
            "If the source does contain speech, run 'moviestar retranscribe "
            "--no-speech-check', or pick one channel with --channel left|right."
        ),
    }


def _outside_speech_warning(source_id: str, outside: int, total: int) -> dict:
    return {
        "code": "words_outside_detected_speech",
        "severity": "warning",
        "message": (
            f"{outside} of {total} transcript words fall outside detected "
            "speech; they may be invented rather than heard."
        ),
        "source": source_id,
        "source_id": source_id,
        "words_outside_speech": outside,
        "words_total": total,
        "remedy": (
            "Spot-check with 'moviestar find' or a short 'moviestar watch' "
            "render; retranscribe with a larger --model or one --channel."
        ),
    }


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
    channel: str = "auto",
    speech_check: bool = True,
    offset_seconds: float = 0.0,
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

    Before Whisper runs, ``channel="auto"`` measures stereo phase and
    transcribes the louder channel when a mono mix would cancel the
    speech (issue #18); ``mix``, ``left``, or ``right`` force a choice.
    ``speech_check`` runs voice activity detection first and skips Whisper
    when there is no speech, since Whisper invents words on non-speech
    audio (issue #19). Both report under ``audio`` and ``warnings``.

    ``offset_seconds`` adds to every word, segment, and warning time, for
    audio cut out of a longer source (see ``transcribe_ranges``).

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
    if channel not in TRANSCRIPTION_CHANNELS:
        raise TranscriptionError(
            f"Unknown channel {channel!r}. Use one of: "
            f"{', '.join(TRANSCRIPTION_CHANNELS)}."
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

    audio_warnings: list[dict] = []
    phase = None
    if channel == "auto":
        try:
            phase = run_stereo_phase_probe(source_path)
        except (FileNotFoundError, RuntimeError) as exc:
            _log(f"  Stereo phase check skipped: {exc}", quiet=quiet)
    channel_used = _choose_transcription_channel(channel, phase)
    if channel == "auto" and channel_used != "mix" and phase is not None:
        _log(
            f"  Left and right channels are out of phase (correlation "
            f"{phase['correlation']:+.2f}); transcribing the {channel_used} "
            "channel.",
            quiet=quiet,
        )
        audio_warnings.append(_out_of_phase_warning(source_id, phase, channel_used))
    audio_report: dict = {
        "channel": {
            "requested": channel,
            "used": channel_used,
            "stereo_correlation": phase["correlation"] if phase else None,
        },
        "speech_check": {"enabled": False},
    }

    try:
        audio = _load_audio(source_path, channel_used)
    except Exception as exc:  # noqa: BLE001 — surface the decoder's error
        raise TranscriptionError(f"Transcription failed: {exc}") from exc
    audio_duration = len(audio) / WHISPER_SAMPLE_RATE

    speech_spans: list[tuple[float, float]] | None = None
    if speech_check:
        speech_spans = [
            (start + offset_seconds, end + offset_seconds)
            for start, end in _detect_speech(audio)
        ]
        speech_seconds = sum(end - start for start, end in speech_spans)
        whisper_skipped = speech_seconds == 0 or speech_seconds < min(
            NO_SPEECH_MAX_SECONDS, NO_SPEECH_MAX_FRACTION * audio_duration
        )
        audio_report["speech_check"] = {
            "enabled": True,
            "detected_speech": format_timecode(speech_seconds),
            "whisper_skipped": whisper_skipped,
        }
        if whisper_skipped:
            _log(
                "  No speech detected by voice activity detection in "
                f"{_format_short_timecode(audio_duration)} of audio; "
                "skipped Whisper.",
                quiet=quiet,
            )
            return {
                "source_id": source_id,
                "source_path": os.path.realpath(source_path),
                "model": model,
                "backend": "faster-whisper",
                "language": language,
                "text": "",
                "duration": format_timecode(audio_duration),
                "words": [],
                "segments": [],
                "no_speech_detected": True,
                "vocabulary": _normalize_vocabulary(vocabulary),
                "audio": audio_report,
                "warnings": [
                    *audio_warnings,
                    _no_speech_warning(source_id, audio_duration, speech_seconds),
                ],
            }

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
            audio,
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
                    "start": format_timecode(seg.start + offset_seconds),
                    "end": format_timecode(seg.end + offset_seconds),
                }
            )
            for w in seg.words or []:
                words.append(
                    {
                        "text": (w.word or "").strip(),
                        "start": format_timecode(w.start + offset_seconds),
                        "end": format_timecode(w.end + offset_seconds),
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
        offset_seconds=offset_seconds,
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
    if speech_spans is not None and len(words) >= OUTSIDE_SPEECH_MIN_WORDS:
        outside = _words_outside_speech(words, speech_spans)
        if outside > OUTSIDE_SPEECH_MAX_FRACTION * len(words):
            _log(
                f"  Warning: {outside} of {len(words)} words fall outside "
                "detected speech.",
                quiet=quiet,
            )
            audio_warnings.append(
                _outside_speech_warning(source_id, outside, len(words))
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
        "audio": audio_report,
    }
    warnings = [*audio_warnings, *coverage_warnings]
    if warnings:
        result["warnings"] = warnings
    return result


def _range_label(start: float, end: float) -> str:
    return f"{format_timecode(start)['text']}-{format_timecode(end)['text']}"


def normalize_transcribe_ranges(
    ranges: Iterable[tuple[float, float]], *, source_duration: float
) -> list[tuple[float, float]]:
    """Sorted, non-overlapping (start, end) windows clamped to the source.

    Raises TranscriptionError for a window that is empty or starts at or
    after the end of the source.
    """
    windows: list[tuple[float, float]] = []
    for start, end in ranges:
        start, end = round(float(start), 3), round(float(end), 3)
        if end <= start:
            raise TranscriptionError(
                f"Range {_range_label(start, end)} must end after it starts."
            )
        if source_duration > 0:
            if start >= source_duration:
                raise TranscriptionError(
                    f"Range {_range_label(start, end)} starts at or after the "
                    "source ends at "
                    f"{format_timecode(source_duration)['text']}."
                )
            end = min(end, round(source_duration, 3))
        windows.append((start, end))
    windows.sort()
    merged: list[tuple[float, float]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def transcribe_ranges(
    source_path: str,
    source_id: str,
    ranges: Iterable[tuple[float, float]],
    *,
    source_duration: float,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    quiet: bool = False,
    vocabulary: Iterable[str] | None = None,
    allow_download: bool = True,
    channel: str = "auto",
    speech_check: bool = True,
) -> dict:
    """Transcribe only the given windows of a source (issue #29).

    Each window is cut to a temporary WAV and transcribed alone, so the
    cost tracks the windows, not the source. Times are on the source clock.
    The result has ``coverage: "ranges"`` and one ``transcribed_ranges``
    entry per window carrying its model and audio checks; everything
    outside the windows has no words. ``duration`` is the whole source.
    """
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"File not found: {source_path}")
    windows = normalize_transcribe_ranges(ranges, source_duration=source_duration)

    parts: list[tuple[float, float, dict]] = []
    with tempfile.TemporaryDirectory(prefix="moviestar-range-") as tmp:
        for index, (start, end) in enumerate(windows):
            _log(f"  Transcribing range {_range_label(start, end)} only.", quiet=quiet)
            window_path = os.path.join(tmp, f"range_{index}.wav")
            try:
                extract_audio_window(source_path, start, end - start, window_path)
            except (FileNotFoundError, RuntimeError) as exc:
                raise TranscriptionError(
                    f"Could not cut audio for range {_range_label(start, end)}: {exc}"
                ) from exc
            part = transcribe_file(
                window_path,
                source_id,
                model=model,
                language=language,
                quiet=quiet,
                vocabulary=vocabulary,
                allow_download=allow_download,
                channel=channel,
                speech_check=speech_check,
                offset_seconds=start,
            )
            parts.append((start, end, part))

    words = [word for _, _, part in parts for word in part["words"]]
    result: dict = {
        "source_id": source_id,
        "source_path": os.path.realpath(source_path),
        "model": model,
        "backend": "faster-whisper",
        "language": language
        or next((p["language"] for _, _, p in parts if p.get("language")), None),
        "text": " ".join(p["text"] for _, _, p in parts if p["text"]),
        "duration": format_timecode(source_duration),
        "words": words,
        "segments": [seg for _, _, part in parts for seg in part["segments"]],
        "no_speech_detected": not words,
        "vocabulary": _normalize_vocabulary(vocabulary),
        "coverage": "ranges",
        "transcribed_ranges": [
            {
                "from": format_timecode(start),
                "to": format_timecode(end),
                "model": model,
                "audio": part["audio"],
            }
            for start, end, part in parts
        ],
    }
    warnings = [w for _, _, part in parts for w in part.get("warnings") or []]
    if warnings:
        result["warnings"] = warnings
    return result


def _subtract(
    start: float, end: float, windows: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Pieces of [start, end] outside every window."""
    pieces = [(start, end)]
    for w_start, w_end in windows:
        next_pieces = []
        for p_start, p_end in pieces:
            if w_end <= p_start or w_start >= p_end:
                next_pieces.append((p_start, p_end))
                continue
            if p_start < w_start:
                next_pieces.append((p_start, w_start))
            if w_end < p_end:
                next_pieces.append((w_end, p_end))
        pieces = next_pieces
    return pieces


def _seconds(entry: dict, key: str) -> float:
    return float(entry[key]["seconds"])


def _outside_windows_warnings(
    warnings: list[dict], windows: list[tuple[float, float]]
) -> list[dict]:
    """Coverage warnings narrowed to what the new windows didn't replace.

    A replaced window has fresh words and its own fresh checks, so an old
    "no words here" warning only survives for the parts outside it, and
    only while those parts are still long enough to report.
    """
    kept = []
    for warning in warnings:
        if warning.get("code") != "speech_energy_without_words":
            kept.append(warning)
            continue
        start, end = _seconds(warning, "from"), _seconds(warning, "to")
        pieces = _subtract(start, end, windows)
        if pieces == [(start, end)]:
            kept.append(warning)
            continue
        kept.extend(
            {**warning, **_coverage_warning(warning["source_id"], p_start, p_end)}
            for p_start, p_end in pieces
            if p_end - p_start >= TRANSCRIPT_COVERAGE_MIN_GAP_SECONDS
        )
    return kept


def merge_range_transcript(base: dict | None, ranged: dict) -> dict:
    """Lay a ranged Whisper transcript over an existing transcript.

    Words whose midpoint falls inside a new window are replaced; everything
    else in ``base`` is kept, including its ``backend`` (so imported
    captions stay ``imported-captions``). Segments that straddle a window
    keep only their outside part. Older ``transcribed_ranges`` overlapped
    by a new window are trimmed, and so are old coverage warnings.
    ``coverage`` stays ``full`` over a full transcript and ``ranges`` over
    a ranges-only one.
    """
    if base is None:
        return ranged
    windows = [
        (_seconds(r, "from"), _seconds(r, "to"))
        for r in ranged.get("transcribed_ranges") or []
    ]

    def outside(entry: dict) -> bool:
        middle = (_seconds(entry, "start") + _seconds(entry, "end")) / 2
        return not any(start <= middle < end for start, end in windows)

    kept_words = [w for w in base.get("words") or [] if outside(w)]
    segments: list[dict] = []
    for seg in base.get("segments") or []:
        seg_start, seg_end = _seconds(seg, "start"), _seconds(seg, "end")
        pieces = _subtract(seg_start, seg_end, windows)
        if pieces == [(seg_start, seg_end)]:
            segments.append(seg)
            continue
        for p_start, p_end in pieces:
            piece_words = [
                w for w in kept_words
                if p_start <= (_seconds(w, "start") + _seconds(w, "end")) / 2 <= p_end
            ]
            if piece_words:
                segments.append(
                    {
                        "text": " ".join(w["text"] for w in piece_words),
                        "start": format_timecode(p_start),
                        "end": format_timecode(p_end),
                    }
                )

    earlier_ranges = []
    for entry in base.get("transcribed_ranges") or []:
        for p_start, p_end in _subtract(
            _seconds(entry, "from"), _seconds(entry, "to"), windows
        ):
            earlier_ranges.append(
                {**entry, "from": format_timecode(p_start), "to": format_timecode(p_end)}
            )

    words = sorted(kept_words + ranged["words"], key=lambda w: _seconds(w, "start"))
    segments = sorted(
        segments + ranged["segments"], key=lambda s: _seconds(s, "start")
    )
    ranges_only = base.get("coverage") == "ranges"
    merged = {
        **base,
        "model": ranged.get("model") if ranges_only else base.get("model"),
        "language": base.get("language") or ranged.get("language"),
        "text": " ".join(
            text for text in ((s.get("text") or "").strip() for s in segments) if text
        ),
        "words": words,
        "segments": segments,
        "no_speech_detected": not words,
        "vocabulary": ranged.get("vocabulary") or base.get("vocabulary") or [],
        "coverage": "ranges" if ranges_only else "full",
        "transcribed_ranges": sorted(
            earlier_ranges + ranged["transcribed_ranges"],
            key=lambda r: _seconds(r, "from"),
        ),
    }
    warnings = [
        *_outside_windows_warnings(base.get("warnings") or [], windows),
        *(ranged.get("warnings") or []),
    ]
    if warnings:
        merged["warnings"] = warnings
    else:
        merged.pop("warnings", None)
    return merged
