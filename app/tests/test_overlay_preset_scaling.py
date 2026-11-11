"""Manual preset defaults scale; authored pixel overrides stay absolute (#17)."""

import copy
import json
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.overlays import plan_overlays


@pytest.fixture(scope="module")
def gray_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("overlay-presets") / "gray.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=gray:s=320x240:r=30:d=1", "-c:v", "mpeg4", str(path),
    ], check=True, capture_output=True)
    return path


def invoke(args):
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def prepare(gray_video, loaded_project, canvas):
    loaded_project(gray_video, frames=False)
    invoke([
        "scenes", "set", "--canvas", f"{canvas[0]}x{canvas[1]}",
        "--scene", "intro=single", "--slot", "intro:main=src_0",
        "--from", "0", "--to", "0.5",
    ])


def add(style, css=None):
    args = [
        "overlays", "add", "--text", "Hi", "--from", "0", "--to", "0.5",
        "--position", "lower-third-left", "--style", style,
    ]
    return invoke(args + (["--css", css] if css else []))


def stored(tmp_path):
    return json.loads((tmp_path / "moviestar" / "spec.json").read_text())["overlays"][0]


def plan(record, canvas):
    # Resolve the public timing shape for the standalone render planner.
    record = {**record, "from": "0", "to": "0.5"}
    return plan_overlays([record], canvas, 0, 0.5)["plans"][0]


@pytest.mark.parametrize("canvas", [(854, 480), (1920, 1080), (1080, 1920), (480, 854)])
@pytest.mark.parametrize("style,font,stroke", [
    ("title", 96, 3), ("lower-third", 42, 0),
    ("caption-default", 64, 4), ("social-bold", 72, 5),
])
def test_manual_presets_report_pixels_and_survive_dump_set(
    gray_video, loaded_project, tmp_path, canvas, style, font, stroke
):
    prepare(gray_video, loaded_project, canvas)
    add(style)
    record = stored(tmp_path)
    factor = min(canvas) / 1080
    expected_font = round(font * factor)
    expected_stroke = round(stroke * factor)
    actual = plan(record, canvas)
    assert actual["font_size"] == expected_font
    assert (actual["stroke"] or {}).get("width", 0) == expected_stroke
    assert actual["anchor_point"] == (
        round(canvas[0] * 96 / 1920), canvas[1] - round(canvas[1] * 180 / 1080)
    )
    if style == "lower-third":
        assert actual["box"]["borderw"] == round(24 * factor)

    path = tmp_path / "overlays.json"
    spec_path = tmp_path / "moviestar" / "spec.json"
    before = spec_path.read_bytes()
    dumped = invoke(["overlays", "dump", "--out", str(path)])
    dry = invoke(["overlays", "set", str(path), "--dry-run"])
    assert spec_path.read_bytes() == before
    for data in (dumped, dry):
        preview = data["resolved_geometry"]
        assert preview["canvas"] == {"width": canvas[0], "height": canvas[1]}
        [item] = preview["items"]
        assert item["id"] == record["id"]
        assert item["font_size_px"] == expected_font
        assert item["stroke_width_px"] == expected_stroke
        assert item["margin_px"] == {
            "x": round(canvas[0] * 96 / 1920), "y": round(canvas[1] * 180 / 1080)
        }
        assert item["padding_px"] == (
            {"x": round(24 * factor), "y": round(18 * factor)}
            if style == "lower-third" else {"x": 0, "y": 0}
        )
    invoke(["overlays", "set", str(path)])
    assert stored(tmp_path) == record
    [item] = invoke(["export", "--dry-run"])["overlays"]["items"]
    assert item["font_size"] == expected_font


@pytest.mark.parametrize("canvas", [(854, 480), (1080, 1920)])
@pytest.mark.parametrize("padding", ["8px 12px", "8 px 12 px"])
def test_pixel_overrides_and_legacy_records_remain_absolute(
    gray_video, loaded_project, tmp_path, canvas, padding
):
    prepare(gray_video, loaded_project, canvas)
    add("lower-third", f"font-size: 27px; padding: {padding}; -moviestar-stroke: 2px black")
    record = stored(tmp_path)
    # Integer margins from editable JSON are authored pixels, not preset units.
    record["position"].update(margin_x=11, margin_y=28)
    path = tmp_path / "overlays.json"
    path.write_text(json.dumps({"overlays": [record]}))
    invoke(["overlays", "set", str(path)])
    actual = plan(stored(tmp_path), canvas)
    assert actual["font_size"] == 27
    assert actual["stroke"]["width"] == 2
    assert actual["box"]["borderw"] == 12
    assert actual["anchor_point"] == (11, canvas[1] - 28)

    # A saved preset record from an older release still renders its stored
    # pixels. Merely loading/exporting a project must not migrate old titles.
    legacy = copy.deepcopy(record)
    legacy["style"]["resolved"].update(font_size=42, padding="18px 24px")
    before = copy.deepcopy(legacy)
    old_plan = plan(legacy, canvas)
    assert old_plan["font_size"] == 42
    assert old_plan["box"]["borderw"] == 24
    assert legacy == before


