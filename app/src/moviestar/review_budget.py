"""Bounded compression of completed review renders, with measured byte limits."""

from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import tempfile

from moviestar.ffmpeg import run_ffprobe
from moviestar.render_profiles import review_video_settings

MAX_RETRIES = 2
MIN_VIDEO_BITRATE = 300_000


def budget_receipt(max_bytes: int) -> dict:
    return {
        "max_bytes": max_bytes, "actual_bytes": None, "met": None,
        "max_retries": MAX_RETRIES, "minimum_video_bitrate_bps": MIN_VIDEO_BITRATE,
        "audio": "copy_final_encoded_audio", "resolution": "preserve",
        "attempts": [], "stop_reason": None,
        "quality_note": "Byte compliance does not verify visual quality or text readability.",
    }


def build_budget_commands(
    source: str, output: str, encoder: str, bitrate: int, pass_log: str,
) -> tuple[list[list[str]], dict]:
    """Retry the completed picture; never resize, truncate, or re-encode audio."""
    codec = review_video_settings(encoder)["codec"]
    settings = {
        "encoder": encoder, "codec": codec, "target_bitrate_bps": bitrate,
        "pixel_format": "yuv420p",
        "rate_control": "two_pass" if encoder == "x264" else "target_bitrate",
    }
    base = ["ffmpeg", "-y", "-v", "error", "-i", source, "-map", "0:v:0"]
    video = ["-c:v", codec, "-b:v", str(bitrate), "-pix_fmt", "yuv420p"]
    commands = []
    if encoder == "x264":
        settings["preset"] = "fast"
        video += ["-preset", "fast", "-passlogfile", pass_log]
        commands.append([*base, *video, "-pass", "1", "-an", "-f", "null", os.devnull])
        video += ["-pass", "2"]
    else:
        settings["profile"] = "high"
        video += ["-profile:v", "high"]
    commands.append([
        *base, "-map", "0:a?", *video, "-c:a", "copy",
        "-movflags", "+faststart", output,
    ])
    return commands, settings


def _run(command: list[str]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(f"{command[0]} failed: {completed.stderr.strip()[-3000:]}")
    return completed.stdout


def _media_info(path: str) -> tuple[dict, dict]:
    probe = run_ffprobe(path)
    video = next((s for s in probe["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("The review output has no measurable video stream.")
    duration = float(probe["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("The review output has no finite positive duration.")
    return probe, video


def fit_review_budget(output: str, encoder: str, max_bytes: int) -> tuple[dict, dict]:
    """Keep the smallest verified candidate; every retry reads the original render.

    The initial render has already received all audio mixing/normalization.
    Temporary candidates are siblings so replacing the output is atomic. The
    original remains intact until a complete candidate has passed verification.
    """
    receipt = budget_receipt(max_bytes)
    settings = review_video_settings(encoder)
    initial_bytes = Path(output).stat().st_size
    receipt["attempts"].append({
        "kind": "initial_review", "actual_bytes": initial_bytes,
        "video_encoding": settings, "selected": True,
    })
    receipt.update(actual_bytes=initial_bytes, met=initial_bytes <= max_bytes)
    if receipt["met"]:
        receipt["stop_reason"] = "already_fits"
        return receipt, settings

    probe, video = _media_info(output)
    duration = float(probe["format"]["duration"])
    # Sum actual final audio packet payload, including normalized audio. Reserve
    # container space separately; the estimate only plans retries, never proves
    # compliance. Final on-disk bytes are authoritative.
    packet_sizes = _run([
        "ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
        "packet=size", "-of", "csv=p=0", output,
    ])
    audio_bytes = sum(int(line.split(",")[0]) for line in packet_sizes.splitlines()
                      if line.split(",")[0].isdigit())
    reserve = max(4096, math.ceil(max_bytes * .03))
    receipt.update(audio_payload_bytes=audio_bytes, reserved_container_bytes=reserve)
    bitrate = math.floor((max_bytes - audio_bytes - reserve) * 8 / duration * .94)
    receipt["stop_reason"] = "retry_limit"
    best_bytes = initial_bytes
    with tempfile.TemporaryDirectory(prefix=".moviestar-budget-", dir=Path(output).parent) as tmp:
        best_path = None
        for retry in range(1, MAX_RETRIES + 1):
            if bitrate < MIN_VIDEO_BITRATE:
                receipt["stop_reason"] = "minimum_video_bitrate"
                break
            candidate = str(Path(tmp) / f"candidate-{retry}.mp4")
            commands, candidate_settings = build_budget_commands(
                output, candidate, encoder, bitrate, str(Path(tmp) / f"pass-{retry}"),
            )
            attempt = {"kind": "compression_retry", "video_encoding": candidate_settings,
                       "commands": commands, "actual_bytes": None, "selected": False}
            receipt["attempts"].append(attempt)
            try:
                for command in commands:
                    _run(command)
                candidate_probe, candidate_video = _media_info(candidate)
                if any(candidate_video[key] != video[key] for key in ("width", "height")):
                    raise ValueError("Compression changed the review dimensions.")
                if abs(float(candidate_probe["format"]["duration"]) - duration) > .1:
                    raise ValueError("Compression changed the review duration.")
                if int(candidate_video["nb_frames"]) != int(video["nb_frames"]):
                    raise ValueError("Compression changed the review frame count.")
                original_audio = [s for s in probe["streams"] if s.get("codec_type") == "audio"]
                candidate_audio = [s for s in candidate_probe["streams"] if s.get("codec_type") == "audio"]
                if len(original_audio) != len(candidate_audio):
                    raise ValueError("Compression changed the review audio streams.")
                actual = Path(candidate).stat().st_size
                attempt["actual_bytes"] = actual
            except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
                attempt["error"] = str(exc)
                receipt["stop_reason"] = "retry_failed"
                break
            if actual < best_bytes:
                best_path, best_bytes, settings = candidate, actual, candidate_settings
                for item in receipt["attempts"]:
                    item["selected"] = item is attempt
            if best_bytes <= max_bytes:
                receipt["stop_reason"] = "fits"
                break
            bitrate = math.floor(bitrate * min(.85, max_bytes / actual * .92))
        if best_path is not None:
            os.replace(best_path, output)
    receipt.update(actual_bytes=Path(output).stat().st_size)
    receipt["met"] = receipt["actual_bytes"] <= max_bytes
    return receipt, settings
