"""Browser email requests keep login material and device authorization separate."""

import importlib
import io
import json
from urllib.error import HTTPError, URLError

import pytest
from click.testing import CliRunner

from moviestar.cli import cli

REQUEST = "123e4567-e89b-42d3-a456-426614174000"
CLAIM = "123e4567-e89b-42d3-a456-426614174001"
PREVIEW = "https://preview.example"


def invoke(*args):
    return CliRunner().invoke(cli, ["account", "open", *args])


@pytest.fixture(autouse=True)
def isolated_account(monkeypatch, tmp_path):
    path = tmp_path / "account.json"
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(path))
    for name in ("MOVIESTAR_ACCOUNT_CLAIM_URL", "MOVIESTAR_ACCOUNT_INSTALLATION_URL", "MOVIESTAR_ACCOUNT_URL"):
        monkeypatch.delenv(name, raising=False)
    accounts = importlib.import_module("moviestar.account")

    def forbidden(*_args, **_kwargs):
        pytest.fail("An email-link request must not open a browser, access credentials or write account state")

    monkeypatch.setattr(accounts.webbrowser, "open", forbidden)
    monkeypatch.setattr(accounts, "_credential_for_connection", forbidden)
    monkeypatch.setattr(accounts, "_write_state", forbidden)
    return path


def transport(monkeypatch, status=202, body=None, error=None, headers=None):
    module = importlib.import_module("moviestar.account_email_link")
    captured = []
    encoded = body if isinstance(body, bytes) else json.dumps(body if body is not None else {"status": "accepted", "expires_in_seconds": 600, "hint": "Check email."}).encode()

    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(encoded)
            self.status = status
            self.headers = headers or {}

    class Opener:
        def open(self, request, timeout):
            captured.append((request, timeout))
            if error:
                raise error
            return Response()

    monkeypatch.setattr(module, "build_opener", lambda *_args: Opener())
    return captured


def test_help_explains_email_requests_and_dry_run():
    result = invoke("--help")
    assert result.exit_code == 0
    assert "--email-link" in result.output
    assert "--request-id" in result.output
    assert "--claim-id" in result.output
    assert "--dry-run" in result.output
    assert "receipt" in result.output.lower()


def test_email_request_uses_json_and_no_installation_credential(monkeypatch, isolated_account):
    isolated_account.write_text(json.dumps({"connection": {"credential_fallback": "mvs_private"}}))
    before = isolated_account.read_bytes()
    sent = transport(monkeypatch)
    result = invoke("--email-link", " Human@Example.com ", "--request-id", REQUEST)
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output == {
        "command": "account open", "status": "email_link_requested", "email": "h***@example.com",
        "request_id": REQUEST, "service_origin": "https://trymoviestar.com", "expires_in_seconds": 600,
        "delivery": "unconfirmed", "opened_browser": False, "account_state_changed": False,
        "hint": "Ask the account owner to open the email and choose Continue with email link. This receipt does not confirm delivery or browser sign-in.",
    }
    request, timeout = sent[0]
    assert request.full_url == "https://trymoviestar.com/api/v1/account/sign-in-links"
    assert request.method == "POST" and timeout == 10
    assert request.get_header("X-moviestar-client") == "moviestar-cli-v1"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Authorization") is None
    assert json.loads(request.data) == {"email": "human@example.com", "request_id": REQUEST}
    assert isolated_account.read_bytes() == before
    assert "mvs_private" not in result.output
    assert "human@example.com" not in result.output


def test_dry_run_uses_pending_preview_origin_without_network(monkeypatch, isolated_account):
    isolated_account.write_text(json.dumps({
        "claim_url": PREVIEW + "/api/v1/account/claims", "installation_url": PREVIEW + "/api/v1/account/installation",
        "pending_claim": {"request_id": CLAIM, "email": "human@example.com", "expires_at": "2099-01-01T00:00:00Z", "claim_secret": "private-claim-secret"},
    }))
    before = isolated_account.read_bytes()
    result = invoke("--email-link", "human@example.com", "--request-id", REQUEST, "--dry-run")
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output["status"] == "dry_run"
    assert output["service_origin"] == PREVIEW
    assert output["claim_id"] == CLAIM and output["would_request_email"] is True
    assert output["request_id"] == REQUEST
    assert isolated_account.read_bytes() == before
    assert "private-claim-secret" not in result.output


@pytest.mark.parametrize("pending", [
    {"email": "other@example.com", "request_id": CLAIM, "expires_at": "2099-01-01T00:00:00Z"},
    {"email": "human@example.com", "request_id": CLAIM, "expires_at": "2000-01-01T00:00:00Z"},
    {"email": "human@example.com", "request_id": CLAIM, "expires_at": None},
    {"email": "human@example.com", "request_id": "invalid", "expires_at": "2099-01-01T00:00:00Z"},
])
def test_does_not_attach_unrelated_or_invalid_pending_claim(pending, isolated_account):
    isolated_account.write_text(json.dumps({"pending_claim": pending}))
    result = invoke("--email-link", "human@example.com", "--dry-run")
    assert result.exit_code == 0
    assert "claim_id" not in json.loads(result.stdout)


