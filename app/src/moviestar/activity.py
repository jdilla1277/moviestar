"""Deterministic visual-change evidence for silent/video-first workflows.

The analyzer deliberately uses a small grayscale raster and simple byte math.
It has no perception-model dependency and makes no claim about whether motion
is editorially important; it only reports what changed between sampled frames.
"""

from __future__ import annotations

import bisect
import json
import math
import os
import subprocess
from dataclasses import dataclass

from moviestar.ffmpeg import check_ffmpeg_available
from moviestar.timecodes import format_timecode


ACTIVITY_ANALYSIS_WIDTH = 160
ACTIVITY_PIXEL_DELTA = 16


@dataclass(frozen=True)
class _DecodeGroup:
    start_index: int
    end_index: int


def _number(value: float) -> str:
    return f"{float(value):.9f}".rstrip("0").rstrip(".") or "0"


def _scaled_height(source_width: int, source_height: int, width: int) -> int:
    height = max(2, round(source_height * width / source_width))
    return height if height % 2 == 0 else height + 1


def _groups(samples: list[dict], interval: float) -> list[_DecodeGroup]:
    """Group samples whose source clock advances continuously.

    A cut makes the source clock jump while result-time stays uniform. Each
    continuous run can be decoded in one FFmpeg process; the visual comparison
    across adjacent groups is still retained so an edit seam remains evidence.
    """
    if not samples:
        return []
    result: list[_DecodeGroup] = []
    start = 0
    for index in range(1, len(samples)):
        source_delta = samples[index]["source_seconds"] - samples[index - 1]["source_seconds"]
        result_delta = samples[index]["result_seconds"] - samples[index - 1]["result_seconds"]
        continuous = (
            source_delta > 0
            and math.isclose(source_delta, result_delta, abs_tol=0.001)
            and math.isclose(result_delta, interval, abs_tol=0.001)
        )
        if not continuous:
            result.append(_DecodeGroup(start, index))
            start = index
    result.append(_DecodeGroup(start, len(samples)))
    return result


def build_activity_timestamp_probe_command(
    video_path: str, from_seconds: float, to_seconds: float
) -> list[str]:
    """Build the bounded frame-timestamp probe used for VFR evidence."""
    return [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-read_intervals", f"{_number(from_seconds)}%{_number(to_seconds)}",
        "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,pkt_pts_time",
        "-of", "json",
        video_path,
    ]


def _probe_timestamps(
    video_path: str, from_seconds: float, to_seconds: float
) -> tuple[list[float], list[str]]:
    check_ffmpeg_available()
    command = build_activity_timestamp_probe_command(
        video_path, from_seconds, to_seconds
    )
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"ffprobe failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON: {exc}") from exc
    timestamps: list[float] = []
    for frame in payload.get("frames", []):
        raw = frame.get("best_effort_timestamp_time", frame.get("pkt_pts_time"))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value <= to_seconds + 0.001:
            timestamps.append(value)
    return sorted(set(timestamps)), command


def build_activity_decode_command(
    video_path: str,
    *,
    seek_seconds: float,
    sample_offset_seconds: float,
    interval: float,
    frame_count: int,
    width: int,
    height: int,
) -> list[str]:
    """Build a raw-grayscale decode that preserves held VFR frames.

    Seeking starts at the last decoded frame at/before the requested sample.
    ``fps`` then carries that frame across a VFR timestamp void. Starting the
    fps grid at ``sample_offset_seconds`` makes the first raw frame correspond
    exactly to the requested evidence clock rather than the preceding packet.
    """
    fps_filter = (
        f"fps=fps=1/{_number(interval)}:"
        f"start_time={_number(sample_offset_seconds)}:round=down"
    )
    return [
        "ffmpeg", "-v", "error",
        "-ss", _number(seek_seconds),
        "-i", video_path,
        "-vf", f"{fps_filter},scale={width}:{height}:flags=area,format=gray",
        "-frames:v", str(frame_count),
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]


def _decode_group(
    video_path: str,
    sample_times: list[float],
    timestamps: list[float],
    *,
    interval: float,
    width: int,
    height: int,
) -> tuple[list[bytes], list[str]]:
    first = sample_times[0]
    prior_index = bisect.bisect_right(timestamps, first + 0.000001) - 1
    seek = timestamps[prior_index] if prior_index >= 0 else first
    offset = max(0.0, first - seek)
    command = build_activity_decode_command(
        video_path,
        seek_seconds=seek,
        sample_offset_seconds=offset,
        interval=interval,
        frame_count=len(sample_times),
        width=width,
        height=height,
    )
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed: {detail or f'exit code {result.returncode}'}")
    frame_size = width * height
    expected = frame_size * len(sample_times)
    if len(result.stdout) != expected:
        raise RuntimeError(
            "ffmpeg decoded an unexpected number of activity frames: "
            f"expected {len(sample_times)}, got {len(result.stdout) // frame_size}"
        )
    return [
        result.stdout[offset:offset + frame_size]
        for offset in range(0, expected, frame_size)
    ], command


