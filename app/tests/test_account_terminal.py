"""Terminal account setup preserves Clerk verification and explicit human consent."""
import importlib
import json
import stat
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from moviestar.cli import cli

account = importlib.import_module("moviestar.account")
TERMS = {"version": "hosted-terms-v1", "consent_version": "hosted-account-v1", "statement": "I confirm I am at least 18 and accept the terms.", "terms_url": "https://trymoviestar.com/terms", "privacy_url": "https://trymoviestar.com/privacy"}

@pytest.fixture
def terminal(monkeypatch, tmp_path):
    from moviestar import account_terminal as native
    path = tmp_path / "account.json"
    monkeypatch.setattr(account, "_account_state_path", lambda: path)
    monkeypatch.setattr(account, "_send_claim", lambda payload: (None, {"status": "approval_pending", "request_id": payload["request_id"], "expires_at": "2099-10-23T12:00:00Z", "terminal": {"frontend_api": "https://clerk.example.com", "terms": TERMS}}))
    requests = []
    def send(state, method, endpoint, data=None, session_jwt=None):
        requests.append((method, endpoint, data, session_jwt))
        if endpoint == "/client": return {}
        if endpoint == "/client/sign_ins":
            raise native.TerminalError("form_identifier_not_found")
        if endpoint == "/client/sign_ups": return {"id": "sua_test", "status": "missing_requirements"}
        if endpoint.endswith("prepare_verification"): return {"id": "sua_test", "status": "missing_requirements"}
        if endpoint.endswith("attempt_verification"):
            assert data == {"strategy": "email_code", "code": "123456"}
            return {"id": "sua_test", "status": "complete", "created_session_id": "sess_test"}
        if endpoint.endswith("/tokens"): return {"jwt": "private-session-jwt"}
        if endpoint.endswith("/end"): return {}
        if method == "GET": return {"status": "approval_pending", "needs_handle": True, "consent_required": True, "installation": {"id": account._read_state()["installation_id"], "name": "Test laptop"}, "terms": TERMS}
        return {"status": "connected", "handle": "@human"}
    monkeypatch.setattr(native, "_request", send)
    monkeypatch.setattr(account, "_exchange_claim", lambda *args: (None, {"status": "connected", "request_id": args[0], "email": "h***@example.com", "handle": "@human", "installation": {"id": args[1], "name": "Test laptop", "status": "approved"}, "credential": "mvs_" + "a" * 43, "device_grant_id": "123e4567-e89b-42d3-a456-426614174000"}))
    monkeypatch.setattr(account, "_store_device_credential", lambda *args: ("os_keyring", None))
    return native, path, requests


def invoke(*args, input=None): return CliRunner().invoke(cli, ["account", *args], input=input)


def test_new_user_can_connect_verify_accept_and_approve_without_browser(terminal):
    native, path, calls = terminal
    begin = invoke("connect", "human@example.com", "--terminal")
    assert begin.exit_code == 0, begin.output
    assert json.loads(begin.stdout)["status"] == "verification_pending"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finish = invoke("verify", "--code-stdin", "--handle", "human", "--accept-terms", TERMS["version"], "--age-18-or-older", "--approve-installation", input="123456\n")
    assert finish.exit_code == 0, finish.output
    assert json.loads(finish.stdout)["status"] == "connected"
    assert "pending_claim" not in json.loads(path.read_text())
    approval = next(c for c in calls if c[0] == "POST" and "/claims/" in c[1])
    assert approval[2]["age_confirmed"] is True
    assert approval[2]["installation_approved"] is True
    assert approval[3] == "private-session-jwt"
    assert "123456" not in begin.stdout + finish.stdout
    assert "private-session-jwt" not in finish.stdout


def test_verification_alone_never_accepts_terms_or_approves_installation(terminal):
    _, _, calls = terminal
    invoke("connect", "human@example.com", "--terminal")
    result = invoke("verify", "--code-stdin", input="123456\n")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "approval_required"
    assert not any(c[0] == "POST" and "/claims/" in c[1] for c in calls)
    result = invoke("verify", "--handle", "human", "--accept-terms", "obsolete", "--age-18-or-older", "--approve-installation")
    assert result.exit_code != 0
    assert not any(c[0] == "POST" and "/claims/" in c[1] for c in calls)


def test_wrong_code_retains_retryable_private_state_and_hides_provider_messages(terminal, monkeypatch):
    native, path, _ = terminal
    invoke("connect", "human@example.com", "--terminal")
    original = native._request
    def send(state, method, endpoint, data=None, session_jwt=None):
        if endpoint.endswith("attempt_verification"): raise native.TerminalError("form_code_incorrect")
        return original(state, method, endpoint, data, session_jwt)
    monkeypatch.setattr(native, "_request", send)
    result = invoke("verify", "--code-stdin", input="wrong\n")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "form_code_incorrect"
    assert "wrong" not in result.stdout
    assert "terminal_auth" in json.loads(path.read_text())["pending_claim"]


def test_existing_user_uses_email_factor_and_keeps_mfa_required(terminal, monkeypatch):
    native, _, calls = terminal
    original = native._request
    def send(state, method, endpoint, data=None, session_jwt=None):
        if endpoint == "/client/sign_ins": return {"id": "sia_test", "supported_first_factors": [{"strategy": "email_code", "email_address_id": "idn_test"}]}
        if endpoint.endswith("prepare_first_factor"): return {"id": "sia_test", "status": "needs_first_factor"}
        if endpoint.endswith("attempt_first_factor"): return {"id": "sia_test", "status": "needs_second_factor", "supported_second_factors": [{"strategy": "totp"}]}
        return original(state, method, endpoint, data, session_jwt)
    monkeypatch.setattr(native, "_request", send)
    assert invoke("connect", "human@example.com", "--terminal").exit_code == 0
    result = invoke("verify", "--code-stdin", input="123456\n")
    assert json.loads(result.stdout)["status"] == "second_factor_required"
    assert not any("/tokens" in c[1] for c in calls)


