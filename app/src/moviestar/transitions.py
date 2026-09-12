"""Pure transition boundary, window, and source-handle resolution.

This module deliberately stops before persistence or rendering. It consumes
MovieStar's canonical resolved project so scene pacing and multi-slot layouts
have already collapsed onto one result timeline, then answers whether the
requested visual transition can be produced without changing that timeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from moviestar.resolved import ResolvedProject, ResolvedSlot, ResolvedSpan


_EPSILON = 0.0005


class TransitionResolutionError(ValueError):
    """The requested boundary cannot be resolved from the project timeline."""


def _round(value: float) -> float:
    return round(float(value), 6)


@dataclass(frozen=True)
class TransitionScene:
    index: int
    name: str
    scene_id: str | None


@dataclass(frozen=True)
class TransitionSlotHandle:
    side: str
    scene_index: int
    scene_name: str
    scene_id: str | None
    slot_name: str
    slot_id: str | None
    source_id: str
    boundary_source_s: float
    edge_speed: float | None
    edge_behavior: str
    required_source_s: float
    available_source_s: float
    available_result_s: float | None
    sufficient: bool

    @property
    def maximum_transition_duration_s(self) -> float | None:
        if self.available_result_s is None:
            return None
        return _round(self.available_result_s * 2)


@dataclass(frozen=True)
class TransitionVisualSample:
    position: str
    at_s: float
    outgoing_weight: float
    incoming_weight: float
    color_weight: float


@dataclass(frozen=True)
class InternalTransitionResolution:
    transition_type: str
    duration_s: float
    outgoing: TransitionScene
    incoming: TransitionScene
    cut_s: float
    window_from_s: float
    window_to_s: float
    outgoing_handles: tuple[TransitionSlotHandle, ...]
    incoming_handles: tuple[TransitionSlotHandle, ...]
    maximum_duration_s: float
    sufficient: bool
    limit_reason: str | None
    limiting_handles: tuple[TransitionSlotHandle, ...]
    visual_samples: tuple[TransitionVisualSample, ...]


@dataclass(frozen=True)
class EdgeTransitionResolution:
    edge: str
    duration_s: float
    scene: TransitionScene
    edge_at_s: float
    window_from_s: float
    window_to_s: float
    maximum_duration_s: float
    sufficient: bool
    limit_reason: str | None
    visual_samples: tuple[TransitionVisualSample, ...]


def _scene_spans(project: ResolvedProject, scene_index: int) -> list[ResolvedSpan]:
    return sorted(
        (span for span in project.spans if span.scene_index == scene_index),
        key=lambda span: (span.start_s, span.index),
    )


def _scene_ref(spans: list[ResolvedSpan]) -> TransitionScene:
    if not spans:
        raise TransitionResolutionError("Scene has no resolved timeline spans.")
    first = spans[0]
    return TransitionScene(
        index=first.scene_index,
        name=first.scene_name,
        scene_id=first.scene_id,
    )


def _validate_duration(duration_s: float) -> float:
    duration = float(duration_s)
    if not math.isfinite(duration) or duration <= 0:
        raise TransitionResolutionError(
            "Transition duration must be a finite value greater than zero."
        )
    return _round(duration)


def _slot_handle(
    *,
    side: str,
    scene: TransitionScene,
    slot: ResolvedSlot,
    source_durations: dict[str, float],
    half_duration_s: float,
) -> TransitionSlotHandle:
    if slot.source_id not in source_durations:
        raise TransitionResolutionError(
            f"Source {slot.source_id!r} has no duration for transition "
            "handle resolution."
        )
    source_duration = float(source_durations[slot.source_id])
    boundary_source_s = (
        slot.source_to_s if side == "outgoing" else slot.source_from_s
    )
    available_source_s = (
        source_duration - boundary_source_s
        if side == "outgoing"
        else boundary_source_s
    )
    available_source_s = _round(max(0.0, available_source_s))

    if slot.held:
        return TransitionSlotHandle(
            side=side,
            scene_index=scene.index,
            scene_name=scene.name,
            scene_id=scene.scene_id,
            slot_name=slot.name,
            slot_id=slot.slot_id,
            source_id=slot.source_id,
            boundary_source_s=_round(boundary_source_s),
            edge_speed=None,
            edge_behavior="authored_hold",
            required_source_s=0.0,
            available_source_s=available_source_s,
            available_result_s=None,
            sufficient=True,
        )

    speed = float(slot.speed or 0.0)
    if not math.isfinite(speed) or speed <= 0:
        raise TransitionResolutionError(
            f"Scene {scene.name!r} slot {slot.name!r} has invalid boundary "
            f"speed {slot.speed!r}."
        )
    required_source_s = _round(half_duration_s * speed)
    available_result_s = _round(available_source_s / speed)
    return TransitionSlotHandle(
        side=side,
        scene_index=scene.index,
        scene_name=scene.name,
        scene_id=scene.scene_id,
        slot_name=slot.name,
        slot_id=slot.slot_id,
        source_id=slot.source_id,
        boundary_source_s=_round(boundary_source_s),
        edge_speed=_round(speed),
        edge_behavior="playback",
        required_source_s=required_source_s,
        available_source_s=available_source_s,
        available_result_s=available_result_s,
        sufficient=available_source_s + _EPSILON >= required_source_s,
    )


def _visual_samples(
    transition_type: str,
    *,
    window_from_s: float,
    cut_s: float,
    window_to_s: float,
) -> tuple[TransitionVisualSample, ...]:
    if transition_type == "dissolve":
        weights = ((1.0, 0.0, 0.0), (0.5, 0.5, 0.0), (0.0, 1.0, 0.0))
    else:
        weights = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))
    return tuple(
        TransitionVisualSample(position, _round(at_s), outgoing, incoming, color)
        for position, at_s, (outgoing, incoming, color) in zip(
            ("start", "cut", "end"),
            (window_from_s, cut_s, window_to_s),
            weights,
        )
    )


def resolve_internal_transition(
    project: ResolvedProject,
    source_durations: dict[str, float],
    *,
    incoming_scene_index: int,
    transition_type: str,
    duration_s: float,
) -> InternalTransitionResolution:
    """Resolve one internal boundary without changing the result timeline."""
    duration = _validate_duration(duration_s)
    if project.shape != "scenes":
        raise TransitionResolutionError(
            "Transitions require a resolved scene composition."
        )
    if transition_type not in {"dissolve", "dip-black", "dip-white"}:
        raise TransitionResolutionError(
            f"Unknown transition type {transition_type!r}."
        )
    outgoing_spans = _scene_spans(project, incoming_scene_index - 1)
    incoming_spans = _scene_spans(project, incoming_scene_index)
    if not outgoing_spans or not incoming_spans:
        raise TransitionResolutionError(
            f"Scene index {incoming_scene_index} does not identify an internal "
            "boundary."
        )
    outgoing = _scene_ref(outgoing_spans)
    incoming = _scene_ref(incoming_spans)
    outgoing_edge = outgoing_spans[-1]
    incoming_edge = incoming_spans[0]
    cut_s = _round(incoming_edge.scene_start_s)
    if not math.isclose(outgoing_edge.scene_end_s, cut_s, abs_tol=_EPSILON):
        raise TransitionResolutionError(
            f"Scenes {outgoing.name!r} and {incoming.name!r} are not contiguous."
        )
    half_duration_s = duration / 2
    window_from_s = _round(cut_s - half_duration_s)
    window_to_s = _round(cut_s + half_duration_s)

    outgoing_duration = outgoing_edge.scene_end_s - outgoing_edge.scene_start_s
    incoming_duration = incoming_edge.scene_end_s - incoming_edge.scene_start_s
    scene_max = _round(2 * min(outgoing_duration, incoming_duration))
    outgoing_handles: tuple[TransitionSlotHandle, ...] = ()
    incoming_handles: tuple[TransitionSlotHandle, ...] = ()
    handle_maxima: list[float] = []
    if transition_type == "dissolve":
        outgoing_handles = tuple(
            _slot_handle(
                side="outgoing",
                scene=outgoing,
                slot=slot,
                source_durations=source_durations,
                half_duration_s=half_duration_s,
            )
            for slot in outgoing_edge.slots
        )
        incoming_handles = tuple(
            _slot_handle(
                side="incoming",
                scene=incoming,
                slot=slot,
                source_durations=source_durations,
                half_duration_s=half_duration_s,
            )
            for slot in incoming_edge.slots
        )
        handle_maxima = [
            maximum
            for item in (*outgoing_handles, *incoming_handles)
            if (maximum := item.maximum_transition_duration_s) is not None
        ]

    maximum_duration_s = min([scene_max, *handle_maxima])
    maximum_duration_s = _round(max(0.0, maximum_duration_s))
    sufficient = duration <= maximum_duration_s + _EPSILON
    limiting_handles: tuple[TransitionSlotHandle, ...] = ()
    limit_reason = None
    if not sufficient:
        limiting_handles = tuple(
            item
            for item in (*outgoing_handles, *incoming_handles)
            if item.maximum_transition_duration_s is not None
            and math.isclose(
                item.maximum_transition_duration_s,
                maximum_duration_s,
                abs_tol=_EPSILON,
            )
        )
        limit_reason = "source_handles" if limiting_handles else "scene_window"

    return InternalTransitionResolution(
        transition_type=transition_type,
        duration_s=duration,
        outgoing=outgoing,
        incoming=incoming,
        cut_s=cut_s,
        window_from_s=window_from_s,
        window_to_s=window_to_s,
        outgoing_handles=outgoing_handles,
        incoming_handles=incoming_handles,
        maximum_duration_s=maximum_duration_s,
        sufficient=sufficient,
        limit_reason=limit_reason,
        limiting_handles=limiting_handles,
        visual_samples=_visual_samples(
            transition_type,
            window_from_s=window_from_s,
            cut_s=cut_s,
            window_to_s=window_to_s,
        ),
    )


def resolve_edge_transition(
    project: ResolvedProject,
    *,
    edge: str,
    duration_s: float,
) -> EdgeTransitionResolution:
    """Resolve an opening or closing fade inward from the project edge."""
    duration = _validate_duration(duration_s)
    if project.shape != "scenes" or not project.spans:
        raise TransitionResolutionError(
            "Transitions require a resolved scene composition."
        )
    if edge not in {"opening", "closing"}:
        raise TransitionResolutionError("Edge must be 'opening' or 'closing'.")
    scene_index = min(span.scene_index for span in project.spans)
    if edge == "closing":
        scene_index = max(span.scene_index for span in project.spans)
    scene = _scene_ref(_scene_spans(project, scene_index))
    maximum_duration_s = _round(project.duration_s)
    sufficient = duration <= maximum_duration_s + _EPSILON
    if edge == "opening":
        edge_at_s = 0.0
        window_from_s = 0.0
        window_to_s = duration
        samples = (
            TransitionVisualSample("edge", 0.0, 0.0, 0.0, 1.0),
            TransitionVisualSample("end", duration, 0.0, 1.0, 0.0),
        )
    else:
        edge_at_s = project.duration_s
        window_from_s = project.duration_s - duration
        window_to_s = project.duration_s
        samples = (
            TransitionVisualSample("start", window_from_s, 1.0, 0.0, 0.0),
            TransitionVisualSample("edge", window_to_s, 0.0, 0.0, 1.0),
        )
    return EdgeTransitionResolution(
        edge=edge,
        duration_s=duration,
        scene=scene,
        edge_at_s=_round(edge_at_s),
        window_from_s=_round(window_from_s),
        window_to_s=_round(window_to_s),
        maximum_duration_s=maximum_duration_s,
        sufficient=sufficient,
        limit_reason=None if sufficient else "project_window",
        visual_samples=samples,
    )
