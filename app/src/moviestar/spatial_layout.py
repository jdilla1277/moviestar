"""Measure the supported spatial layout subset in local Chromium.

This stage consumes the temporal frame without changing its media clock. The
browser owns CSS layout and text measurement; video/image pixels are placeholders
until the compositing stage is connected.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from importlib.resources import files


class SpatialLayoutError(ValueError):
    """A visual tree cannot be measured with the supported layout subset."""


_LAYOUT = {
    "display": "display", "columns": "grid-template-columns", "rows": "grid-template-rows",
    "grid_template_columns": "grid-template-columns", "grid_template_rows": "grid-template-rows",
    "grid_template_areas": "grid-template-areas", "grid_auto_columns": "grid-auto-columns",
    "grid_auto_rows": "grid-auto-rows", "grid_auto_flow": "grid-auto-flow",
    "grid_area": "grid-area", "grid_column": "grid-column", "grid_row": "grid-row",
    "gap": "gap", "row_gap": "row-gap", "column_gap": "column-gap",
    "padding": "padding", "margin": "margin", "width": "width", "height": "height",
    "min_width": "min-width", "max_width": "max-width", "min_height": "min-height",
    "max_height": "max-height", "aspect_ratio": "aspect-ratio", "overflow": "overflow",
    "position": "position", "top": "top", "right": "right", "bottom": "bottom",
    "left": "left", "z_index": "z-index", "flex_direction": "flex-direction",
    "flex_wrap": "flex-wrap", "flex": "flex", "align_items": "align-items",
    "align_self": "align-self", "justify_content": "justify-content",
    "justify_items": "justify-items", "justify_self": "justify-self",
}
_STYLE = {
    "background_color": "background-color", "color": "color", "font_family": "font-family",
    "font_size": "font-size", "font_weight": "font-weight", "line_height": "line-height",
    "text_align": "text-align", "white_space": "white-space", "border_color": "border-color",
    "border_width": "border-width", "border_style": "border-style",
    "border_radius": "border-radius", "opacity": "opacity", "object_fit": "object-fit",
}
_ROOT = {key: value for key, value in {**_LAYOUT, **_STYLE}.items()
         if key not in {"width", "height", "min_width", "max_width", "min_height",
                        "max_height", "aspect_ratio", "position", "top", "right",
                        "bottom", "left"}}
_FRAME_FIELDS = {
    "scene_id", "children", "diagnostics", "result_time_s", "sample_time_s",
    "scene_time_s", "scene_duration_s", "scene_source_time_s",
    "scene_source_duration_s", "canvas", "pacing_segment_id",
}
_UNSAFE_CSS = re.compile(r"url\s*\(|@import|expression\s*\(|javascript\s*:|[;{}]", re.I)


def _css(properties: dict, allowed: dict[str, str], path: str) -> dict[str, str]:
    if not isinstance(properties, dict):
        raise SpatialLayoutError(f"{path}: expected an object")
    result = {}
    for raw_key, raw_value in properties.items():
        if not isinstance(raw_key, str):
            raise SpatialLayoutError(f"{path}: property names must be strings")
        key = raw_key.replace("-", "_")
        if key not in allowed:
            raise SpatialLayoutError(f"{path}.{raw_key}: unsupported CSS property")
        css_key = allowed[key]
        if css_key in result:
            raise SpatialLayoutError(f"{path}.{raw_key}: duplicate rule for {css_key}")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int, float)):
            raise SpatialLayoutError(f"{path}.{raw_key}: expected a CSS value")
        if isinstance(raw_value, (int, float)) and not math.isfinite(raw_value):
            raise SpatialLayoutError(f"{path}.{raw_key}: expected a finite CSS value")
        value = str(raw_value)
        if not value.strip() or _UNSAFE_CSS.search(value):
            raise SpatialLayoutError(f"{path}.{raw_key}: unsafe or empty CSS value")
        if isinstance(raw_value, (int, float)) and key not in {"opacity", "font_weight", "z_index", "flex", "line_height"}:
            value += "px"
        result[css_key] = value
    return result


def _canvas(value: dict) -> dict[str, int]:
    if not isinstance(value, dict):
        raise SpatialLayoutError("canvas: expected width and height")
    result = {}
    for dimension in ("width", "height"):
        size = value.get(dimension)
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 8192:
            raise SpatialLayoutError(f"canvas.{dimension}: expected an integer from 1 to 8192")
        result[dimension] = size
    return result


def prepare_layout_frame(frame: dict, canvas: dict) -> dict:
    """Translate a resolved temporal frame to a safe, explicit CSS layout tree."""
    if not isinstance(frame, dict) or not isinstance(frame.get("children"), list):
        raise SpatialLayoutError("frame.children: expected an expanded scene")
    size = _canvas(canvas)
    root_properties = {key: value for key, value in frame.items() if key not in _FRAME_FIELDS}
    root_style = _css(root_properties, _ROOT, "scene.layout")

    def prepare_node(node: dict) -> dict:
        if not isinstance(node, dict) or not isinstance(node.get("occurrence_path"), list):
            raise SpatialLayoutError("frame.children: expected identified nodes")
        path = node["occurrence_path"]
        where = "/".join(path)
        style = _css(node.get("layout", {}), _LAYOUT, f"{where}.layout")
        style.update(_css(node.get("style", {}), _STYLE, f"{where}.style"))
        kind = node.get("type", "component")
        content = node.get("content")
        text = content.get("text", "") if kind == "text" and isinstance(content, dict) else ""
        return {"path": path, "kind": kind, "content": text, "style": style,
                "children": [prepare_node(child) for child in node.get("children", [])]}

    return {"scene_id": frame.get("scene_id"), "canvas": size,
            "scene_time_s": frame.get("scene_time_s"),
            "result_time_s": frame.get("result_time_s"),
            "root_style": root_style,
            "nodes": [prepare_node(child) for child in frame["children"]],
            "diagnostics": frame.get("diagnostics", [])}


def _chrome_path() -> str | None:
    configured = os.environ.get("MOVIESTAR_CHROME_PATH")
    if configured:
        return configured if os.path.isfile(configured) and os.access(configured, os.X_OK) else None
    for name in ("chrome-headless-shell", "chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    mac_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return mac_chrome if os.path.isfile(mac_chrome) else None


def browser_available() -> bool:
    """Whether local layout measurement prerequisites can be found."""
    return _chrome_path() is not None and shutil.which("node") is not None


def measure_spatial_layout(frame: dict, canvas: dict, *, output_path: Path | None = None) -> dict:
    """Return browser-measured boxes and optionally write the labeled layout PNG."""
    prepared = prepare_layout_frame(frame, canvas)
    chrome = _chrome_path()
    node = shutil.which("node")
    if chrome is None:
        raise SpatialLayoutError("Chrome or chrome-headless-shell is required; set MOVIESTAR_CHROME_PATH to its executable")
    if node is None:
        raise SpatialLayoutError("Node.js 22 or newer is required for browser layout measurement")
    script = files("moviestar").joinpath("spatial_browser.mjs")
    request = {**prepared, "output_path": str(output_path.resolve()) if output_path else None}
    try:
        completed = subprocess.run([node, str(script), chrome], input=json.dumps(request),
                                   text=True, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SpatialLayoutError(f"browser layout failed: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown browser error"
        raise SpatialLayoutError(f"browser layout failed: {detail}")
    try:
        measured = json.loads(completed.stdout)
    except ValueError as exc:
        raise SpatialLayoutError("browser layout returned invalid JSON") from exc
    return {"view": "layout", "canvas": prepared["canvas"],
            "boxes": measured["boxes"], "diagnostics": [*prepared["diagnostics"], *measured["diagnostics"]],
            "artifact": str(output_path.resolve()) if output_path else None}
