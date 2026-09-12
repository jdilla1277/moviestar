"""Pure scene-slot pacing resolution.

The resolver turns authored scene ranges plus a motion document into one
deterministic result timeline.  Every pacing boundary from every slot splits
the scene for all slots.  Explicit pacing values constrain a segment's result
duration; omitted peer values are calculated so the layout stays synchronized.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace

from moviestar.spec import SpecValidationError, parse_timecode_string


_EPSILON = 0.0005


class PacingResolutionError(SpecValidationError):
    """An authored pacing plan cannot resolve to one scene timeline."""


def _round(value: float) -> float:
    return round(float(value), 6)


def _seconds(value, where: str) -> float:
    if isinstance(value, bool):
        raise PacingResolutionError(f"{where}: expected a timecode")
    if isinstance(value, (int, float)):
        return _round(value)
    return parse_timecode_string(value, where)


def _timecode(seconds: float) -> str:
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = total - hours * 3600 - minutes * 60
    return f"{hours}:{minutes:02d}:{secs:06.3f}"


def _entry_range(entry: dict, where: str) -> tuple[float, float] | None:
    value = entry.get("range")
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return _seconds(value[0], where), _seconds(value[1], where)
    if not isinstance(value, dict):
        raise PacingResolutionError(f"{where}: expected a from/to object")
    return (
        _seconds(value.get("from"), f"{where}.from"),
        _seconds(value.get("to"), f"{where}.to"),
    )


def _entry_at(entry: dict, where: str) -> float:
    return _seconds(entry.get("at"), f"{where}.at")


def _entry_duration(entry: dict, where: str) -> float:
    return _seconds(entry.get("duration"), f"{where}.duration")


@dataclass(frozen=True)
class TimeMapPoint:
    """One source-local/result-local breakpoint for a scene slot."""

    source_local_s: float
    result_local_s: float
    held: bool = False


@dataclass(frozen=True)
class ResolvedSlotSegment:
    """One slot's source mapping inside a resolved scene segment."""

    slot_name: str
    source_id: str
    source_from_s: float
    source_to_s: float
    speed: float | None
    held: bool
    values: str
    mode: str
    pacing_id: str | None

    @property
    def consumes_source_s(self) -> float:
        return _round(self.source_to_s - self.source_from_s)


@dataclass(frozen=True)
class ResolvedSegment:
    """A pacing-created segment shared by every slot in one scene."""

    id: str
    scene_index: int
    scene_name: str
    scene: dict
    start_s: float
    end_s: float
    result_local_from_s: float
    result_local_to_s: float
    source_local_from_s: float
    source_local_to_s: float
    held: bool
    slots: tuple[ResolvedSlotSegment, ...]

    @property
    def duration_s(self) -> float:
        return _round(self.end_s - self.start_s)

    def slot(self, name: str) -> ResolvedSlotSegment:
        for slot in self.slots:
            if slot.slot_name == name:
                return slot
        raise PacingResolutionError(
            f"scene {self.scene_name!r} has no resolved slot {name!r}"
        )

    def clipped(self, start_s: float, end_s: float) -> "ResolvedSegment":
        """Return this segment clipped to a global result-time window."""
        clipped_from = max(self.start_s, start_s)
        clipped_to = min(self.end_s, end_s)
        if clipped_from >= clipped_to:
            raise PacingResolutionError("cannot clip a segment to an empty range")
        from_offset = _round(clipped_from - self.start_s)
        to_offset = _round(clipped_to - self.start_s)
        clipped_slots: list[ResolvedSlotSegment] = []
        for slot in self.slots:
            if slot.held:
                source_from = slot.source_from_s
                source_to = slot.source_to_s
            else:
                assert slot.speed is not None
                source_from = _round(slot.source_from_s + from_offset * slot.speed)
                source_to = _round(slot.source_from_s + to_offset * slot.speed)
            clipped_slots.append(
                replace(slot, source_from_s=source_from, source_to_s=source_to)
            )

        if self.held:
            source_local_from = self.source_local_from_s
            source_local_to = self.source_local_to_s
        else:
            source_span = self.source_local_to_s - self.source_local_from_s
            ratio = source_span / self.duration_s
            source_local_from = _round(
                self.source_local_from_s + from_offset * ratio
            )
            source_local_to = _round(
                self.source_local_from_s + to_offset * ratio
            )
        return replace(
            self,
            start_s=clipped_from,
            end_s=clipped_to,
            result_local_from_s=_round(self.result_local_from_s + from_offset),
            result_local_to_s=_round(self.result_local_from_s + to_offset),
            source_local_from_s=source_local_from,
            source_local_to_s=source_local_to,
            slots=tuple(clipped_slots),
        )


