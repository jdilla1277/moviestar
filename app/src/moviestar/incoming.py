"""Accepted finished videos shared with a connected recipient account."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

import click

from moviestar.account import (
    _CLIENT_MARKER, _NoRedirectHandler, _credential_for_connection,
    _read_state, _service_url_error,
)
from moviestar.download import MAX_DOWNLOAD_BYTES, _origin, _save_mp4

_CONTRACT = "recipient-videos-v1"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_FIELDS = ("id", "filename", "shared_by", "accepted_at", "size_bytes", "duration_ms", "width", "height", "permissions", "watch_path", "download_path")
_HINTS = {
    "not_connected": "Run 'moviestar account status' and reconnect this installation if necessary.",
    "account_action_required": "Run 'moviestar account terms' and ask the human to finish account setup/current consent in their browser. Then retry.",
    "invalid_cursor": "Start again with 'moviestar shared videos', then use its next_cursor unchanged.",
    "not_found": "This video is unavailable to the connected account. The sender may have removed access, or the video may be unavailable. Refresh 'moviestar shared videos'.",
    "service_disabled": "Recipient video access is not enabled on this service. Retry when access is available.",
    "rate_limited": "Wait before retrying this read or download.",
    "shared_videos_unavailable": "Shared videos are temporarily unavailable. Retry later; do not accept an invitation or reconnect automatically.",
    "download_unavailable": "The private transfer could not be confirmed. Retry the same video later; no partial destination was published.",
}


def _fail(command: str, status: str, hint: str | None = None, **fields) -> None:
    click.echo(json.dumps({"command": command, "status": status, **fields, "hint": hint or _HINTS[status]}, indent=2))
    raise click.exceptions.Exit(1)


def _validated_origin(url: object) -> str:
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url:
        raise ValueError("invalid_configuration")
    parsed = urlsplit(url)
    if _service_url_error(url) or parsed.path != "/api/v1/account/installation" or parsed.query:
        raise ValueError("invalid_configuration")
    return _origin(url)  # Also validates the port before any credential lookup.


def _connection(command: str, **fields) -> tuple[str, str]:
    state = _read_state()
    if not isinstance(state.get("connection"), dict):
        _fail(command, "not_connected", "Run 'moviestar account connect EMAIL', have the human approve access, then run 'moviestar account status'.", **fields)
    recorded = state.get("installation_url")
    override = os.environ.get("MOVIESTAR_ACCOUNT_INSTALLATION_URL")
    try:
        origin = _validated_origin(override or recorded)
        if recorded is not None and _validated_origin(recorded) != origin:
            raise ValueError("origin_changed")
    except ValueError:
        _fail(command, "invalid_configuration", "Use the service that approved this installation. Remove a conflicting MOVIESTAR_ACCOUNT_INSTALLATION_URL or reconnect explicitly to the intended service.", **fields)
    credential = _credential_for_connection(state)
    if not isinstance(credential, str) or not re.fullmatch(r"mvs_[A-Za-z0-9_-]{43}", credential):
        _fail(command, "not_connected", **fields)
    return origin, credential


def _open(url: str, credential: str, *, method: str = "GET", accept: str = "video/mp4"):
    request = Request(url, headers={"Authorization": f"Bearer {credential}", "X-Moviestar-Client": _CLIENT_MARKER,
                                   "Accept": accept, "Accept-Encoding": "identity"}, method=method)
    try:
        return build_opener(_NoRedirectHandler()).open(request, timeout=60)
    except HTTPError as response:
        return response


def _failure_status(code: int, *, listing: bool) -> str:
    return {401: "not_connected", 409: "account_action_required", 429: "rate_limited",
            400: "invalid_cursor" if listing else "download_unavailable",
            404: "service_disabled" if listing else "not_found", 410: "not_found"}.get(code, "shared_videos_unavailable" if listing else "download_unavailable")


def _video(raw: object) -> dict | None:
    if not isinstance(raw, dict) or any(key not in raw for key in _FIELDS):
        return None
    video_id = raw["id"]
    if not isinstance(video_id, str) or not _UUID.fullmatch(video_id) or video_id != video_id.lower():
        return None
    if (not isinstance(raw["filename"], str) or not 1 <= len(raw["filename"]) <= 4096
            or not isinstance(raw["shared_by"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_]{2,29}", raw["shared_by"])
            or not isinstance(raw["accepted_at"], str) or len(raw["accepted_at"]) > 64
            or not all(type(raw[key]) is int and 0 < raw[key] <= 2**53 - 1 for key in ("size_bytes", "duration_ms", "width", "height"))
            or raw["permissions"] != ["watch", "download"]
            or raw["watch_path"] != f"/account/shared-videos/{video_id}"
            or raw["download_path"] != f"/api/v1/shared-videos/{video_id}/download"):
        return None
    try:
        if datetime.fromisoformat(raw["accepted_at"].replace("Z", "+00:00")).tzinfo is None:
            return None
    except ValueError:
        return None
    return {key: raw[key] for key in _FIELDS}


@click.group()
def shared():
    """Find and download accepted videos shared with your connected account.

    The human accepts invitations in the browser. These commands never accept
    invitations, create share links, or approve an installation.
    """


@shared.command("videos")
@click.option("--cursor", metavar="CURSOR", help="Use next_cursor from this account's previous page unchanged.")
def videos(cursor: str | None) -> None:
    """Read up to 50 accepted, currently accessible finished videos.

    Pending invitations stay in the human's email/browser. Connect this
    installation to the recipient account first. This GET does not accept
    invitations or download files. Repeat with --cursor to page explicitly.
    """
    command = "shared videos"
    if cursor is not None and not _UUID.fullmatch(cursor):
        _fail(command, "invalid_cursor")
    origin, credential = _connection(command)
    endpoint = origin + "/api/v1/shared-videos" + ("?" + urlencode({"cursor": cursor}) if cursor else "")
    try:
        with _open(endpoint, credential, accept="application/json") as response:
            if response.status != 200:
                _fail(command, _failure_status(response.status, listing=True))
            if response.headers.get_content_type() != "application/json":
                raise ValueError("invalid_response")
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("invalid_response")
        body = json.loads(raw)
    except (HTTPException, URLError, TimeoutError, OSError, ValueError, RecursionError):
        _fail(command, "shared_videos_unavailable")
    if not isinstance(body, dict) or body.get("status") != "ok":
        _fail(command, "invalid_response", "The service returned an incomplete shared-video page. Retry later.")
    if body.get("contract_version") != _CONTRACT:
        _fail(command, "unsupported_contract", "This service uses an unsupported recipient-video contract. Update the CLI or check the connected service.")
    rows, next_cursor = body.get("videos"), body.get("next_cursor")
    parsed = [_video(row) for row in rows] if isinstance(rows, list) and len(rows) <= 50 else None
    if (parsed is None or any(row is None for row in parsed) or "next_cursor" not in body
            or len({row["id"] for row in parsed if row is not None}) != len(parsed)
            or (next_cursor is not None and (not isinstance(next_cursor, str) or not _UUID.fullmatch(next_cursor) or next_cursor == cursor))):
        _fail(command, "invalid_response", "The service returned an incomplete or unsafe shared-video page. Retry later.")
    hint = "Download an accepted video with 'moviestar shared download VIDEO_ID --out FILE'."
    if next_cursor:
        hint += f" Read the next page with 'moviestar shared videos --cursor {next_cursor}'."
    elif not parsed:
        hint = "No accepted videos are currently accessible. The human must accept a matching invitation in their browser; removed or unavailable videos are not listed."
    click.echo(json.dumps({"status": "ok", "contract_version": _CONTRACT, "videos": parsed, "next_cursor": next_cursor, "hint": hint}, indent=2))


def _transfer(endpoint: str, credential: str, destination: Path) -> int:
    # Private GET is chunked. HEAD provides a fresh bounded expected length;
    # neither request follows a redirect or trusts a server-supplied path.
    with _open(endpoint, credential, method="HEAD") as head:
        if head.status != 200:
            raise ValueError(_failure_status(head.status, listing=False))
        length = head.headers.get("Content-Length", "")
        if (head.headers.get_content_type() != "video/mp4" or head.headers.get("Content-Encoding", "identity").lower() != "identity"
                or not re.fullmatch(r"[0-9]{1,10}", length) or not 1 <= int(length) <= MAX_DOWNLOAD_BYTES):
            raise ValueError("download_unavailable")
        expected = int(length)
    with _open(endpoint, credential) as response:
        if response.status != 200:
            raise ValueError(_failure_status(response.status, listing=False))
        actual = response.headers.get("Content-Length")
        if (response.headers.get_content_type() != "video/mp4" or response.headers.get("Content-Encoding", "identity").lower() != "identity"
                or (actual is not None and (not re.fullmatch(r"[0-9]{1,10}", actual) or int(actual) != expected))):
            raise ValueError("download_unavailable")
        return _save_mp4(response, destination, expected)


@shared.command("download")
@click.argument("video_id")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), metavar="FILE", help="Save here; defaults to VIDEO_ID.mp4. Existing files are never overwritten.")
@click.option("--dry-run", is_flag=True, help="Preview offline without credentials, network requests, or file writes. Access is not verified.")
def download(video_id: str, out: Path | None, dry_run: bool) -> None:
    """Save an accepted finished VIDEO_ID using this approved installation.

    Access is checked for every HEAD/GET; a saved list is not authorization.
    Redirects are refused and the account bearer stays on the configured
    service. Transfers are bounded to 1 GB and incomplete files are removed.
    The filename defaults to the UUID, never a server-provided filesystem path.
    For anonymous unlisted links, keep using 'moviestar download WATCH_URL'.
    """
    command = "shared download"
    fields = {"download_performed": False, "account_required": True}
    if not _UUID.fullmatch(video_id):
        _fail(command, "invalid_request", "Use a video UUID from 'moviestar shared videos'.", **fields)
    video_id = video_id.lower()
    destination = (out or Path(f"{video_id}.mp4")).expanduser().absolute()
    fields.update(video_id=video_id, out=str(destination))
    if os.path.lexists(destination):
        _fail(command, "error", "Choose another --out path; downloads never overwrite an existing file.", **fields)
    if dry_run:
        preview = {key: value for key, value in fields.items() if key != "out"}
        click.echo(json.dumps({"command": command, "status": "would_download", **preview, "dry_run": True,
                               "would_write_to": str(destination), "access_verified": False,
                               "hint": "This is an offline preview. The live command requires the recipient's approved installation and accepted video access."}, indent=2))
        return
    origin, credential = _connection(command, **fields)
    try:
        size = _transfer(f"{origin}/api/v1/shared-videos/{video_id}/download", credential, destination)
    except FileExistsError:
        _fail(command, "error", "The destination appeared during download. Choose another --out; the existing file was preserved.", **fields)
    except (HTTPException, URLError, TimeoutError, OSError, ValueError) as error:
        status = str(error) if type(error) is ValueError and str(error) in _HINTS else "download_unavailable"
        _fail(command, status, **fields)
    click.echo(json.dumps({"command": command, "status": "downloaded", **fields, "download_performed": True, "size_bytes": size,
                           "hint": "The received MP4 was saved. The sender can remove future access, but cannot recall this downloaded copy."}, indent=2))