def test_terms_acceptance_requires_named_policy_and_age_confirmation(terminal, monkeypatch):
    native, _, _ = terminal
    monkeypatch.setattr(account, "_read_state", lambda: {"connection": {}, "installation_url": "https://trymoviestar.com/api/v1/account/installation"})
    monkeypatch.setattr(account, "_credential_for_connection", lambda state: "mvs_" + "a" * 43)
    transport = Mock(return_value={"status": "consent_required", "terms": TERMS})
    monkeypatch.setattr(native, "_request", transport)
    result = invoke("terms")
    assert json.loads(result.stdout)["status"] == "consent_required"
    assert transport.call_count == 1
    result = invoke("terms", "--accept", TERMS["version"])
    assert result.exit_code == 1
    assert transport.call_count == 1


def test_native_transport_rotates_private_client_token_and_does_not_follow_redirects(monkeypatch, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from moviestar import account_terminal as native
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            seen.append((self.path, self.headers.get("Authorization"), self.headers.get("Origin")))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path.startswith("/v1/client/redirect"):
                self.send_response(307)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/stolen")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Authorization", "rotated-private-client")
            self.end_headers()
            self.wfile.write(b'{"response":{"id":"sua_test"}}')
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = tmp_path / "state.json"
    monkeypatch.setattr(account, "_account_state_path", lambda: path)
    state = {"pending_claim": {"terminal_auth": {"origin": f"http://127.0.0.1:{server.server_port}", "client_jwt": "private-client"}}}
    try:
        assert native._request(state, "POST", "/client/sign_ups", {"email_address": "human@example.com"}) == {"id": "sua_test"}
        assert seen[0][1:] == ("private-client", None)
        assert "_is_native=1" in seen[0][0]
        assert json.loads(path.read_text())["pending_claim"]["terminal_auth"]["client_jwt"] == "rotated-private-client"
        with pytest.raises(native.TerminalError, match="http_307"):
            native._request(state, "POST", "/client/redirect")
        assert len(seen) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_signup_password_is_only_sent_to_clerk_and_never_printed(terminal, monkeypatch):
    native, _, calls = terminal
    original = native._request
    updates = []
    def send(state, method, endpoint, data=None, session_jwt=None):
        if endpoint.endswith("attempt_verification"): return {"id": "sua_test", "status": "missing_requirements", "missing_fields": ["password"]}
        if method == "PATCH":
            updates.append(data)
            return {"id": "sua_test", "status": "complete", "created_session_id": "sess_test"}
        return original(state, method, endpoint, data, session_jwt)
    monkeypatch.setattr(native, "_request", send)
    invoke("connect", "human@example.com", "--terminal")
    pending = invoke("verify", "--code-stdin", input="123456\n")
    assert json.loads(pending.stdout)["missing_fields"] == ["password"]
    finish = invoke("verify", "--password-stdin", input="test password fixture\n")
    assert json.loads(finish.stdout)["status"] == "approval_required"
    assert updates == [{"password": "test password fixture"}]
    assert "test password fixture" not in finish.stdout
    assert not any(c[0] == "POST" and "/claims/" in c[1] for c in calls)


def test_existing_consented_account_can_approve_without_repeating_age_or_handle(terminal, monkeypatch):
    native, _, calls = terminal
    original = native._request
    approvals = []
    def send(state, method, endpoint, data=None, session_jwt=None):
        if endpoint == "/client/sign_ins": return {"id": "sia_test", "supported_first_factors": [{"strategy": "email_code", "email_address_id": "idn_test"}]}
        if endpoint.endswith("prepare_first_factor"): return {"id": "sia_test", "status": "needs_first_factor"}
        if endpoint.endswith("attempt_first_factor"): return {"id": "sia_test", "status": "complete", "created_session_id": "sess_test"}
        if method == "GET" and "/claims/" in endpoint:
            result = original(state, method, endpoint, data, session_jwt)
            return {**result, "consent_required": False, "needs_handle": False, "handle": "@human"}
        if method == "POST" and "/claims/" in endpoint: approvals.append(data)
        return original(state, method, endpoint, data, session_jwt)
    monkeypatch.setattr(native, "_request", send)
    invoke("connect", "human@example.com", "--terminal")
    result = invoke("verify", "--code-stdin", "--approve-installation", input="123456\n")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "connected"
    assert approvals[0]["age_confirmed"] is False
    assert not any(c[1] == "/client/sign_ups" for c in calls)


def test_terms_acceptance_posts_only_current_policy_and_explicit_consent(terminal, monkeypatch):
    native, _, _ = terminal
    monkeypatch.setattr(account, "_read_state", lambda: {"connection": {}, "installation_url": "https://trymoviestar.com/api/v1/account/installation"})
    monkeypatch.setattr(account, "_credential_for_connection", lambda state: "mvs_" + "a" * 43)
    transport = Mock(side_effect=[{"status": "consent_required", "terms": TERMS}, {"status": "accepted", "terms": TERMS}])
    monkeypatch.setattr(native, "_request", transport)
    result = invoke("terms", "--accept", TERMS["version"], "--age-18-or-older")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "accepted"
    assert transport.call_args.args[3] == {"terms_accepted": True, "age_confirmed": True, "terms_version": TERMS["version"], "consent_version": TERMS["consent_version"]}
    assert "mvs_" not in result.stdout
