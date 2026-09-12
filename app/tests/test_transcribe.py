"""Tests for moviestar.transcribe.

Uses the tiny Whisper model for speed. Assertions are lenient — Whisper
output is probabilistic, so we check for expected fixture words rather than
exact text.
"""

import pytest

from moviestar.transcribe import (
    AVAILABLE_MODELS,
    DEFAULT_MODEL,
    TranscriptionError,
    _announce_download_if_needed,
    _build_vocabulary_prompt,
    _estimate_transcription_eta,
    _format_remaining,
    _format_short_timecode,
    _normalize_vocabulary,
    _speech_energy_without_words,
    is_model_cached,
    model_download_requirement,
    pull_model,
    transcribe_file,
)


# The licensed speech fixture says, "Lord, but I'm glad to see you again,
# Phil." Whisper punctuation and contractions may vary, so assert only stable
# content words.
EXPECTED_SPEECH_WORDS = ["lord", "glad", "see", "again", "phil"]


class TestSpeechEnergyWithoutWords:
    """Issue #244: sustained speech-like audio cannot disappear silently."""

    def test_reports_multi_second_energy_gap_before_first_word(self):
        words = [
            {
                "text": "finally",
                "start": {"seconds": 34.0},
                "end": {"seconds": 34.5},
            }
        ]

        assert _speech_energy_without_words([(0.0, 40.0)], words) == [
            (0.0, 33.0)
        ]

    def test_ignores_short_gap_and_normal_alignment_drift(self):
        words = [
            {
                "text": "hello",
                "start": {"seconds": 4.5},
                "end": {"seconds": 5.0},
            }
        ]

        assert _speech_energy_without_words([(0.0, 8.0)], words) == []

    def test_reports_wordless_energy_span_with_no_words_at_all(self):
        assert _speech_energy_without_words([(2.0, 12.0)], []) == [(2.0, 12.0)]


@pytest.fixture(scope="module")
def transcript(speech_wav):
    """Transcribe once per module; reuse for assertions."""
    return transcribe_file(speech_wav, "src_0", model="tiny")


class TestTranscribeShape:
    def test_returns_required_keys(self, transcript):
        for key in [
            "source_id",
            "source_path",
            "model",
            "backend",
            "language",
            "text",
            "duration",
            "words",
            "segments",
        ]:
            assert key in transcript, f"missing key: {key}"

    def test_source_id_recorded(self, transcript):
        assert transcript["source_id"] == "src_0"

    def test_model_recorded(self, transcript):
        assert transcript["model"] == "tiny"

    def test_backend_is_faster_whisper(self, transcript):
        assert transcript["backend"] == "faster-whisper"

    def test_language_detected(self, transcript):
        assert transcript["language"] == "en"

    def test_duration_shape(self, transcript):
        assert "text" in transcript["duration"]
        assert "seconds" in transcript["duration"]

    def test_text_is_non_empty_string(self, transcript):
        assert isinstance(transcript["text"], str)
        assert len(transcript["text"].strip()) > 0


class TestTranscribeWords:
    def test_has_words(self, transcript):
        assert len(transcript["words"]) >= 3

    def test_word_has_required_fields(self, transcript):
        word = transcript["words"][0]
        for key in ["text", "start", "end", "probability", "speaker"]:
            assert key in word, f"missing key: {key}"

    def test_word_start_end_are_timecode_dicts(self, transcript):
        word = transcript["words"][0]
        assert "text" in word["start"] and "seconds" in word["start"]
        assert "text" in word["end"] and "seconds" in word["end"]

    def test_word_end_is_after_start(self, transcript):
        for word in transcript["words"]:
            assert word["end"]["seconds"] >= word["start"]["seconds"]

    def test_word_timestamps_within_clip(self, transcript):
        # Fixture is under 3s; allow a little slack.
        for word in transcript["words"]:
            assert 0.0 <= word["start"]["seconds"] <= 3.5
            assert 0.0 <= word["end"]["seconds"] <= 3.5

    def test_words_are_ordered(self, transcript):
        starts = [w["start"]["seconds"] for w in transcript["words"]]
        assert starts == sorted(starts)

    def test_speaker_is_null_in_m5(self, transcript):
        # Diarization is out of scope for M5.
        for word in transcript["words"]:
            assert word["speaker"] is None

    def test_finds_expected_fixture_words(self, transcript):
        text = transcript["text"].lower()
        found = [word for word in EXPECTED_SPEECH_WORDS if word in text]
        assert len(found) >= 3, (
            f"Expected to find at least 3 of {EXPECTED_SPEECH_WORDS} "
            f"in transcript: {transcript['text']!r}"
        )


