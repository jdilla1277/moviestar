"""Private finished-video upload for a connected MovieStar account."""

from __future__ import annotations

import json
import re
import uuid
from typing import Callable
from pathlib import Path
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

import click

from moviestar.account import (
    _CLIENT_MARKER,
    _NoRedirectHandler,
    _credential_for_connection,
    _installation_url,
    _read_state,
    _service_url_error,
)


MAX_FILE_BYTES = 500_000_000
FREE_LIMITS = {"stored_videos": 3, "total_bytes": 1_000_000_000, "per_video_bytes": MAX_FILE_BYTES}
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_RESERVATION_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


def _emit(status: str, **fields: object) -> None:
    click.echo(json.dumps({"command": "upload", "status": status, **fields}, indent=2))


class UploadError(Exception):
    def __init__(self, status: str, hint: str, **fields: object):
        super().__init__(status)
        self.status, self.hint, self.fields = status, hint, fields


def _fail(status: str, hint: str, **fields: object) -> None:
    raise UploadError(status, hint, **fields)


def _upload_url() -> str | None:
    installation = _installation_url()
    if _service_url_error(installation):
        return None
    parsed = urlsplit(installation)
    if parsed.path != "/api/v1/account/installation" or parsed.query:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/videos/uploads"


def _safe_signed_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError:
        return False
    if not host or parsed.username or parsed.password or parsed.fragment:
        return False
    if parsed.scheme == "https":
        return host.endswith(".r2.cloudflarestorage.com") and host != "r2.cloudflarestorage.com"
    return parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}


def _json_request(url: str, credential: str, payload: dict | None = None, *, method: str = "POST") -> tuple[int, dict]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else (b"" if method == "POST" else None),
        headers={
            "Authorization": f"Bearer {credential}",
            "x-moviestar-client": _CLIENT_MARKER,
            "Content-Type": "application/json",
        },
        method=method,
    )
    opener = build_opener(_NoRedirectHandler())
    try:
        try:
            with opener.open(request, timeout=30) as response:
                status, body = response.status, response.read(65_537)
        except HTTPError as error:
            with error:
                status, body = error.code, error.read(65_537)
    except (URLError, HTTPException, TimeoutError, OSError):
        return 0, {}
    if len(body) > 65_536:
        return status, {}
    try:
        result = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return status, {}
    return status, result if isinstance(result, dict) else {}


def _put_file(url: str, headers: dict[str, str], file: Path) -> bool:
    opener = build_opener(_NoRedirectHandler())
    try:
        with file.open("rb") as stream:
            request = Request(url, data=stream, headers=headers, method="PUT")
            with opener.open(request, timeout=300) as response:
                return 200 <= response.status < 300
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def _validate_result(endpoint: str, credential: str, video_id: str,
                       reservation_id: str, file: Path, size: int, key: str | None) -> dict:
    validation_url = f"{endpoint.removesuffix('/uploads')}/{video_id}/validate"
    status, validation = _json_request(validation_url, credential)
    common = {"video_id": video_id, "filename": file.name, "size_bytes": size,
              "reservation_id": reservation_id, **({"idempotency_key": key} if key else {})}
    if status == 200 and validation.get("status") == "ready" and validation.get("video_id") == video_id:
        media = validation.get("media")
        audio_codec = media.get("audioCodec") if isinstance(media, dict) else None
        if (isinstance(media, dict) and "audioCodec" in media
                and type(media.get("durationMs")) is int and media["durationMs"] > 0
                and type(media.get("width")) is int and media["width"] > 0
                and type(media.get("height")) is int and media["height"] > 0
                and media.get("videoCodec") == "avc" and (audio_codec is None or audio_codec == "aac")):
            public_media = {field: media[field] for field in ("durationMs", "width", "height", "videoCodec", "audioCodec")}
            return {"status": "ready", **common, "media": public_media,
                    "hint": "The video is stored privately. Use 'moviestar share VIDEO_ID' to create an unlisted link when publication is enabled."}
    if status == 422 and validation.get("status") == "invalid_video" and validation.get("video_id") == video_id:
        reason = validation.get("reason")
        quota_released = validation.get("quota_released") is True
        hint = ("Export a browser-compatible H.264/AAC MP4 and upload it again." if quota_released else
                "Retry 'moviestar upload FILE --resume RESERVATION_ID' to finish deleting the invalid upload.")
        _fail("invalid_video", hint,
              **common, reason=reason if isinstance(reason, str) and len(reason) <= 64 else "invalid_mp4",
              quota_released=quota_released)
    if status == 409 and validation.get("status") == "consent_required":
        _fail("consent_required", "Run 'moviestar account terms' to finish account setup, then resume validation.", **common)
    if status == 401:
        _fail("not_connected", "Run 'moviestar account status' and reconnect this installation if needed.", **common)
    _fail("validation_unavailable", "The file is stored privately. Retry 'moviestar upload FILE --resume RESERVATION_ID' to validate it.",
          **common)


