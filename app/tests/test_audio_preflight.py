"""Audio checks before transcription (issues #18 and #19).

Out-of-phase stereo cancels speech when Whisper's input is mixed down to
mono, and Whisper invents words on audio without speech. Moviestar now
measures stereo phase and voice activity first: it transcribes one channel
when the mix would cancel, skips Whisper when there is no speech, and warns
when most transcript words land outside detected speech.
"""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import run_stereo_phase_probe
from moviestar.transcribe import (
    TRANSCRIPTION_CHANNELS,
    _choose_transcription_channel,
    transcribe_file,
)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


@pytest.fixture
def media(tmp_path, speech_wav) -> dict[str, str]:
    """Speech in phase / out of phase, clicks, noise, and mono speech."""
    paths = {
        name: str(tmp_path / f"{name}.wav")
        for name in ("inphase", "antiphase", "clicks", "noise", "mono")
    }
    _ffmpeg("-i", speech_wav, "-af", "pan=stereo|c0=c0|c1=c0", paths["inphase"])
    # The right channel is quieter so channel choice is observable.
    _ffmpeg(
        "-i", speech_wav, "-af", "pan=stereo|c0=c0|c1=-0.5*c0", paths["antiphase"]
    )
    _ffmpeg(
        "-f", "lavfi", "-i",
        "aevalsrc='if(lt(mod(t\\,1)\\,0.004)\\,0.8*sin(2*PI*2000*t)\\,0)'"
        ":s=16000:d=20",
        "-ac", "2", paths["clicks"],
    )
    _ffmpeg(
        "-f", "lavfi", "-i", "anoisesrc=d=5:c=white:a=0.3:seed=1",
        "-f", "lavfi", "-i", "anoisesrc=d=5:c=white:a=0.3:seed=2",
        "-filter_complex", "[0:a][1:a]join=inputs=2:channel_layout=stereo",
        paths["noise"],
    )
    _ffmpeg("-i", speech_wav, "-ac", "1", paths["mono"])
    return paths


class TestStereoPhaseProbe:
    def test_in_phase_speech_correlates(self, media):
        probe = run_stereo_phase_probe(media["inphase"])
        assert probe["correlation"] == pytest.approx(1.0, abs=0.01)

    def test_out_of_phase_speech_anticorrelates(self, media):
        probe = run_stereo_phase_probe(media["antiphase"])
        assert probe["correlation"] < -0.5
        assert probe["left_rms_db"] > probe["right_rms_db"]

    def test_independent_channels_are_uncorrelated(self, media):
        probe = run_stereo_phase_probe(media["noise"])
        assert abs(probe["correlation"]) < 0.2

    def test_mono_has_no_stereo_phase(self, media):
        assert run_stereo_phase_probe(media["mono"]) is None


class TestChannelChoice:
    def test_channels_are_named(self):
        assert TRANSCRIPTION_CHANNELS == ("auto", "mix", "left", "right")

    @pytest.mark.parametrize(
        ("requested", "phase", "expected"),
        [
            ("auto", None, "mix"),
            ("auto", {"correlation": 0.4, "left_rms_db": -20, "right_rms_db": -20}, "mix"),
            ("auto", {"correlation": -0.93, "left_rms_db": -18, "right_rms_db": -24}, "left"),
            ("auto", {"correlation": -0.93, "left_rms_db": -30, "right_rms_db": -18}, "right"),
            ("mix", {"correlation": -1.0, "left_rms_db": -18, "right_rms_db": -18}, "mix"),
            ("right", None, "right"),
        ],
    )
    def test_auto_transcribes_the_louder_channel_only_when_out_of_phase(
        self, requested, phase, expected
    ):
        assert _choose_transcription_channel(requested, phase) == expected


class _WordsModel:
    """Fake WhisperModel yielding fixed words; records what it was given."""

    words: list[tuple[str, float, float]] = []
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio, **kwargs):
        type(self).calls.append(audio)

        class _Word:
            def __init__(self, text, start, end):
                self.word, self.start, self.end, self.probability = text, start, end, 0.9

        class _Segment:
            def __init__(self, words):
                self.words = [_Word(*w) for w in words]
                self.text = " ".join(w[0] for w in words)
                self.start = words[0][1] if words else 0.0
                self.end = words[-1][2] if words else 0.0

        class _Info:
            duration = float(len(audio)) / 16000 if hasattr(audio, "__len__") else 0.0
            language = "en"

        segments = [_Segment(self.words)] if self.words else []
        return iter(segments), _Info()


@pytest.fixture
def words_model(monkeypatch):
    import faster_whisper

    _WordsModel.words = []
    _WordsModel.calls = []
    monkeypatch.setattr(faster_whisper, "WhisperModel", _WordsModel)
    monkeypatch.setattr("moviestar.transcribe.is_model_cached", lambda m: True)
    monkeypatch.setattr(
        "moviestar.transcribe.resolve_cached_model", lambda m: None
    )
    return _WordsModel


