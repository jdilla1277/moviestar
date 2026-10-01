"""Fetch a video page's caption track for ``--captions URL`` (issue #39).

``load`` and ``retranscribe`` import local WebVTT or SRT files through
``caption_import``. This module lets ``--captions`` take a video page URL
instead: yt-dlp (the optional ``url-captions`` extra) reads the page's
caption tracks, picks the best English one, and saves it as WebVTT inside
the workspace, where the existing import path reads it.

Track choice: uploaded (manual) English captions first, then English
auto-captions of English speech. Machine translations are never used,
since they are not the words spoken in the video.

Fetched files live in ``moviestar/captions/``, keyed by the site and video
ID that yt-dlp reads from the URL without a network call::

    moviestar/captions/youtube-jNQXAC9IVRw.en.vtt
    moviestar/captions/youtube-jNQXAC9IVRw.json   # url, track, language

``language`` is a clean code such as ``en`` or ``en-US``. YouTube's raw
track key can carry a track ID (``en-US-njLgzgtehjs``); it is kept as
``track_key``.

Passing the same URL again reuses the saved file, so re-runs are offline
and repeatable even when a site's auto-captions change.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from moviestar.caption_import import CaptionImportError, parse_caption_file

CAPTIONS_DIR = "captions"
EXTRA = "url-captions"
INSTALL_HINT = (
    f"Install the optional fetcher with: pip install 'moviestar[{EXTRA}]', "
    "or download the captions yourself and pass the .vtt or .srt file."
)
MANUAL = "manual"
AUTOMATIC = "automatic"
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class RemoteCaptionsError(ValueError):
    """A caption URL could not be fetched; carries a hint and details."""

    def __init__(self, message: str, hint: str, **details) -> None:
        super().__init__(message)
        self.hint = hint
        self.details = details


def is_caption_url(value: str) -> bool:
    """True when a --captions value is a web page URL, not a file path."""
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _is_english(language: str) -> bool:
    lowered = language.lower()
    return lowered in ("en", "eng", "english") or lowered.startswith(("en-", "en_"))


def _is_translation(formats: list[dict]) -> bool:
    """YouTube marks machine-translated tracks with a ``tlang`` parameter."""
    return any(
        "tlang" in parse_qs(urlparse(fmt.get("url") or "").query) for fmt in formats
    )


def _original_languages(automatic: dict) -> set[str]:
    """Spoken languages named by YouTube's ``<lang>-orig`` auto tracks."""
    return {key[: -len("-orig")] for key in automatic if key.endswith("-orig")}


def _spoken_auto_tracks(automatic: dict) -> list[str]:
    """Auto-caption languages that transcribe the speech, not translate it."""
    originals = _original_languages(automatic)
    keep = []
    for language, formats in automatic.items():
        if _is_translation(formats or []):
            continue
        base = language[: -len("-orig")] if language.endswith("-orig") else language
        if originals and base not in originals:
            continue
        keep.append(language)
    return sorted(keep)


def _language_code(key: str, formats: list[dict]) -> str:
    """Clean language code: the track URL's ``lang`` parameter, else the key."""
    for fmt in formats:
        lang = parse_qs(urlparse(fmt.get("url") or "").query).get("lang")
        if lang and lang[0]:
            return lang[0]
    return key[: -len("-orig")] if key.endswith("-orig") else key


def _rank(language: str) -> tuple:
    """Plain ``en`` first, then regional or numbered variants, ``-orig`` last."""
    return (language.lower() != "en", language.endswith("-orig"), language)


def choose_track(info: dict, url: str) -> dict:
    """Pick the caption track to import from yt-dlp's info dict.

    Returns ``{"kind": "manual"|"automatic", "key": KEY, "language": CODE}``
    where KEY is yt-dlp's track key and CODE a clean language code. Raises
    RemoteCaptionsError naming the available languages when no English
    track transcribes the speech.
    """
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if v}
    automatic = {k: v for k, v in (info.get("automatic_captions") or {}).items() if v}
    english_manual = sorted((k for k in manual if _is_english(k)), key=_rank)
    if english_manual:
        key = english_manual[0]
        return {"kind": MANUAL, "key": key, "language": _language_code(key, manual[key])}
    spoken_auto = _spoken_auto_tracks(automatic)
    english_auto = sorted((k for k in spoken_auto if _is_english(k)), key=_rank)
    if english_auto:
        key = english_auto[0]
        return {
            "kind": AUTOMATIC,
            "key": key,
            "language": _language_code(key, automatic[key]),
        }

    available = {MANUAL: sorted(manual), AUTOMATIC: spoken_auto}
    hint = (
        "Drop --captions to transcribe the audio with Whisper, or pass a "
        "caption file you have downloaded."
    )
    if not manual and not spoken_auto:
        raise RemoteCaptionsError(
            f"No caption tracks are available for {url}.",
            hint,
            available_captions=available,
        )
    raise RemoteCaptionsError(
        f"No English caption track is available for {url}; found "
        f"{', '.join(sorted(set(available[MANUAL] + available[AUTOMATIC])))}.",
        hint,
        available_captions=available,
    )


