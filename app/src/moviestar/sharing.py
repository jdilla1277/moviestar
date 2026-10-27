"""Unlisted hosted-video handoffs for agents and their collaborators."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import click

from moviestar.account import _credential_for_connection, _read_state, _valid_email
from moviestar.upload import (
    FREE_LIMITS, MAX_FILE_BYTES, UploadError, _KEY_RE, _RESERVATION_RE,
    _json_request, _upload_url, upload_video,
)
from moviestar.download import configured_watch_origin, download, watch_link


def _emit(payload: dict) -> None:
    click.echo(json.dumps(payload, indent=2))


def _fail(command: str, status: str, hint: str, **fields: object) -> None:
    if command == "share":
        fields.setdefault("publication_outcome", "not_attempted")
        fields.setdefault("link_created", False)
    _emit({"command": command, "status": status, **fields, "hint": hint})
    raise click.exceptions.Exit(1)


def _owner_from_local_state() -> dict:
    connection = _read_state().get("connection")
    if isinstance(connection, dict):
        handle, email = connection.get("handle"), connection.get("email")
        if isinstance(handle, str) and isinstance(email, str):
            return {"connection": "local_record", "handle": handle, "email": email}
    return {"connection": "not_connected", "handle": None, "email": None}


def _connection(command: str) -> tuple[str, str, str]:
    if command in {"share", "shares"}:
        try:
            configured_watch_origin()
        except ValueError as error:
            _fail(command, "invalid_configuration", str(error))
    endpoint = _upload_url()
    credential = _credential_for_connection(_read_state())
    if not endpoint or not credential:
        _fail(command, "not_connected", "Run 'moviestar account connect EMAIL', then 'moviestar account status'.")
    return endpoint.removesuffix("/api/v1/videos/uploads"), endpoint, credential


def _timestamp(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _link(raw: object, origin: str, video_id: str | None = None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    if (not isinstance(raw.get("id"), str) or not _RESERVATION_RE.fullmatch(raw["id"])
            or not isinstance(raw.get("video_id"), str) or not _RESERVATION_RE.fullmatch(raw["video_id"])
            or (video_id is not None and raw["video_id"] != video_id)
            or raw.get("access") != "unlisted" or raw.get("forwardable") is not True
            or raw.get("downloadable") is not True or not _timestamp(raw.get("created_at"))):
        return None
    revoked = raw.get("revoked_at")
    if revoked is not None:
        if not _timestamp(revoked) or raw.get("url") is not None:
            return None
    else:
        if watch_link(raw.get("url"), allowed_origin=origin) is None:
            return None
    return {field: raw[field] for field in (
        "id", "video_id", "url", "access", "forwardable", "downloadable", "created_at", "revoked_at",
    )}


def _usage(value: object) -> dict | None:
    if not isinstance(value, dict) or not all(type(value.get(k)) is int and value[k] >= 0 for k in ("used_videos", "used_bytes")):
        return None
    return {k: value[k] for k in ("used_videos", "used_bytes")}


def _quota(value: object) -> dict | None:
    if not isinstance(value, dict) or value.get("changed") is not False:
        return None
    before, after = _usage(value.get("before")), _usage(value.get("after"))
    return {"before": before, "after": after, "changed": False} if before is not None and after == before else None


_ERRORS = {
    "not_connected": "Run 'moviestar account status' and reconnect if needed.",
    "not_found": "That video or link is unavailable in this account.",
    "consent_required": "Run 'moviestar account terms' to finish account setup, then retry.",
    "video_not_ready": "Finish media validation with 'moviestar upload FILE --resume VIDEO_ID' before sharing.",
    "link_revoked": "This key belongs to a revoked link. Use a new key to deliberately create another link.",
    "idempotency_conflict": "Use the original video for this key, or choose another idempotency key.",
    "over_quota": "Bring the account within its storage limits before publishing a new link.",
    "rate_limited": "Wait before retrying. A successful request can be retried with its original key.",
    "invalid_cursor": "Use the next_cursor from the previous page for this same listing.",
    "invalid_request": "Check the video, link, cursor, and idempotency key.",
    "publication_paused": "New link creation is paused. Existing links are unaffected; retry this video and key when publication resumes.",
    "sharing_unavailable": "Sharing is unavailable. Retry the same video and idempotency key later.",
}


def _service_failure(command: str, code: int, body: dict, **fields: object) -> None:
    status = body.get("status")
    if code == 401:
        status = "not_connected"
    if not isinstance(status, str) or status not in _ERRORS:
        status = "sharing_unavailable"
    hint = _ERRORS[status]
    if command == "share" and fields.get("publication_outcome") == "unknown":
        hint += (" The link may already be public; do not assume the video is private. "
                 f"Reconcile with 'moviestar share {fields['video_id']} --idempotency-key {fields['idempotency_key']}' "
                 "using the same video and key. If API and watch hosts differ, configure MOVIESTAR_WATCH_ORIGIN first.")
    _fail(command, status, hint, **fields)


_REJECTIONS = {
    400: {"invalid_request"}, 401: {"not_connected"}, 404: {"not_found"},
    409: {"consent_required", "video_not_ready", "link_revoked", "idempotency_conflict", "over_quota"},
    429: {"rate_limited"}, 503: {"publication_paused"},
}


def _publication_rejected(code: int, body: dict) -> bool:
    status = body.get("status")
    return isinstance(status, str) and status in _REJECTIONS.get(code, set())


@click.command("share")
@click.argument("target", metavar="FILE_OR_VIDEO_ID")
@click.option("--to", "recipient", metavar="EMAIL", help="Reserved for email delivery; live use is not available yet.")
@click.option("--idempotency-key", metavar="KEY", help="Reuse KEY with the same file or video after an interrupted handoff.")
@click.option("--dry-run", is_flag=True, help="Preview the handoff without uploading, publishing, or sending email.")
def share(target: str, recipient: str | None, idempotency_key: str | None, dry_run: bool) -> None:
    """Share an uploaded video, or upload and share a local finished MP4.

    Anyone holding the unlisted link can watch and download. It can be forwarded.
    Upload alone remains private. --to requires an exact email address and is
    reserved for a later email-delivery feature; live --to changes nothing.

    Example: moviestar share final.mp4 --idempotency-key handoff-1

    Retry the same file/key to recover its upload and original link. If the
    upload succeeds but publication cannot be confirmed, use its video_id and
    the same key to reconcile. An unknown outcome may already be public.
    """
    delivery = {"to": recipient, "would_send_email": recipient is not None}
    common = {"upload_performed": False, "email_sent": False, "link_created": False, "publication_outcome": "not_attempted", "delivery": delivery}
    if recipient is not None and not _valid_email(recipient):
        _fail("share", "error", "Ask the human for the recipient's exact email address, then retry.", **common)
    if recipient is not None and not dry_run:
        _fail("share", "not_available", "Email delivery is not available yet. Omit --to to create an unlisted link without sending email.", **common)
    if idempotency_key is not None and not _KEY_RE.fullmatch(idempotency_key):
        _fail("share", "invalid_request", "Use 1–128 letters, digits, underscores, or hyphens for the idempotency key.", **common)
    video_id = str(uuid.UUID(target)) if _RESERVATION_RE.fullmatch(target) else None
    file = None if video_id else Path(target).expanduser()
    details = None
    if file is not None:
        try:
            size = file.stat().st_size
            valid = file.is_file() and file.suffix.lower() == ".mp4" and 1 <= size <= MAX_FILE_BYTES
        except OSError:
            valid = False
        if not valid:
            _fail("share", "error", "Use a readable finished .mp4 between 1 byte and 500 MB, or a video UUID.", **common)
        details = {"path": str(file.resolve()), "size_bytes": size}
    if dry_run:
        owner = _owner_from_local_state()
        _emit({"command": "share", "status": "would_share", **common,
               **({"video_id": video_id} if video_id else {"file": details}), "owner": owner,
               "access": {"mode": "unlisted", "forwardable": True, "downloadable": True},
               "quota": {"plan": "unknown", "current_usage": "unavailable", "projected_usage": "unavailable",
                         "free_limits": FREE_LIMITS, "would_use_bytes": details["size_bytes"] if details else 0,
                         "would_use_videos": 1 if file else 0},
               "hint": "Connect with 'moviestar account connect EMAIL' before publishing; service readiness and quota are checked live. --to email delivery is not available yet."})
        return
    origin, endpoint, credential = _connection("share")
    key = idempotency_key or uuid.uuid4().hex
    click.echo(json.dumps({"command": "share", "status": "retry_key", "idempotency_key": key,
                           "hint": "If interrupted, retry the same file or video with --idempotency-key KEY."}), err=True)
    uploaded = False
    if file is not None:
        try:
            result = upload_video(file, idempotency_key=key, connection=(endpoint, credential))
        except UploadError as error:
            _fail("share", error.status, error.hint, **{**common, **error.fields, "idempotency_key": key})
        video_id = result.get("video_id")
        uploaded = result.get("upload_performed") is True
        if result.get("status") != "ready" or not isinstance(video_id, str) or not _RESERVATION_RE.fullmatch(video_id):
            _fail("share", "video_not_ready", _ERRORS["video_not_ready"], **{**common, "upload_performed": uploaded}, idempotency_key=key)
    fields = {**common, "upload_performed": uploaded, "video_id": video_id, "idempotency_key": key,
              "link_created": None, "publication_outcome": "unknown"}
    code, body = _json_request(f"{origin}/api/v1/videos/{video_id}/shares", credential, {"idempotency_key": key})
    if code not in {200, 201} or not isinstance(body.get("status"), str) or body["status"] not in {"shared", "already_shared"}:
        if _publication_rejected(code, body):
            fields.update(link_created=False, publication_outcome="rejected")
        _service_failure("share", code, body, **fields)
    item, quota = _link(body.get("link"), origin, video_id), _quota(body.get("quota"))
    if not item or item["revoked_at"] is not None or body.get("video_id") != video_id or quota is None:
        _service_failure("share", 0, {}, **fields)
    _emit({"command": "share", "status": body["status"], **fields,
           "link_created": body["status"] == "shared", "publication_outcome": body["status"], "link_id": item["id"], "url": item["url"],
           "link": item, "quota": quota, "quota_scope": "link_publication",
           "hint": "Anyone with this unlisted link can watch, download, and forward it. No email was sent."})


@click.group("shares", invoke_without_command=True)
@click.option("--video", "video_id", metavar="VIDEO_ID", help="List only links for this uploaded video.")
@click.option("--cursor", metavar="LINK_ID", help="Continue using the prior response's next_cursor and the same --video scope.")
@click.pass_context
def shares(ctx: click.Context, video_id: str | None, cursor: str | None) -> None:
    """List owned share links, including revoked history, or preview deletion.

    The list returns one page and next_cursor. Use --video to filter a video.
    Deletion of a video and all its links is a separate, future operation.
    """
    if ctx.invoked_subcommand is not None:
        return
    if any(value is not None and not _RESERVATION_RE.fullmatch(value) for value in (video_id, cursor)):
        _fail("shares", "invalid_request", _ERRORS["invalid_request"])
    video_id = str(uuid.UUID(video_id)) if video_id else None
    origin, _, credential = _connection("shares")
    path = f"/api/v1/videos/{video_id}/shares" if video_id else "/api/v1/shares"
    code, body = _json_request(origin + path + (f"?cursor={cursor}" if cursor else ""), credential, method="GET")
    if code != 200 or body.get("status") != "ok":
        _service_failure("shares", code, body)
    raw = body.get("links")
    items = [_link(item, origin, video_id) for item in raw] if isinstance(raw, list) and len(raw) <= 50 else None
    next_cursor, quota = body.get("next_cursor"), _usage(body.get("quota"))
    if (items is None or any(item is None for item in items) or quota is None
            or (next_cursor is not None and (not isinstance(next_cursor, str) or not _RESERVATION_RE.fullmatch(next_cursor)))):
        _service_failure("shares", 0, {})
    _emit({"command": "shares", "status": "ok", "links": items, "next_cursor": next_cursor,
           "quota": quota, **({"video_id": video_id} if video_id else {})})


@shares.command("delete")
@click.argument("video_id")
@click.option("--dry-run", is_flag=True, help="Preview deletion without removing the video.")
def delete_video(video_id: str, dry_run: bool) -> None:
    """Preview deleting a video and all its links; live deletion is unavailable.

    Unlike unshare, deletion would free storage. Downloaded copies cannot be recalled.
    """
    if not dry_run:
        _fail("shares delete", "not_available", "Video deletion is not available yet.", video_id=video_id, video_deleted=False)
    _emit({"command": "shares delete", "status": "would_delete_video", "video_id": video_id,
                     "video_deleted": False, "all_links_revoked": False, "quota_released": False})


@click.command("unshare")
@click.argument("link_id")
@click.option("--dry-run", is_flag=True, help="Preview revocation without changing the link.")
def unshare(link_id: str, dry_run: bool) -> None:
    """Revoke one link, preserving its video, other links, and storage quota.

    Downloads cannot be recalled. Previously issued media URLs may remain valid
    for up to 60 seconds. Video deletion is a separate future operation.
    """
    fields = {"link_id": link_id, "link_revoked": False, "video_deleted": False, "quota_released": False}
    if dry_run:
        _emit({"command": "unshare", "status": "would_revoke_link", **fields})
        return
    if not _RESERVATION_RE.fullmatch(link_id):
        _fail("unshare", "invalid_request", "Use the link_id returned by share or shares.", **fields)
    link_id = str(uuid.UUID(link_id))
    fields["link_id"] = link_id
    origin, _, credential = _connection("unshare")
    code, body = _json_request(f"{origin}/api/v1/shares/{link_id}", credential, method="DELETE")
    if code != 200 or body.get("status") not in {"revoked", "already_revoked"}:
        _service_failure("unshare", code, body, **fields)
    item, quota = _link(body.get("link"), origin), _quota(body.get("quota"))
    if not item or item["id"] != link_id or not item["revoked_at"] or quota is None or body.get("video_deleted") is not False:
        _service_failure("unshare", 0, {}, **fields)
    _emit({"command": "unshare", "status": body["status"], **fields, "link_revoked": True,
           "video_id": item["video_id"], "link": item, "quota": quota})
