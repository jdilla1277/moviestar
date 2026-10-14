"""Encoder selection is a per-invocation contract, independent of host builds."""

import json
import subprocess

import pytest
from click.testing import CliRunner

import moviestar.ffmpeg as ffmpeg
import moviestar.cli as cli_mod
from moviestar.cli import cli


@pytest.mark.parametrize("requested,names,platform,expected", [
    ("auto", {"libx264", "h264_videotoolbox"}, "darwin", "x264"),
    ("auto", {"h264_videotoolbox"}, "darwin", "videotoolbox"),
    ("x264", {"libx264"}, "linux", "x264"),
    ("videotoolbox", {"libx264", "h264_videotoolbox"}, "darwin", "videotoolbox"),
])
def test_selection(requested, names, platform, expected):
    assert ffmpeg.select_video_encoder(requested, names, platform=platform) == expected


@pytest.mark.parametrize("requested,names,platform,code", [
    ("auto", set(), "darwin", "ffmpeg_no_supported_h264_encoder"),
    ("auto", {"h264_videotoolbox"}, "linux", "ffmpeg_no_supported_h264_encoder"),
    ("x264", {"h264_videotoolbox"}, "darwin", "ffmpeg_missing_libx264_encoder"),
    ("videotoolbox", {"libx264"}, "darwin", "ffmpeg_missing_videotoolbox_encoder"),
    ("videotoolbox", {"h264_videotoolbox"}, "linux", "ffmpeg_videotoolbox_unsupported_platform"),
])
def test_selection_errors(requested, names, platform, code):
    with pytest.raises(ffmpeg.FFmpegCapabilityError) as error:
        ffmpeg.select_video_encoder(requested, names, platform=platform)
    assert error.value.code == code
    assert "doctor" in error.value.hint


def test_stream_copy_needs_no_probe(monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe_video_encoders", lambda: pytest.fail("probe"))
    assert ffmpeg.resolve_video_encoder("auto", reencode=False) is None
    with pytest.raises(ffmpeg.FFmpegCapabilityError, match="stream-copy"):
        ffmpeg.resolve_video_encoder("x264", reencode=False)


def test_failed_probe_is_structured(monkeypatch):
    monkeypatch.setattr(ffmpeg.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a[0], 1, "", "listing failed"))
    with pytest.raises(ffmpeg.FFmpegCapabilityError) as error:
        ffmpeg.resolve_video_encoder("auto")
    assert error.value.code == "ffmpeg_capability_probe_failed"


@pytest.mark.parametrize("failure", [FileNotFoundError("ffmpeg"), subprocess.TimeoutExpired("ffmpeg", 15)])
def test_probe_os_errors_are_structured(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(ffmpeg.subprocess, "run", fail)
    with pytest.raises(ffmpeg.FFmpegCapabilityError) as error:
        ffmpeg.resolve_video_encoder("auto")
    assert error.value.code == "ffmpeg_capability_probe_failed"


@pytest.mark.parametrize("preview", [False, True])
def test_videotoolbox_command_profile(preview):
    cmd = ffmpeg.build_extract_clip_command(
        "in.mp4", 0, 1, "out.mp4", precise=True, preview=preview,
        video_encoder="videotoolbox",
    )
    assert cmd[cmd.index("-c:v") + 1] == "h264_videotoolbox"
    assert "-crf" not in cmd and "-preset" not in cmd
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert "-b:v" in cmd


@pytest.fixture
def loaded(tmp_path, monkeypatch, test_video):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["load", test_video, "--as", "src_0", "--no-transcribe"])
    assert result.exit_code == 0, result.output
    return runner


