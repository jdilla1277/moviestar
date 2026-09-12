"""Slot shape masks drawn by FFmpeg alone.

A mask is a single-frame grayscale PNG that becomes a layout slot's
alpha channel: 255 keeps a pixel, 0 drops it, intermediate boundary
values blend the edge. Shapes are drawn with ``geq`` at 4x supersample
and downscaled with lanczos so edges antialias — no imaging library
enters the process, by design (masks only, never frames).

Every shape reduces to one signed-distance comparison. With half-extents
``A = w/2 - r`` and ``B = h/2 - r`` around the frame center, the distance

    D = hypot(max(abs(X-cx)-A, 0), max(abs(Y-cy)-B, 0))

is ``<= r`` exactly inside a rounded rectangle of corner radius ``r``; a
circle is the special case ``A = B = 0``. The ``core`` variant fills
``D <= r``; the ``ring`` variant fills ``r < D <= r + border`` on a frame
padded by the border width, so a border always sits outside the content
edge and never eats the face.

Masks are cached on disk keyed on their shape parameters and generated
once per distinct shape.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

SUPERSAMPLE = 4

_KINDS = ("circle", "rounded")
_VARIANTS = ("core", "ring")


class MaskRenderError(RuntimeError):
    """FFmpeg failed to generate a shape mask."""


def _validate(
    width: int,
    height: int,
    kind: str,
    radius: int,
    border_width: int,
    variant: str,
) -> None:
    if kind not in _KINDS:
        raise ValueError(f"unknown mask kind {kind!r}; expected one of {_KINDS}")
    if variant not in _VARIANTS:
        raise ValueError(
            f"unknown mask variant {variant!r}; expected one of {_VARIANTS}"
        )
    if width <= 0 or height <= 0:
        raise ValueError(f"mask dimensions must be positive, got {width}x{height}")
    if kind == "circle":
        if width != height:
            raise ValueError(
                f"circle mask requires square dimensions, got {width}x{height}"
            )
        if radius:
            raise ValueError(
                "circle mask radius is implied by its dimensions; "
                "pass radius only for kind='rounded'"
            )
    else:
        if radius < 0:
            raise ValueError(f"radius must be >= 0, got {radius}")
        if radius > min(width, height) / 2:
            raise ValueError(
                f"radius {radius} exceeds half the short side of {width}x{height}"
            )
    if variant == "ring" and border_width <= 0:
        raise ValueError("ring mask requires border_width > 0")
    if variant == "core" and border_width:
        raise ValueError("core mask does not take a border_width")


def mask_frame_size(
    *,
    width: int,
    height: int,
    kind: str,
    radius: int = 0,
    border_width: int = 0,
    variant: str = "core",
) -> tuple[int, int]:
    """Pixel dimensions of the generated mask frame.

    A ``core`` mask matches the content region; a ``ring`` mask is padded
    by the border width on every side.
    """
    _validate(width, height, kind, radius, border_width, variant)
    if variant == "ring":
        return (width + 2 * border_width, height + 2 * border_width)
    return (width, height)


def mask_filename(
    *,
    width: int,
    height: int,
    kind: str,
    radius: int = 0,
    border_width: int = 0,
    variant: str = "core",
) -> str:
    """Deterministic cache filename for one distinct shape."""
    _validate(width, height, kind, radius, border_width, variant)
    # v2: tail-snapped output (distro-swscale ghost fix) — cached v1
    # masks may carry 1-4 valued noise and must regenerate.
    key = (
        f"v2:s{SUPERSAMPLE}:{kind}:{variant}:"
        f"{width}x{height}:r{radius}:b{border_width}"
    )
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    return f"{kind}-{variant}-{width}x{height}-{digest}.png"


def _geq_expression(
    width: int,
    height: int,
    kind: str,
    radius: int,
    border_width: int,
    variant: str,
) -> str:
    scale = SUPERSAMPLE
    frame_w, frame_h = mask_frame_size(
        width=width, height=height, kind=kind,
        radius=radius, border_width=border_width, variant=variant,
    )
    cx = (frame_w * scale - 1) / 2
    cy = (frame_h * scale - 1) / 2
    if kind == "circle":
        # Inset the edge half an output pixel: an exactly inscribed
        # circle is tangent to the frame at the cardinal points, leaving
        # no room for the antialiasing ramp — the arc renders as a hard
        # flat spot there. Rounded rects keep exact radii; their straight
        # runs lie on the slot boundary by design, like a rect slot edge.
        content_r = (width * scale - scale) / 2
        a = b = 0.0
    else:
        content_r = radius * scale
        a = width * scale / 2 - content_r
        b = height * scale / 2 - content_r
    distance = f"hypot(max(abs(X-{cx})-{a},0),max(abs(Y-{cy})-{b},0))"
    if variant == "ring":
        outer_r = content_r + border_width * scale
        return f"255*(lte({distance},{outer_r})-lte({distance},{content_r}))"
    return f"255*lte({distance},{content_r})"


def build_mask_command(
    output_path: str,
    *,
    width: int,
    height: int,
    kind: str,
    radius: int = 0,
    border_width: int = 0,
    variant: str = "core",
) -> list[str]:
    """Build the FFmpeg argv that renders one shape mask PNG."""
    frame_w, frame_h = mask_frame_size(
        width=width, height=height, kind=kind,
        radius=radius, border_width=border_width, variant=variant,
    )
    expression = _geq_expression(
        width, height, kind, radius, border_width, variant
    )
    scale = SUPERSAMPLE
    graph = (
        f"format=gray,geq=lum='{expression}',"
        # in_range/out_range pin the scaler to full-range gray, and the
        # lut snaps the tails: distro swscale builds (Ubuntu's
        # 6.1.1-3ubuntu5) leave 1-4 valued ringing/dither noise in flat
        # black regions, which reads as a 1/255 alpha ghost outside
        # every mask. Values below 8 snap to 0 and above 247 to 255 -
        # the same bounds the mask tests use to define an antialiased
        # boundary - so flat regions are exact on every build while the
        # lanczos edge ramp is untouched.
        f"scale={frame_w}:{frame_h}:flags=lanczos:"
        f"in_range=full:out_range=full,"
        f"lutyuv=y='if(lt(val,8),0,if(gt(val,247),255,val))',"
        f"format=gray"
    )
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=black:s={frame_w * scale}x{frame_h * scale}:d=1",
        "-vf", graph,
        "-frames:v", "1",
        str(output_path),
    ]


def ensure_mask(
    masks_dir: Path | str,
    *,
    width: int,
    height: int,
    kind: str,
    radius: int = 0,
    border_width: int = 0,
    variant: str = "core",
) -> Path:
    """Return the cached mask for these parameters, generating it once."""
    masks_dir = Path(masks_dir)
    path = masks_dir / mask_filename(
        width=width, height=height, kind=kind,
        radius=radius, border_width=border_width, variant=variant,
    )
    if path.exists():
        return path
    masks_dir.mkdir(parents=True, exist_ok=True)
    # Generate to a temporary name so an interrupted render never leaves
    # a half-written file at the cache path.
    tmp = path.with_name(f".{path.stem}.tmp.png")
    command = build_mask_command(
        str(tmp),
        width=width, height=height, kind=kind,
        radius=radius, border_width=border_width, variant=variant,
    )
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise MaskRenderError(
            f"mask generation failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    os.replace(tmp, path)
    return path
