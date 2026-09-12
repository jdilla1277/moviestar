"""Multi-track audio document validation and result-time resolution.

The persisted spec stores editorial intent only. This module probes external
media and derives effective source/placed ranges, loop counts, result clipping,
and ducking preset values consumed by the shared watch/export mixer.
"""

from __future__ import annotations

import copy
import difflib
import math
import os
from pathlib import Path
from typing import Callable

from moviestar.timecodes import format_timecode, parse_timecode


AUDIO_DOCUMENT_VERSION = 1
AUDIO_KINDS = {"music", "voiceover", "other"}
AUDIO_DUCKING_PRESETS = {
    "speech": {
        "threshold_db": -30.0,
        "ratio": 8.0,
        "attack_ms": 20.0,
        "release_ms": 250.0,
    }
}
AUDIO_GAIN_MIN_DB = -60.0
AUDIO_GAIN_MAX_DB = 24.0

_DOCUMENT_KEYS = {"version", "audio_mix", "_examples"}
_MIX_KEYS = {"source_audio", "tracks"}
_SOURCE_KEYS = {"muted", "gain_db", "fade_in", "fade_out"}
_TRACK_KEY_ORDER = (
    "id",
    "kind",
    "path",
    "at",
    "source_from",
    "source_to",
    "gain_db",
    "muted",
    "loop",
    "fade_in",
    "fade_out",
    "ducking",
)
_TRACK_KEYS = set(_TRACK_KEY_ORDER)
_DUCKING_KEYS = {"under", "preset"}


class AudioSurfaceValidationError(ValueError):
    """One or more path-addressed audio document validation errors."""

    def __init__(self, errors: list[dict[str, str]]):
        super().__init__(f"{len(errors)} audio validation error(s)")
        self.errors = errors


def default_audio_mix() -> dict:
    return {
        "source_audio": {
            "muted": False,
            "gain_db": 0.0,
            "fade_in": 0.0,
            "fade_out": 0.0,
        },
        "tracks": [],
    }


def audio_document_examples() -> dict:
    """Return copyable intent-only tracks for a fresh dumped document."""
    return {
        "voiceover_track": {
            "id": "narration",
            "kind": "voiceover",
            "path": "narration.wav",
            "at": "0:00:00.000",
            "source_from": "0:00:00.000",
            "source_to": None,
            "gain_db": 0.0,
            "muted": False,
            "loop": False,
            "fade_in": 0.1,
            "fade_out": 0.2,
            "ducking": None,
        },
        "music_track": {
            "id": "music",
            "kind": "music",
            "path": "music.wav",
            "at": "0:00:00.000",
            "source_from": "0:00:00.000",
            "source_to": None,
            "gain_db": -18.0,
            "muted": False,
            "loop": True,
            "fade_in": 1.0,
            "fade_out": 2.0,
            "ducking": {"under": ["narration"], "preset": "speech"},
        },
    }


def _item(path: str, message: str) -> dict[str, str]:
    return {"path": path, "error": message}


def _unknown_keys(
    value: dict, allowed: set[str], path: str, errors: list[dict[str, str]]
) -> None:
    for key in value:
        if key in allowed:
            continue
        suggestion = difflib.get_close_matches(key, sorted(allowed), n=1)
        suffix = f"; did you mean {suggestion[0]!r}?" if suggestion else ""
        errors.append(_item(f"{path}.{key}" if path else key, f"unknown field{suffix}"))