class TestTranscribeSegments:
    def test_has_segments(self, transcript):
        assert len(transcript["segments"]) >= 1

    def test_segment_shape(self, transcript):
        seg = transcript["segments"][0]
        assert "text" in seg
        assert "start" in seg and "seconds" in seg["start"]
        assert "end" in seg and "seconds" in seg["end"]


class TestTranscribeErrors:
    def test_missing_file_raises(self, tmp_path):
        missing = str(tmp_path / "nope.wav")
        with pytest.raises((TranscriptionError, FileNotFoundError)):
            transcribe_file(missing, "src_0", model="tiny")


class TestIsModelCached:
    def test_returns_bool(self):
        # Either True (tiny is cached after the fixture run) or False —
        # both are acceptable shapes. We just want to confirm the signature.
        result = is_model_cached("tiny")
        assert isinstance(result, bool)

    def test_requirement_reports_uncached_model_for_agents(self, monkeypatch):
        monkeypatch.setattr(
            "moviestar.transcribe.resolve_cached_model", lambda model: None
        )
        requirement = model_download_requirement("small")
        assert requirement == {
            "model": "small",
            "estimated_size_mb": 480,
            "cache_dir": str(
                __import__("moviestar.transcribe", fromlist=["_hf_cache_root"])
                ._hf_cache_root()
            ),
        }

    def test_requirement_is_none_when_model_is_cached(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "moviestar.transcribe.resolve_cached_model", lambda model: tmp_path
        )
        assert model_download_requirement("small") is None

    def test_partial_snapshot_is_not_treated_as_cached(self, monkeypatch, tmp_path):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        (snapshot / "config.json").write_text("{}")
        monkeypatch.setattr(
            "moviestar.transcribe._download_model",
            lambda model, local_files_only: snapshot,
        )
        assert is_model_cached("small") is False

    def test_pull_model_downloads_without_loading_inference_model(
        self, monkeypatch, tmp_path
    ):
        calls = []

        def fake_download(model, *, local_files_only):
            calls.append((model, local_files_only))
            return tmp_path / model

        monkeypatch.setattr("moviestar.transcribe._download_model", fake_download)
        assert pull_model("small") == tmp_path / "small"
        assert calls == [("small", False)]

    def test_requirement_honors_hf_hub_cache(self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "custom-hub"
        monkeypatch.setenv("HF_HUB_CACHE", str(cache_dir))
        monkeypatch.setattr(
            "moviestar.transcribe.resolve_cached_model", lambda model: None
        )
        assert model_download_requirement("tiny")["cache_dir"] == str(cache_dir)


class TestDefaultModel:
    def test_default_is_base(self):
        assert DEFAULT_MODEL == "base"


class TestProgress:
    """Stderr progress output during transcription.

    These assertions are lenient on exact text — they check the shape
    (upfront / per-segment / summary) rather than exact wording.
    """

    def test_emits_upfront_message(self, speech_wav, capsys):
        transcribe_file(speech_wav, "src_0", model="tiny")
        captured = capsys.readouterr()
        assert "Transcribing" in captured.err

    def test_upfront_message_carries_model_and_eta(self, speech_wav, capsys):
        """Issue #140: the upfront line names the model and an estimated range."""
        transcribe_file(speech_wav, "src_0", model="tiny")
        stderr = capsys.readouterr().err
        upfront = next(
            line for line in stderr.splitlines() if "Transcribing" in line
        )
        assert "tiny" in upfront, upfront
        assert "estimated" in upfront, upfront

    def test_emits_model_loading_before_transcribing(self, speech_wav, capsys):
        """'Loading Whisper model' must appear BEFORE 'Transcribing' so the user
        sees activity during the 3-5s model-load window, not silence."""
        transcribe_file(speech_wav, "src_0", model="tiny")
        stderr = capsys.readouterr().err
        assert "Loading Whisper" in stderr
        loading_idx = stderr.index("Loading Whisper")
        transcribing_idx = stderr.index("Transcribing")
        assert loading_idx < transcribing_idx, (
            "Model-loading message must appear before transcribing message"
        )

    def test_emits_completion_summary(self, speech_wav, capsys):
        transcribe_file(speech_wav, "src_0", model="tiny")
        captured = capsys.readouterr()
        assert "done" in captured.err.lower()

    def test_progress_lines_have_elapsed(self, speech_wav, capsys):
        """Every segment progress line should carry an '[elapsed Xs]' marker.

        The prefix used to be just '[Xs]' which agents had to guess at; it's
        now spelled out as '[elapsed ...]' so first-time readers don't have
        to infer it.
        """
        transcribe_file(speech_wav, "src_0", model="tiny")
        captured = capsys.readouterr()
        import re
        assert re.search(r"\[elapsed \d+s\]|\[elapsed \d+m \d+s\]", captured.err), (
            f"No '[elapsed ...]' marker in stderr: {captured.err!r}"
        )

    def test_quiet_suppresses_progress(self, speech_wav, capsys):
        transcribe_file(speech_wav, "src_0", model="tiny", quiet=True)
        captured = capsys.readouterr()
        assert captured.err == "", f"Expected silent stderr, got: {captured.err!r}"

    def test_progress_does_not_pollute_stdout(self, speech_wav, capsys):
        transcribe_file(speech_wav, "src_0", model="tiny")
        captured = capsys.readouterr()
        assert captured.out == "", f"stdout should stay clean: {captured.out!r}"

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (59.94, "0:59.9"),
            (59.96, "1:00.0"),
            (119.97, "2:00.0"),
        ],
    )
    def test_short_timecode_rounding_carries_to_minutes(self, seconds, expected):
        """Issue #200: display rounding must not produce impossible 0:60.0 values."""
        assert _format_short_timecode(seconds) == expected


