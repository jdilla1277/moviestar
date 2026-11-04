"""Use a font's outlines when embedded bitmap strikes cannot be drawn (#117)."""

import json
from pathlib import Path
import subprocess
import sys

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import (
    _failure_message, build_overlay_filter_parts, render_segments,
    render_layout_video, render_layout_frame, render_layout_frames,
)
from moviestar.fonts import bundled_font_dir


GENEVA = Path("/System/Library/Fonts/Geneva.ttf")
MAC_GENEVA = pytest.mark.skipif(
    sys.platform != "darwin" or not GENEVA.is_file(),
    reason="requires the macOS Geneva font with embedded bitmap strikes",
)


def text_plan(font, family, stroke=0):
    return {
        "font": {"path": str(font), "family": family},
        "font_size": 24, "font_color": "0xffffff@1.0",
        "lines": ["Registered system"], "line_exprs": [("8", "8")],
        "stroke": {"width": stroke, "color": "black"} if stroke else None,
        "rotate_deg": 0, "enable_from": 0, "enable_to": 1,
    }


def graph_for(plan):
    return ";".join(build_overlay_filter_parts(
        [plan], (320, 160), 0.1, input_label="0:v", output_label="outv", static=True,
    ))


def render_pixels(graph):
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "color=black:s=320x160:r=30:d=0.1", "-filter_complex", graph,
        "-map", "[outv]", "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-",
    ], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout


@MAC_GENEVA
@pytest.mark.parametrize("stroke", [0, 4], ids=["monochrome-error", "generic-stroke-error"])
def test_geneva_bitmap_sizes_render_the_requested_font(stroke):
    plan = text_plan(GENEVA, "Geneva", stroke)
    graph = graph_for(plan)
    pixels = render_pixels(graph)
    assert len(pixels) == 320 * 160
    assert sum(value > 200 for value in pixels) > 100
    assert f"fontfile={GENEVA}" in graph
    assert plan["font"]["family"] == "Geneva"


@pytest.mark.parametrize("stroke", [0, 4])
def test_bundled_outline_font_keeps_the_same_pixels(stroke):
    graph = graph_for(text_plan(bundled_font_dir() / "Inter-Regular.ttf", "Inter", stroke))
    pixels = render_pixels(graph)
    assert sum(value > 200 for value in pixels) > 100
    # Choosing outlines should leave existing outline-only fonts unchanged.
    assert pixels == render_pixels(graph.replace(":ft_load_flags=no_bitmap", ""))


@pytest.fixture(scope="session")
def font_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("font-rendering") / "black.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=black:s=320x160:r=30:d=1", "-c:v", "mpeg4", str(path),
    ], check=True)
    return path


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@MAC_GENEVA
@pytest.mark.parametrize("stroke", [0, 4])
def test_geneva_screenshot_watch_and_export_preserve_font_and_project(
    tmp_path, monkeypatch, loaded_project, font_video, stroke
):
    loaded_project(font_video, frames=False)
    # Stock macOS resolves system fonts by filename. Avoid a machine-specific
    # fontconfig alias choosing another file during this regression.
    monkeypatch.setattr("moviestar.fonts._fc_match", lambda *args: None)
    monkeypatch.setattr("moviestar.fonts._SYSTEM_FONT_DIRS", [GENEVA.parent])
    invoke("scenes", "set", "--canvas", "320x160", "--scene", "proof=single",
           "--slot", "proof:main=src_0", "--from", "0", "--to", "1")
    invoke("overlays", "add", "--text", "Geneva Aa", "--from", "0", "--to", "1",
           "--position", "center", "--style", "caption-default", "--css",
           f"font-family: Geneva; font-size: 24px; -moviestar-stroke: {stroke}px black")
    spec = tmp_path / "moviestar" / "spec.json"
    before = spec.read_bytes()
    original_font = GENEVA.read_bytes()
    for command, extra, suffix in [
        ("screenshot", ["--at", "0.5"], ".png"),
        ("watch", ["--from", "0", "--to", "1", "--precise"], ".mp4"),
        ("export", [], ".mp4"),
    ]:
        output = tmp_path / (command + suffix)
        data = invoke(command, *extra, "--out", str(output))
        assert data["overlays"]["items"][0]["font"]["family"] == "Geneva"
        assert str(GENEVA) in json.dumps(data)
        pixels = subprocess.check_output([
            "ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1",
            "-pix_fmt", "gray", "-f", "rawvideo", "-",
        ])
        assert sum(value > 200 for value in pixels) > 40
        assert spec.read_bytes() == before
    assert GENEVA.read_bytes() == original_font


