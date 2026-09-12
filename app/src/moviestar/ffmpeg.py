"""FFmpeg / ffprobe subprocess helpers.

All FFmpeg interaction goes through subprocess. The full argv is returned
to callers so they can surface it in verbose JSON output (tradeoff #2 —
agents can copy the command and reproduce manually).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


class FFmpegNotFoundError(RuntimeError):
    """Raised when ffmpeg/ffprobe is not on PATH."""


class FFmpegCapabilityError(RuntimeError):
    """Raised when installed FFmpeg lacks a required build capability."""

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def check_ffmpeg_available() -> None:
    """Raise FFmpegNotFoundError if ffprobe is not on PATH."""
    if shutil.which("ffprobe") is None:
        raise FFmpegNotFoundError(
            "ffprobe not found on PATH. Install FFmpeg: brew install ffmpeg"
        )
    if shutil.which("ffmpeg") is None:
        raise FFmpegNotFoundError(
            "ffmpeg not found on PATH. Install FFmpeg: brew install ffmpeg"
        )


def build_ffmpeg_filters_command() -> list[str]:
    """Return the argv used to list the filters compiled into FFmpeg."""
    return ["ffmpeg", "-hide_banner", "-filters"]


def build_ffmpeg_encoders_command() -> list[str]:
    """Return the argv used to list the encoders compiled into FFmpeg."""
    return ["ffmpeg", "-hide_banner", "-encoders"]


# Issue #358: the single catalog behind 'moviestar doctor' and the
# author-time/render preflights. Each entry maps one FFmpeg build
# capability to a stable code, the MovieStar features it gates, and a
# platform-specific remediation. Codes are part of the agent contract —
# never rename one that has shipped.
FFMPEG_FEATURE_REQUIREMENTS: tuple[dict, ...] = (
    {
        "capability": "drawtext",
        "kind": "filter",
        "code": "ffmpeg_missing_drawtext_filter",
        "message": (
            "FFmpeg is missing the 'drawtext' filter required for "
            "burned-in text."
        ),
        "blocks": [
            "overlay/title burn-in on composition export, screenshot, "
            "watch, and inspect",
            "caption burn-in",
            "storyboard timecode labels (the sheet renders unlabeled "
            "with a warning)",
        ],
        "hint": (
            "On macOS/Homebrew, reinstall a full FFmpeg build with "
            "libfreetype/drawtext support, for example: brew reinstall "
            "ffmpeg. On other systems, install FFmpeg with libfreetype "
            "enabled and confirm 'ffmpeg -filters' lists 'drawtext'."
        ),
    },
    {
        "capability": "ass",
        "kind": "filter",
        "code": "ffmpeg_missing_ass_filter",
        "message": (
            "FFmpeg is missing the 'ass' filter required for spoken-word "
            "caption burn-in."
        ),
        "blocks": [
            "captions generate --highlight spoken-word (burned-in "
            "spoken-word highlighting)",
            "overlays carrying a highlight block",
        ],
        "hint": (
            "On macOS/Homebrew, reinstall a full FFmpeg build with libass "
            "support, for example: brew reinstall ffmpeg. On other "
            "systems, install FFmpeg with libass enabled and confirm "
            "'ffmpeg -filters' lists the 'ass' filter."
        ),
    },
    {
        "capability": "alphamerge",
        "kind": "filter",
        "code": "ffmpeg_missing_alphamerge_filter",
        "message": (
            "FFmpeg is missing the 'alphamerge' filter required for "
            "shaped picture-in-picture insets."
        ),
        "blocks": [
            "circle and rounded slot shapes on export, screenshot, "
            "watch, and inspect",
        ],
        "hint": (
            "Install a full FFmpeg build (macOS/Homebrew: brew "
            "reinstall ffmpeg) and confirm 'ffmpeg -filters' lists "
            "'alphamerge'."
        ),
    },
    {
        "capability": "geq",
        "kind": "filter",
        "code": "ffmpeg_missing_geq_filter",
        "message": (
            "FFmpeg is missing the 'geq' filter required to draw slot "
            "shape masks."
        ),
        "blocks": [
            "circle and rounded slot shapes (mask generation)",
        ],
        "hint": (
            "Install a full FFmpeg build (macOS/Homebrew: brew "
            "reinstall ffmpeg) and confirm 'ffmpeg -filters' lists "
            "'geq'."
        ),
    },
    {
        "capability": "libx264",
        "kind": "encoder",
        "code": "ffmpeg_missing_libx264_encoder",
        "message": (
            "FFmpeg is missing the 'libx264' encoder required for "
            "re-encoded video output."
        ),
        "blocks": [
            "export, clip, batch, and watch video re-encoding",
        ],
        "hint": (
            "Install an FFmpeg build with libx264 enabled (macOS/"
            "Homebrew: brew reinstall ffmpeg) and confirm "
            "'ffmpeg -encoders' lists 'libx264'."
        ),
    },
    {
        "capability": "aac",
        "kind": "encoder",
        "code": "ffmpeg_missing_aac_encoder",
        "message": (
            "FFmpeg is missing the 'aac' encoder required for "
            "re-encoded audio output."
        ),
        "blocks": [
            "export audio encoding and audio mixing",
        ],
        "hint": (
            "Install an FFmpeg build with the native aac encoder "
            "(macOS/Homebrew: brew reinstall ffmpeg) and confirm "
            "'ffmpeg -encoders' lists 'aac'."
        ),
    },
)

_DOCTOR_POINTER = (
    " Run 'moviestar doctor' for a full environment capability report."
)


AUDIO_MIX_SAMPLE_RATE = 48_000
AUDIO_MIX_CHANNEL_LAYOUT = "stereo"
AUDIO_MIX_SAMPLE_FORMAT = "fltp"
AUDIO_MIX_LIMITER_CEILING_DBFS = -0.5
AUDIO_MIX_LIMITER_LIMIT = 10 ** (AUDIO_MIX_LIMITER_CEILING_DBFS / 20.0)
AUDIO_MIX_LIMITER_ATTACK_MS = 5.0
AUDIO_MIX_LIMITER_RELEASE_MS = 50.0


def _audio_number(value: float) -> str:
    """Format filter values deterministically without noisy trailing zeroes."""
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def _audio_volume_filter(
    *,
    gain_db: float,
    fade_in: float,
    fade_out: float,
    layer_duration: float,
    layer_offset: float,
) -> str:
    """Build gain + edge fades against a layer's full result-time range."""
    factors = [_audio_number(10 ** (float(gain_db) / 20.0))]
    if fade_in > 0:
        factors.append(
            "min(1,max(0,(t+"
            f"{_audio_number(layer_offset)})/{_audio_number(fade_in)}))"
        )
    if fade_out > 0:
        factors.append(
            "min(1,max(0,("
            f"{_audio_number(layer_duration)}-(t+{_audio_number(layer_offset)}))"
            f"/{_audio_number(fade_out)}))"
        )
    return f"volume='{'*'.join(factors)}':eval=frame"


def _build_mix_layer_graph(
    audio_mix: dict,
    result_duration: float,
    *,
    source_audio_available: bool,
    window_start: float = 0.0,
    window_duration: float | None = None,
) -> dict:
    """Build the shared per-layer half of the audio mix filtergraph.

    Every consumer of the resolved mix shares this graph: the render
    post-pass sums the finalized layer signals into the mastered mix,
    and the loudness report taps one finalized layer per measurement
    pass. Keeping a single builder is what guarantees measurements use
    the exact routing (gain, fades, looping, placement, ducking) that
    watch/export render.

    The base video is graph input 0 (``[0:a]``); callers supply its
    path in their argv. Returns a dict:
      ``track_inputs``   external file paths in graph input order
      ``filter_parts``   filtergraph up through each layer's finalized
                         ``mix_labels`` output (post gain/fades/ducking,
                         pre final sum and limiter)
      ``layer_order``    layer ids in graph order ("source" first when routed)
      ``mix_labels``     layer id -> finalized signal label
      ``skipped_tracks`` [{"id", "reason"}] for tracks with no signal in
                         this window ("muted" or "outside_window")
      ``coverage``       layer id -> (from_s, to_s) result-time span the
                         layer is audible within the window
      ``window``         (window_start, window_duration) after defaulting
    """
    if result_duration <= 0:
        raise ValueError("mix_audio: result_duration must be greater than zero")
    if window_start < 0 or window_start >= result_duration:
        raise ValueError("mix_audio: window_start must be within the result")
    if window_duration is None:
        window_duration = result_duration - window_start
    if window_duration <= 0 or window_start + window_duration > result_duration + 0.001:
        raise ValueError("mix_audio: window must be within the result duration")

    window_end = window_start + window_duration
    track_inputs: list[str] = []
    filter_parts: list[str] = []
    raw_labels: dict[str, str] = {}
    active_tracks: dict[str, dict] = {}
    skipped_tracks: list[dict[str, str]] = []
    coverage: dict[str, tuple[float, float]] = {}

    normalize = (
        f"aresample={AUDIO_MIX_SAMPLE_RATE},"
        f"aformat=sample_fmts={AUDIO_MIX_SAMPLE_FORMAT}:"
        f"channel_layouts={AUDIO_MIX_CHANNEL_LAYOUT}"
    )
    source = audio_mix.get("source_audio") or {}
    if source_audio_available and not source.get("muted", False):
        source_volume = _audio_volume_filter(
            gain_db=float(source.get("gain_db", 0.0)),
            fade_in=float(source.get("fade_in", 0.0)),
            fade_out=float(source.get("fade_out", 0.0)),
            layer_duration=result_duration,
            layer_offset=window_start,
        )
        filter_parts.append(
            f"[0:a]{normalize},atrim=duration={_audio_number(window_duration)},"
            f"asetpts=PTS-STARTPTS,{source_volume},apad,"
            f"atrim=duration={_audio_number(window_duration)}[layer_0_raw]"
        )
        raw_labels["source"] = "layer_0_raw"
        coverage["source"] = (window_start, window_end)

    for track in audio_mix.get("tracks", []):
        if track.get("muted", False):
            skipped_tracks.append({"id": track["id"], "reason": "muted"})
            continue
        placed = track["placed_range"]
        placed_from = float(placed["from"]["seconds"])
        placed_to = float(placed["to"]["seconds"])
        overlap_from = max(window_start, placed_from)
        overlap_to = min(window_end, placed_to)
        if overlap_to <= overlap_from + 0.0000001:
            skipped_tracks.append({"id": track["id"], "reason": "outside_window"})
            continue

        track_inputs.append(track["path"])
        input_index = len(track_inputs)
        source_range = track["source_range"]
        source_from = float(source_range["from"]["seconds"])
        source_to = float(source_range["to"]["seconds"])
        selected_duration = float(source_range["duration"]["seconds"])
        placed_duration = float(placed["duration"]["seconds"])
        placed_offset = overlap_from - placed_from
        overlap_duration = overlap_to - overlap_from
        local_delay_ms = round((overlap_from - window_start) * 1000)
        label = f"layer_{len(raw_labels)}_raw"

        chain = (
            f"[{input_index}:a]{normalize},"
            f"atrim=start={_audio_number(source_from)}:end={_audio_number(source_to)},"
            "asetpts=PTS-STARTPTS"
        )
        if track.get("loop", False):
            loop_samples = max(1, round(selected_duration * AUDIO_MIX_SAMPLE_RATE))
            chain += f",aloop=loop=-1:size={loop_samples}"
        chain += (
            f",atrim=start={_audio_number(placed_offset)}:"
            f"end={_audio_number(placed_offset + overlap_duration)},"
            "asetpts=PTS-STARTPTS,"
            + _audio_volume_filter(
                gain_db=float(track.get("gain_db", 0.0)),
                fade_in=float(track.get("fade_in", 0.0)),
                fade_out=float(track.get("fade_out", 0.0)),
                layer_duration=placed_duration,
                layer_offset=placed_offset,
            )
            + f",adelay={local_delay_ms}:all=1,apad,"
            f"atrim=duration={_audio_number(window_duration)}[{label}]"
        )
        filter_parts.append(chain)
        raw_labels[track["id"]] = label
        active_tracks[track["id"]] = track
        coverage[track["id"]] = (overlap_from, overlap_to)

    layer_order = list(raw_labels)
    safe_name: dict[str, str] = {}
    used_safe_names: set[str] = set()
    for index, layer_id in enumerate(layer_order):
        candidate = re.sub(r"[^A-Za-z0-9_]", "_", layer_id) or f"layer_{index}"
        if candidate in used_safe_names:
            candidate = f"{candidate}_{index}"
        safe_name[layer_id] = candidate
        used_safe_names.add(candidate)
    sidechain_consumers: dict[str, list[str]] = {layer_id: [] for layer_id in layer_order}
    for target_id, track in active_tracks.items():
        for sidechain_id in (track.get("ducking") or {}).get("under", []):
            if sidechain_id in raw_labels:
                sidechain_consumers[sidechain_id].append(target_id)

    final_outputs: dict[str, dict[str, str]] = {}
    building: set[str] = set()

    def finish_layer(layer_id: str) -> None:
        if layer_id in final_outputs:
            return
        if layer_id in building:
            raise ValueError("mix_audio: ducking cycle reached the renderer")
        building.add(layer_id)
        unshared = raw_labels[layer_id]
        track = active_tracks.get(layer_id)
        ducking = (track or {}).get("ducking")
        active_sidechains = [
            sidechain_id
            for sidechain_id in (ducking or {}).get("under", [])
            if sidechain_id in raw_labels
        ]
        if active_sidechains:
            for sidechain_id in active_sidechains:
                finish_layer(sidechain_id)
            sidechain_labels = [
                final_outputs[sidechain_id][layer_id]
                for sidechain_id in active_sidechains
            ]
            if len(sidechain_labels) == 1:
                sidechain_label = sidechain_labels[0]
            else:
                sidechain_label = f"{safe_name[layer_id]}_sidechain"
                filter_parts.append(
                    f"{''.join(f'[{label}]' for label in sidechain_labels)}"
                    f"amix=inputs={len(sidechain_labels)}:duration=longest:normalize=0"
                    f"[{sidechain_label}]"
                )
            resolved = ducking["resolved"]
            threshold = 10 ** (float(resolved["threshold_db"]) / 20.0)
            ducked_label = f"{safe_name[layer_id]}_ducked"
            filter_parts.append(
                f"[{unshared}][{sidechain_label}]sidechaincompress="
                f"threshold={_audio_number(threshold)}:"
                f"ratio={_audio_number(resolved['ratio'])}:"
                f"attack={_audio_number(resolved['attack_ms'])}:"
                f"release={_audio_number(resolved['release_ms'])}"
                f"[{ducked_label}]"
            )
            unshared = ducked_label

        outputs = {"mix": f"{safe_name[layer_id]}_mix"}
        for target_id in sidechain_consumers[layer_id]:
            outputs[target_id] = (
                f"{safe_name[layer_id]}_for_{safe_name[target_id]}"
            )
        output_labels = list(outputs.values())
        if len(output_labels) == 1:
            filter_parts.append(f"[{unshared}]anull[{output_labels[0]}]")
        else:
            filter_parts.append(
                f"[{unshared}]asplit={len(output_labels)}"
                f"{''.join(f'[{label}]' for label in output_labels)}"
            )
        final_outputs[layer_id] = outputs
        building.remove(layer_id)

    for layer_id in layer_order:
        finish_layer(layer_id)

    return {
        "track_inputs": track_inputs,
        "filter_parts": filter_parts,
        "layer_order": layer_order,
        "mix_labels": {
            layer_id: final_outputs[layer_id]["mix"] for layer_id in layer_order
        },
        "skipped_tracks": skipped_tracks,
        "coverage": coverage,
        "window": (window_start, window_duration),
    }


