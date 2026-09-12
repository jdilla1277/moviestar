"""Tests for shared pytest fixtures defined in conftest.py."""

import hashlib
import json
from pathlib import Path

from moviestar.project import MOVIESTAR_DIR, PROJECT_FILE


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_speech_fixture_has_verified_redistribution_provenance():
    speech = FIXTURES_DIR / "speech.wav"
    provenance = FIXTURES_DIR / "SPEECH_PROVENANCE.md"
    license_file = FIXTURES_DIR / "CMU_ARCTIC_LICENSE.txt"

    assert hashlib.sha256(speech.read_bytes()).hexdigest() == (
        "9014c7af0c450ffab8a64f211559ed6eb61d1eb6c8a8288cca40325e64f702e7"
    )
    assert hashlib.sha256(license_file.read_bytes()).hexdigest() == (
        "fe104c6eb4ec5f51975706215fbce95187987bf0b6400b132c72f3f3e7e49b50"
    )
    provenance_text = provenance.read_text(encoding="utf-8")
    assert "cmu_us_bdl_arctic-0.95-release" in provenance_text
    assert "arctic_a0004.wav" in provenance_text
    assert "copied byte-for-byte" in provenance_text


class TestLoadedProject:
    def test_single_source_default_id(self, loaded_project, test_video, tmp_path):
        project = loaded_project(test_video)
        assert len(project["sources"]) == 1
        assert project["sources"][0]["id"] == "src_0"
        assert (tmp_path / MOVIESTAR_DIR / PROJECT_FILE).is_file()

    def test_id_does_not_depend_on_fixture_filename(
        self, loaded_project, test_video
    ):
        # test_video resolves to a file like 'probe_test.mp4' → would auto-derive
        # to 'probe' without --as. The fixture must always pass --as src_0.
        project = loaded_project(test_video)
        assert project["sources"][0]["id"] == "src_0", (
            "fixture should pass --as src_0 so the id never drifts with the "
            "fixture filename"
        )

    def test_multi_source_default_ids(
        self, loaded_project, test_video, silent_video
    ):
        project = loaded_project([test_video, silent_video])
        ids = [s["id"] for s in project["sources"]]
        assert ids == ["src_0", "src_1"]

    def test_explicit_names(self, loaded_project, test_video, silent_video):
        project = loaded_project(
            [test_video, silent_video], names=["holden", "jdilla"]
        )
        ids = [s["id"] for s in project["sources"]]
        assert ids == ["holden", "jdilla"]

    def test_names_length_mismatch_raises(self, loaded_project, test_video):
        import pytest

        with pytest.raises(ValueError, match="len\\(names\\)="):
            loaded_project(test_video, names=["a", "b"])

    def test_transcribe_default_off(self, loaded_project, test_video, tmp_path):
        loaded_project(test_video)
        # With --no-transcribe, the source has no transcript field
        # (or a `transcription_skipped_reason: "flag"`).
        project = json.loads(
            (tmp_path / MOVIESTAR_DIR / PROJECT_FILE).read_text()
        )
        source = project["sources"][0]
        assert source.get("transcript") is None