def test_service_override_does_not_send_a_claim_from_another_origin(monkeypatch, isolated_account):
    isolated_account.write_text(json.dumps({"claim_url": "https://trymoviestar.com/api/v1/account/claims", "pending_claim": {
        "email": "human@example.com", "request_id": CLAIM, "expires_at": "2099-01-01T00:00:00Z",
    }}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_CLAIM_URL", PREVIEW + "/api/v1/account/claims")
    result = invoke("--email-link", "human@example.com", "--dry-run")
    output = json.loads(result.stdout)
    assert result.exit_code == 0 and output["service_origin"] == PREVIEW
    assert "claim_id" not in output


def test_explicit_claim_is_sent_and_server_continues_to_enforce_ownership(monkeypatch):
    sent = transport(monkeypatch)
    result = invoke("--email-link", "human@example.com", "--claim-id", CLAIM)
    assert result.exit_code == 0
    assert json.loads(sent[0][0].data)["claim_id"] == CLAIM
    assert json.loads(result.stdout)["claim_id"] == CLAIM


@pytest.mark.parametrize("args", [
    ("--email-link", "human@example.com\""), ("--email-link", ""),
    ("--email-link", "human@example.com", "--request-id", "bad"),
    ("--email-link", "human@example.com", "--claim-id", "bad"),
    ("--request-id", REQUEST), ("--claim-id", CLAIM), ("--dry-run",),
])
def test_invalid_input_returns_structured_error_without_side_effects(args):
    result = invoke(*args)
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "invalid_request"


@pytest.mark.parametrize("url", ["http://outside.example/path", "https://user:private@example.com/path", "https://example.com:bad/path", "https://example.com/path?token=private", "https://example.com/path#private"])
def test_rejects_unsafe_service_configuration(monkeypatch, url):
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", url)
    result = invoke("--email-link", "human@example.com", "--dry-run")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "invalid_service_url"
    assert "private" not in result.output


@pytest.mark.parametrize("code,expected", [(429, "rate_limited"), (409, "request_conflict"), (503, "unavailable"), (302, "request_rejected")])
def test_service_errors_are_allowlisted_and_retryable_without_disclosing_response(monkeypatch, code, expected):
    sent = transport(monkeypatch, error=HTTPError("https://site.example", code, "private reason", {"Retry-After": "60"}, io.BytesIO(b'{"token":"private-token","error":"private-provider-detail"}')))
    result = invoke("--email-link", "human@example.com", "--request-id", REQUEST)
    output = json.loads(result.stdout)
    assert result.exit_code == 1 and output["status"] == expected
    assert output["request_id"] == REQUEST and len(sent) == 1
    if code == 429:
        assert output["retry_after_seconds"] == 60
    assert "private-" not in result.output


def test_uncertain_transport_can_be_retried_with_the_same_id(monkeypatch):
    sent = transport(monkeypatch, error=URLError("private-network-url"))
    for _ in range(2):
        result = invoke("--email-link", "human@example.com", "--request-id", REQUEST)
        output = json.loads(result.stdout)
        assert result.exit_code == 1 and output["status"] == "request_unconfirmed"
        assert output["request_id"] == REQUEST
        assert "--request-id" in output["hint"] and "private-network" not in result.output
    assert len(sent) == 2
    assert all(json.loads(request.data)["request_id"] == REQUEST for request, _ in sent)


@pytest.mark.parametrize("body", [b"not-json", b"x" * 5000, {}, {"status": "connected"}, {"status": "accepted", "expires_in_seconds": True}, {"status": "accepted", "expires_in_seconds": 600, "token": "private-token"}])
def test_invalid_receipt_never_becomes_authentication_success(monkeypatch, body):
    transport(monkeypatch, body=body)
    result = invoke("--email-link", "human@example.com")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "invalid_response"
    assert "private-token" not in result.output


def test_wire_protocol_and_redirect_refusal_over_loopback(monkeypatch):
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    leaked = []
    captured = []

    @contextmanager
    def server(handler):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}"
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    class RedirectTarget(BaseHTTPRequestHandler):
        def do_POST(self):
            leaked.append(self.path)
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            leaked.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    with server(RedirectTarget) as target:
        class Service(BaseHTTPRequestHandler):
            redirect = False

            def do_POST(self):
                payload = self.rfile.read(int(self.headers["Content-Length"]))
                captured.append((self.path, dict(self.headers), json.loads(payload)))
                if self.redirect:
                    self.send_response(302)
                    self.send_header("Location", target + "/capture")
                    self.end_headers()
                else:
                    self.send_response(202)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"accepted","expires_in_seconds":600,"hint":"private-provider-detail"}')

            def log_message(self, *_args):
                pass

        with server(Service) as origin:
            monkeypatch.setenv("MOVIESTAR_ACCOUNT_CLAIM_URL", origin + "/api/v1/account/claims")
            first = invoke("--email-link", "human@example.com", "--request-id", REQUEST)
            assert first.exit_code == 0, first.output
            assert json.loads(first.stdout)["service_origin"] == origin
            assert "private-provider-detail" not in first.output
            Service.redirect = True
            redirected = invoke("--email-link", "human@example.com", "--request-id", REQUEST)
            assert redirected.exit_code == 1
            assert json.loads(redirected.stdout)["status"] == "request_rejected"
            assert len(captured) == 2 and leaked == []
            for path, headers, body in captured:
                assert path == "/api/v1/account/sign-in-links"
                normalized_headers = {key.lower(): value for key, value in headers.items()}
                assert normalized_headers["x-moviestar-client"] == "moviestar-cli-v1"
                assert "authorization" not in normalized_headers
                assert body == {"email": "human@example.com", "request_id": REQUEST}