class _CollectingLogger:
    """Keep yt-dlp off stdout and stderr, but remember its warnings.

    Most are about video formats, which a caption fetch never downloads.
    When a site blocks a request or the video is gone, yt-dlp still
    returns an info dict and the reason survives only as a warning, so
    ``fetch_captions`` reports these when no track turns up. Errors are
    raised as DownloadError and reported in the JSON envelope.
    """

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages

    def debug(self, msg: str) -> None:
        pass

    info = error = debug

    def warning(self, msg: str) -> None:
        self.messages.append(msg)


# yt-dlp warnings about video formats; they never explain missing captions.
_FORMAT_NOISE = (
    "[jsc]",
    "challenge",
    "JavaScript runtime",
    "impersonat",
    "formats may be missing",
    "No video formats found",
    "Requested format is not available",
)


def _site_reason(messages: list[str]) -> str | None:
    """The first yt-dlp warning that isn't about video formats."""
    for message in messages:
        if not any(noise.lower() in message.lower() for noise in _FORMAT_NOISE):
            return message
    return None


class YtDlpBackend:
    """The network side, kept small so tests can swap it out."""

    def __init__(self) -> None:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RemoteCaptionsError(
                "Reading captions from a URL needs yt-dlp, which is not installed.",
                INSTALL_HINT,
            ) from exc
        self.yt_dlp = yt_dlp
        self.warnings: list[str] = []

    @property
    def version(self) -> str:
        return self.yt_dlp.version.__version__

    def _options(self, **extra) -> dict:
        return {
            "quiet": True,
            "noprogress": True,
            "noplaylist": True,
            "skip_download": True,
            "ignore_no_formats_error": True,
            "logger": _CollectingLogger(self.warnings),
            **extra,
        }

    def video_key(self, url: str) -> str:
        """``<site>-<id>`` read from the URL alone, or a hash of the URL."""
        for extractor in self.yt_dlp.extractor.gen_extractor_classes():
            if extractor.ie_key() == "Generic" or not extractor.suitable(url):
                continue
            video_id = extractor.get_temp_id(url)
            if video_id:
                return _UNSAFE_NAME_RE.sub("_", f"{extractor.ie_key().lower()}-{video_id}")
            break
        return "url-" + hashlib.sha256(url.encode()).hexdigest()[:16]

    def extract(self, url: str) -> dict:
        try:
            with self.yt_dlp.YoutubeDL(self._options()) as ydl:
                info = ydl.extract_info(url, download=False)
        except self.yt_dlp.utils.DownloadError as exc:
            raise _fetch_error(url, exc) from exc
        return ydl.sanitize_info(info)

    def download(self, info: dict, track: dict, out_dir: Path) -> Path:
        """Write the chosen track to ``out_dir`` as WebVTT and return it."""
        options = self._options(
            writesubtitles=track["kind"] == MANUAL,
            writeautomaticsub=track["kind"] == AUTOMATIC,
            subtitleslangs=[re.escape(track["key"])],
            subtitlesformat="vtt/srt/best",
            outtmpl={"default": str(out_dir / "track.%(ext)s")},
            postprocessors=[
                {"key": "FFmpegSubtitlesConvertor", "format": "vtt", "when": "before_dl"}
            ],
        )
        try:
            with self.yt_dlp.YoutubeDL(options) as ydl:
                ydl.process_ie_result(info, download=True)
        except self.yt_dlp.utils.DownloadError as exc:
            raise _fetch_error(info.get("webpage_url") or "", exc) from exc
        written = sorted(out_dir.glob("track.*.vtt"))
        if not written:
            raise RemoteCaptionsError(
                f"yt-dlp did not write the {track['key']} caption track.",
                "Retry, update yt-dlp with 'pip install -U yt-dlp', or pass a "
                "caption file you have downloaded.",
            )
        return written[0]


