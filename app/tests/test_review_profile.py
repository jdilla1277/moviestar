"""A review rendition changes delivery dimensions/quality, not authored layout."""

import json
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import build_extract_clip_command, build_render_segments_command
from tests.conftest import make_odd_sized_video


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def video_info(path):
    data = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ]))
    return next(s for s in data["streams"] if s["codec_type"] == "video"), data


def check_receipt(data, path, expected):
    video, probe = video_info(path)
    assert (video["width"], video["height"]) == expected
    profile = data["render_profile"]
    assert profile["name"] == "review-720p"
    assert profile["max_dimensions"] == {"long_edge": 1280, "short_edge": 720}
    assert profile["upscale"] is False
    assert profile["actual_dimensions"] == {"width": expected[0], "height": expected[1]}
    assert profile["video_encoding"]["pixel_format"] == video["pix_fmt"] == "yuv420p"
    assert video["codec_name"] == "h264"
    assert data["file_size_bytes"] == Path(path).stat().st_size
    return probe


@pytest.mark.parametrize("encoder", ["x264", "videotoolbox"])
@pytest.mark.parametrize("kind", ["extract", "segments"])
def test_review_builders_reencode_with_declared_quality_and_final_scale(encoder, kind):
    if kind == "extract":
        command = build_extract_clip_command("/source.mp4", 0, 1, "/review.mp4",
                                             review=True, video_encoder=encoder)
        graph = command[command.index("-vf") + 1]
    else:
        command = build_render_segments_command(
            [("/source.mp4", 0, 1), ("/source.mp4", 1, 2)], "/review.mp4",
            review=True, video_encoder=encoder, segment_has_audio=[False, False],
            output_fps=30,
        )
        graph = command[command.index("-filter_complex") + 1]
        assert graph.index("concat=") < graph.index("scale=")
        assert graph.index("fps=30") < graph.index("scale=")
    assert "1280" in graph and "720" in graph
    assert "force_divisible_by=2" in graph
    assert "copy" not in command
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    if encoder == "x264":
        assert command[command.index("-crf") + 1] == "23"
        assert command[command.index("-preset") + 1] == "fast"
    else:
        assert command[command.index("-b:v") + 1] == "4M"
        assert "-crf" not in command


@pytest.mark.parametrize("command", ["watch", "export"])
def test_review_dry_run_is_discoverable_read_only_and_honest(
    test_video, loaded_project, tmp_path, command
):
    loaded_project(test_video, frames=False)
    before = snapshot(tmp_path / "moviestar")
    assert "--review" in CliRunner().invoke(cli, [command, "--help"]).output
    args = ["--from", "0", "--to", "1"] if command == "watch" else []
    result = invoke(command, *args, "--review", "--dry-run")
    profile = result["render_profile"]
    assert profile["name"] == "review-720p"
    assert profile["actual_dimensions"] is None
    assert profile["video_encoding"]["crf"] == 23
    assert "file_size_bytes" not in result
    assert snapshot(tmp_path / "moviestar") == before
    assert not (tmp_path / "moviestar-clips").exists()
    assert not list(tmp_path.glob("watch*.mp4"))
    output = result["would_extract_to" if command == "watch" else "would_render_to"]
    assert "review" in Path(output).name
    if command == "watch":
        assert result["precise"] is True


@pytest.mark.parametrize("conflict", ["--preview", "--fast"])
def test_review_rejects_conflicting_export_modes(test_video, loaded_project, conflict):
    loaded_project(test_video, frames=False)
    result = CliRunner().invoke(cli, ["export", "--review", conflict, "--dry-run"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["command"] == "export"
    assert "--review" in data["error"]
    assert conflict in data["error"]
    assert data["hint"]


@pytest.fixture(scope="session")
def review_sources(tmp_path_factory):
    root = tmp_path_factory.mktemp("review-sources")
    sources = {}
    for size in [(1920, 1080), (1080, 1920), (1080, 1080), (640, 360),
                 (2560, 720), (720, 2560)]:
        path = root / f"{size[0]}x{size[1]}.mkv"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            f"color=c=gray:s={size[0]}x{size[1]}:r=30:d=2", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=2", "-c:v", "ffv1", "-c:a", "pcm_s16le",
            "-shortest", str(path),
        ], check=True)
        sources[size] = path
    return sources


