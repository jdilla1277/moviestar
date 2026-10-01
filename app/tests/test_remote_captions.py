"""Fetch captions from a video page URL for --captions (issue #39).

``load VIDEO --captions URL`` and ``retranscribe --captions URL`` hand the
URL to yt-dlp, pick the best English track (uploaded captions first, then
auto-captions of English speech, never machine translations), save it as
``moviestar/captions/<site>-<id>.<lang>.vtt``, and import it through the
same path as a local caption file.
"""

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar import remote_captions
from moviestar.cli import cli
from moviestar.remote_captions import (
    AUTOMATIC,
    MANUAL,
    RemoteCaptionsError,
    choose_track,
    fetch_captions,
    is_caption_url,
)
from tests.conftest import assert_error_envelope
from tests.test_caption_import import YOUTUBE_VTT

URL = "https://www.youtube.com/watch?v=abc123"
KEY = "youtube-abc123"


def _track(lang: str, *, tlang: str | None = None, ext: str = "vtt") -> dict:
    query = f"lang={lang}" + (f"&tlang={tlang}" if tlang else "")
    return {"ext": ext, "url": f"https://example.test/timedtext?{query}"}


def _info(manual=None, automatic=None) -> dict:
    return {
        "id": "abc123",
        "extractor_key": "Youtube",
        "title": "Council meeting",
        "webpage_url": URL,
        "subtitles": manual or {},
        "automatic_captions": automatic or {},
    }


class FakeBackend:
    """Stands in for yt-dlp: no network, records what was asked."""

    version = "test"

    def __init__(self, info=None, text=YOUTUBE_VTT):
        self.info = info if info is not None else _info(manual={"en": [_track("en")]})
        self.text = text
        self.extracted: list[str] = []
        self.downloaded: list[dict] = []

    def video_key(self, url):
        return KEY

    def extract(self, url):
        self.extracted.append(url)
        return self.info

    def download(self, info, track, out_dir):
        self.downloaded.append(track)
        path = Path(out_dir) / f"track.{track['language']}.vtt"
        path.write_text(self.text)
        return path


class OfflineBackend(FakeBackend):
    def extract(self, url):
        raise AssertionError("must not touch the network")


@pytest.fixture
def backend(monkeypatch):
    def install(fake):
        monkeypatch.setattr(remote_captions, "_backend", lambda: fake)
        return fake

    return install


class TestIsCaptionUrl:
    @pytest.mark.parametrize(
        "value",
        [URL, "https://youtu.be/abc123", "http://example.com/talk"],
    )
    def test_web_urls(self, value):
        assert is_caption_url(value)

    @pytest.mark.parametrize(
        "value", ["meeting.en.vtt", "/abs/meeting.srt", "file:///tmp/a.vtt", "https:"]
    )
    def test_paths_are_not_urls(self, value):
        assert not is_caption_url(value)