def test_cli_fallback_and_copy(loaded, monkeypatch):
    calls = []
    def probe():
        calls.append(True)
        return {"h264_videotoolbox"}
    monkeypatch.setattr(ffmpeg, "probe_video_encoders", probe)
    monkeypatch.setattr(ffmpeg.sys, "platform", "darwin")
    result = loaded.invoke(cli, ["clip", "--from", "0", "--to", "1", "--dry-run"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["video_encoder"] == "videotoolbox"
    assert "libx264" in data["note"]
    assert len(calls) == 1
    result = loaded.invoke(cli, ["clip", "--from", "0", "--to", "1", "--fast", "--dry-run"])
    assert json.loads(result.output)["video_encoder"] is None
    assert len(calls) == 1


def test_explicit_copy_rejected(loaded):
    result = loaded.invoke(cli, ["watch", "--from", "0", "--to", "1",
                                 "--video-encoder", "x264", "--dry-run"])
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "video_encoder_requires_reencode"


def test_fast_with_audio_filter_still_selects_encoder(loaded, monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe_video_encoders", lambda: {"libx264"})
    result = loaded.invoke(cli, ["cut", "--from", "0.8", "--to", "1.2"])
    assert result.exit_code == 0, result.output
    result = loaded.invoke(cli, ["export", "--fast", "--audio-join-fade", "0.05",
                                 "--video-encoder", "x264", "--dry-run"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["video_encoder"] == "x264" and data["mode"] == "re-encode"
    assert "--fast" in data["note"]


@pytest.mark.parametrize("command,args", [
    ("clip", ["--from", "0", "--to", "1"]),
    ("watch", ["--from", "0", "--to", "1", "--precise"]),
    ("export", []),
    ("batch", ["recipe.json"]),
])
def test_unavailable_encoder_fails_before_render(loaded, monkeypatch, tmp_path, command, args):
    (tmp_path / "recipe.json").write_text(json.dumps({"clips": [{"from": "0", "to": "1"}]}))
    monkeypatch.setattr(ffmpeg, "probe_video_encoders", lambda: set())
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    for dry in ([], ["--dry-run"]):
        result = loaded.invoke(cli, [command, *args, "--video-encoder", "x264", *dry])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["code"] == "ffmpeg_missing_libx264_encoder"
        assert "doctor" in data["hint"]
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("encoders,platform,selected", [
    (["aac", "h264_videotoolbox"], "darwin", "videotoolbox"),
    (["aac", "h264_videotoolbox"], "linux", None),
    (["aac"], "darwin", None),
])
def test_doctor_alternative_encoders(monkeypatch, encoders, platform, selected):
    from tests.test_doctor import _report
    monkeypatch.setattr(cli_mod, "ffmpeg_capability_report", lambda: _report(encoders=encoders))
    monkeypatch.setattr(ffmpeg.sys, "platform", platform)
    data = json.loads(CliRunner().invoke(cli, ["doctor"]).output)
    assert data["video_encoder"] == selected
    assert data["status"] == ("ready" if selected else "degraded")
    warnings = {w["code"] for w in data.get("warnings", [])}
    assert "ffmpeg_missing_libx264_encoder" not in warnings
    assert ("ffmpeg_no_supported_h264_encoder" in warnings) == (selected is None)
    x264 = next(c for c in data["capabilities"] if c["capability"] == "libx264")
    assert x264["code"] == "ffmpeg_missing_libx264_encoder"


@pytest.mark.parametrize("command,args", [
    ("clip", ["--from", "0", "--to", "1"]),
    ("watch", ["--from", "0", "--to", "1", "--precise"]),
    ("export", []),
    ("export", ["--preview"]),
])
def test_real_videotoolbox_parity(loaded, monkeypatch, tmp_path, command, args):
    if ffmpeg.sys.platform != "darwin" or "h264_videotoolbox" not in ffmpeg.probe_video_encoders():
        pytest.skip("requires advertised macOS VideoToolbox")
    calls = []
    def probe():
        calls.append(True)
        return {"h264_videotoolbox"}
    monkeypatch.setattr(ffmpeg, "probe_video_encoders", probe)
    output = tmp_path / "result.mp4"
    args = [command, *args, "--out", str(output)]
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    dry = loaded.invoke(cli, [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    dry_data = json.loads(dry.stdout)
    real = loaded.invoke(cli, args)
    assert real.exit_code == 0, real.output
    real_data = json.loads(real.stdout)
    assert calls == [True, True]
    assert dry_data["ffmpeg_command"] == real_data["ffmpeg_command"]
    assert real_data["video_encoder"] == "videotoolbox"
    streams = ffmpeg.run_ffprobe(str(output))["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert video["width"] % 2 == video["height"] % 2 == 0
    assert abs(float(video["duration"]) - (2 if command == "export" else 1)) < .1
    assert any(s["codec_name"] == "aac" for s in streams)
    if "--preview" in args:
        assert video["height"] == 480


def test_batch_parent_resolves_once_and_workers_reuse(loaded, monkeypatch, tmp_path):
    calls = []
    def probe():
        calls.append(True)
        return {"libx264"}
    monkeypatch.setattr(ffmpeg, "probe_video_encoders", probe)
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"clips": [{"from": "0", "to": "1"}, {"from": "1", "to": "2"}]}))
    dry = loaded.invoke(cli, ["batch", str(recipe), "--dry-run", "--jobs", "2"])
    real = loaded.invoke(cli, ["batch", str(recipe), "--jobs", "2"])
    assert real.exit_code == 0, real.output
    assert calls == [True, True]
    for data in [json.loads(dry.stdout), json.loads(real.stdout)]:
        assert data["video_encoder"] == "x264"
        assert all(c["video_encoder"] == "x264" for c in data["clips"])
    assert [c["ffmpeg_command"] for c in json.loads(dry.stdout)["clips"]] == [
        c["ffmpeg_command"] for c in json.loads(real.stdout)["clips"]]


def test_encoder_failure_is_not_retried(loaded, monkeypatch):
    monkeypatch.setattr(ffmpeg, "probe_video_encoders", lambda: {"libx264", "h264_videotoolbox"})
    monkeypatch.setattr(ffmpeg.sys, "platform", "darwin")
    calls = []
    def fail(*args, **kwargs):
        calls.append(kwargs["video_encoder"])
        raise RuntimeError("hardware session failed")
    monkeypatch.setattr(cli_mod, "extract_clip", fail)
    result = loaded.invoke(cli, ["clip", "--from", "0", "--to", "1",
                                 "--video-encoder", "videotoolbox"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["video_encoder"] == "videotoolbox"
    assert "hardware session failed" in data["error"]
    assert data["hint"] and calls == ["videotoolbox"]


@pytest.mark.parametrize("command", ["watch", "export"])
def test_scene_encoder_used_for_every_stage(loaded, monkeypatch, tmp_path, command):
    if ffmpeg.sys.platform != "darwin" or "h264_videotoolbox" not in ffmpeg.probe_video_encoders():
        pytest.skip("requires advertised macOS VideoToolbox")
    result = loaded.invoke(cli, ["scenes", "set", "--canvas", "320x240",
        "--scene", "intro=single", "--slot", "intro:main=src_0", "--from", "0", "--to", "1",
        "--audio-from", "intro=src_0", "--scene", "end=single",
        "--slot", "end:main=src_0", "--from", "1", "--to", "2", "--audio-from", "end=src_0"])
    assert result.exit_code == 0, result.output
    result = loaded.invoke(cli, ["overlays", "add", "--text", "Encoder check", "--from", "0", "--to", "2"])
    assert result.exit_code == 0, result.output
    calls = []
    def probe():
        calls.append(True)
        return {"h264_videotoolbox"}
    monkeypatch.setattr(ffmpeg, "probe_video_encoders", probe)
    args = [command, "--out", str(tmp_path / "scene.mp4"), "--video-encoder", "videotoolbox"]
    args += ["--fast"] if command == "export" else ["--from", "0", "--to", "2"]
    dry = loaded.invoke(cli, [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    real = loaded.invoke(cli, args)
    assert real.exit_code == 0, real.output
    dry_data, real_data = json.loads(dry.stdout), json.loads(real.stdout)
    assert calls == [True, True]
    assert dry_data["scene_render_commands"] == real_data["scene_render_commands"]
    assert dry_data["concat_command"] == real_data["concat_command"]
    assert real_data["video_encoder"] == "videotoolbox"
    for cmd in [*[c["command"] for c in real_data["scene_render_commands"]], real_data["concat_command"]]:
        assert cmd[cmd.index("-c:v") + 1] == "h264_videotoolbox"