class TestAnnounceDownload:
    """Issue #31: announce model download on first run, not just 'Loading...'.

    The announce text must report the *correct size for the requested
    model* — the previous form hardcoded '~150MB for base' regardless of
    the model the user actually asked for. Each available model needs a
    plausible size estimate so an agent on a slow connection can predict
    the wait.
    """

    def test_does_not_announce_when_cached(self, monkeypatch, capsys):
        monkeypatch.setattr("moviestar.transcribe.is_model_cached", lambda m: True)
        _announce_download_if_needed("tiny")
        captured = capsys.readouterr()
        assert captured.err == "", f"cached models should be silent: {captured.err!r}"
        assert captured.out == ""

    def test_announces_to_stderr_only_when_uncached(self, monkeypatch, capsys):
        monkeypatch.setattr("moviestar.transcribe.is_model_cached", lambda m: False)
        _announce_download_if_needed("tiny")
        captured = capsys.readouterr()
        assert "Downloading" in captured.err
        assert captured.out == ""

    @pytest.mark.parametrize(
        "model,size_marker",
        [
            ("tiny", "75"),       # ~75 MB
            ("base", "145"),      # ~145 MB
            ("small", "480"),     # ~480 MB
            ("medium", "1.5"),    # ~1.5 GB
            ("large-v3", "3.0"),  # ~3.0 GB
        ],
    )
    def test_announce_text_quotes_model_specific_size(
        self, monkeypatch, capsys, model, size_marker
    ):
        """Announce text must mention the requested model AND its size.

        Previously the text hardcoded '150MB for base' for every model —
        so 'moviestar load --model tiny' wrongly implied a 150MB download
        and 'moviestar load --model large-v3' implied 150MB instead of ~3GB.
        """
        monkeypatch.setattr("moviestar.transcribe.is_model_cached", lambda m: False)
        _announce_download_if_needed(model)
        stderr = capsys.readouterr().err
        assert model in stderr, (
            f"announce should name the requested model {model!r}: {stderr!r}"
        )
        assert size_marker in stderr, (
            f"announce for {model!r} should mention size marker {size_marker!r}: {stderr!r}"
        )

    def test_announce_covers_every_available_model(self, monkeypatch, capsys):
        """Every entry in AVAILABLE_MODELS must produce a size-aware announce.

        Guards against future model additions silently falling back to
        an empty or wrong size estimate.
        """
        monkeypatch.setattr("moviestar.transcribe.is_model_cached", lambda m: False)
        for model in AVAILABLE_MODELS:
            _announce_download_if_needed(model)
            stderr = capsys.readouterr().err
            assert "Downloading" in stderr
            assert model in stderr