def build_mix_audio_command(
    base_video_path: str,
    output_path: str,
    audio_mix: dict,
    result_duration: float,
    *,
    source_audio_available: bool,
    window_start: float = 0.0,
    window_duration: float | None = None,
) -> list[str]:
    """Build the base-mix post-pass for one rendered video.

    ``audio_mix`` is the resolver's read shape. The base MP4 supplies the
    already-routed and retimed source layer; external inputs are placed on
    the finished result clock. A watch render passes its global window start
    and duration so track fades/loops keep the same timing as full export.

    Ducking dependencies form an acyclic graph (validated by the resolver).
    Finalized layer signals are split for the audible sum and downstream
    sidechains so nested ducking uses the actually audible upstream signal.
    """
    graph = _build_mix_layer_graph(
        audio_mix,
        result_duration,
        source_audio_available=source_audio_available,
        window_start=window_start,
        window_duration=window_duration,
    )

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", base_video_path]
    for path in graph["track_inputs"]:
        cmd += ["-i", path]

    if not graph["mix_labels"]:
        return cmd + [
            "-map", "0:v:0", "-map_metadata", "0", "-c:v", "copy", "-an",
            output_path,
        ]

    filter_parts = list(graph["filter_parts"])
    mix_labels = [
        graph["mix_labels"][layer_id] for layer_id in graph["layer_order"]
    ]
    if len(mix_labels) == 1:
        mixed_label = mix_labels[0]
    else:
        mixed_label = "amix_out"
        filter_parts.append(
            f"{''.join(f'[{label}]' for label in mix_labels)}"
            f"amix=inputs={len(mix_labels)}:duration=longest:normalize=0"
            f"[{mixed_label}]"
        )
    limited_label = "amix_limited"
    filter_parts.append(
        f"[{mixed_label}]alimiter="
        f"limit={_audio_number(AUDIO_MIX_LIMITER_LIMIT)}:"
        f"attack={_audio_number(AUDIO_MIX_LIMITER_ATTACK_MS)}:"
        f"release={_audio_number(AUDIO_MIX_LIMITER_RELEASE_MS)}:"
        f"level=false:latency=true[{limited_label}]"
    )
    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "0:v:0",
        "-map", f"[{limited_label}]",
        "-map_metadata", "0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-ar", str(AUDIO_MIX_SAMPLE_RATE),
        "-ac", "2",
        "-shortest",
        output_path,
    ]
    return cmd


def mix_audio(
    base_video_path: str,
    output_path: str,
    audio_mix: dict,
    result_duration: float,
    *,
    source_audio_available: bool,
    window_start: float = 0.0,
    window_duration: float | None = None,
) -> list[str]:
    """Run :func:`build_mix_audio_command` and return its reproducible argv."""
    if not os.path.exists(base_video_path):
        raise FileNotFoundError(f"File not found: {base_video_path}")
    for track in audio_mix.get("tracks", []):
        if not os.path.exists(track["path"]):
            raise FileNotFoundError(f"File not found: {track['path']}")
    check_ffmpeg_available()
    cmd = build_mix_audio_command(
        base_video_path,
        output_path,
        audio_mix,
        result_duration,
        source_audio_available=source_audio_available,
        window_start=window_start,
        window_duration=window_duration,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    return cmd


def build_layer_loudness_probe_command(
    base_video_path: str,
    audio_mix: dict,
    result_duration: float,
    *,
    source_audio_available: bool,
    layer_id: str,
    window_start: float = 0.0,
    window_duration: float | None = None,
    target_lufs: float = -14.0,
    true_peak: float = -1.5,
    lra: float = 11.0,
) -> list[str]:
    """Measure one routed layer of the resolved mix with loudnorm.

    Shares :func:`_build_mix_layer_graph` with the render post-pass, so the
    measured signal is exactly what :func:`build_mix_audio_command` sums —
    gain, fades, looping, placement, and signal-driven ducking applied; the
    final sum and limiter are not. Layers other than the target drain into
    ``anullsink`` so the graph stays identical (ducking sidechains keep
    compressing against the real upstream signal) while only the target
    reaches the loudnorm measurement.
    """
    graph = _build_mix_layer_graph(
        audio_mix,
        result_duration,
        source_audio_available=source_audio_available,
        window_start=window_start,
        window_duration=window_duration,
    )
    if layer_id not in graph["mix_labels"]:
        skipped = {
            entry["id"]: entry["reason"] for entry in graph["skipped_tracks"]
        }
        if layer_id in skipped:
            raise ValueError(
                f"loudness probe: layer '{layer_id}' has no signal in this "
                f"window ({skipped[layer_id]})"
            )
        raise ValueError(f"loudness probe: unknown audio layer '{layer_id}'")

    filter_parts = list(graph["filter_parts"])
    for other_id in graph["layer_order"]:
        if other_id != layer_id:
            filter_parts.append(f"[{graph['mix_labels'][other_id]}]anullsink")
    tap_label = "loudness_tap"
    filter_parts.append(
        f"[{graph['mix_labels'][layer_id]}]"
        f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}:"
        f"print_format=json[{tap_label}]"
    )

    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info",
        "-i", base_video_path,
    ]
    for path in graph["track_inputs"]:
        cmd += ["-i", path]
    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{tap_label}]",
        "-f", "null", "-",
    ]
    return cmd


def _finite_metric(metrics: dict, key: str) -> float | None:
    """Like _float_metric, but silence measures as None, not -inf/JSON poison."""
    value = _float_metric(metrics, key)
    if value is None or not math.isfinite(value):
        return None
    return value


def run_layer_loudness_probe(
    base_video_path: str,
    audio_mix: dict,
    result_duration: float,
    *,
    source_audio_available: bool,
    layer_id: str,
    window_start: float = 0.0,
    window_duration: float | None = None,
) -> tuple[dict, list[str]]:
    """Measure integrated/peak metrics for one routed layer of the mix."""
    if not os.path.exists(base_video_path):
        raise FileNotFoundError(f"File not found: {base_video_path}")
    for track in audio_mix.get("tracks", []):
        if not os.path.exists(track["path"]):
            raise FileNotFoundError(f"File not found: {track['path']}")
    check_ffmpeg_available()
    cmd = build_layer_loudness_probe_command(
        base_video_path,
        audio_mix,
        result_duration,
        source_audio_available=source_audio_available,
        layer_id=layer_id,
        window_start=window_start,
        window_duration=window_duration,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    raw = _parse_loudnorm_json(result.stderr)
    metrics = {
        "integrated_lufs": _finite_metric(raw, "input_i"),
        "true_peak_dbtp": _finite_metric(raw, "input_tp"),
        "lra_lu": _finite_metric(raw, "input_lra"),
        "threshold_lufs": _finite_metric(raw, "input_thresh"),
    }
    return metrics, cmd


def _parse_ffmpeg_filter_names(filters_output: str) -> set[str]:
    """Extract filter names from ``ffmpeg -filters`` stdout."""
    names: set[str] = set()
    for line in filters_output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[A-Z.]{2,3}", parts[0]):
            names.add(parts[1])
    return names


_filter_names_cache: set[str] | None = None


def clear_ffmpeg_capability_cache() -> None:
    """Forget the per-process ``ffmpeg -filters`` listing."""
    global _filter_names_cache
    _filter_names_cache = None


def ffmpeg_filter_available(filter_name: str) -> bool:
    """Return True when ``ffmpeg -filters`` lists ``filter_name``.

    The listing is cached per process: a CLI invocation may probe
    several filters (author-time preflights plus render preflights)
    and the compiled-in filter set cannot change mid-process.
    """
    global _filter_names_cache
    check_ffmpeg_available()
    if _filter_names_cache is None:
        cmd = build_ffmpeg_filters_command()
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(_failure_message("ffmpeg", result))
        _filter_names_cache = _parse_ffmpeg_filter_names(result.stdout)
    return filter_name in _filter_names_cache


def _parse_ffmpeg_encoder_names(encoders_output: str) -> set[str]:
    """Extract encoder names from ``ffmpeg -encoders`` stdout."""
    names: set[str] = set()
    for line in encoders_output.splitlines():
        parts = line.split()
        if (
            len(parts) >= 2
            and parts[1] != "="
            and re.fullmatch(r"[VAS][F.][S.][X.][B.][D.]", parts[0])
        ):
            names.add(parts[1])
    return names


def _ffmpeg_tool_version(tool: str) -> str | None:
    """Best-effort version string from ``<tool> -version``."""
    try:
        result = subprocess.run([tool, "-version"], capture_output=True, text=True)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    match = re.match(rf"{tool} version (\S+)", result.stdout)
    return match.group(1) if match else None


def ffmpeg_capability_report() -> dict:
    """One-shot environment probe behind 'moviestar doctor' (issue #358).

    Reports ffmpeg/ffprobe availability, path, and version plus the
    compiled-in filter and encoder listings. Never raises: a missing
    binary leaves the listings ``None`` and a failed listing probe is
    recorded in ``probe_errors``.
    """
    report: dict = {
        "ffmpeg": {"available": False, "path": None, "version": None},
        "ffprobe": {"available": False, "path": None, "version": None},
        "filters": None,
        "encoders": None,
        "probe_errors": [],
    }
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if path is None:
            continue
        report[tool] = {
            "available": True,
            "path": path,
            "version": _ffmpeg_tool_version(tool),
        }
    if not report["ffmpeg"]["available"]:
        return report
    for key, cmd, parser in (
        ("filters", build_ffmpeg_filters_command(), _parse_ffmpeg_filter_names),
        (
            "encoders",
            build_ffmpeg_encoders_command(),
            _parse_ffmpeg_encoder_names,
        ),
    ):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            report["probe_errors"].append(
                {"probe": key, "error": _failure_message("ffmpeg", result)}
            )
            continue
        report[key] = sorted(parser(result.stdout))
    return report


def _overlay_plans_require_ass_filter(overlay_plans: list[dict] | None) -> bool:
    return any(plan.get("highlight") for plan in overlay_plans or [])


def missing_capability_error(capability: str) -> FFmpegCapabilityError:
    """Build the stable-coded error for one cataloged capability."""
    requirement = next(
        req
        for req in FFMPEG_FEATURE_REQUIREMENTS
        if req["capability"] == capability
    )
    return FFmpegCapabilityError(
        requirement["code"],
        requirement["message"],
        requirement["hint"] + _DOCTOR_POINTER,
    )


def check_ffmpeg_ass_filter_available() -> None:
    """Preflight libass support required for spoken-word highlighted captions."""
    if ffmpeg_filter_available("ass"):
        return
    raise missing_capability_error("ass")


def check_ffmpeg_drawtext_filter_available() -> None:
    """Preflight drawtext support required for burned-in text."""
    if ffmpeg_filter_available("drawtext"):
        return
    raise missing_capability_error("drawtext")


def preflight_overlay_render_capabilities(
    overlay_plans: list[dict] | None,
) -> None:
    """Fail early when overlay plans need FFmpeg capabilities this build lacks."""
    if _overlay_plans_require_ass_filter(overlay_plans):
        check_ffmpeg_ass_filter_available()


def check_ffmpeg_shape_filters_available() -> None:
    """Preflight the filters slot-shape masks require."""
    for filter_name in ("alphamerge", "geq"):
        if not ffmpeg_filter_available(filter_name):
            raise missing_capability_error(filter_name)


def preflight_shape_render_capabilities(layout_slots: list[dict]) -> None:
    """Fail early when shaped slots need FFmpeg capabilities this build
    lacks — before the render, not forty seconds in."""
    if any(_slot_is_shaped(slot) for slot in layout_slots):
        check_ffmpeg_shape_filters_available()


def _failure_message(tool: str, result: subprocess.CompletedProcess) -> str:
    """Compose a non-empty failure message for ffmpeg/ffprobe.

    Falls back to the exit code when stderr is empty so the message
    never ends in a dangling colon. Issue #26: every error in the
    codebase teaches; this one used to render as ``ffmpeg failed: ``
    when stderr was captured but empty.
    """
    stderr = result.stderr.strip()
    detail = stderr if stderr else f"exit code {result.returncode}"
    return f"{tool} failed: {detail}"


def build_ffprobe_command(video_path: str) -> list[str]:
    """Return the argv we use for metadata probes. Exposed so the CLI can log it.

    ``-v error`` (not ``-v quiet``) so that ffprobe still emits its
    error messages to stderr on failure — "moov atom not found",
    "Invalid data found", etc. ``quiet`` would suppress those, leaving
    the wrapper to fall back to "exit code N" with no useful detail.
    Suppressing stderr would mask ffprobe's useful diagnostic and leave the
    wrapper with only an exit code.
    """
    return [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        video_path,
    ]


def run_ffprobe(video_path: str) -> dict:
    """Run ffprobe and return the parsed JSON response.

    Raises:
        FileNotFoundError: if the video path does not exist.
        FFmpegNotFoundError: if ffprobe is not installed.
        RuntimeError: if ffprobe returns a non-zero exit code.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"File not found: {video_path}")

    check_ffmpeg_available()

    cmd = build_ffprobe_command(video_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffprobe", result))
    return json.loads(result.stdout)


def build_last_video_packet_timestamp_command(
    video_path: str,
    at_s: float,
) -> list[str]:
    """Build the bounded packet probe used for VFR lead-in backfill."""
    timestamp = _audio_number(at_s)
    return [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-read_intervals", f"{timestamp}%{timestamp}",
        "-show_packets",
        "-show_entries", "packet=pts_time",
        "-of", "json",
        video_path,
    ]


def probe_last_video_packet_timestamp(
    video_path: str,
    at_s: float,
) -> float | None:
    """Return the last video packet PTS at or before ``at_s``.

    ffprobe seeks backward to the preceding keyframe for the bounded
    interval, so this avoids scanning the source from the beginning. A
    missing prior packet is valid at the start of a stream and returns None.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"File not found: {video_path}")
    if at_s < 0:
        raise ValueError("packet timestamp probe requires at_s >= 0")
    if at_s == 0:
        return 0.0

    check_ffmpeg_available()
    cmd = build_last_video_packet_timestamp_command(video_path, at_s)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffprobe", result))
    data = json.loads(result.stdout)
    timestamps: list[float] = []
    for packet in data.get("packets", []):
        try:
            timestamp = float(packet["pts_time"])
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp <= at_s + 1e-9:
            timestamps.append(timestamp)
    return max(timestamps) if timestamps else None


