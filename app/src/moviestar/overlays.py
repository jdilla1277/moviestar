"""Overlay render planning.

Turns stored overlay records (spec.json shape: string timecodes,
``position`` dict, ``style.resolved``) into renderer-ready plans:
wrapped text lines, pixel anchors, resolved font files, ffmpeg color
strings, and timing local to a render window. The ffmpeg module
consumes plans to build drawtext filter chains; the CLI consumes the
same plans for envelope reporting (fonts, estimated bounds, warnings).

Estimation note: drawtext positions single-line text exactly via
``text_w``/``text_h`` expressions. Wrapping and bounding boxes use a
character-width heuristic (``CHAR_WIDTH_RATIO`` em per char) because
nothing in the stack measures real glyphs; estimates are labeled as
such in envelopes.
"""

from __future__ import annotations

import re

from .fonts import resolve_font
from .timecodes import format_timecode, parse_timecode

CHAR_WIDTH_RATIO = 0.55
DEFAULT_LINE_HEIGHT = 1.25

# ffmpeg's color parser accepts hex (0xRRGGBB) and its own name table,
# which covers the CSS names agents actually send. rgb()/rgba() forms
# are converted below.
_RGBA_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)",
    re.IGNORECASE,
)
_ROTATE_RE = re.compile(r"rotate\(\s*(-?[\d.]+)\s*(deg)?\s*\)", re.IGNORECASE)
_SCALE_RE = re.compile(r"scale\(\s*([\d.]+)\s*\)", re.IGNORECASE)
_SHADOW_RE = re.compile(
    r"^\s*(-?[\d.]+)px\s+(-?[\d.]+)px(?:\s+(-?[\d.]+)px)?\s+(.+?)\s*$"
)


def ffmpeg_color(value: str, alpha: float = 1.0) -> str:
    """Convert a CSS-ish color value to an ffmpeg color string with an
    explicit alpha component."""
    value = str(value).strip()
    match = _RGBA_RE.match(value)
    if match:
        r, g, b = (int(match.group(i)) for i in (1, 2, 3))
        if match.group(4) is not None:
            alpha *= float(match.group(4))
        value = f"0x{r:02x}{g:02x}{b:02x}"
    elif value.startswith("#"):
        hex_part = value[1:]
        if len(hex_part) == 3:
            hex_part = "".join(ch * 2 for ch in hex_part)
        if len(hex_part) == 8:
            alpha *= int(hex_part[6:8], 16) / 255.0
            hex_part = hex_part[:6]
        value = f"0x{hex_part.lower()}"
    alpha = min(max(alpha, 0.0), 1.0)
    return f"{value}@{round(alpha, 4)}"


