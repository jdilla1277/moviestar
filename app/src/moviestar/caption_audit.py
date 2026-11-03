"""Read-only visibility-state analysis for caption and timed-text tracks.

Timing is authoritative; geometry comes from the existing overlay planner and
remains an estimate. Findings request review rather than approve an edit.
"""

from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import re
import subprocess
import sys

from moviestar.overlays import plan_overlays, render_order_key
from moviestar.timecodes import format_timecode, parse_timecode


DEFAULT_MIN_DURATION = 0.25
_ENGLISH_CONNECTORS = frozenset({
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
    "nor", "of", "on", "or", "so", "the", "to", "with", "yet",
})
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def _timecode(seconds: float) -> dict:
    # The normal formatter rounds numeric seconds to milliseconds too. An
    # audit must keep close boundaries distinct and proof times on their grid.
    return {**format_timecode(seconds), "seconds": seconds}


def _range(start: float, end: float) -> dict:
    return {
        "from": _timecode(start),
        "to": _timecode(end),
        "duration": _timecode(end - start),
    }


def _connectors(language: str | None, words) -> list[str]:
    if language not in (None, "en"):
        raise ValueError("Supported connector language is 'en'; use explicit "
                         "connector words for another language.")
    selected = set(_ENGLISH_CONNECTORS if language == "en" else ())
    for word in words:
        word = word.strip().casefold()
        if not _WORD.fullmatch(word):
            raise ValueError("Each connector word must be one word; repeat the "
                             "option to supply several words.")
        selected.add(word)
    return sorted(selected)


def _bounds(components: list[dict]) -> dict | None:
    if not components:
        return None
    boxes = [component["estimated_bounds"] for component in components]
    left = min(box["x"] for box in boxes)
    top = min(box["y"] for box in boxes)
    return {
        "estimate": True, "x": left, "y": top,
        "width": max(box["x"] + box["width"] for box in boxes) - left,
        "height": max(box["y"] + box["height"] for box in boxes) - top,
    }


def _finding(code: str, message: str, *, overlay_ids, result_range, **extra) -> dict:
    return {
        "code": code, "severity": "review", "message": message,
        "overlay_ids": list(overlay_ids), "range": result_range, **extra,
    }


