"""MovieStar CLI — deterministic video editing for AI agents."""

from __future__ import annotations

import base64
import concurrent.futures
import copy
import difflib
import functools
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

import click

from moviestar import __version__
from moviestar.activity import (
    ACTIVITY_ANALYSIS_WIDTH,
    ACTIVITY_PIXEL_DELTA,
    analyze_visual_activity,
)
from moviestar.masks import ensure_mask
from moviestar.ffmpeg import (
    FFMPEG_FEATURE_REQUIREMENTS,
    check_ffmpeg_ass_filter_available,
    check_ffmpeg_drawtext_filter_available,
    ffmpeg_capability_report,
    AUDIO_MIX_CHANNEL_LAYOUT,
    AUDIO_MIX_LIMITER_ATTACK_MS,
    AUDIO_MIX_LIMITER_CEILING_DBFS,
    AUDIO_MIX_LIMITER_LIMIT,
    AUDIO_MIX_LIMITER_RELEASE_MS,
    AUDIO_MIX_SAMPLE_FORMAT,
    AUDIO_MIX_SAMPLE_RATE,
    FFmpegCapabilityError,
    FFmpegNotFoundError,
    build_extract_clip_command,
    build_extract_frame_command,
    build_render_segments_command,
    build_extract_frames_command,
    build_ffprobe_command,
    build_render_layout_frame_command,
    build_render_layout_frames_command,
    build_render_layout_video_command,
    build_highlight_ass,
    build_mix_audio_command,
    compose_storyboard_tiles,
    extract_clip,
    extract_storyboard_frame,
    render_storyboard_padding_tile,
    render_segments,
    extract_frame,
    extract_frames,
    measure_region_stability,
    render_selection_frame,
    render_target_grid_frame,
    render_layout_frame,
    render_layout_frames,
    render_layout_video,
    mix_audio,
    probe_last_video_packet_timestamp,
    run_ffprobe,
    normalize_loudness,
    run_layer_loudness_probe,
    run_loudness_probe,
)
from moviestar.project import (
    DEFAULT_THUMB_WIDTH,
    FRAMES_DIR,
    FRAME_EXTRACTION_SKIP_REASON_FLAG,
    INSPECT_FRAMES_DIR,
    MOVIESTAR_DIR,
    TRANSCRIPTION_SKIP_REASON_FLAG,
    TRANSCRIPTION_SKIP_REASON_NO_AUDIO,
    TRANSCRIPTS_DIR,
    WorkspaceConflictError,
    create_workspace,
    derive_source_id,
    filter_frames_by_range,
    get_project_dir,
    get_explicit_workspace_root,
    is_loaded,
    list_frames,
    load_project,
    load_transcript,
    normalize_skip_reason,
    reset_workspace,
    save_project,
    save_transcript,
    select_load_frame_interval,
    set_explicit_workspace_root,
    slice_transcript,
    subsample_frames,
)
from moviestar.find import (
    STRONG_WARNING_THRESHOLD,
    WARNING_THRESHOLD,
    search_transcript,
    snap_boundaries_to_words,
)
from moviestar.spec import (
    CANVAS_PRESETS,
    M26_DEFAULT_INSET_GEOMETRY,
    caption_recipe_for_track,
    upsert_caption_recipe,
    FRAMING_ANCHORS,
    FRAMING_MODES,
    LAYOUT_PRESETS,
    PIP_INSET_CORNERS,
    PIP_INSET_DEFAULTS,
    PIP_INSET_MAX_FRACTION,
    PIP_INSET_MIN_FRACTION,
    SLOT_GEOMETRY_ANCHORS,
    SLOT_GEOMETRY_MAX_SIZE,
    SLOT_GEOMETRY_MIN_SIZE,
    SLOT_GEOMETRY_SIZE_FRACTIONS,
    SLOT_GEOMETRY_SPEEDS,
    SceneSlotDurationMismatchError,
    SceneValidationError,
    SpecValidationError,
    append_cut,
    append_overlay,
    append_trim,
    default_slot_margin,
    empty_spec,
    ensure_scene_motion_identities,
    geometry_even_pixels,
    get_source,
    layout_regions,
    layout_slots,
    load_spec,
    next_overlay_id,
    normalize_composition_storage,
    pop_revision,
    resolve_source,
    resolve_source_history,
    resolve_slot_motion,
    reconcile_motion_lifecycle,
    resolve_inset_geometry,
    result_to_source_time,
    save_spec,
    set_audio_mix,
    set_composition,
    set_layout_composition,
    set_motion,
    set_overlays,
    set_scene_composition,
    source_ids,
    source_range_in_result,
    sync_project_sources,
    validate_spec,
)
from moviestar.captions import (
    apply_break_edits,
    apply_join_edits,
    apply_suppressions,
    rebalance_orphan_cues,
    CUE_POLICY,
    CaptionParseError,
    apply_caption_rules,
    group_words_into_cues,
    map_words_through_windows,
    next_caption_rule_id,
    normalize_cues,
    parse_caption_rule_expression,
    parse_srt,
    parse_vtt,
    validate_caption_rules,
    validate_caption_overlay_content,
)
from moviestar.fonts import (
    DEFAULT_FONT_FAMILY,
    FontResolutionError,
    add_font,
    default_font_scope,
    list_managed_fonts,
    project_font_dir,
    resolve_font,
    user_font_dir,
)
from moviestar.overlays import overlay_plan_summary, plan_overlays
from moviestar.audio_surface import (
    AUDIO_DUCKING_PRESETS,
    AUDIO_KINDS,
    AudioSurfaceValidationError,
    audio_mix_summary,
    default_audio_mix,
    editable_audio_document,
    normalize_audio_document,
    persisted_audio_mix,
    resolve_audio_mix,
)
from moviestar.motion import PacingResolutionError, resolve_pacing
from moviestar.resolved import (
    ResolvedProjectError,
    resolve_overlays,
    resolve_project,
)
from moviestar.transitions import (
    EdgeTransitionResolution,
    InternalTransitionResolution,
    TransitionResolutionError,
    TransitionSlotHandle,
    resolve_edge_transition,
    resolve_internal_transition,
)
from moviestar.camera import (
    CameraGeometryMutationError,
    CameraResolutionError,
    ZOOM_DEFAULT_TIMING,
    ZOOM_RETURN_SUFFIX,
    expand_zoom_record,
    fill_visible_rect,
    rebase_camera_ranges,
    reconcile_camera_geometry,
    resolve_camera,
    resolve_selection_crop,
)
from moviestar.timecodes import format_timecode, parse_timecode
from moviestar.transcribe import (
    AVAILABLE_MODELS,
    DEFAULT_MODEL,
    TranscriptionError,
    _normalize_vocabulary,
    model_download_requirement,
    pull_model,
    resolve_cached_model,
    transcribe_file,
)
from moviestar.feedback import feedback
from moviestar.subscribe import subscribe as subscribe_command
from moviestar.account import account as account_command


CLIPS_DIR = "moviestar-clips"
LAYOUT_PREVIEWS_DIR = "layout-previews"
WORKSPACE_OPTION_HELP = (
    "Project root to use instead of walking up from cwd. PATH must contain "
    "a loaded moviestar/ workspace."
)


def project_workspace_option(func):
    @click.option(
        "--workspace",
        "workspace_root",
        type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
        help=WORKSPACE_OPTION_HELP,
    )
    @functools.wraps(func)
    def wrapper(*args, workspace_root: Path | None = None, **kwargs):
        set_explicit_workspace_root(workspace_root)
        try:
            return func(*args, **kwargs)
        finally:
            set_explicit_workspace_root(None)

    return wrapper


def _aspect_ratio(width: int, height: int) -> str:
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _parse_canvas_arg(value: str | None) -> dict | None:
    if value is None:
        return None
    value = value.strip().lower()
    if value in CANVAS_PRESETS:
        width, height, aspect = CANVAS_PRESETS[value]
        return {
            "preset": value,
            "width": width,
            "height": height,
            "aspect_ratio": aspect,
        }
    if "x" not in value:
        raise ValueError(
            "Invalid canvas. Use a preset (short, square, landscape) "
            "or custom dimensions like 1080x1920."
        )
    width_text, height_text = value.split("x", 1)
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise ValueError(
            "Invalid canvas dimensions. Use WIDTHxHEIGHT, e.g. 1080x1920."
        ) from exc
    if width <= 0 or height <= 0:
        raise ValueError("Canvas width and height must be positive integers.")
    if width < 16 or height < 16:
        raise ValueError("Canvas width and height must be at least 16 pixels.")
    if width % 2 or height % 2:
        raise ValueError(
            "Canvas width and height must be even numbers for reliable encoding."
        )
    return {
        "preset": None,
        "width": width,
        "height": height,
        "aspect_ratio": _aspect_ratio(width, height),
    }


def _parse_framing_arg(value: str) -> dict:
    raw = value.strip().lower()
    if not raw:
        raise ValueError(
            "Invalid framing. Use fit, fill, fill:<anchor>, fill:x=<0..1>, "
            "or fill:anchor=<x>,<y>."
        )
    if ":" in raw:
        mode, detail = raw.split(":", 1)
    else:
        mode, detail = raw, "center"
    if mode not in FRAMING_MODES:
        raise ValueError(
            f"Invalid framing mode {mode!r}. Use one of: "
            f"{', '.join(sorted(FRAMING_MODES))}."
        )

    if "=" in detail:
        if mode != "fill":
            raise ValueError("Numeric crop anchors require fill framing.")
        return _parse_numeric_fill_framing(detail)

    anchor = detail
    if anchor not in FRAMING_ANCHORS:
        raise ValueError(
            f"Invalid framing anchor {anchor!r}. Use one of: "
            f"{', '.join(sorted(FRAMING_ANCHORS))}, or use numeric "
            "fill:x=<0..1> / fill:anchor=<x>,<y>."
        )
    return {"mode": mode, "anchor": anchor}


def _parse_normalized_framing_value(value: str, field: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid framing {field} value {value!r}. Use a normalized "
            "number from 0 to 1."
        ) from exc
    if not math.isfinite(parsed) or parsed < 0 or parsed > 1:
        raise ValueError(
            f"Invalid framing {field} value {value!r}. Use a normalized "
            "number from 0 to 1."
        )
    return round(parsed, 6)


def _parse_numeric_fill_framing(detail: str) -> dict:
    if detail.startswith("anchor="):
        value = detail[len("anchor=") :]
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 2 or not all(parts):
            raise ValueError(
                "Invalid framing anchor value. Use fill:anchor=<x>,<y> "
                "with normalized numbers from 0 to 1."
            )
        return {
            "mode": "fill",
            "x": _parse_normalized_framing_value(parts[0], "x"),
            "y": _parse_normalized_framing_value(parts[1], "y"),
        }

    values: dict[str, float] = {}
    for part in detail.split(","):
        if "=" not in part:
            raise ValueError(
                "Invalid numeric framing. Use fill:x=<0..1>, "
                "fill:y=<0..1>, or fill:x=<0..1>,y=<0..1>."
            )
        key, value = [text.strip() for text in part.split("=", 1)]
        if key not in {"x", "y"}:
            raise ValueError(
                f"Invalid framing field {key!r}. Use x, y, or "
                "anchor=<x>,<y>."
            )
        if key in values:
            raise ValueError(f"Duplicate framing field {key!r}.")
        values[key] = _parse_normalized_framing_value(value, key)
    if not values:
        raise ValueError("Invalid numeric framing. Use fill:x=<0..1>.")
    return {
        "mode": "fill",
        "x": values.get("x", 0.5),
        "y": values.get("y", 0.5),
    }


def _layout_orientation(canvas: dict) -> str:
    return "vertical" if int(canvas["height"]) >= int(canvas["width"]) else "horizontal"


def _layout_for_preset(
    preset: str, canvas: dict, *, inset_framing: dict | None = None
) -> dict:
    layout: dict = {"preset": preset, "orientation": _layout_orientation(canvas)}
    if preset == "picture-in-picture":
        # New scenes materialize the M26 circular default, so the
        # catalog and would-add envelopes show the same truth; a
        # fit-framed inset materializes rect instead (black wedges).
        fit = (inset_framing or {}).get("mode") == "fit"
        layout["geometry"] = (
            {"inset": {"shape": {"kind": "rect"}}}
            if fit
            else copy.deepcopy({"inset": M26_DEFAULT_INSET_GEOMETRY})
        )
    return layout


def _layout_envelope(
    layout: dict, canvas: dict, *, result_time_s: float
) -> dict:
    preset = layout["preset"]
    regions = layout_regions(layout, canvas, result_time_s=result_time_s)
    for slot_name in regions:
        if resolve_slot_motion(layout, slot_name, canvas) is not None:
            regions[slot_name] = {
                "x": 0,
                "y": 0,
                "width": int(canvas["width"]),
                "height": int(canvas["height"]),
            }
    return {
        "preset": preset,
        "orientation": _layout_orientation(canvas),
        **({"inset": layout["inset"]} if layout.get("inset") else {}),
        **({"geometry": layout["geometry"]} if layout.get("geometry") else {}),
        "slots": layout_slots(preset, canvas),
        "regions": regions,
        "description": LAYOUT_PRESETS[preset]["description"],
    }


def _layout_preview_path(layout: str, canvas: dict) -> str:
    preset = canvas["preset"] or f"{canvas['width']}x{canvas['height']}"
    name = f"{preset}_{layout}.svg"
    return os.path.realpath(os.path.join(MOVIESTAR_DIR, LAYOUT_PREVIEWS_DIR, name))


def _canvas_label(canvas: dict) -> str:
    return canvas["preset"] or f"{canvas['width']}x{canvas['height']}"


def _write_layout_preview(
    layout: dict, canvas: dict, output: str | None = None
) -> str:
    preset = layout["preset"]
    path = os.path.realpath(output) if output else _layout_preview_path(preset, canvas)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    width = int(canvas["width"])
    height = int(canvas["height"])
    scale = min(1.0, 720 / max(width, height))
    view_w = max(1, int(round(width * scale)))
    view_h = max(1, int(round(height * scale)))
    colors = ["#d9ecff", "#ffe3c2", "#d8f7d3", "#f0dcff"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{view_w}" '
        f'height="{view_h}" viewBox="0 0 {width} {height}">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#111827"/>',
    ]
    geometry = layout.get("geometry") or {}
    for i, (slot, region) in enumerate(
        layout_regions(layout, canvas, result_time_s=0.0).items()
    ):
        fill = colors[i % len(colors)]
        x = region["x"]
        y = region["y"]
        w = region["width"]
        h = region["height"]
        shape = (geometry.get(slot) or {}).get("shape") or {}
        kind = shape.get("kind")
        if kind in ("circle", "rounded"):
            # Truthful previews: draw the real shape, with any border
            # as a filled disc under the content — matching how the
            # renderer composites it.
            radius_px = int(round((shape.get("radius") or 0) * min(w, h)))
            border = shape.get("border")
            border_width = int(border["width"]) if border else 0
            if border:
                if kind == "circle":
                    parts.append(
                        f'<circle cx="{x + w / 2}" cy="{y + h / 2}" '
                        f'r="{w / 2 + border_width}" '
                        f'fill="{border["color"]}"/>'
                    )
                else:
                    rx = radius_px + border_width
                    parts.append(
                        f'<rect x="{x - border_width}" '
                        f'y="{y - border_width}" '
                        f'width="{w + 2 * border_width}" '
                        f'height="{h + 2 * border_width}" rx="{rx}" '
                        f'fill="{border["color"]}"/>'
                    )
            if kind == "circle":
                parts.append(
                    f'<circle cx="{x + w / 2}" cy="{y + h / 2}" '
                    f'r="{w / 2}" fill="{fill}"/>'
                )
            else:
                parts.append(
                    f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                    f'rx="{radius_px}" fill="{fill}"/>'
                )
        elif kind == "rect" and shape.get("border"):
            border = shape["border"]
            border_width = int(border["width"])
            parts.append(
                f'<rect x="{x - border_width}" '
                f'y="{y - border_width}" '
                f'width="{w + 2 * border_width}" '
                f'height="{h + 2 * border_width}" '
                f'fill="{border["color"]}"/>'
            )
            parts.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                f'fill="{fill}"/>'
            )
        else:
            parts.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                f'fill="{fill}" stroke="#111827" stroke-width="8"/>'
            )
        parts.append(
            f'<text x="{x + w / 2}" y="{y + h / 2}" '
            'text-anchor="middle" dominant-baseline="middle" '
            'font-family="Arial, sans-serif" font-size="64" '
            f'fill="#111827">{slot}</text>'
        )
    parts.append("</svg>\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path


def _parse_slot_arg(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError("Invalid slot. Use SLOT=SOURCE, e.g. top=holden.")
    slot, source = value.split("=", 1)
    slot = slot.strip()
    source = source.strip()
    if not slot or not source:
        raise ValueError("Invalid slot. Use SLOT=SOURCE, e.g. top=holden.")
    return slot, source


def _parse_start_offset_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid --start-offset value {value!r}. Use seconds, e.g. 1.25."
        ) from exc
    if not math.isfinite(seconds):
        raise ValueError(
            f"Invalid --start-offset value {value!r}. Use a finite number of seconds."
        )
    return seconds


LOUDNESS_TRUE_PEAK_LIMIT = -1.5
LOUDNESS_LRA_TARGET = 11.0
# |measured - target| within this many LU counts as target met. The
# linear-gain post-pass lands well inside this on real content; the
# margin absorbs AAC round-trip drift, not normalization error.
LOUDNESS_TOLERANCE_LU = 1.0


def _parse_loudness_target(value: float | None, command: str) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        _error_exit(command, "--loudness-target must be a finite LUFS value.")
    if value < -70.0 or value > -5.0:
        _error_exit(
            command,
            "--loudness-target must be between -70 and -5 LUFS "
            "(FFmpeg loudnorm's supported range).",
        )
    return float(value)


def _add_loudness_result(
    result: dict, target: float | None, *, dry_run: bool = False
) -> None:
    if target is None:
        return
    envelope: dict = {
        "target_lufs": target,
        "true_peak_limit_dbtp": LOUDNESS_TRUE_PEAK_LIMIT,
        "tolerance_lu": LOUDNESS_TOLERANCE_LU,
        "method": "measured_linear_gain",
        "stage": "post_render_audio_remux",
    }
    if dry_run:
        envelope["measured"] = None
        envelope["note"] = (
            "Achieved loudness is measured from the rendered artifact — "
            "a dry run cannot predict it. The real call reports "
            "pre_normalization, applied_gain_db, measured, delta_lu, "
            "and target_met here."
        )
    result["loudness_normalization"] = envelope


def _finalize_loudness_normalization(
    command: str,
    result: dict,
    target: float | None,
    render_path: str,
) -> None:
    """Run the two-pass loudness post-pass and report what was delivered.

    Must run after any audio-mix remux so the measurement sees the
    finished mix. Issue #352: the previous single-pass loudnorm missed
    requested targets by multiple LU and overshot the true-peak ceiling
    while the envelope claimed success; the envelope now carries the
    measured result and a structured warning when the target is missed.
    """
    if target is None:
        return
    click.echo("Normalizing loudness (two-pass)...", err=True)
    try:
        info = normalize_loudness(
            render_path, target, true_peak_limit=LOUDNESS_TRUE_PEAK_LIMIT
        )
    except (FFmpegNotFoundError, FileNotFoundError, RuntimeError) as exc:
        if os.path.exists(render_path):
            os.remove(render_path)
        _error_exit(command, f"Loudness normalization failed: {exc}")
    plan = info["plan"]
    measured = info["measured"]
    envelope = result["loudness_normalization"]
    envelope["pre_normalization"] = {
        key: info["pre"][key]
        for key in ("integrated_lufs", "true_peak_dbtp", "lra_lu")
    }
    envelope["applied_gain_db"] = plan["gain_db"]
    envelope["gain_limited_by_true_peak"] = plan["limited_by_true_peak"]
    envelope["measured"] = {
        key: measured[key]
        for key in ("integrated_lufs", "true_peak_dbtp", "lra_lu")
    }
    # --loudness-report measured the mix before this post-pass changed the
    # artifact; refresh its final-mix block so both surfaces agree.
    report = result.get("audio_loudness")
    if isinstance(report, dict) and isinstance(report.get("final_mix"), dict):
        report["final_mix"]["loudness"] = _loudness_values_envelope(measured)
    if info["apply_command"] is not None:
        envelope["apply_command"] = info["apply_command"]
    envelope["hint"] = (
        "Verify independently with 'moviestar probe "
        + _path_for_hint(render_path)
        + " --loudness'."
    )
    measured_i = measured["integrated_lufs"]
    delta = None if measured_i is None else round(measured_i - target, 2)
    envelope["delta_lu"] = delta
    met = delta is not None and abs(delta) <= LOUDNESS_TOLERANCE_LU
    envelope["target_met"] = met
    result["file_size_bytes"] = os.path.getsize(render_path)
    if met:
        return
    if plan["reason"] == "input_unmeasurable":
        constraint = "input_unmeasurable"
    elif plan["limited_by_true_peak"]:
        constraint = "true_peak_limit"
    else:
        constraint = "unknown"
    envelope["limiting_constraint"] = constraint
    max_achievable = None
    if constraint == "true_peak_limit":
        pre = envelope["pre_normalization"]
        max_achievable = round(
            pre["integrated_lufs"]
            + (LOUDNESS_TRUE_PEAK_LIMIT - pre["true_peak_dbtp"]),
            2,
        )
        envelope["max_achievable_lufs"] = max_achievable
    if measured_i is None:
        message = (
            f"Rendered audio was too quiet to measure; the "
            f"--loudness-target {target} LUFS request was not applied."
        )
    else:
        message = (
            f"Rendered integrated loudness is {measured_i} LUFS, "
            f"{abs(delta)} LU {'below' if delta < 0 else 'above'} the "
            f"--loudness-target {target} LUFS request "
            f"(tolerance ±{LOUDNESS_TOLERANCE_LU} LU)."
        )
        if max_achievable is not None:
            message += (
                f" The true-peak limit caps this content at about "
                f"{max_achievable} LUFS; re-run with a target at or "
                f"below that to meet it."
            )
    _add_warnings(
        result,
        [
            _warning(
                "loudness_target_missed",
                message,
                target_lufs=target,
                measured_lufs=measured_i,
                delta_lu=delta,
                limiting_constraint=constraint,
                max_achievable_lufs=max_achievable,
            )
        ],
    )


def _loudness_fast_note() -> str:
    return (
        "--fast (stream-copy) is unavailable with --loudness-target "
        "(audio normalization needs an audio filter). Re-encoded "
        "automatically; drop --fast to silence this note."
    )


def _parse_audio_join_fade(value: float | None, command: str) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        _error_exit(command, "--audio-join-fade must be a finite seconds value.")
    if value < 0:
        _error_exit(command, "--audio-join-fade cannot be negative.")
    if value > 5.0:
        _error_exit(command, "--audio-join-fade must be 5 seconds or shorter.")
    return float(value) if value > 0 else None


def _add_audio_join_fade_result(result: dict, fade: float | None) -> None:
    if fade is not None:
        result["audio_join_fade"] = format_timecode(fade)


def _validate_audio_join_fade_durations(
    fade: float | None,
    durations: list[float],
    command: str,
    noun: str = "segment",
) -> None:
    if fade is None:
        return
    if not durations or len(durations) < 2:
        _error_exit(
            command,
            f"--audio-join-fade requires at least two sequential {noun}s.",
        )
    shortest = min(durations)
    max_fade = shortest / 2
    if fade > max_fade:
        _error_exit(
            command,
            f"--audio-join-fade {fade:g}s is too long for the shortest "
            f"{noun} ({shortest:g}s). Use {max_fade:g}s or less.",
        )


def _planned_load_source_ids(
    videos: tuple[str, ...],
    names: tuple[str, ...],
    existing_sources: list[dict] | None = None,
) -> list[str]:
    padded_names: list[str | None] = list(names) + [None] * (
        len(videos) - len(names)
    )
    taken = {source["id"] for source in (existing_sources or [])}
    source_ids: list[str] = []
    for video, override_name in zip(videos, padded_names):
        source_id = override_name or derive_source_id(video, taken)
        taken.add(source_id)
        source_ids.append(source_id)
    return source_ids


def _resolve_start_offsets(
    raw_offsets: tuple[str, ...],
    source_ids: list[str],
) -> list[float | None]:
    offsets: list[float | None] = [None] * len(source_ids)
    source_index = {source_id: i for i, source_id in enumerate(source_ids)}
    positional_index = 0

    for raw in raw_offsets:
        value = raw.strip()
        if not value:
            _error_exit("load", "--start-offset requires a value.")
        if "=" in value:
            source_id, seconds_text = value.split("=", 1)
            source_id = source_id.strip()
            seconds_text = seconds_text.strip()
            if not source_id or not seconds_text:
                _error_exit(
                    "load",
                    "Invalid --start-offset. Use SOURCE=SECONDS, e.g. "
                    "--start-offset screenshare=1.25.",
                )
            if source_id not in source_index:
                available = ", ".join(source_ids)
                _error_exit(
                    "load",
                    f"Unknown --start-offset source {source_id!r}. "
                    f"Available loaded source ids: {available}.",
                )
            index = source_index[source_id]
            if offsets[index] is not None:
                _error_exit(
                    "load",
                    f"Duplicate --start-offset for source {source_id!r}.",
                )
            try:
                offsets[index] = _parse_start_offset_seconds(seconds_text)
            except ValueError as exc:
                _error_exit("load", str(exc))
        else:
            if positional_index >= len(source_ids):
                _error_exit(
                    "load",
                    f"Got more positional --start-offset values than "
                    f"sources ({len(source_ids)}).",
                )
            if offsets[positional_index] is not None:
                source_id = source_ids[positional_index]
                _error_exit(
                    "load",
                    f"Duplicate --start-offset for source {source_id!r}.",
                )
            try:
                offsets[positional_index] = _parse_start_offset_seconds(value)
            except ValueError as exc:
                _error_exit("load", str(exc))
            positional_index += 1

    return offsets


HELP_EPILOG = """\

\b
WORKFLOW

\b
  1. moviestar probe video.mp4          # inspect before loading
  2. moviestar load video.mp4           # index the video (one-time, slow)
  3. moviestar skim                     # browse thumbnails + transcript
  4. moviestar trim --from X --to Y     # edit (repeatable, undoable)
  5. moviestar export --out output.mp4  # render final output

\b
  Multi-source visual workflow:
     load multiple sources -> layouts --canvas short -> scenes set
     -> scenes geometry (optional) -> inspect/screenshot/watch -> export

\b
BROWSING COMMANDS

\b
  doctor       Is this environment ready? FFmpeg/ffprobe presence and
               build capabilities (drawtext, libass, encoders), with
               the features each missing capability blocks. Read-only.
  probe        Inspect a file's metadata (duration, resolution, codec).
               Describes the whole file — not a specific moment in time.
  status       Where am I? Source, edit state, transcript at a glance.
               Read-only orientation primitive after load.
  history      How did this source get here? Step-by-step lineage of
               trims and cuts. Read-only; per-source.
  skim         Fast sample — ~10 thumbnails + transcript across a range.
               Zoom with --from/--to. Use for overview and narrowing down.
  storyboard   One-image contact sheet — labeled frames tiled across the
               whole video. The visual overview for silent footage.
  inspect      Dense sample — many thumbnails + transcript across a range.
               Use for careful scene-by-scene review.
  watch        Extract a video segment for multimodal model analysis.
  screenshot   Single frame at a timecode. Applies edits when in a project.

\b
EDITING COMMANDS

\b
  trim         Narrow to a timecode range. Adds to edit spec.
  cut          Remove a range from the middle of the result. Adds to edit spec.
  concat       Build the composition — stitch segments from one or
               more edited sources into one timeline. Replaces any
               existing composition.
  layouts      Preview visual layout presets for a canvas before
               choosing one for a composition.
  scenes       Author ordered scene-layout compositions. Use
               scenes set for layout changes over result-time.
  scenes geometry  Resize, place, shape, or move a composed slot on
                   the output canvas; distinct from source camera motion.
  scenes motion  Zoom/pan a scene slot and retime it: camera moves,
                 speed changes, target durations, holds/freezes.
  scenes transition  Set, inspect, or remove visual transitions at scene
                     boundaries; audio stays on its existing timeline.
  captions     Generate/import caption overlays for burn-in.
  overlays     Author timed text overlays; captions compile to this
               same overlay primitive.
  audio        Mix routed source audio with voiceover and music on
               the finished-video timeline, including signal-driven ducking.
  fonts        Add/list bundled, project, and user fonts.
  undo         Revert the last edit operation (or last concat).
  spec         Show or replace the current edit spec (JSON).

\b
RE-INDEX COMMANDS

\b
  retranscribe Re-run transcription against the loaded source.
               Keeps frames + spec. Use to switch models (e.g.
               base → large-v3) without 'load --force' wiping
               edit history.

\b
OUTPUT COMMANDS

\b
  export       Render edit spec to moviestar-clips/ by default.
  clip         Extract one independent clip to its own MP4
               (source-time, edit spec untouched).
  batch        Extract many independent clips from a JSON recipe
               (source-time, edit spec untouched, atomic validation).
  clean        Remove generated output artifacts after a dry-run preview.

\b
ACCOUNT COMMANDS

\b
  account connect EMAIL  Ask MovieStar to email the human an account claim.
  account status         Show approval or connection state for this install.
  account disconnect     Revoke this install; local editing keeps working.
  account open           Open the human's account page in a browser.

\b
TIMECODE FORMATS

\b
  All --from, --to, and --at flags accept:
    0:01:23.500   HH:MM:SS.mmm
    83.5          seconds (float)
    1:23          M:SS shorthand

\b
IMAGES

\b
  skim, inspect, screenshot, and storyboard write JPEG files and
  return their paths (out / thumbnails[].path). View them by reading
  the file with your harness's file reader. Pass --inline to embed
  base64 image bytes in the JSON envelope — only useful for
  frameworks that convert envelope bytes into image blocks; in CLI
  harnesses the embedded base64 is plain text the model can't see.

\b
WORKSPACE LOOKUP

\b
  Every project lives in a moviestar/ directory next to your video.
  Read commands (skim, inspect, storyboard, trim, spec, undo, export,
  status, watch, screenshot) walk up from cwd to find the nearest moviestar/
  workspace — the same way git finds .git. So once a project is
  loaded, you can run those commands from any subdirectory of it.
  load and 'load --force' are cwd-only (create / replace in cwd) so
  they never reach up and clobber a parent project's workspace.
"""


def _stub(command: str, milestone: str) -> None:
    """Emit the standard "not yet implemented" JSON payload and exit 1."""
    payload = {
        "error": f"Not implemented. Coming in {milestone}.",
        "command": command,
    }
    click.echo(json.dumps(payload, indent=2))
    sys.exit(1)


def _usage_error_exit(exc: click.UsageError) -> None:
    """Emit the canonical error envelope for a Click parse error (#361).

    Click's standalone handler prints usage errors as prose on stderr
    and leaves stdout EMPTY — the one path where an agent's
    ``json.load(stdout)`` had nothing to parse. Envelope goes to
    stdout; the human-readable usage text stays on stderr; exit code
    stays 2 so parse errors remain distinguishable from application
    errors (exit 1).
    """
    ctx = exc.ctx
    # command_path starts with the prog name (``moviestar`` in real
    # runs, the group name under CliRunner) — strip it so the envelope
    # ``command`` matches the harness-independent CLI verb.
    subpath = ctx.command_path.split()[1:] if ctx is not None else []
    command = " ".join(subpath) or "moviestar"
    help_target = " ".join(["moviestar", *subpath])
    if isinstance(exc, click.exceptions.NoArgsIsHelpError):
        # Its format_message() is the entire help text — too much for
        # an envelope field. The full help still renders via show().
        message = "Missing command."
    else:
        message = exc.format_message()
    hint = f"Run '{help_target} --help' for usage."
    if command == "feedback" and "unexpected extra argument" in message.lower():
        hint = (
            "Short feedback requires --quick: "
            "moviestar feedback --quick \"message\". "
            "Run 'moviestar feedback --help' for usage."
        )
    click.echo(
        json.dumps(
            {
                "command": command,
                "error": message,
                "hint": hint,
            },
            indent=2,
        )
    )
    exc.show()
    sys.exit(2)


class _UsageErrorEnvelopeGroup(click.Group):
    """Root group that keeps the JSON-on-stdout contract for usage errors.

    Runs Click non-standalone so parse errors reach us instead of
    Click's prose-only handler, then replicates standalone exit
    behavior for every other outcome.
    """

    def main(self, *args, **kwargs):  # type: ignore[override]
        kwargs["standalone_mode"] = False
        try:
            rv = super().main(*args, **kwargs)
        except click.UsageError as exc:
            _usage_error_exit(exc)
        except click.ClickException as exc:
            exc.show()
            sys.exit(exc.exit_code)
        except click.exceptions.Abort:
            click.echo("Aborted!", err=True)
            sys.exit(1)
        sys.exit(rv if isinstance(rv, int) else 0)


@click.group(
    cls=_UsageErrorEnvelopeGroup,
    epilog=HELP_EPILOG,
    context_settings={"help_option_names": ["--help"]},
)
@click.version_option(__version__, "--version", message="%(version)s")
def cli() -> None:
    """MovieStar — video editing CLI for AI agents.

    Requires: FFmpeg on PATH (`brew install ffmpeg` / `apt install ffmpeg`).

    Load a video, browse its contents, make edits, and export the result.
    All output is structured JSON on stdout; progress goes to stderr.
    Capturing both streams together (2>&1)? Pass --quiet (or set
    MOVIESTAR_QUIET=1) on load / retranscribe / export so the merged
    stream stays pure JSON.
    """


cli.add_command(subscribe_command)
cli.add_command(feedback)
cli.add_command(account_command)


# ---------- Browsing ----------


def _parse_fps(rate_str: str | None) -> float | None:
    """Parse an r_frame_rate string like '30000/1001' into a float."""
    if not rate_str or rate_str == "0/0":
        return None
    try:
        return float(Fraction(rate_str))
    except (ValueError, ZeroDivisionError):
        return None


def _error_exit(command: str, message: str) -> None:
    click.echo(json.dumps({"error": message, "command": command}, indent=2))
    sys.exit(1)


def _quiet_option(f):
    """``--quiet`` for the progress-emitting commands (issue #362).

    Only load, retranscribe, and export narrate on stderr today; the
    other commands are already silent and deliberately don't take the
    flag. The envvar lets harnesses opt out once instead of editing
    every command line.
    """
    return click.option(
        "--quiet",
        is_flag=True,
        envvar="MOVIESTAR_QUIET",
        help="Suppress progress output on stderr (or set MOVIESTAR_QUIET=1). "
        "The JSON envelope on stdout is unchanged; errors still report "
        "normally.",
    )(f)


def _render_progress_cb(quiet: bool):
    """Per-second render progress callback: no-op when quiet, None to
    take ffmpeg.py's stderr-writer default otherwise."""
    return (lambda _line: None) if quiet else None


def _warning(
    code: str,
    message: str,
    *,
    severity: str = "warning",
    **details,
) -> dict:
    item = {"code": code, "severity": severity, "message": message}
    item.update({key: value for key, value in details.items() if value is not None})
    return item


def _add_warnings(result: dict, warnings: list[dict]) -> None:
    if not warnings:
        return
    existing = result.setdefault("warnings", [])
    existing.extend(warnings)
    result["warning_count"] = len(existing)


def _revision_count(spec: dict, *fields: str) -> int:
    """Count journal entries that touched any requested field."""
    wanted = set(fields)
    return sum(
        bool(wanted & set(revision.get("changed", {})))
        for revision in spec.get("revisions", [])
    )


def _revision_appended_source_op(
    spec: dict, revision: dict
) -> tuple[str, dict] | None:
    """Return the one source op appended by a trim/cut revision."""
    if revision.get("command") not in {"trim", "cut"}:
        return None
    prior_sources = revision.get("changed", {}).get("sources")
    if not isinstance(prior_sources, list):
        return None
    prior_by_id = {
        source.get("id"): source
        for source in prior_sources
        if isinstance(source, dict)
    }
    appended: list[tuple[str, dict]] = []
    for source in spec.get("sources", []):
        sid = source.get("id")
        prior = prior_by_id.get(sid)
        if prior is None:
            continue
        current_ops = source.get("operations", [])
        prior_ops = prior.get("operations", [])
        if len(current_ops) == len(prior_ops) + 1 and current_ops[:-1] == prior_ops:
            appended.append((sid, current_ops[-1]))
        elif current_ops != prior_ops:
            return None
    return appended[0] if len(appended) == 1 else None


def _result_clock_overlays(spec: dict) -> list[dict]:
    """Stored overlays whose timing does not follow a scene identity."""
    recipe_tracks = _caption_recipe_tracks(spec)
    return [
        overlay
        for overlay in spec.get("overlays") or []
        if not (
            (
                overlay.get("kind") == "caption"
                and overlay.get("track") in recipe_tracks
            )
            or (
                overlay.get("kind") == "manual"
                and (overlay.get("timing") or {}).get("space") == "scene"
            )
        )
    ]


def _stale_result_clock_overlay_warning(
    spec: dict,
    old_timeline,
    new_timeline,
    *,
    code: str,
    change_label: str,
) -> dict | None:
    overlays = _result_clock_overlays(spec)
    if not overlays:
        return None

    def scene_clock(timeline) -> list[tuple]:
        if hasattr(timeline, "scenes"):
            return [
                (
                    scene.scene.get("id") or scene.name,
                    round(scene.start_s, 3),
                    round(scene.end_s, 3),
                )
                for scene in timeline.scenes
            ]
        clock = []
        seen_scene_indexes = set()
        for span in timeline.spans:
            if span.scene_index in seen_scene_indexes:
                continue
            seen_scene_indexes.add(span.scene_index)
            clock.append(
                (
                    span.scene_id or span.scene_name,
                    round(span.scene_start_s, 3),
                    round(span.scene_end_s, 3),
                )
            )
        return clock

    if scene_clock(old_timeline) == scene_clock(new_timeline):
        return None
    caption_count = sum(
        1 for overlay in overlays if overlay.get("kind") == "caption"
    )
    manual_count = len(overlays) - caption_count
    return _warning(
        code,
        f"This {change_label} moves the result clock, but {len(overlays)} "
        "stored overlay(s) keep their previous result-time ranges and "
        "will render at the old positions.",
        old_composition_duration=format_timecode(old_timeline.duration_s),
        new_composition_duration=format_timecode(new_timeline.duration_s),
        affected_overlays_count=len(overlays),
        affected_overlay_ids=sorted(overlay["id"] for overlay in overlays),
        caption_cues_count=caption_count,
        manual_overlays_count=manual_count,
        affected_tracks=sorted({overlay["track"] for overlay in overlays}),
        hint=_stale_overlay_hint(caption_count, manual_count),
    )


def _attach_overlay_timeline_mutation(
    result: dict,
    *,
    project: dict,
    old_spec: dict,
    new_spec: dict,
    code: str,
    change_label: str,
) -> None:
    """Report overlay effects of a write that may move the result clock."""
    overlays = new_spec.get("overlays") or []
    if not overlays:
        return
    try:
        old_timeline = resolve_project(project, old_spec)
    except (ResolvedProjectError, SpecValidationError):
        old_timeline = None
    try:
        new_timeline = resolve_project(project, new_spec)
    except (ResolvedProjectError, SpecValidationError):
        return
    _attach_overlay_lifecycle(result, resolve_overlays(new_timeline, overlays))
    if old_timeline is None:
        return
    warning = _stale_result_clock_overlay_warning(
        new_spec,
        old_timeline,
        new_timeline,
        code=code,
        change_label=change_label,
    )
    if warning is not None:
        _add_warnings(result, [warning])


def _stale_overlay_hint(caption_count: int, manual_count: int) -> str:
    """Return remediation scoped to the state that is actually stale.

    Manual-overlay changes do not require derived caption tracks to be
    regenerated.
    """
    parts = []
    if caption_count:
        parts.append(
            "Re-run 'moviestar captions generate' to rebuild frozen "
            "caption tracks on the new clock"
        )
    if manual_count:
        parts.append(
            "retime manual overlays via 'moviestar overlays dump' -> "
            "edit -> 'moviestar overlays set'"
        )
    hint = ", and ".join(parts) + "."
    return hint[0].upper() + hint[1:]


def _error_exit_with_hint(
    command: str, message: str, hint: str, **details
) -> None:
    """Like ``_error_exit`` but with a structured top-level ``hint``
    field carrying the actionable next step.

    Issue #78: extends the #27 convention (errors mirror success
    envelopes) across every command. ``error`` describes what went
    wrong; ``hint`` says what to do about it.
    """
    click.echo(
        json.dumps(
            {"error": message, "command": command, "hint": hint, **details},
            indent=2,
        )
    )
    sys.exit(1)


@cli.group()
def models() -> None:
    """Pre-download local Whisper models used by transcription."""


@models.command("pull")
@click.argument("model", type=click.Choice(AVAILABLE_MODELS))
def models_pull(model: str) -> None:
    """Download MODEL now so later transcription does not need network."""
    requirement = model_download_requirement(model)
    if requirement is None:
        cache_path = resolve_cached_model(model)
        result = {
            "status": "already_cached",
            "model": model,
            "cache_path": str(cache_path),
            "hint": "Model is already ready for offline transcription.",
        }
    else:
        click.echo(
            f"Downloading faster-whisper {model!r} model "
            f"(~{requirement['estimated_size_mb']} MB)...",
            err=True,
        )
        try:
            cache_path = pull_model(model)
        except TranscriptionError as exc:
            _error_exit_with_hint(
                "models pull",
                str(exc),
                f"Check network access to Hugging Face, then retry "
                f"'moviestar models pull {model}'.",
            )
        result = {
            "status": "downloaded",
            "model": model,
            "cache_path": str(cache_path),
            "estimated_size_mb": requirement["estimated_size_mb"],
            "hint": "Model is ready. Future load and retranscribe calls can run without downloading it.",
        }
    click.echo(json.dumps(result, indent=2))


def _ffmpeg_capability_error_exit(
    command: str,
    exc: FFmpegCapabilityError,
    prefix: str | None = None,
) -> None:
    message = str(exc) if prefix is None else f"{prefix}: {exc}"
    _error_exit_with_hint(command, message, exc.hint, code=exc.code)


def _author_time_ass_preflight(command: str) -> None:
    """Issue #358: refuse to persist a spoken-word highlight plan the
    current FFmpeg build cannot export. The render preflights from
    #242 stay in place as defense in depth."""
    try:
        check_ffmpeg_ass_filter_available()
    except FFmpegCapabilityError as exc:
        _ffmpeg_capability_error_exit(command, exc)
    except (FFmpegNotFoundError, RuntimeError):
        # No or broken ffmpeg is surfaced by doctor and the commands
        # that actually need the binary; don't block plan authoring on
        # a failed probe.
        return


def _missing_drawtext_author_warning() -> dict | None:
    """Issue #358: author-time notice that burned-in text cannot render
    in this environment. A warning, not an error — the plan persists
    and flat exports still work; render paths remain the hard guard."""
    try:
        check_ffmpeg_drawtext_filter_available()
    except FFmpegCapabilityError as exc:
        return _warning(exc.code, str(exc), hint=exc.hint)
    except (FFmpegNotFoundError, RuntimeError):
        return None
    return None


@cli.command()
def doctor() -> None:
    """Check FFmpeg environment readiness before building a project.

    Read-only and project-independent: reports ffmpeg/ffprobe
    availability and version plus the build capabilities MovieStar
    relies on (drawtext, libass, output encoders), naming the exact
    features each missing capability blocks and how to fix it.

    \b
    Exit policy:
      0  status "ready"     everything MovieStar needs is present
      0  status "degraded"  ffmpeg runs but optional capabilities are
                            missing; blocked features are listed as
                            warnings with install hints
      1  status "blocked"   ffmpeg or ffprobe is not on PATH; nothing
                            can be probed or rendered

    Commands that need a missing capability still preflight it
    themselves — doctor is an early diagnostic, not the only guard.
    """
    report = ffmpeg_capability_report()
    listings = {"filter": report["filters"], "encoder": report["encoders"]}
    capabilities = []
    warnings = []
    for requirement in FFMPEG_FEATURE_REQUIREMENTS:
        listing = listings[requirement["kind"]]
        available = (
            None if listing is None else requirement["capability"] in listing
        )
        entry = {
            "capability": requirement["capability"],
            "kind": requirement["kind"],
            "available": available,
            "blocks": list(requirement["blocks"]),
        }
        if available is False:
            entry["code"] = requirement["code"]
            entry["hint"] = requirement["hint"]
            warnings.append(
                _warning(
                    requirement["code"],
                    requirement["message"]
                    + " Blocked: "
                    + "; ".join(requirement["blocks"])
                    + ".",
                    hint=requirement["hint"],
                )
            )
        capabilities.append(entry)
    for probe_error in report["probe_errors"]:
        warnings.append(
            _warning(
                "ffmpeg_capability_probe_failed",
                f"Could not list FFmpeg {probe_error['probe']}: "
                f"{probe_error['error']} Capabilities of that kind are "
                "reported as unknown (null).",
            )
        )

    body = {
        "command": "doctor",
        "writes_spec": False,
        "ffmpeg": report["ffmpeg"],
        "ffprobe": report["ffprobe"],
        "capabilities": capabilities,
    }
    missing_tools = [
        tool for tool in ("ffmpeg", "ffprobe") if not report[tool]["available"]
    ]
    if missing_tools:
        result = {
            "status": "blocked",
            "error": (
                f"{' and '.join(missing_tools)} not found on PATH. "
                "Nothing can be probed or rendered."
            ),
            **body,
            "code": f"{missing_tools[0]}_not_found",
            "hint": (
                "Install FFmpeg (it includes ffprobe). macOS/Homebrew: "
                "brew install ffmpeg. Then re-run 'moviestar doctor'."
            ),
        }
        click.echo(json.dumps(result, indent=2))
        sys.exit(1)

    status = "degraded" if warnings else "ready"
    result = {
        "status": status,
        **body,
        "hint": (
            "Environment ready: probing, rendering, text/caption "
            "burn-in, and spoken-word highlighting are all available."
            if status == "ready"
            else "FFmpeg runs, but the capabilities marked unavailable "
            "block the listed features. Each warning carries an "
            "install hint; everything else works today."
        ),
    }
    _add_warnings(result, warnings)
    click.echo(json.dumps(result, indent=2))


def _scene_file_error(command: str) -> None:
    _error_exit_with_hint(
        command,
        "Scene-file authoring is parked for this milestone.",
        "Use repeated flags instead: 'moviestar scenes set --scene "
        "intro=single --slot intro:main=holden --from 0 --to 1'.",
    )


def _no_project_error_exit(command: str) -> None:
    """Issue #78: every command that walks up to find a workspace emits
    the same structured envelope when none is found. Centralized here
    so the ten copies of this prose are now one helper call — the
    convention can't drift between commands.
    """
    explicit_root = get_explicit_workspace_root()
    if explicit_root is not None:
        _error_exit_with_hint(
            command,
            f"No project at --workspace {str(explicit_root)!r}.",
            "--workspace must point at the project root that contains "
            "moviestar/project.json. Drop --workspace to walk up from cwd.",
        )

    _error_exit_with_hint(
        command,
        "No project in this directory or any parent.",
        "Run 'moviestar load <video>' first. moviestar walks up from cwd "
        "to find the nearest moviestar/ workspace, like git finds .git.",
    )


# M13a: helpers for the v0.2 spec format. Single-source projects use
# the first source as the implicit "active" source. Multi-source-aware
# CLI flags (--source <id>) get added in a follow-on commit.


def _load_or_init_spec(project: dict) -> dict:
    """Return the project's edit spec, creating an empty v0.2 spec
    seeded from the project's sources if no spec.json exists yet.
    Replaces v0.1's auto-init-on-read behavior of ``load_spec``.
    """
    seed_sources = [
        {"id": s["id"], "path": s["path"]} for s in project["sources"]
    ]
    spec = load_spec()
    if spec is None:
        spec = empty_spec(seed_sources)
    else:
        spec = sync_project_sources(spec, seed_sources)
    # Stage 3: stored flat concats normalize to scene compositions in
    # memory (canvas inference needs project.json probe dimensions).
    # Read commands never write; the shape persists on the next write.
    source_dims = {
        s["id"]: (
            (int(s["width"]), int(s["height"]))
            if s.get("width") and s.get("height")
            else None
        )
        for s in project["sources"]
    }
    spec = normalize_composition_storage(spec, source_dims)
    # #414 / proposal: scene and slot identities are available as soon
    # as a composition exists — not only after motion is authored — so
    # status, dumps, and anything attaching state can address them.
    # Deterministic and in-memory, like normalization.
    spec, _identity_assignments = ensure_scene_motion_identities(spec)
    # Stage 4: derived caption tracks re-derive whenever the compiled
    # timeline, recipe, or rules changed. In memory only — read
    # commands never write; the fresh cache persists on the next write.
    return _refresh_caption_tracks(project, spec)


def _refresh_caption_tracks(project: dict, spec: dict) -> dict:
    recipes = spec.get("captions") or []
    if not recipes:
        return spec
    import contextlib
    import io

    for recipe in recipes:
        if recipe.get("frozen"):
            continue
        fingerprint = _caption_cache_fingerprint(project, spec, recipe)
        if fingerprint == recipe.get("cache_fingerprint"):
            continue
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                records, _meta = _derive_caption_records(
                    project, spec, recipe, "captions refresh"
                )
        except SystemExit:
            # Derivation failed (e.g. transcript unavailable). Keep the
            # stale cache; the next successful derivation heals it, and
            # the owning command reports the real error when run
            # directly.
            continue
        track = recipe["track"]
        spec["overlays"] = [
            overlay
            for overlay in spec.get("overlays", [])
            if overlay["track"] != track
        ] + records
        recipe["cache_fingerprint"] = fingerprint
    return spec


def _caption_recipe_tracks(spec: dict) -> set[str]:
    """Tracks with an ACTIVE (non-frozen) recipe. Frozen recipes exist
    only to make staleness queryable (#412): they do not re-derive, do
    not block hand-editing, and are counted by the stale warning."""
    return {
        recipe["track"]
        for recipe in spec.get("captions", [])
        if not recipe.get("frozen")
    }


def _caption_staleness(project: dict, spec: dict, recipe: dict) -> bool:
    """A frozen track is stale when the timeline or rules moved since
    it froze — the same fingerprint the derivation cache uses."""
    return _caption_cache_fingerprint(project, spec, recipe) != recipe.get(
        "cache_fingerprint"
    )


def _current_composition_duration(project: dict, spec: dict) -> float | None:
    try:
        return resolve_project(project, spec).duration_s
    except (ResolvedProjectError, SpecValidationError):
        return None


def _freeze_caption_recipe(
    project: dict, spec: dict, recipe: dict
) -> dict:
    """Build the frozen form of a recipe: keeps edits dormant, records
    the clock it froze at, and re-fingerprints over the frozen content
    so staleness compares cleanly from here forward."""
    frozen = {
        **{k: v for k, v in recipe.items() if k != "cache_fingerprint"},
        "frozen": True,
    }
    duration = _current_composition_duration(project, spec)
    if duration is not None:
        frozen["frozen_at_duration"] = duration
    frozen["cache_fingerprint"] = _caption_cache_fingerprint(
        project, spec, frozen
    )
    return frozen


def _caption_cache_fingerprint(
    project: dict, spec: dict, recipe: dict
) -> str:
    """Cache key for one derived track: the compiled timeline, the
    recipe itself, and the project caption rules. Any change re-derives
    the track's cues on the next spec load."""
    try:
        resolved = resolve_project(project, spec)
        timeline_repr = [
            (
                span.scene_name,
                round(span.start_s, 6),
                round(span.end_s, 6),
                span.audio.source_id,
                span.audio.slices,
                span.audio.speed,
                span.held,
                span.layout,
            )
            for span in resolved.spans
        ]
    except (ResolvedProjectError, SpecValidationError):
        timeline_repr = ["unresolved"]
    payload = {
        "v": 2,
        "timeline": timeline_repr,
        "recipe": {
            key: value
            for key, value in recipe.items()
            if key != "cache_fingerprint"
        },
        "rules": project.get("caption_rules", []),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _active_source_id(project: dict) -> str:
    """Return the first source's ID. Single-source default for
    commands that don't (yet) take a `--source <id>` flag.
    """
    sources = project.get("sources") or []
    if not sources:
        raise SpecValidationError("project has no sources")
    return sources[0]["id"]


def _resolve_source_arg(
    project: dict, requested: str | None, command: str
) -> str:
    """Resolve the `--source <id>` argument for source-specific commands
    per D5: single-source projects default to the only source; multi-
    source projects require explicit `--source <id>`. Unknown IDs error
    with the available IDs named.

    Returns the resolved source ID. Calls _error_exit() and never
    returns on disambiguation failure.
    """
    sources = project.get("sources") or []
    if not sources:
        _error_exit(command, "project has no sources")
    available_ids = [s["id"] for s in sources]

    if requested is None:
        if len(sources) == 1:
            return available_ids[0]
        _error_exit(
            command,
            f"This project has {len(sources)} sources "
            f"({', '.join(available_ids)}). Pass --source <id> to pick one.",
        )

    if requested not in available_ids:
        _error_exit(
            command,
            f"Unknown source {requested!r}. "
            f"Available: {', '.join(available_ids)}.",
        )

    return requested


def _get_project_source(project: dict, source_id: str) -> dict:
    """Return the source dict from project.json's sources list by ID."""
    for source in project.get("sources", []):
        if source["id"] == source_id:
            return source
    raise SpecValidationError(f"unknown source_id {source_id!r}")


def _op_for_envelope(op: dict) -> dict:
    """Format a v0.2 operation (with string timecodes on disk) for
    inclusion in a CLI envelope (dict timecodes via format_timecode).

    D2 says timecodes on disk are strings; envelopes still get the
    dual {text, seconds} dict form. This helper bridges the two for
    the agent-facing operation/undone fields.
    """
    from moviestar.spec import parse_timecode_string

    op_type = op.get("type")
    if op_type in ("trim", "cut"):
        from_s = parse_timecode_string(op["from"], f"{op_type}.from")
        to_s = parse_timecode_string(op["to"], f"{op_type}.to")
        return {
            "type": op_type,
            "from": format_timecode(from_s),
            "to": format_timecode(to_s),
        }
    # Future op types: extend here. For now, return as-is.
    return op


def _emit_trim_cumulative_error(
    *,
    from_s: float,
    to_s: float,
    effective: float,
    source_duration: float,
    prior_ops: int,
    plural: str,
    op_word: str,
) -> None:
    """Issue #38: verbose error for the cumulative-trim friction case.

    Pre-#38 wording: ``"trim: range 1.5s-1.8s exceeds result duration
    of 1.0s"``. Technically correct but unhelpful — an agent looking
    at a 2-second source has no idea where the 1.0s came from. This
    helper names the prior-op count and the original source duration
    so the surprise is self-explanatory, and surfaces the two escape
    hatches (``spec --reset`` for a clean start; ``undo`` for stepwise
    revert) in the structured ``hint`` field per the #27/#78
    convention. Adds ``prior_operations: N`` at the envelope's top
    level so an agent can branch programmatically without parsing the
    prose.
    """
    payload = {
        "error": (
            f"trim: range {from_s}s-{to_s}s exceeds the current "
            f"{effective}s result timeline ({prior_ops} prior {plural} "
            f"applied; source duration {source_duration}s). The trim's "
            f"range is interpreted in result-time, not source-time, so "
            f"each new trim narrows the prior result."
        ),
        "command": "trim",
        "prior_operations": prior_ops,
        "hint": (
            f"To start fresh from the source: 'moviestar spec --reset' "
            f"to discard the {prior_ops} prior {op_word}, then re-run "
            f"this trim with source-time timecodes. To revert just the "
            f"last trim: 'moviestar undo'."
        ),
    }
    click.echo(json.dumps(payload, indent=2))
    sys.exit(1)


def _spec_parse_error_exit(
    file_path: str, content: str, exc: json.JSONDecodeError
) -> None:
    """Emit a structured ``parse_error`` envelope for spec --edit's
    JSON failure path. Issue #47.

    json.JSONDecodeError exposes ``lineno`` (1-indexed), ``colno``
    (1-indexed), and ``msg``. We add ``offending_line`` (read back
    from the file content) so the agent sees what they wrote
    without re-fetching the file. ``lineno`` can point past the
    end on EOF errors — guard the index lookup so we don't crash.
    """
    lines = content.splitlines()
    if 1 <= exc.lineno <= len(lines):
        offending = lines[exc.lineno - 1]
    else:
        offending = ""
    payload = {
        "error": f"Could not parse {file_path}: {exc.msg}",
        "command": "spec",
        "parse_error": {
            "file": file_path,
            "line": exc.lineno,
            "column": exc.colno,
            "message": exc.msg,
            "offending_line": offending,
        },
    }
    click.echo(json.dumps(payload, indent=2))
    sys.exit(1)


def _format_elapsed(seconds: float) -> str:
    """Compact elapsed-time string: '<1s', '5s', or '1m 23s'.

    Sub-second operations rendered as '<1s' rather than '0s' — the
    latter looks like a stuck timer to a reader skimming progress.
    """
    if seconds < 1:
        return "<1s"
    if seconds < 60:
        return f"{int(seconds)}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def _image_format_from_path(path: str) -> str:
    """Detect image format from extension. Defaults to 'unknown' for
    unrecognized extensions — agents can still consume the bytes via base64."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return "jpeg"
    if ext == ".png":
        return "png"
    return "unknown"


def _read_image_inline(path: str) -> dict:
    """Read an image file and return {format, base64} for inline embedding.

    Opt-in via --inline on skim/inspect/screenshot/storyboard (issue
    #319, flipping issue #35's embed-by-default): only frameworks that
    convert envelope bytes into image blocks benefit; CLI harnesses
    ingest stdout as text and read the image files instead.
    """
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return {"format": _image_format_from_path(path), "base64": encoded}


def _resolve_unique_output_path(
    path: str,
    reserved_paths: set[str] | None = None,
) -> str:
    """Return ``path`` if it doesn't exist; otherwise append ``_2``,
    ``_3``, ... to the stem until we find one that doesn't.

    Issue #25: ``watch`` and ``screenshot`` auto-name their outputs
    from arg values (e.g. ``watch_5s_15s.mp4``,
    ``screenshot_5.000s.jpg``). Re-invoking with the same args
    silently overwrote the previous output — an iterating agent
    lost work without notice. Auto-suffixing closes the silent-loss
    case without adding a new flag-discovery burden. Explicit
    ``--out`` paths bypass this helper and continue to overwrite via
    ffmpeg's ``-y``.
    """
    reserved_paths = reserved_paths or set()
    if not os.path.exists(path) and os.path.realpath(path) not in reserved_paths:
        return path
    root, ext = os.path.splitext(path)
    i = 2
    candidate = f"{root}_{i}{ext}"
    while (
        os.path.exists(candidate)
        or os.path.realpath(candidate) in reserved_paths
    ):
        i += 1
        candidate = f"{root}_{i}{ext}"
    return candidate


def _default_export_output_path(*, create_parent: bool) -> str:
    """Return the default export path under the user-facing clips dir.

    ``moviestar/`` is internal workspace state; rendered media lands in
    the sibling ``moviestar-clips/`` directory. Dry-run calls pass
    ``create_parent=False`` so the preview does not create directories.
    """
    clips_dir = get_project_dir().parent / CLIPS_DIR
    if create_parent:
        clips_dir.mkdir(parents=True, exist_ok=True)
    return _resolve_unique_output_path(str(clips_dir / "export.mp4"))


def _directory_stats(path: Path) -> tuple[int, int]:
    """Return (file_count, byte_count) for every file below path."""
    file_count = 0
    byte_count = 0
    if not path.exists():
        return file_count, byte_count
    for item in path.rglob("*"):
        if item.is_file():
            file_count += 1
            try:
                byte_count += item.stat().st_size
            except OSError:
                pass
    return file_count, byte_count


def _path_for_hint(path: str) -> str:
    """Prefer a cwd-relative path in hints when it stays inside cwd."""
    try:
        rel = os.path.relpath(path, os.getcwd())
    except ValueError:
        return path
    if rel == "." or rel.startswith(".."):
        return path
    return rel


def _dry_run_setup_commands_for_outputs(paths: list[str | Path]) -> list[list[str]]:
    """Return mkdir commands needed before running dry-run ffmpeg argv.

    Dry-run should not create directories, but agents often copy the
    surfaced ffmpeg argv directly. Make the missing parent-dir setup
    explicit instead of marking those raw commands rerunnable as-is.
    """
    seen: set[str] = set()
    commands: list[list[str]] = []
    for path in paths:
        parent = os.path.dirname(os.path.realpath(str(path)))
        if not parent or parent in seen or os.path.isdir(parent):
            continue
        seen.add(parent)
        commands.append(["mkdir", "-p", parent])
    return commands


def _apply_dry_run_setup(result: dict, setup_commands: list[list[str]]) -> None:
    if not setup_commands:
        result["rerunnable"] = True
        return
    result["rerunnable"] = False
    result["rerunnable_after_setup"] = True
    result["setup_commands"] = setup_commands


def _emit_transcript_text(
    command: str, transcript_out: dict | None, skip_reason: str | None
) -> None:
    """Emit '[HH:MM:SS.mmm] text' lines, one per transcript segment.

    Shared by skim (issue #40) and inspect (issue #69) so the two
    commands can't drift on the line shape. On no-transcript, calls
    ``_error_exit`` with the standard JSON error envelope — non-zero
    exit still produces parseable stdout rather than empty stdout
    that a grep / awk pipeline would silently accept as "no match."
    The public contract is ``--format text`` for transcript-bearing
    commands and structured JSON for the default format.
    """
    if transcript_out is None:
        reason = skip_reason or "no transcript on this project"
        # The "but...with" framing makes the contrast load-bearing —
        # earlier "requires a transcript ({reason})" parsed as "pass
        # {reason}" on first read when reason was a flag name.
        _error_exit(
            command,
            f"--format text requires a transcript, but this project was "
            f"loaded without one ({reason}). Re-run 'moviestar load "
            "--force' without --no-transcribe.",
        )
    for seg in transcript_out["segments"]:
        click.echo(f"[{seg['start']['text']}] {seg['text'].strip()}")


def _slice_transcript_for_response(
    source: dict, from_s: float, to_s: float, words: bool
) -> tuple[dict | None, str | None, str | None]:
    """Return ``(transcript_out, transcription_skipped_reason, words_unavailable_reason)``.

    Shared by skim and inspect — both surface a transcript slice for
    a range with the same ``--words`` opt-in policy and the same
    no-transcript fallback wording. Centralizing here so the two
    callers can't drift.

    - ``transcript_out`` is the segment-level slice when --words is
      off, or the slice with words array preserved when --words is on.
      None when no transcript is loaded.
    - ``transcription_skipped_reason`` carries forward from load when
      the project has no transcript (e.g., "--no-transcribe").
    - ``words_unavailable_reason`` is set only when --words was passed
      against a no-transcript project — surfaces the reason so --words
      doesn't silently no-op (issues #43 (inspect) and the earlier
      skim fix).
    """
    full_transcript = load_transcript(source)
    transcript_out: dict | None = None
    skip_reason: str | None = None
    words_unavailable_reason: str | None = None

    if full_transcript is not None:
        transcript_out = slice_transcript(full_transcript, from_s, to_s)
        if not words:
            # Drop the words array by default — it's ~90% of response size
            # on long videos and only needed for frame-exact trim selection.
            transcript_out.pop("words", None)
    else:
        # Issue #33: normalize legacy values from older project.json
        # files to the canonical enum at the read boundary.
        skip_reason = normalize_skip_reason(
            source.get("transcription_skipped_reason")
        )
        if words:
            words_unavailable_reason = (
                f"--words requested but no transcript is available "
                f"({_skip_reason_prose(skip_reason)}). Re-run "
                "'moviestar load --force' without --no-transcribe to "
                "enable word-level timing."
            )

    return transcript_out, skip_reason, words_unavailable_reason


def _inspect_boundary_exclusions(
    source: dict,
    from_s: float,
    to_s: float,
) -> dict | None:
    """Return transcript segments excluded only because they cross inspect bounds."""
    full_transcript = load_transcript(source)
    if full_transcript is None:
        return None

    leading = []
    trailing = []
    for segment in full_transcript.get("segments", []):
        start = float(segment["start"]["seconds"])
        end = float(segment["end"]["seconds"])
        if start < from_s < end:
            leading.append(segment)
        if start < to_s < end:
            trailing.append(segment)

    result: dict = {}
    if leading:
        earliest_start = min(
            float(segment["start"]["seconds"]) for segment in leading
        )
        result["leading"] = {
            "count": len(leading),
            "earliest_start": format_timecode(earliest_start),
        }
    if trailing:
        latest_end = max(float(segment["end"]["seconds"]) for segment in trailing)
        result["trailing"] = {
            "count": len(trailing),
            "latest_end": format_timecode(latest_end),
        }
    return result or None


def _append_boundary_exclusion_hint(
    hint: str,
    exclusions: dict | None,
) -> str:
    if not exclusions:
        return hint

    suggestions: list[str] = []
    leading = exclusions.get("leading")
    if leading:
        suggestions.append(
            f"Widen --from to {leading['earliest_start']['text']}"
        )
    trailing = exclusions.get("trailing")
    if trailing:
        prefix = "or --to" if suggestions else "Widen --to"
        suggestions.append(f"{prefix} to {trailing['latest_end']['text']}")
    if not suggestions:
        return hint
    return hint + " " + " ".join(suggestions) + " to capture boundary segments."


def _skip_reason_prose(enum_value: str | None) -> str:
    """Translate a transcription_skipped_reason enum into agent-readable
    prose for inclusion in human-facing messages (e.g. ``words_unavailable_reason``).

    The structured field stays an enum (``"flag"``, ``"no_audio"``,
    ``"model_unavailable"``) for stable matching; this helper renders
    each value as a sentence-fragment so a message like
    ``"--words requested but no transcript is available (X)."``
    reads as English instead of leaking the bare token.
    """
    return {
        "flag": "loaded with --no-transcribe",
        "no_audio": "no audio stream in source",
        "model_unavailable": "Whisper model unavailable",
    }.get(enum_value or "", "no transcript loaded")


def _already_loaded_error(requested_source: str) -> None:
    """Emit a structured error when load is called in a directory that already has a project.

    The payload includes currently_loaded (source + project_dir), requested_source,
    and same_source so agents can decide whether to use the existing project
    (skim) or replace it (--force) without guessing.
    """
    existing = load_project()
    current_source = existing["sources"][0]["path"]
    same_source = os.path.realpath(current_source) == os.path.realpath(requested_source)

    # Issue #27: errors mirror the success path's structured `hint`
    # field. The message stays a human sentence; the actionable
    # remediation moves to its own top-level key so an agent reading
    # the envelope finds the next step in the same place across
    # success and error responses.
    if same_source:
        message = (
            f"Project already loaded with {current_source!r} "
            "(same video as requested)."
        )
        hint = (
            "Run 'moviestar skim' to browse the existing project, or "
            "'moviestar load --force' to re-index."
        )
    else:
        message = (
            f"Project already loaded with {current_source!r}. "
            f"You requested a different video: {requested_source!r}."
        )
        hint = (
            "Run 'moviestar load --add <video>' to add the source without "
            "replacing the current project. To replace it instead, run "
            "'moviestar load <video> --force', or cd to a new directory "
            "to start a fresh project."
        )

    payload = {
        "error": message,
        "command": "load",
        "currently_loaded": {
            "source": current_source,
            "project_dir": str(get_project_dir()),
        },
        "requested_source": os.path.realpath(requested_source),
        "same_source": same_source,
        "hint": hint,
    }
    click.echo(json.dumps(payload, indent=2))
    sys.exit(1)


def _caption_rules_loss_summary(project: dict) -> dict:
    """Inventory project caption rules without validating or mutating them."""
    rules = project.get("caption_rules", [])
    if not isinstance(rules, list):
        return {"count": None, "ids": None}
    return {
        "count": len(rules),
        "ids": [
            rule["id"]
            for rule in rules
            if isinstance(rule, dict)
            and isinstance(rule.get("id"), str)
            and rule["id"]
        ],
    }


def _emit_load_dry_run(
    *,
    abs_source: str,
    source_id: str,
    start_offset_seconds: float | None,
    interval: float | None,
    model: str,
    thumb_width: int,
    no_transcribe: bool,
    no_frames: bool,
    force: bool,
    currently_loaded: dict | None,
) -> None:
    """Emit the dry-run envelope for ``moviestar load --dry-run``.

    Probes the source (read-only, no side effect), computes
    ``would_extract_frames_count`` from the probed duration via the
    same exclusive-upper-bound formula inspect uses, decides
    ``would_transcribe`` from
    the flag + audio-stream presence, and builds the would-be
    frame-extraction ffmpeg argv.

    When called with --force on an existing workspace,
    ``currently_loaded`` (captured before this helper runs, so the
    workspace itself is untouched) is included alongside
    ``would_wipe_workspace: true`` — the killer use case that lets
    an agent preview the clobber before paying the click. Caption rules
    are inventoried explicitly, and same-source previews point to the
    non-destructive retranscribe command.
    """
    try:
        probe_data = run_ffprobe(abs_source)
    except FFmpegNotFoundError as exc:
        _error_exit("load", str(exc))
    except (RuntimeError, json.JSONDecodeError) as exc:
        _error_exit("load", f"Could not probe {abs_source}: {exc}")

    fmt = probe_data.get("format", {})
    duration_seconds = float(fmt.get("duration", 0)) if fmt.get("duration") else 0.0
    interval = select_load_frame_interval(duration_seconds, interval)

    video_stream = next(
        (s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"),
        None,
    )

    # Same exclusive-upper-bound formula inspect uses (issue #36
    # stage-2 friction-test follow-up): math.ceil(round(D/I, 9)) for
    # both aligned and unaligned cases, with float-fuzz absorption.
    if no_frames:
        would_extract_frames_count = 0
    elif interval > 0 and duration_seconds > 0:
        would_extract_frames_count = math.ceil(round(duration_seconds / interval, 9))
    else:
        would_extract_frames_count = 0

    # would_transcribe mirrors create_workspace's decision tree.
    if no_transcribe:
        would_transcribe = False
        would_skip_reason = TRANSCRIPTION_SKIP_REASON_FLAG
    elif audio_stream is None:
        would_transcribe = False
        would_skip_reason = TRANSCRIPTION_SKIP_REASON_NO_AUDIO
    else:
        would_transcribe = True
        would_skip_reason = None

    # Would-be project_dir + frame-extraction ffmpeg argv. Neither
    # the dir nor the pattern path is created — purely a string
    # computation that mirrors what create_workspace would build.
    project_dir = (Path.cwd() / MOVIESTAR_DIR).resolve()
    frames_pattern = str(project_dir / FRAMES_DIR / f"{source_id}_%04d.jpg")
    ffmpeg_cmd = build_extract_frames_command(
        abs_source,
        interval,
        frames_pattern,
        scale_width=thumb_width,
    )

    source_dict: dict = {
        "id": source_id,
        "path": abs_source,
        "duration": format_timecode(duration_seconds),
        "width": video_stream.get("width") if video_stream else None,
        "height": video_stream.get("height") if video_stream else None,
        "fps": (
            _parse_fps(video_stream.get("r_frame_rate"))
            if video_stream
            else None
        ),
        "video_codec": video_stream.get("codec_name") if video_stream else None,
        "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
        "frame_interval": interval,
        "start_offset_seconds": start_offset_seconds,
    }

    # Build the hint conditionally on whether --force would wipe.
    same_source = False
    if currently_loaded is not None:
        same_source = os.path.realpath(
            currently_loaded.get("source") or ""
        ) == os.path.realpath(abs_source)
        caption_rules = currently_loaded["caption_rules"]
        caption_rules_count = caption_rules["count"]
        if caption_rules_count is None:
            caption_rules_note = (
                " Caption-rule loss could not be inventoried because the "
                "stored field is malformed."
            )
        else:
            caption_rules_note = (
                f" This removes {caption_rules_count} project caption "
                f"rule(s): {caption_rules['ids']}."
            )
        hint = (
            f"Dry-run only — no workspace created. A real call with --force "
            f"would FIRST wipe the existing workspace at {currently_loaded.get('project_dir')!r} "
            f"(currently loaded: {currently_loaded.get('source')!r}) THEN load "
            f"{abs_source!r}.{caption_rules_note}"
        )
        if same_source:
            source_id = currently_loaded.get("source_id") or "<source-id>"
            hint += (
                " To refresh transcription while preserving caption rules "
                "and other authored state, run 'moviestar retranscribe "
                f"--source {source_id}' instead."
            )
        else:
            hint += " Re-run without --dry-run to commit."
    else:
        hint = (
            "Dry-run only — no workspace created. Re-run without --dry-run "
            "to load. The would_extract_frames_count above is what the real "
            "call would write to moviestar/frames/."
        )

    result: dict = {
        "dry_run": True,
        "status": "would_load",
        "project_dir": str(project_dir),
        "would_extract_frames_count": would_extract_frames_count,
        "would_transcribe": would_transcribe,
        "sources": [source_dict],
        # With --no-frames the frame-extraction ffmpeg never runs —
        # null, not a command a real call wouldn't execute.
        "ffmpeg_command": None if no_frames else ffmpeg_cmd,
        "hint": hint,
    }
    if no_frames:
        result["would_skip_frame_extraction_reason"] = (
            FRAME_EXTRACTION_SKIP_REASON_FLAG
        )
    if would_transcribe:
        result["would_use_model"] = model
        requirement = model_download_requirement(model)
        if requirement is not None:
            result["requires_download"] = requirement
    else:
        result["would_skip_transcription_reason"] = would_skip_reason
    if currently_loaded is not None:
        result["currently_loaded"] = currently_loaded
        result["would_wipe_workspace"] = True
        result["same_source"] = same_source
        result["would_remove"] = {
            "caption_rules": currently_loaded["caption_rules"]
        }

    click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("video", type=click.Path())
@click.option(
    "--loudness",
    is_flag=True,
    help="Measure integrated loudness and true peak with FFmpeg loudnorm.",
)
@click.option("--from", "from_tc", default=None, help="Start timecode for --loudness.")
@click.option("--to", "to_tc", default=None, help="End timecode for --loudness.")
def probe(video: str, loudness: bool, from_tc: str | None, to_tc: str | None) -> None:
    """Inspect a media file's metadata, and optionally audio loudness.

    Run this before 'load' to see what you're working with — no frames,
    no transcript, just the file's structural info. Pass --loudness to
    get machine-readable LUFS / peak metrics for the whole file or a
    --from/--to range.
    """
    abs_path = os.path.realpath(video)
    if (from_tc is not None or to_tc is not None) and not loudness:
        _error_exit("probe", "--from/--to are only supported with --loudness.")

    try:
        data = run_ffprobe(abs_path)
    except FileNotFoundError:
        _error_exit("probe", f"File not found: {abs_path}")
    except FFmpegNotFoundError as exc:
        _error_exit("probe", str(exc))
    except (RuntimeError, json.JSONDecodeError) as exc:
        _error_exit("probe", f"Could not probe {abs_path}: {exc}")

    fmt = data.get("format", {})
    duration_str = fmt.get("duration")
    duration_seconds = float(duration_str) if duration_str else 0.0

    result: dict = {
        "source": abs_path,
        "start_offset_seconds": None,
        "duration": format_timecode(duration_seconds),
        "file_size_bytes": int(fmt.get("size", 0)) if fmt.get("size") else 0,
        "format": fmt.get("format_name", ""),
        "bitrate": int(fmt.get("bit_rate", 0)) if fmt.get("bit_rate") else 0,
    }

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        None,
    )

    # Issue #58: cross-primitive shape consistency. Always emit both
    # `video` and `audio` keys; use null when the stream is absent
    # (matches the existing `transcript: null` convention from the
    # read primitives). An agent doing `r["audio"]` without a
    # presence check now works on any video — silent or not.
    if video_stream is not None:
        result["video"] = {
            "codec": video_stream.get("codec_name", ""),
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "fps": _parse_fps(video_stream.get("r_frame_rate")),
            "pixel_format": video_stream.get("pix_fmt", ""),
        }
    else:
        result["video"] = None

    if audio_stream is not None:
        sample_rate = audio_stream.get("sample_rate")
        result["audio"] = {
            "codec": audio_stream.get("codec_name", ""),
            "sample_rate": int(sample_rate) if sample_rate else None,
            "channels": audio_stream.get("channels"),
        }
    else:
        result["audio"] = None

    result["ffprobe_command"] = build_ffprobe_command(abs_path)

    if loudness:
        if audio_stream is None:
            _error_exit("probe", f"Cannot measure loudness: {abs_path} has no audio stream.")
        try:
            from_s = parse_timecode(from_tc) if from_tc is not None else 0.0
            to_s = parse_timecode(to_tc) if to_tc is not None else duration_seconds
        except ValueError as exc:
            _error_exit("probe", str(exc))
        if from_s < 0:
            _error_exit("probe", "--from cannot be negative.")
        if to_s <= from_s:
            _error_exit("probe", "--to must be after --from.")
        if duration_seconds and to_s > duration_seconds + 0.001:
            _error_exit(
                "probe",
                f"--to {to_s}s is beyond media duration {duration_seconds}s.",
            )
        loudness_duration = to_s - from_s
        range_requested = from_tc is not None or to_tc is not None
        try:
            metrics, cmd = run_loudness_probe(
                abs_path,
                start_time=from_s if from_s > 0 else None,
                duration=loudness_duration if range_requested else None,
                target_lufs=-14.0,
                true_peak=LOUDNESS_TRUE_PEAK_LIMIT,
                lra=LOUDNESS_LRA_TARGET,
            )
        except FileNotFoundError:
            _error_exit("probe", f"File not found: {abs_path}")
        except FFmpegNotFoundError as exc:
            _error_exit("probe", str(exc))
        except RuntimeError as exc:
            _error_exit("probe", f"Could not measure loudness for {abs_path}: {exc}")

        result["loudness"] = {
            **metrics,
            "range": {
                "from": format_timecode(from_s),
                "to": format_timecode(to_s),
                "duration": format_timecode(loudness_duration),
            },
            "ffmpeg_command": cmd,
        }

    click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("videos", nargs=-1, type=click.Path(), required=True)
@click.option(
    "--interval",
    type=float,
    default=None,
    help="Seconds between extracted frames. Omitted: auto-picks a round "
    "interval targeting ~15 frames, capped at 5 seconds. Explicit values "
    "are used exactly.",
)
@click.option("--force", is_flag=True, help="Wipe any existing workspace and re-index.")
@click.option(
    "--add",
    "add",
    is_flag=True,
    help="Extend an existing project by adding the given source(s) "
    "to its sources list. Existing per-source operations and "
    "transcripts are preserved. Cannot combine with --force.",
)
@click.option(
    "--as",
    "names",
    multiple=True,
    help="Optional nickname(s) for the loaded source(s), position-paired "
    "with positional VIDEOS. For multiple videos, repeat --as once per "
    "video or omit it entirely. Default IDs are derived from the filename's "
    "first token (`holden-cam.mp4` → `holden`).",
)
@click.option(
    "--model",
    type=click.Choice(AVAILABLE_MODELS),
    default=DEFAULT_MODEL,
    help=f"Whisper model size (default {DEFAULT_MODEL}).",
)
@click.option(
    "--vocabulary",
    "vocabulary",
    multiple=True,
    help="Names or terms to bias transcription toward, so Whisper spells "
    "them correctly (e.g. --vocabulary 'Amal,Holden,Kubernetes'). "
    "Comma-separated and repeatable. Applies to all loaded sources.",
)
@click.option(
    "--start-offset",
    "start_offsets",
    multiple=True,
    help=(
        "Optional source alignment offset in seconds. Repeat per source. "
        "Use a bare value position-paired with VIDEOS, or SOURCE=SECONDS "
        "after source IDs are known/overridden, e.g. --as screen "
        "--start-offset screen=1.25."
    ),
)
@click.option(
    "--thumb-width",
    "thumb_width",
    type=click.IntRange(64, 1920),
    default=DEFAULT_THUMB_WIDTH,
    show_default=True,
    help="Pixel width of indexed thumbnails served by skim. Raise it "
    "(e.g. 640) for screen recordings where UI text must stay "
    "legible; wider costs disk and index time. For one-off wider "
    "frames without re-indexing, use 'moviestar inspect --width'.",
)
@click.option("--no-transcribe", is_flag=True, help="Skip transcription entirely.")
@click.option(
    "--no-download",
    is_flag=True,
    help="Never download a Whisper model. Error before changing the workspace "
    "if transcription needs a model that is not cached.",
)
@click.option(
    "--no-frames",
    is_flag=True,
    help="Skip thumbnail frame extraction. Fast index-only loads when "
    "you need transcript + raw seek but not visual browsing. skim "
    "returns no thumbnails (with frame_extraction_skipped_reason: "
    "'flag'); inspect and screenshot still work — they extract from "
    "the source on demand.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Validate the source + flags and probe metadata, but skip "
    "frame extraction, transcription, and writing project.json. "
    "Returns the same envelope shape with status: 'would_load', "
    "dry_run: true, would_extract_frames_count, would_transcribe, "
    "and (when --force on an existing workspace) currently_loaded "
    "+ would_wipe_workspace: true + would_remove.caption_rules so "
    "an agent can preview what would be clobbered before paying "
    "the click. A same-source preview points to retranscribe as the "
    "non-destructive path. Dry-run with "
    "multiple videos errors — multi-source dry-run is a follow-on.",
)
@_quiet_option
def load(
    videos: tuple[str, ...],
    interval: float | None,
    force: bool,
    add: bool,
    names: tuple[str, ...],
    model: str,
    vocabulary: tuple[str, ...],
    start_offsets: tuple[str, ...],
    thumb_width: int,
    no_transcribe: bool,
    no_download: bool,
    no_frames: bool,
    dry_run: bool,
    quiet: bool,
) -> None:
    """Load one or more videos into a project workspace.

    Creates moviestar/ in the current directory with a project.json
    (source metadata + transcript pointer) and a frames/ directory of
    extracted thumbnails. Source videos are never copied — we store
    absolute path pointers.

    Multiple paths create a multi-source project. Source IDs are
    derived from each filename's first token (`holden-cam.mp4` →
    `holden`); collisions disambiguate with `_2`/`_3` suffixes.
    Pass `--as <name>` per position to override. For multiple videos,
    repeat `--as` once per video or omit it entirely.

    When --interval is omitted, each source auto-picks a round interval
    targeting roughly 15 indexed frames, capped at the historical
    5-second default. Pass --interval explicitly to use that exact spacing.

    Use `--add` to extend an existing project with new sources
    (preserves per-source edits and transcripts). `--add` cannot be
    combined with `--force`.

    Transcription runs by default using faster-whisper (local, free).
    First run downloads the requested model from HuggingFace:
    tiny ~75 MB · base ~145 MB · small ~480 MB · medium ~1.5 GB · large-v3 ~3.0 GB.
    Use --no-transcribe to skip, or --model tiny|small|medium|large-v3 to
    change size. Pass --vocabulary 'Amal,Holden' to bias the decoder toward
    names/terms it would otherwise misspell.

    Use --no-download for hermetic CI or sandboxed runs. If the requested
    model is not cached, load exits before wiping or creating a workspace;
    pre-warm it explicitly with ``moviestar models pull MODEL``.

    Pass --start-offset to store user-supplied inter-source alignment
    metadata. Bare values pair by position with VIDEOS; SOURCE=SECONDS
    values target the final source ID (from --as or filename-derived).
    The value is metadata only — it does not shift edit timecodes.

    When transcription is skipped, the response includes a
    ``transcription_skipped_reason`` field with a stable enum value.
    Same field surfaces on ``skim`` / ``inspect`` /
    ``status`` for projects without transcripts.

    Use --no-frames to skip thumbnail extraction for fast index-only
    loads. The source carries
    ``frame_extraction_skipped_reason: "flag"``, surfaced on ``skim``
    and ``status``. ``inspect`` and ``screenshot`` are unaffected —
    they extract from the source on demand. ``find`` reads transcripts
    only. Combine with --no-transcribe for the fastest possible load.

    This is the one slow step. After load, 'skim', 'inspect', 'trim',
    and 'export' all read from the pre-built index.

    With ``--dry-run``, load probes the source and decides what would
    happen without creating ``moviestar/`` or running ffmpeg /
    Whisper. Dry-run currently supports a single video; multi-source dry-run
    is not supported. With ``--force`` on an existing workspace,
    the preview inventories caption rules that the reset would remove;
    a same-source preview points to non-destructive ``retranscribe``.
    """
    if not videos:
        _error_exit("load", "Provide at least one video path.")

    if force and add:
        _error_exit(
            "load",
            "--force and --add cannot be combined. --force replaces the "
            "project; --add extends it.",
        )

    if names and len(names) != len(videos):
        _error_exit_with_hint(
            "load",
            f"Got {len(names)} --as name(s) for {len(videos)} videos. "
            "When loading multiple videos, provide exactly one --as per "
            "video or omit --as entirely.",
            "Repeat --as once per video, for example: moviestar load "
            "VIDEO1 VIDEO2 --as NAME1 --as NAME2.",
        )

    # Validate paths exist before doing anything.
    abs_paths: list[str] = []
    for v in videos:
        abs_v = os.path.realpath(v)
        if not os.path.exists(abs_v):
            _error_exit("load", f"File not found: {abs_v}")
        abs_paths.append(abs_v)

    # Multi-source dry-run is out of scope for M13a's first multi-path
    # commit. Single-source dry-run continues to work; agents who need
    # to preview a multi-source load can dry-run each path separately
    # for now.
    if dry_run and len(abs_paths) > 1:
        _error_exit(
            "load",
            "--dry-run with multiple videos isn't supported yet. Re-run "
            "with a single video to preview, or drop --dry-run to load "
            "all sources.",
        )

    if dry_run and add:
        _error_exit(
            "load",
            "--dry-run with --add isn't supported yet. Re-run without "
            "--dry-run to extend the project.",
        )

    # Issue #41: load is a CREATE-IN-CWD operation; never walk up.
    currently_loaded: dict | None = None
    should_reset_workspace = False
    existing_sources: list[dict] = []
    if is_loaded(walk=False):
        if add:
            # --add is the explicit "extend" path; we leave the
            # workspace alone and create_workspace(add=True)
            # appends the new sources.
            try:
                existing_sources = load_project(walk=False).get("sources", [])
            except (json.JSONDecodeError, FileNotFoundError) as exc:
                _error_exit("load", f"Could not read existing project.json: {exc}")
        elif not force:
            _already_loaded_error(abs_paths[0])
        else:
            # Loaded AND --force.
            if dry_run:
                try:
                    existing = load_project(walk=False)
                    existing_source = existing["sources"][0]
                    currently_loaded = {
                        "source": existing_source["path"],
                        "source_id": existing_source.get("id"),
                        "project_dir": str(get_project_dir(walk=False)),
                        "caption_rules": _caption_rules_loss_summary(existing),
                    }
                except (json.JSONDecodeError, FileNotFoundError, KeyError, IndexError):
                    currently_loaded = {
                        "source": None,
                        "source_id": None,
                        "project_dir": str(get_project_dir(walk=False)),
                        "caption_rules": {"count": None, "ids": None},
                    }
            else:
                should_reset_workspace = True
    elif add:
        _error_exit(
            "load",
            "--add requires an existing project in this directory, but "
            "none was found. Drop --add to create a fresh project.",
        )

    planned_source_ids = _planned_load_source_ids(videos, names, existing_sources)
    start_offset_values = _resolve_start_offsets(start_offsets, planned_source_ids)

    if dry_run:
        # Issue #170: preview the same id the real path derives —
        # --as override first, then the first-token rule. Single-source
        # fresh/--force workspace only (multi-video and --add dry-runs
        # are rejected above), so the taken-id set is empty.
        _emit_load_dry_run(
            abs_source=abs_paths[0],
            source_id=planned_source_ids[0],
            start_offset_seconds=start_offset_values[0],
            interval=interval,
            model=model,
            thumb_width=thumb_width,
            no_transcribe=no_transcribe,
            no_frames=no_frames,
            force=force,
            currently_loaded=currently_loaded,
        )
        return

    # Resolve a cold-model requirement before any workspace mutation. Probe
    # only while the selected model is uncached so silent inputs do not fail
    # --no-download and warm loads do not pay an extra ffprobe call.
    requirement = (
        None if no_transcribe else model_download_requirement(model)
    )
    if requirement is not None:
        source_has_audio = False
        for abs_path in abs_paths:
            try:
                probe_data = run_ffprobe(abs_path)
            except FFmpegNotFoundError as exc:
                _error_exit("load", str(exc))
            except (RuntimeError, json.JSONDecodeError) as exc:
                _error_exit("load", f"Could not probe {abs_path}: {exc}")
            if any(
                stream.get("codec_type") == "audio"
                for stream in probe_data.get("streams", [])
            ):
                source_has_audio = True
                break

        if source_has_audio:
            if no_download:
                _error_exit_with_hint(
                    "load",
                    f"Whisper model {model!r} is not cached and --no-download "
                    "forbids network access.",
                    f"Run 'moviestar models pull {model}' before retrying, or "
                    "remove --no-download.",
                    requires_download=requirement,
                )
            if not quiet:
                click.echo(
                    f"Pre-flight: transcription requires downloading the "
                    f"faster-whisper {model!r} model "
                    f"(~{requirement['estimated_size_mb']} MB) to "
                    f"{requirement['cache_dir']}.",
                    err=True,
                )

    if should_reset_workspace:
        reset_workspace()

    # First line the user sees — prints IMMEDIATELY so there's no silent window
    # while probe/frames/model-load run.
    start_time = time.time()
    summary = ", ".join(os.path.basename(p) for p in abs_paths)
    if not quiet:
        if add:
            click.echo(
                f"Adding {summary} to existing project... "
                "(--quiet silences progress)",
                err=True,
            )
        else:
            click.echo(
                f"Loading {summary}... (--quiet silences progress)", err=True
            )

    # Convert names tuple to list with None padding so positions align.
    name_overrides: list[str | None] = list(names) + [None] * (
        len(abs_paths) - len(names)
    )

    try:
        project = create_workspace(
            list(videos),
            interval=interval,
            names=name_overrides,
            add=add,
            transcribe=not no_transcribe,
            model=model,
            frames=not no_frames,
            quiet=quiet,
            vocabulary=_normalize_vocabulary(vocabulary),
            start_offsets=start_offset_values,
            allow_model_download=not no_download,
            thumb_width=thumb_width,
        )
    except WorkspaceConflictError as exc:
        _error_exit("load", str(exc))
    except FFmpegNotFoundError as exc:
        if not add:
            reset_workspace()
        _error_exit("load", str(exc))
    except FileNotFoundError as exc:
        if not add:
            reset_workspace()
        _error_exit("load", str(exc))
    except TranscriptionError as exc:
        if not add:
            reset_workspace()
        _error_exit("load", f"Transcription failed: {exc}")
    except (RuntimeError, json.JSONDecodeError) as exc:
        if not add:
            reset_workspace()
        _error_exit("load", f"Could not probe source: {exc}")

    if add:
        try:
            save_spec(_load_or_init_spec(project), record=False)
        except SpecValidationError as exc:
            _error_exit("load", str(exc))

    total_elapsed = time.time() - start_time
    if not quiet:
        click.echo(f"Loaded in {_format_elapsed(total_elapsed)}.", err=True)

    # Build the output envelope with one entry per source. For --add,
    # we report ALL sources currently in the project (existing + new)
    # so the agent sees the full state.
    output_sources = []
    transcript_warnings: list[dict] = []
    for source in project["sources"]:
        output_source = {
            "id": source["id"],
            "path": source["path"],
            "duration": source["duration"],
            "width": source.get("width"),
            "height": source.get("height"),
            "fps": source.get("fps"),
            "video_codec": source.get("video_codec") or None,
            "audio_codec": source.get("audio_codec") or None,
            "frame_interval": source.get("frame_interval"),
            "start_offset_seconds": source.get("start_offset_seconds"),
            "frames_extracted": source.get("frames_extracted"),
            "thumb_width": source.get("thumb_width", DEFAULT_THUMB_WIDTH),
        }
        if source.get("frame_extraction_skipped_reason"):
            output_source["frame_extraction_skipped_reason"] = source[
                "frame_extraction_skipped_reason"
            ]

        if source.get("transcript"):
            transcript_path = (
                get_project_dir() / source["transcript"]["path"]
            ).resolve()
            transcript_data = json.loads(transcript_path.read_text())
            output_source["transcript"] = {
                "model": source["transcript"].get("model", model),
                "path": str(transcript_path),
                "word_count": len(transcript_data.get("words", [])),
                "duration": transcript_data.get("duration"),
            }
            if transcript_data.get("vocabulary"):
                output_source["transcript"]["vocabulary"] = transcript_data[
                    "vocabulary"
                ]
            transcript_warnings.extend(transcript_data.get("warnings") or [])
        else:
            output_source["transcript"] = None
            output_source["transcription_skipped_reason"] = normalize_skip_reason(
                source.get("transcription_skipped_reason")
            )
        output_sources.append(output_source)

    if add:
        status = "extended"
        hint = (
            f"Project now has {len(output_sources)} source(s). "
            "Use 'moviestar status' for an overview, or "
            "'moviestar skim' to browse a specific source."
        )
    elif len(output_sources) == 1:
        status = "loaded"
        skipped_no_audio = (
            output_sources[0].get("transcription_skipped_reason")
            == "no_audio"
        )
        if skipped_no_audio:
            # Issue #304: no audio stream means the transcript path is
            # a permanent dead end (re-loading won't help) — route to
            # the visual-review workflow, storyboard first.
            if no_frames:
                hint = (
                    "No audio track — transcript unavailable; frames "
                    "were skipped. Use 'moviestar storyboard' for a "
                    "one-image visual overview, 'moviestar inspect' "
                    "for dense review of a range, or 'moviestar "
                    "screenshot --at <timecode>' for a still."
                )
            else:
                hint = (
                    "No audio track — transcript unavailable. Use "
                    "'moviestar storyboard' for a one-image visual "
                    "overview, 'moviestar skim' to browse indexed "
                    "frames, 'moviestar inspect' for dense review of "
                    "a range, or 'moviestar screenshot --at "
                    "<timecode>' for a still."
                )
        elif no_frames and output_sources[0].get("transcript") is None:
            # Friction-test catch (2026-06-09): with frames AND
            # transcript both skipped, skim has nothing to show —
            # don't send the agent there as its first move.
            hint = (
                "Index-only load — frames and transcript were both "
                "skipped. Use 'moviestar inspect' or 'moviestar "
                "screenshot --at <timecode>' for on-demand visuals, or "
                "re-run 'moviestar load --force' without the skip flags "
                "to build the full index."
            )
        elif no_frames:
            # Issue #27 convention: hint reflects post-action state.
            # No thumbnails exist, so don't invite frame browsing —
            # point at on-demand extraction instead.
            hint = (
                "Run 'moviestar skim' to browse the transcript (frames "
                "were skipped). For visuals, use 'moviestar inspect' or "
                "'moviestar screenshot --at <timecode>' — both extract "
                "from the source on demand."
            )
        else:
            hint = (
                "Run 'moviestar skim' to browse frames and transcript. "
                "Narrow with --from/--to. Capture a single frame with "
                "'moviestar screenshot --at <timecode>'."
            )
    else:
        status = "loaded"
        hint = (
            f"Multi-source project loaded ({len(output_sources)} sources). "
            "Use 'moviestar status' for an overview. Source-specific "
            "commands accept '--source <id>' (default: first source)."
        )

    result = {
        "status": status,
        "project_dir": str(get_project_dir()),
        "sources": output_sources,
        "hint": hint,
    }
    _add_warnings(result, transcript_warnings)
    click.echo(json.dumps(result, indent=2))


@cli.command()
@project_workspace_option
@click.option(
    "--model",
    type=click.Choice(AVAILABLE_MODELS),
    default=DEFAULT_MODEL,
    help=f"Whisper model size (default {DEFAULT_MODEL}).",
)
@click.option(
    "--vocabulary",
    "vocabulary",
    multiple=True,
    help="Names or terms to bias transcription toward, so Whisper spells "
    "them correctly (e.g. --vocabulary 'Amal,Holden,Kubernetes'). "
    "Comma-separated and repeatable.",
)
@click.option(
    "--no-download",
    is_flag=True,
    help="Never download a Whisper model; error if MODEL is not cached.",
)
@_quiet_option
def retranscribe(
    model: str, vocabulary: tuple[str, ...], no_download: bool, quiet: bool
) -> None:
    """Re-run transcription against the loaded source. Keeps frames + spec.

    The non-destructive alternative to ``load --force`` when you want
    a higher-quality transcript (e.g. switching from ``base`` to
    ``large-v3``) without losing your frame index or edit history.

    Replaces ``transcripts/<source_id>.json`` and updates the
    project's ``source.transcript.source`` model field. Frames,
    spec.json, and any prior trim / undo history are untouched.

    Also doubles as the "transcribe later" hatch: a project loaded
    with ``--no-transcribe`` can pick up a transcript by running
    ``moviestar retranscribe`` without ``--force``.

    Pass ``--vocabulary 'Amal,Holden'`` to bias the decoder toward
    names/terms it would otherwise misspell.

    Use ``--no-download`` for hermetic runs. Pre-warm a missing model with
    ``moviestar models pull MODEL``.

    Walks up to find the nearest ``moviestar/`` workspace, so it
    works from any subdir of the project.
    """
    if not is_loaded():
        _no_project_error_exit("retranscribe")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("retranscribe", f"Could not read project.json: {exc}")

    source = project["sources"][0]
    source_id = source["id"]
    source_path = source["path"]

    if not os.path.exists(source_path):
        _error_exit_with_hint(
            "retranscribe",
            f"Source video missing: {source_path}. The project's source moved "
            "or was deleted.",
            "Run 'moviestar load <video> --force' to re-index against the "
            "moved-to path, or restore the source at the original location.",
        )

    requirement = model_download_requirement(model)
    if no_download and requirement is not None:
        _error_exit_with_hint(
            "retranscribe",
            f"Whisper model {model!r} is not cached and --no-download "
            "forbids network access.",
            f"Run 'moviestar models pull {model}' before retrying, or remove "
            "--no-download.",
            requires_download=requirement,
        )

    if not quiet:
        click.echo(
            f"Retranscribing {os.path.basename(source_path)} with {model!r}... "
            "(--quiet silences progress)",
            err=True,
        )
    start_time = time.time()

    try:
        transcript = transcribe_file(
            source_path,
            source_id,
            model=model,
            vocabulary=_normalize_vocabulary(vocabulary),
            allow_download=not no_download,
            quiet=quiet,
        )
    except TranscriptionError as exc:
        _error_exit("retranscribe", f"Transcription failed: {exc}")

    # Write transcript inside the discovered project (walk=True) so
    # retranscribe-from-subdir lands in the right place, not in cwd.
    transcript_path = save_transcript(transcript, source_id, walk=True)

    # Update project.json's source.transcript metadata so subsequent
    # status / load output reports the new model rather than a stale
    # value from the original load.
    source["transcript"] = {
        # Same shape create_workspace writes (issue #42 friction agent
        # caught the divergence): `model` is what status / inspect
        # surface; `source` is the legacy field kept for back-compat.
        "model": model,
        "source": f"whisper:{model}",
        "path": f"{TRANSCRIPTS_DIR}/{source_id}.json",
    }
    # Clear any prior --no-transcribe / no-audio reason — there's a
    # transcript now.
    source.pop("transcription_skipped_reason", None)
    save_project(project, walk=True)

    total_elapsed = time.time() - start_time
    if not quiet:
        click.echo(
            f"Retranscribed in {_format_elapsed(total_elapsed)}.", err=True
        )

    result = {
        "status": "retranscribed",
        "project_dir": str(get_project_dir()),
        "source": {
            "id": source_id,
            "path": source_path,
        },
        "transcript": {
            "model": model,
            "path": str(transcript_path),
            "word_count": len(transcript.get("words", [])),
            "duration": transcript.get("duration"),
            **(
                {"vocabulary": transcript["vocabulary"]}
                if transcript.get("vocabulary")
                else {}
            ),
        },
        "hint": (
            "Transcript replaced. Frames and spec are unchanged. Run "
            "'moviestar skim --words' to verify the new word-level "
            "timing, or 'moviestar status' to see the project at a "
            "glance."
        ),
    }
    _add_warnings(result, transcript.get("warnings") or [])
    click.echo(json.dumps(result, indent=2))


@cli.command()
@project_workspace_option
@click.option("--from", "from_tc", help="Start timecode of the range (default 0).")
@click.option("--to", "to_tc", help="End timecode of the range (default video duration).")
@click.option(
    "--count",
    type=int,
    default=10,
    help="Maximum number of thumbnails to return (default 10). "
    "Clamps to available_frames when fewer exist in the range.",
)
@click.option(
    "--words",
    is_flag=True,
    help="Include word-level timing in the transcript slice. Off by default "
    "to keep responses compact. If no transcript exists, the response "
    "carries words_unavailable_reason instead.",
)
@click.option(
    "--inline",
    "inline",
    is_flag=True,
    help="Embed base64 image bytes under image: {format, base64}. Only "
    "useful for frameworks that convert envelope bytes into image "
    "blocks; CLI harnesses should read the returned image files instead.",
)
@click.option(
    "--text-only",
    "text_only",
    is_flag=True,
    help=(
        "JSON transcript-only skim for long content. Omits thumbnails, "
        "frames_omitted, and image bytes entirely."
    ),
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "text"]),
    default="json",
    show_default=True,
    help="Output shape. 'json' (default) returns the full envelope. "
    "'text' emits one '[HH:MM:SS.mmm] segment_text' line per "
    "transcript segment for grep-driven clip discovery — thumbnails "
    "and metadata are omitted, --words has no effect (segments only).",
)
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID for multi-source projects. Optional in single-"
    "source projects (defaults to the only source).",
)
def skim(
    from_tc: str | None,
    to_tc: str | None,
    count: int,
    words: bool,
    inline: bool,
    text_only: bool,
    fmt: str,
    source_arg: str | None,
) -> None:
    """Fast sample of thumbnails + transcript across a range.

    Reads from the pre-built index created by 'moviestar load'. No FFmpeg
    or Whisper calls — milliseconds after load. Narrow the range with
    --from/--to, call again, narrow further. For dense scene-by-scene
    review at intervals finer than the pre-built index, use
    'moviestar inspect'.

    Transcript is segment-level by default (compact). Pass --words to
    include word-by-word timing when you need frame-exact trim points.

    Thumbnails are delivered as files: each entry carries a ``path``
    to view with your file reader. Pass --inline to additionally embed
    the bytes as base64 under ``image: {format, base64}`` (~70-200 KB
    extra per default skim) — only useful for frameworks that convert
    envelope bytes into image blocks.

    Pass ``--text-only`` to keep JSON output but omit thumbnail arrays
    entirely. This is the long-content escape hatch when transcript
    browsing is useful but image payloads would overwhelm the response.

    Sampling strategy: when ``available_frames > --count``, an
    evenly-spaced subsample is taken with the first and last
    indexed frames in the range always included (exception:
    ``--count 1`` returns the middle indexed frame, since the
    first/last rule can't apply with only one slot). Frames that
    were indexed-in-range but skipped are reported in the
    ``frames_omitted`` array (path + timecode, no image) so an agent
    looking for a specific timestamp knows whether it was
    subsampled out or never indexed. The invariant
    ``available_frames == thumbnails_returned + len(frames_omitted)``
    always holds.
    """
    if not is_loaded():
        _no_project_error_exit("skim")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("skim", f"Could not read project.json: {exc}")

    source_id = _resolve_source_arg(project, source_arg, "skim")
    source = _get_project_source(project, source_id)
    duration_seconds = float(source["duration"]["seconds"])
    frame_interval = float(source["frame_interval"])

    # Parse range
    try:
        from_s = parse_timecode(from_tc) if from_tc else 0.0
        to_s = parse_timecode(to_tc) if to_tc else duration_seconds
    except ValueError as exc:
        _error_exit("skim", str(exc))

    # Validate range
    if from_s >= to_s:
        _error_exit(
            "skim",
            f"--from ({from_s}s) must be before --to ({to_s}s)",
        )
    if from_s < 0 or to_s > duration_seconds + 0.5:
        _error_exit(
            "skim",
            f"Range {from_s}s-{to_s}s exceeds video duration of {duration_seconds}s",
        )

    if text_only:
        frames_in_range: list[dict] = []
        thumbnails: list[dict] = []
        frames_omitted: list[dict] = []
    else:
        # Build thumbnail list
        all_frames = list_frames(source)
        frames_in_range = filter_frames_by_range(all_frames, from_s, to_s)
        thumbnails = subsample_frames(frames_in_range, count)

        # Issue #45: surface which indexed frames were dropped during
        # subsampling so agents looking for a specific timecode know
        # whether it was indexed-but-skipped or never indexed at all.
        # `path`+`timecode` only — no image bytes (frames_omitted is
        # about the dropped *list*, not the dropped *content*).
        sampled_paths = {t["path"] for t in thumbnails}
        frames_omitted = [
            {"path": f["path"], "timecode": f["timecode"]}
            for f in frames_in_range
            if f["path"] not in sampled_paths
        ]

        # Paths-first (issue #319, flipping issue #35): embedding is
        # opt-in because CLI harnesses ingest stdout as text — the
        # bytes are never pixels there, just JSON bloat (default 10
        # thumbnails ≈ 70-200 KB extra).
        if inline:
            for thumb in thumbnails:
                thumb["image"] = _read_image_inline(thumb["path"])

    transcript_out, skip_reason, words_unavailable_reason = (
        _slice_transcript_for_response(source, from_s, to_s, words)
    )

    # Build a context-aware hint. Three cases:
    #   1. Zero thumbnails — range fell between indexed frames. Tell the user
    #      to widen, use --words for precise text timing, or re-load finer.
    #   2. Range extends past last indexed frame — mention the gap explicitly.
    #   3. Healthy — standard narrowing hint.
    last_indexed = frames_in_range[-1]["timecode"]["seconds"] if frames_in_range else None

    # Issue #27: hint reflects post-action state. Three geometry
    # branches as before (zero-thumbnails / past-end-of-range /
    # healthy), now layered with transcript availability so we don't
    # invite agents to run --words against a project that has no
    # transcript to slice.
    has_transcript = transcript_out is not None
    frames_skip_reason = source.get("frame_extraction_skipped_reason")

    if text_only:
        hint = (
            "Text-only skim: transcript returned without thumbnails. "
            "Narrow the range with --from and --to, or drop --text-only "
            "when visual thumbnails are needed."
        )
    elif frames_skip_reason:
        # Issue #153: frames were skipped at load, so the empty
        # thumbnail list isn't a range-geometry problem — don't send
        # the agent off tuning --interval.
        hint = (
            "No thumbnails: frames were skipped at load (--no-frames). "
            "Re-run 'moviestar load --force' without --no-frames to "
            "extract thumbnails, or use 'moviestar inspect' / "
            "'moviestar screenshot' for on-demand frames."
        )
        if has_transcript:
            hint += " Transcript data is still available."
    elif len(thumbnails) == 0:
        hint = (
            f"No indexed frames in this range (frame_interval is {frame_interval}s). "
            f"Widen the range or reload with a finer --interval "
            f"(e.g. 'moviestar load --force --interval {frame_interval / 2}')."
        )
        if has_transcript:
            hint += " Transcript data is still available at this granularity."
    elif last_indexed is not None and (to_s - last_indexed) > frame_interval:
        hint = (
            f"Range ends at {to_s:.1f}s but last indexed frame is at {last_indexed:.1f}s "
            f"(frame_interval {frame_interval}s). "
            "For frames closer to the boundary, reload with a finer --interval."
        )
    elif has_transcript:
        hint = (
            "Narrow the range with --from and --to. Pass --words for word-level "
            "timing. For dense frame-by-frame review, run 'moviestar inspect'."
        )
    else:
        # No transcript: don't mention --words (won't work — we'd be
        # pointing at a flag that surfaces words_unavailable_reason
        # rather than doing anything). Point at the actual fix.
        hint = (
            "Narrow the range with --from and --to. For dense frame-by-frame "
            "review, run 'moviestar inspect'. For a silent recording, run "
            "'moviestar activity' to measure visual change and find candidate "
            "active/idle ranges. No transcript loaded — run 'moviestar load "
            "--force' without --no-transcribe to enable word-level timing."
        )

    if fmt == "text":
        _emit_transcript_text("skim", transcript_out, skip_reason)
        return

    result = {
        "range": {
            "from": format_timecode(from_s),
            "to": format_timecode(to_s),
            "duration": format_timecode(to_s - from_s),
        },
        "source": {
            "id": source["id"],
            "path": source["path"],
            "frame_interval": frame_interval,
            "frames_total": source.get("frames_extracted"),
            # Index width is fixed at load time (--thumb-width); for
            # wider on-demand frames use inspect --width (issue #327).
            "thumbnail_width": source.get("thumb_width", DEFAULT_THUMB_WIDTH),
        },
        "transcript": transcript_out,
        "hint": hint,
    }
    if text_only:
        result["text_only"] = True
    else:
        result.update(
            {
                "thumbnails": thumbnails,
                "available_frames": len(frames_in_range),
                "thumbnails_returned": len(thumbnails),
                "frames_omitted": frames_omitted,
            }
        )
    if frames_skip_reason:
        result["frame_extraction_skipped_reason"] = frames_skip_reason
    if transcript_out is None and skip_reason:
        result["transcription_skipped_reason"] = skip_reason
    if words_unavailable_reason:
        result["words_unavailable_reason"] = words_unavailable_reason

    click.echo(json.dumps(result, indent=2))


ACTIVITY_DEFAULT_INTERVAL_SECONDS = 0.25
# 0.5% of full-scale mean luma: low enough to retain UI typing/toasts after
# downscaling, while exact static/held frames remain at zero. The raw score is
# always surfaced so callers can tune this rather than trusting a hidden rule.
ACTIVITY_DEFAULT_THRESHOLD = 0.005
ACTIVITY_DEFAULT_MERGE_GAP_SECONDS = 0.5
ACTIVITY_MAX_SAMPLES = 5000


def _activity_source_segments(
    timeline, from_s: float, to_s: float
) -> list[dict]:
    """Return analyzed result/source segment overlaps for the envelope."""
    result: list[dict] = []
    cursor = 0.0
    for source_from, source_to in timeline.source_segments:
        segment_duration = source_to - source_from
        segment_result_to = cursor + segment_duration
        overlap_from = max(from_s, cursor)
        overlap_to = min(to_s, segment_result_to)
        if overlap_to > overlap_from + 0.000001:
            overlap_source_from = source_from + (overlap_from - cursor)
            overlap_source_to = source_from + (overlap_to - cursor)
            duration = overlap_to - overlap_from
            result.append({
                "result_range": {
                    "from": format_timecode(overlap_from),
                    "to": format_timecode(overlap_to),
                    "duration": format_timecode(duration),
                },
                "source_range": {
                    "from": format_timecode(overlap_source_from),
                    "to": format_timecode(overlap_source_to),
                    "duration": format_timecode(duration),
                },
            })
        cursor = segment_result_to
    return result


def _activity_media_error(message: str) -> None:
    click.echo(json.dumps({
        "error": message,
        "command": "activity",
        "code": "activity_no_video_stream",
        "hint": "Choose a loaded source with a video stream. Run "
        "'moviestar status' to list project sources and their media metadata.",
    }, indent=2))
    sys.exit(1)


@cli.command()
@project_workspace_option
@click.option(
    "--from", "from_tc", default=None,
    help="Result-time start of the analyzed range (default 0).",
)
@click.option(
    "--to", "to_tc", default=None,
    help="Result-time end of the analyzed range (default edited duration).",
)
@click.option(
    "--interval", type=float, default=ACTIVITY_DEFAULT_INTERVAL_SECONDS,
    show_default=True,
    help="Seconds between visual evidence samples. Smaller intervals decode "
    "more frames and localize changes more tightly.",
)
@click.option(
    "--threshold", type=float, default=ACTIVITY_DEFAULT_THRESHOLD,
    show_default=True,
    help="Normalized mean absolute luma difference (0..1) at or above which "
    "a comparison is active.",
)
@click.option(
    "--merge-gap", type=float, default=ACTIVITY_DEFAULT_MERGE_GAP_SECONDS,
    show_default=True,
    help="Coalesce active evidence windows separated by at most this many "
    "idle seconds. Bridged time remains explicit in range evidence.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Validate and report the exact frame/pair count and settings without "
    "running FFmpeg/ffprobe or decoding pixels.",
)
@click.option(
    "--source", "source_arg", default=None,
    help="Source ID. Optional in single-source projects; required in "
    "multi-source projects. The chosen source's edited result is analyzed.",
)
def activity(
    from_tc: str | None,
    to_tc: str | None,
    interval: float,
    threshold: float,
    merge_gap: float,
    dry_run: bool,
    source_arg: str | None,
) -> None:
    """Report deterministic visual change and candidate active/idle ranges.

    This read-only surface is designed for silent screen recordings where
    transcript search has no signal. It samples a small grayscale raster,
    reports per-sample mean absolute luma change, and coalesces comparisons
    over ``--threshold`` into ``active_ranges``. The complementary
    ``idle_ranges`` are evidence-backed candidates too; neither label decides
    what is editorially important and this command never changes the spec.

    ``--from`` and ``--to`` use the chosen source's edited result-time clock.
    Every sample also reports its exact source-time mapping, including jumps
    across cuts. ``source_segments`` makes the two clocks explicit up front.

    VFR/static spans are not disguised as fresh evidence: each sample reports
    the decoded frame's source timestamp and whether it is a held frame.
    ``no_new_frame_ranges`` coalesces intervals with no newly decoded frame.
    Repeated decoded frames whose pixels are unchanged remain distinguishable
    from VFR holds.

    Use ``--dry-run`` first on wide/fine analyses. It returns
    ``would_analyze_frames_count`` and ``would_compare_frame_pairs_count``
    without extraction. Real analysis requires only FFmpeg/ffprobe; it does
    not require a transcript, Whisper, OCR, NumPy, or a perception model.
    """
    if not is_loaded():
        _no_project_error_exit("activity")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit_with_hint(
            "activity",
            f"Could not read project.json: {exc}",
            "Run 'moviestar status' to inspect the workspace, or reload the source.",
        )

    source_id = _resolve_source_arg(project, source_arg, "activity")
    source = _get_project_source(project, source_id)
    width = source.get("width")
    height = source.get("height")
    if not width or not height:
        _activity_media_error(
            f"Source {source_id!r} has no supported video stream metadata."
        )

    source_duration = float(source["duration"]["seconds"])
    try:
        spec = _load_or_init_spec(project)
        timeline = resolve_source(spec, source_id, source_duration)
    except SpecValidationError as exc:
        _error_exit_with_hint(
            "activity", str(exc),
            "Run 'moviestar status' to inspect the current edit state.",
        )

    try:
        from_s = parse_timecode(from_tc) if from_tc is not None else 0.0
        to_s = (
            parse_timecode(to_tc)
            if to_tc is not None else float(timeline.effective_duration)
        )
    except ValueError as exc:
        _error_exit_with_hint(
            "activity", str(exc),
            "Pass seconds, M:SS, or HH:MM:SS.mmm to --from and --to.",
        )

    if not math.isfinite(from_s) or not math.isfinite(to_s):
        _error_exit_with_hint(
            "activity", "--from and --to must be finite timecodes.",
            "Pass seconds, M:SS, or HH:MM:SS.mmm; NaN and infinity are not valid.",
        )
    if from_s >= to_s:
        _error_exit_with_hint(
            "activity",
            f"--from ({from_s}s) must be before --to ({to_s}s).",
            "Choose a non-empty result-time range.",
        )
    if from_s < 0 or to_s > timeline.effective_duration + 0.001:
        _error_exit_with_hint(
            "activity",
            f"Range {from_s}s-{to_s}s exceeds the edited result duration "
            f"of {timeline.effective_duration}s.",
            "Run 'moviestar status' for the current result range, then choose "
            "--from/--to within it.",
        )
    if not math.isfinite(interval) or interval <= 0:
        _error_exit_with_hint(
            "activity", "--interval must be a finite value greater than 0 seconds.",
            "Use a positive interval such as --interval 0.25.",
        )
    if not math.isfinite(threshold) or threshold < 0 or threshold > 1:
        _error_exit_with_hint(
            "activity", "--threshold must be a finite value between 0 and 1.",
            "Use the default 0.005, lower it for subtler changes, or raise it "
            "to suppress small changes.",
        )
    if not math.isfinite(merge_gap) or merge_gap < 0:
        _error_exit_with_hint(
            "activity", "--merge-gap must be finite and cannot be negative.",
            "Use 0 to disable gap bridging or a positive duration such as 0.5.",
        )

    range_duration = to_s - from_s
    sample_count = math.ceil(round(range_duration / interval, 9))
    if sample_count < 2:
        _error_exit_with_hint(
            "activity",
            f"--interval ({interval}s) yields only {sample_count} sample for "
            f"this {range_duration:.3f}s range; visual change needs two.",
            f"Lower --interval below {range_duration:.3f}s or widen the range.",
        )
    if sample_count > ACTIVITY_MAX_SAMPLES:
        minimum_interval = math.ceil(range_duration / ACTIVITY_MAX_SAMPLES * 1000) / 1000
        _error_exit_with_hint(
            "activity",
            f"This request would analyze {sample_count} frames; the per-call "
            f"limit is {ACTIVITY_MAX_SAMPLES}.",
            f"Raise --interval to at least {minimum_interval}, or narrow "
            "--from/--to. Use --dry-run to preview cost.",
        )

    sample_plan = []
    for index in range(sample_count):
        result_seconds = round(from_s + index * interval, 9)
        try:
            source_seconds = result_to_source_time(
                timeline.source_segments, result_seconds
            )
        except SpecValidationError as exc:
            _error_exit_with_hint(
                "activity", str(exc),
                "Narrow the range to the current edited result.",
            )
        sample_plan.append({
            "result_seconds": result_seconds,
            "source_seconds": source_seconds,
        })

    source_segments = _activity_source_segments(timeline, from_s, to_s)
    base_result = {
        "range": {
            "clock": "result",
            "from": format_timecode(from_s),
            "to": format_timecode(to_s),
            "duration": format_timecode(range_duration),
        },
        "source": {"id": source_id, "path": source["path"]},
        "source_segments": source_segments,
        "evidence_clock": {
            "sample_clock": "result",
            "mapping": "Each sample carries result and source time; decoded "
            "frame timestamps are source-time.",
        },
        "settings": {
            "interval_seconds": interval,
            "active_threshold": threshold,
            "merge_gap_seconds": merge_gap,
            "metric": "mean_absolute_luma_difference",
            "metric_range": [0.0, 1.0],
            "changed_pixel_min_luma_delta": ACTIVITY_PIXEL_DELTA,
            "analysis_width": ACTIVITY_ANALYSIS_WIDTH,
        },
    }

    if dry_run:
        result = {
            "dry_run": True,
            "status": "would_analyze",
            "would_analyze_frames_count": sample_count,
            "would_compare_frame_pairs_count": sample_count - 1,
            **base_result,
            "hint": "Dry-run only — no frames decoded. Re-run without "
            "--dry-run to measure visual change.",
        }
        click.echo(json.dumps(result, indent=2))
        return

    try:
        analysis = analyze_visual_activity(
            source["path"],
            source_width=int(width),
            source_height=int(height),
            samples=sample_plan,
            interval=interval,
            threshold=threshold,
            merge_gap=merge_gap,
            range_from=from_s,
            range_to=to_s,
        )
    except (FFmpegNotFoundError, FileNotFoundError, RuntimeError, ValueError) as exc:
        _error_exit_with_hint(
            "activity", f"Visual activity analysis failed: {exc}",
            "Verify the source path and FFmpeg installation, or run "
            "'moviestar activity --dry-run' to validate the request without decoding.",
        )

    active_ranges = analysis["active_ranges"]
    hint = (
        "Use active_ranges as evidence-backed candidates, then inspect a "
        "candidate with 'moviestar inspect --from <start> --to <end>'. "
        "MovieStar has not decided which changes are editorially important."
        if active_ranges else
        "No comparisons reached the active threshold. Lower --threshold, "
        "sample more finely with --interval, or inspect frames directly; a "
        "static result screen may still be editorially important."
    )
    result = {"status": "analyzed", **base_result, **analysis, "hint": hint}
    if analysis["summary"]["held_sample_count"]:
        _add_warnings(result, [_warning(
            "activity_held_frames",
            "Some sample times reused the same decoded VFR frame; see "
            "no_new_frame_ranges and each sample's frame_state.",
            held_sample_count=analysis["summary"]["held_sample_count"],
        )])
    click.echo(json.dumps(result, indent=2))


INSPECT_RANGE_CAP_SECONDS = 120.0
INSPECT_MIN_INTERVAL_SECONDS = 0.01
INSPECT_DEFAULT_INTERVAL_SECONDS = 0.5
INSPECT_SCALE_WIDTH = 640


@cli.command()
@project_workspace_option
@click.option("--from", "from_tc", required=True, help="Start timecode of the range.")
@click.option("--to", "to_tc", required=True, help="End timecode of the range.")
@click.option(
    "--interval",
    type=float,
    default=INSPECT_DEFAULT_INTERVAL_SECONDS,
    help=f"Seconds between extracted frames (default "
    f"{INSPECT_DEFAULT_INTERVAL_SECONDS}). For scene compositions, sampling "
    "restarts at each scene so every overlapping scene gets a thumbnail.",
)
@click.option(
    "--width",
    "width",
    type=click.IntRange(64, 1920),
    default=INSPECT_SCALE_WIDTH,
    show_default=True,
    help="Thumbnail width in px. Raise it (e.g. 960, 1280) to keep UI "
    "text legible when reviewing screen recordings; wider than the "
    "source upscales without adding detail. Each width gets its own "
    "extraction cache. Not supported for scene compositions.",
)
@click.option(
    "--words",
    is_flag=True,
    help="Include word-level timing in the transcript slice (same policy as "
    "skim). If no transcript exists, the response carries "
    "words_unavailable_reason instead.",
)
@click.option(
    "--inline",
    "inline",
    is_flag=True,
    help="Embed base64 image bytes under image: {format, base64}. Only "
    "useful for frameworks that convert envelope bytes into image "
    "blocks; CLI harnesses should read the returned image files instead.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "text"]),
    default="json",
    show_default=True,
    help="Output shape. 'json' (default) returns the full envelope. "
    "'text' emits one '[HH:MM:SS.mmm] segment_text' line per "
    "transcript segment for grep-driven clip discovery — thumbnails "
    "and metadata are dropped, --interval/--inline/--words have "
    "no effect (segments only). Mirrors 'skim --format text'.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Validate everything the real call would (project loaded, "
    "range parses + within source, range cap, interval-vs-range) "
    "but skip running ffmpeg and writing frames. Returns the same "
    "envelope with status: 'would_extract', dry_run: true, "
    "would_write_to (deterministic frames subdir), "
    "would_reuse_cache (true if a real call would hit cache, false "
    "if it would extract fresh — detected by subdir-existence), "
    "and would_extract_frames_count. --inline has no effect "
    "(no frames to embed).",
)
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID for multi-source projects. Optional in single-"
    "source projects (defaults to the only source).",
)
def inspect(
    from_tc: str,
    to_tc: str,
    interval: float,
    width: int,
    words: bool,
    inline: bool,
    fmt: str,
    dry_run: bool,
    source_arg: str | None,
) -> None:
    """Dense thumbnails + transcript for a narrow range, extracted on demand.

    Unlike skim (which reads the pre-built frame index), inspect extracts
    fresh frames at a finer interval — the escape hatch when skim's
    cached frames aren't dense enough. Seconds, not milliseconds.

    Range is capped at 120 seconds — at the default 0.5s interval that's
    already 240 frames per call without scene-boundary resets. Scene
    compositions may return more. Wider overviews are skim's job.

    Without scenes, thumbnails land at from_s, from_s+interval, ...,
    stopping strictly before to_s. So a 10s range at 0.5s interval
    returns 20 frames (5.0, 5.5, ..., 14.5), not 21 — the upper bound
    is exclusive.

    For scene compositions, sampling restarts at each overlapping scene:
    one thumbnail at the beginning of the scene's requested window, then
    another every interval seconds until that scene ends. Short scenes
    therefore still get at least one thumbnail. Each thumbnail identifies
    its scene, scene-local time, and whether it is the window anchor or a
    later interval sample. Scene-aware counts may exceed the composition-
    wide interval count.

    Thumbnails are delivered as files: each entry carries a ``path``
    to view with your file reader. Pass --inline to additionally embed
    the bytes as base64 under ``image: {format, base64}`` — with dense
    intervals over a wide range that JSON grows large (~1.6 MB for 240
    thumbnails), so it's only useful for frameworks that convert
    envelope bytes into image blocks.

    With a saved layout or scene composition and no --source, timecodes
    are composition result-time and inspect renders the composed layout
    thumbnails. Scene compositions return the active scene for each
    thumbnail.

    With ``--format text`` (mirroring skim's), inspect
    emits one ``[HH:MM:SS.mmm] segment_text`` line per transcript
    segment and skips frame extraction entirely — useful for piping
    transcript slices to grep / awk on a narrow window without paying
    for the dense frame extract. A segment is included only when its
    full span sits inside ``[--from, --to]`` (i.e. ``segment.start >=
    from`` AND ``segment.end <= to``); a segment that straddles a
    boundary is excluded. So a 0-2.9s window over a single 0-3.0s
    segment returns zero lines — widen ``--to`` to 3.0 to capture it.

    With ``--dry-run``, inspect validates everything
    a real call would (project loaded, range parses + within source +
    under the 120s cap, and the interval-vs-range guardrail where it
    applies) and emits the
    same envelope shape with ``dry_run: true``, ``status:
    "would_extract"``, top-level ``would_write_to`` (the deterministic
    frames subdir), ``would_reuse_cache`` (true if a real call would
    hit cache, false if it would extract fresh — detected by
    subdir-existence + 1+ frames, the same signal the real call
    uses), and ``would_extract_frames_count`` (the exact selected count;
    for scenes this is the sum of the per-scene counts). Drops ``thumbnails``,
    ``thumbnails_returned`` (don't exist until extraction); drops
    ``extraction.frames_dir`` and ``extraction.cached`` (now top-
    level ``would_*`` fields). ``--inline`` is a no-op under
    ``--dry-run`` (no frames to embed). Dry-run keeps the same
    envelope shape as the real call; hypothetical values arrive in
    ``would_*``-prefixed fields and nothing is written.
    """
    paths_only = not inline
    if not is_loaded():
        _no_project_error_exit("inspect")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("inspect", f"Could not read project.json: {exc}")

    # Parse range
    try:
        from_s = parse_timecode(from_tc)
        to_s = parse_timecode(to_tc)
    except ValueError as exc:
        _error_exit("inspect", str(exc))

    if from_s >= to_s:
        _error_exit(
            "inspect",
            f"--from ({from_s}s) must be before --to ({to_s}s)",
        )

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("inspect", str(exc))

    composition = spec.get("composition")
    if source_arg is None and _is_layout_composition(composition):
        if _is_scene_layout_composition(composition):
            if width != INSPECT_SCALE_WIDTH:
                # Issue #327: scene inspect renders composed canvases
                # at a fixed width — refuse rather than silently
                # ignore the flag.
                _error_exit_with_hint(
                    "inspect",
                    "--width is not supported for scene compositions; "
                    "scene inspect renders composed canvases at a "
                    "fixed width.",
                    "Drop --width, or use 'moviestar screenshot --at "
                    "<tc>' for a full-resolution composed frame.",
                )
            _inspect_scene_layout_composition(
                project=project,
                spec=spec,
                composition=composition,
                from_s=from_s,
                to_s=to_s,
                interval=interval,
                width=width,
                paths_only=paths_only,
                fmt=fmt,
                dry_run=dry_run,
            )
            return

    source_id = _resolve_source_arg(project, source_arg, "inspect")
    source = _get_project_source(project, source_id)
    duration_seconds = float(source["duration"]["seconds"])
    source_path = source["path"]

    # Cap first (independent of source duration) — even if the source were 10h,
    # we wouldn't extract 1000+ frames in one call. Error message echoes the
    # rationale from --help so agents that only see errors learn the why too.
    range_duration = to_s - from_s
    if range_duration > INSPECT_RANGE_CAP_SECONDS:
        # 3-decimal precision avoids the self-contradictory "120.0s exceeds
        # 120s cap" display when the user passed e.g. 120.001s.
        _error_exit_with_hint(
            "inspect",
            (
                f"Range duration ({range_duration:.3f}s) exceeds inspect's "
                f"{INSPECT_RANGE_CAP_SECONDS:.0f}s cap (240 frames at the "
                f"default {INSPECT_DEFAULT_INTERVAL_SECONDS}s interval)."
            ),
            "Use 'moviestar skim' for wider overviews, then narrow with "
            "--from/--to before running inspect.",
        )

    if from_s < 0 or to_s > duration_seconds + 0.5:
        _error_exit(
            "inspect",
            f"Range {from_s}s-{to_s}s exceeds video duration of {duration_seconds}s",
        )

    # Issue #69: text mode emits transcript-only output. Short-circuit
    # before --interval validation and the (expensive) dense frame
    # extraction — neither is used by the text shape. Range validation
    # (above) still applies so an out-of-bounds range surfaces the
    # same JSON error envelope an agent gets in JSON mode. The 120s
    # cap is conceptual ("inspect = narrow"), so it stays in force
    # for both modes.
    if fmt == "text":
        transcript_out, skip_reason, _ = _slice_transcript_for_response(
            source, from_s, to_s, words
        )
        _emit_transcript_text("inspect", transcript_out, skip_reason)
        return

    # Validate interval
    if interval < INSPECT_MIN_INTERVAL_SECONDS:
        _error_exit(
            "inspect",
            f"--interval must be at least {INSPECT_MIN_INTERVAL_SECONDS} seconds",
        )

    # Guardrail: interval too coarse for the requested range. ffmpeg's
    # fps=1/interval filter yields the first frame at t=0 and the next at
    # t=interval — if the range is shorter, you only get a single frame,
    # which surprises users ("--from 20 --to 20.5 --interval 0.5 = 1 frame").
    # Refuse and suggest a concrete interval.
    if interval >= range_duration:
        suggested = round(range_duration / 4, 3) or INSPECT_MIN_INTERVAL_SECONDS
        _error_exit_with_hint(
            "inspect",
            (
                f"--interval ({interval}s) is >= range duration "
                f"({range_duration:.3f}s); this would yield only 1 thumbnail."
            ),
            f"Lower --interval (try --interval {suggested}) or widen the "
            "range with --from/--to.",
        )

    # Deterministic output path — repeat calls with identical args land here.
    # Round-to-ms in the subdir name so "20.0" and "20" hit the same cache
    # and so float noise like "43.300000000000004" doesn't leak into paths.
    inspect_root = get_project_dir() / INSPECT_FRAMES_DIR
    subdir_name = (
        f"{source_id}_{round(from_s, 3)}_{round(to_s, 3)}_"
        f"{round(interval, 3)}_{width}"
    )
    frames_out = inspect_root / subdir_name

    # Frame count for the range. ffmpeg's `fps=1/interval` filter
    # samples at t=0, interval, 2*interval, ... while t < range_duration
    # (the upper bound is exclusive, per the docstring). math.ceil(D/I)
    # produces the right count for both the aligned case (D == k*I →
    # k frames at 0, I, ..., (k-1)*I) and the unaligned case
    # (k = ceil(D/I) frames at 0, I, ..., (k-1)*I < D). The round()
    # absorbs IEEE-754 fuzz from divisions like 1.5/0.5 → 2.999999.
    # Issue #36 stage-2 friction-test follow-up: pre-fix formula was
    # int(D/I) + 1 which over-counted by 1 in the aligned case (e.g.
    # claimed 4 frames for the 1.5/0.5 case where the real call
    # extracts 3).
    expected_frame_count = math.ceil(round(range_duration / interval, 9))
    cached = (
        frames_out.exists()
        and len(sorted(frames_out.glob(f"{source_id}_inspect_*.jpg"))) >= 1
    )

    # Issue #36 stage 2: dry-run forks at the side-effect boundary.
    # Validation, cache detection (subdir-existence + 1+ frames), and
    # frame-count prediction all run identically. Below, dry-run
    # builds the would-be ffmpeg argv via the pure builder, slices
    # the transcript (pure read), and emits the envelope without
    # invoking ffmpeg or creating the frames_out subdir.
    if dry_run:
        # Pure ffmpeg argv — same args the real call would use.
        # `output_pattern` mirrors what extract_frames builds internally
        # so the agent sees the actual would-be argv, not a placeholder.
        output_pattern = str(frames_out / f"{source_id}_inspect_%04d.jpg")
        ffmpeg_cmd = build_extract_frames_command(
            source_path,
            interval,
            output_pattern,
            scale_width=width,
            start_time=from_s,
            duration=range_duration,
        )
        transcript_out, skip_reason, words_unavailable_reason = (
            _slice_transcript_for_response(source, from_s, to_s, words)
        )
        boundary_exclusions = _inspect_boundary_exclusions(source, from_s, to_s)
        hint = (
            "Dry-run only — no frames extracted. Re-run without "
            "--dry-run to extract."
            + (
                " A real call would hit cache and return in ~80ms "
                f"(would_write_to: {frames_out.name}/)."
                if cached
                else f" A real call would extract "
                f"{expected_frame_count} frames "
                f"(would_write_to: {frames_out.name}/)."
            )
        )
        if transcript_out is None:
            hint += (
                " No transcript is loaded; run 'moviestar activity' to "
                "measure visual change and find candidate active/idle ranges."
            )
        result: dict = {
            "dry_run": True,
            "status": "would_extract",
            "would_write_to": str(frames_out.resolve()),
            "would_reuse_cache": cached,
            "would_extract_frames_count": expected_frame_count,
            "range": {
                "from": format_timecode(from_s),
                "to": format_timecode(to_s),
                "duration": format_timecode(range_duration),
            },
            "source": {
                "id": source_id,
                "path": source_path,
            },
            "extraction": {
                "interval_seconds": interval,
                "scale_width": width,
            },
            "ffmpeg_command": ffmpeg_cmd,
            "transcript": transcript_out,
            "hint": _append_boundary_exclusion_hint(hint, boundary_exclusions),
        }
        if boundary_exclusions:
            result["segments_excluded_at_boundaries"] = boundary_exclusions
        if transcript_out is None and skip_reason:
            result["transcription_skipped_reason"] = skip_reason
        if words_unavailable_reason:
            result["words_unavailable_reason"] = words_unavailable_reason
        click.echo(json.dumps(result, indent=2))
        return

    if cached:
        # Idempotent cache hit: reuse existing frames, skip ffmpeg entirely.
        frame_paths = sorted(frames_out.glob(f"{source_id}_inspect_*.jpg"))
    else:
        # Fresh extraction — wipe any partial prior dir first.
        if frames_out.exists():
            import shutil as _shutil
            _shutil.rmtree(frames_out)
        try:
            frame_paths, _argv = extract_frames(
                source_path,
                interval,
                frames_out,
                source_id=f"{source_id}_inspect",
                scale_width=width,
                start_time=from_s,
                duration=range_duration,
            )
        except FFmpegNotFoundError as exc:
            _error_exit("inspect", str(exc))
        except FileNotFoundError as exc:
            _error_exit("inspect", str(exc))
        except RuntimeError as exc:
            _error_exit("inspect", f"Frame extraction failed: {exc}")

    # Frame N (1-indexed) → from_s + (N-1) * interval
    thumbnails = [
        {
            "path": str(p.resolve()),
            "timecode": format_timecode(from_s + i * interval),
        }
        for i, p in enumerate(frame_paths)
    ]

    # Embedding is opt-in via --inline (issue #319, flipping #35).
    if not paths_only:
        for thumb in thumbnails:
            thumb["image"] = _read_image_inline(thumb["path"])

    transcript_out, skip_reason, words_unavailable_reason = (
        _slice_transcript_for_response(source, from_s, to_s, words)
    )
    boundary_exclusions = _inspect_boundary_exclusions(source, from_s, to_s)

    if cached:
        hint = (
            f"Cache hit — reused frames from {frames_out.name}/. "
            "Pass --words for word-level timing. "
            "Use 'moviestar watch' to extract the actual video segment."
        )
    else:
        hint = (
            "Pass --words for word-level timing on tight trim decisions. "
            "Use 'moviestar watch --from <X> --to <Y>' to extract the "
            "actual video segment for multimodal analysis."
        )
    if transcript_out is None:
        hint += (
            " No transcript is loaded; run 'moviestar activity' to measure "
            "visual change and find candidate active/idle ranges."
        )

    result = {
        # Issue #36 stage 2: brought in line with the rest of the
        # status-bearing envelopes (export, watch, spec --edit /
        # --reset, load, retranscribe) so dry-run's would_extract
        # pattern-matches against extracted cleanly.
        "status": "extracted",
        "range": {
            "from": format_timecode(from_s),
            "to": format_timecode(to_s),
            "duration": format_timecode(range_duration),
        },
        "source": {
            "id": source_id,
            "path": source_path,
        },
        "extraction": {
            "interval_seconds": interval,
            "scale_width": width,
            "frames_dir": str(frames_out.resolve()),
            "cached": cached,
        },
        "thumbnails": thumbnails,
        "thumbnails_returned": len(thumbnails),
        "transcript": transcript_out,
        "hint": _append_boundary_exclusion_hint(hint, boundary_exclusions),
    }
    if boundary_exclusions:
        result["segments_excluded_at_boundaries"] = boundary_exclusions
    if transcript_out is None and skip_reason:
        result["transcription_skipped_reason"] = skip_reason
    if words_unavailable_reason:
        result["words_unavailable_reason"] = words_unavailable_reason

    click.echo(json.dumps(result, indent=2))



@cli.command()
@project_workspace_option
@click.option("--from", "from_tc", required=True, help="Start timecode of the clip.")
@click.option("--to", "to_tc", required=True, help="End timecode of the clip.")
@click.option(
    "--out",
    "output",
    default=None,
    help="Output MP4 path. Auto-named 'watch_<from>s_<to>s.mp4' in cwd if "
    "omitted (e.g. 'watch_5.0s_15.0s.mp4'); suffixes _2, _3, ... on repeat "
    "invocations rather than overwriting. Explicit --out paths overwrite "
    "via ffmpeg's -y.",
)
@click.option(
    "--precise",
    is_flag=True,
    help="Re-encode for frame-accurate boundaries (slower but exact).",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Validate everything the real call would (project loaded, "
    "range parses + within source, output path resolves) but skip "
    "running ffmpeg and writing the output file. Returns the same "
    "envelope with status: 'would_extract', dry_run: true, "
    "would_extract_to (the resolved output path *after* auto-suffix "
    "resolution, so it matches what the real call would use), and "
    "ffmpeg_command.",
)
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID for multi-source projects. Optional in single-"
    "source projects (defaults to the only source).",
)
@click.option(
    "--loudness-report",
    "loudness_report",
    is_flag=True,
    default=False,
    help="Measure integrated LUFS and true peak for every routed audio layer "
    "plus the final mix over the requested --from/--to window. Uses the "
    "authored watch mix and reports coverage relative to that window.",
)
def watch(
    from_tc: str,
    to_tc: str,
    output: str | None,
    precise: bool,
    dry_run: bool,
    source_arg: str | None,
    loudness_report: bool,
) -> None:
    """Extract a video segment from the loaded project as MP4.

    Use this to hand an actual video clip to a multimodal model for
    motion/audio analysis that thumbnails can't capture. Different from
    'export' — watch is an *analysis* primitive; export is the final
    render of the edit spec.

    With a saved layout or scene composition and no --source, timecodes
    are composition result-time and watch renders the composed layout.
    Scene compositions may cross layout changes inside one watch range.
    With --source, timecodes are source-time — the original file's
    clock — and that source's edit spec is ignored and left untouched,
    same contract as 'clip' (the deliverable-framed sibling on the
    same extraction engine).

    Default is stream-copy (fast, ±keyframe alignment). Pass --precise
    for frame-accurate re-encoding when exact boundaries matter.

    With ``--dry-run``, watch validates everything
    a real call would (project loaded, range parses + within source,
    output path resolves) and emits the same envelope shape with
    ``dry_run: true``, ``status: "would_extract"``, and
    ``would_extract_to`` (the resolved output path *after*
    auto-suffix resolution, so it matches what the real call would
    use). ``ffmpeg_command`` is included; ``actual_duration`` and
    ``file_size_bytes`` are dropped (post-execution measurements).
    Scene compositions use the same ``would_extract`` status and include
    a multi-step ``scene_render_commands`` + ``concat_command`` plan.
    """
    if not is_loaded():
        _no_project_error_exit("watch")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("watch", f"Could not read project.json: {exc}")

    try:
        from_s = parse_timecode(from_tc)
        to_s = parse_timecode(to_tc)
    except ValueError as exc:
        _error_exit("watch", str(exc))

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("watch", str(exc))

    if loudness_report and source_arg is not None:
        _error_exit_with_hint(
            "watch",
            "--loudness-report cannot be combined with --source because "
            "--source extracts raw source audio.",
            "Remove --source to measure the authored mix over the requested "
            "result-time window.",
        )

    composition = spec.get("composition")
    if source_arg is None and _is_layout_composition(composition):
        if _is_scene_layout_composition(composition):
            _watch_scene_layout_composition(
                project=project,
                spec=spec,
                composition=composition,
                from_s=from_s,
                to_s=to_s,
                output=output,
                preview=False,
                dry_run=dry_run,
                loudness_report=loudness_report,
            )
            return

    source_id = _resolve_source_arg(project, source_arg, "watch")
    source = _get_project_source(project, source_id)
    source_path = source["path"]
    duration_seconds = float(source["duration"]["seconds"])

    # Validate range
    if from_s >= to_s:
        _error_exit(
            "watch",
            f"--from ({from_s}s) must be before --to ({to_s}s)",
        )
    if from_s < 0 or to_s > duration_seconds + 0.5:
        _error_exit(
            "watch",
            f"Range {from_s}s-{to_s}s exceeds video duration of {duration_seconds}s",
        )

    range_duration = to_s - from_s
    resolved_audio_mix = None
    # Issue #381: the default single-source analysis path previews the
    # authored mix. An explicit --source keeps the documented raw-source
    # extraction contract shared with clip.
    if source_arg is None and composition is None:
        resolved_audio_mix = _resolve_project_audio_mix(
            "watch", spec, duration_seconds, get_project_dir()
        )

    # Resolve output path. Auto-name suffixes _2, _3, ... on collision
    # so iterating agents don't silently overwrite prior outputs (#25).
    # Explicit --out paths are honored as-given (and overwrite via -y).
    if output is None:
        base = f"watch_{round(from_s, 3)}s_{round(to_s, 3)}s.mp4"
        output = _resolve_unique_output_path(base)
    abs_output = os.path.realpath(output)

    output_parent = os.path.dirname(abs_output)
    if output_parent and not os.path.isdir(output_parent) and not dry_run:
        _error_exit(
            "watch",
            f"Cannot write to {abs_output}: parent directory does not exist",
        )

    mode = "precise" if precise else "stream-copy"
    note = None
    if not precise:
        note = (
            "stream-copy snaps to nearest keyframe; actual duration may differ "
            "slightly from requested. Use --precise for frame-exact extraction."
        )

    # Issue #36 stage 2: dry-run forks at the side-effect boundary.
    # Everything above (project loaded, range parsed + bounded, output
    # path resolved including #25 auto-suffix) ran
    # identically. Below is where the real call would invoke ffmpeg +
    # probe the output; dry-run instead builds the ffmpeg command via
    # the pure builder and emits the would-be envelope.
    if dry_run:
        ffmpeg_cmd = build_extract_clip_command(
            source_path,
            from_s,
            range_duration,
            abs_output,
            precise=precise,
        )
        result: dict = {
            "dry_run": True,
            "status": "would_extract",
            "would_extract_to": abs_output,
            "source": os.path.realpath(source_path),
            "range": {
                "requested": {
                    "from": format_timecode(from_s),
                    "to": format_timecode(to_s),
                    "duration": format_timecode(range_duration),
                },
            },
            "mode": mode,
            "precise": precise,
            "ffmpeg_command": ffmpeg_cmd,
            "hint": (
                "Dry-run only — no file written. Re-run without --dry-run to "
                "extract. The auto-suffixed would_extract_to path is what the "
                "real call would use; existing files at the unsuffixed path "
                "are not overwritten."
            ),
        }
        if not precise:
            # Dry-run-aware phrasing of the stream-copy keyframe-drift note.
            # The real call's note reads as a post-execution observation
            # ("actual duration may differ"); under dry-run that's a
            # prediction, so spell it as such.
            result["note"] = (
                "stream-copy snaps to nearest keyframe; on a real call, "
                "actual_duration may differ slightly from the requested "
                "range. Use --precise for frame-exact extraction."
            )
        if resolved_audio_mix is not None:
            _attach_audio_schema_envelope(
                result,
                spec=spec,
                result_duration=duration_seconds,
                command="watch",
                source_audio_available=bool(source.get("audio_codec")),
                window_start=from_s,
                window_duration=range_duration,
                resolved_audio_mix=resolved_audio_mix,
                loudness_report=loudness_report,
            )
        _apply_dry_run_setup(
            result,
            _dry_run_setup_commands_for_outputs([abs_output]),
        )
        click.echo(json.dumps(result, indent=2))
        return

    # Extract
    try:
        ffmpeg_cmd = extract_clip(
            source_path,
            from_s,
            range_duration,
            abs_output,
            precise=precise,
        )
    except FFmpegNotFoundError as exc:
        _error_exit("watch", str(exc))
    except FileNotFoundError as exc:
        _error_exit("watch", str(exc))
    except RuntimeError as exc:
        _error_exit("watch", f"Extraction failed: {exc}")

    # Probe the output for actual duration + file size
    try:
        out_probe = run_ffprobe(abs_output)
        actual_duration = float(out_probe.get("format", {}).get("duration", 0.0))
    except (RuntimeError, json.JSONDecodeError, FFmpegNotFoundError):
        actual_duration = 0.0

    result: dict = {
        # Issue #36 stage 2: brought in line with the rest of the
        # status-bearing envelopes (export, spec --edit / --reset,
        # load, retranscribe) so dry-run's would_extract pattern-
        # matches against extracted cleanly.
        "status": "extracted",
        "out": abs_output,
        "source": os.path.realpath(source_path),
        "range": {
            "requested": {
                "from": format_timecode(from_s),
                "to": format_timecode(to_s),
                "duration": format_timecode(range_duration),
            },
            "actual": {
                "duration": format_timecode(actual_duration),
            },
        },
        "mode": mode,
        "precise": precise,
        "file_size_bytes": os.path.getsize(abs_output),
        "ffmpeg_command": ffmpeg_cmd,
        "hint": (
            "Feed this file to a multimodal model for motion/audio analysis. "
            "For thumbnails + transcript of the same range without extracting "
            "video, use 'moviestar inspect --from <X> --to <Y>'."
        ),
    }
    if note:
        result["note"] = note
    if resolved_audio_mix is not None:
        _attach_audio_schema_envelope(
            result,
            spec=spec,
            result_duration=duration_seconds,
            command="watch",
            source_audio_available=bool(source.get("audio_codec")),
            window_start=from_s,
            window_duration=range_duration,
            resolved_audio_mix=resolved_audio_mix,
            loudness_report=loudness_report,
        )

    click.echo(json.dumps(result, indent=2))


def _scene_inspect_samples(
    render_plans: list[dict], interval: float
) -> list[dict]:
    """Build an inspect sampling schedule independently for each scene.

    Every overlapping scene contributes a sample at the beginning of its
    requested window, then another every ``interval`` seconds while the
    sample remains inside that half-open window. Restarting at each scene
    boundary guarantees coverage even when a short scene falls between the
    ticks of a composition-wide interval grid.
    """
    samples: list[dict] = []
    for render_plan in render_plans:
        scene_window = render_plan["scene_window"]
        overlap_from = scene_window["result_range"]["from"]["seconds"]
        overlap_duration = scene_window["result_range"]["duration"]["seconds"]
        local_from = scene_window["scene_local_range"]["from"]["seconds"]
        samples_count = math.ceil(round(overlap_duration / interval, 9))
        for index in range(samples_count):
            samples.append(
                {
                    "result_time": round(overlap_from + index * interval, 3),
                    "scene": scene_window["scene"],
                    "scene_local_time": round(local_from + index * interval, 3),
                    "sample_kind": (
                        "window_anchor" if index == 0 else "scene_interval"
                    ),
                }
            )
    return samples


def _inspect_scene_layout_composition(
    *,
    project: dict,
    spec: dict,
    composition: list[dict],
    from_s: float,
    to_s: float,
    interval: float,
    width: int,
    paths_only: bool,
    fmt: str,
    dry_run: bool,
) -> None:
    if fmt == "text":
        _error_exit(
            "inspect",
            "Text inspect is source/transcript-only. Pass --source <id> "
            "to inspect a source transcript, or use JSON mode for scene "
            "composition thumbnails.",
        )

    range_duration = to_s - from_s
    if range_duration > INSPECT_RANGE_CAP_SECONDS:
        _error_exit_with_hint(
            "inspect",
            (
                f"Range duration ({range_duration:.3f}s) exceeds inspect's "
                f"{INSPECT_RANGE_CAP_SECONDS:.0f}s cap (240 interval samples "
                f"at the default {INSPECT_DEFAULT_INTERVAL_SECONDS}s before "
                "scene-boundary resets)."
            ),
            "Use a narrower range before running scene inspect.",
        )
    if interval < INSPECT_MIN_INTERVAL_SECONDS:
        _error_exit(
            "inspect",
            f"--interval must be at least {INSPECT_MIN_INTERVAL_SECONDS} seconds",
        )
    plan, render_plans = _scene_render_plans_for_range(
        project=project,
        spec=spec,
        composition=composition,
        start_s=from_s,
        end_s=to_s,
        command="inspect",
    )
    frame_samples = _scene_inspect_samples(render_plans, interval)
    expected_frame_count = len(frame_samples)
    inspect_root = get_project_dir() / INSPECT_FRAMES_DIR
    subdir_name = (
        f"scene_layout_{round(from_s, 3)}_{round(to_s, 3)}_"
        f"{round(interval, 3)}"
        f"{_scene_composition_cache_fingerprint(spec, composition)}"
        f"{_overlay_cache_fingerprint(spec)}"
    )
    frames_out = inspect_root / subdir_name
    output_prefix = "scene_inspect"
    frame_paths = [
        frames_out / f"{output_prefix}_{i + 1:04d}.jpg"
        for i in range(expected_frame_count)
    ]
    frame_times = [sample["result_time"] for sample in frame_samples]
    cached = frames_out.exists() and all(path.exists() for path in frame_paths)
    frame_render_plans = [
        _scene_render_plan_at(
            project=project,
            spec=spec,
            composition=composition,
            at_s=frame_time,
            command="inspect",
        )[1]
        for frame_time in frame_times
    ]
    planned_overlays = _overlay_render_context(
        project,
        spec,
        frame_render_plans[0]["canvas_tuple"],
        from_s,
        to_s,
        "inspect",
    )
    frame_overlay_contexts = [
        _overlay_render_context(
            project,
            spec,
            frame_plan["canvas_tuple"],
            frame_time,
            frame_time + 0.001,
            "inspect",
        )
        for frame_time, frame_plan in zip(frame_times, frame_render_plans)
    ]
    ffmpeg_commands = [
        {
            "frame": i + 1,
            "timecode": format_timecode(frame_time),
            "scene": frame_samples[i]["scene"],
            "scene_local_timecode": format_timecode(
                frame_samples[i]["scene_local_time"]
            ),
            "sample_kind": frame_samples[i]["sample_kind"],
            **(
                {"camera_states": frame_plan["camera_states"]}
                if any(
                    state.get("target") != "full"
                    for state in frame_plan.get("camera_states", {}).values()
                )
                else {}
            ),
            "slots": _instant_render_slots(frame_plan, frame_time),
            "command": build_render_layout_frame_command(
                frame_plan["render_slots"],
                str(frame_paths[i]),
                frame_plan["canvas_tuple"],
                overlay_plans=(
                    frame_overlay_contexts[i]["plans"]
                    if frame_overlay_contexts[i]
                    else None
                ),
                highlight_ass=_highlight_ass_arg(frame_overlay_contexts[i]),
            ),
        }
        for i, (frame_time, frame_plan) in enumerate(
            zip(frame_times, frame_render_plans)
        )
    ]

    base_result: dict = {
        "mode": "scene_composition",
        "composition_type": "scenes",
        "composition_canvas": plan["canvas"],
        "composition_duration": format_timecode(plan["composition_duration"]),
        "range": _scene_result_range(from_s, range_duration),
        "scene_windows": [window_plan["scene_window"] for window_plan in render_plans],
        "scenes_count": len({plan["scene_window"]["index"] for plan in render_plans}),
        "pacing_segments_count": len(render_plans),
        "slots_count": sum(len(window_plan["slots"]) for window_plan in render_plans),
        "extraction": {
            "interval_seconds": interval,
            "sampling_mode": "per_scene",
            "window_anchor_samples": len(render_plans),
            "scene_interval_samples": expected_frame_count - len(render_plans),
            "scale_width": width,
        },
        "transcript": None,
        "transcription_skipped_reason": "scene_composition",
    }
    if planned_overlays is not None:
        _attach_overlay_envelope(base_result, planned_overlays)
    if dry_run:
        result = {
            **base_result,
            "dry_run": True,
            "status": "would_extract",
            "would_write_to": str(frames_out.resolve()),
            "would_reuse_cache": cached,
            "would_extract_frames_count": expected_frame_count,
            "render_available": True,
            "ffmpeg_commands": ffmpeg_commands,
            "hint": (
                "Dry-run only - no scene thumbnails extracted. Re-run without "
                "--dry-run to render thumbnails for each scene-active frame."
            ),
        }
        _apply_dry_run_setup(
            result,
            _dry_run_setup_commands_for_outputs(frame_paths),
        )
        click.echo(json.dumps(result, indent=2))
        return

    if not cached:
        if frames_out.exists():
            shutil.rmtree(frames_out)
        frames_out.mkdir(parents=True, exist_ok=True)
        for frame_path, frame_plan, frame_context in zip(
            frame_paths, frame_render_plans, frame_overlay_contexts
        ):
            _write_highlight_ass(frame_context)
            try:
                render_layout_frame(
                    frame_plan["render_slots"],
                    str(frame_path),
                    frame_plan["canvas_tuple"],
                    overlay_plans=(
                        frame_context["plans"] if frame_context else None
                    ),
                    highlight_ass=_highlight_ass_arg(frame_context),
                )
            except FFmpegNotFoundError as exc:
                _error_exit("inspect", str(exc))
            except FileNotFoundError as exc:
                _error_exit("inspect", str(exc))
            except FFmpegCapabilityError as exc:
                _ffmpeg_capability_error_exit(
                    "inspect", exc, "Frame extraction failed"
                )
            except RuntimeError as exc:
                _error_exit("inspect", f"Frame extraction failed: {exc}")

    thumbnails = [
        {
            "path": str(path.resolve()),
            "timecode": format_timecode(frame_time),
            "scene": frame_plan["scene_window"]["scene"],
            "scene_local_timecode": format_timecode(
                frame_samples[i]["scene_local_time"]
            ),
            "sample_kind": frame_samples[i]["sample_kind"],
            "slots": _instant_render_slots(frame_plan, frame_time),
        }
        for i, (path, frame_time, frame_plan) in enumerate(
            zip(frame_paths, frame_times, frame_render_plans)
        )
    ]
    if not paths_only:
        for thumb in thumbnails:
            thumb["image"] = _read_image_inline(thumb["path"])

    result = {
        **base_result,
        "status": "extracted",
        "extraction": {
            **base_result["extraction"],
            "frames_dir": str(frames_out.resolve()),
            "cached": cached,
        },
        "thumbnails": thumbnails,
        "thumbnails_returned": len(thumbnails),
        "ffmpeg_commands": ffmpeg_commands,
        "hint": (
            "Scene thumbnails reflect the active layout at each result-time "
            "frame. Use 'moviestar watch --from <X> --to <Y>' to extract "
            "the same scene range as video."
        ),
    }
    click.echo(json.dumps(result, indent=2))



def _watch_scene_layout_composition(
    *,
    project: dict,
    spec: dict,
    composition: list[dict],
    from_s: float,
    to_s: float,
    output: str | None,
    preview: bool,
    dry_run: bool,
    loudness_report: bool = False,
) -> None:
    plan, render_plans = _scene_render_plans_for_range(
        project=project,
        spec=spec,
        composition=composition,
        start_s=from_s,
        end_s=to_s,
        command="watch",
    )
    resolved_audio_mix = _resolve_project_audio_mix(
        "watch", spec, plan["composition_duration"], get_project_dir()
    )
    if output is None:
        base = f"watch_{round(from_s, 3)}s_{round(to_s, 3)}s.mp4"
        output = _resolve_unique_output_path(base)
    abs_output = os.path.realpath(output)
    output_parent = os.path.dirname(abs_output)
    if output_parent and not os.path.isdir(output_parent) and not dry_run:
        _error_exit(
            "watch",
            f"Cannot write to {abs_output}: parent directory does not exist",
        )

    duration = round(to_s - from_s, 3)
    planned_overlays = _overlay_render_context(
        project,
        spec,
        render_plans[0]["canvas_tuple"],
        from_s,
        to_s,
        "watch",
    )
    artifacts = _scene_video_render_artifacts(
        command="watch",
        project=project,
        spec=spec,
        render_plans=render_plans,
        abs_output=abs_output,
        work_dir=(
            get_project_dir()
            / "scene-renders"
            / f"watch_{round(from_s, 3)}_{round(to_s, 3)}"
        ),
        preview=preview,
    )
    audio_dropped_note = artifacts["audio_dropped_note"]
    base_result: dict = {
        "mode": "scene_composition",
        "composition_type": "scenes",
        "output_fps": artifacts["output_fps"],
        "composition_canvas": plan["canvas"],
        "composition_duration": format_timecode(plan["composition_duration"]),
        "range": {
            "requested": _scene_result_range(from_s, duration),
        },
        "scene_windows": [render_plan["scene_window"] for render_plan in render_plans],
        "scenes_count": len({plan["scene_window"]["index"] for plan in render_plans}),
        "pacing_segments_count": len(render_plans),
        "slots_count": sum(len(render_plan["slots"]) for render_plan in render_plans),
        "preview": preview,
        "camera_render_status": (
            "active"
            if any(plan["animated_camera_slots"] for plan in render_plans)
            else "inactive"
        ),
        "render_order": render_plans[0]["render_order"],
    }
    if audio_dropped_note is not None:
        base_result["audio_dropped_note"] = audio_dropped_note
    if planned_overlays is not None:
        _attach_overlay_envelope(base_result, planned_overlays)

    if dry_run:
        result = {
            **base_result,
            "dry_run": True,
            "status": "would_extract",
            "would_extract_to": abs_output,
            "render_available": True,
            "scene_render_commands": artifacts["scene_render_commands"],
            "concat_command": artifacts["concat_command"],
            "hint": (
                "Dry-run only - no file written. scene_windows shows the "
                "layout changes this watch range would cross."
            ),
        }
        _apply_dry_run_setup(result, artifacts["setup_commands"])
        _attach_audio_schema_envelope(
            result,
            spec=spec,
            result_duration=plan["composition_duration"],
            command="watch",
            source_audio_available=any(artifacts["segment_has_audio"]),
            window_start=from_s,
            window_duration=duration,
            resolved_audio_mix=resolved_audio_mix,
            loudness_report=loudness_report,
        )
        click.echo(json.dumps(result, indent=2))
        return

    try:
        ffmpeg_cmd = _render_scene_video_artifacts(
            artifacts=artifacts,
            abs_output=abs_output,
            preview=preview,
        )
    except FFmpegNotFoundError as exc:
        _error_exit("watch", str(exc))
    except FileNotFoundError as exc:
        _error_exit("watch", str(exc))
    except RuntimeError as exc:
        _error_exit("watch", f"Extraction failed: {exc}")

    try:
        out_probe = run_ffprobe(abs_output)
        actual_duration = float(out_probe.get("format", {}).get("duration", 0.0))
    except (RuntimeError, json.JSONDecodeError, FFmpegNotFoundError):
        actual_duration = 0.0

    result = {
        **base_result,
        "status": "extracted",
        "out": abs_output,
        "range": {
            "requested": _scene_result_range(from_s, duration),
            "actual": {"duration": format_timecode(actual_duration)},
        },
        "file_size_bytes": os.path.getsize(abs_output),
        "scene_render_commands": artifacts["scene_render_commands"],
        "concat_command": ffmpeg_cmd,
        "hint": (
            "Feed this file to a multimodal model for motion/audio analysis. "
            "For thumbnails of the same scene range, use 'moviestar inspect "
            "--from <X> --to <Y>'."
        ),
    }
    _attach_audio_schema_envelope(
        result,
        spec=spec,
        result_duration=plan["composition_duration"],
        command="watch",
        source_audio_available=any(artifacts["segment_has_audio"]),
        window_start=from_s,
        window_duration=duration,
        resolved_audio_mix=resolved_audio_mix,
        loudness_report=loudness_report,
    )
    click.echo(json.dumps(result, indent=2))


def _scene_video_render_artifacts(
    *,
    command: str,
    project: dict,
    spec: dict,
    render_plans: list[dict],
    abs_output: str,
    work_dir: Path,
    preview: bool,
    audio_join_fade: float | None = None,
) -> dict:
    render_plans, transition_render_plan = _apply_internal_transition_rendering(
        command=command,
        project=project,
        spec=spec,
        render_plans=render_plans,
    )
    # Scene clips render separately then concat, so each scene's
    # overlay plan is windowed to that scene's global result range;
    # enable times land relative to the clip's local t=0.
    for render_plan in render_plans:
        window = render_plan["scene_window"]["result_range"]
        scene_planned = _overlay_render_context(
            project,
            spec,
            render_plan["canvas_tuple"],
            window["from"]["seconds"],
            window["to"]["seconds"],
            command,
        )
        render_plan["overlay_context"] = scene_planned
        render_plan["overlay_plans"] = (
            scene_planned["plans"] if scene_planned else None
        )
        postroll = float(render_plan.get("transition_postroll", 0.0))
        if postroll and render_plan["overlay_plans"]:
            authored_duration = float(render_plan["duration"])
            for overlay_plan in render_plan["overlay_plans"]:
                if overlay_plan["enable_to"] >= authored_duration - 0.0005:
                    overlay_plan["enable_to"] = round(
                        float(overlay_plan["enable_to"]) + postroll, 6
                    )
            scene_planned["highlight_ass"] = _highlight_ass_assets(
                scene_planned,
                render_plan["canvas_tuple"],
            )
    scene_clip_paths = [
        work_dir / f"scene_{i + 1:04d}.mp4"
        for i, render_plan in enumerate(render_plans)
    ]
    scene_render_commands = [
        {
            "scene": render_plan["scene_window"]["scene"],
            "index": render_plan["scene_window"]["index"],
            "out": str(scene_clip_paths[i]),
            "command": build_render_layout_video_command(
                render_plan["render_slots"],
                str(scene_clip_paths[i]),
                render_plan["canvas_tuple"],
                render_plan["video_duration"],
                preview=preview,
                audio_from_input=render_plan["audio_from_input"],
                audio_speed=render_plan.get("audio_speed", 1.0),
                output_fps=render_plan.get("output_fps"),
                overlay_plans=render_plan["overlay_plans"],
                highlight_ass=_highlight_ass_arg(
                    render_plan["overlay_context"]
                ),
            ),
            "duration": format_timecode(render_plan["duration"]),
            "video_duration": format_timecode(render_plan["video_duration"]),
            "transition_preroll": format_timecode(
                render_plan.get("transition_preroll", 0.0)
            ),
            "transition_postroll": format_timecode(
                render_plan.get("transition_postroll", 0.0)
            ),
        }
        for i, render_plan in enumerate(render_plans)
    ]
    concat_segments = [
        (str(path), 0.0, render_plan["duration"])
        for path, render_plan in zip(scene_clip_paths, render_plans)
    ]
    segment_has_audio = [
        render_plan["audio_from_input"] is not None
        for render_plan in render_plans
    ]
    concat_command = build_render_segments_command(
        concat_segments,
        abs_output,
        precise=True,
        preview=False,
        segment_has_audio=segment_has_audio,
        fill_missing_audio_with_silence=True,
        audio_join_fade=audio_join_fade,
        output_fps=render_plans[0]["output_fps"],
        video_segment_durations=(
            [render_plan["video_duration"] for render_plan in render_plans]
            if transition_render_plan
            else None
        ),
        video_transitions=transition_render_plan or None,
    )
    setup_commands = _dry_run_setup_commands_for_outputs(
        [*scene_clip_paths, abs_output]
    )
    audio_dropped_note = None
    if any(segment_has_audio) and not all(segment_has_audio):
        audio_dropped_note = (
            f"One or more scene windows have no routed audio; this {command} "
            "render keeps routed audio and fills unrouted windows with silence."
        )
    return {
        "work_dir": work_dir,
        "render_plans": render_plans,
        "scene_clip_paths": scene_clip_paths,
        "scene_render_commands": scene_render_commands,
        "concat_segments": concat_segments,
        "segment_has_audio": segment_has_audio,
        "concat_command": concat_command,
        "output_fps": render_plans[0]["output_fps"],
        "setup_commands": setup_commands,
        "audio_dropped_note": audio_dropped_note,
        "transition_render_plan": transition_render_plan,
    }


def _apply_internal_transition_rendering(
    *,
    command: str,
    project: dict,
    spec: dict,
    render_plans: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Add visual handles around scene cuts while preserving result time."""
    plans = copy.deepcopy(render_plans)
    for plan in plans:
        plan["video_duration"] = float(plan["duration"])
    if command != "export":
        return plans, []

    composition = spec.get("composition") or []
    resolved_project = resolve_project(project, spec)
    source_durations = {
        source["id"]: float(source["duration"]["seconds"])
        for source in project["sources"]
    }
    transition_plans: list[dict] = []
    for incoming_scene_index, scene in enumerate(composition):
        transition = scene.get("transition_in")
        if incoming_scene_index == 0 or not isinstance(transition, dict):
            continue
        resolution = resolve_internal_transition(
            resolved_project,
            source_durations,
            incoming_scene_index=incoming_scene_index,
            transition_type=transition["type"],
            duration_s=float(transition["duration"]),
        )
        if not resolution.sufficient:
            _transition_internal_limit_exit(command, [resolution])

        incoming_plan_index = next(
            (
                i
                for i, plan in enumerate(plans)
                if plan["scene_window"]["index"] == incoming_scene_index
            ),
            None,
        )
        outgoing_indices = [
            i
            for i, plan in enumerate(plans)
            if plan["scene_window"]["index"] == incoming_scene_index - 1
        ]
        if incoming_plan_index is None or not outgoing_indices:
            # Internal rendering currently runs only for complete exports.
            continue
        outgoing_plan_index = outgoing_indices[-1]
        outgoing_plan = plans[outgoing_plan_index]
        incoming_plan = plans[incoming_plan_index]
        half_duration = resolution.duration_s / 2

        if resolution.transition_type == "dissolve":
            outgoing_plan["video_duration"] += half_duration
            incoming_plan["video_duration"] += half_duration
            outgoing_plan["transition_postroll"] = (
                float(outgoing_plan.get("transition_postroll", 0.0))
                + half_duration
            )
            incoming_plan["transition_preroll"] = (
                float(incoming_plan.get("transition_preroll", 0.0))
                + half_duration
            )
            outgoing_handles = {
                handle.slot_name: handle for handle in resolution.outgoing_handles
            }
            incoming_handles = {
                handle.slot_name: handle for handle in resolution.incoming_handles
            }
            for slot in outgoing_plan["render_slots"]:
                handle = outgoing_handles[slot["slot"]]
                if handle.edge_behavior == "playback":
                    slot["source_to"] += handle.required_source_s
            for slot in incoming_plan["render_slots"]:
                handle = incoming_handles[slot["slot"]]
                if handle.edge_behavior == "playback":
                    slot["source_from"] -= handle.required_source_s
                    slot["video_seek_from"] = min(
                        slot.get("video_seek_from", slot["source_from"]),
                        slot["source_from"],
                    )
        # A dissolved incoming clip starts half a transition earlier in
        # result time, so scene-local camera and layout motion must start at
        # the matching negative local time. This keeps the entire scene-owned
        # visual composite attached to its scene pixels through the blend.
        if resolution.transition_type == "dissolve":
            for slot in incoming_plan["render_slots"]:
                animation = slot.get("camera_animation")
                if animation is not None:
                    animation["result_local_from"] = (
                        float(animation["result_local_from"]) - half_duration
                    )
                motion = slot.get("motion")
                if motion is not None:
                    motion["result_time_offset"] = (
                        float(motion.get("result_time_offset", 0.0))
                        - half_duration
                    )

        boundary = _transition_internal_boundary_envelope(resolution)
        transition_plans.append(
            {
                "incoming_index": incoming_plan_index,
                "outgoing_clip_index": outgoing_plan_index,
                "type": resolution.transition_type,
                "duration": resolution.duration_s,
                "outgoing": boundary["outgoing"],
                "incoming": boundary["incoming"],
                "result_cut_at": boundary["result_cut_at"],
                "result_window": boundary["result_window"],
                "alignment": "center",
                "audio_behavior": "unchanged",
                "source_handles": _transition_source_handles(resolution),
            }
        )
    return plans, transition_plans


# Rendered video must match its authored duration. A drift beyond this
# tolerance means a VFR gap (or another future bug) left missing PTS time.
# ~3 frames at 30fps is wide enough for container rounding and far below
# any visible hole (issues #359 and #364).
_RENDER_DURATION_TOLERANCE_S = 0.1


def _rendered_video_duration(path: str) -> float | None:
    """Video-stream duration of a rendered clip, or None if unprobeable.

    Prefers the video stream over format duration because AAC padding
    can stretch the container a few hundredths past the video track.
    """
    try:
        probe = run_ffprobe(path)
    except (RuntimeError, json.JSONDecodeError, FFmpegNotFoundError):
        return None
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("duration"):
            return float(stream["duration"])
    fmt_duration = probe.get("format", {}).get("duration")
    return float(fmt_duration) if fmt_duration else None


def _assert_scene_clip_duration(path: str, render_plan: dict) -> None:
    scene = render_plan["scene_window"]["scene"]
    _assert_rendered_video_duration(
        path,
        float(render_plan.get("video_duration", render_plan["duration"])),
        f"scene {scene!r}",
    )


def _assert_rendered_video_duration(
    path: str,
    expected: float,
    render_name: str,
) -> None:
    """Abort when a render would ship with missing or extra video time."""
    actual = _rendered_video_duration(path)
    if actual is None:
        return
    if abs(actual - expected) <= _RENDER_DURATION_TOLERANCE_S:
        return
    raise RuntimeError(
        f"{render_name} rendered {actual:.3f}s of video but the plan "
        f"requires {expected:.3f}s. The result would have timestamp holes, "
        f"so the render was aborted. This usually means a source has no "
        f"frames in part of its range (VFR screen recordings emit no frames "
        f"while the screen is static)."
    )


def _render_scene_video_artifacts(
    *,
    artifacts: dict,
    abs_output: str,
    preview: bool,
    audio_join_fade: float | None = None,
    quiet: bool = False,
) -> list[str]:
    work_dir: Path = artifacts["work_dir"]
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    for path, render_plan in zip(
        artifacts["scene_clip_paths"],
        artifacts["render_plans"],
    ):
        _write_highlight_ass(render_plan.get("overlay_context"))
        render_layout_video(
            render_plan["render_slots"],
            str(path),
            render_plan["canvas_tuple"],
            render_plan.get("video_duration", render_plan["duration"]),
            preview=preview,
            audio_from_input=render_plan["audio_from_input"],
            audio_speed=render_plan.get("audio_speed", 1.0),
            output_fps=render_plan.get("output_fps"),
            progress_cb=lambda _line: None,
            overlay_plans=render_plan.get("overlay_plans"),
            highlight_ass=_highlight_ass_arg(
                render_plan.get("overlay_context")
            ),
        )
        _assert_scene_clip_duration(str(path), render_plan)
    ffmpeg_cmd = render_segments(
        artifacts["concat_segments"],
        abs_output,
        precise=True,
        preview=False,
        segment_has_audio=artifacts["segment_has_audio"],
        fill_missing_audio_with_silence=True,
        audio_join_fade=audio_join_fade,
        output_fps=artifacts["output_fps"],
        video_segment_durations=(
            [
                render_plan["video_duration"]
                for render_plan in artifacts["render_plans"]
            ]
            if artifacts["transition_render_plan"]
            else None
        ),
        video_transitions=artifacts["transition_render_plan"] or None,
        progress_cb=_render_progress_cb(quiet),
    )
    _assert_rendered_video_duration(
        abs_output,
        sum(end - start for _path, start, end in artifacts["concat_segments"]),
        "scene composition",
    )
    return ffmpeg_cmd


# ---------------------------------------------------------------------------
# M15 — clip extraction.
#
# Agent testing established `clip` + `batch` as a pair over one shared
# extraction engine and rejected an `export --slice` alternative. `clip`
# is the per-clip arity; `batch` is the same operation at recipe scale
# with atomic, all-failures-at-once validation.

# Per-clip recipe keys batch accepts; used for unknown-key
# did-you-mean validation.
_BATCH_CLIP_KEYS = frozenset({"from", "to", "out", "source"})

# Semantic aliases difflib can't catch ("start" is not lexically close
# to "from") — step-3 friction-verification catch. Checked before the
# fuzzy match.
_BATCH_KEY_ALIASES = {
    "start": "from",
    "begin": "from",
    "end": "to",
    "stop": "to",
    "until": "to",
    "output": "out",
    "file": "out",
    "path": "out",
}


def _check_clip_range(
    source: dict, from_tc: str, to_tc: str
) -> tuple[tuple[float, float] | None, tuple[str, str] | None]:
    """Pure range check for the extraction engine (clip / batch).
    Returns ``(parsed, error)`` — exactly one is None. ``parsed`` is
    ``(from_s, to_s)``; ``error`` is ``(message, hint)``.

    Agent-tested contracts are baked in: every error carries a hint,
    bounds errors name which end overflowed, and the original timecode
    token is echoed alongside the parsed seconds. Pure (no exit) so
    batch can collect all failing entries before reporting.
    """
    duration_seconds = float(source["duration"]["seconds"])
    duration_hint = (
        f"Source {source['id']!r} runs 0-{duration_seconds}s. Run "
        "'moviestar status' for source durations."
    )
    try:
        from_s = parse_timecode(from_tc)
        to_s = parse_timecode(to_tc)
    except ValueError as exc:
        return None, (
            str(exc),
            "Timecodes accept SS (83.5), M:SS (1:23), or HH:MM:SS.mmm "
            "(0:01:23.500) — see TIMECODE FORMATS in 'moviestar --help'.",
        )
    if from_s >= to_s:
        return None, (
            f"from ({from_tc!r} = {from_s}s) must be before "
            f"to ({to_tc!r} = {to_s}s)",
            "Swap the from/to values or correct the range.",
        )
    if from_s < 0:
        return None, (
            f"from ({from_tc!r} = {from_s}s) is before the start "
            "of the source",
            duration_hint,
        )
    if to_s > duration_seconds + 0.5:
        if from_s > duration_seconds:
            which = f"from ({from_tc!r} = {from_s}s)"
        else:
            which = f"to ({to_tc!r} = {to_s}s)"
        return None, (
            f"{which} is past the end of the source "
            f"(duration {duration_seconds}s)",
            duration_hint,
        )
    return (from_s, to_s), None


def _validate_clip_range(
    command: str, source: dict, from_tc: str, to_tc: str, *, label: str = ""
) -> tuple[float, float]:
    """Exit-on-error wrapper around _check_clip_range for single-range
    callers (clip)."""
    parsed, err = _check_clip_range(source, from_tc, to_tc)
    if err is not None:
        prefix = f"{label}: " if label else ""
        _error_exit_with_hint(command, f"{prefix}{err[0]}", err[1])
    return parsed


def _clip_entry_envelope(
    source_id: str,
    source_path: str,
    from_s: float,
    to_s: float,
    abs_output: str,
    *,
    precise: bool = True,
) -> dict:
    """The per-clip envelope shared by the extraction engine (clip /
    batch), so both arities report identically."""
    range_duration = to_s - from_s
    return {
        "out": abs_output,
        "source": source_id,
        "range": {
            "requested": {
                "from": format_timecode(from_s),
                "to": format_timecode(to_s),
                "duration": format_timecode(range_duration),
            },
        },
        "mode": "re-encode" if precise else "stream-copy",
        "ffmpeg_command": build_extract_clip_command(
            source_path, from_s, range_duration, abs_output, precise=precise
        ),
    }


def _resolve_clip_out(
    command: str,
    output: str | None,
    auto_basename: str,
    *,
    create_parent: bool = True,
    reserved_auto_paths: set[str] | None = None,
) -> str:
    """Resolve one clip output path. Auto-names land under
    moviestar-clips/ with #25 auto-suffixing; explicit paths are
    honored as-given (and overwrite). Parent directories are created
    as needed because an explicit --out declares that intent. Dry-run
    callers pass create_parent=False so previews have no side
    effects."""
    if output is None:
        clips_dir = get_project_dir().parent / CLIPS_DIR
        if create_parent:
            clips_dir.mkdir(parents=True, exist_ok=True)
        output = _resolve_unique_output_path(
            str(clips_dir / auto_basename),
            reserved_paths=reserved_auto_paths,
        )
    abs_output = os.path.realpath(output)
    output_parent = os.path.dirname(abs_output)
    if output_parent and create_parent:
        try:
            os.makedirs(output_parent, exist_ok=True)
        except OSError as exc:
            _error_exit_with_hint(
                command,
                f"Cannot create parent directory for {abs_output}: {exc}",
                "Pick a writable --out path.",
            )
    return abs_output


@cli.command()
@project_workspace_option
@click.option("--from", "from_tc", required=True, help="Start timecode (source-time).")
@click.option("--to", "to_tc", required=True, help="End timecode (source-time).")
@click.option(
    "--out",
    "output",
    default=None,
    help="Output MP4 path. Auto-named 'moviestar-clips/clip_<from>s_<to>s.mp4' "
    "if omitted; auto-names suffix _2, _3, ... on repeat invocations rather "
    "than overwriting. Explicit --out paths overwrite.",
)
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID for multi-source projects. Optional in single-"
    "source projects (defaults to the only source).",
)
@click.option(
    "--fast",
    is_flag=True,
    help="Stream-copy instead of re-encoding. Faster but snaps to "
    "nearest keyframe (±keyframe drift on the actual duration).",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Validate everything the real call would (project loaded, "
    "range parses + within source, output path resolved including "
    "auto-suffixing) but skip running ffmpeg and writing the output "
    "file. Returns the same envelope shape with status: "
    "'would_extract', dry_run: true, would_extract_to, and "
    "ffmpeg_command; range.actual and file_size_bytes are dropped "
    "(post-execution measurements).",
)
def clip(
    from_tc: str,
    to_tc: str,
    output: str | None,
    source_arg: str | None,
    fast: bool,
    dry_run: bool,
) -> None:
    """Extract one independent clip from a source to its own MP4.

    Stateless with respect to the project's edit spec: timecodes are
    SOURCE-TIME — the original file's clock, the same clock 'find'
    reports in source_range and 'probe' reports overall. Any trims or
    cuts on the source are ignored and left untouched; run 'clip'
    fifteen times and the edit spec never changes.

    For "cut N clips from one source," loop: find the moment, clip it,
    repeat. Each invocation is independent. Outputs land in
    'moviestar-clips/' by default (swept by 'moviestar clean');
    explicit --out paths are deliverables — clean does not touch them.
    Missing parent directories for --out are created automatically.

    Default is **frame-exact re-encode** — "what you asked for == what
    you got". On this path ``range.actual`` should match
    ``range.requested`` within 1 frame (~0.04s at 24fps); larger
    drift is a bug worth reporting. ``--fast`` opts into stream-copy
    (sub-second, but snaps to the nearest keyframe; ``range.actual``
    may drift further from ``range.requested``).

    Different from 'watch' (the same extraction engine with
    analysis-tuned defaults: stream-copy first, cwd auto-names) and
    'export' (renders the project's edit spec).
    """
    if not is_loaded():
        _no_project_error_exit("clip")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("clip", f"Could not read project.json: {exc}")

    source_id = _resolve_source_arg(project, source_arg, "clip")
    source = _get_project_source(project, source_id)
    source_path = source["path"]
    from_s, to_s = _validate_clip_range("clip", source, from_tc, to_tc)
    range_duration = to_s - from_s

    abs_output = _resolve_clip_out(
        "clip",
        output,
        f"clip_{round(from_s, 3)}s_{round(to_s, 3)}s.mp4",
        create_parent=not dry_run,
    )

    base = _clip_entry_envelope(
        source_id,
        os.path.realpath(source_path),
        from_s,
        to_s,
        abs_output,
        precise=not fast,
    )
    keyframe_note = (
        "stream-copy snaps to nearest keyframe; actual duration may differ "
        "slightly from requested. Drop --fast for frame-exact extraction."
    )

    # Dry-run forks at the side-effect boundary (issue #36 convention):
    # everything above ran identically; below is ffmpeg + the output
    # probe.
    if dry_run:
        base.pop("out")
        result: dict = {
            "dry_run": True,
            "status": "would_extract",
            "would_extract_to": abs_output,
            **base,
            "edit_spec_untouched": True,
            "hint": (
                "Dry-run only — no file written. Re-run without --dry-run "
                "to extract. The auto-suffixed would_extract_to path is "
                "what the real call would use."
            ),
        }
        if fast:
            result["note"] = keyframe_note
        click.echo(json.dumps(result, indent=2))
        return

    try:
        ffmpeg_cmd = extract_clip(
            source_path, from_s, range_duration, abs_output, precise=not fast
        )
    except FFmpegNotFoundError as exc:
        _error_exit("clip", str(exc))
    except FileNotFoundError as exc:
        _error_exit("clip", str(exc))
    except RuntimeError as exc:
        _error_exit("clip", f"Extraction failed: {exc}")

    try:
        out_probe = run_ffprobe(abs_output)
        actual_duration = float(out_probe.get("format", {}).get("duration", 0.0))
    except (RuntimeError, json.JSONDecodeError, FFmpegNotFoundError):
        actual_duration = 0.0
    base["range"]["actual"] = {"duration": format_timecode(actual_duration)}
    base["ffmpeg_command"] = ffmpeg_cmd

    result = {
        "status": "extracted",
        **base,
        "edit_spec_untouched": True,
        "file_size_bytes": os.path.getsize(abs_output),
        "hint": (
            "One clip per invocation — repeat with a new --from/--to/--out "
            "for the next clip; the project's edit spec is untouched. Get "
            "ranges from 'moviestar find \"phrase\"' (use source_range)."
        ),
    }
    if fast:
        result["note"] = keyframe_note
    click.echo(json.dumps(result, indent=2))


@cli.command()
@project_workspace_option
@click.argument("recipe", type=click.Path())
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Validate the whole recipe (every entry — all failures "
    "reported at once) and resolve every output path including "
    "auto-names, but skip running ffmpeg and writing any files. "
    "Returns the same envelope shape with status: 'would_extract', "
    "dry_run: true, and would_extract_to per clip; range.actual and "
    "file_size_bytes are dropped (post-execution measurements).",
)
@click.option(
    "--jobs",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Number of clip extractions to run at once. 1 keeps the "
    "sequential behavior; larger values run independent clips in "
    "parallel while preserving recipe order in the JSON response.",
)
def batch(recipe: str, dry_run: bool, jobs: int) -> None:
    """Extract a batch of independent clips described by a JSON recipe.

    \b
    Recipe shape:
      {
        "source": "src_0",
        "clips": [
          {"from": "0:05:10", "to": "0:05:42", "out": "intro.mp4"},
          {"from": "0:12:03", "to": "0:12:31", "out": "qa.mp4"}
        ]
      }

    Top-level "source" is an optional default; each clip may override
    it with its own "source". Both are optional in single-source
    projects. "out" is optional per clip — omitted outputs auto-name
    under 'moviestar-clips/' (swept by 'moviestar clean'); explicit
    "out" paths are deliverables with parent directories created as
    needed. Relative "out" paths resolve against the current working
    directory, not the project workspace. Explicit "out" paths
    overwrite on re-run; auto-names suffix _2, _3, ...

    Timecodes are SOURCE-TIME — the original file's clock, the same
    clock 'find' reports in source_range. The project's edit spec is
    ignored and left untouched.

    Validation is all-or-nothing and reports ALL failing entries at
    once: every entry is checked before any file is written, and the
    error envelope carries an ``errors`` array with one
    ``{entry, error, hint}`` record per bad entry (entries are
    addressed as clips[0], clips[1], ...). Unknown keys are errors,
    with a did-you-mean suggestion.

    Same extraction engine as 'clip' (frame-exact re-encode) — batch
    is the many-clips arity, clip the one-clip arity. By default,
    clips are extracted sequentially; expect a few seconds per clip on
    the re-encode path (a 15-clip job runs about a minute — not a
    hang). Use ``--jobs N`` to run independent clip extractions in
    parallel; ffmpeg is itself multi-threaded, so tune this per
    machine. Each clip's ``range.actual`` should match
    ``range.requested`` within 1 frame (~0.04s at 24fps); larger drift
    is a bug worth reporting.
    """
    if not is_loaded():
        _no_project_error_exit("batch")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("batch", f"Could not read project.json: {exc}")

    recipe_path = Path(recipe)
    if not recipe_path.is_file():
        _error_exit_with_hint(
            "batch",
            f"Recipe file not found: {os.path.realpath(recipe)}",
            "Write a JSON recipe with a \"clips\" list of "
            "{\"from\", \"to\", \"out\"} entries — see 'moviestar batch "
            "--help' for the shape.",
        )
    try:
        recipe_data = json.loads(recipe_path.read_text())
    except json.JSONDecodeError as exc:
        _error_exit_with_hint(
            "batch",
            f"Recipe is not valid JSON: {exc}",
            "See 'moviestar batch --help' for the recipe shape.",
        )

    clips_in = recipe_data.get("clips")
    if not isinstance(clips_in, list) or not clips_in:
        _error_exit_with_hint(
            "batch",
            "Recipe must contain a non-empty \"clips\" list.",
            "See 'moviestar batch --help' for the recipe shape.",
        )
    default_source = recipe_data.get("source")

    # All-or-nothing, all-failures-at-once: validate every entry before
    # writing anything, collecting one {entry, error, hint} record per
    # bad entry (friction-round catch — first-failure-only reporting
    # made a doubly-broken recipe a fix-run-fix-run loop).
    entry_shape_hint = (
        "Each clip entry takes 'from', 'to', and optional 'out' / "
        "'source' — see 'moviestar batch --help'."
    )
    errors: list[dict] = []
    resolved: list[tuple[str, str, float, float, str | None]] = []
    for i, entry in enumerate(clips_in):
        label = f"clips[{i}]"
        if not isinstance(entry, dict):
            errors.append({
                "entry": label,
                "error": "each clip must be an object",
                "hint": entry_shape_hint,
            })
            continue
        problems: list[str] = []
        hint = entry_shape_hint
        unknown = sorted(set(entry.keys()) - _BATCH_CLIP_KEYS)
        if unknown:
            problems.append(f"unknown key(s) {unknown}")
            suggestions = []
            for k in unknown:
                if k in _BATCH_KEY_ALIASES:
                    suggestions.append(_BATCH_KEY_ALIASES[k])
                else:
                    suggestions.extend(
                        difflib.get_close_matches(
                            k, sorted(_BATCH_CLIP_KEYS), n=1
                        )
                    )
            if suggestions:
                hint = (
                    f"Did you mean {', '.join(repr(s) for s in suggestions)}? "
                    + entry_shape_hint
                )
        missing = sorted({"from", "to"} - entry.keys())
        if missing:
            problems.append(f"missing required key(s) {missing}")
        if problems:
            errors.append({
                "entry": label,
                "error": "; ".join(problems),
                "hint": hint,
            })
            continue
        source_id = _resolve_source_arg(
            project, entry.get("source") or default_source, "batch"
        )
        source = _get_project_source(project, source_id)
        parsed, range_err = _check_clip_range(
            source, str(entry["from"]), str(entry["to"])
        )
        if range_err is not None:
            errors.append({
                "entry": label,
                "error": range_err[0],
                "hint": range_err[1],
            })
            continue
        resolved.append(
            (source_id, os.path.realpath(source["path"]), parsed[0], parsed[1],
             entry.get("out"))
        )

    if errors:
        payload = {
            "error": (
                f"Recipe validation failed: {len(errors)} of "
                f"{len(clips_in)} entries invalid. No files were written."
            ),
            "command": "batch",
            "errors": errors,
            "hint": (
                "Fix the listed entries and re-run — validation is "
                "all-or-nothing, so nothing was extracted."
            ),
        }
        click.echo(json.dumps(payload, indent=2))
        sys.exit(1)

    entries: list[dict] = []
    reserved_auto_paths: set[str] = set()
    for i, (source_id, source_path, from_s, to_s, out) in enumerate(resolved):
        abs_output = _resolve_clip_out(
            "batch",
            out,
            f"batch_{i + 1}_{round(from_s, 3)}s_{round(to_s, 3)}s.mp4",
            create_parent=not dry_run,
            reserved_auto_paths=reserved_auto_paths,
        )
        reserved_auto_paths.add(abs_output)
        entry_env = _clip_entry_envelope(
            source_id, source_path, from_s, to_s, abs_output
        )

        if dry_run:
            entry_env["would_extract_to"] = entry_env.pop("out")
            entries.append(entry_env)
            continue

        entries.append(entry_env)

    def _extract_entry(i: int, entry_env: dict) -> dict:
        _source_id, source_path, from_s, to_s, _out = resolved[i]
        abs_output = entry_env["out"]
        ffmpeg_cmd = extract_clip(
            source_path, from_s, to_s - from_s, abs_output, precise=True
        )
        try:
            out_probe = run_ffprobe(abs_output)
            actual_duration = float(
                out_probe.get("format", {}).get("duration", 0.0)
            )
        except (RuntimeError, json.JSONDecodeError, FFmpegNotFoundError):
            actual_duration = 0.0
        entry_env["range"]["actual"] = {
            "duration": format_timecode(actual_duration)
        }
        entry_env["ffmpeg_command"] = ffmpeg_cmd
        entry_env["file_size_bytes"] = os.path.getsize(abs_output)
        return entry_env

    if dry_run:
        result: dict = {
            "dry_run": True,
            "status": "would_extract",
            "clips": entries,
            "clips_total": len(entries),
            "edit_spec_untouched": True,
            "hint": (
                "Dry-run only — recipe validated, no files written. The "
                "would_extract_to paths (including auto-suffixed names) "
                "are what a real call would use. Re-run without "
                "--dry-run to extract."
            ),
        }
        click.echo(json.dumps(result, indent=2))
        return

    if jobs == 1:
        for i, entry_env in enumerate(entries):
            try:
                entries[i] = _extract_entry(i, entry_env)
            except FFmpegNotFoundError as exc:
                _error_exit("batch", str(exc))
            except (FileNotFoundError, RuntimeError) as exc:
                _error_exit_with_hint(
                    "batch",
                    f"clips[{i}]: extraction failed: {exc}",
                    f"Entries before clips[{i}] were already written and "
                    "remain on disk; fix the failure and re-run.",
                )
    else:
        completed: list[dict | None] = [None] * len(entries)
        errors: list[tuple[int, dict]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = {
                executor.submit(_extract_entry, i, entry): i
                for i, entry in enumerate(entries)
            }
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                try:
                    completed[i] = future.result()
                except FFmpegNotFoundError as exc:
                    errors.append(
                        (
                            i,
                            {
                                "entry": f"clips[{i}]",
                                "error": str(exc),
                                "hint": "Install FFmpeg and re-run the batch.",
                            },
                        )
                    )
                except (FileNotFoundError, RuntimeError) as exc:
                    errors.append(
                        (
                            i,
                            {
                                "entry": f"clips[{i}]",
                                "error": f"extraction failed: {exc}",
                                "hint": (
                                    "Parallel extraction may have completed "
                                    "other entries already; fix this entry "
                                    "and re-run."
                                ),
                            },
                        )
                    )
        if errors:
            error_entries = [
                e for _i, e in sorted(errors, key=lambda item: item[0])
            ]
            payload = {
                "error": (
                    f"Parallel extraction failed: {len(errors)} of "
                    f"{len(entries)} entries failed."
                ),
                "command": "batch",
                "errors": error_entries,
                "hint": (
                    "Completed outputs may remain on disk. Fix the failing "
                    "entries and re-run."
                ),
            }
            click.echo(json.dumps(payload, indent=2))
            sys.exit(1)
        entries = [entry for entry in completed if entry is not None]

    result = {
        "status": "extracted",
        "clips": entries,
        "clips_total": len(entries),
        "edit_spec_untouched": True,
        "hint": (
            "All clips extracted in one call; the project's edit spec is "
            "untouched. Re-run with an edited recipe to regenerate — "
            "explicit \"out\" paths overwrite, auto-named ones suffix. "
            "Clean up auto-named outputs with 'moviestar clean'."
        ),
    }
    click.echo(json.dumps(result, indent=2))


@cli.command()
@project_workspace_option
@click.argument("video", type=click.Path(), required=False)
@click.option(
    "--file",
    "file_path",
    type=click.Path(),
    default=None,
    help="Extract from a raw or exported video file, bypassing any loaded "
    "project. The positional VIDEO form remains supported.",
)
@click.option("--at", "timecode", required=True, help="Timecode to capture (HH:MM:SS.mmm, seconds, or M:SS).")
@click.option(
    "--out",
    "output",
    default=None,
    help="Output file path. Auto-generated if omitted (suffixes _2, _3, ... "
    "on repeat invocations rather than overwriting). Explicit --out paths "
    "overwrite.",
)
@click.option(
    "--inline",
    "inline",
    is_flag=True,
    help="Embed base64 image bytes under image: {format, base64}. Only "
    "useful for frameworks that convert envelope bytes into image "
    "blocks; CLI harnesses should read the returned image files instead.",
)
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID for multi-source projects (project mode only). "
    "Optional in single-source projects (defaults to the only source). "
    "Has no effect in file mode.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Validate and print the planned screenshot operation without writing a file.",
)
def screenshot(
    video: str | None,
    file_path: str | None,
    timecode: str,
    output: str | None,
    inline: bool,
    source_arg: str | None,
    dry_run: bool,
) -> None:
    """Capture a single frame at a timecode.

    Two modes:

    \b
      moviestar screenshot --file <video> --at <tc>
                                               raw or exported file,
                                               no project edits applied
      moviestar screenshot --at <tc>           inside a loaded project,
                                               --at is in *result-time*
                                               and resolved to source-time
                                               via the edit spec

    Project mode reports both ``result_timecode`` and ``source_timecode``
    so the agent can map either way. Out-of-bounds errors call out
    *result duration* explicitly. With ``--file`` or the legacy positional
    VIDEO argument, the project's spec is ignored, making this suitable
    for verifying a rendered export.

    The response carries an explicit ``mode: "file" | "project" |
    "layout_composition" | "scene_composition"`` field so agents can
    branch on a single key rather than sniffing for the presence of
    ``result_timecode``.

    Outputs JPEG by default. When --out is omitted, the file is
    written to screenshot_<seconds>s.jpg in the current directory.
    Pass --out foo.png if you need a lossless PNG.

    The frame is delivered as the file at ``out`` — view it with
    your file reader. Pass --inline to additionally embed the bytes
    as base64 under ``image: {format, base64}`` for frameworks that
    convert envelope bytes into image blocks.

    Why JPEG default: at the same 720p frame, JPEG (q=2) is roughly
    5x more token-efficient than PNG once read by a model (and once
    base64-encoded under --inline) — ~16K
    tokens vs ~80K. PNG's losslessness isn't useful for agent
    verification or multimodal analysis, where JPEG q=2 is visually
    indistinguishable. PNG remains an opt-in for pixel-perfect needs.
    """
    try:
        at_seconds = parse_timecode(timecode)
    except ValueError as exc:
        _error_exit("screenshot", str(exc))

    if video is not None and file_path is not None:
        _error_exit(
            "screenshot",
            "Specify the input once, using either --file or positional VIDEO.",
        )

    paths_only = not inline
    input_file = file_path or video
    if input_file is None:
        _screenshot_in_project(at_seconds, output, paths_only, source_arg, dry_run)
        return

    _screenshot_file_mode(input_file, at_seconds, output, paths_only, dry_run)


def _screenshot_file_mode(
    video: str,
    at_seconds: float,
    output: str | None,
    paths_only: bool,
    dry_run: bool,
) -> None:
    """Render a frame from a raw file without project awareness."""
    abs_source = os.path.realpath(video)

    if not os.path.exists(abs_source):
        _error_exit("screenshot", f"File not found: {abs_source}")

    # Probe source for duration + fps (bounds check and frame number)
    try:
        probe_data = run_ffprobe(abs_source)
    except FFmpegNotFoundError as exc:
        _error_exit("screenshot", str(exc))
    except (RuntimeError, json.JSONDecodeError) as exc:
        _error_exit("screenshot", f"Could not probe {abs_source}: {exc}")

    fmt = probe_data.get("format", {})
    duration = float(fmt.get("duration", 0)) if fmt.get("duration") else 0.0
    if duration and at_seconds > duration:
        _error_exit(
            "screenshot",
            f"Timecode {at_seconds}s is beyond source duration {duration}s",
        )

    video_stream = next(
        (s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    fps = _parse_fps(video_stream.get("r_frame_rate")) if video_stream else None

    if output is None:
        # JPEG default — 5x more token-efficient than PNG once
        # base64-encoded, with no visible quality loss for agent
        # verification / multimodal use cases. Pass --out foo.png
        # to opt into lossless PNG.
        # Auto-name suffixes on collision (#25) — iterating agents
        # don't silently overwrite prior shots.
        output = _resolve_unique_output_path(
            f"screenshot_{at_seconds:.3f}s.jpg"
        )
    abs_output = os.path.realpath(output)
    _validate_output_parent("screenshot", abs_output)

    if dry_run:
        result: dict = {
            "dry_run": True,
            "status": "would_extract",
            "mode": "file",
            "would_write_to": abs_output,
            "source": abs_source,
            "timecode": format_timecode(at_seconds, fps=fps),
            "ffmpeg_command": build_extract_frame_command(
                abs_source, at_seconds, abs_output
            ),
            "hint": (
                "Dry-run only — no screenshot written. Re-run without "
                "--dry-run to capture this frame."
            ),
        }
        click.echo(json.dumps(result, indent=2))
        return

    try:
        ffmpeg_cmd = extract_frame(abs_source, at_seconds, abs_output)
    except FFmpegNotFoundError as exc:
        _error_exit("screenshot", str(exc))
    except FileNotFoundError as exc:
        _error_exit("screenshot", str(exc))
    except RuntimeError as exc:
        _error_exit("screenshot", str(exc))

    width, height = _probe_output_dimensions(abs_output)

    result: dict = {
        "mode": "file",
        "out": abs_output,
        "source": abs_source,
        "timecode": format_timecode(at_seconds, fps=fps),
        "width": width,
        "height": height,
        "file_size_bytes": os.path.getsize(abs_output),
        "ffmpeg_command": ffmpeg_cmd,
    }
    # Embedding is opt-in via --inline (issue #319, flipping #35).
    # Format lives in the nested image object so the response shape
    # matches skim/inspect thumbnail entries.
    if not paths_only:
        result["image"] = _read_image_inline(abs_output)
    click.echo(json.dumps(result, indent=2))


def _screenshot_in_project(
    at_seconds: float,
    output: str | None,
    paths_only: bool,
    source_arg: str | None,
    dry_run: bool,
) -> None:
    """Project mode: --at is in result-time. Resolve through the spec."""
    if not is_loaded():
        _error_exit_with_hint(
            "screenshot",
            "No file argument and no project in current directory.",
            "Pass a video path with 'moviestar screenshot --file <video> "
            "--at <tc>' or run 'moviestar load <video>' first to use "
            "result-time mode.",
        )

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("screenshot", f"Could not read project.json: {exc}")

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("screenshot", str(exc))

    composition = spec.get("composition")
    if source_arg is None and _is_layout_composition(composition):
        if _is_scene_layout_composition(composition):
            _screenshot_scene_layout_composition(
                project=project,
                spec=spec,
                composition=composition,
                at_seconds=at_seconds,
                output=output,
                paths_only=paths_only,
                dry_run=dry_run,
            )
            return

    source_id = _resolve_source_arg(project, source_arg, "screenshot")
    source = _get_project_source(project, source_id)
    abs_source = os.path.realpath(source["path"])
    source_duration = float(source["duration"]["seconds"])

    timeline = resolve_source(spec, source_id, source_duration)
    effective = timeline.effective_duration

    # Strict bounds for screenshot: a single-point timecode "1.5s into a
    # 1s timeline" is unambiguously beyond — no epsilon needed (unlike
    # trim's range bounds, which allow a small slack to forgive float
    # comparisons against the effective end). A tiny tolerance covers
    # float-arithmetic noise when the user types the exact end second.
    if at_seconds < 0 or at_seconds > effective + 0.001:
        _error_exit(
            "screenshot",
            f"Timecode {at_seconds}s exceeds result duration of {effective}s",
        )

    # Walk segments so result-time past a cut seam maps to the right
    # source position. For trim-only sources this is equivalent to
    # `source_range[0] + at_seconds`; multi-segment sources need the
    # segment walk to skip the dropped cut hole(s).
    source_seconds = result_to_source_time(
        timeline.source_segments, at_seconds
    )

    fps = source.get("fps")

    if output is None:
        # JPEG default — see the docstring on `screenshot` for the
        # token-efficiency reasoning. Pass --out foo.png for PNG.
        # Auto-name suffixes on collision (#25).
        output = _resolve_unique_output_path(
            f"screenshot_{at_seconds:.3f}s.jpg"
        )
    abs_output = os.path.realpath(output)
    _validate_output_parent("screenshot", abs_output)

    if dry_run:
        result = {
            "dry_run": True,
            "status": "would_extract",
            "mode": "project",
            "would_write_to": abs_output,
            "source": abs_source,
            "result_timecode": format_timecode(at_seconds, fps=fps),
            "source_timecode": format_timecode(source_seconds, fps=fps),
            "operations_applied": timeline.operations_applied,
            "ffmpeg_command": build_extract_frame_command(
                abs_source, source_seconds, abs_output
            ),
            "hint": (
                "Dry-run only — no screenshot written. Re-run without "
                "--dry-run to capture this project frame."
            ),
        }
        click.echo(json.dumps(result, indent=2))
        return

    try:
        ffmpeg_cmd = extract_frame(abs_source, source_seconds, abs_output)
    except FFmpegNotFoundError as exc:
        _error_exit("screenshot", str(exc))
    except FileNotFoundError as exc:
        _error_exit("screenshot", str(exc))
    except RuntimeError as exc:
        _error_exit("screenshot", str(exc))

    width, height = _probe_output_dimensions(abs_output)

    result: dict = {
        "mode": "project",
        "out": abs_output,
        "source": abs_source,
        "result_timecode": format_timecode(at_seconds, fps=fps),
        "source_timecode": format_timecode(source_seconds, fps=fps),
        "operations_applied": timeline.operations_applied,
        "width": width,
        "height": height,
        "file_size_bytes": os.path.getsize(abs_output),
        "ffmpeg_command": ffmpeg_cmd,
    }
    if not paths_only:
        result["image"] = _read_image_inline(abs_output)
    click.echo(json.dumps(result, indent=2))



def _screenshot_scene_layout_composition(
    *,
    project: dict,
    spec: dict,
    composition: list[dict],
    at_seconds: float,
    output: str | None,
    paths_only: bool,
    dry_run: bool,
) -> None:
    plan, render_plan = _scene_render_plan_at(
        project=project,
        spec=spec,
        composition=composition,
        at_s=at_seconds,
        command="screenshot",
    )
    planned_overlays = _overlay_render_context(
        project,
        spec,
        render_plan["canvas_tuple"],
        at_seconds,
        at_seconds + 0.001,
        "screenshot",
    )
    overlay_plans = planned_overlays["plans"] if planned_overlays else None
    if output is None:
        output = _resolve_unique_output_path(
            f"screenshot_{at_seconds:.3f}s.jpg"
        )
    abs_output = os.path.realpath(output)
    _validate_output_parent("screenshot", abs_output)
    active_scene = render_plan["scene_window"]
    slots_at_time = []
    for slot in _instant_render_slots(render_plan, at_seconds):
        slots_at_time.append(
            {
                "slot": slot["slot"],
                "source": slot["source"],
                "source_timecode": slot["source_range"]["from"],
                "region": slot["region"],
                **(
                    {"region_is": slot["region_is"]}
                    if slot.get("region_is") is not None
                    else {}
                ),
                **(
                    {"geometry_source": slot["geometry_source"]}
                    if slot.get("geometry_source") is not None
                    else {}
                ),
                **(
                    {"region_at": slot["region_at"]}
                    if slot.get("region_at") is not None
                    else {}
                ),
                **(
                    {"motion": slot["motion"]}
                    if slot.get("motion") is not None
                    else {}
                ),
                **(
                    {"shape": slot["shape"]}
                    if slot.get("shape") is not None
                    else {}
                ),
                "framing": slot["framing"],
                "camera": render_plan.get("camera_states", {}).get(slot["slot"]),
            }
        )
    scene_out = dict(active_scene)
    scene_out["slots"] = slots_at_time
    base_result = {
        "mode": "scene_composition",
        "composition_type": "scenes",
        "composition_canvas": plan["canvas"],
        "composition_duration": format_timecode(plan["composition_duration"]),
        "result_timecode": format_timecode(at_seconds),
        "active_scene": scene_out,
        "camera_render_status": render_plan.get("camera_render_status"),
    }
    if planned_overlays is not None:
        _attach_overlay_envelope(base_result, planned_overlays)
    ffmpeg_cmd = build_render_layout_frame_command(
        render_plan["render_slots"],
        abs_output,
        render_plan["canvas_tuple"],
        overlay_plans=overlay_plans,
        highlight_ass=_highlight_ass_arg(planned_overlays),
    )
    if dry_run:
        result = {
            **base_result,
            "dry_run": True,
            "status": "would_extract",
            "would_write_to": abs_output,
            "render_available": True,
            "rerunnable": True,
            "ffmpeg_command": ffmpeg_cmd,
            "hint": (
                "Dry-run only - no screenshot written. Re-run without "
                "--dry-run to capture this scene frame."
            ),
        }
        click.echo(json.dumps(result, indent=2))
        return

    _write_highlight_ass(planned_overlays)
    try:
        ffmpeg_cmd = render_layout_frame(
            render_plan["render_slots"],
            abs_output,
            render_plan["canvas_tuple"],
            overlay_plans=overlay_plans,
            highlight_ass=_highlight_ass_arg(planned_overlays),
        )
    except FFmpegNotFoundError as exc:
        _error_exit("screenshot", str(exc))
    except FileNotFoundError as exc:
        _error_exit("screenshot", str(exc))
    except RuntimeError as exc:
        _error_exit("screenshot", str(exc))

    width, height = _probe_output_dimensions(abs_output)
    result = {
        **base_result,
        "status": "extracted",
        "out": abs_output,
        "width": width,
        "height": height,
        "file_size_bytes": os.path.getsize(abs_output),
        "ffmpeg_command": ffmpeg_cmd,
    }
    if not paths_only:
        result["image"] = _read_image_inline(abs_output)
    click.echo(json.dumps(result, indent=2))


# ---------- Storyboard (issue #303) ----------
#
# ~15 tiles is the sweet spot for one sheet: multimodal APIs normalize a
# composite to ~1.15 megapixels regardless of file size, so every tile
# added shrinks the pixel share of the rest. At 15 tiles UI text is
# legible; by 60 only scene structure survives — hence the hard cap.
STORYBOARD_TARGET_FRAMES = 15
STORYBOARD_MAX_FRAMES = 60
STORYBOARD_TILE_WIDTH = 384
STORYBOARD_MIN_INTERVAL_SECONDS = 0.01
_STORYBOARD_NICE_INTERVALS = (
    0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0,
    45.0, 60.0, 120.0, 300.0, 600.0, 900.0, 1800.0, 3600.0,
)


def _auto_storyboard_interval(range_duration: float) -> float:
    """Round interval whose tile count lands closest to the ~15 target.

    Closest-count beats smallest-round-value-over-target: for a 90-second
    video, 5s/18 tiles is fuller and finer than 10s/9 tiles. Ties prefer the
    coarser interval.
    """
    best: tuple[int, float] | None = None
    for nice in _STORYBOARD_NICE_INTERVALS:
        if nice >= range_duration:
            continue
        count = math.ceil(round(range_duration / nice, 9))
        if count > STORYBOARD_MAX_FRAMES:
            continue
        score = abs(count - STORYBOARD_TARGET_FRAMES)
        if best is None or score <= best[0]:
            best = (score, nice)
    if best is not None:
        return best[1]
    # Range shorter than the finest ladder step: bisect it.
    return round(max(range_duration / 2, 0.001), 3)


def _storyboard_columns_fit(frame_count: int) -> int:
    """Default column count: fill the grid rather than leave holes.

    9 frames tile 3x3, not 5x2-with-a-black-hole (2026-07-20 friction
    round misread the hole as content). Considers 3-5 columns for
    larger counts, minimizing padding cells; ties go to the wider
    grid. Explicit --columns bypasses this.
    """
    if frame_count <= 5:
        return frame_count
    best = 3
    best_padding: int | None = None
    for candidate in (3, 4, 5):
        padding = (
            candidate * math.ceil(frame_count / candidate) - frame_count
        )
        if best_padding is None or padding <= best_padding:
            best, best_padding = candidate, padding
    return best


def _storyboard_cap_suggestion(range_duration: float) -> float:
    """Coarsest-necessary round interval that fits under the tile cap."""
    raw = range_duration / STORYBOARD_MAX_FRAMES
    for nice in _STORYBOARD_NICE_INTERVALS:
        if nice >= raw - 1e-9 and nice < range_duration:
            return nice
    return round(raw, 3)


def _compact_timecode_label(seconds: float) -> str:
    """Short burn-in label: M:SS or H:MM:SS, with milliseconds only when
    the sampling is sub-second (labels stay glanceable at tile size)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - hours * 3600 - minutes * 60
    frac = round(secs - int(secs), 3)
    if frac:
        sec_text = f"{secs:06.3f}".rstrip("0").rstrip(".")
    else:
        sec_text = f"{int(secs):02d}"
    if hours:
        return f"{hours}:{minutes:02d}:{sec_text}"
    return f"{minutes}:{sec_text}"


def _storyboard_plan(
    total_duration: float,
    domain_label: str,
    interval: float | None,
    from_s: float | None,
    to_s: float | None,
    columns: int | None,
) -> dict:
    """Validate range + interval and lay out the sampling grid.

    Calls _error_exit and never returns on validation failure.
    Sampling starts at the range start: frame k lands at
    from + k * interval, k while t < to (same exclusive upper bound
    as inspect's fps-filter semantics).
    """
    from_s = 0.0 if from_s is None else from_s
    if to_s is None:
        to_s = total_duration
    elif to_s > total_duration + 0.001:
        _error_exit(
            "storyboard",
            f"--to ({to_s}s) exceeds {domain_label} of {total_duration}s",
        )
    to_s = min(to_s, total_duration)
    if from_s >= to_s:
        _error_exit(
            "storyboard",
            f"--from ({from_s}s) must be before --to ({to_s}s)",
        )
    range_duration = to_s - from_s

    if interval is None:
        interval = _auto_storyboard_interval(range_duration)
        interval_auto = True
    else:
        interval_auto = False
        if interval >= range_duration:
            _error_exit_with_hint(
                "storyboard",
                f"--interval ({interval}s) is >= the sampled range "
                f"({range_duration:.3f}s); this would yield only 1 tile.",
                "Lower --interval, widen the range with --from/--to, or "
                "omit --interval to auto-pick one.",
            )

    # Same count formula as inspect — ceil with a round() to absorb
    # IEEE-754 fuzz from divisions like 1.5/0.5.
    frame_count = math.ceil(round(range_duration / interval, 9))
    if frame_count > STORYBOARD_MAX_FRAMES:
        suggestion = _storyboard_cap_suggestion(range_duration)
        _error_exit_with_hint(
            "storyboard",
            f"--interval ({interval}s) over this range yields "
            f"{frame_count} tiles; storyboard caps at "
            f"{STORYBOARD_MAX_FRAMES} so each tile stays legible.",
            f"Raise --interval (try --interval {suggestion}) or narrow "
            "the range with --from/--to and storyboard it in sections.",
        )

    frame_times = [round(from_s + k * interval, 3) for k in range(frame_count)]
    if columns is None:
        columns = _storyboard_columns_fit(frame_count)
    return {
        "from_s": from_s,
        "to_s": to_s,
        "interval": interval,
        "interval_auto": interval_auto,
        "frame_count": frame_count,
        "frame_times": frame_times,
        "columns": columns,
        "rows": math.ceil(frame_count / columns),
    }


def _storyboard_font(no_labels: bool) -> tuple[str | None, bool, dict | None]:
    """Resolve the label font. Returns (font_path, labels_enabled,
    warning). Falls back to an unlabeled sheet rather than failing the
    whole render if the bundled font is somehow unavailable."""
    if no_labels:
        return None, False, None
    try:
        font = resolve_font(DEFAULT_FONT_FAMILY, "bold")
        return font["path"], True, None
    except FontResolutionError as exc:
        return None, False, _warning(
            "storyboard_labels_unavailable",
            f"Timecode labels skipped: {exc}",
        )


def _storyboard_hint(mode: str, labels_enabled: bool) -> str:
    domain = "result" if mode == "project" else "source"
    if labels_enabled:
        mapping = f"labels are {domain} timecodes"
    else:
        mapping = f"the frames list maps each tile to its {domain} timecode"
    if mode == "project":
        return (
            f"Tiles read left-to-right, top-to-bottom; {mapping} with "
            "edits applied. Zoom with --from/--to, use 'moviestar "
            "inspect' for dense review of a region, or 'moviestar "
            "screenshot --at <tc>' for one full-resolution frame."
        )
    return (
        f"Tiles read left-to-right, top-to-bottom; {mapping}. Zoom with "
        "--from/--to, use 'moviestar screenshot <video> --at <tc>' for "
        "one full-resolution frame, or 'moviestar load <video>' to "
        "start editing."
    )


def _render_storyboard_composite(
    abs_source: str,
    extraction_times: list[float],
    labels: list[str] | None,
    columns: int,
    rows: int,
    abs_output: str,
    font_path: str | None,
) -> list[str]:
    """Extract per-tile frames into a temp dir, then tile the composite.

    Per-frame extraction (vs one fps-filter pass) keeps reported
    timecodes exact and lets project mode sample across cut seams;
    fast-seek per frame also beats a full decode on long sources.
    Returns the tile-pass argv.
    """
    tmp_dir = tempfile.mkdtemp(prefix="moviestar-storyboard-")
    try:
        for i, at_seconds in enumerate(extraction_times):
            extract_storyboard_frame(
                abs_source,
                at_seconds,
                os.path.join(tmp_dir, f"tile_{i + 1:04d}.jpg"),
                scale_width=STORYBOARD_TILE_WIDTH,
                label=labels[i] if labels else None,
                font_path=font_path,
            )
        # Fill leftover grid cells with explicit gray "(end)" tiles so
        # padding can't be misread as black video content (2026-07-20
        # friction round). Sized to match the real tiles.
        padding = columns * rows - len(extraction_times)
        if padding > 0:
            width, height = _probe_output_dimensions(
                os.path.join(tmp_dir, "tile_0001.jpg")
            )
            width = width or STORYBOARD_TILE_WIDTH
            height = height or STORYBOARD_TILE_WIDTH * 9 // 16
            for j in range(padding):
                render_storyboard_padding_tile(
                    width,
                    height,
                    os.path.join(
                        tmp_dir,
                        f"tile_{len(extraction_times) + 1 + j:04d}.jpg",
                    ),
                    label="(end)",
                    font_path=font_path,
                )
        return compose_storyboard_tiles(
            os.path.join(tmp_dir, "tile_%04d.jpg"),
            columns,
            rows,
            abs_output,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _storyboard_emit(
    *,
    mode: str,
    abs_source: str,
    plan: dict,
    frames: list[dict],
    extraction_times: list[float],
    label_times: list[float],
    output: str | None,
    no_labels: bool,
    paths_only: bool,
    dry_run: bool,
    extra: dict,
) -> None:
    """Shared tail for both modes: font, output path, render, envelope."""
    font_path, labels_enabled, label_warning = _storyboard_font(no_labels)
    warnings = [label_warning] if label_warning else []
    # Issue #358: probe drawtext once before the tile loop and degrade
    # to an unlabeled sheet instead of surfacing raw FFmpeg stderr
    # after partially rendering. Dry runs stay probe-free.
    if labels_enabled and not dry_run:
        drawtext_warning = _missing_drawtext_author_warning()
        if drawtext_warning is not None:
            font_path = None
            labels_enabled = False
            drawtext_warning["message"] = (
                "Timecode labels skipped: " + drawtext_warning["message"]
            )
            warnings.append(drawtext_warning)
    labels = (
        [_compact_timecode_label(t) for t in label_times]
        if labels_enabled
        else None
    )

    if output is None:
        # Auto-name suffixes on collision (#25); explicit --out overwrites.
        output = _resolve_unique_output_path("storyboard.jpg")
    abs_output = os.path.realpath(output)
    _validate_output_parent("storyboard", abs_output)

    common = {
        "mode": mode,
        "source": abs_source,
        **extra,
        "range": {
            "from": format_timecode(plan["from_s"]),
            "to": format_timecode(plan["to_s"]),
            "duration": format_timecode(plan["to_s"] - plan["from_s"]),
        },
        "interval": format_timecode(plan["interval"]),
        "interval_auto": plan["interval_auto"],
        "grid": {"columns": plan["columns"], "rows": plan["rows"]},
        "tile_width": STORYBOARD_TILE_WIDTH,
        "frame_count": plan["frame_count"],
        "padding_cells": (
            plan["columns"] * plan["rows"] - plan["frame_count"]
        ),
        "frames": frames,
        "labels": labels_enabled,
    }

    if dry_run:
        result = {
            "dry_run": True,
            "status": "would_render",
            **common,
            "would_write_to": abs_output,
            "hint": (
                "Dry-run only — no storyboard written. Re-run without "
                "--dry-run to render the contact sheet."
            ),
        }
        _add_warnings(result, warnings)
        click.echo(json.dumps(result, indent=2))
        return

    try:
        tile_cmd = _render_storyboard_composite(
            abs_source,
            extraction_times,
            labels,
            plan["columns"],
            plan["rows"],
            abs_output,
            font_path,
        )
    except (FFmpegNotFoundError, FileNotFoundError, RuntimeError) as exc:
        _error_exit("storyboard", str(exc))

    hint = _storyboard_hint(mode, labels_enabled)
    if paths_only:
        hint += f" View the sheet by reading {_path_for_hint(abs_output)}."
    padding_cells = common["padding_cells"]
    if padding_cells:
        hint += (
            f" The final {padding_cells} grid cell(s) are gray '(end)' "
            "padding, not video frames."
        )
    width, height = _probe_output_dimensions(abs_output)
    result = {
        **common,
        "out": abs_output,
        "width": width,
        "height": height,
        "file_size_bytes": os.path.getsize(abs_output),
        "ffmpeg_command": tile_cmd,
        "hint": hint,
    }
    _add_warnings(result, warnings)
    if not paths_only:
        result["image"] = _read_image_inline(abs_output)
    click.echo(json.dumps(result, indent=2))


@cli.command()
@project_workspace_option
@click.argument("video", type=click.Path(), required=False)
@click.option(
    "--interval",
    "interval_arg",
    default=None,
    help="Seconds between sampled frames (timecode formats accepted). "
    "Omitted: auto-picks a round interval targeting ~15 tiles.",
)
@click.option(
    "--from",
    "from_arg",
    default=None,
    help="Start of the sampled range (default 0). Result-time in "
    "project mode.",
)
@click.option(
    "--to",
    "to_arg",
    default=None,
    help="End of the sampled range (default: full duration).",
)
@click.option(
    "--columns",
    "columns",
    type=click.IntRange(1, 12),
    default=None,
    help="Grid columns (default: 3-5, chosen to fill the grid). "
    "Leftover cells render as gray '(end)' tiles, never black.",
)
@click.option(
    "--out",
    "output",
    default=None,
    help="Output image path. Auto-generated storyboard.jpg if omitted "
    "(suffixes _2, _3, ... on repeat invocations rather than "
    "overwriting). Explicit --out paths overwrite.",
)
@click.option(
    "--no-labels",
    "no_labels",
    is_flag=True,
    help="Skip burned-in timecode labels for pristine frames.",
)
@click.option(
    "--inline",
    "inline",
    is_flag=True,
    help="Embed base64 image bytes under image: {format, base64}. Only "
    "useful for frameworks that convert envelope bytes into image "
    "blocks; CLI harnesses should read the returned image files instead.",
)
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID for multi-source projects (project mode only). "
    "Optional in single-source projects (defaults to the only source). "
    "Has no effect in file mode.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Validate and print the sampling plan without writing the image.",
)
def storyboard(
    video: str | None,
    interval_arg: str | None,
    from_arg: str | None,
    to_arg: str | None,
    columns: int | None,
    output: str | None,
    no_labels: bool,
    inline: bool,
    source_arg: str | None,
    dry_run: bool,
) -> None:
    """Contact sheet: one composite image sampling the whole video.

    The visual overview primitive for silent recordings and product
    demos — evenly sampled frames tiled into a single image, each tile
    labeled with its timecode, so one glance maps the entire recording
    before deciding what to edit or extract.

    \b
    Two modes:
      moviestar storyboard <video>   raw file, source-time sampling
      moviestar storyboard           inside a loaded project — samples
                                     result-time through the edit spec
                                     (what export would render)

    \b
    Sampling:
      --interval omitted   auto-picks the round interval whose tile
                           count lands nearest ~15 (may run a few
                           over); the envelope reports the choice
      --interval <tc>      explicit spacing, capped at 60 tiles
      --from/--to          storyboard a sub-range. Half-open: tiles
                           land at from, from+interval, ... strictly
                           before to (the end timecode itself is not
                           sampled). On long videos zoom in rather
                           than raising the tile count.

    ~15 tiles keeps text-level detail legible to multimodal models on
    one sheet; past ~60 the tiles only convey scene structure, so the
    cap refuses with a concrete coarser suggestion.

    Timecode labels burn into each tile (bundled Inter font) so the
    image itself carries the frame-to-time mapping. Pass --no-labels
    for pristine frames; the ``frames`` list in the envelope keeps the
    mapping either way.

    The composite is delivered as the file at ``out`` — view it with
    your file reader. Pass --inline to additionally embed it as base64
    under ``image: {format, base64}`` like skim/screenshot.

    For dense frame-by-frame review of a narrow range use 'moviestar
    inspect'; for one full-resolution frame use 'moviestar screenshot'.
    """
    interval = None
    if interval_arg is not None:
        try:
            interval = parse_timecode(interval_arg)
        except ValueError as exc:
            _error_exit("storyboard", str(exc))
        if interval < STORYBOARD_MIN_INTERVAL_SECONDS:
            _error_exit(
                "storyboard",
                f"--interval must be at least "
                f"{STORYBOARD_MIN_INTERVAL_SECONDS} seconds",
            )
    from_s: float | None = None
    to_s: float | None = None
    try:
        if from_arg is not None:
            from_s = parse_timecode(from_arg)
        if to_arg is not None:
            to_s = parse_timecode(to_arg)
    except ValueError as exc:
        _error_exit("storyboard", str(exc))

    paths_only = not inline
    if video is None:
        _storyboard_in_project(
            interval, from_s, to_s, columns, output, no_labels,
            paths_only, source_arg, dry_run,
        )
        return
    _storyboard_file_mode(
        video, interval, from_s, to_s, columns, output, no_labels,
        paths_only, dry_run,
    )


def _storyboard_file_mode(
    video: str,
    interval: float | None,
    from_s: float | None,
    to_s: float | None,
    columns: int | None,
    output: str | None,
    no_labels: bool,
    paths_only: bool,
    dry_run: bool,
) -> None:
    """File mode: sample a raw file in source time. No project awareness."""
    abs_source = os.path.realpath(video)
    if not os.path.exists(abs_source):
        _error_exit("storyboard", f"File not found: {abs_source}")

    try:
        probe_data = run_ffprobe(abs_source)
    except FFmpegNotFoundError as exc:
        _error_exit("storyboard", str(exc))
    except (RuntimeError, json.JSONDecodeError) as exc:
        _error_exit("storyboard", f"Could not probe {abs_source}: {exc}")

    fmt = probe_data.get("format", {})
    duration = float(fmt.get("duration", 0)) if fmt.get("duration") else 0.0
    if not duration:
        _error_exit(
            "storyboard", f"Could not determine duration of {abs_source}"
        )
    video_stream = next(
        (
            s
            for s in probe_data.get("streams", [])
            if s.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        _error_exit("storyboard", f"No video stream in {abs_source}")
    fps = _parse_fps(video_stream.get("r_frame_rate"))

    plan = _storyboard_plan(
        duration, "source duration", interval, from_s, to_s, columns
    )
    frames = [
        {"index": i, "timecode": format_timecode(t, fps=fps)}
        for i, t in enumerate(plan["frame_times"])
    ]
    _storyboard_emit(
        mode="file",
        abs_source=abs_source,
        plan=plan,
        frames=frames,
        extraction_times=plan["frame_times"],
        label_times=plan["frame_times"],
        output=output,
        no_labels=no_labels,
        paths_only=paths_only,
        dry_run=dry_run,
        extra={},
    )


def _storyboard_in_project(
    interval: float | None,
    from_s: float | None,
    to_s: float | None,
    columns: int | None,
    output: str | None,
    no_labels: bool,
    paths_only: bool,
    source_arg: str | None,
    dry_run: bool,
) -> None:
    """Project mode: sample result-time through the edit spec."""
    if not is_loaded():
        _error_exit_with_hint(
            "storyboard",
            "No video argument and no project in current directory.",
            "Pass a video path (e.g. 'moviestar storyboard <video>') or "
            "run 'moviestar load <video>' first to use result-time mode.",
        )

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("storyboard", f"Could not read project.json: {exc}")

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("storyboard", str(exc))

    composition = spec.get("composition")
    if source_arg is None and _is_layout_composition(composition):
        _error_exit_with_hint(
            "storyboard",
            "This project uses a layout composition; storyboard samples "
            "one source timeline at a time.",
            "Pass --source <id> to storyboard a single source's edited "
            "timeline, or use 'moviestar screenshot --at <tc>' to render "
            "the composed canvas at one moment.",
        )

    source_id = _resolve_source_arg(project, source_arg, "storyboard")
    source = _get_project_source(project, source_id)
    abs_source = os.path.realpath(source["path"])
    source_duration = float(source["duration"]["seconds"])

    timeline = resolve_source(spec, source_id, source_duration)
    fps = source.get("fps")

    plan = _storyboard_plan(
        timeline.effective_duration,
        "result duration",
        interval,
        from_s,
        to_s,
        columns,
    )
    result_times = plan["frame_times"]
    # Walk segments per sample so result-time past a cut seam maps to
    # the right source position (same mapping screenshot uses).
    source_times = [
        round(result_to_source_time(timeline.source_segments, t), 3)
        for t in result_times
    ]
    frames = [
        {
            "index": i,
            "result_timecode": format_timecode(rt, fps=fps),
            "source_timecode": format_timecode(st, fps=fps),
        }
        for i, (rt, st) in enumerate(zip(result_times, source_times))
    ]
    _storyboard_emit(
        mode="project",
        abs_source=abs_source,
        plan=plan,
        frames=frames,
        extraction_times=source_times,
        label_times=result_times,
        output=output,
        no_labels=no_labels,
        paths_only=paths_only,
        dry_run=dry_run,
        extra={
            "source_id": source_id,
            "operations_applied": timeline.operations_applied,
        },
    )


def _validate_output_parent(command: str, abs_output: str) -> None:
    output_parent = os.path.dirname(abs_output)
    if output_parent and not os.path.isdir(output_parent):
        _error_exit(
            command,
            f"Cannot write to {abs_output}: parent directory does not exist",
        )


def _probe_output_dimensions(path: str) -> tuple[int | None, int | None]:
    """Probe a rendered file for width/height. Returns (None, None) if probe fails."""
    try:
        out_probe = run_ffprobe(path)
        out_stream = next(
            (s for s in out_probe.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        return out_stream.get("width"), out_stream.get("height")
    except (RuntimeError, json.JSONDecodeError, FFmpegNotFoundError):
        return None, None


# ---------- Editing ----------


def _result_range(result_duration: float) -> dict:
    """The result clip's timeline always starts at 0; only `to` varies."""
    return {
        "from": format_timecode(0.0),
        "to": format_timecode(result_duration),
        "duration": format_timecode(result_duration),
    }


def _timeline_output(timeline) -> dict:
    """Shape the post-op state for trim/cut/undo/spec responses.

    Always emits ``segments_count`` and ``source_segments`` (truthful
    on any timeline). ``source_range`` (the outer envelope, single
    tuple) is only emitted when there's exactly one segment — on
    multi-segment timelines it would lie about the content. Same shape
    as status's ``edit`` block from step 3's friction fix.
    """
    segments = timeline.source_segments
    out: dict = {
        "segments_count": len(segments),
        "source_segments": [
            _segment_envelope(seg_from, seg_to)
            for seg_from, seg_to in segments
        ],
        "result_range": _result_range(timeline.effective_duration),
        "result_duration": format_timecode(timeline.effective_duration),
        "operations_applied": timeline.operations_applied,
    }
    if len(segments) == 1:
        src_from, src_to = timeline.source_range
        out["source_range"] = {
            "from": format_timecode(src_from),
            "to": format_timecode(src_to),
            "duration": format_timecode(src_to - src_from),
        }
    return out


@cli.command()
@project_workspace_option
@click.option("--from", "from_tc", help="Trim start timecode (in result-time).")
@click.option("--to", "to_tc", help="Trim end timecode (in result-time).")
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID for multi-source projects. Optional in single-"
    "source projects (defaults to the only source).",
)
@click.option(
    "--snap-to-words",
    "snap",
    is_flag=True,
    help="Snap --from / --to outward to whole words in the transcript "
    "so cuts don't fall mid-syllable. Requires a transcript.",
)
def trim(
    from_tc: str | None,
    to_tc: str | None,
    source_arg: str | None,
    snap: bool,
) -> None:
    """Trim to a timecode range. Appends to the edit spec.

    Trim timecodes are interpreted in *result-time* —
    a second trim narrows the current result, not the original source.
    On the first trim, result-time equals source-time. The resolver
    composes the stack into a source-space range for export.

    \b
    Timecode formats accepted:
      --from 5            seconds (float)
      --from 1:23         M:SS shorthand
      --from 0:01:23.500  HH:MM:SS.mmm

    With --snap-to-words, --from / --to falling inside a word
    (mid-syllable) snap *outward* to that word's start/end so the
    full word is preserved. Boundaries that fall in gaps between
    words are unchanged. The response reports both the requested
    timecodes and the post-snap values so you can see what shifted.
    """
    if not is_loaded():
        _no_project_error_exit("trim")

    if from_tc is None and to_tc is None:
        _error_exit(
            "trim",
            "Pass at least one of --from or --to.",
        )

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("trim", f"Could not read project.json: {exc}")

    source_id = _resolve_source_arg(project, source_arg, "trim")
    source = _get_project_source(project, source_id)
    source_duration = float(source["duration"]["seconds"])

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("trim", str(exc))

    current = resolve_source(spec, source_id, source_duration)

    try:
        from_s = parse_timecode(from_tc) if from_tc is not None else 0.0
        to_s = (
            parse_timecode(to_tc)
            if to_tc is not None
            else current.effective_duration
        )
    except ValueError as exc:
        _error_exit("trim", str(exc))

    requested_from_s = from_s
    requested_to_s = to_s

    if snap:
        transcript = load_transcript(source)
        if transcript is None:
            skip_reason = source.get("transcription_skipped_reason") or "unknown"
            payload = {
                "error": (
                    f"trim: --snap-to-words requires a transcript, but this "
                    f"project has none (transcription_skipped_reason: "
                    f"{skip_reason!r})."
                ),
                "command": "trim",
                "transcript_unavailable_reason": skip_reason,
                "hint": (
                    "Re-run 'moviestar load --force' (without --no-transcribe) "
                    "to add the transcript, then retry. Or drop --snap-to-words "
                    "to trim against raw timecodes."
                ),
            }
            click.echo(json.dumps(payload, indent=2))
            sys.exit(1)

        # Words are in source-time; trim from_s/to_s are in current
        # result-time. Translate, snap, translate back.
        cur_src_from = current.source_range[0]
        snapped_from_src, snapped_to_src = snap_boundaries_to_words(
            cur_src_from + from_s,
            cur_src_from + to_s,
            transcript.get("words") or [],
        )
        from_s = round(snapped_from_src - cur_src_from, 3)
        to_s = round(snapped_to_src - cur_src_from, 3)
        # Floor at 0 / cap at effective duration so float-edge snaps
        # don't push us out of the result-time range.
        from_s = max(0.0, from_s)
        to_s = min(current.effective_duration, to_s)

    # Issue #38: pre-check the cumulative-trim case so we can emit a
    # verbose envelope (source-vs-result context + structured hint)
    # *before* the generic SpecValidationError fires. Without this, an
    # agent doing a "fresh" trim from the source's full range hits a
    # terse "exceeds result duration of 1.0s" error and has no way
    # to see the prior trims that shrunk the timeline.
    if (
        current.operations_applied > 0
        and to_s > current.effective_duration + 0.5
    ):
        prior_ops = current.operations_applied
        plural = "trims" if prior_ops != 1 else "trim"
        op_word = "ops" if prior_ops != 1 else "op"
        _emit_trim_cumulative_error(
            from_s=from_s,
            to_s=to_s,
            effective=current.effective_duration,
            source_duration=source_duration,
            prior_ops=prior_ops,
            plural=plural,
            op_word=op_word,
        )

    try:
        new_spec = append_trim(spec, source_id, from_s, to_s, source_duration)
        save_spec(new_spec, command="trim")
    except SpecValidationError as exc:
        _error_exit("trim", str(exc))

    new_timeline = resolve_source(new_spec, source_id, source_duration)
    new_op = get_source(new_spec, source_id)["operations"][-1]

    result: dict = {
        "operation": _op_for_envelope(new_op),
    }
    if snap:
        result["requested_from"] = format_timecode(requested_from_s)
        result["requested_to"] = format_timecode(requested_to_s)
        result["snapped_to_words"] = True
    result.update(_timeline_output(new_timeline))
    result["source"] = {"id": source_id, "path": source["path"]}
    if snap:
        # Use a tolerance so float-arithmetic noise (e.g. requested 38.6
        # rounding to a 38.6000001-ish snapped value) doesn't fire the
        # "shifted" branch. 1ms is well below word-boundary granularity.
        shifted = (
            abs(requested_from_s - from_s) > 0.001
            or abs(requested_to_s - to_s) > 0.001
        )
        if shifted:
            result["hint"] = (
                "Snapped to word boundaries — see requested_from/"
                "requested_to for what shifted. Run 'moviestar undo' to "
                "revert this trim, or 'moviestar export' to render."
            )
        else:
            # Snap was requested but the boundaries were already on word
            # edges — friction-test agent (2026-05-04) flagged this case
            # as silently indistinguishable from "snap shifted things."
            # Acknowledge the no-shift outcome so the agent doesn't have
            # to compare requested_* vs source_range to figure it out.
            result["hint"] = (
                "Boundaries were already on word edges; no shift needed. "
                "Run 'moviestar undo' to revert this trim, or 'moviestar "
                "export' to render."
            )
    else:
        result["hint"] = (
            "Run 'moviestar undo' to revert this trim, or 'moviestar trim' "
            "again to narrow further. 'moviestar export' will render the "
            "edited timeline to MP4."
        )
    _attach_overlay_timeline_mutation(
        result,
        project=project,
        old_spec=spec,
        new_spec=new_spec,
        code="trim_result_clock_changed_overlays_stale",
        change_label="trim",
    )
    click.echo(json.dumps(result, indent=2))


@cli.command()
@project_workspace_option
@click.option("--from", "from_tc", help="Cut start timecode (in result-time).")
@click.option("--to", "to_tc", help="Cut end timecode (in result-time).")
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID for multi-source projects. Optional in single-"
    "source projects (defaults to the only source).",
)
def cut(
    from_tc: str | None,
    to_tc: str | None,
    source_arg: str | None,
) -> None:
    """Remove a range from a source's result. Appends to the edit spec.

    Cut timecodes are interpreted in *result-time* — a cut after a trim
    or another cut operates against the current result, not the original
    source. Internally the resolver stitches the surviving pieces into
    a multi-segment timeline; the agent sees one contiguous result via
    ``result_duration`` and ``source_range``.

    Pass both ``--from`` and ``--to``. "Cut everything before X" or
    "cut everything after X" is the same as ``trim`` from the other
    direction — cut is for removing a slice from the middle.

    \b
    Timecode formats accepted:
      --from 5            seconds (float)
      --from 1:23         M:SS shorthand
      --from 0:01:23.500  HH:MM:SS.mmm

    The response's ``removed`` field names the range that came out (in
    result-time) so the agent doesn't have to compute it from before/
    after durations.
    """
    if not is_loaded():
        _no_project_error_exit("cut")

    if from_tc is None or to_tc is None:
        _error_exit(
            "cut",
            "Pass both --from and --to. Cut removes a range from the "
            "middle of the result; to keep just the start or end, use "
            "'moviestar trim' instead.",
        )

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("cut", f"Could not read project.json: {exc}")

    source_id = _resolve_source_arg(project, source_arg, "cut")
    source = _get_project_source(project, source_id)
    source_duration = float(source["duration"]["seconds"])

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("cut", str(exc))

    try:
        from_s = parse_timecode(from_tc)
        to_s = parse_timecode(to_tc)
    except ValueError as exc:
        _error_exit("cut", str(exc))

    try:
        new_spec = append_cut(spec, source_id, from_s, to_s, source_duration)
        save_spec(new_spec, command="cut")
    except SpecValidationError as exc:
        _error_exit("cut", str(exc))

    new_timeline = resolve_source(new_spec, source_id, source_duration)
    new_op = get_source(new_spec, source_id)["operations"][-1]

    removed_duration = round(to_s - from_s, 3)
    result: dict = {
        "operation": _op_for_envelope(new_op),
        "removed": {
            "from": format_timecode(from_s),
            "to": format_timecode(to_s),
            "duration": format_timecode(removed_duration),
        },
    }
    result.update(_timeline_output(new_timeline))
    result["source"] = {"id": source_id, "path": source["path"]}
    result["hint"] = (
        "Run 'moviestar undo' to revert this cut, or 'moviestar cut' "
        "again to remove another range. 'moviestar export' will render "
        "the edited timeline to MP4."
    )
    _attach_overlay_timeline_mutation(
        result,
        project=project,
        old_spec=spec,
        new_spec=new_spec,
        code="cut_result_clock_changed_overlays_stale",
        change_label="cut",
    )
    click.echo(json.dumps(result, indent=2))


def _segment_request_envelope(
    *,
    source: str,
    src_from: float,
    src_to: float,
    result_from: float,
    framing: dict | None = None,
) -> dict:
    """Format one composition segment for the concat / status envelope.

    Both ranges in dict-timecode form per the moviestar convention.
    ``result_range`` is derived from the segment's position in the
    composition (segments concatenate end-to-end).
    """
    duration = src_to - src_from
    result = {
        "source": source,
        "source_range": {
            "from": format_timecode(src_from),
            "to": format_timecode(src_to),
            "duration": format_timecode(duration),
        },
        "result_range": {
            "from": format_timecode(result_from),
            "to": format_timecode(result_from + duration),
            "duration": format_timecode(duration),
        },
    }
    if framing is not None:
        result["framing"] = framing
    return result


def _format_composition_segments(composition: list[dict]) -> tuple[list[dict], float]:
    """Walk an on-disk composition list and return (envelope_list, total_duration).

    Used by concat's response and status's composition section.
    Result-time placement is computed cumulatively from segment order.
    """
    out: list[dict] = []
    cursor = 0.0
    from moviestar.spec import parse_timecode_string

    for seg in composition:
        src_from = parse_timecode_string(seg["source_from"], "composition.source_from")
        src_to = parse_timecode_string(seg["source_to"], "composition.source_to")
        out.append(
            _segment_request_envelope(
                source=seg["source"],
                src_from=src_from,
                src_to=src_to,
                result_from=cursor,
                framing=seg.get("framing"),
            )
        )
        cursor += src_to - src_from
    return out, round(cursor, 3)


def _concat_presented(spec: dict) -> bool:
    """True when a normalized scene composition should present as flat
    segments: it was authored via `concat` and no motion has been
    authored. Pacing flips presentation to scenes, where result ranges
    stay honest. Controls envelope vocabulary only — capabilities and
    timing always come from the scene model.
    """
    return (
        spec.get("composition_authored_as") == "concat"
        and not (spec.get("motion") or {}).get("scenes")
    )


def _segments_view(composition: list[dict]) -> list[dict]:
    """Project normalized single-slot scenes back to flat segment dicts
    for concat-vocabulary envelopes (status, undo, export)."""
    out: list[dict] = []
    for scene in composition:
        slot = scene["slots"][0]
        seg = {
            "source": slot["source"],
            "source_from": slot["source_from"],
            "source_to": slot["source_to"],
        }
        if slot.get("framing") is not None:
            seg["framing"] = slot["framing"]
        out.append(seg)
    return out


def _layout_presented(spec: dict) -> bool:
    """True when a normalized scene composition should present — and
    execute — through the global-layout lane: it was authored via
    `concat --layout` and no motion has been authored. Motion flips it
    to the scene lanes, whose envelopes carry pacing honestly.
    Selects the lane until B3 merges them; capabilities are identical
    on both.
    """
    return (
        spec.get("composition_authored_as") == "layout"
        and not (spec.get("motion") or {}).get("scenes")
    )


def _is_layout_composition(composition: list[dict] | None) -> bool:
    return bool(
        composition
        and isinstance(composition[0], dict)
        and ("layout" in composition[0] or "slots" in composition[0])
    )


def _is_scene_layout_composition(composition: list[dict] | None) -> bool:
    return bool(
        _is_layout_composition(composition)
        and (
            len(composition or []) > 1
            or any(
                "name" in scene or "audio_from" in scene
                for scene in composition or []
            )
        )
    )


def _static_slot_geometry_metadata(layout: dict, slot_name: str) -> dict:
    """Label static slot regions without pre-claiming moving-slot semantics."""
    geometry = layout.get("geometry") or {}
    slot_geometry = geometry.get(slot_name) or {}
    if slot_geometry.get("motion") is not None:
        return {}
    overridden = slot_name in geometry or (
        slot_name == "inset" and layout.get("inset") is not None
    )
    return {
        "region_is": "instant",
        "geometry_source": "override" if overridden else "preset",
    }


def _moving_slot_geometry_metadata(layout: dict, slot_name: str) -> dict | None:
    geometry = layout.get("geometry") or {}
    slot_geometry = geometry.get(slot_name) or {}
    motion = slot_geometry.get("motion")
    if motion is None:
        return None
    return {
        "geometry_source": "override",
        "motion": motion,
    }


def _slot_envelope_record(
    *,
    layout: dict,
    slot_name: str,
    canvas: dict,
    resolved_start_region: dict,
) -> tuple[dict, dict]:
    moving = _moving_slot_geometry_metadata(layout, slot_name)
    if moving is None:
        return resolved_start_region, _static_slot_geometry_metadata(
            layout, slot_name
        )
    return (
        {
            "x": 0,
            "y": 0,
            "width": int(canvas["width"]),
            "height": int(canvas["height"]),
        },
        {
            **moving,
            "region_is": "envelope",
            "resolved_start_region": layout_regions(
                layout, canvas, result_time_s=0.0
            )[slot_name],
        },
    )


def _instant_render_slots(render_plan: dict, result_time_s: float) -> list[dict]:
    """Format slot records at one verification-surface result timestamp."""
    render_by_name = {
        slot["slot"]: slot for slot in render_plan["render_slots"]
    }
    out: list[dict] = []
    for slot in render_plan["slots"]:
        record = dict(slot)
        render_slot = render_by_name[slot["slot"]]
        record["region"] = render_slot["region"]
        record.pop("resolved_start_region", None)
        record.pop("range_start_region", None)
        motion = render_slot.get("motion")
        if motion is not None:
            record.update(
                {
                    "region_is": "instant",
                    "region_at": format_timecode(result_time_s),
                    "geometry_source": "override",
                    "motion": {
                        "preset": motion["preset"],
                        "speed": motion["speed"],
                    },
                }
            )
        out.append(record)
    return out


def _format_layout_slots(scene: dict, canvas: dict) -> tuple[list[dict], float]:
    from moviestar.spec import parse_timecode_string

    regions = layout_regions(scene["layout"], canvas, result_time_s=0.0)
    out: list[dict] = []
    durations: list[float] = []
    for slot in scene["slots"]:
        src_from = parse_timecode_string(slot["source_from"], "slot.source_from")
        src_to = parse_timecode_string(slot["source_to"], "slot.source_to")
        duration = src_to - src_from
        durations.append(duration)
        region, geometry_metadata = _slot_envelope_record(
            layout=scene["layout"],
            slot_name=slot["slot"],
            canvas=canvas,
            resolved_start_region=regions[slot["slot"]],
        )
        record = {
            "slot": slot["slot"],
            "source": slot["source"],
            "source_range": {
                "from": format_timecode(src_from),
                "to": format_timecode(src_to),
                "duration": format_timecode(duration),
            },
            "region": region,
            **geometry_metadata,
            "framing": slot.get("framing", {"mode": "fill", "anchor": "center"}),
        }
        if slot.get("id"):
            record["slot_id"] = slot["id"]
        out.append(record)
    return out, round(durations[0] if durations else 0.0, 3)


def _format_layout_scene(
    *,
    scene: dict,
    canvas: dict,
    index: int,
    result_start_s: float,
) -> tuple[dict, float]:
    slots_out, duration_s = _format_layout_slots(scene, canvas)
    out = {
        "scene": scene.get("name", f"scene_{index + 1}"),
        "index": index,
        "result_range": _scene_result_range(result_start_s, duration_s),
        "layout": _layout_envelope(scene["layout"], canvas, result_time_s=0.0),
        "audio_from": scene.get("audio_from"),
        "slots": slots_out,
    }
    if scene.get("id"):
        out["scene_id"] = scene["id"]
    if isinstance(scene.get("transition_in"), dict):
        out["transition_in"] = _transition_record_envelope(
            scene["transition_in"]
        )
    return out, duration_s


def _format_scene_composition(
    composition: list[dict], canvas: dict
) -> tuple[list[dict], float, int]:
    scenes_out: list[dict] = []
    cursor = 0.0
    slots_count = 0
    for index, scene in enumerate(composition):
        scene_out, duration_s = _format_layout_scene(
            scene=scene,
            canvas=canvas,
            index=index,
            result_start_s=cursor,
        )
        scenes_out.append(scene_out)
        slots_count += len(scene_out["slots"])
        cursor += duration_s
    return scenes_out, round(cursor, 3), slots_count



def _scene_composition_plan(
    *,
    spec: dict,
    composition: list[dict],
    command: str,
) -> dict:
    if not _is_scene_layout_composition(composition):
        _error_exit(command, "Current composition is not a scene composition.")
    canvas = spec.get("composition_canvas")
    if canvas is None:
        _error_exit(command, "Scene composition is missing composition_canvas.")
    scenes_out, _identity_duration, slots_count = _format_scene_composition(
        composition, canvas
    )
    try:
        timeline = resolve_pacing(composition, spec.get("motion"))
    except PacingResolutionError as exc:
        _error_exit(command, f"Could not resolve scene pacing: {exc}")
    for scene_out, resolved_scene in zip(scenes_out, timeline.scenes):
        scene_out["result_range"] = _scene_result_range(
            resolved_scene.start_s, resolved_scene.duration_s
        )
    return {
        "canvas": canvas,
        "composition_duration": timeline.duration_s,
        "slots_count": slots_count,
        "scenes": scenes_out,
        "pacing_timeline": timeline,
    }


def _scene_global_audio_route(
    project: dict, spec: dict, composition: list[dict], command: str
) -> tuple[str, str, float, tuple] | None:
    """Context for a composition-wide audio route on a scene composition.

    Returns ``(source_id, path, anchor_s, surviving_segments)`` when
    ``composition_audio_from`` is set, or None. The route overrides
    per-scene audio_from: one continuous track anchored at the routed
    source's first slot appearance, linear at 1x in result time.
    """
    audio_from = spec.get("composition_audio_from")
    if audio_from is None:
        return None
    from moviestar.resolved import composition_audio_anchor

    anchor = composition_audio_anchor(composition, audio_from)
    source = next(
        (s for s in project["sources"] if s["id"] == audio_from), None
    )
    if anchor is None or source is None:
        _error_exit(
            command,
            f"composition_audio_from {audio_from!r} is not assigned to "
            "any scene slot.",
        )
    timeline = resolve_source(
        spec, audio_from, float(source["duration"]["seconds"])
    )
    return audio_from, source["path"], anchor, timeline.source_segments


def _legacy_shape_warnings(spec: dict) -> list[dict]:
    """Structured warning for a picture-in-picture scene without a shape.

    An absent shape marks a legacy scene, which keeps rendering a rectangle
    rather than silently adopting the newer circular default.
    """
    warnings: list[dict] = []
    for scene in spec.get("composition") or []:
        if not isinstance(scene, dict):
            continue
        layout = scene.get("layout")
        if (
            not isinstance(layout, dict)
            or layout.get("preset") != "picture-in-picture"
        ):
            continue
        if _stored_slot_shape(layout, "inset") is not None:
            continue
        name = scene.get("name") or "scene"
        warnings.append(
            _warning(
                "legacy_rect_inset",
                f"Scene {name!r} predates the circular inset default and "
                "renders a rectangular inset.",
                severity="info",
                smallest_fix=(
                    f"'moviestar scenes geometry {name}:inset --shape "
                    "circle' adopts the current default; '--shape rect' "
                    "keeps this look and records the choice"
                ),
            )
        )
    return warnings


def _unrendered_transition_warnings(
    spec: dict,
    *,
    include_internal: bool = True,
) -> list[dict]:
    count = (
        sum(
            isinstance(scene, dict)
            and isinstance(scene.get("transition_in"), dict)
            for scene in (spec.get("composition") or [])
        )
        if include_internal
        else 0
    ) + sum(
        isinstance(spec.get(field), dict)
        for field in ("opening_transition", "closing_transition")
    )
    if count == 0:
        return []
    noun = "transition" if count == 1 else "transitions"
    verb = "is" if count == 1 else "are"
    return [
        _warning(
            "authored_transitions_not_rendered",
            f"{count} authored visual {noun} {verb} persisted but not rendered "
            "on this surface yet. This output uses hard cuts for them.",
            authored_transitions_count=count,
            render_status="not_implemented",
            output_behavior="hard_cuts",
            smallest_fix=(
                "Use 'moviestar scenes transition' to inspect or remove the "
                "authored transitions if hard-cut output is not acceptable."
            ),
        )
    ]


def _stored_slot_shape(layout: dict, slot_name: str) -> dict | None:
    """Return the persisted shape block for one layout slot, if any."""
    geometry = layout.get("geometry")
    if not isinstance(geometry, dict):
        return None
    slot_geometry = geometry.get(slot_name)
    if not isinstance(slot_geometry, dict):
        return None
    shape = slot_geometry.get("shape")
    return shape if isinstance(shape, dict) else None


def _render_slot_shape(shape_block: dict, region: dict) -> dict:
    """Materialize a stored shape for the renderer: generate (or reuse)
    the cached masks sized to the resolved region."""
    kind = shape_block["kind"]
    if kind == "rect":
        render_shape: dict = {"kind": "rect"}
        if shape_block.get("border") is not None:
            render_shape["border"] = copy.deepcopy(shape_block["border"])
        return render_shape
    border = shape_block.get("border")
    masks = _shape_mask_files(
        kind, region, shape_block.get("radius"), border
    )
    render_shape: dict = {"kind": kind, "content_mask": masks["content"]}
    if border is not None:
        render_shape["border"] = {
            "width": int(border["width"]),
            "color": border["color"],
            "disc_mask": masks["border_disc"],
        }
    return render_shape


def _paced_segment_render_plan(
    *,
    project: dict,
    spec: dict,
    resolved_scene,
    segment,
    canvas: dict,
    command: str,
    output_fps: float,
    layout_motion_time_s: float,
    global_audio: tuple[str, str, float, tuple] | None = None,
) -> dict:
    """Turn one resolved pacing segment into an ffmpeg-ready layout clip."""
    from moviestar.spec import slice_audio_from_anchor

    scene = resolved_scene.scene
    regions = layout_regions(
        scene["layout"],
        canvas,
        result_time_s=layout_motion_time_s,
    )
    source_by_id = {source["id"]: source for source in project["sources"]}
    authored_slots = {slot["slot"]: slot for slot in scene["slots"]}
    render_slots: list[dict] = []
    slots_out: list[dict] = []
    for resolved_slot in segment.slots:
        source = source_by_id.get(resolved_slot.source_id)
        if source is None:
            _error_exit(
                command,
                f"Scene slot references unknown source {resolved_slot.source_id!r}.",
            )
        authored = authored_slots[resolved_slot.slot_name]
        region = regions[resolved_slot.slot_name]
        framing = authored.get(
            "framing", {"mode": "fill", "anchor": "center"}
        )
        video_seek_from = resolved_slot.source_from_s
        if (
            not resolved_slot.held
            and video_seek_from > 0
            and os.path.exists(source["path"])
        ):
            # Accurate input seeking discards the frame displayed before a
            # VFR void. Retain that packet so the fps stage can extend the
            # actual on-screen content through a mid-void scene start (#365).
            prior_packet = probe_last_video_packet_timestamp(
                source["path"], video_seek_from
            )
            if prior_packet is not None:
                video_seek_from = max(
                    0.0, min(video_seek_from, prior_packet)
                )
        render_slot = {
            "slot": resolved_slot.slot_name,
            "source": resolved_slot.source_id,
            "path": source["path"],
            "source_from": resolved_slot.source_from_s,
            "source_to": resolved_slot.source_to_s,
            "video_seek_from": video_seek_from,
            "speed": resolved_slot.speed,
            "held": resolved_slot.held,
            "region": region,
            "framing": framing,
        }
        slot_motion = resolve_slot_motion(
            scene["layout"], resolved_slot.slot_name, canvas
        )
        if slot_motion is not None:
            render_slot["motion"] = {
                **slot_motion,
                "result_time_offset": layout_motion_time_s,
            }
        shape_block = _stored_slot_shape(
            scene["layout"], resolved_slot.slot_name
        )
        if shape_block is not None:
            render_slot["shape"] = _render_slot_shape(shape_block, region)
        render_slots.append(render_slot)
        report_region, geometry_metadata = _slot_envelope_record(
            layout=scene["layout"],
            slot_name=resolved_slot.slot_name,
            canvas=canvas,
            resolved_start_region=region,
        )
        if slot_motion is not None:
            geometry_metadata["range_start_region"] = region
        slots_out.append(
            {
                "slot": resolved_slot.slot_name,
                **(
                    {"slot_id": authored["id"]}
                    if authored.get("id")
                    else {}
                ),
                "source": resolved_slot.source_id,
                "source_range": {
                    "from": format_timecode(resolved_slot.source_from_s),
                    "to": format_timecode(resolved_slot.source_to_s),
                    "duration": format_timecode(
                        resolved_slot.consumes_source_s
                    ),
                },
                "effective_speed": resolved_slot.speed,
                "held": resolved_slot.held,
                "values": resolved_slot.values,
                "pacing_id": resolved_slot.pacing_id,
                "region": report_region,
                **geometry_metadata,
                **(
                    {"shape": copy.deepcopy(shape_block)}
                    if shape_block is not None
                    else {}
                ),
                "framing": framing,
            }
        )

    audio_from_input = None
    audio_from_range = None
    audio_speed = 1.0
    audio_note = None
    audio_from_anchor_text = None
    if global_audio is not None:
        # Composition-wide route: overrides per-scene audio_from. Linear
        # at 1x in result time, cut-aware, plays through holds.
        from moviestar.resolved import global_audio_slices

        audio_from, ga_path, ga_anchor, ga_segments = global_audio
        audio_from_source = "composition"
        audio_from_anchor_text = format_timecode(ga_anchor)
        slices = [
            (slice_from, slice_to)
            for slice_from, slice_to in global_audio_slices(
                ga_segments, ga_anchor, segment.start_s, segment.end_s
            )
        ]
        if slices:
            audio_from_input = (ga_path, slices)
            audio_from_range = {
                "from": format_timecode(slices[0][0]),
                "to": format_timecode(slices[-1][1]),
                "duration": format_timecode(
                    sum(end - start for start, end in slices)
                ),
            }
        else:
            audio_note = (
                "The composition-wide audio route has no remaining audio "
                "for this result-time segment."
            )
    else:
        audio_from = scene.get("audio_from")
        audio_from_source = "composition" if audio_from is not None else None
        if audio_from is None:
            audio_slots = [
                slot
                for slot in segment.slots
                if source_by_id[slot.source_id].get("audio_codec")
            ]
            if len(audio_slots) == 1:
                audio_from = audio_slots[0].source_id
                audio_from_source = "only_audio_slot"
        audio_slot = next(
            (slot for slot in segment.slots if slot.source_id == audio_from),
            None,
        )
        if audio_slot is not None:
            audio_from_anchor_text = format_timecode(audio_slot.source_from_s)
        if audio_slot is not None and not audio_slot.held:
            audio_source = source_by_id[audio_slot.source_id]
            source_duration = float(audio_source["duration"]["seconds"])
            source_timeline = resolve_source(
                spec, audio_slot.source_id, source_duration
            )
            slices = slice_audio_from_anchor(
                source_timeline.source_segments,
                audio_slot.source_from_s,
                audio_slot.consumes_source_s,
            )
            if slices:
                audio_from_input = (audio_source["path"], slices)
                audio_speed = float(audio_slot.speed or 1.0)
                audio_from_range = {
                    "from": format_timecode(slices[0][0]),
                    "to": format_timecode(slices[-1][1]),
                    "duration": format_timecode(
                        sum(end - start for start, end in slices)
                    ),
                }
        elif audio_from is not None and audio_slot is not None and audio_slot.held:
            audio_note = (
                "Pacing hold inserts silence for this result-time segment."
            )
        elif audio_from is None:
            audio_note = (
                "Scene has multiple audio-capable slots but no audio_from; "
                "this segment will be video-only."
            )

    scene_window = {
        "scene": resolved_scene.name,
        "index": resolved_scene.index,
        "pacing_segment_id": segment.id,
        "result_range": _scene_result_range(segment.start_s, segment.duration_s),
        "scene_result_range": _scene_result_range(
            resolved_scene.start_s, resolved_scene.duration_s
        ),
        "scene_local_range": {
            "from": format_timecode(segment.result_local_from_s),
            "to": format_timecode(segment.result_local_to_s),
            "duration": format_timecode(segment.duration_s),
        },
        "layout": _layout_envelope(
            scene["layout"],
            canvas,
            result_time_s=segment.result_local_from_s,
        ),
        "audio_from": audio_from,
        "audio_from_source": audio_from_source,
        "audio_from_range": audio_from_range,
        "audio_speed": audio_speed,
        "held": segment.held,
        "slots": slots_out,
    }
    if scene.get("id"):
        scene_window["scene_id"] = scene["id"]
    return {
        "scene": scene,
        "canvas": canvas,
        "canvas_tuple": (int(canvas["width"]), int(canvas["height"])),
        "layout": scene_window["layout"],
        "slots": slots_out,
        "render_slots": render_slots,
        "duration": segment.duration_s,
        "composition_duration": None,
        "result_range": scene_window["result_range"],
        "audio_from": audio_from,
        "audio_from_source": audio_from_source,
        "audio_from_input": audio_from_input,
        "audio_from_anchor": audio_from_anchor_text,
        "audio_from_range": audio_from_range,
        "audio_speed": audio_speed,
        "audio_note": audio_note,
        "output_fps": output_fps,
        "scene_window": scene_window,
        "global_result_range": scene_window["result_range"],
    }




def _scene_render_plans_for_range(
    *,
    project: dict,
    spec: dict,
    composition: list[dict],
    start_s: float,
    end_s: float,
    command: str,
) -> tuple[dict, list[dict]]:
    plan = _scene_composition_plan(
        spec=spec,
        composition=composition,
        command=command,
    )
    output_fps = resolve_project(project, spec).output_fps
    composition_duration = plan["composition_duration"]
    if start_s < 0 or end_s > composition_duration:
        _error_exit(
            command,
            f"Range {start_s}s-{end_s}s exceeds scene composition duration "
            f"of {composition_duration}s",
        )
    if start_s >= end_s:
        _error_exit(command, f"--from ({start_s}s) must be before --to ({end_s}s)")

    try:
        camera_plan = resolve_camera(
            composition,
            spec.get("motion") or {"version": 1, "scenes": []},
            plan["pacing_timeline"],
            {
                source["id"]: (int(source["width"]), int(source["height"]))
                for source in project["sources"]
                if source.get("width") and source.get("height")
            },
            plan["canvas"],
        )
    except CameraResolutionError as exc:
        _error_exit(command, f"Could not resolve scene camera: {exc}")

    render_plans: list[dict] = []
    timeline = plan["pacing_timeline"]
    scene_by_index = {scene.index: scene for scene in timeline.scenes}
    global_audio = _scene_global_audio_route(project, spec, composition, command)
    for segment in timeline.segments_overlapping(start_s, end_s):
        resolved_scene = scene_by_index[segment.scene_index]
        render_plan = _paced_segment_render_plan(
            project=project,
            spec=spec,
            resolved_scene=resolved_scene,
            segment=segment,
            canvas=plan["canvas"],
            command=command,
            output_fps=output_fps,
            layout_motion_time_s=timeline.layout_motion_time_at(
                resolved_scene.index,
                segment.start_s,
                plan["canvas"],
            ),
            global_audio=global_audio,
        )
        camera_states: dict[str, dict] = {}
        animated_slots: list[str] = []
        for render_slot in render_plan["render_slots"]:
            slot_name = render_slot["slot"]
            animation = camera_plan.animation_for(
                resolved_scene.name,
                slot_name,
                segment.result_local_from_s,
            )
            start_state = camera_plan.state_at(
                resolved_scene.name, slot_name, segment.result_local_from_s
            )
            end_state = camera_plan.state_at(
                resolved_scene.name, slot_name, segment.result_local_to_s
            )
            from_echo = _camera_state_echo(start_state)
            to_echo = _camera_state_echo(end_state)
            if _camera_echo_is_idle(from_echo) and _camera_echo_is_idle(
                to_echo
            ):
                # #499: a slot with no authored camera used to dump a
                # ~24-line from/to rect echo into every envelope; idle
                # states collapse to the one fact that matters.
                camera_states[slot_name] = {"target": "full"}
            else:
                camera_states[slot_name] = {
                    "from": from_echo,
                    "to": to_echo,
                }
            if animation is not None:
                render_slot["camera_animation"] = animation
                animated_slots.append(slot_name)
        render_plan["camera_states"] = camera_states
        render_plan["camera_render_status"] = (
            "active" if animated_slots else "inactive"
        )
        render_plan["animated_camera_slots"] = animated_slots
        render_plan["render_order"] = [
            "slot_source_range",
            "slot_pacing",
            "slot_camera",
            "slot_framing_layout",
            "canvas_overlays",
        ]
        render_plan["scene_window"]["camera_states"] = camera_states
        render_plan["scene_window"]["animated_camera_slots"] = animated_slots
        render_plans.append(render_plan)
    return plan, render_plans


def _scene_composition_plan_with_audio(
    *,
    project: dict,
    spec: dict,
    composition: list[dict],
    command: str,
) -> dict:
    plan = _scene_composition_plan(
        spec=spec,
        composition=composition,
        command=command,
    )
    _base_plan, render_plans = _scene_render_plans_for_range(
        project=project,
        spec=spec,
        composition=composition,
        start_s=0.0,
        end_s=plan["composition_duration"],
        command=command,
    )
    return _scene_report_from_render_plans(plan, render_plans)


def _scene_report_from_render_plans(plan: dict, render_plans: list[dict]) -> dict:
    """Keep authored scene counts stable while exposing pacing segments."""
    grouped: dict[int, list[dict]] = {}
    for render_plan in render_plans:
        window = render_plan["scene_window"]
        grouped.setdefault(window["index"], []).append(render_plan)

    scenes: list[dict] = []
    for authored in plan["scenes"]:
        members = grouped.get(authored["index"], [])
        if len(members) == 1:
            report = dict(members[0]["scene_window"])
            if "transition_in" in authored:
                report["transition_in"] = authored["transition_in"]
            scenes.append(report)
            continue
        report = dict(authored)
        report["pacing_segments"] = [
            member["scene_window"] for member in members
        ]
        report["pacing_segments_count"] = len(members)
        report["audio_from_source"] = (
            members[0]["audio_from_source"] if members else None
        )
        report["audio_from_ranges"] = [
            member["audio_from_range"]
            for member in members
            if member["audio_from_range"] is not None
        ]
        scenes.append(report)
    return {
        **plan,
        "scenes": scenes,
        "pacing_segments_count": len(render_plans),
    }


def _scene_render_plan_at(
    *,
    project: dict,
    spec: dict,
    composition: list[dict],
    at_s: float,
    command: str,
) -> tuple[dict, dict]:
    plan = _scene_composition_plan(
        spec=spec,
        composition=composition,
        command=command,
    )
    composition_duration = plan["composition_duration"]
    if at_s < 0 or at_s > composition_duration:
        _error_exit(
            command,
            f"Timecode {at_s}s exceeds scene composition duration of "
            f"{composition_duration}s",
        )
    lookup_at_s = at_s
    if lookup_at_s >= composition_duration:
        lookup_at_s = max(0.0, round(composition_duration - 0.001, 3))

    timeline = plan["pacing_timeline"]
    segment = timeline.segment_at(lookup_at_s)
    resolved_scene = timeline.scenes[segment.scene_index]
    frame_segment = segment.clipped(
        lookup_at_s, min(segment.end_s, lookup_at_s + 0.001)
    )
    render_plan = _paced_segment_render_plan(
        project=project,
        spec=spec,
        resolved_scene=resolved_scene,
        segment=frame_segment,
        canvas=plan["canvas"],
        command=command,
        output_fps=resolve_project(project, spec).output_fps,
        layout_motion_time_s=timeline.layout_motion_time_at(
            resolved_scene.index,
            frame_segment.start_s,
            plan["canvas"],
        ),
        global_audio=_scene_global_audio_route(
            project, spec, composition, command
        ),
    )
    try:
        camera_plan = resolve_camera(
            composition,
            spec.get("motion") or {"version": 1, "scenes": []},
            timeline,
            {
                source["id"]: (int(source["width"]), int(source["height"]))
                for source in project["sources"]
                if source.get("width") and source.get("height")
            },
            plan["canvas"],
        )
    except CameraResolutionError as exc:
        _error_exit(command, f"Could not resolve scene camera: {exc}")
    local_at = lookup_at_s - resolved_scene.start_s
    camera_states: dict[str, dict] = {}
    for render_slot in render_plan["render_slots"]:
        state = camera_plan.state_at(
            resolved_scene.name, render_slot["slot"], local_at
        )
        state_echo = _camera_state_echo(state)
        camera_states[render_slot["slot"]] = (
            {"target": "full"}
            if _camera_echo_is_idle(state_echo)
            else state_echo
        )
        if state.target != "full":
            render_slot["camera_crop"] = state.resolved_crop_pixels
    render_plan["camera_states"] = camera_states
    render_plan["camera_render_status"] = "active"
    return plan, render_plan





def _audio_from_range_envelope(
    *,
    spec: dict,
    composition: list[dict],
    source_durations: dict[str, float],
    audio_from: str,
) -> dict | None:
    """Return the source-time audio window used by ``--audio-from``.

    The ``to`` value is the end of the last surviving audio slice, so
    cuts on the audio source are reflected in the envelope.
    """
    from moviestar.spec import parse_timecode_string, slice_audio_from_anchor

    if audio_from not in source_durations:
        return None

    composition_duration = sum(
        parse_timecode_string(seg["source_to"], "composition.source_to")
        - parse_timecode_string(seg["source_from"], "composition.source_from")
        for seg in composition
    )
    anchor = next(
        (
            parse_timecode_string(seg["source_from"], "composition.source_from")
            for seg in composition
            if seg["source"] == audio_from
        ),
        None,
    )
    if anchor is None:
        return None

    timeline = resolve_source(spec, audio_from, source_durations[audio_from])
    slices = slice_audio_from_anchor(
        timeline.source_segments,
        anchor,
        composition_duration,
    )
    if not slices:
        return None

    return {
        "from": format_timecode(anchor),
        "to": format_timecode(slices[-1][1]),
    }


@cli.group("layouts", invoke_without_command=True)
@click.option(
    "--canvas",
    "canvas_arg",
    default="short",
    help="Canvas preset or WIDTHxHEIGHT to preview layouts against.",
)
@click.pass_context
def layouts(ctx: click.Context, canvas_arg: str) -> None:
    """List and preview layout presets for a canvas.

    \b
    Run `moviestar layouts --canvas <preset|WxH>` with no subcommand
    to list every preset, its slot names, resolved regions, and sample
    SVG paths for that canvas.

    \b
    Use this before `scenes set` or `concat --layout` to discover
    preset names, slot names, resolved regions, and sample images.
    """
    if ctx.invoked_subcommand is not None:
        return
    try:
        canvas = _parse_canvas_arg(canvas_arg)
        assert canvas is not None
        layout_items = []
        for name in sorted(LAYOUT_PRESETS):
            layout = _layout_for_preset(name, canvas)
            preview = _write_layout_preview(layout, canvas)
            item = _layout_envelope(layout, canvas, result_time_s=0.0)
            item["preview"] = preview
            layout_items.append(item)
    except (OSError, SpecValidationError, ValueError) as exc:
        _error_exit("layouts", str(exc))
    click.echo(
        json.dumps(
            {
                "canvas": canvas,
                "layouts": layout_items,
                "hint": (
                    "Use 'moviestar layouts preview <name> --canvas ...' "
                    "to inspect one preset, then pass the layout and slot "
                    "names to 'moviestar scenes set --scene NAME=<layout> "
                    "--slot SCENE:SLOT=SOURCE ...' or 'moviestar concat "
                    "--layout <name> --slot SLOT=SOURCE ...'."
                ),
            },
            indent=2,
        )
    )


@layouts.command("preview")
@click.argument("layout")
@click.option(
    "--canvas",
    "canvas_arg",
    default="short",
    help="Canvas preset or WIDTHxHEIGHT to preview the layout against.",
)
@click.option("--out", "output", default=None, help="Preview SVG output path.")
@click.option(
    "--slot-size",
    default=None,
    metavar="SLOT=SIZE",
    help="Preview PIP inset size, e.g. inset=small or inset=0.28.",
)
@click.option(
    "--slot-at",
    default=None,
    metavar="SLOT=ANCHOR",
    help="Preview PIP inset placement, e.g. inset=top-left.",
)
@click.option(
    "--slot-margin",
    default=None,
    metavar="SLOT=PIXELS|X,Y",
    help="Preview PIP inset margin, e.g. inset=64 or inset=64,32.",
)
def layouts_preview(
    layout: str,
    canvas_arg: str,
    output: str | None,
    slot_size: str | None,
    slot_at: str | None,
    slot_margin: str | None,
) -> None:
    """Write a deterministic sample SVG for one layout preset.

    \b
    Preview picture-in-picture geometry without a scene prefix:
      --slot-size inset=small --slot-at inset=top-left --slot-margin inset=64
    """
    try:
        canvas = _parse_canvas_arg(canvas_arg)
        assert canvas is not None
        layout_object = _layout_for_preset(layout, canvas)
        if (
            slot_size is not None
            or slot_at is not None
            or slot_margin is not None
        ):
            if layout != "picture-in-picture":
                raise ValueError(
                    "Slot geometry preview only applies to the "
                    "picture-in-picture inset."
                )
            inset_geometry: dict = {}
            if slot_size is not None:
                if "=" not in slot_size:
                    raise ValueError(
                        "Invalid --slot-size. Use SLOT=SIZE, e.g. inset=small."
                    )
                size_slot, raw_size = [
                    part.strip() for part in slot_size.split("=", 1)
                ]
                if size_slot != "inset":
                    raise ValueError(
                        "--slot-size only supports the picture-in-picture "
                        "'inset' slot."
                    )
                _size_label, size_fraction = _parse_geometry_size(raw_size)
                inset_geometry["size"] = size_fraction
            if slot_at is not None:
                if "=" not in slot_at:
                    raise ValueError(
                        "Invalid --slot-at. Use SLOT=ANCHOR, e.g. "
                        "inset=top-left."
                    )
                at_slot, anchor = [
                    part.strip() for part in slot_at.split("=", 1)
                ]
                if at_slot != "inset":
                    raise ValueError(
                        "--slot-at only supports the picture-in-picture "
                        "'inset' slot."
                    )
                if anchor not in SLOT_GEOMETRY_ANCHORS:
                    raise ValueError(
                        "Invalid --slot-at anchor. Use one of: "
                        f"{', '.join(SLOT_GEOMETRY_ANCHORS)}."
                    )
                inset_geometry["anchor"] = anchor
            if slot_margin is not None:
                if "=" not in slot_margin:
                    raise ValueError(
                        "Invalid --slot-margin. Use SLOT=PIXELS or SLOT=X,Y, "
                        "e.g. inset=64."
                    )
                margin_slot, raw_margin = [
                    part.strip() for part in slot_margin.split("=", 1)
                ]
                if margin_slot != "inset":
                    raise ValueError(
                        "--slot-margin only supports the picture-in-picture "
                        "'inset' slot."
                    )
                margin_x, margin_y = _parse_geometry_margin(raw_margin)
                inset_geometry["margin_x"] = margin_x
                inset_geometry["margin_y"] = margin_y
            layout_object["geometry"] = {"inset": inset_geometry}
        preview = _write_layout_preview(layout_object, canvas, output)
        envelope = _layout_envelope(layout_object, canvas, result_time_s=0.0)
    except (OSError, SpecValidationError, ValueError) as exc:
        _error_exit("layouts", str(exc))
    result = {
        "canvas": canvas,
        "layout": envelope,
        "out": preview,
        "hint": (
            "Open or inspect this SVG to see the layout regions. Use the "
            "slot names in 'moviestar scenes set --slot SCENE:SLOT=SOURCE' "
            "or 'moviestar concat --layout ... --slot SLOT=SOURCE'."
        ),
    }
    click.echo(json.dumps(result, indent=2))


def _parse_scene_arg(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(
            "Invalid scene. Use NAME=LAYOUT, e.g. intro=single."
        )
    name, layout = value.split("=", 1)
    name = name.strip()
    layout = layout.strip()
    if not name:
        raise ValueError("Scene name must not be empty.")
    if layout not in LAYOUT_PRESETS:
        raise ValueError(
            "Unknown layout preset "
            f"{layout!r}. Use one of: {', '.join(sorted(LAYOUT_PRESETS))}."
        )
    return name, layout


def _parse_scene_slot_arg(value: str) -> tuple[str, str, str]:
    if ":" not in value or "=" not in value:
        raise ValueError(
            "Invalid scene slot. Use SCENE:SLOT=SOURCE, e.g. "
            "intro:main=holden."
        )
    scene, rest = value.split(":", 1)
    slot, source = rest.split("=", 1)
    scene = scene.strip()
    slot = slot.strip()
    source = source.strip()
    if not scene or not slot or not source:
        raise ValueError(
            "Scene slot needs non-empty SCENE, SLOT, and SOURCE values."
        )
    return scene, slot, source


def _parse_scene_audio_from_arg(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(
            "Invalid scene audio source. Use SCENE=SOURCE, e.g. "
            "conversation=holden."
        )
    scene, source = value.split("=", 1)
    scene = scene.strip()
    source = source.strip()
    if not scene or not source:
        raise ValueError(
            "Scene audio source needs non-empty SCENE and SOURCE values."
        )
    return scene, source


class _SceneFileSchemaError(ValueError):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} scene-file validation error(s)")


_SCENE_FILE_KEYS = {"canvas", "scenes"}
_SCENE_ENTRY_KEYS = {"id", "name", "layout", "slots", "audio_from"}
_SCENE_SLOT_KEYS = {
    "id", "slot", "source", "from", "to", "framing", "geometry",
}


def _scene_file_error(path: str, message: str) -> dict[str, str]:
    return {"path": path, "error": message}


def _scene_file_string(
    value: object,
    path: str,
    errors: list[dict[str, str]],
    *,
    required: bool = True,
) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        errors.append(_scene_file_error(path, "must be a non-empty string"))
        return None
    return value.strip()


def _scene_file_timecode(
    value: object,
    path: str,
    errors: list[dict[str, str]],
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        errors.append(
            _scene_file_error(path, "must be a timecode string or number of seconds")
        )
        return None
    try:
        return parse_timecode(str(value))
    except ValueError as exc:
        errors.append(_scene_file_error(path, str(exc)))
        return None


def _scene_file_unknown_keys(
    value: dict,
    allowed: set[str],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    for key in sorted(set(value) - allowed):
        key_path = f"{path}.{key}" if path else key
        message = "unknown field"
        matches = difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.6)
        if matches:
            message += f"; did you mean {matches[0]!r}?"
        errors.append(_scene_file_error(key_path, message))


def _load_scene_file_inputs(
    path: str,
) -> tuple[dict, list[dict], list[dict[str, str]]]:
    """Parse editable scene JSON into the same scene-input shape as flags."""
    abs_path = os.path.realpath(path)
    if not os.path.isfile(abs_path):
        raise _SceneFileSchemaError(
            [_scene_file_error("$", f"scene file not found: {abs_path}")]
        )
    try:
        data = json.loads(Path(abs_path).read_text())
    except json.JSONDecodeError as exc:
        raise _SceneFileSchemaError(
            [
                _scene_file_error(
                    "$",
                    f"invalid JSON at line {exc.lineno}, column {exc.colno}: "
                    f"{exc.msg}",
                )
            ]
        ) from exc
    except OSError as exc:
        raise _SceneFileSchemaError(
            [_scene_file_error("$", f"could not read scene file: {exc}")]
        ) from exc

    errors: list[dict[str, str]] = []
    unknown_field_errors: list[dict[str, str]] = []
    if not isinstance(data, dict):
        raise _SceneFileSchemaError(
            [_scene_file_error("$", "must be a JSON object")]
        )
    _scene_file_unknown_keys(
        data, _SCENE_FILE_KEYS, "", unknown_field_errors
    )

    canvas_value = data.get("canvas", "short")
    if not isinstance(canvas_value, str) or not canvas_value.strip():
        errors.append(
            _scene_file_error("canvas", "must be a canvas preset or WIDTHxHEIGHT")
        )
        canvas = None
    else:
        try:
            canvas = _parse_canvas_arg(canvas_value)
        except ValueError as exc:
            errors.append(_scene_file_error("canvas", str(exc)))
            canvas = None

    raw_scenes = data.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        errors.append(_scene_file_error("scenes", "must be a non-empty array"))
        raw_scenes = []

    scene_inputs: list[dict] = []
    for scene_index, raw_scene in enumerate(raw_scenes):
        scene_path = f"scenes[{scene_index}]"
        if not isinstance(raw_scene, dict):
            errors.append(_scene_file_error(scene_path, "must be an object"))
            continue
        _scene_file_unknown_keys(
            raw_scene, _SCENE_ENTRY_KEYS, scene_path, unknown_field_errors
        )
        name = _scene_file_string(
            raw_scene.get("name"), f"{scene_path}.name", errors
        )
        scene_id = _scene_file_string(
            raw_scene.get("id"), f"{scene_path}.id", errors, required=False
        )
        layout = _scene_file_string(
            raw_scene.get("layout"), f"{scene_path}.layout", errors
        )
        audio_from = _scene_file_string(
            raw_scene.get("audio_from"),
            f"{scene_path}.audio_from",
            errors,
            required=False,
        )

        raw_slots = raw_scene.get("slots")
        if not isinstance(raw_slots, list) or not raw_slots:
            errors.append(
                _scene_file_error(f"{scene_path}.slots", "must be a non-empty array")
            )
            raw_slots = []
        slots: list[tuple[str, str, float, float, dict]] = []
        slot_ids: dict[str, str] = {}
        slot_geometry: dict[str, object] = {}
        slot_geometry_paths: dict[str, str] = {}
        has_slot_geometry = False
        for slot_index, raw_slot in enumerate(raw_slots):
            slot_path = f"{scene_path}.slots[{slot_index}]"
            if not isinstance(raw_slot, dict):
                errors.append(_scene_file_error(slot_path, "must be an object"))
                continue
            _scene_file_unknown_keys(
                raw_slot, _SCENE_SLOT_KEYS, slot_path, unknown_field_errors
            )
            slot = _scene_file_string(
                raw_slot.get("slot"), f"{slot_path}.slot", errors
            )
            if "geometry" in raw_slot:
                has_slot_geometry = True
                if slot is not None:
                    slot_geometry[slot] = raw_slot["geometry"]
                    slot_geometry_paths[slot] = f"{slot_path}.geometry"
            slot_id = _scene_file_string(
                raw_slot.get("id"), f"{slot_path}.id", errors, required=False
            )
            source = _scene_file_string(
                raw_slot.get("source"), f"{slot_path}.source", errors
            )
            result_from = _scene_file_timecode(
                raw_slot.get("from"), f"{slot_path}.from", errors
            )
            result_to = _scene_file_timecode(
                raw_slot.get("to"), f"{slot_path}.to", errors
            )
            framing_value = raw_slot.get("framing", "fill:center")
            if not isinstance(framing_value, str):
                errors.append(
                    _scene_file_error(
                        f"{slot_path}.framing",
                        "must be a framing string such as 'fill:left'",
                    )
                )
                framing = None
            else:
                try:
                    framing = _parse_framing_arg(framing_value)
                except ValueError as exc:
                    errors.append(
                        _scene_file_error(f"{slot_path}.framing", str(exc))
                    )
                    framing = None
            if None not in (slot, source, result_from, result_to, framing):
                slots.append(
                    (slot, source, result_from, result_to, framing)  # type: ignore[arg-type]
                )
                if slot_id is not None:
                    slot_ids[slot] = slot_id

        if name is not None and layout is not None:
            scene_input = {
                "name": name,
                "id": scene_id,
                "layout": layout,
                "slots": slots,
                "slot_ids": slot_ids,
                "audio_from": audio_from,
            }
            if has_slot_geometry:
                scene_input["slot_geometry"] = slot_geometry
                scene_input["slot_geometry_paths"] = slot_geometry_paths
            scene_inputs.append(scene_input)

    if errors:
        raise _SceneFileSchemaError(unknown_field_errors + errors)
    assert canvas is not None
    return canvas, scene_inputs, unknown_field_errors


def _scene_file_validation_exit(
    scene_file: str,
    errors: list[dict[str, str]],
    *,
    hint: str | None = None,
) -> None:
    click.echo(
        json.dumps(
            {
                "error": (
                    f"{len(errors)} validation error(s) in "
                    f"{os.path.realpath(scene_file)}."
                ),
                "command": "scenes set",
                "errors": errors,
                "hint": hint
                or (
                    "Fix the listed JSON paths and re-run 'moviestar scenes "
                    "set <file> --dry-run'."
                ),
            },
            indent=2,
        )
    )
    sys.exit(1)


SCENE_SLOT_DURATION_MISMATCH_HINT = (
    "Adjust each slot's --from/--to (or JSON from/to) so every slot has "
    "the same source-range duration. For intentional retiming, create the "
    "scene with equal-duration ranges, then run 'moviestar scenes motion "
    "dump', add speed or duration pacing, and validate it with 'moviestar "
    "scenes motion set <file> --dry-run'."
)


def _scene_result_range(start_s: float, duration_s: float) -> dict:
    return {
        "from": format_timecode(start_s),
        "to": format_timecode(start_s + duration_s),
        "duration": format_timecode(duration_s),
    }


def _surface_scene_envelope(
    *,
    name: str,
    index: int,
    layout: str,
    canvas: dict,
    slot_rows: list[tuple[str, str, float, float, dict]],
    audio_from: str | None,
    result_start_s: float,
) -> tuple[dict, float]:
    if not slot_rows:
        raise ValueError(f"Scene {name!r} needs at least one slot.")

    expected_slots = layout_slots(layout, canvas)
    seen_slots = [slot for slot, _source, _from, _to, _framing in slot_rows]
    unknown = [slot for slot in seen_slots if slot not in expected_slots]
    if unknown:
        raise ValueError(
            f"Scene {name!r}: invalid slot(s) {', '.join(unknown)} "
            f"for layout {layout!r} on canvas {_canvas_label(canvas)!r}. "
            f"Expected: {', '.join(expected_slots)}."
        )
    missing = [slot for slot in expected_slots if slot not in seen_slots]
    if missing:
        raise ValueError(
            f"Scene {name!r} is missing required layout slot(s): "
            f"{', '.join(missing)}. Valid slots: {', '.join(expected_slots)}."
        )

    durations = [
        round(to_s - from_s, 6)
        for _slot, _source, from_s, to_s, _framing in slot_rows
    ]
    if any(duration <= 0 for duration in durations):
        raise ValueError(f"Scene {name!r} slot ranges must have duration > 0.")
    if len({round(duration, 3) for duration in durations}) != 1:
        details = ", ".join(
            f"{slot}={format_timecode(duration)['text']}"
            for (slot, _source, _from, _to, _framing), duration in zip(
                slot_rows, durations
            )
        )
        raise ValueError(
            f"Scene {name!r} slot durations must match. Got {details}."
        )

    duration_s = durations[0]
    layout_object = _layout_for_preset(layout, canvas)
    regions = layout_regions(layout_object, canvas, result_time_s=0.0)
    scene = {
        "scene": name,
        "index": index,
        "result_range": _scene_result_range(result_start_s, duration_s),
        "layout": _layout_envelope(layout_object, canvas, result_time_s=0.0),
        "audio_from": audio_from,
        "slots": [],
    }
    for slot, source, from_s, to_s, framing in slot_rows:
        scene["slots"].append(
            {
                "slot": slot,
                "source": source,
                "source_range": {
                    "from": format_timecode(from_s),
                    "to": format_timecode(to_s),
                    "duration": format_timecode(to_s - from_s),
                },
                "region": regions[slot],
                "framing": framing,
            }
        )
    return scene, duration_s


def _scene_surface_result(
    *,
    status: str,
    canvas: dict,
    scenes: list[dict],
    hint: str,
) -> dict:
    duration_s = sum(
        scene["result_range"]["duration"]["seconds"] for scene in scenes
    )
    return {
        "status": status,
        "dry_run": True,
        "writes_spec": False,
        "composition_type": "scenes",
        "canvas": canvas,
        "scenes_count": len(scenes),
        "slots_count": sum(len(scene["slots"]) for scene in scenes),
        "composition_duration": format_timecode(duration_s),
        "scenes": scenes,
        "hint": hint,
    }


class _SceneFlagPairingError(ValueError):
    """Slot/range flag counts diverged; the fix is the pairing rule."""


def _scene_inputs_from_flags(
    *,
    canvas_arg: str,
    scene_args: tuple[str, ...],
    slot_args: tuple[str, ...],
    from_tcs: tuple[str, ...],
    to_tcs: tuple[str, ...],
    framing_args: tuple[str, ...],
    audio_from_args: tuple[str, ...],
) -> tuple[dict, list[dict]]:
    if not scene_args:
        raise ValueError(
            "Pass at least one --scene NAME=LAYOUT to define scene order."
        )
    if not (len(slot_args) == len(from_tcs) == len(to_tcs)):
        raise _SceneFlagPairingError(
            f"--slot / --from / --to counts must match. Got "
            f"{len(slot_args)} --slot, {len(from_tcs)} --from, "
            f"{len(to_tcs)} --to."
        )
    if framing_args and len(framing_args) != len(slot_args):
        raise ValueError(
            f"--framing count must match --slot count. Got "
            f"{len(framing_args)} --framing for {len(slot_args)} --slot."
        )
    canvas = _parse_canvas_arg(canvas_arg)
    assert canvas is not None
    scene_defs = [_parse_scene_arg(arg) for arg in scene_args]
    scene_names = [name for name, _layout in scene_defs]
    if len(set(scene_names)) != len(scene_names):
        duplicates = sorted(
            {name for name in scene_names if scene_names.count(name) > 1}
        )
        raise ValueError(
            f"Scene names must be unique. Duplicate scene(s): "
            f"{', '.join(duplicates)}."
        )
    audio_from_by_scene: dict[str, str] = {}
    for arg in audio_from_args:
        scene_name, source_id = _parse_scene_audio_from_arg(arg)
        if scene_name in audio_from_by_scene:
            raise ValueError(
                f"Scene {scene_name!r} has multiple --audio-from values. "
                "Pass at most one SCENE=SOURCE audio route per scene."
            )
        audio_from_by_scene[scene_name] = source_id
    unknown_audio_scenes = sorted(set(audio_from_by_scene) - set(scene_names))
    if unknown_audio_scenes:
        raise ValueError(
            "Audio source specified for unknown scene(s): "
            f"{', '.join(unknown_audio_scenes)}."
        )

    slots_by_scene: dict[str, list[tuple[str, str, float, float, dict]]] = {
        name: [] for name in scene_names
    }
    for i, (slot_arg, from_tc, to_tc) in enumerate(
        zip(slot_args, from_tcs, to_tcs)
    ):
        scene_name, slot_name, source_id = _parse_scene_slot_arg(slot_arg)
        if scene_name not in slots_by_scene:
            raise ValueError(
                f"Slot {slot_arg!r} references unknown scene {scene_name!r}."
            )
        try:
            result_from = parse_timecode(from_tc)
            result_to = parse_timecode(to_tc)
        except ValueError as exc:
            message = str(exc).rstrip(".")
            raise ValueError(
                f"{message}. In scenes set, --from and --to are bare "
                "timecodes matched by position to repeated --slot flags; "
                "do not prefix them with a slot name."
            ) from exc
        try:
            framing = (
                _parse_framing_arg(framing_args[i])
                if framing_args
                else {"mode": "fill", "anchor": "center"}
            )
        except ValueError as exc:
            message = str(exc).rstrip(".")
            raise ValueError(
                f"{message}. In scenes set, --framing is matched by position "
                "to repeated --slot flags; use values like fit, fill, or "
                "fill:left / fill:x=0.67, not SLOT=VALUE."
            ) from exc
        slots_by_scene[scene_name].append(
            (slot_name, source_id, result_from, result_to, framing)
        )

    scene_inputs = [
        {
            "name": name,
            "layout": layout,
            "slots": slots_by_scene[name],
            "audio_from": audio_from_by_scene.get(name),
        }
        for name, layout in scene_defs
    ]
    return canvas, scene_inputs


def _preserved_scene_geometry_count(
    composition: object,
    scene_inputs: list[dict],
) -> int:
    """Count stored slot geometries implicitly carried through ``scenes set``."""
    old_scenes = []
    if isinstance(composition, list):
        old_scenes = [
            scene
            for scene in composition
            if isinstance(scene, dict) and scene.get("name")
        ]
    old_by_id = {
        scene["id"]: scene for scene in old_scenes if scene.get("id")
    }
    old_by_name = {scene["name"]: scene for scene in old_scenes}

    preserved = 0
    for scene_input in scene_inputs:
        if (
            scene_input.get("layout") != "picture-in-picture"
            or "slot_geometry" in scene_input
        ):
            continue
        requested_id = scene_input.get("id")
        old_scene = old_by_id.get(requested_id) if requested_id else None
        old_scene = old_scene or old_by_name.get(scene_input.get("name"))
        if not old_scene:
            continue
        old_layout = old_scene.get("layout")
        if not isinstance(old_layout, dict):
            continue
        geometry = old_layout.get("geometry")
        if isinstance(geometry, dict):
            preserved += sum(value is not None for value in geometry.values())
        elif old_layout.get("inset") is not None:
            # Pre-M26 projects stored the same inset override under ``inset``.
            preserved += 1
    return preserved


@cli.group("scenes", invoke_without_command=True)
@click.pass_context
def scenes(ctx: click.Context) -> None:
    """Author scene-layout compositions.

    \b
    Scenes split one composition into ordered result-time ranges. Each
    scene keeps the same canvas but can choose its own preset layout,
    slot assignments, source ranges, framing, and audio source.

    \b
    `scenes set` writes the full ordered scene composition from repeated
    flags or an editable JSON file. `scenes list` reads the current scene
    composition. Incremental `scenes add` remains a parked preview.
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(
        json.dumps(
            {
                "status": "scene_authoring",
                "writes_spec": False,
                "surfaces": [
                    {
                        "name": "one_shot",
                        "command": (
                            "moviestar scenes set --canvas short "
                            "--scene intro=single --slot intro:main=holden ..."
                        ),
                        "tradeoff": (
                            "One command replaces the full scene composition; "
                            "implemented by `moviestar scenes set`."
                        ),
                    },
                    {
                        "name": "incremental_builder",
                        "command": (
                            "moviestar scenes add --name intro --layout single "
                            "--slot main=holden ..."
                        ),
                        "tradeoff": (
                            "Parked preview: add and verify one scene at a "
                            "time once staged scene state exists."
                        ),
                    },
                    {
                        "name": "scene_file",
                        "command": "moviestar scenes set scenes.json --dry-run",
                        "tradeoff": (
                            "Editable, diffable JSON for large compositions; "
                            "implemented by `moviestar scenes set FILE`."
                        ),
                    },
                ],
                "hint": (
                    "Run 'moviestar scenes set --help' for the JSON schema "
                    "and repeated-flag form, 'moviestar scenes transition' "
                    "to discover visual transitions, or 'moviestar scenes "
                    "list' to read the current composition."
                ),
            },
            indent=2,
        )
    )


def _transition_record_envelope(transition: dict | None) -> dict | None:
    if transition is None:
        return None
    return {
        "type": transition["type"],
        "duration": format_timecode(float(transition["duration"])),
    }


def _authored_transition_inventory(
    spec: dict,
    scenes_out: list[dict],
) -> dict:
    internal = []
    for scene in scenes_out[1:]:
        transition = scene.get("transition_in")
        if transition is None:
            continue
        internal.append(
            {
                "incoming": {
                    "scene": scene["scene"],
                    "scene_id": scene["scene_id"],
                },
                "transition": transition,
            }
        )
    return {
        "opening": _transition_record_envelope(spec.get("opening_transition")),
        "internal": internal,
        "closing": _transition_record_envelope(spec.get("closing_transition")),
    }


def _transition_surface_catalog(
    *,
    authored_transitions: dict | None = None,
) -> dict:
    result = {
        "status": "scene_transition_authoring",
        "writes_spec": False,
        "default_boundary_behavior": "cut",
        "default_duration": format_timecode(0.5),
        "transition_types": [
            {
                "type": "dissolve",
                "best_for": "a soft visual blend between two scenes",
                "source_handles": "half the duration from each side",
            },
            {
                "type": "dip-black",
                "best_for": "a chapter break or passage of time",
                "source_handles": "not required",
            },
            {
                "type": "dip-white",
                "best_for": "a bright flash or memory-like beat",
                "source_handles": "not required",
            },
        ],
        "boundary_selectors": {
            "before_scene": "--before SCENE (name or stable scene_id)",
            "opening": "--opening",
            "closing": "--closing",
            "all_internal": "--all",
        },
        "ownership": (
            "An internal transition belongs to the incoming scene, so "
            "--before remains stable when earlier scenes change."
        ),
        "timing": (
            "Internal transitions are centered on the cut. The result "
            "duration does not change."
        ),
        "audio_behavior": "unchanged",
        "scene_file_shape": {
            "internal_boundary": {
                "location": "incoming scene.transition_in",
                "example": {"type": "dissolve", "duration": 0.5},
            },
            "project_edges": {
                "opening": "opening_transition",
                "closing": "closing_transition",
            },
        },
        "resolution_note": (
            "Boundary, timing, and source-handle validation are real. "
            "Authored internal transitions persist in spec.json and render "
            "in export. Opening/closing fades and watch, inspect, and "
            "screenshot transition rendering are not available yet."
        ),
        "hint": (
            "Preview a boundary with 'moviestar scenes transition --before "
            "SCENE --type dissolve --duration 0.5 --dry-run'. Repeating the "
            "command with both --type and --duration will replace that "
            "boundary; --remove restores a cut."
        ),
    }
    if authored_transitions is not None:
        result["authored_transitions"] = authored_transitions
    return result


def _mutate_transition_state(
    spec: dict,
    *,
    internal_indices: list[int],
    edge_name: str | None,
    transition_type: str | None,
    duration_s: float,
    remove: bool,
) -> dict:
    """Return validated persisted transition intent for one command."""
    new_spec = copy.deepcopy(spec)
    transition = None
    if not remove:
        assert transition_type is not None
        transition = {
            "type": transition_type,
            "duration": round(duration_s, 6),
        }
    if internal_indices:
        for index in internal_indices:
            scene = new_spec["composition"][index]
            if transition is None:
                scene.pop("transition_in", None)
            else:
                scene["transition_in"] = copy.deepcopy(transition)
    else:
        assert edge_name is not None
        new_spec[f"{edge_name}_transition"] = transition
    validate_spec(new_spec)
    return new_spec


def _transition_surface_boundary(
    scenes_out: list[dict],
    *,
    incoming_index: int,
    duration_s: float,
) -> dict:
    outgoing = scenes_out[incoming_index - 1]
    incoming = scenes_out[incoming_index]
    cut_s = float(incoming["result_range"]["from"]["seconds"])
    half_s = duration_s / 2
    return {
        "outgoing": {
            "scene": outgoing["scene"],
            "scene_id": outgoing["scene_id"],
        },
        "incoming": {
            "scene": incoming["scene"],
            "scene_id": incoming["scene_id"],
        },
        "result_cut_at": format_timecode(cut_s),
        "result_window": {
            "from": format_timecode(max(0.0, cut_s - half_s)),
            "to": format_timecode(cut_s + half_s),
        },
    }


def _transition_handle_envelope(handle: TransitionSlotHandle) -> dict:
    result = {
        "side": handle.side,
        "scene": handle.scene_name,
        "scene_id": handle.scene_id,
        "slot": handle.slot_name,
        "slot_id": handle.slot_id,
        "source": handle.source_id,
        "boundary_source_time": format_timecode(handle.boundary_source_s),
        "edge_speed": handle.edge_speed,
        "edge_behavior": handle.edge_behavior,
        "required": format_timecode(handle.required_source_s),
        "available": format_timecode(handle.available_source_s),
        "available_in_result_time": (
            None
            if handle.available_result_s is None
            else format_timecode(handle.available_result_s)
        ),
        "maximum_transition_duration": (
            None
            if handle.maximum_transition_duration_s is None
            else format_timecode(handle.maximum_transition_duration_s)
        ),
        "sufficient": handle.sufficient,
    }
    return result


def _transition_visual_progress(
    resolution: InternalTransitionResolution | EdgeTransitionResolution,
) -> list[dict]:
    return [
        {
            "position": sample.position,
            "at": format_timecode(sample.at_s),
            "outgoing_weight": sample.outgoing_weight,
            "incoming_weight": sample.incoming_weight,
            "color_weight": sample.color_weight,
        }
        for sample in resolution.visual_samples
    ]


def _transition_source_handles(
    resolution: InternalTransitionResolution,
) -> dict:
    if resolution.transition_type != "dissolve":
        return {
            "policy": "not_required",
            "reason": "dip transitions use visible scene content",
        }
    return {
        "policy": "half_duration_each_side",
        "required_each_side": format_timecode(resolution.duration_s / 2),
        "maximum_duration": format_timecode(resolution.maximum_duration_s),
        "sufficient": resolution.sufficient,
        "outgoing": [
            _transition_handle_envelope(handle)
            for handle in resolution.outgoing_handles
        ],
        "incoming": [
            _transition_handle_envelope(handle)
            for handle in resolution.incoming_handles
        ],
    }


def _transition_internal_boundary_envelope(
    resolution: InternalTransitionResolution,
    *,
    include_source_handles: bool = False,
) -> dict:
    result = {
        "outgoing": {
            "scene": resolution.outgoing.name,
            "scene_id": resolution.outgoing.scene_id,
        },
        "incoming": {
            "scene": resolution.incoming.name,
            "scene_id": resolution.incoming.scene_id,
        },
        "result_cut_at": format_timecode(resolution.cut_s),
        "result_window": {
            "from": format_timecode(resolution.window_from_s),
            "to": format_timecode(resolution.window_to_s),
        },
        "visual_contributors": [
            {
                "role": "outgoing",
                "scene": resolution.outgoing.name,
                "scene_id": resolution.outgoing.scene_id,
            },
            {
                "role": "incoming",
                "scene": resolution.incoming.name,
                "scene_id": resolution.incoming.scene_id,
            },
        ],
        "visual_progress": _transition_visual_progress(resolution),
    }
    if include_source_handles:
        result["source_handles"] = _transition_source_handles(resolution)
    return result


def _transition_edge_boundary_envelope(
    resolution: EdgeTransitionResolution,
) -> dict:
    scene = {
        "scene": resolution.scene.name,
        "scene_id": resolution.scene.scene_id,
    }
    return {
        "incoming" if resolution.edge == "opening" else "outgoing": scene,
        "result_edge_at": format_timecode(resolution.edge_at_s),
        "result_window": {
            "from": format_timecode(resolution.window_from_s),
            "to": format_timecode(resolution.window_to_s),
        },
        "visual_progress": _transition_visual_progress(resolution),
    }


def _transition_duration_flag(duration_s: float) -> str:
    return f"{round(duration_s, 3):g}"


def _transition_internal_limit_exit(
    command: str,
    resolutions: list[InternalTransitionResolution],
) -> None:
    failed = [resolution for resolution in resolutions if not resolution.sufficient]
    assert failed
    maximum_s = min(resolution.maximum_duration_s for resolution in failed)
    limiting = [
        _transition_handle_envelope(handle)
        for resolution in failed
        for handle in resolution.limiting_handles
    ]
    if any(resolution.limit_reason == "source_handles" for resolution in failed):
        hint = (
            f"Use --duration {_transition_duration_flag(maximum_s)} or shorter, "
            "or retrim/extend every limiting slot to provide the reported "
            "source handles."
        )
    else:
        hint = (
            f"Use --duration {_transition_duration_flag(maximum_s)} or shorter, "
            "or lengthen both scenes around the boundary."
        )
    labels = ", ".join(
        f"{resolution.outgoing.name} -> {resolution.incoming.name}"
        for resolution in failed
    )
    details = {
        "requested_duration": format_timecode(failed[0].duration_s),
        "maximum_duration": format_timecode(maximum_s),
        "limit_reason": (
            "source_handles"
            if any(
                resolution.limit_reason == "source_handles"
                for resolution in failed
            )
            else "scene_window"
        ),
        "limiting_handles": limiting,
        "failed_boundaries": [
            _transition_internal_boundary_envelope(
                resolution,
                include_source_handles=len(failed) > 1,
            )
            for resolution in failed
        ],
    }
    if len(failed) == 1:
        details["source_handles"] = _transition_source_handles(failed[0])
    _error_exit_with_hint(
        command,
        f"The requested {failed[0].transition_type} does not fit: {labels}.",
        hint,
        code="insufficient_transition_media",
        **details,
    )


@scenes.command("transition")
@project_workspace_option
@click.option(
    "--before",
    "before_scene",
    metavar="SCENE",
    help=(
        "Target the internal boundary before SCENE. Accepts a scene name "
        "or stable scene_id; the transition belongs to this incoming scene."
    ),
)
@click.option(
    "--opening",
    is_flag=True,
    help="Target the project opening; a dip becomes a fade from its color.",
)
@click.option(
    "--closing",
    is_flag=True,
    help="Target the project closing; a dip becomes a fade to its color.",
)
@click.option(
    "--all",
    "all_boundaries",
    is_flag=True,
    help="Target every internal scene boundary; opening/closing are excluded.",
)
@click.option(
    "--type",
    "transition_type",
    type=click.Choice(["dissolve", "dip-black", "dip-white"]),
    help="Visual transition type: dissolve, dip-black, or dip-white.",
)
@click.option(
    "--duration",
    "duration_arg",
    metavar="SECONDS",
    default="0.5",
    show_default=True,
    help=(
        "Total transition duration as seconds or a timecode. The default "
        "is 0.5 seconds."
    ),
)
@click.option(
    "--remove",
    is_flag=True,
    help="Remove the selected transition and restore the default hard cut.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Resolve the boundary and timing without writing spec.json.",
)
@click.pass_context
def scenes_transition(
    ctx: click.Context,
    before_scene: str | None,
    opening: bool,
    closing: bool,
    all_boundaries: bool,
    transition_type: str | None,
    duration_arg: str,
    remove: bool,
    dry_run: bool,
) -> None:
    """Set, inspect, or remove visual transitions at scene boundaries.

    \b
    Start with one boundary:
      moviestar scenes transition --before demo --type dissolve
      moviestar scenes transition --before demo --type dip-black --duration 0.8
      moviestar scenes transition --before demo --remove

    \b
    Or address project edges and every internal boundary:
      moviestar scenes transition --opening --type dip-black
      moviestar scenes transition --closing --type dip-black
      moviestar scenes transition --all --type dissolve --duration 0.5

    Run the command without controls to inspect the catalog. Cut is the
    default boundary behavior, so no transition needs to be authored for a
    hard cut. Repeating a set command with both --type and --duration replaces
    the selected boundary; --remove restores its cut.

    Internal durations are total durations centered on the cut. A 0.5-second
    dissolve needs 0.25 seconds of result-time source handles from each side;
    the exact source-time need follows each slot's boundary playback speed.
    Every visible slot must have enough media. The response reports required
    and available handles per slot and rejects the request rather than clamping.
    Dip transitions use the visible end and beginning of the neighboring scenes
    and need no source handles. Opening and closing fades consume their duration
    inward from the project edge. The result duration does not change.

    Audio is unchanged: transitions do not crossfade, shorten, or shift the
    existing audio timeline. Author audio fades independently with the audio
    commands.

    Boundary, timing, and handle validation are real. Without --dry-run, the
    command persists transition intent in spec.json as one undoable revision.
    Internal transitions render in export. Opening/closing fades and watch,
    inspect, and screenshot transition rendering are not available yet.
    """
    command = "scenes transition"
    selectors = [before_scene is not None, opening, closing, all_boundaries]
    selector_count = sum(selectors)
    duration_was_explicit = (
        ctx.get_parameter_source("duration_arg").name != "DEFAULT"
    )
    has_controls = bool(
        selector_count
        or transition_type is not None
        or remove
        or dry_run
        or duration_was_explicit
    )
    if not has_controls:
        authored_transitions = None
        if is_loaded():
            try:
                project = load_project()
                spec = _load_or_init_spec(project)
                composition = spec.get("composition")
                if _is_scene_layout_composition(composition):
                    assert composition is not None
                    plan = _scene_composition_plan(
                        spec=spec,
                        composition=composition,
                        command=command,
                    )
                    authored_transitions = _authored_transition_inventory(
                        spec, plan["scenes"]
                    )
            except (
                json.JSONDecodeError,
                FileNotFoundError,
                SpecValidationError,
            ) as exc:
                _error_exit(command, f"Could not read the scene project: {exc}")
        click.echo(
            json.dumps(
                _transition_surface_catalog(
                    authored_transitions=authored_transitions
                ),
                indent=2,
            )
        )
        return
    if selector_count != 1:
        _error_exit_with_hint(
            command,
            "Choose exactly one transition boundary selector.",
            "Pass one of --before SCENE, --opening, --closing, or --all.",
        )
    if remove and transition_type is not None:
        _error_exit_with_hint(
            command,
            "--remove cannot be combined with --type.",
            "Drop --type to restore the selected boundary to a cut.",
        )
    if remove and duration_was_explicit:
        _error_exit_with_hint(
            command,
            "--remove cannot be combined with --duration.",
            "Drop --duration to restore the selected boundary to a cut.",
        )
    if not remove and transition_type is None:
        _error_exit_with_hint(
            command,
            "Setting a transition requires --type.",
            "Choose --type dissolve, --type dip-black, or --type dip-white.",
        )
    if (opening or closing) and transition_type == "dissolve":
        _error_exit_with_hint(
            command,
            "A dissolve needs two neighboring scenes, but a project edge has "
            "only one.",
            "Use --type dip-black or --type dip-white at a project edge; it "
            "resolves to a fade from or to that color.",
        )
    try:
        duration_s = parse_timecode(duration_arg)
        if not math.isfinite(duration_s) or duration_s <= 0:
            raise ValueError("Transition duration must be greater than zero.")
    except ValueError as exc:
        _error_exit_with_hint(
            command,
            str(exc),
            "Pass a positive duration such as --duration 0.5.",
        )

    if not is_loaded():
        _no_project_error_exit(command)
    try:
        project = load_project()
        spec = _load_or_init_spec(project)
    except (json.JSONDecodeError, FileNotFoundError, SpecValidationError) as exc:
        _error_exit(command, f"Could not read the scene project: {exc}")
    composition = spec.get("composition")
    if not _is_scene_layout_composition(composition):
        _error_exit_with_hint(
            command,
            "Current composition is not a scene composition.",
            "Run 'moviestar scenes set' first, then target a scene boundary.",
        )
    assert composition is not None
    plan = _scene_composition_plan(
        spec=spec,
        composition=composition,
        command=command,
    )
    try:
        resolved_project = resolve_project(project, spec)
    except (ResolvedProjectError, SpecValidationError) as exc:
        _error_exit_with_hint(
            command,
            f"Could not resolve the scene timeline: {exc}",
            "Run 'moviestar scenes list' to inspect the current composition "
            "and repair its scene or pacing state.",
        )
    source_durations = {
        source["id"]: float(source["duration"]["seconds"])
        for source in project["sources"]
    }
    scenes_out = plan["scenes"]
    available_scenes = [
        {"scene": scene["scene"], "scene_id": scene["scene_id"]}
        for scene in scenes_out
    ]

    selector: dict
    boundaries: list[dict]
    internal_indices: list[int] = []
    internal_resolutions: list[InternalTransitionResolution] = []
    edge_resolution: EdgeTransitionResolution | None = None
    edge_name: str | None = None
    ownership = "incoming_scene"
    if before_scene is not None:
        incoming_index = next(
            (
                index
                for index, scene in enumerate(scenes_out)
                if before_scene in {scene["scene"], scene.get("scene_id")}
            ),
            None,
        )
        if incoming_index is None:
            _error_exit_with_hint(
                command,
                f"No scene named or identified by {before_scene!r}.",
                "Pass one of the available scene names or stable IDs to "
                "--before.",
                available_scenes=available_scenes,
            )
        if incoming_index == 0:
            _error_exit_with_hint(
                command,
                f"The first scene {before_scene!r} has no internal boundary "
                "before it.",
                "Use --opening for a project-opening fade, or choose a later "
                "scene with --before.",
                available_scenes=available_scenes,
            )
        selector = {"before": before_scene}
        internal_indices = [incoming_index]
        boundary_duration_s = duration_s
        stored_transition = composition[incoming_index].get("transition_in")
        if remove and isinstance(stored_transition, dict):
            boundary_duration_s = float(stored_transition["duration"])
        boundaries = [
            _transition_surface_boundary(
                scenes_out,
                incoming_index=incoming_index,
                duration_s=boundary_duration_s,
            )
        ]
    elif all_boundaries:
        selector = {"all": True}
        internal_indices = list(range(1, len(scenes_out)))
        boundaries = [
            _transition_surface_boundary(
                scenes_out,
                incoming_index=index,
                duration_s=(
                    float(composition[index]["transition_in"]["duration"])
                    if remove
                    and isinstance(
                        composition[index].get("transition_in"), dict
                    )
                    else duration_s
                ),
            )
            for index in range(1, len(scenes_out))
        ]
    else:
        ownership = "project_edge"
        edge_name = "opening" if opening else "closing"
        total_s = float(plan["composition_duration"])
        if opening:
            selector = {"opening": True}
            first = scenes_out[0]
            boundary_duration_s = duration_s
            stored_transition = spec.get("opening_transition")
            if remove and isinstance(stored_transition, dict):
                boundary_duration_s = float(stored_transition["duration"])
            boundaries = [
                {
                    "incoming": {
                        "scene": first["scene"],
                        "scene_id": first["scene_id"],
                    },
                    "result_edge_at": format_timecode(0.0),
                    "result_window": {
                        "from": format_timecode(0.0),
                        "to": format_timecode(
                            min(boundary_duration_s, total_s)
                        ),
                    },
                }
            ]
        else:
            selector = {"closing": True}
            last = scenes_out[-1]
            boundary_duration_s = duration_s
            stored_transition = spec.get("closing_transition")
            if remove and isinstance(stored_transition, dict):
                boundary_duration_s = float(stored_transition["duration"])
            boundaries = [
                {
                    "outgoing": {
                        "scene": last["scene"],
                        "scene_id": last["scene_id"],
                    },
                    "result_edge_at": format_timecode(total_s),
                    "result_window": {
                        "from": format_timecode(
                            max(0.0, total_s - boundary_duration_s)
                        ),
                        "to": format_timecode(total_s),
                    },
                }
            ]

    if all_boundaries and not internal_indices:
        click.echo(
            json.dumps(
                {
                    "status": "no_internal_transition_boundaries",
                    "writes_spec": False,
                    "requested_dry_run": dry_run,
                    "selector": {"all": True},
                    "boundaries_count": 0,
                    "boundaries": [],
                    "hint": (
                        "This composition has one scene. Add a second scene "
                        "before applying a transition to all internal "
                        "boundaries."
                    ),
                },
                indent=2,
            )
        )
        return

    if not remove:
        assert transition_type is not None
        try:
            if internal_indices:
                internal_resolutions = [
                    resolve_internal_transition(
                        resolved_project,
                        source_durations,
                        incoming_scene_index=index,
                        transition_type=transition_type,
                        duration_s=duration_s,
                    )
                    for index in internal_indices
                ]
                if any(
                    not resolution.sufficient
                    for resolution in internal_resolutions
                ):
                    _transition_internal_limit_exit(
                        command, internal_resolutions
                    )
                boundaries = [
                    _transition_internal_boundary_envelope(
                        resolution,
                        include_source_handles=len(internal_resolutions) > 1,
                    )
                    for resolution in internal_resolutions
                ]
            else:
                assert edge_name is not None
                edge_resolution = resolve_edge_transition(
                    resolved_project,
                    edge=edge_name,
                    duration_s=duration_s,
                )
                if not edge_resolution.sufficient:
                    maximum_s = edge_resolution.maximum_duration_s
                    _error_exit_with_hint(
                        command,
                        f"The requested {edge_name} fade is longer than the "
                        "finished video.",
                        f"Use --duration "
                        f"{_transition_duration_flag(maximum_s)} or shorter.",
                        code="transition_window_too_long",
                        requested_duration=format_timecode(duration_s),
                        maximum_duration=format_timecode(maximum_s),
                        limit_reason=edge_resolution.limit_reason,
                    )
                boundaries = [
                    _transition_edge_boundary_envelope(edge_resolution)
                ]
        except TransitionResolutionError as exc:
            _error_exit_with_hint(
                command,
                str(exc),
                "Run 'moviestar scenes list' to inspect the resolved scene "
                "timeline and source ranges.",
            )

    try:
        new_spec = _mutate_transition_state(
            spec,
            internal_indices=internal_indices,
            edge_name=edge_name,
            transition_type=transition_type,
            duration_s=duration_s,
            remove=remove,
        )
    except SpecValidationError as exc:
        _error_exit(command, str(exc))
    state_changed = new_spec != spec
    writes_spec = state_changed and not dry_run
    if writes_spec:
        try:
            save_spec(new_spec, command=command)
        except SpecValidationError as exc:
            _error_exit(command, str(exc))

    is_bulk = all_boundaries and len(internal_indices) > 1
    if not state_changed:
        status = (
            "scene_transition_already_cut"
            if remove
            else "scene_transition_unchanged"
        )
    elif dry_run:
        if remove:
            status = (
                "would_remove_scene_transitions"
                if is_bulk
                else "would_remove_scene_transition"
            )
        else:
            status = (
                "would_set_scene_transitions"
                if is_bulk
                else "would_set_scene_transition"
            )
    else:
        if remove:
            status = (
                "removed_scene_transitions"
                if is_bulk
                else "removed_scene_transition"
            )
        else:
            status = (
                "set_scene_transitions"
                if is_bulk
                else "set_scene_transition"
            )

    result = {
        "status": status,
        "writes_spec": writes_spec,
        "state_changed": state_changed,
        "requested_dry_run": dry_run,
        "render_status": (
            "export_only" if internal_resolutions else "not_implemented"
        ),
        "resolution_note": (
            "Boundary, timing, and source-handle validation are real. "
            + (
                "The transition was persisted in spec.json. "
                if writes_spec
                else "spec.json was not changed. "
            )
            + (
                "Internal transitions render in export; watch, inspect, and "
                "screenshot do not render them yet."
                if internal_resolutions
                else "Opening and closing transitions do not render yet."
            )
        ),
        "ownership": ownership,
        "selector": selector,
        "boundaries_count": len(boundaries),
    }
    if len(boundaries) == 1:
        result["boundary"] = boundaries[0]
    else:
        result["boundaries"] = boundaries
    if remove:
        result["resulting_boundary_behavior"] = "cut"
        if writes_spec:
            result["hint"] = (
                "The cut is saved. Run 'moviestar undo --composition' to "
                "restore the removed transition. Export now uses the cut."
            )
        elif dry_run and state_changed:
            result["hint"] = (
                "Re-run without --dry-run to save this cut."
            )
        else:
            result["hint"] = (
                "The selected boundary is already a cut; nothing was written."
            )
    else:
        transition = {
            "type": transition_type,
            "duration": format_timecode(duration_s),
            "alignment": (
                "center" if ownership == "incoming_scene" else "inside_edge"
            ),
            "result_duration_change": format_timecode(0.0),
            "maximum_duration": format_timecode(
                min(
                    [
                        resolution.maximum_duration_s
                        for resolution in internal_resolutions
                    ]
                    or [
                        edge_resolution.maximum_duration_s
                        if edge_resolution is not None
                        else duration_s
                    ]
                )
            ),
        }
        if len(boundaries) == 1:
            transition["result_window"] = boundaries[0]["result_window"]
        result["transition"] = transition
        result["source_handles"] = (
            _transition_source_handles(internal_resolutions[0])
            if len(internal_resolutions) == 1
            else {
                "policy": "not_required",
                "reason": "dip transitions use visible scene content",
            }
        )
        if len(internal_resolutions) > 1 and transition_type == "dissolve":
            result["source_handles"] = {
                "policy": "per_boundary",
                "required_each_side": format_timecode(duration_s / 2),
                "maximum_duration": transition["maximum_duration"],
                "sufficient": True,
                "hint": "Inspect each boundary.source_handles record.",
            }
        result["audio"] = {
            "behavior": "unchanged",
            "crossfade": False,
            "timeline_shift": False,
        }
        if writes_spec:
            result["hint"] = (
                "The transition is saved. Run 'moviestar scenes list' to read "
                "it back or 'moviestar undo --composition' to restore the "
                "previous boundary. Internal transitions render in export; "
                "opening and closing fades do not render yet."
            )
        elif dry_run and state_changed:
            result["hint"] = (
                "Re-run without --dry-run to save this transition, then use "
                "'moviestar export' to render an internal boundary."
            )
        else:
            result["hint"] = (
                "The selected boundary already has this transition; nothing "
                "was written. Internal transitions render in export."
            )
    click.echo(json.dumps(result, indent=2))


def _parse_geometry_target(value: str) -> tuple[str, str]:
    parts = [part.strip() for part in value.split(":")]
    if len(parts) != 2 or not all(parts):
        raise ValueError(
            "Invalid geometry target. Use SCENE:SLOT, e.g. demo:inset."
        )
    return parts[0], parts[1]


def _parse_size_fraction(part: str, *, context: str) -> float:
    try:
        fraction = float(part[:-1]) / 100 if part.endswith("%") else float(part)
    except ValueError as exc:
        raise ValueError(context) from exc
    if not math.isfinite(fraction) or not (
        SLOT_GEOMETRY_MIN_SIZE <= fraction <= SLOT_GEOMETRY_MAX_SIZE
    ):
        raise ValueError(
            f"Custom size must be between {SLOT_GEOMETRY_MIN_SIZE:.2f} and "
            f"{SLOT_GEOMETRY_MAX_SIZE:.2f} of the canvas's shorter dimension."
        )
    return fraction


def _parse_geometry_size(value: str) -> tuple[str, float | dict]:
    """Parse ``--size`` to a (label, size) pair; pixel resolution happens
    in :func:`moviestar.spec.resolve_inset_geometry`.

    Square sizes are a single fraction of the shorter canvas dimension.
    ``WxH`` sizes (#497) store ``{"width": f, "height": f}`` — both
    fractions of the shorter dimension — and opt the inset out of the
    circular default.
    """
    text = value.strip().lower()
    named = SLOT_GEOMETRY_SIZE_FRACTIONS.get(text)
    if named is not None:
        return text, named
    if "x" in text:
        parts = text.split("x")
        message = (
            "Invalid non-square size. Use WxH fractions such as "
            "0.40x0.30, or percentages such as 40%x30%."
        )
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError(message)
        return "custom", {
            "width": _parse_size_fraction(parts[0].strip(), context=message),
            "height": _parse_size_fraction(parts[1].strip(), context=message),
        }
    return "custom", _parse_size_fraction(
        text,
        context=(
            "Invalid size. Use small, medium, large, a normalized value "
            "such as 0.28, a percentage such as 28%, or WxH such as "
            "0.40x0.30 for a non-square inset."
        ),
    )


def _parse_geometry_margin(value: str) -> tuple[int, int]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) not in {1, 2} or not all(parts):
        raise ValueError("Invalid margin. Use PIXELS or X,Y, e.g. 64 or 64,32.")
    try:
        margins = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError(
            "Invalid margin. Use non-negative whole pixels, e.g. 64 or 64,32."
        ) from exc
    if any(margin < 0 for margin in margins):
        raise ValueError("Margin must use non-negative pixel values.")
    return (margins[0], margins[-1])


SLOT_SHAPE_KINDS = ("circle", "rounded", "rect")
SLOT_SHAPE_DEFAULT_RADIUS = 0.15
_SHAPE_BORDER_RE = re.compile(r"^(\d+)px\s+(\S+)$")
_SHAPE_COLOR_RE = re.compile(
    r"^(?:[A-Za-z]+|#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})"
    r"|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*[\d.]+\s*)?\))$"
)


SLOT_SHAPE_DEFAULT_BORDER_WIDTH = 4


def _parse_shape_border(value: str) -> dict | None:
    """Parse ``--border`` using the ``'WIDTHpx COLOR'`` vocabulary.

    A bare color is the most common agent phrasing ("give it a white
    border"), so it is accepted with the default width.
    """
    text = value.strip()
    if text.lower() == "none":
        return None
    if _SHAPE_COLOR_RE.match(text):
        return {"width": SLOT_SHAPE_DEFAULT_BORDER_WIDTH, "color": text}
    match = _SHAPE_BORDER_RE.match(text)
    if not match:
        raise ValueError(
            "Invalid border. Use 'WIDTHpx COLOR' such as '4px white', "
            "a bare COLOR for a 4px border, or 'none'."
        )
    width = int(match.group(1))
    if width < 1:
        raise ValueError(
            "Border width must be at least 1px; use --border none to "
            "remove the border."
        )
    color = match.group(2)
    if not _SHAPE_COLOR_RE.match(color):
        raise ValueError(
            f"Invalid border color {color!r}. Use a named color, "
            "#rrggbb, or rgb()/rgba()."
        )
    return {"width": width, "color": color}


def _shape_mask_files(
    kind: str, region: dict, radius: float | None, border: dict | None
) -> dict:
    """Generate the real masks for a resolved shape into the workspace.

    The border mask is a full disc at the padded outer size — it
    composites UNDER the content, never as an annulus over it (a ring
    over the content leaves a coverage seam at the shared edge).
    """
    masks_dir = os.path.realpath(os.path.join(MOVIESTAR_DIR, "masks"))
    width = int(region["width"])
    height = int(region["height"])
    radius_px = (
        int(round(radius * min(width, height))) if radius is not None else 0
    )
    files: dict[str, str] = {}
    if kind == "circle":
        files["content"] = str(
            ensure_mask(masks_dir, width=width, height=height, kind="circle")
        )
    elif kind == "rounded":
        files["content"] = str(
            ensure_mask(
                masks_dir, width=width, height=height,
                kind="rounded", radius=radius_px,
            )
        )
    if border is not None and kind != "rect":
        pad = 2 * int(border["width"])
        if kind == "circle":
            files["border_disc"] = str(
                ensure_mask(
                    masks_dir, width=width + pad, height=height + pad,
                    kind="circle",
                )
            )
        else:
            files["border_disc"] = str(
                ensure_mask(
                    masks_dir, width=width + pad, height=height + pad,
                    kind="rounded", radius=radius_px + int(border["width"]),
                )
            )
    return files


def _write_shape_preview(
    scene_name: str,
    slot_name: str,
    layout: dict,
    canvas: dict,
    kind: str,
    radius: float | None,
    border: dict | None,
    region: dict,
) -> str:
    """Write a truthful preview for one candidate shape via the shared
    layout-preview writer (#482: same scaled display size as the
    catalog), under a per-kind filename so circle-vs-rect stay
    diffable side by side."""
    name = f"{_canvas_label(canvas)}_{scene_name}-{slot_name}-shape-{kind}.svg"
    path = os.path.realpath(
        os.path.join(MOVIESTAR_DIR, LAYOUT_PREVIEWS_DIR, name)
    )
    preview_layout = copy.deepcopy(layout)
    geometry = preview_layout.setdefault("geometry", {})
    slot_geometry = geometry.setdefault(slot_name, {})
    shape_block: dict = {"kind": kind}
    if radius is not None:
        shape_block["radius"] = radius
    if border is not None:
        shape_block["border"] = border
    slot_geometry["shape"] = shape_block
    return _write_layout_preview(preview_layout, canvas, output=path)

def _geometry_scene_catalog(command: str) -> tuple[dict, dict, dict, dict]:
    if not is_loaded():
        _no_project_error_exit(command)
    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit(command, f"Could not read project.json: {exc}")
    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit(command, str(exc))

    composition = spec.get("composition") or []
    scenes_by_name = {
        scene["name"]: scene
        for scene in composition
        if isinstance(scene, dict) and scene.get("name") and scene.get("layout")
    }
    if not scenes_by_name:
        _error_exit_with_hint(
            command,
            "No scene composition to attach slot geometry to.",
            "Run 'moviestar scenes set' to author a picture-in-picture "
            "scene first, then retry with SCENE:inset.",
        )
    canvas = spec.get("composition_canvas") or _parse_canvas_arg("short")
    return spec, scenes_by_name, canvas, project


def _reconcile_geometry_camera_changes(
    command: str,
    *,
    project: dict,
    old_spec: dict,
    new_spec: dict,
) -> tuple[dict, list[dict]]:
    motion = new_spec.get("motion") or {"version": 1, "scenes": []}
    source_dimensions = {
        source["id"]: (int(source["width"]), int(source["height"]))
        for source in project.get("sources", [])
        if source.get("width") and source.get("height")
    }
    default_canvas = _parse_canvas_arg("short")
    assert default_canvas is not None
    try:
        reconciled_motion, changes = reconcile_camera_geometry(
            old_spec.get("composition") or [],
            new_spec.get("composition") or [],
            motion,
            source_dimensions,
            old_spec.get("composition_canvas") or default_canvas,
            new_spec.get("composition_canvas") or default_canvas,
        )
    except CameraGeometryMutationError as exc:
        _error_exit_with_hint(
            command,
            str(exc),
            "Update or remove the named camera move, then retry the slot "
            "geometry change.",
            camera_id=exc.camera_id,
        )
    if reconciled_motion != motion:
        new_spec = set_motion(new_spec, reconciled_motion)
    return new_spec, changes


def _geometry_overview() -> dict:
    return {
        "status": "slot_geometry_authoring",
        "writes_spec": False,
        "addressing": "SCENE:SLOT, for example demo:inset",
        "commands": [
            {
                "command": (
                    "moviestar scenes geometry demo:inset --size small "
                    "--at top-left"
                ),
                "purpose": "adjust an already-authored scene slot",
            },
            {
                "command": (
                    "moviestar scenes set scenes.json --dry-run"
                ),
                "purpose": (
                    "declarative path: put geometry on the addressed "
                    "slot in the scene JSON"
                ),
            },
            {
                "command": (
                    "moviestar layouts preview picture-in-picture --canvas "
                    "short --slot-size inset=small --slot-at inset=top-left"
                ),
                "purpose": "preview geometry against a layout preset",
            },
        ],
        "sizes": {
            **SLOT_GEOMETRY_SIZE_FRACTIONS,
            "custom": (
                f"{SLOT_GEOMETRY_MIN_SIZE:.2f}.."
                f"{SLOT_GEOMETRY_MAX_SIZE:.2f} of the canvas shorter dimension"
            ),
        },
        "anchors": list(SLOT_GEOMETRY_ANCHORS),
        "motion": {"presets": ["bounce"], "speeds": list(SLOT_GEOMETRY_SPEEDS)},
        "shapes": {
            "kinds": list(SLOT_SHAPE_KINDS),
            "border": "'WIDTHpx COLOR' such as '4px white', or 'none'",
            "note": (
                "--shape/--radius/--border persist to spec.json and "
                "render in export, screenshot, and inspect."
            ),
        },
        "hint": (
            "Run 'moviestar scenes geometry --help', then adjust a "
            "picture-in-picture scene or use --dry-run to preview it."
        ),
    }


@scenes.command("geometry")
@project_workspace_option
@click.argument("target", required=False, metavar="SCENE:SLOT")
@click.option(
    "--size",
    default=None,
    help=(
        "Slot size: small (0.22), medium (0.32), large (0.44), a custom "
        "normalized value such as 0.28 (square), or WxH such as "
        "0.40x0.30 for a non-square inset (opts out of circle; the "
        "shape defaults to rounded)."
    ),
)
@click.option(
    "--at",
    "anchor",
    type=click.Choice(SLOT_GEOMETRY_ANCHORS),
    default=None,
    help="Place at one of nine anchors; mutually exclusive with --x/--y.",
)
@click.option("--x", type=float, default=None, help="Normalized left position, 0..1.")
@click.option("--y", type=float, default=None, help="Normalized top position, 0..1.")
@click.option(
    "--margin",
    default=None,
    help=(
        "Anchor margin in whole pixels: PIXELS or X,Y. Default: 4% of the "
        "shorter canvas dimension, resolved to whole pixels."
    ),
)
@click.option(
    "--motion",
    type=click.Choice(["bounce"]),
    default=None,
    help="Named slot motion preset: bounce.",
)
@click.option(
    "--speed",
    type=click.Choice(SLOT_GEOMETRY_SPEEDS),
    default=None,
    help="Motion speed: slow, medium, or fast; requires --motion bounce.",
)
@click.option(
    "--shape",
    type=click.Choice(SLOT_SHAPE_KINDS),
    default=None,
    help=(
        "Inset shape: circle, rounded, or rect. Persists to spec.json "
        "and renders in export, screenshot, and inspect."
    ),
)
@click.option(
    "--radius",
    type=float,
    default=None,
    help=(
        "Rounded-corner radius as a fraction of the slot's shorter "
        f"side, 0..0.5 (default {SLOT_SHAPE_DEFAULT_RADIUS}); implies "
        "--shape rounded."
    ),
)
@click.option(
    "--border",
    default=None,
    help=(
        "Solid border: 'WIDTHpx COLOR' such as '4px white', a bare "
        "COLOR for a 4px border, or 'none' to remove it. The border "
        "sits outside the content edge."
    ),
)
@click.option(
    "--framing",
    default=None,
    help=(
        "Update the inset slot's framing in place: fit, fill, "
        "fill:<anchor>, fill:x=<0..1>, or fill:anchor=<x>,<y>. Shaped "
        "slots need fill framing."
    ),
)
@click.option(
    "--reset",
    is_flag=True,
    help=(
        "Restore the preset default: a medium circular inset with a "
        "4px white border, bottom-right, static. For a plain rectangle "
        "use --shape rect --border none."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate and resolve geometry without writing spec.json.",
)
def scenes_geometry(
    target: str | None,
    size: str | None,
    anchor: str | None,
    x: float | None,
    y: float | None,
    margin: str | None,
    motion: str | None,
    speed: str | None,
    shape: str | None,
    radius: float | None,
    border: str | None,
    framing: str | None,
    reset: bool,
    dry_run: bool,
) -> None:
    """Set a scene slot's size, placement, shape, framing, and motion.

    \b
    Address one slot as SCENE:SLOT:
      moviestar scenes geometry demo:inset --size small --at top-left
      moviestar scenes geometry demo:inset --size 0.28 --x 0.1 --y 0.2
      moviestar scenes geometry demo:inset --motion bounce --speed medium
      moviestar scenes geometry demo:inset --reset

    \b
    Shape the inset (persists and renders):
      moviestar scenes geometry demo:inset --shape circle
      moviestar scenes geometry demo:inset --border '4px white'
      moviestar scenes geometry demo:inset --shape rounded --radius 0.15
      moviestar scenes geometry demo:inset --shape rect
      moviestar scenes geometry demo:inset --shape rect --border '4px black'
      moviestar scenes geometry demo:inset --framing fill:center

    \b
    Sizes small / medium / large are square and use the canvas's shorter
    dimension. --at accepts top-left, top, top-right, left, center, right,
    bottom-left, bottom, or bottom-right. --x and --y are normalized exact
    top-left coordinates and cannot be combined with --at.

    A circle needs a square region, so it needs a named or custom --size.
    fit framing letterboxes with black bars — inside a circle those become
    black wedges — so shaped slots require fill framing.

    Changes persist by default. Use --dry-run to validate and resolve the
    same geometry without writing spec.json. Unspecified controls retain
    their stored values; --reset removes the complete override.
    """
    command = "scenes geometry"
    shape_controls = (shape, radius, border)
    shape_requested = any(value is not None for value in shape_controls)
    geometry_controls = (size, anchor, x, y, margin, motion, speed)
    controls = geometry_controls + shape_controls + (framing,)
    if target is None:
        if reset or dry_run or any(value is not None for value in controls):
            _error_exit_with_hint(
                command,
                "Geometry controls need a SCENE:SLOT target.",
                "Pass a target such as 'moviestar scenes geometry "
                "demo:inset --size small'.",
            )
        click.echo(json.dumps(_geometry_overview(), indent=2))
        return

    if not reset and not any(value is not None for value in controls):
        _error_exit_with_hint(
            command,
            "Pass at least one geometry control, or use --reset.",
            "Use --size, --at, --x/--y, --margin, --motion, --shape, or "
            "--framing. Run 'moviestar scenes list' to inspect the "
            "current geometry.",
        )

    if reset and any(value is not None for value in controls):
        _error_exit_with_hint(
            command,
            "--reset cannot be combined with geometry controls.",
            "Run --reset by itself, or drop --reset and pass only the "
            "geometry values to change.",
        )
    if anchor is not None and (x is not None or y is not None):
        _error_exit_with_hint(
            command,
            "--at cannot be combined with normalized --x/--y placement.",
            "Choose --at ANCHOR, or pass both --x and --y.",
        )
    if margin is not None and (x is not None or y is not None):
        _error_exit_with_hint(
            command,
            "--margin only applies to anchored placement.",
            "Use --margin with --at ANCHOR, or drop --margin when passing "
            "exact --x and --y coordinates.",
        )
    if (x is None) != (y is None):
        _error_exit_with_hint(
            command,
            "Normalized placement is incomplete.",
            "Pass --x and --y together, or use --at ANCHOR.",
        )
    if speed is not None and motion is None:
        _error_exit_with_hint(
            command,
            "--speed has no effect without slot motion.",
            "Add --motion bounce, or drop --speed for a static slot.",
        )
    if x is not None and (
        not math.isfinite(x) or not math.isfinite(y) or x < 0 or x > 1 or y < 0 or y > 1
    ):
        _error_exit_with_hint(
            command,
            "--x and --y must be normalized values from 0 to 1.",
            "Use values such as --x 0.10 --y 0.20.",
        )
    if radius is not None and shape in ("circle", "rect"):
        _error_exit_with_hint(
            command,
            f"--radius only applies to rounded corners, not {shape!r}.",
            "Use --shape rounded --radius 0.15, or drop --radius.",
        )
    if radius is not None and (
        not math.isfinite(radius) or radius < 0 or radius > 0.5
    ):
        _error_exit_with_hint(
            command,
            "--radius must be a fraction of the slot's shorter side, "
            "from 0 to 0.5.",
            "Use values such as --radius 0.15; 0.5 rounds fully (a "
            "circle on a square slot).",
        )

    try:
        scene_name, slot_name = _parse_geometry_target(target)
    except ValueError as exc:
        _error_exit_with_hint(
            command,
            str(exc),
            "Use a target from 'moviestar scenes list', written as SCENE:SLOT.",
        )

    spec, scenes_by_name, canvas, project = _geometry_scene_catalog(command)
    if scene_name not in scenes_by_name:
        available = ", ".join(sorted(scenes_by_name))
        _error_exit_with_hint(
            command,
            f"Unknown scene {scene_name!r}.",
            f"Use one of the current scenes: {available}.",
        )
    scene = scenes_by_name[scene_name]
    preset = scene["layout"]["preset"]
    slot_names = [slot.get("slot") for slot in scene.get("slots", [])]
    if preset != "picture-in-picture" or slot_name != "inset":
        suggested = f"{scene_name}:inset"
        if preset != "picture-in-picture":
            _error_exit_with_hint(
                command,
                f"Scene {scene_name!r} uses {preset!r}; slot geometry only "
                "supports the picture-in-picture inset.",
                "Choose a picture-in-picture scene from 'moviestar scenes "
                "list', or re-author this scene with that layout.",
            )
        _error_exit_with_hint(
            command,
            f"Slot {slot_name!r} is not the picture-in-picture inset.",
            f"Use {suggested!r}. Current slots: {', '.join(slot_names)}.",
        )

    current_layout = scene["layout"]
    current_geometry = copy.deepcopy(
        (current_layout.get("geometry") or {}).get("inset") or {}
    )
    legacy_inset = copy.deepcopy(current_layout.get("inset"))
    new_spec = copy.deepcopy(spec)
    new_scene = next(
        item
        for item in new_spec["composition"]
        if item.get("name") == scene_name
    )
    new_layout = new_scene["layout"]
    migration = None
    if legacy_inset is not None:
        migration = {
            "removed": "layout.inset",
            "previous": legacy_inset,
            "note": (
                "The legacy inset shape cannot coexist with canonical "
                "layout.geometry and was replaced by this write."
            ),
        }
    if reset:
        # #498: reset restores the CURRENT preset default — the
        # materialized circle — matching layouts preview and newly
        # authored scenes. Stripping the geometry instead would drop
        # the scene into the legacy state and fire the
        # legacy_rect_inset warning on a deliberate reset.
        default_geometry = copy.deepcopy(M26_DEFAULT_INSET_GEOMETRY)
        new_layout.pop("inset", None)
        new_layout["geometry"] = {"inset": default_geometry}
        default_region = resolve_inset_geometry(
            default_geometry, canvas, check_bounds=False
        )
        new_spec, camera_changes = _reconcile_geometry_camera_changes(
            command,
            project=project,
            old_spec=spec,
            new_spec=new_spec,
        )
        if not dry_run:
            try:
                save_spec(new_spec, command=command)
            except SpecValidationError as exc:
                _error_exit(command, str(exc))
        result = {
            "status": (
                "would_reset_slot_geometry" if dry_run
                else "reset_slot_geometry"
            ),
            "writes_spec": not dry_run,
            "target": {"scene": scene_name, "slot": slot_name},
            "geometry_source": "preset",
            "geometry": None,
            "stored_geometry": copy.deepcopy(default_geometry),
            "region": default_region,
            "region_is": "instant",
            "resolved_start_region": default_region,
            "camera_changes": camera_changes,
            "hint": (
                "Dry-run only: spec.json was not changed. Re-run without "
                "--dry-run to restore the preset default."
                if dry_run
                else "Reset to the preset default: a medium circular "
                "inset with a 4px white border, bottom-right. For a "
                "plain rectangle use --shape rect --border none."
            ),
        }
        if dry_run:
            result["dry_run"] = True
        if migration is not None:
            result["migration"] = migration
        click.echo(json.dumps(result, indent=2))
        return

    try:
        size_label, size_fraction = (
            _parse_geometry_size(size) if size is not None else ("preset", None)
        )
        margin_xy = _parse_geometry_margin(margin) if margin is not None else None
    except ValueError as exc:
        _error_exit_with_hint(
            command,
            str(exc),
            "Run 'moviestar scenes geometry --help' for size and margin examples.",
        )
    framing_spec = None
    if framing is not None:
        try:
            framing_spec = _parse_framing_arg(framing)
        except ValueError as exc:
            _error_exit_with_hint(
                command,
                str(exc),
                "Run 'moviestar scenes geometry --help' for framing examples.",
            )

    inset_geometry = current_geometry
    if size_fraction is not None:
        inset_geometry["size"] = size_fraction
    if x is not None and y is not None:
        for key in ("anchor", "margin_x", "margin_y"):
            inset_geometry.pop(key, None)
        inset_geometry["x"] = x
        inset_geometry["y"] = y
    elif anchor is not None or margin_xy is not None:
        inset_geometry.pop("x", None)
        inset_geometry.pop("y", None)
        if anchor is not None:
            inset_geometry["anchor"] = anchor
        if margin_xy is not None:
            inset_geometry["margin_x"] = margin_xy[0]
            inset_geometry["margin_y"] = margin_xy[1]
    if motion is not None:
        previous_motion = inset_geometry.get("motion") or {}
        inset_geometry["motion"] = {
            "preset": motion,
            "speed": (
                speed
                or previous_motion.get("speed")
                or "medium"
            ),
        }

    default_margin = default_slot_margin(canvas)
    if "x" in inset_geometry:
        placement = {
            "mode": "normalized",
            "x": inset_geometry["x"],
            "y": inset_geometry["y"],
        }
    else:
        placement = {
            "mode": "anchor",
            "anchor": inset_geometry.get("anchor", "bottom-right"),
            "margin_x": inset_geometry.get("margin_x", default_margin),
            "margin_y": inset_geometry.get("margin_y", default_margin),
        }
    region = resolve_inset_geometry(inset_geometry, canvas, check_bounds=False)
    width = region["width"]
    height = region["height"]
    stored_size = inset_geometry.get("size")
    if size is None and stored_size is not None:
        size_label = next(
            (
                label
                for label, fraction in SLOT_GEOMETRY_SIZE_FRACTIONS.items()
                if fraction == stored_size
            ),
            "custom",
        )
    size_out = {
        "value": size_label,
        "fraction": (
            round(stored_size, 4)
            if isinstance(stored_size, (int, float))
            else None
        ),
        "width": width,
        "height": height,
    }
    if isinstance(stored_size, dict):
        size_out["fraction_w"] = round(float(stored_size["width"]), 4)
        size_out["fraction_h"] = round(float(stored_size["height"]), 4)

    if (
        region["x"] < 0
        or region["y"] < 0
        or region["x"] + width > int(canvas["width"])
        or region["y"] + height > int(canvas["height"])
    ):
        _error_exit_with_hint(
            command,
            "The resolved slot region leaves the canvas.",
            "Reduce --size or --margin, or choose --x/--y values that keep "
            "the full slot inside the canvas.",
            resolved_region=region,
            canvas=canvas,
        )

    shape_fields = None
    if (
        not shape_requested
        and width != height
        and (current_geometry.get("shape") or {}).get("kind") == "circle"
    ):
        # A non-square size opts out of circle (spec rule): downgrade
        # the stored circle to rounded rather than failing the size
        # edit; the border and any stored radius carry over.
        downgraded = copy.deepcopy(current_geometry["shape"])
        downgraded["kind"] = "rounded"
        downgraded.setdefault("radius", SLOT_SHAPE_DEFAULT_RADIUS)
        inset_geometry["shape"] = downgraded
        shape_fields = {
            "shape": downgraded,
            "shape_note": (
                "This size is not square, which opts out of circle; the "
                "stored shape is now rounded. Use a single-fraction "
                "--size for a circle."
            ),
        }
    if shape_requested:
        border_spec = None
        try:
            if border is not None:
                border_spec = _parse_shape_border(border)
        except ValueError as exc:
            _error_exit_with_hint(
                command,
                str(exc),
                "Run 'moviestar scenes geometry --help' for border examples.",
            )
        stored_shape = current_geometry.get("shape") or {}
        shape_note = None
        square = width == height
        if shape is not None:
            kind = shape
        elif radius is not None:
            kind = "rounded"
        elif stored_shape.get("kind") is not None:
            kind = stored_shape["kind"]
        elif square:
            kind = "circle"
        else:
            kind = "rounded"
            shape_note = (
                "This slot's region is not square, which opts out of "
                "circle; the shape defaults to rounded. Set a square "
                "--size to get a circle."
            )
        if kind == "circle" and not square:
            _error_exit_with_hint(
                command,
                f"A circle needs a square region; this slot resolves to "
                f"{width}x{height}.",
                "Set a square size first: --size small, medium, large, "
                "or a custom fraction such as --size 0.28.",
                resolved_region=region,
            )
        inset_slot = next(
            (
                item
                for item in scene.get("slots", [])
                if item.get("slot") == slot_name
            ),
            None,
        )
        framing_mode = (
            framing_spec
            or (inset_slot or {}).get("framing")
            or {"mode": "fill"}
        ).get("mode")
        if kind in ("circle", "rounded") and framing_mode == "fit":
            _error_exit_with_hint(
                command,
                "fit framing letterboxes with black bars; inside a "
                f"{kind} shape those become black wedges.",
                "Add --framing fill:center to this command to switch the "
                "slot to fill framing along with the shape.",
            )

        radius_value = None
        if kind == "rounded":
            if radius is not None:
                radius_value = radius
            else:
                radius_value = stored_shape.get(
                    "radius", SLOT_SHAPE_DEFAULT_RADIUS
                )
        # --border sets or (with 'none') clears the border; when the
        # flag is absent a stored border carries over, unless the kind
        # switches to rect. An explicit --border enables a rect edge.
        if border is not None:
            effective_border = border_spec
        elif kind != "rect":
            effective_border = copy.deepcopy(stored_shape.get("border"))
        else:
            effective_border = None
        shape_block: dict[str, object] = {"kind": kind}
        if radius_value is not None:
            shape_block["radius"] = round(radius_value, 4)
        if effective_border is not None:
            shape_block["border"] = effective_border
        masks = _shape_mask_files(kind, region, radius_value, effective_border)
        preview_layout = copy.deepcopy(current_layout)
        preview_layout.pop("inset", None)
        preview_layout["geometry"] = {
            "inset": {
                key: value
                for key, value in inset_geometry.items()
                if key != "motion"
            }
        }
        preview_path = _write_shape_preview(
            scene_name,
            slot_name,
            preview_layout,
            canvas,
            kind,
            radius_value,
            effective_border,
            region,
        )
        shape_fields = {
            "shape": shape_block,
            "preview": preview_path,
        }
        if masks:
            shape_fields["masks"] = masks
        if shape_note is not None:
            shape_fields["shape_note"] = shape_note
        inset_geometry["shape"] = shape_block

    if framing_spec is not None:
        new_inset_slot = next(
            (
                item
                for item in new_scene.get("slots", [])
                if item.get("slot") == slot_name
            ),
            None,
        )
        if new_inset_slot is not None:
            new_inset_slot["framing"] = framing_spec

    new_layout.pop("inset", None)
    if inset_geometry:
        new_layout["geometry"] = {"inset": inset_geometry}
    new_spec, camera_changes = _reconcile_geometry_camera_changes(
        command,
        project=project,
        old_spec=spec,
        new_spec=new_spec,
    )
    if not dry_run:
        try:
            save_spec(new_spec, command=command)
        except SpecValidationError as exc:
            _error_exit(command, str(exc))

    geometry = (
        {"size": size_out, "placement": placement} if inset_geometry else None
    )
    if geometry is not None and inset_geometry.get("motion") is not None:
        geometry["motion"] = inset_geometry["motion"]
    moving = inset_geometry.get("motion") is not None
    result = {
        "status": (
            "would_set_slot_geometry" if dry_run else "set_slot_geometry"
        ),
        "writes_spec": not dry_run,
        "target": {"scene": scene_name, "slot": slot_name},
        "geometry_source": "override" if inset_geometry else "preset",
        "geometry": geometry,
        "stored_geometry": copy.deepcopy(inset_geometry) or None,
        "region": (
            {
                "x": 0,
                "y": 0,
                "width": int(canvas["width"]),
                "height": int(canvas["height"]),
            }
            if moving
            else region
        ),
        "region_is": "envelope" if moving else "instant",
        "resolved_start_region": region,
        "camera_changes": camera_changes,
        "hint": (
            "Dry-run only: spec.json was not changed. Re-run without "
            "--dry-run to store this geometry."
            if dry_run
            else "Slot geometry stored in spec.json. Run 'moviestar scenes "
            "list' to inspect it or use --reset to restore the preset."
        ),
    }
    if dry_run:
        result["dry_run"] = True
    if migration is not None:
        result["migration"] = migration
    if moving:
        result["motion_note"] = (
            "Bounce starts at the authored placement and follows result time. "
            "Matching bounce motion continues across adjacent scene boundaries; "
            "the region above is its full envelope."
        )
    if shape_fields is not None:
        result["shape"] = shape_fields["shape"]
        shape_preview = {
            key: shape_fields[key]
            for key in ("preview", "masks")
            if key in shape_fields
        }
        if shape_preview:
            result["shape_preview"] = shape_preview
        if "shape_note" in shape_fields:
            result["shape_note"] = shape_fields["shape_note"]
    if framing_spec is not None:
        result["framing"] = framing_spec
    click.echo(json.dumps(result, indent=2))


@scenes.command("set")
@project_workspace_option
@click.pass_context
@click.argument("scene_file", required=False, type=click.Path())
@click.option(
    "--canvas",
    "canvas_arg",
    default="short",
    help="Canvas preset or WIDTHxHEIGHT for every scene.",
)
@click.option(
    "--scene",
    "scene_args",
    multiple=True,
    help=(
        "Define one scene as NAME=LAYOUT, e.g. intro=single. Repeat in "
        "result order."
    ),
)
@click.option(
    "--slot",
    "slot_args",
    multiple=True,
    help=(
        "Assign a slot in a named scene as SCENE:SLOT=SOURCE, e.g. "
        "conversation:top=holden. Repeat for every slot."
    ),
)
@click.option(
    "--from",
    "from_tcs",
    multiple=True,
    help=(
        "Required once per --slot. Edited-source result-time start for the "
        "matching --slot, paired by flag position."
    ),
)
@click.option(
    "--to",
    "to_tcs",
    multiple=True,
    help=(
        "Required once per --slot. Edited-source result-time end for the "
        "matching --slot, paired by flag position."
    ),
)
@click.option(
    "--framing",
    "framing_args",
    multiple=True,
    help=(
        "Frame the matching --slot by flag position. Values: fit, fill, "
        "fill:<anchor>, fill:x=<0..1>, or fill:anchor=<x>,<y>. "
        "For fill:x, 0=left, 0.5=center, 1=right."
    ),
)
@click.option(
    "--audio-from",
    "audio_from_args",
    multiple=True,
    help=(
        "Choose scene audio as SCENE=SOURCE, e.g. conversation=holden. "
        "Repeat for scenes with multiple audio-capable slots."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help=(
        "Validate and resolve the complete scene plan without writing "
        "spec.json. Works identically for JSON-file and repeated-flag input."
    ),
)
def scenes_set(
    ctx: click.Context,
    scene_file: str | None,
    canvas_arg: str,
    scene_args: tuple[str, ...],
    slot_args: tuple[str, ...],
    from_tcs: tuple[str, ...],
    to_tcs: tuple[str, ...],
    framing_args: tuple[str, ...],
    audio_from_args: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Replace the full scene-layout composition.

    \b
    Pass an editable JSON file for large compositions:

    \b
      {
        "canvas": "short",
        "scenes": [{
          "name": "intro", "layout": "single",
          "slots": [{"slot": "main", "source": "holden",
                     "from": "0:12:10", "to": "0:12:16",
                     "framing": "fill:left"}],
          "audio_from": "holden"
        }]
      }

    \b
    JSON file input and repeated --scene/--slot flags are mutually
    exclusive. --dry-run resolves the same full scene plan without
    writing spec.json.

    On a picture-in-picture slot object, optional ``geometry`` carries the
    canonical inset override, for example ``"geometry": {"size": 0.22,
    "anchor": "top-left"}``. Omit it to retain stored geometry when
    re-authoring the same scene; set it to null to clear the override. The
    response's ``geometry_preserved`` count confirms how many stored slot
    geometries were retained because they were omitted from the input.

    After motion is authored, `scenes list` and `spec` expose stable scene and
    slot IDs. Keep those optional `id` fields in JSON when renaming a scene or
    slot so its attached motion follows safely. Deletions report every motion
    record removed from the deleted identity.

    Timing contract: deleting a scene reports its scene-bound overlays
    under ``overlays_detached``. Shortening a scene keeps the overlapping part
    visible and reports any clipped scene-bound overlays.
    """
    flag_input_used = bool(
        scene_args
        or slot_args
        or from_tcs
        or to_tcs
        or framing_args
        or audio_from_args
        or ctx.get_parameter_source("canvas_arg").name != "DEFAULT"
    )
    if scene_file is not None and flag_input_used:
        _error_exit_with_hint(
            "scenes set",
            "A scene JSON file cannot be combined with --canvas, --scene, "
            "--slot, --from, --to, --framing, or --audio-from.",
            "Pick one authoring mode: 'moviestar scenes set scenes.json' "
            "or repeated scene flags.",
        )

    if not is_loaded():
        _no_project_error_exit("scenes set")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("scenes set", f"Could not read project.json: {exc}")

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("scenes set", str(exc))

    file_schema_errors: list[dict[str, str]] = []
    motion_lifecycle = {"renamed": [], "removed": []}
    camera_changes: list[dict] = []
    try:
        if scene_file is not None:
            canvas, scene_inputs, file_schema_errors = _load_scene_file_inputs(
                scene_file
            )
        else:
            canvas, scene_inputs = _scene_inputs_from_flags(
                canvas_arg=canvas_arg,
                scene_args=scene_args,
                slot_args=slot_args,
                from_tcs=from_tcs,
                to_tcs=to_tcs,
                framing_args=framing_args,
                audio_from_args=audio_from_args,
            )
        geometry_preserved = _preserved_scene_geometry_count(
            spec.get("composition"), scene_inputs
        )
        source_durations = {
            source["id"]: float(source["duration"]["seconds"])
            for source in project["sources"]
        }
        new_spec = set_scene_composition(
            spec,
            scenes=scene_inputs,
            source_durations=source_durations,
            canvas=canvas,
        )
        current_motion = spec.get("motion") or {"version": 1, "scenes": []}
        reconciled_motion, motion_lifecycle = reconcile_motion_lifecycle(
            spec.get("composition"),
            new_spec.get("composition"),
            current_motion,
        )
        if reconciled_motion != current_motion:
            new_spec = set_motion(new_spec, reconciled_motion)
        new_spec, camera_changes = _reconcile_geometry_camera_changes(
            "scenes set",
            project=project,
            old_spec=spec,
            new_spec=new_spec,
        )
        audio_capable_sources = {
            source["id"]
            for source in project["sources"]
            if bool(source.get("audio_codec"))
        }
        for scene_index, scene_input in enumerate(scene_inputs):
            name = scene_input["name"]
            scene_audio_sources = {
                source_id
                for _slot, source_id, _from_s, _to_s, _framing in scene_input["slots"]
                if source_id in audio_capable_sources
            }
            if len(scene_audio_sources) > 1 and scene_input["audio_from"] is None:
                message = (
                    f"Scene {name!r} has multiple audio-capable slot sources "
                    f"({', '.join(sorted(scene_audio_sources))}). Pass "
                    f"--audio-from {name}=SOURCE to choose one."
                )
                if scene_file is not None:
                    raise SceneValidationError(
                        f"scenes[{scene_index}].audio_from", message
                    )
                raise ValueError(message)
        scene_plan = _scene_composition_plan_with_audio(
            project=project,
            spec=new_spec,
            composition=new_spec["composition"],
            command="scenes set",
        )
        scenes_out = scene_plan["scenes"]
        duration_s = scene_plan["composition_duration"]
        slots_count = scene_plan["slots_count"]
    except _SceneFileSchemaError as exc:
        assert scene_file is not None
        _scene_file_validation_exit(scene_file, exc.errors)
    except SceneSlotDurationMismatchError as exc:
        if scene_file is not None:
            _scene_file_validation_exit(
                scene_file,
                file_schema_errors
                + [_scene_file_error(exc.path, exc.message)],
                hint=SCENE_SLOT_DURATION_MISMATCH_HINT,
            )
        _error_exit_with_hint(
            "scenes set",
            str(exc),
            SCENE_SLOT_DURATION_MISMATCH_HINT,
        )
    except SceneValidationError as exc:
        if scene_file is not None:
            _scene_file_validation_exit(
                scene_file,
                file_schema_errors
                + [_scene_file_error(exc.path, exc.message)],
            )
        _error_exit("scenes set", str(exc))
    except _SceneFlagPairingError as exc:
        # Issue #481: slots do not default to full source duration;
        # each --slot takes its own --from/--to pair by flag position.
        _error_exit_with_hint(
            "scenes set",
            str(exc),
            "Each --slot takes its own --from/--to pair, matched by "
            "position: --slot demo:inset=holden --from 0:00:00 "
            "--to 0:00:40.",
        )
    except (SpecValidationError, ValueError) as exc:
        _error_exit("scenes set", str(exc))

    if scene_file is not None and file_schema_errors:
        _scene_file_validation_exit(scene_file, file_schema_errors)

    if not dry_run:
        try:
            save_spec(new_spec, command="scenes set")
        except SpecValidationError as exc:
            _error_exit("scenes set", str(exc))

    result = {
        "status": (
            "would_set_scene_composition" if dry_run else "set_scene_composition"
        ),
        "writes_spec": not dry_run,
        "composition_type": "scenes",
        "canvas": canvas,
        "scenes_count": len(scenes_out),
        "slots_count": slots_count,
        "geometry_preserved": geometry_preserved,
        "composition_duration": format_timecode(duration_s),
        "scenes": scenes_out,
        "motion_lifecycle": motion_lifecycle,
        "camera_changes": camera_changes,
    }
    if dry_run:
        result["dry_run"] = True
        result["hint"] = (
            "Dry-run only — spec.json was not changed. Re-run without "
            "--dry-run to write this resolved scene composition."
        )
    else:
        result["hint"] = (
            "Scene composition written to spec.json. Run 'moviestar status' "
            "to inspect scene order and ranges."
        )
    _attach_overlay_timeline_mutation(
        result,
        project=project,
        old_spec=spec,
        new_spec=new_spec,
        code="scene_result_clock_changed_overlays_stale",
        change_label="scene change",
    )
    click.echo(json.dumps(result, indent=2))


@scenes.command("add")
@click.option(
    "--canvas",
    "canvas_arg",
    default="short",
    help="Canvas preset or WIDTHxHEIGHT for the scene preview.",
)
@click.option("--name", "name", required=True, help="Scene name.")
@click.option(
    "--layout",
    "layout",
    required=True,
    help="Preset layout for this scene: single, two-up, picture-in-picture.",
)
@click.option(
    "--slot",
    "slot_args",
    multiple=True,
    help="Assign a source to a layout slot, e.g. top=holden.",
)
@click.option("--from", "from_tcs", multiple=True, help="Matching slot start.")
@click.option("--to", "to_tcs", multiple=True, help="Matching slot end.")
@click.option(
    "--framing",
    "framing_args",
    multiple=True,
    help=(
        "Frame the matching slot. Values: fit, fill, fill:<anchor>, "
        "fill:x=<0..1>, or fill:anchor=<x>,<y>. For fill:x, "
        "0=left, 0.5=center, 1=right."
    ),
)
@click.option(
    "--audio-from",
    "audio_from",
    default=None,
    help="Source whose audio should play during this scene.",
)
def scenes_add(
    canvas_arg: str,
    name: str,
    layout: str,
    slot_args: tuple[str, ...],
    from_tcs: tuple[str, ...],
    to_tcs: tuple[str, ...],
    framing_args: tuple[str, ...],
    audio_from: str | None,
) -> None:
    """Preview adding one scene to an incremental scene composition.

    \b
    Incremental authoring makes each command smaller: add one scene,
    inspect it, then add the next. The tradeoff is that agents need a
    few more commands to build a full edit.

    \b
    This surface preview validates one scene and returns its envelope,
    but it does not write spec.json yet.
    """
    try:
        if layout not in LAYOUT_PRESETS:
            raise ValueError(
                "Unknown layout preset "
                f"{layout!r}. Use one of: {', '.join(sorted(LAYOUT_PRESETS))}."
            )
        if not slot_args:
            raise ValueError("Pass at least one --slot SLOT=SOURCE.")
        if not (len(slot_args) == len(from_tcs) == len(to_tcs)):
            raise ValueError(
                f"--slot / --from / --to counts must match. Got "
                f"{len(slot_args)} --slot, {len(from_tcs)} --from, "
                f"{len(to_tcs)} --to."
            )
        if framing_args and len(framing_args) != len(slot_args):
            raise ValueError(
                f"--framing count must match --slot count. Got "
                f"{len(framing_args)} --framing for {len(slot_args)} --slot."
            )
        canvas = _parse_canvas_arg(canvas_arg)
        assert canvas is not None
        slot_rows = []
        for i, (slot_arg, from_tc, to_tc) in enumerate(
            zip(slot_args, from_tcs, to_tcs)
        ):
            slot_name, source_id = _parse_slot_arg(slot_arg)
            framing = (
                _parse_framing_arg(framing_args[i])
                if framing_args
                else {"mode": "fill", "anchor": "center"}
            )
            slot_rows.append(
                (
                    slot_name,
                    source_id,
                    parse_timecode(from_tc),
                    parse_timecode(to_tc),
                    framing,
                )
            )
        scene, _duration_s = _surface_scene_envelope(
            name=name,
            index=0,
            layout=layout,
            canvas=canvas,
            slot_rows=slot_rows,
            audio_from=audio_from,
            result_start_s=0.0,
        )
    except (SpecValidationError, ValueError) as exc:
        _error_exit("scenes add", str(exc))

    result = _scene_surface_result(
        status="would_add_scene",
        canvas=canvas,
        scenes=[scene],
        hint=(
            "Surface preview only: spec.json was not changed. If this "
            "incremental shape feels too chatty, compare "
            "'moviestar scenes set'."
        ),
    )
    click.echo(json.dumps(result, indent=2))


@scenes.command("list")
@project_workspace_option
def scenes_list() -> None:
    """List the current persisted scene composition.

    \b
    Reads spec.json and returns the same scene envelopes status/export
    use, including inferred single-slot audio routing.
    """
    if not is_loaded():
        _no_project_error_exit("scenes list")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("scenes list", f"Could not read project.json: {exc}")

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("scenes list", str(exc))

    composition = spec.get("composition")
    if (
        not _is_scene_layout_composition(composition)
        or _concat_presented(spec)
        or _layout_presented(spec)
    ):
        current_type = None
        if composition is not None:
            if _concat_presented(spec):
                current_type = "segments"
            else:
                current_type = (
                    "layout" if _is_layout_composition(composition) else "segments"
                )
        if current_type is not None:
            result = {
                "status": "not_scene_composition",
                "writes_spec": False,
                "composition_type": "scenes",
                "current_composition_type": current_type,
                "scenes_count": 0,
                "slots_count": 0,
                "scenes": [],
                "hint": (
                    f"A {current_type} composition is currently set, not a "
                    "scene composition. Use 'moviestar status' to inspect it; "
                    "'moviestar scenes set' will replace it with scenes."
                ),
            }
            click.echo(json.dumps(result, indent=2))
            return
        result = {
            "status": "empty",
            "writes_spec": False,
            "composition_type": "scenes",
            "scenes_count": 0,
            "slots_count": 0,
            "scenes": [],
            "hint": (
                "No scene composition is currently set. Use "
                "'moviestar scenes set' to create one."
            ),
        }
        click.echo(json.dumps(result, indent=2))
        return

    assert composition is not None
    plan = _scene_composition_plan_with_audio(
        project=project,
        spec=spec,
        composition=composition,
        command="scenes list",
    )
    result = {
        "status": "listed",
        "writes_spec": False,
        "composition_type": "scenes",
        "composition_canvas": plan["canvas"],
        "composition_duration": format_timecode(plan["composition_duration"]),
        "opening_transition": _transition_record_envelope(
            spec.get("opening_transition")
        ),
        "closing_transition": _transition_record_envelope(
            spec.get("closing_transition")
        ),
        "scenes_count": len(plan["scenes"]),
        "slots_count": plan["slots_count"],
        "scenes": plan["scenes"],
        "hint": (
            "Use 'moviestar status' for the full project view, "
            "'moviestar export --dry-run' for render commands, or "
            "'moviestar scenes transition' to inspect or change visual "
            "transitions."
        ),
    }
    _add_warnings(result, _unrendered_transition_warnings(spec))
    click.echo(json.dumps(result, indent=2))


@scenes.command("clear")
def scenes_clear() -> None:
    """Preview clearing the scene composition.

    \b
    The implemented command should remove the scene composition and keep
    source workspaces intact. This surface preview returns the write
    envelope shape without changing spec.json.
    """
    click.echo(
        json.dumps(
            {
                "status": "would_clear_scene_composition",
                "dry_run": True,
                "writes_spec": False,
                "composition_type": "scenes",
                "scenes_count": 0,
                "hint": (
                    "Surface preview only: spec.json was not changed. "
                    "The real command should clear the scene composition "
                    "while leaving loaded sources and per-source edits intact."
                ),
            },
            indent=2,
        )
    )


def _parse_inset_fraction(command: str, flag: str, raw: str) -> float:
    text = raw.strip()
    try:
        if text.endswith("%"):
            value = float(text[:-1]) / 100.0
        else:
            value = float(text)
            if value > 1.0:
                raise ValueError
    except ValueError:
        _error_exit_with_hint(
            command,
            f"{flag} {raw!r} is not a canvas fraction.",
            f"Use a percentage of the canvas ('25%') or a fraction "
            f"('0.25'), between {PIP_INSET_MIN_FRACTION} and "
            f"{PIP_INSET_MAX_FRACTION}.",
        )
    if not (PIP_INSET_MIN_FRACTION <= value <= PIP_INSET_MAX_FRACTION):
        _error_exit_with_hint(
            command,
            f"{flag} must be between {PIP_INSET_MIN_FRACTION} and "
            f"{PIP_INSET_MAX_FRACTION} of the canvas.",
            "Small enough to leave the main slot visible, large enough "
            "to read.",
        )
    return value


@scenes.command("inset")
@project_workspace_option
@click.option(
    "--scene",
    "scene_name",
    default=None,
    help="Scene to change (defaults to the only picture-in-picture scene).",
)
@click.option(
    "--corner",
    type=click.Choice(PIP_INSET_CORNERS),
    default=None,
    help="Canvas corner for the inset (default bottom-right).",
)
@click.option(
    "--width",
    "width_arg",
    default=None,
    help="Inset width as a canvas fraction ('25%' or 0.25; default 0.32).",
)
@click.option(
    "--height",
    "height_arg",
    default=None,
    help="Inset height as a canvas fraction ('20%' or 0.20; default 0.24).",
)
def scenes_inset(
    scene_name: str | None,
    corner: str | None,
    width_arg: str | None,
    height_arg: str | None,
) -> None:
    """Move or resize a scene's picture-in-picture inset.

    \b
    With no options, reports the current inset placement and regions.
    With --corner / --width / --height, stores the new placement;
    unspecified values keep their current setting. MovieStar never
    moves an inset on its own — camera preview warns when the inset
    covers selected content and names this command as the fix.

    This command preserves the legacy rectangular inset shape. For square named
    sizes, nine anchors, exact placement, motion, and reset, use
    ``moviestar scenes geometry SCENE:SLOT`` instead.

    Camera targets are re-resolved against a resized inset. The write response
    reports those changes and refuses any crop that would need to be reduced.
    """
    command = "scenes inset"
    if not is_loaded():
        _no_project_error_exit(command)
    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit(command, f"Could not read project.json: {exc}")
    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit(command, str(exc))

    composition = spec.get("composition") or []
    pip_scenes = [
        scene
        for scene in composition
        if isinstance(scene, dict)
        and scene.get("name")
        and (scene.get("layout") or {}).get("preset") == "picture-in-picture"
    ]
    if not pip_scenes:
        _error_exit_with_hint(
            command,
            "No picture-in-picture scene in the current composition.",
            "The inset exists only in picture-in-picture layouts. Author "
            "one with 'moviestar scenes set', then retry.",
        )
    if scene_name is None:
        if len(pip_scenes) == 1:
            scene = pip_scenes[0]
        else:
            names = ", ".join(s["name"] for s in pip_scenes)
            _error_exit_with_hint(
                command,
                f"{len(pip_scenes)} picture-in-picture scenes; pick one "
                "with --scene.",
                f"Scenes with an inset: {names}.",
            )
    else:
        scene = next(
            (s for s in pip_scenes if s["name"] == scene_name), None
        )
        if scene is None:
            names = ", ".join(s["name"] for s in pip_scenes)
            _error_exit_with_hint(
                command,
                f"Scene {scene_name!r} is not a picture-in-picture scene.",
                f"Scenes with an inset: {names}.",
            )

    canvas = spec.get("composition_canvas") or dict(CANVAS_PRESETS["short"])
    current = {
        **PIP_INSET_DEFAULTS,
        **(scene["layout"].get("inset") or {}),
    }
    write = any(
        value is not None for value in (corner, width_arg, height_arg)
    )
    if not write:
        click.echo(
            json.dumps(
                {
                    "status": "inset_settings",
                    "writes_spec": False,
                    "scene": scene["name"],
                    **({"scene_id": scene["id"]} if scene.get("id") else {}),
                    "inset": current,
                    "defaults": PIP_INSET_DEFAULTS,
                    "regions": layout_regions(
                        scene["layout"], canvas, result_time_s=0.0
                    ),
                    "hint": (
                        "Change the placement with --corner "
                        f"({', '.join(PIP_INSET_CORNERS)}) and/or "
                        "--width/--height canvas fractions, e.g. "
                        f"'moviestar scenes inset --scene {scene['name']} "
                        "--corner top-left --width 25%'."
                    ),
                },
                indent=2,
            )
        )
        return

    if scene["layout"].get("geometry"):
        _error_exit_with_hint(
            command,
            f"Scene {scene['name']!r} stores canonical layout.geometry; "
            "'scenes inset' only edits the legacy inset shape.",
            "Adjust or remove the scene's layout.geometry in the spec "
            "instead; the two placement shapes cannot be combined.",
        )

    updated = dict(current)
    if corner is not None:
        updated["corner"] = corner
    if width_arg is not None:
        updated["width"] = _parse_inset_fraction(command, "--width", width_arg)
    if height_arg is not None:
        updated["height"] = _parse_inset_fraction(
            command, "--height", height_arg
        )

    new_spec = copy.deepcopy(spec)
    new_scene = next(
        s for s in new_spec["composition"] if s.get("name") == scene["name"]
    )
    new_scene["layout"]["inset"] = updated
    old_regions = layout_regions(scene["layout"], canvas, result_time_s=0.0)
    new_regions = layout_regions(
        new_scene["layout"], canvas, result_time_s=0.0
    )
    new_spec, camera_changes = _reconcile_geometry_camera_changes(
        command,
        project=project,
        old_spec=spec,
        new_spec=new_spec,
    )
    try:
        save_spec(new_spec, command=command)
    except SpecValidationError as exc:
        _error_exit(command, str(exc))

    click.echo(
        json.dumps(
            {
                "status": "set_inset",
                "writes_spec": True,
                "scene": scene["name"],
                **({"scene_id": scene["id"]} if scene.get("id") else {}),
                "inset": updated,
                "regions_before": old_regions,
                "regions": new_regions,
                "camera_changes": camera_changes,
                "hint": (
                    "Inset placement stored in spec.json; screenshot, "
                    "watch, and export now place the inset at "
                    f"{updated['corner']}. Re-run 'moviestar scenes motion "
                    "camera preview' to re-check coverage warnings, or "
                    "'moviestar undo --composition' to revert."
                ),
            },
            indent=2,
        )
    )


# --- M20 scenes motion (slot camera moves + pacing) ---

_MOTION_FILE_KEYS = {"version", "scenes", "_examples"}
_MOTION_SCENE_KEYS = {"scene", "scene_id", "slots"}
_MOTION_SLOT_KEYS = {
    "slot", "slot_id", "source", "pacing", "camera", "source_extent",
}
_MOTION_PACING_KEYS = {"id", "mode", "range", "at", "space", "speed", "duration"}
_MOTION_CAMERA_KEYS = {
    "id", "kind", "range", "at", "timing", "from", "to", "return_to",
    "authored", "ease",
}
_MOTION_ZOOM_TIMING_KEYS = {"move_in", "hold", "move_out"}
_MOTION_RANGE_KEYS = {"from", "to", "space"}
_MOTION_STATE_KEYS = {"target"}
_MOTION_TARGET_KEYS = {"space", "units", "rect"}
_MOTION_RECT_KEYS = {"x", "y", "w", "h"}
MOTION_PACING_MODES = {"speed", "duration", "hold"}
MOTION_EASES = {"linear", "in", "out", "in-out", "cut"}
MOTION_TARGET_UNITS = {"normalized", "pixels"}
MOTION_DEFAULT_MOVE_SECONDS = 0.5
MOTION_TIME_DEFAULTS = {
    "pacing": "source-local",
    "camera": "result-local",
    "source-local_origin": "the slot's source_from",
    "result-local_origin": "the authored scene's start after pacing",
}
_MOTION_EXAMPLE = {
    "pacing": [
        {
            "id": "skip-loading",
            "mode": "speed",
            "range": {"from": "0:18", "to": "0:28", "space": "source-local"},
            "speed": 5.0,
        },
        {
            "id": "fit-wait",
            "mode": "duration",
            "range": {"from": "0:18", "to": "0:38", "space": "source-local"},
            "duration": "0:04",
        },
        {
            "id": "hold-result",
            "mode": "hold",
            "at": "0:30",
            "space": "source-local",
            "duration": "0:02",
        },
    ],
    "camera": [
        {
            "id": "zoom-command-palette",
            "range": {"from": "0:03", "to": "0:06", "space": "result-local"},
            "from": {"target": "full"},
            "to": {
                "target": {
                    "space": "source",
                    "units": "pixels",
                    "rect": {"x": 346, "y": 130, "w": 806, "h": 346},
                }
            },
            "ease": "in-out",
        },
        {
            "id": "pan-to-result",
            "range": {"from": "0:06", "to": "0:09", "space": "result-local"},
            "to": {
                "target": {
                    "space": "source",
                    "units": "normalized",
                    "rect": {"x": 0.54, "y": 0.48, "w": 0.36, "h": 0.28},
                }
            },
            "ease": "linear",
        },
        {
            "id": "back-to-full",
            "at": "0:12",
            "to": {"target": "full"},
        },
    ],
}


def _motion_tc(seconds: float) -> str:
    return format_timecode(seconds)["text"]


def _motion_validation_exit(
    command: str, motion_file: str, errors: list[dict[str, str]]
) -> None:
    click.echo(
        json.dumps(
            {
                "error": (
                    f"{len(errors)} validation error(s) in "
                    f"{os.path.realpath(motion_file)}."
                ),
                "command": command,
                "errors": errors,
                "hint": (
                    "Fix the listed JSON paths and re-run 'moviestar scenes "
                    "motion set <file> --dry-run'."
                ),
            },
            indent=2,
        )
    )
    sys.exit(1)


def _motion_scene_catalog(command: str) -> tuple[dict, dict, dict, dict]:
    """Load project + spec and return (catalog, canvas, project, spec) for the
    current named scene composition, or error-exit with the hint chain.
    """
    if not is_loaded():
        _no_project_error_exit(command)
    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit(command, f"Could not read project.json: {exc}")
    try:
        spec = _load_or_init_spec(project)
        spec, _identity_assignments = ensure_scene_motion_identities(spec)
    except SpecValidationError as exc:
        _error_exit(command, str(exc))

    composition = spec.get("composition")
    named_scenes = [
        scene
        for scene in (composition or [])
        if isinstance(scene, dict) and scene.get("name") and scene.get("layout")
    ]
    if not named_scenes:
        _error_exit_with_hint(
            command,
            "No scene composition to attach motion to.",
            "Motion addresses one scene and one slot. Run 'moviestar "
            "scenes set' to author scenes first, then retry.",
        )

    sources = {source["id"]: source for source in project.get("sources", [])}
    canvas = spec.get("composition_canvas") or dict(CANVAS_PRESETS["short"])
    catalog: dict[str, dict] = {}
    for scene in named_scenes:
        preset = scene["layout"]["preset"]
        slots: dict[str, dict] = {}
        for slot in scene.get("slots", []):
            from moviestar.spec import parse_timecode_string

            duration = parse_timecode_string(
                slot["source_to"], "source_to"
            ) - parse_timecode_string(slot["source_from"], "source_from")
            source = sources.get(slot.get("source"), {})
            slots[slot["slot"]] = {
                "id": slot.get("id"),
                "source": slot.get("source"),
                "duration": round(duration, 3),
                "source_width": source.get("width"),
                "source_height": source.get("height"),
            }
        catalog[scene["name"]] = {
            "id": scene.get("id"),
            "layout_preset": preset,
            "regions": layout_regions(scene["layout"], canvas, result_time_s=0.0),
            "slots": slots,
        }
    return catalog, canvas, project, spec


def _motion_timecode(
    value: object, path: str, errors: list[dict[str, str]]
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        errors.append(
            _scene_file_error(path, "must be a timecode string or number of seconds")
        )
        return None
    try:
        return parse_timecode(str(value))
    except ValueError as exc:
        errors.append(_scene_file_error(path, str(exc)))
        return None


def _motion_range(
    value: object,
    path: str,
    default_space: str,
    wrong_space_message: str,
    errors: list[dict[str, str]],
) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        errors.append(
            _scene_file_error(path, "must be an object with 'from' and 'to'")
        )
        return None
    _scene_file_unknown_keys(value, _MOTION_RANGE_KEYS, path, errors)
    from_s = _motion_timecode(value.get("from"), f"{path}.from", errors)
    to_s = _motion_timecode(value.get("to"), f"{path}.to", errors)
    space = value.get("space", default_space)
    if space != default_space:
        errors.append(_scene_file_error(f"{path}.space", wrong_space_message))
        return None
    if from_s is None or to_s is None:
        return None
    if from_s >= to_s:
        errors.append(
            _scene_file_error(
                path,
                f"'from' ({_motion_tc(from_s)}) must be before "
                f"'to' ({_motion_tc(to_s)})",
            )
        )
        return None
    return (from_s, to_s)


def _motion_id(
    value: object,
    path: str,
    seen_ids: dict[str, str],
    errors: list[dict[str, str]],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        errors.append(_scene_file_error(path, "must be a non-empty string"))
        return None
    stripped = value.strip()
    if stripped in seen_ids:
        errors.append(
            _scene_file_error(
                path,
                f"duplicate id {stripped!r}; ids must be unique across the "
                f"whole motion file (also used at {seen_ids[stripped]})",
            )
        )
        return None
    seen_ids[stripped] = path
    return stripped


def _motion_slot_bounds_error(
    kind: str,
    end_s: float,
    slot_name: str,
    slot_duration: float,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    errors.append(
        _scene_file_error(
            path,
            f"{kind} ends at {_motion_tc(end_s)} but slot "
            f"{slot_name!r} only has {_motion_tc(slot_duration)} of "
            "source-local material (source-local time starts at 0:00 at "
            "the slot's source_from)",
        )
    )


def _motion_pacing_entry(
    raw: object,
    path: str,
    slot_name: str,
    slot_duration: float,
    seen_ids: dict[str, str],
    errors: list[dict[str, str]],
    unknown_errors: list[dict[str, str]],
) -> dict | None:
    if not isinstance(raw, dict):
        errors.append(_scene_file_error(path, "must be an object"))
        return None
    _scene_file_unknown_keys(raw, _MOTION_PACING_KEYS, path, unknown_errors)
    entry_id = _motion_id(raw.get("id"), f"{path}.id", seen_ids, errors)
    mode = raw.get("mode")
    if mode not in MOTION_PACING_MODES:
        errors.append(
            _scene_file_error(
                f"{path}.mode",
                f"must be one of: {', '.join(sorted(MOTION_PACING_MODES))}",
            )
        )
        return None

    entry: dict = {"id": entry_id, "path": path, "mode": mode}
    wrong_space = (
        "pacing is source-local: it names the source material to retime. "
        "Use source-local times (or omit 'space')."
    )
    if mode == "hold":
        for key in ("range", "speed"):
            if key in raw:
                errors.append(
                    _scene_file_error(
                        f"{path}.{key}",
                        "hold selects one frame with 'at' plus a result "
                        f"'duration'; {key!r} does not apply",
                    )
                )
        at_s = _motion_timecode(raw.get("at"), f"{path}.at", errors)
        duration_s = _motion_timecode(
            raw.get("duration"), f"{path}.duration", errors
        )
        space = raw.get("space", "source-local")
        if space != "source-local":
            errors.append(_scene_file_error(f"{path}.space", wrong_space))
            return None
        if at_s is None or duration_s is None:
            return None
        if duration_s <= 0:
            errors.append(
                _scene_file_error(f"{path}.duration", "must be greater than 0")
            )
            return None
        if at_s > slot_duration + 0.001:
            _motion_slot_bounds_error(
                "hold 'at'", at_s, slot_name, slot_duration, f"{path}.at", errors
            )
            return None
        entry.update({"at": at_s, "duration": duration_s})
        return entry

    if "at" in raw:
        errors.append(
            _scene_file_error(
                f"{path}.at",
                f"{mode} retimes a 'range'; 'at' applies only to holds",
            )
        )
        return None
    range_pair = _motion_range(
        raw.get("range"), f"{path}.range", "source-local", wrong_space, errors
    )
    if mode == "speed":
        if "duration" in raw:
            errors.append(
                _scene_file_error(
                    f"{path}.duration",
                    "speed mode takes a 'speed' multiplier; use mode "
                    "'duration' to fit a range into a target duration",
                )
            )
            return None
        speed = raw.get("speed")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            errors.append(
                _scene_file_error(f"{path}.speed", "must be a number, e.g. 5.0")
            )
            return None
        if speed <= 0:
            errors.append(
                _scene_file_error(
                    f"{path}.speed",
                    "must be greater than 0 (>1 speeds up, <1 slows down)",
                )
            )
            return None
        entry["speed"] = float(speed)
    else:  # duration
        if "speed" in raw:
            errors.append(
                _scene_file_error(
                    f"{path}.speed",
                    "duration mode derives the speed from the target "
                    "'duration'; drop 'speed' or use mode 'speed'",
                )
            )
            return None
        duration_s = _motion_timecode(
            raw.get("duration"), f"{path}.duration", errors
        )
        if duration_s is None:
            return None
        if duration_s <= 0:
            errors.append(
                _scene_file_error(f"{path}.duration", "must be greater than 0")
            )
            return None
        entry["duration"] = duration_s

    if range_pair is None:
        return None
    if range_pair[1] > slot_duration + 0.001:
        _motion_slot_bounds_error(
            "range", range_pair[1], slot_name, slot_duration,
            f"{path}.range.to", errors,
        )
        return None
    entry["range"] = range_pair
    return entry


def _motion_target(
    raw: object,
    path: str,
    slot_info: dict,
    errors: list[dict[str, str]],
    unknown_errors: list[dict[str, str]],
) -> dict | None:
    if raw == "full":
        return {"target": "full"}
    if not isinstance(raw, dict):
        errors.append(
            _scene_file_error(
                path, "must be 'full' or an object with space/units/rect"
            )
        )
        return None
    _scene_file_unknown_keys(raw, _MOTION_TARGET_KEYS, path, unknown_errors)
    if raw.get("space", "source") != "source":
        errors.append(
            _scene_file_error(
                f"{path}.space",
                "camera targets are source-relative; use 'source'",
            )
        )
        return None
    units = raw.get("units")
    if units not in MOTION_TARGET_UNITS:
        errors.append(
            _scene_file_error(
                f"{path}.units",
                f"must be one of: {', '.join(sorted(MOTION_TARGET_UNITS))}",
            )
        )
        return None
    rect_raw = raw.get("rect")
    if not isinstance(rect_raw, dict):
        errors.append(
            _scene_file_error(f"{path}.rect", "must be an object with x/y/w/h")
        )
        return None
    _scene_file_unknown_keys(
        rect_raw, _MOTION_RECT_KEYS, f"{path}.rect", unknown_errors
    )
    rect: dict[str, float] = {}
    for key in ("x", "y", "w", "h"):
        value = rect_raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(
                _scene_file_error(f"{path}.rect.{key}", "must be a number")
            )
            return None
        rect[key] = float(value)
    if rect["w"] <= 0 or rect["h"] <= 0:
        errors.append(
            _scene_file_error(f"{path}.rect", "w and h must be greater than 0")
        )
        return None
    if rect["x"] < 0 or rect["y"] < 0:
        errors.append(
            _scene_file_error(f"{path}.rect", "x and y must not be negative")
        )
        return None
    if units == "normalized":
        if rect["x"] + rect["w"] > 1.000001 or rect["y"] + rect["h"] > 1.000001:
            errors.append(
                _scene_file_error(
                    f"{path}.rect",
                    "normalized rect must stay inside the source: "
                    "x + w and y + h must not exceed 1.0",
                )
            )
            return None
    else:
        width = slot_info.get("source_width")
        height = slot_info.get("source_height")
        if width and height:
            if rect["x"] + rect["w"] > width + 0.5 or (
                rect["y"] + rect["h"] > height + 0.5
            ):
                errors.append(
                    _scene_file_error(
                        f"{path}.rect",
                        f"pixel rect must stay inside the {width}x{height} "
                        f"source video for slot source "
                        f"{slot_info.get('source')!r}",
                    )
                )
                return None
    return {"target": {"space": "source", "units": units, "rect": rect}}


def _motion_camera_state(
    raw_state: object,
    state_path: str,
    slot_info: dict,
    errors: list[dict[str, str]],
    unknown_errors: list[dict[str, str]],
) -> dict | None:
    if not isinstance(raw_state, dict):
        errors.append(
            _scene_file_error(
                state_path, "must be an object like {\"target\": ...}"
            )
        )
        return None
    _scene_file_unknown_keys(
        raw_state, _MOTION_STATE_KEYS, state_path, unknown_errors
    )
    return _motion_target(
        raw_state.get("target"),
        f"{state_path}.target",
        slot_info,
        errors,
        unknown_errors,
    )


def _motion_zoom_camera_entry(
    raw: dict,
    path: str,
    entry_id: str | None,
    ease: str,
    slot_info: dict,
    errors: list[dict[str, str]],
    unknown_errors: list[dict[str, str]],
) -> dict | None:
    """Validate a kind:"zoom" camera record (incremental zoom authoring).

    'at' is the arrival moment; timing carries viewer-facing move_in /
    hold / move_out durations that survive pacing edits.
    """
    if ease == "cut":
        errors.append(
            _scene_file_error(
                f"{path}.ease",
                "zoom records animate in and out; use a plain camera "
                "record with ease 'cut' for instant switches",
            )
        )
        return None
    if "range" in raw:
        errors.append(
            _scene_file_error(
                f"{path}.range",
                "zoom records use 'at' (the moment the camera reaches "
                "its target), not 'range'",
            )
        )
        return None
    at_s = _motion_timecode(raw.get("at"), f"{path}.at", errors)
    if at_s is None:
        return None

    timing_raw = raw.get("timing", {})
    if not isinstance(timing_raw, dict):
        errors.append(
            _scene_file_error(
                f"{path}.timing",
                "must be an object with move_in/hold/move_out seconds",
            )
        )
        return None
    _scene_file_unknown_keys(
        timing_raw, _MOTION_ZOOM_TIMING_KEYS, f"{path}.timing", unknown_errors
    )
    timing: dict[str, float] = {}
    for key, default in ZOOM_DEFAULT_TIMING.items():
        seconds = _motion_timecode(
            timing_raw.get(key, default), f"{path}.timing.{key}", errors
        )
        if seconds is None:
            return None
        if seconds <= 0:
            errors.append(
                _scene_file_error(
                    f"{path}.timing.{key}", "must be greater than 0"
                )
            )
            return None
        timing[key] = seconds
    start_s = at_s - timing["move_in"]
    if start_s < -0.0005:
        errors.append(
            _scene_file_error(
                f"{path}.at",
                f"the {timing['move_in']}s move-in would start at "
                f"{_motion_tc(start_s)} — before the scene begins",
            )
        )
        return None

    if "to" not in raw:
        errors.append(
            _scene_file_error(path, "missing required 'to' camera state")
        )
        return None
    to_state = _motion_camera_state(
        raw["to"], f"{path}.to", slot_info, errors, unknown_errors
    )
    if to_state is None:
        return None
    from_state = None
    if "from" in raw:
        from_state = _motion_camera_state(
            raw["from"], f"{path}.from", slot_info, errors, unknown_errors
        )
        if from_state is None:
            return None
    return_state = None
    if "return_to" in raw:
        return_state = _motion_camera_state(
            raw["return_to"], f"{path}.return_to", slot_info, errors,
            unknown_errors,
        )
        if return_state is None:
            return None
    authored = raw.get("authored")
    if authored is not None and not isinstance(authored, dict):
        errors.append(
            _scene_file_error(f"{path}.authored", "must be an object")
        )
        return None
    return {
        "id": entry_id,
        "path": path,
        "kind": "zoom",
        "start": start_s,
        "end": at_s + timing["hold"] + timing["move_out"],
        "at": at_s,
        "timing": timing,
        "ease": ease,
        "default_duration_applied": False,
        "authored_at": False,
        "from": from_state,
        "to": to_state,
        "return_to": return_state,
        "authored": authored,
    }


def _motion_camera_entry(
    raw: object,
    path: str,
    slot_info: dict,
    seen_ids: dict[str, str],
    errors: list[dict[str, str]],
    unknown_errors: list[dict[str, str]],
) -> dict | None:
    if not isinstance(raw, dict):
        errors.append(_scene_file_error(path, "must be an object"))
        return None
    _scene_file_unknown_keys(raw, _MOTION_CAMERA_KEYS, path, unknown_errors)
    entry_id = _motion_id(raw.get("id"), f"{path}.id", seen_ids, errors)

    ease = raw.get("ease", "in-out")
    if ease not in MOTION_EASES:
        errors.append(
            _scene_file_error(
                f"{path}.ease",
                f"must be one of: {', '.join(sorted(MOTION_EASES))}",
            )
        )
        return None

    kind = raw.get("kind")
    if kind is not None and kind != "zoom":
        errors.append(
            _scene_file_error(
                f"{path}.kind",
                "must be 'zoom' (the incremental camera record) or "
                "omitted for a plain move",
            )
        )
        return None
    if kind == "zoom":
        return _motion_zoom_camera_entry(
            raw, path, entry_id, ease, slot_info, errors, unknown_errors
        )
    for key in ("timing", "return_to", "authored"):
        if key in raw:
            errors.append(
                _scene_file_error(
                    f"{path}.{key}",
                    f"{key!r} applies to kind 'zoom' records only",
                )
            )
            return None

    has_range = "range" in raw
    has_at = "at" in raw
    if has_range and has_at:
        errors.append(
            _scene_file_error(
                path, "use either 'range' or 'at' for a camera move, not both"
            )
        )
        return None
    if not has_range and not has_at:
        errors.append(
            _scene_file_error(
                path,
                "a camera move needs 'range' (result-local from/to) or 'at' "
                f"(start time; runs {MOTION_DEFAULT_MOVE_SECONDS}s by default)",
            )
        )
        return None

    default_duration_applied = False
    if has_range:
        wrong_space = (
            "camera moves are result-local: they describe what the viewer "
            "sees after pacing. Use result-local times (or omit 'space')."
        )
        range_pair = _motion_range(
            raw.get("range"), f"{path}.range", "result-local", wrong_space, errors
        )
        if range_pair is None:
            return None
        start_s, end_s = range_pair
    else:
        at_s = _motion_timecode(raw.get("at"), f"{path}.at", errors)
        if at_s is None:
            return None
        start_s = at_s
        end_s = at_s if ease == "cut" else at_s + MOTION_DEFAULT_MOVE_SECONDS
        default_duration_applied = ease != "cut"

    if "to" not in raw:
        errors.append(
            _scene_file_error(path, "missing required 'to' camera state")
        )
        return None

    to_state = _motion_camera_state(
        raw["to"], f"{path}.to", slot_info, errors, unknown_errors
    )
    from_state = None
    if "from" in raw:
        from_state = _motion_camera_state(
            raw["from"], f"{path}.from", slot_info, errors, unknown_errors
        )
        if from_state is None:
            return None
    if to_state is None:
        return None
    return {
        "id": entry_id,
        "path": path,
        "start": start_s,
        "end": end_s,
        "ease": ease,
        "default_duration_applied": default_duration_applied,
        "authored_at": has_at,
        "from": from_state,
        "to": to_state,
    }


def _motion_overlap_errors(
    scenes: list[dict], errors: list[dict[str, str]]
) -> None:
    for scene in scenes:
        for slot in scene["slots"]:
            ranged = sorted(
                (entry for entry in slot["pacing"] if entry.get("range")),
                key=lambda entry: entry["range"][0],
            )
            for prev, entry in zip(ranged, ranged[1:]):
                if entry["range"][0] < prev["range"][1] - 1e-9:
                    overlap_from = entry["range"][0]
                    overlap_to = min(prev["range"][1], entry["range"][1])
                    errors.append(
                        _scene_file_error(
                            entry["path"],
                            f"pacing ranges {prev['id']!r} "
                            f"({_motion_tc(prev['range'][0])}-"
                            f"{_motion_tc(prev['range'][1])}) and "
                            f"{entry['id']!r} "
                            f"({_motion_tc(entry['range'][0])}-"
                            f"{_motion_tc(entry['range'][1])}) overlap; "
                            f"source-local {_motion_tc(overlap_from)}-"
                            f"{_motion_tc(overlap_to)} needs one chosen "
                            "speed",
                        )
                    )
            holds = [
                entry for entry in slot["pacing"] if entry.get("mode") == "hold"
            ]
            for hold in holds:
                for ranged_entry in ranged:
                    start_s, end_s = ranged_entry["range"]
                    if start_s < hold["at"] < end_s:
                        errors.append(
                            _scene_file_error(
                                hold["path"],
                                f"hold {hold['id']!r} falls inside pacing range "
                                f"{ranged_entry['id']!r}. Smallest fix: move the "
                                "hold to a range boundary or split the range.",
                            )
                        )
            moves = sorted(slot["camera"], key=lambda entry: entry["start"])
            for prev, entry in zip(moves, moves[1:]):
                if entry["start"] < prev["end"] - 1e-9:
                    errors.append(
                        _scene_file_error(
                            entry["path"],
                            f"camera moves {prev['id']!r} "
                            f"({_motion_tc(prev['start'])}-"
                            f"{_motion_tc(prev['end'])}) and "
                            f"{entry['id']!r} "
                            f"({_motion_tc(entry['start'])}-"
                            f"{_motion_tc(entry['end'])}) overlap. "
                            f"Smallest fix: start {entry['id']!r} at "
                            f"{_motion_tc(prev['end'])} or later",
                        )
                    )


def _load_motion_plan(
    path: str, catalog: dict
) -> tuple[list[dict], list[str]]:
    """Parse and validate motion JSON against the current scene catalog.

    Returns (normalized scenes, assigned_ids). Raises
    ``_SceneFileSchemaError`` with path-addressed errors on any failure.
    """
    abs_path = os.path.realpath(path)
    if not os.path.isfile(abs_path):
        raise _SceneFileSchemaError(
            [_scene_file_error("$", f"motion file not found: {abs_path}")]
        )
    try:
        data = json.loads(Path(abs_path).read_text())
    except json.JSONDecodeError as exc:
        raise _SceneFileSchemaError(
            [
                _scene_file_error(
                    "$",
                    f"invalid JSON at line {exc.lineno}, column {exc.colno}: "
                    f"{exc.msg}",
                )
            ]
        ) from exc
    except OSError as exc:
        raise _SceneFileSchemaError(
            [_scene_file_error("$", f"could not read motion file: {exc}")]
        ) from exc
    if not isinstance(data, dict):
        raise _SceneFileSchemaError(
            [_scene_file_error("$", "must be a JSON object")]
        )

    errors: list[dict[str, str]] = []
    unknown_errors: list[dict[str, str]] = []
    seen_ids: dict[str, str] = {}
    _scene_file_unknown_keys(data, _MOTION_FILE_KEYS, "", unknown_errors)
    if data.get("version") != 1:
        errors.append(
            _scene_file_error(
                "version", "must be 1 (the current motion schema version)"
            )
        )

    raw_scenes = data.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        errors.append(_scene_file_error("scenes", "must be a non-empty array"))
        raw_scenes = []

    scenes: list[dict] = []
    seen_scene_names: dict[str, str] = {}
    for scene_index, raw_scene in enumerate(raw_scenes):
        scene_path = f"scenes[{scene_index}]"
        if not isinstance(raw_scene, dict):
            errors.append(_scene_file_error(scene_path, "must be an object"))
            continue
        _scene_file_unknown_keys(
            raw_scene, _MOTION_SCENE_KEYS, scene_path, unknown_errors
        )
        scene_name = _scene_file_string(
            raw_scene.get("scene"), f"{scene_path}.scene", errors
        )
        if scene_name is None:
            continue
        if scene_name not in catalog:
            errors.append(
                _scene_file_error(
                    f"{scene_path}.scene",
                    f"unknown scene {scene_name!r}. This composition has: "
                    f"{', '.join(catalog)}",
                )
            )
            continue
        if scene_name in seen_scene_names:
            errors.append(
                _scene_file_error(
                    f"{scene_path}.scene",
                    f"duplicate entry for scene {scene_name!r} (also at "
                    f"{seen_scene_names[scene_name]})",
                )
            )
            continue
        seen_scene_names[scene_name] = f"{scene_path}.scene"
        scene_info = catalog[scene_name]

        raw_slots = raw_scene.get("slots")
        if not isinstance(raw_slots, list) or not raw_slots:
            errors.append(
                _scene_file_error(f"{scene_path}.slots", "must be a non-empty array")
            )
            continue
        slots: list[dict] = []
        seen_slot_names: dict[str, str] = {}
        for slot_index, raw_slot in enumerate(raw_slots):
            slot_path = f"{scene_path}.slots[{slot_index}]"
            if not isinstance(raw_slot, dict):
                errors.append(_scene_file_error(slot_path, "must be an object"))
                continue
            _scene_file_unknown_keys(
                raw_slot, _MOTION_SLOT_KEYS, slot_path, unknown_errors
            )
            slot_name = _scene_file_string(
                raw_slot.get("slot"), f"{slot_path}.slot", errors
            )
            if slot_name is None:
                continue
            if slot_name not in scene_info["slots"]:
                errors.append(
                    _scene_file_error(
                        f"{slot_path}.slot",
                        f"unknown slot {slot_name!r} in scene "
                        f"{scene_name!r}. Its {scene_info['layout_preset']} "
                        "layout has slots: "
                        f"{', '.join(scene_info['slots'])}",
                    )
                )
                continue
            if slot_name in seen_slot_names:
                errors.append(
                    _scene_file_error(
                        f"{slot_path}.slot",
                        f"duplicate entry for slot {slot_name!r} (also at "
                        f"{seen_slot_names[slot_name]})",
                    )
                )
                continue
            seen_slot_names[slot_name] = f"{slot_path}.slot"
            slot_info = scene_info["slots"][slot_name]

            pacing_entries: list[dict] = []
            raw_pacing = raw_slot.get("pacing", [])
            if not isinstance(raw_pacing, list):
                errors.append(
                    _scene_file_error(f"{slot_path}.pacing", "must be an array")
                )
                raw_pacing = []
            for entry_index, raw_entry in enumerate(raw_pacing):
                entry = _motion_pacing_entry(
                    raw_entry,
                    f"{slot_path}.pacing[{entry_index}]",
                    slot_name,
                    slot_info["duration"],
                    seen_ids,
                    errors,
                    unknown_errors,
                )
                if entry is not None:
                    pacing_entries.append(entry)

            camera_entries: list[dict] = []
            raw_camera = raw_slot.get("camera", [])
            if not isinstance(raw_camera, list):
                errors.append(
                    _scene_file_error(f"{slot_path}.camera", "must be an array")
                )
                raw_camera = []
            for entry_index, raw_entry in enumerate(raw_camera):
                entry = _motion_camera_entry(
                    raw_entry,
                    f"{slot_path}.camera[{entry_index}]",
                    slot_info,
                    seen_ids,
                    errors,
                    unknown_errors,
                )
                if entry is not None:
                    camera_entries.append(entry)

            slots.append(
                {
                    "slot": slot_name,
                    "slot_id": slot_info.get("id"),
                    "pacing": pacing_entries,
                    "camera": camera_entries,
                }
            )
        scenes.append(
            {
                "scene": scene_name,
                "scene_id": scene_info.get("id"),
                "slots": slots,
            }
        )

    _motion_overlap_errors(scenes, errors)
    if errors or unknown_errors:
        raise _SceneFileSchemaError(unknown_errors + errors)

    assigned_ids: list[str] = []
    counters = {"pace": 0, "cam": 0}

    def _next_free(prefix: str) -> str:
        while True:
            counters[prefix] += 1
            candidate = f"{prefix}_{counters[prefix]:04d}"
            if candidate not in seen_ids:
                seen_ids[candidate] = "(assigned)"
                return candidate

    for scene in scenes:
        for slot in scene["slots"]:
            for entry in slot["pacing"]:
                if entry["id"] is None:
                    entry["id"] = _next_free("pace")
                    assigned_ids.append(entry["id"])
            for entry in slot["camera"]:
                if entry["id"] is None:
                    entry["id"] = _next_free("cam")
                    assigned_ids.append(entry["id"])
    return scenes, assigned_ids


def _motion_range_echo(from_s: float, to_s: float, space: str) -> dict:
    return {
        "from": _motion_tc(from_s),
        "to": _motion_tc(to_s),
        "space": space,
    }


def _motion_pacing_echo(entry: dict) -> dict:
    echo: dict = {"id": entry["id"], "mode": entry["mode"]}
    if entry["mode"] == "hold":
        echo["at"] = _motion_tc(entry["at"])
        echo["space"] = "source-local"
        echo["duration"] = _motion_tc(entry["duration"])
        return echo
    echo["range"] = _motion_range_echo(*entry["range"], "source-local")
    if entry["mode"] == "speed":
        echo["speed"] = entry["speed"]
    else:
        echo["duration"] = _motion_tc(entry["duration"])
    return echo


def _motion_camera_echo(entry: dict) -> dict:
    if entry.get("kind") == "zoom":
        echo = {
            "id": entry["id"],
            "kind": "zoom",
            "at": _motion_tc(entry["at"]),
            "timing": {
                key: round(value, 3)
                for key, value in entry["timing"].items()
            },
        }
        if entry["from"] is not None:
            echo["from"] = entry["from"]
        echo["to"] = entry["to"]
        if entry.get("return_to") is not None:
            echo["return_to"] = entry["return_to"]
        if entry.get("authored") is not None:
            echo["authored"] = entry["authored"]
        echo["ease"] = entry["ease"]
        return echo
    echo = {"id": entry["id"]}
    if entry["authored_at"]:
        echo["at"] = _motion_tc(entry["start"])
    else:
        echo["range"] = _motion_range_echo(
            entry["start"], entry["end"], "result-local"
        )
    if entry["from"] is not None:
        echo["from"] = entry["from"]
    echo["to"] = entry["to"]
    echo["ease"] = entry["ease"]
    return echo


def _motion_document_from_normalized(scenes: list[dict]) -> dict:
    return {
        "version": 1,
        "scenes": [
            {
                "scene": scene["scene"],
                **(
                    {"scene_id": scene["scene_id"]}
                    if scene.get("scene_id") else {}
                ),
                "slots": [
                    {
                        "slot": slot["slot"],
                        **(
                            {"slot_id": slot["slot_id"]}
                            if slot.get("slot_id") else {}
                        ),
                        "pacing": [
                            _motion_pacing_echo(entry)
                            for entry in slot["pacing"]
                        ],
                        "camera": [
                            _motion_camera_echo(entry)
                            for entry in slot["camera"]
                        ],
                    }
                    for slot in scene["slots"]
                ],
            }
            for scene in scenes
        ],
    }


def _apply_rebased_camera_times(scenes: list[dict], motion: dict) -> None:
    timing_by_id = {
        camera["id"]: camera
        for scene in motion.get("scenes", [])
        for slot in scene.get("slots", [])
        for camera in slot.get("camera", [])
    }
    for scene in scenes:
        for slot in scene["slots"]:
            for camera in slot["camera"]:
                canonical = timing_by_id[camera["id"]]
                if camera.get("kind") == "zoom":
                    at_s = parse_timecode(str(canonical["at"]))
                    camera["at"] = at_s
                    camera["start"] = at_s - camera["timing"]["move_in"]
                    camera["end"] = (
                        at_s
                        + camera["timing"]["hold"]
                        + camera["timing"]["move_out"]
                    )
                    continue
                if "range" in canonical:
                    camera["start"] = parse_timecode(canonical["range"]["from"])
                    camera["end"] = parse_timecode(canonical["range"]["to"])
                    camera["authored_at"] = False
                else:
                    camera["start"] = parse_timecode(canonical["at"])
                    camera["end"] = (
                        camera["start"]
                        if canonical.get("ease") == "cut"
                        else camera["start"] + MOTION_DEFAULT_MOVE_SECONDS
                    )
                    camera["authored_at"] = True


def _motion_timeline_echo(timeline) -> list[dict]:
    scenes: list[dict] = []
    for scene in timeline.scenes:
        slots = [slot["slot"] for slot in scene.scene["slots"]]
        scenes.append(
            {
                "scene": scene.name,
                "range": _motion_range_echo(
                    scene.start_s, scene.end_s, "result"
                ),
                "duration": format_timecode(scene.duration_s),
                "segments": [
                    {
                        "id": segment.id,
                        "range": _motion_range_echo(
                            segment.start_s, segment.end_s, "result"
                        ),
                        "source_local_range": _motion_range_echo(
                            segment.source_local_from_s,
                            segment.source_local_to_s,
                            "source-local",
                        ),
                        "duration": format_timecode(segment.duration_s),
                        "held": segment.held,
                        "slots": [
                            {
                                "slot": slot.slot_name,
                                "source": slot.source_id,
                                "source_range": _motion_range_echo(
                                    slot.source_from_s,
                                    slot.source_to_s,
                                    "source",
                                ),
                                "mode": slot.mode,
                                "values": slot.values,
                                "pacing_id": slot.pacing_id,
                                "effective_speed": slot.speed,
                                "contributes_result": format_timecode(
                                    segment.duration_s
                                ),
                                "held": slot.held,
                            }
                            for slot in segment.slots
                        ],
                    }
                    for segment in scene.segments
                ],
                "time_maps": {
                    slot_name: [
                        {
                            "source_local": format_timecode(point.source_local_s),
                            "result_local": format_timecode(point.result_local_s),
                            "held": point.held,
                        }
                        for point in scene.time_map(slot_name)
                    ]
                    for slot_name in slots
                },
            }
        )
    return scenes


def _camera_echo_is_idle(echo: dict) -> bool:
    return echo.get("target") == "full" and echo.get("active_move_id") is None


def _camera_state_echo(state) -> dict:
    return {
        "target": state.target,
        "authored_units": state.authored_units,
        "rect_pixels": state.rect_pixels,
        "rect_normalized": state.rect_normalized,
        "resolved_crop_pixels": state.resolved_crop_pixels,
        "derived_zoom": state.zoom,
        "adjustments": list(state.adjustments),
        "active_move_id": state.active_move_id,
        "progress": state.progress,
        "eased_progress": state.eased_progress,
    }


def _motion_resolved_state(state: dict | None, slot_info: dict) -> dict | None:
    """Expand a camera state's target rect into both coordinate forms."""
    if state is None:
        return None
    target = state["target"]
    if target == "full":
        return {"target": "full"}
    rect = target["rect"]
    width = slot_info.get("source_width")
    height = slot_info.get("source_height")
    resolved: dict = {
        "space": "source",
        "units": target["units"],
        "rect": rect,
    }
    if width and height:
        if target["units"] == "pixels":
            normalized = {
                "x": round(rect["x"] / width, 4),
                "y": round(rect["y"] / height, 4),
                "w": round(rect["w"] / width, 4),
                "h": round(rect["h"] / height, 4),
            }
            pixels = rect
        else:
            normalized = rect
            pixels = {
                "x": round(rect["x"] * width),
                "y": round(rect["y"] * height),
                "w": round(rect["w"] * width),
                "h": round(rect["h"] * height),
            }
        resolved["rect_normalized"] = normalized
        resolved["rect_pixels"] = pixels
    return {"target": resolved}


def _motion_preview(
    scenes: list[dict], catalog: dict, timeline, camera_plan
) -> tuple[list[dict], dict, list[dict]]:
    """Build the (echo, resolved_preview, warnings) triple for set."""
    echo: list[dict] = []
    pacing_effects: list[dict] = []
    camera_states: list[dict] = []
    warnings: list[dict] = []
    resolved_camera_by_id = {move.id: move for move in camera_plan.moves}

    for scene in scenes:
        scene_name = scene["scene"]
        scene_info = catalog[scene_name]
        scene_echo: dict = {"scene": scene_name, "slots": []}
        if scene.get("scene_id"):
            scene_echo["scene_id"] = scene["scene_id"]
        explicit_pacing_slots: set[str] = set()

        for slot in scene["slots"]:
            slot_name = slot["slot"]
            slot_info = scene_info["slots"][slot_name]
            region = scene_info["regions"][slot_name]
            slot_echo: dict = {
                "slot": slot_name,
                "pacing": [_motion_pacing_echo(e) for e in slot["pacing"]],
                "camera": [],
            }
            if slot.get("slot_id"):
                slot_echo["slot_id"] = slot["slot_id"]

            for entry in slot["pacing"]:
                explicit_pacing_slots.add(slot_name)
                effect: dict = {
                    "id": entry["id"],
                    "scene": scene_name,
                    "slot": slot_name,
                    "mode": entry["mode"],
                    "values": "authored",
                    "held_frame_at_source_local": None,
                }
                if entry["mode"] == "hold":
                    effect["consumes_source"] = format_timecode(0)
                    effect["contributes_result"] = format_timecode(
                        entry["duration"]
                    )
                    effect["effective_speed"] = None
                    effect["held_frame_at_source_local"] = _motion_tc(entry["at"])
                else:
                    consumed = entry["range"][1] - entry["range"][0]
                    if entry["mode"] == "speed":
                        speed = entry["speed"]
                        contributed = consumed / speed
                    else:
                        contributed = entry["duration"]
                        speed = consumed / contributed
                    effect["consumes_source"] = format_timecode(consumed)
                    effect["contributes_result"] = format_timecode(contributed)
                    effect["effective_speed"] = round(speed, 4)
                pacing_effects.append(effect)

            last_state: dict = {"target": "full"}
            for entry in sorted(slot["camera"], key=lambda e: e["start"]):
                resolved_to = _motion_resolved_state(entry["to"], slot_info)
                resolved_from = _motion_resolved_state(entry["from"], slot_info)
                from_state_source = "authored"
                if resolved_from is None:
                    resolved_from = last_state
                    from_state_source = "previous_state"
                state_entry = {
                    "id": entry["id"],
                    "scene": scene_name,
                    "slot": slot_name,
                    "range": _motion_range_echo(
                        entry["start"], entry["end"], "result-local"
                    ),
                    "ease": entry["ease"],
                    "default_duration_applied": entry["default_duration_applied"],
                    "from_state_source": from_state_source,
                    "from_state": resolved_from,
                    "to_state": resolved_to,
                }
                if entry.get("kind") == "zoom":
                    state_entry["kind"] = "zoom"
                    state_entry["at"] = _motion_tc(entry["at"])
                    state_entry["timing"] = {
                        key: round(value, 3)
                        for key, value in entry["timing"].items()
                    }
                resolved_move = resolved_camera_by_id[entry["id"]]
                state_entry.update(
                    {
                        "global_result_range": _motion_range_echo(
                            resolved_move.result_from_s,
                            resolved_move.result_to_s,
                            "result",
                        ),
                        "source_equivalent_range": _motion_range_echo(
                            resolved_move.source_equivalent_range[0],
                            resolved_move.source_equivalent_range[1],
                            "source-local",
                        ),
                        "resolved_from_state": _camera_state_echo(
                            resolved_move.from_state
                        ),
                        "resolved_to_state": _camera_state_echo(
                            resolved_move.to_state
                        ),
                    }
                )
                target = resolved_to["target"]
                if target != "full" and "rect_pixels" in target:
                    rect_px = target["rect_pixels"]
                    factor = max(
                        region["width"] / rect_px["w"],
                        region["height"] / rect_px["h"],
                    )
                    if factor > 1.001:
                        factor = round(factor, 2)
                        warnings.append(
                            _warning(
                                "upscaled_target",
                                f"camera {entry['id']!r} renders a "
                                f"{rect_px['w']}x{rect_px['h']}px source rect "
                                f"into the {region['width']}x"
                                f"{region['height']}px {slot_name!r} slot "
                                f"region (~{factor}x upscale); expect visible "
                                "softness. Intentional zooms are never "
                                "limited.",
                                camera_id=entry["id"],
                                source_rect_pixels=(
                                    f"{rect_px['w']}x{rect_px['h']}"
                                ),
                                rendered_size=(
                                    f"{region['width']}x{region['height']}"
                                ),
                                upscale_factor=factor,
                            )
                        )
                    rect_aspect = rect_px["w"] / rect_px["h"]
                    region_aspect = region["width"] / region["height"]
                    if abs(rect_aspect - region_aspect) / region_aspect > 0.02:
                        state_entry["aspect_note"] = (
                            f"target aspect {round(rect_aspect, 3)} differs "
                            "from the slot region aspect "
                            f"{round(region_aspect, 3)}; the real resolver "
                            "expands or shifts the crop to match and reports "
                            "the exact adjustment"
                        )
                camera_states.append(state_entry)
                slot_echo["camera"].append(_motion_camera_echo(entry))
                if entry.get("kind") == "zoom":
                    last_state = _motion_resolved_state(
                        entry.get("return_to") or {"target": "full"},
                        slot_info,
                    )
                else:
                    last_state = resolved_to
            scene_echo["slots"].append(slot_echo)

        if explicit_pacing_slots:
            for peer_name in scene_info["slots"]:
                if peer_name in explicit_pacing_slots:
                    continue
                pacing_effects.append(
                    {
                        "id": None,
                        "scene": scene_name,
                        "slot": peer_name,
                        "mode": None,
                        "values": "calculated",
                        "note": (
                            "unbound: MovieStar calculated matching pacing "
                            "for each resolved segment so this slot keeps "
                            "the same scene duration; see resolved_timeline "
                            "for the numeric speeds and hold durations"
                        ),
                    }
                )
        echo.append(scene_echo)

    resolved_preview = {
        "composition_duration": format_timecode(timeline.duration_s),
        "resolved_timeline": _motion_timeline_echo(timeline),
        "camera_render_status": "all_verification_surfaces_active",
        "pacing_effects": pacing_effects,
        "camera_states": camera_states,
        "audio_note": (
            "The render retimes the scene's selected audio source with its "
            "resolved slot time map (pitch preserved) and inserts silence "
            "during holds."
        ),
    }
    return echo, resolved_preview, warnings


def _motion_coverage_warning(
    scenes: list[dict], catalog: dict
) -> dict | None:
    """Describe current scene/slot identities omitted by a valid plan."""
    submitted = {scene["scene"]: scene for scene in scenes}
    missing_scenes: list[dict] = []
    missing_slots: list[dict] = []

    for scene_name, scene_info in catalog.items():
        submitted_scene = submitted.get(scene_name)
        scene_missing = submitted_scene is None
        if scene_missing:
            missing_scenes.append(
                {
                    "scene": scene_name,
                    "scene_id": scene_info.get("id"),
                    "motion_behavior": {
                        "pacing": "default_1x",
                        "camera": "none",
                    },
                }
            )
        submitted_slots = {
            slot["slot"] for slot in (submitted_scene or {}).get("slots", [])
        }
        for slot_name, slot_info in scene_info["slots"].items():
            if slot_name in submitted_slots:
                continue
            missing_slots.append(
                {
                    "scene": scene_name,
                    "scene_id": scene_info.get("id"),
                    "slot": slot_name,
                    "slot_id": slot_info.get("id"),
                    "motion_behavior": {
                        "pacing": (
                            "default_1x"
                            if scene_missing
                            else "unbound_calculated_or_default_1x"
                        ),
                        "camera": "none",
                    },
                }
            )

    if not missing_scenes and not missing_slots:
        return None
    return _warning(
        "motion_plan_partial_coverage",
        f"Motion plan omits {len(missing_scenes)} current scene(s) and "
        f"{len(missing_slots)} current slot(s). Omitted scenes use default "
        "1x pacing and no camera motion. Omitted slots in included paced "
        "scenes are unbound and may receive calculated pacing to stay "
        "synchronized; otherwise they use default 1x, and they have no "
        "camera motion.",
        missing_scenes=missing_scenes,
        missing_slots=missing_slots,
    )


def _matching_stored_motion_record(
    current: dict,
    stored: list[dict],
    *,
    id_key: str,
    name_key: str,
) -> dict | None:
    """Match by stable identity, with names reserved for legacy records."""
    current_id = current.get(id_key)
    if current_id:
        for record in stored:
            if record.get(id_key) == current_id:
                return record
        for record in stored:
            legacy_name_matches = (
                not record.get(id_key)
                and record.get(name_key) == current.get(name_key)
            )
            if legacy_name_matches:
                return record
        return None
    return next(
        (
            record
            for record in stored
            if record.get(name_key) == current.get(name_key)
        ),
        None,
    )


def _materialize_motion_dump(skeleton: dict, stored_motion: dict) -> dict:
    """Overlay authored motion on the complete current-composition shape."""
    dumped = copy.deepcopy(skeleton)
    stored_scenes = stored_motion.get("scenes", [])
    for scene in dumped["scenes"]:
        stored_scene = _matching_stored_motion_record(
            scene,
            stored_scenes,
            id_key="scene_id",
            name_key="scene",
        )
        if stored_scene is None:
            continue
        stored_slots = stored_scene.get("slots", [])
        for slot in scene["slots"]:
            stored_slot = _matching_stored_motion_record(
                slot,
                stored_slots,
                id_key="slot_id",
                name_key="slot",
            )
            if stored_slot is None:
                continue
            slot["pacing"] = copy.deepcopy(stored_slot.get("pacing", []))
            slot["camera"] = copy.deepcopy(stored_slot.get("camera", []))
    return dumped


@scenes.group("motion", invoke_without_command=True)
@click.pass_context
def scenes_motion(ctx: click.Context) -> None:
    """Zoom, pan, and retime scene slots (motion + pacing).

    \b
    Motion covers four slot-level operations:
      - camera moves: zoom into a source region, pan between
        regions, return to full-frame
      - speed changes: play a source range faster or slower (5x, 0.5x)
      - target durations: fit a source range into an exact result
        duration ("make this 20s wait take 4s")
      - holds: freeze one frame for a chosen duration

    \b
    Motion is slot-relative: camera targets use the slot's source-video
    coordinates (normalized 0..1 or pixels), so a zoom keeps pointing
    at the same UI region when the scene layout changes. Pacing ranges
    are source-local time measured from the slot's source_from. Camera
    ranges are result-local time measured from the authored scene's
    start after pacing.

    \b
    Output-canvas slot motion is separate:
      scenes geometry SCENE:SLOT --motion bounce
    This moves the composed slot itself rather than its camera.
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(
        json.dumps(
            {
                "status": "motion_authoring",
                "writes_spec": False,
                "motion_model": "scene_slot_motion",
                "operations": [
                    "camera moves: zoom / pan / return to full-frame",
                    "speed: play a source range faster or slower (5x, 0.5x)",
                    "duration: fit a source range into a target result "
                    "duration",
                    "hold: freeze one frame for a chosen duration",
                ],
                "commands": [
                    {
                        "command": (
                            "moviestar scenes motion target --at 0:42 "
                            "--slot main"
                        ),
                        "purpose": (
                            "save the exact source frame to mark up for a "
                            "zoom (clean + coordinate-grid copies)"
                        ),
                    },
                    {
                        "command": "moviestar scenes motion dump --out motion.json",
                        "purpose": (
                            "write an editable skeleton for the current "
                            "scene composition"
                        ),
                    },
                    {
                        "command": "moviestar scenes motion set motion.json --dry-run",
                        "purpose": "validate and preview the motion plan",
                    },
                    {
                        "command": "moviestar scenes motion set motion.json",
                        "purpose": "replace the stored motion plan",
                    },
                ],
                "time_defaults": MOTION_TIME_DEFAULTS,
                "hint": (
                    "Start with 'moviestar scenes motion dump' for an "
                    "editable skeleton, then 'moviestar scenes motion set "
                    "--help' for the JSON schema and examples. For output-"
                    "canvas slot movement such as bounce, use 'moviestar "
                    "scenes geometry SCENE:SLOT --help'."
                ),
            },
            indent=2,
        )
    )


def _motion_target_context(
    command: str, timecode: str, slot_name: str | None
) -> dict:
    """Resolve --at/--slot to the scene, slot, source, and times they name.

    Shared by 'scenes motion target' and the incremental camera commands
    so every surface agrees on which frame a result timecode means.
    """
    try:
        at_s = parse_timecode(timecode)
    except ValueError as exc:
        _error_exit(command, str(exc))

    catalog, canvas, project, spec = _motion_scene_catalog(command)
    try:
        timeline = resolve_pacing(spec["composition"], spec.get("motion"))
    except PacingResolutionError as exc:
        _error_exit(command, f"Could not resolve scene pacing: {exc}")

    composition_duration = timeline.duration_s
    if at_s < 0 or at_s > composition_duration:
        _error_exit(
            command,
            f"Timecode {_motion_tc(at_s)} exceeds scene composition duration "
            f"of {_motion_tc(composition_duration)}",
        )
    lookup_at_s = at_s
    if lookup_at_s >= composition_duration:
        lookup_at_s = max(0.0, round(composition_duration - 0.001, 3))

    segment = timeline.segment_at(lookup_at_s)
    resolved_scene = timeline.scenes[segment.scene_index]
    scene_name = resolved_scene.name
    scene_info = catalog[scene_name]

    slot_names = list(scene_info["slots"])
    if slot_name is None:
        if len(slot_names) == 1:
            slot_name = slot_names[0]
        else:
            _error_exit_with_hint(
                command,
                f"Scene {scene_name!r} has {len(slot_names)} slots; pick one "
                "with --slot.",
                f"A camera move changes one scene slot only. Re-run with "
                f"--slot {slot_names[0]} (slots here: "
                f"{', '.join(slot_names)}).",
            )
    elif slot_name not in scene_info["slots"]:
        mistaken_bare_name = slot_name.rsplit(":", 1)[-1]
        suggested_slot = (
            mistaken_bare_name
            if ":" in slot_name and mistaken_bare_name in slot_names
            else slot_names[0]
        )
        _error_exit_with_hint(
            command,
            f"Unknown slot {slot_name!r} in scene {scene_name!r}.",
            f"Use --slot {suggested_slot} (a bare slot name; --at already "
            f"selects scene {scene_name!r}). Its "
            f"{scene_info['layout_preset']} layout has slots: "
            f"{', '.join(slot_names)}.",
        )
    slot_info = scene_info["slots"][slot_name]

    source_id = slot_info.get("source")
    source = next(
        (s for s in project.get("sources", []) if s["id"] == source_id), None
    )
    if source is None:
        _error_exit(
            command,
            f"Scene slot references unknown source {source_id!r}.",
        )
    width = source.get("width")
    height = source.get("height")
    if not width or not height:
        _error_exit(
            command,
            f"Source {source_id!r} has no probed video dimensions; camera "
            "targeting needs a video source.",
        )

    source_s = timeline.source_time_at(lookup_at_s, slot_name)
    authored_scene = next(
        scene
        for scene in spec["composition"]
        if scene.get("name") == scene_name
    )
    authored_slot = next(
        slot
        for slot in authored_scene["slots"]
        if slot["slot"] == slot_name
    )
    framing = authored_slot.get("framing", {"mode": "fill", "anchor": "center"})

    try:
        camera_plan = resolve_camera(
            spec["composition"],
            spec.get("motion") or {"version": 1, "scenes": []},
            timeline,
            {
                s["id"]: (int(s["width"]), int(s["height"]))
                for s in project["sources"]
                if s.get("width") and s.get("height")
            },
            canvas,
        )
    except CameraResolutionError as exc:
        _error_exit(command, f"Could not resolve scene camera: {exc}")

    return {
        "catalog": catalog,
        "canvas": canvas,
        "project": project,
        "spec": spec,
        "timeline": timeline,
        "camera_plan": camera_plan,
        "resolved_scene": resolved_scene,
        "scene_name": scene_name,
        "scene_info": scene_info,
        "slot_name": slot_name,
        "slot_info": slot_info,
        "source": source,
        "framing": framing,
        "at_s": at_s,
        "lookup_at_s": lookup_at_s,
        "scene_local_s": round(lookup_at_s - resolved_scene.start_s, 6),
        "source_s": source_s,
    }


@scenes_motion.command("target")
@project_workspace_option
@click.option(
    "--at",
    "timecode",
    required=True,
    help="Result timecode of the frame to mark up (HH:MM:SS.mmm, seconds, "
    "or M:SS).",
)
@click.option(
    "--slot",
    "slot_name",
    default=None,
    help="Bare slot name to target, e.g. inset rather than demo:inset "
    "(defaults to the scene's only slot; --at selects the scene).",
)
@click.option(
    "--out",
    "output",
    default=None,
    help="Clean frame output path; the grid copy lands beside it with a "
    "_grid suffix. Auto-generated if omitted (suffixes _2, _3, ... on "
    "repeat invocations).",
)
@click.option(
    "--no-grid",
    "no_grid",
    is_flag=True,
    help="Skip the coordinate-grid copy.",
)
@click.option(
    "--inline",
    "inline",
    is_flag=True,
    help="Embed base64 image bytes for the clean frame under image: "
    "{format, base64}.",
)
def scenes_motion_target(
    timecode: str,
    slot_name: str | None,
    output: str | None,
    no_grid: bool,
    inline: bool,
) -> None:
    """Save the exact source frame to mark up for a zoom.

    \b
    Writes the original frame from one scene slot's source — not the
    final canvas with PIP, captions, or an existing crop applied — so
    you can identify a point or draw a box in the image's own pixel
    coordinates. Two copies:
      - a clean frame (out)
      - a copy with a labeled coordinate grid every 10% (grid_out)

    \b
    --at is result time (what a viewer sees); the response also maps it
    to the scene-local and source timecodes. visible_source_region shows
    the part of this source the slot currently displays at --at, after
    any existing camera moves and fill/fit framing.

    Nothing is written to spec.json. Next step: 'moviestar scenes motion
    camera preview' with --box or --point using this image's coordinates.
    """
    command = "scenes motion target"
    context = _motion_target_context(command, timecode, slot_name)
    source = context["source"]
    slot_name = context["slot_name"]
    scene_name = context["scene_name"]
    width = int(source["width"])
    height = int(source["height"])
    fps = source.get("fps")

    if output is None:
        base = (
            f"target_{scene_name}_{slot_name}_{context['at_s']:.3f}s.jpg"
        )
        output = _resolve_unique_output_path(base)
    abs_output = os.path.realpath(output)
    _validate_output_parent(command, abs_output)
    root, ext = os.path.splitext(abs_output)
    grid_output = None
    if not no_grid:
        grid_output = _resolve_unique_output_path(
            f"{root}_grid{ext}", reserved_paths={abs_output}
        )

    try:
        ffmpeg_cmd = extract_frame(
            source["path"], context["source_s"], abs_output
        )
        if grid_output is not None:
            from moviestar.fonts import bundled_font_dir

            render_target_grid_frame(
                source["path"],
                context["source_s"],
                grid_output,
                width,
                height,
                str(bundled_font_dir() / "Inter-Regular.ttf"),
            )
    except FFmpegNotFoundError as exc:
        _error_exit(command, str(exc))
    except FileNotFoundError as exc:
        _error_exit(command, str(exc))
    except RuntimeError as exc:
        _error_exit(command, str(exc))

    out_width, out_height = _probe_output_dimensions(abs_output)
    state = context["camera_plan"].state_at(
        scene_name, slot_name, context["scene_local_s"]
    )
    visible = fill_visible_rect(
        state.resolved_crop_pixels,
        context["scene_info"]["regions"][slot_name],
        context["framing"],
    )
    peer_slots = [
        {"slot": name, "source": info.get("source")}
        for name, info in context["scene_info"]["slots"].items()
        if name != slot_name
    ]

    result: dict = {
        "status": "target_frame",
        "writes_spec": False,
        "scene": scene_name,
        **(
            {"scene_id": context["scene_info"]["id"]}
            if context["scene_info"].get("id") else {}
        ),
        "slot": slot_name,
        **({"slot_id": context["slot_info"]["id"]} if context["slot_info"].get("id") else {}),
        "source": source["id"],
        "result_timecode": format_timecode(context["at_s"], fps=fps),
        "scene_local_timecode": format_timecode(
            context["scene_local_s"], fps=fps
        ),
        "source_timecode": format_timecode(context["source_s"], fps=fps),
        "out": abs_output,
        **({"grid_out": grid_output} if grid_output is not None else {}),
        "width": out_width,
        "height": out_height,
        "file_size_bytes": os.path.getsize(abs_output),
        "coordinate_space": {
            "units": "pixels",
            "origin": "top-left of this image",
            "width": width,
            "height": height,
            "note": (
                "--box and --point coordinates for 'scenes motion camera "
                "preview' use this image's pixel coordinates; normalized "
                "0..1 values are also accepted."
            ),
        },
        "visible_source_region": {
            "rect_pixels": visible,
            "camera_target": state.target,
            "camera_zoom": state.zoom,
            "active_move_id": state.active_move_id,
            "note": (
                "the part of this source the slot shows at --at (existing "
                "camera moves + framing applied); the saved image is the "
                "full source frame"
            ),
        },
        "peer_slots": peer_slots,
        "camera_scope": (
            "A camera move changes this slot only; peer slots, captions, "
            "overlays, and audio stay unchanged."
        ),
        "ffmpeg_command": ffmpeg_cmd,
        "hint": (
            "Draw a box around the content that must stay visible, then "
            "preview the framing: 'moviestar scenes motion camera preview "
            f"--at {timecode} --slot {slot_name} --box "
            "left,top,right,bottom' (pixels in this image), or use "
            "'--point x,y --zoom 2.5' for a compact target. Nothing "
            "changes spec.json until 'camera apply'."
        ),
    }
    if inline:
        result["image"] = _read_image_inline(abs_output)
    click.echo(json.dumps(result, indent=2))


# --- incremental camera authoring (agent-friendly zooming) ---

_CAMERA_PREVIEWS_DIRNAME = "camera-previews"
_CAMERA_PREVIEW_VERSION = 1
CAMERA_ZOOM_DEFAULT_PADDING = 0.1
CAMERA_ZOOM_DEFAULT_MOVE_S = 0.5
CAMERA_ZOOM_DEFAULT_HOLD_S = 2.0
_CAMERA_FAST_MOVE_S = 0.3
_CAMERA_SHORT_HOLD_S = 0.5
# Softness attribution (issue #450): a sub-canvas source upscales even at
# zoom 1.0 (the baseline), so warning on total upscale made every real
# zoom noisy. Warn only when the zoom's own contribution passes this.
_CAMERA_SOFT_ZOOM_FACTOR = 1.5
# Magnitude guidance (issue #451): typical intentional zooms read well
# between ~1.5x and ~4x. Outside these bounds the surface flags it.
_CAMERA_EXTREME_ZOOM = 6.0
_CAMERA_EXTREME_ZOOM_SUGGESTED = 4.0
_CAMERA_MIN_PERCEPTIBLE_ZOOM = 1.2
# SSIM between the selected region at hold start vs hold end below which
# the content visibly changed (menu closed, page scrolled). Ordinary
# motion in a screen recording scores well above this; a layout change
# scores far below.
_CAMERA_HOLD_STABILITY_MIN_SSIM = 0.65


def _camera_previews_dir() -> Path:
    return get_project_dir() / _CAMERA_PREVIEWS_DIRNAME


def _camera_preview_fingerprint(project: dict, spec: dict) -> str:
    """Everything a previewed framing depends on, hashed.

    apply refuses when this changes between preview and apply: a scene,
    layout, pacing, overlay, caption, canvas, or source edit can move
    the previewed frame or change what covers it.
    """
    payload = {
        "composition": spec.get("composition"),
        "canvas": spec.get("composition_canvas"),
        "motion": spec.get("motion"),
        "overlays": spec.get("overlays"),
        "captions": spec.get("captions"),
        "sources": [
            {
                "id": source.get("id"),
                "path": source.get("path"),
                "width": source.get("width"),
                "height": source.get("height"),
                "duration": source.get("duration"),
            }
            for source in project.get("sources", [])
        ],
    }
    return hashlib.md5(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]


def _next_camera_preview_id() -> str:
    directory = _camera_previews_dir()
    highest = 0
    if directory.is_dir():
        for entry in directory.glob("preview_*.json"):
            suffix = entry.stem.rsplit("_", 1)[-1]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"preview_{highest + 1:04d}"


def _load_camera_preview(command: str, preview_id: str) -> dict:
    path = _camera_previews_dir() / f"{preview_id}.json"
    if not path.is_file():
        existing = sorted(
            entry.stem for entry in _camera_previews_dir().glob("preview_*.json")
        ) if _camera_previews_dir().is_dir() else []
        _error_exit_with_hint(
            command,
            f"No camera preview named {preview_id!r}.",
            (
                "Existing previews: " + ", ".join(existing) + ". "
                if existing
                else "No previews exist yet. "
            )
            + "Run 'moviestar scenes motion camera preview' to create one.",
        )
    try:
        record = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        _error_exit(command, f"Could not read preview record {path}: {exc}")
    return record


def _parse_float_list(
    command: str, flag: str, raw: str, count: int, meaning: str
) -> list[float]:
    parts = [part.strip() for part in raw.split(",")]
    try:
        values = [float(part) for part in parts]
    except ValueError:
        values = None
    if values is None or len(values) != count:
        _error_exit_with_hint(
            command,
            f"{flag} must be {count} comma-separated numbers ({meaning}).",
            f"Example: {flag} {','.join(str(10 * (i + 1)) for i in range(count))}",
        )
    return values


def _parse_padding(command: str, raw: str) -> float:
    text = raw.strip()
    try:
        if text.endswith("%"):
            value = float(text[:-1]) / 100.0
        else:
            value = float(text)
            if value > 1.0:
                raise ValueError
    except ValueError:
        _error_exit_with_hint(
            command,
            f"--padding {raw!r} is not a padding amount.",
            "Use a percentage of the box size, added on every side: "
            "'15%' or the fraction '0.15'.",
        )
    if value < 0:
        _error_exit(command, "--padding must not be negative")
    return value


def _selection_units(
    command: str, units_flag: str | None, values: list[float]
) -> tuple[str, bool]:
    """Choose pixel vs normalized interpretation; return (units, inferred)."""
    if units_flag is not None:
        if units_flag not in MOTION_TARGET_UNITS:
            _error_exit(
                command,
                "--units must be 'pixels' or 'normalized'",
            )
        return units_flag, False
    if all(0.0 <= value <= 1.0 for value in values):
        return "normalized", True
    return "pixels", True


def _motion_ids_in_use(motion: dict) -> set[str]:
    ids: set[str] = set()
    for scene in motion.get("scenes", []):
        for slot in scene.get("slots", []):
            for entry in [*slot.get("pacing", []), *slot.get("camera", [])]:
                if entry.get("id"):
                    ids.add(entry["id"])
    return ids


def _next_camera_move_id(motion: dict) -> str:
    used = _motion_ids_in_use(motion)
    index = 0
    while True:
        index += 1
        candidate = f"cam_{index:04d}"
        if candidate not in used and f"{candidate}{ZOOM_RETURN_SUFFIX}" not in used:
            return candidate


def _camera_record_start_s(record: dict) -> float:
    if record.get("kind") == "zoom":
        at_s = parse_timecode(str(record["at"]))
        timing = record.get("timing") or {}
        move_in = parse_timecode(
            str(timing.get("move_in", CAMERA_ZOOM_DEFAULT_MOVE_S))
        )
        return at_s - move_in
    if "range" in record:
        return parse_timecode(str(record["range"]["from"]))
    return parse_timecode(str(record["at"]))


def _merge_camera_record(
    motion: dict,
    *,
    scene_name: str,
    scene_id: str | None,
    slot_name: str,
    slot_id: str | None,
    record: dict,
    replace_id: str | None = None,
) -> dict:
    """Insert (or replace) one camera record, keeping the slot's camera
    list sorted by start time so the resolver's ordering contract holds."""
    merged = copy.deepcopy(motion) if motion else {"version": 1, "scenes": []}
    merged.setdefault("version", 1)
    merged.setdefault("scenes", [])
    scene_record = _matching_stored_motion_record(
        {"scene": scene_name, "scene_id": scene_id},
        merged["scenes"],
        id_key="scene_id",
        name_key="scene",
    )
    if scene_record is None:
        scene_record = {
            "scene": scene_name,
            **({"scene_id": scene_id} if scene_id else {}),
            "slots": [],
        }
        merged["scenes"].append(scene_record)
    scene_record.setdefault("slots", [])
    slot_record = _matching_stored_motion_record(
        {"slot": slot_name, "slot_id": slot_id},
        scene_record["slots"],
        id_key="slot_id",
        name_key="slot",
    )
    if slot_record is None:
        slot_record = {
            "slot": slot_name,
            **({"slot_id": slot_id} if slot_id else {}),
            "pacing": [],
            "camera": [],
        }
        scene_record["slots"].append(slot_record)
    slot_record.setdefault("camera", [])
    if replace_id is not None:
        slot_record["camera"] = [
            entry
            for entry in slot_record["camera"]
            if entry.get("id") != replace_id
        ]
    slot_record["camera"].append(record)
    slot_record["camera"].sort(key=_camera_record_start_s)
    return merged


def _parse_zoom_selection(
    command: str,
    *,
    box_arg: str | None,
    point_arg: str | None,
    units_flag: str | None,
    width: float,
    height: float,
) -> dict:
    """Parse --box / --point into source pixels + a requested echo."""
    if box_arg is not None:
        values = _parse_float_list(
            command, "--box", box_arg, 4, "left,top,right,bottom edges",
        )
        units, units_inferred = _selection_units(command, units_flag, values)
        left, top, right, bottom = values
        if units == "normalized":
            left, right = left * width, right * width
            top, bottom = top * height, bottom * height
        if right <= left or bottom <= top:
            _error_exit_with_hint(
                command,
                "--box edges are out of order: right must be greater than "
                "left and bottom greater than top.",
                "--box is left,top,right,bottom — the last two numbers "
                "are the right and bottom EDGES, not a width/height.",
            )
        box_pixels = {
            "x": left, "y": top, "w": right - left, "h": bottom - top,
        }
        return {
            "box_pixels": box_pixels,
            "point_pixels": None,
            "units": units,
            "units_inferred": units_inferred,
        }
    values = _parse_float_list(
        command, "--point", point_arg, 2, "x,y center of attention"
    )
    units, units_inferred = _selection_units(command, units_flag, values)
    px, py = values
    if units == "normalized":
        px, py = px * width, py * height
    return {
        "box_pixels": None,
        "point_pixels": (px, py),
        "units": units,
        "units_inferred": units_inferred,
    }


def _build_zoom_candidate(
    command: str,
    context: dict,
    *,
    crop,
    requested: dict,
    place: tuple[float, float] | None,
    move_in_s: float,
    hold_s: float,
    move_out_s: float,
    ease: str,
    move_id: str,
    base_motion: dict,
    camera_plan,
) -> dict:
    """Turn a resolved selection into a candidate zoom record, verify it
    against the stored plan, and assemble the shared warning set.

    ``base_motion``/``camera_plan`` describe the plan WITHOUT the move
    being authored (preview passes the stored plan; update passes the
    plan with the old version of the move stripped)."""
    scene_name = context["scene_name"]
    slot_name = context["slot_name"]
    resolved_scene = context["resolved_scene"]
    region = context["scene_info"]["regions"][slot_name]
    project = context["project"]
    spec = context["spec"]

    local_at = context["scene_local_s"]
    start_local = round(local_at - move_in_s, 6)
    end_local = round(local_at + hold_s + move_out_s, 6)
    if start_local < -0.0005:
        _error_exit_with_hint(
            command,
            f"The {move_in_s}s move-in would start at scene-local "
            f"{_motion_tc(start_local)} — before scene {scene_name!r} "
            "begins.",
            f"Smallest fix: --move-in {max(0.1, round(local_at, 2))} or "
            "a later --at.",
        )
    if end_local > resolved_scene.duration_s + 0.0005:
        available = max(0.0, resolved_scene.duration_s - local_at)
        _error_exit_with_hint(
            command,
            f"The hold plus move-out ends at scene-local "
            f"{_motion_tc(end_local)} but paced scene {scene_name!r} ends "
            f"at {_motion_tc(resolved_scene.duration_s)}.",
            f"Smallest fix: keep --hold plus --move-out within "
            f"{round(available, 2)}s, or use an earlier --at.",
        )

    pre_state = camera_plan.state_at(
        scene_name, slot_name, max(0.0, start_local)
    )
    if pre_state.target == "full":
        return_to = {"target": "full"}
    else:
        return_to = {
            "target": {
                "space": "source",
                "units": "pixels",
                "rect": {
                    k: round(v, 2)
                    for k, v in pre_state.rect_pixels.items()
                },
            }
        }
    zoom_record = {
        "id": move_id,
        "kind": "zoom",
        "at": _motion_tc(local_at),
        "timing": {
            "move_in": round(move_in_s, 3),
            "hold": round(hold_s, 3),
            "move_out": round(move_out_s, 3),
        },
        "ease": ease,
        "to": {
            "target": {
                "space": "source",
                "units": "pixels",
                "rect": {
                    k: round(v, 2) for k, v in crop.crop_pixels.items()
                },
            }
        },
        "return_to": return_to,
        "authored": {
            **requested,
            "place": list(place) if place is not None else [0.5, 0.5],
            "source_anchor": _motion_tc(context["source_s"]),
        },
    }

    candidate_motion = _merge_camera_record(
        base_motion,
        scene_name=scene_name,
        scene_id=context["scene_info"].get("id"),
        slot_name=slot_name,
        slot_id=context["slot_info"].get("id"),
        record=zoom_record,
    )
    spec_candidate = copy.deepcopy(spec)
    spec_candidate["motion"] = candidate_motion
    try:
        resolve_camera(
            spec_candidate["composition"],
            candidate_motion,
            context["timeline"],
            {
                s["id"]: (int(s["width"]), int(s["height"]))
                for s in project["sources"]
                if s.get("width") and s.get("height")
            },
            context["canvas"],
        )
    except CameraResolutionError as exc:
        _error_exit_with_hint(
            command,
            f"This move cannot coexist with the stored camera plan: {exc}",
            "List existing moves with 'moviestar scenes motion camera "
            "list', then remove/update one or shift --at.",
        )

    global_from = round(resolved_scene.start_s + start_local, 6)
    global_end = round(resolved_scene.start_s + end_local, 6)
    selection_canvas = _selection_canvas_rect(crop, region)

    warnings: list[dict] = []
    baseline_upscale = (
        round(crop.upscale_factor / crop.zoom, 2) if crop.zoom else 0.0
    )
    if crop.zoom > _CAMERA_SOFT_ZOOM_FACTOR:
        factor = round(crop.upscale_factor, 2)
        if crop.kind == "point":
            softness_fix = "reduce --zoom, or accept softness"
        elif crop.padded_pixels != crop.selection_pixels:
            softness_fix = "reduce --padding, or accept softness"
        else:
            softness_fix = "select a larger box, or accept softness"
        warnings.append(
            _warning(
                "upscaled_target",
                f"This zoom renders a {round(crop.crop_pixels['w'])}x"
                f"{round(crop.crop_pixels['h'])}px source rect into the "
                f"{region['width']}x{region['height']}px {slot_name!r} "
                f"slot region (~{factor}x total upscale: ~{baseline_upscale}x "
                "is inherent to this source at full view, the zoom "
                f"contributes ~{round(crop.zoom, 2)}x). Zooms under ~2x "
                "beyond the baseline usually read fine; beyond ~3x expect "
                "visible softness. Intentional zooms are never limited.",
                upscale_factor=factor,
                baseline_upscale=baseline_upscale,
                zoom_contribution=round(crop.zoom, 2),
                smallest_fix=softness_fix,
            )
        )
    if crop.zoom > _CAMERA_EXTREME_ZOOM:
        if crop.kind == "point":
            magnitude_fix = f"try --zoom {_CAMERA_EXTREME_ZOOM_SUGGESTED:g}"
        else:
            base = crop.base_view_pixels
            selection = crop.selection_pixels
            padding_options = [
                (base[dimension] / _CAMERA_EXTREME_ZOOM_SUGGESTED
                 - selection[axis]) / (2 * selection[axis])
                for dimension, axis in (("w", "w"), ("h", "h"))
                if selection[axis] > 0
            ]
            suggested = min(
                (option for option in padding_options if option > 0),
                default=None,
            )
            magnitude_fix = (
                f"try --padding {round(suggested * 20) * 5}%"
                if suggested is not None
                else "select a larger box"
            )
        warnings.append(
            _warning(
                "extreme_zoom",
                f"The content is magnified ~{round(crop.zoom, 1)}x versus "
                "the slot's normal full-source view; typical intentional "
                "zooms land between ~1.5x and ~4x. Intentional zooms are "
                "never limited.",
                zoom=round(crop.zoom, 2),
                smallest_fix=(
                    f"{magnitude_fix} for a ~"
                    f"{_CAMERA_EXTREME_ZOOM_SUGGESTED:g}x framing, or keep "
                    "this magnification"
                ),
            )
        )
    elif crop.zoom < _CAMERA_MIN_PERCEPTIBLE_ZOOM:
        warnings.append(
            _warning(
                "barely_perceptible_zoom",
                f"This selection (plus aspect matching) nearly fills the "
                f"slot's normal view, so the move only reaches "
                f"~{round(crop.zoom, 2)}x and may not read as a zoom.",
                severity="info",
                zoom=round(crop.zoom, 2),
                smallest_fix=(
                    "accept the subtle move, tighten the box to the most "
                    "important part, or split the target into two zooms"
                ),
            )
        )
    if "selection_exceeds_slot_view" in crop.adjustments:
        warnings.append(
            _warning(
                "selection_larger_than_slot_view",
                "The selected box (plus padding) is larger than this "
                "slot can ever show at its aspect ratio; the crop is the "
                "largest possible view and the selection will be cut.",
                smallest_fix="select a smaller box or reduce --padding",
            )
        )
    if "shifted_inside_source_bounds" in crop.adjustments:
        warnings.append(
            _warning(
                "crop_shifted_at_edge",
                "The selection sits near a source edge; MovieStar shifted "
                "the crop inside the frame instead of cutting the box "
                "off. The selection image shows the final crop.",
                severity="info",
            )
        )
    if "placement_limited_to_keep_selection_visible" in crop.adjustments:
        warnings.append(
            _warning(
                "placement_limited",
                "--place was limited so the whole selection stays inside "
                "the crop.",
                severity="info",
                effective_placement=list(crop.effective_placement),
            )
        )
    if move_in_s < _CAMERA_FAST_MOVE_S or move_out_s < _CAMERA_FAST_MOVE_S:
        warnings.append(
            _warning(
                "fast_camera_move",
                f"A move under {_CAMERA_FAST_MOVE_S}s can read as an "
                "accidental jump rather than an intentional zoom.",
                smallest_fix="try --move-in 0.5 or slower",
            )
        )
    if hold_s < _CAMERA_SHORT_HOLD_S:
        warnings.append(
            _warning(
                "short_camera_hold",
                f"A hold under {_CAMERA_SHORT_HOLD_S}s gives viewers "
                "little time to read the zoomed content.",
                smallest_fix="try --hold 1.5 or longer",
            )
        )
    hold_end_global = min(
        context["lookup_at_s"] + hold_s,
        context["timeline"].duration_s - 0.001,
    )
    source_at_arrival = context["source_s"]
    source_at_hold_end = context["timeline"].source_time_at(
        hold_end_global, slot_name
    )
    if abs(source_at_hold_end - source_at_arrival) > 0.001:
        stability = measure_region_stability(
            context["source"]["path"],
            source_at_arrival,
            source_at_hold_end,
            crop.crop_pixels,
        )
        if (
            stability is not None
            and stability < _CAMERA_HOLD_STABILITY_MIN_SSIM
        ):
            warnings.append(
                _warning(
                    "selection_changes_during_hold",
                    "The selected area changes substantially while the "
                    f"camera holds (similarity {round(stability, 2)} "
                    f"between the {_motion_tc(source_at_arrival)} and "
                    f"{_motion_tc(source_at_hold_end)} source frames) — "
                    "e.g. a menu closing or a page scrolling mid-hold.",
                    similarity=round(stability, 3),
                    source_range_compared={
                        "from": _motion_tc(source_at_arrival),
                        "to": _motion_tc(source_at_hold_end),
                        "space": "source",
                    },
                    smallest_fix=(
                        "shorten --hold, pick a steadier --at, or re-run "
                        "'scenes motion target' at the hold's end to check "
                        "what viewers will see"
                    ),
                )
            )
    warnings.extend(
        _selection_coverage_warnings(
            command=command,
            project=project,
            spec_candidate=spec_candidate,
            context=context,
            crop=crop,
            padding=requested.get("padding"),
            region=region,
            selection_canvas=selection_canvas,
            window_from_s=global_from,
            window_to_s=global_end,
        )
    )
    return {
        "zoom_record": zoom_record,
        "candidate_motion": candidate_motion,
        "spec_candidate": spec_candidate,
        "start_local": start_local,
        "end_local": end_local,
        "global_from": global_from,
        "global_at": context["lookup_at_s"],
        "global_end": global_end,
        "selection_canvas": selection_canvas,
        "warnings": warnings,
    }


def _zoom_resolved_echo(crop, slot_name: str, selection_canvas: dict) -> dict:
    return {
        "crop_pixels": crop.crop_pixels,
        "crop_normalized": crop.crop_normalized,
        "zoom": crop.zoom,
        **(
            {"requested_zoom": crop.requested_zoom}
            if crop.requested_zoom is not None else {}
        ),
        "upscale_factor": crop.upscale_factor,
        "baseline_upscale": (
            round(crop.upscale_factor / crop.zoom, 2) if crop.zoom else 0.0
        ),
        "adjustments": list(crop.adjustments),
        "effective_placement": list(crop.effective_placement),
        "selection_lands_at": {
            "slot_region": slot_name,
            "canvas_rect_pixels": selection_canvas,
        },
    }


def _camera_preview_command_text(record: dict) -> str:
    """Reconstruct the preview invocation from a stored preview record,
    so stale-apply errors hand back a directly re-runnable command."""
    requested = record.get("requested", {})
    timing = record.get("timing", {})
    parts = [
        "moviestar scenes motion camera preview",
        f"--at {record['arrives_at']['result']}",
        f"--slot {record['slot']}",
    ]
    if requested.get("selection") == "box":
        box = requested["box_pixels"]
        parts.append(
            f"--box {round(box['x'], 2)},{round(box['y'], 2)},"
            f"{round(box['x'] + box['w'], 2)},{round(box['y'] + box['h'], 2)}"
        )
        if requested.get("padding") is not None:
            parts.append(
                f"--padding {round(requested['padding'] * 100, 2):g}%"
            )
    else:
        point = requested["point_pixels"]
        parts.append(f"--point {point[0]},{point[1]}")
        parts.append(f"--zoom {requested['zoom']}")
    if requested.get("place"):
        place = requested["place"]
        parts.append(f"--place {place[0]},{place[1]}")
    parts.append(
        f"--move-in {timing['move_in']} --hold {timing['hold']} "
        f"--move-out {timing['move_out']} --ease {timing['ease']}"
    )
    return " ".join(parts)


def _render_composition_frame(
    command: str, project: dict, spec: dict, at_s: float, output: str
) -> None:
    """Render one full-composition frame (slots + overlays) at a result
    time — the same path screenshot uses, so preview equals export."""
    _plan, render_plan = _scene_render_plan_at(
        project=project,
        spec=spec,
        composition=spec["composition"],
        at_s=at_s,
        command=command,
    )
    planned_overlays = _overlay_render_context(
        project,
        spec,
        render_plan["canvas_tuple"],
        at_s,
        at_s + 0.001,
        command,
    )
    _write_highlight_ass(planned_overlays)
    try:
        render_layout_frame(
            render_plan["render_slots"],
            output,
            render_plan["canvas_tuple"],
            overlay_plans=planned_overlays["plans"] if planned_overlays else None,
            highlight_ass=_highlight_ass_arg(planned_overlays),
        )
    except FFmpegNotFoundError as exc:
        _error_exit(command, str(exc))
    except FileNotFoundError as exc:
        _error_exit(command, str(exc))
    except RuntimeError as exc:
        _error_exit(command, str(exc))


def _rects_intersect(first: dict, second: dict) -> bool:
    return (
        max(first["x"], second["x"])
        < min(first["x"] + first["width"], second["x"] + second["width"])
        and max(first["y"], second["y"])
        < min(first["y"] + first["height"], second["y"] + second["height"])
    )


def _caption_position_for_scene(
    recipe: dict, scene_name: str, layout_preset: str | None
) -> str:
    """Resolve one scene's caption position from a recipe's placement
    policy — the same precedence 'captions placement' documents: scene
    overrides beat layout overrides; both beat the default."""
    placement = recipe.get("placement") or {}
    position = (
        placement.get("default") or recipe.get("position") or "bottom"
    )
    for override in placement.get("overrides", []):
        selector = override.get("selector", {})
        if selector.get("layout") == layout_preset:
            position = override["position"]
        if selector.get("scene") == scene_name:
            position = override["position"]
    return position


def _suggested_inset_relocation(
    *,
    spec: dict,
    scene_name: str,
    selection_canvas: dict,
    canvas: dict,
) -> dict[str, str] | None:
    """Return a canonical anchored relocation that actually clears content."""
    scene = next(
        (
            item
            for item in spec.get("composition") or []
            if item.get("name") == scene_name
        ),
        None,
    )
    if scene is None:
        return None
    layout = scene.get("layout") or {}
    # Migrating a legacy shape can alter its dimensions, and an anchored start
    # position cannot promise clearance for an inset that continues to move.
    if layout.get("inset") is not None:
        return None
    geometry = copy.deepcopy((layout.get("geometry") or {}).get("inset") or {})
    if geometry.get("motion") is not None:
        return None

    candidates: list[tuple[float, int, str]] = []
    selection_center = (
        selection_canvas["x"] + selection_canvas["width"] / 2,
        selection_canvas["y"] + selection_canvas["height"] / 2,
    )
    for order, anchor in enumerate(SLOT_GEOMETRY_ANCHORS):
        candidate_geometry = copy.deepcopy(geometry)
        candidate_geometry.pop("x", None)
        candidate_geometry.pop("y", None)
        candidate_geometry["anchor"] = anchor
        try:
            candidate_region = resolve_inset_geometry(candidate_geometry, canvas)
        except SpecValidationError:
            continue
        if _rects_intersect(selection_canvas, candidate_region):
            continue
        candidate_center = (
            candidate_region["x"] + candidate_region["width"] / 2,
            candidate_region["y"] + candidate_region["height"] / 2,
        )
        distance_squared = (
            (candidate_center[0] - selection_center[0]) ** 2
            + (candidate_center[1] - selection_center[1]) ** 2
        )
        candidates.append((-distance_squared, order, anchor))

    if not candidates:
        return None
    anchor = min(candidates)[2]
    return {
        "anchor": anchor,
        "command": (
            f"moviestar scenes geometry {scene_name}:inset --at {anchor}"
        ),
    }


def _suggested_place_to_clear(
    crop,
    region: dict,
    cover: dict,
    source: tuple[float, float],
    padding: float,
) -> tuple[float, float] | None:
    """Return a cent-precision ``--place`` that the real resolver verifies."""
    selection_canvas = _selection_canvas_rect(crop, region)
    place_x, place_y = crop.placement
    effective_x, effective_y = crop.effective_placement
    region_w = float(region["width"])
    region_h = float(region["height"])
    deltas = (
        ("y", -(selection_canvas["y"] + selection_canvas["height"]
                - cover["y"])),
        ("y", cover["y"] + cover["height"] - selection_canvas["y"]),
        ("x", -(selection_canvas["x"] + selection_canvas["width"]
                - cover["x"])),
        ("x", cover["x"] + cover["width"] - selection_canvas["x"]),
    )
    candidates: set[tuple[float, float]] = set()
    for axis, delta in deltas:
        if axis == "y":
            raw = effective_y + delta / region_h
            fixed = place_x
        else:
            raw = effective_x + delta / region_w
            fixed = place_y
        # The exact boundary often falls between two printable hundredths.
        # Try both sides plus their immediate neighbors, then trust the
        # canonical resolver and the same intersection check as the warning.
        rounded = {math.floor(raw * 100), math.ceil(raw * 100)}
        for hundredths in rounded | {value - 1 for value in rounded} | {
            value + 1 for value in rounded
        }:
            moved = hundredths / 100
            if not 0.0 <= moved <= 1.0:
                continue
            candidate = (
                (round(fixed, 2), moved)
                if axis == "y"
                else (moved, round(fixed, 2))
            )
            candidates.add(candidate)

    ranked = sorted(
        candidates,
        key=lambda value: (
            abs(value[0] - place_x) + abs(value[1] - place_y),
            value,
        ),
    )
    for candidate in ranked:
        try:
            resolved = resolve_selection_crop(
                source=source,
                region=region,
                box_pixels=(
                    crop.selection_pixels if crop.kind == "box" else None
                ),
                point_pixels=(
                    (
                        crop.selection_pixels["x"],
                        crop.selection_pixels["y"],
                    )
                    if crop.kind == "point" else None
                ),
                zoom=crop.requested_zoom,
                padding=padding,
                place=candidate,
            )
        except CameraResolutionError:
            continue
        if "selection_exceeds_slot_view" in resolved.adjustments:
            continue
        if not _rects_intersect(_selection_canvas_rect(resolved, region), cover):
            return candidate
    return None


def _selection_canvas_rect(crop, region: dict) -> dict[str, float]:
    selection = crop.selection_in_region
    return {
        "x": round(region["x"] + selection["x"], 2),
        "y": round(region["y"] + selection["y"], 2),
        "width": round(max(2.0, selection["w"]), 2),
        "height": round(max(2.0, selection["h"]), 2),
    }


def _coverage_suggestion(
    crop,
    region: dict,
    cover: dict,
    source: tuple[float, float],
    padding: float | None,
) -> dict | None:
    """Compute a replayable place, adding box padding only when required."""
    current_padding = padding or 0.0
    place = _suggested_place_to_clear(
        crop, region, cover, source, current_padding
    )
    if place is not None:
        return {"place": place}
    if crop.kind != "box" or padding is None:
        return None

    # One percentage point is the command's useful human precision. Stop at
    # 1000%: beyond that, preserving this target via a smaller box or moving
    # the covering element is a clearer fix than recommending extreme context.
    first_percent = math.floor(current_padding * 100 + 1e-9) + 1
    for percent in range(first_percent, 1001):
        candidate_padding = percent / 100
        try:
            candidate_crop = resolve_selection_crop(
                source=source,
                region=region,
                box_pixels=crop.selection_pixels,
                padding=candidate_padding,
                place=crop.placement,
            )
        except CameraResolutionError:
            break
        if "selection_exceeds_slot_view" in candidate_crop.adjustments:
            break
        if not _rects_intersect(
            _selection_canvas_rect(candidate_crop, region), cover
        ):
            place = tuple(round(value, 2) for value in crop.placement)
        else:
            place = _suggested_place_to_clear(
                candidate_crop,
                region,
                cover,
                source,
                candidate_padding,
            )
        if place is not None:
            return {
                "place": place,
                "padding": candidate_padding,
                "padding_delta": round(candidate_padding - padding, 6),
            }
    return None


def _selection_coverage_warnings(
    *,
    command: str,
    project: dict,
    spec_candidate: dict,
    context: dict,
    crop,
    padding: float | None,
    region: dict,
    selection_canvas: dict,
    window_from_s: float,
    window_to_s: float,
) -> list[dict]:
    """Warn when captions, overlays, or a later-drawn slot (PIP inset)
    cover the selected content after it is placed in the final frame."""
    warnings: list[dict] = []
    source_dims = (
        float(context["source"]["width"]),
        float(context["source"]["height"]),
    )

    def _suggestion_for(cover: dict) -> dict | None:
        return _coverage_suggestion(
            crop, region, cover, source_dims, padding
        )

    def _fields(suggestion: dict | None) -> dict:
        if suggestion is None:
            return {}
        fields = {"suggested_place": list(suggestion["place"])}
        if "padding" in suggestion:
            fields.update(
                suggested_padding=suggestion["padding"],
                padding_delta=suggestion["padding_delta"],
            )
        return fields

    def _place_fix(suggestion: dict | None) -> str:
        if suggestion is None:
            return (
                "change --place so the selected content sits clear "
                "(e.g. --place 0.5,0.35 raises it)"
            )
        place = suggestion["place"]
        if "padding" in suggestion:
            return (
                f"change --padding to {suggestion['padding'] * 100:g}% and "
                f"--place to {place[0]:g},{place[1]:g} — the smallest "
                "one-percent context increase that enables a clear placement"
            )
        return (
            f"change --place to {place[0]:g},{place[1]:g} — the smallest "
            "shift that moves the selected content clear"
        )

    def _command_fix(suggestion: dict | None) -> str:
        if suggestion is None:
            return "change --place to keep the selection clear of it"
        place = suggestion["place"]
        padding_clause = (
            f"--padding {suggestion['padding'] * 100:g}% "
            if "padding" in suggestion else ""
        )
        return (
            f"use {padding_clause}--place {place[0]:g},{place[1]:g} to "
            "move the selection clear"
        )

    def _fixes(suggestion: dict | None) -> list[str]:
        place_fix = (
            _place_fix(suggestion)
        )
        return [
            "reduce the zoom (smaller --zoom or a larger --box)",
            place_fix,
            "move the captions with 'moviestar captions placement'",
            "accept the overlap and apply anyway",
        ]

    regions = context["scene_info"]["regions"]
    slot_order = list(context["scene_info"]["slots"])
    own_index = slot_order.index(context["slot_name"])
    for peer_name in slot_order[own_index + 1:]:
        peer_region = regions[peer_name]
        if _rects_intersect(selection_canvas, peer_region):
            suggestion = _suggestion_for(peer_region)
            place_clause = _command_fix(suggestion)
            if (
                peer_name == "inset"
                and context["scene_info"]["layout_preset"]
                == "picture-in-picture"
            ):
                inset_relocation = _suggested_inset_relocation(
                    spec=spec_candidate,
                    scene_name=context["scene_name"],
                    selection_canvas=selection_canvas,
                    canvas=context["canvas"],
                )
                if inset_relocation is not None:
                    inset_fields = {
                        "suggested_inset_anchor": inset_relocation["anchor"],
                        "suggested_inset_command": inset_relocation["command"],
                    }
                    smallest_fix = (
                        f"move the inset with {inset_relocation['command']!r}, "
                        f"or {place_clause}"
                    )
                else:
                    inset_fields = {}
                    smallest_fix = f"{place_clause}, or accept the overlap"
            else:
                inset_fields = {}
                smallest_fix = f"{place_clause}, or accept the overlap"
            warnings.append(
                _warning(
                    "selected_content_under_pip",
                    f"The {peer_name!r} slot draws over the selected "
                    f"content in the final frame (it covers canvas region "
                    f"{peer_region['x']},{peer_region['y']} "
                    f"{peer_region['width']}x{peer_region['height']}). "
                    "MovieStar never moves an inset automatically.",
                    covering_slot=peer_name,
                    covering_region=peer_region,
                    **_fields(suggestion),
                    **inset_fields,
                    smallest_fix=smallest_fix,
                )
            )

    canvas_tuple = (
        int(context["canvas"]["width"]),
        int(context["canvas"]["height"]),
    )
    planned = _overlay_render_context(
        project,
        spec_candidate,
        canvas_tuple,
        window_from_s,
        window_to_s,
        command,
    )
    for plan in (planned or {}).get("plans", []):
        bounds = plan.get("estimated_bounds")
        if not bounds or not bounds.get("estimate", True):
            continue
        if _rects_intersect(selection_canvas, bounds):
            suggestion = _suggestion_for(bounds)
            warnings.append(
                _warning(
                    "selected_content_covered",
                    f"Overlay {plan.get('id')!r} "
                    f"({plan.get('text', '')[:40]!r}) overlaps the selected "
                    "content's final position (estimated bounds "
                    f"{bounds['x']},{bounds['y']} "
                    f"{bounds['width']}x{bounds['height']}).",
                    overlay_id=plan.get("id"),
                    estimated_bounds={
                        key: bounds[key]
                        for key in ("x", "y", "width", "height")
                    },
                    **_fields(suggestion),
                    fixes=_fixes(suggestion),
                )
            )

    # Caption recipes whose cues are not materialized as overlays (a
    # failed derivation or a frozen track with cleared cues) have no
    # estimated_bounds above. Fall back to the position-baseline lane
    # so a caption track never silently covers the selection (#436).
    materialized_tracks = {
        overlay.get("track")
        for overlay in spec_candidate.get("overlays", [])
    }
    canvas_width = int(context["canvas"]["width"])
    canvas_height = int(context["canvas"]["height"])
    for recipe in spec_candidate.get("captions") or []:
        track = recipe.get("track")
        if track in materialized_tracks:
            continue
        position = _caption_position_for_scene(
            recipe,
            context["scene_name"],
            context["scene_info"]["layout_preset"],
        )
        baseline_y = canvas_height * _CAPTION_POSITION_BASELINES.get(
            position, 0.9
        )
        lane = {
            "x": 0,
            "y": round(baseline_y - 0.07 * canvas_height),
            "width": canvas_width,
            "height": round(0.08 * canvas_height),
        }
        if _rects_intersect(selection_canvas, lane):
            suggestion = _suggestion_for(lane)
            warnings.append(
                _warning(
                    "selected_content_in_caption_lane",
                    f"Caption track {track!r} has no rendered cues to "
                    "measure (derivation unavailable or the track is "
                    f"frozen), but its {position!r} lane (estimated "
                    f"canvas band {lane['x']},{lane['y']} "
                    f"{lane['width']}x{lane['height']}) overlaps the "
                    "selected content's final position.",
                    track=track,
                    position=position,
                    estimated_lane=lane,
                    **_fields(suggestion),
                    fixes=_fixes(suggestion),
                )
            )
    return warnings


@scenes_motion.group("camera", invoke_without_command=True)
@click.pass_context
def scenes_motion_camera(ctx: click.Context) -> None:
    """Author one zoom at a time: target frame -> preview -> apply.

    \b
    The incremental flow (no JSON editing, no crop math):
      1. scenes motion target   -- save the frame to mark up
      2. camera preview         -- see the exact from/to framing
      3. camera apply           -- save exactly what was previewed
      4. camera list/update/remove -- adjust later

    Bulk editing via 'scenes motion dump' / 'set' remains available.
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(
        json.dumps(
            {
                "status": "camera_authoring",
                "writes_spec": False,
                "workflow": [
                    {
                        "command": (
                            "moviestar scenes motion target --at 0:42 "
                            "--slot main"
                        ),
                        "purpose": "save the exact source frame to mark up",
                    },
                    {
                        "command": (
                            "moviestar scenes motion camera preview "
                            "--at 0:42 --slot main "
                            "--box left,top,right,bottom --padding 15%"
                        ),
                        "purpose": (
                            "render the exact from/to framing without "
                            "writing spec.json (or --point x,y --zoom 2.5)"
                        ),
                    },
                    {
                        "command": (
                            "moviestar scenes motion camera apply "
                            "preview_0001"
                        ),
                        "purpose": "save exactly the previewed move",
                    },
                ],
                "hint": (
                    "Start with 'moviestar scenes motion target' to get a "
                    "frame you can mark up, then preview before anything "
                    "is saved."
                ),
            },
            indent=2,
        )
    )


@scenes_motion_camera.command("preview")
@project_workspace_option
@click.option(
    "--at",
    "timecode",
    required=True,
    help="Result timecode where the camera is fully zoomed in (the frame "
    "you marked up with 'scenes motion target'). The move-in runs BEFORE "
    "this moment: \"at 0:42, zoom in over one second\" means --at 0:43 "
    "--move-in 1.0.",
)
@click.option(
    "--slot",
    "slot_name",
    default=None,
    help="Bare slot name to zoom, e.g. inset rather than demo:inset "
    "(defaults to the scene's only slot; --at selects the scene).",
)
@click.option(
    "--box",
    "box_arg",
    default=None,
    help="left,top,right,bottom — box edges around ALL content that must "
    "stay visible (the last two numbers are the right and bottom EDGES, "
    "not width/height). Pixels of the target image, or normalized 0..1.",
)
@click.option(
    "--point",
    "point_arg",
    default=None,
    help="x,y center of attention (pixels of the target image or "
    "normalized 0..1). Requires --zoom.",
)
@click.option(
    "--zoom",
    "zoom_arg",
    type=float,
    default=None,
    help="With --point: how much larger the content appears vs the "
    "slot's normal full-source view (2.5 = 2.5x larger).",
)
@click.option(
    "--units",
    "units_flag",
    default=None,
    help="Force 'pixels' or 'normalized' for --box/--point. Inferred "
    "when omitted (all values <= 1.0 reads as normalized).",
)
@click.option(
    "--padding",
    "padding_arg",
    default=None,
    help="Context added outside the selected box on every side, as a "
    "share of the box size ('15%' or 0.15). Box mode only. "
    f"Default {round(CAMERA_ZOOM_DEFAULT_PADDING * 100)}%.",
)
@click.option(
    "--place",
    "place_arg",
    default=None,
    help="x,y normalized position where the selection's center lands "
    "inside the final crop (default 0.5,0.5 = centered). Use e.g. "
    "0.5,0.35 to keep content above a caption lane.",
)
@click.option(
    "--move-in",
    "move_in_s",
    type=float,
    default=CAMERA_ZOOM_DEFAULT_MOVE_S,
    show_default=True,
    help="Seconds the zoom-in takes, ending at --at.",
)
@click.option(
    "--hold",
    "hold_s",
    type=float,
    default=CAMERA_ZOOM_DEFAULT_HOLD_S,
    show_default=True,
    help="Seconds to stay at the zoomed framing after --at.",
)
@click.option(
    "--move-out",
    "move_out_s",
    type=float,
    default=None,
    help="Seconds the zoom-out takes (defaults to --move-in).",
)
@click.option(
    "--ease",
    "ease",
    default="in-out",
    show_default=True,
    help="Easing for both moves: linear, in, out, or in-out.",
)
@click.option(
    "--id",
    "move_id_arg",
    default=None,
    help="Name for the camera move (defaults to the next free cam_NNNN). "
    "Used later by camera update / remove.",
)
@click.option(
    "--video",
    "render_video",
    is_flag=True,
    help="Also render a short low-resolution video of the move in, hold, "
    "and move out. Slower; skip it while iterating on framing — the "
    "from/to images already show the exact final framing.",
)
def scenes_motion_camera_preview(
    timecode: str,
    slot_name: str | None,
    box_arg: str | None,
    point_arg: str | None,
    zoom_arg: float | None,
    units_flag: str | None,
    padding_arg: str | None,
    place_arg: str | None,
    move_in_s: float,
    hold_s: float,
    move_out_s: float | None,
    ease: str,
    move_id_arg: str | None,
    render_video: bool,
) -> None:
    """Preview a zoom's exact framing without writing spec.json.

    \b
    Renders three images:
      - selection: your box/point and MovieStar's final crop drawn on
        the source frame
      - from: the full final composition as the move starts
      - to: the full final composition once the zoom arrives
    --video additionally renders a short low-resolution clip of the
    move in, hold, and move out (slower; the images already show the
    exact framing).

    \b
    The box is the important content, not a crop to calculate.
    MovieStar expands it with --padding, matches the slot's aspect
    ratio, keeps it inside the source frame, and shifts near edges so
    the box always stays visible. --at is the moment the camera is
    fully zoomed: the move runs [at - move-in, at], holds --hold
    seconds, then returns over --move-out seconds. So "at 0:42, zoom
    in over one second" is --at 0:43 --move-in 1.0. The response's
    timing_sentence spells out the resolved schedule — check it if
    the phrasing was ambiguous.

    The response ends with the exact apply command. Adjust and re-run
    preview until the from/to images look right; nothing is saved
    until 'camera apply'.
    """
    command = "scenes motion camera preview"
    context = _motion_target_context(command, timecode, slot_name)
    project = context["project"]
    spec = context["spec"]
    slot_name = context["slot_name"]
    scene_name = context["scene_name"]
    resolved_scene = context["resolved_scene"]
    source = context["source"]
    width = float(source["width"])
    height = float(source["height"])
    region = context["scene_info"]["regions"][slot_name]

    if (box_arg is None) == (point_arg is None):
        _error_exit_with_hint(
            command,
            "Choose what should be visible with exactly one of --box or "
            "--point.",
            "--box left,top,right,bottom keeps a whole area visible "
            "(dialog, panel, toolbar); --point x,y --zoom 2.5 centers a "
            "compact target. Get coordinates from 'moviestar scenes "
            "motion target'.",
        )
    if point_arg is not None and zoom_arg is None:
        _error_exit_with_hint(
            command,
            "--point needs --zoom to say how far to zoom in.",
            "--zoom 2.5 makes the content 2.5x larger than the slot's "
            "normal full-source view.",
        )
    if box_arg is not None and zoom_arg is not None:
        _error_exit_with_hint(
            command,
            "--zoom applies to --point selections only.",
            "A box's size plus --padding controls how far a box zooms.",
        )
    if ease not in MOTION_EASES - {"cut"}:
        _error_exit(
            command,
            "--ease must be one of: in, in-out, linear, out",
        )
    move_out_s = move_out_s if move_out_s is not None else move_in_s
    for label, value in (
        ("--move-in", move_in_s),
        ("--hold", hold_s),
        ("--move-out", move_out_s),
    ):
        if value <= 0:
            _error_exit(command, f"{label} must be greater than 0 seconds")

    padding = (
        _parse_padding(command, padding_arg)
        if padding_arg is not None
        else CAMERA_ZOOM_DEFAULT_PADDING
    )
    place = None
    if place_arg is not None:
        place_values = _parse_float_list(
            command, "--place", place_arg, 2,
            "normalized x,y position inside the crop",
        )
        place = (place_values[0], place_values[1])

    selection = _parse_zoom_selection(
        command,
        box_arg=box_arg,
        point_arg=point_arg,
        units_flag=units_flag,
        width=width,
        height=height,
    )
    box_pixels = selection["box_pixels"]
    point_pixels = selection["point_pixels"]
    if box_pixels is not None:
        requested = {
            "selection": "box",
            "box_pixels": {k: round(v, 2) for k, v in box_pixels.items()},
            "padding": padding,
            "units": selection["units"],
            "units_inferred": selection["units_inferred"],
        }
    else:
        requested = {
            "selection": "point",
            "point_pixels": [round(v, 2) for v in point_pixels],
            "zoom": zoom_arg,
            "units": selection["units"],
            "units_inferred": selection["units_inferred"],
        }
    if place is not None:
        requested["place"] = list(place)

    try:
        crop = resolve_selection_crop(
            source=(width, height),
            region=region,
            box_pixels=box_pixels,
            point_pixels=point_pixels,
            zoom=zoom_arg,
            padding=padding if box_pixels is not None else 0.0,
            place=place,
        )
    except CameraResolutionError as exc:
        _error_exit_with_hint(
            command,
            str(exc),
            "Coordinates come from the image written by 'moviestar scenes "
            f"motion target --at {timecode} --slot {slot_name}' "
            f"({int(width)}x{int(height)} pixels, origin top-left).",
        )

    stored_motion = spec.get("motion") or {"version": 1, "scenes": []}
    if move_id_arg is not None:
        move_id = move_id_arg.strip()
        if not move_id or ":" in move_id:
            _error_exit(
                command,
                "--id must be a non-empty name without ':' characters",
            )
        if move_id in _motion_ids_in_use(stored_motion):
            _error_exit_with_hint(
                command,
                f"Motion id {move_id!r} is already in use.",
                "Pick another --id, or change the existing move with "
                f"'moviestar scenes motion camera update --id {move_id}'.",
            )
    else:
        move_id = _next_camera_move_id(stored_motion)

    candidate = _build_zoom_candidate(
        command,
        context,
        crop=crop,
        requested=requested,
        place=place,
        move_in_s=move_in_s,
        hold_s=hold_s,
        move_out_s=move_out_s,
        ease=ease,
        move_id=move_id,
        base_motion=stored_motion,
        camera_plan=context["camera_plan"],
    )
    zoom_record = candidate["zoom_record"]
    spec_candidate = candidate["spec_candidate"]

    preview_id = _next_camera_preview_id()
    base = f"camera_{preview_id}"
    selection_out = os.path.realpath(f"{base}_selection.jpg")
    from_out = os.path.realpath(f"{base}_from.jpg")
    to_out = os.path.realpath(f"{base}_to.jpg")
    _validate_output_parent(command, selection_out)

    boxes = [
        {
            "rect": crop.crop_pixels,
            "color": "yellow@0.9",
            "thickness": 3,
        },
    ]
    if crop.kind == "box":
        boxes.append(
            {
                "rect": crop.selection_pixels,
                "color": "lime@0.9",
                "thickness": 3,
            }
        )
    else:
        px, py = point_pixels
        boxes.extend(
            [
                {
                    "rect": {"x": px - 14, "y": py - 1.5, "w": 28, "h": 3},
                    "color": "lime@0.9",
                    "thickness": "fill",
                },
                {
                    "rect": {"x": px - 1.5, "y": py - 14, "w": 3, "h": 28},
                    "color": "lime@0.9",
                    "thickness": "fill",
                },
            ]
        )
    try:
        render_selection_frame(
            source["path"], context["source_s"], selection_out, boxes
        )
    except FFmpegNotFoundError as exc:
        _error_exit(command, str(exc))
    except FileNotFoundError as exc:
        _error_exit(command, str(exc))
    except RuntimeError as exc:
        _error_exit(command, str(exc))

    _render_composition_frame(
        command, project, spec_candidate, candidate["global_from"], from_out
    )
    _render_composition_frame(
        command, project, spec_candidate, candidate["global_at"], to_out
    )

    motion_out = None
    if render_video:
        motion_out = os.path.realpath(f"{base}_motion.mp4")
        _video_plan, video_render_plans = _scene_render_plans_for_range(
            project=project,
            spec=spec_candidate,
            composition=spec_candidate["composition"],
            start_s=candidate["global_from"],
            end_s=candidate["global_end"],
            command=command,
        )
        artifacts = _scene_video_render_artifacts(
            command=command,
            project=project,
            spec=spec_candidate,
            render_plans=video_render_plans,
            abs_output=motion_out,
            work_dir=(
                get_project_dir() / "scene-renders" / f"camera_{preview_id}"
            ),
            preview=True,
        )
        try:
            _render_scene_video_artifacts(
                artifacts=artifacts,
                abs_output=motion_out,
                preview=True,
            )
        except FFmpegNotFoundError as exc:
            _error_exit(command, str(exc))
        except FileNotFoundError as exc:
            _error_exit(command, str(exc))
        except RuntimeError as exc:
            _error_exit(command, f"Motion preview render failed: {exc}")

    warnings = candidate["warnings"]
    fingerprint = _camera_preview_fingerprint(project, spec)
    apply_command = f"moviestar scenes motion camera apply {preview_id}"
    resolved_echo = _zoom_resolved_echo(
        crop, slot_name, candidate["selection_canvas"]
    )
    local_at = context["scene_local_s"]
    arrives_at = {
        "result": _motion_tc(context["at_s"]),
        "scene_local": _motion_tc(local_at),
        "source": _motion_tc(context["source_s"]),
    }
    timing_echo = {
        "move_in": round(move_in_s, 3),
        "hold": round(hold_s, 3),
        "move_out": round(move_out_s, 3),
        "ease": ease,
    }
    # Plain-sentence schedule: both friction rounds showed "at X, zoom
    # in over 1s" vs --at-is-arrival as the top translation burden
    # (issue #449); a wrong guess should be self-evident on the first
    # preview without decoding the structured fields.
    timing_sentence = (
        f"Zoom begins at {_motion_tc(candidate['global_from'])}, is fully "
        f"zoomed from {_motion_tc(context['at_s'])} to "
        f"{_motion_tc(context['at_s'] + hold_s)}, and returns to the "
        f"previous framing by {_motion_tc(candidate['global_end'])} "
        "(result time)."
    )
    preview_record = {
        "version": _CAMERA_PREVIEW_VERSION,
        "id": preview_id,
        "fingerprint": fingerprint,
        "move_id": move_id,
        "scene": scene_name,
        "scene_id": context["scene_info"].get("id"),
        "slot": slot_name,
        "slot_id": context["slot_info"].get("id"),
        "source": source["id"],
        "arrives_at": arrives_at,
        "timing": timing_echo,
        "timing_sentence": timing_sentence,
        "requested": requested,
        "resolved": resolved_echo,
        "record": zoom_record,
        "images": {
            "selection": selection_out,
            "from": from_out,
            "to": to_out,
            **({"motion": motion_out} if motion_out else {}),
        },
        "apply_command": apply_command,
    }
    preview_record["preview_command"] = _camera_preview_command_text(
        preview_record
    )
    previews_dir = _camera_previews_dir()
    previews_dir.mkdir(parents=True, exist_ok=True)
    (previews_dir / f"{preview_id}.json").write_text(
        json.dumps(preview_record, indent=2) + "\n"
    )

    peer_slots = [
        name
        for name in context["scene_info"]["slots"]
        if name != slot_name
    ]
    result = {
        "status": "camera_preview",
        "writes_spec": False,
        "preview_id": preview_id,
        "scene": scene_name,
        "slot": slot_name,
        "source": source["id"],
        "move_id": move_id,
        "arrives_at": arrives_at,
        "timing": timing_echo,
        "timing_sentence": timing_sentence,
        "window": {
            "result_from": _motion_tc(candidate["global_from"]),
            "result_to": _motion_tc(candidate["global_end"]),
        },
        "requested": requested,
        "resolved": resolved_echo,
        "selection_out": selection_out,
        "from_out": from_out,
        "to_out": to_out,
        **(
            {
                "motion_out": motion_out,
                "motion_note": (
                    "low-resolution preview of the move in, hold, and "
                    "move out; the from/to images are the exact framing"
                ),
            }
            if motion_out
            else {}
        ),
        "changes": {
            "slot": slot_name,
            "unchanged": [
                *(f"slot {name!r}" for name in peer_slots),
                "captions",
                "overlays",
                "audio",
            ],
        },
        "apply_command": apply_command,
        "hint": (
            "Open the from/to images to check the framing (selection "
            "shows your box and the final crop). Adjust --box/--point/"
            "--padding/--place and re-run preview until it looks right, "
            f"then save exactly this move with '{apply_command}'. Nothing "
            "was written to spec.json."
        ),
    }
    _add_warnings(result, warnings)
    click.echo(json.dumps(result, indent=2))


def _camera_followup_commands(move_id: str, preview_command: str | None) -> dict:
    return {
        "list": "moviestar scenes motion camera list",
        "update": (
            f"moviestar scenes motion camera update --id {move_id} "
            "--hold 2.5 (any of --at/--padding/--place/--zoom/"
            "--move-in/--hold/--move-out/--ease)"
        ),
        "remove": f"moviestar scenes motion camera remove --id {move_id}",
        **({"preview": preview_command} if preview_command else {}),
        "undo": "moviestar undo --composition",
    }


@scenes_motion_camera.command("apply")
@project_workspace_option
@click.argument("preview_id")
def scenes_motion_camera_apply(preview_id: str) -> None:
    """Save exactly the camera move that was previewed.

    \b
    apply refuses when the scene, layout, pacing, overlays, captions,
    or sources changed after the preview — the previewed framing could
    no longer match. Re-running the same apply does not create a
    duplicate move.

    The saved record keeps both what you asked for (box/point, padding,
    placement, timing) and the final source rectangle the renderer
    uses, so later "more padding" or "stronger zoom" edits are exact.
    """
    command = "scenes motion camera apply"
    _catalog, canvas, project, spec = _motion_scene_catalog(command)
    record = _load_camera_preview(command, preview_id)
    zoom_record = record.get("record")
    if not isinstance(zoom_record, dict) or not zoom_record.get("id"):
        _error_exit(
            command,
            f"Preview record {preview_id!r} is malformed; re-run "
            "'moviestar scenes motion camera preview'.",
        )
    move_id = zoom_record["id"]
    preview_command = record.get("preview_command")
    stored_motion = spec.get("motion") or {"version": 1, "scenes": []}

    for scene in stored_motion.get("scenes", []):
        for slot in scene.get("slots", []):
            for entry in slot.get("camera", []):
                if entry.get("id") != move_id:
                    continue
                if entry == zoom_record:
                    click.echo(
                        json.dumps(
                            {
                                "status": "already_applied",
                                "writes_spec": False,
                                "preview_id": preview_id,
                                "move_id": move_id,
                                "scene": record.get("scene"),
                                "slot": record.get("slot"),
                                "commands": _camera_followup_commands(
                                    move_id, preview_command
                                ),
                                "hint": (
                                    "This preview is already saved as "
                                    f"camera move {move_id!r}; re-running "
                                    "apply never duplicates a move."
                                ),
                            },
                            indent=2,
                        )
                    )
                    return
                _error_exit_with_hint(
                    command,
                    f"A different camera move with id {move_id!r} already "
                    "exists.",
                    "Change it with 'moviestar scenes motion camera "
                    f"update --id {move_id}' or remove it with 'moviestar "
                    f"scenes motion camera remove --id {move_id}', then "
                    "re-preview.",
                )

    fingerprint = _camera_preview_fingerprint(project, spec)
    if fingerprint != record.get("fingerprint"):
        _error_exit_with_hint(
            command,
            f"The project changed after {preview_id} was created — a "
            "scene, layout, pacing, overlay, caption, or source edit can "
            "move the previewed framing.",
            "Preview again against the current project, check the new "
            "images, then apply the new preview id"
            + (f": {preview_command}" if preview_command else "."),
        )

    candidate_motion = _merge_camera_record(
        stored_motion,
        scene_name=record["scene"],
        scene_id=record.get("scene_id"),
        slot_name=record["slot"],
        slot_id=record.get("slot_id"),
        record=zoom_record,
    )
    try:
        timeline = resolve_pacing(spec["composition"], candidate_motion)
        resolve_camera(
            spec["composition"],
            candidate_motion,
            timeline,
            {
                s["id"]: (int(s["width"]), int(s["height"]))
                for s in project["sources"]
                if s.get("width") and s.get("height")
            },
            canvas,
        )
    except (PacingResolutionError, CameraResolutionError) as exc:
        _error_exit_with_hint(
            command,
            f"The previewed move no longer resolves: {exc}",
            "Re-run the preview against the current project and apply "
            "the new preview id.",
        )

    new_spec = set_motion(spec, candidate_motion)
    save_spec(new_spec, command="scenes motion camera apply")
    result = {
        "status": "camera_move_applied",
        "writes_spec": True,
        "preview_id": preview_id,
        "move_id": move_id,
        "scene": record.get("scene"),
        "slot": record.get("slot"),
        "source": record.get("source"),
        "arrives_at": record.get("arrives_at"),
        "timing": record.get("timing"),
        "resolved": record.get("resolved"),
        "camera_move": zoom_record,
        "changes": {
            "slot": record.get("slot"),
            "unchanged": ["peer slots", "captions", "overlays", "audio"],
        },
        "commands": _camera_followup_commands(move_id, preview_command),
        "hint": (
            f"Saved exactly the previewed move as {move_id!r}. Screenshot, "
            "watch, and export now render it. Adjust with 'moviestar "
            f"scenes motion camera update --id {move_id}', remove with "
            "'... camera remove', or roll back with 'moviestar undo "
            "--composition'."
        ),
    }
    click.echo(json.dumps(result, indent=2))


def _find_stored_camera_record(
    motion: dict, move_id: str
) -> tuple[dict, dict, dict] | None:
    """Return (scene_record, slot_record, camera_record) for a move id."""
    for scene in motion.get("scenes", []):
        for slot in scene.get("slots", []):
            for entry in slot.get("camera", []):
                if entry.get("id") == move_id:
                    return scene, slot, entry
    return None


def _camera_ids_hint(motion: dict) -> str:
    ids = sorted(
        entry["id"]
        for scene in motion.get("scenes", [])
        for slot in scene.get("slots", [])
        for entry in slot.get("camera", [])
        if entry.get("id")
    )
    if not ids:
        return (
            "No camera moves are stored yet. Author one with 'moviestar "
            "scenes motion camera preview' then 'camera apply'."
        )
    return "Stored camera moves: " + ", ".join(ids) + "."


@scenes_motion_camera.command("list")
@project_workspace_option
def scenes_motion_camera_list() -> None:
    """List stored camera moves with exact follow-up commands.

    Shows both incremental zoom records (kind "zoom") and plain moves
    authored through the bulk 'scenes motion set' surface.
    """
    command = "scenes motion camera list"
    catalog, _canvas, _project, spec = _motion_scene_catalog(command)
    motion = spec.get("motion") or {"version": 1, "scenes": []}
    try:
        timeline = resolve_pacing(spec["composition"], motion)
    except PacingResolutionError as exc:
        _error_exit(command, f"Could not resolve scene pacing: {exc}")
    scene_starts = {scene.name: scene.start_s for scene in timeline.scenes}

    moves: list[dict] = []
    for scene in motion.get("scenes", []):
        scene_name = scene.get("scene")
        scene_start = scene_starts.get(scene_name)
        for slot in scene.get("slots", []):
            slot_name = slot.get("slot")
            slot_info = (
                catalog.get(scene_name, {}).get("slots", {}).get(slot_name, {})
            )
            for entry in slot.get("camera", []):
                move_id = entry.get("id")
                item: dict = {
                    "id": move_id,
                    "kind": entry.get("kind", "move"),
                    "scene": scene_name,
                    "slot": slot_name,
                    "source": slot_info.get("source"),
                }
                if entry.get("kind") == "zoom":
                    local_at = parse_timecode(str(entry["at"]))
                    item["arrives_at"] = {
                        "scene_local": _motion_tc(local_at),
                        **(
                            {"result": _motion_tc(scene_start + local_at)}
                            if scene_start is not None else {}
                        ),
                    }
                    item["timing"] = entry.get("timing")
                    target = entry.get("to", {}).get("target")
                    if (
                        isinstance(target, dict)
                        and slot_info.get("source_width")
                        and slot_info.get("source_height")
                        and target.get("units") == "pixels"
                    ):
                        rect = target.get("rect", {})
                        if rect.get("w") and rect.get("h"):
                            item["zoom"] = round(
                                min(
                                    slot_info["source_width"] / rect["w"],
                                    slot_info["source_height"] / rect["h"],
                                ),
                                3,
                            )
                    authored = entry.get("authored")
                    if authored:
                        item["authored"] = {
                            key: authored[key]
                            for key in (
                                "selection", "padding", "zoom", "place",
                            )
                            if key in authored
                        }
                else:
                    if "range" in entry:
                        item["range"] = entry["range"]
                    elif "at" in entry:
                        item["at"] = entry["at"]
                item["ease"] = entry.get("ease", "in-out")
                item["commands"] = {
                    **(
                        {
                            "update": (
                                "moviestar scenes motion camera update "
                                f"--id {move_id} ..."
                            )
                        }
                        if entry.get("kind") == "zoom" else {}
                    ),
                    "remove": (
                        f"moviestar scenes motion camera remove --id {move_id}"
                    ),
                }
                moves.append(item)

    click.echo(
        json.dumps(
            {
                "status": "camera_moves",
                "writes_spec": False,
                "count": len(moves),
                "moves": moves,
                "hint": (
                    "Adjust a zoom with 'moviestar scenes motion camera "
                    "update --id <id>' (any of --at/--box/--point/--zoom/"
                    "--padding/--place/--move-in/--hold/--move-out/--ease), "
                    "or remove one with '... camera remove --id <id>'. "
                    "Plain records edit through 'scenes motion dump'/'set'."
                    if moves
                    else "No camera moves stored. Author one with "
                    "'moviestar scenes motion camera preview' then "
                    "'camera apply'."
                ),
            },
            indent=2,
        )
    )


@scenes_motion_camera.command("remove")
@project_workspace_option
@click.option("--id", "move_id", required=True, help="Camera move to remove.")
def scenes_motion_camera_remove(move_id: str) -> None:
    """Remove one stored camera move (zoom or plain).

    Peer slots, other camera moves, pacing, captions, overlays, and
    audio are untouched. Undo with 'moviestar undo --composition'.
    """
    command = "scenes motion camera remove"
    _catalog, _canvas, _project, spec = _motion_scene_catalog(command)
    motion = spec.get("motion") or {"version": 1, "scenes": []}
    found = _find_stored_camera_record(motion, move_id)
    if found is None:
        _error_exit_with_hint(
            command,
            f"No camera move named {move_id!r}.",
            _camera_ids_hint(motion),
        )
    new_motion = copy.deepcopy(motion)
    _scene, slot_record, entry = _find_stored_camera_record(
        new_motion, move_id
    )
    slot_record["camera"] = [
        item for item in slot_record["camera"] if item.get("id") != move_id
    ]
    new_spec = set_motion(spec, new_motion)
    save_spec(new_spec, command="scenes motion camera remove")
    click.echo(
        json.dumps(
            {
                "status": "camera_move_removed",
                "writes_spec": True,
                "move_id": move_id,
                "scene": _scene.get("scene"),
                "slot": slot_record.get("slot"),
                "removed": entry,
                "hint": (
                    f"Removed camera move {move_id!r}; every other slot "
                    "and layer is unchanged. Roll back with 'moviestar "
                    "undo --composition'."
                ),
            },
            indent=2,
        )
    )


@scenes_motion_camera.command("update")
@project_workspace_option
@click.option("--id", "move_id", required=True, help="Zoom move to change.")
@click.option(
    "--at",
    "timecode",
    default=None,
    help="New result timecode where the camera is fully zoomed "
    "(stays within the move's scene). The move-in runs BEFORE this "
    "moment.",
)
@click.option("--box", "box_arg", default=None, help="New left,top,right,bottom box.")
@click.option("--point", "point_arg", default=None, help="New x,y center of attention.")
@click.option(
    "--zoom", "zoom_arg", type=float, default=None,
    help="New zoom amount (point selections).",
)
@click.option(
    "--units", "units_flag", default=None,
    help="Force 'pixels' or 'normalized' for --box/--point.",
)
@click.option("--padding", "padding_arg", default=None, help="New padding ('20%' or 0.2).")
@click.option("--place", "place_arg", default=None, help="New x,y placement inside the crop.")
@click.option("--move-in", "move_in_arg", type=float, default=None, help="New move-in seconds.")
@click.option("--hold", "hold_arg", type=float, default=None, help="New hold seconds.")
@click.option("--move-out", "move_out_arg", type=float, default=None, help="New move-out seconds.")
@click.option("--ease", "ease_arg", default=None, help="New easing: linear, in, out, in-out.")
def scenes_motion_camera_update(
    move_id: str,
    timecode: str | None,
    box_arg: str | None,
    point_arg: str | None,
    zoom_arg: float | None,
    units_flag: str | None,
    padding_arg: str | None,
    place_arg: str | None,
    move_in_arg: float | None,
    hold_arg: float | None,
    move_out_arg: float | None,
    ease_arg: str | None,
) -> None:
    """Change one stored zoom, keeping everything you don't override.

    \b
    Because the record keeps what you originally asked for (box/point,
    padding, placement), edits like "--padding 25%" or "--zoom 3"
    recompute the exact crop from intent — no coordinates needed.
    The same framing rules as preview apply, and the same warnings
    are reported. Verify with 'moviestar screenshot --at <arrival>'.
    """
    command = "scenes motion camera update"
    _catalog, _canvas, _project, spec_probe = _motion_scene_catalog(command)
    motion = spec_probe.get("motion") or {"version": 1, "scenes": []}
    found = _find_stored_camera_record(motion, move_id)
    if found is None:
        _error_exit_with_hint(
            command,
            f"No camera move named {move_id!r}.",
            _camera_ids_hint(motion),
        )
    scene_record, slot_record, entry = found
    if entry.get("kind") != "zoom":
        _error_exit_with_hint(
            command,
            f"Camera move {move_id!r} is a plain record, not an "
            "incremental zoom.",
            "Edit plain camera records through 'moviestar scenes motion "
            "dump' and 'scenes motion set', or remove this one and "
            "author a zoom with 'camera preview' + 'camera apply'.",
        )
    overrides = [
        timecode, box_arg, point_arg, zoom_arg, padding_arg, place_arg,
        move_in_arg, hold_arg, move_out_arg, ease_arg,
    ]
    if all(value is None for value in overrides):
        _error_exit_with_hint(
            command,
            "Nothing to change.",
            "Pass any of --at/--box/--point/--zoom/--padding/--place/"
            "--move-in/--hold/--move-out/--ease.",
        )

    scene_name = scene_record.get("scene")
    slot_name = slot_record.get("slot")
    stripped_motion = copy.deepcopy(motion)
    _s, stripped_slot, _e = _find_stored_camera_record(
        stripped_motion, move_id
    )
    stripped_slot["camera"] = [
        item
        for item in stripped_slot["camera"]
        if item.get("id") != move_id
    ]

    if timecode is None:
        # Re-anchor at the move's current arrival moment in result time.
        try:
            timeline = resolve_pacing(spec_probe["composition"], motion)
        except PacingResolutionError as exc:
            _error_exit(command, f"Could not resolve scene pacing: {exc}")
        scene_start = next(
            (
                scene.start_s
                for scene in timeline.scenes
                if scene.name == scene_name
            ),
            None,
        )
        if scene_start is None:
            _error_exit(
                command,
                f"Scene {scene_name!r} for move {move_id!r} no longer "
                "exists in the composition.",
            )
        timecode = _motion_tc(
            scene_start + parse_timecode(str(entry["at"]))
        )

    context = _motion_target_context(command, timecode, slot_name)
    if context["scene_name"] != scene_name:
        _error_exit_with_hint(
            command,
            f"--at {timecode} lands in scene "
            f"{context['scene_name']!r}, but {move_id!r} belongs to "
            f"scene {scene_name!r}.",
            "A camera move stays within its scene. Pick an --at inside "
            f"{scene_name!r}, or remove this move and preview a new one "
            "in the other scene.",
        )
    project = context["project"]
    spec = context["spec"]
    source = context["source"]
    width = float(source["width"])
    height = float(source["height"])

    try:
        stripped_plan = resolve_camera(
            spec["composition"],
            stripped_motion,
            context["timeline"],
            {
                s["id"]: (int(s["width"]), int(s["height"]))
                for s in project["sources"]
                if s.get("width") and s.get("height")
            },
            context["canvas"],
        )
    except CameraResolutionError as exc:
        _error_exit(command, f"Could not resolve scene camera: {exc}")

    authored = entry.get("authored") or {}
    if box_arg is not None and point_arg is not None:
        _error_exit(
            command,
            "Use exactly one of --box or --point.",
        )
    if box_arg is not None or point_arg is not None:
        selection = _parse_zoom_selection(
            command,
            box_arg=box_arg,
            point_arg=point_arg,
            units_flag=units_flag,
            width=width,
            height=height,
        )
        box_pixels = selection["box_pixels"]
        point_pixels = selection["point_pixels"]
        units = selection["units"]
        units_inferred = selection["units_inferred"]
    elif authored.get("selection") == "point":
        point = authored.get("point_pixels") or []
        box_pixels = None
        point_pixels = (float(point[0]), float(point[1]))
        units = authored.get("units", "pixels")
        units_inferred = False
    elif authored.get("selection") == "box" and authored.get("box_pixels"):
        box_pixels = {
            key: float(value)
            for key, value in authored["box_pixels"].items()
        }
        point_pixels = None
        units = authored.get("units", "pixels")
        units_inferred = False
    else:
        # Bulk-authored zoom without intent: the stored crop is already
        # aspect-matched, so treating it as a zero-padding box keeps the
        # framing identical unless geometry flags change it.
        target = entry.get("to", {}).get("target")
        rect = target.get("rect") if isinstance(target, dict) else None
        if not rect:
            _error_exit(
                command,
                f"Camera move {move_id!r} has no stored selection or "
                "crop to recompute from.",
            )
        if target.get("units") == "normalized":
            rect = {
                "x": rect["x"] * width,
                "y": rect["y"] * height,
                "w": rect["w"] * width,
                "h": rect["h"] * height,
            }
        box_pixels = rect
        point_pixels = None
        units = "pixels"
        units_inferred = False
        if padding_arg is None:
            authored = {**authored, "padding": 0.0}

    zoom = zoom_arg
    if point_pixels is not None and zoom is None:
        zoom = authored.get("zoom")
        if zoom is None:
            _error_exit(
                command,
                "--point needs --zoom to say how far to zoom in.",
            )
    if box_pixels is not None and zoom_arg is not None:
        _error_exit_with_hint(
            command,
            "--zoom applies to --point selections only.",
            "A box's size plus --padding controls how far a box zooms.",
        )
    if padding_arg is not None:
        padding = _parse_padding(command, padding_arg)
    else:
        padding = authored.get("padding", CAMERA_ZOOM_DEFAULT_PADDING)
    if place_arg is not None:
        place_values = _parse_float_list(
            command, "--place", place_arg, 2,
            "normalized x,y position inside the crop",
        )
        place = (place_values[0], place_values[1])
    elif authored.get("place"):
        place = (
            float(authored["place"][0]),
            float(authored["place"][1]),
        )
    else:
        place = None

    stored_timing = entry.get("timing") or {}
    move_in_s = (
        move_in_arg
        if move_in_arg is not None
        else float(stored_timing.get("move_in", CAMERA_ZOOM_DEFAULT_MOVE_S))
    )
    hold_s = (
        hold_arg
        if hold_arg is not None
        else float(stored_timing.get("hold", CAMERA_ZOOM_DEFAULT_HOLD_S))
    )
    move_out_s = (
        move_out_arg
        if move_out_arg is not None
        else float(stored_timing.get("move_out", move_in_s))
    )
    for label, value in (
        ("--move-in", move_in_s),
        ("--hold", hold_s),
        ("--move-out", move_out_s),
    ):
        if value <= 0:
            _error_exit(command, f"{label} must be greater than 0 seconds")
    ease = ease_arg if ease_arg is not None else entry.get("ease", "in-out")
    if ease not in MOTION_EASES - {"cut"}:
        _error_exit(command, "--ease must be one of: in, in-out, linear, out")

    try:
        crop = resolve_selection_crop(
            source=(width, height),
            region=context["scene_info"]["regions"][slot_name],
            box_pixels=box_pixels,
            point_pixels=point_pixels,
            zoom=zoom if point_pixels is not None else None,
            padding=padding if box_pixels is not None else 0.0,
            place=place,
        )
    except CameraResolutionError as exc:
        _error_exit_with_hint(
            command,
            str(exc),
            "Coordinates come from 'moviestar scenes motion target "
            f"--at {timecode} --slot {slot_name}' "
            f"({int(width)}x{int(height)} pixels, origin top-left).",
        )
    if box_pixels is not None:
        requested = {
            "selection": "box",
            "box_pixels": {k: round(v, 2) for k, v in box_pixels.items()},
            "padding": padding,
            "units": units,
            "units_inferred": units_inferred,
        }
    else:
        requested = {
            "selection": "point",
            "point_pixels": [round(v, 2) for v in point_pixels],
            "zoom": zoom,
            "units": units,
            "units_inferred": units_inferred,
        }
    if place is not None:
        requested["place"] = list(place)

    candidate = _build_zoom_candidate(
        command,
        context,
        crop=crop,
        requested=requested,
        place=place,
        move_in_s=move_in_s,
        hold_s=hold_s,
        move_out_s=move_out_s,
        ease=ease,
        move_id=move_id,
        base_motion=stripped_motion,
        camera_plan=stripped_plan,
    )
    new_spec = set_motion(spec, candidate["candidate_motion"])
    save_spec(new_spec, command="scenes motion camera update")
    arrival = _motion_tc(context["at_s"])
    result = {
        "status": "camera_move_updated",
        "writes_spec": True,
        "move_id": move_id,
        "scene": scene_name,
        "slot": slot_name,
        "arrives_at": {
            "result": arrival,
            "scene_local": _motion_tc(context["scene_local_s"]),
            "source": _motion_tc(context["source_s"]),
        },
        "timing": {
            "move_in": round(move_in_s, 3),
            "hold": round(hold_s, 3),
            "move_out": round(move_out_s, 3),
            "ease": ease,
        },
        "requested": requested,
        "resolved": _zoom_resolved_echo(
            crop, slot_name, candidate["selection_canvas"]
        ),
        "camera_move": candidate["zoom_record"],
        "commands": _camera_followup_commands(move_id, None),
        "hint": (
            f"Updated camera move {move_id!r} from its stored intent. "
            f"Verify visually with 'moviestar screenshot --at {arrival}' "
            "or roll back with 'moviestar undo --composition'."
        ),
    }
    _add_warnings(result, candidate["warnings"])
    click.echo(json.dumps(result, indent=2))


@scenes_motion.command("set")
@project_workspace_option
@click.argument("motion_file", type=click.Path())
@click.option(
    "--dry-run",
    is_flag=True,
    help=(
        "Validate and resolve the motion plan without writing spec.json."
    ),
)
def scenes_motion_set(motion_file: str, dry_run: bool) -> None:
    """Replace the slot motion plan from an editable JSON file.

    \b
    motion.json shape ('scenes motion dump' writes a skeleton):

    \b
      {
        "version": 1,
        "scenes": [{
          "scene": "walkthrough",
          "slots": [{
            "slot": "main",
            "pacing": [
              {"mode": "speed", "speed": 5.0,
               "range": {"from": "0:18", "to": "0:28",
                         "space": "source-local"}},
              {"mode": "duration", "duration": "0:04",
               "range": {"from": "0:18", "to": "0:38"}},
              {"mode": "hold", "at": "0:30", "duration": "0:02"}
            ],
            "camera": [
              {"range": {"from": "0:03", "to": "0:06",
                         "space": "result-local"},
               "from": {"target": "full"},
               "to": {"target": {"space": "source", "units": "pixels",
                      "rect": {"x": 346, "y": 130, "w": 806, "h": 346}}},
               "ease": "in-out"}
            ]
          }]
        }]
      }

    \b
    Omitted slot pacing is unbound: MovieStar calculates the matching
    speed, duration, or hold so every slot in a scene keeps the same
    resolved duration. A camera move with "at" instead of "range" runs
    for 0.5s with in-out easing; "ease": "cut" switches instantly. The
    camera stays at its last state until another move; there is no
    automatic return to full-frame.

    Source-local 0:00 is the slot's source_from. Result-local 0:00 is
    the authored scene's start after pacing.

    """
    command = "scenes motion set"
    catalog, canvas, project, spec = _motion_scene_catalog(command)
    try:
        motion_scenes, assigned_ids = _load_motion_plan(motion_file, catalog)
    except _SceneFileSchemaError as exc:
        _motion_validation_exit(command, motion_file, exc.errors)
    coverage_warning = _motion_coverage_warning(motion_scenes, catalog)

    motion_document = _motion_document_from_normalized(motion_scenes)
    try:
        timeline = resolve_pacing(spec["composition"], motion_document)
    except PacingResolutionError as exc:
        _motion_validation_exit(
            command,
            motion_file,
            [_scene_file_error("scenes", str(exc))],
        )

    try:
        old_motion = spec.get("motion") or {"version": 1, "scenes": []}
        old_timeline = resolve_pacing(spec["composition"], old_motion)
        motion_document, camera_rebases = rebase_camera_ranges(
            old_motion,
            motion_document,
            old_timeline,
            timeline,
        )
        _apply_rebased_camera_times(motion_scenes, motion_document)
    except (PacingResolutionError, CameraResolutionError) as exc:
        _motion_validation_exit(
            command,
            motion_file,
            [_scene_file_error("scenes", str(exc))],
        )

    duration_by_scene = {scene.name: scene.duration_s for scene in timeline.scenes}
    camera_bounds_errors: list[dict[str, str]] = []
    for scene in motion_scenes:
        scene_duration_s = duration_by_scene[scene["scene"]]
        for slot in scene["slots"]:
            for entry in slot["camera"]:
                if entry["end"] > scene_duration_s + 0.001:
                    zoom_fix = (
                        " The zoom keeps its viewer-facing move/hold "
                        "durations across pacing changes; this scene no "
                        "longer has room for them. Smallest fix: shorten "
                        "timing.hold/move_out, or re-author with "
                        "'moviestar scenes motion camera preview'."
                        if entry.get("kind") == "zoom"
                        else ""
                    )
                    camera_bounds_errors.append(
                        _scene_file_error(
                            entry["path"],
                            f"camera move ends at {_motion_tc(entry['end'])} but "
                            f"paced scene {scene['scene']!r} ends at "
                            f"{_motion_tc(scene_duration_s)} result-local."
                            + zoom_fix,
                        )
                    )
    if camera_bounds_errors:
        _motion_validation_exit(command, motion_file, camera_bounds_errors)

    try:
        camera_plan = resolve_camera(
            spec["composition"],
            motion_document,
            timeline,
            {
                source["id"]: (int(source["width"]), int(source["height"]))
                for source in project["sources"]
                if source.get("width") and source.get("height")
            },
            canvas,
        )
    except CameraResolutionError as exc:
        _motion_validation_exit(
            command,
            motion_file,
            [_scene_file_error("scenes", str(exc))],
        )

    motion_echo, resolved_preview, warnings = _motion_preview(
        motion_scenes, catalog, timeline, camera_plan
    )
    if coverage_warning is not None:
        warnings.insert(0, coverage_warning)
    new_spec = set_motion(spec, {"version": 1, "scenes": motion_echo})
    pacing_count = sum(
        len(slot["pacing"]) for scene in motion_scenes for slot in scene["slots"]
    )
    camera_count = sum(
        len(slot["camera"]) for scene in motion_scenes for slot in scene["slots"]
    )
    result = {
        "status": "would_set_motion" if dry_run else "set_motion",
        "writes_spec": not dry_run,
        "requested_dry_run": dry_run,
        "motion_model": "scene_slot_motion",
        "motion_file": os.path.realpath(motion_file),
        "scenes_count": len(motion_scenes),
        "pacing_count": pacing_count,
        "camera_count": camera_count,
        "assigned_ids": assigned_ids,
        "camera_rebases": camera_rebases,
        "time_defaults": MOTION_TIME_DEFAULTS,
        "motion": motion_echo,
        "resolved_preview": resolved_preview,
        "hint": (
            "Dry run only: nothing was written. Remove --dry-run to replace "
            "the stored motion plan."
            if dry_run
            else "Motion was stored atomically in spec.json. Screenshot, "
            "inspect, watch, and export now render the same resolved pacing "
            "and animated camera plan."
        ),
    }
    if not dry_run:
        save_spec(new_spec, command="scenes motion set")
    _add_warnings(result, warnings)
    _attach_overlay_timeline_mutation(
        result,
        project=project,
        old_spec=spec,
        new_spec=new_spec,
        code="motion_result_clock_changed_overlays_stale",
        change_label="motion change",
    )
    click.echo(json.dumps(result, indent=2))


@scenes_motion.command("dump")
@project_workspace_option
@click.option(
    "--out",
    "out_path",
    default="motion.json",
    help="Where to write the editable motion JSON (default: motion.json).",
)
def scenes_motion_dump(out_path: str) -> None:
    """Write an editable motion JSON skeleton for the current scenes.

    \b
    The skeleton lists every scene and slot in the current composition
    with empty "pacing" and "camera" arrays. Splice in entries — the
    response carries copyable speed / duration / hold and zoom / pan /
    return-to-full examples — then validate with
    'moviestar scenes motion set motion.json --dry-run'.

    """
    command = "scenes motion dump"
    catalog, _canvas, _project, spec = _motion_scene_catalog(command)
    skeleton = {
        "version": 1,
        "scenes": [
            {
                "scene": scene_name,
                **(
                    {"scene_id": scene_info["id"]}
                    if scene_info.get("id") else {}
                ),
                "slots": [
                    {
                        "slot": slot_name,
                        **(
                            {"slot_id": slot_info["id"]}
                            if slot_info.get("id") else {}
                        ),
                        **(
                            {"source": slot_info["source"]}
                            if slot_info.get("source") else {}
                        ),
                        "source_extent": _motion_range_echo(
                            0,
                            slot_info["duration"],
                            "source-local",
                        ),
                        "pacing": [],
                        "camera": [],
                    }
                    for slot_name, slot_info in scene_info["slots"].items()
                ],
            }
            for scene_name, scene_info in catalog.items()
        ],
        "_examples": _MOTION_EXAMPLE,
    }
    stored_motion = spec.get("motion") or {"version": 1, "scenes": []}
    template = not bool(stored_motion.get("scenes"))
    dumped_motion = _materialize_motion_dump(skeleton, stored_motion)
    abs_out = os.path.realpath(out_path)
    try:
        Path(abs_out).write_text(json.dumps(dumped_motion, indent=2) + "\n")
    except OSError as exc:
        _error_exit(command, f"could not write {abs_out}: {exc}")

    click.echo(
        json.dumps(
            {
                "status": "dumped_motion",
                "writes_spec": False,
                "template": template,
                "out": abs_out,
                "scenes": list(catalog),
                "time_defaults": MOTION_TIME_DEFAULTS,
                "example": _MOTION_EXAMPLE,
                "hint": (
                    "Splice example entries into the skeleton (edit values "
                    "to your content), then validate with 'moviestar scenes "
                    "motion set "
                    f"{os.path.basename(abs_out)} --dry-run'. Pacing ranges "
                    "are source-local; camera ranges are result-local."
                ),
            },
            indent=2,
        )
    )


# --- M21 schema/resolver: result-time multi-track audio ---
#
# Audio intent persists in spec.json and every read re-probes external media
# against the current finished-video duration. The shared mixer consumes the
# complete authored graph, including signal-driven sidechain ducking.

AUDIO_SCHEMA_RESOLVER_NOTE = (
    "Audio intent persists in spec.json and the FFmpeg mixer renders "
    "source/external gain, mute, fades, placement, looping, and signal-driven "
    "ducking. An explicit final limiter protects the sum before optional "
    "whole-mix loudness normalization."
)


def _audio_surface_context(command: str) -> tuple[dict, dict, float, Path]:
    if not is_loaded():
        _no_project_error_exit(command)
    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit(command, f"Could not read project.json: {exc}")
    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit(command, str(exc))

    try:
        result_duration = resolve_project(project, spec).duration_s
    except ResolvedProjectError:
        _error_exit_with_hint(
            command,
            "Audio tracks need one unambiguous finished-video timeline, "
            "but this multi-source project has no composition.",
            "Run 'moviestar scenes set' or 'moviestar concat' to build the "
            "visual timeline, then retry audio authoring.",
        )
    except PacingResolutionError as exc:
        _error_exit(command, f"Could not resolve scene pacing: {exc}")

    if result_duration <= 0:
        _error_exit_with_hint(
            command,
            "The current finished-video timeline has zero duration.",
            "Add source material to the edit or composition, then retry.",
        )
    return project, spec, result_duration, get_project_dir()


def _audio_probe_file(path: str) -> dict:
    try:
        probe = run_ffprobe(path)
    except FileNotFoundError:
        raise ValueError(f"file not found: {path}") from None
    except FFmpegNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    except (RuntimeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not probe {path}: {exc}") from exc
    audio_stream = next(
        (
            stream
            for stream in probe.get("streams", [])
            if stream.get("codec_type") == "audio"
        ),
        None,
    )
    if audio_stream is None:
        raise ValueError(f"{path} has no audio stream")
    duration_raw = probe.get("format", {}).get("duration")
    duration = float(duration_raw) if duration_raw else 0.0
    if duration <= 0:
        duration_raw = audio_stream.get("duration")
        duration = float(duration_raw) if duration_raw else 0.0
    sample_rate = audio_stream.get("sample_rate")
    return {
        "duration_seconds": duration,
        "codec": audio_stream.get("codec_name"),
        "sample_rate": int(sample_rate) if sample_rate else None,
        "channels": audio_stream.get("channels"),
    }


def _audio_surface_validation_exit(
    command: str, errors: list[dict[str, str]], *, source_file: str | None = None
) -> None:
    if len(errors) == 1:
        message = f"{errors[0]['path']}: {errors[0]['error']}"
    else:
        message = f"{len(errors)} validation errors in the audio mix."
    details: dict = {"errors": errors}
    if source_file is not None:
        details["audio_file"] = os.path.realpath(source_file)
    _error_exit_with_hint(
        command,
        message,
        "Fix the listed JSON paths or flags, then validate with 'moviestar "
        "audio set <file> --dry-run'. Run 'moviestar audio dump --out "
        "audio.json' for the canonical editable shape.",
        **details,
    )


def _resolve_project_audio_mix(
    command: str,
    spec: dict,
    result_duration: float,
    project_dir: Path,
) -> dict:
    try:
        return resolve_audio_mix(
            spec["audio_mix"],
            result_duration=result_duration,
            base_dir=project_dir.parent,
            probe_audio=_audio_probe_file,
        )
    except AudioSurfaceValidationError as exc:
        _audio_surface_validation_exit(command, exc.errors)


def _audio_summary(audio_mix: dict) -> dict:
    result = audio_mix_summary(audio_mix)
    result["timeline_space"] = "finished-video result time"
    result["schema_resolver_note"] = AUDIO_SCHEMA_RESOLVER_NOTE
    result["renders_audio_mix"] = True
    result["renderer_status"] = "complete"
    return result


def _audio_mutation_envelope(
    *,
    status: str,
    audio_mix: dict,
    writes_spec: bool,
    hint: str,
    **extra,
) -> dict:
    return {
        "status": status,
        "schema_resolver_note": AUDIO_SCHEMA_RESOLVER_NOTE,
        "writes_spec": writes_spec,
        "renders_audio_mix": True,
        "renderer_status": "complete",
        "audio_mix": _audio_summary(audio_mix),
        **extra,
        "hint": hint,
    }


def _audio_mix_is_edited(spec: dict) -> bool:
    return spec.get("audio_mix", default_audio_mix()) != default_audio_mix()


def _audio_mix_temp_path(output_path: str) -> str:
    output = Path(output_path)
    suffix = output.suffix or ".mp4"
    return str(output.with_name(f".{output.stem}.moviestar-audio-mix{suffix}"))


_LOUDNESS_REPORT_DRY_RUN_NOTE = (
    "Loudness is measured from the real rendered mix, so --dry-run skips "
    "measurement. Re-run without --dry-run for per-track and final-mix LUFS."
)

_LOUDNESS_REPORT_HINT = (
    "Compare tracks[].loudness.integrated_lufs (LUFS; higher is louder). "
    "A music bed within ~10 LU of speech competes with it. Lower a layer "
    "with a more negative gain_db via 'moviestar audio source/add/set', "
    "or duck it under speech with --duck-under."
)


def _finite_or_none(value) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _loudness_values_envelope(metrics: dict) -> dict:
    loudness = {
        key: _finite_or_none(metrics.get(key))
        for key in (
            "integrated_lufs", "true_peak_dbtp", "lra_lu", "threshold_lufs",
        )
    }
    integrated = loudness["integrated_lufs"]
    loudness["silent"] = integrated is None or integrated <= -70.0
    return loudness


def _loudness_window_envelope(
    result_duration: float,
    window_start: float,
    window_duration: float | None,
) -> dict:
    if window_duration is None:
        window_duration = result_duration - window_start
    return {
        "from": format_timecode(window_start),
        "to": format_timecode(window_start + window_duration),
        "duration": format_timecode(window_duration),
    }


def _build_audio_loudness_report(
    *,
    base_path: str,
    final_path: str,
    resolved: dict,
    result_duration: float,
    window_start: float,
    window_duration: float | None,
    source_audio_available: bool,
) -> dict:
    """Measure per-layer and final-mix loudness for one rendered result.

    Per-layer numbers come from the shared mix filtergraph (see
    ``build_layer_loudness_probe_command``), so they reflect gain, fades,
    looping, placement, and ducking exactly as rendered. The final mix is
    probed from the rendered file itself — post limiter and post optional
    whole-mix normalization. Every routed layer appears in the report;
    layers with no signal carry an explicit ``excluded_reason``.
    """
    if window_duration is None:
        window_duration = result_duration - window_start
    window_end = window_start + window_duration
    window = _loudness_window_envelope(
        result_duration, window_start, window_duration
    )

    tracks = resolved.get("tracks", [])
    if not source_audio_available and not tracks:
        return {
            "measured": True,
            "window": window,
            "tracks": [],
            "final_mix": None,
            "note": "The rendered result has no audio stream to measure.",
        }

    source = resolved.get("source_audio") or {}
    entries: list[dict] = []
    to_measure: list[tuple[dict, str, float, float]] = []

    source_entry: dict = {
        "id": "source",
        "kind": "source_audio",
        "gain_db": float(source.get("gain_db", 0.0)),
        "muted": bool(source.get("muted", False)),
        "ducking": None,
    }
    if not source_audio_available:
        source_entry["loudness"] = None
        source_entry["excluded_reason"] = "no_audio_stream"
    elif source_entry["muted"]:
        source_entry["loudness"] = None
        source_entry["excluded_reason"] = "muted"
    else:
        to_measure.append((source_entry, "source", window_start, window_end))
    entries.append(source_entry)

    for track in tracks:
        ducking = track.get("ducking")
        entry: dict = {
            "id": track["id"],
            "kind": track["kind"],
            "gain_db": float(track.get("gain_db", 0.0)),
            "muted": bool(track.get("muted", False)),
            "loop": bool(track.get("loop", False)),
            "ducking": (
                {"under": list(ducking["under"]), "preset": ducking["preset"]}
                if ducking
                else None
            ),
        }
        if entry["muted"]:
            entry["loudness"] = None
            entry["excluded_reason"] = "muted"
        else:
            placed = track["placed_range"]
            overlap_from = max(window_start, float(placed["from"]["seconds"]))
            overlap_to = min(window_end, float(placed["to"]["seconds"]))
            if overlap_to <= overlap_from + 0.0000001:
                entry["loudness"] = None
                entry["excluded_reason"] = "outside_window"
            else:
                to_measure.append((entry, track["id"], overlap_from, overlap_to))
        entries.append(entry)

    for entry, layer_id, cover_from, cover_to in to_measure:
        metrics, _cmd = run_layer_loudness_probe(
            base_path,
            resolved,
            result_duration,
            source_audio_available=source_audio_available,
            layer_id=layer_id,
            window_start=window_start,
            window_duration=window_duration,
        )
        entry["measured_stage"] = "post_processing_pre_final_mix"
        entry["coverage"] = {
            "from": format_timecode(cover_from),
            "to": format_timecode(cover_to),
            "duration": format_timecode(cover_to - cover_from),
            "fraction_of_window": round(
                (cover_to - cover_from) / window_duration, 4
            ),
        }
        entry["loudness"] = _loudness_values_envelope(metrics)

    if to_measure:
        final_metrics, _cmd = run_loudness_probe(final_path)
        final_mix = {
            "measured_stage": "as_rendered",
            "loudness": _loudness_values_envelope(final_metrics),
        }
        note = None
    else:
        final_mix = None
        note = (
            "No routed layer is audible (every layer is muted, silent, or "
            "outside the rendered window); the result has no audio to measure."
        )

    report = {
        "measured": True,
        "window": window,
        "tracks": entries,
        "final_mix": final_mix,
        "hint": _LOUDNESS_REPORT_HINT,
    }
    if note is not None:
        report["note"] = note
    return report


def _audio_loudness_report_or_error(**kwargs) -> dict:
    """Never fail a completed render because measurement failed."""
    try:
        return _build_audio_loudness_report(**kwargs)
    except (
        FFmpegNotFoundError, FileNotFoundError, RuntimeError, ValueError
    ) as exc:
        return {
            "measured": False,
            "window": _loudness_window_envelope(
                kwargs["result_duration"],
                kwargs["window_start"],
                kwargs["window_duration"],
            ),
            "error": f"Loudness measurement failed: {exc}",
            "note": "The render completed; only the loudness report failed.",
        }


def _attach_audio_schema_envelope(
    result: dict,
    *,
    spec: dict,
    result_duration: float,
    command: str,
    source_audio_available: bool,
    window_start: float = 0.0,
    window_duration: float | None = None,
    resolved_audio_mix: dict | None = None,
    loudness_report: bool = False,
) -> None:
    resolved = resolved_audio_mix or _resolve_project_audio_mix(
        command, spec, result_duration, get_project_dir()
    )
    edited = _audio_mix_is_edited(spec)
    result["audio_mix"] = _audio_summary(resolved)
    ducking_targets = [
        {
            "id": track["id"],
            "sidechains": list(track["ducking"]["under"]),
            "preset": track["ducking"]["preset"],
            "resolved": dict(track["ducking"]["resolved"]),
        }
        for track in resolved.get("tracks", [])
        if track.get("ducking")
    ]
    if ducking_targets:
        result["audio_ducking"] = {
            "targets": ducking_targets,
            "filter_order": [
                "layer_gain_and_fades",
                "combine_sidechains",
                "sidechaincompress",
                "final_mix",
            ],
            "signal_driven": True,
        }
    result["audio_mix_default_passthrough_equivalent"] = not edited
    result["audio_working_format"] = {
        "sample_rate_hz": AUDIO_MIX_SAMPLE_RATE,
        "channel_layout": AUDIO_MIX_CHANNEL_LAYOUT,
        "sample_format": AUDIO_MIX_SAMPLE_FORMAT,
    }
    if not edited:
        result["audio_mix_applied_to_ffmpeg_command"] = False
        if loudness_report:
            if result.get("dry_run"):
                result["audio_loudness"] = {
                    "measured": False,
                    "window": _loudness_window_envelope(
                        result_duration, window_start, window_duration
                    ),
                    "note": _LOUDNESS_REPORT_DRY_RUN_NOTE,
                }
            else:
                # Passthrough mix: the rendered output *is* the base, so
                # both the source layer and the final mix measure from it.
                out_path = result.get("out")
                result["audio_loudness"] = _audio_loudness_report_or_error(
                    base_path=out_path,
                    final_path=out_path,
                    resolved=resolved,
                    result_duration=result_duration,
                    window_start=window_start,
                    window_duration=window_duration,
                    source_audio_available=source_audio_available,
                )
        return

    render_path = (
        result.get("out")
        or result.get("would_render_to")
        or result.get("would_extract_to")
    )
    if not isinstance(render_path, str):
        raise ValueError("audio mix envelope requires a render output path")
    mixed_path = _audio_mix_temp_path(render_path)
    try:
        audio_command = build_mix_audio_command(
            render_path,
            mixed_path,
            resolved,
            result_duration,
            source_audio_available=source_audio_available,
            window_start=window_start,
            window_duration=window_duration,
        )
    except ValueError as exc:
        _error_exit(command, str(exc))

    result["audio_mix_command"] = audio_command
    result["audio_mix_applied_to_ffmpeg_command"] = True
    result["audio_mix_stage"] = "post_render_remux"
    result["audio_mix_safety"] = {
        "limiter": {
            "filter": "alimiter",
            "always_on": True,
            "ceiling_dbfs": AUDIO_MIX_LIMITER_CEILING_DBFS,
            "limit_linear": round(AUDIO_MIX_LIMITER_LIMIT, 6),
            "attack_ms": AUDIO_MIX_LIMITER_ATTACK_MS,
            "release_ms": AUDIO_MIX_LIMITER_RELEASE_MS,
            "auto_level": False,
            "latency_compensated": True,
        }
    }
    filter_order = []
    if result.get("audio_join_fade") is not None:
        filter_order.append("source_join_fades")
    filter_order.append("layer_gain_and_fades")
    if ducking_targets:
        filter_order.extend(["combine_sidechains", "sidechaincompress"])
    filter_order.extend(["final_mix", "final_limiter"])
    result["audio_mix_filter_order"] = filter_order
    result["render_available"] = True
    result.pop("render_blocked", None)
    if result.get("dry_run"):
        result["hint"] += (
            " audio_mix_command is the deterministic post-render remux that "
            "applies the resolved base mix without re-encoding video."
        )
        if loudness_report:
            result["audio_loudness"] = {
                "measured": False,
                "window": _loudness_window_envelope(
                    result_duration, window_start, window_duration
                ),
                "note": _LOUDNESS_REPORT_DRY_RUN_NOTE,
            }
        return

    loudness_report_payload = None
    try:
        if os.path.exists(mixed_path):
            os.remove(mixed_path)
        mix_audio(
            render_path,
            mixed_path,
            resolved,
            result_duration,
            source_audio_available=source_audio_available,
            window_start=window_start,
            window_duration=window_duration,
        )
        if loudness_report:
            # Measure while the pre-mix base still exists at render_path;
            # the per-layer probes rebuild the mix graph from it, and the
            # final mix is probed from the mastered file at mixed_path.
            loudness_report_payload = _audio_loudness_report_or_error(
                base_path=render_path,
                final_path=mixed_path,
                resolved=resolved,
                result_duration=result_duration,
                window_start=window_start,
                window_duration=window_duration,
                source_audio_available=source_audio_available,
            )
        os.replace(mixed_path, render_path)
    except (FFmpegNotFoundError, FileNotFoundError, RuntimeError, ValueError) as exc:
        if os.path.exists(mixed_path):
            os.remove(mixed_path)
        if os.path.exists(render_path):
            os.remove(render_path)
        _error_exit(command, f"Audio mix failed: {exc}")
    result["file_size_bytes"] = os.path.getsize(render_path)
    if loudness_report_payload is not None:
        result["audio_loudness"] = loudness_report_payload


@cli.group("audio", invoke_without_command=True)
@click.pass_context
def audio(ctx: click.Context) -> None:
    """Author source audio, voiceover, and music on the final timeline.

    \b
    Result time is the finished video's clock: 0:00 is the beginning
    after trims, scenes, speed changes, and holds. External audio stays
    on this clock; it does not retime with an underlying video source.

    \b
    Common command-first path:
      audio source --gain-db -8
      audio add narration.wav --as narration --kind voiceover
      audio add music.mp3 --as music --kind music --gain-db -18
                --loop --duck-under narration

    \b
    Complete file path:
      audio dump --out audio.json
      audio set audio.json --dry-run
      audio set audio.json

    Gain is a relative volume adjustment in decibels: 0 dB leaves the
    signal unchanged, negative values turn it down.

    Check the balance without listening: 'moviestar export
    --loudness-report' measures integrated LUFS per routed layer plus
    the final mix, using the exact rendered routing.
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(
        json.dumps(
            {
                "status": "audio_authoring",
                "schema_resolver_note": AUDIO_SCHEMA_RESOLVER_NOTE,
                "writes_spec": False,
                "renders_audio_mix": True,
                "renderer_status": "complete",
                "audio_model": "source_layer_plus_result_time_tracks",
                "commands": [
                    {
                        "command": "moviestar audio source --gain-db -8",
                        "purpose": "turn down or mute routed source audio",
                    },
                    {
                        "command": (
                            "moviestar audio add FILE --as ID --kind "
                            "voiceover|music|other"
                        ),
                        "purpose": "place one local audio file in result time",
                    },
                    {
                        "command": "moviestar audio dump --out audio.json",
                        "purpose": "write the complete editable mix document",
                    },
                    {
                        "command": "moviestar audio set audio.json --dry-run",
                        "purpose": "validate and preview complete replacement",
                    },
                ],
                "timeline_space": "finished-video result time",
                "ducking_presets": sorted(AUDIO_DUCKING_PRESETS),
                "hint": (
                    "Use 'moviestar audio source --help' and 'moviestar audio "
                    "add --help' for the command-first workflow, or start with "
                    "'moviestar audio dump' for the complete file workflow."
                ),
            },
            indent=2,
        )
    )


@audio.command("source")
@project_workspace_option
@click.option(
    "--gain-db",
    type=float,
    default=None,
    help="Relative volume in decibels (0 unchanged; negative turns it down).",
)
@click.option("--mute", "muted", flag_value=True, default=None, help="Mute source audio.")
@click.option("--unmute", "muted", flag_value=False, help="Unmute source audio.")
@click.option("--fade-in", type=float, default=None, help="Fade-in seconds.")
@click.option("--fade-out", type=float, default=None, help="Fade-out seconds.")
def audio_source(
    gain_db: float | None,
    muted: bool | None,
    fade_in: float | None,
    fade_out: float | None,
) -> None:
    """Inspect or adjust the routed source-audio layer.

    This does not choose a camera/source ID. Existing concat and scene
    audio routing first resolve one source-audio stream; this command
    controls that finished stream's volume, mute, and edge fades.
    """
    command = "audio source"
    _project, spec, result_duration, project_dir = _audio_surface_context(command)
    audio_mix = _resolve_project_audio_mix(
        command, spec, result_duration, project_dir
    )
    changed = any(value is not None for value in (gain_db, muted, fade_in, fade_out))
    if not changed:
        click.echo(
            json.dumps(
                _audio_mutation_envelope(
                    status="audio_source",
                    audio_mix=audio_mix,
                    writes_spec=False,
                    hint=(
                        "Pass --gain-db, --mute/--unmute, --fade-in, or "
                        "--fade-out to update this source layer."
                    ),
                ),
                indent=2,
            )
        )
        return

    document = editable_audio_document(spec["audio_mix"])
    source = document["audio_mix"]["source_audio"]
    for key, value in (
        ("gain_db", gain_db),
        ("muted", muted),
        ("fade_in", fade_in),
        ("fade_out", fade_out),
    ):
        if value is not None:
            source[key] = value
    try:
        normalized = normalize_audio_document(
            document,
            result_duration=result_duration,
            base_dir=project_dir.parent,
            probe_audio=_audio_probe_file,
        )
    except AudioSurfaceValidationError as exc:
        _audio_surface_validation_exit(command, exc.errors)
    new_spec = set_audio_mix(spec, persisted_audio_mix(normalized))
    try:
        save_spec(new_spec, command="audio source")
    except SpecValidationError as exc:
        _error_exit(command, str(exc))
    click.echo(
        json.dumps(
            _audio_mutation_envelope(
                status="source_audio_updated",
                audio_mix=normalized,
                writes_spec=True,
                hint=(
                    "Source-audio intent saved to spec.json. Add voiceover/music "
                    "with 'moviestar audio add', or restore this state with "
                    "'moviestar undo --audio'. Watch/export apply this base "
                    "mix, including signal-driven ducking."
                ),
            ),
            indent=2,
        )
    )


@audio.command("add")
@project_workspace_option
@click.argument("audio_file", type=click.Path())
@click.option("--as", "track_id", required=True, help="Stable track ID.")
@click.option("--kind", type=click.Choice(sorted(AUDIO_KINDS)), required=True)
@click.option("--at", "at_tc", default="0", help="Finished-video start time.")
@click.option("--from", "source_from", default="0", help="Start in the audio file.")
@click.option("--to", "source_to", default=None, help="End in the audio file.")
@click.option(
    "--gain-db",
    type=float,
    default=0.0,
    show_default=True,
    help="Relative volume in decibels.",
)
@click.option("--mute", is_flag=True, help="Add the track muted.")
@click.option("--loop", is_flag=True, help="Repeat the selected range to result end.")
@click.option("--fade-in", type=float, default=0.0, show_default=True)
@click.option("--fade-out", type=float, default=0.0, show_default=True)
@click.option(
    "--duck-under",
    multiple=True,
    help="Lower this track while LAYER_ID is audible; repeatable. Use 'source' for routed source audio.",
)
@click.option(
    "--ducking",
    "ducking_preset",
    type=click.Choice(sorted(AUDIO_DUCKING_PRESETS)),
    default=None,
    help=(
        "Ducking preset. 'speech' lowers this track via sidechain compression "
        "at -30 dB threshold, 8:1 ratio, 20 ms attack, and 250 ms release; "
        "it is the default with --duck-under."
    ),
)
def audio_add(
    audio_file: str,
    track_id: str,
    kind: str,
    at_tc: str,
    source_from: str,
    source_to: str | None,
    gain_db: float,
    mute: bool,
    loop: bool,
    fade_in: float,
    fade_out: float,
    duck_under: tuple[str, ...],
    ducking_preset: str | None,
) -> None:
    """Add one local audio file as an independent result-time track.

    --at uses the finished video's clock. --from/--to select material
    inside AUDIO_FILE. A non-looping track ends naturally; --loop repeats
    the selected range to the video end. --duck-under is directional:
    adding it to music means "turn music down while this speech layer is
    audible."

    MovieStar probes and validates the real file, then persists canonical
    intent in spec.json. Watch/export consume gain, mute, fades, placement,
    looping, and signal-driven ducking now.
    """
    command = "audio add"
    _project, spec, result_duration, project_dir = _audio_surface_context(command)
    document = editable_audio_document(spec["audio_mix"])
    ducking = None
    if duck_under or ducking_preset:
        ducking = {
            "under": list(duck_under),
            "preset": ducking_preset or "speech",
        }
    document["audio_mix"]["tracks"].append(
        {
            "id": track_id,
            "kind": kind,
            "path": os.path.realpath(audio_file),
            "at": at_tc,
            "source_from": source_from,
            "source_to": source_to,
            "gain_db": gain_db,
            "muted": mute,
            "loop": loop,
            "fade_in": fade_in,
            "fade_out": fade_out,
            "ducking": ducking,
        }
    )
    try:
        normalized = normalize_audio_document(
            document,
            result_duration=result_duration,
            base_dir=Path.cwd(),
            probe_audio=_audio_probe_file,
        )
    except AudioSurfaceValidationError as exc:
        _audio_surface_validation_exit(command, exc.errors)
    new_spec = set_audio_mix(spec, persisted_audio_mix(normalized))
    try:
        save_spec(new_spec, command="audio add")
    except SpecValidationError as exc:
        _error_exit(command, str(exc))
    added = next(track for track in normalized["tracks"] if track["id"] == track_id)
    click.echo(
        json.dumps(
            _audio_mutation_envelope(
                status="audio_track_added",
                audio_mix=normalized,
                writes_spec=True,
                tracks=[added],
                hint=(
                    "Track intent saved to spec.json. Run 'moviestar status' "
                    "to inspect the resolved mix, "
                    "'moviestar audio dump --out audio.json' for bulk editing, "
                    "or 'moviestar undo --audio' to revert. Watch/export apply "
                    "the complete mix, including signal-driven ducking. Check "
                    "per-track LUFS with 'moviestar export --loudness-report'."
                ),
            ),
            indent=2,
        )
    )


@audio.command("dump")
@project_workspace_option
@click.option("--out", "out_path", default="audio.json", help="Editable JSON path.")
def audio_dump(out_path: str) -> None:
    """Write the complete editable persisted mix to JSON.

    Edit source_audio or tracks, then validate with 'audio set FILE
    --dry-run'. Derived media and placed-range fields are recomputed by set
    and intentionally omitted from this editable document.
    """
    command = "audio dump"
    _project, spec, _duration, _project_dir = _audio_surface_context(command)
    document = editable_audio_document(spec["audio_mix"])
    abs_out = os.path.realpath(out_path)
    try:
        Path(abs_out).write_text(json.dumps(document, indent=2) + "\n")
    except OSError as exc:
        _error_exit(command, f"Could not write {abs_out}: {exc}")
    click.echo(
        json.dumps(
            {
                "status": "dumped_audio_mix",
                "schema_resolver_note": AUDIO_SCHEMA_RESOLVER_NOTE,
                "writes_spec": False,
                "writes_file": True,
                "out": abs_out,
                "tracks_count": len(document["audio_mix"]["tracks"]),
                "hint": (
                    f"Edit {os.path.basename(abs_out)}, then run 'moviestar "
                    f"audio set {os.path.basename(abs_out)} --dry-run' to "
                    "validate before replacing the persisted audio state."
                ),
            },
            indent=2,
        )
    )


@audio.command("set")
@project_workspace_option
@click.argument("audio_file", type=click.Path())
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate and resolve without updating spec.json.",
)
def audio_set(audio_file: str, dry_run: bool) -> None:
    """Validate and replace the complete persisted mix from JSON.

    Relative track paths resolve from the JSON file's directory. Validation
    probes every media file, resolves result-time placement and looping, and
    rejects unknown/cyclic ducking references atomically.
    """
    command = "audio set"
    _project, spec, result_duration, _project_dir = _audio_surface_context(command)
    abs_file = os.path.realpath(audio_file)
    try:
        content = Path(abs_file).read_text()
        document = json.loads(content)
    except FileNotFoundError:
        _error_exit_with_hint(
            command,
            f"Audio document not found: {abs_file}",
            "Run 'moviestar audio dump --out audio.json' to create the "
            "canonical editable shape.",
        )
    except (OSError, json.JSONDecodeError) as exc:
        _error_exit_with_hint(
            command,
            f"Could not parse {abs_file}: {exc}",
            "Fix the JSON or recreate it with 'moviestar audio dump'.",
        )
    try:
        normalized = normalize_audio_document(
            document,
            result_duration=result_duration,
            base_dir=Path(abs_file).parent,
            probe_audio=_audio_probe_file,
        )
    except AudioSurfaceValidationError as exc:
        _audio_surface_validation_exit(command, exc.errors, source_file=abs_file)

    if dry_run:
        status = "would_set_audio_mix"
        writes_spec = False
        hint = (
            "Dry-run only: the complete mix validated and resolved, but "
            "spec.json was unchanged. Re-run without --dry-run to persist it."
        )
    else:
        status = "audio_mix_set"
        writes_spec = True
        new_spec = set_audio_mix(spec, persisted_audio_mix(normalized))
        try:
            save_spec(new_spec, command="audio set")
        except SpecValidationError as exc:
            _error_exit(command, str(exc))
        hint = (
            "Audio mix replaced atomically in spec.json. Run 'moviestar "
            "status' to inspect it or 'moviestar undo --audio' to restore the "
            "prior complete mix. Watch/export render the complete graph."
        )
    click.echo(
        json.dumps(
            _audio_mutation_envelope(
                status=status,
                audio_mix=normalized,
                writes_spec=writes_spec,
                requested_dry_run=dry_run,
                audio_file=abs_file,
                hint=hint,
            ),
            indent=2,
        )
    )


OVERLAY_STYLE_PRESETS = {
    "caption-default": {
        "font_family": "Inter",
        "font_size": 64,
        "font_weight": "bold",
        "color": "#ffffff",
        "stroke_color": "#000000",
        "stroke_width": 4,
        "text_align": "center",
        "max_width": "86%",
    },
    "social-bold": {
        "font_family": "Inter",
        "font_size": 72,
        "font_weight": "bold",
        "color": "#ffffff",
        "stroke_color": "#000000",
        "stroke_width": 5,
        "text_align": "center",
        "max_width": "88%",
    },
    "title": {
        "font_family": "Inter",
        "font_size": 96,
        "font_weight": "bold",
        "color": "#ffffff",
        "stroke_color": "#000000",
        "stroke_width": 3,
        "text_align": "center",
        "max_width": "90%",
    },
    "lower-third": {
        "font_family": "Inter",
        "font_size": 42,
        "font_weight": "bold",
        "color": "#ffffff",
        "background_color": "#000000",
        "background_opacity": 0.65,
        "text_align": "left",
        "padding": "18px 24px",
    },
}

OVERLAY_CSS_PROPERTIES = {
    "font-family",
    "font-size",
    "font-weight",
    "color",
    "background-color",
    "opacity",
    "text-align",
    "line-height",
    "letter-spacing",
    "max-width",
    "padding",
    "border-radius",
    "text-transform",
    "text-shadow",
    "transform",
    "transform-origin",
    "-moviestar-stroke",
    "-moviestar-highlight-color",
}


def _css_to_key(prop: str) -> str:
    if prop == "-moviestar-stroke":
        return "stroke"
    if prop == "-moviestar-highlight-color":
        return "highlight_color"
    return prop.replace("-", "_")


def _parse_overlay_css(css: str | None) -> dict:
    if css is None or not css.strip():
        return {}
    resolved: dict = {}
    unknown: list[str] = []
    for declaration in css.split(";"):
        declaration = declaration.strip()
        if not declaration:
            continue
        if ":" not in declaration:
            raise ValueError(
                f"Invalid CSS declaration {declaration!r}. Use property: value."
            )
        prop, value = declaration.split(":", 1)
        prop = prop.strip().lower()
        value = value.strip()
        if prop not in OVERLAY_CSS_PROPERTIES:
            unknown.append(prop)
            continue
        if prop == "transform":
            lowered = value.lower()
            pieces = [piece.strip() for piece in lowered.split(")") if piece.strip()]
            bad = [
                piece
                for piece in pieces
                if not (piece.startswith("rotate(") or piece.startswith("scale("))
            ]
            if bad:
                raise ValueError(
                    "Overlay CSS supports transform only for rotate(...) "
                    "and scale(...)."
                )
        resolved[_css_to_key(prop)] = value
    if unknown:
        noun = "property" if len(unknown) == 1 else "properties"
        raise ValueError(
            f"Unsupported overlay CSS {noun}: {', '.join(unknown)}. "
            "Run 'moviestar overlays --help' for the supported CSS subset."
        )
    return resolved


def _px_int(value, prop: str) -> int:
    """Normalize a px size to a plain int. Friction fix: resolved
    ``font_size`` flipped between int (preset) and string (CSS) shapes,
    forcing downstream consumers into two code paths."""
    if isinstance(value, bool):
        raise ValueError(f"Invalid {prop} value {value!r}.")
    if isinstance(value, (int, float)):
        return int(round(value))
    text = str(value).strip().lower()
    if text.endswith("px"):
        text = text[:-2].strip()
    try:
        return int(round(float(text)))
    except ValueError:
        raise ValueError(
            f"Invalid {prop} value {value!r}. Use a px size like "
            f"'{prop}: 72px'."
        ) from None


def _overlay_style_envelope(
    *,
    preset: str,
    css: str | None,
    highlight_color: str | None = None,
) -> dict:
    if preset not in OVERLAY_STYLE_PRESETS:
        raise ValueError(
            f"Unknown overlay style preset {preset!r}. Use one of: "
            f"{', '.join(sorted(OVERLAY_STYLE_PRESETS))}."
        )
    resolved = dict(OVERLAY_STYLE_PRESETS[preset])
    css_resolved = _parse_overlay_css(css)
    stroke = css_resolved.pop("stroke", None)
    if stroke is not None:
        parts = str(stroke).split(maxsplit=1)
        if parts:
            resolved["stroke_width"] = parts[0]
        if len(parts) > 1:
            resolved["stroke_color"] = parts[1]
    resolved.update(css_resolved)
    if "font_size" in resolved:
        resolved["font_size"] = _px_int(resolved["font_size"], "font-size")
    if "stroke_width" in resolved:
        resolved["stroke_width"] = _px_int(
            resolved["stroke_width"], "-moviestar-stroke"
        )
    try:
        resolve_font(
            resolved.get("font_family", "Inter"),
            resolved.get("font_weight", "normal"),
        )
    except FontResolutionError as exc:
        raise ValueError(str(exc)) from exc
    if highlight_color is not None:
        resolved["highlight_color"] = highlight_color
    return {
        "preset": preset,
        "css": css or None,
        "resolved": resolved,
    }


def _split_css_declarations(css: str | None) -> list[str]:
    """Split CSS-ish declarations on semicolons or top-level commas.

    ``--style-for`` accepts compact agent-friendly input such as
    ``source=color:#fff,highlight:#fc0`` while still allowing rgb(...)
    values that contain commas.
    """
    if css is None:
        return []
    chunks: list[str] = []
    current: list[str] = []
    depth = 0
    for char in css:
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        if depth == 0 and char in ";,":
            chunk = "".join(current).strip()
            if chunk:
                chunks.append(chunk)
            current = []
            continue
        current.append(char)
    chunk = "".join(current).strip()
    if chunk:
        chunks.append(chunk)
    return chunks


def _normalize_caption_style_for_css(css: str) -> str:
    declarations: list[str] = []
    for declaration in _split_css_declarations(css):
        if ":" not in declaration:
            raise ValueError(
                f"Invalid --style-for declaration {declaration!r}. "
                "Use SOURCE=property:value[,property:value]."
            )
        prop, value = declaration.split(":", 1)
        prop = prop.strip()
        if prop.lower() == "highlight":
            prop = "-moviestar-highlight-color"
        declarations.append(f"{prop}: {value.strip()}")
    return "; ".join(declarations)


def _caption_highlight_color_from_css(css: str | None) -> str | None:
    for declaration in _split_css_declarations(css):
        if ":" not in declaration:
            continue
        prop, value = declaration.split(":", 1)
        if prop.strip().lower() in {"highlight", "-moviestar-highlight-color"}:
            return value.strip()
    return None


def _combine_css(base_css: str | None, override_css: str | None) -> str | None:
    pieces = [
        piece.strip()
        for piece in (base_css, override_css)
        if piece and piece.strip()
    ]
    return "; ".join(pieces) if pieces else None


def _parse_caption_style_for_args(
    values: tuple[str, ...], command: str
) -> dict[str, str]:
    style_for: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            _error_exit(
                command,
                f"Invalid --style-for {value!r}. Use SOURCE=CSS, for "
                "example --style-for jdilla='color:#fff,highlight:#fc0'.",
            )
        source, css = value.split("=", 1)
        source = source.strip()
        css = css.strip()
        if not source or not css:
            _error_exit(
                command,
                f"Invalid --style-for {value!r}. Source and CSS must be non-empty.",
            )
        if source in style_for:
            _error_exit(command, f"Duplicate --style-for source {source!r}.")
        try:
            style_for[source] = _normalize_caption_style_for_css(css)
        except ValueError as exc:
            _error_exit(command, str(exc))
    return style_for


OVERLAY_POSITION_PRESETS = {
    "bottom",
    "top",
    "center",
    "lower-third-left",
    "lower-third-right",
}


def _overlay_position_envelope(
    *,
    preset: str | None = None,
    x: float | None = None,
    y: float | None = None,
    margin_x: int | None = None,
    margin_y: int | None = None,
    anchor: str | None = None,
    default_preset: str = "top",
) -> dict:
    """Resolve a position preset or normalized coordinates into the
    canonical position shape. The two paths are mutually exclusive because
    accepting both would make precedence ambiguous in the envelope."""
    if x is not None or y is not None:
        if preset is not None:
            raise ValueError(
                "Choose one positioning path: a preset (--position) or "
                "normalized coordinates (--x/--y), not both."
            )
        if x is None or y is None:
            raise ValueError("Pass both --x and --y for normalized positioning.")
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("--x and --y must be normalized values from 0.0 to 1.0.")
        return {
            "coordinate_space": "canvas",
            "x": x,
            "y": y,
            "anchor": anchor or "center",
        }
    if preset is None:
        preset = default_preset
    if preset not in OVERLAY_POSITION_PRESETS:
        raise ValueError(
            f"Unknown overlay position {preset!r}. Use one of: "
            f"{', '.join(sorted(OVERLAY_POSITION_PRESETS))}."
        )
    position = {
        "preset": preset,
        "coordinate_space": "canvas",
        "safe_area": True,
    }
    if preset == "bottom":
        position.update({"anchor": "bottom-center", "margin_y": 180})
    elif preset == "top":
        position.update({"anchor": "top-center", "margin_y": 160})
    elif preset == "center":
        position.update({"anchor": "center"})
    elif preset == "lower-third-left":
        position.update({"anchor": "bottom-left", "margin_x": 96, "margin_y": 180})
    elif preset == "lower-third-right":
        position.update({"anchor": "bottom-right", "margin_x": 96, "margin_y": 180})
    if margin_x is not None:
        position["margin_x"] = margin_x
    if margin_y is not None:
        position["margin_y"] = margin_y
    if anchor is not None:
        position["anchor"] = anchor
    return position


def _overlay_record(
    *,
    overlay_id: str,
    track: str,
    kind: str,
    text: str,
    from_tc: str,
    to_tc: str,
    position: dict,
    style: dict,
    z_index: int,
    highlight: dict | None = None,
    tokens: list[dict] | None = None,
    source: str | None = None,
    timing: dict | None = None,
) -> dict:
    """Build the canonical on-disk overlay shape stored in spec.json.
    Timecodes are stored as strings; envelopes convert via
    :func:`_overlay_view`."""
    from_s = parse_timecode(from_tc)
    to_s = parse_timecode(to_tc)
    if from_s >= to_s:
        raise ValueError(
            f"'from' ({format_timecode(from_s)['text']}) must be before "
            f"'to' ({format_timecode(to_s)['text']})."
        )
    if not text:
        raise ValueError("Overlay text must be a non-empty string.")
    record = {
        "id": overlay_id,
        "track": track,
        "kind": kind,
        "text": text,
        "z_index": z_index,
        "position": position,
        "style": style,
    }
    if kind == "manual":
        record["timing"] = timing or {
            "space": "result",
            "from": format_timecode(from_s)["text"],
            "to": format_timecode(to_s)["text"],
        }
    else:
        record["from"] = format_timecode(from_s)["text"]
        record["to"] = format_timecode(to_s)["text"]
    if highlight is not None:
        record["highlight"] = highlight
    if tokens is not None:
        record["tokens"] = tokens
    if source is not None:
        record["source"] = source
    return record


def _scene_overlay_timing_preview(
    project: dict,
    spec: dict,
    *,
    scene_ref: str,
    from_tc: str,
    to_tc: str,
    command: str,
) -> tuple[dict, dict, list[dict]]:
    """Validate and resolve a scene-local timing record for authoring."""
    try:
        from_s = parse_timecode(from_tc)
        to_s = parse_timecode(to_tc)
    except ValueError as exc:
        _error_exit(command, str(exc))
    if from_s < 0 or from_s >= to_s:
        _error_exit(
            command,
            f"Scene-local 'from' ({format_timecode(from_s)['text']}) must be "
            f"non-negative and before 'to' ({format_timecode(to_s)['text']}).",
        )

    composition = spec.get("composition") or []
    scenes = [scene for scene in composition if isinstance(scene, dict)]
    scene = next(
        (
            candidate
            for candidate in scenes
            if scene_ref in {candidate.get("id"), candidate.get("name")}
        ),
        None,
    )
    if scene is None:
        available = ", ".join(
            f"{candidate.get('name')} ({candidate.get('id')})"
            for candidate in scenes
        )
        _error_exit_with_hint(
            command,
            f"Unknown scene {scene_ref!r} for scene-bound overlay timing.",
            (
                f"Use a scene name or stable scene ID. Available: {available}."
                if available
                else "Build a scene composition with 'moviestar scenes set' "
                "before adding a scene-bound overlay."
            ),
        )

    try:
        resolved = resolve_project(project, spec)
    except (ResolvedProjectError, SpecValidationError) as exc:
        _error_exit(command, f"Could not resolve scene timing: {exc}")
    matching = [
        span
        for span in resolved.spans
        if span.scene_id == scene.get("id")
        or (
            span.scene_id is None
            and span.scene_name == scene.get("name")
        )
    ]
    if not matching:
        _error_exit(command, f"Scene {scene.get('name')!r} has no resolved span.")

    scene_start = min(span.scene_start_s for span in matching)
    scene_end = max(span.scene_end_s for span in matching)
    scene_duration = scene_end - scene_start
    visible_from = min(max(from_s, 0.0), scene_duration)
    visible_to = min(max(to_s, 0.0), scene_duration)
    resolved_from = scene_start + visible_from
    resolved_to = scene_start + visible_to
    visibility = (
        "outside_scene"
        if from_s >= scene_duration
        else "clipped"
        if to_s > scene_duration
        else "visible"
    )
    timing = {
        "space": "scene",
        "scene": scene["id"],
        "from": format_timecode(from_s)["text"],
        "to": format_timecode(to_s)["text"],
    }
    timing_view = {
        "space": "scene",
        "scene": scene["id"],
        "scene_name": scene["name"],
        "from": format_timecode(from_s),
        "to": format_timecode(to_s),
        "duration": format_timecode(to_s - from_s),
        "visibility": visibility,
        "resolved_result_range": {
            "from": format_timecode(resolved_from),
            "to": format_timecode(resolved_to),
            "duration": format_timecode(max(0.0, resolved_to - resolved_from)),
        },
        "scene_result_range": {
            "from": format_timecode(scene_start),
            "to": format_timecode(scene_end),
            "duration": format_timecode(scene_duration),
        },
    }
    warnings = []
    if to_s > scene_duration:
        warnings.append(
            _warning(
                "overlay_scene_range_clipped",
                (
                    f"The overlay is entirely outside scene "
                    f"{scene['name']!r} and will not render."
                    if visibility == "outside_scene"
                    else f"The overlay extends past scene {scene['name']!r}; "
                    "only the overlapping scene-local range is currently "
                    "visible."
                ),
                source="overlays",
                scene=scene["name"],
                scene_id=scene["id"],
                renders=visibility != "outside_scene",
                requested_range={
                    "from": format_timecode(from_s),
                    "to": format_timecode(to_s),
                },
                visible_range={
                    "from": format_timecode(visible_from),
                    "to": format_timecode(visible_to),
                },
            )
        )
    return timing, timing_view, warnings


def _scene_timed_overlay_view(record: dict, timing_view: dict) -> dict:
    view = _overlay_view(record)
    view.pop("from", None)
    view.pop("to", None)
    view.pop("duration", None)
    view["timing"] = timing_view
    return view


def _overlay_view(record: dict) -> dict:
    """Convert an on-disk overlay record into the envelope shape:
    timecodes as {text, seconds} dicts plus a derived duration."""
    view = dict(record)
    timing = record.get("timing")
    if timing is not None:
        from_s = parse_timecode(timing["from"])
        to_s = parse_timecode(timing["to"])
        timing_view = dict(timing)
        timing_view["from"] = format_timecode(from_s)
        timing_view["to"] = format_timecode(to_s)
        timing_view["duration"] = format_timecode(to_s - from_s)
        resolved_timing = record.get("resolved_timing")
        if resolved_timing and timing["space"] == "scene":
            timing_view["scene_name"] = resolved_timing["scene_name"]
            timing_view["visibility"] = resolved_timing["visibility"]
            timing_view["resolved_result_range"] = {
                "from": format_timecode(resolved_timing["result_from_s"]),
                "to": format_timecode(resolved_timing["result_to_s"]),
                "duration": format_timecode(
                    resolved_timing["result_to_s"]
                    - resolved_timing["result_from_s"]
                ),
            }
        view.pop("resolved_timing", None)
        view["timing"] = timing_view
    else:
        from_s = parse_timecode(record["from"])
        to_s = parse_timecode(record["to"])
        view["from"] = format_timecode(from_s)
        view["to"] = format_timecode(to_s)
        view["duration"] = format_timecode(to_s - from_s)
    tokens = record.get("tokens")
    if tokens is not None:
        view["tokens"] = [
            {
                "text": token["text"],
                "from": format_timecode(parse_timecode(token["from"])),
                "to": format_timecode(parse_timecode(token["to"])),
                **(
                    {"source": token["source"]}
                    if token.get("source") is not None
                    else {}
                ),
            }
            for token in tokens
        ]
    view["estimated_bounds"] = {
        "status": "reported_at_render",
        "note": (
            "screenshot/inspect/watch and 'export --dry-run' report "
            "estimated canvas-space bounds, overflow warnings, and "
            "overlap warnings for overlays active in the rendered window."
        ),
    }
    return view


def _overlay_render_order(overlays: list[dict]) -> list[str]:
    return [
        overlay["id"]
        for overlay in sorted(
            overlays,
            key=lambda item: (item.get("z_index", 0), item["track"], item["id"]),
        )
    ]


def _overlay_render_context(
    project: dict,
    spec: dict,
    canvas_tuple: tuple[int, int],
    window_from: float,
    window_to: float,
    command: str,
) -> dict | None:
    """Plan the overlays active in a render window, or None when the
    spec has no overlays. Font and style-resolution failures exit with
    structured errors — exported video never silently changes fonts."""
    stored_overlays = spec.get("overlays") or []
    if not stored_overlays:
        return None
    try:
        overlay_state = resolve_overlays(
            resolve_project(project, spec), stored_overlays
        )
        planned = plan_overlays(
            list(overlay_state.records), canvas_tuple, window_from, window_to
        )
        planned["highlight_ass"] = _highlight_ass_assets(planned, canvas_tuple)
    except FontResolutionError as exc:
        _error_exit_with_hint(
            command,
            str(exc),
            "Edit the overlay style with 'moviestar overlays dump' + "
            "'moviestar overlays set'. Run 'moviestar overlays --help' "
            "for the CSS subset and style presets.",
        )
    except ValueError as exc:
        _error_exit(command, str(exc))
    planned["total_count"] = len(stored_overlays)
    planned["detached"] = list(overlay_state.detached)
    planned["clipped"] = [
        _overlay_clipped_envelope(issue) for issue in overlay_state.clipped
    ]
    return planned


def _highlight_ass_assets(
    planned: dict, canvas_tuple: tuple[int, int]
) -> dict | None:
    """ASS render assets for the window's spoken-word highlight plans.

    The path is content-addressed under the project workspace, so
    dry-run commands reference the same file a real render writes and
    stale files never get reused for changed captions."""
    highlight_plans = [p for p in planned["plans"] if p.get("highlight")]
    if not highlight_plans:
        return None
    content = build_highlight_ass(highlight_plans, canvas_tuple)
    digest = hashlib.md5(content.encode()).hexdigest()[:10]
    path = get_project_dir() / "highlight-ass" / f"hl_{digest}.ass"
    return {
        "path": str(path),
        "fontsdir": os.path.dirname(highlight_plans[0]["font"]["path"]),
        "content": content,
        "overlay_ids": [p["id"] for p in highlight_plans],
    }


def _highlight_ass_arg(planned: dict | None) -> tuple[str, str] | None:
    if not planned or not planned.get("highlight_ass"):
        return None
    assets = planned["highlight_ass"]
    return (assets["path"], assets["fontsdir"])


def _write_highlight_ass(planned: dict | None) -> None:
    """Write the window's ASS file before a real render. No-op when
    the window has no highlight plans."""
    if not planned or not planned.get("highlight_ass"):
        return
    assets = planned["highlight_ass"]
    path = Path(assets["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(assets["content"])


def _overlay_envelope_block(planned: dict) -> dict:
    """Render-command envelope block: which overlays burned in, from
    which tracks, with which fonts, plus estimation warnings."""
    block = {
        "burned_in": True,
        "active_count": len(planned["plans"]),
        "total_count": planned["total_count"],
        "tracks": sorted({p["track"] for p in planned["plans"]}),
        "fonts": planned["fonts"],
        "items": [overlay_plan_summary(p) for p in planned["plans"]],
    }
    if planned.get("highlight_ass"):
        block["highlight"] = {
            "rendered": True,
            "ass_file": planned["highlight_ass"]["path"],
            "fontsdir": planned["highlight_ass"]["fontsdir"],
            "overlay_ids": planned["highlight_ass"]["overlay_ids"],
        }
    if planned["warnings"]:
        block["warnings"] = planned["warnings"]
    if planned.get("detached"):
        block["detached"] = planned["detached"]
    if planned.get("clipped"):
        block["clipped"] = planned["clipped"]
    return block


def _overlay_warning_code(message: str) -> str:
    text = message.lower()
    if "estimated bounds overlap" in text:
        return "overlay_estimated_bounds_overlap"
    if "overflow" in text:
        return "overlay_estimated_bounds_overflow"
    if "border-radius" in text:
        return "overlay_border_radius_square_corners"
    if "text-shadow blur radius" in text:
        return "overlay_text_shadow_blur_ignored"
    if "text-shadow" in text and "was not understood" in text:
        return "overlay_text_shadow_skipped"
    if "letter-spacing" in text:
        return "overlay_letter_spacing_ignored"
    if "token(s)" in text:
        return "captions_highlight_token_mismatch"
    if "background boxes" in text:
        return "captions_highlight_background_box_dropped"
    if "transforms" in text:
        return "captions_highlight_transform_dropped"
    return "overlay_render_limitation"


def _overlay_warning_objects(planned: dict) -> list[dict]:
    warnings = []
    collisions_by_message = {
        collision["message"]: collision
        for collision in planned.get("collisions") or []
    }
    for message in planned.get("warnings") or []:
        collision = collisions_by_message.get(message)
        if collision is not None:
            details = {
                key: value
                for key, value in collision.items()
                if key != "message"
            }
            warnings.append(
                _warning(
                    "overlay_estimated_bounds_overlap",
                    message,
                    source="overlays",
                    **details,
                )
            )
            continue
        overlay_id = message.split(":", 1)[0] if ":" in message else None
        warnings.append(
            _warning(
                _overlay_warning_code(message),
                message,
                source="overlays",
                overlay_id=overlay_id,
            )
        )
    return warnings


def _caption_content_warning_objects(
    records: list[dict], *, source: str
) -> list[dict]:
    warnings = []
    for issue in validate_caption_overlay_content(records):
        details = {
            key: value
            for key, value in issue.items()
            if key not in {"code", "message"}
        }
        warnings.append(
            _warning(
                issue["code"],
                issue["message"],
                source=source,
                **details,
            )
        )
    return warnings


def _attach_overlay_envelope(result: dict, planned: dict) -> None:
    result["overlays"] = _overlay_envelope_block(planned)
    _add_warnings(
        result,
        [
            *_overlay_warning_objects(planned),
            *_overlay_lifecycle_warning_objects(planned),
        ],
    )


def _overlay_clipped_envelope(issue: dict) -> dict:
    return {
        "id": issue["id"],
        "text": issue["text"],
        "scene": issue["scene"],
        "scene_name": issue["scene_name"],
        "renders": issue["renders"],
        "requested_range": {
            "from": format_timecode(issue["requested_from_s"]),
            "to": format_timecode(issue["requested_to_s"]),
        },
        "visible_range": {
            "from": format_timecode(issue["visible_from_s"]),
            "to": format_timecode(issue["visible_to_s"]),
        },
    }


def _overlay_lifecycle_warning_objects(state) -> list[dict]:
    warnings = []
    detached = (
        list(state.get("detached") or [])
        if isinstance(state, dict)
        else list(state.detached)
    )
    clipped_raw = (
        list(state.get("clipped") or [])
        if isinstance(state, dict)
        else [_overlay_clipped_envelope(issue) for issue in state.clipped]
    )
    if detached:
        warnings.append(
            _warning(
                "overlays_detached",
                f"{len(detached)} scene-bound overlay(s) reference deleted "
                "scenes and will not render until removed or rebound.",
                source="overlays",
                overlays=detached,
            )
        )
    for issue in clipped_raw:
        warnings.append(
            _warning(
                "overlay_scene_range_clipped",
                (
                    f"Overlay {issue['id']!r} is entirely outside scene "
                    f"{issue['scene_name']!r} and will not render."
                    if not issue["renders"]
                    else f"Overlay {issue['id']!r} extends past scene "
                    f"{issue['scene_name']!r}; only its overlap will render."
                ),
                source="overlays",
                overlay_id=issue["id"],
                scene=issue["scene_name"],
                scene_id=issue["scene"],
                renders=issue["renders"],
                requested_range=issue["requested_range"],
                visible_range=issue["visible_range"],
            )
        )
    return warnings


def _attach_overlay_lifecycle(result: dict, state) -> None:
    detached = list(state.detached)
    clipped = [_overlay_clipped_envelope(issue) for issue in state.clipped]
    if detached:
        result["overlays_detached"] = detached
    if clipped:
        result["overlays_clipped"] = clipped
    _add_warnings(result, _overlay_lifecycle_warning_objects(state))


def _overlay_cache_fingerprint(spec: dict) -> str:
    """Cache-key suffix for inspect thumbnail dirs. Editing overlays
    must invalidate cached thumbnails or agents verify stale text."""
    overlays = spec.get("overlays") or []
    if not overlays:
        return ""
    digest = hashlib.md5(
        json.dumps(overlays, sort_keys=True).encode()
    ).hexdigest()[:8]
    return f"_ov{digest}"


def _scene_composition_cache_fingerprint(
    spec: dict, composition: list[dict]
) -> str:
    """Cache-key suffix for scene composition thumbnails.

    Scene boundaries define the per-scene sampling schedule, while layout,
    framing, source ranges, and canvas define the pixels at those times. A
    composition edit must therefore select a new cache directory even when
    inspect's range and interval are unchanged.
    """
    payload = {
        "composition": composition,
        "composition_canvas": spec.get("composition_canvas"),
    }
    digest = hashlib.md5(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:8]
    return f"_sc{digest}"


def _flat_overlays_note(spec: dict, result: dict) -> None:
    """Explain when a source-only render will not burn in overlays."""
    overlays = spec.get("overlays") or []
    if overlays:
        message = (
            f"{len(overlays)} stored overlay(s) were NOT burned in - "
            "source-specific exports in multi-source projects render only "
            "that source's edit. Omit --source in a single-source project, "
            "or build a composition with 'moviestar scenes set' or "
            "'moviestar concat --layout' before exporting to include overlays."
        )
        result["overlays_note"] = message
        _add_warnings(
            result,
            [
                _warning(
                    "overlays_not_burned_in_flat_export",
                    message,
                    overlays_count=len(overlays),
                )
            ],
        )


def _overlay_result(
    *,
    status: str,
    spec_overlays: list[dict],
    shown_overlays: list[dict],
    writes_spec: bool,
    hint: str,
) -> dict:
    """Success envelope for the real overlay commands. ``tracks``,
    ``overlays_count``, and ``render_order`` describe the full overlay
    state; ``overlays`` echoes only the records the command touched."""
    return {
        "status": status,
        "writes_spec": writes_spec,
        "overlay_model": "timed_text_overlays",
        "tracks": sorted({overlay["track"] for overlay in spec_overlays}),
        "overlays_count": len(spec_overlays),
        "overlays": [_overlay_view(overlay) for overlay in shown_overlays],
        "render_order": _overlay_render_order(spec_overlays),
        "hint": hint,
    }


def _status_overlays_block(
    overlays: list[dict],
    recipe_tracks: set[str] | None = None,
    frozen_stale: dict | None = None,
    resolved_state=None,
) -> dict:
    """Summarize stored overlay state by track for ``status``.

    Coverage is the outer result-time span for every overlay on a
    track. Use min/max rather than list order because ``overlays set``
    accepts intentionally reordered and overlapping cues.
    """
    resolved_by_id = {
        overlay["id"]: overlay
        for overlay in (resolved_state.records if resolved_state else overlays)
    }
    track_summaries: list[dict] = []
    for track in sorted({overlay["track"] for overlay in overlays}):
        track_overlays = [
            overlay for overlay in overlays if overlay["track"] == track
        ]
        resolved_track = [
            resolved_by_id[overlay["id"]]
            for overlay in track_overlays
            if overlay["id"] in resolved_by_id
        ]
        resolved_ranges = []
        for overlay in resolved_track:
            if "from" in overlay and "to" in overlay:
                resolved_ranges.append(
                    (
                        parse_timecode(overlay["from"]),
                        parse_timecode(overlay["to"]),
                    )
                )
                continue
            timing = overlay.get("timing") or {}
            if timing.get("space") == "result":
                resolved_ranges.append(
                    (
                        parse_timecode(timing["from"]),
                        parse_timecode(timing["to"]),
                    )
                )
        coverage = None
        if resolved_ranges:
            from_s = min(item[0] for item in resolved_ranges)
            to_s = max(item[1] for item in resolved_ranges)
            coverage = {
                "from": format_timecode(from_s),
                "to": format_timecode(to_s),
            }
        style_presets = sorted(
            {
                overlay["style"]["preset"]
                for overlay in track_overlays
                if overlay.get("style", {}).get("preset")
            }
        )
        highlight_mode_values: set[str] = set()
        for overlay in track_overlays:
            mode = (overlay.get("highlight") or {}).get("mode")
            # Caption overlays without an explicit highlight block render
            # without highlighting, so report the effective mode as none.
            if mode is None and overlay.get("kind") == "caption":
                mode = "none"
            if mode:
                highlight_mode_values.add(mode)
        # Friction (stage-4 round 1): agents fixing placement complaints
        # could not see a track's current position from status. Surface
        # the preset(s) in use; "custom" covers explicit x/y records.
        positions = sorted(
            {
                (overlay.get("position") or {}).get("preset") or "custom"
                for overlay in track_overlays
            }
        )
        summary_extra = {}
        if any(o.get("kind") == "caption" for o in track_overlays):
            derived = track in (recipe_tracks or set())
            summary_extra = {
                "caption_model": "derived" if derived else "materialized",
                "follows_timeline_edits": derived,
            }
            # #412: staleness is queryable from status, not just the
            # motion-set warning. Derived tracks are never stale.
            if derived:
                summary_extra["stale"] = False
            elif frozen_stale is not None and track in frozen_stale:
                summary_extra["stale"] = frozen_stale[track]
        track_summaries.append(
            {
                "track": track,
                "count": len(track_overlays),
                **summary_extra,
                "ids": sorted(overlay["id"] for overlay in track_overlays),
                "coverage": coverage,
                "timing_spaces": sorted(
                    {
                        (overlay.get("timing") or {}).get("space", "result")
                        for overlay in track_overlays
                    }
                ),
                "positions": positions,
                "style_presets": style_presets,
                "highlight_modes": sorted(highlight_mode_values),
            }
        )

    result = {
        "count": len(overlays),
        "ids": sorted(overlay["id"] for overlay in overlays),
        # Keep the original scan-friendly string list for compatibility.
        "tracks": [summary["track"] for summary in track_summaries],
        "track_summaries": track_summaries,
        "style_presets": sorted(
            {
                preset
                for summary in track_summaries
                for preset in summary["style_presets"]
            }
        ),
        "highlight_modes": sorted(
            {
                mode
                for summary in track_summaries
                for mode in summary["highlight_modes"]
            }
        ),
    }
    if resolved_state and resolved_state.detached:
        result["detached"] = list(resolved_state.detached)
    if resolved_state and resolved_state.clipped:
        result["clipped"] = [
            _overlay_clipped_envelope(issue)
            for issue in resolved_state.clipped
        ]
    return result


def _font_view(font: dict) -> dict:
    return {
        "family": font["family"],
        "weight": font["weight"],
        "source": font["source"],
        "path": font["path"],
        **(
            {"font_family": font["font_family"]}
            if font.get("font_family") != font.get("family")
            else {}
        ),
    }


@cli.group("fonts", invoke_without_command=True)
@click.pass_context
def fonts(ctx: click.Context) -> None:
    """Manage fonts usable by captions and overlays.

    \b
    Project fonts live in moviestar/fonts and travel with the project.
    User fonts live in ~/.moviestar/fonts and are available everywhere.

    \b
    Reference listed names from captions/overlays with CSS:
      --css 'font-family: My Font'
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(
        json.dumps(
            {
                "status": "font_surface",
                "writes_spec": False,
                "commands": [
                    "moviestar fonts list",
                    "moviestar fonts add path/to/font.ttf --name 'My Font'",
                ],
                "project_font_dir": (
                    str(project_font_dir()) if project_font_dir() is not None else None
                ),
                "user_font_dir": str(user_font_dir()),
                "hint": (
                    "Add a project font, then reference it from captions or "
                    "overlays with --css 'font-family: My Font'."
                ),
            },
            indent=2,
        )
    )


@fonts.command("list")
@project_workspace_option
def fonts_list() -> None:
    """List bundled and registered fonts usable by font-family."""
    fonts = [_font_view(font) for font in list_managed_fonts()]
    click.echo(
        json.dumps(
            {
                "status": "listed_fonts",
                "writes_spec": False,
                "fonts_count": len(fonts),
                "fonts": fonts,
                "project_font_dir": (
                    str(project_font_dir()) if project_font_dir() is not None else None
                ),
                "user_font_dir": str(user_font_dir()),
                "hint": (
                    "Use a listed family in overlay/caption CSS, for example "
                    "--css 'font-family: Inter'."
                ),
            },
            indent=2,
        )
    )


@fonts.command("add")
@project_workspace_option
@click.argument("font_file", type=click.Path())
@click.option(
    "--name",
    default=None,
    help="Family name to use in overlay/caption CSS (default: font metadata).",
)
@click.option(
    "--scope",
    type=click.Choice(["project", "user"]),
    default=None,
    help="Install into the current project or user font directory.",
)
def fonts_add(font_file: str, name: str | None, scope: str | None) -> None:
    """Install a font without modifying MovieStar's package files."""
    selected_scope = scope or default_font_scope()
    try:
        font = add_font(font_file, name=name, scope=selected_scope)
    except (FileNotFoundError, ValueError, OSError) as exc:
        _error_exit_with_hint(
            "fonts add",
            str(exc),
            "Pass an existing .ttf, .otf, or .ttc file. Inside a project, "
            "fonts add installs to moviestar/fonts by default; outside a "
            "project, use --scope user.",
        )
    click.echo(
        json.dumps(
            {
                "status": "added_font",
                "writes_spec": False,
                "font": _font_view(font),
                "scope": selected_scope,
                "font_dir": str(Path(font["path"]).parent),
                "hint": (
                    f"Reference this font with --css 'font-family: "
                    f"{font['family']}'. Run 'moviestar fonts list' to "
                    f"see all registered names."
                ),
            },
            indent=2,
        )
    )


@cli.group("captions", invoke_without_command=True)
@click.pass_context
def captions(ctx: click.Context) -> None:
    """Generate/import caption overlays for burn-in.

    \b
    Product story: let your agent burn captions into the video.
    Implementation model: captions compile into timed text overlays.
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(
        json.dumps(
            {
                "status": "caption_surface",
                "writes_spec": False,
                "caption_model": "derived",
                "commands": [
                    "moviestar captions generate",
                    "moviestar captions import <captions.srt|captions.vtt>",
                    "moviestar captions dump",
                    "moviestar captions placement --default P --for SEL=P",
                    "moviestar captions break --at T | --at-word W",
                    "moviestar captions join --at T",
                    "moviestar captions suppress --from T --to T",
                    "moviestar captions rules add --merge MATCH=REPLACEMENT",
                    "moviestar captions rules list",
                    "moviestar captions materialize",
                ],
                "hint": (
                    "Start with 'moviestar captions generate --help'. "
                    "Generated tracks are derived: they re-compute from the "
                    "current timeline on every render, so pacing changes and "
                    "scene edits never leave captions stale. Verify with "
                    "'moviestar captions dump'; edit with rules/break/join/"
                    "suppress; 'moviestar captions materialize' freezes a "
                    "track into plain overlays for hand-editing."
                ),
            },
            indent=2,
        )
    )


_CAPTION_PLACEMENT_POSITIONS = (
    "bottom", "top", "center", "lower-third-left", "lower-third-right",
)


def _caption_track_cues(spec: dict, track: str, command: str) -> list[dict]:
    cues = [
        overlay
        for overlay in spec.get("overlays", [])
        if overlay.get("kind") == "caption" and overlay.get("track") == track
    ]
    if not cues:
        tracks = sorted(
            {
                overlay["track"]
                for overlay in spec.get("overlays", [])
                if overlay.get("kind") == "caption"
            }
        )
        _error_exit_with_hint(
            command,
            f"No caption cues on track {track!r}."
            + (f" Caption tracks: {', '.join(tracks)}." if tracks else ""),
            "Run 'moviestar captions generate' to create the track, or pass "
            "--track <name> to pick an existing one.",
        )
    return cues


def _caption_scene_at(project: dict, spec: dict, at_s: float) -> str | None:
    try:
        resolved = resolve_project(project, spec)
    except (ResolvedProjectError, SpecValidationError):
        return None
    if not resolved.spans:
        return None
    return resolved.span_at(min(at_s, resolved.duration_s)).scene_name


def _parse_placement_overrides(
    override_args: tuple[str, ...], command: str
) -> list[dict]:
    overrides: list[dict] = []
    for raw in override_args:
        selector, sep, position = raw.partition("=")
        if not sep or not selector or not position:
            _error_exit_with_hint(
                command,
                f"Could not parse placement override {raw!r}.",
                "Use SELECTOR=POSITION, e.g. layout:two-up=center or "
                "scene:intro=top.",
            )
        kind, ksep, name = selector.partition(":")
        if not ksep or kind not in ("layout", "scene") or not name:
            _error_exit_with_hint(
                command,
                f"Unknown placement selector {selector!r}.",
                "Selectors are layout:<preset> or scene:<name> — e.g. "
                "layout:two-up=center or scene:intro=top.",
            )
        if position not in _CAPTION_PLACEMENT_POSITIONS:
            _error_exit_with_hint(
                command,
                f"Unknown position {position!r}.",
                "Positions: " + ", ".join(_CAPTION_PLACEMENT_POSITIONS) + ".",
            )
        overrides.append(
            {"selector": {kind: name}, "position": position}
        )
    return overrides


# Estimated text-baseline height per position, as a fraction of canvas
# height. Good enough for placement proof without rendering; the exact
# baseline depends on font metrics and wrapping.
_CAPTION_POSITION_BASELINES = {
    "bottom": 0.90,
    "top": 0.10,
    "center": 0.50,
    "lower-third-left": 0.72,
    "lower-third-right": 0.72,
}


def _caption_placement_preview(
    project: dict, spec: dict, default: str, overrides: list[dict]
) -> list[dict]:
    """Per-scene resolved placement: which rule fires where, with an
    estimated pixel baseline and the scene's result range so agents can
    prove placement (e.g. clear of a two-up seam) without rendering."""
    composition = spec.get("composition")
    if not composition or not _is_layout_composition(composition):
        return []
    canvas = spec.get("composition_canvas") or {}
    canvas_height = int(canvas.get("height") or 0)
    scene_ranges: dict[str, dict] = {}
    try:
        resolved = resolve_project(project, spec)
        for span in resolved.spans:
            scene_ranges.setdefault(
                span.scene_name,
                {
                    "from": format_timecode(span.scene_start_s),
                    "to": format_timecode(span.scene_end_s),
                },
            )
    except (ResolvedProjectError, SpecValidationError):
        pass
    preview: list[dict] = []
    for index, scene in enumerate(composition):
        name = scene.get("name", f"scene_{index + 1}")
        layout = scene.get("layout", {}).get("preset")
        position = default
        rule = "default"
        for override in overrides:
            selector = override["selector"]
            if selector.get("layout") == layout:
                position, rule = override["position"], f"layout:{layout}"
            if selector.get("scene") == name:
                position, rule = override["position"], f"scene:{name}"
        entry = {
            "scene": name,
            "layout": layout,
            "position": position,
            "rule": rule,
        }
        if canvas_height:
            entry["estimated_baseline"] = {
                "y_px": round(
                    canvas_height
                    * _CAPTION_POSITION_BASELINES.get(position, 0.9)
                ),
                "canvas_height": canvas_height,
                "note": "estimate; exact baseline depends on font metrics",
            }
        if name in scene_ranges:
            entry["result_range"] = scene_ranges[name]
        preview.append(entry)
    return preview


@captions.command("dump")
@project_workspace_option
@click.option("--track", default="captions", help="Caption track to dump.")
def captions_dump(track: str) -> None:
    """Show a caption track's derived cues with provenance.

    \b
    Each cue names:
      - its result-time range on the current timeline;
      - the scene that owns it;
      - the source words it was derived from;
      - which placement rule fired.

    \b
    This is the verification surface — check where cues land after any
    timeline edit without rendering. It is not a round-trip editor:
    change text with 'captions rules', boundaries with 'captions
    break'/'captions join', and spans with 'captions suppress'.
    """
    command = "captions dump"
    project, spec = _overlay_project_spec(command)
    cues = _caption_track_cues(spec, track, command)
    recipe = caption_recipe_for_track(spec, track)
    derived = recipe is not None and not recipe.get("frozen")
    # #412: staleness is queryable state. Derived tracks are never
    # stale (they re-derive at load); frozen recipes compare their
    # freeze fingerprint against the current timeline; recipe-less
    # legacy tracks cannot answer and report null.
    if derived:
        stale_block: dict = {"stale": False}
    elif recipe is not None:
        stale = _caption_staleness(project, spec, recipe)
        stale_block = {"stale": stale}
        if stale:
            frozen_at = recipe.get("frozen_at_duration")
            current = _current_composition_duration(project, spec)
            if frozen_at is not None:
                stale_block["frozen_clock"] = format_timecode(frozen_at)
            if current is not None:
                stale_block["current_clock"] = format_timecode(current)
    else:
        stale_block = {
            "stale": None,
            "stale_note": (
                "This track predates caption recipes; staleness is "
                "unknown. Re-run 'moviestar captions generate' for a "
                "derived track."
            ),
        }
    placement = (
        _caption_placement_resolver(project, spec, recipe)
        if derived
        else None
    )
    cue_views = []
    for cue in cues:
        from_s = parse_timecode(cue["from"]) if isinstance(
            cue["from"], str
        ) else float(cue["from"])
        to_s = parse_timecode(cue["to"]) if isinstance(
            cue["to"], str
        ) else float(cue["to"])
        cue_views.append(
            {
                "id": cue["id"],
                "text": cue["text"],
                "result_range": {
                    "from": format_timecode(from_s),
                    "to": format_timecode(to_s),
                },
                "scene": _caption_scene_at(
                    project, spec, (from_s + to_s) / 2
                ),
                "source": cue.get("source"),
                "tokens_count": len(cue.get("tokens", [])),
                "placement": {
                    "position": (cue.get("position") or {}).get(
                        "preset", "bottom"
                    ),
                    "rule": (
                        placement((from_s + to_s) / 2)[1]
                        if placement is not None
                        else "frozen"
                    ),
                },
            }
        )
    click.echo(
        json.dumps(
            {
                "status": "caption_track_dump",
                "writes_spec": False,
                "track": track,
                "caption_model": (
                    "derived" if derived else "materialized"
                ),
                "follows_timeline_edits": derived,
                **stale_block,
                "cues_count": len(cue_views),
                "cues": cue_views,
                **(
                    {
                        "recipe": {
                            "source": recipe.get("source"),
                            "range": recipe.get("range"),
                            "position": recipe.get("position") or "bottom",
                            "placement": recipe.get("placement")
                            or {
                                "default": recipe.get("position") or "bottom",
                                "overrides": [],
                            },
                            "style": recipe.get("style") or "social-bold",
                            "css": recipe.get("css"),
                            "highlight": recipe.get("highlight"),
                            "edits": recipe.get("edits", []),
                        }
                    }
                    if recipe is not None
                    else {}
                ),
                "hint": (
                    "Cues re-derive from the current timeline on every "
                    "render. Fix text with 'moviestar captions rules add', "
                    "boundaries with 'moviestar captions break' or 'join', "
                    "and unwanted spans with 'moviestar captions suppress'."
                    if derived
                    else "This track is frozen: cues are plain overlays "
                    "that do not follow timeline edits. Edit via 'moviestar "
                    "overlays dump' -> 'moviestar overlays set', or re-run "
                    "'moviestar captions generate' for a derived track."
                ),
            },
            indent=2,
        )
    )


def _require_caption_recipe(spec: dict, track: str, command: str) -> dict:
    recipe = caption_recipe_for_track(spec, track)
    if recipe is None or recipe.get("frozen"):
        _error_exit_with_hint(
            command,
            f"Track {track!r} is frozen: its cues are plain overlays and "
            "its caption recipe is not editable.",
            "Re-run 'moviestar captions generate' to return to a derived "
            "track, or hand-edit frozen cues via 'moviestar overlays dump' "
            "-> 'moviestar overlays set'.",
        )
    return recipe


def _caption_edit_seq(recipe: dict) -> int:
    """Highest edit sequence number ever used on this recipe. IDs are
    never reused after removal — a stale id in an agent's context must
    error, not silently address a different edit (friction finding)."""
    suffixes = [
        int(edit["id"].rsplit("_", 1)[1])
        for edit in recipe.get("edits", [])
        if isinstance(edit.get("id"), str)
        and edit["id"].rsplit("_", 1)[-1].isdigit()
    ]
    return max([recipe.get("edit_seq", 0), *suffixes])


def _next_caption_edit_id(recipe: dict) -> str:
    return f"edit_{_caption_edit_seq(recipe) + 1:04d}"


def _persist_caption_edits(
    project: dict, spec: dict, recipe: dict, edits: list[dict], command: str
) -> tuple[dict, list[dict]]:
    """Persist a recipe's new edit list and re-derive its cues.

    Returns (new_spec, derived_records). The revision journal snapshots
    the recipe while the cue cache is replaced in the same write."""
    updated = {
        **recipe,
        "edits": edits,
        "edit_seq": max(
            _caption_edit_seq(recipe),
            _caption_edit_seq({"edits": edits, "edit_seq": 0}),
        ),
        "cache_fingerprint": None,
    }
    records, _meta = _derive_caption_records(project, spec, updated, command)
    updated["cache_fingerprint"] = _caption_cache_fingerprint(
        project, spec, updated
    )
    new_spec = upsert_caption_recipe(spec, updated)
    new_spec, _replaced = _replace_caption_track(
        new_spec, recipe["track"], records, command
    )
    return new_spec, records


def _remove_caption_edit(
    project: dict,
    spec: dict,
    track: str,
    edit_id: str,
    op: str,
    command: str,
) -> None:
    recipe = _require_caption_recipe(spec, track, command)
    target = next(
        (e for e in recipe.get("edits", []) if e.get("id") == edit_id), None
    )
    if target is None:
        existing = ", ".join(
            e.get("id", "?") for e in recipe.get("edits", [])
        ) or "none"
        _error_exit_with_hint(
            command,
            f"No edit {edit_id!r} on track {track!r} (existing: {existing}).",
            "Run 'moviestar captions dump' — the recipe block lists every "
            "edit with its id.",
        )
    if target.get("op") != op:
        _error_exit_with_hint(
            command,
            f"Edit {edit_id!r} is a {target.get('op')!r} edit, not a "
            f"{op!r} edit.",
            f"Remove it with 'moviestar captions {target.get('op')} "
            f"--remove {edit_id}'.",
        )
    remaining = [
        e for e in recipe.get("edits", []) if e.get("id") != edit_id
    ]
    _new_spec, records = _persist_caption_edits(
        project, spec, recipe, remaining, command
    )
    click.echo(
        json.dumps(
            {
                "status": "caption_edit_removed",
                "writes_spec": True,
                "track": track,
                "removed": target,
                "cues_count": len(records),
                "hint": (
                    "The edit is out of the recipe; cues re-derived without "
                    "it. Run 'moviestar captions dump' to review."
                ),
            },
            indent=2,
        )
    )


def _caption_token_anchor(
    token: dict,
    cue: dict,
    command: str,
    *,
    source_token: bool = False,
) -> dict:
    source_from = (
        token.get("source_token_from") if source_token else None
    ) or token.get("source_from")
    if source_from is None:
        _error_exit_with_hint(
            command,
            "This cue predates word-anchored editing and carries no "
            "source-time anchors.",
            "Re-run 'moviestar captions generate' once to refresh the "
            "track, then retry.",
        )
    return {
        "source": token.get("source") or cue.get("source"),
        "source_time_s": round(parse_timecode(source_from), 3),
        "word": token["text"],
    }


def _duplicate_caption_edit(
    recipe: dict, op: str, source: str | None, time_s: float
) -> dict | None:
    for edit in recipe.get("edits", []):
        if (
            edit.get("op") == op
            and edit.get("source") == source
            and abs(float(edit.get("source_time_s", -1)) - time_s) <= 0.02
        ):
            return edit
    return None


@captions.command("break")
@project_workspace_option
@click.option("--track", default="captions", help="Caption track to edit.")
@click.option(
    "--at", "at_tc", default=None,
    help="Result timecode inside the cue to split (snaps to the nearest "
    "word boundary).",
)
@click.option(
    "--at-word", "at_word", default=None,
    help="Split before this word (exact text match).",
)
@click.option(
    "--occurrence", default=1, show_default=True,
    help="Which occurrence of --at-word to split at.",
)
@click.option(
    "--remove", "remove_id", default=None,
    help="Remove a stored break edit by id (see the recipe block in "
    "'captions dump').",
)
def captions_break(
    track: str,
    at_tc: str | None,
    at_word: str | None,
    occurrence: int,
    remove_id: str | None,
) -> None:
    """Split the cue containing a point into two cues.

    \b
    The break is part of the caption recipe and is stored
    word-anchored, so it survives pacing changes, scene reorders, and
    every other timeline edit — the cue boundary stays attached to the
    spoken word, not to a timestamp. If a later edit removes the word
    from the timeline, the break lies dormant until the word returns.

    \b
    Address the point either way (both are equivalent):
      --at 0:12.4            result timecode, snapped to a word boundary
      --at-word 'pivot'      the word the new cue should start at

    Undo a stored break with --remove <edit-id>.
    """
    command = "captions break"
    project, spec = _overlay_project_spec(command)
    if remove_id is not None:
        if at_tc is not None or at_word is not None:
            _error_exit(
                command, "--remove cannot be combined with --at/--at-word."
            )
        _remove_caption_edit(project, spec, track, remove_id, "break", command)
        return
    if (at_tc is None) == (at_word is None):
        _error_exit_with_hint(
            command,
            "Pass exactly one of --at or --at-word.",
            "Use --at <result-timecode> or --at-word '<text>' (add "
            "--occurrence N when the word repeats).",
        )
    if occurrence != 1 and at_word is None:
        _error_exit(command, "--occurrence requires --at-word.")
    recipe = _require_caption_recipe(spec, track, command)
    cues = _caption_track_cues(spec, track, command)

    target = None
    token_index: int | None = None
    anchor_space = "result" if at_tc is not None else "word"
    if at_tc is not None:
        try:
            at_s = parse_timecode(at_tc)
        except ValueError as exc:
            _error_exit(command, str(exc))
        for cue in cues:
            if parse_timecode(cue["from"]) < at_s < parse_timecode(cue["to"]):
                target = cue
                break
        if target is None:
            _error_exit_with_hint(
                command,
                f"No cue contains result time {at_tc} on track {track!r}.",
                "Run 'moviestar captions dump' to see cue ranges.",
            )
        token_index = next(
            (
                j
                for j, token in enumerate(target.get("tokens", []))
                if parse_timecode(token["from"]) >= at_s
            ),
            None,
        )
    else:
        seen = 0
        wanted = (at_word or "").strip(".,!?").lower()
        for cue in cues:
            for j, token in enumerate(cue.get("tokens", [])):
                if token.get("text", "").strip(".,!?").lower() == wanted:
                    seen += 1
                    if seen == occurrence:
                        target, token_index = cue, j
                        break
            if target is not None:
                break
        if target is None:
            _error_exit_with_hint(
                command,
                f"Word {at_word!r} (occurrence {occurrence}) not found in "
                f"track {track!r}.",
                "Run 'moviestar captions dump' to inspect cue text, or "
                "'moviestar find' to locate the phrase in the transcript.",
            )
    tokens = target.get("tokens", [])
    if token_index is None or token_index >= len(tokens):
        _error_exit_with_hint(
            command,
            "No word after that point to start the new cue at.",
            "Pick a point before the cue's last word — run 'moviestar "
            "captions dump' to see token counts.",
        )
    if token_index == 0:
        anchor0 = _caption_token_anchor(tokens[0], target, command)
        existing = _duplicate_caption_edit(
            recipe, "break", anchor0["source"], anchor0["source_time_s"]
        )
        if existing is not None:
            _error_exit_with_hint(
                command,
                f"Word {tokens[0]['text']!r} already starts a cue — a "
                f"break exists here ({existing['id']}).",
                f"Remove it with 'moviestar captions break --remove "
                f"{existing['id']}' if you want the boundary gone.",
            )
        _error_exit_with_hint(
            command,
            f"Word {tokens[0]['text']!r} already starts a cue.",
            "Nothing to split. To merge it into the previous cue instead, "
            "use 'moviestar captions join'.",
        )
    anchor = _caption_token_anchor(tokens[token_index], target, command)
    existing = _duplicate_caption_edit(
        recipe, "break", anchor["source"], anchor["source_time_s"]
    )
    if existing is not None:
        _error_exit_with_hint(
            command,
            f"A break already exists at {anchor['word']!r} "
            f"({existing['id']}).",
            f"Remove it with 'moviestar captions break --remove "
            f"{existing['id']}' if you want the boundary gone.",
        )
    edit = {
        "id": _next_caption_edit_id(recipe),
        "op": "break",
        **anchor,
    }
    original_from = parse_timecode(target["from"])
    original_to = parse_timecode(target["to"])
    _new_spec, records = _persist_caption_edits(
        project, spec, recipe, [*recipe.get("edits", []), edit], command
    )
    cues_after = [
        {
            "id": record["id"],
            "text": record["text"],
            "from": record["from"],
            "to": record["to"],
        }
        for record in records
        if parse_timecode(record["to"]) > original_from - 0.01
        and parse_timecode(record["from"]) < original_to + 0.01
    ]
    click.echo(
        json.dumps(
            {
                "status": "caption_cue_broken",
                "writes_spec": True,
                "track": track,
                "edit": {
                    "id": edit["id"],
                    "op": "break",
                    "anchor": {"space": anchor_space, **anchor},
                },
                "cue_before": {"id": target["id"], "text": target["text"]},
                "cues_after": cues_after,
                "persistence_note": (
                    "The break is part of the recipe and word-anchored: it "
                    "survives pacing changes, reorders, and cuts."
                ),
                "hint": (
                    "Run 'moviestar captions dump' to review the new "
                    "boundaries, 'moviestar captions join' to merge them "
                    f"back, or 'moviestar captions break --remove "
                    f"{edit['id']}' to undo this edit."
                ),
            },
            indent=2,
        )
    )


@captions.command("join")
@project_workspace_option
@click.option("--track", default="captions", help="Caption track to edit.")
@click.option(
    "--at", "at_tc", default=None,
    help="Result timecode of the boundary between the two cues to merge.",
)
@click.option(
    "--after-word", "after_word", default=None,
    help="Merge the cue ending with this word into the cue that "
    "follows it (exact text match).",
)
@click.option(
    "--remove", "remove_id", default=None,
    help="Remove a stored join edit by id (see the recipe block in "
    "'captions dump').",
)
def captions_join(
    track: str,
    at_tc: str | None,
    after_word: str | None,
    remove_id: str | None,
) -> None:
    """Merge the two cues that meet at a boundary.

    \b
    The join is part of the caption recipe and stored word-anchored,
    so it survives timeline edits. Cue limits still apply on every
    derivation: a join that would exceed the 42-character or
    3.5-second cue budget is rejected here rather than silently
    producing an unreadable cue, and a stored join re-checks the
    limits every time cues re-derive.

    \b
    Address the boundary either way (both are equivalent):
      --at 0:03.8            where one cue ends and the next begins
      --after-word 'pain'    the last word of the earlier cue

    Undo a stored join with --remove <edit-id>.
    """
    command = "captions join"
    project, spec = _overlay_project_spec(command)
    if remove_id is not None:
        if at_tc is not None or after_word is not None:
            _error_exit(
                command,
                "--remove cannot be combined with --at/--after-word.",
            )
        _remove_caption_edit(project, spec, track, remove_id, "join", command)
        return
    if (at_tc is None) == (after_word is None):
        _error_exit_with_hint(
            command,
            "Pass exactly one of --at or --after-word.",
            "Use --at <boundary-timecode> or --after-word '<text>' — the "
            "last word of the earlier cue.",
        )
    recipe = _require_caption_recipe(spec, track, command)
    cues = _caption_track_cues(spec, track, command)
    ordered = sorted(cues, key=lambda cue: parse_timecode(cue["from"]))

    left = right = None
    if at_tc is not None:
        try:
            at_s = parse_timecode(at_tc)
        except ValueError as exc:
            _error_exit(command, str(exc))
        for i, cue in enumerate(ordered[:-1]):
            if abs(parse_timecode(cue["to"]) - at_s) <= 0.15:
                left, right = cue, ordered[i + 1]
                break
        if left is None or right is None:
            _error_exit_with_hint(
                command,
                f"No cue boundary at result time {at_tc} on track {track!r}.",
                "Run 'moviestar captions dump' — the boundary is where one "
                "cue's range ends and the next begins.",
            )
    else:
        wanted = (after_word or "").strip(".,!?").lower()
        for i, cue in enumerate(ordered[:-1]):
            tokens = cue.get("tokens", [])
            last = tokens[-1]["text"] if tokens else cue["text"].split()[-1]
            if last.strip(".,!?").lower() == wanted:
                left, right = cue, ordered[i + 1]
                break
        if left is None or right is None:
            _error_exit_with_hint(
                command,
                f"No cue on track {track!r} ends with the word "
                f"{after_word!r}.",
                "Run 'moviestar captions dump' to see each cue's text — "
                "--after-word matches the earlier cue's final word.",
            )
    merged_text = f"{left['text']} {right['text']}"
    merged_span = parse_timecode(right["to"]) - parse_timecode(left["from"])
    if len(merged_text) > 42 or merged_span > 3.5:
        _error_exit_with_hint(
            command,
            f"Joining these cues would exceed cue limits "
            f"({len(merged_text)} chars, {round(merged_span, 2)}s; limits "
            "are 42 chars / 3.5s).",
            "Cue limits keep captions readable at the viewer's clock. "
            "Break a neighboring cue first, or leave the boundary.",
        )
    left_tokens = left.get("tokens", [])
    if not left_tokens:
        _error_exit_with_hint(
            command,
            "The earlier cue has segment timing only, so the boundary "
            "cannot be word-anchored.",
            "Retranscribe the source for word timing ('moviestar "
            "retranscribe --source <id>'), then regenerate captions.",
        )
    anchor = _caption_token_anchor(left_tokens[-1], left, command)
    existing = _duplicate_caption_edit(
        recipe, "join", anchor["source"], anchor["source_time_s"]
    )
    if existing is not None:
        _error_exit_with_hint(
            command,
            f"A join already exists after {anchor['word']!r} "
            f"({existing['id']}).",
            f"Remove it with 'moviestar captions join --remove "
            f"{existing['id']}' if you want the cues split again.",
        )
    edit = {
        "id": _next_caption_edit_id(recipe),
        "op": "join",
        **anchor,
    }
    boundary_s = parse_timecode(left["to"])
    _new_spec, records = _persist_caption_edits(
        project, spec, recipe, [*recipe.get("edits", []), edit], command
    )
    merged = next(
        (
            {
                "id": record["id"],
                "text": record["text"],
                "from": record["from"],
                "to": record["to"],
            }
            for record in records
            if parse_timecode(record["from"]) <= boundary_s
            and parse_timecode(record["to"]) >= boundary_s
        ),
        None,
    )
    click.echo(
        json.dumps(
            {
                "status": "caption_cues_joined",
                "writes_spec": True,
                "track": track,
                "edit": {
                    "id": edit["id"],
                    "op": "join",
                    "anchor": anchor,
                },
                "merged_cue": merged,
                "persistence_note": (
                    "The join is part of the recipe and word-anchored; cue "
                    "limits re-apply on every derivation."
                ),
                "hint": (
                    "Run 'moviestar captions dump' to review, or 'moviestar "
                    f"captions join --remove {edit['id']}' to split again."
                ),
            },
            indent=2,
        )
    )


@captions.command("suppress")
@project_workspace_option
@click.option("--track", default="captions", help="Caption track to edit.")
@click.option("--from", "from_tc", default=None, help="Result-time start.")
@click.option("--to", "to_tc", default=None, help="Result-time end.")
@click.option(
    "--source", "source_id", default=None,
    help="Limit the suppression to words from this source.",
)
@click.option(
    "--remove", "remove_id", default=None,
    help="Remove a stored suppression by id (see the recipe block in "
    "'captions dump').",
)
def captions_suppress(
    track: str,
    from_tc: str | None,
    to_tc: str | None,
    source_id: str | None,
    remove_id: str | None,
) -> None:
    """Never caption the words spoken in a span.

    The suppression is part of the caption recipe and is stored
    against the covered source words, so it keeps suppressing those
    words wherever timeline edits move them — and cues re-balance
    around the gap on every derivation. Use it for crosstalk, filler,
    or intentionally uncaptioned moments.

    Undo a stored suppression with --remove <edit-id>.
    """
    command = "captions suppress"
    project, spec = _overlay_project_spec(command)
    if remove_id is not None:
        if from_tc is not None or to_tc is not None:
            _error_exit(
                command, "--remove cannot be combined with --from/--to."
            )
        _remove_caption_edit(
            project, spec, track, remove_id, "suppress", command
        )
        return
    if from_tc is None or to_tc is None:
        _error_exit_with_hint(
            command,
            "Pass a result-time range with --from and --to.",
            "Example: moviestar captions suppress --from 0:01.2 --to 0:02.0",
        )
    recipe = _require_caption_recipe(spec, track, command)
    cues = _caption_track_cues(spec, track, command)
    try:
        from_s = parse_timecode(from_tc)
        to_s = parse_timecode(to_tc)
    except ValueError as exc:
        _error_exit(command, str(exc))
    if from_s >= to_s:
        _error_exit_with_hint(
            command,
            "--from must be before --to.",
            "Pass a result-time range, e.g. --from 0:01.2 --to 0:02.0. "
            "Run 'moviestar captions dump' to see cue ranges.",
        )
    covered: dict[str, list[tuple[float, str]]] = {}
    covered_split_token = False
    for cue in cues:
        for token in cue.get("tokens", []):
            token_from = parse_timecode(token["from"])
            token_to = parse_timecode(token["to"])
            token_source = token.get("source") or cue.get("source")
            if token_from >= to_s or token_to <= from_s:
                continue
            if source_id is not None and token_source != source_id:
                continue
            anchor = _caption_token_anchor(
                token, cue, command, source_token=True
            )
            covered_split_token = covered_split_token or (
                token.get("source_token_from") is not None
            )
            covered.setdefault(token_source, []).append(
                (anchor["source_time_s"], token["text"])
            )
    if not covered:
        existing_suppressions = [
            edit
            for edit in recipe.get("edits", [])
            if edit.get("op") == "suppress"
        ]
        already = ""
        if existing_suppressions:
            already = (
                " Existing suppression(s) may already cover it: "
                + "; ".join(
                    f"{edit['id']} ({', '.join(edit.get('words', []))})"
                    for edit in existing_suppressions
                )
                + "."
            )
        _error_exit_with_hint(
            command,
            f"No caption words between {from_tc} and {to_tc} on track "
            f"{track!r}"
            + (f" from source {source_id!r}" if source_id else "")
            + "." + already,
            "Run 'moviestar captions dump' to see where cues sit on the "
            "current timeline"
            + (
                " — the recipe block lists every suppression"
                if existing_suppressions
                else ""
            )
            + ".",
        )
    spans = [
        {
            "source": source,
            "from_s": round(min(t for t, _w in hits) - 0.005, 3),
            "to_s": round(max(t for t, _w in hits) + 0.005, 3),
        }
        for source, hits in sorted(covered.items())
    ]
    words = [w for _source, hits in sorted(covered.items()) for _t, w in hits]
    edit = {
        "id": _next_caption_edit_id(recipe),
        "op": "suppress",
        "spans": spans,
        "words": words,
    }
    _new_spec, records = _persist_caption_edits(
        project, spec, recipe, [*recipe.get("edits", []), edit], command
    )
    click.echo(
        json.dumps(
            {
                "status": "caption_span_suppressed",
                "writes_spec": True,
                "track": track,
                "edit": {
                    "id": edit["id"],
                    "op": "suppress",
                    "result_range": {
                        "from": format_timecode(from_s),
                        "to": format_timecode(to_s),
                    },
                    "spans": spans,
                    "source": source_id,
                },
                "suppressed_words": words,
                **(
                    {
                        "split_token_policy": (
                            "suppressing any split child suppresses its whole "
                            "source token"
                        )
                    }
                    if covered_split_token
                    else {}
                ),
                "cues_count": len(records),
                "persistence_note": (
                    "Suppressions attach to the covered source words and "
                    "keep applying wherever timeline edits move them; cues "
                    "re-balance around the gap on every derivation."
                ),
                "hint": (
                    "Run 'moviestar captions dump' to confirm the span is "
                    "gone, or 'moviestar captions suppress --remove "
                    f"{edit['id']}' to bring the words back."
                ),
            },
            indent=2,
        )
    )


@captions.command("placement")
@project_workspace_option
@click.option("--track", default="captions", help="Caption track to adjust.")
@click.option(
    "--default", "default_position", default=None,
    help="Default position for every scene.",
)
@click.option(
    "--for", "override_args", multiple=True,
    help="Override as SELECTOR=POSITION; selectors are layout:<preset> "
    "or scene:<name>. Repeatable.",
)
def captions_placement(
    track: str, default_position: str | None, override_args: tuple[str, ...]
) -> None:
    """Set where a caption track sits, per scene, without regenerating.

    \b
    Placement is policy, not per-cue coordinates:
      --default bottom                    every scene, unless overridden
      --for layout:two-up=center          every two-up scene
      --for scene:intro=top               one scene by name

    \b
    Positions: bottom, top, center, lower-third-left, lower-third-right.
    --track defaults to 'captions' (the generate default).

    Scene overrides beat layout overrides; both beat the default. The
    envelope's resolved_preview shows, per scene, which rule fired,
    the position's estimated baseline in canvas pixels, and the
    scene's result range — proof of where cues land without rendering.
    The same resolution appears in 'captions dump' and every render
    surface.
    """
    command = "captions placement"
    project, spec = _overlay_project_spec(command)
    _caption_track_cues(spec, track, command)
    if default_position is None and not override_args:
        _error_exit_with_hint(
            command,
            "Nothing to set: pass --default and/or --for overrides.",
            "Example: moviestar captions placement --default bottom "
            "--for layout:two-up=center",
        )
    default = default_position or "bottom"
    if default not in _CAPTION_PLACEMENT_POSITIONS:
        _error_exit_with_hint(
            command,
            f"Unknown position {default!r}.",
            "Positions: " + ", ".join(_CAPTION_PLACEMENT_POSITIONS) + ".",
        )
    overrides = _parse_placement_overrides(override_args, command)
    composition = spec.get("composition") or []
    scene_names = {
        scene.get("name", f"scene_{i + 1}")
        for i, scene in enumerate(composition)
    }
    layouts = {
        scene.get("layout", {}).get("preset") for scene in composition
    }
    for override in overrides:
        selector = override["selector"]
        if "scene" in selector and selector["scene"] not in scene_names:
            _error_exit_with_hint(
                command,
                f"Unknown scene {selector['scene']!r}.",
                "Scenes: " + ", ".join(sorted(scene_names)) + ".",
            )
        if "layout" in selector and selector["layout"] not in layouts:
            _error_exit_with_hint(
                command,
                f"No scene uses layout {selector['layout']!r}.",
                "Layouts in this composition: "
                + ", ".join(sorted(x for x in layouts if x)) + ".",
            )
    recipe = caption_recipe_for_track(spec, track)
    if recipe is None:
        _error_exit_with_hint(
            command,
            f"Track {track!r} is frozen: it has no caption recipe to "
            "carry a placement policy.",
            "Re-run 'moviestar captions generate' to create a derived "
            "track, or reposition frozen cues via 'moviestar overlays "
            "dump' -> 'moviestar overlays set'.",
        )
    updated = {
        **recipe,
        "position": default,
        "placement": {"default": default, "overrides": overrides},
        "cache_fingerprint": None,
    }
    records, _meta = _derive_caption_records(project, spec, updated, command)
    updated["cache_fingerprint"] = _caption_cache_fingerprint(
        project, spec, updated
    )
    new_spec = upsert_caption_recipe(spec, updated)
    new_spec, _replaced = _replace_caption_track(
        new_spec, track, records, command
    )
    click.echo(
        json.dumps(
            {
                "status": "caption_placement_set",
                "writes_spec": True,
                "track": track,
                "placement": {"default": default, "overrides": overrides},
                "resolved_preview": _caption_placement_preview(
                    project, new_spec, default, overrides
                ),
                "cues_count": len(records),
                "hint": (
                    "resolved_preview shows which rule fires per scene, "
                    "with estimated baselines. The policy is part of the "
                    "recipe and re-applies on every derivation. Verify "
                    "visually with 'moviestar screenshot --at <t>' inside "
                    "an affected scene."
                ),
            },
            indent=2,
        )
    )


@captions.command("materialize")
@project_workspace_option
@click.option("--track", default="captions", help="Caption track to freeze.")
def captions_materialize(track: str) -> None:
    """Convert a derived caption track into plain stored overlays.

    The escape hatch for arbitrary hand-editing: materialized cues are
    ordinary overlays you can edit via 'overlays dump' -> 'overlays
    set'. The cost: the track is frozen — it stops following timeline
    edits, and pacing changes warn that it is stale (the same warning
    manual overlays get).

    The recipe is kept, frozen: staleness stays queryable ('captions
    dump' reports stale: true once the timeline moves) and stored
    break/join/suppress edits are retained dormant. The frozen cues
    keep the edits' *effects*; a later 'captions generate' replaces
    the frozen recipe — and its retained edits — with a fresh one.
    """
    command = "captions materialize"
    _project, spec = _overlay_project_spec(command)
    cues = _caption_track_cues(spec, track, command)
    recipe = caption_recipe_for_track(spec, track)
    if recipe is None:
        _error_exit_with_hint(
            command,
            f"Track {track!r} is already frozen: it has no caption recipe.",
            "Frozen cues are ordinary overlays — edit them via 'moviestar "
            "overlays dump' -> 'moviestar overlays set'.",
        )
    retained_edits = recipe.get("edits", [])
    frozen = _freeze_caption_recipe(_project, spec, recipe)
    new_spec = upsert_caption_recipe(spec, frozen)
    try:
        save_spec(new_spec, command="captions materialize")
    except SpecValidationError as exc:
        _error_exit(command, str(exc))
    result = {
        "status": "caption_track_materialized",
        "writes_spec": True,
        "track": track,
        "caption_model": "materialized",
        "follows_timeline_edits": False,
        "cues_count": len(cues),
        # #412: the recipe is kept, frozen — its fingerprint makes
        # staleness queryable on dump/status, and stored edits stay
        # dormant instead of being discarded. 'captions generate'
        # replaces the frozen recipe (and its edits) with a fresh one.
        "recipe_frozen": True,
        "retained_edits_count": len(retained_edits),
        "hint": (
            "The track is now ordinary overlays: edit via 'moviestar "
            "overlays dump' -> 'moviestar overlays set'. Timeline edits "
            "no longer update it; 'moviestar captions dump' reports "
            "stale: true once the clock moves, and 'moviestar scenes "
            "motion set' warns at the moment of the change. The recipe "
            "and its edits are retained frozen; 'moviestar captions "
            "generate' returns to a derived track and replaces them."
        ),
    }
    if retained_edits:
        result["retained_edits"] = retained_edits
    click.echo(json.dumps(result, indent=2))


@captions.group("rules", invoke_without_command=True)
@click.pass_context
def caption_rules(ctx: click.Context) -> None:
    """Manage exact project-level token corrections.

    Rules persist in project metadata and are applied by every future
    ``captions generate`` call before words are grouped into cues.

    Split rules divide the source and result spans into equal timing
    slices. Their children have distinct anchors for break/join edits,
    while suppressing any child suppresses its whole source token.

    \b
    Examples:
      moviestar captions rules add --replace 'Worse with OpenClaw=works with OpenClaw'
      moviestar captions rules add --merge 'ground -truthing=ground-truthing'
      moviestar captions rules add --split 'code=coding agent'
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(
        json.dumps(
            {
                "status": "caption_rules_surface",
                "writes_spec": False,
                "commands": [
                    "moviestar captions rules add --merge 'A B=AB'",
                    "moviestar captions rules add --replace 'wrong=right'",
                    "moviestar captions rules add --replace 'Worse with "
                    "OpenClaw=works with OpenClaw'",
                    "moviestar captions rules add --split 'code=coding agent'",
                    "moviestar captions rules list",
                    "moviestar captions rules remove <rule-id>",
                ],
                "hint": (
                    "Use --merge for an exact multi-token sequence that should "
                    "become one timed token, or --replace for an exact "
                    "same-cardinality N-to-N phrase substitution. Each "
                    "replacement word keeps its matched word's timing and "
                    "provenance. Use --split to expand one token; its source "
                    "and result spans are divided into equal timing slices. "
                    "Split children have distinct break/join anchors; "
                    "suppression remains atomic to the whole source token. "
                    "Matching is case-sensitive. Add and list "
                    "report current application counts when a derived caption "
                    "track exists."
                ),
            },
            indent=2,
        )
    )


def _caption_rules_project(command: str) -> tuple[dict, list[dict]]:
    if not is_loaded():
        _no_project_error_exit(command)
    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit(command, f"Could not read project.json: {exc}")
    rules = project.get("caption_rules", [])
    try:
        validate_caption_rules(rules)
    except ValueError as exc:
        _error_exit_with_hint(
            command,
            str(exc),
            "Repair project.json's caption_rules array, or remove the field "
            "and add rules again with 'moviestar captions rules add'.",
        )
    return project, rules


def _caption_rules_current_applications(
    project: dict, rules: list[dict]
) -> dict:
    """Count applications against active recipes on the current timeline.

    Counts are deliberately derived on read instead of persisted: an "ever
    fired" bit would become stale after a cut, reorder, or source change.
    Rule CRUD remains available before captions are generated, in which case
    the envelope says that current applicability is unavailable.
    """
    rule_counts = {rule["id"]: 0 for rule in rules}
    unavailable_rules = [
        {
            "id": rule["id"],
            "applications_count": None,
            "matches_current_captions": None,
        }
        for rule in rules
    ]
    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError:
        return {
            "available": False,
            "basis": "current_derived_caption_tracks",
            "reason": "caption_spec_unavailable",
            "rules": unavailable_rules,
        }

    recipes = [
        recipe
        for recipe in spec.get("captions", [])
        if not recipe.get("frozen")
    ]
    if not recipes:
        return {
            "available": False,
            "basis": "current_derived_caption_tracks",
            "reason": "no_derived_caption_tracks",
            "rules": unavailable_rules,
        }

    import contextlib
    import io

    derived_tracks: list[str] = []
    unavailable_tracks: list[str] = []
    for recipe in recipes:
        track = recipe["track"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                _records, meta = _derive_caption_records(
                    project, spec, recipe, "captions rules list"
                )
        except SystemExit:
            unavailable_tracks.append(track)
            continue
        derived_tracks.append(track)
        for applied in meta["caption_rules_report"]["applied"]:
            rule_counts[applied["id"]] += applied["applications_count"]

    if not derived_tracks:
        return {
            "available": False,
            "basis": "current_derived_caption_tracks",
            "reason": "caption_derivation_unavailable",
            "rules": unavailable_rules,
            "unavailable_tracks": unavailable_tracks,
        }

    rule_applications = [
        {
            "id": rule["id"],
            "applications_count": rule_counts[rule["id"]],
            "matches_current_captions": rule_counts[rule["id"]] > 0,
        }
        for rule in rules
    ]
    result = {
        "available": True,
        "basis": "current_derived_caption_tracks",
        "tracks": derived_tracks,
        "rules": rule_applications,
        "unmatched_rule_ids": [
            item["id"]
            for item in rule_applications
            if not item["matches_current_captions"]
        ],
    }
    if unavailable_tracks:
        result["unavailable_tracks"] = unavailable_tracks
    return result


def _caption_rules_list_result(
    rules: list[dict], current_applications: dict | None = None
) -> dict:
    return {
        "status": "caption_rules",
        "writes_spec": False,
        "rules_count": len(rules),
        "rules": rules,
        **(
            {"current_applications": current_applications}
            if current_applications is not None
            else {}
        ),
        "hint": (
            "Rules apply automatically to current and future derived caption "
            "passes. Check current_applications for case-sensitive no-ops. "
            "Add another with 'moviestar captions rules add', or remove one "
            "with 'moviestar captions rules remove <rule-id>'."
            if rules
            else "No caption rules configured. Add one with moviestar "
            "captions rules add --replace 'A B=X Y' for a phrase, or "
            "--merge 'A B=AB' to collapse tokens, or --split 'A=B C' "
            "to expand one token."
        ),
    }


@caption_rules.command("list")
@project_workspace_option
def caption_rules_list() -> None:
    """List persistent caption token corrections."""
    project, rules = _caption_rules_project("captions rules list")
    current = _caption_rules_current_applications(project, rules)
    click.echo(json.dumps(_caption_rules_list_result(rules, current), indent=2))


@caption_rules.command("add")
@project_workspace_option
@click.option(
    "--merge",
    default=None,
    help="Exact multi-token merge as 'TOKEN TOKEN=REPLACEMENT'.",
)
@click.option(
    "--replace",
    "replace_expression",
    default=None,
    help=(
        "Exact same-cardinality substitution as "
        "'WRONG PHRASE=RIGHT PHRASE'."
    ),
)
@click.option(
    "--split",
    "split_expression",
    default=None,
    help="Expand one token with equal timing as 'ONE TOKEN=MULTIPLE TOKENS'.",
)
def caption_rules_add(
    merge: str | None,
    replace_expression: str | None,
    split_expression: str | None,
) -> None:
    """Add one persistent exact-match caption correction."""
    provided = [
        ("merge", merge),
        ("replace", replace_expression),
        ("split", split_expression),
    ]
    selected = [
        (rule_type, value)
        for rule_type, value in provided
        if value is not None
    ]
    if len(selected) != 1:
        _error_exit(
            "captions rules add",
            "Pass exactly one of --merge, --replace, or --split.",
        )
    rule_type, expression = selected[0]
    try:
        parsed = parse_caption_rule_expression(expression, rule_type)
    except ValueError as exc:
        _error_exit_with_hint(
            "captions rules add",
            str(exc),
            "Use --replace 'A B C=X Y Z' for same-cardinality N-to-N "
            "substitutions, --merge 'A B=AB' for N-to-1 corrections, or "
            "--split 'A=B C' for 1-to-N corrections.",
            supported_forms={
                "replace": "N tokens to N tokens",
                "merge": "N tokens to 1 token (N >= 2)",
                "split": "1 token to N tokens (N >= 2)",
            },
        )

    project, rules = _caption_rules_project("captions rules add")
    duplicate = next(
        (
            rule
            for rule in rules
            if all(rule[key] == parsed[key] for key in parsed)
        ),
        None,
    )
    if duplicate is not None:
        _error_exit_with_hint(
            "captions rules add",
            f"The same caption rule already exists as {duplicate['id']!r}.",
            "Run 'moviestar captions rules list' to inspect configured rules.",
        )
    rule = {"id": next_caption_rule_id(rules), **parsed}
    project["caption_rules"] = [*rules, rule]
    save_project(project, walk=True)
    current = _caption_rules_current_applications(
        project, project["caption_rules"]
    )
    current_rule = next(
        item for item in current["rules"] if item["id"] == rule["id"]
    )
    if current_rule["applications_count"] == 0:
        hint = (
            "The rule is stored but currently matches no words on derived "
            "caption tracks. Matching is case-sensitive; inspect exact text "
            "with 'moviestar captions dump'. The rule remains available for "
            "future caption passes."
        )
    elif current_rule["applications_count"] is not None:
        hint = (
            f"The rule currently applies {current_rule['applications_count']} "
            "time(s) and will re-evaluate on every future derived caption "
            "pass. Run 'moviestar captions rules list' to inspect all rules."
        )
    else:
        hint = (
            "The rule will apply automatically on every future derived "
            "caption pass. Current applicability is unavailable until a "
            "derived track exists; run 'moviestar captions generate' first."
        )
    click.echo(
        json.dumps(
            {
                "status": "added_caption_rule",
                "writes_spec": False,
                "writes_project": True,
                "rule": rule,
                "rules_count": len(project["caption_rules"]),
                "current_applications": current,
                "hint": hint,
            },
            indent=2,
        )
    )


@caption_rules.command("remove")
@project_workspace_option
@click.argument("rule_id")
def caption_rules_remove(rule_id: str) -> None:
    """Remove one persistent caption rule by ID."""
    project, rules = _caption_rules_project("captions rules remove")
    removed = next((rule for rule in rules if rule["id"] == rule_id), None)
    if removed is None:
        available = ", ".join(rule["id"] for rule in rules) or "none"
        _error_exit_with_hint(
            "captions rules remove",
            f"Unknown caption rule {rule_id!r}. Available: {available}.",
            "Run 'moviestar captions rules list' to inspect configured rules.",
        )
    project["caption_rules"] = [rule for rule in rules if rule["id"] != rule_id]
    save_project(project, walk=True)
    click.echo(
        json.dumps(
            {
                "status": "removed_caption_rule",
                "writes_spec": False,
                "writes_project": True,
                "removed_rule": removed,
                "rules_count": len(project["caption_rules"]),
                "hint": (
                    "The rule is removed. Future 'moviestar captions generate' "
                    "calls will use the remaining project rules."
                ),
            },
            indent=2,
        )
    )


def _caption_audio_windows(
    *,
    project: dict,
    spec: dict,
    command: str,
    source_filter: str | None,
) -> tuple[list[dict], list[str], str, list[dict]]:
    """Result-time audio windows for caption mapping.

    Returns ``(windows, notes, source_rule, warnings)``. Each window says which
    source range is *heard* starting at which result time:
    ``{"source", "source_from", "source_to", "result_from"}``.
    Captions follow the audio, so windows come from audio routing —
    scene ``audio_from``, the layout audio source, segment sources, or
    (with ``--source``, no composition) the source's edited timeline.
    """
    from moviestar.spec import parse_timecode_string

    composition = spec.get("composition")
    notes: list[str] = []
    warnings: list[dict] = []
    windows: list[dict] = []

    def filtered(source: str, where: str) -> bool:
        if source_filter is not None and source != source_filter:
            message = (
                f"{where} routes audio from '{source}', not the requested "
                f"--source '{source_filter}'; skipped."
            )
            notes.append(message)
            warnings.append(
                _warning(
                    "captions_skipped_source_mismatch",
                    message,
                    where=where,
                    routed_source=source,
                    requested_source=source_filter,
                )
            )
            return True
        return False

    if composition is not None and _is_scene_layout_composition(composition):
        try:
            resolved = resolve_project(project, spec)
        except ResolvedProjectError as exc:
            _error_exit(command, exc.message)
        except PacingResolutionError as exc:
            _error_exit(command, f"Could not resolve scene pacing: {exc}")
        for span in resolved.spans:
            scene_name = span.scene_name
            audio = span.audio.source_id
            if audio is None:
                message = (
                    f"Scene '{scene_name}' has no routed audio; no captions "
                    f"generated for its result range."
                )
                notes.append(message)
                warnings.append(
                    _warning(
                        "captions_skipped_scene_no_audio",
                        message,
                        scene=scene_name,
                    )
                )
                continue
            if filtered(audio, f"Scene '{scene_name}'"):
                continue
            if span.held or not span.audio.slices:
                message = (
                    f"Scene '{scene_name}' is in a pacing hold; its silence "
                    "does not generate captions."
                )
                notes.append(message)
                continue
            # One window per surviving audio slice, so words inside a cut
            # hole are never heard and words after it land on the
            # compressed result clock — exactly what the renderer plays.
            speed = span.audio.speed or 1.0
            result_cursor = span.start_s
            for slice_from, slice_to in span.audio.slices:
                windows.append(
                    {
                        "source": audio,
                        "source_from": slice_from,
                        "source_to": slice_to,
                        "result_from": result_cursor,
                        "speed": speed,
                        "scene": scene_name,
                        "where": f"scene '{scene_name}'",
                    }
                )
                result_cursor = round(
                    result_cursor + (slice_to - slice_from) / speed, 6
                )
        rule = (
            "explicit --source over scene audio routing"
            if source_filter
            else "scene audio source"
        )
        return windows, notes, rule, warnings

    if source_filter is None:
        available = ", ".join(sorted(s["id"] for s in project["sources"]))
        _error_exit_with_hint(
            command,
            "No composition to route caption timing through and no "
            "--source given.",
            f"Pass --source <id> (available: {available}) to caption one "
            "source's edited timeline, or build a composition first with "
            "'moviestar scenes set'.",
        )
    source = next(
        (s for s in project["sources"] if s["id"] == source_filter), None
    )
    if source is None:
        available = ", ".join(sorted(s["id"] for s in project["sources"]))
        _error_exit(
            command,
            f"Unknown source {source_filter!r}. Available: {available}.",
        )
    timeline = resolve_source(
        spec, source_filter, float(source["duration"]["seconds"])
    )
    cursor = 0.0
    for seg_from, seg_to in timeline.source_segments:
        windows.append(
            {
                "source": source_filter,
                "source_from": seg_from,
                "source_to": seg_to,
                "result_from": cursor,
                "where": "edited source timeline",
            }
        )
        cursor = round(cursor + (seg_to - seg_from), 3)
    return (
        windows,
        notes,
        "explicit --source over the source's edited timeline",
        warnings,
    )


def _transcript_items_for_source(
    project: dict, source_id: str, command: str
) -> dict:
    """Load one source's transcript as caption-mapper items:
    ``{"words": [...], "segments": [...]}`` with float seconds."""
    source = next(
        (s for s in project["sources"] if s["id"] == source_id), None
    )
    if source is None:
        available = ", ".join(sorted(s["id"] for s in project["sources"]))
        _error_exit(
            command,
            f"Unknown source {source_id!r}. Available: {available}.",
        )
    info = source.get("transcript")
    if not info:
        _error_exit_with_hint(
            command,
            f"Source '{source_id}' has no transcript to caption from.",
            f"Run 'moviestar retranscribe --source {source_id}' to "
            "transcribe it, or check transcription_skipped_reason in "
            "'moviestar status'.",
        )
    transcript_path = (get_project_dir() / info["path"]).resolve()
    try:
        data = json.loads(transcript_path.read_text())
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit(
            command,
            f"Could not read transcript for '{source_id}' at "
            f"{transcript_path}: {exc}",
        )
    words = [
        {
            "text": w["text"],
            "start_s": w["start"]["seconds"],
            "end_s": w["end"]["seconds"],
            "speaker": w.get("speaker"),
        }
        for w in data.get("words", [])
        if w.get("text")
    ]
    segments = [
        {
            "text": (s.get("text") or "").strip(),
            "start_s": s["start"]["seconds"],
            "end_s": s["end"]["seconds"],
        }
        for s in data.get("segments", [])
        if (s.get("text") or "").strip()
    ]
    return {"words": words, "segments": segments}


def _replace_caption_track(
    spec: dict, track: str, records: list[dict], command: str
) -> tuple[dict, int]:
    """Atomically replace one track's overlays with new records.
    Returns (new_spec, replaced_count)."""
    kept = [o for o in spec.get("overlays", []) if o["track"] != track]
    replaced = len(spec.get("overlays", [])) - len(kept)
    new_spec = set_overlays(spec, kept + records)
    try:
        save_spec(new_spec, command=command)
    except SpecValidationError as exc:
        _error_exit(command, str(exc))
    return new_spec, replaced


def _caption_records_from_cues(
    *,
    cues: list[dict],
    spec: dict,
    track: str,
    position: str,
    style: str,
    css: str | None,
    style_for: dict[str, str] | None = None,
    highlight_mode: str,
    highlight_color: str | None,
    command: str,
    cue_positions: list[str] | None = None,
) -> list[dict]:
    """Build canonical caption overlay records from result-time cues,
    through the same position/style validators `overlays add` uses.
    ``cue_positions`` (parallel to ``cues``) applies per-cue placement
    from a recipe's placement policy; ``position`` is the default."""
    kept_ids = {
        o["id"] for o in spec.get("overlays", []) if o["track"] != track
    }
    records: list[dict] = []
    position_env_cache: dict[str, dict] = {}

    def position_env(preset: str) -> dict:
        if preset not in position_env_cache:
            try:
                position_env_cache[preset] = _overlay_position_envelope(
                    preset=preset, default_preset="bottom"
                )
            except ValueError as exc:
                _error_exit(command, str(exc))
        return position_env_cache[preset]

    pos_env = position_env(position)
    style_env_cache: dict[tuple[str | None, str | None], dict] = {}

    def style_for_source(source: str | None) -> tuple[dict, str | None]:
        override_css = (style_for or {}).get(source or "")
        combined_css = _combine_css(css, override_css)
        source_highlight = _caption_highlight_color_from_css(override_css)
        base_highlight = _caption_highlight_color_from_css(css)
        cue_highlight = source_highlight or base_highlight or highlight_color
        cache_key = (source, cue_highlight)
        if cache_key not in style_env_cache:
            try:
                style_env_cache[cache_key] = _overlay_style_envelope(
                    preset=style,
                    css=combined_css,
                    highlight_color=cue_highlight,
                )
            except ValueError as exc:
                _error_exit(command, str(exc))
        return style_env_cache[cache_key], cue_highlight

    for cue_index, cue in enumerate(cues):
        overlay_id = next_overlay_id(kept_ids, "caption")
        kept_ids.add(overlay_id)
        source = cue.get("source")
        style_env, cue_highlight = style_for_source(source)
        tokens = None
        if cue.get("tokens"):
            tokens = [
                {
                    "text": token["text"],
                    "from": format_timecode(token["from_s"])["text"],
                    "to": format_timecode(token["to_s"])["text"],
                    **(
                        {"source": token["source"]}
                        if token.get("source") is not None
                        else {}
                    ),
                    **(
                        {
                            "source_from": format_timecode(
                                token["source_start_s"]
                            )["text"]
                        }
                        if token.get("source_start_s") is not None
                        else {}
                    ),
                    **(
                        {
                            "source_token_from": format_timecode(
                                token["source_token_start_s"]
                            )["text"]
                        }
                        if token.get("source_token_start_s") is not None
                        else {}
                    ),
                }
                for token in cue["tokens"]
            ]
        cue_pos_env = pos_env
        if cue_positions is not None:
            cue_pos_env = position_env(cue_positions[cue_index])
        try:
            records.append(
                _overlay_record(
                    overlay_id=overlay_id,
                    track=track,
                    kind="caption",
                    text=cue["text"],
                    from_tc=str(cue["from_s"]),
                    to_tc=str(cue["to_s"]),
                    position=cue_pos_env,
                    style=style_env,
                    z_index=100,
                    highlight={"mode": highlight_mode, "color": cue_highlight},
                    tokens=tokens,
                    source=source,
                )
            )
        except ValueError as exc:
            _error_exit(command, f"Cue at {cue['from_s']}s: {exc}")
    return records


def _caption_placement_resolver(project, spec, recipe):
    """cue-midpoint -> (position, rule) from the recipe's placement
    policy. Scene overrides beat layout overrides; both beat the
    default."""
    placement = recipe.get("placement") or {}
    default = placement.get("default") or recipe.get("position") or "bottom"
    overrides = placement.get("overrides") or []
    if not overrides:
        return lambda mid: (default, "default")
    try:
        resolved = resolve_project(project, spec)
    except (ResolvedProjectError, SpecValidationError):
        return lambda mid: (default, "default")
    if not resolved.spans:
        return lambda mid: (default, "default")

    def resolver(mid: float) -> tuple[str, str]:
        span = resolved.span_at(min(mid, resolved.duration_s))
        position, rule = default, "default"
        for override in overrides:
            selector = override["selector"]
            if selector.get("layout") and selector["layout"] == span.layout:
                position = override["position"]
                rule = f"layout:{span.layout}"
        for override in overrides:
            selector = override["selector"]
            if selector.get("scene") == span.scene_name:
                position = override["position"]
                rule = f"scene:{span.scene_name}"
        return position, rule

    return resolver


def _derive_caption_records(
    project: dict, spec: dict, recipe: dict, command: str
) -> tuple[list[dict], dict]:
    """Derive one track's cue records from its recipe against the
    current compiled timeline.

    The single derivation pipeline behind `captions generate` and the
    spec-load cache refresh: audio windows -> transcript mapping ->
    rules -> grouping -> placement -> canonical overlay records.
    Errors exit with the calling command's envelope; the refresh path
    traps them and keeps the stale cache instead.
    """
    track = recipe["track"]
    highlight = (recipe.get("highlight") or {}).get("mode", "none")
    active_color = (recipe.get("highlight") or {}).get("color")
    style_for = recipe.get("style_for") or {}
    range_spec = recipe.get("range") or {}
    range_from = range_to = None
    if range_spec.get("from") is not None:
        range_from = parse_timecode(range_spec["from"])
    if range_spec.get("to") is not None:
        range_to = parse_timecode(range_spec["to"])

    caption_rule_values = project.get("caption_rules", [])
    windows, notes, source_rule, warnings = _caption_audio_windows(
        project=project,
        spec=spec,
        command=command,
        source_filter=recipe.get("source"),
    )
    if not windows:
        _error_exit_with_hint(
            command,
            "No audio windows resolved for caption generation."
            + (f" {notes[0]}" if notes else ""),
            "Check audio routing with 'moviestar status', or pass "
            "--source <id> to pick the transcript explicitly.",
        )

    needed_sources = sorted({window["source"] for window in windows})
    transcripts = {
        sid: _transcript_items_for_source(project, sid, command)
        for sid in needed_sources
    }

    def in_range(item: dict) -> bool:
        mid = (item["start_s"] + item["end_s"]) / 2
        if range_from is not None and mid < range_from:
            return False
        if range_to is not None and mid >= range_to:
            return False
        return True

    recipe_edits = recipe.get("edits") or []
    word_sources = {sid for sid in needed_sources if transcripts[sid]["words"]}
    mapped_words, unmapped = map_words_through_windows(
        apply_suppressions(
            {sid: transcripts[sid]["words"] for sid in word_sources},
            recipe_edits,
        ),
        [w for w in windows if w["source"] in word_sources],
    )
    mapped_words, caption_rules_report = apply_caption_rules(
        [word for word in mapped_words if in_range(word)], caption_rule_values
    )
    segment_fallback_sources = [
        sid for sid in needed_sources if sid not in word_sources
    ]
    mapped_segments: list[dict] = []
    if segment_fallback_sources:
        mapped_segments, seg_unmapped = map_words_through_windows(
            {
                sid: transcripts[sid]["segments"]
                for sid in segment_fallback_sources
            },
            [w for w in windows if w["source"] in segment_fallback_sources],
        )
        unmapped.extend(seg_unmapped)
        message = (
            "Sources without word timing fell back to segment timing "
            f"({', '.join(segment_fallback_sources)}); their cues have no "
            "tokens, so spoken-word highlighting is unavailable there."
        )
        notes.append(message)
        warnings.append(
            _warning(
                "captions_segment_timing_fallback",
                message,
                sources=segment_fallback_sources,
            )
        )

    cues = group_words_into_cues(mapped_words)
    cues.extend(
        {
            "text": seg["text"],
            "from_s": seg["start_s"],
            "to_s": seg["end_s"],
            "source": seg.get("source"),
            "tokens": None,
        }
        for seg in mapped_segments
        if in_range(seg)
    )
    cues.sort(key=lambda cue: (cue["from_s"], cue["to_s"]))
    cues = rebalance_orphan_cues(cues)
    cues = apply_join_edits(apply_break_edits(cues, recipe_edits), recipe_edits)
    cues.sort(key=lambda cue: (cue["from_s"], cue["to_s"]))
    if not cues:
        _error_exit_with_hint(
            command,
            "The transcript produced no caption cues in the requested range.",
            "Check transcript word counts in 'moviestar status', widen "
            "--from/--to, or verify the audio-routed sources have speech.",
        )
    if highlight == "spoken-word":
        untokenized = sum(1 for cue in cues if not cue.get("tokens"))
        if untokenized:
            _error_exit_with_hint(
                command,
                f"{untokenized} cue(s) have segment timing only, but "
                f"spoken-word highlighting needs word timing.",
                "Re-run with --highlight none, or retranscribe the source "
                "so word timing is available ('moviestar retranscribe "
                "--source <id>').",
            )

    placement = _caption_placement_resolver(project, spec, recipe)
    cue_positions = []
    cue_rules = []
    for cue in cues:
        position, rule = placement((cue["from_s"] + cue["to_s"]) / 2)
        cue_positions.append(position)
        cue_rules.append(rule)
    records = _caption_records_from_cues(
        cues=cues,
        spec=spec,
        track=track,
        position=recipe.get("position") or "bottom",
        style=recipe.get("style") or "social-bold",
        css=recipe.get("css"),
        style_for=style_for,
        highlight_mode=highlight,
        highlight_color=active_color,
        command=command,
        cue_positions=cue_positions,
    )
    warnings.extend(
        _caption_content_warning_objects(records, source="captions")
    )
    meta = {
        "windows": windows,
        "notes": notes,
        "warnings": warnings,
        "source_rule": source_rule,
        "needed_sources": needed_sources,
        "cues": cues,
        "cue_rules": cue_rules,
        "caption_rules_report": caption_rules_report,
        "unmapped": unmapped,
    }
    return records, meta


@captions.command("generate")
@project_workspace_option
@click.option("--track", default="captions", help="Overlay track to create.")
@click.option(
    "--source",
    "source_id",
    default=None,
    help="Transcript source ID. Omit to use scene audio routing when available.",
)
@click.option(
    "--from",
    "from_tc",
    default=None,
    help="Caption only from this result-time (default: whole composition).",
)
@click.option(
    "--to",
    "to_tc",
    default=None,
    help="Caption only up to this result-time (default: whole composition).",
)
@click.option(
    "--position",
    default="bottom",
    help="Overlay position preset: bottom, top, center, lower-third-left/right.",
)
@click.option("--style", default="social-bold", help="Caption style preset.")
@click.option("--css", default=None, help="CSS-like declaration subset.")
@click.option(
    "--style-for",
    "style_for_args",
    multiple=True,
    help=(
        "Source-specific caption CSS as SOURCE=CSS. Repeat for multiple "
        "sources; accepts highlight as shorthand for -moviestar-highlight-color."
    ),
)
@click.option(
    "--highlight",
    default="none",
    type=click.Choice(["none", "spoken-word"]),
    help="Caption highlighting mode.",
)
@click.option(
    "--highlight-color",
    default=None,
    help="Active word color. Requires --highlight spoken-word. "
    "Default #ffe94a.",
)
def captions_generate(
    track: str,
    source_id: str | None,
    from_tc: str | None,
    to_tc: str | None,
    position: str,
    style: str,
    css: str | None,
    style_for_args: tuple[str, ...],
    highlight: str,
    highlight_color: str | None,
) -> None:
    """Generate a derived caption track from the project's transcript.

    Captions follow the audio: scene compositions caption each scene's
    routed audio source; without a composition, pass --source to
    caption one source's edited timeline. Word timing is preserved as
    per-cue tokens so spoken-word highlighting can be enabled.

    \b
    The track is derived: it re-computes from the current timeline on
    every render, so pacing changes, reorders, and cuts never leave
    captions stale — there is no regenerate step. Edits are part of
    the recipe and survive every timeline change:
      text fixes        moviestar captions rules add
      cue boundaries    moviestar captions break / join
      unwanted spans    moviestar captions suppress
      placement         moviestar captions placement

    Verify cues without rendering via 'moviestar captions dump'. To
    hand-edit cues as plain overlays instead, freeze the track with
    'moviestar captions materialize' (it stops following timeline
    edits). Re-running generate replaces the track and its recipe.
    """
    if highlight_color is not None and highlight != "spoken-word":
        _error_exit(
            "captions generate",
            "--highlight-color requires --highlight spoken-word. "
            "Add --highlight spoken-word or drop --highlight-color.",
        )
    active_color = (
        (highlight_color or "#ffe94a") if highlight == "spoken-word" else None
    )
    # Issue #358: a spoken-word plan that this FFmpeg build cannot
    # export must fail here, before any transcript work or spec write.
    if highlight == "spoken-word":
        _author_time_ass_preflight("captions generate")
    range_from = range_to = None
    try:
        if from_tc is not None:
            range_from = parse_timecode(from_tc)
        if to_tc is not None:
            range_to = parse_timecode(to_tc)
    except ValueError as exc:
        _error_exit("captions generate", str(exc))
    if (
        range_from is not None
        and range_to is not None
        and range_from >= range_to
    ):
        _error_exit(
            "captions generate",
            f"--from ({range_from}s) must be before --to ({range_to}s).",
        )
    style_for = _parse_caption_style_for_args(
        style_for_args, "captions generate"
    )

    project, spec = _overlay_project_spec("captions generate")
    caption_rule_values = project.get("caption_rules", [])
    try:
        validate_caption_rules(caption_rule_values)
    except ValueError as exc:
        _error_exit_with_hint(
            "captions generate",
            str(exc),
            "Repair project.json's caption_rules array, or remove the field "
            "and add rules again with 'moviestar captions rules add'.",
        )
    known_sources = {source["id"] for source in project["sources"]}
    unknown_style_sources = sorted(set(style_for) - known_sources)
    if unknown_style_sources:
        _error_exit(
            "captions generate",
            "--style-for source must name a loaded source. Unknown: "
            f"{', '.join(unknown_style_sources)}. Available: "
            f"{', '.join(sorted(known_sources))}.",
        )

    # The recipe is the authoritative caption state for this track;
    # cues are derived from it now and re-derived whenever the compiled
    # timeline, recipe, or rules change (see _refresh_caption_tracks).
    recipe = {
        "track": track,
        "source": source_id,
        "range": (
            {"from": from_tc, "to": to_tc}
            if from_tc is not None or to_tc is not None
            else None
        ),
        "position": position,
        "style": style,
        "css": css,
        "style_for": style_for,
        "highlight": {"mode": highlight, "color": active_color},
        "placement": None,
        "edits": [],
        "cache_fingerprint": None,
    }
    records, meta = _derive_caption_records(
        project, spec, recipe, "captions generate"
    )
    windows = meta["windows"]
    notes = meta["notes"]
    warnings = meta["warnings"]
    drawtext_warning = _missing_drawtext_author_warning()
    if drawtext_warning is not None:
        warnings.append(drawtext_warning)
    source_rule = meta["source_rule"]
    needed_sources = meta["needed_sources"]
    cues = meta["cues"]
    caption_rules_report = meta["caption_rules_report"]
    unmapped = meta["unmapped"]
    recipe["cache_fingerprint"] = _caption_cache_fingerprint(
        project, spec, recipe
    )
    spec = upsert_caption_recipe(spec, recipe)
    new_spec, replaced = _replace_caption_track(
        spec, track, records, "captions generate"
    )

    for span in unmapped:
        message = (
            f"{span['words']} transcript item(s) from '{span['source']}' "
            f"(source-time {span['source_span']['from_s']}s-"
            f"{span['source_span']['to_s']}s) fall outside the composition's "
            f"audio windows and were not captioned."
        )
        notes.append(message)
        warnings.append(
            _warning(
                "captions_unmapped_transcript_items",
                message,
                source=span["source"],
                transcript_items=span["words"],
                source_span=span["source_span"],
            )
        )

    result = _overlay_result(
        status="generated_captions",
        spec_overlays=new_spec["overlays"],
        shown_overlays=records,
        writes_spec=True,
        hint=(
            "This track is derived: it re-computes from the current "
            "timeline on every render, so later pacing or scene edits "
            "never require regenerating. Verify cues with 'moviestar "
            "captions dump' or 'moviestar screenshot --at <t>'. Edit via "
            "'moviestar captions rules' (text), 'break'/'join' "
            "(boundaries), 'suppress' (spans), and 'placement' (position)."
        ),
    )
    result["caption_model"] = "derived"
    result["follows_timeline_edits"] = True
    result["caption_source"] = {
        "type": "transcript",
        "source_rule": source_rule,
        "sources": needed_sources,
        "windows": [
            {
                "where": window["where"],
                "source": window["source"],
                "result_from": format_timecode(window["result_from"]),
            }
            for window in windows
        ],
    }
    result["cues_count"] = len(records)
    result["replaced_overlays"] = replaced
    result["segmentation"] = CUE_POLICY
    result["coverage"] = {
        "from": format_timecode(cues[0]["from_s"]),
        "to": format_timecode(cues[-1]["to_s"]),
    }
    result["highlight"] = {"mode": highlight, "color": active_color}
    result["caption_rules"] = caption_rules_report
    if style_for:
        counts_by_source: dict[str, int] = {}
        for record in records:
            record_source = record.get("source")
            if record_source is not None:
                counts_by_source[record_source] = (
                    counts_by_source.get(record_source, 0) + 1
                )
        result["style_for"] = {
            "key": "source_id",
            "fallback": {
                "style": style,
                "css": css,
                "highlight_color": active_color,
            },
            "applied": {
                source: {
                    "css": css_override,
                    "overlays_count": counts_by_source.get(source, 0),
                    **(
                        {"highlight_color": color}
                        if (
                            color := _caption_highlight_color_from_css(
                                css_override
                            )
                        )
                        is not None
                        else {}
                    ),
                }
                for source, css_override in style_for.items()
                if counts_by_source.get(source, 0)
            },
            "unused": [
                source
                for source in style_for
                if counts_by_source.get(source, 0) == 0
            ],
        }
    if notes:
        result["notes"] = notes
    _add_warnings(result, warnings)
    click.echo(json.dumps(result, indent=2))


@captions.command("import")
@project_workspace_option
@click.argument("caption_file")
@click.option("--track", default="captions", help="Overlay track to create.")
@click.option(
    "--position",
    default="bottom",
    help="Overlay position preset: bottom, top, center, lower-third-left/right.",
)
@click.option("--style", default="caption-default", help="Caption style preset.")
@click.option("--css", default=None, help="CSS-like declaration subset.")
def captions_import(
    caption_file: str,
    track: str,
    position: str,
    style: str,
    css: str | None,
) -> None:
    """Import SRT/VTT cues as a frozen caption track.

    Cue timing is interpreted as composition result-time. Line breaks
    inside cues are preserved. Replaces any existing overlays on
    --track; edit afterwards with 'moviestar overlays dump' ->
    'moviestar overlays set'.

    Imported tracks are frozen, not derived: an SRT/VTT file carries
    fixed timestamps with no transcript to re-derive from, so the
    track does not follow timeline edits and pacing changes warn when
    it goes stale — the same contract as 'captions materialize'. For
    captions that follow the timeline automatically, use 'moviestar
    captions generate'.
    """
    suffix = Path(caption_file).suffix.lower()
    if suffix not in {".srt", ".vtt"}:
        _error_exit(
            "captions import",
            "Caption import accepts .srt or .vtt files.",
        )
    _project, spec = _overlay_project_spec("captions import")
    abs_file = os.path.realpath(caption_file)
    try:
        content = Path(abs_file).read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, IsADirectoryError, OSError) as exc:
        _error_exit_with_hint(
            "captions import",
            f"Could not read caption file {abs_file}: {exc}",
            "Check the path; caption import expects an existing .srt or "
            ".vtt file.",
        )
    try:
        cues = parse_srt(content) if suffix == ".srt" else parse_vtt(content)
        cues, notes = normalize_cues(cues)
    except CaptionParseError as exc:
        _error_exit_with_hint(
            "captions import",
            f"Could not parse {suffix.lstrip('.')} file: {exc}",
            "Cues need 'HH:MM:SS,mmm --> HH:MM:SS,mmm' timing lines (SRT) "
            "or a WEBVTT header with 'HH:MM:SS.mmm' timing (VTT).",
        )

    cue_records = [
        {
            "text": cue["text"],
            "from_s": cue["from_s"],
            "to_s": cue["to_s"],
            "tokens": None,
        }
        for cue in cues
    ]
    records = _caption_records_from_cues(
        cues=cue_records,
        spec=spec,
        track=track,
        position=position,
        style=style,
        css=css,
        highlight_mode="none",
        highlight_color=None,
        command="captions import",
    )
    # Importing produces fixed-timestamp cues with no transcript to
    # re-derive from: the track is frozen from here. A frozen recipe
    # stub keeps staleness queryable (#412) exactly like materialize.
    spec = upsert_caption_recipe(
        spec,
        _freeze_caption_recipe(
            _project, spec, {"track": track, "imported": True, "edits": []}
        ),
    )
    new_spec, replaced = _replace_caption_track(
        spec, track, records, "captions import"
    )

    result = _overlay_result(
        status="imported_captions",
        spec_overlays=new_spec["overlays"],
        shown_overlays=records,
        writes_spec=True,
        hint=(
            "Verify with 'moviestar screenshot --at <t>' or 'moviestar "
            "watch', then edit text/timing via 'moviestar overlays dump' "
            "-> 'moviestar overlays set'. Re-running captions import "
            "replaces this track."
        ),
    )
    result["caption_import"] = {
        "file": abs_file,
        "format": suffix.lstrip("."),
        "timing": "result_time",
    }
    result["cues_count"] = len(records)
    # Overlapping cues can end after the last-starting cue, so coverage.to
    # is the max cue end, not the last cue's end.
    result["coverage"] = {
        "from": format_timecode(cues[0]["from_s"]),
        "to": format_timecode(max(cue["to_s"] for cue in cues)),
    }
    result["replaced_overlays"] = replaced
    if notes:
        result["notes"] = notes
    click.echo(json.dumps(result, indent=2))


def _overlay_project_spec(command: str) -> tuple[dict, dict]:
    """Shared load-project-and-spec preamble for the overlay commands."""
    if not is_loaded():
        _no_project_error_exit(command)
    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit(command, f"Could not read project.json: {exc}")
    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit(command, str(exc))
    return project, spec


def _normalized_overlay(raw: dict, *, existing_ids: set[str]) -> dict:
    """Rebuild one overlay from an overlays.json entry through the same
    validators `overlays add` uses. Inputs (preset, css, timing, text,
    z_index) are authoritative; `style.resolved` is derived and always
    recomputed here, so stale resolved blocks in edited files can't
    lie. Raises ValueError with a one-overlay message."""
    if not isinstance(raw, dict):
        raise ValueError("Overlay entry must be a JSON object.")
    kind = raw.get("kind", "manual")
    if kind not in {"caption", "manual"}:
        raise ValueError(
            f"Unknown overlay kind {kind!r}. Use 'caption' or 'manual'."
        )
    track = raw.get("track") or ("captions" if kind == "caption" else "titles")
    if not isinstance(track, str):
        raise ValueError("Overlay 'track' must be a string.")
    text = raw.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("Overlay 'text' must be a non-empty string.")
    for field in ("from", "to"):
        if not isinstance(raw.get(field), (str, int, float)):
            raise ValueError(f"Overlay '{field}' must be a timecode.")
    z_index = raw.get("z_index", 100 if kind == "caption" else 200)
    if isinstance(z_index, bool) or not isinstance(z_index, int):
        raise ValueError("Overlay 'z_index' must be an integer.")

    position_in = raw.get("position") or {}
    if not isinstance(position_in, dict):
        raise ValueError("Overlay 'position' must be an object.")
    margin_x = position_in.get("margin_x")
    margin_y = position_in.get("margin_y")
    for name, value in (("margin_x", margin_x), ("margin_y", margin_y)):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ValueError(f"Overlay position '{name}' must be an integer.")
    position = _overlay_position_envelope(
        preset=position_in.get("preset"),
        x=position_in.get("x"),
        y=position_in.get("y"),
        margin_x=margin_x,
        margin_y=margin_y,
        anchor=position_in.get("anchor"),
        default_preset="bottom" if kind == "caption" else "top",
    )

    style_in = raw.get("style") or {}
    if not isinstance(style_in, dict):
        raise ValueError("Overlay 'style' must be an object.")
    style = _overlay_style_envelope(
        preset=style_in.get("preset")
        or ("caption-default" if kind == "caption" else "title"),
        css=style_in.get("css"),
        highlight_color=(raw.get("highlight") or {}).get("color"),
    )

    overlay_id = raw.get("id")
    if overlay_id is None:
        overlay_id = next_overlay_id(existing_ids, kind)
    elif not isinstance(overlay_id, str) or not overlay_id:
        raise ValueError("Overlay 'id' must be a non-empty string.")
    source = raw.get("source")
    if source is not None and (not isinstance(source, str) or not source):
        raise ValueError("Overlay 'source' must be a non-empty string when set.")

    return _overlay_record(
        overlay_id=overlay_id,
        track=track,
        kind=kind,
        text=text,
        from_tc=str(raw["from"]),
        to_tc=str(raw["to"]),
        position=position,
        style=style,
        z_index=z_index,
        highlight=raw.get("highlight"),
        tokens=raw.get("tokens"),
        source=source,
    )


def _preview_timing_overlay(
    project: dict,
    spec: dict,
    raw: dict,
    *,
    existing_ids: set[str],
) -> tuple[dict, dict, list[dict]]:
    """Normalize one bulk-edit record with explicit timing when manual."""
    if not isinstance(raw, dict):
        raise ValueError("Overlay entry must be a JSON object.")
    timing = raw.get("timing")
    kind = raw.get("kind", "manual")
    if timing is None and kind == "caption":
        record = _normalized_overlay(raw, existing_ids=existing_ids)
        return record, _overlay_view(record), []
    if timing is None and kind == "manual" and all(
        field in raw for field in ("from", "to")
    ):
        timing = {
            "space": "result",
            "from": raw["from"],
            "to": raw["to"],
        }
    if not isinstance(timing, dict):
        raise ValueError("Overlay 'timing' must be an object.")
    space = timing.get("space")
    if space not in {"result", "scene"}:
        raise ValueError("Overlay timing 'space' must be 'result' or 'scene'.")
    for field in ("from", "to"):
        if not isinstance(timing.get(field), (str, int, float)):
            raise ValueError(f"Overlay timing '{field}' must be a timecode.")

    normalized_input = {key: value for key, value in raw.items() if key != "timing"}
    normalized_input["from"] = timing["from"]
    normalized_input["to"] = timing["to"]
    record = _normalized_overlay(normalized_input, existing_ids=existing_ids)
    if record["kind"] == "caption":
        return record, _overlay_view(record), []

    if space == "scene":
        scene_ref = timing.get("scene")
        if not isinstance(scene_ref, str) or not scene_ref:
            raise ValueError(
                "Scene-space overlay timing needs a scene name or stable "
                "scene ID in 'timing.scene'."
            )
        stored = next(
            (
                overlay
                for overlay in spec.get("overlays", [])
                if overlay.get("id") == raw.get("id")
                and (overlay.get("timing") or {}).get("space") == "scene"
                and (overlay.get("timing") or {}).get("scene") == scene_ref
            ),
            None,
        )
        known_scene = any(
            scene_ref in {scene.get("id"), scene.get("name")}
            for scene in (spec.get("composition") or [])
            if isinstance(scene, dict)
        )
        if stored is not None and not known_scene:
            from_s = parse_timecode(timing["from"])
            to_s = parse_timecode(timing["to"])
            if from_s < 0 or from_s >= to_s:
                raise ValueError(
                    "Scene-local overlay 'from' must be non-negative and "
                    "before 'to'."
                )
            canonical_timing = {
                "space": "scene",
                "scene": scene_ref,
                "from": format_timecode(from_s)["text"],
                "to": format_timecode(to_s)["text"],
            }
            timing_view = {
                **canonical_timing,
                "from": format_timecode(from_s),
                "to": format_timecode(to_s),
                "duration": format_timecode(to_s - from_s),
                "visibility": "detached",
                "renders": False,
            }
            warnings = []
        else:
            canonical_timing, timing_view, warnings = (
                _scene_overlay_timing_preview(
                    project,
                    spec,
                    scene_ref=scene_ref,
                    from_tc=str(timing["from"]),
                    to_tc=str(timing["to"]),
                    command="overlays set",
                )
            )
    else:
        from_s = parse_timecode(timing["from"])
        to_s = parse_timecode(timing["to"])
        canonical_timing = {
            "space": "result",
            "from": format_timecode(from_s)["text"],
            "to": format_timecode(to_s)["text"],
        }
        timing_view = {
            "space": "result",
            "from": format_timecode(from_s),
            "to": format_timecode(to_s),
            "duration": format_timecode(to_s - from_s),
        }
        warnings = []

    canonical = dict(record)
    canonical["timing"] = canonical_timing
    return canonical, _scene_timed_overlay_view(record, timing_view), warnings


@cli.group("overlays", invoke_without_command=True)
@click.pass_context
def overlays(ctx: click.Context) -> None:
    """Author timed text overlays.

    \b
    Captions, titles, lower thirds, labels, and creative emphasis text
    all share this timed overlay model. Overlays live in spec.json;
    edit them in bulk with 'overlays dump' -> edit -> 'overlays set'.

    \b
    Style presets (--style):
      caption-default, social-bold, title, lower-third

    \b
    Position presets (--position):
      bottom, top, center, lower-third-left, lower-third-right
      (or normalized --x/--y instead of a preset)

    \b
    Timing spaces:
      result (default)  --from/--to stay on the finished-video clock.
      scene             add --scene NAME_OR_ID; --from/--to become
                        scene-local and follow that stable scene.

    \b
    CSS subset (--css) accepts only these properties:
      font-family, font-size, font-weight, color, background-color,
      opacity, text-align, line-height, letter-spacing, max-width,
      padding, border-radius, text-transform, text-shadow,
      transform (rotate/scale only), transform-origin,
      -moviestar-stroke (width + color, e.g. '4px black'),
      -moviestar-highlight-color
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(
        json.dumps(
            {
                "status": "overlay_surface",
                "writes_spec": False,
                "implementation_order": [
                    "timed text overlays",
                    "caption generation/import",
                    "spoken-word highlighting",
                ],
                "css_subset": sorted(OVERLAY_CSS_PROPERTIES),
                "style_presets": sorted(OVERLAY_STYLE_PRESETS),
                "position_presets": sorted(OVERLAY_POSITION_PRESETS),
                "timing_spaces": ["result", "scene"],
                "hint": (
                    "Use 'moviestar overlays add --help' for manual timed "
                    "text, or 'moviestar captions generate --help' for the "
                    "caption launch path."
                ),
            },
            indent=2,
        )
    )


@overlays.command("add")
@project_workspace_option
@click.option("--track", default="titles", help="Overlay track name.")
@click.option("--text", required=True, help="Overlay text.")
@click.option(
    "--from",
    "from_tc",
    required=True,
    help=(
        "Start time. Scene-local with --scene; otherwise finished-video "
        "result-time."
    ),
)
@click.option(
    "--to",
    "to_tc",
    required=True,
    help=(
        "End time. Scene-local with --scene; otherwise finished-video "
        "result-time."
    ),
)
@click.option(
    "--scene",
    "scene_ref",
    default=None,
    help="Bind timing to a scene name or stable scene ID. When set, "
    "--from/--to are scene-local and the stored record follows that scene.",
)
@click.option(
    "--position",
    default=None,
    help="Position preset: bottom, top, center, lower-third-left/right. "
    "Default: top. Mutually exclusive with --x/--y.",
)
@click.option("--x", type=float, default=None, help="Normalized x (0.0-1.0).")
@click.option("--y", type=float, default=None, help="Normalized y (0.0-1.0).")
@click.option("--style", default="title", help="Overlay style preset.")
@click.option(
    "--css",
    default=None,
    help="CSS-like declaration subset; 'moviestar overlays --help' lists "
    "the accepted properties.",
)
@click.option("--z-index", default=200, type=int, help="Overlay stack order.")
def overlays_add(
    track: str,
    text: str,
    from_tc: str,
    to_tc: str,
    scene_ref: str | None,
    position: str | None,
    x: float | None,
    y: float | None,
    style: str,
    css: str | None,
    z_index: int,
) -> None:
    """Add one manual timed text overlay to the edit spec.

    Timing is result-time by default. Pass ``--scene NAME_OR_ID`` to
    make ``--from``/``--to`` scene-local; the stored record uses the
    stable scene ID so it follows renames, reordering, and pacing. Style
    comes from a preset plus optional CSS-subset overrides.
    """
    project, spec = _overlay_project_spec("overlays add")
    try:
        existing_ids = {o["id"] for o in spec.get("overlays", [])}
        record = _overlay_record(
            overlay_id=next_overlay_id(existing_ids, "manual"),
            track=track,
            kind="manual",
            text=text,
            from_tc=from_tc,
            to_tc=to_tc,
            position=_overlay_position_envelope(preset=position, x=x, y=y),
            style=_overlay_style_envelope(preset=style, css=css),
            z_index=z_index,
        )
    except ValueError as exc:
        _error_exit("overlays add", str(exc))

    warnings: list[dict] = []
    timing_view = None
    if scene_ref is not None:
        timing, timing_view, warnings = _scene_overlay_timing_preview(
            project,
            spec,
            scene_ref=scene_ref,
            from_tc=from_tc,
            to_tc=to_tc,
            command="overlays add",
        )
        record["timing"] = timing

    new_spec = append_overlay(spec, record)
    try:
        save_spec(new_spec, command="overlays add")
    except SpecValidationError as exc:
        _error_exit("overlays add", str(exc))

    result = _overlay_result(
        status="added_overlay",
        spec_overlays=new_spec["overlays"],
        shown_overlays=[record],
        writes_spec=True,
        hint=(
            "Run 'moviestar overlays dump --out overlays.json' to edit the "
            "full overlay state as JSON, or 'moviestar undo --overlays' to "
            "revert this overlay edit."
        ),
    )
    if timing_view is not None:
        result["overlays"] = [_scene_timed_overlay_view(record, timing_view)]
    drawtext_warning = _missing_drawtext_author_warning()
    if drawtext_warning is not None:
        warnings.append(drawtext_warning)
    _add_warnings(result, warnings)
    click.echo(json.dumps(result, indent=2))


@overlays.command("dump")
@project_workspace_option
@click.option(
    "--out",
    "output",
    default="overlays.json",
    help="Where to write the editable overlay state.",
)
def overlays_dump(output: str) -> None:
    """Write the complete overlay state to editable JSON.

    Edit text, timing, position, z_index, style preset, or css in the
    file, then apply it with 'moviestar overlays set <file>'. The
    style.resolved block is derived from preset + css and is recomputed
    on set, so edit the inputs, not the resolved values.

    Manual overlays nest time under ``timing``. Use
    ``space: result`` for finished-video time, or ``space: scene`` with
    ``scene: NAME_OR_ID`` for scene-local time. Validate that shape with
    ``overlays set <file> --dry-run`` before applying the file.
    """
    _, spec = _overlay_project_spec("overlays dump")
    records = spec.get("overlays", [])
    path = os.path.realpath(output)
    try:
        with open(path, "w") as fh:
            json.dump({"overlays": records}, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        _error_exit("overlays dump", f"Could not write {path}: {exc}")

    if records:
        hint = (
            f"Edit {os.path.basename(path)} (text, from/to, position, "
            "z_index, style preset/css), then run 'moviestar overlays set "
            f"{os.path.basename(path)} --dry-run' to validate and "
            "re-run without --dry-run to apply."
        )
    else:
        hint = (
            "No overlays yet. Run 'moviestar overlays add --text ... "
            "--from ... --to ...' to create one, or edit this file "
            "directly and apply it with 'moviestar overlays set'."
        )
    click.echo(
        json.dumps(
            {
                "status": "dumped_overlays",
                "writes_file": True,
                "out": path,
                "overlay_model": "timed_text_overlays",
                "tracks": sorted({o["track"] for o in records}),
                "overlays_count": len(records),
                "derived_fields_note": (
                    "style.resolved is derived from preset + css and is "
                    "recomputed on 'overlays set'."
                ),
                "hint": hint,
            },
            indent=2,
        )
    )


@overlays.command("set")
@project_workspace_option
@click.argument("overlay_file")
@click.option(
    "--dry-run",
    is_flag=True,
    help=(
        "Validate without writing spec.json; echoes normalized overlays "
        "with recomputed style.resolved."
    ),
)
def overlays_set(overlay_file: str, dry_run: bool) -> None:
    """Replace the complete overlay state from an overlays.json file.

    Runs every overlay through the same validators as 'overlays add'
    and reports all failures at once, each with its index and id.
    Replacement is atomic: nothing is written unless every overlay
    validates. --dry-run runs the identical validation without writing.
    When derived captions exist, include their cues unchanged by starting
    from 'overlays dump'; edit only the manual overlays in that file.

    Nested ``timing`` records use ``result`` or ``scene`` space. Dry-run
    resolves the same records without writing; omit it to persist them.

    \b
      {"overlays": [
        {"text": "End card", "timing": {
          "space": "result", "from": "6", "to": "8"}},
        {"text": "Chapter title", "timing": {
          "space": "scene", "scene": "chapter",
          "from": "0.2", "to": "0.8"}}
      ]}
    """
    project, spec = _overlay_project_spec("overlays set")
    path = os.path.realpath(overlay_file)
    if not os.path.exists(path):
        _error_exit_with_hint(
            "overlays set",
            f"Overlay file not found: {path}",
            "Run 'moviestar overlays dump --out overlays.json' to write "
            "the current overlay state, edit it, then re-run "
            "'moviestar overlays set overlays.json --dry-run'.",
        )
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        _error_exit_with_hint(
            "overlays set",
            f"Could not parse {path}: {exc}",
            "The overlay file must be JSON. Re-create it with "
            "'moviestar overlays dump --out overlays.json'.",
        )
    if not isinstance(data, dict) or not isinstance(data.get("overlays"), list):
        _error_exit_with_hint(
            "overlays set",
            "Overlay file must be a JSON object with an 'overlays' array.",
            "Run 'moviestar overlays dump --out overlays.json' to see "
            "the expected shape.",
        )

    raw_overlays = data["overlays"]

    # Issue #358: a highlight plan this FFmpeg build cannot export must
    # not be persisted (or validated as persistable on --dry-run).
    if any(
        isinstance(raw, dict) and raw.get("highlight") for raw in raw_overlays
    ):
        _author_time_ass_preflight("overlays set")

    # Derived caption tracks are recipe-owned: their cues must survive this
    # complete-state replacement unchanged, and hand edits would be silently
    # clobbered. Distinguish an incomplete document from a cue edit so the
    # error points at the actual recovery path. Run this before choosing the
    # legacy or Stage 5 timing parser so mixed files cannot bypass ownership.
    recipe_tracks = _caption_recipe_tracks(spec)
    if recipe_tracks:
        stored_cues = {
            overlay["id"]: overlay
            for overlay in spec.get("overlays", [])
            if overlay.get("kind") == "caption"
            and overlay.get("track") in recipe_tracks
        }
        submitted_cues = {
            raw.get("id"): raw
            for raw in raw_overlays
            if isinstance(raw, dict)
            and raw.get("kind") == "caption"
            and raw.get("track") in recipe_tracks
        }
        submitted_by_id = {
            raw.get("id"): raw
            for raw in raw_overlays
            if isinstance(raw, dict) and isinstance(raw.get("id"), str)
        }
        omitted_cue_ids = sorted(set(stored_cues) - set(submitted_by_id))
        modified_tracks = sorted(
            {
                (submitted_cues.get(cue_id) or stored_cues.get(cue_id))[
                    "track"
                ]
                for cue_id in set(stored_cues) | set(submitted_cues)
                if cue_id not in omitted_cue_ids
                and submitted_cues.get(cue_id) != stored_cues.get(cue_id)
            }
        )
        if modified_tracks:
            _error_exit_with_hint(
                "overlays set",
                f"Caption track(s) {', '.join(modified_tracks)} are derived: "
                "their cues re-derive from the caption recipe, so hand "
                "edits here would be clobbered on the next timeline "
                "change.",
                "Edit derived captions via 'moviestar captions rules', "
                "'break', 'join', 'suppress', or 'placement' - or freeze "
                "the track first with 'moviestar captions materialize' to "
                "hand-edit its cues as plain overlays.",
                reason="derived_caption_cues_modified",
                modified_tracks=modified_tracks,
            )
        if omitted_cue_ids:
            omitted_tracks = sorted(
                {stored_cues[cue_id]["track"] for cue_id in omitted_cue_ids}
            )
            _error_exit_with_hint(
                "overlays set",
                "Overlay file omits derived caption cues from track(s) "
                f"{', '.join(omitted_tracks)}, but 'overlays set' replaces "
                "the complete overlay state. Derived cues must be included "
                "unchanged.",
                "Run 'moviestar overlays dump --out overlays.json', edit "
                "the manual overlays in that complete file, then validate "
                "and apply it with 'moviestar overlays set overlays.json'.",
                reason="derived_caption_cues_omitted",
                omitted_tracks=omitted_tracks,
                omitted_cue_ids=omitted_cue_ids,
            )

    if any(isinstance(raw, dict) and "timing" in raw for raw in raw_overlays):
        given_ids = {
            raw["id"]
            for raw in raw_overlays
            if isinstance(raw, dict) and isinstance(raw.get("id"), str)
        }
        seen_ids: set[str] = set()
        assigned_ids: list[str] = []
        canonical: list[dict] = []
        views: list[dict] = []
        warnings: list[dict] = []
        errors: list[dict] = []
        for index, raw in enumerate(raw_overlays):
            raw_id = raw.get("id") if isinstance(raw, dict) else None
            try:
                record, view, record_warnings = _preview_timing_overlay(
                    project,
                    spec,
                    raw,
                    existing_ids=given_ids | seen_ids,
                )
            except ValueError as exc:
                errors.append({"index": index, "id": raw_id, "error": str(exc)})
                continue
            if raw_id is None:
                assigned_ids.append(record["id"])
            seen_ids.add(record["id"])
            canonical.append(record)
            views.append(view)
            warnings.extend(record_warnings)

        if errors:
            click.echo(
                json.dumps(
                    {
                        "error": (
                            f"{len(errors)} of {len(raw_overlays)} overlays "
                            "failed timing validation."
                        ),
                        "command": "overlays set",
                        "overlay_errors": errors,
                        "hint": (
                            f"Fix the listed overlays in {path} and re-run "
                            "with --dry-run. Every record in a timing-preview "
                            "file needs an explicit timing object."
                        ),
                    },
                    indent=2,
                )
            )
            sys.exit(1)

        duplicate_ids = sorted(
            {
                record["id"]
                for record in canonical
                if sum(item["id"] == record["id"] for item in canonical) > 1
            }
        )
        if duplicate_ids:
            _error_exit_with_hint(
                "overlays set",
                f"Duplicate overlay id(s): {', '.join(duplicate_ids)}.",
                "Each overlay needs a unique id. Rename the duplicates, or "
                "drop the id field to auto-assign one.",
            )

        result = {
            "status": "validated_overlays" if dry_run else "set_overlays",
            "dry_run": dry_run,
            "writes_spec": not dry_run,
            "input": path,
            "overlay_model": "timed_text_overlays",
            "timing_spaces": ["result", "scene"],
            "tracks": sorted({record["track"] for record in canonical}),
            "overlays_count": len(canonical),
            "render_order": _overlay_render_order(canonical),
            "overlays": views,
            "hint": (
                "Validation passed - no changes written. Re-run without "
                "--dry-run to replace the overlay state."
                if dry_run
                else "Overlay state replaced. Run 'moviestar status' to "
                "inspect resolved timing and lifecycle warnings."
            ),
        }
        if assigned_ids:
            result["assigned_ids"] = assigned_ids
        if dry_run:
            result["derived_fields_note"] = (
                "style.resolved is derived from preset + css and is "
                "recomputed on 'overlays set'."
            )
        if canonical:
            drawtext_warning = _missing_drawtext_author_warning()
            if drawtext_warning is not None:
                warnings.append(drawtext_warning)
        _add_warnings(result, warnings)
        candidate_spec = set_overlays(spec, canonical)
        try:
            overlay_state = resolve_overlays(
                resolve_project(project, candidate_spec), canonical
            )
        except (ResolvedProjectError, SpecValidationError):
            overlay_state = None
        if overlay_state is not None:
            _attach_overlay_lifecycle(result, overlay_state)
        if not dry_run:
            try:
                save_spec(candidate_spec, command="overlays set")
            except SpecValidationError as exc:
                _error_exit("overlays set", str(exc))
        click.echo(json.dumps(result, indent=2))
        return

    given_ids = {
        raw["id"]
        for raw in raw_overlays
        if isinstance(raw, dict) and isinstance(raw.get("id"), str)
    }
    seen_ids: set[str] = set()
    assigned_ids: list[str] = []
    normalized: list[dict] = []
    errors: list[dict] = []
    for index, raw in enumerate(raw_overlays):
        raw_id = raw.get("id") if isinstance(raw, dict) else None
        try:
            record = _normalized_overlay(
                raw, existing_ids=given_ids | seen_ids
            )
        except ValueError as exc:
            errors.append({"index": index, "id": raw_id, "error": str(exc)})
            continue
        if raw_id is None:
            assigned_ids.append(record["id"])
        seen_ids.add(record["id"])
        normalized.append(record)

    if errors:
        click.echo(
            json.dumps(
                {
                    "error": (
                        f"{len(errors)} of {len(raw_overlays)} overlays "
                        "failed validation."
                    ),
                    "command": "overlays set",
                    "overlay_errors": errors,
                    "hint": (
                        f"Fix the listed overlays in {path} and re-run "
                        "'moviestar overlays set' with --dry-run to "
                        "re-validate."
                    ),
                },
                indent=2,
            )
        )
        sys.exit(1)

    duplicate_ids = sorted(
        {r["id"] for r in normalized if [n["id"] for n in normalized].count(r["id"]) > 1}
    )
    if duplicate_ids:
        _error_exit_with_hint(
            "overlays set",
            f"Duplicate overlay id(s): {', '.join(duplicate_ids)}.",
            "Each overlay needs a unique id. Rename the duplicates, or "
            "drop the id field to auto-assign one.",
        )

    replaced_count = len(spec.get("overlays", []))
    result = {
        "status": "validated_overlays" if dry_run else "set_overlays",
        "dry_run": dry_run,
        "writes_spec": not dry_run,
        "input": path,
        "replaced_count": replaced_count,
        "overlay_model": "timed_text_overlays",
        "tracks": sorted({r["track"] for r in normalized}),
        "overlays_count": len(normalized),
        "render_order": _overlay_render_order(normalized),
        "hint": (
            "Validation passed - no changes written. Re-run without "
            "--dry-run to replace the overlay state."
            if dry_run
            else "Overlay state replaced. Run 'moviestar status' to see "
            "overlays alongside the composition, or 'moviestar overlays "
            "dump' to re-edit. Run 'moviestar undo --overlays' to revert."
        ),
    }
    if assigned_ids:
        result["assigned_ids"] = assigned_ids
    _add_warnings(
        result, _caption_content_warning_objects(normalized, source="overlays")
    )
    if normalized:
        drawtext_warning = _missing_drawtext_author_warning()
        if drawtext_warning is not None:
            _add_warnings(result, [drawtext_warning])
    if dry_run:
        result["overlays"] = [_overlay_view(record) for record in normalized]
        result["derived_fields_note"] = (
            "style.resolved is derived from preset + css and is recomputed "
            "on 'overlays set'."
        )

    if not dry_run:
        new_spec = set_overlays(spec, normalized)
        try:
            save_spec(new_spec, command="overlays set")
        except SpecValidationError as exc:
            _error_exit("overlays set", str(exc))

    click.echo(json.dumps(result, indent=2))


@cli.command()
@project_workspace_option
@click.option(
    "--segment",
    "segment_args",
    multiple=True,
    help="Source ID for one composition segment. Repeat once per "
    "segment; pair each with a --from / --to in the same position.",
)
@click.option(
    "--from",
    "from_tcs",
    multiple=True,
    help="Result-time start for the matching --segment.",
)
@click.option(
    "--to",
    "to_tcs",
    multiple=True,
    help="Result-time end for the matching --segment.",
)
@click.option(
    "--audio-from",
    "audio_from",
    default=None,
    help="Route audio from this source across every segment of the "
    "composition. Useful when video cuts between cams but the audio "
    "comes from one host's track. The named source must appear in "
    "--segment, and its edited result must be at least as long as the "
    "composition. Audio starts at the named source's first composition "
    "segment (the source-time reached by that segment's --from), then "
    "plays linearly from there; pre-trim sources to align that anchor "
    "with the visual window. Persists on the composition — future "
    "`export` calls honor it without re-passing. Re-run `concat` "
    "(no --audio-from) to clear.",
)
@click.option(
    "--layout",
    "layout_arg",
    default=None,
    help=(
        "Set one global preset layout for the composition. Values: "
        "single, two-up, picture-in-picture. Pair with --slot "
        "SLOT=SOURCE plus matching --from/--to values. If multiple "
        "layout slot sources have audio, --audio-from is required."
    ),
)
@click.option(
    "--slot",
    "slot_args",
    multiple=True,
    help=(
        "Assign a source to a layout slot, e.g. top=holden or inset=host. "
        "Repeat once per required slot."
    ),
)
@click.option(
    "--canvas",
    "canvas_arg",
    default=None,
    help=(
        "Set the composition output canvas. Presets: short=1080x1920, "
        "square=1080x1080, landscape=1920x1080. Custom dimensions use "
        "WIDTHxHEIGHT, e.g. 1080x1920. Persists on the composition; "
        "`export` renders the requested canvas."
    ),
)
@click.option(
    "--framing",
    "framing_args",
    multiple=True,
    help=(
        "Frame the matching --segment inside the canvas. Values: fit, "
        "fill, or fill:<anchor> where anchor is center, left, right, top, "
        "or bottom; use fill:x=<0..1> or fill:anchor=<x>,<y> for numeric "
        "crop anchors. For fill:x, 0=left, 0.5=center, 1=right. "
        "If --canvas is set and no --framing is passed, each "
        "segment defaults to fill:center. Persists on the composition; "
        "`export` applies framing per segment."
    ),
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help=(
        "Validate and preview the composition envelope without writing "
        "spec.json. Returns dry_run: true and status: "
        "'would_set_composition' or 'would_set_layout_composition'."
    ),
)
def concat(
    segment_args: tuple[str, ...],
    from_tcs: tuple[str, ...],
    to_tcs: tuple[str, ...],
    audio_from: str | None,
    layout_arg: str | None,
    slot_args: tuple[str, ...],
    canvas_arg: str | None,
    framing_args: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Build the composition — stitch segments from one or more edited
    sources into a single timeline.

    Each ``--segment`` is paired with the next ``--from`` and ``--to``
    (positional matching across the three repeated flags). Segment
    ranges are in **result-time per source** — `--segment holden
    --from 0:00 --to 0:30` means "the first 30 seconds of holden's
    *current edited result*."

    \b
      moviestar concat \\
        --segment holden --from 0:00 --to 0:30 \\
        --segment jdilla --from 0:00 --to 0:15 \\
        --segment holden --from 0:30 --to 1:00

    Each call REPLACES any existing composition. The prior composition
    is pushed onto an undo stack — `moviestar undo` (no --source)
    restores it. To clear the composition entirely, undo through to
    null.

    Composition is a SNAPSHOT: segment ranges are resolved through
    each source's current edits and stored as source-time on disk. If
    you later trim or cut a source, the composition does not change.
    Re-run `concat` to bring new edits into the final video.

    \b
    --audio-from <source-id> (multicam audio routing):
      Sets the composition's audio source to ONE named source's track,
      played continuously across every segment. Persisted on the
      composition (top-level `composition_audio_from` in spec.json)
      and surfaced in `status` + `export` envelopes. Re-run `concat`
      without --audio-from to clear. The named source must appear in
      --segment, and its edited result must be at least as long as
      the composition (otherwise audio would run out before the
      video). Audio starts at the named source's first composition
      segment (the source-time reached by that segment's --from), then
      plays linearly from there; pre-trim sources to align that anchor
      with the visual window.

    \b
    --layout <preset> and --slot <slot=source> (global layout):
      Sets one preset layout across the whole composition. Use
      `moviestar layouts --canvas short` first to inspect presets,
      slot names, and sample images. Slot ranges are in result-time per
      source and are snapshotted to source-time, matching regular
      concat semantics. For layout changes over result-time, use
      `moviestar scenes set` to author ordered scene ranges.

    \b
      moviestar concat --canvas short --layout two-up \\
        --slot top=holden --from 0:00 --to 0:30 \\
        --slot bottom=jdilla --from 0:00 --to 0:30 \\
        --audio-from holden

    \b
    --canvas <preset|WxH> and --framing <mode[:anchor]> (canvas surface):
      Sets the final output canvas and per-segment/per-slot framing on the
      composition; export renders those settings. Presets:
      short=1080x1920, square=1080x1080, landscape=1920x1080. Framing
      values are fit, fill, or fill:<anchor> where anchor is center,
      left, right, top, or bottom. Numeric fill crop anchors are
      fill:x=<0..1> for horizontal crop center or fill:anchor=<x>,<y>
      for both axes; x=0 crops from the left, x=0.5 centers, and x=1
      crops from the right. If --canvas is set and no --framing is
      passed, every segment or slot defaults to fill:center.

    \b
      moviestar scenes set --canvas short --scene clip=single \\
        --slot clip:main=speaker --from 0 --to 10 \\
        --framing 'fill:x=0.75'

    ``--dry-run`` runs the same validation and emits the same segment
    ranges, durations, audio routing, and warnings as a real concat,
    but does not write ``spec.json``. Use it to preview a composition
    before replacing the current one.
    """
    if not is_loaded():
        _no_project_error_exit("concat")

    layout_requested = layout_arg is not None or bool(slot_args)
    if layout_requested:
        if segment_args:
            _error_exit(
                "concat",
                "--layout / --slot cannot be mixed with --segment. Use "
                "one authoring surface at a time.",
            )
        if layout_arg is None:
            _error_exit("concat", "--slot requires --layout <preset>.")
        if canvas_arg is None:
            _error_exit(
                "concat",
                "--layout requires --canvas so slot regions can be resolved.",
            )
        if not slot_args:
            _error_exit(
                "concat",
                "Pass at least one --slot SLOT=SOURCE with --layout.",
            )
        if not (len(slot_args) == len(from_tcs) == len(to_tcs)):
            _error_exit(
                "concat",
                f"--slot / --from / --to counts must match. Got "
                f"{len(slot_args)} --slot, {len(from_tcs)} --from, "
                f"{len(to_tcs)} --to. Each slot needs all three.",
            )
        if framing_args and len(framing_args) != len(slot_args):
            _error_exit(
                "concat",
                f"--framing count must match --slot count for --layout. Got "
                f"{len(framing_args)} --framing for {len(slot_args)} --slot.",
            )
        try:
            project = load_project()
            spec = _load_or_init_spec(project)
            canvas = _parse_canvas_arg(canvas_arg)
            assert canvas is not None
            slots: list[tuple[str, str, float, float, dict | None]] = []
            for i, (slot_arg, from_tc, to_tc) in enumerate(
                zip(slot_args, from_tcs, to_tcs)
            ):
                slot_name, source_id = _parse_slot_arg(slot_arg)
                from_s = parse_timecode(from_tc)
                to_s = parse_timecode(to_tc)
                framing = (
                    _parse_framing_arg(framing_args[i])
                    if framing_args
                    else {"mode": "fill", "anchor": "center"}
                )
                slots.append((slot_name, source_id, from_s, to_s, framing))
            source_durations = {
                s["id"]: float(s["duration"]["seconds"])
                for s in project["sources"]
            }
            audio_codec_by_id = {
                s["id"]: s.get("audio_codec") for s in project["sources"]
            }
            visible_audio_sources = {
                source_id
                for _slot, source_id, _from, _to, _framing in slots
                if audio_codec_by_id.get(source_id)
            }
            if len(visible_audio_sources) > 1 and audio_from is None:
                _error_exit_with_hint(
                    "concat",
                    "Global layouts with multiple audio-capable sources "
                    "require --audio-from <source> so audio routing is explicit.",
                    "Choose the source whose audio should play across the "
                    "layout, then re-run with --audio-from. Available "
                    f"audio-capable slot sources: "
                    f"{', '.join(sorted(visible_audio_sources))}.",
                )
            new_spec = set_layout_composition(
                spec,
                layout=layout_arg,
                slots=slots,
                source_durations=source_durations,
                audio_from=audio_from,
                canvas=canvas,
            )
        except (json.JSONDecodeError, FileNotFoundError) as exc:
            _error_exit("concat", f"Could not read project.json: {exc}")
        except (SpecValidationError, ValueError) as exc:
            _error_exit("concat", str(exc))

        scene = new_spec["composition"][0]
        slots_out, composition_duration = _format_layout_slots(scene, canvas)
        history_len = _revision_count(new_spec, "composition")
        result = {
            "layout": _layout_envelope(
                scene["layout"], canvas, result_time_s=0.0
            ),
            "slots": slots_out,
            "slots_count": len(slots_out),
            "composition_duration": format_timecode(composition_duration),
            "operations_applied": history_len + 1,
            "canvas": canvas,
        }
        if dry_run:
            result["dry_run"] = True
            result["status"] = "would_set_layout_composition"
        if audio_from is not None:
            result["audio_from"] = audio_from
        result["hint"] = (
            (
                "Dry-run only — layout composition validated but spec.json "
                "was not changed. Re-run without --dry-run to set it."
            )
            if dry_run
            else (
                "Global layout composition saved. Use 'moviestar screenshot', "
                "'moviestar inspect', 'moviestar watch', or 'moviestar export' "
                "without --source to verify and render the composed layout."
            )
        )
        if not dry_run:
            save_spec(new_spec, command="concat")
        _attach_overlay_timeline_mutation(
            result,
            project=project,
            old_spec=spec,
            new_spec=new_spec,
            code="concat_result_clock_changed_overlays_stale",
            change_label="concat",
        )
        click.echo(json.dumps(result, indent=2))
        return

    if not segment_args and not from_tcs and not to_tcs:
        _error_exit(
            "concat",
            "Pass at least one --segment <id> --from X --to Y. Each "
            "--segment must be paired with --from and --to.",
        )

    if not (len(segment_args) == len(from_tcs) == len(to_tcs)):
        _error_exit(
            "concat",
            f"--segment / --from / --to counts must match. Got "
            f"{len(segment_args)} --segment, {len(from_tcs)} --from, "
            f"{len(to_tcs)} --to. Each segment needs all three.",
        )
    if framing_args and len(framing_args) != len(segment_args):
        _error_exit(
            "concat",
            f"--framing count must match --segment count when provided. Got "
            f"{len(framing_args)} --framing for {len(segment_args)} --segment.",
        )
    if framing_args and canvas_arg is None:
        _error_exit(
            "concat",
            "--framing requires --canvas because framing describes how a "
            "segment fits inside the output canvas.",
        )

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("concat", f"Could not read project.json: {exc}")

    # Validate every --segment names a known source up front so the
    # agent gets one clean error rather than the first bad parse.
    available = {s["id"] for s in project["sources"]}
    unknown = [sid for sid in segment_args if sid not in available]
    if unknown:
        _error_exit(
            "concat",
            f"Unknown source(s): {', '.join(repr(s) for s in unknown)}. "
            f"Available: {', '.join(sorted(available))}.",
        )

    # M14 step 1.4: --audio-from validation lives on the spec layer
    # (set_composition checks both that the named source is in the
    # segment list AND that its edited result is long enough). The CLI
    # just passes the flag through.

    source_durations: dict[str, float] = {
        s["id"]: float(s["duration"]["seconds"]) for s in project["sources"]
    }

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("concat", str(exc))

    # Parse + bundle into (source, from_s, to_s) triples.
    segments: list[tuple[str, float, float]] = []
    for sid, from_tc, to_tc in zip(segment_args, from_tcs, to_tcs):
        try:
            from_s = parse_timecode(from_tc)
            to_s = parse_timecode(to_tc)
        except ValueError as exc:
            _error_exit("concat", str(exc))
        segments.append((sid, from_s, to_s))

    try:
        canvas = _parse_canvas_arg(canvas_arg)
        if framing_args:
            framings: list[dict | None] | None = [
                _parse_framing_arg(value) for value in framing_args
            ]
        elif canvas is not None:
            framings = [{"mode": "fill", "anchor": "center"} for _ in segments]
        else:
            framings = None
    except ValueError as exc:
        _error_exit("concat", str(exc))

    try:
        new_spec = set_composition(
            spec,
            segments,
            source_durations,
            audio_from=audio_from,
            canvas=canvas,
            framings=framings,
        )
    except SpecValidationError as exc:
        _error_exit("concat", str(exc))

    segments_out, composition_duration = _format_composition_segments(
        new_spec["composition"]
    )
    history_len = _revision_count(new_spec, "composition")

    # M14 step 1.2: warn at concat time when the composition will render
    # video-only because one or more referenced sources have no audio
    # stream. The same situation is also surfaced at export time via
    # `audio_dropped_note`; raising it here lets the agent restructure
    # the composition (or accept the trade-off) before they hit render.
    audio_codec_by_id = {
        s["id"]: s.get("audio_codec") for s in project["sources"]
    }
    referenced_silent = sorted({
        sid for sid, _from, _to in segments
        if not audio_codec_by_id.get(sid)
    })

    result: dict = {
        "composition": segments_out,
        "segments_count": len(segments_out),
        "composition_duration": format_timecode(composition_duration),
        "operations_applied": history_len + 1,
    }
    if canvas is not None:
        result["canvas"] = canvas
    if dry_run:
        result["dry_run"] = True
        result["status"] = "would_set_composition"
    if audio_from is not None:
        # M14 step 1.4: persisted on the composition; export honors
        # it automatically.
        result["audio_from"] = audio_from
        audio_from_range = _audio_from_range_envelope(
            spec=new_spec,
            composition=new_spec["composition"],
            source_durations=source_durations,
            audio_from=audio_from,
        )
        # M14 step 1.5: surface the anchor (source-time of audio_from's
        # first appearance in the composition) so the agent can verify
        # audio alignment without re-reading the ffmpeg command. The
        # spec has already snapshotted result-time → source-time on
        # audio_from's own timeline.
        from moviestar.spec import parse_timecode_string as _ptcs
        for seg in new_spec["composition"]:
            if seg["source"] == audio_from:
                result["audio_from_anchor"] = format_timecode(
                    _ptcs(seg["source_from"], "composition.source_from")
                )
                break
        if audio_from_range is not None:
            result["audio_from_range"] = audio_from_range
    # silent_render_note is moot when audio_from is set to an audio-
    # bearing source — the silent segments no longer contribute audio.
    audio_from_is_audible = (
        audio_from is not None and audio_codec_by_id.get(audio_from)
    )
    if referenced_silent and not audio_from_is_audible:
        names = ", ".join(repr(s) for s in referenced_silent)
        result["silent_render_note"] = (
            f"Composition includes source(s) with no audio track ({names}). "
            f"`moviestar export` will render this composition video-only — "
            f"mixing audio with silent tracks across segments is deferred. "
            f"Re-run `moviestar concat` using only sources that all have "
            f"audio to keep audio in the rendered result, or pass "
            f"`--audio-from <source>` to route audio from one named "
            f"source across the composition."
        )
    result["hint"] = (
        (
            "Dry-run only — composition validated but spec.json was not "
            "changed. Re-run without --dry-run to set it, then run "
            "'moviestar export' (no --source) to render."
        )
        if dry_run
        else (
            "Composition snapshotted from each source's current edits. "
            "Run 'moviestar export' (no --source) to render the composition "
            "to MP4, or 'moviestar undo' to revert this concat. Re-run "
            "'moviestar concat' with a new segment list to replace; per-"
            "source edits made after this call won't affect the composition "
            "until you re-concat."
        )
    )
    if not dry_run:
        save_spec(new_spec, command="concat")
    _attach_overlay_timeline_mutation(
        result,
        project=project,
        old_spec=spec,
        new_spec=new_spec,
        code="concat_result_clock_changed_overlays_stale",
        change_label="concat",
    )
    click.echo(json.dumps(result, indent=2))


@cli.command()
@project_workspace_option
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID for multi-source projects. Optional in single-"
    "source projects (defaults to the only source).",
)
@click.option(
    "--overlays",
    "undo_overlays",
    is_flag=True,
    help="Undo only when the latest revision changed overlays/captions.",
)
@click.option(
    "--audio",
    "undo_audio",
    is_flag=True,
    help="Undo only when the latest revision changed audio.",
)
@click.option(
    "--composition",
    "undo_composition",
    is_flag=True,
    help="Undo only when the latest revision changed scenes or motion.",
)
def undo(
    source_arg: str | None,
    undo_overlays: bool,
    undo_audio: bool,
    undo_composition: bool,
) -> None:
    """Undo the last command.

    Every spec-writing command journals one revision; bare undo
    pops the most recent one atomically, restoring exactly the fields
    that command touched — coupled edits (a scenes set that reconciled
    motion, a caption edit that re-derived cues) restore together.
    Trim/cut remain stored per source and also enter the journal.

    \b
    Modes:
      (no flag)            Undo the last command, whatever it touched.
      --source <id>        Guarded undo: the latest command must be a trim
                           or cut on that source.
      --overlays           Guarded undo: succeeds only when the last
                           command touched overlays or captions.
      --audio              Guarded undo: last command touched the audio mix.
      --composition        Guarded undo: last command touched the
                           composition, canvas, routing, or motion.

    \b
    The guards never pop out of order — when the last command touched a
    different family, they error naming it, so you can bare-undo it or
    stop.

    \b
    Legacy spec files:
      The revision journal replaces composition_history, overlays_history,
      audio_mix_history, motion_history, and captions_history with
      "revisions": []. Porting preserves the current edit, but cannot
      preserve their old undo order because those separate stacks never
      recorded how commands from different families interleaved. It also
      cannot undo edits made before the port. The load error includes an
      exact jq transform template.
    """
    if not is_loaded():
        _no_project_error_exit("undo")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("undo", f"Could not read project.json: {exc}")

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("undo", str(exc))

    selected_modes = sum(
        (
            source_arg is not None,
            bool(undo_overlays),
            bool(undo_audio),
            bool(undo_composition),
        )
    )
    if selected_modes > 1:
        _error_exit_with_hint(
            "undo",
            "--source, --overlays, --audio, and --composition are mutually "
            "exclusive.",
            "Choose one state family: 'moviestar undo --audio', 'moviestar "
            "undo --overlays', 'moviestar undo --composition', or "
            "'moviestar undo --source <id>'.",
        )

    revisions = spec.get("revisions") or []
    if revisions:
        last = revisions[-1]
        touched = set(last["changed"])
        source_op = _revision_appended_source_op(spec, last)
        if source_arg is not None and (
            source_op is None or source_op[0] != source_arg
        ):
            changed_label = ", ".join(sorted(touched))
            _error_exit_with_hint(
                "undo",
                f"--source {source_arg!r} expects the last command to be a "
                "trim/cut on that source, but the last command was "
                f"'{last['command']}' (changed: {changed_label}).",
                "Run 'moviestar undo' with no flag to undo the last command, "
                "or leave it in place.",
            )
        guards = (
            (undo_overlays, {"overlays", "captions"}, "--overlays"),
            (undo_audio, {"audio_mix"}, "--audio"),
            (
                undo_composition,
                {
                    "composition",
                    "composition_audio_from",
                    "composition_canvas",
                    "composition_authored_as",
                    "opening_transition",
                    "closing_transition",
                    "motion",
                },
                "--composition",
            ),
        )
        for enabled, families, flag in guards:
            if enabled and not (touched & families):
                _error_exit_with_hint(
                    "undo",
                    f"{flag} expects the last command to have touched that "
                    f"state, but the last command was "
                    f"'{last['command']}' (changed: "
                    f"{', '.join(sorted(touched))}).",
                    "Run 'moviestar undo' with no flag to undo "
                    f"'{last['command']}', or leave it in place.",
                )
        try:
            new_spec, popped = pop_revision(spec)
        except SpecValidationError as exc:
            _error_exit("undo", str(exc))
        try:
            save_spec(new_spec, record=False)
        except SpecValidationError as exc:
            _error_exit("undo", str(exc))
        undone = {
            "command": popped["command"],
            "fields": sorted(popped["changed"]),
        }
        result = {
            "status": "command_undone",
            "writes_spec": True,
            "undone": undone,
            "revisions_remaining": len(new_spec.get("revisions") or []),
        }
        if source_op is not None:
            sid, operation = source_op
            source = _get_project_source(project, sid)
            source_duration = float(source["duration"]["seconds"])
            timeline = resolve_source(new_spec, sid, source_duration)
            undone.update(_op_for_envelope(operation))
            result.update(_timeline_output(timeline))
            result["source"] = {"id": sid, "path": source["path"]}
        more_revisions = bool(new_spec.get("revisions"))
        if source_op is not None and (
            timeline.operations_applied > 0 or more_revisions
        ):
            result["hint"] = (
                f"Undid '{popped['command']}'. Run 'moviestar trim' to add "
                "a new source edit, or 'moviestar undo' again to step "
                "further back."
            )
        elif source_op is not None:
            result["hint"] = (
                "Spec is now empty. Run 'moviestar status' to see your "
                "starting state, or 'moviestar trim --from X --to Y' to "
                "start a fresh edit."
            )
        else:
            result["hint"] = (
                f"Undid '{popped['command']}'. Run 'moviestar status' to "
                "review the restored state"
                + (
                    ", or 'moviestar undo' again to step further back."
                    if more_revisions
                    else "."
                )
            )
        _attach_overlay_timeline_mutation(
            result,
            project=project,
            old_spec=spec,
            new_spec=new_spec,
            code="undo_result_clock_changed_overlays_stale",
            change_label="undo",
        )
        click.echo(json.dumps(result, indent=2))
        return

    _error_exit_with_hint(
        "undo",
        "No command revision to undo.",
        "Run a spec-writing command first. A ported legacy edit starts with "
        "no undoable revisions; its preserved pre-port state is the new "
        "starting point.",
    )


@cli.command()
@project_workspace_option
@click.option(
    "--edit",
    "edit_path",
    type=click.Path(),
    help="Validate and replace the spec as one atomic, undoable revision.",
)
@click.option(
    "--reset",
    "reset",
    is_flag=True,
    help="Wipe the spec back to a clean empty state (no operations). "
    "Idempotent. Sibling of --edit; the two flags are mutually exclusive.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Validate everything --edit would (file exists, parses, schema, "
    "source_id) but skip writing spec.json. Returns the same envelope as "
    "the real call with status: 'would_replace' and dry_run: true.",
)
def spec(edit_path: str | None, reset: bool, dry_run: bool) -> None:
    """Show, replace, or reset the current edit spec (JSON).

    Plain ``moviestar spec`` dumps the current spec to stdout. With
    ``--edit path/to/file.json`` the spec is validated and replaced
    wholesale as one atomic revision; one ``moviestar undo`` restores
    the entire prior spec. With ``--reset`` the spec is cleared back to
    a clean empty state as one atomic revision (the typical "start over"
    hatch; safer and clearer than looping ``moviestar undo`` until it
    errors).

    Output shapes (read commands return raw state; write commands
    return a confirmation envelope):

    \b
      moviestar spec               → raw on-disk JSON. Pipe with
                                     ``| jq .`` directly. No wrapper.
      moviestar spec --edit FILE   → confirmation envelope —
                                     ``status: "replaced"``, post-write
                                     ``source_range`` / ``result_range`` /
                                     ``result_duration`` / ``operations_applied``, the new ``spec``
                                     itself, ``source: {id, path}``, and a
                                     ``hint``. Pipe needs ``| jq .spec`` to
                                     reach the spec body.
      moviestar spec --reset       → same envelope, ``status: "reset"``.
                                     Idempotent: calling on an already-
                                     empty spec succeeds with the same
                                     envelope.
      moviestar spec --edit FILE --dry-run
                                   → preview-only envelope —
                                     ``dry_run: true``, ``status:
                                     "would_replace"``, the resolved
                                     timeline a real --edit would
                                     produce, but **spec.json is not
                                     written**. Validation fires the
                                     same as a real --edit (file exists,
                                     parses, schema, source_id) so an
                                     agent constructing specs
                                     programmatically catches bugs
                                     before replacing the current edit.

    Parse errors on ``--edit`` include a structured
    ``parse_error`` sub-object alongside the top-level error
    string: ``file``, ``line``, ``column``, ``message``, and the
    literal ``offending_line`` read back from the file. Both
    ``line`` and ``column`` are 1-indexed (i.e. ``column: 19``
    means the 19th character). Lets an agent generating specs
    programmatically point at the exact char in their generation
    logic without regex-parsing the exception text.

    Note: ``spec --edit`` (and ``--reset``) require a loaded
    project — they target the current workspace, not the spec
    file in isolation. Running from outside any project surfaces
    the standard "no project" error before the spec file is read,
    even when ``--edit`` is given an absolute path. ``cd`` into
    the project (or any subdir) first, or rely on the walk-up
    rule by running from anywhere inside the project tree.
    """
    if reset and edit_path is not None:
        _error_exit_with_hint(
            "spec",
            "--reset and --edit are mutually exclusive. --reset wipes the "
            "spec; --edit replaces it with the file contents.",
            "Pick one: pass --reset alone to wipe to empty, or --edit FILE "
            "alone to replace the spec wholesale.",
        )

    # Issue #36 stage 1: --dry-run is a preview of an action. It needs
    # an action mode. spec dump (no mode flag) has no side effect to
    # preview; reset is sequenced for stage 2. Refuse loudly so an
    # agent passing --dry-run gets pointed at the supported shape
    # rather than silently getting a no-op or a stage-2 surprise.
    if dry_run and edit_path is None and not reset:
        _error_exit_with_hint(
            "spec",
            "--dry-run requires an action mode. spec's dump mode has no "
            "side effect to preview.",
            "Add --edit FILE to preview a spec replacement, or drop "
            "--dry-run to dump the current spec.",
        )
    if dry_run and reset:
        _error_exit_with_hint(
            "spec",
            "--dry-run is not yet supported on --reset.",
            "For now, use --reset directly — the operation is fast and "
            "reversible by re-applying ops via 'moviestar trim'.",
        )

    if not is_loaded():
        _no_project_error_exit("spec")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("spec", f"Could not read project.json: {exc}")

    try:
        current_spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("spec", str(exc))

    source = project["sources"][0]
    source_id = source["id"]
    source_duration = float(source["duration"]["seconds"])

    if reset:
        seed_sources = [
            {"id": s["id"], "path": s["path"]} for s in project["sources"]
        ]
        new_spec = empty_spec(seed_sources)
        try:
            new_spec = save_spec(new_spec, command="spec")
        except SpecValidationError as exc:
            _error_exit("spec", str(exc))
        new_timeline = resolve_source(new_spec, source_id, source_duration)
        result = {
            "status": "reset",
            "spec": new_spec,
            **_timeline_output(new_timeline),
            "source": {"id": source_id, "path": source["path"]},
            "hint": (
                "Spec reset to empty. Run 'moviestar trim --from X --to Y' "
                "to add an operation, 'moviestar undo' to restore the prior "
                "spec atomically, or 'moviestar spec --edit <file>' to load "
                "a complete spec from disk."
            ),
        }
        click.echo(json.dumps(result, indent=2))
        return

    if edit_path is None:
        # Dump mode — pretty-print the current spec.
        click.echo(json.dumps(current_spec, indent=2))
        return

    # --edit replace mode.
    abs_edit = os.path.realpath(edit_path)
    if not os.path.exists(abs_edit):
        _error_exit("spec", f"File not found: {abs_edit}")

    try:
        content = open(abs_edit).read()
    except OSError as exc:
        _error_exit("spec", f"Could not read {abs_edit}: {exc}")

    try:
        new_spec = json.loads(content)
    except json.JSONDecodeError as exc:
        # Issue #47: structured parse_error envelope so an agent
        # generating specs programmatically can point at the exact
        # char in their generation logic without regex-parsing the
        # exception text.
        _spec_parse_error_exit(abs_edit, content, exc)

    # Validate structure before checking semantic guards — a malformed
    # spec fails schema validation with a precise message, which is
    # more useful than a generic "source_id" complaint.
    try:
        validate_spec(new_spec)
    except SpecValidationError as exc:
        _error_exit("spec", str(exc))

    # Cross-project guard: in v0.2, the spec carries a sources list.
    # The replacement must reference the same source IDs as the project,
    # otherwise trim/undo output would report misleading metadata.
    spec_ids = sorted(source_ids(new_spec))
    project_ids = sorted(s["id"] for s in project["sources"])
    if spec_ids != project_ids:
        _error_exit(
            "spec",
            f"Spec source IDs {spec_ids} do not match project source IDs "
            f"{project_ids}. Replacing a spec across projects is not supported.",
        )

    # Issue #36 stage 1: dry-run forks here. Everything above (file
    # exists, JSON parses, schema valid, source_id matches) ran
    # identically — the load-bearing property of dry-run is that
    # validation fires the same way. Below is the side-effect
    # boundary; dry-run stops short.
    new_timeline = resolve_source(new_spec, source_id, source_duration)
    if dry_run:
        result = {
            "dry_run": True,
            "status": "would_replace",
            "spec": new_spec,
            **_timeline_output(new_timeline),
            "source": {"id": source_id, "path": source["path"]},
            "hint": (
                "Dry-run only — no spec written. Re-run without --dry-run "
                "to commit the replacement as one atomic, undoable revision. "
                "The resolved source_range / result_range "
                "above are what the real call would produce."
            ),
        }
        _attach_overlay_timeline_mutation(
            result,
            project=project,
            old_spec=current_spec,
            new_spec=new_spec,
            code="spec_result_clock_changed_overlays_stale",
            change_label="spec replacement",
        )
        click.echo(json.dumps(result, indent=2))
        return

    try:
        new_spec = save_spec(new_spec, command="spec")
    except SpecValidationError as exc:
        _error_exit("spec", str(exc))

    result = {
        "status": "replaced",
        "spec": new_spec,
        **_timeline_output(new_timeline),
        "source": {"id": source_id, "path": source["path"]},
        "hint": (
            "Spec replaced as one atomic revision. Run 'moviestar undo' "
            "once to restore the entire prior spec, or 'moviestar spec "
            "--edit <file>' again for another wholesale replacement."
        ),
    }
    _attach_overlay_timeline_mutation(
        result,
        project=project,
        old_spec=current_spec,
        new_spec=new_spec,
        code="spec_result_clock_changed_overlays_stale",
        change_label="spec replacement",
    )
    click.echo(json.dumps(result, indent=2))


@cli.command()
@project_workspace_option
def status() -> None:
    """Show project sources, edits, transcripts, and overlays at a glance.

    Read primitive — answers "where am I?" without mutating anything.
    Composes project.json metadata with the resolved edit spec so you
    can see all sources, their current result durations, transcript
    availability, and per-source operation counts in one call.

    Multi-source projects: every source's metadata + per-source edit
    state appears in the ``sources`` array. Single-source projects
    look the same with a one-element ``sources`` array.

    Use this when you've lost track of state mid-session, or before
    a destructive command like ``spec --edit`` to confirm what you're
    about to replace.
    """
    if not is_loaded():
        _no_project_error_exit("status")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("status", f"Could not read project.json: {exc}")

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("status", str(exc))

    sources_out: list[dict] = []
    total_ops = 0

    for source in project["sources"]:
        sid = source["id"]
        source_duration = float(source["duration"]["seconds"])
        timeline = resolve_source(spec, sid, source_duration)
        src_from, src_to = timeline.source_range
        total_ops += timeline.operations_applied

        section: dict = {
            "id": sid,
            "path": source["path"],
            "duration": source["duration"],
            "width": source.get("width"),
            "height": source.get("height"),
            "fps": source.get("fps"),
            # Issue #58: normalize legacy "" → null at the output boundary.
            "video_codec": source.get("video_codec") or None,
            "audio_codec": source.get("audio_codec") or None,
            "frame_interval": source.get("frame_interval"),
            "start_offset_seconds": source.get("start_offset_seconds"),
            "frames_extracted": source.get("frames_extracted"),
            "thumb_width": source.get("thumb_width", DEFAULT_THUMB_WIDTH),
        }
        if source.get("frame_extraction_skipped_reason"):
            section["frame_extraction_skipped_reason"] = source[
                "frame_extraction_skipped_reason"
            ]

        transcript_info = source.get("transcript")
        if transcript_info:
            transcript_path = (get_project_dir() / transcript_info["path"]).resolve()
            try:
                transcript_data = json.loads(transcript_path.read_text())
                section["transcript"] = {
                    "model": transcript_info.get("model"),
                    "path": str(transcript_path),
                    "word_count": len(transcript_data.get("words", [])),
                    "duration": transcript_data.get("duration"),
                }
            except (json.JSONDecodeError, FileNotFoundError):
                section["transcript"] = None
        else:
            section["transcript"] = None
            # Issue #33: normalize legacy values from older project.json files.
            section["transcription_skipped_reason"] = normalize_skip_reason(
                source.get("transcription_skipped_reason")
            )

        segments = timeline.source_segments
        edit_block: dict = {
            "operations_applied": timeline.operations_applied,
            "segments_count": len(segments),
            "source_segments": [
                _segment_envelope(seg_from, seg_to) for seg_from, seg_to in segments
            ],
            "result_range": _result_range(timeline.effective_duration),
            "result_duration": format_timecode(timeline.effective_duration),
        }
        # Friction-test catch (step 3, 2026-05-14): status's edit.source_range
        # was a single (from, to) tuple — truthful for trim-only sources but a
        # quiet lie post-cut, where the result is multiple stitched segments
        # with a hole between them. Keep source_range as a convenience for the
        # single-segment case (matches the trim envelope's shape); drop it
        # when multi-segment so agents reach for source_segments instead.
        if len(segments) == 1:
            edit_block["source_range"] = {
                "from": format_timecode(src_from),
                "to": format_timecode(src_to),
                "duration": format_timecode(src_to - src_from),
            }
        section["edit"] = edit_block
        sources_out.append(section)

    n_sources = len(sources_out)
    if total_ops == 0:
        if n_sources == 1:
            hint = (
                "No edits yet. Run 'moviestar trim --from X --to Y' to start "
                "editing, 'moviestar skim' to browse the video, or 'moviestar "
                "export' to render the source as-is."
            )
        else:
            hint = (
                f"{n_sources} sources loaded, no edits yet. "
                "Run 'moviestar trim --source <id> --from X --to Y' to start "
                "editing one source, 'moviestar layouts --canvas <preset>' "
                "to inspect layout presets, or 'moviestar concat --layout "
                "<preset> --slot SLOT=SOURCE ...' to build a global layout "
                "composition."
            )
    else:
        # Friction-test catch (step 3, 2026-05-14): when any source's result
        # is multi-segment (post-cut), nudge the agent toward `history` so
        # they see the actual source-time layout instead of only the
        # operations_applied counter.
        any_multi_segment = any(
            s["edit"]["segments_count"] > 1 for s in sources_out
        )
        history_pointer = (
            " 'moviestar history --source <id>' shows the step-by-step "
            "lineage when a cut has split a source into multiple segments."
            if any_multi_segment
            else ""
        )
        if n_sources == 1:
            hint = (
                "Run 'moviestar spec' for the operation list, 'moviestar undo' "
                "to revert the last edit, or 'moviestar export' to render."
                + history_pointer
            )
        else:
            hint = (
                "Run 'moviestar spec' for the full operation list, or "
                "'moviestar undo --source <id>' to revert one source's last "
                "edit. To render the full multi-source composition, run "
                "'moviestar concat' to set the segments, then 'moviestar "
                "export'. 'moviestar export --source <id>' renders one "
                "source's edit only."
                + history_pointer
            )

    # M13b step 4: when composition is populated, surface it as an
    # agent-friendly segment list (source + source_range + result_range +
    # duration) and nudge the hint toward `export` (no --source).
    composition_raw = spec.get("composition")
    composition_type: str | None = None
    scenes_count: int | None = None
    slots_count: int | None = None
    if composition_raw is not None:
        if _is_layout_composition(composition_raw):
            composition_canvas = spec.get("composition_canvas")
            assert composition_canvas is not None
            if _is_scene_layout_composition(composition_raw) and _concat_presented(
                spec
            ):
                # Concat-authored: present the normalized scenes as the
                # flat segment vocabulary the agent authored.
                composition_segments, composition_duration = (
                    _format_composition_segments(_segments_view(composition_raw))
                )
                composition_out = composition_segments
                segments_count = len(composition_segments)
                composition_type = "segments"
                composition_duration_out = format_timecode(composition_duration)
                hint = (
                    "Composition is populated. Run 'moviestar export' to render "
                    "the full composition to MP4, 'moviestar undo' "
                    "to revert this concat, or 'moviestar concat' with a new "
                    "segment list to replace."
                )
            elif _is_scene_layout_composition(
                composition_raw
            ) and not _layout_presented(spec):
                scene_plan = _scene_composition_plan_with_audio(
                    project=project,
                    spec=spec,
                    composition=composition_raw,
                    command="status",
                )
                composition_out = scene_plan["scenes"]
                segments_count = 0
                composition_type = "scenes"
                scenes_count = len(scene_plan["scenes"])
                slots_count = scene_plan["slots_count"]
                composition_duration_out = format_timecode(
                    scene_plan["composition_duration"]
                )
                hint = (
                    "Scene composition is populated. Review scene order and "
                    "ranges in the composition array, or run 'moviestar undo' "
                    "to revert."
                )
            else:
                scene = composition_raw[0]
                layout_slots_out, composition_duration = _format_layout_slots(
                    scene, composition_canvas
                )
                composition_out = {
                    "layout": _layout_envelope(
                        scene["layout"],
                        composition_canvas,
                        result_time_s=0.0,
                    ),
                    "slots": layout_slots_out,
                }
                segments_count = 0
                composition_type = "layout"
                scenes_count = 1
                slots_count = len(layout_slots_out)
                composition_duration_out = format_timecode(composition_duration)
                hint = (
                    "Global layout composition is populated. Layout-aware "
                    "screenshot, inspect thumbnails, watch previews, and export "
                    "are available without --source. Use 'moviestar undo' to revert."
                )
        else:
            composition_segments, composition_duration = _format_composition_segments(
                composition_raw
            )
            composition_out = composition_segments
            segments_count = len(composition_segments)
            composition_type = "segments"
            composition_duration_out = format_timecode(composition_duration)
            hint = (
                "Composition is populated. Run 'moviestar export' to render "
                "the full composition to MP4, 'moviestar undo' "
                "to revert this concat, or 'moviestar concat' with a new "
                "segment list to replace."
            )
    else:
        composition_out = None
        segments_count = 0
        composition_duration_out = None

    result = {
        "project_dir": str(get_project_dir()),
        "sources": sources_out,
        "composition": composition_out,
        "segments_count": segments_count,
        "composition_duration": composition_duration_out,
        "hint": hint,
    }
    composition_canvas = spec.get("composition_canvas")
    if composition_canvas is not None:
        result["composition_canvas"] = composition_canvas
    if composition_type is not None:
        result["composition_type"] = composition_type
    if scenes_count is not None:
        result["scenes_count"] = scenes_count
    if slots_count is not None:
        result["slots_count"] = slots_count
    # M19: surface stored overlay state so agents can see the text
    # layer at a glance without re-dumping overlays.json.
    overlays_state = spec.get("overlays") or []
    if overlays_state:
        resolved_overlay_state = None
        try:
            resolved_overlay_state = resolve_overlays(
                resolve_project(project, spec), overlays_state
            )
        except (ResolvedProjectError, SpecValidationError):
            pass
        frozen_stale = {
            recipe["track"]: _caption_staleness(project, spec, recipe)
            for recipe in spec.get("captions", [])
            if recipe.get("frozen")
        }
        result["overlays"] = _status_overlays_block(
            overlays_state,
            _caption_recipe_tracks(spec),
            frozen_stale,
            resolved_overlay_state,
        )
        if resolved_overlay_state is not None:
            _add_warnings(
                result,
                _overlay_lifecycle_warning_objects(resolved_overlay_state),
            )
    motion_state = spec.get("motion") or {"version": 1, "scenes": []}
    if motion_state.get("scenes"):
        pacing_entries = [
            entry
            for scene in motion_state["scenes"]
            for slot in scene.get("slots", [])
            for entry in slot.get("pacing", [])
        ]
        camera_entries = [
            entry
            for scene in motion_state["scenes"]
            for slot in scene.get("slots", [])
            for entry in slot.get("camera", [])
        ]
        resolved_duration = None
        if composition_raw is not None and _is_scene_layout_composition(
            composition_raw
        ):
            try:
                resolved_duration = resolve_pacing(
                    composition_raw, motion_state
                ).duration_s
            except PacingResolutionError:
                resolved_duration = None
        result["motion"] = {
            "version": motion_state["version"],
            "scenes_count": len(motion_state["scenes"]),
            "pacing_count": len(pacing_entries),
            "camera_count": len(camera_entries),
            "pacing_ids": [entry["id"] for entry in pacing_entries],
            "camera_ids": [entry["id"] for entry in camera_entries],
            "resolved_duration": (
                format_timecode(resolved_duration)
                if resolved_duration is not None else None
            ),
            "camera_render_status": "all_verification_surfaces_active",
        }
    caption_rules_state = project.get("caption_rules") or []
    if caption_rules_state:
        result["caption_rules"] = {
            "count": len(caption_rules_state),
            "ids": [
                rule.get("id") if isinstance(rule, dict) else None
                for rule in caption_rules_state
            ],
        }
    # M14 step 1.4: surface composition_audio_from when set so the agent
    # can see the audio routing decision at a glance without re-reading
    # the last concat envelope.
    composition_audio_from = spec.get("composition_audio_from")
    if composition_audio_from is not None:
        result["composition_audio_from"] = composition_audio_from
        if composition_out is not None:
            if _is_layout_composition(composition_raw) and (
                not _is_scene_layout_composition(composition_raw)
                or _layout_presented(spec)
            ):
                assert isinstance(composition_out, dict)
                audio_from_slot = next(
                    (
                        slot
                        for slot in composition_out["slots"]
                        if slot["source"] == composition_audio_from
                    ),
                    None,
                )
                if audio_from_slot is not None:
                    result["audio_from_anchor"] = audio_from_slot["source_range"][
                        "from"
                    ]
            elif composition_raw is not None:
                assert isinstance(composition_out, list)
                audio_from_segment = next(
                    (
                        seg
                        for seg in composition_out
                        if seg.get("source") == composition_audio_from
                    ),
                    None,
                )
                if audio_from_segment is not None:
                    result["audio_from_anchor"] = audio_from_segment["source_range"][
                        "from"
                    ]
                if _concat_presented(spec):
                    flat_view = _segments_view(composition_raw)
                elif not _is_layout_composition(composition_raw):
                    flat_view = composition_raw
                else:
                    # Scene-presented composition with a global route:
                    # the plain composition_audio_from field suffices.
                    flat_view = None
                if flat_view is not None:
                    source_durations = {
                        source["id"]: float(source["duration"]["seconds"])
                        for source in project["sources"]
                    }
                    audio_from_range = _audio_from_range_envelope(
                        spec=spec,
                        composition=flat_view,
                        source_durations=source_durations,
                        audio_from=composition_audio_from,
                    )
                    if audio_from_range is not None:
                        result["audio_from_range"] = audio_from_range
    # M21 schema/resolver: audio intent is always present after migration.
    # Resolve it against the same finished-video duration status reports.
    audio_intent = spec["audio_mix"]
    if composition_duration_out is not None:
        audio_result_duration = composition_duration_out["seconds"]
    elif n_sources == 1:
        audio_result_duration = sources_out[0]["edit"]["result_duration"]["seconds"]
    else:
        audio_result_duration = None
    if audio_result_duration is None:
        unresolved = _audio_summary(audio_intent)
        unresolved["resolution_status"] = "unavailable"
        unresolved["resolution_reason"] = (
            "Multi-source project has no finished composition timeline."
        )
        result["audio_mix"] = unresolved
    else:
        resolved_audio = _resolve_project_audio_mix(
            "status", spec, audio_result_duration, get_project_dir()
        )
        result["audio_mix"] = _audio_summary(resolved_audio)

    audio_edited = audio_intent != default_audio_mix()
    if audio_edited:
        if total_ops == 0 and composition_raw is None:
            result["hint"] = (
                "No visual spec edits yet, but audio edits exist. Run "
                "'moviestar audio dump --out audio.json' to review them or "
                "'moviestar undo --audio' to restore the prior mix."
            )
        else:
            result["hint"] += (
                " Audio edits also exist; run 'moviestar audio dump --out "
                "audio.json' to review them."
            )
    _add_warnings(result, _legacy_shape_warnings(spec))
    _add_warnings(result, _unrendered_transition_warnings(spec))
    click.echo(json.dumps(result, indent=2))


@cli.command()
@project_workspace_option
@click.option(
    "--clips",
    "clips",
    is_flag=True,
    help=f"Clean the generated media directory ({CLIPS_DIR}/).",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Preview what would be deleted without removing files.",
)
@click.option(
    "--force",
    "force",
    is_flag=True,
    help="Actually delete the selected generated-output directory.",
)
def clean(clips: bool, dry_run: bool, force: bool) -> None:
    """Clean generated output artifacts.

    ``moviestar/`` is internal project state and is never touched by
    this command. ``moviestar-clips/`` is the user-facing generated media
    directory used by default export output and future clip extraction.

    Destructive cleanup is explicit and non-interactive for agent use:
    run ``moviestar clean --clips --dry-run`` to preview, then
    ``moviestar clean --clips --force`` to delete.
    """
    if not clips:
        _error_exit_with_hint(
            "clean",
            "Choose a cleanup target.",
            "Pass --clips to clean the generated media directory.",
        )
    if dry_run and force:
        _error_exit_with_hint(
            "clean",
            "--dry-run and --force cannot be combined.",
            "Use --dry-run to preview or --force to delete.",
        )
    if not dry_run and not force:
        _error_exit_with_hint(
            "clean",
            "Refusing to delete generated clips without an explicit mode.",
            "Run 'moviestar clean --clips --dry-run' to preview, or "
            "'moviestar clean --clips --force' to delete.",
        )
    if not is_loaded():
        _no_project_error_exit("clean")

    target = get_project_dir().parent / CLIPS_DIR
    abs_target = target.resolve()
    files_count, bytes_count = _directory_stats(target)

    if dry_run:
        status = "would_clean" if target.exists() else "nothing_to_clean"
        result = {
            "dry_run": True,
            "status": status,
            "target": "clips",
            "path": str(abs_target),
            "would_delete_directory": target.exists(),
            "files_count": files_count,
            "bytes_count": bytes_count,
            "hint": (
                "Dry-run only — no files deleted. Re-run with --force "
                f"to delete {CLIPS_DIR}/."
                if target.exists()
                else f"No {CLIPS_DIR}/ directory exists for this workspace."
            ),
        }
        click.echo(json.dumps(result, indent=2))
        return

    if not target.exists():
        result = {
            "status": "nothing_to_clean",
            "target": "clips",
            "path": str(abs_target),
            "deleted_directory": False,
            "files_count": 0,
            "bytes_count": 0,
            "hint": f"No {CLIPS_DIR}/ directory exists for this workspace.",
        }
        click.echo(json.dumps(result, indent=2))
        return

    try:
        shutil.rmtree(target)
    except OSError as exc:
        _error_exit("clean", f"Could not delete {abs_target}: {exc}")

    result = {
        "status": "cleaned",
        "target": "clips",
        "path": str(abs_target),
        "deleted_directory": True,
        "files_count": files_count,
        "bytes_count": bytes_count,
        "hint": (
            f"Deleted {CLIPS_DIR}/. Future default exports will recreate it."
        ),
    }
    click.echo(json.dumps(result, indent=2))


def _segment_envelope(src_from: float, src_to: float) -> dict:
    """Format one source-time segment for history's JSON envelope.
    Dict timecodes per the moviestar convention."""
    return {
        "source_from": format_timecode(src_from),
        "source_to": format_timecode(src_to),
        "duration": format_timecode(round(src_to - src_from, 3)),
    }


def _history_text(
    source_id: str,
    source_path: str,
    source_duration: float,
    steps: tuple,
) -> str:
    """Render the lineage as a compact human-readable table.

    Each step is one line: "step N  <op>  → <result_duration>". When a
    step's result has multiple segments (post-cut), the source-time
    segments are listed indented below so an agent eyeballing the
    output sees the seam layout.
    """
    lines: list[str] = []
    src_dur_text = format_timecode(source_duration)["text"]
    lines.append(f"source: {source_path} ({src_dur_text})")
    lines.append("")
    for step in steps:
        timeline = step.timeline
        result_text = format_timecode(timeline.effective_duration)["text"]
        if step.op is None:
            op_label = "original"
        else:
            op = step.op
            op_label = f"{op['type']:<4} {op['from']}..{op['to']}"
        lines.append(f"step {step.op_index}  {op_label:<38}  → {result_text}")
        segs = timeline.source_segments
        if len(segs) > 1:
            # Multi-segment result — list each so the seam is visible.
            for src_from, src_to in segs:
                seg_from = format_timecode(src_from)["text"]
                seg_to = format_timecode(src_to)["text"]
                lines.append(f"{'':<48}    [src {seg_from}..{seg_to}]")
        else:
            # Single segment — one inline line.
            src_from, src_to = segs[0]
            seg_from = format_timecode(src_from)["text"]
            seg_to = format_timecode(src_to)["text"]
            lines.append(f"{'':<48}    [src {seg_from}..{seg_to}]")
    return "\n".join(lines)


@cli.command()
@project_workspace_option
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID for multi-source projects. Optional in single-"
    "source projects (defaults to the only source).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "text"]),
    default="json",
    show_default=True,
    help="Output shape. 'json' (default) returns the full envelope with "
    "per-step operations and source-time segments. 'text' renders a "
    "human-readable table for eyeballing or grep.",
)
def history(source_arg: str | None, fmt: str) -> None:
    """Show the step-by-step lineage of one source's edits.

    Read-only — answers "how did this source get to its current state?"
    Each step in the response is the result *after* the op at that
    index has been applied. Step 0 is the initial (pre-op) state; a
    source with N ops produces N+1 steps.

    After a cut, a source's result is internally a list of source-time
    segments stitched together. History exposes that list via
    ``source_segments`` per step so an agent debugging an unexpected
    export can see exactly which bytes of the original file feed each
    part of the current result.

    Use this when:
    - You're confused about why ``result_duration`` is what it is.
    - You want to audit the ops on a source before re-running ``concat``.
    - A cut composed across a prior seam and you want to see the layout.

    For project-wide orientation (which sources exist, transcripts,
    composition status), use ``moviestar status`` — history is the
    per-source deep-dive.
    """
    if not is_loaded():
        _no_project_error_exit("history")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("history", f"Could not read project.json: {exc}")

    source_id = _resolve_source_arg(project, source_arg, "history")
    source = _get_project_source(project, source_id)
    source_duration = float(source["duration"]["seconds"])

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("history", str(exc))

    steps = resolve_source_history(spec, source_id, source_duration)

    if fmt == "text":
        click.echo(
            _history_text(
                source_id=source_id,
                source_path=source["path"],
                source_duration=source_duration,
                steps=steps,
            )
        )
        return

    steps_out: list[dict] = []
    for step in steps:
        timeline = step.timeline
        steps_out.append(
            {
                "op_index": step.op_index,
                "op": _op_for_envelope(step.op) if step.op is not None else None,
                "result_duration": format_timecode(timeline.effective_duration),
                "source_segments": [
                    _segment_envelope(src_from, src_to)
                    for src_from, src_to in timeline.source_segments
                ],
            }
        )

    last_step = steps_out[-1]
    if last_step["op"] is None:
        hint = (
            "No edits yet. Run 'moviestar trim --from X --to Y' or "
            "'moviestar cut --from X --to Y' to start editing this source."
        )
    else:
        hint = (
            "Run 'moviestar undo' to pop the last op, or 'moviestar export' "
            "to render this source's current edit."
        )

    result = {
        "source": {
            "id": source_id,
            "path": source["path"],
            "source_duration": format_timecode(source_duration),
        },
        "steps": steps_out,
        "hint": hint,
    }
    click.echo(json.dumps(result, indent=2))


# ---------- Transcript search ----------


def _format_match(
    match: dict,
    *,
    source_id: str,
    segments: tuple[tuple[float, float], ...],
    source_only: bool,
    transcript: dict | None = None,
    context_segments: int = 0,
) -> dict:
    """Wrap a raw search-result match into the response shape.

    Adds ``source`` (which source's transcript matched), ``source_range``
    (verbose dict shape), and ``result_range`` translated through that
    source's segment list. Walks the segments so matches in the second
    segment of a cut source still get a correct result-time, and
    matches falling inside a cut hole (or outside any segment) get a
    null ``result_range``. ``excluded_by_trim`` mirrors that null in
    ``--source`` mode for programmatic branching.
    """
    m_from, m_to = match["source_range"]
    mapped = source_range_in_result(segments, m_from, m_to)
    excluded = mapped is None
    out: dict = {
        "source": source_id,
        "text": match["text"],
        "score": match["score"],
        "source_range": {
            "from": format_timecode(m_from),
            "to": format_timecode(m_to),
            "duration": format_timecode(m_to - m_from),
        },
    }
    # Issue #176: widen-to-segment range for sentence-level clip boundaries.
    seg_from, seg_to = match.get("segment_range", match["source_range"])
    out["segment_range"] = {
        "from": format_timecode(seg_from),
        "to": format_timecode(seg_to),
        "duration": format_timecode(seg_to - seg_from),
    }
    # Issue #118: stable "right spot" signal for borderline fuzzy scores —
    # the longest verbatim run of the query that landed here (or null).
    out["contiguous_match"] = match.get("contiguous_match")
    if mapped is None:
        out["result_range"] = None
    else:
        rt_from, rt_to = mapped
        out["result_range"] = {
            "from": format_timecode(rt_from),
            "to": format_timecode(rt_to),
            "duration": format_timecode(rt_to - rt_from),
        }
    if source_only:
        out["excluded_by_trim"] = excluded
    if "suggested_query" in match:
        out["suggested_query"] = match["suggested_query"]
    if transcript is not None and context_segments > 0:
        out.update(_match_context_fields(transcript, m_from, m_to, context_segments))
    return out


def _match_context_fields(
    transcript: dict, match_from: float, match_to: float, context_segments: int
) -> dict:
    """Return adjacent transcript segments around a find match.

    Issue #175: each context element is a {text, start, end} segment
    object (verbose timecodes), not flattened prose — clip-boundary
    selection is the main reason agents ask for context, and prose
    strings forced a second read of the transcript JSON for the times.
    """
    segments = transcript.get("segments") or []
    before = [
        _context_segment(seg)
        for seg in segments
        if seg["end"]["seconds"] <= match_from + 0.01
        and (seg.get("text") or "").strip()
    ][-context_segments:]
    after = [
        _context_segment(seg)
        for seg in segments
        if seg["start"]["seconds"] >= match_to - 0.01
        and (seg.get("text") or "").strip()
    ][:context_segments]

    out: dict = {}
    if before:
        out["context_before"] = before
    if after:
        out["context_after"] = after
    return out


def _context_segment(seg: dict) -> dict:
    return {
        "text": (seg.get("text") or "").strip(),
        "start": format_timecode(seg["start"]["seconds"]),
        "end": format_timecode(seg["end"]["seconds"]),
    }


def _find_suggested_query(matches: list[dict]) -> dict | None:
    for match in matches:
        if "suggested_query" in match:
            return match["suggested_query"]
    return None


def _find_suggested_query_hint(suggested_query: dict) -> str:
    query_arg = json.dumps(suggested_query["query"])
    return (
        f" A tighter query scored {suggested_query['score']} inside this "
        f"timecode; try 'moviestar find {query_arg}' if you want the "
        f"higher-confidence wording."
    )


def _find_hint(
    matches: list[dict],
    best_score: int | None,
    *,
    source_only: bool,
    exact: bool,
) -> str:
    """State-aware hint per #27/#78. Branches on best_score band, mode,
    and whether the empty result was caused by --exact or by the
    result-time scope filter."""
    if best_score is None:
        # Empty result. Three real cases:
        # - --exact mode, phrase not literally in transcript.
        # - Fuzzy + default scope, but every match was in a trimmed-away
        #   region (D4 filter dropped them all).
        # - Fuzzy + --source, transcript empty (rare; would need to be
        #   wordless).
        if exact:
            return (
                "No exact matches. Drop --exact to fuzzy-search, or run "
                "'moviestar skim --words' to inspect the transcript directly."
            )
        if not source_only:
            return (
                "No matches in the current edit's range. Pass --source to "
                "search the full original transcript including trimmed-away "
                "regions, or run 'moviestar undo' to widen the edit."
            )
        return (
            "No matches at all in this transcript. Try a shorter query, or "
            "run 'moviestar skim --words' to inspect the transcript directly."
        )
    if best_score < STRONG_WARNING_THRESHOLD:
        # < 50: probably noise. Strong warning.
        base = (
            f"No matches found that closely resemble your query "
            f"(best score: {best_score}). The phrase may not be in this "
            f"transcript. Try a shorter query, or run 'moviestar skim "
            f"--words' to inspect the transcript directly."
        )
        if not source_only:
            base += " Pass --source to also search trimmed-away regions."
        return base
    if best_score < WARNING_THRESHOLD:
        # 50-69: weak. Warn but offer it.
        base = (
            f"No close matches above the fuzzy threshold "
            f"(best score: {best_score}). Best available shown — verify "
            f"the 'text' field (and 'contiguous_match' for the verbatim "
            f"slice of your query that landed) before trimming."
        )
        if not source_only:
            base += " Pass --source to also search trimmed-away regions."
        return base
    # >= 70: solid. Use the top match's concrete from/to in the
    # suggested command — agents copy-paste the hint, so a runnable
    # example beats placeholders. Friction-test feedback (2026-05-04).
    top = matches[0]
    top_from = top["source_range"]["from"]["text"]
    top_to = top["source_range"]["to"]["text"]
    seg_from = top["segment_range"]["from"]["text"]
    seg_to = top["segment_range"]["to"]["text"]
    # Issue #177: offer both next steps — trim mutates the edit spec,
    # clip/batch extract standalone files. A trim-only hint walked one
    # agent into 15 spec mutations on a fan-cut task.
    return (
        f"To build the edit, pipe a match into trim with --snap-to-words "
        f"for clean boundaries — e.g. for the top match: 'moviestar trim "
        f"--from {top_from} --to {top_to} --snap-to-words'. To extract a "
        f"match as its own file instead (no edit-spec change), use "
        f"'moviestar clip --from {top_from} --to {top_to}' — or 'moviestar "
        f"batch' for several. For whole-sentence boundaries, use the match's "
        f"segment_range instead (e.g. --from {seg_from} --to {seg_to})."
    )


@cli.command("find")
@project_workspace_option
@click.argument("query")
@click.option(
    "--exact",
    is_flag=True,
    help="Exact substring match instead of fuzzy. May return zero matches.",
)
@click.option(
    "--limit",
    type=int,
    default=5,
    show_default=True,
    help="Maximum matches to return.",
)
@click.option(
    "--source",
    "source_only",
    is_flag=True,
    help="Search the full original transcript including trimmed-away "
    "regions. Default scope is the current edit's surviving range.",
)
@click.option(
    "--context",
    "context_segments",
    type=int,
    default=0,
    show_default=True,
    help=(
        "Include this many adjacent transcript segments before/after each "
        "match as context_before/context_after — arrays of {text, start, "
        "end} segment objects with timecodes usable as clip boundaries. "
        "Default 0 preserves the compact match shape."
    ),
)
@click.option(
    "--include-low-score",
    is_flag=True,
    help=(
        "Return fuzzy candidates even when the best score is below the "
        "70-point match threshold. Default is a structured no-match "
        "envelope for low-score fuzzy results."
    ),
)
def find_cmd(
    query: str,
    exact: bool,
    limit: int,
    source_only: bool,
    context_segments: int,
    include_low_score: bool,
) -> None:
    """Fuzzy-search the project's transcript for a phrase.

    Returns matches with their source_range, segment_range, result_range,
    and a fuzzy match score (0-100). Default mode is fuzzy because the
    human asking for the clip will hear the audio differently from how
    Whisper transcribed it ("get to the pain" vs "get to *that*
    pain"). Pass --exact when you know the transcript wording.

    \b
    Ranges:
      source_range  — the matched words' span on the source timeline.
      segment_range — that span widened to the enclosing transcript
                      sentence/segment(s). Use it for clean clip
                      boundaries on the clip/batch fan-cut path instead
                      of hand-widening a word-narrow source_range.
      result_range  — the span translated to the current edit timeline.

    \b
    Scope:
      Default — searches only the surviving result-time transcript.
                Matches in trimmed-away regions don't appear.
      --source — searches the full original transcript. Matches in
                 trimmed-away regions are flagged with
                 excluded_by_trim: true and result_range: null.

    Low-score fuzzy matches: when the best score is below 70, the
    default response is a structured no-match envelope with
    ``no_matches_above_threshold: true``. Pass ``--include-low-score``
    to inspect the best available candidates anyway.

    \b
    Score interpretation (rapidfuzz partial_ratio, 0-100):
      90+   near-exact match (minor word swap or punctuation drift)
      70-89 solid fuzzy match
      50-69 weak partial overlap, may be the wrong phrase (warning hint)
      <50   probably noise (strong warning hint)

    Long literal queries can score lower than a tighter sub-query that
    hits the same spot, so a borderline score reads as "maybe wrong".
    Each match's ``contiguous_match`` field reports the longest verbatim
    run of your query that landed there ({text, tokens, query_coverage},
    or null) — a stable "right spot" signal: trust a borderline score
    when contiguous_match covers the distinctive part of your query.
    """
    if not is_loaded():
        _no_project_error_exit("find")

    if not query.strip():
        _error_exit("find", "Pass a non-empty query string.")

    if limit < 1:
        _error_exit("find", "--limit must be at least 1.")
    if context_segments < 0:
        _error_exit("find", "--context must be 0 or greater.")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("find", f"Could not read project.json: {exc}")

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("find", str(exc))

    # Iterate over every source. For each: load transcript, search,
    # tag matches with that source's id and its specific result-range
    # mapping. Sources without transcripts get reported in
    # `sources_skipped` so agents see partial coverage.
    all_matches: list[dict] = []
    sources_searched: list[str] = []
    sources_skipped: list[dict] = []

    for source in project["sources"]:
        sid = source["id"]
        transcript = load_transcript(source)
        if transcript is None:
            skip_reason = source.get("transcription_skipped_reason") or "unknown"
            sources_skipped.append({
                "source": sid,
                "transcription_skipped_reason": skip_reason,
            })
            continue

        sources_searched.append(sid)
        source_duration = float(source["duration"]["seconds"])
        timeline = resolve_source(spec, sid, source_duration)
        segments = timeline.source_segments

        # Use a generous per-source limit so we have headroom to
        # combine + re-sort across sources before applying the final
        # global --limit cap.
        per_source_limit = max(limit * 2, limit + 5)
        raw = search_transcript(transcript, query, exact=exact, limit=per_source_limit)

        if not source_only:
            # Default scope: only keep matches that survive contiguously
            # in the result. A match in a cut hole — or spanning a seam
            # — is dropped here. --source mode keeps them and marks each
            # via excluded_by_trim in _format_match.
            raw = [
                m
                for m in raw
                if source_range_in_result(
                    segments,
                    m["source_range"][0],
                    m["source_range"][1],
                )
                is not None
            ]

        for m in raw:
            all_matches.append(
                _format_match(
                    m,
                    source_id=sid,
                    segments=segments,
                    source_only=source_only,
                    transcript=transcript,
                    context_segments=context_segments,
                )
            )

    # No transcripts at all → structured error per the M11 fail-no-
    # transcript path. Multi-source: this only fires if every source
    # is missing a transcript.
    if not sources_searched:
        skip_reasons = sorted({s["transcription_skipped_reason"] for s in sources_skipped})
        payload = {
            "error": (
                f"find: no transcripts available across {len(sources_skipped)} "
                f"source(s) (skip reasons: {', '.join(skip_reasons)})."
            ),
            "command": "find",
            "sources_skipped": sources_skipped,
            "hint": (
                "Re-run 'moviestar load --force' (without --no-transcribe) "
                "to add transcripts, then retry."
            ),
        }
        click.echo(json.dumps(payload, indent=2))
        sys.exit(1)

    # Combine + re-sort across sources, take top `limit`.
    all_matches.sort(key=lambda m: -m["score"])
    formatted = all_matches[:limit]
    best_score = formatted[0]["score"] if formatted else None
    suggested_query = _find_suggested_query(formatted)

    if (
        not exact
        and best_score is not None
        and best_score < WARNING_THRESHOLD
        and not include_low_score
    ):
        result: dict = {
            "command": "find",
            "query": query,
            "exact": exact,
            "source_only": source_only,
            "matches": [],
            "no_matches_above_threshold": True,
            "threshold": WARNING_THRESHOLD,
            "best_score": best_score,
            "best_available_matches": formatted,
            "sources_searched": sources_searched,
            "hint": (
                f"No transcript matches scored at least {WARNING_THRESHOLD} "
                f"(best score: {best_score}). Try a shorter query, use "
                f"'moviestar skim --text-only' or 'moviestar skim --words' "
                f"to scan the transcript, or re-run with "
                f"--include-low-score to inspect the weak candidates."
            ),
        }
        if suggested_query is not None:
            result["suggested_query"] = suggested_query
            result["hint"] += _find_suggested_query_hint(suggested_query)
        if not source_only:
            result["hint"] += " Pass --source to also search trimmed-away regions."
        if sources_skipped:
            result["sources_skipped"] = sources_skipped
        click.echo(json.dumps(result, indent=2))
        return

    result: dict = {
        "command": "find",
        "query": query,
        "exact": exact,
        "source_only": source_only,
        "matches": formatted,
        "sources_searched": sources_searched,
        "hint": _find_hint(
            formatted, best_score, source_only=source_only, exact=exact
        ),
    }
    if suggested_query is not None:
        result["suggested_query"] = suggested_query
        result["hint"] += _find_suggested_query_hint(suggested_query)
    if sources_skipped:
        result["sources_skipped"] = sources_skipped
    if best_score is not None and best_score < WARNING_THRESHOLD:
        result["best_score"] = best_score

    click.echo(json.dumps(result, indent=2))


# ---------- Output ----------



def _export_scene_layout_composition(
    *,
    project: dict,
    spec: dict,
    composition: list[dict],
    output: str | None,
    fast: bool,
    preview: bool,
    dry_run: bool,
    loudness_target: float | None,
    audio_join_fade: float | None,
    loudness_report: bool = False,
    quiet: bool = False,
) -> None:
    plan, render_plans = _scene_render_plans_for_range(
        project=project,
        spec=spec,
        composition=composition,
        start_s=0.0,
        end_s=_scene_composition_plan(
            spec=spec,
            composition=composition,
            command="export",
        )["composition_duration"],
        command="export",
    )
    resolved_audio_mix = _resolve_project_audio_mix(
        "export", spec, plan["composition_duration"], get_project_dir()
    )
    default_output = output is None
    if default_output:
        output = _default_export_output_path(create_parent=not dry_run)
    abs_output = os.path.realpath(output)
    output_parent = os.path.dirname(abs_output)
    if (
        output_parent
        and not os.path.isdir(output_parent)
        and not dry_run
    ):
        _error_exit(
            "export",
            f"Cannot write to {abs_output}: parent directory does not exist",
        )

    planned_overlays = _overlay_render_context(
        project,
        spec,
        render_plans[0]["canvas_tuple"],
        0.0,
        plan["composition_duration"],
        "export",
    )
    artifacts = _scene_video_render_artifacts(
        command="export",
        project=project,
        spec=spec,
        render_plans=render_plans,
        abs_output=abs_output,
        work_dir=get_project_dir() / "scene-renders" / "export",
        preview=preview,
        audio_join_fade=audio_join_fade,
    )
    if (
        loudness_target is not None
        and not any(artifacts["segment_has_audio"])
        and not _audio_mix_is_edited(spec)
    ):
        _error_exit(
            "export",
            "--loudness-target requires an exported audio track, but this "
            "scene composition has no routed audio.",
        )
    _validate_audio_join_fade_durations(
        audio_join_fade,
        [
            segment[2] - segment[1]
            for segment in artifacts["concat_segments"]
        ],
        "export",
        noun="scene",
    )
    plan = _scene_report_from_render_plans(plan, render_plans)

    def audio_route(render_plan: dict) -> dict:
        scene = render_plan["scene_window"]
        return {
            "scene": scene["scene"],
            "index": scene["index"],
            "audio_from": render_plan["audio_from"],
            "audio_from_source": render_plan["audio_from_source"],
            "audio_from_range": render_plan["audio_from_range"],
            "result_range": scene["result_range"],
        }

    mode = "preview" if preview else "scene_composition"
    history_len = _revision_count(spec, "composition")
    base_result: dict = {
        "composition_type": "scenes",
        "output_fps": artifacts["output_fps"],
        "composition_canvas": plan["canvas"],
        "composition_duration": format_timecode(plan["composition_duration"]),
        "result_duration": format_timecode(plan["composition_duration"]),
        "operations_applied": history_len,
        "mode": mode,
        "fast": fast,
        "preview": preview,
        "scenes_count": len(plan["scenes"]),
        "pacing_segments_count": len(render_plans),
        "slots_count": plan["slots_count"],
        "composition": plan["scenes"],
        "audio_routes": [
            audio_route(render_plan) for render_plan in render_plans
        ],
        "camera_render_status": (
            "active"
            if any(plan["animated_camera_slots"] for plan in render_plans)
            else "inactive"
        ),
        "render_order": render_plans[0]["render_order"],
        "transition_render_status": (
            "active" if artifacts["transition_render_plan"] else "inactive"
        ),
        "transition_render_plan": [
            {
                **transition,
                "duration": format_timecode(transition["duration"]),
            }
            for transition in artifacts["transition_render_plan"]
        ],
    }
    if artifacts["audio_dropped_note"] is not None:
        base_result["audio_dropped_note"] = artifacts["audio_dropped_note"]
    _add_audio_join_fade_result(base_result, audio_join_fade)
    if planned_overlays is not None:
        _attach_overlay_envelope(base_result, planned_overlays)
    _add_warnings(base_result, _legacy_shape_warnings(spec))
    _add_warnings(
        base_result,
        _unrendered_transition_warnings(spec, include_internal=False),
    )

    concat_presented = _concat_presented(spec)
    if (
        concat_presented
        and spec.get("composition_audio_from") is None
        and any(artifacts["segment_has_audio"])
        and not all(artifacts["segment_has_audio"])
    ):
        # Stage-3 policy change, surfaced explicitly: flat concat
        # exports used to drop audio entirely when any segment was
        # silent; the scene pipeline keeps audio per segment.
        base_result["audio_policy_note"] = (
            "Segments without an audio stream stay silent while audible "
            "segments keep their audio. (Flat concat exports previously "
            "dropped audio entirely when any segment was silent; route "
            "one source across the timeline with --audio-from to "
            "restore a single continuous track.)"
        )
    if concat_presented:
        # Concat-authored composition: keep the flat segment vocabulary
        # the agent authored. Rendering still runs the scene pipeline.
        segments_out, _segments_total = _format_composition_segments(
            _segments_view(composition)
        )
        base_result["composition_type"] = "segments"
        base_result["composition"] = segments_out
        base_result["segments_count"] = len(segments_out)
        if not preview:
            # The scene pipeline always re-encodes; concat vocabulary
            # reports the encode strategy in `mode`.
            base_result["mode"] = "re-encode"
        audio_from = spec.get("composition_audio_from")
        if audio_from is not None:
            base_result["audio_from"] = audio_from
            base_result["audio_from_source"] = "composition"
            flat_view = _segments_view(composition)
            anchor_seg = next(
                (seg for seg in flat_view if seg["source"] == audio_from),
                None,
            )
            if anchor_seg is not None:
                base_result["audio_from_anchor"] = format_timecode(
                    parse_timecode(anchor_seg["source_from"])
                )
            audio_from_range = _audio_from_range_envelope(
                spec=spec,
                composition=flat_view,
                source_durations={
                    source["id"]: float(source["duration"]["seconds"])
                    for source in project["sources"]
                },
                audio_from=audio_from,
            )
            if audio_from_range is not None:
                base_result["audio_from_range"] = audio_from_range

    if _layout_presented(spec):
        # Layout-authored composition: keep the {layout, slots} shape
        # the agent authored. Rendering runs the scene pipeline (B3);
        # the single scene's plan supplies the same slot/audio detail
        # the retired layout lane reported.
        scene = composition[0]
        layout_slots_out, layout_duration = _format_layout_slots(
            scene, base_result["composition_canvas"]
        )
        base_result["composition"] = {
            "layout": _layout_envelope(
                scene["layout"],
                base_result["composition_canvas"],
                result_time_s=0.0,
            ),
            "slots": layout_slots_out,
            "result_range": {
                "from": format_timecode(0.0),
                "to": format_timecode(layout_duration),
                "duration": format_timecode(layout_duration),
            },
        }
        base_result["composition_type"] = "layout"
        base_result["slots_count"] = len(layout_slots_out)
        if not preview:
            base_result["mode"] = "layout"
        audio_from = spec.get("composition_audio_from")
        if audio_from is not None:
            af_slot = next(
                (
                    slot
                    for slot in scene["slots"]
                    if slot["source"] == audio_from
                ),
                None,
            )
            base_result["audio_from"] = audio_from
            base_result["audio_from_source"] = "composition"
            if af_slot is not None:
                base_result["audio_from_anchor"] = format_timecode(
                    parse_timecode(af_slot["source_from"])
                )

    if dry_run:
        result: dict = {
            **base_result,
            "dry_run": True,
            "status": "would_render",
            "would_render_to": abs_output,
            "render_available": True,
            "scene_render_commands": artifacts["scene_render_commands"],
            "ffmpeg_command": artifacts["concat_command"],
            "concat_command": artifacts["concat_command"],
            "hint": (
                "Dry-run only - no file written. Re-run without --dry-run "
                "to render the composition, 'moviestar undo' to revert the "
                "concat, or 'moviestar concat' to replace it."
                if concat_presented
                else "Dry-run only - no file written. Re-run without "
                "--dry-run to render this scene composition."
            ),
        }
        _apply_dry_run_setup(result, artifacts["setup_commands"])
        if fast:
            result["note"] = (
                "--fast (stream-copy) is incompatible with rendering this "
                "composition (compositing and concat need filter_complex). "
                "Re-encoded automatically; drop --fast to silence this note."
            )
        if fast and loudness_target is not None:
            result["note"] = _loudness_fast_note()
        _attach_audio_schema_envelope(
            result,
            spec=spec,
            result_duration=plan["composition_duration"],
            command="export",
            source_audio_available=any(artifacts["segment_has_audio"]),
            resolved_audio_mix=resolved_audio_mix,
            loudness_report=loudness_report,
        )
        _add_loudness_result(result, loudness_target, dry_run=True)
        click.echo(json.dumps(result, indent=2))
        return

    if not quiet:
        click.echo(
            f"Rendering scene composition ({mode})... "
            "(--quiet silences progress)",
            err=True,
        )
    start_time = time.time()
    try:
        ffmpeg_cmd = _render_scene_video_artifacts(
            artifacts=artifacts,
            abs_output=abs_output,
            preview=preview,
            audio_join_fade=audio_join_fade,
            quiet=quiet,
        )
    except FFmpegNotFoundError as exc:
        _error_exit("export", str(exc))
    except FileNotFoundError as exc:
        _error_exit("export", str(exc))
    except FFmpegCapabilityError as exc:
        _ffmpeg_capability_error_exit("export", exc, "Render failed")
    except RuntimeError as exc:
        _error_exit("export", f"Render failed: {exc}")
    elapsed = time.time() - start_time
    if not quiet:
        click.echo(f"Rendered in {_format_elapsed(elapsed)}.", err=True)

    try:
        out_probe = run_ffprobe(abs_output)
        actual_duration = float(out_probe.get("format", {}).get("duration", 0.0))
    except (RuntimeError, json.JSONDecodeError, FFmpegNotFoundError):
        actual_duration = 0.0

    result = {
        **base_result,
        "status": "exported",
        "out": abs_output,
        "actual_duration": format_timecode(actual_duration),
        "file_size_bytes": os.path.getsize(abs_output),
        "scene_render_commands": artifacts["scene_render_commands"],
        "ffmpeg_command": ffmpeg_cmd,
        "concat_command": ffmpeg_cmd,
        "hint": (
            "Inspect with 'moviestar probe " + _path_for_hint(abs_output) + "', "
            "'moviestar undo' to revert the scene composition, or "
            "'moviestar scenes set' to replace it."
        ),
    }
    if fast:
        result["note"] = (
            "--fast (stream-copy) is unavailable for scene layout export "
            "(scene compositing and concat need filter_complex). Re-encoded "
            "automatically; drop --fast to silence this note."
        )
    if fast and loudness_target is not None:
        result["note"] = _loudness_fast_note()
    _attach_audio_schema_envelope(
        result,
        spec=spec,
        result_duration=plan["composition_duration"],
        command="export",
        source_audio_available=any(artifacts["segment_has_audio"]),
        resolved_audio_mix=resolved_audio_mix,
        loudness_report=loudness_report,
    )
    _add_loudness_result(result, loudness_target)
    _finalize_loudness_normalization(
        "export", result, loudness_target, abs_output
    )
    click.echo(json.dumps(result, indent=2))


def _export_composition(
    *,
    project: dict,
    spec: dict,
    composition: list[dict],
    output: str | None,
    fast: bool,
    preview: bool,
    dry_run: bool,
    loudness_target: float | None,
    audio_join_fade: float | None,
    loudness_report: bool = False,
    quiet: bool = False,
) -> None:
    """Render a populated composition to one MP4.

    Build a ``(source_path, src_from, src_to)`` list from the
    composition's snapshotted source-time ranges, look up each source
    file path via project.json, and feed the result to
    :func:`render_segments` (which handles input deduplication so
    single-source-with-multiple-segments uses one ``-i``, and multi-
    source uses one ``-i`` per distinct file).

    Stream-copy (``--fast``) is incompatible with filter_complex
    concat, so on a multi-segment composition the mode auto-overrides
    to re-encode with an explanatory ``note`` (same shape as the
    cut-induced override).
    """
    from moviestar.spec import parse_timecode_string

    if _is_layout_composition(composition):
        if _is_scene_layout_composition(composition):
            _export_scene_layout_composition(
                project=project,
                spec=spec,
                composition=composition,
                output=output,
                fast=fast,
                preview=preview,
                dry_run=dry_run,
                loudness_target=loudness_target,
                audio_join_fade=audio_join_fade,
                loudness_report=loudness_report,
                quiet=quiet,
            )
            return

    # B3: composition storage is normalized to scenes at spec load, so
    # a non-scene composition cannot reach export. Fail loudly rather
    # than silently rendering with retired flat-concat semantics.
    _error_exit(
        "export",
        "Composition storage is not in the normalized scene form. "
        "Reload the project (any moviestar command normalizes stored "
        "compositions) and retry.",
    )


@cli.command("export")
@project_workspace_option
@click.option(
    "--out",
    "output",
    default=None,
    help="Output MP4 path (default 'moviestar-clips/export.mp4' in cwd).",
)
@click.option(
    "--fast",
    is_flag=True,
    help="Stream-copy instead of re-encoding. Faster but snaps to "
    "nearest keyframe (±keyframe drift on actual_duration).",
)
@click.option(
    "--preview",
    is_flag=True,
    help="Re-encode at 480p ultrafast for fast verification renders.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Validate everything the real call would (project loaded, "
    "spec resolves, output path resolves, flag combination valid) "
    "but skip running ffmpeg and writing the output file. Returns "
    "the same envelope as the real call with status: 'would_render', "
    "dry_run: true, would_render_to (the resolved output path), and "
    "ffmpeg_command — the agent sees exactly what would run. Scene "
    "compositions also include scene_render_commands because rendering "
    "happens in per-scene steps before the final concat.",
)
@click.option(
    "--source",
    "source_arg",
    default=None,
    help="Source ID — renders just that source's per-source edit. "
    "Omit to render the composition (from concat, layout concat, or "
    "scenes set) or to default to the only source in a single-source "
    "project.",
)
@click.option(
    "--loudness-target",
    type=float,
    default=None,
    help="Normalize exported audio to integrated LUFS, e.g. -14 for "
    "shorts. Measured two-pass linear gain: the envelope reports the "
    "achieved loudness (met within ±1 LU), and a structured warning "
    "flags targets the -1.5 dBTP true-peak limit makes unreachable.",
)
@click.option(
    "--audio-join-fade",
    type=float,
    default=None,
    help="Fade audio out/in at sequential joins without changing result timing.",
)
@click.option(
    "--loudness-report",
    "loudness_report",
    is_flag=True,
    default=False,
    help="Measure integrated LUFS and true peak for every routed audio "
    "layer (source audio, music, voiceover) plus the final mix, using the "
    "exact rendered routing — gain, fades, looping, placement, and ducking "
    "applied. Adds one FFmpeg analysis pass per audible layer. Compare "
    "audio_loudness.tracks[].loudness.integrated_lufs to check balance "
    "(e.g. music drowning dialogue) without listening to the export.",
)
@_quiet_option
def export_cmd(
    output: str | None,
    fast: bool,
    preview: bool,
    dry_run: bool,
    source_arg: str | None,
    loudness_target: float | None,
    audio_join_fade: float | None,
    loudness_report: bool,
    quiet: bool,
) -> None:
    """Render the current edit spec to a final MP4.

    Reads the current ``moviestar/spec.json``, composes the operation
    stack via the resolver, and renders the resulting source range.

    Default is **frame-exact re-encode** (libx264 -preset fast -crf 18) —
    "what you asked for == what you got". For long renders where the
    re-encode time matters, ``--fast`` opts into stream-copy (sub-second
    on any clip but snaps to the nearest keyframe). ``--preview``
    re-encodes at 480p ultrafast for fast verification renders.

    ``actual_duration`` is probed from the rendered file and may drift
    ±1 frame from ``result_duration`` due to container framing on
    re-encode. ``--fast`` drift can be larger because of keyframe
    alignment.

    Empty spec (no operations) renders a copy of the source — export
    becomes a passthrough rather than a special-case error.

    With ``--dry-run``, export validates everything
    a real call would (project loaded, spec resolves, output parent
    exists, --fast/--preview not both) and emits the same envelope
    shape with ``dry_run: true``, ``status: "would_render"``, and
    ``would_render_to`` (the resolved output path) — but **does not run
    ffmpeg or write the file**. ``ffmpeg_command`` is included so the
    agent sees exactly what would run; ``actual_duration`` and
    ``file_size_bytes`` are dropped (post-execution measurements that
    don't exist in dry-run).

    Multicam audio routing: set `concat --audio-from <source>` on the
    composition; `export` honors it automatically. There is no
    export-side audio-routing flag — audio routing is a property of
    the composition, persisted in spec.json, and inherited by every
    render.
    """
    if not is_loaded():
        _no_project_error_exit("export")

    if fast and preview:
        _error_exit_with_hint(
            "export",
            "--fast and --preview cannot be combined. --fast is stream-copy "
            "(no re-encode); --preview is a low-res re-encode.",
            "Pick one: --fast for sub-second stream-copy renders, "
            "--preview for fast 480p ultrafast verification renders, "
            "or neither for the default frame-exact re-encode.",
        )
    loudness_target = _parse_loudness_target(loudness_target, "export")
    audio_join_fade = _parse_audio_join_fade(audio_join_fade, "export")

    try:
        project = load_project()
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        _error_exit("export", f"Could not read project.json: {exc}")

    try:
        spec = _load_or_init_spec(project)
    except SpecValidationError as exc:
        _error_exit("export", str(exc))

    composition = spec.get("composition")

    # M13b step 5 dispatch:
    #   --source set        → per-source path (existing M13a behavior).
    #   no flag + composition populated → render the composition.
    #   no flag + composition null + single-source → per-source default.
    #   no flag + composition null + multi-source → helpful error naming
    #     both `--source <id>` and `concat` as paths forward.
    if source_arg is None and composition is not None:
        _export_composition(
            project=project,
            spec=spec,
            composition=composition,
            output=output,
            fast=fast,
            preview=preview,
            dry_run=dry_run,
            loudness_target=loudness_target,
            audio_join_fade=audio_join_fade,
            loudness_report=loudness_report,
            quiet=quiet,
        )
        return

    if source_arg is None and len(project["sources"]) > 1:
        sids = ", ".join(s["id"] for s in project["sources"])
        _error_exit(
            "export",
            f"This project has {len(project['sources'])} sources ({sids}) "
            f"and no composition is set. Pass --source <id> to render one "
            f"source's edit, or run 'moviestar concat --segment <id> --from "
            f"X --to Y ...' to set a composition first.",
        )

    source_id = _resolve_source_arg(project, source_arg, "export")
    source = _get_project_source(project, source_id)
    source_path = source["path"]
    source_duration = float(source["duration"]["seconds"])
    if (
        loudness_target is not None
        and not source.get("audio_codec")
        and not _audio_mix_is_edited(spec)
    ):
        _error_exit(
            "export",
            "--loudness-target requires an exported audio track, but this "
            "source has no audio stream.",
        )
    if audio_join_fade is not None and not source.get("audio_codec"):
        _error_exit(
            "export",
            "--audio-join-fade requires an exported audio track, but this "
            "source has no audio stream.",
        )

    timeline = resolve_source(spec, source_id, source_duration)
    segments = timeline.source_segments
    duration = timeline.effective_duration
    resolved_audio_mix = _resolve_project_audio_mix(
        "export", spec, duration, get_project_dir()
    )
    source_canvas = None
    if source.get("width") is not None and source.get("height") is not None:
        source_canvas = (int(source["width"]), int(source["height"]))
    planned_overlays = None
    if len(project["sources"]) == 1 and source_canvas is not None:
        planned_overlays = _overlay_render_context(
            project, spec, source_canvas, 0.0, duration, "export"
        )
    overlay_plans = planned_overlays["plans"] if planned_overlays else None
    overlays_active = bool(overlay_plans)
    # Build the (path, src_from, src_to) list the renderer consumes —
    # one entry for trim-only paths, multiple for post-cut multi-segment.
    render_inputs = [
        (source_path, seg_from, seg_to) for seg_from, seg_to in segments
    ]
    src_from = segments[0][0]
    src_to = segments[-1][1]

    # Resolve output path. Auto-name in moviestar-clips/ if omitted.
    default_output = output is None
    if default_output:
        output = _default_export_output_path(create_parent=not dry_run)
    abs_output = os.path.realpath(output)

    output_parent = os.path.dirname(abs_output)
    if (
        output_parent
        and not os.path.isdir(output_parent)
        and not dry_run
    ):
        _error_exit(
            "export",
            f"Cannot write to {abs_output}: parent directory does not exist",
        )

    # Map flags → render params. Default is re-encode (precise=True);
    # --fast is stream-copy (both False); --preview is low-res re-encode.
    multi_segment = len(segments) > 1
    _validate_audio_join_fade_durations(
        audio_join_fade,
        [seg_to - seg_from for seg_from, seg_to in segments],
        "export",
    )
    fast_overridden_for_segments = False
    fast_overridden_for_overlays = False
    if preview:
        precise_arg = False
        preview_arg = True
        mode = "preview"
    elif fast:
        # Stream-copy is incompatible with the filter_complex concat path
        # used for multi-segment results — keep --fast as a user signal
        # but override the render mode to re-encode so the output is
        # correct. The envelope's `note` explains the override.
        if multi_segment:
            precise_arg = True
            preview_arg = False
            mode = "re-encode"
            fast_overridden_for_segments = True
        elif overlays_active:
            precise_arg = True
            preview_arg = False
            mode = "re-encode"
            fast_overridden_for_overlays = True
        else:
            precise_arg = False
            preview_arg = False
            mode = "stream-copy"
        if loudness_target is not None:
            precise_arg = True
            preview_arg = False
            mode = "re-encode"
    else:
        precise_arg = True
        preview_arg = False
        mode = "re-encode"

    # Issue #36 stage 2: dry-run forks at the side-effect boundary.
    # Everything above (project loaded, spec parses, mode chosen,
    # output path resolves) ran identically. Below is
    # where the real call would invoke ffmpeg + read the file back;
    # dry-run instead builds the ffmpeg command via the pure builder
    # and emits the would-be envelope.
    if dry_run:
        ffmpeg_cmd = build_render_segments_command(
            render_inputs,
            abs_output,
            precise=precise_arg,
            preview=preview_arg,
            segment_has_audio=[bool(source.get("audio_codec"))] * len(render_inputs),
            segment_fps=[float(source.get("fps") or 30.0)] * len(render_inputs),
            audio_join_fade=audio_join_fade,
            overlay_plans=overlay_plans,
            overlay_canvas=source_canvas,
            highlight_ass=_highlight_ass_arg(planned_overlays),
        )
        result: dict = {
            "dry_run": True,
            "status": "would_render",
            "would_render_to": abs_output,
            "source": {"id": source_id, "path": os.path.realpath(source_path)},
            "segments_count": len(segments),
            "source_segments": [
                _segment_envelope(seg_from, seg_to)
                for seg_from, seg_to in segments
            ],
            "result_range": _result_range(duration),
            "result_duration": format_timecode(duration),
            "operations_applied": timeline.operations_applied,
            "mode": mode,
            "fast": fast,
            "preview": preview,
            "ffmpeg_command": ffmpeg_cmd,
            "hint": (
                "Dry-run only — no file written. Re-run without --dry-run "
                "to render. The resolved source_segments / result_range "
                "above are what the real call would produce."
            ),
        }
        # source_range is the outer envelope and is only truthful when
        # there's a single segment. Multi-segment paths surface
        # source_segments (the truth) and drop source_range to avoid
        # misleading agents — same shape as status's post-cut envelope.
        if not multi_segment:
            result["source_range"] = {
                "from": format_timecode(src_from),
                "to": format_timecode(src_to),
                "duration": format_timecode(src_to - src_from),
            }
        if fast_overridden_for_segments:
            result["note"] = (
                "--fast (stream-copy) is unavailable when the result spans "
                "multiple source segments (a cut introduces a seam). "
                "Re-encoded automatically for a correct render; drop "
                "--fast to silence this note."
            )
        elif fast_overridden_for_overlays:
            result["note"] = (
                "--fast (stream-copy) is unavailable when overlays burn in "
                "(text rendering needs filter_complex). Re-encoded "
                "automatically; drop --fast to silence this note."
            )
        elif mode == "stream-copy":
            result["note"] = (
                "stream-copy snaps to nearest keyframe; actual_duration on a "
                "real call may differ slightly from result_duration. Drop "
                "--fast for frame-exact rendering (the default)."
            )
        if fast and loudness_target is not None:
            result["note"] = _loudness_fast_note()
        _add_loudness_result(result, loudness_target, dry_run=True)
        _add_audio_join_fade_result(result, audio_join_fade)
        if timeline.operations_applied == 0:
            result["passthrough_note"] = (
                "No operations in spec — a real call would render the full "
                "source. Run 'moviestar trim --from X --to Y' first to "
                "narrow the timeline."
            )
        if planned_overlays is not None:
            _attach_overlay_envelope(result, planned_overlays)
        else:
            _flat_overlays_note(spec, result)
        _attach_audio_schema_envelope(
            result,
            spec=spec,
            result_duration=duration,
            command="export",
            source_audio_available=bool(source.get("audio_codec")),
            resolved_audio_mix=resolved_audio_mix,
            loudness_report=loudness_report,
        )
        _apply_dry_run_setup(
            result,
            _dry_run_setup_commands_for_outputs([abs_output]),
        )
        click.echo(json.dumps(result, indent=2))
        return

    # Stderr progress mirrors load's pattern — humans + h-in-the-loop
    # agents see the mode they got without parsing stdout JSON. Stdout
    # stays pure JSON for `| jq` and other tooling.
    if not quiet:
        click.echo(
            f"Rendering ({mode})... (--quiet silences progress)", err=True
        )
    start_time = time.time()

    try:
        _write_highlight_ass(planned_overlays)
        ffmpeg_cmd = render_segments(
            render_inputs,
            abs_output,
            precise=precise_arg,
            preview=preview_arg,
            segment_has_audio=[bool(source.get("audio_codec"))] * len(render_inputs),
            segment_fps=[float(source.get("fps") or 30.0)] * len(render_inputs),
            audio_join_fade=audio_join_fade,
            overlay_plans=overlay_plans,
            overlay_canvas=source_canvas,
            highlight_ass=_highlight_ass_arg(planned_overlays),
            progress_cb=_render_progress_cb(quiet),
        )
        if (
            multi_segment
            or overlays_active
            or loudness_target is not None
            or audio_join_fade is not None
        ):
            _assert_rendered_video_duration(
                abs_output, duration, "source export"
            )
    except FFmpegNotFoundError as exc:
        _error_exit("export", str(exc))
    except FileNotFoundError as exc:
        _error_exit("export", str(exc))
    except FFmpegCapabilityError as exc:
        _ffmpeg_capability_error_exit("export", exc, "Render failed")
    except RuntimeError as exc:
        _error_exit("export", f"Render failed: {exc}")

    elapsed = time.time() - start_time
    if not quiet:
        click.echo(f"Rendered in {_format_elapsed(elapsed)}.", err=True)

    # Probe the rendered file for the actual duration we got.
    try:
        out_probe = run_ffprobe(abs_output)
        actual_duration = float(out_probe.get("format", {}).get("duration", 0.0))
    except (RuntimeError, json.JSONDecodeError, FFmpegNotFoundError):
        actual_duration = 0.0

    result: dict = {
        "status": "exported",
        "out": abs_output,
        "source": {"id": source_id, "path": os.path.realpath(source_path)},
        "segments_count": len(segments),
        "source_segments": [
            _segment_envelope(seg_from, seg_to)
            for seg_from, seg_to in segments
        ],
        "result_range": _result_range(duration),
        "result_duration": format_timecode(duration),
        "actual_duration": format_timecode(actual_duration),
        "operations_applied": timeline.operations_applied,
        "mode": mode,
        "fast": fast,
        "preview": preview,
        "file_size_bytes": os.path.getsize(abs_output),
        "ffmpeg_command": ffmpeg_cmd,
        "hint": (
            "Inspect with 'moviestar screenshot --at <timecode>' "
            "(interpreted in result-time when run inside this project) "
            "or 'moviestar probe " + _path_for_hint(abs_output) + "'."
        ),
    }
    if not multi_segment:
        result["source_range"] = {
            "from": format_timecode(src_from),
            "to": format_timecode(src_to),
            "duration": format_timecode(src_to - src_from),
        }
    if fast_overridden_for_segments:
        result["note"] = (
            "--fast (stream-copy) is unavailable when the result spans "
            "multiple source segments (a cut introduces a seam). "
            "Re-encoded automatically for a correct render; drop "
            "--fast to silence this note."
        )
    elif fast_overridden_for_overlays:
        result["note"] = (
            "--fast (stream-copy) is unavailable when overlays burn in "
            "(text rendering needs filter_complex). Re-encoded automatically; "
            "drop --fast to silence this note."
        )
    elif mode == "stream-copy":
        result["note"] = (
            "stream-copy snaps to nearest keyframe; actual duration may differ "
            "slightly from requested. Drop --fast for frame-exact rendering "
            "(the default)."
        )
    if fast and loudness_target is not None:
        result["note"] = _loudness_fast_note()
    _add_loudness_result(result, loudness_target)
    _add_audio_join_fade_result(result, audio_join_fade)
    if timeline.operations_applied == 0:
        result["passthrough_note"] = (
            "No operations in spec — output mirrors the full source. "
            "Run 'moviestar trim --from X --to Y' first to narrow the timeline."
        )
    if planned_overlays is not None:
        _attach_overlay_envelope(result, planned_overlays)
    else:
        _flat_overlays_note(spec, result)
    _attach_audio_schema_envelope(
        result,
        spec=spec,
        result_duration=duration,
        command="export",
        source_audio_available=bool(source.get("audio_codec")),
        resolved_audio_mix=resolved_audio_mix,
        loudness_report=loudness_report,
    )
    _finalize_loudness_normalization(
        "export", result, loudness_target, abs_output
    )

    click.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    cli()