def _number(
    value: object,
    path: str,
    errors: list[dict[str, str]],
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(_item(path, "must be a finite number"))
        return None
    result = float(value)
    if not math.isfinite(result):
        errors.append(_item(path, "must be a finite number"))
        return None
    if minimum is not None and result < minimum:
        errors.append(_item(path, f"must be at least {minimum}"))
        return None
    if maximum is not None and result > maximum:
        errors.append(_item(path, f"must be at most {maximum}"))
        return None
    return result


def _boolean(
    value: object, path: str, errors: list[dict[str, str]]
) -> bool | None:
    if not isinstance(value, bool):
        errors.append(_item(path, "must be true or false"))
        return None
    return value


def _time(
    value: object, path: str, errors: list[dict[str, str]]
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        errors.append(_item(path, "must be a timecode string or number of seconds"))
        return None
    try:
        result = parse_timecode(str(value))
    except ValueError as exc:
        errors.append(_item(path, str(exc)))
        return None
    if result < 0:
        errors.append(_item(path, "cannot be negative"))
        return None
    return result


def _source_audio(
    raw: object, result_duration: float, errors: list[dict[str, str]]
) -> dict:
    path = "audio_mix.source_audio"
    if not isinstance(raw, dict):
        errors.append(_item(path, "must be an object"))
        return default_audio_mix()["source_audio"]
    _unknown_keys(raw, _SOURCE_KEYS, path, errors)
    muted = _boolean(raw.get("muted", False), f"{path}.muted", errors)
    gain = _number(
        raw.get("gain_db", 0),
        f"{path}.gain_db",
        errors,
        minimum=AUDIO_GAIN_MIN_DB,
        maximum=AUDIO_GAIN_MAX_DB,
    )
    fade_in = _number(raw.get("fade_in", 0), f"{path}.fade_in", errors, minimum=0)
    fade_out = _number(
        raw.get("fade_out", 0), f"{path}.fade_out", errors, minimum=0
    )
    for name, fade in (("fade_in", fade_in), ("fade_out", fade_out)):
        if fade is not None and fade > result_duration + 0.001:
            errors.append(
                _item(
                    f"{path}.{name}",
                    f"cannot exceed result duration {result_duration:.3f}s",
                )
            )
    return {
        "muted": False if muted is None else muted,
        "gain_db": 0.0 if gain is None else gain,
        "fade_in": 0.0 if fade_in is None else fade_in,
        "fade_out": 0.0 if fade_out is None else fade_out,
    }


def _probe_track(
    path: str,
    probe_audio: Callable[[str], dict],
    error_path: str,
    errors: list[dict[str, str]],
) -> dict | None:
    try:
        media = probe_audio(path)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        errors.append(_item(error_path, str(exc)))
        return None
    duration = media.get("duration_seconds")
    if not isinstance(duration, (int, float)) or duration <= 0:
        errors.append(_item(error_path, "audio duration must be greater than zero"))
        return None
    return media


def _track(
    raw: object,
    index: int,
    *,
    result_duration: float,
    base_dir: Path,
    probe_audio: Callable[[str], dict],
    errors: list[dict[str, str]],
) -> dict | None:
    path = f"audio_mix.tracks[{index}]"
    if not isinstance(raw, dict):
        errors.append(_item(path, "must be an object"))
        return None
    _unknown_keys(raw, _TRACK_KEYS, path, errors)

    track_id = raw.get("id")
    if not isinstance(track_id, str) or not track_id.strip():
        errors.append(_item(f"{path}.id", "must be a non-empty string"))
        track_id = None
    else:
        track_id = track_id.strip()
        if track_id == "source":
            errors.append(
                _item(f"{path}.id", "'source' is reserved for routed source audio")
            )

    kind = raw.get("kind")
    if kind not in AUDIO_KINDS:
        errors.append(
            _item(
                f"{path}.kind",
                f"must be one of: {', '.join(sorted(AUDIO_KINDS))}",
            )
        )

    raw_path = raw.get("path")
    media_path: str | None = None
    media: dict | None = None
    if not isinstance(raw_path, str) or not raw_path.strip():
        errors.append(_item(f"{path}.path", "must be a non-empty file path"))
    else:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        media_path = os.path.realpath(candidate)
        media = _probe_track(media_path, probe_audio, f"{path}.path", errors)

    at = _time(raw.get("at", 0), f"{path}.at", errors)
    source_from = _time(raw.get("source_from", 0), f"{path}.source_from", errors)
    source_to_raw = raw.get("source_to")
    source_to = (
        None
        if source_to_raw is None
        else _time(source_to_raw, f"{path}.source_to", errors)
    )
    gain = _number(
        raw.get("gain_db", 0),
        f"{path}.gain_db",
        errors,
        minimum=AUDIO_GAIN_MIN_DB,
        maximum=AUDIO_GAIN_MAX_DB,
    )
    muted = _boolean(raw.get("muted", False), f"{path}.muted", errors)
    loop = _boolean(raw.get("loop", False), f"{path}.loop", errors)
    fade_in = _number(raw.get("fade_in", 0), f"{path}.fade_in", errors, minimum=0)
    fade_out = _number(
        raw.get("fade_out", 0), f"{path}.fade_out", errors, minimum=0
    )

    ducking_raw = raw.get("ducking")
    ducking: dict | None = None
    if ducking_raw is not None:
        if not isinstance(ducking_raw, dict):
            errors.append(_item(f"{path}.ducking", "must be an object or null"))
        else:
            _unknown_keys(ducking_raw, _DUCKING_KEYS, f"{path}.ducking", errors)
            under = ducking_raw.get("under")
            preset = ducking_raw.get("preset", "speech")
            if not isinstance(under, list) or not under:
                errors.append(
                    _item(f"{path}.ducking.under", "must be a non-empty array")
                )
                under = []
            elif any(not isinstance(value, str) or not value for value in under):
                errors.append(
                    _item(
                        f"{path}.ducking.under",
                        "every sidechain id must be a non-empty string",
                    )
                )
                under = []
            elif len(set(under)) != len(under):
                errors.append(
                    _item(f"{path}.ducking.under", "sidechain ids must be unique")
                )
            if preset not in AUDIO_DUCKING_PRESETS:
                errors.append(
                    _item(
                        f"{path}.ducking.preset",
                        f"must be one of: {', '.join(AUDIO_DUCKING_PRESETS)}",
                    )
                )
            if isinstance(under, list) and preset in AUDIO_DUCKING_PRESETS:
                ducking = {
                    "under": list(under),
                    "preset": preset,
                    "resolved": copy.deepcopy(AUDIO_DUCKING_PRESETS[preset]),
                }

    if media is None or at is None or source_from is None:
        return None
    media_duration = float(media["duration_seconds"])
    effective_source_to = media_duration if source_to is None else source_to
    if source_from >= effective_source_to:
        errors.append(
            _item(
                f"{path}.source_to",
                "must be after source_from and within the external audio file",
            )
        )
        return None
    if effective_source_to > media_duration + 0.001:
        errors.append(
            _item(
                f"{path}.source_to",
                f"cannot exceed audio duration {media_duration:.3f}s",
            )
        )
        return None
    if at >= result_duration - 0.0005:
        errors.append(
            _item(
                f"{path}.at",
                f"must be before result end {result_duration:.3f}s",
            )
        )
        return None

    selected_duration = effective_source_to - source_from
    available_result = result_duration - at
    effective_duration = (
        available_result if loop else min(selected_duration, available_result)
    )
    for name, fade in (("fade_in", fade_in), ("fade_out", fade_out)):
        if fade is not None and fade > effective_duration + 0.001:
            errors.append(
                _item(
                    f"{path}.{name}",
                    f"cannot exceed placed duration {effective_duration:.3f}s",
                )
            )

    return {
        "_document_index": index,
        "id": track_id,
        "kind": kind,
        "path": media_path,
        "at": format_timecode(at)["text"],
        "source_from": format_timecode(source_from)["text"],
        "source_to": (
            None if source_to_raw is None else format_timecode(effective_source_to)["text"]
        ),
        "gain_db": 0.0 if gain is None else gain,
        "muted": False if muted is None else muted,
        "loop": False if loop is None else loop,
        "fade_in": 0.0 if fade_in is None else fade_in,
        "fade_out": 0.0 if fade_out is None else fade_out,
        "ducking": ducking,
        "media": {
            "duration": format_timecode(media_duration),
            "codec": media.get("codec"),
            "sample_rate": media.get("sample_rate"),
            "channels": media.get("channels"),
        },
        "source_range": {
            "from": format_timecode(source_from),
            "to": format_timecode(effective_source_to),
            "duration": format_timecode(selected_duration),
        },
        "placed_range": {
            "from": format_timecode(at),
            "to": format_timecode(at + effective_duration),
            "duration": format_timecode(effective_duration),
        },
        "clipped_at_result_end": (
            not bool(loop) and selected_duration > available_result + 0.001
        ),
        "loop_count": (
            round(effective_duration / selected_duration, 3) if loop else 1.0
        ),
    }


def _duck_graph_errors(
    tracks: list[dict],
    errors: list[dict[str, str]],
    *,
    declared_ids: list[str],
) -> None:
    ids = [track.get("id") for track in tracks if track.get("id")]
    valid = {"source", *declared_ids}
    seen: set[str] = set()
    for track in tracks:
        index = track["_document_index"]
        track_id = track.get("id")
        if track_id in seen:
            errors.append(
                _item(
                    f"audio_mix.tracks[{index}].id",
                    f"duplicate track id {track_id!r}",
                )
            )
        if track_id:
            seen.add(track_id)
        ducking = track.get("ducking")
        if not ducking:
            continue
        for side_index, sidechain in enumerate(ducking["under"]):
            path = f"audio_mix.tracks[{index}].ducking.under[{side_index}]"
            if sidechain not in valid:
                errors.append(
                    _item(
                        path,
                        f"unknown sidechain {sidechain!r}; available: "
                        f"{', '.join(sorted(valid))}",
                    )
                )
            if sidechain == track_id:
                errors.append(_item(path, "a track cannot duck under itself"))

    graph = {
        track["id"]: [
            sidechain
            for sidechain in (track.get("ducking") or {}).get("under", [])
            if sidechain != "source" and sidechain in ids
        ]
        for track in tracks
        if track.get("id")
    }

    def visit(node: str, active: list[str], done: set[str]) -> list[str] | None:
        if node in active:
            start = active.index(node)
            return active[start:] + [node]
        if node in done:
            return None
        active.append(node)
        for child in graph.get(node, []):
            cycle = visit(child, active, done)
            if cycle:
                return cycle
        active.pop()
        done.add(node)
        return None

    done: set[str] = set()
    for node in graph:
        cycle = visit(node, [], done)
        if cycle:
            errors.append(
                _item(
                    "audio_mix.tracks",
                    f"ducking cycle detected: {' -> '.join(cycle)}",
                )
            )
            break


def normalize_audio_document(
    document: object,
    *,
    result_duration: float,
    base_dir: Path,
    probe_audio: Callable[[str], dict],
) -> dict:
    errors: list[dict[str, str]] = []
    if not isinstance(document, dict):
        raise AudioSurfaceValidationError([_item("$", "must be a JSON object")])
    _unknown_keys(document, _DOCUMENT_KEYS, "", errors)
    if document.get("version") != AUDIO_DOCUMENT_VERSION:
        errors.append(
            _item("version", f"must equal {AUDIO_DOCUMENT_VERSION}")
        )
    raw_mix = document.get("audio_mix")
    if not isinstance(raw_mix, dict):
        errors.append(_item("audio_mix", "must be an object"))
        raise AudioSurfaceValidationError(errors)
    _unknown_keys(raw_mix, _MIX_KEYS, "audio_mix", errors)
    source = _source_audio(raw_mix.get("source_audio", {}), result_duration, errors)
    raw_tracks = raw_mix.get("tracks")
    if not isinstance(raw_tracks, list):
        errors.append(_item("audio_mix.tracks", "must be an array"))
        raw_tracks = []
    tracks: list[dict] = []
    for index, raw_track in enumerate(raw_tracks):
        normalized = _track(
            raw_track,
            index,
            result_duration=result_duration,
            base_dir=base_dir,
            probe_audio=probe_audio,
            errors=errors,
        )
        if normalized is not None:
            tracks.append(normalized)
    declared_ids = [
        track["id"].strip()
        for track in raw_tracks
        if isinstance(track, dict)
        and isinstance(track.get("id"), str)
        and track["id"].strip()
    ]
    _duck_graph_errors(tracks, errors, declared_ids=declared_ids)
    if errors:
        raise AudioSurfaceValidationError(errors)
    for track in tracks:
        track.pop("_document_index", None)
    return {"source_audio": source, "tracks": tracks}


def resolve_audio_mix(
    audio_mix: dict,
    *,
    result_duration: float,
    base_dir: Path,
    probe_audio: Callable[[str], dict],
) -> dict:
    """Resolve persisted audio intent into the complete read envelope shape."""
    return normalize_audio_document(
        {"version": AUDIO_DOCUMENT_VERSION, "audio_mix": copy.deepcopy(audio_mix)},
        result_duration=result_duration,
        base_dir=base_dir,
        probe_audio=probe_audio,
    )


def editable_audio_document(audio_mix: dict) -> dict:
    tracks = []
    for track in audio_mix.get("tracks", []):
        editable = {
            key: copy.deepcopy(track.get(key)) for key in _TRACK_KEY_ORDER
        }
        if editable.get("ducking") is not None:
            editable["ducking"].pop("resolved", None)
        tracks.append(editable)
    return {
        "version": AUDIO_DOCUMENT_VERSION,
        "_examples": audio_document_examples(),
        "audio_mix": {
            "source_audio": copy.deepcopy(audio_mix.get("source_audio")),
            "tracks": tracks,
        },
    }


def persisted_audio_mix(resolved_audio_mix: dict) -> dict:
    """Strip resolver-only fields before storing audio intent in spec.json."""
    return editable_audio_document(resolved_audio_mix)["audio_mix"]


def audio_mix_summary(audio_mix: dict) -> dict:
    tracks = copy.deepcopy(audio_mix.get("tracks") or [])
    return {
        "source_audio": copy.deepcopy(
            audio_mix.get("source_audio") or default_audio_mix()["source_audio"]
        ),
        "tracks_count": len(tracks),
        "track_ids": [track.get("id") for track in tracks],
        "tracks": tracks,
        "ducked_tracks_count": sum(bool(track.get("ducking")) for track in tracks),
    }
