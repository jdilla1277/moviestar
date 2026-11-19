"""Metadata-only initial-edit reads and trim dry runs using the existing engine.

No media is opened and no project/spec is written. This deliberately supports an
unsaved baseline only; it is not a second resolver or a saved-revision store.
"""
from __future__ import annotations

import copy
import re
from uuid import UUID

from moviestar.spec import (
    REVISION_FIELDS, SpecValidationError, append_revision, append_trim,
    empty_spec, resolve_source, validate_spec,
)


class EditValidationError(ValueError):
    def __init__(self, code: str, message: str, field: str = "operation"):
        super().__init__(message)
        self.code, self.field = code, field


def _integer(value, low, high, field):
    if type(value) is not int or not low <= value <= high:
        raise EditValidationError("invalid_edit", f"Use an integer between {low} and {high}.", field)
    return value


def _sources(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 16:
        raise EditValidationError("invalid_edit", "Use 1–16 ready video source references.", "sources")
    seen = set()
    sources = []
    for value in values:
        if not isinstance(value, dict) or set(value) != {"source_id", "asset_id", "media"}:
            raise EditValidationError("invalid_edit", "Use a portable source ID, asset ID and measured media.", "sources")
        source = value["source_id"]
        if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", source) or source in seen:
            raise EditValidationError("invalid_edit", "Use unique stable source IDs.", "source_id")
        seen.add(source)
        try:
            asset = str(UUID(value["asset_id"]))
        except (ValueError, TypeError, AttributeError):
            raise EditValidationError("invalid_edit", "Use an asset UUID, not a path or URL.", "asset_id") from None
        media = value["media"]
        if not isinstance(media, dict) or set(media) != {"duration_ms", "width", "height", "audio_codec"}:
            raise EditValidationError("invalid_edit", "Measured video metadata is required.", "media")
        duration = _integer(media["duration_ms"], 1, 86400000, "media.duration_ms")
        width = _integer(media["width"], 1, 65536, "media.width")
        height = _integer(media["height"], 1, 65536, "media.height")
        audio = media["audio_codec"]
        if audio is not None and (not isinstance(audio, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", audio)):
            raise EditValidationError("invalid_edit", "Use a measured codec or null.", "media.audio_codec")
        sources.append(dict(id=source, path=f"asset:{asset}", duration_ms=duration,
                            width=width, height=height, audio_codec=audio))
    return sources


def _resolved(spec, sources):
    states = []
    for source in sources:
        timeline = resolve_source(spec, source["id"], source["duration_ms"] / 1000)
        states.append(dict(source_id=source["id"], source_duration_ms=source["duration_ms"],
                           result_duration_ms=round(timeline.effective_duration * 1000),
                           source_segments_ms=[[round(a * 1000), round(b * 1000)] for a, b in timeline.source_segments],
                           operations_applied=timeline.operations_applied))
    return dict(sources=states, composition=None, canvas=None,
                result_duration_ms=states[0]["result_duration_ms"] if len(states) == 1 else None,
                undo_remaining=len(spec["revisions"]))


def prepare_initial_edit(sources: list[dict], operation: dict | None = None) -> dict:
    """Read explicit unsaved state, or project one trim without persistent IO.

    Authorization and metadata freshness belong to the service. Callers must
    not treat this function or a client-provided result as an access grant.
    """
    sources = _sources(copy.deepcopy(sources))
    spec = empty_spec([dict(id=s["id"], path=s["path"]) for s in sources])
    result = dict(origin="unsaved_baseline", spec=spec, resolved=_resolved(spec, sources))
    if operation is None:
        return result
    if not isinstance(operation, dict) or set(operation) != {"type", "source_id", "time_space", "from_ms", "to_ms"}:
        raise EditValidationError("invalid_edit", "Trim requires type, source_id, time_space, from_ms and to_ms.")
    if operation["type"] != "trim" or operation["time_space"] != "source_result":
        raise EditValidationError("unsupported_feature", "Only trim in source_result time is available.")
    selected = next((s for s in sources if s["id"] == operation["source_id"]), None)
    if selected is None:
        raise EditValidationError("invalid_edit", "Select a source returned by the project edit read.", "operation.source_id")
    start = _integer(operation["from_ms"], 0, 86400000, "operation.from_ms")
    end = _integer(operation["to_ms"], 1, 86400000, "operation.to_ms")
    try:
        candidate = append_trim(spec, selected["id"], start / 1000, end / 1000, selected["duration_ms"] / 1000)
        candidate = append_revision(spec, candidate, "trim")
        validate_spec(candidate)
    except SpecValidationError as error:
        raise EditValidationError("invalid_edit", str(error)) from None
    result.update(changes=bool(candidate["revisions"]),
                  changed_fields=[f for f in REVISION_FIELDS if spec.get(f) != candidate.get(f)],
                  current_undo_remaining=0, candidate_spec=candidate,
                  projected_resolved=_resolved(candidate, sources), warnings=[])
    return result