def _complete_result(endpoint: str, credential: str, reservation_id: str,
                       file: Path, size: int, key: str | None) -> dict:
    status, completion = _json_request(f"{endpoint}/{reservation_id}/complete", credential)
    if status == 409 and completion.get("status") == "consent_required":
        _fail("consent_required", "Run 'moviestar account terms' to finish account setup, then resume this upload.", reservation_id=reservation_id)
    video_id = completion.get("video_id")
    completed = status in {200, 201} and completion.get("status") in {"stored_unverified", "ready"}
    rejected = status == 422 and completion.get("status") == "invalid_video"
    if (not (completed or rejected) or not isinstance(video_id, str) or
            not _RESERVATION_RE.fullmatch(video_id)):
        _fail(
            completion.get("status", "upload_unavailable") if status else "upload_unavailable",
            "Retry 'moviestar upload FILE --resume RESERVATION_ID' with the reported reservation ID.",
            reservation_id=reservation_id, **({"idempotency_key": key} if key else {}),
        )
    stored_size = completion.get("size_bytes")
    return _validate_result(endpoint, credential, video_id, reservation_id, file,
                            stored_size if type(stored_size) is int and stored_size > 0 else size, key)


@click.command("upload")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--idempotency-key", metavar="KEY", help="Reuse KEY when retrying an interrupted upload of this same file.")
@click.option("--resume", metavar="RESERVATION_ID", help="Retry completion or validation without uploading the file again.")
@click.option("--dry-run", is_flag=True, help="Preview file size, account target, and Free limits without reserving or uploading.")
def upload(file: Path, idempotency_key: str | None, resume: str | None, dry_run: bool) -> None:
    """Store a finished MP4 privately in your MovieStar account.

    Upload does not create or send a share link. Connect first with
    ``moviestar account connect EMAIL`` and confirm with account status.
    If transfer fails, retry with the reported idempotency key. If completion
    or validation fails, retry with ``--resume`` and the reported reservation ID.
    The retry key is printed to stderr before any network request so an
    interrupted command can reuse it.

    Example: moviestar upload final.mp4
    """
    try:
        result = upload_video(file, idempotency_key=idempotency_key, resume=resume,
                              dry_run=dry_run, announce_retry=_announce_retry)
    except UploadError as error:
        _emit(error.status, hint=error.hint, **error.fields)
        raise click.exceptions.Exit(1)
    _emit(**result)


def _announce_retry(key: str) -> None:
    click.echo(json.dumps({
        "command": "upload", "status": "retry_key", "idempotency_key": key,
        "hint": "If interrupted, retry the same file with --idempotency-key KEY.",
    }), err=True)