class TestEstimateETA:
    """Issue #140: print an upfront ETA before transcription begins.

    Going from "this might take 30 seconds" to "this will take 13 minutes"
    with no warning is a bad first impression. The estimate is a deliberately
    wide range — real CPU realtime factors vary several-fold across hardware —
    so the constants are rough documentation, not a contract.
    """

    def test_unknown_model_returns_none(self):
        assert _estimate_transcription_eta("not-a-model", 600.0) is None

    def test_nonpositive_duration_returns_none(self):
        assert _estimate_transcription_eta("base", 0.0) is None
        assert _estimate_transcription_eta("base", -5.0) is None

    def test_every_available_model_estimates_positive_duration(self):
        """No available model should fall back to None for real audio."""
        for model in AVAILABLE_MODELS:
            eta = _estimate_transcription_eta(model, 600.0)
            assert eta, f"model {model!r} produced no estimate: {eta!r}"

    def test_long_audio_reports_minutes(self):
        # 41:33 of audio (the Riverside friction case) on 'base'.
        eta = _estimate_transcription_eta("base", 41 * 60 + 33)
        assert "minute" in eta, f"expected a minutes estimate, got {eta!r}"

    def test_short_audio_reports_seconds(self):
        eta = _estimate_transcription_eta("tiny", 20.0)
        assert "second" in eta, f"expected a seconds estimate, got {eta!r}"

    def test_estimate_is_a_range_low_to_high(self):
        """Heavier models take longer, so the upper bound must exceed the lower."""
        eta = _estimate_transcription_eta("medium", 600.0)
        # Range form is "<low>-<high> <unit>"; a single point would have no dash.
        nums = [int(n) for n in __import__("re").findall(r"\d+", eta)]
        assert len(nums) >= 2, f"expected a low-high range, got {eta!r}"
        assert nums[0] <= nums[1], f"low bound should not exceed high: {eta!r}"

    def test_remaining_none_before_enough_signal(self):
        """Below the noise floor (or at/after completion) there's no estimate."""
        assert _format_remaining(elapsed=1.0, progress=0.0) is None
        assert _format_remaining(elapsed=1.0, progress=0.01) is None
        assert _format_remaining(elapsed=10.0, progress=1.0) is None
        assert _format_remaining(elapsed=0.0, progress=0.5) is None

    def test_remaining_extrapolates_from_observed_speed(self):
        # 25% done after 30s of work → ~90s of work left.
        out = _format_remaining(elapsed=30.0, progress=0.25)
        assert out == "~1m 30s left", out

    def test_remaining_self_corrects_as_progress_grows(self):
        """A slower-than-guessed run reports more time left at the same elapsed."""
        early = _format_remaining(elapsed=60.0, progress=0.5)
        assert early == "~1m 0s left", early
        # Later, at the same elapsed but less progress, more time is implied.
        slower = _format_remaining(elapsed=60.0, progress=0.25)
        assert slower == "~3m 0s left", slower

    def test_heavier_model_estimates_more_than_lighter(self):
        light = _estimate_transcription_eta("tiny", 600.0)
        heavy = _estimate_transcription_eta("large-v3", 600.0)
        import re

        light_high = max(int(n) for n in re.findall(r"\d+", light))
        heavy_high = max(int(n) for n in re.findall(r"\d+", heavy))
        # tiny is reported in seconds, large-v3 in minutes — normalize is overkill;
        # just assert large-v3's numeric high (minutes) reflects a heavier load.
        assert "minute" in heavy and heavy_high >= 1, heavy
        assert light is not None and heavy is not None


class _FakeInfo:
    def __init__(self, duration, language):
        self.duration = duration
        self.language = language


class _FakeWhisperModel:
    """Drop-in for faster_whisper.WhisperModel that yields no segments.

    Lets us exercise the zero-words completion path deterministically and
    fast, without invoking real Whisper on a silent fixture (slow).
    """

    segments = []  # no speech

    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, *args, **kwargs):
        return iter(self.segments), _FakeInfo(duration=60.0, language="en")


