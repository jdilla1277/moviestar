"""Private finished-video upload for a connected MovieStar account."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
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


def _fail(status: str, hint: str, **fields: object) -> None:
    _emit(status, hint=hint, **fields)
    raise click.exceptions.Exit(1)


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


def _json_request(url: str, credential: str, payload: dict | None = None) -> tuple[int, dict]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else b"",
        headers={
            "Authorization": f"Bearer {credential}",
            "x-moviestar-client": _CLIENT_MARKER,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=30) as response:
            status, body = response.status, response.read(65_537)
    except HTTPError as error:
        status, body = error.code, error.read(65_537)
    except (URLError, TimeoutError, OSError):
        return 0, {}
    if len(body) > 65_536:
        return status, {}
    try:
        result = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
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


def _complete_and_emit(endpoint: str, credential: str, reservation_id: str,
                       file: Path, size: int, key: str | None) -> None:
    status, completion = _json_request(f"{endpoint}/{reservation_id}/complete", credential)
    if status not in {200, 201} or completion.get("status") != "stored_unverified" or not isinstance(completion.get("video_id"), str):
        _fail(
            completion.get("status", "upload_unavailable") if status else "upload_unavailable",
            "Retry 'moviestar upload FILE --resume RESERVATION_ID' with the reported reservation ID.",
            reservation_id=reservation_id, **({"idempotency_key": key} if key else {}),
        )
    _emit("stored_unverified", video_id=completion["video_id"], filename=file.name,
          size_bytes=completion.get("size_bytes", size), reservation_id=reservation_id,
          **({"idempotency_key": key} if key else {}),
          hint="The file is stored privately. It cannot be shared until media validation is available.")


@click.command("upload")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--idempotency-key", metavar="KEY", help="Reuse KEY when retrying an interrupted upload of this same file.")
@click.option("--resume", metavar="RESERVATION_ID", help="Retry completion of a transferred file without uploading it again.")
@click.option("--dry-run", is_flag=True, help="Preview file size, account target, and Free limits without reserving or uploading.")
def upload(file: Path, idempotency_key: str | None, resume: str | None, dry_run: bool) -> None:
    """Store a finished MP4 privately in your MovieStar account.

    Upload does not create or send a share link. Connect first with
    ``moviestar account connect EMAIL`` and confirm with account status.
    If transfer fails, retry with the reported idempotency key. If completion
    fails, retry with ``--resume`` and the reported reservation ID.

    Example: moviestar upload final.mp4
    """
    if file.suffix.lower() != ".mp4":
        _fail("invalid_file", "Export a finished .mp4 and retry.")
    try:
        size = file.stat().st_size
    except OSError:
        _fail("invalid_file", "The video file cannot be read.")
    if size < 1 or size > MAX_FILE_BYTES:
        _fail("invalid_file", "Use an MP4 between 1 byte and 500 MB.", size_bytes=size)
    state = _read_state()
    credential = _credential_for_connection(state)
    endpoint = _upload_url()
    if not credential or not endpoint:
        _fail("not_connected", "Run 'moviestar account connect EMAIL', then 'moviestar account status'.")

    if dry_run:
        if resume is not None:
            _fail("invalid_request", "Use --dry-run for a new upload, or --resume to retry completion.")
        connection = state.get("connection")
        owner = connection if isinstance(connection, dict) else {}
        parsed = urlsplit(endpoint)
        _emit("would_upload", file={"path": str(file.resolve()), "size_bytes": size},
              account={"handle": owner.get("handle"), "email": owner.get("email"),
                       "service_origin": f"{parsed.scheme}://{parsed.netloc}"},
              quota={"free_limits": FREE_LIMITS, "would_use_videos": 1,
                     "would_use_bytes": size, "current_usage": "unavailable",
                     "projected_usage": "unavailable"},
              upload_performed=False, reservation_created=False,
              hint="Current usage is checked by the service when the upload starts.")
        return

    key = idempotency_key or uuid.uuid4().hex
    if not _KEY_RE.fullmatch(key):
        _fail("invalid_request", "Use 1–128 letters, digits, underscores, or hyphens for the idempotency key.")

    if resume is not None:
        if not _RESERVATION_RE.fullmatch(resume):
            _fail("invalid_request", "Use the reservation ID reported by the failed upload.")
        _complete_and_emit(endpoint, credential, resume, file, size, None)
        return

    status, initiation = _json_request(endpoint, credential, {
        "filename": file.name, "size_bytes": size, "idempotency_key": key,
    })
    if status == 200 and initiation.get("status") == "already_completed":
        reservation_id = initiation.get("reservation_id")
        if not isinstance(reservation_id, str) or not _RESERVATION_RE.fullmatch(reservation_id):
            _fail("upload_unavailable", "The upload service returned an invalid reservation.", idempotency_key=key)
        _complete_and_emit(endpoint, credential, reservation_id, file, size, key)
        return
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

    _complete_and_emit(endpoint, credential, reservation_id, file, size, key)