@dataclass(frozen=True)
class ResolvedScene:
    """One authored scene placed on the paced result timeline."""

    index: int
    scene: dict
    name: str
    start_s: float
    end_s: float
    segments: tuple[ResolvedSegment, ...]

    @property
    def duration_s(self) -> float:
        return _round(self.end_s - self.start_s)

    def time_map(self, slot_name: str) -> tuple[TimeMapPoint, ...]:
        if not self.segments:
            return ()
        points = [TimeMapPoint(0.0, 0.0)]
        for segment in self.segments:
            slot = segment.slot(slot_name)
            if segment.held:
                points.append(
                    TimeMapPoint(
                        source_local_s=segment.source_local_from_s,
                        result_local_s=segment.result_local_to_s,
                        held=True,
                    )
                )
            else:
                points.append(
                    TimeMapPoint(
                        source_local_s=segment.source_local_to_s,
                        result_local_s=segment.result_local_to_s,
                    )
                )
        return tuple(points)


@dataclass(frozen=True)
class ResolvedTimeline:
    scenes: tuple[ResolvedScene, ...]

    @property
    def duration_s(self) -> float:
        return self.scenes[-1].end_s if self.scenes else 0.0

    def segment_at(self, at_s: float) -> ResolvedSegment:
        lookup = float(at_s)
        if self.scenes and math.isclose(lookup, self.duration_s, abs_tol=_EPSILON):
            lookup = max(0.0, self.duration_s - 0.000001)
        for scene in self.scenes:
            for segment in scene.segments:
                if segment.start_s <= lookup < segment.end_s:
                    return segment
        raise PacingResolutionError(
            f"result time {_timecode(at_s)} is outside the composition"
        )

    def source_time_at(self, at_s: float, slot_name: str) -> float:
        segment = self.segment_at(at_s)
        slot = segment.slot(slot_name)
        if slot.held:
            return slot.source_from_s
        assert slot.speed is not None
        return _round(slot.source_from_s + (at_s - segment.start_s) * slot.speed)

    def layout_motion_time_at(
        self, scene_index: int, at_s: float, canvas: dict
    ) -> float:
        """Return time within a contiguous run of matching slot motion.

        A newly started bounce begins at its authored placement. Adjacent
        scenes with the same resolved moving slots share a clock, preventing
        a visible reset at an editorial boundary. A static scene or a geometry
        change begins a new run because there is no continuous path to carry.
        """
        from moviestar.spec import resolve_slot_motion

        position = next(
            i for i, scene in enumerate(self.scenes)
            if scene.index == scene_index
        )
        current = self.scenes[position]

        def motion_state(scene: ResolvedScene) -> dict[str, dict]:
            return {
                slot["slot"]: motion
                for slot in scene.scene["slots"]
                if (
                    motion := resolve_slot_motion(
                        scene.scene["layout"], slot["slot"], canvas
                    )
                )
                is not None
            }

        current_state = motion_state(current)
        if not current_state:
            return _round(at_s - current.start_s)

        run_start_s = current.start_s
        for previous in reversed(self.scenes[:position]):
            if motion_state(previous) != current_state:
                break
            run_start_s = previous.start_s
        return _round(at_s - run_start_s)

    def segments_overlapping(
        self, start_s: float, end_s: float
    ) -> tuple[ResolvedSegment, ...]:
        out: list[ResolvedSegment] = []
        for scene in self.scenes:
            for segment in scene.segments:
                if max(start_s, segment.start_s) < min(end_s, segment.end_s):
                    out.append(segment.clipped(start_s, end_s))
        return tuple(out)