def build_audio_energy_probe_command(
    media_path: str,
    *,
    silence_threshold_db: float = -35.0,
    min_silence_duration: float = 2.0,
) -> list[str]:
    """Build a lightweight sustained-audio probe for transcript QA.

    ``silencedetect`` emits silence boundaries to stderr. The caller inverts
    those boundaries into occupied spans, so pauses shorter than two seconds
    stay inside the same span instead of becoming noisy transcript-hole
    candidates.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        "-i",
        media_path,
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        (
            f"silencedetect=noise={silence_threshold_db:g}dB:"
            f"d={min_silence_duration}"
        ),
        "-f",
        "null",
        "-",
    ]


_SILENCE_EVENT_RE = re.compile(
    r"silence_(start|end):\s*(-?(?:\d+(?:\.\d*)?|\.\d+))"
)


def _parse_audio_energy_spans(
    stderr: str, duration_seconds: float
) -> list[tuple[float, float]]:
    """Invert FFmpeg ``silencedetect`` events into non-silent spans."""
    duration = max(0.0, float(duration_seconds))
    if duration == 0:
        return []

    spans: list[tuple[float, float]] = []
    occupied_start = 0.0
    in_silence = False
    for match in _SILENCE_EVENT_RE.finditer(stderr):
        event, raw_seconds = match.groups()
        seconds = min(duration, max(0.0, float(raw_seconds)))
        if event == "start":
            if not in_silence and seconds > occupied_start:
                spans.append((occupied_start, seconds))
            in_silence = True
        else:
            occupied_start = max(occupied_start, seconds)
            in_silence = False

    if not in_silence and occupied_start < duration:
        spans.append((occupied_start, duration))
    return spans


def run_audio_energy_probe(
    media_path: str,
    duration_seconds: float,
    *,
    silence_threshold_db: float = -35.0,
    min_silence_duration: float = 2.0,
) -> list[tuple[float, float]]:
    """Return sustained non-silent spans for transcript coverage checks."""
    if not os.path.exists(media_path):
        raise FileNotFoundError(f"File not found: {media_path}")
    check_ffmpeg_available()
    cmd = build_audio_energy_probe_command(
        media_path,
        silence_threshold_db=silence_threshold_db,
        min_silence_duration=min_silence_duration,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    return _parse_audio_energy_spans(result.stderr, duration_seconds)


def build_loudness_probe_command(
    media_path: str,
    start_time: float | None = None,
    duration: float | None = None,
    target_lufs: float = -14.0,
    true_peak: float = -1.5,
    lra: float = 11.0,
) -> list[str]:
    """Return the argv used to measure audio loudness with loudnorm."""
    cmd: list[str] = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info"]
    if start_time is not None:
        cmd += ["-ss", str(start_time)]
    cmd += ["-i", media_path]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += [
        "-vn",
        "-af",
        (
            f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}:"
            "print_format=json"
        ),
        "-f",
        "null",
        "-",
    ]
    return cmd


def _parse_loudnorm_json(stderr: str) -> dict:
    """Extract the loudnorm JSON object FFmpeg writes to stderr."""
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("ffmpeg loudnorm output did not include JSON metrics")
    try:
        return json.loads(stderr[start:end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffmpeg loudnorm output was not valid JSON") from exc


def _float_metric(metrics: dict, key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run_loudness_probe(
    media_path: str,
    start_time: float | None = None,
    duration: float | None = None,
    target_lufs: float = -14.0,
    true_peak: float = -1.5,
    lra: float = 11.0,
) -> tuple[dict, list[str]]:
    """Measure integrated loudness and peak metrics for an audio stream."""
    if not os.path.exists(media_path):
        raise FileNotFoundError(f"File not found: {media_path}")

    check_ffmpeg_available()

    cmd = build_loudness_probe_command(
        media_path,
        start_time=start_time,
        duration=duration,
        target_lufs=target_lufs,
        true_peak=true_peak,
        lra=lra,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))

    raw = _parse_loudnorm_json(result.stderr)
    metrics = {
        "integrated_lufs": _float_metric(raw, "input_i"),
        "true_peak_dbtp": _float_metric(raw, "input_tp"),
        "lra_lu": _float_metric(raw, "input_lra"),
        "threshold_lufs": _float_metric(raw, "input_thresh"),
        "target_offset_lu": _float_metric(raw, "target_offset"),
        "normalization_target": {
            "integrated_lufs": target_lufs,
            "true_peak_dbtp": true_peak,
            "lra_lu": lra,
        },
    }
    return metrics, cmd


# loudnorm reports -70 LUFS for silence; treat anything at that floor
# as unmeasurable rather than planning a +56 dB "correction".
LOUDNESS_PROBE_FLOOR_LUFS = -69.9

# The default AAC bitrate measurably shifts integrated loudness on noisy
# content (+0.8 LU observed on the issue #352 pink-noise fixture), which
# would eat most of the documented ±1 LU tolerance. 192k keeps the
# round-trip under 0.1 LU.
LOUDNESS_POSTPASS_AUDIO_BITRATE = "192k"


def plan_loudness_normalization(
    target_lufs: float,
    measured_i: float | None,
    measured_tp: float | None,
    true_peak_limit: float = -1.5,
) -> dict:
    """Plan the linear gain that lands a rendered mix on ``target_lufs``.

    Integrated loudness scales exactly with linear gain, so the exact
    correction is ``target - measured``, capped so the scaled true peak
    stays at or under ``true_peak_limit``. Single-pass loudnorm was
    retired here because its dynamic mode missed targets by up to 8.7 LU
    and overshot the true-peak ceiling (issue #352); a measured linear
    gain is deterministic and makes "target unachievable" computable.
    """
    if (
        measured_i is None
        or not math.isfinite(measured_i)
        or measured_i <= LOUDNESS_PROBE_FLOOR_LUFS
    ):
        return {
            "gain_db": None,
            "limited_by_true_peak": False,
            "predicted_lufs": None,
            "reason": "input_unmeasurable",
        }
    gain = target_lufs - measured_i
    limited = False
    if measured_tp is not None and math.isfinite(measured_tp):
        headroom = true_peak_limit - measured_tp
        if gain > headroom:
            gain = headroom
            limited = True
    gain = round(gain, 2)
    return {
        "gain_db": gain,
        "limited_by_true_peak": limited,
        "predicted_lufs": round(measured_i + gain, 2),
        "reason": None,
    }


def build_loudness_normalize_command(
    input_path: str,
    output_path: str,
    gain_db: float,
) -> list[str]:
    """Return the argv for the loudness post-pass (video stream copied)."""
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", input_path,
        "-map", "0:v:0?",
        "-map", "0:a:0",
        "-c:v", "copy",
        "-af",
        (
            f"volume={_audio_number(gain_db)}dB,"
            f"aresample={EXPORT_AUDIO_SAMPLE_RATE}"
        ),
        "-c:a", "aac",
        "-b:a", LOUDNESS_POSTPASS_AUDIO_BITRATE,
        output_path,
    ]


def normalize_loudness(
    media_path: str,
    target_lufs: float,
    true_peak_limit: float = -1.5,
) -> dict:
    """Two-pass loudness normalization of a rendered artifact, in place.

    Pass 1 measures the artifact with the same loudnorm probe that backs
    ``probe --loudness``; the plan applies the exact linear gain that
    reaches ``target_lufs``, capped by true-peak headroom; a verify probe
    then measures what was actually delivered so callers can report the
    achieved loudness rather than assume it.

    Returns ``{"pre", "plan", "apply_command", "measured"}`` where
    ``pre``/``measured`` are probe metric dicts and ``apply_command`` is
    the post-pass argv (``None`` when the input was unmeasurable and no
    gain was applied).
    """
    pre, _ = run_loudness_probe(
        media_path, target_lufs=target_lufs, true_peak=true_peak_limit
    )
    plan = plan_loudness_normalization(
        target_lufs,
        pre["integrated_lufs"],
        pre["true_peak_dbtp"],
        true_peak_limit=true_peak_limit,
    )
    apply_cmd = None
    if plan["gain_db"] is not None:
        output = Path(media_path)
        temp_path = str(
            output.with_name(
                f".{output.stem}.moviestar-loudness{output.suffix or '.mp4'}"
            )
        )
        apply_cmd = build_loudness_normalize_command(
            media_path, temp_path, plan["gain_db"]
        )
        try:
            result = subprocess.run(apply_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(_failure_message("ffmpeg", result))
            os.replace(temp_path, media_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    measured, _ = run_loudness_probe(
        media_path, target_lufs=target_lufs, true_peak=true_peak_limit
    )
    return {
        "pre": pre,
        "plan": plan,
        "apply_command": apply_cmd,
        "measured": measured,
    }


def build_extract_frame_command(
    video_path: str,
    at_seconds: float,
    output_path: str,
    quality: int = 2,
) -> list[str]:
    """Return the argv used to extract a single frame."""
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(at_seconds),
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", str(quality),
        output_path,
    ]


def extract_frame(
    video_path: str,
    at_seconds: float,
    output_path: str,
    quality: int = 2,
) -> list[str]:
    """Extract a single frame from video_path at at_seconds, writing to output_path.

    Returns the full ffmpeg argv that was run (for logging in CLI output).

    Raises:
        FileNotFoundError: if the source video does not exist.
        FFmpegNotFoundError: if ffmpeg is not installed.
        RuntimeError: if ffmpeg returns a non-zero exit code.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"File not found: {video_path}")

    check_ffmpeg_available()

    cmd = build_extract_frame_command(video_path, at_seconds, output_path, quality)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    return cmd


def build_target_grid_frame_command(
    video_path: str,
    at_seconds: float,
    output_path: str,
    width: int,
    height: int,
    font_path: str,
    quality: int = 2,
) -> list[str]:
    """Argv for one source frame overlaid with a labeled coordinate grid.

    Grid lines land every 10% of the frame; labels are source-pixel
    coordinates so an agent can read --box / --point values straight
    off the image. A 1px black grid under the white one keeps the
    lines visible on both light and dark content.
    """
    step_x = width / 10.0
    step_y = height / 10.0
    font_size = max(12, round(min(width, height) / 30))
    filters = [
        f"drawgrid=x=1:y=1:w={step_x:.4f}:h={step_y:.4f}:t=1:color=black@0.5",
        f"drawgrid=w={step_x:.4f}:h={step_y:.4f}:t=1:color=white@0.7",
    ]
    escaped_font = _filtergraph_escape(font_path)
    label = (
        f"drawtext=fontfile={escaped_font}:fontsize={font_size}"
        ":fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=3"
    )
    for i in range(1, 10):
        x_px = round(i * step_x)
        filters.append(f"{label}:text={x_px}:x={x_px + 4}:y=4")
    for i in range(1, 10):
        y_px = round(i * step_y)
        filters.append(f"{label}:text={y_px}:x=4:y={y_px + 4}")
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(at_seconds),
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", str(quality),
        "-vf", ",".join(filters),
        output_path,
    ]


