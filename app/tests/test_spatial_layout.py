"""Measured layout of saved spatial scenes."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.project import save_project
from moviestar.spatial_layout import (
    SpatialLayoutError, browser_available, measure_spatial_layout, prepare_layout_frame,
)
from moviestar.spec import empty_spec, save_spec


def _project():
    spec = empty_spec([{"id": "alice", "path": "/tmp/alice.mp4"}])
    spec["composition_canvas"] = {"preset": None, "width": 1280, "height": 720, "aspect_ratio": "16:9"}
    spec["composition"] = [{"id": "scene_a", "name": "scene_a", "layout": {"preset": "single"},
                            "slots": [{"slot": "main", "source": "alice",
                                       "source_from": "0:00:00.000", "source_to": "0:00:08.000"}]}]
    spec["spatial"] = {"definitions": {"pair": {
        "name": "ListenerPair", "inputs": [{"name": "left", "type": "video", "required": True},
                                           {"name": "right", "type": "video", "required": True}],
        "children": [{"id": "left_video", "type": "video", "content": {"input": "left"},
                      "layout": {"width": "80px", "height": "60px"}},
                     {"id": "right_video", "type": "video", "content": {"input": "right"},
                      "layout": {"width": "80px", "height": "60px"}},
                     {"id": "name", "type": "text", "content": {"text": "Alice"}}],
    }}, "scenes": {"scene_a": {"layout": {"display": "grid", "columns": "1fr 2fr",
                                           "gap": "16px", "children": [{
        "instance_id": "listeners", "definition_id": "pair",
        "bindings": {"left": {"asset_id": "alice", "source": {"start": 0, "end": 8}},
                     "right": {"asset_id": "alice", "source": {"start": 0, "end": 8}}},
        "layout": {"height": "120px"},
    }]}}}}
    return spec


def _source_project(spec):
    return {"sources": [{**source, "duration": {"seconds": 20}, "fps": 30,
                         "width": 1280, "height": 720, "has_audio": False}
                        for source in spec["sources"]]}


def _frame():
    return {
        "scene_id": "scene_a", "scene_time_s": 2, "result_time_s": 2,
        "display": "grid", "columns": "1fr 2fr", "gap": "16px",
        "children": [
            {"occurrence_path": ["scene_a", "left"], "type": "shape", "content": {},
             "layout": {"height": "120px"}, "style": {"background_color": "#123456"}},
            {"occurrence_path": ["scene_a", "right"], "type": "text",
             "content": {"text": "A two-line\nheadline"},
             "layout": {"height": "120px"}, "style": {"font_size": "28px"}},
        ],
        "diagnostics": [],
    }


def test_layout_preparation_maps_nested_css_and_rejects_unsafe_values():
    frame = _frame()
    prepared = prepare_layout_frame(frame, {"width": 640, "height": 360})
    assert prepared["canvas"] == {"width": 640, "height": 360}
    assert prepared["root_style"] == {"display": "grid", "grid-template-columns": "1fr 2fr",
                                      "row-gap": "16px", "column-gap": "16px"}
    assert prepared["nodes"][1]["style"]["font-size"] == "28px"
    assert prepared["nodes"][1]["content"] == "A two-line\nheadline"
    assert prepared["nodes"][0]["path"] == ["scene_a", "left"]
    frame["columns"] = "grid-placeholder"
    frame["grid-template-columns"] = "1fr 2fr"
    with pytest.raises(SpatialLayoutError, match="columns"):
        prepare_layout_frame(frame, {"width": 640, "height": 360})
    del frame["columns"]
    assert prepare_layout_frame(frame, {"width": 640, "height": 360})["root_style"]["grid-template-columns"] == "1fr 2fr"
    frame["children"][0]["style"]["background_color"] = "url(https://example.com/pixel)"
    with pytest.raises(SpatialLayoutError, match="background_color"):
        prepare_layout_frame(frame, {"width": 640, "height": 360})
    frame["children"][0]["style"] = {"unknown_property": "red"}
    with pytest.raises(SpatialLayoutError, match="unknown_property"):
        prepare_layout_frame(frame, {"width": 640, "height": 360})


def test_layout_preparation_preserves_component_paths_and_draft_diagnostics():
    frame = _frame()
    frame["children"][0] = {
        "occurrence_path": ["scene_a", "pair"], "definition_id": "ListenerPair",
        "layout": {"display": "flex", "gap": "8px", "height": "120px"}, "style": {},
        "children": [{"occurrence_path": ["scene_a", "pair", "video"],
                      "type": "video", "content": None, "media": None,
                      "layout": {"width": "80px", "height": "60px"}, "style": {}}],
    }
    frame["diagnostics"] = [{"code": "missing_binding", "path": ["scene_a", "pair", "video"]}]
    prepared = prepare_layout_frame(frame, {"width": 640, "height": 360})
    assert prepared["nodes"][0]["children"][0]["path"] == ["scene_a", "pair", "video"]
    assert prepared["diagnostics"] == frame["diagnostics"]


def test_layout_preparation_rejects_bad_canvas_and_invalid_css():
    frame = _frame()
    with pytest.raises(SpatialLayoutError, match="canvas"):
        prepare_layout_frame(frame, {"width": 0, "height": 360})
    frame["columns"] = "repeat(2, url(https://example.com/x))"
    with pytest.raises(SpatialLayoutError, match="columns"):
        prepare_layout_frame(frame, {"width": 640, "height": 360})
    frame["columns"] = "1fr 2fr"
    frame["width"] = "50vw"
    with pytest.raises(SpatialLayoutError, match="scene.layout.width"):
        prepare_layout_frame(frame, {"width": 640, "height": 360})


def test_numeric_css_preserves_unitless_values_and_adds_units_to_lengths():
    frame = _frame()
    frame["children"][0]["layout"] = {
        "width": 100, "height": 50, "aspect_ratio": 2,
        "grid_column": 2, "grid_row": 1, "grid_area": 2, "gap": 8,
    }
    frame["children"][0]["style"] = {"font_size": 24, "line_height": 1.5, "opacity": 0.5}
    prepared = prepare_layout_frame(frame, {"width": 640, "height": 360})
    assert prepared["nodes"][0]["style"] == {
        "width": "100px", "height": "50px", "aspect-ratio": "2",
        "grid-column-start": "auto", "grid-column-end": "auto",
        "grid-row-start": "2", "grid-row-end": "auto", "row-gap": "8px", "column-gap": "8px",
        "font-size": "24px", "line-height": "1.5", "opacity": "0.5",
    }


@pytest.mark.skipif(not browser_available(), reason="local Chrome and Node are required")
def test_browser_measures_grid_and_writes_labeled_png(tmp_path):
    image = tmp_path / "layout.png"
    result = measure_spatial_layout(_frame(), {"width": 640, "height": 360}, output_path=image)
    assert result["canvas"] == {"width": 640, "height": 360}
    assert result["view"] == "layout"
    assert result["boxes"][0]["path"] == ["scene_a", "left"]
    left, right = result["boxes"]
    assert left["rect"]["x"] == pytest.approx(0, abs=1)
    assert left["rect"]["width"] == pytest.approx(208, abs=1)
    assert right["rect"]["x"] == pytest.approx(224, abs=1)
    assert right["rect"]["width"] == pytest.approx(416, abs=1)
    assert len(right["text_lines"]) == 2
    assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert result["artifact"] == str(image)


@pytest.mark.skipif(not browser_available(), reason="local Chrome and Node are required")
def test_browser_measures_nested_flex_and_positioned_child():
    frame = _frame()
    frame["display"] = "flex"
    del frame["columns"]
    frame["padding"] = "20px"
    frame["children"][0] = {
        "occurrence_path": ["scene_a", "card"], "definition_id": "Card",
        "layout": {"display": "flex", "width": "300px", "height": "180px", "gap": "10px"},
        "style": {}, "children": [{
            "occurrence_path": ["scene_a", "card", "video"], "type": "video",
            "content": None, "media": None,
            "layout": {"width": "100px", "height": "80px"}, "style": {},
        }, {
            "occurrence_path": ["scene_a", "card", "badge"], "type": "shape",
            "content": {}, "layout": {"position": "absolute", "left": "40px",
                                      "top": "20px", "width": "50px", "height": "30px",
                                      "z_index": 2}, "style": {},
        }],
    }
    layout = measure_spatial_layout(frame, {"width": 640, "height": 360})
    card, video, badge, _ = layout["boxes"]
    assert card["rect"] == {"x": 20, "y": 20, "width": 300, "height": 180}
    assert video["rect"]["width"] == 100
    assert badge["rect"] == {"x": 60, "y": 40, "width": 50, "height": 30}
    assert [item["dom_order"] for item in layout["boxes"]] == [0, 1, 2, 3]


@pytest.mark.skipif(not browser_available(), reason="local Chrome and Node are required")
def test_browser_rejects_css_values_that_it_cannot_apply():
    frame = _frame()
    frame["display"] = "not-a-display-mode"
    with pytest.raises(SpatialLayoutError, match=r"scene.layout.display.*invalid CSS value"):
        measure_spatial_layout(frame, {"width": 640, "height": 360})


@pytest.mark.skipif(not browser_available(), reason="local Chrome and Node are required")
@pytest.mark.parametrize("target,property_name,value,error_path", [
    ("child", "position", "not-a-position", "scene_a/left.position"),
    ("child", "min_width", "not-a-length", "scene_a/left.min-width"),
    ("scene", "overflow", "not-an-overflow", "scene.layout.overflow"),
])
def test_browser_rejects_invalid_values_even_when_property_has_a_preset(
    target, property_name, value, error_path
):
    frame = _frame()
    properties = frame if target == "scene" else frame["children"][0]["layout"]
    properties[property_name] = value
    with pytest.raises(SpatialLayoutError) as error:
        measure_spatial_layout(frame, {"width": 640, "height": 360})
    assert f"{error_path}: invalid CSS value" in str(error.value)


@pytest.mark.skipif(not browser_available(), reason="local Chrome and Node are required")
def test_browser_measures_numeric_ratio_and_grid_placement():
    frame = _frame()
    frame["columns"] = "1fr 1fr"
    frame["rows"] = "100px 100px"
    frame["align_items"] = "start"
    frame["children"] = frame["children"][:1]
    frame["children"][0]["layout"] = {
        "width": 100, "aspect_ratio": 2, "grid_column": 2, "grid_row": 2,
    }
    result = measure_spatial_layout(frame, {"width": 640, "height": 360})
    assert result["boxes"][0]["rect"] == {"x": 328, "y": 116, "width": 100, "height": 50}


@pytest.mark.skipif(not browser_available(), reason="local Chrome and Node are required")
def test_model_layout_cli_uses_saved_scene_and_result_time(tmp_path, monkeypatch):
    spec = _project()
    layout = spec["spatial"]["scenes"]["scene_a"]["layout"]
    layout["columns"] = "1fr 2fr"
    layout["gap"] = "16px"
    layout["children"][0]["layout"] = {"height": "120px"}
    layout["children"].append({"id": "headline", "type": "text",
                               "content": {"text": "Hello"}, "layout": {"height": "120px"}})
    save_spec(spec, cwd=tmp_path)
    save_project(_source_project(spec), cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["composition", "model", "layout", "--at", "0:00:02.000",
                                      "--canvas", "640x360", "--out", "layout.png"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["status"] == "measured"
    assert envelope["frame"]["scene_id"] == "scene_a"
    assert envelope["frame"]["result_time_s"] == 2
    assert envelope["layout"]["canvas"] == {"width": 640, "height": 360}
    assert [box["path"][-1] for box in envelope["layout"]["boxes"]] == [
        "listeners", "left_video", "right_video", "name", "headline"]
    assert Path(envelope["layout"]["artifact"]).is_file()
    bad = CliRunner().invoke(cli, ["composition", "model", "layout", "--at", "0:00:02.000",
                                   "--canvas", "0x360"])
    assert bad.exit_code == 1
    assert json.loads(bad.output)["command"] == "composition model layout"


def test_model_layout_reports_missing_browser_as_json(tmp_path, monkeypatch):
    spec = _project()
    save_spec(spec, cwd=tmp_path)
    save_project(_source_project(spec), cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MOVIESTAR_CHROME_PATH", str(tmp_path / "missing-chrome"))
    result = CliRunner().invoke(cli, ["composition", "model", "layout", "--at", "0:00:02.000"])
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["command"] == "composition model layout"
    assert "MOVIESTAR_CHROME_PATH" in error["error"]