@pytest.mark.parametrize("source_size,expected", [
    ((1920, 1080), (1280, 720)), ((1080, 1920), (720, 1280)),
    ((1080, 1080), (720, 720)), ((640, 360), (640, 360)),
    ((2560, 720), (1280, 360)), ((720, 2560), (360, 1280)),
])
@pytest.mark.parametrize("command", ["export", "watch"])
def test_review_actual_dimensions_aspect_audio_and_no_upscale(
    review_sources, loaded_project, tmp_path, command, source_size, expected
):
    loaded_project(review_sources[source_size], frames=False)
    before = snapshot(tmp_path / "moviestar")
    args = ["--from", "0.2", "--to", "1.8", "--source", "src_0"] if command == "watch" else []
    out = tmp_path / f"{command}-review.mp4"
    data = invoke(command, *args, "--review", "--out", str(out))
    probe = check_receipt(data, out, expected)
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    duration = float(probe["format"]["duration"])
    assert duration == pytest.approx(1.6 if command == "watch" else 2.0, abs=0.08)
    assert snapshot(tmp_path / "moviestar") == before


def test_review_odd_source_uses_even_padding_without_cropping(loaded_project, tmp_path):
    source = make_odd_sized_video(tmp_path / "odd.mkv", 321, 241)
    loaded_project(source, frames=False)
    out = tmp_path / "odd-review.mp4"
    data = invoke("export", "--review", "--out", str(out))
    check_receipt(data, out, (322, 242))


