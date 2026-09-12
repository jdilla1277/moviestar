"""The project compiler — one canonical resolved timeline for every shape.

Every project form — source-only, flat concat, global layout, scene
composition — resolves
into the same ordered list of :class:`ResolvedSpan` objects before any
downstream system (captions, overlays, audio, status, preview, export)
consumes it.

The scene path builds on the pacing resolver
(:func:`moviestar.motion.resolve_pacing`), which already models
source-local, scene-local, and global result coordinate spaces. The
other shapes are adapters into the same span model: a flat concat is a
sequence of implicit single-slot full-frame scenes, a global layout is
one scene, a source-only edit is one implicit scene per surviving
source segment.

Two contracts distinguish this model from the per-shape plan builders
it consolidates:

- Audio is carried as a **slice list** (source-time ranges surviving
  the source's edit stack), never a collapsed outer range. A scene
  whose routed audio has internal cuts keeps every gap visible.
- Spans are contiguous in global result time and cover exactly
  ``[0, duration_s]`` for every shape, so consumers never re-derive
  the finished-video clock per shape.

The parity matrix in ``tests/test_resolved.py`` locks the model against the
existing plan builders while surfaces migrate to the shared representation.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field, replace

from moviestar.motion import resolve_pacing
from moviestar.spec import (
    composition_shape,
    layout_regions,
    parse_timecode_string,
    resolve_source,
    slice_audio_from_anchor,
)
from moviestar.timecodes import format_timecode

_DEFAULT_FPS = 30.0
_DEFAULT_FRAMING = {"mode": "fill", "anchor": "center"}

_HOLD_AUDIO_NOTE = "Pacing hold inserts silence for this result-time segment."
_MULTI_AUDIO_NOTE = (
    "Scene has multiple audio-capable slots but no audio_from; "
    "this segment will be video-only."
)


class ResolvedProjectError(Exception):
    """The project cannot resolve to one finished-video timeline."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        self.message = message
        self.hint = hint
        super().__init__(message)


@dataclass(frozen=True)
class ResolvedAudio:
    """The routed audio for one span, cut-aware.

    ``slices`` are source-time ranges on ``source_id`` surviving that
    source's edit stack — never a collapsed outer range. ``routing``
    records why this source was chosen: ``"composition"`` (authored
    audio_from), ``"only_audio_slot"`` (inferred single audio-capable
    slot), ``"segment"`` (the span's own visual segment), or ``None``.
    """

    source_id: str | None
    routing: str | None
    slices: tuple[tuple[float, float], ...] = ()
    anchor_s: float | None = None
    speed: float = 1.0
    note: str | None = None


@dataclass(frozen=True)
class ResolvedSlot:
    """One slot's resolved playback within a span."""

    name: str
    source_id: str
    source_from_s: float
    source_to_s: float
    speed: float | None = 1.0
    held: bool = False
    values: str = "default"
    pacing_id: str | None = None
    slot_id: str | None = None
    region: dict | None = None
    framing: dict | None = None


@dataclass(frozen=True)
class ResolvedSpan:
    """One contiguous piece of the finished video.

    Carries every coordinate space at once: global result
    (``start_s``/``end_s``), the owning scene's global range
    (``scene_start_s``/``scene_end_s``), and the scene-local range
    (``scene_local_from_s``/``scene_local_to_s``).
    """

    index: int
    scene_index: int
    scene_name: str
    start_s: float
    end_s: float
    scene_start_s: float
    scene_end_s: float
    scene_local_from_s: float
    scene_local_to_s: float
    slots: tuple[ResolvedSlot, ...]
    audio: ResolvedAudio
    scene_id: str | None = None
    pacing_segment_id: str | None = None
    held: bool = False
    layout: str | None = None
    canvas: dict | None = None
    output_fps: float = _DEFAULT_FPS

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 6)

    def slot(self, name: str) -> ResolvedSlot | None:
        for slot in self.slots:
            if slot.name == name:
                return slot
        return None


