"""Regression checks for reviewed account lifecycle and transport edges."""

import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request

import pytest
from click.testing import CliRunner

from moviestar.cli import cli


INSTALLATION_ID = "123e4567-e89b-42d3-a456-426614174000"
CREDENTIAL = "mvs_" + "a" * 43
PREVIEW = "https://preview.example"


def invoke(*args):
    return CliRunner().invoke(cli, ["account", *args])


def connected_state(monkeypatch, tmp_path, *, with_url=True, with_credential=True):
    state_path = tmp_path / "account.json"
    connection = {
        "email": "h***@example.com",
        "handle": "@m24",
        "installation_name": "Test Mac",
        "credential_storage": "private_file",
    }
    if with_credential:
        connection["credential_fallback"] = CREDENTIAL
    state = {"version": 1, "installation_id": INSTALLATION_ID, "connection": connection}
    if with_url:
        state["installation_url"] = f"{PREVIEW}/api/v1/account/installation"
        state["claim_url"] = f"{PREVIEW}/api/v1/account/claims"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr("moviestar.account._account_state_path", lambda: state_path)
    return state_path


def account_service(status_code, body):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_DELETE(self):
            self.respond()

        def respond(self):
            self.send_response(status_code)
            self.send_header("Content-Type", "text/html" if body.startswith(b"<") else "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.mark.parametrize("command", ["status", "disconnect"])
def test_unrelated_html_401_keeps_the_connection(monkeypatch, tmp_path, command):
    state_path = connected_state(monkeypatch, tmp_path)
    before = state_path.read_text()
    server, thread = account_service(401, b"<html>Sign in to this proxy</html>")
    monkeypatch.setenv(
        "MOVIESTAR_ACCOUNT_INSTALLATION_URL",
        f"http://127.0.0.1:{server.server_port}/api/v1/account/installation",
    )
    try:
        result = invoke(command)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "error"
    assert state_path.read_text() == before


def test_disconnect_requires_the_service_revoked_response(monkeypatch, tmp_path):
    state_path = connected_state(monkeypatch, tmp_path)
    before = state_path.read_text()
    server, thread = account_service(200, b"{}")
    monkeypatch.setenv(
        "MOVIESTAR_ACCOUNT_INSTALLATION_URL",
        f"http://127.0.0.1:{server.server_port}/api/v1/account/installation",
    )
    try:
        result = invoke("disconnect")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "error"
    assert state_path.read_text() == before


def test_unavailable_keyring_can_be_explicitly_forgotten_locally(monkeypatch, tmp_path):
    state_path = connected_state(monkeypatch, tmp_path, with_credential=False)
    assert invoke("disconnect").exit_code == 1
    assert "connection" in json.loads(state_path.read_text())

    result = invoke("disconnect", "--local")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["remote_grant_revoked"] is False
    assert "revoke" in payload["warning"].lower()
    state = json.loads(state_path.read_text())
    assert "connection" not in state
    assert "installation_id" not in state


def test_disconnect_then_connect_another_owner_uses_a_new_installation(monkeypatch, tmp_path):
    account = importlib.import_module("moviestar.account")
    state_path = connected_state(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "moviestar.account._installation_request",
        lambda *_args: (200, {"status": "revoked"}, None),
    )
    assert invoke("disconnect").exit_code == 0
    forgotten = json.loads(state_path.read_text())
    assert "installation_id" not in forgotten
    assert "claim_url" not in forgotten
    assert "installation_url" not in forgotten

    sent = []

    class Response:
        status = 202

        def __init__(self, request_id):
            self.request_id = request_id

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({
                "status": "approval_pending",
                "request_id": self.request_id,
                "expires_at": "2026-10-04T12:00:00Z",
            }).encode()

    class Opener:
        def open(self, request, timeout):
            sent.append(request.full_url)
            return Response(json.loads(request.data)["request_id"])

    monkeypatch.delenv("MOVIESTAR_ACCOUNT_CLAIM_URL", raising=False)
    monkeypatch.setattr(account, "build_opener", lambda *_args: Opener())
    result = invoke("connect", "other@example.com")

    assert result.exit_code == 0
    assert sent == [account._DEFAULT_CLAIM_URL]
    state = json.loads(state_path.read_text())
    assert state["installation_id"] != INSTALLATION_ID
    assert state["claim_url"] == account._DEFAULT_CLAIM_URL


def test_expired_pending_claim_gets_a_new_request_id(monkeypatch, tmp_path):
    account = importlib.import_module("moviestar.account")
    state_path = tmp_path / "account.json"
    state_path.write_text(json.dumps({
        "installation_id": INSTALLATION_ID,
        "claim_url": PREVIEW + "/api/v1/account/claims",
        "pending_claim": {
            "request_id": "123e4567-e89b-42d3-a456-426614174001",
            "email": "human@example.com",
            "claim_secret": "old-private-secret-with-enough-length",
            "expires_at": "2020-01-01T00:00:00Z",
        },
    }), encoding="utf-8")
    monkeypatch.setattr(account, "_account_state_path", lambda: state_path)
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_CLAIM_URL", raising=False)

    payload, state = account._pending_request("human@example.com")

    assert payload["request_id"] != "123e4567-e89b-42d3-a456-426614174001"
    assert state["claim_url"] == account._DEFAULT_CLAIM_URL


def test_pending_exchange_remembers_preview_after_environment_is_cleared(monkeypatch, tmp_path):
    account = importlib.import_module("moviestar.account")
    state_path = tmp_path / "account.json"
    state_path.write_text(json.dumps({
        "installation_id": INSTALLATION_ID,
        "installation_url": f"{PREVIEW}/api/v1/account/installation",
        "claim_url": f"{PREVIEW}/api/v1/account/claims",
    }), encoding="utf-8")
    monkeypatch.setattr(account, "_account_state_path", lambda: state_path)
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_CLAIM_URL", raising=False)
    requests = []

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"status":"approval_pending"}'

    class Opener:
        def open(self, request, timeout):
            requests.append(request.full_url)
            return Response()

    monkeypatch.setattr(account, "build_opener", lambda *_args: Opener())
    error, _ = account._exchange_claim("claim-id", INSTALLATION_ID, "private-secret")

    assert error is None
    assert requests == [f"{PREVIEW}/api/v1/account/claims/claim-id/exchange"]


