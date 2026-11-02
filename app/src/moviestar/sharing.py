"""Unlisted hosted-video handoffs for agents and their collaborators."""
from __future__ import annotations

import json
import re
import shlex
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import click

from moviestar.account import _credential_for_connection, _read_state
from moviestar.upload import (
    FREE_LIMITS, MAX_FILE_BYTES, UploadError, _KEY_RE, _RESERVATION_RE,
    _json_request, _upload_url, upload_video,
)
from moviestar.download import configured_watch_origin, download, watch_link


def _emit(payload: dict) -> None:
    click.echo(json.dumps(payload, indent=2))


def _fail(command: str, status: str, hint: str, **fields: object) -> None:
    if status == "consent_required":
        message = "Accept the Terms of Service before sharing."
        fields["error"] = message
        hint = ("Run 'moviestar account terms' to review the policies and get VERSION. "
                "After the human agrees and confirms they are at least 18, run "
                "'moviestar account terms --accept VERSION --age-18-or-older'. " + hint)
        click.echo(f"{message} {hint}", err=True)
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


def _connection(command: str, **failure_fields: object) -> tuple[str, str, str]:
    if command in {"share", "shares"}:
        try:
            configured_watch_origin()
        except ValueError as error:
            _fail(command, "invalid_configuration", str(error), **failure_fields)
    endpoint = _upload_url()
    credential = _credential_for_connection(_read_state())
    if not endpoint or not credential:
        _fail(command, "not_connected", "Run 'moviestar account connect EMAIL', then 'moviestar account status'.", **failure_fields)
    return endpoint.removesuffix("/api/v1/videos/uploads"), endpoint, credential


