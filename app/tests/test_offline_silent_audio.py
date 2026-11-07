"""Cold-cache offline loads must check speech before changing the project."""

import json
import wave
import weakref
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar import transcribe


@pytest.fixture
def offline(monkeypatch, tmp_path):
    import faster_whisper

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-model-cache"))
    monkeypatch.setattr(transcribe, "resolve_cached_model", lambda model: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline cold-cache input must not initialize/download Whisper")

    monkeypatch.setattr(faster_whisper, "WhisperModel", forbidden)
    monkeypatch.setattr(transcribe, "_announce_download_if_needed", forbidden)
    decode, vad = transcribe._load_audio, transcribe._detect_speech
    calls = {"decode": 0, "vad": 0}
    buffers = []

    def counted_decode(*args):
        assert all(ref() is None for ref in buffers), "Do not retain prior sources' PCM buffers"
        calls["decode"] += 1
        audio = decode(*args)
        buffers.append(weakref.ref(audio))
        return audio

    def counted_vad(*args):
        calls["vad"] += 1
        return vad(*args)

    monkeypatch.setattr(transcribe, "_load_audio", counted_decode)
    monkeypatch.setattr(transcribe, "_detect_speech", counted_vad)
    return calls


@pytest.fixture
def silence(tmp_path):
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(16000 * 2 * 3))
    return str(path)


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.stdout, repr(result.exception)
    return result, json.loads(result.stdout)


def snapshot():
    return {str(p): p.read_bytes() for p in Path("moviestar").rglob("*") if p.is_file()}


def seed(source):
    result, _ = invoke("load", source, "--as", "original", "--no-frames", "--no-transcribe")
    assert result.exit_code == 0, result.output
    Path("moviestar/keep.txt").write_text("existing project must survive")


@pytest.mark.parametrize("mode", ["fresh", "force", "add", "retranscribe"])
@pytest.mark.parametrize("ranged", [False, True])
def test_silent_audio_succeeds_without_model_and_is_checked_once(offline, silence, mode, ranged):
    if mode != "fresh":
        seed(silence)
    if mode == "retranscribe":
        args = ["retranscribe"]
        range_flag = "--range"
    else:
        args = ["load", silence, "--no-frames"]
        if mode != "fresh":
            args += [f"--{mode}"]
        range_flag = "--transcribe-range"
    if ranged:
        args += [range_flag, "1", "2"]
    result, body = invoke(*args, "--model", "small", "--no-download")
    assert result.exit_code == 0, result.output
    assert "requires_download" not in body
    assert offline == {"decode": 1, "vad": 1}
    project = json.loads(Path("moviestar/project.json").read_text())
    source = project["sources"][-1]
    transcript = json.loads(Path(f"moviestar/transcripts/{source['id']}.json").read_text())
    assert transcript["words"] == []
    assert transcript["no_speech_detected"] is True
    [warning] = transcript["warnings"]
    assert warning["code"] == "no_speech_in_audio"
    assert warning["source_id"] == source["id"]
    if ranged:
        [window] = transcript["transcribed_ranges"]
        assert window["from"]["seconds"] == 1
        assert window["to"]["seconds"] == 2
    if mode == "add":
        assert len(project["sources"]) == 2
        assert Path("moviestar/keep.txt").read_text() == "existing project must survive"
    assert not Path("empty-model-cache").exists()


@pytest.mark.parametrize("mode", ["fresh", "force", "add"])
@pytest.mark.parametrize("speech_first", [False, True])
def test_mixed_silent_and_speech_sources_fail_before_any_mutation(offline, silence, mode, speech_first):
    if mode != "fresh":
        seed(silence)
    before = snapshot()
    speech = str(Path(__file__).parent / "fixtures/speech.wav")
    sources = [speech, silence] if speech_first else [silence, speech]
    options = [] if mode == "fresh" else [f"--{mode}"]
    result, body = invoke("load", *sources, *options, "--no-frames", "--model", "small", "--no-download")
    assert result.exit_code == 1, result.output
    assert body["requires_download"]["model"] == "small"
    assert "models pull small" in body["hint"]
    assert snapshot() == before
    assert Path("moviestar").exists() == (mode != "fresh")
    checked = 1 if speech_first else 2
    assert offline == {"decode": checked, "vad": checked}


