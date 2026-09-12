"""Timecode parsing and formatting.

Input formats accepted by `parse_timecode`:
  - "H:MM:SS.mmm" (e.g. "0:01:23.500")
  - "M:SS" shorthand (e.g. "1:23")
  - float seconds as a string (e.g. "83.5")

`format_timecode` always produces a verbose dict with "text" and "seconds".
If `fps` is supplied, it also includes "frame".
"""

from __future__ import annotations


# Suffix appended to format-error messages so the error itself teaches
# the accepted forms. Issue #26: every other error in the codebase
# teaches; the timecode error was the exception until now.
_TIMECODE_FORMATS_HINT = (
    "expected SS (e.g. 83.5), M:SS (e.g. 1:23), "
    "or HH:MM:SS.mmm (e.g. 0:01:23.500)"
)


def _format_error(value: object) -> ValueError:
    return ValueError(
        f"Invalid timecode: {value!r} ({_TIMECODE_FORMATS_HINT})"
    )


def parse_timecode(value: str) -> float:
    """Parse a timecode string into float seconds.

    Raises ValueError on invalid input. Format errors carry the
    accepted forms in the message; negative-value errors stay
    focused on the negativity.
    """
    if not isinstance(value, str) or not value:
        raise _format_error(value)

    if ":" in value:
        parts = value.split(":")
        if len(parts) == 3:
            h, m, s = parts
        elif len(parts) == 2:
            h, m, s = "0", parts[0], parts[1]
        else:
            raise _format_error(value)
        try:
            hours = int(h)
            minutes = int(m)
            seconds = float(s)
        except ValueError as exc:
            raise _format_error(value) from exc
        if hours < 0 or minutes < 0 or seconds < 0:
            raise ValueError(f"Invalid timecode (negative): {value!r}")
        return hours * 3600 + minutes * 60 + seconds

    try:
        result = float(value)
    except ValueError as exc:
        raise _format_error(value) from exc
    if result < 0:
        raise ValueError(f"Invalid timecode (negative): {value!r}")
    return result


def format_timecode(seconds: float, fps: float | None = None) -> dict:
    """Format float seconds into a verbose timecode dict.

    Returns {"text": "H:MM:SS.mmm", "seconds": float}
    If fps is provided, also includes "frame": int.

    Seconds is rounded to milliseconds (3 decimal places) to match the
    text field's precision. This keeps arithmetic like from_s + i * interval
    from leaking float-representation noise ("42.870000000000005") into
    output JSON and downstream artifacts like cache-dir names.
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - hours * 3600 - minutes * 60
    text = f"{hours}:{minutes:02d}:{secs:06.3f}"
    result: dict = {"text": text, "seconds": round(seconds, 3)}
    if fps is not None:
        result["frame"] = int(round(seconds * fps))
    return result
