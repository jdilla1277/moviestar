"""Issue #358: proactive FFmpeg capability evidence.

Three layers, tested here end-to-end at the CLI surface:

1. ``moviestar doctor`` — environment-wide FFmpeg/ffprobe + build
   capability report with stable codes, blocked-feature lists, and
   install hints.
2. Author-time preflight — ``captions generate --highlight
   spoken-word`` and ``overlays set`` refuse to persist a highlight
   plan the current FFmpeg build cannot export; text-overlay
   authoring warns when ``drawtext`` is missing.
3. Storyboard degrades to an unlabeled sheet with a structured
   warning instead of surfacing raw FFmpeg stderr mid-render.

The just-in-time render preflights from #242 are covered in
test_ffmpeg.py and stay in place as defense in depth.
"""

import json

import pytest
from click.testing import CliRunner
from tests.conftest import (
    assert_error_envelope,
    inject_synthetic_transcript,
    make_segment,
    make_word,
)

import moviestar.cli as cli_mod
import moviestar.ffmpeg as ffmpeg_mod
from moviestar.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


def _report(**overrides) -> dict:
    """A complete, healthy capability report; override fields per test."""
    report = {
        "ffmpeg": {
            "available": True,
            "path": "/opt/homebrew/bin/ffmpeg",
            "version": "7.1.1",
        },
        "ffprobe": {
            "available": True,
            "path": "/opt/homebrew/bin/ffprobe",
            "version": "7.1.1",
        },
        "filters": ["alphamerge", "ass", "drawtext", "geq", "scale"],
        "encoders": ["aac", "libx264"],
        "probe_errors": [],
    }
    report.update(overrides)
    return report


def _capability(data: dict, name: str) -> dict:
    entries = [c for c in data["capabilities"] if c["capability"] == name]
    assert entries, f"no capability entry for {name!r} in {data['capabilities']!r}"
    return entries[0]


def _warning_codes(data: dict) -> set[str]:
    return {w["code"] for w in data.get("warnings", [])}


class TestDoctorCommand:
    def test_complete_environment_is_ready(self, runner, monkeypatch):
        monkeypatch.setattr(cli_mod, "ffmpeg_capability_report", _report)
        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "ready"
        assert data["ffmpeg"]["available"] is True
        assert data["ffmpeg"]["version"] == "7.1.1"
        assert data["ffprobe"]["available"] is True
        assert data.get("warnings", []) == []
        assert all(c["available"] is True for c in data["capabilities"])
        # Every capability names the MovieStar features it gates, even
        # when present, so agents can read the mapping proactively.
        assert all(c["blocks"] for c in data["capabilities"])
        assert data["writes_spec"] is False

    def test_missing_drawtext_degrades_and_names_blocked_features(
        self, runner, monkeypatch
    ):
        monkeypatch.setattr(
            cli_mod,
            "ffmpeg_capability_report",
            lambda: _report(filters=["ass", "scale"]),
        )
        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "degraded"
        entry = _capability(data, "drawtext")
        assert entry["available"] is False
        assert entry["code"] == "ffmpeg_missing_drawtext_filter"
        blocked = " ".join(entry["blocks"]).lower()
        assert "overlay" in blocked
        assert "storyboard" in blocked
        assert "ffmpeg" in entry["hint"]
        assert "ffmpeg_missing_drawtext_filter" in _warning_codes(data)

    def test_missing_ass_degrades_and_names_spoken_word(
        self, runner, monkeypatch
    ):
        monkeypatch.setattr(
            cli_mod,
            "ffmpeg_capability_report",
            lambda: _report(filters=["drawtext", "scale"]),
        )
        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "degraded"
        entry = _capability(data, "ass")
        assert entry["available"] is False
        assert entry["code"] == "ffmpeg_missing_ass_filter"
        assert "spoken-word" in " ".join(entry["blocks"]).lower()
        assert "libass" in entry["hint"]
        assert "ffmpeg_missing_ass_filter" in _warning_codes(data)

    def test_missing_ffmpeg_blocks_with_nonzero_exit(
        self, runner, monkeypatch
    ):
        monkeypatch.setattr(
            cli_mod,
            "ffmpeg_capability_report",
            lambda: _report(
                ffmpeg={"available": False, "path": None, "version": None},
                ffprobe={"available": False, "path": None, "version": None},
                filters=None,
                encoders=None,
            ),
        )
        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="doctor")
        assert data["status"] == "blocked"
        assert data["code"] == "ffmpeg_not_found"
        # Capability availability is unknown without ffmpeg, not false.
        assert all(c["available"] is None for c in data["capabilities"])

    def test_missing_encoder_degrades(self, runner, monkeypatch):
        monkeypatch.setattr(
            cli_mod,
            "ffmpeg_capability_report",
            lambda: _report(encoders=["aac"]),
        )
        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "degraded"
        entry = _capability(data, "libx264")
        assert entry["available"] is False
        assert entry["code"] == "ffmpeg_missing_libx264_encoder"
        assert "export" in " ".join(entry["blocks"]).lower()

    def test_failed_capability_probe_degrades(self, runner, monkeypatch):
        monkeypatch.setattr(
            cli_mod,
            "ffmpeg_capability_report",
            lambda: _report(
                filters=None,
                probe_errors=[{"probe": "filters", "error": "boom"}],
            ),
        )
        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["status"] == "degraded"
        assert _capability(data, "drawtext")["available"] is None
        assert "ffmpeg_capability_probe_failed" in _warning_codes(data)

    def test_doctor_help_documents_exit_policy(self, runner):
        result = runner.invoke(cli, ["doctor", "--help"])
        assert result.exit_code == 0
        for token in ("ready", "degraded", "blocked"):
            assert token in result.output

    def test_help_browsing_section_lists_doctor(self, runner):
        result = runner.invoke(cli, ["--help"])
        out = result.output
        browsing_idx = out.index("BROWSING COMMANDS")
        editing_idx = out.index("EDITING COMMANDS", browsing_idx)
        assert "doctor" in out[browsing_idx:editing_idx]