def upload_video(file: Path, *, idempotency_key: str | None = None,
                 resume: str | None = None, dry_run: bool = False,
                 announce_retry: Callable[[str], None] | None = None,
                 connection: tuple[str, str] | None = None) -> dict:
    """Upload/validate without printing a result, for composed CLI workflows."""
    if file.suffix.lower() != ".mp4":
        _fail("invalid_file", "Export a finished .mp4 and retry.")
    try:
        size = file.stat().st_size
    except OSError:
        _fail("invalid_file", "The video file cannot be read.")
    if size < 1 or size > MAX_FILE_BYTES:
        _fail("invalid_file", "Use an MP4 between 1 byte and 500 MB.", size_bytes=size)
    state = _read_state()
    credential = connection[1] if connection else _credential_for_connection(state)
    endpoint = connection[0] if connection else _upload_url()
    if not credential or not endpoint:
        _fail("not_connected", "Run 'moviestar account connect EMAIL', then 'moviestar account status'.")

    if dry_run:
        if resume is not None:
            _fail("invalid_request", "Use --dry-run for a new upload, or --resume to retry completion.")
        connection = state.get("connection")
        owner = connection if isinstance(connection, dict) else {}
        parsed = urlsplit(endpoint)
        return dict(status="would_upload", file={"path": str(file.resolve()), "size_bytes": size},
              account={"handle": owner.get("handle"), "email": owner.get("email"),
                       "service_origin": f"{parsed.scheme}://{parsed.netloc}"},
              quota={"free_limits": FREE_LIMITS, "would_use_videos": 1,
                     "would_use_bytes": size, "current_usage": "unavailable",
                     "projected_usage": "unavailable"},
              upload_performed=False, reservation_created=False,
              hint="Current usage is checked by the service when the upload starts.")

    key = idempotency_key or uuid.uuid4().hex
    if not _KEY_RE.fullmatch(key):
        _fail("invalid_request", "Use 1–128 letters, digits, underscores, or hyphens for the idempotency key.")

    if resume is not None:
        if not _RESERVATION_RE.fullmatch(resume):
            _fail("invalid_request", "Use the reservation ID reported by the failed upload.")
        return {**_complete_result(endpoint, credential, resume, file, size, None), "upload_performed": False}

    if announce_retry:
        announce_retry(key)
    status, initiation = _json_request(endpoint, credential, {
        "filename": file.name, "size_bytes": size, "idempotency_key": key,
    })
    if status == 409 and initiation.get("status") == "consent_required":
        _fail("consent_required", "Run 'moviestar account terms' to review and accept account terms and age confirmation, then retry with the same idempotency key.", idempotency_key=key)
    if status == 200 and initiation.get("status") == "already_completed":
        reservation_id = initiation.get("reservation_id")
        if not isinstance(reservation_id, str) or not _RESERVATION_RE.fullmatch(reservation_id):
            _fail("upload_unavailable", "The upload service returned an invalid reservation.", idempotency_key=key)
        return {**_complete_result(endpoint, credential, reservation_id, file, size, key), "upload_performed": False}
    if status not in {200, 201} or initiation.get("status") != "upload_pending":
        _fail(
            initiation.get("status", "upload_unavailable") if status else "upload_unavailable",
            "Check account status and quota, then retry with the same idempotency key.",
            idempotency_key=key,
        )
    reservation_id = initiation.get("reservation_id")
    signed = initiation.get("upload")
    if not isinstance(reservation_id, str) or not _RESERVATION_RE.fullmatch(reservation_id) or not isinstance(signed, dict):
        _fail("upload_unavailable", "The upload service returned an invalid reservation.", idempotency_key=key)
    headers = signed.get("headers")
    if (signed.get("method") != "PUT" or not _safe_signed_url(signed.get("url"))
            or not isinstance(headers, dict) or headers.get("Content-Type") != "video/mp4"
            or headers.get("Content-Length") != str(size)):
        _fail("upload_unavailable", "The upload service returned invalid transfer instructions.", idempotency_key=key)

    signed_headers = {"Content-Type": "video/mp4", "Content-Length": str(size)}
    if not _put_file(signed["url"], signed_headers, file):
        _fail(
            "transfer_failed", "Retry 'moviestar upload FILE --idempotency-key KEY' with the same file and reported key.",
            idempotency_key=key, reservation_id=reservation_id,
        )

    try:
        return {**_complete_result(endpoint, credential, reservation_id, file, size, key), "upload_performed": True}
    except UploadError as error:
        error.fields["upload_performed"] = True
        raise