def _scene_duration(scene: dict) -> float:
    first = scene["slots"][0]
    start = parse_timecode_string(first["source_from"], "slot.source_from")
    end = parse_timecode_string(first["source_to"], "slot.source_to")
    return _round(end - start)


def _motion_scene(scene: dict, motion: dict | None) -> dict:
    if not motion:
        return {"slots": []}
    scene_id = scene.get("id")
    for record in motion.get("scenes", []):
        if scene_id is not None and record.get("scene_id") == scene_id:
            return record
        if record.get("scene") == scene.get("name"):
            return record
    return {"slots": []}


def _slot_entries(scene: dict, motion_scene: dict) -> dict[str, list[dict]]:
    entries = {slot["slot"]: [] for slot in scene["slots"]}
    slot_ids = {
        slot["id"]: slot["slot"]
        for slot in scene["slots"]
        if slot.get("id") is not None
    }
    for record in motion_scene.get("slots", []):
        slot_id = record.get("slot_id")
        slot_name = slot_ids.get(slot_id) if slot_id is not None else None
        slot_name = slot_name or record.get("slot")
        if slot_name in entries:
            entries[slot_name] = list(record.get("pacing", []))
    return entries


def _validated_entries(
    scene_name: str,
    scene_duration: float,
    entries_by_slot: dict[str, list[dict]],
) -> tuple[
    dict[str, list[tuple[dict, float, float]]],
    dict[float, list[tuple[str, dict]]],
]:
    ranges: dict[str, list[tuple[dict, float, float]]] = {
        slot: [] for slot in entries_by_slot
    }
    holds: dict[float, list[tuple[str, dict]]] = {}
    for slot_name, entries in entries_by_slot.items():
        for index, entry in enumerate(entries):
            where = f"scene {scene_name!r} slot {slot_name!r} pacing[{index}]"
            mode = entry.get("mode")
            if mode == "hold":
                at_s = _entry_at(entry, where)
                duration_s = _entry_duration(entry, where)
                if at_s < 0 or at_s > scene_duration + _EPSILON:
                    raise PacingResolutionError(
                        f"{where}: hold is outside 0-{_timecode(scene_duration)}"
                    )
                if duration_s <= 0:
                    raise PacingResolutionError(f"{where}: duration must be positive")
                holds.setdefault(at_s, []).append((slot_name, entry))
                continue
            if mode not in {"speed", "duration"}:
                raise PacingResolutionError(
                    f"{where}: unknown pacing mode {mode!r}"
                )
            pair = _entry_range(entry, f"{where}.range")
            assert pair is not None
            start_s, end_s = pair
            if start_s < 0 or start_s >= end_s or end_s > scene_duration + _EPSILON:
                raise PacingResolutionError(
                    f"{where}: range {_timecode(start_s)}-{_timecode(end_s)} "
                    f"is outside 0-{_timecode(scene_duration)}"
                )
            ranges[slot_name].append((entry, start_s, end_s))

        ordered = sorted(ranges[slot_name], key=lambda item: (item[1], item[2]))
        for previous, current in zip(ordered, ordered[1:]):
            if current[1] < previous[2] - _EPSILON:
                raise PacingResolutionError(
                    f"scene {scene_name!r} slot {slot_name!r}: pacing "
                    f"{previous[0].get('id')!r} and {current[0].get('id')!r} "
                    f"overlap at {_timecode(current[1])}-"
                    f"{_timecode(min(previous[2], current[2]))}"
                )
        ranges[slot_name] = ordered

        slot_holds = [
            (at_s, entry)
            for at_s, records in holds.items()
            for held_slot, entry in records
            if held_slot == slot_name
        ]
        seen_hold_points: dict[float, dict] = {}
        for at_s, hold in slot_holds:
            if at_s in seen_hold_points:
                raise PacingResolutionError(
                    f"scene {scene_name!r} slot {slot_name!r}: holds "
                    f"{seen_hold_points[at_s].get('id')!r} and "
                    f"{hold.get('id')!r} overlap at {_timecode(at_s)}"
                )
            seen_hold_points[at_s] = hold
            for ranged, start_s, end_s in ordered:
                if start_s + _EPSILON < at_s < end_s - _EPSILON:
                    raise PacingResolutionError(
                        f"scene {scene_name!r} slot {slot_name!r}: hold "
                        f"{hold.get('id')!r} falls inside pacing range "
                        f"{ranged.get('id')!r}. Smallest fix: move the hold "
                        "to a range boundary or split the range."
                    )
    return ranges, holds


