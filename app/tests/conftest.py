"""Shared pytest fixtures + slow-test gating."""

import json
import subprocess
from pathlib import Path

import pytest


# -------------------- Slow-test gating --------------------
#
# Whisper-dependent tests are slow (~3-15s each). They're skipped by default
# so `pytest` finishes in seconds for normal dev iteration. CI runs the
# full suite via `pytest --run-slow`.
#
# Auto-marking keeps this robust: any test that uses the speech_wav /
# speech_video fixtures is treated as slow without an explicit decorator.
# New whisper-dependent tests get picked up automatically.

_WHISPER_FIXTURE_NAMES = {"speech_wav", "speech_video"}


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="run slow tests (real faster-whisper runs)",
    )


def pytest_collection_modifyitems(config, items):
    # Auto-mark whisper-dependent tests as slow
    for item in items:
        if _WHISPER_FIXTURE_NAMES & set(item.fixturenames):
            item.add_marker(pytest.mark.slow)

    if config.getoption("--run-slow"):
        return

    skip_slow = pytest.mark.skip(
        reason="slow test (whisper); pass --run-slow to include"
    )
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


# -------------------- Fixtures --------------------


@pytest.fixture(autouse=True)
def _fresh_ffmpeg_capability_cache():
    """Isolate the per-process ``ffmpeg -filters`` cache between tests.

    ``ffmpeg_filter_available`` memoizes the listing (issue #358); tests
    that fake ``subprocess.run`` must never leak a synthetic listing
    into — or inherit a real one from — a neighboring test.
    """
    from moviestar import ffmpeg as _ffmpeg

    _ffmpeg.clear_ffmpeg_capability_cache()
    yield
    _ffmpeg.clear_ffmpeg_capability_cache()


FIXTURES_DIR = Path(__file__).parent / "fixtures"
SPEECH_WAV_PATH = FIXTURES_DIR / "speech.wav"


@pytest.fixture(scope="session")
def speech_wav() -> str:
    """Path to the licensed 2.6s mono 16kHz speech fixture.

    Checked in with its source provenance and license so tests are
    deterministic without depending on a local TTS engine.
    """
    assert SPEECH_WAV_PATH.exists(), f"fixture missing: {SPEECH_WAV_PATH}"
    return str(SPEECH_WAV_PATH)


@pytest.fixture(scope="session")
def speech_video(tmp_path_factory, speech_wav) -> str:
    """Generate a short video that muxes the speech fixture with a testsrc2 visual."""
    path = tmp_path_factory.mktemp("fixtures") / "speech.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=3:size=320x240:rate=30",
            "-i", speech_wav,
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.fixture(scope="session")
def test_video(tmp_path_factory) -> str:
    """Generate a 2-second 320x240 test video with audio. Cached per session."""
    path = tmp_path_factory.mktemp("fixtures") / "probe_test.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=2:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.fixture(scope="session")
def test_video_60fps(tmp_path_factory) -> str:
    """Generate a 2-second 320x240 60fps test video with audio."""
    path = tmp_path_factory.mktemp("fixtures") / "probe_test_60fps.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=2:size=320x240:rate=60",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.fixture(scope="session")
def silent_video(tmp_path_factory) -> str:
    """Generate a 1-second 160x120 silent test video."""
    path = tmp_path_factory.mktemp("fixtures") / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=1:size=160x120:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-an",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.fixture(scope="session")
def vfr_gap_video(tmp_path_factory) -> str:
    """Generate a 10s VFR video with a frameless gap from ~2s to ~7s.

    Models a macOS screen recording, which emits no frames while the
    screen is static. `select` drops every frame in the window and
    `-fps_mode vfr` preserves the resulting timestamp gap instead of
    re-spacing the survivors. Frames exist ~0-1.97s and ~7.03-10s;
    pre-gap frames are red and post-gap frames are blue so tests can
    distinguish which side ffmpeg uses for a lead-in freeze.
    """
    path = tmp_path_factory.mktemp("fixtures") / "vfr_gap.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=red:duration=10:size=320x240:rate=30",
            "-vf",
            (
                "drawbox=x=0:y=0:w=320:h=240:color=blue:t=fill:"
                "enable='gte(t,7)',select='not(between(t,2,7))'"
            ),
            "-fps_mode", "vfr",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-an",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.fixture(scope="session")
