"""Artifact-bound, reviewer-reported evidence; never an automatic approval."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from moviestar.ffmpeg import run_ffprobe
from moviestar.fonts import user_font_dir

DIMENSIONS = ("technical", "source_alignment", "visual", "motion", "listening")
STATUSES = ("passed", "rejected", "unknown", "unavailable")
STATE_POLICY = (
    "Conservative project/spec content (excluding undo history), transcript and "
    "managed-font contents, and referenced-file size/mtime/ctime/inode. "
    "Media dependencies are stat-checked; artifact and evidence files are SHA-256 checked."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _stamp(path: Path) -> dict:
    try:
        stat = path.stat()
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                "ctime_ns": stat.st_ctime_ns, "inode": stat.st_ino, "device": stat.st_dev}
    except OSError:
        return {"available": False}


def file_identity(path: str | Path) -> dict:
    path = Path(path).expanduser().resolve()
    before = _stamp(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if _stamp(path) != before:
        raise ValueError(f"File changed while being read: {path}")
    return {"path": str(path), "size_bytes": before["size"], "sha256": digest.hexdigest()}


def _referenced_paths(value, workspace: Path):
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key in {"path", "file"} or key.endswith("_path")):
                if not item or "://" in item:
                    continue
                path = Path(item).expanduser()
                if not path.is_absolute():
                    local = workspace / path
                    path = local if local.exists() else workspace.parent / path
                yield path.resolve()
            else:
                yield from _referenced_paths(item, workspace)
    elif isinstance(value, list):
        for item in value:
            yield from _referenced_paths(item, workspace)


def project_state(workspace: str | Path) -> dict:
    """Conservative local invalidation, without rehashing hours of source media."""
    workspace = Path(workspace).resolve()
    project = json.loads((workspace / "project.json").read_text())
    spec_path = workspace / "spec.json"
    spec = json.loads(spec_path.read_text()) if spec_path.exists() else None
    if not isinstance(project, dict) or (spec is not None and not isinstance(spec, dict)):
        raise ValueError("Project and spec must be JSON objects.")
    if spec is not None:
        spec = {key: value for key, value in spec.items() if key != "revisions"}
    small_files = {}
    for directory in (workspace / "transcripts", workspace / "fonts", user_font_dir()):
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    small_files[str(path.resolve())] = file_identity(path)["sha256"]
    paths = set(_referenced_paths([project, spec], workspace))
    payload = {"project": project, "spec": spec, "local_contents": small_files,
               "referenced_files": {str(path): _stamp(path) for path in sorted(paths)}}
    return {"fingerprint": _digest(payload), "policy": STATE_POLICY}


def _atomic_write(path: Path, data: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".review-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_report(output: str, workspace: str | Path, state: dict, render_facts: dict) -> tuple[Path, dict]:
    identity = file_identity(output)
    probe = run_ffprobe(identity["path"])
    duration = float(probe["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Review artifact must have a finite positive duration.")
    if not any(s.get("codec_type") == "video" for s in probe["streams"]):
        raise ValueError("Review artifact has no video stream.")
    report = {
        "version": 1, "id": f"review_{uuid4().hex}", "created_at": _now(),
        "clock": "artifact_seconds",
        "artifact": {**identity, "duration_seconds": duration},
        "project": {"workspace": str(Path(workspace).resolve()), "state": state},
        "render_facts": render_facts, "entries": [],
        "provenance_note": "Review judgments are caller-reported, not authenticated or independently verified.",
    }
    directory = Path(workspace) / "reviews"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report['id']}.json"
    _atomic_write(path, report)
    return path.resolve(), report


def load_report(path: Path) -> dict:
    report = json.loads(path.read_text())
    if not isinstance(report, dict) or report.get("version") != 1:
        raise ValueError("Unsupported or malformed review report; expected version 1.")
    if report.get("clock") != "artifact_seconds" or not isinstance(report.get("entries"), list):
        raise ValueError("Malformed review report clock or entries.")
    duration = report["artifact"]["duration_seconds"]
    if not _finite_number(duration) or duration <= 0:
        raise ValueError("Malformed review artifact duration.")
    seen = {}
    for entry in report["entries"]:
        if entry["dimension"] not in DIMENSIONS or entry["status"] not in STATUSES:
            raise ValueError("Malformed review dimension or status.")
        identifier = entry["id"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("Review entry IDs must be nonempty and unique.")
        if any(not isinstance(entry[key], str) or not entry[key].strip() for key in ("actor", "method")):
            raise ValueError("Review entries need an actor and method.")
        if entry.get("provenance") != "reviewer_reported":
            raise ValueError("Review judgments must identify caller-reported provenance.")
        coverage = entry["coverage"]
        ranges, samples = coverage["ranges"], coverage["sample_times"]
        if not isinstance(ranges, list) or not isinstance(samples, list) or (ranges and samples):
            raise ValueError("Malformed review coverage; use ranges or sampled instants.")
        if not ranges and not samples and entry["status"] in {"passed", "rejected"}:
            raise ValueError("Passed/rejected entries require concrete coverage.")
        for interval in ranges:
            if (not isinstance(interval, list) or len(interval) != 2
                or not all(_finite_number(value) for value in interval)
                or not 0 <= interval[0] < interval[1] <= duration):
                raise ValueError("Malformed review range outside artifact duration.")
        if any(not _finite_number(value) or not 0 <= value < duration for value in samples):
            raise ValueError("Malformed review sample outside artifact duration.")
        if samples and entry["dimension"] in {"motion", "listening"}:
            raise ValueError("Still samples cannot establish motion/listening coverage.")
        supersedes = entry.get("supersedes", [])
        if not isinstance(supersedes, list):
            raise ValueError("Malformed supersession list.")
        for prior in supersedes:
            if prior not in seen or seen[prior] != entry["dimension"]:
                raise ValueError("Supersession must reference a prior entry in the same dimension.")
        seen[identifier] = entry["dimension"]
    return report


def _finite_number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def currency(report: dict) -> dict:
    reasons = []
    try:
        actual = file_identity(report["artifact"]["path"])
        artifact_current = actual["sha256"] == report["artifact"]["sha256"]
        if not artifact_current:
            reasons.append("Artifact bytes changed.")
    except (OSError, ValueError) as exc:
        artifact_current = False
        reasons.append(f"Artifact unavailable: {exc}")
    try:
        state = project_state(report["project"]["workspace"])
        project_current = state["fingerprint"] == report["project"]["state"]["fingerprint"]
        if not project_current:
            reasons.append("Project state or a tracked input changed.")
    except (OSError, ValueError) as exc:
        project_current = False
        reasons.append(f"Project state unavailable: {exc}")
    return {"current": artifact_current and project_current,
            "artifact_current": artifact_current, "project_current": project_current, "reasons": reasons}


def _evidence_current(entry: dict) -> bool:
    for expected in entry.get("evidence", []):
        try:
            if file_identity(expected["path"])["sha256"] != expected["sha256"]:
                return False
        except (OSError, ValueError):
            return False
    return True


def _coverage(entries: list[dict], duration: float) -> dict:
    ranges, samples = [], set()
    for entry in entries:
        if entry["status"] == "passed" and entry["evidence_current"]:
            ranges.extend(entry["coverage"]["ranges"])
            samples.update(entry["coverage"]["sample_times"])
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    gaps, cursor = [], 0.0
    for start, end in merged:
        if start > cursor:
            gaps.append([cursor, start])
        cursor = end
    if cursor < duration:
        gaps.append([cursor, duration])
    return {"ranges": merged, "sample_times": sorted(samples), "unreviewed_ranges": gaps,
            "full": not gaps, "note": "Sampled instants never establish continuous coverage."}


def view_report(report: dict) -> dict:
    result = copy.deepcopy(report)
    result["currency"] = currency(report)
    superseded = {identifier for entry in report["entries"] for identifier in entry.get("supersedes", [])}
    for entry in result["entries"]:
        entry["superseded"] = entry["id"] in superseded
        entry["evidence_current"] = _evidence_current(entry)
    dimensions = {}
    for dimension in DIMENSIONS:
        entries = [e for e in result["entries"] if e["dimension"] == dimension and not e["superseded"]]
        coverage = _coverage(entries, report["artifact"]["duration_seconds"])
        if not result["currency"]["current"]:
            status = "stale"
        elif not entries:
            status = "unreviewed"
        elif any(not e["evidence_current"] for e in entries):
            status = "unknown"
        elif any(e["status"] == "rejected" for e in entries):
            status = "rejected"
        elif any(e["status"] == "unknown" for e in entries):
            status = "unknown"
        elif all(e["status"] == "unavailable" for e in entries):
            status = "unavailable"
        elif any(e["status"] == "unavailable" for e in entries) or not coverage["full"]:
            status = "partial"
        else:
            status = "passed"
        dimensions[dimension] = {"status": status, "coverage": coverage,
                                 "active_entry_ids": [e["id"] for e in entries]}
    result["dimensions"] = dimensions
    next_steps = []
    if report.get("render_facts", {}).get("size_budget", {}).get("met") is False:
        next_steps.append("The byte limit was not met. Do not share through the size-limited route; choose a shorter clip or larger limit.")
    if not result["currency"]["current"]:
        next_steps.append("This report is stale. Render a new artifact with --review-report and review it again.")
    else:
        for dimension, summary in dimensions.items():
            status = summary["status"]
            if status == "passed":
                continue
            if status == "rejected":
                next_steps.append(f"Inspect rejected {dimension} evidence; revise or explicitly supersede after re-review.")
            elif dimension == "listening":
                next_steps.append("Listen to the artifact's unreviewed ranges; if listening is unavailable, ask the human to listen and record their judgment.")
            elif dimension == "motion":
                next_steps.append("Play unreviewed artifact ranges to assess motion; still images cannot establish motion review.")
            else:
                next_steps.append(f"Review {dimension} ({status}); record the actual method, coverage, and limitations, or ask a capable human reviewer.")
    result["next_steps"] = next_steps
    return result


def make_entry(
    report: dict, *, dimension: str, status: str, actor: str, method: str,
    full: bool = False, start: float | None = None, end: float | None = None,
    at: tuple[float, ...] = (), note: str = "", limitations: str = "",
    evidence: tuple[Path, ...] = (), supersedes: tuple[str, ...] = (),
) -> dict:
    if dimension not in DIMENSIONS or status not in STATUSES:
        raise ValueError("Unknown review dimension or status.")
    if not actor.strip() or not method.strip():
        raise ValueError("Actor and method must describe who reviewed and how.")
    duration = report["artifact"]["duration_seconds"]
    range_requested = start is not None or end is not None
    scopes = int(full) + int(range_requested) + int(bool(at))
    if scopes > 1 or (scopes == 0 and status in {"passed", "rejected"}):
        raise ValueError("Choose exactly one coverage form: --full, --from/--to, or repeated --at.")
    if status in {"unknown", "unavailable"} and not note.strip():
        raise ValueError("Unknown/unavailable evidence needs a --note explaining the limitation and next step.")
    ranges = []
    if full:
        ranges = [[0.0, duration]]
    elif range_requested:
        if start is None or end is None or not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < end <= duration:
            raise ValueError(f"Review range must satisfy 0 <= from < to <= {duration} artifact seconds.")
        ranges = [[start, end]]
    if at:
        if dimension in {"motion", "listening"}:
            raise ValueError("Motion/listening evidence requires a playback range or --full, not still instants.")
        if any(not math.isfinite(value) or not 0 <= value < duration for value in at):
            raise ValueError(f"Sample times must be finite and in [0, {duration}) artifact seconds.")
    by_id = {entry["id"]: entry for entry in report["entries"]}
    for identifier in supersedes:
        if identifier not in by_id or by_id[identifier]["dimension"] != dimension:
            raise ValueError("--supersedes must identify an existing entry in the same dimension.")
    return {
        "id": f"entry_{uuid4().hex}", "created_at": _now(), "dimension": dimension,
        "status": status, "actor": actor.strip(), "method": method.strip(),
        "provenance": "reviewer_reported", "note": note, "limitations": limitations,
        "coverage": {"ranges": ranges, "sample_times": sorted(set(at))},
        "evidence": [file_identity(path) for path in evidence], "supersedes": list(supersedes),
    }


@contextmanager
def _write_lock(path: Path):
    lock = path.with_suffix(path.suffix + ".lock")
    try:
        stream = lock.open("x")
    except FileExistsError as exc:
        raise ValueError("Report is locked by another writer. Retry later; after an interrupted write, remove the .lock only after confirming no writer is running.") from exc
    try:
        stream.close()
        yield
    finally:
        lock.unlink(missing_ok=True)


def record_entry(path: Path, *, dry_run: bool = False, **kwargs) -> tuple[dict, dict]:
    with nullcontext() if dry_run else _write_lock(path):
        report = load_report(path)
        if not currency(report)["current"]:
            raise ValueError("Review report is stale. Render again with --review-report; old evidence cannot approve a new revision.")
        entry = make_entry(report, **kwargs)
        if not dry_run:
            report["entries"].append(entry)
            _atomic_write(path, report)
        return entry, view_report(report)
