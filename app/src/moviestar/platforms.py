"""Versioned social-platform UI occlusion profiles and geometry linting.

The profiles are intentionally conservative reference masks, not pixel-perfect
copies of a particular app build.  They let Moviestar compare geometry it
already knows (timed overlays and composed layout slots) with the parts of a
vertical player commonly occupied by navigation, actions, descriptions, and
bottom chrome.  This module never inspects rendered pixels or runs OCR.
"""

from __future__ import annotations

from copy import deepcopy


PLATFORM_PROFILE_VERSION = "2026-10-01"
PLATFORM_CHOICES = ("tiktok", "instagram-reels", "youtube-shorts")

# Bounds are normalized to the output canvas.  Profiles use broad, stable UI
# families rather than pretending one phone/app state has universal pixels.
# The source URLs are surfaced in the command envelope so agents can distinguish
# Moviestar's versioned reference masks from a platform guarantee.
_PLATFORM_PROFILES = {
    "tiktok": {
        "label": "TikTok",
        "guidance_url": (
            "https://ads.tiktok.com/business/creativecenter/quicktok/online/"
            "tiktok_creative_accelerator/pc/en"
        ),
        "regions": (
            ("top_navigation", "top navigation", 0.0, 0.0, 1.0, 0.11),
            ("right_actions", "right actions", 0.78, 0.28, 0.22, 0.50),
            ("bottom_chrome", "caption and bottom chrome", 0.0, 0.76, 1.0, 0.24),
        ),
    },
    "instagram-reels": {
        "label": "Instagram Reels",
        "guidance_url": (
            "https://www.facebook.com/business/ads/facebook-instagram-reels-ads"
        ),
        "regions": (
            ("top_navigation", "top navigation", 0.0, 0.0, 1.0, 0.10),
            ("right_actions", "right actions", 0.80, 0.34, 0.20, 0.45),
            ("bottom_chrome", "description and bottom chrome", 0.0, 0.78, 1.0, 0.22),
        ),
    },
    "youtube-shorts": {
        "label": "YouTube Shorts",
        "guidance_url": "https://support.google.com/youtube/answer/16215842",
        "regions": (
            ("top_navigation", "top navigation", 0.0, 0.0, 1.0, 0.08),
            ("right_actions", "right actions", 0.79, 0.34, 0.21, 0.46),
            ("bottom_chrome", "description and bottom chrome", 0.0, 0.79, 1.0, 0.21),
        ),
    },
}


def _scale_rect(
    normalized: tuple[float, float, float, float],
    canvas: tuple[int, int],
) -> dict[str, int]:
    x, y, width, height = normalized
    canvas_width, canvas_height = canvas
    left = round(x * canvas_width)
    top = round(y * canvas_height)
    right = round((x + width) * canvas_width)
    bottom = round((y + height) * canvas_height)
    return {
        "x": left,
        "y": top,
        "width": max(1, right - left),
        "height": max(1, bottom - top),
    }


def resolve_platform_profile(
    platform: str, canvas: tuple[int, int]
) -> dict:
    """Resolve one normalized profile into concrete canvas pixels."""
    if platform not in _PLATFORM_PROFILES:
        choices = ", ".join(PLATFORM_CHOICES)
        raise ValueError(f"Unknown platform {platform!r}. Use one of: {choices}.")
    width, height = canvas
    if width <= 0 or height <= 0:
        raise ValueError("Platform preview canvas dimensions must be positive.")

    source = deepcopy(_PLATFORM_PROFILES[platform])
    regions = []
    for region_id, label, x, y, region_width, region_height in source["regions"]:
        regions.append(
            {
                "id": region_id,
                "label": label,
                "bounds": _scale_rect(
                    (x, y, region_width, region_height), (width, height)
                ),
            }
        )
    return {
        "platform": platform,
        "label": source["label"],
        "version": PLATFORM_PROFILE_VERSION,
        "expected_aspect_ratio": "9:16",
        "canvas": {"width": width, "height": height},
        "guidance_url": source["guidance_url"],
        "ui_regions": regions,
    }


def _intersection(first: dict, second: dict) -> dict | None:
    left = max(first["x"], second["x"])
    top = max(first["y"], second["y"])
    right = min(
        first["x"] + first["width"], second["x"] + second["width"]
    )
    bottom = min(
        first["y"] + first["height"], second["y"] + second["height"]
    )
    if left >= right or top >= bottom:
        return None
    return {
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
    }


def lint_platform_targets(profile: dict, targets: list[dict]) -> list[dict]:
    """Return deterministic target/UI intersections for one frame.

    Targets are known canvas-space rectangles with ``id``, ``type``, and
    ``bounds``.  One issue is returned per intersecting UI region so the agent
    can see which placement constraint it needs to clear.
    """
    issues = []
    for target in targets:
        bounds = target["bounds"]
        target_area = max(1, bounds["width"] * bounds["height"])
        for region in profile["ui_regions"]:
            intersection = _intersection(bounds, region["bounds"])
            if intersection is None:
                continue
            overlap_area = intersection["width"] * intersection["height"]
            overlap_percent = round(overlap_area / target_area * 100, 1)
            message = (
                f"{target['type'].replace('_', ' ').title()} "
                f"{target['id']!r} overlaps {profile['label']}'s "
                f"{region['label']} reference region by {overlap_percent:g}%. "
                "Move or resize it, then preview this frame again."
            )
            issues.append(
                {
                    "code": "platform_ui_occlusion",
                    "platform": profile["platform"],
                    "profile_version": profile["version"],
                    "target_id": target["id"],
                    "target_type": target["type"],
                    "target_bounds": dict(bounds),
                    "ui_region": region["id"],
                    "ui_region_bounds": dict(region["bounds"]),
                    "intersection": intersection,
                    "overlap_percent": overlap_percent,
                    "message": message,
                }
            )
    return issues


def canvas_matches_platform_aspect(profile: dict, tolerance: float = 0.02) -> bool:
    canvas = profile["canvas"]
    actual = canvas["width"] / canvas["height"]
    return abs(actual - 9 / 16) <= tolerance
