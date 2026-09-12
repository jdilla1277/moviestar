"""Edit spec — schema, resolver, and pure mutations.

The spec is JSON on disk at ``moviestar/spec.json``. It is versioned and
validated against a bundled JSON Schema before every write.

Version 0.2 supports multi-source projects: each source carries its own
operations stack (independent edit history). The optional ``composition``
block stitches per-source edits into a unified result. Version 0.1 is no
longer supported; the old single-source code paths have been removed.

The ``cut`` operation composes with trim in result-time: the agent sees one
contiguous result regardless of how many segments are stitched together
underneath. :class:`EffectiveTimeline` exposes the segment list via
``source_segments`` for callers that need precise rendering;
``source_range`` stays a single tuple (outer envelope) for backward
compatibility with trim-only callers.

Trim and cut timecodes are in **result-time** — a subsequent op
operates on the current result, not the original source.
:func:`resolve_source` walks one source's operation stack and returns
the effective timeline.

On-disk timecodes are compact strings (``"H:MM:SS.mmm"``); envelopes
expand to the dual ``{text, seconds}`` shape via the bridge in
:mod:`moviestar.timecodes`. The compact form keeps multi-segment
specs readable.

Public surface:

- :class:`EffectiveTimeline` — composed result for one source.
- :class:`TimelineStep` — one entry in a source's per-step history.
- :class:`SpecValidationError` — raised by validation and bounds checks.
- :func:`validate_spec` — schema enforcement.
- :func:`resolve_source` — pure per-source resolver; no I/O.
- :func:`resolve_source_history` — per-step state for the
  ``moviestar history`` command.
- :func:`resolve_all_sources` — dict of per-source timelines.
- :func:`get_source` / :func:`source_ids` — helpers.
- :func:`empty_spec` — seed spec from a list of source dicts.
- :func:`append_trim` — append a trim to one source's stack.
- :func:`append_cut` — append a cut to one source's stack.
- :func:`pop_last_op` — pop the last op from one source's stack.
- :func:`load_spec` / :func:`save_spec` — disk I/O. Old v0.1 specs
  surface a structured "reload the project" error on read.
- :func:`parse_timecode_string` — string → seconds bridge for the
  compact on-disk timecode form.

The public surface above is the compatibility boundary for callers; internal
implementation history is not required to understand or use it.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from moviestar.project import MOVIESTAR_DIR
from moviestar.audio_surface import (
    AUDIO_DUCKING_PRESETS,
    AUDIO_GAIN_MAX_DB,
    AUDIO_GAIN_MIN_DB,
    AUDIO_KINDS,
    default_audio_mix,
)
from moviestar.timecodes import format_timecode


SCHEMA_VERSION = "0.2"
TRANSITION_TYPES = ("dissolve", "dip-black", "dip-white")
EDGE_TRANSITION_TYPES = ("dip-black", "dip-white")
LEGACY_GLOBAL_HISTORY_FIELDS = (
    "composition_history",
    "overlays_history",
    "audio_mix_history",
    "motion_history",
    "captions_history",
)


def _legacy_history_port_message(fields: list[str]) -> str:
    names = ", ".join(fields)
    return (
        "This spec uses the retired global undo history fields: "
        f"{names}. Its current edit is portable, but its old undo history "
        "cannot be preserved because the separate stacks did not record "
        "cross-command order. Preserve every current-state field, delete "
        "the five legacy history fields, and start the new journal with "
        "\"revisions\": []. Edits made before the port cannot be undone; "
        "the preserved edit becomes the new starting point. Backup-first "
        "port template (run from the "
        "project root): cp moviestar/spec.json "
        "moviestar/spec.pre-stage7.json && jq "
        "'del(.composition_history, .overlays_history, .audio_mix_history, "
        ".motion_history, .captions_history) | .revisions = []' "
        "moviestar/spec.pre-stage7.json > moviestar/spec.stage7.json && "
        "mv moviestar/spec.stage7.json moviestar/spec.json"
    )


CANVAS_PRESETS = {
    "short": (1080, 1920, "9:16"),
    "square": (1080, 1080, "1:1"),
    "landscape": (1920, 1080, "16:9"),
}

LAYOUT_PRESETS = {
    "single": {
        "slots": ["main"],
        "description": "One source fills the whole canvas.",
    },
    "two-up": {
        "slots_portrait": ["top", "bottom"],
        "slots_landscape": ["left", "right"],
        "description": (
            "Two equal regions. Portrait canvases use top/bottom; "
            "landscape canvases use left/right."
        ),
    },
    "picture-in-picture": {
        "slots": ["main", "inset"],
        "description": (
            "Main source fills the canvas; inset source appears over it "
            "at the resolved slot geometry."
        ),
    },
}


def _canvas_label(canvas: dict) -> str:
    return canvas["preset"] or f"{canvas['width']}x{canvas['height']}"


FRAMING_MODES = {"fit", "fill"}
FRAMING_ANCHORS = {"center", "left", "right", "top", "bottom"}
SCHEMA_FILENAME = "spec-0.2.json"
SPEC_FILE = "spec.json"


class SpecValidationError(ValueError):
    """Raised when a spec violates its schema or a bounds check fails."""


class SceneValidationError(SpecValidationError):
    """Scene-authoring validation failure with a JSON-addressable path."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


class SceneSlotDurationMismatchError(SceneValidationError):
    """Scene slots resolve to different unpaced durations."""


@dataclass(frozen=True)
class EffectiveTimeline:
    """The result of composing one source's edit stack.

    ``source_segments`` is a tuple of ``(from_seconds, to_seconds)``
    pairs in SOURCE time — the actual ranges to read from the raw file
    when rendering. For trim-only stacks this is always a single
    segment; cut ops can split the result into multiple stitched
    segments.

    ``source_range`` is the outer envelope ``(first_from, last_to)`` of
    the segments. For trim-only callers it's identical to today's
    behavior. For multi-segment results it's a lossy summary — code
    that needs to render correctly across cuts must use
    ``source_segments``.

    ``effective_duration`` is the total length of the edited result —
    what the agent sees on this source.
    """

    source_id: str
    source_range: tuple[float, float]
    source_segments: tuple[tuple[float, float], ...]
    effective_duration: float
    source_duration: float
    operations_applied: int


@dataclass(frozen=True)
class TimelineStep:
    """One step in a source's edit lineage.

    ``op_index`` is 0 for the initial state (before any op) and
    increments by 1 per op. ``op`` is ``None`` at index 0 and the
    op dict otherwise. ``timeline`` is the resolved state after the
    op at this index has been applied.

    Consumed by :func:`resolve_source_history` and surfaced by the
    ``moviestar history`` command.
    """

    op_index: int
    op: dict | None
    timeline: EffectiveTimeline


# ---------- Timecode parsing ----------


def parse_timecode_string(text: str, where: str) -> float:
    """Parse an on-disk timecode string ("H:MM:SS.mmm") to seconds.

    Schema validation rejects malformed strings up front; this is the
    runtime parser used by the resolver.
    """
    try:
        h_str, m_str, s_str = text.split(":")
        seconds = int(h_str) * 3600 + int(m_str) * 60 + float(s_str)
    except (ValueError, AttributeError) as exc:
        raise SpecValidationError(
            f"{where}: malformed timecode {text!r} (expected H:MM:SS.mmm)"
        ) from exc
    return round(seconds, 3)