def _timestamp(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _link(raw: object, origin: str, video_id: str | None = None) -> dict | None:
    fields = ("id", "video_id", "url", "access", "forwardable", "downloadable", "created_at", "revoked_at")
    if not isinstance(raw, dict) or any(field not in raw for field in fields):
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
    return {field: raw[field] for field in fields}


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
    "consent_required": "Then retry the same video and idempotency key.",
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
        recipient = fields.get("delivery", {}).get("to")
        email_option = f" --to {shlex.quote(recipient)}" if isinstance(recipient, str) else ""
        hint += (" The link may already be public; do not assume the video is private. "
                 f"Reconcile with 'moviestar share {fields['video_id']} --idempotency-key {fields['idempotency_key']}{email_option}' "
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


def _valid_recipient(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 254 or value.count("@") != 1:
        return False
    local, domain = value.split("@")
    return (0 < len(local) <= 64 and re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+", local) is not None
            and not local.startswith(".") and not local.endswith(".") and ".." not in local
            and "." in domain and all(len(part) <= 63 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", part) for part in domain.split("."))
            and re.fullmatch(r"[A-Za-z]{2,63}", domain.split(".")[-1]) is not None)


def _delivery(raw: object, link_id: str, recipient: str | None = None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    fields = ("id", "link_id", "to", "status", "created_at", "accepted_at")
    if (any(k not in raw for k in fields) or not isinstance(raw["id"], str) or not _RESERVATION_RE.fullmatch(raw["id"])
            or raw["link_id"] != link_id or not _valid_recipient(raw["to"])
            or (recipient is not None and raw["to"] != recipient)
            or not isinstance(raw["status"], str) or raw["status"] not in {"pending", "accepted", "failed", "unknown"} or not _timestamp(raw["created_at"])
            or (raw["status"] == "accepted" and not _timestamp(raw["accepted_at"]))
            or (raw["status"] != "accepted" and raw["accepted_at"] is not None)):
        return None
    return {k: raw[k] for k in fields}


def _email_request(command: str, link_id: str, recipient: str, key: str, origin: str,
                   credential: str, fields: dict, *, success_status: str | None = None) -> None:
    code, body = _json_request(f"{origin}/api/v1/shares/{link_id}/deliveries", credential,
                               {"to": recipient, "idempotency_key": key})
    item = _delivery(body.get("delivery"), link_id, recipient)
    status = body.get("status")
    retry = (f"moviestar shares email {link_id} --to {shlex.quote(recipient)} --idempotency-key {key}")
    safe = {**fields, "link_id": link_id, "idempotency_key": key, "email_sent": None,
            "delivery": item or {"to": recipient, "status": "pending"}}
    if code == 200 and isinstance(status, str) and status in {"accepted", "already_accepted"} and item and item["status"] == "accepted" and body.get("email_sent") is True:
        _emit({"command": command, "status": success_status or status, **safe, "email_sent": True, "email_outcome": status,
               "hint": "The email provider accepted this message; inbox delivery is not confirmed. Anyone with the link can watch, download, and forward it."})
        return
    if item and isinstance(status, str) and ((code == 202 and status in {"delivery_pending", "in_progress"} and item["status"] == "pending")
                 or (code == 409 and status == "delivery_unknown" and item["status"] == "unknown")
                 or (code == 502 and status == "email_failed" and item["status"] == "failed")):
        safe["email_sent"] = False if item["status"] == "failed" else None
        hint = ("Delivery is uncertain. Check with the recipient before deliberately sending again with a new key."
                if item["status"] == "unknown" else f"Email is not confirmed. Recover this request with: {retry}")
        _fail(command, status, hint, **safe)
    known = {400: {"invalid_request"}, 401: {"not_connected"}, 404: {"not_found"},
             409: {"consent_required", "video_not_ready", "link_revoked", "idempotency_conflict"},
             429: {"rate_limited"}, 503: {"email_delivery_paused"}}
    if isinstance(status, str) and status in known.get(code, set()):
        _fail(command, status, f"Email was not confirmed. Check account status and the link, then retry the same request: {retry}", **safe)
    _fail(command, "delivery_pending", f"The email response could not be verified; it may have been sent. Recover with the same recipient and key: {retry}", **safe)


@click.command("share")
@click.argument("target", metavar="FILE_OR_VIDEO_ID")
@click.option("--to", "recipient", metavar="EMAIL", help="Email the forwardable unlisted link to one exact address supplied by the human.")
@click.option("--idempotency-key", metavar="KEY", help="Reuse KEY with the same file or video after an interrupted handoff.")
@click.option("--dry-run", is_flag=True, help="Preview the handoff without uploading, publishing, or sending email.")
def share(target: str, recipient: str | None, idempotency_key: str | None, dry_run: bool) -> None:
    """Share an uploaded video, or upload and share a local finished MP4.

    Anyone holding the unlisted link can watch and download. It can be forwarded.
    Upload alone remains private. --to requires an exact email address and is
    sends that person the forwardable link, without requiring an account.

    Example: moviestar share final.mp4 --idempotency-key handoff-1

    Retry the same file/key to recover its upload and original link. If the
    upload succeeds but publication cannot be confirmed, use its video_id and
    the same key to reconcile. An unknown outcome may already be public.
    """
    delivery = {"to": recipient, "would_send_email": recipient is not None}
    common = {"upload_performed": False, "email_sent": False, "link_created": False, "publication_outcome": "not_attempted", "delivery": delivery}
    if recipient is not None and not _valid_recipient(recipient):
        _fail("share", "error", "Ask the human for the recipient's exact email address, then retry.", **common)
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
               "hint": "Connect with 'moviestar account connect EMAIL' before publishing; service readiness and quota are checked live. --to sends an unlisted, forwardable link when enabled."})
        return
    origin, endpoint, credential = _connection("share", **common)
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
    published = {**fields, "link_created": body["status"] == "shared", "publication_outcome": body["status"],
                 "link_id": item["id"], "url": item["url"], "link": item, "quota": quota, "quota_scope": "link_publication"}
    if recipient is not None:
        _email_request("share", item["id"], recipient.lower(), key, origin, credential, published, success_status=body["status"])
        return
    _emit({"command": "share", "status": body["status"], **published,
           "hint": "Anyone with this unlisted link can watch, download, and forward it. No email was sent."})


@click.group("shares", invoke_without_command=True)
@click.option("--video", "video_id", metavar="VIDEO_ID", help="List only links for this uploaded video.")
@click.option("--cursor", metavar="LINK_ID", help="Continue using the prior response's next_cursor and the same --video scope.")
@click.pass_context
def shares(ctx: click.Context, video_id: str | None, cursor: str | None) -> None:
    """List owned share links, including revoked history, or preview deletion.

    The list returns one page and next_cursor. Use --video to filter a video.
    Use moviestar delete VIDEO_ID to delete a video and all its links.
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


@shares.command("email")
@click.argument("link_id")
@click.option("--to", "recipient", required=True, metavar="EMAIL", help="One exact email address supplied by the human.")
@click.option("--idempotency-key", metavar="KEY", help="Reuse KEY for a retry; a new key deliberately sends another email.")
@click.option("--dry-run", is_flag=True, help="Preview email delivery without a network request.")
def email_link(link_id: str, recipient: str, idempotency_key: str | None, dry_run: bool) -> None:
    """Email an existing unlisted link, or deliberately resend it with a new key.

    The link remains forwardable and downloadable without a recipient account.
    Retries must keep the same link, recipient and key. Provider acceptance does
    not confirm inbox delivery. An uncertain request stops retrying after 23 hours.
    """
    if not _RESERVATION_RE.fullmatch(link_id) or not _valid_recipient(recipient) or (idempotency_key is not None and not _KEY_RE.fullmatch(idempotency_key)):
        _fail("shares email", "invalid_request", "Use a link UUID, one exact email address, and a valid idempotency key.", email_sent=False)
    link_id = str(uuid.UUID(link_id))
    if dry_run:
        _emit({"command": "shares email", "status": "would_email", "link_id": link_id, "delivery": {"to": recipient}, "email_sent": False, "owner": _owner_from_local_state(),
               "hint": "This emails a forwardable unlisted link; it does not restrict access to this address."})
        return
    origin, _, credential = _connection("shares email")
    key = idempotency_key or uuid.uuid4().hex
    click.echo(json.dumps({"command": "shares email", "status": "retry_key", "idempotency_key": key,
                           "hint": "Retry the same link and recipient with --idempotency-key KEY."}), err=True)
    _email_request("shares email", link_id, recipient.lower(), key, origin, credential, {})


@shares.command("deliveries")
@click.argument("link_id")
@click.option("--cursor", metavar="DELIVERY_ID", help="Use the prior next_cursor with the same link.")
def email_history(link_id: str, cursor: str | None) -> None:
    """List email recipients and provider-acceptance history for an owned link.

    Returns one page and next_cursor. Acceptance is not proof of inbox delivery.
    Listing remains available when sending is paused or the link is revoked.
    """
    if not _RESERVATION_RE.fullmatch(link_id) or (cursor is not None and not _RESERVATION_RE.fullmatch(cursor)):
        _fail("shares deliveries", "invalid_request", "Use a link UUID and the previous delivery cursor.")
    link_id = str(uuid.UUID(link_id))
    origin, _, credential = _connection("shares deliveries")
    code, body = _json_request(f"{origin}/api/v1/shares/{link_id}/deliveries" + (f"?cursor={cursor}" if cursor else ""), credential, method="GET")
    if code != 200 or body.get("status") != "ok":
        _service_failure("shares deliveries", code, body)
    raw, cursor = body.get("deliveries"), body.get("next_cursor")
    items = [_delivery(item, link_id) for item in raw] if isinstance(raw, list) and len(raw) <= 50 else None
    if items is None or any(item is None for item in items) or (cursor is not None and (not isinstance(cursor, str) or not _RESERVATION_RE.fullmatch(cursor))):
        _fail("shares deliveries", "sharing_unavailable", "Could not verify delivery history. Retry this link's previous cursor.")
    _emit({"command": "shares deliveries", "status": "ok", "link_id": link_id, "deliveries": items, "next_cursor": cursor})


def _delete_uploaded_video(video_id: str, dry_run: bool, command: str) -> None:
    fields = {"video_id": video_id, "video_deleted": False, "all_links_revoked": False,
              "quota_released": False, "deletion_outcome": "not_attempted"}
    if not _RESERVATION_RE.fullmatch(video_id):
        _fail(command, "invalid_request", "Use the video_id returned by upload or share. Local files are not deleted.", **fields)
    video_id = str(uuid.UUID(video_id))
    fields["video_id"] = video_id
    if dry_run:
        _emit({"command": command, "status": "would_delete_video", **fields,
               "owner": _owner_from_local_state(),
               "hint": "This would permanently delete the uploaded video and revoke all its links. Already downloaded copies cannot be recalled. No ownership or service check was performed."})
        return
    origin, _, credential = _connection(command, **fields)
    code, body = _json_request(f"{origin}/api/v1/videos/{video_id}", credential, method="DELETE")
    status = body.get("status")
    usage = _usage(body.get("quota"))
    if (code == 200 and isinstance(status, str) and status in {"deleted", "already_deleted"}
            and body.get("video_id") == video_id and body.get("video_deleted") is True
            and body.get("all_links_revoked") is True and body.get("quota_released") is True
            and usage is not None):
        _emit({"command": command, "status": status, "video_id": video_id,
               "video_deleted": True, "all_links_revoked": True, "quota_released": True,
               "deletion_outcome": status, "quota": usage,
               "hint": "The hosted video is deleted and its quota is available. Already downloaded copies cannot be recalled."})
        return
    if (code == 503 and status == "deletion_pending" and body.get("video_id") == video_id
            and body.get("all_links_revoked") is True and body.get("video_deleted") is False
            and body.get("quota_released") is False):
        _fail(command, "deletion_pending", f"Links are disabled; storage cleanup is pending and quota remains charged. Retry 'moviestar delete {video_id}'.",
              video_id=video_id, video_deleted=False, all_links_revoked=True, quota_released=False, deletion_outcome="pending")
    rejected = {400: "invalid_request", 401: "not_connected", 404: "not_found"}
    if code in rejected and status == rejected[code]:
        hint = ("Run 'moviestar account status' and reconnect if needed." if code == 401
                else "Check that this video ID belongs to the connected account.")
        _fail(command, status, hint, **fields)
    _fail(command, "deletion_unavailable", f"The deletion outcome is unknown. Retry 'moviestar delete {video_id}' to confirm it; do not assume the video or quota is unchanged.",
          video_id=video_id, video_deleted=None, all_links_revoked=None, quota_released=None, deletion_outcome="unknown")


@click.command("delete")
@click.argument("video_id")
@click.option("--dry-run", is_flag=True, help="Preview deletion locally without accessing credentials or the network.")
def delete(video_id: str, dry_run: bool) -> None:
    """Permanently delete an uploaded video and all its links, freeing quota.

    VIDEO_ID is returned by upload or share. Local files and projects stay intact.
    Downloaded copies cannot be recalled. Retry the same ID after interruption.
    """
    _delete_uploaded_video(video_id, dry_run, "delete")


@shares.command("delete")
@click.argument("video_id")
@click.option("--dry-run", is_flag=True, help="Preview deletion without removing the video.")
def delete_video(video_id: str, dry_run: bool) -> None:
    """Delete an uploaded video and all its links; alias for moviestar delete.

    Unlike unshare, deletion frees storage. Downloaded copies cannot be recalled.
    """
    _delete_uploaded_video(video_id, dry_run, "shares delete")


@click.command("unshare")
@click.argument("link_id")
@click.option("--dry-run", is_flag=True, help="Preview revocation without changing the link.")
def unshare(link_id: str, dry_run: bool) -> None:
    """Revoke one link, preserving its video, other links, and storage quota.

    Downloads cannot be recalled. Previously issued media URLs may remain valid
    for up to 60 seconds. Use moviestar delete VIDEO_ID to delete its hosted video and free quota.
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
