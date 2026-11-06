"""Accepted CSS transparency must survive authoring and real rendering (#97)."""

import json
import subprocess

import numpy as np
import pytest
from click.testing import CliRunner

from moviestar.cli import cli


@pytest.fixture(scope="module")
def gray_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("transparent-overlay") / "gray.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=gray:s=320x240:r=30:d=1", "-c:v", "mpeg4", str(path),
    ], check=True, capture_output=True)
    return path


def _frame(path):
    seek = [] if path.suffix == ".png" else ["-ss", "0.25"]
    return subprocess.check_output([
        "ffmpeg", "-v", "error", *seek, "-i", str(path),
        "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ])


@pytest.mark.parametrize("command", ["screenshot", "watch", "preview", "export"])
@pytest.mark.parametrize("invisible_text", [False, True])
def test_transparent_overlay_matches_zero_alpha_pixels_on_every_render_path(
    gray_video, loaded_project, tmp_path, command, invisible_text
):
    loaded_project(gray_video, frames=False)
    runner = CliRunner()

    def invoke(args):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    invoke([
        "scenes", "set", "--canvas", "320x240", "--scene", "intro=single",
        "--slot", "intro:main=src_0", "--from", "0", "--to", "0.5",
    ])
    foreground = "transparent" if invisible_text else "white"
    invoke([
        "overlays", "add", "--text", "Clear text", "--from", "0", "--to", "0.5",
        "--x", "0.5", "--y", "0.5", "--style", "lower-third", "--css",
        f"font-size: 32px; color: {foreground}; background-color: transparent; "
        "opacity: 0.5; padding: 8px 12px; -moviestar-stroke: 3px transparent; "
        "text-shadow: 2px 3px transparent",
    ])
    spec_path = tmp_path / "moviestar" / "spec.json"
    authored = json.loads(spec_path.read_text())
    assert authored["overlays"][0]["style"]["resolved"]["background_color"] == "transparent"

    def render(name):
        path = tmp_path / (name + (".png" if command == "screenshot" else ".mp4"))
        if command == "screenshot":
            args = ["screenshot", "--at", "0.25"]
        elif command == "watch":
            args = ["watch", "--from", "0", "--to", "0.5", "--precise"]
        else:
            args = ["export", *(["--preview"] if command == "preview" else [])]
        before = spec_path.read_bytes()
        result = invoke([*args, "--out", str(path)])
        assert result["overlays"]["burned_in"] is True
        assert spec_path.read_bytes() == before
        return _frame(path)

    transparent = render("transparent")
    # The zero-alpha hex form already renders correctly. Use its independently
    # decoded pixels as a visual control, including lower-third background
    # opacity, padding, a stroke, and a shadow rather than checking argv alone.
    records = authored["overlays"]
    records[0]["style"]["css"] = records[0]["style"]["css"].replace("transparent", "#00000000")
    replacement = tmp_path / "reference-overlays.json"
    replacement.write_text(json.dumps({"overlays": records}))
    invoke(["overlays", "set", str(replacement)])
    assert transparent == render("zero-alpha-reference")

    pixels = np.frombuffer(transparent, dtype=np.uint8).reshape(-1, 3).astype(int)
    assert pixels.size > 0
    difference = np.abs(pixels - pixels[0]).max()
    if invisible_text:
        assert difference <= 2, "transparent glyphs/stroke/shadow changed the gray frame"
    else:
        assert difference > 20, "text must remain visible above the transparent box"
