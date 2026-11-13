"""Create a private account original using the approved CLI installation."""

from __future__ import annotations

import json
import re
import uuid
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import click

from moviestar.account import _CLIENT_MARKER, _NoRedirectHandler
from moviestar.cloud import _connection
from moviestar.upload import _safe_signed_url

COMMAND = "cloud assets create"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
# Admission only: the service validates the original bytes and live allowance.
_FORMATS = {
    "mp4": ("video", "mp4", "video/mp4"), "mov": ("video", "mov", "video/quicktime"),
    "webm": ("video", "webm", "video/webm"), "mp3": ("audio", "mp3", "audio/mpeg"),
    "wav": ("audio", "wav", "audio/wav"), "m4a": ("audio", "m4a", "audio/mp4"),
    "flac": ("audio", "flac", "audio/flac"), "jpg": ("image", "jpeg", "image/jpeg"),
    "jpeg": ("image", "jpeg", "image/jpeg"), "png": ("image", "png", "image/png"),
    "webp": ("image", "webp", "image/webp"), "gif": ("image", "gif", "image/gif"),
    "srt": ("transcript", "srt", "application/x-subrip"),
    "vtt": ("transcript", "vtt", "text/vtt"), "txt": ("transcript", "txt", "text/plain"),
}
_MAX_BYTES = {"video": 1_000_000_000, "audio": 1_000_000_000, "image": 32 * 1024 * 1024, "transcript": 262144}
_HINTS = {
    "credential_invalid": "Run 'moviestar account status' and reconnect this installation if needed.",
    "account_consent_required": "Review the current terms on your account website with 'moviestar account open'.",
    "asset_limit": "Your account's asset slots are full. Free a slot or upgrade on the account website.",
    "storage_limit": "This original does not fit the account's remaining storage allowance.",
    "file_too_large": "This original exceeds your account's per-file limit.",
    "existing_video_over_limit": "An existing asset exceeds your current plan's per-file limit. Manage storage on the website.",
    "payment_grace": "Uploads are paused while your account's renewal payment needs attention. Open the account website.",
    "account_deleting": "This account is being deleted and cannot accept uploads.",
    "unsupported_format": "Choose MP4, MOV, WebM, MP3, WAV, M4A, FLAC, JPEG, PNG, WebP, GIF, SRT, VTT, or TXT. Renaming a file does not convert it.",
    "invalid_size": "Use a nonempty file within the format's size limit; the service also enforces your account allowance.",
    "invalid_request": "Check the filename, upload ID and idempotency key in the command help.",
    "idempotency_conflict": "Retry the original file and key. Use a new key only to deliberately create another asset.",
    "upload_conflict": "Stored bytes differ from this upload's declaration. Correct the file and start a new upload with a new key.",
    "upload_incomplete": "Retry the same file with its original --idempotency-key to finish transferring it.",
    "upload_expired": "The reservation expired. Start again with a new idempotency key.",
    "asset_invalid": "The original failed server format validation. Correct the file and start a new upload with a new key.",
    "not_found": "The upload is unavailable to this account. Check the upload ID and connected account.",
    "unsupported_operation": "This service does not support account asset creation yet. Update the service or retry after rollout.",
    "asset_unavailable": "The outcome is uncertain. Retry the same file and idempotency key, or use --resume with the upload ID if provided.",
    "transfer_failed": "The transfer outcome is uncertain. Retry the same file with its original --idempotency-key.",
    "invalid_response": "The service returned incomplete upload instructions. Preserve the retry key and upload ID; retry after checking the service.",
}


def _fail(code: str, **fields):
    click.echo(json.dumps({"command": COMMAND, "status": code, "hint": _HINTS[code], **fields}, indent=2))
    raise click.exceptions.Exit(1)


def _request(url: str, credential: str, payload=None, key: str | None = None):
    request = Request(url, method="POST", data=json.dumps(payload).encode() if payload is not None else b"",
        headers={"Authorization": f"Bearer {credential}", "x-moviestar-client": _CLIENT_MARKER,
            "Content-Type": "application/json", "Accept": "application/json", **({"Idempotency-Key": key} if key else {})})
    try:
        try:
            response = build_opener(_NoRedirectHandler()).open(request, timeout=300)
        except HTTPError as error:
            response = error
        with response:
            status, raw = response.status, response.read(65_537)
        if len(raw) > 65_536:
            return status, {}
        body = json.loads(raw)
        return status, body if isinstance(body, dict) else {}
    except (URLError, HTTPException, OSError, ValueError):
        return 0, {}


def _failure(status, body, *, reservation=False, **fields):
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if status == 401: code = "credential_invalid"
    if reservation and status in {404, 405}: code = "unsupported_operation"
    _fail(code if isinstance(code, str) and code in _HINTS else "asset_unavailable", **fields)