@pytest.mark.parametrize("diagnostic", [
    "Monocromatic (1bpp) fonts are not supported.",
    "Monochromatic (1bpp) fonts are not supported.",
])
def test_unsupported_bitmap_error_identifies_font_size_and_remedy(diagnostic):
    plan = text_plan("/fonts/bitmap.ttf", "Bitmap Family")
    result = subprocess.CompletedProcess(["ffmpeg"], 1, stdout="", stderr=diagnostic)
    message = _failure_message("ffmpeg", result, overlay_plans=[plan, plan])
    assert diagnostic in message
    assert "Bitmap Family" in message
    assert "24px" in message
    assert "/fonts/bitmap.ttf" in message
    assert "outline" in message.lower()
    assert "Inter" in message
    assert message.count("Bitmap Family") == 1


def test_generic_filter_error_does_not_claim_bitmap_font_failure():
    diagnostic = "Generic error in an external library"
    result = subprocess.CompletedProcess(["ffmpeg"], 1, stdout="", stderr=diagnostic)
    assert _failure_message("ffmpeg", result, overlay_plans=[
        text_plan("/fonts/font.ttf", "Some Font"),
    ]) == f"ffmpeg failed: {diagnostic}"


@pytest.fixture
def bitmap_font(tmp_path):
    # An original, one-glyph BDF fixture generated at runtime; no third-party
    # font file is distributed. FreeType ignores no_bitmap on bitmap-only faces.
    path = tmp_path / "bitmap.bdf"
    path.write_text("""STARTFONT 2.1
FONT -moviestar-test-medium-r-normal--8-80-75-75-c-80-iso10646-1
SIZE 8 75 75
FONTBOUNDINGBOX 8 8 0 0
STARTPROPERTIES 2
FONT_ASCENT 8
FONT_DESCENT 0
ENDPROPERTIES
CHARS 1
STARTCHAR A
ENCODING 65
SWIDTH 500 0
DWIDTH 8 0
BBX 8 8 0 0
BITMAP
18
24
42
7E
42
42
42
00
ENDCHAR
ENDFONT
""")
    return path


@pytest.mark.parametrize("mode", ["segments", "layout_video", "frame", "frames"])
def test_bitmap_glyph_errors_fail_even_when_ffmpeg_exits_successfully(
    tmp_path, font_video, bitmap_font, mode
):
    plan = text_plan(bitmap_font, "Bitmap Test")
    plan.update(font_size=8, lines=["A"])
    slots = [{"path": str(font_video), "source_from": 0.0, "source_to": 1.0,
              "region": {"x": 0, "y": 0, "width": 320, "height": 160},
              "has_audio": False}]
    out = str(tmp_path / "output.mp4")
    def render():
        if mode == "segments":
            render_segments([(str(font_video), 0, 1)], out, precise=True,
                            segment_has_audio=[False], output_canvas=(320, 160),
                            overlay_canvas=(320, 160), overlay_plans=[plan],
                            progress_cb=lambda line: None)
            return Path(out)
        if mode == "layout_video":
            render_layout_video(slots, out, (320, 160), 1.0,
                                overlay_plans=[plan], progress_cb=lambda line: None)
            return Path(out)
        if mode == "frame":
            path = tmp_path / "frame.png"
            render_layout_frame(slots, str(path), (320, 160), overlay_plans=[plan])
            return path
        paths, _ = render_layout_frames(slots, tmp_path / "frames", "proof", (320, 160),
                                        1.0, 0.5, overlay_plans=[plan])
        return paths[0]

    with pytest.raises(RuntimeError) as exc:
        render()
    message = str(exc.value)
    assert "Bitmap Test at 8px" in message
    assert str(bitmap_font) in message
    assert "outline" in message.lower()
    assert "Inter" in message


def test_unsupported_bitmap_font_cli_error_is_actionable_without_substitution(
    tmp_path, monkeypatch, loaded_project, font_video, bitmap_font
):
    loaded_project(font_video, frames=False)
    # Fontconfig may resolve installed BDF faces even though managed imports
    # accept only TTF/OTF/TTC. Model that system resolution without installing it.
    monkeypatch.setattr("moviestar.fonts._fc_match", lambda *args: str(bitmap_font))
    invoke("scenes", "set", "--canvas", "320x160", "--scene", "proof=single",
           "--slot", "proof:main=src_0", "--from", "0", "--to", "1")
    invoke("overlays", "add", "--text", "A", "--from", "0", "--to", "1",
           "--style", "caption-default", "--css",
           "font-family: Bitmap Test; font-size: 8px; -moviestar-stroke: 0px black")
    spec = tmp_path / "moviestar" / "spec.json"
    before = spec.read_bytes()
    result = CliRunner().invoke(cli, ["screenshot", "--at", "0.5",
                                     "--out", str(tmp_path / "unsupported.png")])
    data = json.loads(result.stdout)
    assert result.exit_code == 1
    assert data["command"] == "screenshot"
    assert "Bitmap Test at 8px" in data["error"]
    assert str(bitmap_font) in data["error"]
    assert "outline" in data["error"].lower()
    assert "Inter" in data["error"]
    assert "out" not in data
    assert spec.read_bytes() == before
