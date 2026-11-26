"""Feedback uses only its approved connection; contact resolution stays server-side."""

import importlib
import json
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError

import pytest
from click.testing import CliRunner

from moviestar.cli import cli

feedback = importlib.import_module("moviestar.feedback")
account = importlib.import_module("moviestar.account")
SECRET = "mvs_" + "a" * 43


@pytest.fixture
def connected(tmp_path, monkeypatch):
    path = tmp_path / "account.json"
    path.write_text(json.dumps({"installation_id": "synthetic-installation",
        "installation_url": "https://trymoviestar.com/api/v1/account/installation",
        "email": "must-not-be-collected@example.invalid",
        "connection": {"credential_storage": "private_file", "credential_fallback": SECRET}}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(path))
    monkeypatch.delenv("MOVIESTAR_FEEDBACK_URL", raising=False)
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)


class Reply:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self):
        return json.dumps({"status": "received", "anonymous": False,
            "account_linked": True, "contact_available": True,
            "feedback_id": "synthetic-receipt", "reporter_email": "not-for-the-agent@example.invalid"}).encode()


def test_signed_in_feedback_uses_only_approved_auth_header_not_local_contact(connected, monkeypatch):
    requests = []
    monkeypatch.setattr(feedback, "urlopen", lambda request, **kwargs: requests.append(request) or Reply())
    result = CliRunner().invoke(cli, ["feedback", "--quick", "Deliberately shared note"])
    assert result.exit_code == 0, result.stdout
    request = requests[0]
    assert request.get_header("Authorization") == "Bearer " + SECRET
    assert request.get_header("X-moviestar-client") == "moviestar-cli-v1"
    assert json.loads(request.data) == {"message": "Deliberately shared note", "moviestar_version": feedback.__version__}
    body = json.loads(result.stdout)
    assert body["account_linked"] is True
    assert body["anonymous"] is False
    for text in [SECRET, "must-not-be-collected@example.invalid", "not-for-the-agent@example.invalid"]:
        assert text not in result.output


@pytest.mark.parametrize("url", ["https://other.example/api/feedback", "https://trymoviestar.com/not-feedback",
    "https://trymoviestar.com/api/feedback?forward=other", "http://trymoviestar.com/api/feedback",
    "https://trymoviestar.com:8443/api/feedback"])
def test_custom_endpoint_never_reads_or_receives_saved_credential(connected, monkeypatch, url):
    monkeypatch.setenv("MOVIESTAR_FEEDBACK_URL", url)
    monkeypatch.setattr(account, "_credential_for_connection", lambda state: pytest.fail("must not read credential"))
    monkeypatch.setattr(feedback, "urlopen", lambda *a, **kw: pytest.fail("must not send"))
    result = CliRunner().invoke(cli, ["feedback", "--quick", "Note"])
    assert result.exit_code == 1
    assert "--anonymous" in result.stdout or "HTTPS" in result.stdout
    assert SECRET not in result.output


def test_anonymous_bypasses_all_account_and_credential_access(connected, monkeypatch):
    monkeypatch.setattr(account, "_read_state", lambda: pytest.fail("anonymous must not read account state"))
    requests = []
    class AnonymousReply(Reply):
        def read(self): return b'{"status":"received","anonymous":true}'
    monkeypatch.setattr(feedback, "urlopen", lambda request, **kwargs: requests.append(request) or AnonymousReply())
    result = CliRunner().invoke(cli, ["feedback", "--anonymous", "--quick", "Anonymous note"])
    assert result.exit_code == 0, result.stdout
    assert requests[0].get_header("Authorization") is None
    assert json.loads(result.stdout)["anonymous"] is True


def test_unavailable_connected_credential_does_not_silently_submit_anonymously(connected, monkeypatch):
    monkeypatch.setattr(account, "_credential_for_connection", lambda state: None)
    monkeypatch.setattr(feedback, "urlopen", lambda *a, **kw: pytest.fail("must not send"))
    result = CliRunner().invoke(cli, ["feedback", "--quick", "Note"])
    assert result.exit_code == 1
    assert "--anonymous" in result.stdout


def test_template_does_not_read_account_state(connected, monkeypatch):
    monkeypatch.setattr(account, "_read_state", lambda: pytest.fail("template must not read account state"))
    result = CliRunner().invoke(cli, ["feedback", "--template"])
    assert result.exit_code == 0
    assert "account" in result.stdout.lower()
    assert "--anonymous" in result.stdout


