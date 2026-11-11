"""Create-only upload headers and recovery through real HTTP/CLI requests."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

import pytest
from click.testing import CliRunner

from moviestar.cli import cli


RESERVATION = "123e4567-e89b-42d3-a456-426614174000"
VIDEO = "123e4567-e89b-42d3-a456-426614174002"
CREDENTIAL = "mvs_" + "c" * 43
MEDIA = {"durationMs": 1000, "width": 64, "height": 64, "videoCodec": "avc", "audioCodec": "aac"}


@pytest.fixture
def service(tmp_path, monkeypatch, request):
    file = tmp_path / "finished.mp4"
    original = b"\x00\x00\x00\x18ftypisom" + b"finished test bytes"
    if getattr(request, "param", None) == "large":
        original += b"x" * (16 * 1024 * 1024 - len(original))
    file.write_bytes(original)
    state = {
        "headers": {"Content-Type": "video/mp4", "Content-Length": str(len(original)), "If-None-Match": "*"},
        "object": None, "mode": "ok", "closed": False, "completed": False,
        "requests": [], "keys": [], "errors": [], "dropped": False,
        "completion_down": False, "validation_down": False,
        "signed_header_names": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            state["requests"].append(self.path)
            if self.headers.get("Authorization") != f"Bearer {CREDENTIAL}":
                state["errors"].append("missing account authentication")
                return self.reply(401, {})
            if self.path == "/api/v1/videos/uploads":
                state["keys"].append(payload["idempotency_key"])
                if state["completed"]:
                    return self.reply(200, {"status": "already_completed", "reservation_id": RESERVATION})
                return self.reply(201, {
                    "status": "upload_pending", "reservation_id": RESERVATION,
                    "upload": {"method": "PUT", "url": origin + "/object?" + urlencode({
                        "capability": "test-private",
                        **({"X-Amz-SignedHeaders": state["signed_header_names"]}
                           if state["signed_header_names"] else {}),
                    }),
                               "headers": state["headers"]},
                })
            if self.path == f"/api/v1/videos/uploads/{RESERVATION}/complete":
                if state["completion_down"]:
                    return self.reply(503, {"status": "upload_unavailable"})
                if state["closed"]:
                    return self.reply(409, {"status": "upload_closed"})
                if state["object"] is None:
                    return self.reply(409, {"status": "upload_missing"})
                state["completed"] = True
                return self.reply(201, {"status": "stored_unverified", "video_id": VIDEO,
                                        "size_bytes": len(state["object"])})
            if self.path == f"/api/v1/videos/{VIDEO}/validate":
                if state["validation_down"]:
                    return self.reply(503, {"status": "validation_unavailable"})
                return self.reply(200, {"status": "ready", "video_id": VIDEO, "media": MEDIA})
            self.reply(404, {})

        def do_PUT(self):
            state["requests"].append("PUT")
            state.setdefault("put_headers", []).append(dict(self.headers))
            if any(self.headers.get(name) for name in ("Authorization", "Cookie", "X-Unrelated")):
                state["errors"].append("unapproved header forwarded to storage")
            requires_condition = any(name.lower() == "if-none-match" and value == "*"
                                     for name, value in state["headers"].items())
            if requires_condition and self.headers.get("If-None-Match") != "*":
                return self.reply(403, {})
            if state["mode"] in {"early-412", "early-drop"}:
                # Reject before reading the body, as real object storage can.
                # A large sender can hit BrokenPipe before urllib reads 412.
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                if state["mode"] == "early-412":
                    self.reply(412, {})
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return self.connection.close()
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if state["mode"] == "precondition":
                return self.reply(412, {})
            if self.headers.get("If-None-Match") == "*" and state["object"] is not None:
                return self.reply(412, {})
            if state["mode"] == "missing":
                return self.reply(503, {})
            state["object"] = data
            if state["mode"] == "lost-reply" and not state["dropped"]:
                state["dropped"] = True
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return self.connection.close()
            self.reply(200, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    account = tmp_path / "account.json"
    account.write_text(json.dumps({
        "installation_url": origin + "/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": CREDENTIAL},
    }))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(account))
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)

    def invoke(*args):
        result = CliRunner().invoke(cli, ["upload", str(file), *args])
        assert result.stdout, repr(result.exception)
        assert CREDENTIAL not in result.output
        assert "capability=test-private" not in result.output
        assert "untrusted" not in result.output
        return result, json.loads(result.stdout)

    try:
        yield state, invoke, original
        assert not state["errors"]
        assert file.read_bytes() == original
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("name", ["If-None-Match", "if-none-match", "iF-NoNe-MaTcH", None])
def test_signed_condition_reaches_actual_put_and_legacy_uploads_still_work(service, name):
    state, invoke, original = service
    state["headers"].pop("If-None-Match")
    if name:
        state["headers"][name] = "*"
    state["headers"].update(Authorization="Bearer untrusted", Cookie="untrusted", **{"X-Unrelated": "untrusted"})
    result, body = invoke("--idempotency-key", "same-upload")
    assert result.exit_code == 0, result.output
    assert body["status"] == "ready" and body["upload_performed"] is True
    assert state["object"] == original
    headers = {key.lower(): value for key, value in state["put_headers"][0].items()}
    assert headers.get("if-none-match") == ("*" if name else None)
    assert headers["content-type"] == "video/mp4"
    assert headers["content-length"] == str(len(original))
    assert state["keys"] == ["same-upload"]


@pytest.mark.parametrize("condition", [None, "", '"etag"', "*, *", "*\r\nX-Test: bad", 1, ["*"], {"value": "*"}])
def test_invalid_condition_fails_before_put_with_recovery_key(service, condition):
    state, invoke, _ = service
    state["headers"]["If-None-Match"] = condition
    result, body = invoke("--idempotency-key", "same-upload")
    assert result.exit_code == 1
    assert body["status"] == "upload_unavailable"
    assert "If-None-Match" in body["hint"]
    assert body["idempotency_key"] == "same-upload"
    assert body["reservation_id"] == RESERVATION
    assert state["requests"] == ["/api/v1/videos/uploads"]
    assert state["object"] is None


def test_duplicate_case_variants_are_rejected(service):
    state, invoke, _ = service
    state["headers"]["if-none-match"] = "*"
    result, body = invoke("--idempotency-key", "same-upload")
    assert result.exit_code == 1
    assert body["status"] == "upload_unavailable"
    assert state["object"] is None


@pytest.mark.parametrize("provided", [False, True])
def test_condition_required_by_signed_url_must_have_a_supported_header(service, provided):
    state, invoke, original = service
    state["signed_header_names"] = "content-length;content-type;host;if-none-match"
    if not provided:
        del state["headers"]["If-None-Match"]
    result, body = invoke("--idempotency-key", "same-upload")
    if provided:
        assert result.exit_code == 0 and body["status"] == "ready"
        assert state["object"] == original
    else:
        assert result.exit_code == 1
        assert body["status"] == "upload_unavailable" and "If-None-Match" in body["hint"]
        assert body["reservation_id"] == RESERVATION
        assert body["idempotency_key"] == "same-upload"
        assert state["requests"] == ["/api/v1/videos/uploads"]
        assert state["object"] is None


@pytest.mark.parametrize("missing", ["Content-Type", "Content-Length"])
def test_required_transfer_headers_remain_required(service, missing):
    state, invoke, _ = service
    del state["headers"][missing]
    result, body = invoke("--idempotency-key", "same-upload")
    assert result.exit_code == 1 and body["status"] == "upload_unavailable"
    assert state["requests"] == ["/api/v1/videos/uploads"]


@pytest.mark.parametrize("retry", ["key", "resume"])
def test_lost_put_reply_recovers_same_reservation_without_overwriting(service, retry):
    state, invoke, original = service
    state.update(mode="lost-reply", completion_down=True)
    first, body = invoke("--idempotency-key", "same-upload")
    assert first.exit_code == 1 and body["status"] == "upload_unavailable"
    assert body["upload_performed"] is None and "--resume" in body["hint"]
    assert body["reservation_id"] == RESERVATION
    assert body["idempotency_key"] == "same-upload"
    assert state["object"] == original
    state["completion_down"] = False
    second, body = invoke(*(["--resume", RESERVATION] if retry == "resume"
                           else ["--idempotency-key", "same-upload"]))
    assert second.exit_code == 0, second.output
    assert body["status"] == "ready" and body["video_id"] == VIDEO
    assert body["upload_performed"] is False
    assert state["keys"] == ["same-upload"] * (1 if retry == "resume" else 2)
    assert state["requests"].count("PUT") == (1 if retry == "resume" else 2)
    assert state["requests"].count(f"/api/v1/videos/uploads/{RESERVATION}/complete") == 2
    assert state["object"] == original


def test_existing_marker_rejects_late_put_and_completion_cannot_claim_ready(service):
    state, invoke, _ = service
    state.update(object=b"permanent marker", closed=True)
    result, body = invoke("--idempotency-key", "same-upload")
    assert result.exit_code == 1 and body["status"] == "upload_closed"
    assert body["reservation_id"] == RESERVATION
    assert body["idempotency_key"] == "same-upload"
    assert body["upload_performed"] is False
    assert "--resume" in body["hint"]
    assert state["object"] == b"permanent marker"
    assert state["keys"] == ["same-upload"]


def test_unstored_failed_transfer_keeps_same_key_retry_path(service):
    state, invoke, original = service
    state["mode"] = "missing"
    first, body = invoke("--idempotency-key", "same-upload")
    assert first.exit_code == 1 and body["status"] == "transfer_failed"
    assert body["idempotency_key"] == "same-upload" and "--idempotency-key" in body["hint"]
    assert state["requests"] == ["/api/v1/videos/uploads", "PUT"]
    state["mode"] = "ok"
    second, body = invoke("--idempotency-key", "same-upload")
    assert second.exit_code == 0 and body["status"] == "ready"
    assert state["keys"] == ["same-upload", "same-upload"]
    assert state["object"] == original


def test_legacy_412_is_not_mistaken_for_a_confirmed_conditional_upload(service):
    state, invoke, _ = service
    del state["headers"]["If-None-Match"]
    state["mode"] = "precondition"
    result, body = invoke("--idempotency-key", "same-upload")
    assert result.exit_code == 1 and body["status"] == "transfer_failed"
    assert state["requests"] == ["/api/v1/videos/uploads", "PUT"]
    assert state["object"] is None


@pytest.mark.parametrize("unavailable", ["completion_down", "validation_down"])
def test_conditional_conflict_with_uncertain_completion_is_resumable(service, unavailable):
    state, invoke, original = service
    state["object"] = original
    state[unavailable] = True
    first, body = invoke("--idempotency-key", "same-upload")
    assert first.exit_code == 1
    assert body["status"] == ("upload_unavailable" if unavailable == "completion_down" else "validation_unavailable")
    assert body["upload_performed"] is False
    assert body["reservation_id"] == RESERVATION
    assert body["idempotency_key"] == "same-upload"
    assert "--resume" in body["hint"]
    state[unavailable] = False
    second, body = invoke("--resume", RESERVATION)
    assert second.exit_code == 0 and body["status"] == "ready"
    assert body["upload_performed"] is False
    assert state["keys"] == ["same-upload"]
    assert state["requests"].count("PUT") == 1
    assert state["object"] == original


@pytest.mark.parametrize("service", ["large"], indirect=True)
def test_large_early_412_recovers_without_looping_same_key(service, monkeypatch):
    import moviestar.upload as uploads

    state, invoke, original = service
    state.update(object=original, mode="early-412")
    real_put = uploads._put_file
    statuses = []

    def observe(*args):
        status = real_put(*args)
        statuses.append(status)
        return status

    monkeypatch.setattr(uploads, "_put_file", observe)
    first, body = invoke("--idempotency-key", "same-upload")
    assert statuses == [0], "regression must exercise the lost early response"
    assert first.exit_code == 0, first.output
    assert body["status"] == "ready" and body["upload_performed"] is None
    second, body = invoke("--idempotency-key", "same-upload")
    assert second.exit_code == 0 and body["video_id"] == VIDEO
    assert body["upload_performed"] is False
    assert state["keys"] == ["same-upload", "same-upload"]
    assert state["requests"].count("PUT") == 1
    assert state["object"] == original


@pytest.mark.parametrize("service", ["large"], indirect=True)
@pytest.mark.parametrize("unavailable", ["completion_down", "validation_down"])
def test_large_early_rejection_with_unavailable_reconciliation_can_resume(service, unavailable):
    state, invoke, original = service
    state.update(object=original, mode="early-412")
    state[unavailable] = True
    first, body = invoke("--idempotency-key", "same-upload")
    assert first.exit_code == 1
    assert body["status"] == ("upload_unavailable" if unavailable == "completion_down" else "validation_unavailable")
    assert body["upload_performed"] is None
    assert body["reservation_id"] == RESERVATION and body["idempotency_key"] == "same-upload"
    assert "--resume" in body["hint"]
    state[unavailable] = False
    second, body = invoke("--resume", RESERVATION)
    assert second.exit_code == 0 and body["status"] == "ready"
    assert state["keys"] == ["same-upload"] and state["requests"].count("PUT") == 1
    assert state["object"] == original


@pytest.mark.parametrize("service", ["large"], indirect=True)
def test_large_early_rejection_cannot_overwrite_or_validate_a_closed_marker(service):
    state, invoke, _ = service
    state.update(object=b"permanent marker", closed=True, mode="early-412")
    result, body = invoke("--idempotency-key", "same-upload")
    assert result.exit_code == 1 and body["status"] == "upload_closed"
    assert body["upload_performed"] is None
    assert body["reservation_id"] == RESERVATION and body["idempotency_key"] == "same-upload"
    assert state["requests"] == ["/api/v1/videos/uploads", "PUT",
                                  f"/api/v1/videos/uploads/{RESERVATION}/complete"]
    assert state["object"] == b"permanent marker"


@pytest.mark.parametrize("service", ["large"], indirect=True)
def test_uncertain_conditional_transfer_with_no_object_keeps_same_key_retry(service):
    state, invoke, original = service
    state["mode"] = "early-drop"
    first, body = invoke("--idempotency-key", "same-upload")
    assert first.exit_code == 1 and body["status"] == "transfer_failed"
    assert body["upload_performed"] is None
    assert body["idempotency_key"] == "same-upload" and body["reservation_id"] == RESERVATION
    assert "--idempotency-key" in body["hint"]
    assert state["requests"] == ["/api/v1/videos/uploads", "PUT",
                                  f"/api/v1/videos/uploads/{RESERVATION}/complete"]
    assert state["object"] is None
    state["mode"] = "ok"
    second, body = invoke("--idempotency-key", "same-upload")
    assert second.exit_code == 0 and body["status"] == "ready"
    assert state["keys"] == ["same-upload", "same-upload"]
    assert state["object"] == original


def test_lost_conditional_put_reply_reconciles_accepted_bytes_immediately(service):
    state, invoke, original = service
    state["mode"] = "lost-reply"
    result, body = invoke("--idempotency-key", "same-upload")
    assert result.exit_code == 0 and body["status"] == "ready"
    assert body["upload_performed"] is None
    assert state["keys"] == ["same-upload"] and state["requests"].count("PUT") == 1
    assert state["object"] == original


def test_uncertain_legacy_transfer_does_not_attempt_conditional_reconciliation(service):
    state, invoke, _ = service
    del state["headers"]["If-None-Match"]
    state["mode"] = "early-drop"
    result, body = invoke("--idempotency-key", "same-upload")
    assert result.exit_code == 1 and body["status"] == "transfer_failed"
    assert state["requests"] == ["/api/v1/videos/uploads", "PUT"]
