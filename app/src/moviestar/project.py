"""Project workspace — the moviestar/ directory.

A project workspace lives in the current working directory. It contains
a project.json file (source references, metadata, per-source transcripts
when they exist) and a frames/ directory of extracted thumbnails.

Source videos are NEVER copied into the workspace. project.json stores
absolute path pointers to the originals. The workspace also holds the edit
specification, generated frame index, and transcripts when requested.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from moviestar.ffmpeg import extract_frames, run_ffprobe
from moviestar.timecodes import format_timecode
from moviestar.transcribe import (
    DEFAULT_MODEL,
    TranscriptionError,
    transcribe_file,
)


def _log(msg: str, *, quiet: bool) -> None:
    """Print a progress line to stderr unless quiet."""
    if not quiet:
        print(msg, file=sys.stderr, flush=True)


MOVIESTAR_DIR = "moviestar"
PROJECT_FILE = "project.json"
FRAMES_DIR = "frames"
# Index thumbnail width. 320 distinguishes scenes; screen recordings
# with UI text want more (issue #327) — `load --thumb-width` overrides.
DEFAULT_THUMB_WIDTH = 320
TRANSCRIPTS_DIR = "transcripts"
INSPECT_FRAMES_DIR = "inspect-frames"
SCHEMA_VERSION = "0.1"

# Omitted ``load --interval`` values target the same compact visual overview
# as storyboard, but never exceed the historical 5-second default. Keeping
# the ladder here lets every source in a multi-source load select from its own
# probed duration while explicit intervals remain exact.
LOAD_TARGET_FRAMES = 15
LOAD_MAX_DEFAULT_INTERVAL_SECONDS = 5.0
LOAD_MIN_DEFAULT_INTERVAL_SECONDS = 0.001
_LOAD_NICE_INTERVALS = (0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0)

# Issue #33: stable enum for source.transcription_skipped_reason.
# Agents switch on these values rather than the legacy prose
# strings ("--no-transcribe", "no audio track"). Add new values
# here when new skip reasons appear; do NOT introduce free-form
# strings at write sites.
TRANSCRIPTION_SKIP_REASON_FLAG = "flag"  # user passed --no-transcribe
TRANSCRIPTION_SKIP_REASON_NO_AUDIO = "no_audio"  # source has no audio stream
TRANSCRIPTION_SKIP_REASON_MODEL_UNAVAILABLE = "model_unavailable"  # reserved
TRANSCRIPTION_SKIP_REASONS: frozenset[str] = frozenset({
    TRANSCRIPTION_SKIP_REASON_FLAG,
    TRANSCRIPTION_SKIP_REASON_NO_AUDIO,
    TRANSCRIPTION_SKIP_REASON_MODEL_UNAVAILABLE,
})

# Issue #153: stable enum for source.frame_extraction_skipped_reason.
# Mirrors transcription_skipped_reason — agents switch on enum values,
# not prose. Only one reason exists today; add values here when new
# skip reasons appear.
FRAME_EXTRACTION_SKIP_REASON_FLAG = "flag"  # user passed --no-frames
FRAME_EXTRACTION_SKIP_REASONS: frozenset[str] = frozenset({
    FRAME_EXTRACTION_SKIP_REASON_FLAG,
})

# Legacy values from earlier versions, mapped to canonical enum
# values at the read boundary so old project.json files render the
# new shape without a schema migration.
_LEGACY_TRANSCRIPTION_SKIP_REASONS: dict[str, str] = {
    "--no-transcribe": TRANSCRIPTION_SKIP_REASON_FLAG,
    "no audio track": TRANSCRIPTION_SKIP_REASON_NO_AUDIO,
}

_EXPLICIT_WORKSPACE_ROOT: Path | None = None


def set_explicit_workspace_root(path: Path | str | None) -> None:
    """Pin project-scoped commands to an explicit project root.

    ``path`` is the directory that contains ``moviestar/``. This is
    intentionally scoped by the CLI wrapper around a single command
    invocation; library callers should keep passing ``cwd`` directly.
    """
    global _EXPLICIT_WORKSPACE_ROOT
    _EXPLICIT_WORKSPACE_ROOT = Path(path).expanduser().resolve() if path else None


def get_explicit_workspace_root() -> Path | None:
    """Return the project root supplied by ``--workspace``, if any."""
    return _EXPLICIT_WORKSPACE_ROOT


def normalize_skip_reason(value: str | None) -> str | None:
    """Map a stored ``transcription_skipped_reason`` to the canonical enum.

    Pass-through for ``None`` and for already-canonical values; rewrites
    legacy prose strings (``"--no-transcribe"``, ``"no audio track"``)
    to the matching enum value. Use at every read boundary that surfaces
    the field to a user — load output, status, skim, inspect — so an
    older project.json doesn't leak the legacy shape into responses.
    """
    if value is None:
        return None
    return _LEGACY_TRANSCRIPTION_SKIP_REASONS.get(value, value)


def select_load_frame_interval(
    duration_seconds: float,
    requested_interval: float | None,
) -> float:
    """Return the concrete interval stored for one loaded source.

    Explicit values are returned unchanged. When the option was omitted,
    choose the round interval whose predicted frame count is closest to the
    ~15-frame overview target, preferring the coarser interval on ties. The
    historical 5-second default is the ceiling, so long sources do not become
    more expensive. Sources shorter than the finest round interval are sampled
    at half their duration, bounded to a positive millisecond.
    """
    if requested_interval is not None:
        return requested_interval

    best: tuple[int, float] | None = None
    for nice in _LOAD_NICE_INTERVALS:
        if nice >= duration_seconds:
            continue
        count = math.ceil(round(duration_seconds / nice, 9))
        score = abs(count - LOAD_TARGET_FRAMES)
        if best is None or score <= best[0]:
            best = (score, nice)
    if best is not None:
        return min(best[1], LOAD_MAX_DEFAULT_INTERVAL_SECONDS)

    return round(
        max(duration_seconds / 2, LOAD_MIN_DEFAULT_INTERVAL_SECONDS),
        3,
    )


def find_project_dir(start: Path | None = None) -> Path | None:
    """Walk up the directory tree to find the nearest ``moviestar/``
    workspace (the one containing a ``project.json``). Mirrors how
    ``git`` finds ``.git`` from anywhere inside a repo.

    Returns the absolute path to the ``moviestar/`` directory if
    found at or above ``start``, else ``None``. ``start`` defaults
    to ``Path.cwd()``. Issue #41.
    """
    if start is None and _EXPLICIT_WORKSPACE_ROOT is not None:
        ms = _EXPLICIT_WORKSPACE_ROOT / MOVIESTAR_DIR
        if (ms / PROJECT_FILE).is_file():
            return ms
        return None

    base = Path(start).resolve() if start else Path.cwd().resolve()
    for candidate in [base, *base.parents]:
        ms = candidate / MOVIESTAR_DIR
        if (ms / PROJECT_FILE).is_file():
            return ms
    return None


def get_project_dir(cwd: Path | None = None, *, walk: bool = True) -> Path:
    """Return the workspace ``moviestar/`` path for the current scope.

    With ``walk=True`` (default), walks up from ``cwd`` to find an
    existing workspace (issue #41) — read commands (skim, inspect,
    trim, undo, spec, export, watch, screenshot, status) use this so
    they work from any subdir of a project, like ``git`` does.

    With ``walk=False``, returns ``cwd / moviestar`` regardless of
    any ancestor workspace. Use this for *destructive* /
    *create-in-cwd* operations (``load``, ``reset_workspace``) so
    running them from a subdir cannot reach up and clobber a parent
    project.

    When no ancestor workspace exists, both modes return
    ``cwd / moviestar`` — i.e. the path where ``load`` would write
    a fresh project.
    """
    if walk:
        found = find_project_dir(cwd)
        if found is not None:
            return found
    if cwd is None and _EXPLICIT_WORKSPACE_ROOT is not None:
        base = _EXPLICIT_WORKSPACE_ROOT
    else:
        base = Path(cwd) if cwd else Path.cwd()
    return base / MOVIESTAR_DIR


def is_loaded(cwd: Path | None = None, *, walk: bool = True) -> bool:
    """True if a project workspace exists at or above ``cwd``.

    Defaults to walk-up (issue #41). Pass ``walk=False`` to check
    only ``cwd`` (used by ``load`` so an ancestor workspace doesn't
    block creating a nested workspace in cwd).
    """
    if walk:
        return find_project_dir(cwd) is not None
    return (get_project_dir(cwd, walk=False) / PROJECT_FILE).is_file()


def load_project(cwd: Path | None = None, *, walk: bool = True) -> dict:
    """Read project.json from the resolved workspace and return it."""
    return json.loads(
        (get_project_dir(cwd, walk=walk) / PROJECT_FILE).read_text()
    )


def save_project(data: dict, cwd: Path | None = None, *, walk: bool = False) -> None:
    """Write project.json (workspace directory must exist).

    Defaults to ``walk=False`` — writes are cwd-scoped by intent, so
    a subdir-of-project ``load`` cannot accidentally clobber a
    parent project's project.json (issue #41). Pass ``walk=True``
    only when you genuinely want to write into a discovered
    ancestor workspace.
    """
    (get_project_dir(cwd, walk=walk) / PROJECT_FILE).write_text(
        json.dumps(data, indent=2)
    )


def reset_workspace(cwd: Path | None = None) -> None:
    """Remove the ``moviestar/`` directory IN ``cwd``. Never walks up.

    Destructive operation — always cwd-only so that ``load --force``
    from a subdir cannot wipe a parent project's workspace
    (issue #41). No-op if cwd has no workspace.
    """
    ms_dir = get_project_dir(cwd, walk=False)
    if ms_dir.exists():
        shutil.rmtree(ms_dir)


def save_transcript(
    transcript: dict,
    source_id: str,
    cwd: Path | None = None,
    *,
    walk: bool = False,
) -> Path:
    """Write transcripts/{source_id}.json inside the workspace.

    Creates the transcripts/ directory if needed. Returns the absolute
    path. Defaults to ``walk=False`` (cwd-only) — used by
    ``create_workspace`` which has already pinned ``ms_dir`` to cwd
    so accidentally walking up there would be wrong.

    Pass ``walk=True`` when writing a transcript into an *existing*
    discovered project from any subdir (e.g. ``moviestar
    retranscribe`` from a subdir of the project, issue #42).
    """
    transcripts_dir = get_project_dir(cwd, walk=walk) / TRANSCRIPTS_DIR
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out = transcripts_dir / f"{source_id}.json"
    out.write_text(json.dumps(transcript, indent=2))
    return out.resolve()


def list_frames(source: dict, cwd: Path | None = None) -> list[dict]:
    """Return the pre-built frame index for a source, sorted by timecode.

    Each entry: {"path": <absolute>, "timecode": {"text": ..., "seconds": ...}}.
    Frame N (1-indexed by ffmpeg's %04d pattern) corresponds to time
    (N - 1) * frame_interval. We enumerate the sorted files and derive
    timecodes — the filename → index parse is authoritative.
    """
    source_id = source["id"]
    interval = float(source["frame_interval"])
    frames_subdir = source.get("frames_dir", FRAMES_DIR)
    frames_dir = get_project_dir(cwd) / frames_subdir

    files = sorted(frames_dir.glob(f"{source_id}_*.jpg"))
    result: list[dict] = []
    for i, f in enumerate(files):
        seconds = i * interval
        result.append(
            {
                "path": str(f.resolve()),
                "timecode": format_timecode(seconds),
            }
        )
    return result


def filter_frames_by_range(
    frames: list[dict],
    from_seconds: float,
    to_seconds: float,
) -> list[dict]:
    """Return frames whose timecode falls within [from_seconds, to_seconds] inclusive."""
    return [
        f
        for f in frames
        if from_seconds <= f["timecode"]["seconds"] <= to_seconds
    ]


def subsample_frames(frames: list[dict], count: int) -> list[dict]:
    """Evenly-distribute subsample.

    When count >= 2 and the input has 2+ frames, the first and last frames
    of the input are always included. Note: "first" and "last" here mean
    the first and last INDEXED frames passed in — they won't necessarily
    align with any surrounding range boundary, since skim only has access
    to frames extracted at load-time by the fixed interval. If a range
    extends past the last indexed frame, the caller is responsible for
    noting the gap.

    - count <= 0 → []
    - count == 1 → [middle frame]
    - count >= len(frames) → all frames
    - otherwise → first + evenly-spaced intermediate + last, deduped
    """
    n = len(frames)
    if count <= 0:
        return []
    if n == 0:
        return []
    if count >= n:
        return list(frames)
    if count == 1:
        return [frames[n // 2]]

    # Evenly distribute indices from 0 to n-1, guaranteeing first and last.
    indices = sorted(
        {round(i * (n - 1) / (count - 1)) for i in range(count)}
    )
    return [frames[i] for i in indices]


def load_transcript(source: dict, cwd: Path | None = None) -> dict | None:
    """Read the transcript JSON file for a source.

    Returns None if the source has no transcript (e.g., silent video,
    --no-transcribe).
    """
    transcript_ref = source.get("transcript")
    if not transcript_ref:
        return None
    transcript_path = get_project_dir(cwd) / transcript_ref["path"]
    return json.loads(transcript_path.read_text())


def slice_transcript(
    transcript: dict,
    from_seconds: float,
    to_seconds: float,
) -> dict:
    """Return a shallow copy of transcript with words/segments filtered to the range.

    A word or segment is IN the range iff its start >= from_seconds AND
    its end <= to_seconds. Boundary-crossing words are excluded for
    simpler semantic.

    `text` is rebuilt from the sliced segments; all other top-level
    metadata (model, language, backend, source_id, source_path, duration)
    is preserved.
    """
    def _in_range(entry: dict) -> bool:
        start = entry["start"]["seconds"]
        end = entry["end"]["seconds"]
        return from_seconds <= start and end <= to_seconds

    sliced_words = [w for w in transcript.get("words", []) if _in_range(w)]
    sliced_segments = [s for s in transcript.get("segments", []) if _in_range(s)]

    result = dict(transcript)  # shallow copy
    result["words"] = sliced_words
    result["segments"] = sliced_segments
    # Rebuild text from the sliced words — segments from Whisper can span many
    # seconds, so filtering them strictly would often leave text empty even
    # when individual words in the range exist. Word-level reassembly is
    # more faithful to the range.
    result["text"] = " ".join(
        (w["text"] or "").strip() for w in sliced_words if w["text"]
    ).strip()
    return result


def _parse_fps(rate_str: str | None) -> float | None:
    if not rate_str or rate_str == "0/0":
        return None
    try:
        return float(Fraction(rate_str))
    except (ValueError, ZeroDivisionError):
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


import re


_FILENAME_TOKEN_RE = re.compile(r"^([a-zA-Z0-9]+)")


def derive_source_id(path: str, taken: set[str] | None = None) -> str:
    """Derive a stable source ID from a file path's basename.

    Strategy:
      1. Take the basename's first token (alphanumerics before the
         first non-alphanumeric character — typically ``-``, ``_``,
         ``.``).
      2. Lowercase it.
      3. If empty/unparseable, fall back to ``src_N`` where N is
         the next free index in ``taken``.
      4. If the derived ID collides with one already in ``taken``,
         append ``_2``, ``_3``, etc. until unique.

    Examples:
      ``"/abs/holden-2026-5-1__11-41-19-CFR.mp4"`` → ``"holden"``
      ``"/abs/jdilla-2026-5-1__13-41-19-CFR.mp4"`` → ``"jdilla"``
      ``"/abs/riverside_screenshare_hd_synced-...mp4"`` → ``"riverside"``
      ``"/abs/_weird.mp4"`` → ``"src_0"`` (or next free src_N)
    """
    taken = taken or set()
    basename = os.path.basename(path)
    # Strip extension for clarity, though the regex would skip it anyway.
    stem, _ext = os.path.splitext(basename)
    match = _FILENAME_TOKEN_RE.match(stem)

    if match:
        candidate = match.group(1).lower()
    else:
        # Empty or non-alphanumeric leading char — fall back to src_N.
        n = 0
        while f"src_{n}" in taken:
            n += 1
        return f"src_{n}"

    if candidate not in taken:
        return candidate
    # Collision — append _2, _3, ...
    n = 2
    while f"{candidate}_{n}" in taken:
        n += 1
    return f"{candidate}_{n}"


def _build_source_entry(
    abs_source: str,
    source_id: str,
    interval: float | None,
    frames_dir: Path,
    transcribe: bool,
    model: str,
    cwd: Path | None,
    quiet: bool,
    frames: bool = True,
    vocabulary: list[str] | None = None,
    start_offset_seconds: float | None = None,
    allow_model_download: bool = True,
    thumb_width: int = DEFAULT_THUMB_WIDTH,
) -> dict:
    """Probe, extract frames, transcribe one source. Returns the source
    entry dict for project.json. Used by create_workspace.

    Same logic the existing create_workspace runs for its single source
    — factored out so the multi-source path can loop cleanly.
    """
    _log(f"  [{source_id}] Probing metadata...", quiet=quiet)
    probe = run_ffprobe(abs_source)
    fmt = probe.get("format", {})
    duration_seconds = float(fmt.get("duration", 0)) if fmt.get("duration") else 0.0
    interval = select_load_frame_interval(duration_seconds, interval)

    video_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "audio"),
        None,
    )

    if video_stream:
        w = video_stream.get("width", "?")
        h = video_stream.get("height", "?")
        _log(f"  [{source_id}] Probed: {w}x{h}, {duration_seconds:.1f}s", quiet=quiet)

    if frames:
        _log(f"  [{source_id}] Extracting frames (every {interval}s)...", quiet=quiet)
        frame_paths, _argv = extract_frames(
            abs_source, interval, frames_dir, source_id,
            scale_width=thumb_width,
        )
        _log(f"  [{source_id}] Extracted {len(frame_paths)} frames.", quiet=quiet)
    else:
        _log(f"  [{source_id}] Skipping frame extraction (--no-frames).", quiet=quiet)
        frame_paths = []

    entry: dict = {
        "id": source_id,
        "path": abs_source,
        "duration": format_timecode(duration_seconds),
        "width": video_stream.get("width") if video_stream else None,
        "height": video_stream.get("height") if video_stream else None,
        "fps": _parse_fps(video_stream.get("r_frame_rate")) if video_stream else None,
        "video_codec": video_stream.get("codec_name") if video_stream else None,
        "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
        "frame_interval": interval,
        "start_offset_seconds": start_offset_seconds,
        "frames_extracted": len(frame_paths),
        "frames_dir": FRAMES_DIR,
        "thumb_width": thumb_width,
    }

    if not frames:
        entry["frame_extraction_skipped_reason"] = FRAME_EXTRACTION_SKIP_REASON_FLAG

    if not transcribe:
        entry["transcript"] = None
        entry["transcription_skipped_reason"] = TRANSCRIPTION_SKIP_REASON_FLAG
    elif audio_stream is None:
        entry["transcript"] = None
        entry["transcription_skipped_reason"] = TRANSCRIPTION_SKIP_REASON_NO_AUDIO
    else:
        transcript = transcribe_file(
            abs_source,
            source_id,
            model=model,
            quiet=quiet,
            vocabulary=vocabulary,
            allow_download=allow_model_download,
        )
        save_transcript(transcript, source_id, cwd)
        entry["transcript"] = {
            "model": model,
            "source": f"whisper:{model}",
            "path": f"{TRANSCRIPTS_DIR}/{source_id}.json",
        }

    return entry


class WorkspaceConflictError(Exception):
    """Raised when create_workspace can't honor the request —
    e.g., explicit `--as` names collide, or `--add` is invoked against
    a directory that has no existing project."""


def create_workspace(
    source_paths: list[str],
    interval: float | None,
    *,
    names: list[str | None] | None = None,
    add: bool = False,
    cwd: Path | None = None,
    transcribe: bool = True,
    model: str = DEFAULT_MODEL,
    quiet: bool = False,
    frames: bool = True,
    vocabulary: list[str] | None = None,
    start_offsets: list[float | None] | None = None,
    allow_model_download: bool = True,
    thumb_width: int = DEFAULT_THUMB_WIDTH,
) -> dict:
    """Create or extend a multi-source moviestar/ workspace.

    For each path in ``source_paths``: probe, extract frames, transcribe
    (per the flags), build a source entry. With ``frames=False``
    (``--no-frames``, issue #153) frame extraction is skipped and each
    entry carries ``frame_extraction_skipped_reason: "flag"``. Source IDs are derived from
    the filename's first token (D4) unless an entry in ``names`` is
    non-None at the same index.

    With ``add=False`` (default): create a fresh project. Errors if a
    project already exists in cwd (the CLI handles --force separately
    by calling reset_workspace first).

    With ``add=True``: extend an existing project by appending the new
    sources to its ``sources`` list. Existing per-source operations
    and transcripts are preserved, along with project-level caption rules.
    Errors if no project exists in cwd.

    Returns the (possibly extended) project dict that was written.
    """
    if not source_paths:
        raise ValueError("source_paths must contain at least one path")
    if names is not None and len(names) > len(source_paths):
        raise WorkspaceConflictError(
            f"Got {len(names)} --as names but only {len(source_paths)} sources. "
            f"Each --as pairs with one positional path."
        )

    # Pad names to match source_paths length so zip works cleanly.
    padded_names = list(names) if names else []
    padded_names.extend([None] * (len(source_paths) - len(padded_names)))
    padded_offsets = list(start_offsets) if start_offsets else []
    if len(padded_offsets) > len(source_paths):
        raise WorkspaceConflictError(
            f"Got {len(padded_offsets)} --start-offset values but only "
            f"{len(source_paths)} sources."
        )
    padded_offsets.extend([None] * (len(source_paths) - len(padded_offsets)))

    ms_dir = get_project_dir(cwd, walk=False)
    frames_dir = ms_dir / FRAMES_DIR
    existing: dict = {}

    if add:
        if not (ms_dir / PROJECT_FILE).exists():
            raise WorkspaceConflictError(
                f"--add requires an existing project in {ms_dir}, but "
                f"none was found. Drop --add to create a fresh project."
            )
        existing = load_project(cwd, walk=False)
        sources: list[dict] = list(existing.get("sources", []))
    else:
        ms_dir.mkdir(parents=True, exist_ok=True)
        sources = []

    frames_dir.mkdir(parents=True, exist_ok=True)

    taken_ids: set[str] = {s["id"] for s in sources}

    # Validate explicit --as names up-front: must be unique among
    # themselves and not collide with existing sources.
    explicit_names = [n for n in padded_names if n is not None]
    if len(explicit_names) != len(set(explicit_names)):
        dups = [n for n in explicit_names if explicit_names.count(n) > 1]
        raise WorkspaceConflictError(
            f"Duplicate --as names: {sorted(set(dups))}. Each source needs "
            f"a unique name."
        )
    for n in explicit_names:
        if n in taken_ids:
            raise WorkspaceConflictError(
                f"--as name {n!r} collides with an existing source in "
                f"the project. Pick a different name."
            )

    new_sources: list[dict] = []
    for path, override_name, start_offset_seconds in zip(
        source_paths, padded_names, padded_offsets
    ):
        abs_source = os.path.realpath(path)
        # Derive IDs from the user-facing argument, not the resolved
        # target. Symlinks should keep the name the agent typed while
        # path/probing still use the real source file.
        source_id = override_name or derive_source_id(path, taken_ids)
        taken_ids.add(source_id)
        entry = _build_source_entry(
            abs_source=abs_source,
            source_id=source_id,
            interval=interval,
            frames_dir=frames_dir,
            transcribe=transcribe,
            model=model,
            cwd=cwd,
            quiet=quiet,
            frames=frames,
            vocabulary=vocabulary,
            start_offset_seconds=start_offset_seconds,
            thumb_width=thumb_width,
            allow_model_download=allow_model_download,
        )
        sources.append(entry)
        new_sources.append(entry)

    project: dict = {
        "version": SCHEMA_VERSION,
        "loaded_at": _utc_now_iso(),
        "sources": sources,
    }
    if "caption_rules" in existing:
        project["caption_rules"] = existing["caption_rules"]
    save_project(project, cwd, walk=False)
    return project