def test_saved_relative_defaults_follow_canvas_changes(
    gray_video, loaded_project, tmp_path
):
    prepare(gray_video, loaded_project, (854, 480))
    add("title")
    before = stored(tmp_path)
    invoke([
        "scenes", "set", "--canvas", "1920x1080", "--scene", "intro=single",
        "--slot", "intro:main=src_0", "--from", "0", "--to", "0.5",
    ])
    assert stored(tmp_path) == before
    [item] = invoke(["export", "--dry-run"])["overlays"]["items"]
    assert item["font_size"] == 96
    assert item["estimated_bounds"]["x"] == 96


def test_source_size_and_legacy_bulk_input_report_actual_pixels(
    gray_video, loaded_project, tmp_path
):
    loaded_project(gray_video, frames=False)
    path = tmp_path / "overlays.json"
    # The legacy from/to form takes a separate bulk-parser branch.
    path.write_text(json.dumps({"overlays": [{
        "text": "Hi", "from": "0", "to": "0.5",
        "position": {"preset": "top-left"}, "style": {"preset": "title"},
    }]}))
    spec_path = tmp_path / "moviestar" / "spec.json"
    assert not spec_path.exists()
    dry = invoke(["overlays", "set", str(path), "--dry-run"])
    assert not spec_path.exists()
    geometry = dry["resolved_geometry"]
    assert geometry["canvas"] == {"width": 320, "height": 240}
    assert geometry["items"][0]["font_size_px"] == 21
    assert geometry["items"][0]["margin_px"] == {"x": 16, "y": 36}


def test_dump_remains_editable_if_a_saved_font_is_unavailable(
    gray_video, loaded_project, tmp_path
):
    prepare(gray_video, loaded_project, (854, 480))
    add("title")
    spec_path = tmp_path / "moviestar" / "spec.json"
    spec = json.loads(spec_path.read_text())
    spec["overlays"][0]["style"]["resolved"]["font_family"] = "MissingLocalFontXYZ"
    spec_path.write_text(json.dumps(spec))
    before = spec_path.read_bytes()
    path = tmp_path / "overlays.json"
    result = invoke(["overlays", "dump", "--out", str(path)])
    assert result["resolved_geometry"]["available"] is False
    assert "MissingLocalFontXYZ" in result["resolved_geometry"]["detail"]
    assert json.loads(path.read_text())["overlays"] == spec["overlays"]
    assert spec_path.read_bytes() == before


def frame(path):
    return subprocess.check_output([
        "ffmpeg", "-v", "error", *([] if path.suffix == ".png" else ["-ss", "0.25"]),
        "-i", str(path), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ])


@pytest.mark.parametrize("canvas", [(854, 480), (960, 540), (540, 960)])
@pytest.mark.parametrize("style,font,stroke,padding", [
    ("title", 96, 3, "0px"), ("lower-third", 42, 0, None),
])
@pytest.mark.parametrize("command", ["screenshot", "watch", "preview", "export"])
def test_rendered_preset_matches_explicit_scaled_pixels(
    gray_video, loaded_project, tmp_path, canvas, style, font, stroke, padding, command
):
    prepare(gray_video, loaded_project, canvas)
    add(style)
    spec_path = tmp_path / "moviestar" / "spec.json"

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
        return frame(path)

    preset_pixels = render("preset")
    record = stored(tmp_path)
    factor = min(canvas) / 1080
    record["style"]["css"] = (
        f"font-size: {round(font * factor)}px; "
        f"-moviestar-stroke: {round(stroke * factor)}px black; "
        f"padding: {padding or f'{round(18 * factor)}px {round(24 * factor)}px'}"
    )
    record["position"].update(
        margin_x=round(canvas[0] * 96 / 1920),
        margin_y=round(canvas[1] * 180 / 1080),
    )
    path = tmp_path / "reference.json"
    path.write_text(json.dumps({"overlays": [record]}))
    invoke(["overlays", "set", str(path)])
    assert preset_pixels == render("explicit-pixels")
    assert max(preset_pixels) - min(preset_pixels) > 30, "the title must actually be visible"