class TestSpeechCheck:
    def test_no_speech_skips_whisper_and_says_why(self, media, words_model):
        words_model.words = [("see", 1.0, 1.2), ("you", 1.3, 1.5)]

        result = transcribe_file(media["clicks"], "src_0", model="tiny", quiet=True)

        assert words_model.calls == []
        assert result["no_speech_detected"] is True
        assert result["words"] == []
        codes = [w["code"] for w in result["warnings"]]
        assert codes == ["no_speech_in_audio"]
        assert "--no-speech-check" in result["warnings"][0]["remedy"]
        assert result["audio"]["speech_check"]["whisper_skipped"] is True
        assert result["duration"]["seconds"] == pytest.approx(20.0, abs=0.1)

    def test_short_clip_that_is_all_speech_still_transcribes(
        self, speech_wav, words_model, tmp_path
    ):
        # A sub-half-second reply is entirely speech; the absolute floor
        # for "no speech" must not swallow it.
        short = str(tmp_path / "short.wav")
        _ffmpeg("-ss", "0.6", "-t", "0.45", "-i", speech_wav, short)
        words_model.words = [("glad", 0.1, 0.4)]

        result = transcribe_file(short, "src_0", model="tiny", quiet=True)

        assert len(words_model.calls) == 1
        assert result["no_speech_detected"] is False
        assert result["audio"]["speech_check"]["whisper_skipped"] is False

    def test_speech_check_can_be_disabled(self, media, words_model):
        result = transcribe_file(
            media["clicks"], "src_0", model="tiny", quiet=True, speech_check=False
        )
        assert len(words_model.calls) == 1
        assert result["audio"]["speech_check"] == {"enabled": False}

    def test_most_words_outside_speech_are_flagged(
        self, media, words_model, tmp_path
    ):
        mixed = str(tmp_path / "speech_then_clicks.wav")
        _ffmpeg(
            "-i", media["inphase"], "-i", media["clicks"],
            "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1", mixed,
        )
        # Speech ends near 2.6 s; these words sit in the clicks.
        words_model.words = [
            (f"word{i}", 4.0 + i, 4.3 + i) for i in range(12)
        ]

        result = transcribe_file(mixed, "src_0", model="tiny", quiet=True)

        flagged = [
            w for w in result["warnings"]
            if w["code"] == "words_outside_detected_speech"
        ]
        assert len(flagged) == 1
        assert flagged[0]["words_outside_speech"] == 12
        assert flagged[0]["words_total"] == 12


class TestChannelSelection:
    def test_out_of_phase_audio_transcribes_the_louder_channel(
        self, media, words_model
    ):
        words_model.words = [("glad", 1.0, 1.3)]

        result = transcribe_file(media["antiphase"], "src_0", model="tiny", quiet=True)

        [audio] = words_model.calls
        # The fake sees the left channel alone, not a cancelled mix.
        assert float(np.abs(audio).max()) > 0.1
        assert result["audio"]["channel"]["used"] == "left"
        assert result["audio"]["channel"]["stereo_correlation"] < -0.5
        [warning] = [
            w for w in result["warnings"]
            if w["code"] == "audio_channels_out_of_phase"
        ]
        assert warning["channel_used"] == "left"
        assert "--channel" in warning["remedy"]

    def test_explicit_mix_keeps_the_mixdown(self, media, words_model):
        transcribe_file(
            media["antiphase"], "src_0", model="tiny", quiet=True,
            channel="mix", speech_check=False,
        )
        from faster_whisper.audio import decode_audio

        [audio] = words_model.calls
        speech_peak = float(np.abs(decode_audio(media["mono"])).max())
        # Left and half-inverted right partly cancel: about a quarter
        # of the speech level survives the mixdown.
        assert float(np.abs(audio).max()) < 0.5 * speech_peak

    def test_real_whisper_recovers_out_of_phase_speech(self, media):
        result = transcribe_file(media["antiphase"], "src_0", model="tiny", quiet=True)
        text = result["text"].lower()
        assert "glad" in text
        assert result["audio"]["channel"]["used"] == "left"


class TestCliFlags:
    def _video(self, tmp_path: Path, audio: str) -> str:
        out = str(tmp_path / "clip.mp4")
        _ffmpeg(
            "-f", "lavfi", "-i", "color=c=black:s=160x120:r=30:d=3",
            "-i", audio, "-shortest", "-c:v", "libx264", "-c:a", "aac", out,
        )
        return out

    def test_load_and_retranscribe_pass_channel_and_speech_check(
        self, media, words_model, tmp_path, monkeypatch
    ):
        words_model.words = [("glad", 1.0, 1.3)]
        video = self._video(tmp_path, media["antiphase"])
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        loaded = runner.invoke(
            cli, ["load", video, "--as", "src_0", "--no-frames", "--channel", "right"]
        )
        assert loaded.exit_code == 0, loaded.stdout
        transcript = json.loads(
            (tmp_path / "moviestar" / "transcripts" / "src_0.json").read_text()
        )
        assert transcript["audio"]["channel"] == {
            "requested": "right", "used": "right", "stereo_correlation": None,
        }

        redone = runner.invoke(
            cli, ["retranscribe", "--channel", "auto", "--no-speech-check", "--quiet"]
        )
        assert redone.exit_code == 0, redone.stdout
        data = json.loads(redone.stdout)
        codes = [w["code"] for w in data.get("warnings", [])]
        assert "audio_channels_out_of_phase" in codes
        transcript = json.loads(
            (tmp_path / "moviestar" / "transcripts" / "src_0.json").read_text()
        )
        assert transcript["audio"]["channel"]["used"] == "left"
        assert transcript["audio"]["speech_check"] == {"enabled": False}

    def test_invalid_channel_is_a_usage_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["retranscribe", "--channel", "center"])
        assert result.exit_code != 0
        assert "center" in result.stdout
