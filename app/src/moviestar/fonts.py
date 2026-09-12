"""Font resolution for the overlay renderer.

MovieStar bundles Inter (SIL OFL 1.1 — see fonts/OFL-LICENSE.txt) so the
built-in style presets render identically on every machine. Any other
family resolves against system fonts; a family that can't be found is a
structured failure, never a silent substitution — exported video must
not quietly change its look between machines.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from moviestar.project import find_project_dir


class FontResolutionError(Exception):
    """Raised when a requested font family can't be resolved locally."""


DEFAULT_FONT_FAMILY = "Inter"

_BUNDLED_FILES = {
    "bold": "Inter-Bold.ttf",
    "normal": "Inter-Regular.ttf",
}

_FONT_EXTENSIONS = {".ttf", ".otf", ".ttc"}
_MANIFEST_FILE = "fonts.json"
_USER_FONT_DIR = Path.home() / ".moviestar" / "fonts"

# Searched in order; first match wins. Deterministic within a machine.
_SYSTEM_FONT_DIRS = [
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library" / "Fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path.home() / ".local" / "share" / "fonts",
]

_BOLD_WEIGHTS = {"bold", "bolder", "600", "700", "800", "900"}


def bundled_font_dir() -> Path:
    return Path(__file__).parent / "fonts"


def user_font_dir() -> Path:
    return _USER_FONT_DIR


def project_font_dir(project_dir: Path | None = None) -> Path | None:
    project_dir = project_dir or find_project_dir()
    if project_dir is None:
        return None
    return project_dir / "fonts"


def _normalize_family(family: str) -> str:
    return "".join(str(family).lower().split())


def normalize_weight(weight: object) -> str:
    """Collapse CSS-ish weights to the two faces the renderer supports."""
    if str(weight).strip().lower() in _BOLD_WEIGHTS:
        return "bold"
    return "normal"


def _manifest_path(font_dir: Path) -> Path:
    return font_dir / _MANIFEST_FILE


def _load_manifest(font_dir: Path) -> dict:
    path = _manifest_path(font_dir)
    if not path.is_file():
        return {"fonts": []}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"fonts": []}
    if not isinstance(data, dict) or not isinstance(data.get("fonts"), list):
        return {"fonts": []}
    return data


def _save_manifest(font_dir: Path, data: dict) -> None:
    font_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(font_dir).write_text(json.dumps(data, indent=2))


def _decode_name(platform_id: int, raw: bytes) -> str:
    if platform_id in (0, 3):
        return raw.decode("utf-16-be", errors="replace").strip("\x00").strip()
    return raw.decode("macroman", errors="replace").strip("\x00").strip()


def _font_offset(blob: bytes) -> int:
    if blob[:4] != b"ttcf":
        return 0
    if len(blob) < 16:
        raise ValueError("Invalid TrueType collection.")
    return struct.unpack(">I", blob[12:16])[0]


def _font_names(path: Path) -> dict[int, str]:
    blob = path.read_bytes()
    offset = _font_offset(blob)
    if len(blob) < offset + 12:
        raise ValueError("Invalid font file.")
    sfnt = blob[offset:offset + 4]
    if sfnt not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
        raise ValueError("Unsupported font file.")
    table_count = struct.unpack(">H", blob[offset + 4:offset + 6])[0]
    records_start = offset + 12
    name_offset = None
    name_length = None
    for i in range(table_count):
        pos = records_start + i * 16
        if len(blob) < pos + 16:
            break
        tag = blob[pos:pos + 4]
        if tag == b"name":
            name_offset, name_length = struct.unpack(">II", blob[pos + 8:pos + 16])
            break
    if name_offset is None or name_length is None:
        raise ValueError("Font has no name table.")
    table = blob[name_offset:name_offset + name_length]
    if len(table) < 6:
        raise ValueError("Invalid font name table.")
    _format, count, string_offset = struct.unpack(">HHH", table[:6])
    names: dict[int, str] = {}
    for i in range(count):
        pos = 6 + i * 12
        if len(table) < pos + 12:
            break
        platform_id, _encoding, _language, name_id, length, off = struct.unpack(
            ">HHHHHH", table[pos:pos + 12]
        )
        raw_start = string_offset + off
        raw = table[raw_start:raw_start + length]
        if not raw:
            continue
        text = _decode_name(platform_id, raw)
        if text and name_id not in names:
            names[name_id] = text
    return names


