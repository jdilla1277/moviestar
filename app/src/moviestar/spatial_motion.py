"""Deterministic visual motion on the canonical scene-result clock.

Tracks are independent, linearly interpolated, and hold their endpoint values.
Translation is in CSS pixels or percent of the target's untransformed box.
Scale uses the top-left origin; opacity multiplies the resolved base opacity.
"""

from math import isfinite
import re


class SpatialMotionError(ValueError):
    """A visual motion track cannot be sampled unambiguously."""


_TRANSLATION = re.compile(r'^([+-]?(?:\d+(?:\.\d*)?|\.\d+))(px|%)$')
_PROPERTIES = {'translate_x', 'translate_y', 'scale', 'opacity'}


def _number(value: object) -> bool:
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)
    except OverflowError:
        return False


def _value(value: object, property: str, path: str) -> tuple[float, str]:
    if property in {'translate_x', 'translate_y'}:
        if _number(value):
            return float(value), 'px'
        match = _TRANSLATION.fullmatch(value) if isinstance(value, str) else None
        if match and isfinite(float(match[1])):
            return float(match[1]), match[2]
        raise SpatialMotionError(f'{path}: use finite pixels (number or px) or a percentage')
    if not _number(value) or (property == 'scale' and value <= 0) or (property == 'opacity' and not 0 <= value <= 1):
        domain = 'positive scale' if property == 'scale' else 'opacity from 0 to 1'
        raise SpatialMotionError(f'{path}: expected finite {domain}')
    return float(value), ''


def validate_motion(motion: object, path: str) -> None:
    if not isinstance(motion, dict) or set(motion) - _PROPERTIES:
        raise SpatialMotionError(f'{path}: expected translate_x, translate_y, scale, or opacity tracks')
    for property, frames in motion.items():
        where = f'{path}.{property}'
        if not isinstance(frames, list) or not frames:
            raise SpatialMotionError(f'{where}: expected one or more keyframes')
        previous, unit = -1, None
        for index, frame in enumerate(frames):
            here = f'{where}[{index}]'
            if not isinstance(frame, dict) or set(frame) != {'at', 'value'}:
                raise SpatialMotionError(f'{here}: expected only at (scene seconds) and value')
            at = frame['at']
            if not _number(at) or at < 0 or at <= previous:
                raise SpatialMotionError(f'{here}.at: use finite non-negative, strictly increasing scene seconds')
            _, current_unit = _value(frame['value'], property, f'{here}.value')
            if unit is not None and current_unit != unit:
                raise SpatialMotionError(f'{where}: every keyframe must use the same unit')
            previous, unit = at, current_unit


def sample_motion(motion: dict, scene_time_s: float, path: str) -> dict:
    validate_motion(motion, path)
    if not _number(scene_time_s) or scene_time_s < 0:
        raise SpatialMotionError(f'{path}: motion needs a finite non-negative scene_time_s')
    result = {'scene_time_s': scene_time_s, 'translate_x': '0px', 'translate_y': '0px',
              'scale': 1, 'opacity': 1}
    for property, frames in motion.items():
        left = right = frames[0]
        for frame in frames:
            right = frame
            if frame['at'] >= scene_time_s:
                break
            left = frame
        a, unit = _value(left['value'], property, path)
        b, _ = _value(right['value'], property, path)
        fraction = (max(0, min(1, (scene_time_s - left['at']) / (right['at'] - left['at'])))
                    if right['at'] > left['at'] else 0)
        # Weighted endpoints avoid overflow from subtracting opposite signed values.
        value = a * (1 - fraction) + b * fraction
        result[property] = f'{value:.12g}{unit}' if unit else value
    return result