@dataclass(frozen=True)
class ResolvedProject:
    """The canonical compiled timeline for a project.

    ``shape`` names the authored form (``"source"``, ``"concat"``,
    ``"layout"``, ``"scenes"``) for reporting only — consumers should
    branch on span content, not on shape.
    """

    shape: str
    duration_s: float
    spans: tuple[ResolvedSpan, ...]
    canvas: dict | None = None
    overlays_burnable: bool = False
    output_fps: float = _DEFAULT_FPS

    def span_at(self, at_s: float) -> ResolvedSpan:
        """The span playing at global result time ``at_s``.

        The exact end of the timeline resolves to the last span, so
        callers probing ``duration_s`` don't fall off the edge.
        """
        if not self.spans:
            raise ResolvedProjectError("Project resolves to an empty timeline.")
        last = len(self.spans) - 1
        for i, span in enumerate(self.spans):
            if at_s < span.end_s or i == last:
                return span
        return self.spans[last]

    def spans_overlapping(
        self, start_s: float, end_s: float
    ) -> list[ResolvedSpan]:
        return [
            span
            for span in self.spans
            if span.end_s > start_s and span.start_s < end_s
        ]


@dataclass(frozen=True)
class ResolvedOverlays:
    """Manual overlay timing compiled onto the finished-video clock."""

    records: tuple[dict, ...]
    detached: tuple[dict, ...] = ()
    clipped: tuple[dict, ...] = ()


def resolve_overlays(
    project: ResolvedProject, overlays: list[dict]
) -> ResolvedOverlays:
    """Resolve stored result/scene timing into planner-ready records.

    Caption cues already carry derived result-time ranges and pass through.
    Manual records retain their authored ``timing`` block while gaining
    transient top-level ``from``/``to`` values for the existing renderer.
    """
    records: list[dict] = []
    detached: list[dict] = []
    clipped: list[dict] = []
    spans_by_scene: dict[str, list[ResolvedSpan]] = {}
    for span in project.spans:
        if span.scene_id:
            spans_by_scene.setdefault(span.scene_id, []).append(span)

    for overlay in overlays:
        if overlay.get("kind") != "manual":
            records.append(copy.deepcopy(overlay))
            continue
        timing = overlay["timing"]
        from_s = parse_timecode_string(timing["from"], "overlay.timing.from")
        to_s = parse_timecode_string(timing["to"], "overlay.timing.to")
        record = copy.deepcopy(overlay)
        if timing["space"] == "result":
            record["from"] = format_timecode(from_s)["text"]
            record["to"] = format_timecode(to_s)["text"]
            record["resolved_timing"] = {
                "space": "result",
                "from_s": from_s,
                "to_s": to_s,
            }
            records.append(record)
            continue

        scene_id = timing["scene"]
        scene_spans = spans_by_scene.get(scene_id)
        if not scene_spans:
            detached.append(
                {
                    "id": overlay["id"],
                    "text": overlay["text"],
                    "scene": scene_id,
                }
            )
            continue
        scene_start = min(span.scene_start_s for span in scene_spans)
        scene_end = max(span.scene_end_s for span in scene_spans)
        scene_duration = scene_end - scene_start
        visible_from = min(from_s, scene_duration)
        visible_to = min(to_s, scene_duration)
        renders = from_s < scene_duration
        if to_s > scene_duration:
            clipped.append(
                {
                    "id": overlay["id"],
                    "text": overlay["text"],
                    "scene": scene_id,
                    "scene_name": scene_spans[0].scene_name,
                    "requested_from_s": from_s,
                    "requested_to_s": to_s,
                    "visible_from_s": visible_from,
                    "visible_to_s": visible_to,
                    "renders": renders,
                }
            )
        if not renders:
            continue
        record["from"] = format_timecode(scene_start + from_s)["text"]
        record["to"] = format_timecode(scene_start + visible_to)["text"]
        record["resolved_timing"] = {
            "space": "scene",
            "scene": scene_id,
            "scene_name": scene_spans[0].scene_name,
            "from_s": from_s,
            "to_s": to_s,
            "result_from_s": scene_start + from_s,
            "result_to_s": scene_start + visible_to,
            "visibility": "clipped" if to_s > scene_duration else "visible",
        }
        records.append(record)

    return ResolvedOverlays(
        records=tuple(records),
        detached=tuple(detached),
        clipped=tuple(clipped),
    )


# -------------------- shape detection --------------------
#
# Shape detection lives in spec.composition_shape (stage 3 moved it
# there beside the storage normalizer); re-exported here for callers
# of the compiler.


# -------------------- resolvers per shape --------------------