def _asset(raw, file: Path, size: int, descriptor):
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not _UUID.fullmatch(raw["id"]):
        return None
    kind, format_, mime = descriptor
    if (raw.get("filename") != file.name or type(raw.get("size_bytes")) is not int or raw["size_bytes"] != size
            or (raw.get("kind"), raw.get("format"), raw.get("content_type")) != (kind, format_, mime)):
        return None
    # Return public identity/declaration only; never echo arbitrary backend fields.
    return {name: raw[name] for name in ("id", "filename", "size_bytes", "kind", "format", "content_type", "state") if name in raw}


def _put(url, headers, file: Path, size: int):
    try:
        with file.open("rb") as stream:
            if file.stat().st_size != size: return False
            request = Request(url, method="PUT", headers=headers, data=stream)
            try:
                response = build_opener(_NoRedirectHandler()).open(request, timeout=300)
            except HTTPError as error:
                response = error
            with response:
                # A lost successful conditional PUT is safe to replay. Completion
                # checks the staged object against the original declaration.
                return 200 <= response.status < 300 or response.status == 412
    except (URLError, HTTPException, OSError):
        return False


@click.command("create")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--idempotency-key", metavar="KEY", help="Reuse this key and original file after an interruption. Generated when omitted.")
@click.option("--resume", metavar="UPLOAD_ID", help="Retry completion/validation only; no file transfer or new reservation.")
def create_asset(file: Path, idempotency_key: str | None, resume: str | None):
    """Upload FILE privately to your connected account, without a project.

    Supports video, audio, image and UTF-8 caption/transcript originals. The
    service validates the content and enforces your shared account allowance.
    This does not attach the asset to a project or publish a share link.

    No FFmpeg or transcription stack is required. A generated retry key is
    printed to stderr before the first request; stdout contains one JSON result.
    Keep the original file unchanged when retrying a key. --resume uses the
    upload_id, not the asset ID, and requires the same original file declaration.
    """
    descriptor = _FORMATS.get(file.suffix.lower().lstrip("."))
    if descriptor is None: _fail("unsupported_format")
    if (not file.name or file.name != file.name.strip() or len(file.name.encode("utf-8")) > 240
            or re.search(r"[/\\\x00-\x1f\x7f]", file.name)):
        _fail("invalid_request")
    try:
        size = file.stat().st_size
    except OSError:
        _fail("invalid_request")
    if size < 1 or size > _MAX_BYTES[descriptor[0]]:
        _fail("invalid_size", size_bytes=size, max_format_bytes=_MAX_BYTES[descriptor[0]])
    if (idempotency_key is not None and not _KEY.fullmatch(idempotency_key)) or (resume is not None and not _UUID.fullmatch(resume)):
        _fail("invalid_request")
    cloud_endpoint, credential = _connection(COMMAND)
    endpoint = cloud_endpoint.removesuffix("/account/cloud") + "/assets/uploads"
    key = idempotency_key or (uuid.uuid4().hex if resume is None else None)
    common = {"idempotency_key": key} if key else {}
    if resume is None:
        click.echo(json.dumps({"command": COMMAND, "status": "retry_key", "idempotency_key": key,
            "hint": "If interrupted, retry the same original with --idempotency-key KEY."}), err=True)
        status, body = _request(endpoint, credential, {"filename": file.name, "size_bytes": size}, key)
        if status not in {200, 201}: _failure(status, body, reservation=True, **common)
        upload_id = body.get("upload_id")
        if not isinstance(upload_id, str) or not _UUID.fullmatch(upload_id): _fail("invalid_response", **common)
        common["upload_id"] = upload_id
        expected = _asset(body.get("asset"), file, size, descriptor)
        if expected is None or body.get("idempotency_key") != key: _fail("invalid_response", **common)
        common["asset_id"] = expected["id"]
        grant = body.get("upload")
        if grant is not None:
            headers = {"Content-Type": descriptor[2], "Content-Length": str(size), "If-None-Match": "*"}
            if (not isinstance(grant, dict) or grant.get("method") != "PUT"
                    or not _safe_signed_url(grant.get("url")) or grant.get("headers") != headers):
                _fail("invalid_response", **common)
            if not _put(grant["url"], headers, file, size): _fail("transfer_failed", **common)
    else:
        upload_id = resume
        common["upload_id"] = upload_id
    status, completed = _request(f"{endpoint}/{upload_id}/complete", credential)
    if status != 200: _failure(status, completed, **common)
    ready = _asset(completed.get("asset"), file, size, descriptor)
    if (ready is None or ready.get("state") != "ready" or completed.get("upload_id") != upload_id
            or (common.get("asset_id") and ready["id"] != common["asset_id"])):
        _fail("invalid_response", **common)
    click.echo(json.dumps({"command": COMMAND, "status": "ready", "asset": ready, **common,
        "hint": "The original is stored privately in your account. No project reference or share link was created."}, indent=2))


def register_asset_upload(cloud: click.Group):
    # The project-reference client also contributes to this group. Register
    # after cloud's imports so both clients keep their own commands when combined.
    assets = cloud.commands.get("assets")
    if assets is None:
        assets = click.Group("assets", help="Work with account-owned assets on the connected service.")
        cloud.add_command(assets)
    assets.help = "Upload private originals or find ready account assets on the connected service."
    assets.add_command(create_asset)
