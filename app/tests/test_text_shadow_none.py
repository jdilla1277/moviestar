"""Explicit CSS shadow resets stay quiet and render without shadows (#196)."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import OVERLAY_STYLE_PRESETS, _overlay_style_envelope, cli
from moviestar.overlays import plan_overlays
from tests.test_overlays import _record


@pytest.mark.parametrize("value", ["none", "NONE", " NoNe "])
def test_none_overrides_a_preset_shadow_and_survives_round_trips(monkeypatch, value):
    monkeypatch.setitem(OVERLAY_STYLE_PRESETS["title"], "text_shadow", "8px 8px black")
    style = _overlay_style_envelope(preset="title", css=f"text-shadow: {value}")
    assert style["resolved"]["text_shadow"] == value.strip()
    for _ in range(3):
        style = _overlay_style_envelope(**{k: style[k] for k in ("preset", "css")})
        assert style["resolved"]["text_shadow"] == value.strip()
        planned = plan_overlays([_record(style=style)], (1080, 1920), 0, 6)
        assert planned["plans"][0]["shadow"] is None
        assert not any("text-shadow" in w for w in planned["warnings"])


@pytest.mark.parametrize("value", ["none", "NONE", " none "])
def test_stored_shadow_reset_is_understood_without_reauthoring(value):
    record = _record()
    record["style"]["resolved"]["text_shadow"] = value
    planned = plan_overlays([record], (1080, 1920), 0, 6)
    assert planned["plans"][0]["shadow"] is None
    assert not any("text-shadow" in w for w in planned["warnings"])


@pytest.mark.parametrize("value,warning", [
    ("nonsense", "was not understood"),
    ("2px 3px 4px black", "blur radius is ignored"),
])
def test_invalid_and_blurred_shadow_warnings_remain(value, warning):
    record = _record()
    record["style"]["resolved"]["text_shadow"] = value
    planned = plan_overlays([record], (1080, 1920), 0, 6)
    assert any(warning in w for w in planned["warnings"])


@pytest.mark.parametrize("command", ["screenshot", "export"])
@pytest.mark.parametrize("value", ["none", "NONE"])
@pytest.mark.parametrize("highlight", [False, True])
def test_none_survives_imports_and_matches_actual_unshadowed_pixels(
    loaded_project, tmp_path, command, value, highlight
):
    video = tmp_path / "gray.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=gray:s=320x240:r=25:d=0.6", "-c:v", "mpeg4", str(video),
    ], check=True, capture_output=True)
    loaded_project(video, frames=False)

    def invoke(*args):
        result = CliRunner().invoke(cli, list(args))
        assert result.exit_code == 0, (result.stdout, result.exception)
        return json.loads(result.stdout)

    css = "font-size: 40px; -moviestar-stroke: 0px; color: white"
    invoke("overlays", "add", "--text", "Shadow", "--from", "0", "--to", "0.6",
           "--css", css + "; text-shadow: 8px 8px black")
    spec_path = tmp_path / "moviestar/spec.json"
    replacement = tmp_path / "overlays.json"
    records = json.loads(spec_path.read_text())["overlays"]
    if highlight:
        records[0]["highlight"] = {"mode": "spoken-word", "color": "#ffe94a"}
        records[0]["tokens"] = [
            {"text": "Shadow", "from": "0:00:00.000", "to": "0:00:00.600"},
        ]
        replacement.write_text(json.dumps({"overlays": records}))
        invoke("overlays", "set", str(replacement))

    def render(name):
        path = tmp_path / (name + (".png" if command == "screenshot" else ".mp4"))
        args = ["screenshot", "--at", "0.2"] if command == "screenshot" else ["export"]
        result = invoke(*args, "--out", str(path))
        assert not any(w.get("code") == "overlay_text_shadow_skipped"
                       for w in result.get("warnings", []) if isinstance(w, dict))
        assert not any("was not understood" in str(w) for w in result.get("warnings", []))
        return subprocess.check_output([
            "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
        ])

    shadowed = render("shadowed")
    records = json.loads(spec_path.read_text())["overlays"]
    records[0]["style"]["css"] = css + f"; text-shadow: {value}"
    replacement.write_text(json.dumps({"overlays": records}))
    invoke("overlays", "set", str(replacement))
    for _ in range(3):
        spec = invoke("spec")
        assert spec["overlays"][0]["style"]["resolved"]["text_shadow"] == value
        complete = tmp_path / "complete.json"
        complete.write_text(json.dumps(spec))
        invoke("spec", "--edit", str(complete), "--dry-run")
        invoke("spec", "--edit", str(complete))
        replacement.write_text(json.dumps({"overlays": spec["overlays"]}))
        invoke("overlays", "set", str(replacement))
    reset = render("reset")
    records[0]["style"]["css"] = css
    replacement.write_text(json.dumps({"overlays": records}))
    invoke("overlays", "set", str(replacement))
    assert reset == render("unshadowed-reference")
    assert reset != shadowed
