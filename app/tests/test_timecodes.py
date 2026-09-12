"""Tests for moviestar.timecodes."""

import pytest

from moviestar.timecodes import format_timecode, parse_timecode


class TestParseTimecode:
    def test_parse_full_format(self):
        assert parse_timecode("0:01:23.500") == pytest.approx(83.5)
        assert parse_timecode("1:00:00.000") == pytest.approx(3600.0)

    def test_parse_full_format_zero(self):
        assert parse_timecode("0:00:00.000") == pytest.approx(0.0)

    def test_parse_short_format(self):
        assert parse_timecode("1:23") == pytest.approx(83.0)
        assert parse_timecode("0:05") == pytest.approx(5.0)

    def test_parse_float_seconds(self):
        assert parse_timecode("83.5") == pytest.approx(83.5)
        assert parse_timecode("0") == pytest.approx(0.0)
        assert parse_timecode("3600") == pytest.approx(3600.0)

    def test_parse_integer_seconds(self):
        assert parse_timecode("5") == pytest.approx(5.0)

    def test_parse_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_timecode("abc")
        with pytest.raises(ValueError):
            parse_timecode("")
        with pytest.raises(ValueError):
            parse_timecode("-1:00")

    def test_format_error_teaches_accepted_forms(self):
        """Issue #26: every other error in the codebase teaches; the
        timecode error was the exception. Format errors now name the
        accepted forms so an agent can correct without reading docs."""
        with pytest.raises(ValueError) as exc_info:
            parse_timecode("abc")
        msg = str(exc_info.value)
        # Concrete examples of each format appear in the message.
        assert "83.5" in msg or "SS" in msg
        assert "1:23" in msg or "M:SS" in msg
        assert "0:01:23.500" in msg or "HH:MM:SS" in msg

    def test_format_error_only_one_invalid_timecode_prefix(self):
        """The CLI wraps timecode errors via _error_exit; downstream
        regression assertions count occurrences of 'invalid timecode'.
        Adding the formats hint must not introduce a second prefix."""
        with pytest.raises(ValueError) as exc_info:
            parse_timecode("abc")
        msg = str(exc_info.value).lower()
        assert msg.count("invalid timecode") == 1

    def test_negative_error_stays_focused(self):
        """The negative-value error stays about negativity — the
        formats hint would be misleading there."""
        with pytest.raises(ValueError) as exc_info:
            parse_timecode("-1:00")
        msg = str(exc_info.value)
        assert "negative" in msg.lower()
        # No formats hint on this branch — value is negative, not malformed.
        assert "83.5" not in msg


class TestFormatTimecode:
    def test_format_without_fps(self):
        result = format_timecode(83.5)
        assert result == {"text": "0:01:23.500", "seconds": 83.5}

    def test_format_with_fps(self):
        result = format_timecode(83.5, fps=30.0)
        assert result == {"text": "0:01:23.500", "seconds": 83.5, "frame": 2505}

    def test_format_zero(self):
        assert format_timecode(0.0) == {"text": "0:00:00.000", "seconds": 0.0}

    def test_format_one_hour(self):
        assert format_timecode(3600.0) == {"text": "1:00:00.000", "seconds": 3600.0}

    def test_format_includes_milliseconds(self):
        result = format_timecode(7261.123)
        assert result["text"] == "2:01:01.123"


class TestRoundtrip:
    @pytest.mark.parametrize("seconds", [0.0, 1.5, 83.5, 3600.0, 7261.123])
    def test_format_then_parse(self, seconds):
        formatted = format_timecode(seconds)
        assert parse_timecode(formatted["text"]) == pytest.approx(seconds, abs=0.001)