def _change(previous: bytes, current: bytes) -> dict:
    total_delta = 0
    changed = 0
    for before, after in zip(previous, current):
        delta = abs(before - after)
        total_delta += delta
        if delta >= ACTIVITY_PIXEL_DELTA:
            changed += 1
    pixel_count = len(current)
    return {
        "mean_absolute_luma_difference": round(
            total_delta / (255.0 * pixel_count), 6
        ),
        "changed_pixel_ratio": round(changed / pixel_count, 6),
    }


def _range_evidence(samples: list[dict], start: float, end: float) -> tuple[list[dict], list[dict]]:
    comparisons = [
        sample for sample in samples[1:]
        if sample["clock"]["result"]["seconds"] > start + 0.000001
        and sample["clock"]["result"]["seconds"] <= end + 0.000001
    ]
    active = [sample for sample in comparisons if sample["classification"] == "active"]
    return comparisons, active


def _active_ranges(
    samples: list[dict],
    *,
    threshold: float,
    merge_gap: float,
) -> list[dict]:
    windows: list[tuple[float, float]] = []
    for index, sample in enumerate(samples[1:], start=1):
        if sample["classification"] != "active":
            continue
        windows.append((
            samples[index - 1]["clock"]["result"]["seconds"],
            sample["clock"]["result"]["seconds"],
        ))
    merged: list[list[float]] = []
    for start, end in windows:
        if merged and start - merged[-1][1] <= merge_gap + 0.000001:
            merged[-1][1] = end
        else:
            merged.append([start, end])

    result: list[dict] = []
    for start, end in merged:
        comparisons, active = _range_evidence(samples, start, end)
        scores = [
            sample["visual_change"]["mean_absolute_luma_difference"]
            for sample in active
        ]
        active_seconds = sum(
            sample["clock"]["result"]["seconds"]
            - samples[sample["index"] - 1]["clock"]["result"]["seconds"]
            for sample in active
        )
        maximum = max(scores)
        result.append({
            "from": format_timecode(start),
            "to": format_timecode(end),
            "duration": format_timecode(end - start),
            "confidence": round(min(1.0, maximum / max(threshold * 4, 0.000001)), 3),
            "evidence": {
                "active_sample_count": len(active),
                "comparison_sample_count": len(comparisons),
                "sample_indexes": [sample["index"] for sample in active],
                "max_change": maximum,
                "mean_change": round(sum(scores) / len(scores), 6),
                "bridged_idle_seconds": round(max(0.0, end - start - active_seconds), 3),
            },
        })
    return result


def _idle_ranges(
    samples: list[dict], active_ranges: list[dict], *, from_s: float, to_s: float,
    threshold: float,
) -> list[dict]:
    spans: list[tuple[float, float]] = []
    cursor = from_s
    for active in active_ranges:
        active_from = active["from"]["seconds"]
        active_to = active["to"]["seconds"]
        if active_from > cursor + 0.000001:
            spans.append((cursor, active_from))
        cursor = max(cursor, active_to)
    if cursor < to_s - 0.000001:
        spans.append((cursor, to_s))

    result: list[dict] = []
    for start, end in spans:
        comparisons, active = _range_evidence(samples, start, end)
        scores = [
            sample["visual_change"]["mean_absolute_luma_difference"]
            for sample in comparisons
            if sample["visual_change"] is not None
            and sample["classification"] != "active"
        ]
        maximum = max(scores, default=0.0)
        result.append({
            "from": format_timecode(start),
            "to": format_timecode(end),
            "duration": format_timecode(end - start),
            "confidence": round(1.0 - min(1.0, maximum / max(threshold, 0.000001)), 3),
            "evidence": {
                "comparison_sample_count": len(comparisons),
                "active_sample_count": len(active),
                "max_change": maximum,
                "held_sample_count": sum(
                    sample["frame_state"] == "held" for sample in comparisons
                ),
            },
        })
    return result


def _no_new_frame_ranges(samples: list[dict]) -> list[dict]:
    windows: list[tuple[float, float, int]] = []
    for index, sample in enumerate(samples[1:], start=1):
        if sample["frame_state"] not in {"held", "unavailable"}:
            continue
        windows.append((
            samples[index - 1]["clock"]["result"]["seconds"],
            sample["clock"]["result"]["seconds"],
            sample["index"],
        ))
    merged: list[dict] = []
    for start, end, index in windows:
        if merged and math.isclose(start, merged[-1]["end"], abs_tol=0.001):
            merged[-1]["end"] = end
            merged[-1]["indexes"].append(index)
        else:
            merged.append({"start": start, "end": end, "indexes": [index]})
    return [
        {
            "from": format_timecode(item["start"]),
            "to": format_timecode(item["end"]),
            "duration": format_timecode(item["end"] - item["start"]),
            "evidence": {
                "held_sample_count": sum(
                    samples[index]["frame_state"] == "held"
                    for index in item["indexes"]
                ),
                "unavailable_sample_count": sum(
                    samples[index]["frame_state"] == "unavailable"
                    for index in item["indexes"]
                ),
                "sample_indexes": item["indexes"],
            },
        }
        for item in merged
    ]