def vfr_delayed_first_frame_video(tmp_path_factory) -> str:
    """Generate VFR media whose first video frame is delayed until 3s."""
    path = tmp_path_factory.mktemp("fixtures") / "vfr_delayed_first_frame.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=blue:duration=7:size=320x240:rate=30",
            "-vf", "setpts=PTS+3/TB",
            "-fps_mode", "vfr",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-an",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.fixture(scope="session")
def audio_only_file(tmp_path_factory) -> str:
    """Generate a 1-second audio-only WAV file."""
    path = tmp_path_factory.mktemp("fixtures") / "audio_only.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            str(path),
        ],
        check=True,
    )
    return str(path)


# -------------------- Loaded-project fixture --------------------
#
# M13a retro action item: the per-test `_load_*` helpers in test_cli.py
# (15+ of them) all reinvent `moviestar load <video> --as src_0
# --interval 1.0 --no-transcribe`. When a fixture filename changes or
# `derive_source_id` runs over the path, the auto-derived ID quietly
# drifts (`probe_test.mp4` → `probe`, `speech.wav` → `speech`) and
# tests that hardcoded `src_0` start failing. Two M13a CI cycles
# burned on exactly this pattern.
#
# Prefer this fixture in new tests over hand-rolled `_load` helpers.
# It always passes `--as src_<n>` so source IDs stay deterministic
# regardless of the fixture filename.


@pytest.fixture
def loaded_project(tmp_path, monkeypatch):
    """Load one or more videos into a tmp workspace with stable source IDs.

    Returns a callable. Tests use it like::

        def test_something(loaded_project, test_video):
            project = loaded_project(test_video)            # one source, id "src_0"
            project = loaded_project([a, b])                # two sources: "src_0", "src_1"
            project = loaded_project(a, names=["holden"])   # explicit ids
            project = loaded_project(a, transcribe=True, model="tiny")

    Defaults: ``--no-transcribe``, ``--interval 1.0``, deterministic
    ``--as src_0/src_1/...`` so tests don't depend on
    ``derive_source_id``'s filename-coupled output. Returns the parsed
    ``load`` envelope.
    """
    from click.testing import CliRunner

    from moviestar.cli import cli as _cli

    runner = CliRunner()

    def _load(
        video,
        *,
        names=None,
        interval: float = 1.0,
        transcribe: bool = False,
        model: str | None = None,
        frames: bool = True,
        cwd=None,
    ) -> dict:
        videos = [video] if isinstance(video, (str, Path)) else list(video)
        videos = [str(v) for v in videos]
        if names is None:
            names = [f"src_{i}" for i in range(len(videos))]
        elif len(names) != len(videos):
            raise ValueError(
                f"len(names)={len(names)} != len(videos)={len(videos)}"
            )
        monkeypatch.chdir(cwd or tmp_path)
        cmd = ["load", *videos]
        for n in names:
            cmd += ["--as", n]
        cmd += ["--interval", str(interval)]
        if not transcribe:
            cmd += ["--no-transcribe"]
        elif model:
            cmd += ["--model", model]
        if not frames:
            cmd += ["--no-frames"]
        result = runner.invoke(_cli, cmd)
        assert result.exit_code == 0, f"load failed: {result.stdout}"
        return json.loads(result.stdout)

    return _load


# -------------------- Shared assertion helpers --------------------
#
# 04-30 retro action item: tests anchor on structured envelope keys,
# not prose tokens. These helpers centralize the envelope-shape checks
# the #27 / #78 conventions require so per-command tests don't have
# to re-derive the structure each time. Locks new commands into the
# convention as soon as they ship a test through these helpers.


def assert_error_envelope(
    data: dict,
    *,
    command: str,
    expect_hint: bool = True,
) -> None:
    """Assert the canonical error-envelope shape from issues #27 / #78.

    Every error response carries:
      - ``command``: the CLI verb that failed.
      - ``error``: a non-empty plain-language description of what went
        wrong. Stays remediation-free per the convention.
      - ``hint`` (when ``expect_hint=True``): a non-empty actionable
        next step. Optional only for purely-descriptive errors with no
        meaningful remediation (e.g. unrecognized timecode format).

    Use this in any new test that asserts an error response — anchoring
    on structured keys means future convention sweeps that move tokens
    between fields don't break these tests. Per CLAUDE.md's "tests
    anchor on structured keys, not prose tokens" rule.
    """
    assert isinstance(data, dict), f"expected dict, got {type(data).__name__}: {data!r}"
    assert data.get("command") == command, (
        f"expected command={command!r}, got {data.get('command')!r} in {data!r}"
    )
    err = data.get("error")
    assert isinstance(err, str) and err, (
        f"error must be a non-empty string, got {err!r}"
    )
    if expect_hint:
        hint = data.get("hint")
        assert isinstance(hint, str) and hint, (
            f"error envelope must carry a non-empty 'hint' field per #27/#78 "
            f"convention. Got: {data!r}"
        )