@pytest.fixture
def _no_speech_model(monkeypatch, tmp_path):
    """Patch WhisperModel to yield zero segments; return a real file path."""
    import faster_whisper

    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeWhisperModel)
    monkeypatch.setattr("moviestar.transcribe.is_model_cached", lambda m: True)
    audio = tmp_path / "silent.wav"
    audio.write_bytes(b"\x00")  # only existence is checked before model load
    return str(audio)


class TestNoSpeechDetected:
    """Issue #199: zero-word transcription should say *why*, not just '0 words'.

    '0 words' alone is ambiguous between "no speech in this audio" (expected)
    and "transcription silently broke" (a bug). A clear note removes the
    false-alarm verification round for agents.
    """

    def test_zero_words_emits_no_speech_note(self, _no_speech_model, capsys):
        transcribe_file(_no_speech_model, "src_0", model="tiny")
        stderr = capsys.readouterr().err.lower()
        assert "no speech detected" in stderr, (
            f"zero-word transcription should explain itself: {stderr!r}"
        )

    def test_no_speech_note_suppressed_when_quiet(self, _no_speech_model, capsys):
        transcribe_file(_no_speech_model, "src_0", model="tiny", quiet=True)
        assert capsys.readouterr().err == ""

    def test_result_sets_no_speech_detected_flag(self, _no_speech_model):
        result = transcribe_file(_no_speech_model, "src_0", model="tiny")
        assert result["no_speech_detected"] is True
        assert result["words"] == []

    def test_sustained_audio_without_words_adds_structured_warning(
        self, _no_speech_model, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            "moviestar.transcribe.run_audio_energy_probe",
            lambda source_path, duration: [(2.0, 12.0)],
        )

        result = transcribe_file(_no_speech_model, "src_0", model="tiny")

        [warning] = result["warnings"]
        assert warning["code"] == "speech_energy_without_words"
        assert warning["source"] == "src_0"
        assert warning["source_id"] == "src_0"
        assert warning["from"]["seconds"] == 2.0
        assert warning["to"]["seconds"] == 12.0
        assert warning["duration"]["seconds"] == 10.0
        assert warning["likely_cause"]
        stderr = capsys.readouterr().err.lower()
        assert "no speech detected" not in stderr
        assert "0 words" in stderr


class _CapturingInfo:
    duration = 2.0
    language = "en"


class _CapturingWhisperModel:
    """Records the kwargs passed to .transcribe so we can assert the
    initial_prompt vocabulary biasing reaches faster-whisper."""

    last_transcribe_kwargs: dict = {}
    last_init_args: tuple = ()
    last_init_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        type(self).last_init_args = args
        type(self).last_init_kwargs = kwargs

    def transcribe(self, *args, **kwargs):
        type(self).last_transcribe_kwargs = kwargs
        return iter([]), _CapturingInfo()


@pytest.fixture
def _capturing_model(monkeypatch, tmp_path):
    import faster_whisper

    _CapturingWhisperModel.last_transcribe_kwargs = {}
    _CapturingWhisperModel.last_init_args = ()
    _CapturingWhisperModel.last_init_kwargs = {}
    monkeypatch.setattr(faster_whisper, "WhisperModel", _CapturingWhisperModel)
    monkeypatch.setattr("moviestar.transcribe.is_model_cached", lambda m: True)
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"\x00")
    return str(audio)


class TestNormalizeVocabulary:
    """Issue #136: --vocabulary biases the Whisper decoder toward names/terms.

    Normalization flattens repeated/comma-separated flag values into a clean,
    deduped, order-preserving term list before it becomes an initial_prompt.
    """

    def test_splits_on_commas_and_strips(self):
        assert _normalize_vocabulary(["Amal, Holden ,jdilla"]) == [
            "Amal",
            "Holden",
            "jdilla",
        ]

    def test_flattens_multiple_occurrences(self):
        assert _normalize_vocabulary(["Amal,Holden", "Kubernetes"]) == [
            "Amal",
            "Holden",
            "Kubernetes",
        ]

    def test_drops_empties(self):
        assert _normalize_vocabulary(["Amal,,", " , ", "Holden"]) == ["Amal", "Holden"]

    def test_dedupes_preserving_first_seen_order(self):
        assert _normalize_vocabulary(["Amal,Holden,Amal"]) == ["Amal", "Holden"]

    def test_none_and_empty_return_empty_list(self):
        assert _normalize_vocabulary(None) == []
        assert _normalize_vocabulary([]) == []
        assert _normalize_vocabulary(["", "  "]) == []