def analyze_visual_activity(
    video_path: str,
    *,
    source_width: int,
    source_height: int,
    samples: list[dict],
    interval: float,
    threshold: float,
    merge_gap: float,
    range_from: float,
    range_to: float,
    analysis_width: int = ACTIVITY_ANALYSIS_WIDTH,
) -> dict:
    """Decode sample frames and return evidence/ranges in result-time order."""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"File not found: {video_path}")
    if not samples:
        raise ValueError("activity analysis requires at least one sample")

    analysis_height = _scaled_height(source_width, source_height, analysis_width)
    raw_frames: list[bytes | None] = [None] * len(samples)
    decoded_times: list[float | None] = [None] * len(samples)
    commands: list[dict] = []

    for group in _groups(samples, interval):
        group_samples = samples[group.start_index:group.end_index]
        source_times = [item["source_seconds"] for item in group_samples]
        probe_from = max(0.0, source_times[0])
        probe_to = source_times[-1] + interval + 0.001
        timestamps, probe_command = _probe_timestamps(
            video_path, probe_from, probe_to
        )
        frames, decode_command = _decode_group(
            video_path,
            source_times,
            timestamps,
            interval=interval,
            width=analysis_width,
            height=analysis_height,
        )
        for offset, (source_time, frame) in enumerate(zip(source_times, frames)):
            index = group.start_index + offset
            raw_frames[index] = frame
            timestamp_index = bisect.bisect_right(
                timestamps, source_time + 0.000001
            ) - 1
            if timestamp_index >= 0:
                decoded_times[index] = timestamps[timestamp_index]
        commands.append({
            "sample_indexes": list(range(group.start_index, group.end_index)),
            "ffprobe_command": probe_command,
            "ffmpeg_command": decode_command,
        })

    evidence_samples: list[dict] = []
    previous_frame: bytes | None = None
    previous_decoded_time: float | None = None
    for index, (plan, frame, decoded_time) in enumerate(
        zip(samples, raw_frames, decoded_times)
    ):
        visual_change = (
            None if previous_frame is None or frame is None
            else _change(previous_frame, frame)
        )
        if index == 0:
            classification = "baseline"
            frame_state = "baseline" if decoded_time is not None else "unavailable"
        else:
            score = (
                visual_change["mean_absolute_luma_difference"]
                if visual_change is not None else 0.0
            )
            classification = "active" if score >= threshold else "idle"
            if decoded_time is None:
                frame_state = "unavailable"
            elif (
                previous_decoded_time is not None
                and math.isclose(decoded_time, previous_decoded_time, abs_tol=0.000001)
            ):
                frame_state = "held"
            elif score == 0:
                frame_state = "decoded_unchanged"
            else:
                frame_state = "new"
        evidence_samples.append({
            "index": index,
            "clock": {
                "result": format_timecode(plan["result_seconds"]),
                "source": format_timecode(plan["source_seconds"]),
            },
            "decoded_frame": {
                "source_time": (
                    format_timecode(decoded_time)
                    if decoded_time is not None else None
                ),
                "newly_decoded_since_previous_sample": (
                    None if index == 0 else frame_state not in {"held", "unavailable"}
                ),
            },
            "frame_state": frame_state,
            "visual_change": visual_change,
            "classification": classification,
        })
        previous_frame = frame
        previous_decoded_time = decoded_time

    active_ranges = _active_ranges(
        evidence_samples, threshold=threshold, merge_gap=merge_gap
    )
    idle_ranges = _idle_ranges(
        evidence_samples,
        active_ranges,
        from_s=range_from,
        to_s=range_to,
        threshold=threshold,
    )
    no_new_frame_ranges = _no_new_frame_ranges(evidence_samples)
    held_count = sum(sample["frame_state"] == "held" for sample in evidence_samples)
    return {
        "analysis_dimensions": {"width": analysis_width, "height": analysis_height},
        "samples": evidence_samples,
        "active_ranges": active_ranges,
        "idle_ranges": idle_ranges,
        "no_new_frame_ranges": no_new_frame_ranges,
        "summary": {
            "sample_count": len(evidence_samples),
            "comparison_count": max(0, len(evidence_samples) - 1),
            "active_range_count": len(active_ranges),
            "idle_range_count": len(idle_ranges),
            "held_sample_count": held_count,
            "no_new_frame_range_count": len(no_new_frame_ranges),
            "active_duration_seconds": round(sum(
                item["duration"]["seconds"] for item in active_ranges
            ), 3),
            "idle_duration_seconds": round(sum(
                item["duration"]["seconds"] for item in idle_ranges
            ), 3),
        },
        "commands": commands,
    }