def test_legacy_pending_preview_claim_saves_preview_installation_url(monkeypatch, tmp_path):
    account = importlib.import_module("moviestar.account")
    state_path = tmp_path / "account.json"
    state_path.write_text(json.dumps({
        "installation_id": INSTALLATION_ID,
        "pending_claim": {
            "request_id": "123e4567-e89b-42d3-a456-426614174001",
            "email": "human@example.com",
            "claim_secret": "private-secret-with-enough-length-1234",
            "expires_at": "2026-10-04T12:00:00Z",
        },
    }), encoding="utf-8")
    monkeypatch.setattr(account, "_account_state_path", lambda: state_path)
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_CLAIM_URL", PREVIEW + "/api/v1/account/claims")
    monkeypatch.setattr(account, "_exchange_claim", lambda *_args: (None, {
        "status": "connected",
        "request_id": "123e4567-e89b-42d3-a456-426614174001",
        "email": "human@example.com",
        "handle": "@m24",
        "installation": {"id": INSTALLATION_ID, "name": "Test Mac", "status": "approved"},
        "credential": CREDENTIAL,
        "device_grant_id": "123e4567-e89b-42d3-a456-426614174004",
    }))
    monkeypatch.setattr(account, "_store_device_credential", lambda *_args: ("private_file", CREDENTIAL))

    result = invoke("status")

    assert result.exit_code == 0, result.output
    state = json.loads(state_path.read_text())
    assert state["installation_url"] == PREVIEW + "/api/v1/account/installation"
    assert state["claim_url"] == PREVIEW + "/api/v1/account/claims"


