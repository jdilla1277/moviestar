"""Measure the supported spatial layout subset in local Chromium.

This stage consumes the temporal frame without changing its media clock. The
browser owns CSS layout and text measurement. This command uses media
placeholders; spatial_render fills the same scene geometry with asset pixels.
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

from moviestar.spatial_format import SpatialFormatError, matches_canvas, validate_format_rules


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
    "format_rules",
}
_UNSAFE_CSS = re.compile(r"url\s*\(|@import|expression\s*\(|javascript\s*:|[;{}]", re.I)
_LENGTH_PROPERTIES = {
    "grid-template-columns", "grid-template-rows", "grid-auto-columns", "grid-auto-rows",
    "gap", "row-gap", "column-gap", "padding", "margin", "width", "height",
    "min-width", "max-width", "min-height", "max-height", "top", "right", "bottom", "left",
    "font-size", "border-width", "border-radius",
}


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
        if isinstance(raw_value, (int, float)) and css_key in _LENGTH_PROPERTIES:
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


def _resolve_styles(base: dict, rules: list, local: dict, canvas: dict,
                    path: list[str], diagnostics: list[dict], *, root: bool = False) -> dict:
    """Cascade base, highest-priority matching rules, and occurrence edits."""
    where = 'scene' if root else '/'.join(path)
    allowed_layout = _ROOT if root else _LAYOUT
    style = _css(base.get('layout', {}), allowed_layout, f'{where}.layout')
    style.update(_css(base.get('style', {}), _STYLE, f'{where}.style'))
    sources = {key: {'layer': 'base'} for key in style}
    overrides = _css(local.get('layout', {}), allowed_layout, f'{where}.customizations.layout')
    overrides.update(_css(local.get('style', {}), _STYLE, f'{where}.customizations.style'))
    try:
        validate_format_rules(rules, f'{where}.format_rules')
    except SpatialFormatError as exc:
        raise SpatialLayoutError(str(exc)) from exc
    matched = []
    candidates: dict[str, list[tuple[int, str, str]]] = {}
    for rule in rules:
        if not matches_canvas(rule['when'], canvas):
            continue
        identity = rule['id']
        matched.append(identity)
        patch = _css(rule.get('layout', {}), allowed_layout, f'{where}.format_rules.{identity}.layout')
        patch.update(_css(rule.get('style', {}), _STYLE, f'{where}.format_rules.{identity}.style'))
        for key, value in patch.items():
            candidates.setdefault(key, []).append((rule.get('priority', 0), identity, value))
    for key, choices in sorted(candidates.items(), key=lambda item: max(choice[0] for choice in item[1])):
        if key in overrides:
            continue
        priority = max(choice[0] for choice in choices)
        winners = [choice for choice in choices if choice[0] == priority]
        identities = [choice[1] for choice in winners]
        if len({choice[2] for choice in winners}) > 1:
            diagnostics.append({'code': 'ambiguous_format_rule', 'severity': 'error', 'path': path,
                                'property': key, 'rule_ids': identities, 'priority': priority})
            sources[key] = {'layer': 'ambiguous', 'rule_ids': identities, 'priority': priority}
            # Inspection can still show draft base geometry; preview rejects it.
            continue
        # Reinsert declarations in cascade order. Updating a dict value in place
        # can let a base longhand override a later format shorthand in Chrome.
        style.pop(key, None)
        style[key] = winners[0][2]
        sources[key] = {'layer': 'format_rule', 'rule_ids': identities, 'priority': priority}
    for key, value in overrides.items():
        style.pop(key, None)
        style[key] = value
    sources.update({key: {'layer': 'local_customization'} for key in overrides})
    return {'style': style, 'rule_sources': sources, 'matched_format_rules': matched}


def prepare_layout_frame(frame: dict, canvas: dict) -> dict:
    """Translate a resolved temporal frame to a safe, explicit CSS layout tree."""
    if not isinstance(frame, dict) or not isinstance(frame.get("children"), list):
        raise SpatialLayoutError("frame.children: expected an expanded scene")
    size = _canvas(canvas)
    diagnostics = list(frame.get('diagnostics', []))
    root_properties = {key: value for key, value in frame.items() if key not in _FRAME_FIELDS}
    scene_layout = _resolve_styles({'layout': root_properties}, frame.get('format_rules', []), {},
                                    size, [frame.get('scene_id') or 'scene'], diagnostics, root=True)

    def prepare_node(node: dict) -> dict:
        if not isinstance(node, dict) or not isinstance(node.get("occurrence_path"), list):
            raise SpatialLayoutError("frame.children: expected identified nodes")
        path = node["occurrence_path"]
        where = "/".join(path)
        resolved = _resolve_styles(node.get('base_rules', node), node.get('format_rules', []),
                                   node.get('local_overrides', {}), size, path, diagnostics)
        kind = node.get("type", "component")
        content = node.get("content")
        text = content.get("text", "") if kind == "text" and isinstance(content, dict) else ""
        caption = node.get("caption") if kind == "caption" else None
        if kind == "caption":
            if not isinstance(caption, dict):
                raise SpatialLayoutError(f"{where}: resolve caption cues with model frame first")
            text = caption["text"]
        prepared = {"path": path, "kind": kind, "content": text, **resolved,
                    "children": [prepare_node(child) for child in node.get("children", [])]}
        if caption is not None:
            prepared["caption"] = caption
        return prepared

    return {"scene_id": frame.get("scene_id"), "canvas": size,
            "scene_time_s": frame.get("scene_time_s"),
            "result_time_s": frame.get("result_time_s"),
            "root_style": scene_layout['style'], "scene_layout": scene_layout,
            "nodes": [prepare_node(child) for child in frame["children"]],
            "diagnostics": diagnostics}


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
    node = shutil.which("node")
    if _chrome_path() is None or node is None:
        return False
    try:
        check = subprocess.run([node, "-p", "Boolean(globalThis.WebSocket) && "
                                "Number(process.versions.node.split('.')[0]) >= 22"],
                               text=True, capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return check.returncode == 0 and check.stdout.strip() == "true"


def measure_spatial_layout(frame: dict, canvas: dict, *, output_path: Path | None = None) -> dict:
    """Return browser-measured boxes and optionally write the labeled layout PNG."""
    prepared = prepare_layout_frame(frame, canvas)
    request = {**prepared, "output_path": str(output_path.resolve()) if output_path else None}
    measured = _run_browser(request)
    return {"view": "layout", "canvas": prepared["canvas"],
            "scene_layout": measured['scene_layout'],
            "boxes": measured["boxes"], "diagnostics": [*prepared["diagnostics"], *measured["diagnostics"]],
            "artifact": str(output_path.resolve()) if output_path else None}


def _run_browser(request: dict) -> dict:
    """Run the shared page used by measurement and rendered frame preparation."""
    chrome = _chrome_path()
    node = shutil.which("node")
    if chrome is None:
        raise SpatialLayoutError("Chrome or chrome-headless-shell is required; set MOVIESTAR_CHROME_PATH to its executable")
    if node is None:
        raise SpatialLayoutError("Node.js 22 or newer is required for browser layout measurement")
    script = files("moviestar").joinpath("spatial_browser.mjs")
    try:
        completed = subprocess.run([node, str(script), chrome], input=json.dumps(request),
                                   text=True, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SpatialLayoutError(f"browser layout failed: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown browser error"
        raise SpatialLayoutError(f"browser layout failed: {detail}")
    try:
        return json.loads(completed.stdout)
    except ValueError as exc:
        raise SpatialLayoutError("browser layout returned invalid JSON") from exc