def _tc(value, where: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return parse_timecode_string(value, where)


def _source_index(project: dict) -> dict[str, dict]:
    return {source["id"]: source for source in project.get("sources", [])}


def _source_fps(source: dict | None) -> float:
    if not source:
        return _DEFAULT_FPS
    return float(source.get("fps") or _DEFAULT_FPS)


def _composition_output_fps(
    project: dict, spans: tuple[ResolvedSpan, ...]
) -> float:
    """Choose one cadence from the active video sources in the timeline."""
    active_source_ids = {
        slot.source_id for span in spans for slot in span.slots
    }
    rates: list[float] = []
    for source in project.get("sources", []):
        if source.get("id") not in active_source_ids:
            continue
        try:
            rate = float(source.get("fps"))
        except (TypeError, ValueError):
            continue
        if rate > 0 and math.isfinite(rate):
            rates.append(rate)
    return max(rates, default=_DEFAULT_FPS)


def _normalize_output_fps(
    project: dict, resolved: ResolvedProject
) -> ResolvedProject:
    """Apply the compiled project cadence to every independently rendered span."""
    output_fps = _composition_output_fps(project, resolved.spans)
    return replace(
        resolved,
        output_fps=output_fps,
        spans=tuple(
            replace(span, output_fps=output_fps) for span in resolved.spans
        ),
    )


def _segment_audio(source: dict | None, from_s: float, to_s: float) -> ResolvedAudio:
    """A span playing its own visual segment's audio (source/concat)."""
    if not source or not source.get("audio_codec"):
        return ResolvedAudio(source_id=None, routing=None)
    return ResolvedAudio(
        source_id=source["id"],
        routing="segment",
        slices=((from_s, to_s),),
        anchor_s=from_s,
    )


def _split_slices(
    slices: list[tuple[float, float]], durations: list[float]
) -> list[tuple[tuple[float, float], ...]]:
    """Split one global slice walk into per-span slice lists.

    Used when a flat concat routes one audio source across the whole
    composition: the global ``slice_audio_from_anchor`` result is
    divided sequentially so each span carries exactly its own share.
    """
    out: list[tuple[tuple[float, float], ...]] = []
    remaining = [list(s) for s in slices]
    for duration in durations:
        need = duration
        share: list[tuple[float, float]] = []
        while remaining and need > 1e-9:
            seg_from, seg_to = remaining[0]
            take = min(seg_to - seg_from, need)
            share.append((seg_from, seg_from + take))
            need -= take
            if seg_to - seg_from - take <= 1e-9:
                remaining.pop(0)
            else:
                remaining[0][0] = seg_from + take
        out.append(tuple(share))
    return out


def _resolve_source_only(project: dict, spec: dict) -> ResolvedProject:
    sources = project.get("sources") or []
    if len(sources) != 1:
        raise ResolvedProjectError(
            "This multi-source project has no composition, so there is no "
            "single finished-video timeline to resolve.",
            hint=(
                "Run 'moviestar scenes set' or 'moviestar concat' to build "
                "the visual timeline first."
            ),
        )
    source = sources[0]
    timeline = resolve_source(
        spec, source["id"], float(source["duration"]["seconds"])
    )
    spans: list[ResolvedSpan] = []
    cursor = 0.0
    for i, (seg_from, seg_to) in enumerate(timeline.source_segments):
        seg_duration = seg_to - seg_from
        start_s = round(cursor, 6)
        end_s = round(cursor + seg_duration, 6)
        spans.append(
            ResolvedSpan(
                index=i,
                scene_index=i,
                scene_name=f"segment_{i + 1}",
                start_s=start_s,
                end_s=end_s,
                scene_start_s=start_s,
                scene_end_s=end_s,
                scene_local_from_s=0.0,
                scene_local_to_s=round(seg_duration, 6),
                slots=(
                    ResolvedSlot(
                        name="main",
                        source_id=source["id"],
                        source_from_s=seg_from,
                        source_to_s=seg_to,
                    ),
                ),
                audio=_segment_audio(source, seg_from, seg_to),
                output_fps=_source_fps(source),
            )
        )
        cursor = end_s
    return ResolvedProject(
        shape="source",
        duration_s=round(timeline.effective_duration, 6),
        spans=tuple(spans),
    )


def _resolve_concat(project: dict, spec: dict) -> ResolvedProject:
    composition = spec["composition"]
    sources = _source_index(project)
    canvas = spec.get("composition_canvas")

    parsed: list[tuple[str, float, float, dict | None]] = []
    for seg in composition:
        parsed.append(
            (
                seg["source"],
                _tc(seg["source_from"], "composition.source_from"),
                _tc(seg["source_to"], "composition.source_to"),
                seg.get("framing"),
            )
        )
    durations = [to_s - from_s for _sid, from_s, to_s, _f in parsed]
    total = sum(durations)

    audio_from = spec.get("composition_audio_from")
    routed_slices: list[tuple[tuple[float, float], ...]] | None = None
    routed_anchor: float | None = None
    if audio_from is not None and audio_from in sources:
        anchor = next(
            (from_s for sid, from_s, _to, _f in parsed if sid == audio_from),
            None,
        )
        if anchor is not None:
            af_source = sources[audio_from]
            af_timeline = resolve_source(
                spec, audio_from, float(af_source["duration"]["seconds"])
            )
            global_slices = slice_audio_from_anchor(
                af_timeline.source_segments, anchor, total
            )
            routed_slices = _split_slices(global_slices, durations)
            routed_anchor = anchor

    spans: list[ResolvedSpan] = []
    cursor = 0.0
    for i, (source_id, from_s, to_s, framing) in enumerate(parsed):
        source = sources.get(source_id)
        start_s = round(cursor, 6)
        end_s = round(cursor + (to_s - from_s), 6)
        if routed_slices is not None:
            audio = ResolvedAudio(
                source_id=audio_from,
                routing="composition",
                slices=routed_slices[i],
                anchor_s=routed_anchor if i == 0 else None,
            )
        else:
            audio = _segment_audio(source, from_s, to_s)
        spans.append(
            ResolvedSpan(
                index=i,
                scene_index=i,
                scene_name=f"segment_{i + 1}",
                start_s=start_s,
                end_s=end_s,
                scene_start_s=start_s,
                scene_end_s=end_s,
                scene_local_from_s=0.0,
                scene_local_to_s=round(to_s - from_s, 6),
                slots=(
                    ResolvedSlot(
                        name="main",
                        source_id=source_id,
                        source_from_s=from_s,
                        source_to_s=to_s,
                        framing=framing,
                    ),
                ),
                audio=audio,
                canvas=canvas,
                output_fps=_source_fps(source),
            )
        )
        cursor = end_s
    return ResolvedProject(
        shape="concat",
        duration_s=round(total, 6),
        spans=tuple(spans),
        canvas=canvas,
    )


def _resolve_layout(project: dict, spec: dict) -> ResolvedProject:
    composition = spec["composition"]
    scene = composition[0]
    canvas = spec.get("composition_canvas")
    if canvas is None:
        raise ResolvedProjectError(
            "Layout composition is missing composition_canvas."
        )
    sources = _source_index(project)
    regions = layout_regions(scene["layout"], canvas, result_time_s=0.0)

    slots: list[ResolvedSlot] = []
    slot_fps: list[float] = []
    audio_capable: list[ResolvedSlot] = []
    duration = 0.0
    for i, slot in enumerate(scene["slots"]):
        from_s = _tc(slot["source_from"], "slot.source_from")
        to_s = _tc(slot["source_to"], "slot.source_to")
        if i == 0:
            duration = to_s - from_s
        source = sources.get(slot["source"])
        resolved_slot = ResolvedSlot(
            name=slot["slot"],
            source_id=slot["source"],
            source_from_s=from_s,
            source_to_s=to_s,
            slot_id=slot.get("id"),
            region=regions[slot["slot"]],
            framing=slot.get("framing", dict(_DEFAULT_FRAMING)),
        )
        slots.append(resolved_slot)
        slot_fps.append(_source_fps(source))
        if source and source.get("audio_codec"):
            audio_capable.append(resolved_slot)

    audio_from = spec.get("composition_audio_from")
    if audio_from is not None:
        af_slot = next(
            (slot for slot in slots if slot.source_id == audio_from), None
        )
        af_source = sources.get(audio_from)
        if af_slot is None or af_source is None:
            raise ResolvedProjectError(
                f"composition_audio_from {audio_from!r} is not assigned to a "
                "layout slot."
            )
        af_timeline = resolve_source(
            spec, audio_from, float(af_source["duration"]["seconds"])
        )
        slices = slice_audio_from_anchor(
            af_timeline.source_segments, af_slot.source_from_s, duration
        )
        audio = ResolvedAudio(
            source_id=audio_from,
            routing="composition",
            slices=tuple(slices),
            anchor_s=af_slot.source_from_s,
        )
    elif len(audio_capable) == 1:
        only = audio_capable[0]
        audio = ResolvedAudio(
            source_id=only.source_id,
            routing="only_audio_slot",
            slices=((only.source_from_s, only.source_to_s),),
            anchor_s=only.source_from_s,
        )
    else:
        audio = ResolvedAudio(
            source_id=None,
            routing=None,
            note=(
                _MULTI_AUDIO_NOTE if len(audio_capable) > 1 else None
            ),
        )

    duration = round(duration, 6)
    span = ResolvedSpan(
        index=0,
        scene_index=0,
        scene_name=scene.get("name", "scene_1"),
        scene_id=scene.get("id"),
        start_s=0.0,
        end_s=duration,
        scene_start_s=0.0,
        scene_end_s=duration,
        scene_local_from_s=0.0,
        scene_local_to_s=duration,
        slots=tuple(slots),
        audio=audio,
        layout=scene["layout"]["preset"],
        canvas=canvas,
        output_fps=max(slot_fps, default=_DEFAULT_FPS),
    )
    return ResolvedProject(
        shape="layout",
        duration_s=duration,
        spans=(span,),
        canvas=canvas,
        overlays_burnable=True,
    )


def composition_audio_anchor(
    composition: list[dict], audio_from: str
) -> float | None:
    """Source-time anchor for a composition-wide audio route: the
    routed source's first slot appearance, in composition order.

    Mirrors concat's contract — "audio starts at the named source's
    first composition segment." Returns None when the source is not
    assigned to any slot.
    """
    for scene in composition:
        for slot in scene.get("slots", []):
            if slot["source"] == audio_from:
                return _tc(slot["source_from"], "slot.source_from")
    return None


def global_audio_slices(
    audio_segments,
    anchor_s: float,
    window_from_s: float,
    window_to_s: float,
) -> tuple[tuple[float, float], ...]:
    """Slices of a globally routed audio source heard in a result-time
    window.

    The route is anchored at ``anchor_s`` in the source's surviving
    segments and plays linearly at 1x from result time 0, so the audio
    heard at result time ``t`` is ``t`` seconds along the anchored
    concatenation of surviving slices. Cut holes in the audio source
    are skipped, pacing does not retime the route, and the route keeps
    playing through holds.
    """
    full = slice_audio_from_anchor(audio_segments, anchor_s, window_to_s)
    out: list[tuple[float, float]] = []
    skip = window_from_s
    for seg_from, seg_to in full:
        length = seg_to - seg_from
        if skip >= length - 1e-9:
            skip -= length
            continue
        out.append((round(seg_from + max(skip, 0.0), 6), seg_to))
        skip = 0.0
    return tuple(out)


def _scene_segment_audio(
    *,
    spec: dict,
    scene: dict,
    segment,
    sources: dict[str, dict],
) -> ResolvedAudio:
    """Mirror of the render path's per-segment audio resolution.

    Same routing vocabulary and the same cut-aware slice walk the
    exporter uses (``slice_audio_from_anchor`` over the audio source's
    surviving segments, anchored at the slot's source position).
    """
    audio_from = scene.get("audio_from")
    routing = "composition" if audio_from is not None else None
    if audio_from is None:
        capable = [
            slot
            for slot in segment.slots
            if sources.get(slot.source_id, {}).get("audio_codec")
        ]
        if len(capable) == 1:
            audio_from = capable[0].source_id
            routing = "only_audio_slot"
        elif len(capable) > 1:
            return ResolvedAudio(
                source_id=None, routing=None, note=_MULTI_AUDIO_NOTE
            )

    audio_slot = next(
        (slot for slot in segment.slots if slot.source_id == audio_from),
        None,
    )
    if audio_from is None or audio_slot is None:
        return ResolvedAudio(source_id=audio_from, routing=routing)
    if audio_slot.held:
        return ResolvedAudio(
            source_id=audio_from,
            routing=routing,
            speed=1.0,
            note=_HOLD_AUDIO_NOTE,
        )
    source = sources[audio_slot.source_id]
    timeline = resolve_source(
        spec, audio_slot.source_id, float(source["duration"]["seconds"])
    )
    slices = slice_audio_from_anchor(
        timeline.source_segments,
        audio_slot.source_from_s,
        audio_slot.consumes_source_s,
    )
    return ResolvedAudio(
        source_id=audio_from,
        routing=routing,
        slices=tuple(slices),
        anchor_s=audio_slot.source_from_s,
        speed=float(audio_slot.speed or 1.0),
    )


def _resolve_scenes(project: dict, spec: dict) -> ResolvedProject:
    composition = spec["composition"]
    canvas = spec.get("composition_canvas")
    if canvas is None:
        raise ResolvedProjectError(
            "Scene composition is missing composition_canvas."
        )
    sources = _source_index(project)
    timeline = resolve_pacing(composition, spec.get("motion"))

    # A composition-wide audio route overrides per-scene routing: one
    # continuous track anchored at the routed source's first slot
    # appearance, linear at 1x in result time (see global_audio_slices).
    global_audio_from = spec.get("composition_audio_from")
    global_anchor: float | None = None
    global_segments: tuple[tuple[float, float], ...] | None = None
    if global_audio_from is not None:
        global_anchor = composition_audio_anchor(
            composition, global_audio_from
        )
        af_source = sources.get(global_audio_from)
        if global_anchor is None or af_source is None:
            raise ResolvedProjectError(
                f"composition_audio_from {global_audio_from!r} is not "
                "assigned to any scene slot."
            )
        global_segments = resolve_source(
            spec, global_audio_from, float(af_source["duration"]["seconds"])
        ).source_segments

    spans: list[ResolvedSpan] = []
    index = 0
    for resolved_scene in timeline.scenes:
        scene = resolved_scene.scene
        authored_slots = {slot["slot"]: slot for slot in scene["slots"]}
        for segment in resolved_scene.segments:
            regions = layout_regions(
                scene["layout"],
                canvas,
                result_time_s=timeline.layout_motion_time_at(
                    resolved_scene.index, segment.start_s, canvas
                ),
            )
            slots: list[ResolvedSlot] = []
            slot_fps: list[float] = []
            for resolved_slot in segment.slots:
                authored = authored_slots.get(resolved_slot.slot_name, {})
                slots.append(
                    ResolvedSlot(
                        name=resolved_slot.slot_name,
                        source_id=resolved_slot.source_id,
                        source_from_s=resolved_slot.source_from_s,
                        source_to_s=resolved_slot.source_to_s,
                        speed=resolved_slot.speed,
                        held=resolved_slot.held,
                        values=resolved_slot.values,
                        pacing_id=resolved_slot.pacing_id,
                        slot_id=authored.get("id"),
                        region=regions[resolved_slot.slot_name],
                        framing=authored.get(
                            "framing", dict(_DEFAULT_FRAMING)
                        ),
                    )
                )
                slot_fps.append(
                    _source_fps(sources.get(resolved_slot.source_id))
                )
            if global_segments is not None:
                assert global_anchor is not None
                audio = ResolvedAudio(
                    source_id=global_audio_from,
                    routing="composition",
                    slices=global_audio_slices(
                        global_segments,
                        global_anchor,
                        segment.start_s,
                        segment.end_s,
                    ),
                    anchor_s=global_anchor,
                    speed=1.0,
                )
            else:
                audio = _scene_segment_audio(
                    spec=spec,
                    scene=scene,
                    segment=segment,
                    sources=sources,
                )
            spans.append(
                ResolvedSpan(
                    index=index,
                    scene_index=resolved_scene.index,
                    scene_name=resolved_scene.name,
                    scene_id=scene.get("id"),
                    pacing_segment_id=segment.id,
                    start_s=segment.start_s,
                    end_s=segment.end_s,
                    scene_start_s=resolved_scene.start_s,
                    scene_end_s=resolved_scene.end_s,
                    scene_local_from_s=segment.result_local_from_s,
                    scene_local_to_s=segment.result_local_to_s,
                    slots=tuple(slots),
                    audio=audio,
                    held=segment.held,
                    layout=scene["layout"]["preset"],
                    canvas=canvas,
                    output_fps=max(slot_fps, default=_DEFAULT_FPS),
                )
            )
            index += 1
    return ResolvedProject(
        shape="scenes",
        duration_s=timeline.duration_s,
        spans=tuple(spans),
        canvas=canvas,
        overlays_burnable=True,
    )


# -------------------- entry point --------------------


def resolve_project(project: dict, spec: dict) -> ResolvedProject:
    """Compile a project into the canonical resolved timeline.

    ``project`` is the parsed project.json (source metadata), ``spec``
    the parsed spec.json (edits, composition, motion). Raises
    :class:`ResolvedProjectError` when the project has no single
    finished-video timeline (multi-source with no composition, missing
    canvas). Pacing conflicts propagate as
    :class:`moviestar.motion.PacingResolutionError`.
    """
    shape = composition_shape(spec)
    if shape == "source":
        resolved = _resolve_source_only(project, spec)
    elif shape == "concat":
        resolved = _resolve_concat(project, spec)
    elif shape == "layout":
        resolved = _resolve_layout(project, spec)
    else:
        resolved = _resolve_scenes(project, spec)
    return _normalize_output_fps(project, resolved)