def test_scene_review_preserves_typography_timing_and_final_audio_processing(
    review_sources, loaded_project, tmp_path
):
    loaded_project(review_sources[(1080, 1920)], frames=False)
    invoke("scenes", "set", "--canvas", "1080x1920", "--scene", "one=single",
           "--slot", "one:main=src_0", "--from", "0", "--to", "1",
           "--scene", "two=single", "--slot", "two:main=src_0", "--from", "1", "--to", "2")
    invoke("overlays", "add", "--text", "Keep the authored line breaks and placement",
           "--from", "0", "--to", "2", "--position", "center", "--style", "caption-default",
           "--css", "font-size: 72px; max-width: 80%; -moviestar-stroke: 2px black")
    invoke("audio", "source", "--gain-db", "-3")
    spec = tmp_path / "moviestar" / "spec.json"
    before = spec.read_bytes()
    master = tmp_path / "master.mp4"
    full = invoke("export", "--out", str(master))
    master_bytes = master.read_bytes()
    reference = full["overlays"]["items"][0]
    assert len(reference["lines"]) >= 2
    for command, args in [("export", ["--loudness-target", "-18"]),
                          ("watch", ["--from", "0.2", "--to", "1.8"])]:
        out = tmp_path / f"scene-{command}-review.mp4"
        data = invoke(command, "--review", *args, "--out", str(out))
        check_receipt(data, out, (720, 1280))
        text = data["overlays"]["items"][0]
        for key in ("lines", "font_size", "estimated_bounds"):
            assert text[key] == reference[key]
        assert data["composition_canvas"] == full["composition_canvas"]
        assert data["audio_mix_applied_to_ffmpeg_command"] is True
        assert "force_original_aspect_ratio" in " ".join(data["concat_command"])
        for scene in data["scene_render_commands"]:
            assert "force_original_aspect_ratio=decrease:force_divisible_by=2" not in " ".join(scene["command"])
        if command == "export":
            assert data["loudness_normalization"]["measured"] is not None
        # Independently downscale a master frame. The review should show the
        # same composition; small pixel differences from H.264 are expected.
        expected = subprocess.check_output([
            "ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(master),
            "-vf", "scale=720:1280", "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-",
        ])
        actual = subprocess.check_output([
            "ffmpeg", "-v", "error", "-ss", "0.3", "-i", str(out),
            "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-",
        ])
        assert len(actual) == len(expected)
        background = expected[0]
        ink = [i for i, value in enumerate(expected) if abs(value - background) > 10]
        assert ink
        left = max(0, min(i % 720 for i in ink) - 10)
        right = min(720, max(i % 720 for i in ink) + 11)
        top = max(0, min(i // 720 for i in ink) - 10)
        bottom = min(1280, max(i // 720 for i in ink) + 11)
        errors = [abs(actual[y * 720 + x] - expected[y * 720 + x])
                  for y in range(top, bottom) for x in range(left, right)]
        assert sum(errors) / len(errors) < 3.0
        assert spec.read_bytes() == before
        assert master.read_bytes() == master_bytes


def test_default_review_export_preserves_master_and_uses_unique_names(
    test_video, loaded_project, tmp_path
):
    loaded_project(test_video, frames=False)
    clips = tmp_path / "moviestar-clips"
    clips.mkdir()
    master = clips / "export.mp4"
    master.write_bytes(b"existing master")
    first = invoke("export", "--review")
    second = invoke("export", "--review")
    assert Path(first["out"]).name == "review-720p.mp4"
    assert Path(second["out"]).name == "review-720p_2.mp4"
    assert master.read_bytes() == b"existing master"


def test_review_preserves_edited_source_segments_and_duration(
    review_sources, loaded_project, tmp_path
):
    loaded_project(review_sources[(640, 360)], frames=False)
    invoke("trim", "--from", "0.1", "--to", "1.9")
    invoke("cut", "--from", "0.5", "--to", "1.0")
    spec = tmp_path / "moviestar" / "spec.json"
    before = spec.read_bytes()
    out = tmp_path / "edited-review.mp4"
    data = invoke("export", "--review", "--out", str(out))
    probe = check_receipt(data, out, (640, 360))
    assert data["segments_count"] == 2
    assert float(probe["format"]["duration"]) == pytest.approx(1.3, abs=0.08)
    assert spec.read_bytes() == before


def test_review_resizes_after_dissolve_without_changing_duration(
    review_sources, loaded_project, tmp_path
):
    loaded_project(review_sources[(1920, 1080)], frames=False)
    invoke("scenes", "set", "--canvas", "1920x1080", "--scene", "one=single",
           "--slot", "one:main=src_0", "--from", "0.2", "--to", "1.0",
           "--scene", "two=single", "--slot", "two:main=src_0", "--from", "1", "--to", "1.8")
    invoke("scenes", "transition", "--before", "two", "--type", "dissolve", "--duration", "0.2")
    spec = tmp_path / "moviestar" / "spec.json"
    before = spec.read_bytes()
    out = tmp_path / "dissolve-review.mp4"
    data = invoke("export", "--review", "--out", str(out))
    probe = check_receipt(data, out, (1280, 720))
    graph = data["concat_command"][data["concat_command"].index("-filter_complex") + 1]
    assert graph.index("xfade=") < graph.index("force_original_aspect_ratio=decrease")
    assert float(probe["format"]["duration"]) == pytest.approx(1.6, abs=0.08)
    assert spec.read_bytes() == before


def test_review_output_without_video_is_not_reported_as_verified(
    test_video, loaded_project, tmp_path, monkeypatch
):
    from moviestar import cli as cli_module

    loaded_project(test_video, frames=False)
    output = tmp_path / "unverified.mp4"
    original = cli_module.run_ffprobe

    def incomplete_probe(path):
        if str(path) == str(output):
            return {"streams": [], "format": {"duration": "2"}}
        return original(path)

    monkeypatch.setattr(cli_module, "run_ffprobe", incomplete_probe)
    result = CliRunner().invoke(cli, ["export", "--review", "--out", str(output)])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["command"] == "export"
    assert "video" in data["error"].lower()
    assert data["hint"]
    assert "render_profile" not in data
    assert output.exists()  # retain the encoded file for diagnosis