def _fetch_error(url: str, exc: Exception) -> RemoteCaptionsError:
    message = re.sub(r"^ERROR:\s*", "", str(exc)).strip()
    return RemoteCaptionsError(
        f"Could not read captions from {url}: {message}",
        "Check that the URL opens a single public video, update yt-dlp with "
        "'pip install -U yt-dlp', or pass a caption file you have downloaded.",
    )


def _backend() -> YtDlpBackend:
    return YtDlpBackend()


def _cached(cache_dir: Path | None, key: str) -> dict | None:
    """The saved record for ``key`` when its caption file is still there."""
    if cache_dir is None:
        return None
    record_path = cache_dir / f"{key}.json"
    try:
        record = json.loads(record_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not record.get("file"):
        return None
    if not (cache_dir / record["file"]).is_file():
        return None
    return record


def fetch_captions(
    url: str,
    staging_dir: Path,
    *,
    cache_dir: Path | None = None,
    quiet: bool = False,
) -> dict:
    """Resolve a caption URL to a WebVTT file without touching the workspace.

    Reuses ``cache_dir/<key>.json`` and its caption file when present;
    otherwise fetches into ``staging_dir``. Either way the result is staged
    in ``staging_dir`` (``load --force`` may wipe ``cache_dir``) and
    validated, so a bad URL fails before the workspace changes. Returns the
    record to save with :func:`store_captions`, plus ``staged_path``.
    """
    backend = _backend()
    key = backend.video_key(url)
    staging_dir.mkdir(parents=True, exist_ok=True)
    record = _cached(cache_dir, key)
    if record is not None:
        staged = staging_dir / record["file"]
        shutil.copyfile(cache_dir / record["file"], staged)
        cached = True
    else:
        if not quiet:
            print(f"  Fetching captions from {url}...", file=sys.stderr, flush=True)
        info = backend.extract(url)
        if info.get("_type") in ("playlist", "multi_video"):
            raise RemoteCaptionsError(
                f"{url} is a playlist, not a single video.",
                "Pass the URL of the one video whose captions you want.",
            )
        try:
            track = choose_track(info, url)
        except RemoteCaptionsError as exc:
            messages = list(getattr(backend, "warnings", []))
            reason = _site_reason(messages)
            if reason is None:
                raise
            raise RemoteCaptionsError(
                f"{exc} yt-dlp reported: {reason}",
                f"{exc.hint} A sign-in or bot check means the site blocked "
                "this network; retry from another network.",
                **exc.details,
                yt_dlp_messages=messages,
            ) from exc
        work = staging_dir / f"{key}.download"
        work.mkdir(parents=True, exist_ok=True)
        downloaded = backend.download(info, track, work)
        language = _UNSAFE_NAME_RE.sub("_", track["language"])
        staged = staging_dir / f"{key}.{language}.vtt"
        shutil.move(str(downloaded), staged)
        shutil.rmtree(work, ignore_errors=True)
        record = {
            "url": url,
            "file": staged.name,
            "extractor": info.get("extractor_key") or info.get("extractor"),
            "video_id": info.get("id"),
            "title": info.get("title"),
            "track": track["kind"],
            "language": track["language"],
            "track_key": track["key"],
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fetched_with": f"yt-dlp {backend.version}",
        }
        cached = False
    try:
        parse_caption_file(str(staged))
    except CaptionImportError as exc:
        raise RemoteCaptionsError(
            f"The caption track fetched from {url} could not be read: {exc}",
            "Pass a caption file you have downloaded instead.",
        ) from exc
    return {**record, "key": key, "cached": cached, "staged_path": str(staged)}


def store_captions(fetched: dict, cache_dir: Path) -> str:
    """Move a staged caption file into ``cache_dir``; return its real path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    final = cache_dir / fetched["file"]
    staged = Path(fetched["staged_path"])
    if staged.resolve() != final.resolve():
        shutil.copyfile(staged, final)
    record = {
        k: v for k, v in fetched.items() if k not in ("key", "cached", "staged_path")
    }
    (cache_dir / f"{fetched['key']}.json").write_text(json.dumps(record, indent=2) + "\n")
    return os.path.realpath(final)


def provenance(fetched: dict) -> dict:
    """The fields added to a transcript's ``captions`` block."""
    return {
        "url": fetched["url"],
        "track": fetched["track"],
        "language": fetched["language"],
        "track_key": fetched["track_key"],
    }