class TestChooseTrack:
    def test_uploaded_english_wins_over_auto_captions(self):
        info = _info(
            manual={"en": [_track("en")], "de": [_track("de")]},
            automatic={"en": [_track("en")], "en-orig": [_track("en")]},
        )
        assert choose_track(info, URL) == {"kind": MANUAL, "language": "en"}

    def test_regional_uploaded_tracks_count_as_english(self):
        info = _info(
            manual={
                "es": [_track("es")],
                "en-US-njLgzgtehjs": [_track("en-US")],
                "en-eEY6OEpapPo": [_track("en")],
            }
        )
        assert choose_track(info, URL)["language"] == "en-US-njLgzgtehjs"

    def test_auto_captions_of_english_speech(self):
        info = _info(
            automatic={
                "en": [_track("en")],
                "en-orig": [_track("en")],
                "es": [_track("en", tlang="es")],
            }
        )
        assert choose_track(info, URL) == {"kind": AUTOMATIC, "language": "en"}

    def test_translated_auto_captions_are_never_used(self):
        info = _info(
            automatic={
                "es-orig": [_track("es")],
                "es": [_track("es")],
                "en": [_track("es", tlang="en")],
            }
        )
        with pytest.raises(RemoteCaptionsError) as raised:
            choose_track(info, URL)
        assert "No English caption track" in str(raised.value)
        assert raised.value.details["available_captions"] == {
            MANUAL: [],
            AUTOMATIC: ["es", "es-orig"],
        }

    def test_auto_english_is_rejected_when_the_speech_is_another_language(self):
        # HLS-sourced tracks carry no tlang, but the -orig track names the
        # spoken language.
        info = _info(automatic={"fr-orig": [_track("fr")], "en": [{"ext": "vtt"}]})
        with pytest.raises(RemoteCaptionsError):
            choose_track(info, URL)

    def test_other_sites_auto_captions_without_orig_markers(self):
        info = _info(automatic={"en-US": [{"ext": "vtt", "url": "https://x/a.vtt"}]})
        assert choose_track(info, URL) == {"kind": AUTOMATIC, "language": "en-US"}

    def test_no_tracks_at_all(self):
        with pytest.raises(RemoteCaptionsError) as raised:
            choose_track(_info(), URL)
        assert "No caption tracks" in str(raised.value)
        assert "Whisper" in raised.value.hint


class TestFetchCaptions:
    def test_fetch_stages_the_track_without_touching_the_cache(self, tmp_path, backend):
        fake = backend(FakeBackend())
        fetched = fetch_captions(URL, tmp_path / "stage", cache_dir=tmp_path / "cache")

        assert fetched["file"] == f"{KEY}.en.vtt"
        assert Path(fetched["staged_path"]).read_text() == YOUTUBE_VTT
        assert fetched["track"] == MANUAL and fetched["language"] == "en"
        assert fetched["cached"] is False
        assert fake.downloaded == [{"kind": MANUAL, "language": "en"}]
        assert not (tmp_path / "cache").exists()

    def test_saved_track_is_reused_offline(self, tmp_path, backend):
        backend(FakeBackend())
        cache = tmp_path / "cache"
        remote_captions.store_captions(
            fetch_captions(URL, tmp_path / "first", cache_dir=cache), cache
        )

        backend(OfflineBackend())
        again = fetch_captions(URL, tmp_path / "second", cache_dir=cache)

        assert again["cached"] is True
        assert again["url"] == URL
        assert Path(again["staged_path"]).read_text() == YOUTUBE_VTT

    def test_unreadable_track_is_a_structured_error(self, tmp_path, backend):
        backend(FakeBackend(text="<html>not captions</html>"))
        with pytest.raises(RemoteCaptionsError) as raised:
            fetch_captions(URL, tmp_path / "stage")
        assert "could not be read" in str(raised.value)

    def test_playlists_are_rejected(self, tmp_path, backend):
        backend(FakeBackend(info={"_type": "playlist", "entries": []}))
        with pytest.raises(RemoteCaptionsError, match="playlist"):
            fetch_captions(URL, tmp_path / "stage")

    def test_missing_yt_dlp_names_the_extra(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "yt_dlp", None)
        with pytest.raises(RemoteCaptionsError) as raised:
            remote_captions.YtDlpBackend()
        assert "moviestar[url-captions]" in raised.value.hint


class TestVideoKey:
    @pytest.fixture
    def real(self):
        pytest.importorskip("yt_dlp")
        return remote_captions.YtDlpBackend()

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            "https://youtu.be/jNQXAC9IVRw",
            "https://m.youtube.com/watch?v=jNQXAC9IVRw&t=10s",
            "https://www.youtube.com/shorts/jNQXAC9IVRw",
        ],
    )
    def test_youtube_urls_share_one_key_offline(self, real, url):
        assert real.video_key(url) == "youtube-jNQXAC9IVRw"

    def test_unknown_sites_fall_back_to_a_url_hash(self, real):
        key = real.video_key("https://example.com/talks/keynote")
        assert key.startswith("url-") and len(key) == 20


