"""Explicit replacement of an owned finished video, with durable retry intent."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

import click

from moviestar.account import _NoRedirectHandler, _credential_for_connection, _read_state
from moviestar.account_plan import capacity_failure
from moviestar.download import _origin, configured_watch_origin, watch_link
from moviestar.replacement_state import StateBusy, StateError, has_records, locked_requests
from moviestar.sharing import _link, _timestamp
from moviestar.upload import MAX_FILE_BYTES, _KEY_RE, _RESERVATION_RE, _json_request, _safe_signed_url, _upload_url


class ReplacementError(Exception):
    def __init__(self, status: str, hint: str):
        self.status, self.hint = status, hint


class FileChanged(Exception):
    pass


def _uuid(value: object) -> str | None:
    return str(uuid.UUID(value)) if isinstance(value, str) and _RESERVATION_RE.fullmatch(value) else None


def _number(value: object, minimum=0) -> bool:
    return type(value) is int and minimum <= value <= 9_007_199_254_740_991


def _connection(*, viewer: bool) -> tuple[str, str]:
    if viewer:
        try:
            configured_watch_origin()
        except ValueError:
            raise ReplacementError("invalid_configuration", "Set MOVIESTAR_WATCH_ORIGIN to an HTTPS origin without a path or credentials.") from None
    endpoint = _upload_url()
    try:
        origin = _origin(endpoint) if endpoint else None
    except ValueError:
        raise ReplacementError("invalid_configuration", "The configured account service origin is invalid.") from None
    state = _read_state()
    credential = _credential_for_connection(state)
    if not endpoint or not credential:
        raise ReplacementError("not_connected", "Run 'moviestar account connect EMAIL', then 'moviestar account status'.")
    return origin, credential


class Source:
    """Hash the selected file, then withhold the final PUT chunk if it changes."""
    def __init__(self, path: Path, stream):
        self.stream, self.path = stream, path
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_FILE_BYTES:
            raise ReplacementError("invalid_file", "Choose a regular MP4 between 1 byte and the 1 GB client ceiling.")
        self.size, self.stamp = before.st_size, (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        digest = hashlib.sha256()
        remaining = self.size
        while remaining:
            block = stream.read(min(remaining, 1024 * 1024))
            if not block:
                raise FileChanged()
            remaining -= len(block)
            digest.update(block)
        after = os.fstat(stream.fileno())
        if stream.read(1) or self.stamp != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise FileChanged()
        self.sha256 = digest.hexdigest()
        self.remaining, self.sent_hash = self.size, hashlib.sha256()
        stream.seek(0)

    def read(self, length=-1):
        if length == 0:
            return b""
        if self.remaining == self.size:
            current = os.fstat(self.stream.fileno())
            if self.stamp != (current.st_size, current.st_mtime_ns, current.st_ctime_ns):
                raise FileChanged()
        if not self.remaining:
            return b""
        block = self.stream.read(self.remaining if length < 0 else min(length, self.remaining))
        if not block:
            raise FileChanged()
        self.remaining -= len(block)
        self.sent_hash.update(block)
        if self.remaining == 0:
            current = os.fstat(self.stream.fileno())
            if self.sent_hash.hexdigest() != self.sha256 or self.stamp != (current.st_size, current.st_mtime_ns, current.st_ctime_ns):
                raise FileChanged()
        return block

    def fingerprint(self):
        return {"filename": self.path.name, "size_bytes": self.size, "sha256": self.sha256}


def _put_stream(url: str, headers: dict, source: Source) -> int:
    try:
        request = Request(url, data=source, headers=headers, method="PUT")
        with build_opener(_NoRedirectHandler()).open(request, timeout=300) as response:
            return response.status
    except HTTPError as response:
        with response:
            return response.code
    except (URLError, HTTPException, TimeoutError, OSError):
        return 0


def _status(body: dict, video: str, job: str | None = None, record: dict | None = None) -> dict | None:
    outcome, base, version = body.get("status"), body.get("base_version"), body.get("version")
    if (not isinstance(outcome, str) or outcome not in {"reserved", "processing", "replaced", "replacement_closed", "invalid_video"}
            or _uuid(body.get("video_id")) != video or _uuid(body.get("replacement_id")) is None
            or (job is not None and _uuid(body["replacement_id"]) != job)
            or not _number(base, 1) or not _number(version, 1)
            or version != base + (1 if outcome == "replaced" else 0)
            or not isinstance(body.get("filename"), str) or not 1 <= len(body["filename"]) <= 255
            or not _number(body.get("size_bytes"), 1) or body["size_bytes"] > MAX_FILE_BYTES
            or not _timestamp(body.get("expires_at")) or type(body.get("cleanup_pending")) is not bool
            or (outcome == "replaced" and not _timestamp(body.get("updated_at")))
            or (outcome != "replaced" and body.get("updated_at") is not None)):
        return None
    if record is not None and (base != record["expected_version"] or body["filename"] != record["filename"] or body["size_bytes"] != record["size_bytes"]):
        return None
    result = {name: body[name] for name in ("status", "filename", "size_bytes", "base_version", "version", "expires_at", "cleanup_pending")}
    result.update(video_id=video, replacement_id=_uuid(body["replacement_id"]), updated_at=body.get("updated_at"))
    if isinstance(body.get("reason"), str) and body["reason"] in {"cancelled", "expired", "invalid_video", "interrupted", "deleted"}:
        result["reason"] = body["reason"]
    return result


def _ack(code: int, body: dict, allowed: dict, video: str, job=None, record=None):
    outcome = body.get("status")
    if not isinstance(outcome, str) or outcome not in allowed.get(code, ()):
        return None
    return _status(body, video, job, record)


_READ = {200: ("reserved", "processing", "replaced", "replacement_closed")}
_RESERVE = {200: ("reserved", "processing", "replaced"), 201: ("reserved",), 409: ("replacement_closed",)}
_COMPLETE = {200: ("replaced",), 409: ("replacement_closed",), 422: ("invalid_video",)}


_HINTS = {
    "not_connected": "Check 'moviestar account status' and reconnect if needed; keep the original request key.",
    "not_found": "This video or replacement is unavailable in this account. Check the original request before starting another.",
    "consent_required": "Review 'moviestar account terms' with the human, then retry the same replacement. Consent is never accepted automatically.",
    "video_not_ready": "The video must be ready. A hold, pause or deletion prevents replacement; retry only when the service permits it.",
    "version_conflict": "The video changed. Review a new --dry-run before deliberately starting a new request with a new key.",
    "replacement_busy": "Another operation is in progress or awaiting cleanup. Check replacement-status and retry the same request later.",
    "idempotency_conflict": "The service has different input for this key. Keep the original key and inspect its original replacement; do not silently start another.",
    "upload_missing": "The service has no completed upload for this request. Cancel this replacement before deliberately starting a new request.",
    "invalid_request": "Check the video ID, filename, file size and request key.",
}


def _service_error(code: int, body: dict):
    capacity = capacity_failure(code, body)
    if capacity:
        return ReplacementError(capacity[0], capacity[1])
    outcome = "not_connected" if code == 401 else body.get("status")
    if isinstance(outcome, str) and outcome in _HINTS:
        return ReplacementError(outcome, _HINTS[outcome])
    return ReplacementError("replacement_unavailable", "Check replacement-status or retry the same file and request key. An uncertain completion may already have published; do not switch keys.")


def _preview(origin: str, credential: str, video: str, source: Source) -> dict:
    root = f"{origin}/api/v1/videos/{video}"
    code, body = _json_request(root + "/replacements/preflight", credential, {"filename": source.path.name, "size_bytes": source.size})
    if code != 200 or body.get("status") != "ok":
        raise _service_error(code, body)
    values = ("current_version", "current_size_bytes", "used_videos", "current_used_bytes", "resulting_used_bytes", "active_link_count")
    handle = body.get("owner_handle")
    if (_uuid(body.get("video_id")) != video or not all(_number(body.get(field)) for field in values)
            or not 1 <= body["current_version"] <= 2147483646 or body["current_size_bytes"] < 1
            or body.get("filename") != source.path.name or body.get("size_bytes") != source.size
            or not isinstance(handle, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_]{2,29}", handle)
            or body.get("temporary_bytes_reserved") != 2 * source.size
            or body.get("links_path") != f"/api/v1/videos/{video}/shares"
            or body["current_used_bytes"] < body["current_size_bytes"]
            or body["resulting_used_bytes"] != body["current_used_bytes"] - body["current_size_bytes"] + source.size):
        raise _service_error(0, {})
    raw = body.get("active_links")
    if not isinstance(raw, list) or len(raw) > 50:
        raise _service_error(0, {})
    links = []
    ids = set()
    for item in raw:
        if (not isinstance(item, dict) or _uuid(item.get("id")) is None or watch_link(item.get("url"), allowed_origin=origin) is None
                or not _timestamp(item.get("created_at")) or _uuid(item["id"]) in ids):
            raise _service_error(0, {})
        ids.add(_uuid(item["id"]))
        links.append({"id": _uuid(item["id"]), "url": item["url"], "created_at": item["created_at"]})
    cursor, cursors = body.get("next_cursor"), set()
    while cursor is not None:
        if _uuid(cursor) is None or _uuid(cursor) in cursors or len(ids) >= 10000:
            raise ReplacementError("preview_incomplete", "The link preview could not be completed. Retry without starting a replacement.")
        cursor = _uuid(cursor); cursors.add(cursor)
        code, page = _json_request(root + f"/shares?cursor={cursor}", credential, method="GET")
        if code != 200 or page.get("status") != "ok":
            raise _service_error(code, page)
        raw = page.get("links")
        if not isinstance(raw, list) or not raw or len(raw) > 50:
            raise _service_error(0, {})
        for item in raw:
            parsed = _link(item, origin, video)
            if parsed is None or parsed["id"] in ids:
                raise _service_error(0, {})
            ids.add(parsed["id"])
            if parsed["revoked_at"] is None:
                links.append({field: parsed[field] for field in ("id", "url", "created_at")})
        cursor = page.get("next_cursor")
    if len(links) != body["active_link_count"]:
        raise ReplacementError("preview_changed", "The active link list changed during preview. Retry before replacing anything.")
    return {"status": "would_replace", "video_id": video, "file": {"path": str(source.path), **source.fingerprint()},
        "account": {"handle": "@" + handle, "service_origin": origin}, "current_version": body["current_version"],
        "active_links": links, "active_link_count": len(links),
        "quota": {"limits_verified": True, "snapshot_only": True, "would_use_additional_video_slots": 0,
            "current_usage": {"used_videos": body["used_videos"], "used_bytes": body["current_used_bytes"]},
            "projected_usage": {"used_videos": body["used_videos"], "used_bytes": body["resulting_used_bytes"]},
            "temporary_bytes_reserved": body["temporary_bytes_reserved"]},
        "upload_performed": False, "video_replaced": False, "replacement_outcome": "not_attempted", "email_sent": False,
        "hint": "This read-only service preview reserves nothing. Live replacement rechecks the version, access and capacity. All active links will show the new cut; revoked access stays revoked."}


def _validate_record(record: dict, source: Source | None = None):
    if (type(record.get("schema_version")) is not int or record["schema_version"] != 1 or not isinstance(record.get("idempotency_key"), str) or not _KEY_RE.fullmatch(record["idempotency_key"])
            or not isinstance(record.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
            or not isinstance(record.get("filename"), str) or not _number(record.get("size_bytes"), 1)
            or (record.get("expected_version") is not None and not _number(record["expected_version"], 1))
            or (record.get("replacement_id") is not None and _uuid(record["replacement_id"]) is None)
            or (record.get("reservation_attempted") is True and record.get("expected_version") is None)
            or (record.get("completion_started") is True and record.get("replacement_id") is None)
            or ("remote_status" in record and (not isinstance(record["remote_status"], str)
                or record["remote_status"] not in {"reserved", "processing", "replaced", "replacement_closed", "invalid_video"}))
            or ("cleanup_pending" in record and type(record["cleanup_pending"]) is not bool)
            or not all(type(record.get(field)) is bool for field in ("reservation_attempted", "completion_started", "upload_confirmed"))):
        raise StateError("Invalid recovery record")
    if source is not None and any(record.get(key) != value for key, value in source.fingerprint().items()):
        raise ReplacementError("file_changed", "This request belongs to different file bytes or a different filename. Restore the original file, or inspect/cancel the original replacement before starting another.")


def _finished(record: dict) -> bool:
    return record.get("remote_status") in {"replaced", "replacement_closed", "invalid_video"} and record.get("cleanup_pending") is False


def _adopt(store, record: dict, origin: str, video: str) -> dict:
    """Bind an explicitly reconciled older intent without losing another one."""
    _validate_record(record)
    if record.get("video_id") != video:
        raise StateError("Recovery video mismatch")
    active = store.current()
    if active is not None:
        _validate_record(active)
        if active["idempotency_key"] != record["idempotency_key"] and active["reservation_attempted"] and not _finished(active):
            raise StateError("Another request requires reconciliation")
    record = {**record, "service_origin": origin}
    store.save(record, active=True)
    return record


def _reconciliation_error(record: dict) -> ReplacementError:
    _validate_record(record)
    return ReplacementError("replacement_reconciliation_required", "An older recovery record needs reconciliation. Select its original service and retry with the original --idempotency-key or --resume ID, or read/cancel its replacement ID. No fresh key was selected.")


def _safe_upload(body: dict, record: dict, origin: str) -> tuple[str, dict]:
    upload = body.get("upload")
    headers = {"Content-Type": "video/mp4", "Content-Length": str(record["size_bytes"]), "If-None-Match": "*"}
    if (not isinstance(upload, dict) or upload.get("method") != "PUT" or upload.get("headers") != headers
            or not _safe_signed_url(upload.get("url")) or upload.get("expires_at") != body.get("expires_at")):
        raise _service_error(0, {})
    if datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise ReplacementError("replacement_expired", "This upload capability expired. Cancel the original replacement before deliberately starting a new request; retries never extend its expiry.")
    url = upload["url"]
    try:
        parsed = urlsplit(url)
        if (any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url
                or (parsed.scheme == "http" and _origin(url) != _origin(origin))
                or (parsed.scheme == "https" and parsed.port not in {None, 443})):
            raise ValueError("Unsafe upload destination")
    except ValueError:
        raise _service_error(0, {}) from None
    return url, headers


def _fields(record: dict | None, uploaded=False):
    return {"idempotency_key": record["idempotency_key"], "replacement_id": record.get("replacement_id"),
            "video_replaced": None if record.get("completion_started") else False,
            "replacement_outcome": "unknown" if record.get("completion_started") else "not_attempted",
            "upload_performed": uploaded} if record else {"video_replaced": False, "replacement_outcome": "not_attempted", "upload_performed": False}


def _result(entry: dict, key: str | None, uploaded=False):
    outcome = entry["status"]
    return {**entry, **({"idempotency_key": key} if key else {}), "upload_performed": uploaded, "email_sent": False,
        "video_replaced": True if outcome == "replaced" else False if outcome in {"replacement_closed", "invalid_video"} else None,
        "replacement_outcome": "replaced" if outcome == "replaced" else "not_published" if outcome in {"replacement_closed", "invalid_video"} else "unknown"}


def _remember(store, record, entry):
    record.update(replacement_id=entry["replacement_id"], remote_status=entry["status"], cleanup_pending=entry["cleanup_pending"])
    store.save(record)


def _live(store, record: dict, source: Source, origin: str, credential: str, resume: str | None, progress: dict) -> dict:
    root = f"{origin}/api/v1/videos/{record['video_id']}/replacements"
    job = record.get("replacement_id")

    def finish(entry):
        saved = True
        try:
            _remember(store, record, entry)
        except StateError:
            saved = False
        return {**_result(entry, record["idempotency_key"], progress["uploaded"]), "recovery_state_saved": saved}

    if job:
        code, raw = _json_request(root + "/" + job, credential, method="GET")
        entry = _ack(code, raw, _READ, record["video_id"], job, record)
        if entry is None:
            raise _service_error(code, raw)
        if entry["status"] in {"replaced", "replacement_closed"}:
            return finish(entry)
    if record["expected_version"] is None:
        preview = _preview(origin, credential, record["video_id"], source)
        record["expected_version"] = preview["current_version"]
        store.save(record)
    if not job or (not resume and not record["upload_confirmed"] and not record["completion_started"]):
        record["reservation_attempted"] = True
        store.save(record)
        code, raw = _json_request(root, credential, {field: record[field] for field in ("filename", "size_bytes", "expected_version", "idempotency_key")})
        entry = _ack(code, raw, _RESERVE, record["video_id"], job, record)
        if entry is None:
            refusals = {400: ("invalid_request",), 401: ("not_connected",), 404: ("not_found",),
                409: ("consent_required", "video_not_ready", "version_conflict", "replacement_busy", "file_too_large",
                      "video_limit", "storage_limit", "existing_video_over_limit", "payment_grace", "account_deleting")}
            if job is None and isinstance(raw.get("status"), str) and raw["status"] in refusals.get(code, ()):
                record["reservation_attempted"] = False
                store.save(record)
            raise _service_error(code, raw)
        job = entry["replacement_id"]
        _remember(store, record, entry)
        if entry["status"] in {"replaced", "replacement_closed"}:
            return finish(entry)
        if entry["status"] == "reserved" and not resume:
            url, headers = _safe_upload(raw, record, origin)
            progress["uploaded"] = None
            code = _put_stream(url, headers, source)
            if not (200 <= code < 300 or code == 412):
                raise ReplacementError("transfer_failed", "Retry the same file and request key. The conditional upload cannot overwrite bytes already accepted for this request.")
            progress["uploaded"] = code != 412
            record["upload_confirmed"] = True
            store.save(record)
    record["completion_started"] = True
    store.save(record)
    code, raw = _json_request(root + "/" + job + "/complete", credential)
    entry = _ack(code, raw, _COMPLETE, record["video_id"], job, record)
    if entry and entry["status"] in {"replaced", "replacement_closed", "invalid_video"}:
        return finish(entry)
    if code == 0 or code >= 500 or 200 <= code < 300:
        read_code, observed = _json_request(root + "/" + job, credential, method="GET")
        recovered = _ack(read_code, observed, _READ, record["video_id"], job, record)
        if recovered and recovered["status"] in {"replaced", "replacement_closed"}:
            return finish(recovered)
    raise _service_error(code, raw)


@click.command("replace")
@click.argument("video_id")
@click.argument("file", type=click.Path(path_type=Path))
@click.option("--dry-run", is_flag=True, help="Read-only live preview of owner, all active URLs and projected usage; no transfer or reservation.")
@click.option("--idempotency-key", metavar="KEY", help="Reuse the original request key; retries also recover it from private local state.")
@click.option("--resume", metavar="REPLACEMENT_ID", help="Check status and retry completion only, without another PUT; requires the saved file/request binding.")
def replace(video_id: str, file: Path, dry_run: bool, idempotency_key: str | None, resume: str | None):
    """Replace an owned finished MP4 behind its existing watch URLs.

    --dry-run contacts the service for a read-only preview. A live invocation
    uploads/validates the selected cut, then switches the existing video. It
    uses no extra video slot and sends no email. Local files stay unchanged.
    The original remains available until commit, subject to independent
    holds, pauses or deletion. Private recovery records bind the
    request to the file's digest, account, service and original version.
    """
    record, progress = None, {"uploaded": False}
    video = _uuid(video_id)
    try:
        if video is None or (resume is not None and _uuid(resume) is None) or (idempotency_key is not None and not _KEY_RE.fullmatch(idempotency_key)) or (dry_run and resume):
            raise ReplacementError("invalid_request", "Use a video UUID, an optional original key, and either --dry-run or --resume.")
        origin, credential = _connection(viewer=True)
        # Video UUIDs identify an immutable owner/resource at this service.
        # Every recovery request still authorizes the current credential remotely.
        # Grants, credentials, installation IDs and public handles can all change.
        scope = origin + "\n" + video
        with ExitStack() as contexts:
            store = contexts.enter_context(locked_requests(scope)) if not dry_run else None
            if store is not None:
                active = store.current()
                record = store.find(_uuid(resume)) if resume else store.read(idempotency_key) if idempotency_key else active
                if record is None:
                    legacy = store.legacy(video)
                    matches = [item for item in legacy if (resume and item.get("replacement_id") == _uuid(resume))
                        or (idempotency_key and item.get("idempotency_key") == idempotency_key)]
                    if len(matches) == 1:
                        record = matches[0]
                        record = _adopt(store, record, origin, video)
                    elif legacy:
                        record = legacy[0]
                        raise _reconciliation_error(record)
                if resume and record is None:
                    raise ReplacementError("replacement_state_missing", "Use replacement-status to inspect this ID. Resume requires the original private recovery record; it never guesses file bytes or starts a new request.")
                if record is not None:
                    _validate_record(record)
                    if record.get("video_id") != video or record.get("service_origin") != origin:
                        raise StateError("Recovery service/video mismatch")
                    if store.current() is None:
                        store.save(record, active=True)
            if file.suffix.lower() != ".mp4" or not file.name or len(file.name) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in file.name):
                raise ReplacementError("invalid_file", "Choose a readable finished .mp4 file.")
            descriptor = os.open(file, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
            with os.fdopen(descriptor, "rb") as stream:
                source = Source(file, stream)
                if dry_run:
                    result = _preview(origin, credential, video, source)
                else:
                    if record is not None:
                        if any(record.get(k) != v for k, v in source.fingerprint().items()):
                            if idempotency_key or resume or (record.get("reservation_attempted") and not _finished(record)):
                                _validate_record(record, source)
                            else:
                                record = None
                    if record is None:
                        legacy = store.legacy(video)
                        if legacy:
                            record = legacy[0]
                            raise _reconciliation_error(record)
                        if active is not None:
                            _validate_record(active)
                            if active.get("reservation_attempted") and not _finished(active):
                                record = active
                                raise ReplacementError("replacement_in_progress", "Inspect or cancel the original replacement before starting another request with different input or a different key.")
                        record = {"schema_version": 1, "video_id": video, "service_origin": origin, **source.fingerprint(),
                            "idempotency_key": idempotency_key or uuid.uuid4().hex, "expected_version": None, "replacement_id": None,
                            "reservation_attempted": False, "completion_started": False, "upload_confirmed": False}
                        store.save(record, active=True)
                    click.echo(json.dumps({"command": "shares replace", "status": "retry_key", "video_id": video,
                        "idempotency_key": record["idempotency_key"], "replacement_id": record.get("replacement_id")}), err=True)
                    result = _live(store, record, source, origin, credential, _uuid(resume), progress)
        click.echo(json.dumps({"command": "shares replace", **result}, indent=2))
        if result["status"] in {"invalid_video", "replacement_closed"}:
            raise click.exceptions.Exit(1)
    except (ReplacementError, StateError, FileChanged, OSError, KeyboardInterrupt) as error:
        if isinstance(error, ReplacementError):
            status, hint = error.status, error.hint
        elif isinstance(error, StateBusy):
            status, hint = "replacement_busy_local", "Another replacement command is using this video. Wait for it to finish before retrying."
        elif isinstance(error, StateError):
            status, hint = "replacement_state_unavailable", "Private recovery state could not be read or saved. Restore access to account configuration and retry the same request; no new key was selected."
        elif isinstance(error, FileChanged):
            status, hint = "file_changed", "The selected file changed while being read. Restore the original file before retrying; check replacement-status for any earlier attempt."
        elif isinstance(error, KeyboardInterrupt):
            status, hint = "interrupted", "Retry the same file and request key; check replacement-status if completion was interrupted."
        else:
            status, hint = "invalid_file", "The selected MP4 could not be read. Local files were not modified; check replacement-status for any earlier attempt."
        click.echo(json.dumps({"command": "shares replace", "status": status, "video_id": video,
            **_fields(record, progress["uploaded"]), "email_sent": False, "hint": hint}, indent=2))
        raise click.exceptions.Exit(1)


def _inspect(video_id: str, replacement_id: str, cancel: bool):
    command = "shares cancel-replacement" if cancel else "shares replacement-status"
    try:
        video, job = _uuid(video_id), _uuid(replacement_id)
        if not video or not job:
            raise ReplacementError("invalid_request", "Use the original video and replacement UUIDs.")
        origin, credential = _connection(viewer=False)
        code, raw = _json_request(f"{origin}/api/v1/videos/{video}/replacements/{job}", credential, method="DELETE" if cancel else "GET")
        allowed = {200: ("replaced", "replacement_closed")} if cancel else _READ
        entry = _ack(code, raw, allowed, video, job)
        if entry is None:
            raise _service_error(code, raw)
        result = _result(entry, None)
        scope = origin + "\n" + video
        try:
            if has_records(scope):
                with locked_requests(scope) as store:
                    record = store.find(job)
                    if record is None:
                        matches = [item for item in store.legacy(video) if item.get("replacement_id") == job]
                        if len(matches) == 1:
                            candidate = matches[0]
                            _validate_record(candidate)
                            if _status(raw, video, job, candidate) is not None:
                                record = _adopt(store, candidate, origin, video)
                    if record:
                        _validate_record(record)
                        if record.get("service_origin") != origin:
                            raise StateError("Recovery service mismatch")
                        if _status(raw, video, job, record) is not None:
                            if store.current() is None:
                                store.save(record, active=True)
                            _remember(store, record, entry)
                            result["idempotency_key"] = record["idempotency_key"]
        except (StateError, OSError):
            result["recovery_state_saved"] = False
        click.echo(json.dumps({"command": command, **result}, indent=2))
    except ReplacementError as error:
        click.echo(json.dumps({"command": command, "status": error.status, "video_id": _uuid(video_id),
            "replacement_id": _uuid(replacement_id), "video_replaced": None, "replacement_outcome": "unknown",
            "upload_performed": False, "email_sent": False, "hint": error.hint}, indent=2))
        raise click.exceptions.Exit(1)


@click.command("replacement-status")
@click.argument("video_id")
@click.argument("replacement_id")
def replacement_status(video_id: str, replacement_id: str):
    """Read the outcome of the original replacement, without a local file."""
    _inspect(video_id, replacement_id, False)


@click.command("cancel-replacement")
@click.argument("video_id")
@click.argument("replacement_id")
def cancel_replacement(video_id: str, replacement_id: str):
    """Cancel unpublished replacement bytes. A committed cut cannot be undone."""
    _inspect(video_id, replacement_id, True)