def assert_no_project_envelope(data: dict, *, command: str) -> None:
    """Assert the standard "no project in this directory" error shape.

    The 10-command sweep in #78 collapsed this prose into a single
    helper (`_no_project_error_exit`). Tests asserting that path
    should use this helper rather than re-checking the prose tokens.

    The error description names "no project"; the hint names the
    `moviestar load` remediation AND a walk-up signal (per issue #41,
    walk-up is the lookup rule and the hint should make that
    discoverable so an agent who's lost knows what was tried).
    """
    assert_error_envelope(data, command=command, expect_hint=True)
    err = data["error"].lower()
    hint = data["hint"].lower()
    assert "no project" in err, (
        f"{command}: error should describe 'no project': {err!r}"
    )
    assert "load" in hint, (
        f"{command}: hint should name 'moviestar load': {hint!r}"
    )
    assert any(
        token in hint for token in ("walks up", "walk up", "ancestor", "git")
    ), (
        f"{command}: hint should signal walk-up was attempted: {hint!r}"
    )
    # Hint isn't duplicating the error description.
    assert "no project" not in hint, (
        f"{command}: hint should not duplicate the error description: {hint!r}"
    )


# -------------------- Synthetic transcript helpers --------------------
#
# Tests that exercise transcript-dependent commands (find,
# trim --snap-to-words) shouldn't have to pay Whisper's 3-15s per run.
# These helpers let a test ``moviestar load --no-transcribe`` against
# any short fixture video, then write a hand-crafted transcript JSON
# directly into the workspace and update project.json so the project
# looks transcribed.
#
# The synthetic transcript matches the on-disk shape produced by
# ``moviestar.transcribe.transcribe_file`` — same fields, same nested
# timecode dicts. New transcript-dependent commands can compose
# ``make_word`` / ``make_segment`` into bespoke transcripts without
# re-deriving the structure.


def make_word(text: str, start: float, end: float) -> dict:
    """Build a synthetic word object matching the on-disk transcript shape."""
    return {
        "text": text,
        "start": {"text": "", "seconds": start},
        "end": {"text": "", "seconds": end},
        "probability": 1.0,
        "speaker": None,
    }


def make_segment(text: str, start: float, end: float) -> dict:
    """Build a synthetic segment object matching the on-disk transcript shape."""
    return {
        "text": text,
        "start": {"text": "", "seconds": start},
        "end": {"text": "", "seconds": end},
    }


def inject_synthetic_transcript(
    tmp_path: Path,
    words: list[dict],
    segments: list[dict],
) -> None:
    """Write a hand-crafted transcript into an already-loaded project.

    Call after ``moviestar load --no-transcribe`` to give the project
    a transcript without paying the Whisper cost. Updates
    ``project.json`` to point at the new transcript file and clears
    the ``transcription_skipped_reason`` flag.

    Compose ``words`` and ``segments`` from :func:`make_word` and
    :func:`make_segment`. Test-specific transcript content (e.g. the
    "this is where you get to the pain" data) lives in the test
    module that uses it; only the infrastructure to write it into
    the project workspace is shared here.
    """
    project_dir = tmp_path / "moviestar"
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text())
    source = project["sources"][0]
    source_id = source["id"]

    transcript = {
        "source_id": source_id,
        "source_path": source["path"],
        "model": "synthetic",
        "backend": "synthetic",
        "language": "en",
        "text": " ".join(w["text"] for w in words),
        "duration": source["duration"],
        "words": words,
        "segments": segments,
    }
    transcripts_dir = project_dir / "transcripts"
    transcripts_dir.mkdir(exist_ok=True)
    transcript_path = transcripts_dir / f"{source_id}.json"
    transcript_path.write_text(json.dumps(transcript))

    source["transcript"] = {
        "model": "synthetic",
        "source": "synthetic",
        "path": f"transcripts/{source_id}.json",
    }
    source.pop("transcription_skipped_reason", None)
    project_path.write_text(json.dumps(project))