def font_metadata(path: str | Path) -> dict:
    path = Path(path)
    if path.suffix.lower() not in _FONT_EXTENSIONS:
        raise ValueError("Font files must be .ttf, .otf, or .ttc.")
    names = _font_names(path)
    family = names.get(16) or names.get(1) or path.stem
    subfamily = names.get(17) or names.get(2) or "normal"
    return {
        "family": family,
        "weight": normalize_weight(subfamily),
        "subfamily": subfamily,
    }


def _safe_font_filename(name: str, suffix: str) -> str:
    stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)
    stem = "_".join(part for part in stem.split("_") if part)
    return f"{stem or 'font'}{suffix.lower()}"


def _scope_font_dir(scope: str) -> Path:
    if scope == "user":
        return user_font_dir()
    if scope == "project":
        font_dir = project_font_dir()
        if font_dir is None:
            raise ValueError(
                "No project in this directory or any parent; use --scope user "
                "or run 'moviestar load <video>' first."
            )
        return font_dir
    raise ValueError("Font scope must be 'project' or 'user'.")


def default_font_scope() -> str:
    return "project" if find_project_dir() is not None else "user"


def add_font(path: str | Path, *, name: str | None = None, scope: str | None = None) -> dict:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Font file not found: {source}")
    metadata = font_metadata(source)
    display_name = (name or metadata["family"]).strip()
    if not display_name:
        raise ValueError("Font name must be non-empty.")
    selected_scope = scope or default_font_scope()
    font_dir = _scope_font_dir(selected_scope)
    font_dir.mkdir(parents=True, exist_ok=True)
    target = font_dir / _safe_font_filename(display_name, source.suffix)
    source_bytes = source.read_bytes()
    if target.exists() and target.read_bytes() != source_bytes:
        digest = source_bytes[:4096].hex()[:10]
        target = font_dir / _safe_font_filename(f"{display_name}_{digest}", source.suffix)
    if source != target.resolve():
        shutil.copy2(source, target)

    manifest = _load_manifest(font_dir)
    entry = {
        "family": display_name,
        "font_family": metadata["family"],
        "weight": metadata["weight"],
        "subfamily": metadata["subfamily"],
        "path": target.name,
        "source": selected_scope,
        "added_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    wanted = (_normalize_family(display_name), metadata["weight"])
    kept = [
        item
        for item in manifest["fonts"]
        if (_normalize_family(item.get("family", "")), item.get("weight")) != wanted
    ]
    kept.append(entry)
    manifest["fonts"] = sorted(
        kept, key=lambda item: (item.get("family", ""), item.get("weight", ""))
    )
    _save_manifest(font_dir, manifest)
    return _font_entry(font_dir, entry)


def _font_entry(font_dir: Path, item: dict) -> dict:
    path = font_dir / item["path"]
    return {
        "family": item.get("family") or item.get("font_family") or path.stem,
        "font_family": item.get("font_family") or item.get("family") or path.stem,
        "weight": normalize_weight(item.get("weight", "normal")),
        "path": str(path),
        "source": item.get("source", "project"),
        **({"subfamily": item["subfamily"]} if item.get("subfamily") else {}),
    }


def _managed_font_entries() -> list[dict]:
    entries: list[dict] = []
    dirs: list[tuple[str, Path | None]] = [
        ("project", project_font_dir()),
        ("user", user_font_dir()),
    ]
    for source, font_dir in dirs:
        if font_dir is None or not font_dir.is_dir():
            continue
        manifest = _load_manifest(font_dir)
        seen = set()
        for item in manifest["fonts"]:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            entry = _font_entry(font_dir, {**item, "source": item.get("source", source)})
            if Path(entry["path"]).is_file():
                entries.append(entry)
                seen.add(Path(entry["path"]).resolve())
        for path in sorted(font_dir.iterdir()):
            if path.suffix.lower() not in _FONT_EXTENSIONS:
                continue
            if path.resolve() in seen:
                continue
            try:
                metadata = font_metadata(path)
            except ValueError:
                continue
            entries.append(
                {
                    "family": metadata["family"],
                    "font_family": metadata["family"],
                    "weight": metadata["weight"],
                    "path": str(path),
                    "source": source,
                    "subfamily": metadata["subfamily"],
                }
            )
    return entries


def list_managed_fonts() -> list[dict]:
    bundled = [
        {
            "family": DEFAULT_FONT_FAMILY,
            "font_family": DEFAULT_FONT_FAMILY,
            "weight": weight,
            "path": str(bundled_font_dir() / filename),
            "source": "bundled",
        }
        for weight, filename in _BUNDLED_FILES.items()
    ]
    return sorted(
        bundled + _managed_font_entries(),
        key=lambda item: (item["source"], item["family"], item["weight"]),
    )


def _fc_match(family: str, weight: str) -> str | None:
    """Resolve via fontconfig when available. fc-match always answers
    with *some* fallback font, so the returned family must actually
    match the request before we trust the file path."""
    pattern = family if weight == "normal" else f"{family}:weight=bold"
    try:
        proc = subprocess.run(
            ["fc-match", "-f", "%{family}\t%{file}", pattern],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or "\t" not in proc.stdout:
        return None
    matched_family, file_path = proc.stdout.split("\t", 1)
    wanted = _normalize_family(family)
    families = {_normalize_family(f) for f in matched_family.split(",")}
    if wanted not in families:
        return None
    file_path = file_path.strip()
    return file_path or None


def _scan_font_dirs(family: str, weight: str, font_dirs: list[Path]) -> str | None:
    """Filename-based fallback scan for systems without fontconfig
    (stock macOS). Matches font files whose stem starts with the
    normalized family name, preferring the requested weight variant."""
    wanted = _normalize_family(family)
    exact: list[Path] = []
    weighted: list[Path] = []
    loose: list[Path] = []
    for font_dir in font_dirs:
        if not font_dir.is_dir():
            continue
        for path in sorted(font_dir.rglob("*")):
            if path.suffix.lower() not in _FONT_EXTENSIONS:
                continue
            stem = _normalize_family(path.stem)
            if not stem.startswith(wanted):
                continue
            variant = stem[len(wanted):].lstrip("-_")
            if weight == "bold":
                if variant == "bold":
                    weighted.append(path)
                elif "bold" in variant and "italic" not in variant:
                    loose.append(path)
                elif variant == "":
                    exact.append(path)
            else:
                if variant in ("", "regular"):
                    exact.append(path)
                elif "italic" not in variant and "bold" not in variant:
                    loose.append(path)
    for bucket in (weighted, exact, loose):
        if bucket:
            return str(bucket[0])
    return None


def resolve_font(family: str, weight: object = "normal") -> dict:
    """Resolve a font family + weight to a concrete font file.

    Returns ``{"family", "weight", "path", "source"}`` where source is
    ``bundled`` or ``system``. Raises :class:`FontResolutionError` when
    the family isn't available — the renderer never substitutes fonts
    silently.
    """
    face = normalize_weight(weight)
    if _normalize_family(family) == _normalize_family(DEFAULT_FONT_FAMILY):
        path = bundled_font_dir() / _BUNDLED_FILES[face]
        return {
            "family": DEFAULT_FONT_FAMILY,
            "font_family": DEFAULT_FONT_FAMILY,
            "ass_family": DEFAULT_FONT_FAMILY,
            "weight": face,
            "path": str(path),
            "source": "bundled",
        }

    wanted = _normalize_family(family)
    managed = [
        entry
        for entry in _managed_font_entries()
        if wanted
        in {
            _normalize_family(entry["family"]),
            _normalize_family(entry["font_family"]),
        }
    ]
    if managed:
        exact = [entry for entry in managed if entry["weight"] == face]
        entry = (exact or managed)[0]
        return {
            "family": entry["family"],
            "font_family": entry["font_family"],
            "ass_family": entry["font_family"],
            "weight": entry["weight"],
            "path": entry["path"],
            "source": entry["source"],
        }

    custom_dirs = [
        font_dir
        for font_dir in (project_font_dir(), user_font_dir())
        if font_dir is not None
    ]
    custom_found = _scan_font_dirs(family, face, custom_dirs)
    if custom_found is not None:
        source = "user"
        project_dir = project_font_dir()
        if project_dir is not None and Path(custom_found).is_relative_to(project_dir):
            source = "project"
        return {
            "family": family,
            "font_family": family,
            "ass_family": family,
            "weight": face,
            "path": custom_found,
            "source": source,
        }

    found = _fc_match(family, face) or _scan_font_dirs(family, face, _SYSTEM_FONT_DIRS)
    if found is not None:
        return {
            "family": family,
            "font_family": family,
            "ass_family": family,
            "weight": face,
            "path": found,
            "source": "system",
        }

    raise FontResolutionError(
        f"Font family {family!r} was not found on this system. MovieStar "
        f"bundles '{DEFAULT_FONT_FAMILY}' for deterministic rendering — "
        f"drop the font-family override, or set "
        f"\"font-family: {DEFAULT_FONT_FAMILY}\" in --css."
    )