def _words():
    return [make_word("hello", 0.1, 0.4), make_word("world", 0.5, 0.9)]


def _segments():
    return [make_segment("hello world", 0.1, 0.9)]


def _composed_project(runner, test_video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli,
        ["load", test_video, "--as", "src_0",
         "--interval", "1.0", "--no-transcribe"],
    )
    assert result.exit_code == 0, result.stdout
    inject_synthetic_transcript(tmp_path, _words(), _segments())
    result = runner.invoke(
        cli,
        ["scenes", "set", "--canvas", "short",
         "--scene", "intro=single",
         "--slot", "intro:main=src_0", "--from", "0", "--to", "2",
         "--audio-from", "intro=src_0"],
    )
    assert result.exit_code == 0, result.stdout


class TestAuthorTimeHighlightPreflight:
    def test_captions_generate_spoken_word_blocked_without_ass(
        self, runner, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            ffmpeg_mod, "ffmpeg_filter_available", lambda name: name != "ass"
        )
        result = runner.invoke(
            cli, ["captions", "generate", "--highlight", "spoken-word"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="captions generate")
        assert data["code"] == "ffmpeg_missing_ass_filter"
        assert "doctor" in data["hint"]
        # Preflight fires before any spec write: nothing persisted.
        spec_path = tmp_path / "moviestar" / "spec.json"
        assert not spec_path.exists() or "spoken-word" not in spec_path.read_text()

    def test_captions_generate_spoken_word_succeeds_with_ass(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _composed_project(runner, test_video, tmp_path, monkeypatch)
        monkeypatch.setattr(
            ffmpeg_mod, "ffmpeg_filter_available", lambda name: True
        )
        result = runner.invoke(
            cli, ["captions", "generate", "--highlight", "spoken-word"]
        )
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["highlight"]["mode"] == "spoken-word"

    def test_overlays_set_highlight_blocked_without_ass(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _composed_project(runner, test_video, tmp_path, monkeypatch)
        monkeypatch.setattr(
            ffmpeg_mod, "ffmpeg_filter_available", lambda name: name != "ass"
        )
        overlay_file = tmp_path / "overlays.json"
        overlay_file.write_text(
            json.dumps(
                {
                    "overlays": [
                        {
                            "text": "hello",
                            "from": "0",
                            "to": "1",
                            "highlight": {
                                "mode": "spoken-word",
                                "color": "#ffe94a",
                            },
                        }
                    ]
                }
            )
        )
        result = runner.invoke(cli, ["overlays", "set", str(overlay_file)])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="overlays set")
        assert data["code"] == "ffmpeg_missing_ass_filter"

    def test_overlays_set_without_highlight_not_blocked_by_missing_ass(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _composed_project(runner, test_video, tmp_path, monkeypatch)
        monkeypatch.setattr(
            ffmpeg_mod, "ffmpeg_filter_available", lambda name: name != "ass"
        )
        overlay_file = tmp_path / "overlays.json"
        overlay_file.write_text(
            json.dumps(
                {"overlays": [{"text": "hello", "from": "0", "to": "1"}]}
            )
        )
        result = runner.invoke(cli, ["overlays", "set", str(overlay_file)])
        assert result.exit_code == 0, result.stdout


class TestAuthorTimeDrawtextWarning:
    def test_overlays_add_warns_without_drawtext(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _composed_project(runner, test_video, tmp_path, monkeypatch)
        monkeypatch.setattr(
            ffmpeg_mod,
            "ffmpeg_filter_available",
            lambda name: name != "drawtext",
        )
        result = runner.invoke(
            cli,
            ["overlays", "add", "--text", "Title",
             "--from", "0", "--to", "1"],
        )
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        # The overlay still persists — burn-in is degraded, not authoring.
        assert data["overlays_count"] == 1
        assert "ffmpeg_missing_drawtext_filter" in _warning_codes(data)

    def test_captions_generate_warns_without_drawtext(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _composed_project(runner, test_video, tmp_path, monkeypatch)
        monkeypatch.setattr(
            ffmpeg_mod,
            "ffmpeg_filter_available",
            lambda name: name != "drawtext",
        )
        result = runner.invoke(cli, ["captions", "generate"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert "ffmpeg_missing_drawtext_filter" in _warning_codes(data)

    def test_overlays_set_warns_without_drawtext(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _composed_project(runner, test_video, tmp_path, monkeypatch)
        monkeypatch.setattr(
            ffmpeg_mod,
            "ffmpeg_filter_available",
            lambda name: name != "drawtext",
        )
        overlay_file = tmp_path / "overlays.json"
        overlay_file.write_text(
            json.dumps(
                {"overlays": [{"text": "hello", "from": "0", "to": "1"}]}
            )
        )
        result = runner.invoke(cli, ["overlays", "set", str(overlay_file)])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert "ffmpeg_missing_drawtext_filter" in _warning_codes(data)

    def test_overlays_add_no_warning_with_drawtext(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        _composed_project(runner, test_video, tmp_path, monkeypatch)
        monkeypatch.setattr(
            ffmpeg_mod, "ffmpeg_filter_available", lambda name: True
        )
        result = runner.invoke(
            cli,
            ["overlays", "add", "--text", "Title",
             "--from", "0", "--to", "1"],
        )
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert "ffmpeg_missing_drawtext_filter" not in _warning_codes(data)


class TestStoryboardDrawtextDegradation:
    def test_storyboard_renders_unlabeled_without_drawtext(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            ffmpeg_mod,
            "ffmpeg_filter_available",
            lambda name: name != "drawtext",
        )
        result = runner.invoke(
            cli,
            ["storyboard", test_video, "--from", "0", "--to", "2"],
        )
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["labels"] is False
        assert "ffmpeg_missing_drawtext_filter" in _warning_codes(data)
        # The sheet itself still rendered — degradation, not failure.
        assert (tmp_path / "storyboard.jpg").exists()

    def test_storyboard_dry_run_skips_capability_probe(
        self, runner, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)

        def _fail_probe(name):
            pytest.fail("dry-run must not spawn a capability probe")

        monkeypatch.setattr(
            ffmpeg_mod, "ffmpeg_filter_available", _fail_probe
        )
        result = runner.invoke(
            cli,
            ["storyboard", test_video, "--from", "0", "--to", "2",
             "--dry-run"],
        )
        assert result.exit_code == 0, result.stdout
