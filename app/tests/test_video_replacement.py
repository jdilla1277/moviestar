"""Replacement previews, durable retries and explicit failure outcomes."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar import replacement

VIDEO = "d0ed1b10-3317-4e5b-a56c-a609863c1001"
JOB = "d0ed1b10-3317-4e5b-a56c-a609863c1002"
LINK = "d0ed1b10-3317-4e5b-a56c-a609863c1003"
API = "https://api.example.test"
WATCH = "https://watch.example.test/v/" + "w" * 43
TOKEN = "mvs_" + "t" * 43


def invoke(*args):
    result = CliRunner().invoke(cli, ["shares", *args])
    assert result.stdout, repr(result.exception)
    return result, json.loads(result.stdout)


@pytest.fixture
def service(tmp_path, monkeypatch):
    account = tmp_path / "config" / "account.json"
    account.parent.mkdir()
    account.write_text(json.dumps({"installation_url": API + "/api/v1/account/installation",
        "connection": {"handle": "@maker", "email": "m***@example.test", "credential_storage": "private_file",
                       "credential_fallback": TOKEN}}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(account))
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)
    monkeypatch.setenv("MOVIESTAR_WATCH_ORIGIN", "https://watch.example.test")
    file = tmp_path / "cut.mp4"
    file.write_bytes(b"chosen finished cut")
    calls, uploads = [], []
    expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    state = {"version": 1, "published": False, "lost_reply": False, "status_error": False,
             "lost_reservation": False, "put_failed": False, "complete_error": None}

    def status(outcome="reserved"):
        return {"status": outcome, "video_id": VIDEO, "replacement_id": JOB, "filename": file.name,
                "size_bytes": len(b"chosen finished cut"), "base_version": 1, "version": 2 if outcome == "replaced" else 1,
                "expires_at": expires, "updated_at": "2026-11-04T12:00:00Z" if outcome == "replaced" else None,
                "cleanup_pending": outcome not in {"replaced", "replacement_closed"}}

    def request(url, credential, payload=None, *, method="POST"):
        assert credential == TOKEN
        calls.append((url, method, payload))
        if url.endswith("/preflight"):
            size = payload["size_bytes"]
            return 200, {"status": "ok", "video_id": VIDEO, "owner_handle": "maker", "current_version": state["version"],
                "current_size_bytes": 10, "filename": file.name, "size_bytes": size, "used_videos": 3,
                "current_used_bytes": 30, "resulting_used_bytes": 20 + size, "active_link_count": 1,
                "active_links": [{"id": LINK, "url": WATCH, "created_at": "2026-11-04T10:00:00Z"}],
                "next_cursor": None, "links_path": f"/api/v1/videos/{VIDEO}/shares", "temporary_bytes_reserved": 2 * size}
        if url.endswith("/replacements"):
            if state["lost_reservation"]:
                state["lost_reservation"] = False
                return 0, {}
            body = status("replaced" if state["published"] else "reserved")
            body["upload"] = {"method": "PUT", "url": "https://test.r2.cloudflarestorage.com/object?signed=PRIVATE",
                "expires_at": expires, "headers": {"Content-Type": "video/mp4", "Content-Length": str(len(b"chosen finished cut")), "If-None-Match": "*"}}
            return 200, body
        if method == "GET":
            return (0, {}) if state["status_error"] else (200, status("replaced" if state["published"] else "reserved"))
        if method == "DELETE":
            return 200, {**status("replacement_closed"), "reason": "cancelled"}
        if url.endswith("/complete"):
            if state["complete_error"]:
                return state["complete_error"]
            state["published"] = True; state["version"] = 2
            if state["lost_reply"]:
                state["lost_reply"] = False
                return 0, {}
            return 200, status("replaced")
        pytest.fail("Unexpected API request")

    def put(url, headers, source):
        assert "Authorization" not in headers and "Cookie" not in headers
        uploads.append((url, headers, source.read()))
        return (0 if state["put_failed"] else 200)

    monkeypatch.setattr(replacement, "_json_request", request)
    monkeypatch.setattr(replacement, "_put_stream", put)
    return file, account, calls, uploads, state, status


def test_commands_explain_live_read_only_preview_and_resumption():
    for command in ["replace", "replacement-status", "cancel-replacement"]:
        result = CliRunner().invoke(cli, ["shares", command, "--help"])
        assert result.exit_code == 0, result.output
    help_text = CliRunner().invoke(cli, ["shares", "replace", "--help"]).output
    assert "--resume" in help_text and "--dry-run" in help_text
    assert "read-only" in help_text.lower()


def test_preview_shows_live_owner_links_and_quota_without_mutation_or_journal(service):
    file, account, calls, uploads, _, _ = service
    before = {p: p.read_bytes() for p in account.parent.rglob("*") if p.is_file()}
    result, body = invoke("replace", VIDEO.upper(), str(file), "--dry-run")
    assert result.exit_code == 0, result.output
    assert body["status"] == "would_replace" and body["video_id"] == VIDEO
    assert body["account"]["handle"] == "@maker" and body["active_links"][0]["url"] == WATCH
    assert body["quota"]["would_use_additional_video_slots"] == 0
    assert body["quota"]["projected_usage"]["used_bytes"] == 20 + file.stat().st_size
    assert len(calls) == 1 and calls[0][0].endswith("/preflight") and not uploads
    assert {p: p.read_bytes() for p in account.parent.rglob("*") if p.is_file()} == before


def test_live_replace_keeps_urls_sends_conditional_headers_and_writes_private_recovery_state(service):
    file, account, calls, uploads, _, _ = service
    result, body = invoke("replace", VIDEO, str(file), "--idempotency-key", "cut-v2")
    assert result.exit_code == 0, result.output
    assert body["status"] == "replaced" and body["video_replaced"] is True and body["version"] == 2
    assert body["replacement_id"] == JOB and body["email_sent"] is False
    assert uploads[0][1] == {"Content-Type": "video/mp4", "Content-Length": str(file.stat().st_size), "If-None-Match": "*"}
    assert uploads[0][2] == file.read_bytes()
    assert calls[1][2] == {"filename": file.name, "size_bytes": file.stat().st_size, "expected_version": 1, "idempotency_key": "cut-v2"}
    journals = list(account.parent.glob("replacement-requests/**/*.json"))
    assert journals
    for p in journals:
        assert p.stat().st_mode & 0o777 == 0o600
        assert TOKEN not in p.read_text() and "signed=PRIVATE" not in p.read_text() and WATCH not in p.read_text()
    assert TOKEN not in result.output and "signed=PRIVATE" not in result.output


def test_retry_after_lost_publication_reply_reads_status_without_reserving_or_uploading_again(service):
    file, _, calls, uploads, state, _ = service
    state["lost_reply"] = True; state["status_error"] = True
    first, failure = invoke("replace", VIDEO, str(file))
    assert first.exit_code == 1 and failure["video_replaced"] is None
    key = failure["idempotency_key"]
    calls.clear(); state["status_error"] = False
    retry, success = invoke("replace", VIDEO, str(file))
    assert retry.exit_code == 0 and success["status"] == "replaced" and success["idempotency_key"] == key
    assert len(uploads) == 1
    assert len(calls) == 1 and calls[0][1] == "GET"


@pytest.mark.parametrize("retry_option", ["default", "key", "resume"])
def test_missing_file_does_not_erase_an_earlier_uncertain_completion(service, retry_option):
    file, _, calls, _, state, _ = service
    state["lost_reply"] = True; state["status_error"] = True
    _, first = invoke("replace", VIDEO, str(file))
    file.unlink(); calls.clear()
    options = [] if retry_option == "default" else ["--idempotency-key", first["idempotency_key"]] if retry_option == "key" else ["--resume", JOB]
    result, body = invoke("replace", VIDEO, str(file), *options)
    assert result.exit_code == 1 and body["status"] == "invalid_file"
    assert body["video_replaced"] is None and body["replacement_outcome"] == "unknown"
    assert body["idempotency_key"] == first["idempotency_key"] and body["replacement_id"] == JOB
    assert not calls


@pytest.mark.parametrize("field,value", [("remote_status", []), ("cleanup_pending", "false")])
def test_corrupt_recovery_outcome_is_rejected_before_network_work(service, field, value):
    file, account, calls, _, state, _ = service
    state["lost_reply"] = True; state["status_error"] = True
    invoke("replace", VIDEO, str(file))
    journal = next(account.parent.glob("replacement-requests/**/request-*.json"))
    record = json.loads(journal.read_text()); record[field] = value
    journal.write_text(json.dumps(record))
    file.write_bytes(b"x" * file.stat().st_size); calls.clear()
    result, body = invoke("replace", VIDEO, str(file))
    assert result.exit_code == 1 and body["status"] == "replacement_state_unavailable"
    assert body["video_replaced"] is None and not calls


def test_lost_reservation_reply_reuses_saved_expected_version_and_key(service):
    file, _, calls, _, state, _ = service
    state["lost_reservation"] = True
    first, failure = invoke("replace", VIDEO, str(file), "--idempotency-key", "persisted")
    assert first.exit_code == 1
    state["version"] = 99  # A new preflight must not change the saved request.
    calls.clear()
    retry, success = invoke("replace", VIDEO, str(file), "--idempotency-key", "persisted")
    assert retry.exit_code == 0, retry.output
    assert not any(url.endswith("/preflight") for url, _, _ in calls)
    assert calls[0][2]["expected_version"] == 1


def test_changed_bytes_with_same_filename_and_size_never_rebind_an_uncertain_request(service):
    file, _, calls, _, state, _ = service
    state["lost_reservation"] = True
    invoke("replace", VIDEO, str(file), "--idempotency-key", "same-request")
    file.write_bytes(b"x" * file.stat().st_size)
    calls.clear()
    result, body = invoke("replace", VIDEO, str(file), "--idempotency-key", "same-request")
    assert result.exit_code == 1 and body["status"] == "file_changed"
    assert not calls


def test_resume_never_puts_file_bytes_again(service):
    file, _, calls, uploads, state, _ = service
    state["complete_error"] = (503, {"status": "replacement_unavailable"})
    first, failure = invoke("replace", VIDEO, str(file))
    assert first.exit_code == 1 and failure["replacement_id"] == JOB
    state["complete_error"] = None; calls.clear()
    retry, body = invoke("replace", VIDEO, str(file), "--resume", JOB)
    assert retry.exit_code == 0 and body["video_replaced"] is True
    assert len(uploads) == 1 and all(not url.endswith("/preflight") and not url.endswith("/replacements") for url, _, _ in calls)


@pytest.mark.parametrize("code,outcome", [(401, "not_connected"), (409, "consent_required"), (409, "video_not_ready"), (409, "storage_limit"), (503, "replacement_unavailable")])
def test_service_errors_are_redacted_and_keep_the_same_request(service, code, outcome):
    file, _, _, _, state, _ = service
    state["complete_error"] = (code, {"status": outcome, "hint": TOKEN, "object_key": "PRIVATE", "url": "https://secret.example"})
    result, body = invoke("replace", VIDEO, str(file), "--idempotency-key", "recover")
    assert result.exit_code == 1 and body["status"] == outcome
    assert body["idempotency_key"] == "recover" and body["replacement_id"] == JOB
    assert body["video_replaced"] is None
    assert TOKEN not in result.output and "https://secret.example" not in result.output and "PRIVATE" not in result.output


def test_status_and_cancel_work_without_a_local_file_or_valid_watch_origin(service, monkeypatch):
    file, _, _, uploads, _, _ = service
    file.unlink(); monkeypatch.setenv("MOVIESTAR_WATCH_ORIGIN", "https://bad.example/path")
    read, status = invoke("replacement-status", VIDEO, JOB)
    assert read.exit_code == 0 and status["status"] == "reserved"
    cancelled, body = invoke("cancel-replacement", VIDEO, JOB)
    assert cancelled.exit_code == 0 and body["status"] == "replacement_closed" and body["reason"] == "cancelled"
    assert body["video_replaced"] is False and not uploads


@pytest.mark.parametrize("code,outcome", [(409, "replaced"), (422, "replaced"), (200, "reserved"), (200, "invalid_video")])
def test_inconsistent_completion_acknowledgements_never_claim_success(service, code, outcome):
    file, _, _, _, state, status = service
    state["complete_error"] = (code, status(outcome))
    result, body = invoke("replace", VIDEO, str(file))
    assert result.exit_code == 1
    assert body["video_replaced"] is None and body["replacement_outcome"] == "unknown"


def test_malformed_reason_is_redacted_instead_of_crashing(service):
    file, _, _, _, state, status = service
    state["complete_error"] = (422, {**status("invalid_video"), "reason": [TOKEN]})
    result, body = invoke("replace", VIDEO, str(file))
    assert result.exit_code == 1 and body["status"] == "invalid_video"
    assert TOKEN not in result.output and body["video_replaced"] is False


@pytest.mark.parametrize("fail_on", [1, 2, 3, 4, 5, 6])
def test_failed_journal_write_stops_before_completion(service, monkeypatch, fail_on):
    from moviestar.replacement_state import Requests, StateError
    file, _, calls, _, _, _ = service
    original, writes = Requests.save, []
    def save(self, *args, **kwargs):
        writes.append(1)
        if len(writes) == fail_on:
            raise StateError("PRIVATE disk error")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Requests, "save", save)
    result, body = invoke("replace", VIDEO, str(file))
    assert result.exit_code == 1 and body["status"] == "replacement_state_unavailable"
    assert not any(url.endswith("/complete") for url, _, _ in calls)
    assert body["video_replaced"] is not True and "PRIVATE" not in result.output


def test_lost_local_receipt_does_not_hide_a_confirmed_publication(service, monkeypatch):
    from moviestar.replacement_state import Requests, StateError
    file, _, _, _, _, _ = service
    original, writes = Requests.save, []
    def save(self, *args, **kwargs):
        writes.append(1)
        if len(writes) == 7:
            raise StateError("PRIVATE disk error")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Requests, "save", save)
    result, body = invoke("replace", VIDEO, str(file))
    assert result.exit_code == 0 and body["video_replaced"] is True
    assert body["recovery_state_saved"] is False


def test_second_process_cannot_start_a_competing_request(service):
    from moviestar.replacement_state import locked_requests
    file, _, calls, _, _, _ = service
    origin, _, identity = replacement._connection(viewer=True)
    with locked_requests(origin + "\n" + identity + "\n" + VIDEO):
        result, body = invoke("replace", VIDEO, str(file))
    assert result.exit_code == 1 and body["status"] == "replacement_busy_local" and not calls


@pytest.mark.parametrize("url", [
    "https://evil.example.test/upload", "https://test.r2.cloudflarestorage.com.evil.test/upload",
    "https://user:secret@test.r2.cloudflarestorage.com/upload", "https://test.r2.cloudflarestorage.com:bad/upload",
    "http://127.0.0.1:65432/upload", "https://test.r2.cloudflarestorage.com/upload#secret",
])
def test_unsafe_upload_destinations_never_receive_bytes_or_credentials(service, monkeypatch, url):
    file, _, _, uploads, _, _ = service
    original = replacement._json_request
    def request(*args, **kwargs):
        code, body = original(*args, **kwargs)
        if args[0].endswith("/replacements"):
            body["upload"]["url"] = url
        return code, body
    monkeypatch.setattr(replacement, "_json_request", request)
    result, body = invoke("replace", VIDEO, str(file))
    assert result.exit_code == 1 and body["video_replaced"] is False and not uploads
    assert "secret" not in result.output


def test_generated_key_is_saved_and_announced_before_the_first_request(service, monkeypatch):
    file, account, calls, _, _, _ = service
    original = replacement._json_request
    def request(*args, **kwargs):
        assert list(account.parent.glob("replacement-requests/**/request-*.json"))
        if not calls:
            raise KeyboardInterrupt()
        return original(*args, **kwargs)
    monkeypatch.setattr(replacement, "_json_request", request)
    result, body = invoke("replace", VIDEO, str(file))
    announcement = json.loads(result.stderr.splitlines()[0])
    assert body["status"] == "interrupted" and body["idempotency_key"] == announcement["idempotency_key"]
    assert TOKEN not in result.stderr


def test_preview_collects_every_active_link_and_never_follows_server_supplied_paths(service, monkeypatch):
    file, _, calls, _, _, _ = service
    cursor = "d0ed1b10-3317-4e5b-a56c-a609863c1010"
    other = "d0ed1b10-3317-4e5b-a56c-a609863c1011"
    original = replacement._json_request
    def request(url, credential, payload=None, *, method="POST"):
        if method == "GET":
            assert url == f"{API}/api/v1/videos/{VIDEO}/shares?cursor={cursor}"
            calls.append((url, method, payload))
            item = {"id": other, "video_id": VIDEO, "url": WATCH[:-43] + "z" * 43,
                "access": "unlisted", "forwardable": True, "downloadable": True,
                "created_at": "2026-11-04T09:00:00Z", "revoked_at": None}
            return 200, {"status": "ok", "links": [item], "next_cursor": None}
        code, body = original(url, credential, payload, method=method)
        body.update(active_link_count=2, next_cursor=cursor)
        return code, body
    monkeypatch.setattr(replacement, "_json_request", request)
    result, body = invoke("replace", VIDEO, str(file), "--dry-run")
    assert result.exit_code == 0 and len(body["active_links"]) == 2
    assert len(calls) == 2


@pytest.mark.parametrize("corrupt", ["cycle", "foreign_link", "wrong_count", "foreign_path"])
def test_incomplete_or_unsafe_previews_never_reserve(service, monkeypatch, corrupt):
    file, _, calls, uploads, _, _ = service
    original = replacement._json_request
    def request(url, credential, payload=None, *, method="POST"):
        if method == "GET":
            calls.append((url, method, payload))
            item = {"id": JOB, "video_id": VIDEO, "url": None, "access": "unlisted", "forwardable": True,
                "downloadable": True, "created_at": "2026-11-04T09:00:00Z", "revoked_at": "2026-11-04T10:00:00Z"}
            return 200, {"status": "ok", "links": [item], "next_cursor": LINK}
        code, body = original(url, credential, payload, method=method)
        if corrupt == "cycle": body["next_cursor"] = LINK
        if corrupt == "foreign_link": body["active_links"][0]["url"] = "https://evil.example.test/v/" + "x" * 43
        if corrupt == "wrong_count": body["active_link_count"] = 2
        if corrupt == "foreign_path": body["links_path"] = "https://evil.example.test/steal"
        return code, body
    monkeypatch.setattr(replacement, "_json_request", request)
    result, body = invoke("replace", VIDEO, str(file), "--dry-run")
    assert result.exit_code == 1 and body["video_replaced"] is False and not uploads
    assert not any(url.endswith("/replacements") for url, _, _ in calls)


def test_changed_file_during_upload_withholds_the_final_chunk_and_never_completes(service, monkeypatch):
    file, _, calls, _, _, _ = service
    def put(url, headers, source):
        first = source.read(2)
        assert first == file.read_bytes()[:2]
        with file.open("r+b") as stream:
            stream.seek(2); stream.write(b"x" * (file.stat().st_size - 2))
        source.read()
        pytest.fail("changed bytes must not finish the upload")
    monkeypatch.setattr(replacement, "_put_stream", put)
    result, body = invoke("replace", VIDEO, str(file))
    assert result.exit_code == 1 and body["status"] == "file_changed"
    assert not any(url.endswith("/complete") for url, _, _ in calls)


def test_explicitly_refused_reservation_does_not_block_a_reviewed_new_key(service, monkeypatch):
    file, _, calls, _, state, _ = service
    original = replacement._json_request
    rejected, base = [False], [1]
    def request(url, credential, payload=None, *, method="POST"):
        if url.endswith("/replacements") and not rejected[0]:
            rejected[0] = True
            return 409, {"status": "version_conflict"}
        if url.endswith("/replacements"):
            base[0] = payload["expected_version"]
        code, body = original(url, credential, payload, method=method)
        if "base_version" in body:
            body["base_version"] = base[0]
            body["version"] = base[0] + (body["status"] == "replaced")
        return code, body
    monkeypatch.setattr(replacement, "_json_request", request)
    first, failed = invoke("replace", VIDEO, str(file), "--idempotency-key", "stale-cut")
    assert first.exit_code == 1 and failed["status"] == "version_conflict"
    state["version"] = 2
    retry, body = invoke("replace", VIDEO, str(file), "--idempotency-key", "reviewed-cut")
    assert retry.exit_code == 0, retry.output
    assert body["version"] == 3 and calls[-2][2]["expected_version"] == 2


def test_expired_upload_instructions_are_not_used(service, monkeypatch):
    file, _, _, uploads, _, _ = service
    original = replacement._json_request
    def request(*args, **kwargs):
        code, body = original(*args, **kwargs)
        if args[0].endswith("/replacements"):
            body["expires_at"] = body["upload"]["expires_at"] = "2001-01-01T00:00:00Z"
        return code, body
    monkeypatch.setattr(replacement, "_json_request", request)
    result, body = invoke("replace", VIDEO, str(file))
    assert result.exit_code == 1 and not uploads
    assert body["status"] == "replacement_expired"


def test_status_read_survives_unavailable_local_recovery_storage(service, monkeypatch):
    _, _, _, _, _, _ = service
    def unavailable(*_args):
        raise PermissionError("PRIVATE local path")
    monkeypatch.setattr(replacement, "has_records", unavailable)
    result, body = invoke("replacement-status", VIDEO, JOB)
    assert result.exit_code == 0 and body["status"] == "reserved"
    assert body["recovery_state_saved"] is False and "PRIVATE" not in result.output