class TestCli:
    @pytest.fixture
    def no_whisper(self, monkeypatch):
        def _fail(*args, **kwargs):
            raise AssertionError("Whisper must not run for imported captions")

        monkeypatch.setattr("moviestar.project.transcribe_file", _fail)
        monkeypatch.setattr("moviestar.cli.transcribe_file", _fail)

    def _load(self, *args):
        return CliRunner().invoke(cli, ["load", *args])

    def _transcript(self, root: Path) -> dict:
        return json.loads((root / "moviestar" / "transcripts" / "src_0.json").read_text())

    def test_url_matches_the_downloaded_vtt(
        self, test_video, tmp_path, monkeypatch, backend, no_whisper
    ):
        """Acceptance: a URL and its downloaded .en.vtt give one transcript."""
        local, remote = tmp_path / "local", tmp_path / "remote"
        local.mkdir()
        remote.mkdir()
        vtt = local / "meeting.en.vtt"
        vtt.write_text(YOUTUBE_VTT)
        monkeypatch.chdir(local)
        from_file = self._load(
            test_video, "--as", "src_0", "--no-frames", "--captions", str(vtt)
        )
        assert from_file.exit_code == 0, from_file.stdout

        backend(FakeBackend())
        monkeypatch.chdir(remote)
        from_url = self._load(test_video, "--as", "src_0", "--no-frames", "--captions", URL)
        assert from_url.exit_code == 0, from_url.stdout

        expected, actual = self._transcript(local), self._transcript(remote)
        for field in ("words", "segments", "text", "language", "duration", "backend"):
            assert actual[field] == expected[field], field

        saved = remote / "moviestar" / "captions" / f"{KEY}.en.vtt"
        assert saved.read_text() == YOUTUBE_VTT
        assert actual["captions"]["path"] == str(saved.resolve())
        assert actual["captions"]["url"] == URL
        assert actual["captions"]["track"] == MANUAL
        record = json.loads((saved.parent / f"{KEY}.json").read_text())
        assert record["url"] == URL and record["file"] == saved.name

        [source] = json.loads(from_url.stdout)["sources"]
        assert source["transcript"]["captions_url"] == URL

    def test_no_caption_track_fails_before_the_workspace_exists(
        self, test_video, tmp_path, monkeypatch, backend
    ):
        monkeypatch.chdir(tmp_path)
        backend(FakeBackend(info=_info(automatic={"fr-orig": [_track("fr")]})))

        result = self._load(test_video, "--captions", URL)

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="load")
        assert "No English caption track" in data["error"]
        assert data["available_captions"] == {MANUAL: [], AUTOMATIC: ["fr-orig"]}
        assert not (tmp_path / "moviestar").exists()

    def test_failed_fetch_leaves_a_forced_workspace_untouched(
        self, test_video, tmp_path, monkeypatch, backend, loaded_project
    ):
        loaded_project(test_video, frames=False)
        before = (tmp_path / "moviestar" / "project.json").read_text()
        backend(FakeBackend(info=_info()))

        result = self._load(test_video, "--force", "--captions", URL)

        assert result.exit_code == 1
        assert (tmp_path / "moviestar" / "project.json").read_text() == before

    def test_reload_reuses_the_saved_track_offline(
        self, test_video, tmp_path, monkeypatch, backend, no_whisper
    ):
        monkeypatch.chdir(tmp_path)
        backend(FakeBackend())
        first = self._load(test_video, "--as", "src_0", "--no-frames", "--captions", URL)
        assert first.exit_code == 0, first.stdout

        backend(OfflineBackend())
        again = self._load(
            test_video, "--as", "src_0", "--no-frames", "--force", "--captions", URL
        )

        assert again.exit_code == 0, again.stdout
        assert (tmp_path / "moviestar" / "captions" / f"{KEY}.en.vtt").exists()
        assert self._transcript(tmp_path)["captions"]["url"] == URL

    def test_urls_and_files_mix_across_videos(
        self, test_video, tmp_path, monkeypatch, backend, no_whisper
    ):
        monkeypatch.chdir(tmp_path)
        backend(FakeBackend())
        vtt = tmp_path / "b.vtt"
        vtt.write_text(YOUTUBE_VTT)

        result = self._load(
            test_video, test_video, "--as", "a", "--as", "b", "--no-frames",
            "--captions", URL, "--captions", str(vtt),
        )

        assert result.exit_code == 0, result.stdout
        transcripts = tmp_path / "moviestar" / "transcripts"
        a = json.loads((transcripts / "a.json").read_text())
        b = json.loads((transcripts / "b.json").read_text())
        assert a["captions"]["url"] == URL
        assert "url" not in b["captions"]

    def test_dry_run_does_not_fetch(self, test_video, tmp_path, monkeypatch, backend):
        monkeypatch.chdir(tmp_path)
        backend(OfflineBackend())

        result = self._load(test_video, "--captions", URL, "--dry-run")

        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["would_import_captions"] == URL
        assert data["would_transcribe"] is False
        assert not (tmp_path / "moviestar").exists()

    def test_missing_yt_dlp_is_a_structured_error(
        self, test_video, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setitem(sys.modules, "yt_dlp", None)

        result = self._load(test_video, "--captions", URL)

        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert_error_envelope(data, command="load")
        assert "moviestar[url-captions]" in data["hint"]
        assert not (tmp_path / "moviestar").exists()

    def test_retranscribe_fetches_then_reuses_offline(
        self, loaded_project, test_video, tmp_path, backend, no_whisper
    ):
        loaded_project(test_video, frames=False)
        backend(FakeBackend())
        first = CliRunner().invoke(cli, ["retranscribe", "--captions", URL, "--quiet"])
        assert first.exit_code == 0, first.stdout
        data = json.loads(first.stdout)
        assert data["transcript"]["backend"] == "imported-captions"
        assert data["transcript"]["captions_url"] == URL
        assert (tmp_path / "moviestar" / "captions" / f"{KEY}.json").exists()

        backend(OfflineBackend())
        again = CliRunner().invoke(cli, ["retranscribe", "--captions", URL, "--quiet"])
        assert again.exit_code == 0, again.stdout

    def test_retranscribe_without_a_track_keeps_the_transcript(
        self, loaded_project, test_video, tmp_path, backend
    ):
        loaded_project(test_video, frames=False)
        project = (tmp_path / "moviestar" / "project.json").read_text()
        backend(FakeBackend(info=_info()))

        result = CliRunner().invoke(cli, ["retranscribe", "--captions", URL])

        assert result.exit_code == 1
        assert_error_envelope(json.loads(result.stdout), command="retranscribe")
        assert (tmp_path / "moviestar" / "project.json").read_text() == project
        assert not (tmp_path / "moviestar" / "captions").exists()


@pytest.mark.slow
def test_real_youtube_captions_match_the_ytdlp_download(tmp_path):
    """Network: fetch a real track and compare it with yt-dlp's own file."""
    yt_dlp = pytest.importorskip("yt_dlp")
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    fetched = fetch_captions(url, tmp_path / "stage", quiet=True)
    assert fetched["track"] == MANUAL and fetched["language"] == "en"

    direct = tmp_path / "direct"
    options = {
        "quiet": True,
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": ["en"],
        "subtitlesformat": "vtt",
        "outtmpl": {"default": str(direct / "%(id)s.%(ext)s")},
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])
    expected = direct / "jNQXAC9IVRw.en.vtt"
    assert Path(fetched["staged_path"]).read_text() == expected.read_text()