def audit_caption_states(
    records: list[dict],
    duration: float,
    *,
    canvas: tuple[int, int],
    tracks: tuple[str, ...] = (),
    combine_tracks: bool = False,
    min_duration: float = DEFAULT_MIN_DURATION,
    language: str | None = None,
    connector_words: tuple[str, ...] = (),
    available_tracks: tuple[str, ...] = (),
) -> dict:
    """Audit planner-ready records already resolved onto the result clock.

    Default: each caption track is its own treatment. Explicit track selection
    also accepts manual text; combining tracks is an explicit grouping choice.
    Empty gaps remain in each group's complete [0, duration) state sequence.
    """
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Audit duration must be finite and greater than zero.")
    if not math.isfinite(min_duration) or min_duration < 0:
        raise ValueError("min_duration must be finite and nonnegative.")
    connectors = _connectors(language, connector_words)
    available = {record["track"] for record in records} | set(available_tracks)
    selected = sorted(set(tracks) if tracks else {
        record["track"] for record in records if record.get("kind") == "caption"
    })
    missing = set(selected) - available
    if missing:
        raise ValueError(f"Unknown text track(s): {', '.join(sorted(missing))}. "
                         f"Available tracks: {', '.join(sorted(available)) or 'none'}.")
    if combine_tracks and (not tracks or len(selected) < 2):
        raise ValueError("Combining tracks requires at least two explicit tracks.")

    components, excluded, warnings = [], [], []
    seen = set()
    for record in sorted(records, key=render_order_key):
        if record["track"] not in selected:
            continue
        identity = record["id"]
        if identity in seen:
            raise ValueError(f"Duplicate overlay ID: {identity!r}.")
        seen.add(identity)
        start, end = (parse_timecode(record[key]) for key in ("from", "to"))
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError(f"{identity}: overlay range must be finite and nonempty.")
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            excluded.append({"id": identity, "reason": "outside_timeline"})
            continue
        # Planning one record at a time avoids the renderer's all-pairs
        # collision search: this audit enumerates timing and geometry changes.
        planned = plan_overlays([record], canvas, start, end)
        plan = planned["plans"][0]
        if plan["opacity"] <= 0:
            excluded.append({"id": identity, "reason": "zero_opacity"})
            continue
        if not plan["text"].strip():
            excluded.append({"id": identity, "reason": "empty_text"})
            continue
        component = {
            "id": identity, "track": plan["track"], "kind": plan["kind"],
            "text": plan["text"], "range": _range(start, end),
            "geometry_source": "overlay_planner_estimate",
            "estimated_bounds": plan["estimated_bounds"],
            "anchor": plan["anchor"], "font_size": plan["font_size"],
            "rotation_degrees": plan["rotate_deg"],
            "background": plan["box"],
        }
        components.append(component)
        for message in planned["warnings"]:
            warnings.append(_finding(
                "caption_geometry_estimate_warning", message,
                overlay_ids=[identity], result_range=component["range"],
            ))
        if end - start < min_duration - 1e-9:
            warnings.append(_finding(
                "caption_short_component",
                f"{identity} is authored for only {end - start:.3f}s; "
                "review whether this text has enough reading time.",
                overlay_ids=[identity], result_range=component["range"],
                threshold_seconds=min_duration,
            ))

    by_id = {component["id"]: component for component in components}
    order = {component["id"]: index for index, component in enumerate(components)}
    groups = [selected] if combine_tracks else [[track] for track in selected]
    states = []
    for group_tracks in groups:
        group = "+".join(group_tracks)
        events = defaultdict(lambda: {"start": [], "end": []})
        events[0.0]
        events[duration]
        for component in components:
            if component["track"] in group_tracks:
                events[component["range"]["from"]["seconds"]]["start"].append(component["id"])
                events[component["range"]["to"]["seconds"]]["end"].append(component["id"])
        boundaries = sorted(events)
        active = set()
        previous_visible = None
        for start, end in zip(boundaries, boundaries[1:]):
            active.difference_update(events[start]["end"])
            active.update(events[start]["start"])
            active_ids = sorted(active, key=order.__getitem__)
            current = [by_id[identity] for identity in active_ids]
            state = {
                "id": f"state_{len(states) + 1:05d}", "group": group,
                "tracks": group_tracks, "range": _range(start, end),
                "active_ids": active_ids,
                "text_components": [
                    {key: component[key] for key in ("id", "track", "text", "estimated_bounds")}
                    for component in current
                ],
                "estimated_bounds": _bounds(current),
                "geometry_change": None,
            }
            if current and previous_visible is not None:
                before, after = previous_visible["estimated_bounds"], state["estimated_bounds"]
                changed = [key for key in ("x", "y", "width", "height")
                           if before[key] != after[key]]
                if changed:
                    state["geometry_change"] = {
                        "previous_state": previous_visible["id"],
                        "geometry_source": "overlay_planner_estimate",
                        "changed_fields": changed, "before": before, "after": after,
                    }
            if current:
                previous_visible = state
            states.append(state)
            if current and end - start < min_duration - 1e-9:
                warnings.append(_finding(
                    "caption_short_state",
                    f"This combination of text lasts only {end - start:.3f}s.",
                    overlay_ids=active_ids, result_range=state["range"],
                    state_id=state["id"], threshold_seconds=min_duration,
                ))
            words = [word.casefold() for component in current
                     for word in _WORD.findall(component["text"])]
            if connectors and words and all(word in connectors for word in words):
                warnings.append(_finding(
                    "caption_connector_only_state",
                    "Only configured connector words appear in this treatment; "
                    "review the surrounding text and timing.",
                    overlay_ids=active_ids, result_range=state["range"],
                    state_id=state["id"], words=words,
                ))

    return {
        "status": "caption_audited", "writes_spec": False,
        "tracks": selected,
        "grouping": "combined_tracks" if combine_tracks else "per_track",
        "range": _range(0, duration),
        "canvas": {"width": canvas[0], "height": canvas[1]},
        "policy": {
            "min_duration_seconds": min_duration,
            "connector_language": language, "connector_words": connectors,
        },
        "summary": {
            "components": len(components), "states": len(states),
            "empty_states": sum(not state["active_ids"] for state in states),
            "geometry_changes": sum(state["geometry_change"] is not None for state in states),
        },
        "components": components, "states": states, "excluded": excluded,
        "coverage": {
            "clock": "result", "selected_tracks": "complete_resolved_intervals",
            "visibility_basis": "authored_intervals_and_overlay_opacity",
            "connector_check": "configured_word_list" if connectors else "not_requested",
            "geometry": "estimated", "visual_review": "not_performed",
            "contrast": "not_checked", "motion": "not_checked", "listening": "not_checked",
        },
        "warning_count": len(warnings), "warnings": warnings,
        "limitations": [
            "Checks authored text timing, not OCR, contrast, occlusion, or readability.",
            "Geometry is the existing planner's character-width estimate; rotated "
            "or italic glyph ink may extend beyond it.",
            "Changing text naturally changes estimated bounds; a geometry change "
            "does not by itself mean the design is wrong.",
            "Connector-word and duration findings are advisory and can describe intentional edits.",
        ],
    }