def _active_range(
    ranges: list[tuple[dict, float, float]], start_s: float, end_s: float
) -> dict | None:
    for entry, entry_from, entry_to in ranges:
        if entry_from <= start_s + _EPSILON and entry_to >= end_s - _EPSILON:
            return entry
    return None


def _speed_for_entry(entry: dict, where: str) -> float:
    if entry["mode"] == "speed":
        speed = float(entry["speed"])
    else:
        start_s, end_s = _entry_range(entry, f"{where}.range") or (0.0, 0.0)
        duration_s = _entry_duration(entry, where)
        speed = (end_s - start_s) / duration_s
    if speed <= 0:
        raise PacingResolutionError(f"{where}: effective speed must be positive")
    return speed


def _segment_id(
    scene: dict, kind: str, source_from_s: float, source_to_s: float
) -> str:
    scene_key = scene.get("id") or scene.get("name") or "scene"
    raw = f"{scene_key}|{kind}|{source_from_s:.6f}|{source_to_s:.6f}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"seg_{digest}"


def _conflict_error(
    scene_name: str,
    start_s: float,
    end_s: float,
    constraints: list[tuple[str, dict, float]],
    noun: str = "range",
) -> PacingResolutionError:
    details = ", ".join(
        f"{slot}:{entry.get('id')}={_timecode(duration)}"
        for slot, entry, duration in constraints
    )
    return PacingResolutionError(
        f"scene {scene_name!r} {noun} {_timecode(start_s)}-"
        f"{_timecode(end_s)} has conflicting explicit durations: {details}. "
        "Smallest fix: remove one explicit constraint or make their result "
        "durations match."
    )