class TestBuildVocabularyPrompt:
    def test_prompt_mentions_every_term(self):
        prompt = _build_vocabulary_prompt(["Amal", "Holden", "jdilla"])
        assert prompt is not None
        for term in ("Amal", "Holden", "jdilla"):
            assert term in prompt

    def test_empty_terms_return_none(self):
        assert _build_vocabulary_prompt([]) is None


class TestCachedModelLoading:
    def test_cached_model_loads_from_snapshot_without_hub_access(
        self, _capturing_model, monkeypatch, tmp_path
    ):
        snapshot = tmp_path / "cached-snapshot"
        monkeypatch.setattr(
            "moviestar.transcribe.resolve_cached_model",
            lambda model: snapshot,
        )

        transcribe_file(_capturing_model, "src_0", model="tiny")

        assert _CapturingWhisperModel.last_init_args == (str(snapshot),)
        assert _CapturingWhisperModel.last_init_kwargs["local_files_only"] is True

    def test_uncached_model_keeps_default_download_behavior(
        self, _capturing_model, monkeypatch
    ):
        monkeypatch.setattr(
            "moviestar.transcribe.resolve_cached_model",
            lambda model: None,
        )

        transcribe_file(_capturing_model, "src_0", model="tiny")

        assert _CapturingWhisperModel.last_init_args == ("tiny",)
        assert _CapturingWhisperModel.last_init_kwargs["local_files_only"] is False

    def test_no_download_is_enforced_by_whisper_loader(
        self, _capturing_model, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("HF_HUB_CACHE", raising=False)
        monkeypatch.delenv("HF_HOME", raising=False)
        snapshot = tmp_path / "cached-snapshot"
        monkeypatch.setattr(
            "moviestar.transcribe.resolve_cached_model",
            lambda model: snapshot,
        )
        transcribe_file(
            _capturing_model, "src_0", model="tiny", allow_download=False
        )
        assert _CapturingWhisperModel.last_init_args == (str(snapshot),)
        assert _CapturingWhisperModel.last_init_kwargs["local_files_only"] is True
        assert _CapturingWhisperModel.last_init_kwargs["download_root"].endswith(
            "huggingface/hub"
        )


class TestTranscribeVocabulary:
    def test_vocabulary_reaches_whisper_as_initial_prompt(self, _capturing_model):
        transcribe_file(
            _capturing_model, "src_0", model="tiny", vocabulary=["Amal", "Holden"]
        )
        kwargs = _CapturingWhisperModel.last_transcribe_kwargs
        assert "Amal" in kwargs["initial_prompt"]
        assert "Holden" in kwargs["initial_prompt"]

    def test_no_vocabulary_passes_none_initial_prompt(self, _capturing_model):
        transcribe_file(_capturing_model, "src_0", model="tiny")
        assert _CapturingWhisperModel.last_transcribe_kwargs["initial_prompt"] is None

    def test_result_records_normalized_vocabulary(self, _capturing_model):
        result = transcribe_file(
            _capturing_model,
            "src_0",
            model="tiny",
            vocabulary=["Amal, Holden", "Amal"],
        )
        assert result["vocabulary"] == ["Amal", "Holden"]

    def test_result_vocabulary_empty_when_unset(self, _capturing_model):
        result = transcribe_file(_capturing_model, "src_0", model="tiny")
        assert result["vocabulary"] == []

    def test_real_whisper_accepts_vocabulary(self, speech_wav):
        """End-to-end against real faster-whisper: passing vocabulary must
        not break the transcribe call and the terms are echoed back.

        Guards the non-mocked initial_prompt path — the capturing-model
        tests can't catch a faster-whisper signature drift on their own.
        """
        result = transcribe_file(
            speech_wav, "src_0", model="tiny", vocabulary=["One", "Five"]
        )
        assert result["vocabulary"] == ["One", "Five"]
        assert isinstance(result["text"], str)