def plan_caption_proofs(report: dict, fps: float, max_frames: int = 12) -> dict:
    """Select real output-frame times; do not invent a frame in a tiny gap."""
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("Proof frame rate must be finite and positive.")
    if max_frames < 1 or max_frames > 100:
        raise ValueError("Proof frame limit must be between 1 and 100.")
    risky_states = {warning.get("state_id") for warning in report["warnings"]}
    risky_components = {identity for warning in report["warnings"]
                        for identity in warning.get("overlay_ids", [])}
    by_frame = {}
    for state in report["states"]:
        if not state["active_ids"]:
            continue
        start = state["range"]["from"]["seconds"]
        end = state["range"]["to"]["seconds"]
        first, last = math.floor(start * fps), math.floor(end * fps)
        while first / fps < start:
            first += 1
        while last / fps >= end:
            last -= 1
        if first > last:
            state["proof"] = {"status": "unavailable",
                              "reason": "no_output_frame_in_state"}
            continue
        frame = (first + last) // 2
        at = frame / fps
        state["proof"] = {"status": "not_rendered", "at": _timecode(at)}
        candidate = by_frame.setdefault(frame, {
            "at": _timecode(at), "state_ids": [], "risk": False,
        })
        candidate["state_ids"].append(state["id"])
        candidate["risk"] |= (state["id"] in risky_states
                              or bool(set(state["active_ids"]) & risky_components))
    candidates = list(by_frame.values())
    candidates.sort(key=lambda item: item["at"]["seconds"])
    if len(candidates) <= max_frames:
        selected = candidates
    else:
        # Keep endpoints and distribute findings, so a long list of early
        # warnings cannot spend the entire budget before the ending.
        risky = [candidate for candidate in candidates if candidate["risk"]]
        selected = ([risky[0] if risky else candidates[0]] if max_frames == 1
                    else [candidates[0], candidates[-1]])
        for pool in (risky, candidates):
            remaining = [candidate for candidate in pool if candidate not in selected]
            count = min(max_frames - len(selected), len(remaining))
            if count:
                selected.extend(remaining[min(len(remaining) - 1,
                                              math.floor((i + 0.5) * len(remaining) / count))]
                                for i in range(count))
        selected.sort(key=lambda item: item["at"]["seconds"])
    report["proofs"] = {
        "requested": False, "rendered": 0, "candidate_frames": len(candidates),
        "planned_frames": len(selected), "max_frames": max_frames,
        "output_fps": fps, "selection": "timeline_endpoints_and_distributed_findings",
        "frames": [], "samples": [
            {key: item[key] for key in ("at", "state_ids")} for item in selected
        ],
    }
    return report


def render_caption_proofs(report: dict, project_root: Path, output_dir: Path) -> dict:
    """Render explicit project screenshots for a supported overlay composition."""
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=False, exist_ok=False)
    frames = []
    for index, sample in enumerate(report["proofs"]["samples"]):
        at = sample["at"]["seconds"]
        output = output_dir / f"frame_{index + 1:04d}.jpg"
        command = [
            sys.executable, "-m", "moviestar", "screenshot", "--workspace", str(project_root),
            "--at", str(at), "--out", str(output),
        ]
        try:
            rendered = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Proof rendering timed out at {at}s.") from exc
        if rendered.returncode:
            try:
                detail = json.loads(rendered.stdout).get("error", "render failed")
            except (ValueError, AttributeError):
                detail = rendered.stderr[-500:] or "render failed"
            raise RuntimeError(f"Could not render proof at {at}s: {detail}")
        if not output.is_file() or not output.stat().st_size:
            raise RuntimeError(f"Proof rendering produced no image at {at}s.")
        frames.append({"kind": "frame", "out": str(output), **sample})
        for state in report["states"]:
            if state["id"] in sample["state_ids"]:
                state["proof"] = {"status": "rendered", "out": str(output), "at": sample["at"]}
    report["proofs"].update(requested=True, rendered=len(frames), frames=frames)
    report["coverage"]["proof_states"] = sum(len(frame["state_ids"]) for frame in frames)
    report["coverage"]["proof_note"] = "Rendered samples are evidence to inspect, not visual approval."
    report["writes_files"] = True
    report["out"] = str(output_dir / "report.json")
    Path(report["out"]).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report