def _px_value(value, canvas_extent: int, where: str) -> float:
    """Resolve a px/percent length against a canvas dimension."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.endswith("%"):
        return float(text[:-1]) / 100.0 * canvas_extent
    if text.endswith("px"):
        text = text[:-2]
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(
            f"{where}: could not parse length {value!r}; use px or %."
        ) from exc


def _parse_padding(value) -> tuple[int, int]:
    """CSS-style padding shorthand -> (vertical_px, horizontal_px)."""
    if value is None:
        return (0, 0)
    parts = str(value).replace("px", "").split()
    try:
        nums = [int(round(float(p))) for p in parts if p]
    except ValueError:
        return (0, 0)
    if not nums:
        return (0, 0)
    if len(nums) == 1:
        return (nums[0], nums[0])
    return (nums[0], nums[1])


def _parse_transform(transform: str | None) -> tuple[float, float]:
    """-> (rotate_degrees, scale_factor). Input was validated to only
    contain rotate()/scale() at authoring time."""
    if not transform:
        return (0.0, 1.0)
    rotate = 0.0
    scale = 1.0
    match = _ROTATE_RE.search(transform)
    if match:
        rotate = float(match.group(1))
    match = _SCALE_RE.search(transform)
    if match:
        scale = float(match.group(1))
    return (rotate, scale)


def _wrap_text(text: str, font_size: int, max_width_px: float | None) -> list[str]:
    """Greedy word wrap using the char-width heuristic. Manual line
    breaks in the text are always honored."""
    lines: list[str] = []
    char_w = font_size * CHAR_WIDTH_RATIO
    for raw_line in text.split("\n"):
        words = raw_line.split()
        if not words:
            lines.append("")
            continue
        if max_width_px is None or max_width_px <= 0:
            lines.append(" ".join(words))
            continue
        max_chars = max(1, int(max_width_px / char_w))
        current = words[0]
        for word in words[1:]:
            if len(current) + 1 + len(word) <= max_chars:
                current += " " + word
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _line_height_px(resolved: dict, font_size: int) -> int:
    value = resolved.get("line_height")
    if value is None:
        return int(round(font_size * DEFAULT_LINE_HEIGHT))
    text = str(value).strip()
    if text.endswith("px"):
        return int(round(float(text[:-2])))
    try:
        return int(round(float(text) * font_size))
    except ValueError:
        return int(round(font_size * DEFAULT_LINE_HEIGHT))


def _anchor_bounds(
    anchor: str,
    canvas: tuple[int, int],
    est_w: float,
    est_h: float,
    margin_x: int,
    margin_y: int,
    norm_x: float | None,
    norm_y: float | None,
) -> tuple[float, float]:
    """Top-left corner of the estimated text block for an anchor."""
    width, height = canvas
    if norm_x is not None and norm_y is not None:
        cx, cy = norm_x * width, norm_y * height
        if anchor == "center":
            return (cx - est_w / 2, cy - est_h / 2)
        x = cx - est_w / 2
        y = cy - est_h / 2
        if "left" in anchor:
            x = cx
        elif "right" in anchor:
            x = cx - est_w
        if "top" in anchor:
            y = cy
        elif "bottom" in anchor:
            y = cy - est_h
        return (x, y)
    x = (width - est_w) / 2
    y = (height - est_h) / 2
    if "left" in anchor:
        x = margin_x
    elif "right" in anchor:
        x = width - est_w - margin_x
    if "top" in anchor:
        y = margin_y
    elif "bottom" in anchor:
        y = height - est_h - margin_y
    return (x, y)


def _drawtext_xy(
    anchor: str,
    canvas: tuple[int, int],
    margin_x: int,
    margin_y: int,
    norm_x: float | None,
    norm_y: float | None,
    line_index: int,
    line_count: int,
    line_height: int,
    block_h: float,
    pad_y: int,
) -> tuple[str, str]:
    """Exact drawtext x/y expressions for one wrapped line. x uses
    text_w so horizontal placement is glyph-accurate; y distributes
    lines across the estimated block."""
    width, height = canvas
    if norm_x is not None and norm_y is not None:
        x_expr = f"{norm_x}*{width}-text_w/2"
        block_top = f"{norm_y}*{height}-{block_h / 2}"
        if "left" in anchor:
            x_expr = f"{norm_x}*{width}"
        elif "right" in anchor:
            x_expr = f"{norm_x}*{width}-text_w"
        if "top" in anchor:
            block_top = f"{norm_y}*{height}"
        elif "bottom" in anchor:
            block_top = f"{norm_y}*{height}-{block_h}"
    else:
        x_expr = "(w-text_w)/2"
        if "left" in anchor:
            x_expr = str(margin_x)
        elif "right" in anchor:
            x_expr = f"w-text_w-{margin_x}"
        block_top = f"(h-{block_h})/2"
        if "top" in anchor:
            block_top = str(margin_y)
        elif "bottom" in anchor:
            block_top = f"h-{block_h}-{margin_y}"
    line_offset = pad_y + line_index * line_height
    y_expr = f"{block_top}+{line_offset}"
    return (x_expr, y_expr)


def render_order_key(record: dict):
    return (record.get("z_index", 0), record["track"], record["id"])


def _anchor_point(
    anchor: str,
    canvas: tuple[int, int],
    margin_x: int,
    margin_y: int,
    norm_x: float | None,
    norm_y: float | None,
) -> tuple[float, float]:
    """The pixel the anchor pins to — the point libass \\pos targets."""
    width, height = canvas
    if norm_x is not None and norm_y is not None:
        return (norm_x * width, norm_y * height)
    x = width / 2
    y = height / 2
    if "left" in anchor:
        x = margin_x
    elif "right" in anchor:
        x = width - margin_x
    if "top" in anchor:
        y = margin_y
    elif "bottom" in anchor:
        y = height - margin_y
    return (x, y)


def _plan_highlight(
    record: dict,
    text: str,
    window_from: float,
    window_to: float,
    warnings: list[str],
) -> dict | None:
    """Build the spoken-word highlight plan for one overlay, or None
    when highlighting can't render (with a warning explaining why).

    Missing tokens are an error, not a warning: spoken-word highlighting
    without word timing fails with a fix, never
    silently renders unhighlighted.
    """
    oid = record["id"]
    tokens = record.get("tokens")
    if not tokens:
        raise ValueError(
            f"Overlay {oid!r} has highlight mode 'spoken-word' but no word "
            f"tokens. Regenerate captions from a word-timed transcript with "
            f"'moviestar captions generate --highlight spoken-word', or set "
            f"the overlay's highlight mode to 'none' via 'moviestar overlays "
            f"dump' -> 'moviestar overlays set'."
        )
    words = text.split()
    if len(words) != len(tokens):
        warnings.append(
            f"{oid}: text has {len(words)} word(s) but {len(tokens)} "
            f"token(s) — the text was likely edited without updating "
            f"tokens. Rendering without highlight; regenerate captions or "
            f"fix tokens via overlays dump/set."
        )
        return None
    color = record.get("highlight", {}).get("color") or "#ffe94a"
    token_plans = []
    for index, token in enumerate(tokens):
        t_from = parse_timecode(token["from"])
        t_to = parse_timecode(token["to"])
        if t_to <= t_from:
            # Zero-duration tokens (a whisper-tiny artifact) become
            # gaps: the word never highlights but the cue still renders.
            continue
        if t_to <= window_from or t_from >= window_to:
            continue
        token_plans.append(
            {
                "word_index": index,
                "from_local": round(max(t_from, window_from) - window_from, 6),
                "to_local": round(min(t_to, window_to) - window_from, 6),
            }
        )
    return {"mode": "spoken-word", "color": color, "tokens": token_plans}


def _estimated_bounds_intersection(first: dict, second: dict) -> dict | None:
    """Return the strict intersection of two estimated bounds.

    Edges that only touch do not overlap, matching the half-open timing
    semantics used by overlay windows.
    """
    left = max(first["x"], second["x"])
    top = max(first["y"], second["y"])
    right = min(
        first["x"] + first["width"],
        second["x"] + second["width"],
    )
    bottom = min(
        first["y"] + first["height"],
        second["y"] + second["height"],
    )
    if left >= right or top >= bottom:
        return None
    return {
        "estimate": True,
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
    }


def _find_overlay_collisions(
    plans: list[dict], window_from: float, window_to: float
) -> list[dict]:
    """Find pairs that overlap in both result-time and estimated bounds."""
    collisions: list[dict] = []
    for index, first in enumerate(plans):
        for second in plans[index + 1:]:
            overlap_from = max(
                first["from_result"], second["from_result"], window_from
            )
            overlap_to = min(
                first["to_result"], second["to_result"], window_to
            )
            if overlap_from >= overlap_to:
                continue
            intersection = _estimated_bounds_intersection(
                first["estimated_bounds"], second["estimated_bounds"]
            )
            if intersection is None:
                continue
            overlay_ids = [first["id"], second["id"]]
            from_tc = format_timecode(overlap_from)
            to_tc = format_timecode(overlap_to)
            message = (
                f"{overlay_ids[0]} + {overlay_ids[1]}: estimated bounds "
                f"overlap by {intersection['width']}x{intersection['height']} "
                f"at ({intersection['x']}, {intersection['y']}) while both "
                f"overlays are active from {from_tc['text']} to "
                f"{to_tc['text']}. Adjust timing or position if this "
                f"stacking is not intentional."
            )
            collisions.append(
                {
                    "overlay_ids": overlay_ids,
                    "from": from_tc,
                    "to": to_tc,
                    "estimated_intersection": intersection,
                    "message": message,
                }
            )
    return collisions


def plan_overlays(
    overlays: list[dict],
    canvas: tuple[int, int],
    window_from: float,
    window_to: float,
) -> dict:
    """Build render plans for the overlays active in a result-time
    window. Timing is half-open [from, to): an overlay ending exactly
    at ``window_from`` is not active.

    Returns ``{"plans", "fonts", "warnings", "collisions"}``. Raises
    :class:`moviestar.fonts.FontResolutionError` when a style requests
    an unavailable font family.
    """
    plans: list[dict] = []
    fonts: dict[str, dict] = {}
    warnings: list[str] = []
    width, height = canvas

    for record in sorted(overlays, key=render_order_key):
        o_from = parse_timecode(record["from"])
        o_to = parse_timecode(record["to"])
        if o_to <= window_from or o_from >= window_to:
            continue

        resolved = record.get("style", {}).get("resolved", {})
        oid = record["id"]

        font = resolve_font(
            resolved.get("font_family", "Inter"),
            resolved.get("font_weight", "normal"),
        )
        fonts.setdefault(f"{font['family']}:{font['weight']}", font)

        rotate_deg, scale = _parse_transform(resolved.get("transform"))
        font_size = int(round(resolved.get("font_size", 48) * scale))

        text = record["text"]
        transform_case = str(resolved.get("text_transform") or "none").lower()
        if transform_case == "uppercase":
            text = text.upper()
        elif transform_case == "lowercase":
            text = text.lower()

        pad_y, pad_x = _parse_padding(resolved.get("padding"))
        max_width_px: float | None = None
        if resolved.get("max_width") is not None:
            max_width_px = (
                _px_value(resolved["max_width"], width, f"{oid}: max-width")
                - 2 * pad_x
            )
        lines = _wrap_text(text, font_size, max_width_px)
        line_height = _line_height_px(resolved, font_size)

        char_w = font_size * CHAR_WIDTH_RATIO
        est_text_w = max((len(line) for line in lines), default=0) * char_w
        est_w = est_text_w + 2 * pad_x
        est_h = len(lines) * line_height + 2 * pad_y

        opacity = float(resolved.get("opacity", 1.0))
        font_color = ffmpeg_color(resolved.get("color", "#ffffff"), opacity)

        stroke = None
        if resolved.get("stroke_width"):
            stroke = {
                "width": int(resolved["stroke_width"]),
                "color": ffmpeg_color(
                    resolved.get("stroke_color", "#000000"), opacity
                ),
            }

        box = None
        if resolved.get("background_color") is not None:
            box_alpha = opacity * float(resolved.get("background_opacity", 1.0))
            box = {
                "color": ffmpeg_color(resolved["background_color"], box_alpha),
                "borderw": max(pad_x, pad_y),
            }
        if resolved.get("border_radius") is not None:
            warnings.append(
                f"{oid}: border-radius is accepted but boxes render with "
                f"square corners in this slice."
            )

        shadow = None
        if resolved.get("text_shadow") is not None:
            match = _SHADOW_RE.match(str(resolved["text_shadow"]))
            if match:
                shadow = {
                    "x": int(round(float(match.group(1)))),
                    "y": int(round(float(match.group(2)))),
                    "color": ffmpeg_color(match.group(4), opacity),
                }
                if match.group(3) and float(match.group(3)) > 0:
                    warnings.append(
                        f"{oid}: text-shadow blur radius is ignored; "
                        f"shadows render sharp."
                    )
            else:
                warnings.append(
                    f"{oid}: text-shadow {resolved['text_shadow']!r} was not "
                    f"understood; use 'Xpx Ypx color'. Shadow skipped."
                )
        if resolved.get("letter_spacing") is not None:
            warnings.append(
                f"{oid}: letter-spacing is not supported by the renderer "
                f"and was ignored."
            )

        position = record.get("position", {})
        anchor = position.get("anchor", "center")
        margin_x = int(position.get("margin_x", 0) or 0)
        margin_y = int(position.get("margin_y", 0) or 0)
        norm_x = position.get("x")
        norm_y = position.get("y")

        bounds_x, bounds_y = _anchor_bounds(
            anchor, canvas, est_w, est_h, margin_x, margin_y, norm_x, norm_y
        )
        estimated_bounds = {
            "estimate": True,
            "x": int(round(bounds_x)),
            "y": int(round(bounds_y)),
            "width": int(round(est_w)),
            "height": int(round(est_h)),
        }
        if (
            bounds_x < 0
            or bounds_y < 0
            or bounds_x + est_w > width
            or bounds_y + est_h > height
        ):
            warnings.append(
                f"{oid}: estimated text block "
                f"{estimated_bounds['width']}x{estimated_bounds['height']} at "
                f"({estimated_bounds['x']}, {estimated_bounds['y']}) may "
                f"overflow the {width}x{height} canvas. Shorten the text, "
                f"reduce font-size, or adjust max-width/position."
            )

        highlight_plan = None
        if record.get("highlight", {}).get("mode") == "spoken-word":
            highlight_plan = _plan_highlight(
                record, text, window_from, window_to, warnings
            )
        if highlight_plan is not None:
            if box is not None:
                box = None
                warnings.append(
                    f"{oid}: background boxes are not rendered on "
                    f"spoken-word-highlighted captions; remove "
                    f"background-color or use highlight mode 'none'."
                )
            if rotate_deg:
                rotate_deg = 0.0
                warnings.append(
                    f"{oid}: transforms are not rendered on spoken-word-"
                    f"highlighted captions; the rotation was ignored."
                )

        line_exprs = [
            _drawtext_xy(
                anchor,
                canvas,
                margin_x,
                margin_y,
                norm_x,
                norm_y,
                i,
                len(lines),
                line_height,
                est_h,
                pad_y,
            )
            for i in range(len(lines))
        ]

        plans.append(
            {
                "id": oid,
                "track": record["track"],
                "kind": record["kind"],
                "z_index": record.get("z_index", 0),
                "text": text,
                "lines": lines,
                "line_exprs": line_exprs,
                "font": font,
                "font_size": font_size,
                "font_color": font_color,
                "stroke": stroke,
                "box": box,
                "shadow": shadow,
                "line_height": line_height,
                "rotate_deg": rotate_deg,
                "opacity": opacity,
                "anchor": anchor,
                "estimated_bounds": estimated_bounds,
                "from_result": o_from,
                "to_result": o_to,
                "enable_from": round(max(o_from, window_from) - window_from, 6),
                "enable_to": round(min(o_to, window_to) - window_from, 6),
                "lane_center": (
                    bounds_x + est_w / 2,
                    bounds_y + est_h / 2,
                ),
                "anchor_point": _anchor_point(
                    anchor, canvas, margin_x, margin_y, norm_x, norm_y
                ),
                "highlight": highlight_plan,
                "resolved_style": resolved,
                **(
                    {"timing": record["resolved_timing"]}
                    if record.get("resolved_timing")
                    else {}
                ),
            }
        )

    collisions = _find_overlay_collisions(plans, window_from, window_to)
    warnings.extend(collision["message"] for collision in collisions)
    return {
        "plans": plans,
        "fonts": sorted(fonts.values(), key=lambda f: (f["family"], f["weight"])),
        "warnings": warnings,
        "collisions": collisions,
    }


def _timing_summary(timing: dict) -> dict:
    summary = {
        "space": timing["space"],
        "from": format_timecode(timing["from_s"]),
        "to": format_timecode(timing["to_s"]),
    }
    if timing["space"] == "scene":
        summary.update(
            {
                "scene": timing["scene"],
                "scene_name": timing["scene_name"],
                "visibility": timing["visibility"],
                "resolved_result_range": {
                    "from": format_timecode(timing["result_from_s"]),
                    "to": format_timecode(timing["result_to_s"]),
                },
            }
        )
    return summary


def overlay_plan_summary(plan: dict) -> dict:
    """Envelope-facing summary of one render plan."""
    return {
        "id": plan["id"],
        "track": plan["track"],
        "kind": plan["kind"],
        "z_index": plan["z_index"],
        "text": plan["text"],
        "lines": plan["lines"],
        "from": format_timecode(plan["from_result"]),
        "to": format_timecode(plan["to_result"]),
        "font": {
            "family": plan["font"]["family"],
            "weight": plan["font"]["weight"],
            "source": plan["font"]["source"],
        },
        "font_size": plan["font_size"],
        "anchor": plan["anchor"],
        "rotate_deg": plan["rotate_deg"],
        "opacity": plan["opacity"],
        "estimated_bounds": plan["estimated_bounds"],
        **(
            {"timing": _timing_summary(plan["timing"])}
            if plan.get("timing")
            else {}
        ),
        **(
            {
                "highlight": {
                    "mode": plan["highlight"]["mode"],
                    "color": plan["highlight"]["color"],
                    "rendered": True,
                    "tokens_count": len(plan["highlight"]["tokens"]),
                }
            }
            if plan.get("highlight")
            else {}
        ),
    }