@pytest.mark.parametrize("command", ["status", "disconnect"])
def test_legacy_connected_state_requires_explicit_service_origin(monkeypatch, tmp_path, command):
    state_path = connected_state(monkeypatch, tmp_path, with_url=False)
    before = state_path.read_text()
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)

    result = invoke(command)

    assert result.exit_code == 1
    assert "MOVIESTAR_ACCOUNT_INSTALLATION_URL" in result.stdout
    assert state_path.read_text() == before


def test_bearer_request_rejects_plain_http_to_non_loopback(monkeypatch):
    account = importlib.import_module("moviestar.account")
    monkeypatch.setenv(
        "MOVIESTAR_ACCOUNT_INSTALLATION_URL",
        "http://outside.example/api/v1/account/installation",
    )
    monkeypatch.setattr(account, "build_opener", lambda *_args, **_kwargs: (
        pytest.fail("credential must not be sent")
    ))

    status, body, error = account._installation_request("GET", CREDENTIAL)

    assert status is None and body is None
    assert "HTTPS" in error


@pytest.mark.parametrize("request_kind", ["send", "exchange"])
def test_claim_requests_reject_plain_http_outside_loopback(monkeypatch, request_kind):
    account = importlib.import_module("moviestar.account")
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_CLAIM_URL", "http://outside.example/api/v1/account/claims")
    monkeypatch.setattr(account, "build_opener", lambda *_args: pytest.fail("private claim data must not be sent"))

    if request_kind == "send":
        error, _ = account._send_claim({"request_id": "some-id"})
    else:
        error, _ = account._exchange_claim("some-id", INSTALLATION_ID, "private-secret")

    assert "HTTPS" in error


@pytest.mark.parametrize("request_kind", ["send", "exchange"])
def test_claim_requests_do_not_follow_redirects(monkeypatch, request_kind):
    account = importlib.import_module("moviestar.account")
    forwarded = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            forwarded.append(self.path)
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *_args):
            pass

    target = HTTPServer(("127.0.0.1", 0), Target)

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/capture")
            self.end_headers()

        def log_message(self, *_args):
            pass

    redirect = HTTPServer(("127.0.0.1", 0), Redirect)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (target, redirect)]
    for thread in threads:
        thread.start()
    try:
        monkeypatch.setenv("MOVIESTAR_ACCOUNT_CLAIM_URL", f"http://127.0.0.1:{redirect.server_port}/api/v1/account/claims")
        if request_kind == "send":
            error, _ = account._send_claim({"request_id": "some-id"})
        else:
            error, _ = account._exchange_claim("some-id", INSTALLATION_ID, "private-secret")
        assert "302" in error
        assert forwarded == []
    finally:
        for server in (target, redirect):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


def test_bearer_request_does_not_follow_redirect(monkeypatch):
    account = importlib.import_module("moviestar.account")
    handler = account._NoRedirectHandler()
    request = Request("https://preview.example/api/v1/account/installation")
    result = handler.redirect_request(
        request,
        None,
        302,
        "Redirect",
        {},
        "https://outside.example/collect",
    )

    assert result is None


def test_redirecting_service_never_receives_bearer_at_target(monkeypatch):
    account = importlib.import_module("moviestar.account")
    forwarded = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            forwarded.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    target = HTTPServer(("127.0.0.1", 0), Target)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/capture")
            self.end_headers()

        def log_message(self, *_args):
            pass

    redirect = HTTPServer(("127.0.0.1", 0), Redirect)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (target, redirect)
    ]
    for thread in threads:
        thread.start()
    try:
        monkeypatch.setenv(
            "MOVIESTAR_ACCOUNT_INSTALLATION_URL",
            f"http://127.0.0.1:{redirect.server_port}/api/v1/account/installation",
        )
        status, _, _ = account._installation_request("GET", CREDENTIAL)
        assert status == 302
        assert forwarded == []
    finally:
        for server in (target, redirect):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