def test_anonymous_rejects_an_explicit_contact_address(connected, tmp_path, monkeypatch):
    file = tmp_path / "note.md"
    file.write_text("Note\n\n## Follow-up email (optional)\nexplicit@example.invalid\n")
    monkeypatch.setattr(feedback, "urlopen", lambda *a, **kw: pytest.fail("must not send"))
    result = CliRunner().invoke(cli, ["feedback", "--anonymous", "--file", str(file)])
    assert result.exit_code == 1
    assert "follow-up" in result.stdout.lower()


def test_revoked_credential_has_no_anonymous_retry(connected, monkeypatch):
    requests = []
    def reject(request, **kwargs):
        requests.append(request)
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"invalid"}'))
    monkeypatch.setattr(feedback, "urlopen", reject)
    result = CliRunner().invoke(cli, ["feedback", "--quick", "Note"])
    assert result.exit_code == 1
    assert len(requests) == 1
    assert requests[0].get_header("Authorization") == "Bearer " + SECRET
    assert SECRET not in result.output


def test_old_service_cannot_silently_claim_account_linkage(connected, monkeypatch):
    class OldReply(Reply):
        def read(self): return b'{"anonymous":true,"feedback_id":"already-received"}'
    monkeypatch.setattr(feedback, "urlopen", lambda *a, **kw: OldReply())
    result = CliRunner().invoke(cli, ["feedback", "--quick", "Note"])
    assert result.exit_code == 1
    receipt = json.loads(result.stdout)
    assert receipt["feedback_id"] == "already-received"
    assert receipt["anonymous"] is True
    assert "may have been received" in receipt["error"]


def test_redirect_never_forwards_report_or_credential(connected, monkeypatch, tmp_path):
    captured = []
    source_auth = []
    class Destination(BaseHTTPRequestHandler):
        def do_GET(self):
            captured.append(self.path);self.send_response(200);self.end_headers()
        def do_POST(self): self.do_GET()
        def log_message(self, *args): pass
    destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            source_auth.append(self.headers.get("Authorization"))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/capture")
            self.end_headers()
        def log_message(self, *args): pass
    source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for server in (source, destination): Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{source.server_port}"
    monkeypatch.setenv("MOVIESTAR_FEEDBACK_URL", origin + "/api/feedback")
    path = tmp_path / "account.json"
    state = json.loads(path.read_text())
    state["installation_url"] = origin + "/api/v1/account/installation"
    path.write_text(json.dumps(state))
    try:
        result = CliRunner().invoke(cli, ["feedback", "--quick", "Note"])
        assert result.exit_code == 1
        assert "302" in result.stdout
        assert source_auth == ["Bearer " + SECRET]
        assert captured == []
    finally:
        for server in (source, destination): server.shutdown();server.server_close()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.invalid/report",
    "http://example.invalid/api/feedback", "https://example.invalid:bad/api/feedback",
    "https://user:password@example.invalid/api/feedback"])
def test_unsafe_anonymous_endpoint_is_rejected_before_any_account_or_network_access(connected, monkeypatch, url):
    monkeypatch.setenv("MOVIESTAR_FEEDBACK_URL", url)
    monkeypatch.setattr(account, "_read_state", lambda: pytest.fail("must not read account state"))
    monkeypatch.setattr(feedback, "urlopen", lambda *a, **kw: pytest.fail("must not send"))
    result = CliRunner().invoke(cli, ["feedback", "--anonymous", "--quick", "Note"])
    assert result.exit_code == 1
    assert "service URL" in result.stdout
    assert "password" not in result.output


@pytest.mark.parametrize("failure", ["timeout", "connection", "http", "json", "encoding", "shape"])
@pytest.mark.parametrize("anonymous", [False, True])
def test_ambiguous_delivery_never_claims_account_linked_report_is_anonymous(
    connected, monkeypatch, failure, anonymous
):
    requests = []

    class InvalidReply(Reply):
        def read(self):
            return {"json": b"{", "encoding": b"\xff", "shape": b"[]"}[failure]

    def send(request, **kwargs):
        requests.append(request)
        if failure == "timeout":
            raise TimeoutError("Timed out waiting for receipt")
        if failure == "connection":
            raise OSError("Connection reset while reading receipt")
        if failure == "http":
            raise HTTPError(request.full_url, 503, "Unavailable", {}, io.BytesIO(b'{}'))
        return InvalidReply()

    monkeypatch.setattr(feedback, "urlopen", send)
    args = ["feedback", "--quick", "Synthetic report"] + (["--anonymous"] if anonymous else [])
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1
    assert len(requests) == 1
    assert requests[0].get_header("Authorization") == (None if anonymous else "Bearer " + SECRET)
    receipt = json.loads(result.stdout)
    assert receipt["anonymous"] is (True if anonymous else None)
    if not anonymous:
        assert "may have been received" in receipt["hint"]
        assert "Do not resend automatically" in receipt["hint"]
    assert SECRET not in result.output
