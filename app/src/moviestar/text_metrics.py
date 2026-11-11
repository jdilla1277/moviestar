"""Font advances and outline ink bounds for overlay line fitting.

HarfBuzz reads the same resolved font file (face zero for collections) that
FFmpeg draws. Measurements use 26.6 pixel units and OpenType outlines, never
embedded bitmap strikes. They are not rasterized pixel bounds: renderer/font
engine versions, hinting, bidi processing, stroke, shadow, and rotation may
change the final painted pixels.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import uharfbuzz as hb


@lru_cache(maxsize=32)
def _font(path: str, mtime_ns: int, ctime_ns: int, size: int, font_size: int) -> hb.Font:
    # Stat fields invalidate a cached face when a registered font is replaced.
    face = hb.Face(Path(path).read_bytes(), 0)
    if not face.glyph_count or not any(
        tag in face.table_tags for tag in ('glyf', 'CFF ', 'CFF2')
    ):
        raise ValueError('font has no supported OpenType outlines')
    font = hb.Font(face)
    font.scale = (font_size * 64, font_size * 64)
    return font


class TextMeasurer:
    """One font/size with bounded candidate-string caching for greedy wrap."""

    def __init__(self, path: str, font_size: int):
        self.font_size = font_size
        self.reason = None
        try:
            stat = Path(path).stat()
            self.font = _font(path, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, font_size)
        except (OSError, ValueError) as error:
            # Existing bitmap-only fonts can still reach the renderer's own
            # actionable error. Never silently substitute another font.
            self.font = None
            self.reason = str(error)
        self.measure = lru_cache(maxsize=512)(self._measure)

    @property
    def method(self) -> str:
        return 'harfbuzz' if self.font is not None else 'character-estimate'

    def _measure(self, text: str) -> dict:
        if self.font is None:
            width = len(text) * self.font_size * 0.55
            return {'advance_width': width, 'ink_bounds': None}
        buffer = hb.Buffer()
        buffer.add_str(text)
        # Match drawtext's HarfBuzz run properties. FFmpeg may additionally
        # perform FriBidi preprocessing; that remains a documented estimate.
        buffer.direction = 'ltr'
        buffer.script = 'Latn'
        buffer.language = 'en'
        hb.shape(self.font, buffer)
        x = y = 0
        bounds = []
        for info, pos in zip(buffer.glyph_infos or [], buffer.glyph_positions or []):
            extents = self.font.get_glyph_extents(info.codepoint)
            if extents is not None and extents.width and extents.height:
                left = x + pos.x_offset + extents.x_bearing
                top = -(y + pos.y_offset + extents.y_bearing)
                bounds.append((left, top, left + extents.width, top - extents.height))
            x += pos.x_advance
            y += pos.y_advance
        if bounds:
            left = min(b[0] for b in bounds) / 64
            top = min(b[1] for b in bounds) / 64
            right = max(b[2] for b in bounds) / 64
            bottom = max(b[3] for b in bounds) / 64
        else:
            left = top = right = bottom = 0
        return {
            'advance_width': x / 64,
            'ink_bounds': {'x': left, 'y': top, 'width': right-left, 'height': bottom-top},
        }

    def summary(self, lines: list[str], content_width: float | None) -> dict:
        return {
            'method': self.method,
            'engine_version': hb.version_string() if self.font is not None else None,
            'estimated': self.font is None,
            'font_face_index': 0,
            'font_size': self.font_size,
            'content_width': content_width,
            'lines': [self.measure(line) for line in lines],
            'remaining_estimates': [
                'rasterization and renderer shaping differences',
                'vertical block placement, padding, stroke, shadow, and rotation',
            ],
            **({'fallback_reason': self.reason} if self.reason else {}),
        }
