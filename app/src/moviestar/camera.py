"""Pure scene-slot camera resolution.

Camera records are authored in scene result-local time and source-relative
coordinates.  This module normalizes targets, derives renderable crops and
zoom, resolves persistent/default states, and maps moves back through the
paced scene timeline without invoking FFmpeg.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace

from moviestar.motion import ResolvedScene, ResolvedTimeline
from moviestar.spec import SpecValidationError, layout_regions, parse_timecode_string


_EPSILON = 0.0005


class CameraResolutionError(SpecValidationError):
    """A camera plan cannot resolve safely."""


class CameraGeometryMutationError(CameraResolutionError):
    """A layout edit would make a named camera move unsafe."""

    def __init__(self, message: str, *, camera_id: str):
        super().__init__(message)
        self.camera_id = camera_id


def _round(value: float) -> float:
    return round(float(value), 6)


def _seconds(value, where: str) -> float:
    if isinstance(value, bool):
        raise CameraResolutionError(f"{where}: expected a timecode")
    if isinstance(value, (int, float)):
        return _round(value)
    return parse_timecode_string(value, where)


def _rect_dict(x: float, y: float, w: float, h: float) -> dict[str, float]:
    return {"x": _round(x), "y": _round(y), "w": _round(w), "h": _round(h)}


@dataclass(frozen=True)
class CameraState:
    target: str
    authored_units: str
    rect_pixels: dict[str, float]
    rect_normalized: dict[str, float]
    resolved_crop_pixels: dict[str, float]
    zoom: float
    adjustments: tuple[str, ...] = ()
    active_move_id: str | None = None
    progress: float | None = None
    eased_progress: float | None = None


@dataclass(frozen=True)
class ResolvedCameraMove:
    id: str
    scene_index: int
    scene_name: str
    slot_name: str
    result_local_from_s: float
    result_local_to_s: float
    result_from_s: float
    result_to_s: float
    ease: str
    from_state: CameraState
    to_state: CameraState
    from_state_source: str
    default_duration_applied: bool
    source_equivalent_range: tuple[float, float]

    @property
    def duration_s(self) -> float:
        return _round(self.result_local_to_s - self.result_local_from_s)


@dataclass(frozen=True)
class SourceAnchor:
    kind: str
    source_local_s: float
    hold_offset_s: float | None = None


@dataclass(frozen=True)
class ResolvedCameraPlan:
    pacing: ResolvedTimeline
    moves: tuple[ResolvedCameraMove, ...]
    default_states: dict[tuple[str, str], CameraState]

    def _slot_moves(self, scene_name: str, slot_name: str):
        return tuple(
            move
            for move in self.moves
            if move.scene_name == scene_name and move.slot_name == slot_name
        )

    def state_at(
        self, scene_name: str, slot_name: str, result_local_s: float
    ) -> CameraState:
        state = self.default_states[(scene_name, slot_name)]
        at_s = float(result_local_s)
        for move in self._slot_moves(scene_name, slot_name):
            if at_s < move.result_local_from_s - _EPSILON:
                break
            if move.duration_s <= _EPSILON:
                state = move.to_state
                continue
            if at_s >= move.result_local_to_s - _EPSILON:
                state = move.to_state
                continue
            progress = min(
                1.0,
                max(0.0, (at_s - move.result_local_from_s) / move.duration_s),
            )
            eased = _ease(progress, move.ease)
            return _interpolate_state(
                move.from_state,
                move.to_state,
                eased,
                move.id,
                progress,
            )
        return state

    def source_anchor_at(
        self, scene_name: str, slot_name: str, result_local_s: float
    ) -> SourceAnchor:
        scene = next(scene for scene in self.pacing.scenes if scene.name == scene_name)
        return _source_anchor(scene, slot_name, result_local_s)

    def animation_for(
        self, scene_name: str, slot_name: str, result_local_from_s: float
    ) -> dict | None:
        """Return the numeric, FFmpeg-ready timeline for one rendered clip.

        The move times remain scene-result-local. ``result_local_from`` tells
        the renderer where this independently rendered pacing clip begins, so
        one camera move stays continuous across pacing-created clip boundaries.
        """
        moves = self._slot_moves(scene_name, slot_name)
        if not moves:
            return None

        def crop(state: CameraState) -> dict[str, float]:
            return dict(state.resolved_crop_pixels)

        return {
            "result_local_from": _round(result_local_from_s),
            "default_crop": crop(self.default_states[(scene_name, slot_name)]),
            "moves": [
                {
                    "id": move.id,
                    "from": move.result_local_from_s,
                    "to": move.result_local_to_s,
                    "ease": move.ease,
                    "from_crop": crop(move.from_state),
                    "to_crop": crop(move.to_state),
                }
                for move in moves
            ],
        }


def _timecode(seconds: float) -> str:
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = total - hours * 3600 - minutes * 60
    return f"{hours}:{minutes:02d}:{secs:06.3f}"


def _ease(progress: float, ease: str) -> float:
    if ease == "linear":
        return progress
    if ease == "in":
        return progress * progress
    if ease == "out":
        return 1.0 - (1.0 - progress) ** 2
    if ease == "in-out":
        return progress * progress * (3.0 - 2.0 * progress)
    if ease == "cut":
        return 1.0
    raise CameraResolutionError(f"unknown camera ease {ease!r}")


def _normalize_rect(rect: dict, width: float, height: float, units: str):
    values = {key: float(rect[key]) for key in ("x", "y", "w", "h")}
    if units == "normalized":
        normalized = values
        pixels = _rect_dict(
            values["x"] * width,
            values["y"] * height,
            values["w"] * width,
            values["h"] * height,
        )
    elif units == "pixels":
        pixels = values
        normalized = _rect_dict(
            values["x"] / width,
            values["y"] / height,
            values["w"] / width,
            values["h"] / height,
        )
    else:
        raise CameraResolutionError(f"camera target units must be pixels or normalized")
    if (
        pixels["x"] < 0
        or pixels["y"] < 0
        or pixels["w"] <= 0
        or pixels["h"] <= 0
        or pixels["x"] + pixels["w"] > width + _EPSILON
        or pixels["y"] + pixels["h"] > height + _EPSILON
    ):
        raise CameraResolutionError(
            f"camera target {pixels} is outside {int(width)}x{int(height)} source bounds"
        )
    return pixels, normalized


def _aspect_crop(
    rect: dict[str, float],
    source: tuple[float, float],
    region: dict,
) -> tuple[dict[str, float], tuple[str, ...]]:
    width, height = source
    target_aspect = float(region["width"]) / float(region["height"])
    crop_w, crop_h = rect["w"], rect["h"]
    adjustments: list[str] = []
    if not math.isclose(crop_w / crop_h, target_aspect, rel_tol=1e-6):
        adjustments.append("aspect_corrected")
        if crop_w / crop_h < target_aspect:
            crop_w = crop_h * target_aspect
        else:
            crop_h = crop_w / target_aspect
    if crop_w > width:
        crop_w = width
        crop_h = width / target_aspect
        adjustments.append("reduced_to_source_bounds")
    if crop_h > height:
        crop_h = height
        crop_w = height * target_aspect
        adjustments.append("reduced_to_source_bounds")
    center_x = rect["x"] + rect["w"] / 2
    center_y = rect["y"] + rect["h"] / 2
    x = min(max(center_x - crop_w / 2, 0.0), width - crop_w)
    y = min(max(center_y - crop_h / 2, 0.0), height - crop_h)
    if not math.isclose(x, center_x - crop_w / 2, abs_tol=_EPSILON) or not math.isclose(
        y, center_y - crop_h / 2, abs_tol=_EPSILON
    ):
        adjustments.append("shifted_inside_source_bounds")
    return _rect_dict(x, y, crop_w, crop_h), tuple(dict.fromkeys(adjustments))


def fill_visible_rect(
    rect: dict[str, float], region: dict, framing: dict | None = None
) -> dict[str, float]:
    """The sub-rect of ``rect`` the slot actually shows on the canvas.

    Mirrors the renderer's ``_canvas_filter``: fill framing scales the
    (camera-cropped) content to cover the slot region, then crops to the
    region's aspect at the framing anchor. Fit framing shows all of
    ``rect`` (with padding bars), so the rect comes back unchanged.
    """
    mode = (framing or {}).get("mode", "fill")
    if mode == "fit":
        return _rect_dict(**{key: rect[key] for key in ("x", "y", "w", "h")})
    region_w = float(region["width"])
    region_h = float(region["height"])
    scale = max(region_w / rect["w"], region_h / rect["h"])
    visible_w = region_w / scale
    visible_h = region_h / scale
    anchor = (framing or {}).get("anchor", "center")
    x_anchor = (framing or {}).get("x")
    y_anchor = (framing or {}).get("y")
    if x_anchor is not None:
        x_fraction = float(x_anchor)
    else:
        x_fraction = {"left": 0.0, "right": 1.0}.get(anchor, 0.5)
    if y_anchor is not None:
        y_fraction = float(y_anchor)
    else:
        y_fraction = {"top": 0.0, "bottom": 1.0}.get(anchor, 0.5)
    return _rect_dict(
        rect["x"] + (rect["w"] - visible_w) * x_fraction,
        rect["y"] + (rect["h"] - visible_h) * y_fraction,
        visible_w,
        visible_h,
    )


@dataclass(frozen=True)
class SelectionCrop:
    """A box/point selection resolved into the renderer's crop rect.

    ``selection_pixels`` is what the agent asked to keep visible;
    ``crop_pixels`` is the final source rect the renderer will use.
    Both are kept so later edits ("more padding", "stronger zoom")
    can re-derive the crop from intent.
    """

    kind: str  # "box" | "point"
    selection_pixels: dict[str, float]
    padded_pixels: dict[str, float]
    crop_pixels: dict[str, float]
    crop_normalized: dict[str, float]
    zoom: float
    requested_zoom: float | None
    base_view_pixels: dict[str, float]
    placement: tuple[float, float]
    effective_placement: tuple[float, float]
    selection_in_region: dict[str, float]
    upscale_factor: float
    adjustments: tuple[str, ...]


def resolve_selection_crop(
    *,
    source: tuple[float, float],
    region: dict,
    box_pixels: dict | None = None,
    point_pixels: tuple[float, float] | None = None,
    zoom: float | None = None,
    padding: float = 0.0,
    place: tuple[float, float] | None = None,
) -> SelectionCrop:
    """Resolve "keep this visible" into the exact crop the renderer uses.

    Box mode sizes the crop to contain the padding-expanded box; point
    mode sizes it from ``zoom`` (content ``zoom``x larger than the
    slot's normal full-source view). Either way the crop matches the
    slot region's aspect, stays inside the source, and shifts rather
    than cutting the selection near an edge. ``place`` positions the
    selection's center within the crop (normalized, default centered).
    """
    width, height = float(source[0]), float(source[1])
    if (box_pixels is None) == (point_pixels is None):
        raise CameraResolutionError(
            "selection needs exactly one of a box or a point"
        )
    if point_pixels is not None and zoom is None:
        raise CameraResolutionError("a point selection needs a zoom amount")
    if zoom is not None and zoom <= 1e-6:
        raise CameraResolutionError("zoom must be greater than 0")
    if padding < 0:
        raise CameraResolutionError("padding must not be negative")

    aspect = float(region["width"]) / float(region["height"])
    base_w = min(width, height * aspect)
    base_h = base_w / aspect
    base_view = _rect_dict(
        (width - base_w) / 2, (height - base_h) / 2, base_w, base_h
    )
    adjustments: list[str] = []

    if box_pixels is not None:
        kind = "box"
        selection = {key: float(box_pixels[key]) for key in ("x", "y", "w", "h")}
        if selection["w"] <= 0 or selection["h"] <= 0:
            raise CameraResolutionError("box width and height must be positive")
        if (
            selection["x"] < -_EPSILON
            or selection["y"] < -_EPSILON
            or selection["x"] + selection["w"] > width + _EPSILON
            or selection["y"] + selection["h"] > height + _EPSILON
        ):
            raise CameraResolutionError(
                f"selection box {selection} is outside the "
                f"{int(width)}x{int(height)} source frame"
            )
        pad_x = padding * selection["w"]
        pad_y = padding * selection["h"]
        padded = {
            "x": selection["x"] - pad_x,
            "y": selection["y"] - pad_y,
            "w": selection["w"] + 2 * pad_x,
            "h": selection["h"] + 2 * pad_y,
        }
        clamped = {
            "x": max(0.0, padded["x"]),
            "y": max(0.0, padded["y"]),
        }
        clamped["w"] = min(width, padded["x"] + padded["w"]) - clamped["x"]
        clamped["h"] = min(height, padded["y"] + padded["h"]) - clamped["y"]
        if (
            not math.isclose(clamped["w"], padded["w"], abs_tol=_EPSILON)
            or not math.isclose(clamped["h"], padded["h"], abs_tol=_EPSILON)
        ):
            adjustments.append("padding_clamped_to_source")
        padded = clamped

        min_crop_w = max(padded["w"], padded["h"] * aspect)
        if min_crop_w > base_w + _EPSILON:
            crop_w = base_w
            adjustments.append("selection_exceeds_slot_view")
        else:
            crop_w = min_crop_w
            if not math.isclose(
                padded["w"] / padded["h"], aspect, rel_tol=1e-6
            ):
                adjustments.append("aspect_matched_to_slot")
        crop_h = crop_w / aspect
        if zoom is not None:
            raise CameraResolutionError(
                "zoom applies to point selections; box size plus padding "
                "controls a box selection"
            )
    else:
        kind = "point"
        px, py = float(point_pixels[0]), float(point_pixels[1])
        if (
            px < -_EPSILON
            or py < -_EPSILON
            or px > width + _EPSILON
            or py > height + _EPSILON
        ):
            raise CameraResolutionError(
                f"point ({px}, {py}) is outside the "
                f"{int(width)}x{int(height)} source frame"
            )
        selection = {"x": px, "y": py, "w": 0.0, "h": 0.0}
        padded = dict(selection)
        crop_w = base_w / float(zoom)
        crop_h = crop_w / aspect

    place_x, place_y = place if place is not None else (0.5, 0.5)
    if not (0.0 <= place_x <= 1.0 and 0.0 <= place_y <= 1.0):
        raise CameraResolutionError(
            "place coordinates are normalized: both values must be 0..1"
        )
    selection_cx = selection["x"] + selection["w"] / 2
    selection_cy = selection["y"] + selection["h"] / 2

    def _clamped_place(value: float, extent: float, crop_extent: float) -> float:
        margin = extent / (2 * crop_extent) if crop_extent > 0 else 0.0
        if margin > 0.5:
            return 0.5
        return min(max(value, margin), 1.0 - margin)

    place_x_eff = _clamped_place(place_x, selection["w"], crop_w)
    place_y_eff = _clamped_place(place_y, selection["h"], crop_h)
    if not math.isclose(place_x_eff, place_x, abs_tol=1e-9) or not math.isclose(
        place_y_eff, place_y, abs_tol=1e-9
    ):
        adjustments.append("placement_limited_to_keep_selection_visible")

    crop_x = selection_cx - place_x_eff * crop_w
    crop_y = selection_cy - place_y_eff * crop_h
    shifted_x = min(max(crop_x, 0.0), width - crop_w)
    shifted_y = min(max(crop_y, 0.0), height - crop_h)
    if not math.isclose(shifted_x, crop_x, abs_tol=_EPSILON) or not math.isclose(
        shifted_y, crop_y, abs_tol=_EPSILON
    ):
        adjustments.append("shifted_inside_source_bounds")
    crop_x, crop_y = shifted_x, shifted_y

    if crop_w > 0 and crop_h > 0:
        effective = (
            _round((selection_cx - crop_x) / crop_w),
            _round((selection_cy - crop_y) / crop_h),
        )
    else:
        effective = (place_x_eff, place_y_eff)

    scale = float(region["width"]) / crop_w if crop_w else 0.0
    selection_in_region = _rect_dict(
        (selection["x"] - crop_x) * scale,
        (selection["y"] - crop_y) * scale,
        selection["w"] * scale,
        selection["h"] * scale,
    )
    return SelectionCrop(
        kind=kind,
        selection_pixels=_rect_dict(**selection),
        padded_pixels=_rect_dict(**padded),
        crop_pixels=_rect_dict(crop_x, crop_y, crop_w, crop_h),
        crop_normalized=_rect_dict(
            crop_x / width, crop_y / height, crop_w / width, crop_h / height
        ),
        zoom=_round(base_w / crop_w) if crop_w else 0.0,
        requested_zoom=float(zoom) if zoom is not None else None,
        base_view_pixels=base_view,
        placement=(_round(place_x), _round(place_y)),
        effective_placement=effective,
        selection_in_region=selection_in_region,
        upscale_factor=_round(float(region["width"]) / crop_w) if crop_w else 0.0,
        adjustments=tuple(dict.fromkeys(adjustments)),
    )


def _find_scene_for_motion(composition: list[dict], motion_scene: dict) -> dict | None:
    scene_id = motion_scene.get("scene_id")
    if scene_id is not None:
        match = next(
            (scene for scene in composition if scene.get("id") == scene_id),
            None,
        )
        if match is not None:
            return match
    scene_name = motion_scene.get("scene")
    return next(
        (scene for scene in composition if scene.get("name") == scene_name),
        None,
    )


def _find_slot_for_motion(scene: dict, motion_slot: dict) -> dict | None:
    slot_id = motion_slot.get("slot_id")
    if slot_id is not None:
        match = next(
            (slot for slot in scene.get("slots", []) if slot.get("id") == slot_id),
            None,
        )
        if match is not None:
            return match
    slot_name = motion_slot.get("slot")
    return next(
        (slot for slot in scene.get("slots", []) if slot.get("slot") == slot_name),
        None,
    )


def _rect_changed(before: dict[str, float], after: dict[str, float]) -> bool:
    return any(
        not math.isclose(before[key], after[key], abs_tol=_EPSILON)
        for key in ("x", "y", "w", "h")
    )


def _authored_selection_crop(
    authored: dict,
    *,
    source: tuple[int, int],
    region: dict,
) -> SelectionCrop | None:
    place = authored.get("place")
    placement = (
        (float(place[0]), float(place[1]))
        if isinstance(place, (list, tuple)) and len(place) == 2
        else None
    )
    if authored.get("selection") == "box" and isinstance(
        authored.get("box_pixels"), dict
    ):
        return resolve_selection_crop(
            source=source,
            region=region,
            box_pixels=authored["box_pixels"],
            padding=float(authored.get("padding", 0.0)),
            place=placement,
        )
    point = authored.get("point_pixels")
    if (
        authored.get("selection") == "point"
        and isinstance(point, (list, tuple))
        and len(point) == 2
        and authored.get("zoom") is not None
    ):
        return resolve_selection_crop(
            source=source,
            region=region,
            point_pixels=(float(point[0]), float(point[1])),
            zoom=float(authored["zoom"]),
            place=placement,
        )
    return None


def reconcile_camera_geometry(
    old_composition: list[dict],
    new_composition: list[dict],
    motion: dict,
    source_dimensions: dict[str, tuple[int, int]],
    old_canvas: dict,
    new_canvas: dict,
) -> tuple[dict, list[dict]]:
    """Re-resolve camera targets after a scene slot's region changes.

    Incremental zoom records retain box/point intent, so their stored target
    crop is recomputed. Plain camera rectangles remain authored source
    coordinates and are only re-resolved. Any new crop that would have to be
    reduced to source bounds fails the whole layout mutation with its move ID.
    """
    updated = copy.deepcopy(motion)
    changes: list[dict] = []
    for motion_scene in updated.get("scenes", []):
        old_scene = _find_scene_for_motion(old_composition, motion_scene)
        new_scene = _find_scene_for_motion(new_composition, motion_scene)
        if old_scene is None or new_scene is None:
            continue
        old_regions = layout_regions(old_scene["layout"], old_canvas, result_time_s=0.0)
        new_regions = layout_regions(new_scene["layout"], new_canvas, result_time_s=0.0)
        for motion_slot in motion_scene.get("slots", []):
            old_slot = _find_slot_for_motion(old_scene, motion_slot)
            new_slot = _find_slot_for_motion(new_scene, motion_slot)
            if old_slot is None or new_slot is None:
                continue
            old_slot_name = old_slot["slot"]
            new_slot_name = new_slot["slot"]
            old_dimensions = source_dimensions.get(old_slot.get("source"))
            new_dimensions = source_dimensions.get(new_slot.get("source"))
            if old_dimensions is None or new_dimensions is None:
                continue
            old_region = old_regions[old_slot_name]
            new_region = new_regions[new_slot_name]
            old_aspect = float(old_region["width"]) / float(old_region["height"])
            new_aspect = float(new_region["width"]) / float(new_region["height"])
            if old_dimensions == new_dimensions and math.isclose(
                old_aspect, new_aspect, rel_tol=1e-9
            ):
                continue

            for camera in motion_slot.get("camera", []):
                camera_id = str(camera.get("id", "(unnamed)"))
                authored_crop = None
                if camera.get("kind") == "zoom" and isinstance(
                    camera.get("authored"), dict
                ):
                    try:
                        authored_crop = _authored_selection_crop(
                            camera["authored"],
                            source=new_dimensions,
                            region=new_region,
                        )
                    except CameraResolutionError as exc:
                        raise CameraGeometryMutationError(
                            f"Camera move {camera_id!r} cannot be re-resolved: "
                            f"{exc}",
                            camera_id=camera_id,
                        ) from exc
                    if (
                        authored_crop is not None
                        and "selection_exceeds_slot_view"
                        in authored_crop.adjustments
                    ):
                        raise CameraGeometryMutationError(
                            f"Camera move {camera_id!r} cannot keep its authored "
                            "selection visible in the new slot aspect.",
                            camera_id=camera_id,
                        )

                for state_name in ("from", "to", "return_to"):
                    old_raw = camera.get(state_name)
                    if old_raw is None:
                        continue
                    try:
                        before = _resolve_state(
                            old_raw, old_dimensions, old_region
                        )
                    except CameraResolutionError as exc:
                        raise CameraGeometryMutationError(
                            f"Camera move {camera_id!r} has an invalid "
                            f"{state_name} crop: {exc}",
                            camera_id=camera_id,
                        ) from exc
                    resolution = "explicit_crop_reresolved"
                    if state_name == "to" and authored_crop is not None:
                        camera[state_name] = {
                            "target": {
                                "space": "source",
                                "units": "pixels",
                                "rect": dict(authored_crop.crop_pixels),
                            }
                        }
                        resolution = "authored_selection_recomputed"
                    try:
                        after = _resolve_state(
                            camera[state_name], new_dimensions, new_region
                        )
                    except CameraResolutionError as exc:
                        raise CameraGeometryMutationError(
                            f"Camera move {camera_id!r} cannot re-resolve its "
                            f"{state_name} crop: {exc}",
                            camera_id=camera_id,
                        ) from exc
                    if not _rect_changed(
                        before.resolved_crop_pixels,
                        after.resolved_crop_pixels,
                    ):
                        continue
                    if "reduced_to_source_bounds" in after.adjustments:
                        raise CameraGeometryMutationError(
                            f"Camera move {camera_id!r} would need its "
                            f"{state_name} crop reduced to fit the new slot "
                            "aspect.",
                            camera_id=camera_id,
                        )
                    changes.append(
                        {
                            "id": camera_id,
                            "scene": new_scene.get("name"),
                            "slot": new_slot_name,
                            "state": state_name,
                            "resolution": resolution,
                            "region_pixels": {
                                "from": dict(old_region),
                                "to": dict(new_region),
                            },
                            "crop_pixels": {
                                "from": dict(before.resolved_crop_pixels),
                                "to": dict(after.resolved_crop_pixels),
                            },
                        }
                    )
    return updated, changes


def _resolve_state(raw: dict, dimensions: tuple[int, int], region: dict) -> CameraState:
    width, height = map(float, dimensions)
    target = raw.get("target")
    if target == "full":
        pixels = _rect_dict(0, 0, width, height)
        return CameraState(
            target="full",
            authored_units="full",
            rect_pixels=pixels,
            rect_normalized=_rect_dict(0, 0, 1, 1),
            resolved_crop_pixels=pixels,
            zoom=1.0,
        )
    if not isinstance(target, dict) or target.get("space", "source") != "source":
        raise CameraResolutionError("camera state target must be 'full' or source-relative")
    units = target.get("units")
    pixels, normalized = _normalize_rect(target.get("rect", {}), width, height, units)
    crop, adjustments = _aspect_crop(pixels, (width, height), region)
    zoom = min(width / pixels["w"], height / pixels["h"])
    return CameraState(
        target="rect",
        authored_units=units,
        rect_pixels=_rect_dict(**pixels),
        rect_normalized=_rect_dict(**normalized),
        resolved_crop_pixels=crop,
        zoom=_round(zoom),
        adjustments=adjustments,
    )


def _interpolate_state(
    start: CameraState,
    end: CameraState,
    eased: float,
    move_id: str,
    progress: float,
) -> CameraState:
    def mixed(field: str) -> float:
        a = start.resolved_crop_pixels[field]
        b = end.resolved_crop_pixels[field]
        return _round(a + (b - a) * eased)

    crop = {field: mixed(field) for field in ("x", "y", "w", "h")}
    return replace(
        end,
        target="interpolated",
        resolved_crop_pixels=crop,
        active_move_id=move_id,
        progress=_round(progress),
        eased_progress=_round(eased),
    )


def _motion_scene(scene: dict, motion: dict) -> dict:
    scene_id = scene.get("id")
    for record in motion.get("scenes", []):
        if scene_id is not None and record.get("scene_id") == scene_id:
            return record
        if record.get("scene") == scene.get("name"):
            return record
    return {"slots": []}


def _source_anchor(
    scene: ResolvedScene, slot_name: str, result_local_s: float
) -> SourceAnchor:
    local = float(result_local_s)
    if math.isclose(local, scene.duration_s, abs_tol=_EPSILON):
        return SourceAnchor("source", scene.segments[-1].source_local_to_s)
    for segment in scene.segments:
        if segment.result_local_from_s <= local < segment.result_local_to_s:
            offset = local - segment.result_local_from_s
            if segment.held:
                return SourceAnchor("hold", segment.source_local_from_s, _round(offset))
            slot = segment.slot(slot_name)
            return SourceAnchor(
                "source",
                _round(segment.source_local_from_s + offset * float(slot.speed or 1.0)),
            )
    raise CameraResolutionError(
        f"result-local time {result_local_s}s is outside scene {scene.name!r}"
    )


def _result_for_anchor(
    scene: ResolvedScene, slot_name: str, anchor: SourceAnchor
) -> float:
    if anchor.kind == "hold":
        for segment in scene.segments:
            if segment.held and math.isclose(
                segment.source_local_from_s,
                anchor.source_local_s,
                abs_tol=_EPSILON,
            ):
                offset = float(anchor.hold_offset_s or 0.0)
                if offset > segment.duration_s + _EPSILON:
                    raise CameraResolutionError(
                        f"camera offset {offset}s no longer fits hold at "
                        f"source-local {_timecode(anchor.source_local_s)}; "
                        f"new hold duration is {segment.duration_s}s. Smallest "
                        "fix: lengthen the hold or author the camera range again."
                    )
                return _round(segment.result_local_from_s + offset)
        raise CameraResolutionError(
            f"camera was attached to hold at source-local "
            f"{_timecode(anchor.source_local_s)}, but that hold no longer exists. "
            "Smallest fix: restore the hold or author the camera range again."
        )
    if math.isclose(anchor.source_local_s, scene.segments[-1].source_local_to_s, abs_tol=_EPSILON):
        return scene.duration_s
    for segment in scene.segments:
        if segment.held:
            continue
        if (
            segment.source_local_from_s - _EPSILON
            <= anchor.source_local_s
            <= segment.source_local_to_s + _EPSILON
        ):
            slot = segment.slot(slot_name)
            return _round(
                segment.result_local_from_s
                + (anchor.source_local_s - segment.source_local_from_s)
                / float(slot.speed or 1.0)
            )
    raise CameraResolutionError(
        f"camera source anchor {_timecode(anchor.source_local_s)} no longer "
        "maps into the paced scene"
    )


def rebase_camera_ranges(
    old_motion: dict,
    new_motion: dict,
    old_pacing: ResolvedTimeline,
    new_pacing: ResolvedTimeline,
) -> tuple[dict, list[dict]]:
    """Keep unchanged camera records attached to source content after pacing edits."""
    rebased = copy.deepcopy(new_motion)
    changes: list[dict] = []
    old_scene_by_name = {scene.name: scene for scene in old_pacing.scenes}
    new_scene_by_name = {scene.name: scene for scene in new_pacing.scenes}
    old_records = {
        (scene.get("scene"), slot.get("slot"), camera.get("id")): camera
        for scene in old_motion.get("scenes", [])
        for slot in scene.get("slots", [])
        for camera in slot.get("camera", [])
    }
    for scene_record in rebased.get("scenes", []):
        scene_name = scene_record.get("scene")
        if scene_name not in old_scene_by_name or scene_name not in new_scene_by_name:
            continue
        for slot_record in scene_record.get("slots", []):
            slot_name = slot_record.get("slot")
            for camera in slot_record.get("camera", []):
                old = old_records.get((scene_name, slot_name, camera.get("id")))
                if old is None:
                    continue
                timing_keys = {"range", "at"}
                old_intent = {k: v for k, v in old.items() if k not in timing_keys}
                new_intent = {k: v for k, v in camera.items() if k not in timing_keys}
                old_timing = {k: old[k] for k in timing_keys if k in old}
                new_timing = {k: camera[k] for k in timing_keys if k in camera}
                if old_intent != new_intent or old_timing != new_timing:
                    continue
                old_scene = old_scene_by_name[scene_name]
                new_scene = new_scene_by_name[scene_name]
                if "range" in old:
                    start = _seconds(old["range"]["from"], "camera.range.from")
                    end = _seconds(old["range"]["to"], "camera.range.to")
                    start_anchor = _source_anchor(old_scene, slot_name, start)
                    end_anchor = _source_anchor(old_scene, slot_name, end)
                    new_start = _result_for_anchor(new_scene, slot_name, start_anchor)
                    new_end = _result_for_anchor(new_scene, slot_name, end_anchor)
                    camera["range"] = {
                        "from": _timecode(new_start),
                        "to": _timecode(new_end),
                        "space": "result-local",
                    }
                else:
                    start = _seconds(old["at"], "camera.at")
                    anchor = _source_anchor(old_scene, slot_name, start)
                    new_start = _result_for_anchor(new_scene, slot_name, anchor)
                    camera["at"] = _timecode(new_start)
                    new_end = new_start
                if camera.get("range") == old.get("range") and camera.get("at") == old.get("at"):
                    continue
                changes.append(
                    {
                        "id": camera["id"],
                        "scene": scene_name,
                        "slot": slot_name,
                        "from": old_timing,
                        "to": {k: camera[k] for k in timing_keys if k in camera},
                    }
                )
    return rebased, changes


ZOOM_RETURN_SUFFIX = ":return"
ZOOM_DEFAULT_TIMING = {"move_in": 0.5, "hold": 2.0, "move_out": 0.5}


def expand_zoom_record(raw: dict) -> list[dict]:
    """Expand a kind:"zoom" camera record into its two renderable moves.

    A zoom record stores the moment the camera reaches its target
    (``at``, scene result-local) plus viewer-facing ``timing`` durations
    (move_in / hold / move_out). The renderer sees a move in to ``to``
    ending at ``at``, the implicit hold, and a move out to ``return_to``
    (the pre-zoom state, default full frame).
    """
    where = f"camera zoom record {raw.get('id')!r}"
    at_s = _seconds(raw.get("at"), f"{where}.at")
    timing = raw.get("timing") or {}
    durations: dict[str, float] = {}
    for key, default in ZOOM_DEFAULT_TIMING.items():
        value = timing.get(key, default)
        seconds = _seconds(value, f"{where}.timing.{key}")
        if seconds <= 0:
            raise CameraResolutionError(
                f"{where}.timing.{key} must be greater than 0"
            )
        durations[key] = seconds
    ease = raw.get("ease", "in-out")
    if ease == "cut":
        raise CameraResolutionError(
            f"{where}: zoom records animate in and out; use a plain camera "
            "record with ease 'cut' for instant switches"
        )
    start_s = _round(at_s - durations["move_in"])
    out_from_s = _round(at_s + durations["hold"])
    out_to_s = _round(out_from_s + durations["move_out"])
    move_in = {
        "id": raw["id"],
        "range": {"from": start_s, "to": at_s, "space": "result-local"},
        "to": raw["to"],
        "ease": ease,
    }
    if raw.get("from") is not None:
        move_in["from"] = raw["from"]
    move_out = {
        "id": f"{raw['id']}{ZOOM_RETURN_SUFFIX}",
        "range": {"from": out_from_s, "to": out_to_s, "space": "result-local"},
        "to": raw.get("return_to", {"target": "full"}),
        "ease": ease,
    }
    return [move_in, move_out]


def resolve_camera(
    composition: list[dict],
    motion: dict,
    pacing: ResolvedTimeline,
    source_dimensions: dict[str, tuple[int, int]],
    canvas: dict,
) -> ResolvedCameraPlan:
    """Resolve every authored camera record against the paced scene timeline."""
    moves: list[ResolvedCameraMove] = []
    defaults: dict[tuple[str, str], CameraState] = {}
    for resolved_scene in pacing.scenes:
        scene = composition[resolved_scene.index]
        regions = layout_regions(scene["layout"], canvas, result_time_s=0.0)
        motion_slots = _motion_scene(scene, motion).get("slots", [])
        records_by_slot_id = {
            record["slot_id"]: record
            for record in motion_slots
            if record.get("slot_id")
        }
        records_by_slot_name = {
            record.get("slot"): record for record in motion_slots
        }
        for slot in scene["slots"]:
            slot_name = slot["slot"]
            dimensions = source_dimensions.get(slot["source"])
            if dimensions is None or not all(dimensions):
                raise CameraResolutionError(
                    f"camera resolution needs dimensions for source {slot['source']!r}"
                )
            full = _resolve_state({"target": "full"}, dimensions, regions[slot_name])
            defaults[(resolved_scene.name, slot_name)] = full
            previous = full
            previous_end = -1.0
            slot_record = records_by_slot_id.get(slot.get("id"))
            slot_record = slot_record or records_by_slot_name.get(slot_name, {})
            camera_records: list[dict] = []
            for raw in slot_record.get("camera", []):
                if isinstance(raw, dict) and raw.get("kind") == "zoom":
                    camera_records.extend(expand_zoom_record(raw))
                else:
                    camera_records.append(raw)
            for index, raw in enumerate(camera_records):
                where = f"scene {resolved_scene.name!r} slot {slot_name!r} camera[{index}]"
                ease = raw.get("ease", "in-out")
                default_duration = False
                if "range" in raw:
                    start = _seconds(raw["range"].get("from"), f"{where}.range.from")
                    end = _seconds(raw["range"].get("to"), f"{where}.range.to")
                else:
                    start = _seconds(raw.get("at"), f"{where}.at")
                    end = start if ease == "cut" else _round(start + 0.5)
                    default_duration = ease != "cut"
                if start < previous_end - _EPSILON:
                    raise CameraResolutionError(
                        f"camera moves overlap in {where}; start at {previous_end}s or later"
                    )
                if start < 0 or end > resolved_scene.duration_s + _EPSILON or end < start:
                    raise CameraResolutionError(
                        f"{where} range {start}-{end}s is outside paced scene duration "
                        f"0-{resolved_scene.duration_s}s"
                    )
                to_state = _resolve_state(raw["to"], dimensions, regions[slot_name])
                if raw.get("from") is not None:
                    from_state = _resolve_state(raw["from"], dimensions, regions[slot_name])
                    from_source = "authored"
                else:
                    from_state = previous
                    from_source = "previous_state"
                start_anchor = _source_anchor(resolved_scene, slot_name, start)
                end_anchor = _source_anchor(resolved_scene, slot_name, end)
                moves.append(
                    ResolvedCameraMove(
                        id=raw["id"],
                        scene_index=resolved_scene.index,
                        scene_name=resolved_scene.name,
                        slot_name=slot_name,
                        result_local_from_s=start,
                        result_local_to_s=end,
                        result_from_s=_round(resolved_scene.start_s + start),
                        result_to_s=_round(resolved_scene.start_s + end),
                        ease=ease,
                        from_state=from_state,
                        to_state=to_state,
                        from_state_source=from_source,
                        default_duration_applied=default_duration,
                        source_equivalent_range=(
                            start_anchor.source_local_s,
                            end_anchor.source_local_s,
                        ),
                    )
                )
                previous = to_state
                previous_end = end
    return ResolvedCameraPlan(pacing, tuple(moves), defaults)
