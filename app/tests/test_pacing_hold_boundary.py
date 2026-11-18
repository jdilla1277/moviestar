"""End-of-scene holds must not leave accidental millisecond render segments."""

import json
import array
import math
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.motion import PacingResolutionError, resolve_pacing


def motion(at):
    return {"version": 1, "scenes": [{"scene": "demo", "slots": [{
        "slot": "main", "pacing": [{"id": "hold-end", "mode": "hold",
        "at": f"0:00:{at:09.6f}", "duration": "0:00:00.300"}],
    }]}]}


@pytest.mark.parametrize("source_start", [0, 10])
@pytest.mark.parametrize("tail", [0.0001, 0.0005, 0.001])
def test_resolver_rejects_millisecond_hold_tail_with_exact_end(tail, source_start):
    composition = [{"name": "demo", "slots": [{"slot": "main", "source": "source",
                    "source_from": f"0:00:{source_start:06.3f}",
                    "source_to": f"0:00:{source_start + 2:06.3f}"}]}]
    plan = motion(2 - tail)
    # Internal callers can supply numeric source times without timecode rounding.
    plan["scenes"][0]["slots"][0]["pacing"][0]["at"] = 2 - tail
    with pytest.raises(PacingResolutionError, match=r'hold-end.*at.*2\.000'):
        resolve_pacing(composition, plan)


@pytest.fixture
def project(test_video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    for args in [
        ["load", test_video, "--as", "source", "--no-transcribe", "--no-frames"],
        ["scenes", "set", "--canvas", "640x360", "--scene", "demo=single",
         "--slot", "demo:main=source", "--from", "0", "--to", "2",
         "--audio-from", "demo=source"],
    ]:
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
    return runner, tmp_path


@pytest.mark.parametrize("dry_run", [False, True])
def test_hold_tail_rejected_without_changing_spec(project, dry_run):
    runner, root = project
    spec = root / "moviestar/spec.json"
    before = spec.read_bytes()
    document = root / "motion.json"
    document.write_text(json.dumps(motion(1.999)))
    result = runner.invoke(cli, ["scenes", "motion", "set", str(document)] +
                           (["--dry-run"] if dry_run else []))
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["command"] == "scenes motion set"
    assert "hold-end" in json.dumps(body)
    assert "2.000" in json.dumps(body)
    assert spec.read_bytes() == before


@pytest.mark.parametrize("at", [2, 1.8])
def test_exact_end_and_real_final_segments_export_with_fade(project, at):
    runner, root = project
    document = root / "motion.json"
    document.write_text(json.dumps(motion(at)))
    applied = runner.invoke(cli, ["scenes", "motion", "set", str(document)])
    assert applied.exit_code == 0, applied.output
    output = root / "result.mp4"
    args = ["export", "--audio-join-fade", "0.05", "--video-encoder", "x264", "--out", str(output)]
    preview = runner.invoke(cli, args + ["--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert not output.exists()
    assert json.loads(preview.stdout)["result_duration"]["seconds"] == pytest.approx(2.3)
    rendered = runner.invoke(cli, args)
    assert rendered.exit_code == 0, rendered.output
    assert output.is_file()
    # Inspect the delivered streams and decode the audio independently of the
    # render plan: source tone must remain, and the inserted hold must be silent.
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)
    ]))
    assert {stream["codec_type"] for stream in probe["streams"]} == {"audio", "video"}
    for stream in probe["streams"]:
        assert float(stream["duration"]) == pytest.approx(2.3, abs=1 / 30)
    samples = array.array("f", subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(output), "-vn", "-ac", "1",
        "-ar", "48000", "-f", "f32le", "pipe:1",
    ]))

    def rms(start, end):
        window = samples[round(start * 48000):round(end * 48000)]
        assert window
        return math.sqrt(sum(value * value for value in window) / len(window))

    assert rms(0.2, 0.4) > 0.02
    assert rms(at + 0.1, at + 0.2) < 0.002
    if at < 2:
        assert rms(2.2, 2.25) > 0.02


@pytest.mark.parametrize("dry_run", [False, True])
def test_intentional_short_tail_fade_failure_is_structured(project, dry_run):
    runner, root = project
    document = root / "motion.json"
    document.write_text(json.dumps(motion(1.98)))
    applied = runner.invoke(cli, ["scenes", "motion", "set", str(document)])
    assert applied.exit_code == 0, applied.output
    result = runner.invoke(cli, ["export", "--audio-join-fade", "0.05", "--out", str(root / "result.mp4")] +
                           (["--dry-run"] if dry_run else []))
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["command"] == "export"
    assert "0.01" in body["error"]
    assert not (root / "result.mp4").exists()
    assert not (root / "moviestar/scene-renders").exists()


@pytest.mark.parametrize("at", [1.9996, 2.0004])
def test_timecodes_rounding_to_exact_end_do_not_lose_source_duration(at):
    composition = [{"name": "demo", "slots": [{"slot": "main", "source": "source",
                    "source_from": "0:00:00.000", "source_to": "0:00:02.000"}]}]
    timeline = resolve_pacing(composition, motion(at))
    assert timeline.duration_s == pytest.approx(2.3)
    assert len(timeline.scenes[0].segments) == 2
    assert timeline.scenes[0].segments[-1].held
    assert timeline.scenes[0].segments[-1].source_local_from_s == 2