@pytest.mark.parametrize("ranged", [False, True])
def test_retranscribe_speech_keeps_existing_transcript_and_spec(offline, ranged):
    speech = str(Path(__file__).parent / "fixtures/speech.wav")
    seed(speech)
    Path("moviestar/transcripts").mkdir(exist_ok=True)
    Path("moviestar/transcripts/original.json").write_text('{"words": [{"text": "keep"}]}')
    Path("moviestar/spec.json").write_text('{"keep": true}')
    before = snapshot()
    options = ["--range", "0", "2"] if ranged else []
    result, body = invoke("retranscribe", *options, "--model", "small", "--no-download")
    assert result.exit_code == 1, result.output
    assert body["requires_download"]["model"] == "small"
    assert snapshot() == before


@pytest.mark.parametrize("command", ["load", "retranscribe"])
def test_disabling_speech_check_still_requires_a_model(offline, silence, command):
    if command == "retranscribe":
        seed(silence)
    before = snapshot()
    options = [silence, "--no-frames"] if command == "load" else []
    result, body = invoke(command, *options, "--model", "small", "--no-download", "--no-speech-check")
    assert result.exit_code == 1, result.output
    assert body["requires_download"]["model"] == "small"
    assert offline == {"decode": 0, "vad": 0}
    assert snapshot() == before


def test_multiple_silent_sources_reuse_each_result_with_the_right_id(offline, silence):
    result, body = invoke("load", silence, silence, "--as", "left", "--as", "right", "--no-frames", "--no-download")
    assert result.exit_code == 0, result.output
    assert offline == {"decode": 2, "vad": 2}
    for source in body["sources"]:
        transcript = json.loads(Path(f"moviestar/transcripts/{source['id']}.json").read_text())
        assert transcript["source_id"] == source["id"]
        assert transcript["warnings"][0]["source_id"] == source["id"]


def test_direct_transcription_skips_uncached_model_on_silence(offline, silence):
    result = transcribe.transcribe_file(silence, "quiet", model="small", allow_download=False)
    assert result["words"] == []
    assert result["warnings"][0]["code"] == "no_speech_in_audio"
    assert offline == {"decode": 1, "vad": 1}


@pytest.mark.parametrize("command", ["load", "retranscribe"])
def test_silent_windows_preserve_imported_captions_outside_windows(offline, silence, command):
    captions = Path("captions.vtt")
    captions.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:00.500\nKeep\n\n00:00:01.000 --> 00:00:02.000\nRemove\n")
    if command == "retranscribe":
        seed(silence)
        args = [command, "--range", "1", "2"]
    else:
        args = [command, silence, "--as", "original", "--no-frames", "--transcribe-range", "1", "2"]
    result, _ = invoke(*args, "--captions", str(captions), "--model", "small", "--no-download")
    assert result.exit_code == 0, result.output
    transcript = json.loads(Path("moviestar/transcripts/original.json").read_text())
    assert [word["text"] for word in transcript["words"]] == ["Keep"]
    assert transcript["backend"] == "imported-captions"
    assert offline == {"decode": 1, "vad": 1}


def test_cached_speech_still_transcribes_once(offline, monkeypatch, tmp_path):
    import faster_whisper
    from types import SimpleNamespace

    snapshot_path = tmp_path / "cached-model"
    monkeypatch.setattr(transcribe, "resolve_cached_model", lambda model: snapshot_path)
    initializations = []

    class CachedModel:
        def __init__(self, path, **options):
            initializations.append((path, options["local_files_only"]))

        def transcribe(self, audio, **options):
            return iter([]), SimpleNamespace(duration=len(audio) / 16000, language="en")

    monkeypatch.setattr(faster_whisper, "WhisperModel", CachedModel)
    speech = str(Path(__file__).parent / "fixtures/speech.wav")
    result, _ = invoke("load", speech, "--no-frames", "--no-download")
    assert result.exit_code == 0, result.output
    assert initializations == [(str(snapshot_path), True)]
    assert offline == {"decode": 1, "vad": 1}


@pytest.mark.parametrize("mode", ["force", "add", "retranscribe"])
def test_failed_audio_check_is_structured_and_preserves_workspace(offline, silence, monkeypatch, mode):
    seed(silence)
    before = snapshot()

    def broken_decode(*args):
        raise RuntimeError("Cannot decode this input")

    monkeypatch.setattr(transcribe, "_load_audio", broken_decode)
    args = ["retranscribe"] if mode == "retranscribe" else ["load", silence, f"--{mode}", "--no-frames"]
    result, body = invoke(*args, "--no-download")
    assert result.exit_code == 1, result.output
    assert "Cannot decode this input" in body["error"]
    assert "requires_download" not in body
    assert snapshot() == before