def _format_timecode_string(seconds: float) -> str:
    """Format float seconds as an on-disk timecode string. Mirror of
    :func:`parse_timecode_string`."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - hours * 3600 - minutes * 60
    return f"{hours}:{minutes:02d}:{secs:06.3f}"


# ---------- Validation ----------
#
# Hand-rolled to avoid a new dep. The predicates below enforce the same
# shape as ``schema/spec-0.2.json``; the JSON file is the agent-readable
# contract; this is the runtime gate. If they drift, the JSON file is
# the source of truth.


_TIMECODE_PATTERN_HINT = "H:MM:SS.mmm (e.g. '0:01:23.456')"


def _require_field(obj: dict, field: str, context: str) -> None:
    if field not in obj:
        raise SpecValidationError(f"{context}: missing required field '{field}'")


def _validate_timecode(value, where: str) -> None:
    if not isinstance(value, str):
        raise SpecValidationError(
            f"{where}: expected timecode string in {_TIMECODE_PATTERN_HINT}, "
            f"got {type(value).__name__}"
        )
    parts = value.split(":")
    if len(parts) != 3:
        raise SpecValidationError(
            f"{where}: malformed timecode {value!r} (expected {_TIMECODE_PATTERN_HINT})"
        )


def _validate_range_op(op: dict, op_name: str, source_id: str, index: int) -> None:
    """Shared shape check for trim and cut — same on-disk shape, same
    bounds rules. Caller-side bounds against the resolved result live
    in :func:`append_trim` / :func:`append_cut`.
    """
    where = f"sources[{source_id}].operations[{index}] ({op_name})"
    _require_field(op, "from", where)
    _require_field(op, "to", where)
    _validate_timecode(op["from"], f"{where}.from")
    _validate_timecode(op["to"], f"{where}.to")
    from_s = parse_timecode_string(op["from"], f"{where}.from")
    to_s = parse_timecode_string(op["to"], f"{where}.to")
    if from_s < 0:
        raise SpecValidationError(f"{where}: 'from' cannot be negative")
    if from_s >= to_s:
        raise SpecValidationError(
            f"{where}: 'from' ({from_s}s) must be before 'to' ({to_s}s)"
        )


def _validate_trim(op: dict, source_id: str, index: int) -> None:
    _validate_range_op(op, "trim", source_id, index)


def _validate_cut(op: dict, source_id: str, index: int) -> None:
    _validate_range_op(op, "cut", source_id, index)


def _validate_composition(
    segments: list,
    valid_source_ids: set[str],
    where: str = "composition",
    canvas: dict | None = None,
) -> None:
    """Shape check for a composition array.

    Flat compositions are segment arrays. Global layouts are stored as a
    one-item scene array, while scene layouts extend that shape to multiple
    ordered layout scenes. Mixed shapes are invalid.
    """
    if not isinstance(segments, list):
        raise SpecValidationError(f"{where}: must be an array")
    if segments and isinstance(segments[0], dict) and (
        "layout" in segments[0] or "slots" in segments[0]
    ):
        _validate_layout_composition(segments, valid_source_ids, where, canvas)
        return
    for i, seg in enumerate(segments):
        loc = f"{where}[{i}]"
        if not isinstance(seg, dict):
            raise SpecValidationError(f"{loc}: must be an object")
        if "layout" in seg or "slots" in seg:
            raise SpecValidationError(
                f"{loc}: cannot mix layout scenes with flat composition segments"
            )
        for field in ("source", "source_from", "source_to"):
            _require_field(seg, field, loc)
        if not isinstance(seg["source"], str) or not seg["source"]:
            raise SpecValidationError(f"{loc}.source: must be a non-empty string")
        if seg["source"] not in valid_source_ids:
            raise SpecValidationError(
                f"{loc}.source: unknown source {seg['source']!r}; "
                f"available: {', '.join(sorted(valid_source_ids))}"
            )
        _validate_timecode(seg["source_from"], f"{loc}.source_from")
        _validate_timecode(seg["source_to"], f"{loc}.source_to")
        from_s = parse_timecode_string(seg["source_from"], f"{loc}.source_from")
        to_s = parse_timecode_string(seg["source_to"], f"{loc}.source_to")
        if from_s < 0:
            raise SpecValidationError(f"{loc}: 'source_from' cannot be negative")
        if from_s >= to_s:
            raise SpecValidationError(
                f"{loc}: 'source_from' ({from_s}s) must be before 'source_to' "
                f"({to_s}s)"
            )
        if "framing" in seg:
            _validate_framing(seg["framing"], f"{loc}.framing")


def _validate_layout_inset(inset: object, preset: str, where: str) -> None:
    if inset is None:
        return
    if preset != "picture-in-picture":
        raise SpecValidationError(
            f"{where}.inset: only applies to the picture-in-picture layout"
        )
    if not isinstance(inset, dict):
        raise SpecValidationError(f"{where}.inset: must be an object")
    unknown = set(inset) - {"corner", "width", "height"}
    if unknown:
        raise SpecValidationError(
            f"{where}.inset: unknown field(s) "
            f"{', '.join(sorted(unknown))}; allowed: corner, width, height"
        )
    corner = inset.get("corner")
    if corner is not None and corner not in PIP_INSET_CORNERS:
        raise SpecValidationError(
            f"{where}.inset.corner: must be one of "
            f"{', '.join(PIP_INSET_CORNERS)}"
        )
    for field in ("width", "height"):
        value = inset.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (
            PIP_INSET_MIN_FRACTION <= float(value) <= PIP_INSET_MAX_FRACTION
        ):
            raise SpecValidationError(
                f"{where}.inset.{field}: must be a canvas fraction between "
                f"{PIP_INSET_MIN_FRACTION} and {PIP_INSET_MAX_FRACTION}"
            )


def _validate_transition(
    transition: object,
    where: str,
    *,
    allowed_types: tuple[str, ...] = TRANSITION_TYPES,
) -> None:
    """Validate one persisted visual-transition record."""
    if not isinstance(transition, dict):
        raise SpecValidationError(f"{where}: must be an object")
    unknown = sorted(set(transition) - {"type", "duration"})
    if unknown:
        raise SpecValidationError(
            f"{where}: unknown field(s) {', '.join(unknown)}; "
            "allowed: type, duration"
        )
    for field in ("type", "duration"):
        _require_field(transition, field, where)
    if transition["type"] not in allowed_types:
        raise SpecValidationError(
            f"{where}.type: must be one of {', '.join(allowed_types)}"
        )
    duration = transition["duration"]
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or float(duration) <= 0
    ):
        raise SpecValidationError(
            f"{where}.duration: must be a finite number greater than zero"
        )


def _validate_layout_composition(
    scenes: list,
    valid_source_ids: set[str],
    where: str,
    canvas: dict | None,
) -> None:
    if canvas is None:
        raise SpecValidationError(
            f"{where}: layout compositions require composition_canvas"
        )
    if not scenes:
        raise SpecValidationError(f"{where}: layout compositions need a scene")
    seen_identity_ids: set[str] = set()
    for scene_index, scene in enumerate(scenes):
        loc = f"{where}[{scene_index}]"
        if not isinstance(scene, dict):
            raise SpecValidationError(f"{loc}: must be an object")
        _require_field(scene, "layout", loc)
        _require_field(scene, "slots", loc)
        if "name" in scene and (
            not isinstance(scene["name"], str) or not scene["name"].strip()
        ):
            raise SpecValidationError(f"{loc}.name: must be a non-empty string")
        if "id" in scene and (
            not isinstance(scene["id"], str) or not scene["id"].strip()
        ):
            raise SpecValidationError(f"{loc}.id: must be a non-empty string")
        if scene.get("id") in seen_identity_ids:
            raise SpecValidationError(
                f"{loc}.id: duplicate scene/slot identity {scene['id']!r}"
            )
        if scene.get("id"):
            seen_identity_ids.add(scene["id"])
        if "transition_in" in scene:
            if scene_index == 0:
                raise SpecValidationError(
                    f"{loc}.transition_in: the first scene has no internal "
                    "boundary; use opening_transition for a project-edge fade"
                )
            _validate_transition(scene["transition_in"], f"{loc}.transition_in")

        layout = scene["layout"]
        if not isinstance(layout, dict):
            raise SpecValidationError(f"{loc}.layout: must be an object")
        _require_field(layout, "preset", f"{loc}.layout")
        preset = layout["preset"]
        if preset not in LAYOUT_PRESETS:
            raise SpecValidationError(
                f"{loc}.layout.preset: must be one of "
                f"{', '.join(sorted(LAYOUT_PRESETS))}"
            )
        orientation = layout.get("orientation")
        expected_orientation = layout_orientation(canvas)
        if orientation is not None and orientation != expected_orientation:
            raise SpecValidationError(
                f"{loc}.layout.orientation: expected {expected_orientation!r} "
                f"for this canvas"
            )
        _validate_layout_inset(layout.get("inset"), preset, f"{loc}.layout")
        _validate_layout_geometry(
            layout.get("geometry"), preset, f"{loc}.layout.geometry", canvas
        )
        _validate_shape_framing(scene, loc)
        if (
            layout.get("inset") is not None
            and layout.get("geometry") is not None
        ):
            raise SpecValidationError(
                f"{loc}.layout: 'inset' and 'geometry' cannot both be set; "
                "'geometry' is the canonical shape — remove the legacy "
                "'inset' or drop 'geometry'"
            )

        slots = scene["slots"]
        if not isinstance(slots, list) or not slots:
            raise SpecValidationError(f"{loc}.slots: must be a non-empty array")
        expected_slots = set(layout_slots(preset, canvas))
        seen_slots: set[str] = set()
        slot_sources: set[str] = set()
        durations: set[float] = set()
        for i, slot in enumerate(slots):
            slot_loc = f"{loc}.slots[{i}]"
            if not isinstance(slot, dict):
                raise SpecValidationError(f"{slot_loc}: must be an object")
            if "id" in slot and (
                not isinstance(slot["id"], str) or not slot["id"].strip()
            ):
                raise SpecValidationError(
                    f"{slot_loc}.id: must be a non-empty string"
                )
            if slot.get("id") in seen_identity_ids:
                raise SpecValidationError(
                    f"{slot_loc}.id: duplicate scene/slot identity "
                    f"{slot['id']!r}"
                )
            if slot.get("id"):
                seen_identity_ids.add(slot["id"])
            for field in ("slot", "source", "source_from", "source_to"):
                _require_field(slot, field, slot_loc)
            slot_name = slot["slot"]
            if slot_name not in expected_slots:
                raise SpecValidationError(
                    f"{slot_loc}.slot: unknown slot {slot_name!r}; expected "
                    f"{', '.join(sorted(expected_slots))}"
                )
            if slot_name in seen_slots:
                raise SpecValidationError(
                    f"{slot_loc}.slot: duplicate slot {slot_name!r}"
                )
            seen_slots.add(slot_name)
            source = slot["source"]
            if not isinstance(source, str) or not source:
                raise SpecValidationError(
                    f"{slot_loc}.source: must be a non-empty string"
                )
            if source not in valid_source_ids:
                raise SpecValidationError(
                    f"{slot_loc}.source: unknown source {source!r}; available: "
                    f"{', '.join(sorted(valid_source_ids))}"
                )
            slot_sources.add(source)
            _validate_timecode(slot["source_from"], f"{slot_loc}.source_from")
            _validate_timecode(slot["source_to"], f"{slot_loc}.source_to")
            from_s = parse_timecode_string(
                slot["source_from"], f"{slot_loc}.source_from"
            )
            to_s = parse_timecode_string(slot["source_to"], f"{slot_loc}.source_to")
            if from_s < 0:
                raise SpecValidationError(
                    f"{slot_loc}.source_from: cannot be negative (got {from_s}s)"
                )
            if from_s >= to_s:
                raise SpecValidationError(
                    f"{slot_loc}: 'source_from' ({from_s}s) must be before "
                    f"'source_to' ({to_s}s)"
                )
            durations.add(round(to_s - from_s, 3))
            if "framing" in slot:
                _validate_framing(slot["framing"], f"{slot_loc}.framing")
        missing = expected_slots - seen_slots
        if missing:
            raise SpecValidationError(
                f"{loc}.slots: missing required slot(s): {', '.join(sorted(missing))}"
            )
        if len(durations) > 1:
            raise SpecValidationError(
                f"{loc}.slots: all slots must have equal duration"
            )
        audio_from = scene.get("audio_from")
        if audio_from is not None:
            if not isinstance(audio_from, str) or not audio_from.strip():
                raise SpecValidationError(
                    f"{loc}.audio_from: must be a non-empty string"
                )
            if audio_from not in slot_sources:
                raise SpecValidationError(
                    f"{loc}.audio_from: {audio_from!r} must be assigned to "
                    f"a layout slot. Slot sources: {', '.join(sorted(slot_sources))}."
                )


def _validate_source(source: dict, index: int) -> None:
    where = f"sources[{index}]"
    _require_field(source, "id", where)
    _require_field(source, "path", where)
    _require_field(source, "operations", where)
    if not isinstance(source["id"], str) or not source["id"]:
        raise SpecValidationError(f"{where}.id: must be a non-empty string")
    if not isinstance(source["path"], str) or not source["path"]:
        raise SpecValidationError(f"{where}.path: must be a non-empty string")
    ops = source["operations"]
    if not isinstance(ops, list):
        raise SpecValidationError(f"{where}.operations: must be an array")
    sid = source["id"]
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise SpecValidationError(
                f"sources[{sid}].operations[{i}]: must be an object"
            )
        _require_field(op, "type", f"sources[{sid}].operations[{i}]")
        if op["type"] == "trim":
            _validate_trim(op, sid, i)
        elif op["type"] == "cut":
            _validate_cut(op, sid, i)
        else:
            raise SpecValidationError(
                f"sources[{sid}].operations[{i}]: unknown type "
                f"{op['type']!r} (supported: 'trim', 'cut')"
            )


def _validate_audio_number(
    value,
    where: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecValidationError(f"{where}: must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise SpecValidationError(f"{where}: must be a finite number")
    if minimum is not None and number < minimum:
        raise SpecValidationError(f"{where}: must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise SpecValidationError(f"{where}: must be at most {maximum}")


def _validate_audio_mix(audio_mix, where: str = "audio_mix") -> None:
    """Validate persisted audio intent without probing external files."""
    if not isinstance(audio_mix, dict):
        raise SpecValidationError(f"{where}: must be an object")
    for field in ("source_audio", "tracks"):
        _require_field(audio_mix, field, where)

    source = audio_mix["source_audio"]
    source_where = f"{where}.source_audio"
    if not isinstance(source, dict):
        raise SpecValidationError(f"{source_where}: must be an object")
    for field in ("muted", "gain_db", "fade_in", "fade_out"):
        _require_field(source, field, source_where)
    if not isinstance(source["muted"], bool):
        raise SpecValidationError(f"{source_where}.muted: must be true or false")
    _validate_audio_number(
        source["gain_db"],
        f"{source_where}.gain_db",
        minimum=AUDIO_GAIN_MIN_DB,
        maximum=AUDIO_GAIN_MAX_DB,
    )
    for field in ("fade_in", "fade_out"):
        _validate_audio_number(source[field], f"{source_where}.{field}", minimum=0)

    tracks = audio_mix["tracks"]
    if not isinstance(tracks, list):
        raise SpecValidationError(f"{where}.tracks: must be an array")
    ids: list[str] = []
    for index, track in enumerate(tracks):
        track_where = f"{where}.tracks[{index}]"
        if not isinstance(track, dict):
            raise SpecValidationError(f"{track_where}: must be an object")
        for field in (
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
        ):
            _require_field(track, field, track_where)
        track_id = track["id"]
        if not isinstance(track_id, str) or not track_id.strip():
            raise SpecValidationError(f"{track_where}.id: must be non-empty")
        if track_id == "source":
            raise SpecValidationError(
                f"{track_where}.id: 'source' is reserved for routed source audio"
            )
        if track_id in ids:
            raise SpecValidationError(
                f"{where}.tracks: duplicate id {track_id!r}"
            )
        ids.append(track_id)
        if track["kind"] not in AUDIO_KINDS:
            raise SpecValidationError(
                f"{track_where}.kind: must be one of "
                f"{', '.join(sorted(AUDIO_KINDS))}"
            )
        if not isinstance(track["path"], str) or not track["path"]:
            raise SpecValidationError(f"{track_where}.path: must be non-empty")
        for field in ("at", "source_from"):
            _validate_timecode(track[field], f"{track_where}.{field}")
            if parse_timecode_string(track[field], f"{track_where}.{field}") < 0:
                raise SpecValidationError(
                    f"{track_where}.{field}: cannot be negative"
                )
        source_to = track["source_to"]
        if source_to is not None:
            _validate_timecode(source_to, f"{track_where}.source_to")
            source_from_s = parse_timecode_string(
                track["source_from"], f"{track_where}.source_from"
            )
            source_to_s = parse_timecode_string(
                source_to, f"{track_where}.source_to"
            )
            if source_from_s >= source_to_s:
                raise SpecValidationError(
                    f"{track_where}.source_to: must be after source_from"
                )
        _validate_audio_number(
            track["gain_db"],
            f"{track_where}.gain_db",
            minimum=AUDIO_GAIN_MIN_DB,
            maximum=AUDIO_GAIN_MAX_DB,
        )
        for field in ("muted", "loop"):
            if not isinstance(track[field], bool):
                raise SpecValidationError(
                    f"{track_where}.{field}: must be true or false"
                )
        for field in ("fade_in", "fade_out"):
            _validate_audio_number(
                track[field], f"{track_where}.{field}", minimum=0
            )
        ducking = track["ducking"]
        if ducking is None:
            continue
        if not isinstance(ducking, dict):
            raise SpecValidationError(
                f"{track_where}.ducking: must be an object or null"
            )
        for field in ("under", "preset"):
            _require_field(ducking, field, f"{track_where}.ducking")
        under = ducking["under"]
        if not isinstance(under, list) or not under:
            raise SpecValidationError(
                f"{track_where}.ducking.under: must be a non-empty array"
            )
        if any(not isinstance(value, str) or not value for value in under):
            raise SpecValidationError(
                f"{track_where}.ducking.under: ids must be non-empty strings"
            )
        if len(set(under)) != len(under):
            raise SpecValidationError(
                f"{track_where}.ducking.under: ids must be unique"
            )
        if ducking["preset"] not in AUDIO_DUCKING_PRESETS:
            raise SpecValidationError(
                f"{track_where}.ducking.preset: must be one of "
                f"{', '.join(sorted(AUDIO_DUCKING_PRESETS))}"
            )

    valid_sidechains = {"source", *ids}
    graph: dict[str, list[str]] = {}
    for index, track in enumerate(tracks):
        ducking = track["ducking"]
        graph[track["id"]] = []
        if ducking is None:
            continue
        for side_index, sidechain in enumerate(ducking["under"]):
            side_where = (
                f"{where}.tracks[{index}].ducking.under[{side_index}]"
            )
            if sidechain not in valid_sidechains:
                raise SpecValidationError(
                    f"{side_where}: unknown sidechain {sidechain!r}; available: "
                    f"{', '.join(sorted(valid_sidechains))}"
                )
            if sidechain == track["id"]:
                raise SpecValidationError(
                    f"{side_where}: a track cannot duck under itself"
                )
            if sidechain != "source":
                graph[track["id"]].append(sidechain)

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
            raise SpecValidationError(
                f"{where}.tracks: ducking cycle detected: {' -> '.join(cycle)}"
            )


def validate_spec(spec: dict) -> None:
    """Validate against the v0.2 schema. Raises :class:`SpecValidationError`."""
    if not isinstance(spec, dict):
        raise SpecValidationError("spec must be a JSON object")

    legacy_histories = sorted(set(spec) & set(LEGACY_GLOBAL_HISTORY_FIELDS))
    if legacy_histories:
        raise SpecValidationError(_legacy_history_port_message(legacy_histories))

    _require_field(spec, "version", "spec")
    if spec["version"] != SCHEMA_VERSION:
        raise SpecValidationError(
            f"spec: unsupported version {spec['version']!r}; "
            f"expected {SCHEMA_VERSION!r}"
        )

    _require_field(spec, "sources", "spec")
    sources = spec["sources"]
    if not isinstance(sources, list):
        raise SpecValidationError("spec: 'sources' must be an array")
    if len(sources) == 0:
        raise SpecValidationError("spec: 'sources' must have at least one source")

    seen_ids: set[str] = set()
    for i, source in enumerate(sources):
        if not isinstance(source, dict):
            raise SpecValidationError(f"sources[{i}]: must be an object")
        _validate_source(source, i)
        sid = source["id"]
        if sid in seen_ids:
            raise SpecValidationError(f"sources: duplicate id {sid!r}")
        seen_ids.add(sid)

    canvas = spec.get("composition_canvas")
    if canvas is not None:
        _validate_canvas(canvas, "composition_canvas")

    # composition: null OR array of composition_segment dicts OR one
    # M17 global layout scene. M13a kept this null; M13b step 4 lights
    # up flat segments via `concat`.
    composition = spec.get("composition")
    if composition is not None:
        _validate_composition(composition, seen_ids, canvas=canvas)

    for field in ("opening_transition", "closing_transition"):
        transition = spec.get(field)
        if transition is not None:
            _validate_transition(
                transition,
                field,
                allowed_types=EDGE_TRANSITION_TYPES,
            )

    # composition_audio_from: M14 step 1.4 — top-level optional string
    # naming a source whose audio should play across the whole
    # composition. Null when not set. The source ID must exist in the
    # spec and must appear in the current composition's segment list
    # (enforced by set_composition; revalidated here only for shape).
    audio_from = spec.get("composition_audio_from")
    if audio_from is not None:
        if not isinstance(audio_from, str) or not audio_from:
            raise SpecValidationError(
                "spec: 'composition_audio_from' must be a non-empty string"
            )
        if audio_from not in seen_ids:
            raise SpecValidationError(
                f"spec: 'composition_audio_from' references unknown source "
                f"{audio_from!r}; available: {', '.join(sorted(seen_ids))}"
            )

    # composition_authored_as: stage-3 normalization marker recording
    # the vocabulary the agent authored (presentation only).
    authored_as = spec.get("composition_authored_as")
    if authored_as is not None and authored_as not in COMPOSITION_AUTHORED_AS:
        raise SpecValidationError(
            "spec: 'composition_authored_as' must be one of "
            f"{', '.join(sorted(COMPOSITION_AUTHORED_AS))} or null"
        )

    # overlays: M19 timed text overlays. Structural validation only —
    # preset names, CSS-subset semantics, and resolved-style derivation
    # live in the CLI layer, which writes the canonical shape validated
    # here. Captions and manual text share one shape (kind is the only
    # discriminator).
    overlays = spec.get("overlays", [])
    if not isinstance(overlays, list):
        raise SpecValidationError("spec: 'overlays' must be an array")
    seen_overlay_ids: set[str] = set()
    for i, overlay in enumerate(overlays):
        _validate_overlay(overlay, i)
        oid = overlay["id"]
        if oid in seen_overlay_ids:
            raise SpecValidationError(f"overlays: duplicate id {oid!r}")
        seen_overlay_ids.add(oid)

    _validate_audio_mix(spec.get("audio_mix", default_audio_mix()))

    motion = spec.get("motion", {"version": 1, "scenes": []})
    _validate_motion_document(motion, "motion")

    captions = spec.get("captions", [])
    _validate_caption_recipes(captions, "captions")
    _require_field(spec, "revisions", "spec")
    _validate_revisions(spec["revisions"], "revisions")
    _validate_revision_chain(spec)


def _validate_caption_recipes(recipes, where: str) -> None:
    """Caption recipes: the authoritative state of derived caption
    tracks. Stored cue overlays are a cache derived from these; the
    schema stays permissive because the CLI is the contract surface."""
    if not isinstance(recipes, list):
        raise SpecValidationError(f"spec: '{where}' must be an array")
    seen_tracks: set[str] = set()
    for i, recipe in enumerate(recipes):
        r_where = f"{where}[{i}]"
        if not isinstance(recipe, dict):
            raise SpecValidationError(f"{r_where}: must be an object")
        track = recipe.get("track")
        if not isinstance(track, str) or not track:
            raise SpecValidationError(
                f"{r_where}.track: must be a non-empty string"
            )
        if track in seen_tracks:
            raise SpecValidationError(
                f"{r_where}.track: duplicate recipe for track {track!r}"
            )
        seen_tracks.add(track)
        edits = recipe.get("edits", [])
        if not isinstance(edits, list):
            raise SpecValidationError(f"{r_where}.edits: must be an array")


def _validate_motion_range(value, where: str, expected_space: str) -> None:
    if not isinstance(value, dict):
        raise SpecValidationError(f"{where}: must be an object")
    for field in ("from", "to", "space"):
        _require_field(value, field, where)
    _validate_timecode(value["from"], f"{where}.from")
    _validate_timecode(value["to"], f"{where}.to")
    start_s = parse_timecode_string(value["from"], f"{where}.from")
    end_s = parse_timecode_string(value["to"], f"{where}.to")
    if start_s >= end_s:
        raise SpecValidationError(
            f"{where}: 'from' ({value['from']}) must be before 'to' "
            f"({value['to']})"
        )
    if value["space"] != expected_space:
        raise SpecValidationError(f"{where}.space: must be {expected_space!r}")


def _validate_motion_document(document, where: str) -> None:
    if not isinstance(document, dict):
        raise SpecValidationError(f"{where}: must be an object")
    if document.get("version") != 1:
        raise SpecValidationError(f"{where}.version: must be 1")
    scenes = document.get("scenes")
    if not isinstance(scenes, list):
        raise SpecValidationError(f"{where}.scenes: must be an array")
    seen_ids: set[str] = set()
    seen_scenes: set[str] = set()
    for scene_index, scene in enumerate(scenes):
        scene_where = f"{where}.scenes[{scene_index}]"
        if not isinstance(scene, dict):
            raise SpecValidationError(f"{scene_where}: must be an object")
        scene_name = scene.get("scene")
        if not isinstance(scene_name, str) or not scene_name:
            raise SpecValidationError(
                f"{scene_where}.scene: must be a non-empty string"
            )
        if scene_name in seen_scenes:
            raise SpecValidationError(
                f"{where}.scenes: duplicate scene {scene_name!r}"
            )
        seen_scenes.add(scene_name)
        scene_id = scene.get("scene_id")
        if scene_id is not None and (
            not isinstance(scene_id, str) or not scene_id.strip()
        ):
            raise SpecValidationError(
                f"{scene_where}.scene_id: must be a non-empty string"
            )
        slots = scene.get("slots")
        if not isinstance(slots, list):
            raise SpecValidationError(f"{scene_where}.slots: must be an array")
        seen_slots: set[str] = set()
        for slot_index, slot in enumerate(slots):
            slot_where = f"{scene_where}.slots[{slot_index}]"
            if not isinstance(slot, dict):
                raise SpecValidationError(f"{slot_where}: must be an object")
            slot_name = slot.get("slot")
            if not isinstance(slot_name, str) or not slot_name:
                raise SpecValidationError(
                    f"{slot_where}.slot: must be a non-empty string"
                )
            if slot_name in seen_slots:
                raise SpecValidationError(
                    f"{scene_where}.slots: duplicate slot {slot_name!r}"
                )
            seen_slots.add(slot_name)
            slot_id = slot.get("slot_id")
            if slot_id is not None and (
                not isinstance(slot_id, str) or not slot_id.strip()
            ):
                raise SpecValidationError(
                    f"{slot_where}.slot_id: must be a non-empty string"
                )
            pacing = slot.get("pacing", [])
            camera = slot.get("camera", [])
            if not isinstance(pacing, list):
                raise SpecValidationError(f"{slot_where}.pacing: must be an array")
            if not isinstance(camera, list):
                raise SpecValidationError(f"{slot_where}.camera: must be an array")
            for kind, entries in (("pacing", pacing), ("camera", camera)):
                for entry_index, entry in enumerate(entries):
                    entry_where = f"{slot_where}.{kind}[{entry_index}]"
                    if not isinstance(entry, dict):
                        raise SpecValidationError(f"{entry_where}: must be an object")
                    entry_id = entry.get("id")
                    if not isinstance(entry_id, str) or not entry_id:
                        raise SpecValidationError(
                            f"{entry_where}.id: must be a non-empty string"
                        )
                    if entry_id in seen_ids:
                        raise SpecValidationError(f"{where}: duplicate id {entry_id!r}")
                    seen_ids.add(entry_id)
                    if kind == "pacing":
                        _validate_pacing_record(entry, entry_where)
                    else:
                        _validate_camera_record(entry, entry_where)


def _validate_pacing_record(entry: dict, where: str) -> None:
    mode = entry.get("mode")
    if mode not in {"speed", "duration", "hold"}:
        raise SpecValidationError(
            f"{where}.mode: must be one of duration, hold, speed"
        )
    if mode == "hold":
        _validate_timecode(entry.get("at"), f"{where}.at")
        if entry.get("space") != "source-local":
            raise SpecValidationError(
                f"{where}.space: hold pacing must be 'source-local'"
            )
        _validate_timecode(entry.get("duration"), f"{where}.duration")
        if parse_timecode_string(entry["duration"], f"{where}.duration") <= 0:
            raise SpecValidationError(f"{where}.duration: must be positive")
        return
    _validate_motion_range(entry.get("range"), f"{where}.range", "source-local")
    if mode == "speed":
        speed = entry.get("speed")
        if (
            isinstance(speed, bool)
            or not isinstance(speed, (int, float))
            or speed <= 0
        ):
            raise SpecValidationError(f"{where}.speed: must be a positive number")
    else:
        _validate_timecode(entry.get("duration"), f"{where}.duration")
        if parse_timecode_string(entry["duration"], f"{where}.duration") <= 0:
            raise SpecValidationError(f"{where}.duration: must be positive")


def _validate_camera_record(entry: dict, where: str) -> None:
    # Detailed geometry, bounds, and interpolation validation is performed by
    # the camera resolver because it needs source dimensions and slot regions.
    kind = entry.get("kind")
    if kind is not None and kind != "zoom":
        raise SpecValidationError(
            f"{where}.kind: must be 'zoom' or omitted for a plain move"
        )
    if kind == "zoom":
        if "range" in entry:
            raise SpecValidationError(
                f"{where}: zoom records use 'at' (the arrival moment), "
                "not 'range'"
            )
        _validate_timecode(entry.get("at"), f"{where}.at")
        timing = entry.get("timing")
        if timing is not None:
            if not isinstance(timing, dict):
                raise SpecValidationError(
                    f"{where}.timing: must be an object with "
                    "move_in/hold/move_out seconds"
                )
            for key in ("move_in", "hold", "move_out"):
                if key not in timing:
                    continue
                value = timing[key]
                if isinstance(value, str):
                    seconds = parse_timecode_string(
                        value, f"{where}.timing.{key}"
                    )
                elif isinstance(value, bool) or not isinstance(
                    value, (int, float)
                ):
                    raise SpecValidationError(
                        f"{where}.timing.{key}: must be seconds or a timecode"
                    )
                else:
                    seconds = float(value)
                if seconds <= 0:
                    raise SpecValidationError(
                        f"{where}.timing.{key}: must be positive"
                    )
        if "return_to" in entry and not isinstance(entry["return_to"], dict):
            raise SpecValidationError(
                f"{where}.return_to: must be a camera state object"
            )
        if "authored" in entry and not isinstance(entry["authored"], dict):
            raise SpecValidationError(
                f"{where}.authored: must be an object"
            )
    elif "range" in entry:
        _validate_motion_range(entry["range"], f"{where}.range", "result-local")
    elif "at" in entry:
        _validate_timecode(entry["at"], f"{where}.at")
    else:
        raise SpecValidationError(f"{where}: needs 'range' or 'at'")
    if "to" not in entry or not isinstance(entry["to"], dict):
        raise SpecValidationError(f"{where}.to: must be a camera state object")


OVERLAY_KINDS = {"caption", "manual"}


def _validate_overlay(
    overlay,
    index: int,
    *,
    where_prefix: str = "overlays",
) -> None:
    where = f"{where_prefix}[{index}]"
    if not isinstance(overlay, dict):
        raise SpecValidationError(f"{where}: must be an object")
    for field in ("id", "track", "kind", "text"):
        _require_field(overlay, field, where)
    for field in ("id", "track", "text"):
        value = overlay[field]
        if not isinstance(value, str) or not value:
            raise SpecValidationError(
                f"{where}.{field}: must be a non-empty string"
            )
    if overlay["kind"] not in OVERLAY_KINDS:
        raise SpecValidationError(
            f"{where}.kind: must be one of {', '.join(sorted(OVERLAY_KINDS))}"
        )
    if overlay["kind"] == "manual":
        if "from" in overlay or "to" in overlay:
            raise SpecValidationError(
                f"{where}: manual overlays must not use top-level "
                "'from'/'to'; use 'timing'"
            )
        _require_field(overlay, "timing", where)
        timing = overlay["timing"]
        if not isinstance(timing, dict):
            raise SpecValidationError(f"{where}.timing: must be an object")
        space = timing.get("space")
        if space not in {"result", "scene"}:
            raise SpecValidationError(
                f"{where}.timing.space: must be 'result' or 'scene'"
            )
        for field in ("from", "to"):
            _require_field(timing, field, f"{where}.timing")
        if space == "scene":
            scene_id = timing.get("scene")
            if not isinstance(scene_id, str) or not scene_id:
                raise SpecValidationError(
                    f"{where}.timing.scene: must be a stable scene ID"
                )
        from_s = parse_timecode_string(
            timing["from"], f"{where}.timing.from"
        )
        to_s = parse_timecode_string(timing["to"], f"{where}.timing.to")
    else:
        if "timing" in overlay:
            raise SpecValidationError(
                f"{where}: caption overlays must not use 'timing'; "
                "use top-level 'from'/'to'"
            )
        for field in ("from", "to"):
            _require_field(overlay, field, where)
        from_s = parse_timecode_string(overlay["from"], f"{where}.from")
        to_s = parse_timecode_string(overlay["to"], f"{where}.to")
    if from_s >= to_s:
        raise SpecValidationError(
            f"{where}: overlay 'from' must be before 'to'"
        )
    z_index = overlay.get("z_index", 0)
    if isinstance(z_index, bool) or not isinstance(z_index, int):
        raise SpecValidationError(f"{where}.z_index: must be an integer")
    for field in ("position", "style"):
        if not isinstance(overlay.get(field), dict):
            raise SpecValidationError(f"{where}.{field}: must be an object")
    highlight = overlay.get("highlight")
    if highlight is not None and not isinstance(highlight, dict):
        raise SpecValidationError(f"{where}.highlight: must be an object")
    tokens = overlay.get("tokens")
    if tokens is not None:
        if not isinstance(tokens, list):
            raise SpecValidationError(f"{where}.tokens: must be an array")
        for j, token in enumerate(tokens):
            token_where = f"{where}.tokens[{j}]"
            if not isinstance(token, dict):
                raise SpecValidationError(f"{token_where}: must be an object")
            for field in ("text", "from", "to"):
                _require_field(token, field, token_where)
            parse_timecode_string(token["from"], f"{token_where}.from")
            parse_timecode_string(token["to"], f"{token_where}.to")


def overlay_id_prefix(kind: str) -> str:
    """On-disk overlay ID prefix per kind: cap_0001 / manual_0001."""
    return "cap" if kind == "caption" else "manual"


def next_overlay_id(existing_ids, kind: str) -> str:
    """Return the next free zero-padded overlay ID for ``kind``.

    Scans ``existing_ids`` for the kind's prefix and increments past
    the highest numeric suffix, so IDs stay stable when overlays are
    removed from the middle of the list.
    """
    prefix = overlay_id_prefix(kind)
    highest = 0
    for oid in existing_ids:
        if not isinstance(oid, str) or not oid.startswith(f"{prefix}_"):
            continue
        suffix = oid[len(prefix) + 1 :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}_{highest + 1:04d}"


def set_overlays(spec: dict, overlays: list[dict]) -> dict:
    """Return a new spec with the overlay state replaced wholesale.

    The dump -> edit -> set loop replaces the complete list atomically;
    validation happens in save_spec before anything touches disk. The
    The revision journal snapshots the prior state at the save boundary.
    """
    new_spec = copy.deepcopy(spec)
    new_spec["overlays"] = copy.deepcopy(overlays)
    return new_spec


def set_motion(spec: dict, motion: dict) -> dict:
    """Replace the complete motion document."""
    new_spec = copy.deepcopy(spec)
    new_spec["motion"] = copy.deepcopy(motion)
    validate_spec(new_spec)
    return new_spec


def ensure_scene_motion_identities(spec: dict) -> tuple[dict, list[dict]]:
    """Add stable internal identities to named scenes and their slots.

    Existing projects remain byte-compatible until motion is authored. At that
    boundary this helper adds IDs, after which scene-file edits can preserve a
    renamed scene or slot by carrying its internal ``id`` forward.
    """
    new_spec = copy.deepcopy(spec)
    composition = new_spec.get("composition")
    if not isinstance(composition, list) or not all(
        isinstance(scene, dict)
        and scene.get("name")
        and isinstance(scene.get("slots"), list)
        for scene in composition
    ):
        return new_spec, []

    used: set[str] = {
        value
        for scene in composition
        for value in [
            scene.get("id"),
            *[slot.get("id") for slot in scene.get("slots", [])],
        ]
        if isinstance(value, str) and value
    }
    counters = {"scene": 0, "slot": 0}

    def next_id(kind: str) -> str:
        while True:
            counters[kind] += 1
            candidate = f"{kind}_{counters[kind]:04d}"
            if candidate not in used:
                used.add(candidate)
                return candidate

    assigned: list[dict] = []
    for scene in composition:
        if not scene.get("id"):
            scene["id"] = next_id("scene")
            assigned.append(
                {"kind": "scene", "id": scene["id"], "scene": scene["name"]}
            )
        for slot in scene["slots"]:
            if not slot.get("id"):
                slot["id"] = next_id("slot")
                assigned.append(
                    {
                        "kind": "slot",
                        "id": slot["id"],
                        "scene": scene["name"],
                        "slot": slot["slot"],
                    }
                )
    return new_spec, assigned


def _motion_record_ids(record: dict) -> list[str]:
    return [
        entry["id"]
        for slot in record.get("slots", [])
        for kind in ("pacing", "camera")
        for entry in slot.get(kind, [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    ]


def reconcile_motion_lifecycle(
    old_composition: list[dict] | None,
    new_composition: list[dict] | None,
    motion: dict,
) -> tuple[dict, dict]:
    """Carry motion through scene edits by identity and report every change."""
    old_scenes = [scene for scene in (old_composition or []) if isinstance(scene, dict)]
    new_scenes = [scene for scene in (new_composition or []) if isinstance(scene, dict)]
    old_by_id = {scene.get("id"): scene for scene in old_scenes if scene.get("id")}
    old_by_name = {scene.get("name"): scene for scene in old_scenes if scene.get("name")}
    new_by_id = {scene.get("id"): scene for scene in new_scenes if scene.get("id")}
    new_by_name = {scene.get("name"): scene for scene in new_scenes if scene.get("name")}
    reconciled = {"version": motion.get("version", 1), "scenes": []}
    renamed: list[dict] = []
    removed: list[dict] = []

    for motion_scene in motion.get("scenes", []):
        scene_id = motion_scene.get("scene_id")
        old_scene = old_by_id.get(scene_id) if scene_id else None
        old_scene = old_scene or old_by_name.get(motion_scene.get("scene"))
        new_scene = new_by_id.get(scene_id) if scene_id else None
        if new_scene is None and old_scene is not None and old_scene.get("id"):
            new_scene = new_by_id.get(old_scene["id"])
        if new_scene is None:
            new_scene = new_by_name.get(
                old_scene.get("name") if old_scene else motion_scene.get("scene")
            )
        if new_scene is None:
            removed.append(
                {
                    "kind": "scene",
                    "scene": motion_scene.get("scene"),
                    "scene_id": scene_id,
                    "motion_ids": _motion_record_ids(motion_scene),
                }
            )
            continue

        old_name = motion_scene.get("scene")
        new_name = new_scene.get("name", old_name)
        resolved_scene_id = new_scene.get("id") or scene_id
        if old_name != new_name:
            renamed.append(
                {
                    "kind": "scene",
                    "id": resolved_scene_id,
                    "from": old_name,
                    "to": new_name,
                }
            )
        old_slots = old_scene.get("slots", []) if old_scene else []
        old_slots_by_id = {
            slot.get("id"): slot for slot in old_slots if slot.get("id")
        }
        old_slots_by_name = {
            slot.get("slot"): slot for slot in old_slots if slot.get("slot")
        }
        new_slots = new_scene.get("slots", [])
        new_slots_by_id = {
            slot.get("id"): slot for slot in new_slots if slot.get("id")
        }
        new_slots_by_name = {
            slot.get("slot"): slot for slot in new_slots if slot.get("slot")
        }
        slots_out: list[dict] = []
        for motion_slot in motion_scene.get("slots", []):
            slot_id = motion_slot.get("slot_id")
            old_slot = old_slots_by_id.get(slot_id) if slot_id else None
            old_slot = old_slot or old_slots_by_name.get(motion_slot.get("slot"))
            new_slot = new_slots_by_id.get(slot_id) if slot_id else None
            if new_slot is None and old_slot is not None and old_slot.get("id"):
                new_slot = new_slots_by_id.get(old_slot["id"])
            if new_slot is None:
                new_slot = new_slots_by_name.get(
                    old_slot.get("slot") if old_slot else motion_slot.get("slot")
                )
            if new_slot is None:
                removed.append(
                    {
                        "kind": "slot",
                        "scene": old_name,
                        "scene_id": resolved_scene_id,
                        "slot": motion_slot.get("slot"),
                        "slot_id": slot_id,
                        "motion_ids": _motion_record_ids(
                            {"slots": [motion_slot]}
                        ),
                    }
                )
                continue
            old_slot_name = motion_slot.get("slot")
            new_slot_name = new_slot.get("slot", old_slot_name)
            resolved_slot_id = new_slot.get("id") or slot_id
            if old_slot_name != new_slot_name:
                renamed.append(
                    {
                        "kind": "slot",
                        "id": resolved_slot_id,
                        "from": old_slot_name,
                        "to": new_slot_name,
                    }
                )
            slot_out = copy.deepcopy(motion_slot)
            slot_out["slot"] = new_slot_name
            if resolved_slot_id:
                slot_out["slot_id"] = resolved_slot_id
            slots_out.append(slot_out)
        if slots_out:
            scene_out = copy.deepcopy(motion_scene)
            scene_out["scene"] = new_name
            if resolved_scene_id:
                scene_out["scene_id"] = resolved_scene_id
            scene_out["slots"] = slots_out
            reconciled["scenes"].append(scene_out)

    return reconciled, {"renamed": renamed, "removed": removed}


def append_overlay(spec: dict, overlay: dict) -> dict:
    """Return a new spec with one overlay appended.

    The revision journal snapshots the prior complete list on save.
    """
    new_spec = copy.deepcopy(spec)
    new_spec.setdefault("overlays", []).append(copy.deepcopy(overlay))
    return new_spec


def _validate_canvas(canvas: dict, where: str) -> None:
    if not isinstance(canvas, dict):
        raise SpecValidationError(f"{where}: must be an object or null")
    for key in ("preset", "width", "height", "aspect_ratio"):
        _require_field(canvas, key, where)
    preset = canvas["preset"]
    if preset is not None:
        if not isinstance(preset, str) or preset not in CANVAS_PRESETS:
            raise SpecValidationError(
                f"{where}.preset: must be one of "
                f"{', '.join(sorted(CANVAS_PRESETS))} or null"
            )
    for key in ("width", "height"):
        value = canvas[key]
        if not isinstance(value, int) or value <= 0:
            raise SpecValidationError(f"{where}.{key}: must be a positive integer")
        if value % 2 != 0:
            raise SpecValidationError(f"{where}.{key}: must be even")
    if not isinstance(canvas["aspect_ratio"], str) or not canvas["aspect_ratio"]:
        raise SpecValidationError(f"{where}.aspect_ratio: must be a non-empty string")


def layout_orientation(canvas: dict) -> str:
    return "vertical" if int(canvas["height"]) >= int(canvas["width"]) else "horizontal"


def layout_slots(layout: str, canvas: dict) -> list[str]:
    if layout not in LAYOUT_PRESETS:
        raise SpecValidationError(
            f"layout: must be one of {', '.join(sorted(LAYOUT_PRESETS))}"
        )
    preset = LAYOUT_PRESETS[layout]
    if layout == "two-up":
        key = (
            "slots_portrait"
            if layout_orientation(canvas) == "vertical"
            else "slots_landscape"
        )
        return list(preset[key])
    return list(preset["slots"])


PIP_INSET_CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")
PIP_INSET_DEFAULTS = {"corner": "bottom-right", "width": 0.32, "height": 0.24}
PIP_INSET_MIN_FRACTION = 0.05
PIP_INSET_MAX_FRACTION = 0.6

# M25 canonical slot geometry (``layout.geometry.inset``). The legacy
# ``layout.inset`` shape above stays valid for existing specs but cannot
# coexist with ``geometry`` on the same layout.
SLOT_GEOMETRY_ANCHORS = (
    "top-left",
    "top",
    "top-right",
    "left",
    "center",
    "right",
    "bottom-left",
    "bottom",
    "bottom-right",
)
SLOT_GEOMETRY_SIZE_FRACTIONS = {
    "small": 0.22,
    "medium": 0.32,
    "large": 0.44,
}
# M26: new picture-in-picture scenes materialize the circular default
# at write time — a square medium inset with a thin white ring. Absent
# geometry means the scene predates the default (legacy) and keeps
# rendering a rectangle. White is the short-form convention; over light
# content it fades benignly to the borderless look ('--border black'
# is the one-flag fix).
M26_DEFAULT_INSET_GEOMETRY = {
    "size": SLOT_GEOMETRY_SIZE_FRACTIONS["medium"],
    "shape": {
        "kind": "circle",
        "border": {"width": 4, "color": "white"},
    },
}
SLOT_GEOMETRY_MOTION_PRESETS = ("bounce",)
SLOT_GEOMETRY_SPEEDS = ("slow", "medium", "fast")
SLOT_GEOMETRY_MIN_SIZE = PIP_INSET_MIN_FRACTION
# Canonical geometry can use the full shorter canvas dimension. Keep this
# separate from the legacy PIP inset's 0.60 ceiling: resolved bounds still
# reject any size/placement combination that leaves the canvas.
SLOT_GEOMETRY_MAX_SIZE = 1.0
_SLOT_GEOMETRY_FIELDS = (
    "size", "anchor", "margin_x", "margin_y", "x", "y", "motion", "shape",
)
_SLOT_SHAPE_KINDS = ("circle", "rounded", "rect")
_SLOT_SHAPE_FIELDS = ("kind", "radius", "border")
_SLOT_BORDER_FIELDS = ("width", "color")
# Mirrors the authoring vocabulary: CSS-style names, #hex, rgb()/rgba().
_SLOT_BORDER_COLOR_RE = re.compile(
    r"^(?:[A-Za-z]+|#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})"
    r"|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*[\d.]+\s*)?\))$"
)
_BOUNCE_SPEED_MULTIPLIERS = {"slow": 0.5, "medium": 1.0, "fast": 1.5}


def geometry_even_pixels(value: float) -> int:
    """Round an authored dimension to even pixels for yuv420p safety."""
    rounded = max(2, int(round(value)))
    return rounded if rounded % 2 == 0 else rounded + 1


def default_slot_margin(canvas: dict) -> int:
    """Default anchor margin: 4% of the shorter canvas dimension, min 16px."""
    return max(
        16, int(round(min(int(canvas["width"]), int(canvas["height"])) * 0.04))
    )


def _legacy_pip_inset_dimensions(canvas: dict) -> tuple[int, int]:
    """The preset inset pixels — deliberately not even-rounded, so specs
    without an authored size keep rendering the exact legacy rectangle
    (346x461 on the short canvas)."""
    width = int(canvas["width"])
    height = int(canvas["height"])
    return (
        max(2, int(round(width * PIP_INSET_DEFAULTS["width"]))),
        max(2, int(round(height * PIP_INSET_DEFAULTS["height"]))),
    )


def resolve_inset_geometry(
    inset_geometry: dict,
    canvas: dict,
    *,
    where: str = "layout.geometry.inset",
    check_bounds: bool = True,
) -> dict[str, int]:
    """Resolve a canonical inset geometry override to a pixel region.

    Authored sizes are square (a fraction of the shorter canvas
    dimension) and round to even pixels; without ``size`` the legacy
    preset dimensions are preserved exactly. Out-of-bounds regions
    raise instead of clamping.
    """
    canvas_w = int(canvas["width"])
    canvas_h = int(canvas["height"])
    size = inset_geometry.get("size")
    if isinstance(size, dict):
        short = min(canvas_w, canvas_h)
        width = geometry_even_pixels(short * float(size["width"]))
        height = geometry_even_pixels(short * float(size["height"]))
    elif size is not None:
        side = geometry_even_pixels(min(canvas_w, canvas_h) * float(size))
        width, height = side, side
    else:
        width, height = _legacy_pip_inset_dimensions(canvas)

    if inset_geometry.get("x") is not None:
        x = int(round(canvas_w * float(inset_geometry["x"])))
        y = int(round(canvas_h * float(inset_geometry["y"])))
    else:
        anchor = inset_geometry.get("anchor", "bottom-right")
        default_margin = default_slot_margin(canvas)
        margin_x = int(inset_geometry.get("margin_x", default_margin))
        margin_y = int(inset_geometry.get("margin_y", default_margin))
        horizontal = anchor.rsplit("-", 1)[-1] if "-" in anchor else anchor
        vertical = anchor.split("-", 1)[0] if "-" in anchor else anchor
        if horizontal == "left":
            x = margin_x
        elif horizontal == "right":
            x = canvas_w - width - margin_x
        else:
            x = (canvas_w - width) // 2
        if vertical == "top":
            y = margin_y
        elif vertical == "bottom":
            y = canvas_h - height - margin_y
        else:
            y = (canvas_h - height) // 2

    region = {"x": x, "y": y, "width": width, "height": height}
    if check_bounds and (
        x < 0 or y < 0 or x + width > canvas_w or y + height > canvas_h
    ):
        raise SpecValidationError(
            f"{where}: resolved region {width}x{height} at ({x}, {y}) "
            f"leaves the {canvas_w}x{canvas_h} canvas; reduce size or "
            "margins, or move x/y inward"
        )
    return region


def resolve_slot_motion(
    layout: dict, slot_name: str, canvas: dict
) -> dict | None:
    """Resolve a stored named motion preset into renderer-ready values."""
    if slot_name != "inset":
        return None
    geometry = layout.get("geometry")
    if not isinstance(geometry, dict):
        return None
    inset = geometry.get("inset")
    if not isinstance(inset, dict):
        return None
    motion = inset.get("motion")
    if not isinstance(motion, dict) or motion.get("preset") != "bounce":
        return None

    speed = motion.get("speed") or "medium"
    multiplier = _BOUNCE_SPEED_MULTIPLIERS.get(speed)
    if multiplier is None:
        raise SpecValidationError(
            "layout.geometry.inset.motion.speed: must be one of "
            f"{', '.join(SLOT_GEOMETRY_SPEEDS)}"
        )
    region = resolve_inset_geometry(inset, canvas)
    shorter = min(int(canvas["width"]), int(canvas["height"]))
    # The measured 640x360 spike used a 200x140 px/s vector. Scale that
    # same 10:7 direction from the shorter canvas dimension.
    velocity_x = round(shorter * (5 / 9) * multiplier, 6)
    velocity_y = round(shorter * (7 / 18) * multiplier, 6)
    return {
        "preset": "bounce",
        "speed": speed,
        "origin": {"x": region["x"], "y": region["y"]},
        "bounds": {
            "x": int(canvas["width"]) - region["width"],
            "y": int(canvas["height"]) - region["height"],
        },
        "velocity": {"x": velocity_x, "y": velocity_y},
    }


def _bounce_coordinate(
    origin: int, bound: int, velocity: float, result_time_s: float
) -> int:
    if bound <= 0:
        return 0
    position = abs(
        (velocity * result_time_s + origin + bound) % (2 * bound) - bound
    )
    return min(bound, max(0, int(round(position))))


def _validate_layout_geometry(
    geometry: object, preset: str, where: str, canvas: dict | None
) -> None:
    if geometry is None:
        return
    if preset != "picture-in-picture":
        raise SpecValidationError(
            f"{where}: only applies to the picture-in-picture layout"
        )
    if not isinstance(geometry, dict):
        raise SpecValidationError(f"{where}: must be an object")
    unknown = set(geometry) - {"inset"}
    if unknown:
        raise SpecValidationError(
            f"{where}: unknown slot(s) {', '.join(sorted(unknown))}; "
            "only the 'inset' slot supports geometry"
        )
    inset = geometry.get("inset")
    if inset is None:
        raise SpecValidationError(f"{where}: must configure the 'inset' slot")
    loc = f"{where}.inset"
    if not isinstance(inset, dict):
        raise SpecValidationError(f"{loc}: must be an object")
    unknown = set(inset) - set(_SLOT_GEOMETRY_FIELDS)
    if unknown:
        raise SpecValidationError(
            f"{loc}: unknown field(s) {', '.join(sorted(unknown))}; "
            f"allowed: {', '.join(_SLOT_GEOMETRY_FIELDS)}"
        )
    if not inset:
        raise SpecValidationError(
            f"{loc}: needs at least one of "
            f"{', '.join(_SLOT_GEOMETRY_FIELDS)}; remove 'geometry' to "
            "use the preset rectangle"
        )

    size = inset.get("size")
    if size is not None:
        if isinstance(size, dict):
            unknown = set(size) - {"width", "height"}
            if unknown:
                raise SpecValidationError(
                    f"{loc}.size: unknown field(s) "
                    f"{', '.join(sorted(unknown))}; allowed: width, height"
                )
            for key in ("width", "height"):
                value = size.get(key)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or not (
                        SLOT_GEOMETRY_MIN_SIZE
                        <= float(value)
                        <= SLOT_GEOMETRY_MAX_SIZE
                    )
                ):
                    raise SpecValidationError(
                        f"{loc}.size.{key}: must be a number between "
                        f"{SLOT_GEOMETRY_MIN_SIZE} and "
                        f"{SLOT_GEOMETRY_MAX_SIZE} (fraction of the "
                        "shorter canvas dimension)"
                    )
        elif (
            isinstance(size, bool)
            or not isinstance(size, (int, float))
            or not math.isfinite(float(size))
            or not SLOT_GEOMETRY_MIN_SIZE <= float(size) <= SLOT_GEOMETRY_MAX_SIZE
        ):
            raise SpecValidationError(
                f"{loc}.size: must be a number between "
                f"{SLOT_GEOMETRY_MIN_SIZE} and {SLOT_GEOMETRY_MAX_SIZE} "
                "(fraction of the shorter canvas dimension), or a "
                "{width, height} object for a non-square inset"
            )

    anchor = inset.get("anchor")
    if anchor is not None and anchor not in SLOT_GEOMETRY_ANCHORS:
        raise SpecValidationError(
            f"{loc}.anchor: must be one of {', '.join(SLOT_GEOMETRY_ANCHORS)}"
        )

    has_exact = "x" in inset or "y" in inset
    if has_exact and ("x" not in inset or "y" not in inset):
        raise SpecValidationError(
            f"{loc}: 'x' and 'y' must be set together"
        )
    for key in ("x", "y"):
        value = inset.get(key)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise SpecValidationError(
                f"{loc}.{key}: must be a normalized number from 0 to 1"
            )
    if has_exact and anchor is not None:
        raise SpecValidationError(
            f"{loc}: exact x/y placement cannot be combined with 'anchor'"
        )
    has_margin = "margin_x" in inset or "margin_y" in inset
    if has_exact and has_margin:
        raise SpecValidationError(
            f"{loc}: margins only apply to anchored placement; drop "
            "margin_x/margin_y or use 'anchor'"
        )
    for key in ("margin_x", "margin_y"):
        value = inset.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SpecValidationError(
                f"{loc}.{key}: must be a non-negative whole number of pixels"
            )

    motion = inset.get("motion")
    if motion is not None:
        motion_loc = f"{loc}.motion"
        if not isinstance(motion, dict):
            raise SpecValidationError(f"{motion_loc}: must be an object")
        unknown = set(motion) - {"preset", "speed"}
        if unknown:
            raise SpecValidationError(
                f"{motion_loc}: unknown field(s) "
                f"{', '.join(sorted(unknown))}; allowed: preset, speed"
            )
        if motion.get("preset") not in SLOT_GEOMETRY_MOTION_PRESETS:
            raise SpecValidationError(
                f"{motion_loc}.preset: must be one of "
                f"{', '.join(SLOT_GEOMETRY_MOTION_PRESETS)}"
            )
        speed = motion.get("speed")
        if speed is not None and speed not in SLOT_GEOMETRY_SPEEDS:
            raise SpecValidationError(
                f"{motion_loc}.speed: must be one of "
                f"{', '.join(SLOT_GEOMETRY_SPEEDS)}"
            )

    shape = inset.get("shape")
    if shape is not None:
        shape_loc = f"{loc}.shape"
        if not isinstance(shape, dict):
            raise SpecValidationError(f"{shape_loc}: must be an object")
        unknown = set(shape) - set(_SLOT_SHAPE_FIELDS)
        if unknown:
            raise SpecValidationError(
                f"{shape_loc}: unknown field(s) {', '.join(sorted(unknown))}; "
                f"allowed: {', '.join(_SLOT_SHAPE_FIELDS)}"
            )
        kind = shape.get("kind")
        if kind not in _SLOT_SHAPE_KINDS:
            raise SpecValidationError(
                f"{shape_loc}.kind: must be one of "
                f"{', '.join(_SLOT_SHAPE_KINDS)}"
            )
        radius = shape.get("radius")
        if radius is not None:
            if kind != "rounded":
                raise SpecValidationError(
                    f"{shape_loc}.radius: only applies to the 'rounded' "
                    f"kind, not {kind!r}"
                )
            if (
                isinstance(radius, bool)
                or not isinstance(radius, (int, float))
                or not math.isfinite(float(radius))
                or not 0 <= float(radius) <= 0.5
            ):
                raise SpecValidationError(
                    f"{shape_loc}.radius: must be a number from 0 to 0.5 "
                    "(fraction of the slot's shorter side)"
                )
        border = shape.get("border")
        if border is not None:
            border_loc = f"{shape_loc}.border"
            if not isinstance(border, dict):
                raise SpecValidationError(f"{border_loc}: must be an object")
            unknown = set(border) - set(_SLOT_BORDER_FIELDS)
            if unknown:
                raise SpecValidationError(
                    f"{border_loc}: unknown field(s) "
                    f"{', '.join(sorted(unknown))}; allowed: "
                    f"{', '.join(_SLOT_BORDER_FIELDS)}"
                )
            width = border.get("width")
            if (
                isinstance(width, bool)
                or not isinstance(width, int)
                or width < 1
            ):
                raise SpecValidationError(
                    f"{border_loc}.width: must be a whole number of "
                    "pixels >= 1"
                )
            color = border.get("color")
            if not isinstance(color, str) or not _SLOT_BORDER_COLOR_RE.match(
                color
            ):
                raise SpecValidationError(
                    f"{border_loc}.color: must be a color name, #hex, or "
                    "rgb()/rgba() value"
                )
        # Without an authored size the inset keeps the legacy preset
        # rectangle, which is not square, and a WxH size is non-square
        # by request; a circle would be an ellipse either way.
        if kind == "circle" and (
            inset.get("size") is None or isinstance(inset.get("size"), dict)
        ):
            raise SpecValidationError(
                f"{shape_loc}: a circle needs a square region; set "
                f"{loc}.size to a named or single-fraction square size"
            )

    if canvas is not None:
        resolve_inset_geometry(inset, canvas, where=loc)


def _scene_slot_framing_mode(scene: dict, slot_name: str) -> str:
    for slot in scene.get("slots") or []:
        if isinstance(slot, dict) and slot.get("slot") == slot_name:
            framing = slot.get("framing")
            if isinstance(framing, dict):
                return str(framing.get("mode", "fill"))
    return "fill"


def _validate_shape_framing(scene: dict, where: str) -> None:
    """fit framing letterboxes; inside a curved shape the bars become
    black wedges — reject the combination at write time, whoever the
    writer is."""
    layout = scene.get("layout")
    if not isinstance(layout, dict):
        return
    geometry = layout.get("geometry")
    if not isinstance(geometry, dict):
        return
    inset = geometry.get("inset")
    if not isinstance(inset, dict):
        return
    shape = inset.get("shape")
    if not isinstance(shape, dict):
        return
    kind = shape.get("kind")
    if kind not in ("circle", "rounded"):
        return
    if _scene_slot_framing_mode(scene, "inset") == "fit":
        raise SpecValidationError(
            f"{where}: the inset slot uses fit framing, which letterboxes "
            f"with black bars; inside a {kind} shape those become black "
            "wedges. Use fill framing (e.g. 'moviestar scenes geometry "
            "SCENE:inset --framing fill:center'), or set the shape to rect"
        )


def layout_regions(
    layout: dict, canvas: dict, *, result_time_s: float
) -> dict[str, dict[str, int]]:
    """Resolve a stored layout to pixel regions at a motion result time."""
    if not isinstance(layout, dict):
        raise SpecValidationError("layout: must be an object")
    preset = layout.get("preset")
    if preset not in LAYOUT_PRESETS:
        raise SpecValidationError(
            f"layout.preset: must be one of {', '.join(sorted(LAYOUT_PRESETS))}"
        )
    if (
        isinstance(result_time_s, bool)
        or not isinstance(result_time_s, (int, float))
        or not math.isfinite(result_time_s)
        or result_time_s < 0
    ):
        raise SpecValidationError("result_time_s: must be a finite number >= 0")

    inset_config = layout.get("inset")
    width = int(canvas["width"])
    height = int(canvas["height"])
    if preset == "single":
        return {"main": {"x": 0, "y": 0, "width": width, "height": height}}
    if preset == "two-up":
        if layout_orientation(canvas) == "vertical":
            top_h = height // 2
            return {
                "top": {"x": 0, "y": 0, "width": width, "height": top_h},
                "bottom": {
                    "x": 0,
                    "y": top_h,
                    "width": width,
                    "height": height - top_h,
                },
            }
        left_w = width // 2
        return {
            "left": {"x": 0, "y": 0, "width": left_w, "height": height},
            "right": {
                "x": left_w,
                "y": 0,
                "width": width - left_w,
                "height": height,
            },
        }
    if preset == "picture-in-picture":
        geometry = layout.get("geometry")
        if geometry is not None and inset_config is not None:
            raise SpecValidationError(
                "layout: 'inset' and 'geometry' cannot both be set; "
                "'geometry' is the canonical shape — remove the legacy "
                "'inset' or drop 'geometry'"
            )
        if isinstance(geometry, dict) and geometry.get("inset"):
            inset_region = resolve_inset_geometry(geometry["inset"], canvas)
            motion = resolve_slot_motion(layout, "inset", canvas)
            if motion is not None:
                inset_region = {
                    **inset_region,
                    "x": _bounce_coordinate(
                        motion["origin"]["x"],
                        motion["bounds"]["x"],
                        motion["velocity"]["x"],
                        result_time_s,
                    ),
                    "y": _bounce_coordinate(
                        motion["origin"]["y"],
                        motion["bounds"]["y"],
                        motion["velocity"]["y"],
                        result_time_s,
                    ),
                }
            return {
                "main": {"x": 0, "y": 0, "width": width, "height": height},
                "inset": inset_region,
            }
        config = {**PIP_INSET_DEFAULTS, **(inset_config or {})}
        inset_w = max(2, int(round(width * float(config["width"]))))
        inset_h = max(2, int(round(height * float(config["height"]))))
        margin = max(16, int(round(min(width, height) * 0.04)))
        corner = config["corner"]
        x = margin if "left" in corner else width - inset_w - margin
        y = margin if "top" in corner else height - inset_h - margin
        return {
            "main": {"x": 0, "y": 0, "width": width, "height": height},
            "inset": {
                "x": max(0, x),
                "y": max(0, y),
                "width": inset_w,
                "height": inset_h,
            },
        }
    raise AssertionError(f"Unhandled layout preset {preset!r}")


def _validate_framing(framing: dict, where: str) -> None:
    if not isinstance(framing, dict):
        raise SpecValidationError(f"{where}: must be an object")
    _require_field(framing, "mode", where)
    mode = framing["mode"]
    if mode not in FRAMING_MODES:
        raise SpecValidationError(
            f"{where}.mode: must be one of {', '.join(sorted(FRAMING_MODES))}"
        )
    anchor = framing.get("anchor")
    if anchor is not None and anchor not in FRAMING_ANCHORS:
        raise SpecValidationError(
            f"{where}.anchor: must be one of {', '.join(sorted(FRAMING_ANCHORS))}"
        )
    has_numeric_anchor = False
    for key in ("x", "y"):
        if key not in framing:
            continue
        has_numeric_anchor = True
        value = framing[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SpecValidationError(
                f"{where}.{key}: must be a normalized number from 0 to 1"
            )
        if not math.isfinite(float(value)) or value < 0 or value > 1:
            raise SpecValidationError(
                f"{where}.{key}: must be a normalized number from 0 to 1"
            )
    if has_numeric_anchor and mode != "fill":
        raise SpecValidationError(f"{where}: numeric x/y anchors require fill mode")


# ---------- Helpers ----------


def get_source(spec: dict, source_id: str) -> dict:
    """Return the source dict for ``source_id``. Raises if not found."""
    for source in spec["sources"]:
        if source["id"] == source_id:
            return source
    available = ", ".join(s["id"] for s in spec["sources"])
    raise SpecValidationError(
        f"unknown source_id {source_id!r}; available: {available}"
    )


def source_ids(spec: dict) -> list[str]:
    """Return the list of source IDs in the spec, preserving order."""
    return [s["id"] for s in spec["sources"]]


# ---------- Resolver ----------


def _apply_trim_to_segments(
    segments: tuple[tuple[float, float], ...],
    a_result: float,
    b_result: float,
) -> tuple[tuple[float, float], ...]:
    """Keep result-time ``[a_result, b_result]`` of the stitched segments.

    Walks each segment's contribution to result-time and emits a new
    source-time range for the portion within the trim window. Segments
    entirely outside the window are dropped.
    """
    out: list[tuple[float, float]] = []
    cursor = 0.0  # current position in result-time across the stitched segments
    for src_from, src_to in segments:
        seg_len = src_to - src_from
        seg_result_start = cursor
        seg_result_end = cursor + seg_len
        result_lo = max(seg_result_start, a_result)
        result_hi = min(seg_result_end, b_result)
        if result_lo < result_hi:
            src_lo = src_from + (result_lo - seg_result_start)
            src_hi = src_from + (result_hi - seg_result_start)
            out.append((round(src_lo, 3), round(src_hi, 3)))
        cursor = seg_result_end
    return tuple(out)


def _apply_cut_to_segments(
    segments: tuple[tuple[float, float], ...],
    a_result: float,
    b_result: float,
) -> tuple[tuple[float, float], ...]:
    """Remove result-time ``[a_result, b_result]`` from the stitched
    segments.

    A cut spanning multiple segments splits them at the cut boundaries
    and drops the middle. The mutation keeps the segment count internal and
    exposes ``effective_duration`` plus the new ``source_range`` envelope.
    """
    out: list[tuple[float, float]] = []
    cursor = 0.0
    for src_from, src_to in segments:
        seg_len = src_to - src_from
        seg_result_start = cursor
        seg_result_end = cursor + seg_len
        if seg_result_end <= a_result or seg_result_start >= b_result:
            # No overlap with the cut — keep segment as-is.
            out.append((round(src_from, 3), round(src_to, 3)))
        else:
            # Overlaps the cut window: keep prefix and/or suffix if
            # either survives. A cut that splits the segment exactly
            # at its boundary collapses to one or the other.
            if seg_result_start < a_result:
                src_break = src_from + (a_result - seg_result_start)
                out.append((round(src_from, 3), round(src_break, 3)))
            if seg_result_end > b_result:
                src_break = src_from + (b_result - seg_result_start)
                out.append((round(src_break, 3), round(src_to, 3)))
        cursor = seg_result_end
    return tuple(out)


def _segments_duration(segments: tuple[tuple[float, float], ...]) -> float:
    return round(sum(b - a for a, b in segments), 3)


def _make_timeline(
    source_id: str,
    segments: tuple[tuple[float, float], ...],
    source_duration: float,
    operations_applied: int,
) -> EffectiveTimeline:
    """Build an EffectiveTimeline from a segment list. ``source_range``
    falls back to ``(0, 0)`` if the segment list is empty (which
    append-side bounds checks prevent in practice)."""
    if segments:
        outer_from = segments[0][0]
        outer_to = segments[-1][1]
    else:
        outer_from = outer_to = 0.0
    return EffectiveTimeline(
        source_id=source_id,
        source_range=(outer_from, outer_to),
        source_segments=segments,
        effective_duration=_segments_duration(segments),
        source_duration=round(source_duration, 3),
        operations_applied=operations_applied,
    )


def _resolve_steps(spec: dict, source_id: str, source_duration: float):
    """Yield ``(op_or_None, segments_tuple)`` after each step, starting
    with the initial pre-op state (``op=None``).

    Shared loop body for :func:`resolve_source` and
    :func:`resolve_source_history` — IEEE-754 ms-rounding is applied
    inside the apply helpers so callers get already-rounded segments.
    """
    source = get_source(spec, source_id)
    ops = source.get("operations", [])
    segments: tuple[tuple[float, float], ...] = ((0.0, round(source_duration, 3)),)
    yield None, segments
    for op in ops:
        a = parse_timecode_string(op["from"], f"{op['type']}.from")
        b = parse_timecode_string(op["to"], f"{op['type']}.to")
        if op["type"] == "trim":
            segments = _apply_trim_to_segments(segments, a, b)
        elif op["type"] == "cut":
            segments = _apply_cut_to_segments(segments, a, b)
        yield op, segments


def resolve_source(
    spec: dict, source_id: str, source_duration: float
) -> EffectiveTimeline:
    """Compose the operations stack for one source. Returns the
    effective timeline for that source's edit.

    Result-time semantics: each op's from/to is interpreted relative
    to the timeline produced by all previous ops on this source. Each
    source has its own independent stack. Cut ops produce a multi-
    segment result, exposed via ``source_segments``.
    """
    final_segments: tuple[tuple[float, float], ...] = ()
    ops_applied = 0
    for op, segments in _resolve_steps(spec, source_id, source_duration):
        final_segments = segments
        if op is not None:
            ops_applied += 1
    return _make_timeline(source_id, final_segments, source_duration, ops_applied)


def result_to_source_time(
    segments: tuple[tuple[float, float], ...],
    result_seconds: float,
) -> float:
    """Map a result-time second back to source-time given a segment list.

    Used by screenshot (``--at``) and any other command that needs to
    pick a source byte position for a given result-time. Walks the
    stitched segments cumulatively; the seam between segments is
    invisible to the agent, but the source-time jumps across it
    (skipping the dropped cut hole).

    Raises :class:`SpecValidationError` if ``result_seconds`` is
    negative or past the total result duration. A small float-rounding
    tolerance covers the exact-end case so an agent passing
    ``effective_duration`` doesn't trip the bounds check.
    """
    if result_seconds < 0:
        raise SpecValidationError(
            f"result_seconds {result_seconds}s cannot be negative"
        )
    cursor = 0.0
    last_idx = len(segments) - 1
    for i, (seg_from, seg_to) in enumerate(segments):
        seg_len = seg_to - seg_from
        seg_end = cursor + seg_len
        # Non-last segment: prefer the *next* segment at the exact seam
        # so result-time 10 in [(0,10), (15,30)] maps to source 15, not
        # source 10. The last segment includes its right edge so the
        # final result-time (== total duration) is reachable.
        owns = (
            result_seconds < seg_end
            if i < last_idx
            else result_seconds <= seg_end + 0.001
        )
        if owns:
            offset = max(0.0, result_seconds - cursor)
            return round(seg_from + offset, 3)
        cursor = seg_end
    raise SpecValidationError(
        f"result_seconds {result_seconds}s exceeds total result duration "
        f"({round(cursor, 3)}s)"
    )


def source_range_in_result(
    segments: tuple[tuple[float, float], ...],
    src_from: float,
    src_to: float,
) -> tuple[float, float] | None:
    """Map a source-time range into result-time, *only* when the entire
    range fits inside one segment.

    Returns ``None`` when the range crosses a seam (i.e., spans a cut
    hole) or falls entirely outside the surviving segments. Used by
    find to drop transcript matches that no longer survive as a
    contiguous phrase in the result after a cut.
    """
    cursor = 0.0
    for seg_from, seg_to in segments:
        seg_len = seg_to - seg_from
        if src_from >= seg_from and src_to <= seg_to:
            offset_from = src_from - seg_from
            offset_to = src_to - seg_from
            return (
                round(cursor + offset_from, 3),
                round(cursor + offset_to, 3),
            )
        cursor += seg_len
    return None


def slice_audio_from_anchor(
    audio_segments: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    anchor: float,
    target_duration: float,
) -> list[tuple[float, float]]:
    """Walk audio_from's source-time segment list starting at ``anchor``
    and return slices summing to at most ``target_duration`` seconds.

    Used by :func:`set_composition` (validation: caller compares the
    summed slice duration to ``target_duration`` to detect underflow)
    and by export render (passes slices to ffmpeg as audio_from_input).

    Segments before ``anchor`` are skipped. The first kept segment may
    start partway through if ``anchor`` falls inside it. The last kept
    segment may end partway through if ``target_duration`` runs out.

    Anchoring at audio_from's first appearance in the composition (rather
    than at audio_segments[0]) is what makes
    ``--audio-from <source>`` "just work" without forcing the agent to
    pre-trim the audio source to match the visual segments.
    """
    slices: list[tuple[float, float]] = []
    remaining = target_duration
    for seg_from, seg_to in audio_segments:
        if remaining <= 0:
            break
        if seg_to <= anchor:
            continue
        take_from = max(seg_from, anchor)
        take_duration = min(seg_to - take_from, remaining)
        slices.append((take_from, take_from + take_duration))
        remaining -= take_duration
    return slices


def resolve_source_history(
    spec: dict, source_id: str, source_duration: float
) -> tuple[TimelineStep, ...]:
    """Return one :class:`TimelineStep` per state in the source's
    lineage, starting with the initial pre-op state.

    A source with N ops produces N+1 steps. The ``moviestar history`` command
    renders this directly.
    """
    steps: list[TimelineStep] = []
    op_index = 0
    for op, segments in _resolve_steps(spec, source_id, source_duration):
        timeline = _make_timeline(source_id, segments, source_duration, op_index)
        steps.append(TimelineStep(op_index=op_index, op=op, timeline=timeline))
        op_index += 1
    return tuple(steps)


def resolve_all_sources(
    spec: dict, source_durations: dict[str, float]
) -> dict[str, EffectiveTimeline]:
    """Resolve every source in the spec. Returns a dict keyed by source_id.

    ``source_durations`` is a {source_id: duration_seconds} map — the
    caller is responsible for looking these up (typically from
    project.json).
    """
    return {
        sid: resolve_source(spec, sid, source_durations[sid])
        for sid in source_ids(spec)
    }


# ---------- Composition shape + storage normalization (stage 3) ----------

COMPOSITION_AUTHORED_AS = {"concat", "layout", "scenes"}

_NORMALIZE_FALLBACK_DIMS = (1920, 1080)


def composition_shape(spec: dict) -> str:
    """Structural shape of the stored composition.

    ``"source"`` (no composition), ``"concat"`` (flat segments),
    ``"layout"`` (one unnamed layout scene), or ``"scenes"``.
    The concat and layout cases exist only pre-normalization; see
    :func:`normalize_composition_storage`.
    """
    composition = spec.get("composition")
    if composition is None:
        return "source"
    if not (
        composition
        and isinstance(composition[0], dict)
        and ("layout" in composition[0] or "slots" in composition[0])
    ):
        return "concat"
    if len(composition) > 1 or any(
        "name" in scene or "audio_from" in scene for scene in composition
    ):
        return "scenes"
    return "layout"


def _is_flat_segments(segments: list) -> bool:
    return bool(segments) and isinstance(segments[0], dict) and not (
        "layout" in segments[0] or "slots" in segments[0]
    )


def _even_dim(value: int) -> int:
    return max(16, value - (value % 2))


def _inferred_canvas(segments: list[dict], source_dims) -> dict:
    """Canvas for a canvasless flat concat: the first segment's source
    dimensions (even-ized for encoder safety), matching the canvasless
    render rule of normalizing every segment to the first source's
    resolution. Falls back to 1920x1080 when no dimensions are known.
    """
    dims = None
    for seg in segments:
        dims = source_dims.get(seg["source"])
        if dims:
            break
    if not dims:
        dims = _NORMALIZE_FALLBACK_DIMS
    width = _even_dim(int(dims[0]))
    height = _even_dim(int(dims[1]))
    divisor = math.gcd(width, height) or 1
    return {
        "preset": None,
        "width": width,
        "height": height,
        "aspect_ratio": f"{width // divisor}:{height // divisor}",
    }


def _flat_segments_to_scenes(segments: list[dict]) -> list[dict]:
    scenes: list[dict] = []
    for i, seg in enumerate(segments):
        slot = {
            "slot": "main",
            "source": seg["source"],
            "source_from": seg["source_from"],
            "source_to": seg["source_to"],
        }
        if seg.get("framing") is not None:
            slot["framing"] = seg["framing"]
        scenes.append(
            {
                "name": f"segment_{i + 1}",
                "layout": {"preset": "single"},
                "slots": [slot],
            }
        )
    return scenes


def _is_unnamed_layout(segments: list) -> bool:
    """A legacy global layout: exactly one layout scene with no
    name and no per-scene audio routing (audio goes through
    ``composition_audio_from``)."""
    return (
        len(segments) == 1
        and isinstance(segments[0], dict)
        and ("layout" in segments[0] or "slots" in segments[0])
        and "name" not in segments[0]
        and "audio_from" not in segments[0]
    )


def _layout_to_scenes(segments: list[dict]) -> list[dict]:
    """Name the single layout scene, making it a scene composition.
    Layout, slots, framing, and any slot IDs are preserved exactly."""
    scene = copy.deepcopy(segments[0])
    return [{"name": "scene_1", **scene}]


def normalize_composition_storage(spec: dict, source_dims) -> dict:
    """Rewrite stored flat concats and global layouts as scene
    compositions.

    Each flat segment becomes one single-slot full-frame scene; a
    global layout becomes the same scene with a generated name.
    Per-segment audio is reproduced by the only-audio-slot inference
    and ``composition_audio_from`` keeps working via the
    composition-wide route. ``composition_authored_as`` records the
    authored vocabulary (``"concat"`` / ``"layout"``) so read surfaces
    keep reporting the shape the agent authored. Pure and idempotent;
    ``source_dims`` maps source ID to ``(width, height)``
    (or None) for canvas inference on canvasless concats.

    Applied in memory at spec load — read commands never write; the
    normalized shape persists on the next write.
    """

    def _legacy(segments) -> bool:
        return _is_flat_segments(segments) or _is_unnamed_layout(segments)

    composition = spec.get("composition")
    needs = composition is not None and _legacy(composition)
    if not needs:
        return spec

    new_spec = copy.deepcopy(spec)
    composition = new_spec.get("composition")
    if composition is not None and _is_flat_segments(composition):
        if new_spec.get("composition_canvas") is None:
            new_spec["composition_canvas"] = _inferred_canvas(
                composition, source_dims
            )
        new_spec["composition"] = _flat_segments_to_scenes(composition)
        new_spec["composition_authored_as"] = "concat"
    elif composition is not None and _is_unnamed_layout(composition):
        new_spec["composition"] = _layout_to_scenes(composition)
        new_spec["composition_authored_as"] = "layout"
    return new_spec


# ---------- Pure mutations ----------


def empty_spec(sources: list[dict]) -> dict:
    """Return a fresh spec with N sources, each with no operations.

    ``sources`` is a list of {id, path} dicts. The spec.json shape
    duplicates ``path`` across project.json and spec.json — small
    redundancy that lets spec.json be self-contained for the
    rendering layer.
    """
    spec = {
        "version": SCHEMA_VERSION,
        "sources": [
            {"id": s["id"], "path": s["path"], "operations": []}
            for s in sources
        ],
        "composition": None,
        "composition_audio_from": None,
        "composition_canvas": None,
        "opening_transition": None,
        "closing_transition": None,
        "overlays": [],
        "audio_mix": default_audio_mix(),
        "motion": {"version": 1, "scenes": []},
        "captions": [],
        "revisions": [],
    }
    validate_spec(spec)
    return spec


def sync_project_sources(spec: dict, sources: list[dict]) -> dict:
    """Return ``spec`` with any missing project sources appended.

    ``moviestar load --add`` extends project.json after spec.json may
    already exist. Spec-aware commands validate against spec["sources"],
    so append newly loaded project sources with empty operation stacks.
    The same source is appended to stored source snapshots so undoing an
    earlier trim or cut cannot remove a source added outside the journal.
    Existing spec source entries are preserved as-is.
    """
    existing_ids = {source["id"] for source in spec.get("sources", [])}
    missing_sources = [
        source for source in sources if source["id"] not in existing_ids
    ]
    if not missing_sources:
        return spec

    new_spec = copy.deepcopy(spec)
    for source in missing_sources:
        source_entry = {
            "id": source["id"],
            "path": source["path"],
            "operations": [],
        }
        new_spec["sources"].append(copy.deepcopy(source_entry))
        for revision in new_spec.get("revisions", []):
            prior_sources = revision.get("changed", {}).get("sources")
            if prior_sources is None:
                continue
            prior_ids = {prior["id"] for prior in prior_sources}
            if source["id"] not in prior_ids:
                prior_sources.append(copy.deepcopy(source_entry))

    validate_spec(new_spec)
    return new_spec


def _trim_op(from_s: float, to_s: float) -> dict:
    """Build a trim op with on-disk string timecodes."""
    return {
        "type": "trim",
        "from": _format_timecode_string(from_s),
        "to": _format_timecode_string(to_s),
    }


def _cut_op(from_s: float, to_s: float) -> dict:
    """Build a cut op with on-disk string timecodes."""
    return {
        "type": "cut",
        "from": _format_timecode_string(from_s),
        "to": _format_timecode_string(to_s),
    }


def append_trim(
    spec: dict,
    source_id: str,
    from_s: float,
    to_s: float,
    source_duration: float,
) -> dict:
    """Return a new spec with a trim appended to ``source_id``'s stack.

    Result-time semantics + bounds checks per source. Other sources'
    stacks are untouched.
    """
    if from_s < 0:
        raise SpecValidationError(f"trim: 'from' cannot be negative (got {from_s}s)")
    if from_s >= to_s:
        raise SpecValidationError(
            f"trim: 'from' ({from_s}s) must be before 'to' ({to_s}s)"
        )

    timeline = resolve_source(spec, source_id, source_duration)
    effective = timeline.effective_duration
    if to_s > effective + 0.5:
        raise SpecValidationError(
            f"trim: range {from_s}s-{to_s}s exceeds result duration of "
            f"{effective}s"
        )

    new_spec = json.loads(json.dumps(spec))  # deep copy
    target = get_source(new_spec, source_id)
    target["operations"].append(_trim_op(from_s, to_s))
    validate_spec(new_spec)
    return new_spec


def append_cut(
    spec: dict,
    source_id: str,
    from_s: float,
    to_s: float,
    source_duration: float,
) -> dict:
    """Return a new spec with a cut appended to ``source_id``'s stack.

    Result-time semantics + bounds checks per source. Other sources'
    stacks are untouched. Cuts error only on impossible operations:

    - ``from`` negative
    - ``from >= to``
    - ``to`` exceeds the source's current ``effective_duration``
    - the cut would remove the entire result (leaves 0 seconds)

    Cuts that compose across prior seams are allowed silently — the
    resolver handles the splitting internally.
    """
    if from_s < 0:
        raise SpecValidationError(f"cut: 'from' cannot be negative (got {from_s}s)")
    if from_s >= to_s:
        raise SpecValidationError(
            f"cut: 'from' ({from_s}s) must be before 'to' ({to_s}s)"
        )

    timeline = resolve_source(spec, source_id, source_duration)
    effective = timeline.effective_duration
    if to_s > effective + 0.5:
        raise SpecValidationError(
            f"cut: range {from_s}s-{to_s}s exceeds result duration of "
            f"{effective}s"
        )
    if from_s <= 0.0 and to_s >= effective - 0.001:
        raise SpecValidationError(
            f"cut: range {from_s}s-{to_s}s would remove the entire result "
            f"({effective}s)"
        )

    new_spec = json.loads(json.dumps(spec))  # deep copy
    target = get_source(new_spec, source_id)
    target["operations"].append(_cut_op(from_s, to_s))
    validate_spec(new_spec)
    return new_spec


def pop_last_op(spec: dict, source_id: str) -> tuple[dict, dict]:
    """Pop the last op from ``source_id``'s stack. Returns
    ``(new_spec, popped_op)``. Raises on empty stack.
    """
    new_spec = json.loads(json.dumps(spec))  # deep copy
    target = get_source(new_spec, source_id)
    ops = target.get("operations", [])
    if not ops:
        raise SpecValidationError(
            f"No operations to undo for source {source_id!r}."
        )
    popped = ops.pop()
    validate_spec(new_spec)
    return new_spec, popped


def set_composition(
    spec: dict,
    segments: list[tuple[str, float, float]],
    source_durations: dict[str, float],
    audio_from: str | None = None,
    canvas: dict | None = None,
    framings: list[dict | None] | None = None,
) -> dict:
    """Set the composition to a new list of segments, replacing any
    prior composition.

    ``segments`` is a list of ``(source_id, result_from, result_to)``
    tuples in result-time per source. Each is resolved through that
    source's current timeline at this moment — the resulting
    composition stores SOURCE-TIME, so future per-source edits don't shift it.

    Bounds checks per source mirror :func:`append_trim`:

    - source_id must exist
    - ``result_from < result_to``
    - ``result_to`` must not exceed the source's current
      ``effective_duration``

    ``audio_from``: optional source ID whose audio plays
    across the whole composition. Must (a) be one of the sources
    referenced by ``segments`` and (b) have an edited result long
    enough to cover the composition's total duration (else the audio
    track would run short of the video). Persisted to
    ``composition_audio_from``. Pass ``None`` to clear. The revision
    journal restores routing and segments together.

    ``canvas`` / ``framings``: optional composition canvas and
    per-segment framing records. These are persisted and validated so
    export can render each segment into the requested output canvas.

    Raises :class:`SpecValidationError` on empty input or any of the
    above. Other sources' per-source edit stacks are untouched.
    """
    if not segments:
        raise SpecValidationError(
            "concat: at least one segment required (--segment <id> --from X --to Y)"
        )

    if framings is not None and len(framings) != len(segments):
        raise SpecValidationError(
            "concat: framing count must match segment count when provided"
        )

    resolved: list[dict] = []
    composition_duration = 0.0
    for i, (sid, result_from, result_to) in enumerate(segments):
        loc = f"segment[{i}]"
        if sid not in source_durations:
            available = ", ".join(source_ids(spec))
            raise SpecValidationError(
                f"{loc}: unknown source {sid!r}; available: {available}"
            )
        if result_from < 0:
            raise SpecValidationError(
                f"{loc}: 'from' cannot be negative (got {result_from}s)"
            )
        if result_from >= result_to:
            raise SpecValidationError(
                f"{loc}: 'from' ({result_from}s) must be before 'to' "
                f"({result_to}s)"
            )

        timeline = resolve_source(spec, sid, source_durations[sid])
        if result_to > timeline.effective_duration + 0.5:
            raise SpecValidationError(
                f"{loc} ({sid}): range {result_from}s-{result_to}s exceeds "
                f"result duration of {timeline.effective_duration}s"
            )

        # Snapshot to source-time at this moment (D6).
        src_from = result_to_source_time(
            timeline.source_segments, result_from
        )
        src_to = result_to_source_time(timeline.source_segments, result_to)
        segment_record = {
            "source": sid,
            "source_from": _format_timecode_string(src_from),
            "source_to": _format_timecode_string(src_to),
        }
        if framings is not None and framings[i] is not None:
            segment_record["framing"] = framings[i]
        resolved.append(segment_record)
        composition_duration += result_to - result_from

    # M14 step 1.4 + 1.5: validate audio_from. Anchor = source-time of
    # the first segment that references audio_from. Audio plays from
    # that anchor onwards through audio_from's edited result for the
    # composition duration — that's what makes the multicam case
    # ("video cuts between cams, audio from one source") work without
    # forcing the agent to pre-trim the audio source.
    if audio_from is not None:
        segment_sids = {sid for sid, _f, _t in segments}
        if audio_from not in segment_sids:
            raise SpecValidationError(
                f"concat: --audio-from {audio_from!r} must name a source "
                f"that appears in --segment. Segments reference: "
                f"{', '.join(sorted(segment_sids))}."
            )
        audio_timeline = resolve_source(
            spec, audio_from, source_durations[audio_from]
        )
        # Anchor: source-time of audio_from's first appearance in the
        # composition. Translates result-time → source-time through
        # audio_from's own timeline (respects any per-source trims/cuts
        # on audio_from).
        first_result_from = next(
            result_from for sid, result_from, _ in segments if sid == audio_from
        )
        anchor_source_time = result_to_source_time(
            audio_timeline.source_segments, first_result_from
        )
        af_slices = slice_audio_from_anchor(
            audio_timeline.source_segments,
            anchor_source_time,
            composition_duration,
        )
        available = sum(s_to - s_from for s_from, s_to in af_slices)
        if available + 0.5 < composition_duration:
            raise SpecValidationError(
                f"concat: --audio-from {audio_from!r}'s edited result "
                f"only has {available:.3f}s of audio after the anchor at "
                f"source-time {anchor_source_time:.3f}s (where {audio_from!r}'s "
                f"first segment lives), but the composition is "
                f"{composition_duration:.3f}s. Audio would run out before "
                f"the video. Either move {audio_from!r}'s first segment "
                f"earlier in source-time so more audio is available after "
                f"the anchor, or trim the composition shorter, or pick a "
                f"longer audio source."
            )

    new_spec = json.loads(json.dumps(spec))  # deep copy
    new_spec["composition"] = resolved
    new_spec["composition_audio_from"] = audio_from
    new_spec["composition_canvas"] = canvas
    new_spec["composition_authored_as"] = "concat"
    validate_spec(new_spec)
    return new_spec


def set_layout_composition(
    spec: dict,
    *,
    layout: str,
    slots: list[tuple[str, str, float, float, dict | None]],
    source_durations: dict[str, float],
    audio_from: str | None,
    canvas: dict,
) -> dict:
    """Set one global layout scene as the composition.

    ``slots`` is a list of ``(slot_name, source_id, result_from,
    result_to, framing)`` tuples. Ranges are result-time per source and
    are snapshotted to source-time, matching flat ``concat`` semantics.
    This entry point writes exactly one global layout scene; multi-scene
    authoring uses ``set_scene_composition``.
    """
    expected_slots = set(layout_slots(layout, canvas))
    if not slots:
        raise SpecValidationError("concat --layout: at least one --slot is required")
    seen_slots = {slot_name for slot_name, _sid, _from, _to, _framing in slots}
    missing = expected_slots - seen_slots
    extra = seen_slots - expected_slots
    if missing:
        raise SpecValidationError(
            f"concat --layout {layout!r}: missing slot(s): "
            f"{', '.join(sorted(missing))}"
        )
    if extra:
        raise SpecValidationError(
            f"concat --layout {layout!r}: unknown slot(s): "
            f"{', '.join(sorted(extra))}; expected "
            f"{', '.join(sorted(expected_slots))}"
        )

    resolved_slots: list[dict] = []
    durations: set[float] = set()
    source_ranges_by_slot: dict[str, tuple[float, float]] = {}
    for i, (slot_name, sid, result_from, result_to, framing) in enumerate(slots):
        loc = f"slot[{i}]"
        if sid not in source_durations:
            available = ", ".join(source_ids(spec))
            raise SpecValidationError(
                f"{loc}: unknown source {sid!r}; available: {available}"
            )
        if result_from < 0:
            raise SpecValidationError(
                f"{loc}: 'from' cannot be negative (got {result_from}s)"
            )
        if result_from >= result_to:
            raise SpecValidationError(
                f"{loc}: 'from' ({result_from}s) must be before 'to' "
                f"({result_to}s)"
            )
        timeline = resolve_source(spec, sid, source_durations[sid])
        if result_to > timeline.effective_duration + 0.5:
            raise SpecValidationError(
                f"{loc} ({sid}): range {result_from}s-{result_to}s exceeds "
                f"result duration of {timeline.effective_duration}s"
            )
        src_from = result_to_source_time(timeline.source_segments, result_from)
        src_to = result_to_source_time(timeline.source_segments, result_to)
        source_ranges_by_slot[slot_name] = (src_from, src_to)
        duration = round(result_to - result_from, 3)
        durations.add(duration)
        record = {
            "slot": slot_name,
            "source": sid,
            "source_from": _format_timecode_string(src_from),
            "source_to": _format_timecode_string(src_to),
        }
        if framing is not None:
            record["framing"] = framing
        resolved_slots.append(record)
    if len(durations) != 1:
        raise SpecValidationError(
            "concat --layout: all slots must have the same duration"
        )

    composition_duration = next(iter(durations))
    if audio_from is not None:
        slot_sources = {sid for _slot, sid, _from, _to, _framing in slots}
        if audio_from not in slot_sources:
            raise SpecValidationError(
                f"concat --layout: --audio-from {audio_from!r} must name "
                f"a source assigned to a layout slot. Slot sources: "
                f"{', '.join(sorted(slot_sources))}."
            )
        audio_timeline = resolve_source(
            spec, audio_from, source_durations[audio_from]
        )
        audio_slot = next(
            slot_name for slot_name, sid, _from, _to, _framing in slots
            if sid == audio_from
        )
        anchor_source_time = source_ranges_by_slot[audio_slot][0]
        af_slices = slice_audio_from_anchor(
            audio_timeline.source_segments,
            anchor_source_time,
            composition_duration,
        )
        available = sum(s_to - s_from for s_from, s_to in af_slices)
        if available + 0.5 < composition_duration:
            raise SpecValidationError(
                f"concat --layout: --audio-from {audio_from!r}'s edited "
                f"result only has {available:.3f}s of audio after the "
                f"anchor at source-time {anchor_source_time:.3f}s, but "
                f"the layout composition is {composition_duration:.3f}s."
            )

    scene = {
        "layout": {
            "preset": layout,
            "orientation": layout_orientation(canvas),
        },
        "slots": resolved_slots,
    }

    new_spec = json.loads(json.dumps(spec))
    new_spec["composition"] = [scene]
    new_spec["composition_audio_from"] = audio_from
    new_spec["composition_canvas"] = canvas
    new_spec["composition_authored_as"] = "layout"
    validate_spec(new_spec)
    return new_spec


def set_scene_composition(
    spec: dict,
    *,
    scenes: list[dict],
    source_durations: dict[str, float],
    canvas: dict,
) -> dict:
    """Set one or more layout scenes as the composition.

    Each input scene is a dict with ``name``, ``layout``, ``slots`` and
    optional ``audio_from``. Slot ranges are result-time per source and
    are snapshotted to source-time, matching ``concat`` and global-layout
    semantics.
    """
    if not scenes:
        raise SceneValidationError("scenes", "at least one scene is required")

    current_composition = spec.get("composition")
    old_scenes = [
        scene
        for scene in (current_composition or [])
        if isinstance(scene, dict) and scene.get("name")
    ]
    old_by_id = {
        scene["id"]: scene for scene in old_scenes if scene.get("id")
    }
    old_by_name = {scene["name"]: scene for scene in old_scenes}

    resolved_scenes: list[dict] = []
    scene_names: set[str] = set()
    for index, scene_def in enumerate(scenes):
        scene_path = f"scenes[{index}]"
        name = str(scene_def.get("name", "")).strip()
        if not name:
            raise SceneValidationError(f"{scene_path}.name", "name is required")
        if name in scene_names:
            raise SceneValidationError(
                f"{scene_path}.name", f"duplicate scene name {name!r}"
            )
        scene_names.add(name)
        layout = scene_def.get("layout")
        if layout not in LAYOUT_PRESETS:
            raise SceneValidationError(
                f"{scene_path}.layout",
                f"unknown layout {layout!r}; "
                f"use one of: {', '.join(sorted(LAYOUT_PRESETS))}"
            )

        requested_id = scene_def.get("id")
        old_scene = old_by_id.get(requested_id) if requested_id else None
        old_scene = old_scene or old_by_name.get(name)
        old_layout = (old_scene or {}).get("layout", {})
        carried_layout_fields: dict = {}
        if layout == "picture-in-picture":
            if "slot_geometry" in scene_def:
                slot_geometry = scene_def["slot_geometry"]
                geometry_path = f"{scene_path}.slot_geometry"
                geometry_paths = scene_def.get("slot_geometry_paths", {})
                if not isinstance(slot_geometry, dict):
                    raise SceneValidationError(
                        geometry_path, "must be an object keyed by slot name"
                    )
                unknown_slots = set(slot_geometry) - {"inset"}
                if unknown_slots:
                    unknown_slot = sorted(unknown_slots)[0]
                    raise SceneValidationError(
                        geometry_paths.get(unknown_slot, geometry_path),
                        f"only the 'inset' slot supports geometry; got "
                        f"{', '.join(sorted(unknown_slots))}",
                    )
                inset_geometry = slot_geometry.get("inset")
                if inset_geometry is not None:
                    geometry = {"inset": inset_geometry}
                    inset_path = geometry_paths.get("inset", geometry_path)
                    try:
                        _validate_layout_geometry(
                            geometry, layout, inset_path, canvas
                        )
                    except SpecValidationError as exc:
                        path, _, message = str(exc).partition(": ")
                        path = path.replace(
                            f"{inset_path}.inset", inset_path, 1
                        )
                        raise SceneValidationError(
                            path, message or str(exc)
                        ) from exc
                    carried_layout_fields["geometry"] = json.loads(
                        json.dumps(geometry)
                    )
            elif isinstance(old_layout, dict):
                if old_layout.get("geometry") is not None:
                    carried_layout_fields["geometry"] = json.loads(
                        json.dumps(old_layout["geometry"])
                    )
                elif old_layout.get("inset") is not None:
                    carried_layout_fields["inset"] = json.loads(
                        json.dumps(old_layout["inset"])
                    )
            if (
                "geometry" not in carried_layout_fields
                and "inset" not in carried_layout_fields
                # An explicit slot_geometry key — including a null that
                # clears a stored override — is a decision; the default
                # only fills silence.
                and "slot_geometry" not in scene_def
            ):
                inset_framing = next(
                    (
                        framing
                        for slot_name, _sid, _from, _to, framing
                        in scene_def.get("slots", [])
                        if slot_name == "inset"
                    ),
                    None,
                )
                fit_inset = (
                    isinstance(inset_framing, dict)
                    and inset_framing.get("mode") == "fit"
                )
                if old_scene is not None or fit_inset:
                    # Re-authoring a scene that never stored a shape must
                    # not change its look, and a circle cannot take fit
                    # framing (black wedges) — both materialize the
                    # rectangle they have always rendered.
                    carried_layout_fields["geometry"] = {
                        "inset": {"shape": {"kind": "rect"}}
                    }
                else:
                    carried_layout_fields["geometry"] = json.loads(
                        json.dumps({"inset": M26_DEFAULT_INSET_GEOMETRY})
                    )
        elif "slot_geometry" in scene_def:
            raise SceneValidationError(
                f"{scene_path}.slot_geometry",
                "only applies to the picture-in-picture layout",
            )

        slots = scene_def.get("slots", [])
        expected_slots = set(layout_slots(layout, canvas))
        if not slots:
            raise SceneValidationError(
                f"{scene_path}.slots", "at least one slot is required"
            )
        seen_slots = {
            slot_name for slot_name, _sid, _from, _to, _framing in slots
        }
        extra = seen_slots - expected_slots
        missing = expected_slots - seen_slots
        if extra:
            raise SceneValidationError(
                f"{scene_path}.slots",
                f"invalid slot(s): "
                f"{', '.join(sorted(extra))} for layout {layout!r} "
                f"on canvas {_canvas_label(canvas)!r}. Expected: "
                f"{', '.join(sorted(expected_slots))}"
            )
        if missing:
            raise SceneValidationError(
                f"{scene_path}.slots",
                f"missing slot(s): "
                f"{', '.join(sorted(missing))}"
            )

        resolved_slots: list[dict] = []
        durations: set[float] = set()
        source_ranges_by_slot: dict[str, tuple[float, float]] = {}
        for i, (slot_name, sid, result_from, result_to, framing) in enumerate(slots):
            slot_path = f"{scene_path}.slots[{i}]"
            if sid not in source_durations:
                available = ", ".join(sorted(source_durations))
                raise SceneValidationError(
                    f"{slot_path}.source",
                    f"unknown source {sid!r}; available: {available}",
                )
            if result_from < 0:
                raise SceneValidationError(
                    f"{slot_path}.from",
                    f"cannot be negative (got {result_from}s)",
                )
            if result_from >= result_to:
                raise SceneValidationError(
                    f"{slot_path}.from",
                    f"must be before 'to' ({result_from}s >= {result_to}s)",
                )
            timeline = resolve_source(spec, sid, source_durations[sid])
            if result_to > timeline.effective_duration + 0.5:
                raise SceneValidationError(
                    f"{slot_path}.to",
                    f"range {result_from}s-{result_to}s for {sid!r} exceeds "
                    f"result duration of {timeline.effective_duration}s"
                )
            src_from = result_to_source_time(timeline.source_segments, result_from)
            src_to = result_to_source_time(timeline.source_segments, result_to)
            source_ranges_by_slot[slot_name] = (src_from, src_to)
            duration = round(result_to - result_from, 3)
            durations.add(duration)
            record = {
                "slot": slot_name,
                "source": sid,
                "source_from": _format_timecode_string(src_from),
                "source_to": _format_timecode_string(src_to),
            }
            if framing is not None:
                record["framing"] = framing
            resolved_slots.append(record)
        if len(durations) != 1:
            details = ", ".join(
                f"{slot_name}={_format_timecode_string(round(to_s - from_s, 3))}"
                for slot_name, _sid, from_s, to_s, _framing in slots
            )
            raise SceneSlotDurationMismatchError(
                f"{scene_path}.slots",
                f"slot durations must match. "
                f"Got {details}."
            )

        composition_duration = next(iter(durations))
        audio_from = scene_def.get("audio_from")
        if audio_from is not None:
            slot_sources = {sid for _slot, sid, _from, _to, _framing in slots}
            if audio_from not in slot_sources:
                raise SceneValidationError(
                    f"{scene_path}.audio_from",
                    f"{audio_from!r} "
                    f"must name a source assigned to a layout slot. Slot "
                    f"sources: {', '.join(sorted(slot_sources))}."
                )
            audio_timeline = resolve_source(
                spec, audio_from, source_durations[audio_from]
            )
            audio_slot = next(
                slot_name
                for slot_name, sid, _from, _to, _framing in slots
                if sid == audio_from
            )
            anchor_source_time = source_ranges_by_slot[audio_slot][0]
            af_slices = slice_audio_from_anchor(
                audio_timeline.source_segments,
                anchor_source_time,
                composition_duration,
            )
            available = sum(s_to - s_from for s_from, s_to in af_slices)
            if available + 0.5 < composition_duration:
                raise SceneValidationError(
                    f"{scene_path}.audio_from",
                    f"{audio_from!r}'s "
                    f"edited result only has {available:.3f}s of audio after "
                    f"the anchor at source-time {anchor_source_time:.3f}s, but "
                    f"the scene is {composition_duration:.3f}s."
                )

        resolved_scene = {
            "name": name,
            "layout": {
                "preset": layout,
                "orientation": layout_orientation(canvas),
                **carried_layout_fields,
            },
            "slots": resolved_slots,
        }
        if audio_from is not None:
            resolved_scene["audio_from"] = audio_from
        resolved_scenes.append(resolved_scene)

    identity_active = any(
        scene.get("id")
        or any(slot.get("id") for slot in scene.get("slots", []))
        for scene in old_scenes
    ) or any(scene_def.get("id") for scene_def in scenes)
    if identity_active:
        used = {
            value
            for scene in old_scenes
            for value in [
                scene.get("id"),
                *[slot.get("id") for slot in scene.get("slots", [])],
            ]
            if isinstance(value, str) and value
        }
        counters = {"scene": 0, "slot": 0}

        def next_identity(kind: str) -> str:
            while True:
                counters[kind] += 1
                candidate = f"{kind}_{counters[kind]:04d}"
                if candidate not in used:
                    used.add(candidate)
                    return candidate

        for scene_def, resolved_scene in zip(scenes, resolved_scenes):
            requested_id = scene_def.get("id")
            old_scene = old_by_id.get(requested_id) if requested_id else None
            old_scene = old_scene or old_by_name.get(resolved_scene["name"])
            resolved_scene["id"] = (
                requested_id
                or (old_scene or {}).get("id")
                or next_identity("scene")
            )
            old_slots = (old_scene or {}).get("slots", [])
            old_slots_by_id = {
                slot["id"]: slot for slot in old_slots if slot.get("id")
            }
            old_slots_by_name = {
                slot["slot"]: slot for slot in old_slots if slot.get("slot")
            }
            requested_slot_ids = scene_def.get("slot_ids", {})
            for resolved_slot in resolved_scene["slots"]:
                requested_slot_id = requested_slot_ids.get(resolved_slot["slot"])
                old_slot = (
                    old_slots_by_id.get(requested_slot_id)
                    if requested_slot_id else None
                )
                old_slot = old_slot or old_slots_by_name.get(resolved_slot["slot"])
                resolved_slot["id"] = (
                    requested_slot_id
                    or (old_slot or {}).get("id")
                    or next_identity("slot")
                )

    # A transition is owned by its incoming scene. Re-authoring that same
    # scene keeps the authored boundary, while moving it to the first
    # position naturally drops the now-inapplicable internal transition.
    for index, (scene_def, resolved_scene) in enumerate(
        zip(scenes, resolved_scenes)
    ):
        if index == 0:
            continue
        requested_id = scene_def.get("id")
        old_scene = old_by_id.get(requested_id) if requested_id else None
        old_scene = old_scene or old_by_name.get(resolved_scene["name"])
        if isinstance((old_scene or {}).get("transition_in"), dict):
            resolved_scene["transition_in"] = copy.deepcopy(
                old_scene["transition_in"]
            )

    new_spec = json.loads(json.dumps(spec))
    new_spec["composition"] = resolved_scenes
    new_spec["composition_audio_from"] = None
    new_spec["composition_canvas"] = canvas
    new_spec["composition_authored_as"] = None
    validate_spec(new_spec)
    return new_spec


def set_audio_mix(spec: dict, audio_mix: dict) -> dict:
    """Atomically replace audio intent."""
    new_spec = copy.deepcopy(spec)
    new_spec["audio_mix"] = copy.deepcopy(audio_mix)
    validate_spec(new_spec)
    return new_spec


def caption_recipe_for_track(spec: dict, track: str) -> dict | None:
    """The stored caption recipe for ``track``, or None (frozen track)."""
    for recipe in spec.get("captions", []):
        if recipe.get("track") == track:
            return recipe
    return None


def upsert_caption_recipe(spec: dict, recipe: dict) -> dict:
    """Replace or add one track's caption recipe.

    A recipe is the authoritative caption state for a derived track:
    the stored cue overlays are a cache re-derived whenever the
    recipe's ``cache_fingerprint`` no longer matches the compiled
    timeline (see cli._refresh_caption_tracks).
    """
    new_spec = copy.deepcopy(spec)
    recipes = [
        r
        for r in new_spec.get("captions", [])
        if r.get("track") != recipe.get("track")
    ]
    recipes.append(copy.deepcopy(recipe))
    new_spec["captions"] = recipes
    validate_spec(new_spec)
    return new_spec


def remove_caption_recipe(spec: dict, track: str) -> dict:
    """Drop one track's recipe (materialize / import-over).

    The track's stored cues become frozen overlays that no longer follow
    timeline edits.
    """
    new_spec = copy.deepcopy(spec)
    new_spec["captions"] = [
        r for r in new_spec.get("captions", []) if r.get("track") != track
    ]
    validate_spec(new_spec)
    return new_spec


# ---------- Revisions (stage 7) ----------
#
# One revision per spec-mutating command: the command name plus the
# prior values of exactly the fields it touched. Bare `undo` pops the
# last revision atomically, so coupled edits (composition + reconciled
# motion, caption recipe + cue cache) restore together — the contract
# the four independent per-family history stacks could not express.

REVISION_FIELDS = (
    "sources",
    "composition",
    "composition_audio_from",
    "composition_canvas",
    "composition_authored_as",
    "opening_transition",
    "closing_transition",
    "motion",
    "overlays",
    "audio_mix",
    "captions",
)

REVISION_LIMIT = 50


def append_revision(before: dict, after: dict, command: str) -> dict:
    """Return ``after`` with one revision recording what ``command``
    changed relative to ``before``. No tracked field changed → no
    revision. The revision list is FIFO-capped at REVISION_LIMIT."""
    changed = {
        field: copy.deepcopy(before.get(field))
        for field in REVISION_FIELDS
        if before.get(field) != after.get(field)
    }
    new_spec = copy.deepcopy(after)
    # The persisted journal belongs to the project being mutated, not to
    # an incoming replacement document (notably `spec --edit`).
    new_spec["revisions"] = copy.deepcopy(before.get("revisions", []))
    if changed:
        new_spec["revisions"] = [
            *new_spec["revisions"],
            {"command": command, "changed": changed},
        ][-REVISION_LIMIT:]
    return new_spec


def pop_revision(spec: dict) -> tuple[dict, dict]:
    """Restore the fields the most recent revision touched, atomically.

    Returns ``(restored_spec, popped_revision)``. Raises
    :class:`SpecValidationError` when no revisions exist.
    """
    revisions = spec.get("revisions") or []
    if not revisions:
        raise SpecValidationError("No revision to undo.")
    new_spec = copy.deepcopy(spec)
    popped = new_spec["revisions"].pop()
    for field, prior in popped["changed"].items():
        new_spec[field] = copy.deepcopy(prior)
    validate_spec(new_spec)
    return new_spec, popped


def _validate_revisions(revisions, where: str) -> None:
    if not isinstance(revisions, list):
        raise SpecValidationError(f"spec: '{where}' must be an array")
    if len(revisions) > REVISION_LIMIT:
        raise SpecValidationError(
            f"spec: '{where}' cannot contain more than {REVISION_LIMIT} entries"
        )
    for i, revision in enumerate(revisions):
        r_where = f"{where}[{i}]"
        if not isinstance(revision, dict):
            raise SpecValidationError(f"{r_where}: must be an object")
        if not isinstance(revision.get("command"), str) or not revision["command"]:
            raise SpecValidationError(
                f"{r_where}.command: must be a non-empty string"
            )
        changed = revision.get("changed")
        if not isinstance(changed, dict) or not changed:
            raise SpecValidationError(
                f"{r_where}.changed: must be a non-empty object"
            )
        unknown = sorted(set(changed) - set(REVISION_FIELDS))
        if unknown:
            raise SpecValidationError(
                f"{r_where}.changed: unknown field(s) {', '.join(unknown)}"
            )


def _validate_revision_chain(spec: dict) -> None:
    """Validate every state that successive undo calls could restore."""
    revisions = spec.get("revisions", [])
    if not revisions:
        return
    candidate = copy.deepcopy(spec)
    candidate["revisions"] = []
    for index in range(len(revisions) - 1, -1, -1):
        for field, prior in revisions[index]["changed"].items():
            candidate[field] = copy.deepcopy(prior)
        try:
            validate_spec(candidate)
        except SpecValidationError as exc:
            raise SpecValidationError(
                f"revisions[{index}].changed: invalid prior state: {exc}"
            ) from exc


# ---------- I/O ----------


def _spec_path(cwd: Path | None) -> Path:
    """Resolve moviestar/spec.json against the nearest project workspace.

    Walks up from ``cwd`` (issue #41) so spec/trim/undo work from any
    subdir of a project — same contract the CLI commands use via
    ``get_project_dir``. If no ancestor workspace exists, falls back
    to ``cwd / moviestar / spec.json``.
    """
    from moviestar.project import find_project_dir
    found = find_project_dir(cwd)
    if found is not None:
        return found / SPEC_FILE
    base = Path(cwd) if cwd else Path.cwd()
    return base / MOVIESTAR_DIR / SPEC_FILE


def is_v01_spec(data: dict) -> bool:
    """Detect a v0.1-shaped spec by version field. Used by load_spec
    to surface a friendly "reload the project" error rather than the
    generic v0.2 schema-validation error."""
    return isinstance(data, dict) and data.get("version") == "0.1"


def load_spec(cwd: Path | None = None) -> dict | None:
    """Read v0.2 spec.json, returning None if absent.

    Raises :class:`SpecValidationError` on parse errors, invalid
    on-disk content, or v0.1-shaped specs (per D3 — clean break;
    user must reload the project).
    """
    path = _spec_path(cwd)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SpecValidationError(f"Could not parse {path}: {exc}") from exc

    if is_v01_spec(data):
        raise SpecValidationError(
            f"{path}: this is a legacy v0.1 spec. "
            f"Reload the project (`moviestar load --force <video>`) "
            f"to upgrade. v0.2 introduces multi-source projects and a "
            f"new on-disk format; old specs aren't auto-migrated."
        )

    legacy_histories = sorted(set(data) & set(LEGACY_GLOBAL_HISTORY_FIELDS))
    if legacy_histories:
        raise SpecValidationError(_legacy_history_port_message(legacy_histories))

    _migrate_in_place(data)
    validate_spec(data)
    return data


def _migrate_in_place(data: dict) -> None:
    """Lift legacy spec.json shapes to current. Mutates ``data`` directly.

    - Older specs may lack the top-level ``composition_audio_from`` key.
      Default to None.
    - Older specs may lack the top-level ``composition_canvas`` key. Default to
      None.
    - Older specs may lack the top-level ``overlays`` key. Default to [].
    - Older specs may lack ``audio_mix``. Default to an audible 0 dB routed
      source layer and no external tracks.
    - Older specs may lack ``motion``. Seed an empty version-1 document so
      existing projects retain identity pacing until motion is authored.
    - Older specs may lack project-edge transition fields. Default both to
      None, preserving the existing hard-cut behavior.
    """
    if "composition_audio_from" not in data:
        data["composition_audio_from"] = None
    if "composition_canvas" not in data:
        data["composition_canvas"] = None
    if "opening_transition" not in data:
        data["opening_transition"] = None
    if "closing_transition" not in data:
        data["closing_transition"] = None
    # Pre-M19 lacked the top-level `overlays` key. Default to empty.
    if "overlays" not in data:
        data["overlays"] = []
    _migrate_manual_overlay_timing(data["overlays"])
    for revision in data.get("revisions", []):
        changed = revision.get("changed", {}) if isinstance(revision, dict) else {}
        if isinstance(changed.get("overlays"), list):
            _migrate_manual_overlay_timing(changed["overlays"])
    if "audio_mix" not in data:
        data["audio_mix"] = default_audio_mix()
    if "motion" not in data:
        data["motion"] = {"version": 1, "scenes": []}
    # Pre-stage-4 specs lacked caption recipes. Default to empty: every
    # existing caption track is a frozen cue list until regenerated.
    if "captions" not in data:
        data["captions"] = []
    if "revisions" not in data:
        data["revisions"] = []


def _migrate_manual_overlay_timing(overlays) -> None:
    """Promote legacy manual overlay clocks to explicit result time."""
    if not isinstance(overlays, list):
        return
    for overlay in overlays:
        if (
            not isinstance(overlay, dict)
            or overlay.get("kind") != "manual"
            or "timing" in overlay
            or "from" not in overlay
            or "to" not in overlay
        ):
            continue
        overlay["timing"] = {
            "space": "result",
            "from": overlay.pop("from"),
            "to": overlay.pop("to"),
        }


def save_spec(
    spec: dict,
    cwd: Path | None = None,
    *,
    command: str | None = None,
    record: bool = True,
) -> dict:
    """Validate then atomically replace spec.json.

    When ``command`` is given (every mutating CLI command passes its
    name) and ``record`` is true, the write appends one revision
    diffing the tracked families against the spec currently on disk —
    the revision journal that bare ``undo`` pops. ``record=False`` is for
    undo itself, so restoring state never journals a new entry.
    In-memory maintenance that piggybacks on a write (normalization,
    caption-cache refresh, identity assignment) is attributed to that
    write's command: its save is what persisted the change.
    """
    if record and command is not None:
        prior = load_spec(cwd)
        if prior is None:
            # First write of a project: journal against the empty spec
            # so even the opening command is undoable.
            prior = empty_spec(
                [
                    {"id": s["id"], "path": s["path"]}
                    for s in spec.get("sources", [])
                ]
            )
        spec = append_revision(prior, spec, command)
    validate_spec(spec)
    path = _spec_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(spec, indent=2))
    tmp.replace(path)
    return spec
