"""Platform UI preview and deterministic safe-zone linting."""

import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import build_platform_preview_command
from moviestar.platforms import (
    PLATFORM_PROFILE_VERSION,
    canvas_matches_platform_aspect,
    lint_platform_targets,
    resolve_platform_profile,
)


@pytest.fixture
def runner():
    return CliRunner()


def test_profile_regions_scale_to_canvas_and_are_versioned():
    profile = resolve_platform_profile("tiktok", (1080, 1920))

    assert profile["platform"] == "tiktok"
    assert profile["version"] == PLATFORM_PROFILE_VERSION
    assert profile["expected_aspect_ratio"] == "9:16"
    assert profile["canvas"] == {"width": 1080, "height": 1920}
    assert {region["id"] for region in profile["ui_regions"]} == {
        "top_navigation",
        "right_actions",
        "bottom_chrome",
    }
    assert all(region["bounds"]["width"] > 0 for region in profile["ui_regions"])


def test_lint_reports_intersection_and_clear_targets():
    profile = resolve_platform_profile("youtube-shorts", (1080, 1920))
    issues = lint_platform_targets(
        profile,
        [
            {
                "id": "caption_0001",
                "type": "overlay",
                "bounds": {"x": 200, "y": 1600, "width": 650, "height": 120},
            },
            {
                "id": "title_0001",
                "type": "overlay",
                "bounds": {"x": 250, "y": 500, "width": 400, "height": 100},
            },
        ],
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "platform_ui_occlusion"
    assert issues[0]["target_id"] == "caption_0001"
    assert issues[0]["ui_region"] == "bottom_chrome"
    assert issues[0]["overlap_percent"] > 0


def test_platform_aspect_check_accepts_vertical_and_rejects_landscape():
    assert canvas_matches_platform_aspect(
        resolve_platform_profile("tiktok", (1080, 1920))
    )
    assert not canvas_matches_platform_aspect(
        resolve_platform_profile("tiktok", (1920, 1080))
    )


def test_preview_command_draws_versioned_ui_regions(tmp_path):
    profile = resolve_platform_profile("instagram-reels", (1080, 1920))
    command = build_platform_preview_command(
        "frame.jpg",
        str(tmp_path / "annotated.jpg"),
        profile,
        "/tmp/Inter-Bold.ttf",
    )

    graph = command[command.index("-vf") + 1]
    assert graph.count("drawbox=") == 6  # translucent fill + border per region
    assert "drawtext=" in graph
    assert "Instagram Reels" in graph
    assert command[-1].endswith("annotated.jpg")


def _scene_project(runner, loaded_project, test_video):
    loaded_project(
        [test_video, test_video],
        names=["screen", "speaker"],
        frames=False,
    )
    authored = runner.invoke(
        cli,
        [
            "scenes", "set", "--canvas", "short",
            "--scene", "demo=picture-in-picture",
            "--slot", "demo:main=screen", "--from", "0", "--to", "1",
            "--slot", "demo:inset=speaker", "--from", "0", "--to", "1",
            "--audio-from", "demo=screen",
        ],
    )
    assert authored.exit_code == 0, authored.stdout
    overlay = runner.invoke(
        cli,
        [
            "overlays", "add", "--track", "captions", "--text", "Read me",
            "--from", "0", "--to", "1", "--position", "bottom",
        ],
    )
    assert overlay.exit_code == 0, overlay.stdout


def test_screenshot_platform_dry_run_lints_known_geometry(
    runner, loaded_project, test_video
):
    _scene_project(runner, loaded_project, test_video)

    result = runner.invoke(
        cli,
        ["screenshot", "--at", "0.5", "--platform", "tiktok", "--dry-run"],
    )

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    preview = data["platform_preview"]
    assert preview["platform"] == "tiktok"
    assert preview["profile_version"] == PLATFORM_PROFILE_VERSION
    assert preview["annotated"] is False
    assert preview["known_targets_checked"] >= 2  # caption + PiP inset
    assert {target["id"] for target in preview["known_targets"]} >= {
        "manual_0001",
        "demo:inset",
    }
    assert preview["ocr_performed"] is False
    assert "baked-in" in preview["limitations"]
    assert preview["ffmpeg_command"][0] == "ffmpeg"
    warnings = [
        warning for warning in data["warnings"]
        if warning["code"] == "platform_ui_occlusion"
    ]
    assert any(warning["target_id"] == "manual_0001" for warning in warnings)
    assert any(warning["target_id"] == "demo:inset" for warning in warnings)
    assert all(warning["platform"] == "tiktok" for warning in warnings)


def test_screenshot_help_teaches_platform_preview(runner):
    result = runner.invoke(cli, ["screenshot", "--help"])
    root = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert root.exit_code == 0
    compact = " ".join(result.output.split())
    assert "--platform" in compact
    assert "tiktok" in compact
    assert "instagram-reels" in compact
    assert "youtube-shorts" in compact
    assert "does not use OCR" in compact
    assert "TikTok/Reels/Shorts UI masks" in " ".join(root.output.split())


def test_file_mode_platform_preview_marks_unknown_content_unchecked(
    runner, test_video, tmp_path
):
    result = runner.invoke(
        cli,
        [
            "screenshot", "--file", test_video, "--at", "0.5",
            "--platform", "youtube-shorts", "--out", str(tmp_path / "shot.jpg"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["platform_preview"]["annotated"] is True
    assert data["platform_preview"]["known_targets_checked"] == 0
    assert data["platform_preview"]["ocr_performed"] is False
    assert (tmp_path / "shot.jpg").exists()