def render_target_grid_frame(
    video_path: str,
    at_seconds: float,
    output_path: str,
    width: int,
    height: int,
    font_path: str,
    quality: int = 2,
) -> list[str]:
    """Extract one frame with a labeled coordinate grid burned in.

    Returns the full ffmpeg argv that was run (for logging in CLI output).

    Raises:
        FileNotFoundError: if the source video does not exist.
        FFmpegNotFoundError: if ffmpeg is not installed.
        RuntimeError: if ffmpeg returns a non-zero exit code.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"File not found: {video_path}")

    check_ffmpeg_available()

    cmd = build_target_grid_frame_command(
        video_path, at_seconds, output_path, width, height, font_path, quality
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    return cmd


def build_selection_frame_command(
    video_path: str,
    at_seconds: float,
    output_path: str,
    boxes: list[dict],
    quality: int = 2,
) -> list[str]:
    """Argv for one source frame with selection/crop rectangles drawn on.

    Each box is ``{"rect": {x,y,w,h}, "color": str, "thickness": int}``
    in source pixels. Used by camera preview so the agent sees its own
    box and MovieStar's final crop on the same frame.
    """
    filters = []
    for box in boxes:
        rect = box["rect"]
        filters.append(
            f"drawbox=x={round(rect['x'])}:y={round(rect['y'])}"
            f":w={max(1, round(rect['w']))}:h={max(1, round(rect['h']))}"
            f":color={box.get('color', 'lime')}"
            f":t={box.get('thickness', 3)}"
        )
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(at_seconds),
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", str(quality),
        "-vf", ",".join(filters),
        output_path,
    ]


def render_selection_frame(
    video_path: str,
    at_seconds: float,
    output_path: str,
    boxes: list[dict],
    quality: int = 2,
) -> list[str]:
    """Extract one frame with selection/crop rectangles burned in.

    Returns the full ffmpeg argv that was run (for logging in CLI output).

    Raises:
        FileNotFoundError: if the source video does not exist.
        FFmpegNotFoundError: if ffmpeg is not installed.
        RuntimeError: if ffmpeg returns a non-zero exit code.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"File not found: {video_path}")

    check_ffmpeg_available()

    cmd = build_selection_frame_command(
        video_path, at_seconds, output_path, boxes, quality
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    return cmd


def measure_region_stability(
    video_path: str,
    at_a: float,
    at_b: float,
    crop: dict,
    scale_width: int = 320,
) -> float | None:
    """SSIM between one source region at two times (1.0 = unchanged).

    Used by camera preview to detect the selected content changing
    mid-hold (a menu closing, a page scrolling). Best-effort: returns
    None when ffmpeg fails or emits no score, and the caller skips the
    check rather than failing the command.
    """
    if not os.path.exists(video_path):
        return None
    crop_filter = (
        f"crop={round(crop['w'])}:{round(crop['h'])}:"
        f"{round(crop['x'])}:{round(crop['y'])},"
        f"scale={scale_width}:-2,setsar=1"
    )
    cmd = [
        "ffmpeg", "-loglevel", "info",
        "-ss", str(at_a), "-i", video_path,
        "-ss", str(at_b), "-i", video_path,
        "-filter_complex",
        f"[0:v]{crop_filter}[a];[1:v]{crop_filter}[b];[a][b]ssim",
        "-frames:v", "1", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"All:([0-9.]+)", result.stderr)
    if match is None:
        return None
    return float(match.group(1))


def build_extract_frames_command(
    video_path: str,
    interval: float,
    output_pattern: str,
    scale_width: int = 320,
    quality: int = 5,
    start_time: float | None = None,
    duration: float | None = None,
) -> list[str]:
    """Return the argv used to extract frames at a fixed interval.

    Uses -threads 1 on the encoder because mjpeg thread init fails when
    the filter produces fewer frames than the thread pool can seed.

    Optional start_time / duration scope extraction to a range:
      -ss <start_time>   placed before -i for fast seek
      -t <duration>      placed after -i
    """
    cmd: list[str] = ["ffmpeg", "-y", "-loglevel", "error"]
    if start_time is not None:
        cmd += ["-ss", str(start_time)]
    cmd += ["-i", video_path]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += [
        "-vf", f"fps=1/{interval},scale={scale_width}:-1",
        "-q:v", str(quality),
        "-threads", "1",
        "-strict", "unofficial",
        output_pattern,
    ]
    return cmd


def extract_frames(
    video_path: str,
    interval: float,
    output_dir: Path,
    source_id: str,
    scale_width: int = 320,
    quality: int = 5,
    start_time: float | None = None,
    duration: float | None = None,
) -> tuple[list[Path], list[str]]:
    """Extract frames at fixed interval. Returns (sorted paths, ffmpeg argv).

    Output file naming: {source_id}_{sequence:04d}.jpg (ffmpeg uses 1-based
    sequences internally, we sort the result).

    Optional start_time/duration scope extraction limits the inspected range.
    When set, frame N (1-indexed) corresponds to source time
    `start_time + (N-1) * interval`.

    Raises:
        FileNotFoundError: if the source video does not exist.
        FFmpegNotFoundError: if ffmpeg is not installed.
        RuntimeError: if ffmpeg returns a non-zero exit code.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"File not found: {video_path}")

    check_ffmpeg_available()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pattern = str(output_dir / f"{source_id}_%04d.jpg")
    cmd = build_extract_frames_command(
        video_path,
        interval,
        pattern,
        scale_width,
        quality,
        start_time=start_time,
        duration=duration,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))

    paths = sorted(output_dir.glob(f"{source_id}_*.jpg"))
    return paths, cmd


def build_storyboard_frame_command(
    video_path: str,
    at_seconds: float,
    output_path: str,
    scale_width: int = 384,
    quality: int = 2,
    label: str | None = None,
    font_path: str | None = None,
) -> list[str]:
    """Argv for one storyboard tile: fast-seek extract, scale, and an
    optional burned-in timecode label.

    The label renders with ``expansion=none`` so timecode colons are
    literal text, not drawtext option separators. Bottom-left with a
    translucent box so it reads as chrome, not content. Labels need
    both ``label`` and ``font_path``; either missing skips drawtext.
    """
    vf = f"scale={scale_width}:-1"
    if label is not None and font_path is not None:
        fontsize = max(12, scale_width // 16)
        vf += (
            f",drawtext=fontfile={_filtergraph_escape(font_path)}"
            f":text={_filtergraph_escape(label)}"
            ":expansion=none"
            f":fontsize={fontsize}"
            ":fontcolor=white"
            ":box=1:boxcolor=black@0.55:boxborderw=6"
            ":x=10:y=h-th-10"
        )
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(at_seconds),
        "-i", video_path,
        "-frames:v", "1",
        "-vf", vf,
        "-q:v", str(quality),
        output_path,
    ]


def extract_storyboard_frame(
    video_path: str,
    at_seconds: float,
    output_path: str,
    scale_width: int = 384,
    quality: int = 2,
    label: str | None = None,
    font_path: str | None = None,
) -> list[str]:
    """Extract one labeled storyboard tile. Returns the ffmpeg argv.

    Raises:
        FileNotFoundError: if the source video does not exist.
        FFmpegNotFoundError: if ffmpeg is not installed.
        RuntimeError: if ffmpeg fails, or succeeds without producing a
            frame (a seek past the last video frame exits 0 with an
            empty output).
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"File not found: {video_path}")

    check_ffmpeg_available()

    cmd = build_storyboard_frame_command(
        video_path,
        at_seconds,
        output_path,
        scale_width=scale_width,
        quality=quality,
        label=label,
        font_path=font_path,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(
            f"ffmpeg produced no frame at {at_seconds}s — the timecode "
            "may be past the last video frame in the stream"
        )
    return cmd


def build_storyboard_padding_tile_command(
    width: int,
    height: int,
    output_path: str,
    quality: int = 2,
    label: str | None = None,
    font_path: str | None = None,
) -> list[str]:
    """Argv for a filler tile occupying grid cells past the last frame.

    Dark gray with a centered label distinguishes padding from real black
    content. Gray + '(end)' cannot be mistaken for a decoded frame.
    """
    cmd: list[str] = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=0x262626:size={width}x{height}",
        "-frames:v", "1",
        # Match the video tiles' full-range JPEG pixel format. lavfi
        # color is limited-range yuv420p; a mid-sequence pixel-format
        # change makes ffmpeg reinit the filter graph and the tile
        # mosaic restarts with only the padding tile in it.
        "-pix_fmt", "yuvj420p",
    ]
    if label is not None and font_path is not None:
        fontsize = max(12, width // 16)
        cmd += [
            "-vf",
            f"drawtext=fontfile={_filtergraph_escape(font_path)}"
            f":text={_filtergraph_escape(label)}"
            ":expansion=none"
            f":fontsize={fontsize}"
            ":fontcolor=0x8a8a8a"
            ":x=(w-tw)/2:y=(h-th)/2",
        ]
    cmd += ["-q:v", str(quality), "-strict", "unofficial", output_path]
    return cmd


def render_storyboard_padding_tile(
    width: int,
    height: int,
    output_path: str,
    quality: int = 2,
    label: str | None = None,
    font_path: str | None = None,
) -> list[str]:
    """Render one filler tile. Returns the ffmpeg argv.

    Raises:
        FFmpegNotFoundError: if ffmpeg is not installed.
        RuntimeError: if ffmpeg returns a non-zero exit code.
    """
    check_ffmpeg_available()

    cmd = build_storyboard_padding_tile_command(
        width, height, output_path, quality, label=label, font_path=font_path
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    return cmd


def build_storyboard_tile_command(
    frame_pattern: str,
    columns: int,
    rows: int,
    output_path: str,
    quality: int = 2,
) -> list[str]:
    """Argv that tiles numbered frame files into one composite image.

    Unused grid cells (partial last row) pad with black — the tile
    filter's default. -threads 1 for the same mjpeg thread-init
    reason as build_extract_frames_command.
    """
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", "1",
        "-i", frame_pattern,
        "-frames:v", "1",
        "-vf", f"tile={columns}x{rows}",
        "-q:v", str(quality),
        "-threads", "1",
        "-strict", "unofficial",
        output_path,
    ]


def compose_storyboard_tiles(
    frame_pattern: str,
    columns: int,
    rows: int,
    output_path: str,
    quality: int = 2,
) -> list[str]:
    """Tile extracted frames into the composite. Returns the ffmpeg argv.

    Raises:
        FFmpegNotFoundError: if ffmpeg is not installed.
        RuntimeError: if ffmpeg returns a non-zero exit code.
    """
    check_ffmpeg_available()

    cmd = build_storyboard_tile_command(
        frame_pattern, columns, rows, output_path, quality
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    return cmd


def build_extract_clip_command(
    video_path: str,
    start_time: float,
    duration: float,
    output_path: str,
    precise: bool = False,
    preview: bool = False,
) -> list[str]:
    """Return the argv used to extract a video segment.

    Three modes (callers pick at most one):

    - **Stream copy** (default — both flags False): ``-c copy``. Fast
      but snaps to nearest keyframe; actual duration may differ by up
      to a few seconds.
    - **Precise** (``precise=True``): re-encodes with libx264 (preset
      fast, crf 18) + aac. Frame-accurate boundaries.
    - **Preview** (``preview=True``): re-encodes at 480p with libx264
      ultrafast (crf 28). Fast verification renders.

    If both flags are True, ``preview`` wins (it already implies a
    re-encode, so the precise boundary semantic comes for free). The
    CLI surfaces this as an error before reaching here.
    """
    cmd: list[str] = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(start_time),
        "-i", video_path,
        "-t", str(duration),
    ]
    if preview:
        cmd += [
            "-vf", "scale=-2:480",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-c:a", "aac",
        ]
    elif precise:
        cmd += [
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "aac",
        ]
    else:
        cmd += ["-c", "copy"]
    cmd.append(output_path)
    return cmd


EXPORT_AUDIO_SAMPLE_RATE = 48000


def extract_clip(
    video_path: str,
    start_time: float,
    duration: float,
    output_path: str,
    precise: bool = False,
    preview: bool = False,
) -> list[str]:
    """Extract a video segment. Returns the ffmpeg argv that was run.

    See ``build_extract_clip_command`` for stream-copy / precise /
    preview semantics.

    Raises:
        FileNotFoundError: if the source video does not exist.
        FFmpegNotFoundError: if ffmpeg is not installed.
        RuntimeError: if ffmpeg returns a non-zero exit code.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"File not found: {video_path}")

    check_ffmpeg_available()

    cmd = build_extract_clip_command(
        video_path, start_time, duration, output_path,
        precise=precise, preview=preview,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    return cmd


def _canvas_filter(
    width: int,
    height: int,
    framing: dict | None,
) -> str:
    """Return a video filter chain that maps one segment into a canvas."""
    mode = (framing or {}).get("mode", "fill")
    anchor = (framing or {}).get("anchor", "center")
    if mode == "fit":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )

    x_anchor = (framing or {}).get("x")
    y_anchor = (framing or {}).get("y")
    if x_anchor is not None:
        x = f"(iw-{width})*{_format_filter_number(float(x_anchor))}"
    else:
        x = {
            "left": "0",
            "right": f"iw-{width}",
        }.get(anchor, f"(iw-{width})/2")
    if y_anchor is not None:
        y = f"(ih-{height})*{_format_filter_number(float(y_anchor))}"
    else:
        y = {
            "top": "0",
            "bottom": f"ih-{height}",
        }.get(anchor, f"(ih-{height})/2")
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:{x}:{y},setsar=1"
    )


def _format_filter_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _camera_ease_expression(progress: str, ease: str) -> str:
    if ease == "linear":
        return progress
    if ease == "in":
        return f"({progress})*({progress})"
    if ease == "out":
        return f"1-(1-({progress}))*(1-({progress}))"
    if ease == "in-out":
        return f"({progress})*({progress})*(3-2*({progress}))"
    if ease == "cut":
        return "1"
    raise ValueError(f"render_layout: unknown camera ease {ease!r}")


def _camera_field_expression(
    animation: dict,
    field: str,
    *,
    frame_counter: str,
    output_fps: float,
) -> str:
    """Piecewise crop-field expression evaluated once per output frame."""
    offset = _format_filter_number(float(animation["result_local_from"]))
    fps = _format_filter_number(float(output_fps))
    local_time = f"{frame_counter}/{fps}+{offset}"
    moves = animation.get("moves", [])

    def branch(index: int, current: float) -> str:
        if index >= len(moves):
            return _format_filter_number(current)
        move = moves[index]
        start = float(move["from"])
        end = float(move["to"])
        start_text = _format_filter_number(start)
        end_text = _format_filter_number(end)
        start_value = float(move["from_crop"][field])
        end_value = float(move["to_crop"][field])
        after = branch(index + 1, end_value)
        before = _format_filter_number(current)
        if end <= start + 1e-9 or move.get("ease") == "cut":
            return f"if(lt({local_time},{start_text}),{before},{after})"
        duration = _format_filter_number(end - start)
        progress = f"(({local_time})-{start_text})/{duration}"
        eased = _camera_ease_expression(progress, move.get("ease", "in-out"))
        delta = _format_filter_number(end_value - start_value)
        interpolated = (
            f"{_format_filter_number(start_value)}+({delta})*({eased})"
        )
        return (
            f"if(lt({local_time},{start_text}),{before},"
            f"if(lt({local_time},{end_text}),{interpolated},{after}))"
        )

    return branch(0, float(animation["default_crop"][field]))


def _filter_expression(expression: str) -> str:
    """Escape an arithmetic expression embedded in one filter option."""
    return expression.replace(",", r"\,")


def _animated_camera_filter(
    animation: dict,
    region: dict,
    framing: dict | None,
    output_fps: float,
) -> str:
    """Compose source crop animation and existing slot framing.

    ``perspective`` maps the resolved source rectangle to a stable intermediate
    frame while evaluating its four corners for every output frame.  The
    following dynamic scale plus fit/pad or fill/crop stage restores the crop's
    aspect ratio and produces the exact fixed-size slot region.
    """
    on = {
        field: _camera_field_expression(
            animation, field, frame_counter="on", output_fps=output_fps
        )
        for field in ("x", "y", "w", "h")
    }
    n = {
        field: _camera_field_expression(
            animation, field, frame_counter="n", output_fps=output_fps
        )
        for field in ("w", "h")
    }
    corners = {
        "x0": on["x"],
        "y0": on["y"],
        "x1": f"({on['x']})+({on['w']})",
        "y1": on["y"],
        "x2": on["x"],
        "y2": f"({on['y']})+({on['h']})",
        "x3": f"({on['x']})+({on['w']})",
        "y3": f"({on['y']})+({on['h']})",
    }
    perspective = "perspective=" + ":".join(
        f"{key}={_filter_expression(value)}" for key, value in corners.items()
    ) + ":interpolation=cubic:sense=source:eval=frame"

    width = int(region["width"])
    height = int(region["height"])
    aspect = f"(({n['w']})/({n['h']}))"
    mode = (framing or {}).get("mode", "fill")
    if mode == "fit":
        scaled_w = f"max(2,trunc(min({width},{height}*{aspect})/2)*2)"
        scaled_h = f"max(2,trunc(min({height},{width}/{aspect})/2)*2)"
        framing_filter = (
            f"scale=w={_filter_expression(scaled_w)}:"
            f"h={_filter_expression(scaled_h)}:eval=frame,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:eval=frame,setsar=1"
        )
    else:
        scaled_w = f"max(2,trunc(max({width},{height}*{aspect})/2)*2)"
        scaled_h = f"max(2,trunc(max({height},{width}/{aspect})/2)*2)"
        anchor = (framing or {}).get("anchor", "center")
        x_anchor = (framing or {}).get("x")
        y_anchor = (framing or {}).get("y")
        if x_anchor is not None:
            crop_x = f"(iw-{width})*{_format_filter_number(float(x_anchor))}"
        else:
            crop_x = {
                "left": "0",
                "right": f"iw-{width}",
            }.get(anchor, f"(iw-{width})/2")
        if y_anchor is not None:
            crop_y = f"(ih-{height})*{_format_filter_number(float(y_anchor))}"
        else:
            crop_y = {
                "top": "0",
                "bottom": f"ih-{height}",
            }.get(anchor, f"(ih-{height})/2")
        framing_filter = (
            f"scale=w={_filter_expression(scaled_w)}:"
            f"h={_filter_expression(scaled_h)}:eval=frame,"
            f"crop={width}:{height}:{crop_x}:{crop_y},setsar=1"
        )
    return f"{perspective},{framing_filter}"


def _validate_layout_slots(
    layout_slots: list[dict],
    canvas: tuple[int, int],
    duration: float,
) -> None:
    if not layout_slots:
        raise ValueError("render_layout: empty layout_slots list")
    canvas_width, canvas_height = canvas
    if canvas_width <= 0 or canvas_height <= 0:
        raise ValueError("render_layout: canvas dimensions must be positive")
    if duration <= 0:
        raise ValueError("render_layout: duration must be positive")
    for i, slot in enumerate(layout_slots):
        for key in ("path", "source_from", "source_to", "region"):
            if key not in slot:
                raise ValueError(f"render_layout: slot {i} missing {key!r}")
        held = bool(slot.get("held"))
        invalid_range = (
            slot["source_from"] < 0
            or slot["source_from"] > slot["source_to"]
            or (not held and slot["source_from"] == slot["source_to"])
        )
        if invalid_range:
            raise ValueError(f"render_layout: invalid source range for slot {i}")
        video_seek_from = slot.get("video_seek_from", slot["source_from"])
        if video_seek_from < 0 or video_seek_from > slot["source_from"]:
            raise ValueError(
                f"render_layout: invalid video seek for slot {i}"
            )
        region = slot["region"]
        for key in ("x", "y", "width", "height"):
            if key not in region:
                raise ValueError(f"render_layout: slot {i} region missing {key!r}")
            if key in ("width", "height") and int(region[key]) <= 0:
                raise ValueError(f"render_layout: slot {i} region {key} must be positive")
        shape = slot.get("shape")
        if shape is not None:
            kind = shape.get("kind")
            if kind not in ("circle", "rounded", "rect"):
                raise ValueError(
                    f"render_layout: slot {i} unknown shape kind {kind!r}"
                )
            if kind != "rect" and not shape.get("content_mask"):
                raise ValueError(
                    f"render_layout: slot {i} shape {kind!r} requires "
                    "content_mask"
                )
            border = shape.get("border")
            if border is not None:
                width = border.get("width")
                if not isinstance(width, int) or width < 1:
                    raise ValueError(
                        f"render_layout: slot {i} border width must be a "
                        "whole number of pixels >= 1"
                    )
                if not border.get("color"):
                    raise ValueError(
                        f"render_layout: slot {i} border requires a color"
                    )
                if kind != "rect" and not border.get("disc_mask"):
                    raise ValueError(
                        f"render_layout: slot {i} border requires a "
                        "disc_mask"
                    )


def _slot_is_shaped(slot: dict) -> bool:
    shape = slot.get("shape")
    return shape is not None and shape.get("kind") in ("circle", "rounded")


def _append_layout_video_inputs(cmd: list[str], layout_slots: list[dict]) -> None:
    for slot in layout_slots:
        seek_from = slot.get("video_seek_from", slot["source_from"])
        cmd += ["-ss", str(seek_from), "-i", slot["path"]]


def _append_shape_mask_inputs(
    cmd: list[str], layout_slots: list[dict], duration: float
) -> None:
    """Append one looped still-image input per shaped slot.

    Masks always append AFTER every slot and audio input so existing
    stream numbering — including the audio ``start_index`` — never
    shifts when a slot gains a shape. The loop is bounded to the clip
    duration (plus headroom for framesync's trailing repeat): an
    unbounded ``-loop 1`` stream can keep a filter graph pulling
    frames forever in graphs that combine masks with animated-camera
    chains, spinning instead of reaching EOF.
    """
    bound = _audio_number(duration + 1.0)
    for slot in layout_slots:
        if _slot_is_shaped(slot):
            mask_input = ["-loop", "1", "-t", bound, "-i"]
            cmd += [*mask_input, slot["shape"]["content_mask"]]
            border = slot["shape"].get("border")
            if border is not None:
                cmd += [*mask_input, border["disc_mask"]]


# Keep the compositor and looped-mask inputs alive long enough for a
# single decoded frame. Source decoding itself is intentionally unbounded:
# the nearest usable VFR frame may be seconds away from the requested time.
LAYOUT_FRAME_DECODE_DURATION = 0.1


def _layout_filter_parts(
    layout_slots: list[dict],
    canvas: tuple[int, int],
    duration: float,
    *,
    output_label: str = "outv",
    output_fps: float | None = None,
    frame_decode: bool = False,
    mask_start_index: int | None = None,
) -> list[str]:
    canvas_width, canvas_height = canvas
    rate = f":r={output_fps}" if output_fps is not None else ""
    parts: list[str] = [
        f"color=c=black:s={canvas_width}x{canvas_height}:d={duration}{rate}[base]"
    ]
    overlay_base = "base"
    next_mask_index = mask_start_index
    for i, slot in enumerate(layout_slots):
        region = slot["region"]
        slot_label = f"slotv{i}"
        fps_filter = f",fps={output_fps}" if output_fps is not None else ""
        if slot.get("held"):
            frame_duration = 1.0 / (output_fps or 30.0)
            timing = (
                f"trim=start=0:end={frame_duration},setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={duration},"
                f"trim=end={duration}{fps_filter}"
            )
        else:
            seek_from = slot.get("video_seek_from", slot["source_from"])
            source_offset = slot["source_from"] - seek_from
            source_duration = slot["source_to"] - seek_from
            speed = float(slot.get("speed", 1.0))
            if frame_decode:
                # A VFR seek can land in a long packet void. The scene
                # planner seeks back to the last displayed frame when one
                # exists; for a leading void, FFmpeg returns the first frame
                # after the seek. Do not discard either with the 0.1s
                # compositor window — rebase whichever frame arrives first.
                timing = f"trim=start=0,setpts=(PTS-STARTPTS)/{speed}"
            elif output_fps is not None:
                # VFR sources (screen recordings emit no frames while
                # the screen is static) can start a slot mid-void or
                # run out of frames before the slot ends. Fill the CFR
                # grid BEFORE the rebase — `setpts=PTS-STARTPTS` on a
                # sparse stream would silently collapse a frameless
                # lead-in — and clone-pad the tail to the full clip
                # duration so `overlay=shortest=1` cannot truncate it.
                # When issue #365's packet probe seeks before source_from,
                # start the grid at that authored offset: fps retains the
                # earlier packet and uses it to backfill a VFR lead-in.
                fill_fps = output_fps / speed
                timing = (
                    f"trim=start=0:end={source_duration},"
                    f"fps={fill_fps}:start_time={_audio_number(source_offset)},"
                    f"setpts=(PTS-STARTPTS)/{speed},"
                    f"fps={output_fps},"
                    f"tpad=stop_mode=clone:stop_duration={duration},"
                    f"trim=end={duration}"
                )
            else:
                timing = (
                    f"trim=start=0:end={source_duration},"
                    f"setpts=(PTS-STARTPTS)/{speed}{fps_filter}"
                )
        camera_crop = slot.get("camera_crop")
        camera_animation = slot.get("camera_animation")
        crop_filter = ""
        if camera_crop is not None:
            crop_filter = (
                f"crop={round(camera_crop['w'])}:{round(camera_crop['h'])}:"
                f"{round(camera_crop['x'])}:{round(camera_crop['y'])},"
            )
        if camera_animation is not None:
            if output_fps is None:
                raise ValueError(
                    "render_layout: animated camera requires output_fps"
                )
            content_filter = _animated_camera_filter(
                camera_animation,
                region,
                slot.get("framing"),
                output_fps,
            )
        else:
            content_filter = (
                f"{crop_filter}"
                f"{_canvas_filter(int(region['width']), int(region['height']), slot.get('framing'))}"
            )
        shape = slot.get("shape") or {}
        border = shape.get("border")
        if _slot_is_shaped(slot):
            # The mask becomes the slot's alpha: content converts to
            # rgba, alphamerge takes luma from the looped mask input.
            # The final overlay converts back for the yuv encode.
            if next_mask_index is None:
                raise ValueError(
                    "render_layout: shaped slots require mask_start_index"
                )
            content_label = f"shaped{i}" if border is not None else slot_label
            parts.append(
                f"[{i}:v]{timing},"
                f"{content_filter},format=rgba"
                f"[slotc{i}]"
            )
            parts.append(f"[{next_mask_index}:v]format=gray[maskv{i}]")
            parts.append(f"[slotc{i}][maskv{i}]alphamerge[{content_label}]")
            next_mask_index += 1
            if border is not None:
                # The border is a color disc UNDER the content, on a
                # frame padded by the border width — an annulus over
                # the content would leave a coverage seam where two
                # antialiased edges meet at the same radius.
                from moviestar.overlays import ffmpeg_color

                border_width = int(border["width"])
                outer_w = int(region["width"]) + 2 * border_width
                outer_h = int(region["height"]) + 2 * border_width
                parts.append(
                    f"color=c={ffmpeg_color(border['color'])}:"
                    f"s={outer_w}x{outer_h}:d={duration}{rate},"
                    f"format=rgba[bcol{i}]"
                )
                parts.append(
                    f"[{next_mask_index}:v]format=gray[dmaskv{i}]"
                )
                parts.append(f"[bcol{i}][dmaskv{i}]alphamerge[disc{i}]")
                parts.append(
                    f"[disc{i}][{content_label}]"
                    f"overlay={border_width}:{border_width}[{slot_label}]"
                )
                next_mask_index += 1
        elif border is not None:
            # A sharp rectangle needs no alpha masks. Put the scaled
            # content over a larger solid-color rectangle so the edge
            # sits outside the authored region, matching shaped borders.
            from moviestar.overlays import ffmpeg_color

            border_width = int(border["width"])
            outer_w = int(region["width"]) + 2 * border_width
            outer_h = int(region["height"]) + 2 * border_width
            parts.append(
                f"[{i}:v]{timing},"
                f"{content_filter}"
                f"[rectc{i}]"
            )
            parts.append(
                f"color=c={ffmpeg_color(border['color'])}:"
                f"s={outer_w}x{outer_h}:d={duration}{rate},"
                f"format=rgba[bcol{i}]"
            )
            parts.append(
                f"[bcol{i}][rectc{i}]"
                f"overlay={border_width}:{border_width}[{slot_label}]"
            )
        else:
            chain = (
                f"[{i}:v]{timing},"
                f"{content_filter}"
                f"[{slot_label}]"
            )
            parts.append(chain)
        overlay_label = output_label if i == len(layout_slots) - 1 else f"lay{i}"
        motion = slot.get("motion")
        # A bordered bubble is larger than its content region; its
        # origin shifts back by the border width so the content lands
        # exactly where an unbordered slot would.
        border_shift = int(border["width"]) if border is not None else 0
        if motion is None:
            overlay_x = str(int(region["x"]) - border_shift)
            overlay_y = str(int(region["y"]) - border_shift)
        else:
            if motion.get("preset") != "bounce":
                raise ValueError(
                    f"render_layout: unknown slot motion {motion.get('preset')!r}"
                )
            offset = _format_filter_number(
                float(motion.get("result_time_offset", 0.0))
            )

            def bounce_expression(axis: str) -> str:
                velocity = _format_filter_number(float(motion["velocity"][axis]))
                bound = int(motion["bounds"][axis])
                phase = int(motion["origin"][axis]) + bound
                expression = (
                    f"abs(mod({velocity}*(t+{offset})+{phase},{2 * bound})"
                    f"-{bound})"
                )
                if border_shift:
                    expression = f"{expression}-{border_shift}"
                return f"'{expression}'"

            overlay_x = bounce_expression("x")
            overlay_y = bounce_expression("y")
        parts.append(
            f"[{overlay_base}][{slot_label}]"
            f"overlay=x={overlay_x}:y={overlay_y}:shortest=1"
            f"[{overlay_label}]"
        )
        overlay_base = overlay_label
    return parts


def _filtergraph_escape(value: str) -> str:
    """Escape a literal value for use inside ``-filter_complex``.

    Values pass through two parsers: the filter's own option parser
    (which eats ``\\``, ``'``, and ``:``) and the filtergraph parser
    (which additionally eats ``[]``, ``,``, and ``;``). Escape both
    levels, innermost first. Verified against ffmpeg 8 with drawtext
    ``expansion=none``; apostrophes, colons, percents, and backslashes
    all render literally.
    """
    level1 = "".join("\\" + ch if ch in "\\':" else ch for ch in value)
    return "".join("\\" + ch if ch in "\\'[],;" else ch for ch in level1)


# ---- Spoken-word highlight rendering (ASS/libass lane) ----
#
# drawtext draws one string in one color, so per-word highlighting
# renders through the `ass` filter instead: one Dialogue event per
# token window, with the active word wrapped in an inline color
# override. libass lays out identical text identically across events,
# so only the word color changes frame to frame.

_ASS_COLOR_NAMES = {
    "white": "ffffff",
    "black": "000000",
    "red": "ff0000",
    "green": "008000",
    "blue": "0000ff",
    "yellow": "ffff00",
    "cyan": "00ffff",
    "magenta": "ff00ff",
    "orange": "ffa500",
    "gray": "808080",
    "grey": "808080",
}

_ASS_ALIGNMENT = {
    "bottom-left": 1,
    "bottom-center": 2,
    "bottom-right": 3,
    "left": 4,
    "center": 5,
    "right": 6,
    "top-left": 7,
    "top-center": 8,
    "top-right": 9,
}


def _ass_color(css_color: str, alpha: float = 1.0) -> str:
    """CSS-ish color -> ASS &HAABBGGRR& (alpha 00 = opaque)."""
    value = str(css_color).strip().lower()
    if value == "transparent":
        value = "000000"
        alpha = 0.0
    elif value.startswith("#"):
        value = value[1:]
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        if len(value) == 8:
            alpha *= int(value[6:8], 16) / 255.0
            value = value[:6]
    elif value.startswith("0x"):
        value = value[2:]
    elif value in _ASS_COLOR_NAMES:
        value = _ASS_COLOR_NAMES[value]
    else:
        raise ValueError(
            f"Color {css_color!r} is not supported for spoken-word "
            f"highlighted captions; use a hex color like #ffe94a."
        )
    r, g, b = value[0:2], value[2:4], value[4:6]
    ass_alpha = round((1.0 - min(max(alpha, 0.0), 1.0)) * 255)
    return f"&H{ass_alpha:02X}{b.upper()}{g.upper()}{r.upper()}"


def _ass_timestamp(seconds: float) -> str:
    """Seconds -> ASS h:mm:ss.cc timestamp (centisecond precision)."""
    total_cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    """Escape libass override-block braces in literal text."""
    return text.replace("{", "\\{").replace("}", "\\}")


def _ass_style_name(overlay_id: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in overlay_id)


def _ass_event_text(
    plan: dict, active_word: int | None, base_color: str, highlight_color: str
) -> str:
    """One Dialogue event's text: wrapped lines joined with \\N, the
    active word (by global index across lines) recolored inline."""
    word_index = 0
    out_lines = []
    for line in plan["lines"]:
        rendered = []
        for word in line.split():
            escaped = _ass_escape(word)
            if word_index == active_word:
                rendered.append(
                    f"{{\\1c{highlight_color}&}}{escaped}"
                    f"{{\\1c{base_color}&}}"
                )
            else:
                rendered.append(escaped)
            word_index += 1
        out_lines.append(" ".join(rendered))
    return "\\N".join(out_lines)


def build_highlight_ass(
    overlay_plans: list[dict], canvas: tuple[int, int]
) -> str:
    """ASS document rendering the highlight-lane plans on a canvas.

    One Style per plan; per plan, one Dialogue event per token window
    (active word recolored) plus gap events (no active word) so the
    caption never blinks between words. Layer preserves render order.
    """
    width, height = canvas
    styles: list[str] = []
    events: list[str] = []
    for layer, plan in enumerate(overlay_plans):
        highlight = plan["highlight"]
        resolved = plan["resolved_style"]
        opacity = plan["opacity"]
        base_color = _ass_color(resolved.get("color", "#ffffff"), opacity)
        highlight_color = _ass_color(highlight["color"], opacity)
        stroke = plan.get("stroke")
        outline = stroke["width"] if stroke else 0
        outline_color = (
            _ass_color(resolved.get("stroke_color", "#000000"), opacity)
            if stroke
            else _ass_color("#000000", 0.0)
        )
        shadow_px = 0
        back_color = outline_color
        if plan.get("shadow"):
            shadow_px = max(
                abs(plan["shadow"].get("x", 0)), abs(plan["shadow"].get("y", 0))
            )
            # With BorderStyle=1 libass uses BackColour as shadow color.
            back_color = _ass_color(
                resolved.get("text_shadow", "2px 2px black").rsplit(
                    maxsplit=1
                )[-1],
                opacity,
            )
        bold = -1 if plan["font"]["weight"] == "bold" else 0
        style_name = _ass_style_name(plan["id"])
        ass_family = plan["font"].get("ass_family") or plan["font"]["family"]
        styles.append(
            f"Style: {style_name},{ass_family},"
            f"{plan['font_size']},{base_color},{base_color},"
            f"{outline_color},{back_color},{bold},0,0,0,100,100,0,0,1,"
            f"{outline},{shadow_px},5,0,0,0,1"
        )

        alignment = _ASS_ALIGNMENT.get(plan["anchor"], 5)
        ax, ay = plan["anchor_point"]
        prefix = (
            f"{{\\an{alignment}\\pos({round(ax, 2)},{round(ay, 2)})\\q2}}"
        )

        def emit(start: float, end: float, active: int | None) -> None:
            if end - start <= 0.0005:
                return
            start_ts = _ass_timestamp(start)
            end_ts = _ass_timestamp(end)
            if end_ts == start_ts:
                # Sub-centisecond events (static single-frame windows)
                # would round to zero length and never render; one
                # centisecond keeps them visible at t=0.
                end_ts = _ass_timestamp(start + 0.01)
            text = _ass_event_text(plan, active, base_color, highlight_color)
            events.append(
                f"Dialogue: {layer},{start_ts},"
                f"{end_ts},{style_name},,0,0,0,,{prefix}{text}"
            )

        cursor = plan["enable_from"]
        for token in sorted(
            highlight["tokens"], key=lambda t: t["from_local"]
        ):
            token_from = max(token["from_local"], cursor)
            if token_from > cursor:
                emit(cursor, token_from, None)
            emit(token_from, token["to_local"], token["word_index"])
            cursor = max(cursor, token["to_local"])
        if cursor < plan["enable_to"]:
            emit(cursor, plan["enable_to"], None)

    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, "
            "SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
            "MarginV, Encoding",
            *styles,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text",
            *events,
            "",
        ]
    )


def _drawtext_filter(
    plan: dict,
    line: str,
    x_expr: str,
    y_expr: str,
    enable: tuple[float, float] | None,
) -> str:
    """One drawtext filter for one wrapped line of an overlay plan.

    One filter per line keeps horizontal alignment glyph-accurate
    (``text_w`` is per-line); drawtext itself can't center multi-line
    text on the ffmpeg versions we support.
    """
    parts = [
        f"drawtext=fontfile={_filtergraph_escape(plan['font']['path'])}",
        f"text={_filtergraph_escape(line)}",
        "expansion=none",
        f"fontsize={plan['font_size']}",
        f"fontcolor={plan['font_color']}",
    ]
    if plan.get("stroke"):
        parts.append(f"borderw={plan['stroke']['width']}")
        parts.append(f"bordercolor={plan['stroke']['color']}")
    if plan.get("box"):
        parts.append("box=1")
        parts.append(f"boxcolor={plan['box']['color']}")
        if plan["box"]["borderw"]:
            parts.append(f"boxborderw={plan['box']['borderw']}")
    if plan.get("shadow"):
        parts.append(f"shadowx={plan['shadow']['x']}")
        parts.append(f"shadowy={plan['shadow']['y']}")
        parts.append(f"shadowcolor={plan['shadow']['color']}")
    parts.append(f"x={x_expr}")
    parts.append(f"y={y_expr}")
    if enable is not None:
        start, end = enable
        parts.append(f"enable=gte(t\\,{start})*lt(t\\,{end})")
    return ":".join(parts)


def build_overlay_filter_parts(
    overlay_plans: list[dict],
    canvas: tuple[int, int],
    duration: float,
    *,
    input_label: str,
    output_label: str,
    static: bool = False,
    highlight_ass: tuple[str, str] | None = None,
) -> list[str]:
    """Filter parts that burn overlay plans onto a composed stream.

    Plans arrive pre-sorted in render order (z_index, track, id) from
    :func:`moviestar.overlays.plan_overlays`; painter's-algorithm
    chaining makes later plans render on top.

    Three shapes per plan:
    - no rotation: drawtext directly on the running stream, timed with
      a half-open ``gte(t,from)*lt(t,to)`` enable window;
    - rotation: text drawn centered on a transparent full-canvas lane,
      rotated with ``rotw``/``roth`` expansion so corners never clip,
      then overlaid with its center at the anchor target;
    - spoken-word highlight: all highlight-lane plans render in one
      ``ass`` filter pass (``highlight_ass`` = (file path, fontsdir)),
      inserted at the first highlight plan's position so z-order holds.

    ``static=True`` drops enable windows for single-frame renders,
    where plans were already filtered to the frame's timestamp.
    """
    if not overlay_plans:
        return [] if input_label == output_label else [
            f"[{input_label}]null[{output_label}]"
        ]
    if any(p.get("highlight") for p in overlay_plans) and highlight_ass is None:
        raise ValueError(
            "render_overlays: spoken-word highlight plans require a "
            "highlight_ass (path, fontsdir) input"
        )
    width, height = canvas
    parts: list[str] = []
    current = input_label

    # One chain step per drawtext plan plus (at most) one ass step for
    # all highlight plans, placed at the first highlight plan's slot.
    steps: list[dict] = []
    ass_emitted = False
    for plan in overlay_plans:
        if plan.get("highlight"):
            if not ass_emitted:
                steps.append({"ass": True})
                ass_emitted = True
            continue
        steps.append({"plan": plan})

    for i, step in enumerate(steps):
        out = output_label if i == len(steps) - 1 else f"txt{i}"
        if step.get("ass"):
            ass_path, fontsdir = highlight_ass
            parts.append(
                f"[{current}]ass=filename={_filtergraph_escape(ass_path)}"
                f":fontsdir={_filtergraph_escape(fontsdir)}[{out}]"
            )
            current = out
            continue
        plan = step["plan"]
        enable = None if static else (plan["enable_from"], plan["enable_to"])
        if plan["rotate_deg"]:
            radians = f"{plan['rotate_deg']}*PI/180"
            lane_chain = [
                f"color=c=black@0.0:s={width}x{height}:d={max(duration, 0.001)}",
                "format=rgba",
            ]
            block_h = plan["estimated_bounds"]["height"]
            for line_index, line in enumerate(plan["lines"]):
                # Lane text centers on the lane; the overlay step places it.
                y_centered = (
                    f"(h-{block_h})/2+{line_index * plan['line_height']}"
                )
                lane_chain.append(
                    _drawtext_filter(
                        plan, line, "(w-text_w)/2", y_centered, None
                    )
                )
            if plan["opacity"] < 1.0:
                lane_chain.append(f"colorchannelmixer=aa={plan['opacity']}")
            lane_chain.append(
                f"rotate={radians}:ow=rotw({radians}):oh=roth({radians})"
                f":c=none"
            )
            lane_label = f"txtlane{i}"
            parts.append(",".join(lane_chain) + f"[{lane_label}]")
            cx, cy = plan["lane_center"]
            overlay_opts = [
                f"x={round(cx, 2)}-overlay_w/2",
                f"y={round(cy, 2)}-overlay_h/2",
            ]
            if enable is not None:
                overlay_opts.append(
                    f"enable=gte(t\\,{enable[0]})*lt(t\\,{enable[1]})"
                )
            parts.append(
                f"[{current}][{lane_label}]overlay="
                + ":".join(overlay_opts)
                + f"[{out}]"
            )
        else:
            chain = [
                _drawtext_filter(plan, line, x_expr, y_expr, enable)
                for line, (x_expr, y_expr) in zip(
                    plan["lines"], plan["line_exprs"]
                )
            ]
            parts.append(f"[{current}]" + ",".join(chain) + f"[{out}]")
        current = out
    return parts


def _append_audio_from_inputs(
    cmd: list[str],
    start_index: int,
    audio_from_input: tuple[str, list[tuple[float, float]]] | None,
) -> list[str]:
    if audio_from_input is None:
        return []
    af_path, af_slices = audio_from_input
    if not af_slices:
        raise ValueError("render_layout: audio_from_input requires at least one slice")
    labels: list[str] = []
    for j, (af_from, af_to) in enumerate(af_slices):
        if af_from < 0 or af_from >= af_to:
            raise ValueError(
                f"render_layout: invalid audio_from_input slice ({af_from}, {af_to})"
            )
        cmd += ["-ss", str(af_from), "-i", af_path]
        labels.append(f"af{j}")
    return labels


def _audio_from_filter_parts(
    *,
    start_index: int,
    audio_from_input: tuple[str, list[tuple[float, float]]] | None,
    audio_speed: float = 1.0,
) -> tuple[list[str], str | None]:
    if audio_from_input is None:
        return [], None
    _af_path, af_slices = audio_from_input
    parts: list[str] = []
    labels: list[str] = []
    for j, (af_from, af_to) in enumerate(af_slices):
        duration = af_to - af_from
        label = f"af{j}"
        retime = ""
        if not math.isclose(audio_speed, 1.0):
            remaining = audio_speed
            factors: list[float] = []
            while remaining > 2.0 + 1e-9:
                factors.append(2.0)
                remaining /= 2.0
            while remaining < 0.5 - 1e-9:
                factors.append(0.5)
                remaining /= 0.5
            if not math.isclose(remaining, 1.0):
                factors.append(remaining)
            retime = "," + ",".join(f"atempo={factor}" for factor in factors)
        parts.append(
            f"[{start_index + j}:a]atrim=start=0:end={duration},"
            f"asetpts=PTS-STARTPTS{retime}[{label}]"
        )
        labels.append(f"[{label}]")
    if len(labels) == 1:
        return parts, labels[0]
    parts.append(f"{''.join(labels)}aconcat=n={len(labels)}:v=0:a=1[afout]")
    return parts, "[afout]"


def build_render_layout_video_command(
    layout_slots: list[dict],
    output_path: str,
    canvas: tuple[int, int],
    duration: float,
    preview: bool = False,
    audio_from_input: tuple[str, list[tuple[float, float]]] | None = None,
    overlay_plans: list[dict] | None = None,
    highlight_ass: tuple[str, str] | None = None,
    audio_speed: float = 1.0,
    output_fps: float | None = None,
) -> list[str]:
    """Render simultaneous layout slots composited onto one canvas."""
    _validate_layout_slots(layout_slots, canvas, duration)
    cmd: list[str] = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-progress", "pipe:2", "-nostats",
    ]
    _append_layout_video_inputs(cmd, layout_slots)
    _append_audio_from_inputs(cmd, len(layout_slots), audio_from_input)
    audio_input_count = len(audio_from_input[1]) if audio_from_input else 0
    _append_shape_mask_inputs(cmd, layout_slots, duration)

    # Overlays burn in at canvas resolution, before any preview
    # downscale, so preview and full renders show identical text.
    composed_label = "vlaid" if overlay_plans else (
        "vraw" if preview else "outv"
    )
    filter_parts = _layout_filter_parts(
        layout_slots,
        canvas,
        duration,
        output_label=composed_label,
        output_fps=output_fps,
        mask_start_index=len(layout_slots) + audio_input_count,
    )
    if overlay_plans:
        filter_parts.extend(
            build_overlay_filter_parts(
                overlay_plans,
                canvas,
                duration,
                input_label=composed_label,
                output_label="vraw" if preview else "outv",
                highlight_ass=highlight_ass,
            )
        )
    audio_parts, audio_label = _audio_from_filter_parts(
        start_index=len(layout_slots),
        audio_from_input=audio_from_input,
        audio_speed=audio_speed,
    )
    filter_parts.extend(audio_parts)
    if preview:
        filter_parts.append("[vraw]scale=-2:480[outv]")

    cmd += ["-filter_complex", ";".join(filter_parts), "-map", "[outv]"]
    if audio_label is not None:
        cmd += ["-map", audio_label]
    if preview:
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "18"]
    if audio_label is not None:
        cmd += ["-c:a", "aac"]
    cmd.append(output_path)
    return cmd


def build_render_layout_frame_command(
    layout_slots: list[dict],
    output_path: str,
    canvas: tuple[int, int],
    quality: int = 2,
    overlay_plans: list[dict] | None = None,
    highlight_ass: tuple[str, str] | None = None,
) -> list[str]:
    """Render one composited layout frame to an image file."""
    _validate_layout_slots(
        layout_slots, canvas, duration=LAYOUT_FRAME_DECODE_DURATION
    )
    cmd: list[str] = ["ffmpeg", "-y", "-loglevel", "error"]
    _append_layout_video_inputs(cmd, layout_slots)
    _append_shape_mask_inputs(
        cmd, layout_slots, LAYOUT_FRAME_DECODE_DURATION
    )
    if overlay_plans:
        filter_parts = _layout_filter_parts(
            layout_slots,
            canvas,
            LAYOUT_FRAME_DECODE_DURATION,
            output_label="vlaid",
            frame_decode=True,
            mask_start_index=len(layout_slots),
        )
        # Single frame: plans were filtered to the frame's timestamp
        # already, so enable windows are dropped (static=True).
        filter_parts.extend(
            build_overlay_filter_parts(
                overlay_plans,
                canvas,
                LAYOUT_FRAME_DECODE_DURATION,
                input_label="vlaid",
                output_label="outv",
                static=True,
                highlight_ass=highlight_ass,
            )
        )
    else:
        filter_parts = _layout_filter_parts(
            layout_slots,
            canvas,
            LAYOUT_FRAME_DECODE_DURATION,
            frame_decode=True,
            mask_start_index=len(layout_slots),
        )
    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[outv]",
        "-frames:v", "1",
        "-q:v", str(quality),
        output_path,
    ]
    return cmd


def build_render_layout_frames_command(
    layout_slots: list[dict],
    output_pattern: str,
    canvas: tuple[int, int],
    duration: float,
    interval: float,
    scale_width: int = 320,
    quality: int = 5,
    overlay_plans: list[dict] | None = None,
    highlight_ass: tuple[str, str] | None = None,
) -> list[str]:
    """Render a sequence of composited layout thumbnails."""
    if interval <= 0:
        raise ValueError("render_layout: interval must be positive")
    _validate_layout_slots(layout_slots, canvas, duration)
    cmd: list[str] = ["ffmpeg", "-y", "-loglevel", "error"]
    _append_layout_video_inputs(cmd, layout_slots)
    _append_shape_mask_inputs(cmd, layout_slots, duration)
    composed_label = "vlaid" if overlay_plans else "vraw"
    filter_parts = _layout_filter_parts(
        layout_slots, canvas, duration, output_label=composed_label,
        mask_start_index=len(layout_slots),
    )
    if overlay_plans:
        # Overlays apply at canvas resolution before thumbnail
        # downscale so text lands in thumbnails too.
        filter_parts.extend(
            build_overlay_filter_parts(
                overlay_plans,
                canvas,
                duration,
                input_label=composed_label,
                output_label="vraw",
                highlight_ass=highlight_ass,
            )
        )
    filter_parts.append(f"[vraw]fps=1/{interval},scale={scale_width}:-1[outv]")
    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[outv]",
        "-q:v", str(quality),
        "-threads", "1",
        "-strict", "unofficial",
        output_pattern,
    ]
    return cmd


def build_render_segments_command(
    segments: list[tuple[str, float, float]],
    output_path: str,
    precise: bool = False,
    preview: bool = False,
    segment_has_audio: list[bool] | None = None,
    segment_fps: list[float] | None = None,
    fill_missing_audio_with_silence: bool = False,
    segment_resolutions: list[tuple[int, int] | None] | None = None,
    audio_from_input: tuple[str, list[tuple[float, float]]] | None = None,
    audio_join_fade: float | None = None,
    output_canvas: tuple[int, int] | None = None,
    segment_framings: list[dict | None] | None = None,
    overlay_plans: list[dict] | None = None,
    overlay_canvas: tuple[int, int] | None = None,
    highlight_ass: tuple[str, str] | None = None,
    output_fps: float | None = None,
    video_segment_durations: list[float] | None = None,
    video_transitions: list[dict] | None = None,
) -> list[str]:
    """Render multiple source-time ranges concatenated to one output.

    ``segments`` is a list of ``(source_path, src_from, src_to)`` tuples
    rendered in order. Multi-segment lists emit **one ``-i`` per segment,
    each preceded by its own ``-ss <src_from>``** so ffmpeg fast-seeks
    each input to its segment start instead of demuxing from t=0. On
    large source files this avoids decoding each file from its beginning.

    The trade-off versus the input-dedup shape (one ``-i`` per
    unique source, multiple ``trim`` filters from t=0) is that we open
    each source file multiple times when a single source contributes
    multiple segments. Memory cost is negligible; the win is that each
    decoder only processes its own slice, not the entire source from t=0.

    Single-segment lists defer to :func:`build_extract_clip_command` so
    the trim-only argv shape is preserved (stream-copy stays cheap).
    Multi-segment lists always re-encode via filter_complex concat —
    ``-c copy`` is incompatible with filter graphs, and stream-copy
    across keyframe boundaries was already approximate.

    ``segment_has_audio`` is a parallel list indicating which segments
    have an audio stream. Defaults to "all have audio" if omitted. If
    *any* segment is audio-less, the output drops audio entirely unless
    ``fill_missing_audio_with_silence`` is true, in which case segments
    with audio keep it and audio-less segments receive generated silence.

    ``segment_fps`` is a parallel list of source frame rates. When supplied,
    each segment is filled onto that CFR grid before timestamps are rebased,
    preserving frameless VFR spans, then clone-padded to its authored duration.

    ``output_fps`` enforces one cadence after concat. Supplying it also disables
    the single-segment stream-copy shortcut because CFR normalization requires
    a video filter.

    ``video_segment_durations`` and ``video_transitions`` render scene-owned
    visual transition handles without changing the authored result or audio
    timeline. Video clips may be longer than their matching ``segments`` entry;
    audio is still trimmed to the original segment duration. Each transition
    names the incoming clip index, MovieStar transition type, and duration.

    ``audio_from_input``: when set, replaces per-segment
    audio with a single audio track sourced from one named source. The
    tuple is ``(path, [(src_from, src_to), ...])`` — one or more
    source-time slices of that source's edited result, summing to the
    composition duration. Extra ``-i`` inputs are appended after the
    video segments (input indices ``[n, n+k)``), atrim'd to their
    slices, aconcat'd to one ``[outa]`` stream, and mapped as the
    output's only audio. ``segment_has_audio`` is ignored under this
    path — the named source's audio is authoritative. Single-segment
    video shortcut is also skipped (filter_complex required to mix a
    custom audio track).

    ``output_canvas`` / ``segment_framings``: when set, every
    segment is scaled into the requested output canvas before concat.
    ``fill`` scales to cover then crops according to the anchor; ``fit``
    scales to fit then pads. This intentionally skips the single-segment
    stream-copy shortcut because canvas rendering needs a filter graph.
    """
    if not segments:
        raise ValueError("render_segments: empty segments list")

    if segment_has_audio is None:
        segment_has_audio = [True] * len(segments)
    if len(segment_has_audio) != len(segments):
        raise ValueError(
            "render_segments: segment_has_audio length must match segments"
        )
    if segment_fps is not None:
        if len(segment_fps) != len(segments):
            raise ValueError(
                "render_segments: segment_fps length must match segments"
            )
        if any(fps <= 0 for fps in segment_fps):
            raise ValueError("render_segments: segment_fps values must be positive")
    if output_fps is not None and output_fps <= 0:
        raise ValueError("render_segments: output_fps must be positive")
    if (video_segment_durations is None) != (video_transitions is None):
        raise ValueError(
            "render_segments: video_segment_durations and video_transitions "
            "must be supplied together"
        )
    if video_segment_durations is not None:
        if len(video_segment_durations) != len(segments):
            raise ValueError(
                "render_segments: video_segment_durations length must match "
                "segments"
            )
        for i, (video_duration, segment) in enumerate(
            zip(video_segment_durations, segments)
        ):
            authored_duration = segment[2] - segment[1]
            if (
                isinstance(video_duration, bool)
                or not isinstance(video_duration, (int, float))
                or not math.isfinite(video_duration)
                or video_duration + 1e-9 < authored_duration
            ):
                raise ValueError(
                    "render_segments: video_segment_durations must be finite "
                    f"and no shorter than authored segment {i}"
                )
    transitions_by_index: dict[int, dict] = {}
    video_prerolls = [0.0] * len(segments)
    if video_transitions is not None:
        required_video_durations = [
            segment[2] - segment[1] for segment in segments
        ]
        for transition in video_transitions:
            if not isinstance(transition, dict):
                raise ValueError(
                    "render_segments: each video transition must be an object"
                )
            incoming_index = transition.get("incoming_index")
            if (
                not isinstance(incoming_index, int)
                or incoming_index <= 0
                or incoming_index >= len(segments)
                or incoming_index in transitions_by_index
            ):
                raise ValueError(
                    "render_segments: each transition incoming_index must be "
                    "a unique internal segment boundary"
                )
            transition_type = transition.get("type")
            if transition_type not in {"dissolve", "dip-black", "dip-white"}:
                raise ValueError(
                    "render_segments: unknown video transition type "
                    f"{transition_type!r}"
                )
            duration = transition.get("duration")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration <= 0
            ):
                raise ValueError(
                    "render_segments: video transition duration must be positive"
                )
            adjacent_durations = [
                segments[incoming_index - 1][2]
                - segments[incoming_index - 1][1],
                segments[incoming_index][2] - segments[incoming_index][1],
            ]
            if duration > 2 * min(adjacent_durations) + 1e-9:
                raise ValueError(
                    "render_segments: video transition duration exceeds its "
                    "adjacent segment windows"
                )
            transitions_by_index[incoming_index] = transition
            if transition_type == "dissolve":
                half = float(duration) / 2
                required_video_durations[incoming_index - 1] += half
                required_video_durations[incoming_index] += half
                video_prerolls[incoming_index] += half
        assert video_segment_durations is not None
        for i, (actual, required) in enumerate(
            zip(video_segment_durations, required_video_durations)
        ):
            if actual + 1e-9 < required:
                raise ValueError(
                    "render_segments: video_segment_durations lacks the "
                    f"required dissolve handles for segment {i}"
                )
    if fill_missing_audio_with_silence and audio_from_input is not None:
        raise ValueError(
            "render_segments: fill_missing_audio_with_silence is incompatible "
            "with audio_from_input"
        )
    if audio_join_fade is not None:
        if audio_join_fade < 0:
            raise ValueError("render_segments: audio_join_fade cannot be negative")
        if audio_join_fade == 0:
            audio_join_fade = None
    if audio_join_fade is not None:
        shortest_segment = min(src_to - src_from for _path, src_from, src_to in segments)
        max_fade = shortest_segment / 2
        if audio_join_fade > max_fade:
            raise ValueError(
                "render_segments: audio_join_fade must be no longer than half "
                "the shortest segment duration"
            )
    if audio_join_fade is not None and audio_from_input is not None:
        raise ValueError(
            "render_segments: audio_join_fade is incompatible with audio_from_input"
        )

    if audio_from_input is not None:
        af_path, af_slices = audio_from_input
        if not af_slices:
            raise ValueError(
                "render_segments: audio_from_input requires at least one slice"
            )
        for af_from, af_to in af_slices:
            if af_from < 0 or af_from >= af_to:
                raise ValueError(
                    f"render_segments: invalid audio_from_input slice "
                    f"({af_from}, {af_to})"
                )
        # Named-source audio wins; ignore per-segment audio presence.
        use_audio = True
    else:
        use_audio = (
            any(segment_has_audio)
            if fill_missing_audio_with_silence
            else all(segment_has_audio)
        )

    if segment_resolutions is not None and len(segment_resolutions) != len(segments):
        raise ValueError(
            "render_segments: segment_resolutions length must match segments"
        )
    if output_canvas is None and segment_framings is not None:
        raise ValueError("render_segments: segment_framings requires output_canvas")
    if output_canvas is not None:
        canvas_width, canvas_height = output_canvas
        if canvas_width <= 0 or canvas_height <= 0:
            raise ValueError("render_segments: output_canvas dimensions must be positive")
        if segment_framings is None:
            segment_framings = [None] * len(segments)
        if len(segment_framings) != len(segments):
            raise ValueError(
                "render_segments: segment_framings length must match segments"
            )

    # ffmpeg's concat filter requires identical resolution + SAR across
    # inputs. Normalize every segment to the first segment's resolution
    # so multi-source compositions with mixed-size sources just work.
    # No-op when all segments share the same resolution.
    target_resolution: tuple[int, int] | None = None
    if output_canvas is None and segment_resolutions is not None:
        first = next((r for r in segment_resolutions if r is not None), None)
        if first is not None and any(
            r is not None and r != first for r in segment_resolutions
        ):
            target_resolution = first

    if overlay_plans and overlay_canvas is None:
        raise ValueError("render_segments: overlay_canvas is required with overlays")

    # Single-segment trim-only shortcut (stream-copy stays cheap). Skip
    # when audio_from_input is set because we need filter_complex to
    # mix the named-source audio in regardless of segment count. Skip
    # when output_canvas is set because scale/crop/pad needs a filter
    # graph even for one source range. Skip when overlays are present
    # because drawtext/ass burn-in also needs a filter graph.
    if (
        len(segments) == 1
        and audio_from_input is None
        and audio_join_fade is None
        and output_canvas is None
        and not overlay_plans
        and output_fps is None
        and video_transitions is None
    ):
        path, src_from, src_to = segments[0]
        return build_extract_clip_command(
            path, src_from, src_to - src_from, output_path,
            precise=precise, preview=preview,
        )

    # Multi-segment path. One -i per segment so each decoder fast-
    # seeks to its own slice instead of reading the file from t=0.
    # `-progress pipe:2` emits structured key=value progress blocks to
    # stderr (~1/sec) so render_segments can re-emit moviestar-shaped
    # progress lines for long renders (issue #133). `-nostats` strips
    # ffmpeg's default \r-style stats so the stderr stream is just
    # progress blocks + real errors — easy to demux.
    cmd: list[str] = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-progress", "pipe:2", "-nostats",
    ]
    for path, src_from, _src_to in segments:
        cmd += ["-ss", str(src_from), "-i", path]
    # Extra -i per audio_from slice — same fast-seek shape, just feeds
    # the audio side of the filter graph instead of the video side.
    if audio_from_input is not None:
        af_path, af_slices = audio_from_input
        for af_from, _af_to in af_slices:
            cmd += ["-ss", str(af_from), "-i", af_path]

    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for i, (_path, src_from, src_to) in enumerate(segments):
        # Input-side `-ss <src_from>` already fast-seeked the decoder
        # to the segment start, and ffmpeg resets the post-seek
        # stream's timestamps to 0. So the trim filter operates in
        # POST-SEEK time: start=0 to end=<segment duration>. ffmpeg's
        # default accurate-seek mode handles keyframe alignment; the
        # trim filter is a belt-and-braces guard for any decoders that
        # ignore it.
        duration = src_to - src_from
        video_duration = (
            video_segment_durations[i]
            if video_segment_durations is not None
            else duration
        )
        if segment_fps is not None:
            # VFR sources can contain multi-second spans with no frames
            # (notably macOS screen recordings while the screen is static).
            # Fill those spans before rebasing timestamps; rebasing first
            # collapses a frameless lead-in and silently shortens the segment.
            # Clone-pad the tail so concat receives exactly the authored
            # duration even when the source has no late frames (issue #364).
            fps = segment_fps[i]
            video_chain = (
                f"[{i}:v]trim=start=0:end={video_duration},"
                f"fps={fps}:start_time=0,"
                f"setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration={video_duration},"
                f"trim=end={video_duration}"
            )
        else:
            video_chain = (
                f"[{i}:v]trim=start=0:end={video_duration},"
                f"setpts=PTS-STARTPTS"
            )
        if video_transitions is not None:
            video_chain += ",settb=AVTB"
        if target_resolution is not None:
            w, h = target_resolution
            video_chain += f",scale={w}:{h},setsar=1"
        if output_canvas is not None:
            assert segment_framings is not None
            video_chain += "," + _canvas_filter(
                output_canvas[0],
                output_canvas[1],
                segment_framings[i],
            )
        incoming_transition = transitions_by_index.get(i)
        if incoming_transition is not None and incoming_transition["type"] in {
            "dip-black", "dip-white"
        }:
            half = float(incoming_transition["duration"]) / 2
            color = (
                "black"
                if incoming_transition["type"] == "dip-black"
                else "white"
            )
            video_chain += (
                f",fade=t=in:st=0:d={_audio_number(half)}:color={color}"
            )
        outgoing_transition = transitions_by_index.get(i + 1)
        if outgoing_transition is not None and outgoing_transition["type"] in {
            "dip-black", "dip-white"
        }:
            half = float(outgoing_transition["duration"]) / 2
            color = (
                "black"
                if outgoing_transition["type"] == "dip-black"
                else "white"
            )
            fade_start = video_prerolls[i] + duration - half
            video_chain += (
                f",fade=t=out:st={_audio_number(fade_start)}:"
                f"d={_audio_number(half)}:color={color}"
            )
        filter_parts.append(f"{video_chain}[v{i}]")
        # Per-segment audio only when there is no override.
        if audio_from_input is None and use_audio:
            fade_duration = audio_join_fade if audio_join_fade is not None else None
            if segment_has_audio[i]:
                audio_chain = (
                    f"[{i}:a]atrim=start=0:end={duration},"
                    f"asetpts=PTS-STARTPTS"
                )
            else:
                audio_chain = f"anullsrc=r=48000:cl=stereo:d={duration},asetpts=PTS-STARTPTS"
            if fade_duration is not None:
                if i > 0:
                    audio_chain += f",afade=t=in:st=0:d={fade_duration}"
                if i < len(segments) - 1:
                    fade_start = max(0.0, duration - fade_duration)
                    audio_chain += f",afade=t=out:st={fade_start}:d={fade_duration}"
            filter_parts.append(f"{audio_chain}[a{i}]")
            concat_inputs.append(
                f"[v{i}]" if video_transitions is not None else f"[v{i}][a{i}]"
            )
        else:
            concat_inputs.append(f"[v{i}]")

    n = len(segments)
    # Build the audio-side filter chain when audio_from_input drives it.
    # Each slice becomes one atrim'd stream; multiple slices aconcat
    # together; the result is the only [outa] of the final concat.
    if audio_from_input is not None:
        af_path, af_slices = audio_from_input
        af_streams: list[str] = []
        for j, (af_from, af_to) in enumerate(af_slices):
            af_duration = af_to - af_from
            label = f"af{j}"
            filter_parts.append(
                f"[{n + j}:a]atrim=start=0:end={af_duration},"
                f"asetpts=PTS-STARTPTS[{label}]"
            )
            af_streams.append(f"[{label}]")
        if len(af_streams) == 1:
            audio_out_label = af_streams[0].strip("[]")
        else:
            filter_parts.append(
                f"{''.join(af_streams)}aconcat=n={len(af_streams)}:v=0:a=1[afout]"
            )
            audio_out_label = "afout"
    else:
        audio_out_label = None

    a_count = 1 if (use_audio and audio_from_input is None) else 0
    video_output_label = (
        "vraw" if (preview or overlay_plans or output_fps is not None) else "outv"
    )
    concat_outputs = (
        f"[{video_output_label}][outa]" if a_count else f"[{video_output_label}]"
    )
    if video_transitions is not None:
        current_video_label = "v0"
        authored_elapsed = segments[0][2] - segments[0][1]
        for i in range(1, n):
            next_label = f"vx{i}"
            transition = transitions_by_index.get(i)
            if transition is None or transition["type"] in {
                "dip-black", "dip-white"
            }:
                filter_parts.append(
                    f"[{current_video_label}][v{i}]"
                    f"concat=n=2:v=1:a=0[{next_label}]"
                )
            else:
                transition_duration = float(transition["duration"])
                offset = authored_elapsed - transition_duration / 2
                filter_parts.append(
                    f"[{current_video_label}][v{i}]xfade="
                    "transition=fade:"
                    f"duration={_audio_number(transition_duration)}:"
                    f"offset={_audio_number(offset)}[{next_label}]"
                )
            current_video_label = next_label
            authored_elapsed += segments[i][2] - segments[i][1]
        if current_video_label != video_output_label:
            filter_parts.append(
                f"[{current_video_label}]null[{video_output_label}]"
            )
        if a_count:
            filter_parts.append(
                f"{''.join(f'[a{i}]' for i in range(n))}"
                f"concat=n={n}:v=0:a=1[outa]"
            )
    elif preview:
        # Concat to a raw label first; overlays, if present, burn in
        # before the preview downscale so preview and full render place
        # text the same way.
        raw_outputs = (
            f"[{video_output_label}][outa]" if a_count else f"[{video_output_label}]"
        )
        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={n}:v=1:a={a_count}{raw_outputs}"
        )
    else:
        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={n}:v=1:a={a_count}{concat_outputs}"
        )

    if overlay_plans:
        assert overlay_canvas is not None
        overlay_out = "vov" if (preview or output_fps is not None) else "outv"
        filter_parts.extend(
            build_overlay_filter_parts(
                overlay_plans,
                overlay_canvas,
                sum(src_to - src_from for _path, src_from, src_to in segments),
                input_label=video_output_label,
                output_label=overlay_out,
                highlight_ass=highlight_ass,
            )
        )
        video_output_label = overlay_out
    if output_fps is not None:
        fps_out = "vfps" if preview else "outv"
        filter_parts.append(
            f"[{video_output_label}]fps={output_fps}[{fps_out}]"
        )
        video_output_label = fps_out
    if preview:
        filter_parts.append(f"[{video_output_label}]scale=-2:480[outv]")

    mapped_audio_label: str | None
    if audio_from_input is not None:
        mapped_audio_label = audio_out_label
    elif use_audio:
        mapped_audio_label = "outa"
    else:
        mapped_audio_label = None
    cmd += ["-filter_complex", ";".join(filter_parts)]
    cmd += ["-map", "[outv]"]
    if mapped_audio_label is not None:
        cmd += ["-map", f"[{mapped_audio_label}]"]
    if preview:
        cmd += [
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        ]
        if use_audio:
            cmd += ["-c:a", "aac"]
    else:
        # Multi-segment always re-encodes. precise=True and the stream-copy
        # fallback both land here; the caller's intent of "fast" can't be
        # honored across a filter graph, so the envelope layer is responsible
        # for surfacing the override in the response.
        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        ]
        if use_audio:
            cmd += ["-c:a", "aac"]
    cmd.append(output_path)
    return cmd


_PROGRESS_KV_LINE = re.compile(r"^[a-z_]+=.+$")


def _format_render_elapsed(seconds: float) -> str:
    """Compact wall-clock formatter for the in-render progress line.
    Mirrors :func:`moviestar.cli._format_elapsed` minus its sub-second
    rendering (renders take seconds-to-minutes, not microseconds).
    """
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def _format_position(seconds: float) -> str:
    """HH:MM:SS for in-render progress; matches ffmpeg's `out_time`
    field shape so the agent recognizes it."""
    if seconds < 0:
        seconds = 0.0
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def render_segments(
    segments: list[tuple[str, float, float]],
    output_path: str,
    precise: bool = False,
    preview: bool = False,
    segment_has_audio: list[bool] | None = None,
    segment_fps: list[float] | None = None,
    fill_missing_audio_with_silence: bool = False,
    segment_resolutions: list[tuple[int, int] | None] | None = None,
    audio_from_input: tuple[str, list[tuple[float, float]]] | None = None,
    audio_join_fade: float | None = None,
    output_canvas: tuple[int, int] | None = None,
    segment_framings: list[dict | None] | None = None,
    overlay_plans: list[dict] | None = None,
    overlay_canvas: tuple[int, int] | None = None,
    highlight_ass: tuple[str, str] | None = None,
    output_fps: float | None = None,
    video_segment_durations: list[float] | None = None,
    video_transitions: list[dict] | None = None,
    progress_cb=None,
) -> list[str]:
    """Render multiple source-time ranges concatenated to one output.

    See :func:`build_render_segments_command` for the argv shape and
    the single-vs-multi-segment fallback.

    ``progress_cb`` (issue #133): optional ``Callable[[str], None]``
    invoked with one moviestar-shaped progress line per emitted
    update, e.g. ``"[elapsed 12s] rendered 0:00:23 / 0:01:05 (35%)"``.
    Default is ``sys.stderr.write`` (with a trailing newline) so a
    long export shows liveness instead of looking hung. The multi-
    segment path adds ``-progress pipe:2`` to the ffmpeg argv; the
    single-segment stream-copy shortcut (``build_extract_clip_command``)
    is sub-second and doesn't emit progress.

    Raises:
        FileNotFoundError: if any source file does not exist.
        FFmpegNotFoundError: if ffmpeg is not installed.
        RuntimeError: if ffmpeg returns a non-zero exit code.
        ValueError: if ``segments`` is empty.
    """
    if not segments:
        raise ValueError("render_segments: empty segments list")

    for path, _, _ in segments:
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
    if audio_from_input is not None:
        af_path, _ = audio_from_input
        if not os.path.exists(af_path):
            raise FileNotFoundError(f"File not found: {af_path}")

    check_ffmpeg_available()
    preflight_overlay_render_capabilities(overlay_plans)

    cmd = build_render_segments_command(
        segments, output_path,
        precise=precise, preview=preview,
        segment_has_audio=segment_has_audio,
        segment_fps=segment_fps,
        fill_missing_audio_with_silence=fill_missing_audio_with_silence,
        segment_resolutions=segment_resolutions,
        audio_from_input=audio_from_input,
        audio_join_fade=audio_join_fade,
        output_canvas=output_canvas,
        segment_framings=segment_framings,
        overlay_plans=overlay_plans,
        overlay_canvas=overlay_canvas,
        highlight_ass=highlight_ass,
        output_fps=output_fps,
        video_segment_durations=video_segment_durations,
        video_transitions=video_transitions,
    )

    # Single-segment stream-copy shortcut skips the progress wiring —
    # build_extract_clip_command doesn't add `-progress` and the
    # operation is sub-second anyway. Detect by the absence of the
    # progress flag we inject in the multi-segment path.
    if "-progress" not in cmd:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(_failure_message("ffmpeg", result))
        return cmd

    if progress_cb is None:
        progress_cb = lambda line: sys.stderr.write(line + "\n")

    total_duration = sum(src_to - src_from for _, src_from, src_to in segments)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    start = time.time()
    last_emit = 0.0
    error_lines: list[str] = []
    assert proc.stderr is not None
    for raw_line in proc.stderr:
        line = raw_line.rstrip("\n")
        # Demux: ffmpeg's structured progress is `key=value`; real
        # errors don't match that shape. Buffer the latter for the
        # failure message; parse the former for the progress signal.
        if _PROGRESS_KV_LINE.match(line):
            if line.startswith("out_time_us="):
                try:
                    position = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                elapsed = time.time() - start
                # Throttle to ~1/sec so a fast render doesn't spam the
                # console; emit immediately on the first sample.
                if last_emit > 0 and (elapsed - last_emit) < 1.0:
                    continue
                last_emit = elapsed
                if total_duration > 0:
                    pct = min(100, int(position / total_duration * 100))
                    pct_text = f" ({pct}%)"
                else:
                    pct_text = ""
                progress_cb(
                    f"[elapsed {_format_render_elapsed(elapsed)}] "
                    f"rendered {_format_position(position)} / "
                    f"{_format_position(total_duration)}{pct_text}"
                )
        else:
            error_lines.append(line)

    proc.wait()
    if proc.returncode != 0:
        # Synthesize the same shape _failure_message would produce
        # from a captured CompletedProcess.
        stderr_text = "\n".join(error_lines).strip()
        detail = stderr_text if stderr_text else f"exit code {proc.returncode}"
        raise RuntimeError(f"ffmpeg failed: {detail}")
    return cmd


def render_layout_video(
    layout_slots: list[dict],
    output_path: str,
    canvas: tuple[int, int],
    duration: float,
    preview: bool = False,
    audio_from_input: tuple[str, list[tuple[float, float]]] | None = None,
    progress_cb=None,
    overlay_plans: list[dict] | None = None,
    highlight_ass: tuple[str, str] | None = None,
    audio_speed: float = 1.0,
    output_fps: float | None = None,
) -> list[str]:
    """Render simultaneous layout slots composited onto one MP4."""
    for slot in layout_slots:
        if not os.path.exists(slot["path"]):
            raise FileNotFoundError(f"File not found: {slot['path']}")
    if audio_from_input is not None:
        af_path, _af_slices = audio_from_input
        if not os.path.exists(af_path):
            raise FileNotFoundError(f"File not found: {af_path}")

    check_ffmpeg_available()
    preflight_overlay_render_capabilities(overlay_plans)
    preflight_shape_render_capabilities(layout_slots)
    cmd = build_render_layout_video_command(
        layout_slots,
        output_path,
        canvas,
        duration,
        preview=preview,
        audio_from_input=audio_from_input,
        overlay_plans=overlay_plans,
        highlight_ass=highlight_ass,
        audio_speed=audio_speed,
        output_fps=output_fps,
    )
    if progress_cb is None:
        progress_cb = lambda line: sys.stderr.write(line + "\n")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    start = time.time()
    last_emit = 0.0
    error_lines: list[str] = []
    assert proc.stderr is not None
    for raw_line in proc.stderr:
        line = raw_line.rstrip("\n")
        if _PROGRESS_KV_LINE.match(line):
            if line.startswith("out_time_us="):
                try:
                    position = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                elapsed = time.time() - start
                if last_emit > 0 and (elapsed - last_emit) < 1.0:
                    continue
                last_emit = elapsed
                if duration > 0:
                    pct = min(100, int(position / duration * 100))
                    pct_text = f" ({pct}%)"
                else:
                    pct_text = ""
                progress_cb(
                    f"[elapsed {_format_render_elapsed(elapsed)}] "
                    f"rendered {_format_position(position)} / "
                    f"{_format_position(duration)}{pct_text}"
                )
        else:
            error_lines.append(line)

    proc.wait()
    if proc.returncode != 0:
        stderr_text = "\n".join(error_lines).strip()
        detail = stderr_text if stderr_text else f"exit code {proc.returncode}"
        raise RuntimeError(f"ffmpeg failed: {detail}")
    return cmd


def render_layout_frame(
    layout_slots: list[dict],
    output_path: str,
    canvas: tuple[int, int],
    quality: int = 2,
    overlay_plans: list[dict] | None = None,
    highlight_ass: tuple[str, str] | None = None,
) -> list[str]:
    """Render one composited layout frame to an image file."""
    for slot in layout_slots:
        if not os.path.exists(slot["path"]):
            raise FileNotFoundError(f"File not found: {slot['path']}")
    check_ffmpeg_available()
    preflight_overlay_render_capabilities(overlay_plans)
    preflight_shape_render_capabilities(layout_slots)
    cmd = build_render_layout_frame_command(
        layout_slots,
        output_path,
        canvas,
        quality=quality,
        overlay_plans=overlay_plans,
        highlight_ass=highlight_ass,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    return cmd


def render_layout_frames(
    layout_slots: list[dict],
    output_dir: Path,
    output_prefix: str,
    canvas: tuple[int, int],
    duration: float,
    interval: float,
    scale_width: int = 320,
    quality: int = 5,
    overlay_plans: list[dict] | None = None,
    highlight_ass: tuple[str, str] | None = None,
) -> tuple[list[Path], list[str]]:
    """Render composited layout thumbnails. Returns (sorted paths, argv)."""
    for slot in layout_slots:
        if not os.path.exists(slot["path"]):
            raise FileNotFoundError(f"File not found: {slot['path']}")
    check_ffmpeg_available()
    preflight_overlay_render_capabilities(overlay_plans)
    preflight_shape_render_capabilities(layout_slots)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / f"{output_prefix}_%04d.jpg")
    cmd = build_render_layout_frames_command(
        layout_slots,
        pattern,
        canvas,
        duration,
        interval,
        scale_width=scale_width,
        quality=quality,
        overlay_plans=overlay_plans,
        highlight_ass=highlight_ass,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_failure_message("ffmpeg", result))
    paths = sorted(output_dir.glob(f"{output_prefix}_*.jpg"))
    return paths, cmd
