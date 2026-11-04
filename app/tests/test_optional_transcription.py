"""Local editing and recovery when the transcription extra is absent."""

import builtins
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.transcribe import TranscriptionError, pull_model, transcribe_file


@pytest.fixture(autouse=True)
def without_whisper(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def import_without_whisper(name, *args, **kwargs):
        if name == "faster_whisper" or name.startswith("faster_whisper."):
            raise ModuleNotFoundError("No module named 'faster_whisper'", name="faster_whisper")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_whisper)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "models"))


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.stdout, repr(result.exception)
    return result, json.loads(result.stdout)


def assert_install_hint(result, body, command):
    assert result.exit_code == 1, result.output
    assert body["command"] == command
    assert body["code"] == "transcription_not_installed"
    assert "python -m pip install 'moviestar[transcribe]'" in body["hint"]


@pytest.mark.parametrize("options", [(), ("--dry-run",), ("--no-download",)])
def test_load_requires_extra_before_creating_a_workspace(test_video, options):
    result, body = invoke("load", test_video, "--no-frames", *options)
    assert_install_hint(result, body, "load")
    assert not Path("moviestar").exists()
    assert not Path("models").exists()


@pytest.mark.parametrize("command", ["load", "retranscribe"])
def test_missing_extra_preserves_existing_workspace(test_video, command):
    result, _ = invoke("load", test_video, "--no-frames", "--no-transcribe")
    assert result.exit_code == 0
    workspace = Path("moviestar")
    before = {str(path): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    args = ("load", test_video, "--force", "--no-frames") if command == "load" else ("retranscribe",)
    result, body = invoke(*args)
    assert_install_hint(result, body, command)
    assert {str(path): path.read_bytes() for path in workspace.rglob("*") if path.is_file()} == before


def test_imported_captions_and_local_render_work_without_whisper(test_video):
    captions = Path("captions.vtt")
    captions.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello there\n")
    result, _ = invoke("load", test_video, "--no-frames", "--captions", str(captions))
    assert result.exit_code == 0, result.output
    result, _ = invoke("retranscribe", "--captions", str(captions))
    assert result.exit_code == 0, result.output
    result, _ = invoke("trim", "--from", "0", "--to", "1")
    assert result.exit_code == 0, result.output
    result, body = invoke("export", "--out", "clip.mp4")
    assert result.exit_code == 0, result.output
    assert Path(body["out"]).stat().st_size > 0
    assert not Path("models").exists()


def test_models_pull_has_a_structured_install_hint_without_downloading():
    result, body = invoke("models", "pull", "tiny")
    assert_install_hint(result, body, "models pull")
    assert not Path("models").exists()


@pytest.mark.parametrize("operation", ["transcribe", "pull"])
def test_python_api_reports_the_install_command(test_video, operation):
    with pytest.raises(TranscriptionError, match=r"moviestar\[transcribe\]"):
        transcribe_file(test_video, "source") if operation == "transcribe" else pull_model("tiny")