def resolve_pacing(
    composition: list[dict], motion: dict | None = None
) -> ResolvedTimeline:
    """Resolve scene pacing into global result-time segments."""
    resolved_scenes: list[ResolvedScene] = []
    global_cursor = 0.0
    for scene_index, scene in enumerate(composition):
        scene_name = scene.get("name", f"scene_{scene_index + 1}")
        source_duration = _scene_duration(scene)
        entries_by_slot = _slot_entries(scene, _motion_scene(scene, motion))
        ranges, holds = _validated_entries(
            scene_name, source_duration, entries_by_slot
        )
        boundaries = {0.0, source_duration, *holds.keys()}
        for slot_ranges in ranges.values():
            for _entry, start_s, end_s in slot_ranges:
                boundaries.update((start_s, end_s))
        ordered_boundaries = sorted(boundaries)

        source_slots = {slot["slot"]: slot for slot in scene["slots"]}
        local_cursor = 0.0
        segments: list[ResolvedSegment] = []

        def append_segment(
            *,
            source_from_s: float,
            source_to_s: float,
            result_duration: float,
            held: bool,
            authored: dict[str, dict],
        ) -> None:
            nonlocal local_cursor
            result_duration = _round(result_duration)
            segment_slots: list[ResolvedSlotSegment] = []
            for slot_name, slot in source_slots.items():
                base = parse_timecode_string(
                    slot["source_from"], f"scene {scene_name!r}.{slot_name}.source_from"
                )
                entry = authored.get(slot_name)
                if held:
                    speed = None
                    mode = "hold"
                    absolute_from = _round(base + source_from_s)
                    absolute_to = absolute_from
                else:
                    source_length = source_to_s - source_from_s
                    speed = _round(source_length / result_duration)
                    mode = entry.get("mode") if entry is not None else "speed"
                    absolute_from = _round(base + source_from_s)
                    absolute_to = _round(base + source_to_s)
                if entry is not None:
                    values = "authored"
                    pacing_id = entry.get("id")
                elif held or not math.isclose(
                    result_duration, source_to_s - source_from_s, abs_tol=_EPSILON
                ):
                    values = "calculated"
                    pacing_id = None
                else:
                    values = "default"
                    pacing_id = None
                segment_slots.append(
                    ResolvedSlotSegment(
                        slot_name=slot_name,
                        source_id=slot["source"],
                        source_from_s=absolute_from,
                        source_to_s=absolute_to,
                        speed=speed,
                        held=held,
                        values=values,
                        mode=mode,
                        pacing_id=pacing_id,
                    )
                )
            kind = "hold" if held else "range"
            segments.append(
                ResolvedSegment(
                    id=_segment_id(scene, kind, source_from_s, source_to_s),
                    scene_index=scene_index,
                    scene_name=scene_name,
                    scene=scene,
                    start_s=_round(global_cursor + local_cursor),
                    end_s=_round(global_cursor + local_cursor + result_duration),
                    result_local_from_s=local_cursor,
                    result_local_to_s=_round(local_cursor + result_duration),
                    source_local_from_s=source_from_s,
                    source_local_to_s=source_to_s,
                    held=held,
                    slots=tuple(segment_slots),
                )
            )
            local_cursor = _round(local_cursor + result_duration)

        for boundary_index, boundary in enumerate(ordered_boundaries):
            hold_records = holds.get(boundary, [])
            if hold_records:
                constraints = [
                    (
                        slot_name,
                        entry,
                        _entry_duration(entry, f"hold {entry.get('id')!r}"),
                    )
                    for slot_name, entry in hold_records
                ]
                target = constraints[0][2]
                if any(
                    not math.isclose(duration, target, abs_tol=_EPSILON)
                    for _slot, _entry, duration in constraints[1:]
                ):
                    raise _conflict_error(
                        scene_name, boundary, boundary, constraints, noun="hold"
                    )
                append_segment(
                    source_from_s=boundary,
                    source_to_s=boundary,
                    result_duration=target,
                    held=True,
                    authored={slot: entry for slot, entry in hold_records},
                )

            if boundary_index == len(ordered_boundaries) - 1:
                continue
            next_boundary = ordered_boundaries[boundary_index + 1]
            if next_boundary <= boundary + _EPSILON:
                continue
            source_length = next_boundary - boundary
            authored = {
                slot_name: entry
                for slot_name, slot_ranges in ranges.items()
                if (entry := _active_range(slot_ranges, boundary, next_boundary))
                is not None
            }
            constraints = []
            for slot_name, entry in authored.items():
                speed = _speed_for_entry(entry, f"pacing {entry.get('id')!r}")
                constraints.append((slot_name, entry, _round(source_length / speed)))
            target = constraints[0][2] if constraints else source_length
            if any(
                not math.isclose(duration, target, abs_tol=_EPSILON)
                for _slot, _entry, duration in constraints[1:]
            ):
                raise _conflict_error(
                    scene_name, boundary, next_boundary, constraints
                )
            append_segment(
                source_from_s=boundary,
                source_to_s=next_boundary,
                result_duration=target,
                held=False,
                authored=authored,
            )

        scene_end = _round(global_cursor + local_cursor)
        resolved_scenes.append(
            ResolvedScene(
                index=scene_index,
                scene=scene,
                name=scene_name,
                start_s=global_cursor,
                end_s=scene_end,
                segments=tuple(segments),
            )
        )
        global_cursor = scene_end
    return ResolvedTimeline(tuple(resolved_scenes))
