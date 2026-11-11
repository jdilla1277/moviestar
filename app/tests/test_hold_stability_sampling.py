"""Hold stability compares one requested frame pair, not later frames (#171)."""

import json
import re
import subprocess

import pytest
from click.testing import CliRunner

from moviestar import ffmpeg
from moviestar.cli import cli


@pytest.fixture(scope="module")
def switching_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("hold-stability") / "switching.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=black:size=320x240:duration=1:rate=30",
        "-f", "lavfi", "-i", "color=white:size=320x240:duration=1:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True, capture_output=True)
    return path


def isolated_reference(video, a, b, tmp_path):
    """Compare independently extracted, lossless, single-frame snapshots.

    Unlike the production two-seek graph, each reference input physically
    contains one frame, so later decoder/filter work cannot affect its SSIM.
    Keep the source YUV pixel format to avoid RGB/range conversion differences.
    """
    paths = []
    for index, at in enumerate((a, b)):
        path = tmp_path / f"frame-{index}.mkv"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-ss", str(at), "-i", str(video),
            "-an", "-frames:v", "1", "-vf",
            "crop=160:120:80:60,scale=320:-2,setsar=1",
            "-c:v", "ffv1", "-pix_fmt", "yuv420p", str(path),
        ], check=True, capture_output=True)
        paths.append(path)
    result = subprocess.run([
        "ffmpeg", "-v", "info", "-i", str(paths[0]), "-i", str(paths[1]),
        "-filter_complex", "[0:v][1:v]ssim", "-an", "-f", "null", "-",
    ], check=True, capture_output=True, text=True)
    return float(re.search(r"All:(-?[0-9.]+)", result.stderr).group(1))


@pytest.mark.parametrize("a,b,changes", [
    (0.8, 1.4, True),       # Reported failure: later frames converge to white.
    (1.4, 0.8, True),       # Comparison must work in either seek order.
    (0, 1.4, True),
    (0.811, 1.405, True),   # Different fractional seek/first-frame timestamps.
    (0.8, 1.95, True),      # Last decodable frame near EOF.
    (0.1, 0.9, False),      # Both requested frames black; one later turns white.
    (1.1, 1.7, False),      # Both requested frames white.
])
def test_score_uses_exactly_one_pair_and_matches_isolated_frames(
    switching_video, tmp_path, monkeypatch, a, b, changes
):
    expected = isolated_reference(switching_video, a, b, tmp_path)
    assert (expected < 0.65) is changes
    original_run = subprocess.run
    monkeypatch.chdir(tmp_path)

    def trace_pairs(command, *args, **kwargs):
        command = list(command)
        index = command.index("-filter_complex") + 1
        command[index] = command[index].replace("ssim", "ssim=stats_file=pairs.txt")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(ffmpeg.subprocess, "run", trace_pairs)
    score = ffmpeg.measure_region_stability(
        str(switching_video), a, b, {"x": 80, "y": 60, "w": 160, "h": 120}
    )
    assert score == pytest.approx(expected, abs=1e-6)
    pairs = (tmp_path / "pairs.txt").read_text().splitlines()
    assert len(pairs) == 1, f"Expected only the requested pair, compared {len(pairs)}"


def test_change_outside_selected_region_does_not_lower_score(tmp_path):
    path = tmp_path / "partial.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=black:size=320x240:duration=2:rate=30",
        "-vf", "drawbox=x=160:y=0:w=160:h=240:color=white:t=fill:enable='gte(t,1)'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True, capture_output=True)
    assert ffmpeg.measure_region_stability(
        str(path), 0.8, 1.4, {"x": 0, "y": 0, "w": 128, "h": 120}
    ) == pytest.approx(1.0)


@pytest.mark.parametrize("a,b", [(2.2, 0.8), (0.8, 2.2)])
def test_absent_frame_at_eof_has_no_score(switching_video, a, b):
    assert ffmpeg.measure_region_stability(
        str(switching_video), a, b, {"x": 80, "y": 60, "w": 160, "h": 120}
    ) is None


def preview_hold(video, loaded_project, tmp_path, *, at, hold, move):
    loaded_project(str(video), frames=False)
    runner = CliRunner()
    scene = runner.invoke(cli, [
        "scenes", "set", "--canvas", "320x240", "--scene", "demo=single",
        "--slot", "demo:main=src_0", "--from", "0", "--to", "2",
    ])
    assert scene.exit_code == 0, scene.output
    spec_path = tmp_path / "moviestar" / "spec.json"
    before = spec_path.read_bytes()
    preview = runner.invoke(cli, [
        "scenes", "motion", "camera", "preview", "--at", str(at), "--slot", "main",
        "--box", "80,60,240,180", "--padding", "0", "--move-in", str(move),
        "--hold", str(hold), "--move-out", str(move),
    ])
    assert preview.exit_code == 0, preview.output
    assert spec_path.read_bytes() == before
    return json.loads(preview.stdout)


def test_preview_stays_quiet_when_only_later_frames_change(
    switching_video, loaded_project, tmp_path
):
    data = preview_hold(switching_video, loaded_project, tmp_path, at=0.1, hold=0.8, move=0.05)
    assert not [
        warning for warning in data.get("warnings", [])
        if warning["code"] == "selection_changes_during_hold"
    ]


@pytest.fixture(scope="module")
def inverted_pattern(tmp_path_factory):
    path = tmp_path_factory.mktemp("inverted-hold") / "pattern.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "nullsrc=s=320x240:r=30:d=2,"
        "geq=lum='16+219*mod(floor(X/4)+floor(Y/4)+gte(T,1),2)':cb=128:cr=128",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True, capture_output=True)
    return path


def test_valid_negative_similarity_matches_isolated_frames(inverted_pattern, tmp_path):
    expected = isolated_reference(inverted_pattern, 0.8, 1.4, tmp_path)
    assert expected < 0
    assert ffmpeg.measure_region_stability(
        str(inverted_pattern), 0.8, 1.4, {"x": 80, "y": 60, "w": 160, "h": 120}
    ) == pytest.approx(expected, abs=1e-6)


def test_valid_negative_similarity_produces_a_real_preview_warning(
    inverted_pattern, loaded_project, tmp_path
):
    data = preview_hold(inverted_pattern, loaded_project, tmp_path, at=0.8, hold=0.6, move=0.3)
    [warning] = [
        warning for warning in data.get("warnings", [])
        if warning["code"] == "selection_changes_during_hold"
    ]
    assert warning["similarity"] < 0
    assert warning["source_range_compared"]["from"] == "0:00:00.800"
    assert warning["source_range_compared"]["to"] == "0:00:01.400"
