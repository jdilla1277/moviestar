"""Public CLI behavior for owned Cloud project metadata, using synthetic HTTP."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from click.testing import CliRunner

from moviestar.cli import cli

PROJECT = "123e4567-e89b-42d3-a456-426614174001"
OWNER = "123e4567-e89b-42d3-a456-426614174002"
TOKEN = "mvs_" + "a" * 43


@pytest.fixture
def service(tmp_path, monkeypatch):
    state = {"project": None, "key": None, "calls": [], "revoked": False, "lose_reply": False,
             "capabilities": {"contract_version": "m33.3-personal-projects-1", "operations": ["projects:read", "projects:write"],
                              "scopes": ["projects:read", "projects:write"], "project_access": "owner_only",
                              "limits": {"active_projects_per_owner": 50}, "saved_edits_enabled": False}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass

        def reply(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            state["calls"].append(("GET", self.path, dict(self.headers)))
            assert self.headers.get("Authorization") == f"Bearer {TOKEN}"
            assert self.headers.get("x-moviestar-client") == "moviestar-cli-v1"
            if state["revoked"]:
                return self.reply(401, {"error": {"code": "credential_invalid", "message": TOKEN}})
            if self.path.endswith("/capabilities"):
                return self.reply(200, state["capabilities"])
            if self.path.startswith("/api/v1/account/cloud/projects?"):
                return self.reply(200, {"items": [state["project"]] if state["project"] else [], "next_cursor": "opaque_next"})
            if self.path.endswith("/projects/" + PROJECT) and state["project"]:
                return self.reply(200, state["project"])
            return self.reply(404, {"error": {"code": "not_found"}})

        def do_POST(self):
            state["calls"].append(("POST", self.path, dict(self.headers)))
            assert self.path == "/api/v1/account/cloud/projects"
            assert self.headers.get("Authorization") == f"Bearer {TOKEN}"
            assert self.headers.get("x-moviestar-client") == "moviestar-cli-v1"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = self.headers.get("Idempotency-Key")
            if state["project"]:
                if key != state["key"] or body["title"] != state["project"]["title"]:
                    return self.reply(409, {"error": {"code": "idempotency_conflict"}})
                return self.reply(200, {"status": "created", "project": state["project"]})
            state["key"] = key
            state["project"] = {"id": PROJECT, "title": body["title"], "state": "active", "owner_id": OWNER, "current_revision": None}
            if state["lose_reply"]:
                self.close_connection = True
                return
            self.reply(201, {"status": "created", "project": state["project"]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = tmp_path / "account.json"
    profile.write_text(json.dumps({"installation_url": f"http://127.0.0.1:{server.server_port}/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": TOKEN}}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(profile))
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)
    yield state, profile
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def invoke(*args):
    result = CliRunner().invoke(cli, ["cloud", *args])
    assert result.stdout, repr(result.exception)
    return result, json.loads(result.stdout)


def test_create_list_and_reopen_across_commands_without_local_media_work(service, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    before = service[1].read_bytes()
    result, body = invoke("capabilities")
    assert result.exit_code == 0 and body["project_access"] == "owner_only"
    result, created = invoke("projects", "create", "Highlights", "--idempotency-key", "my-project")
    assert result.exit_code == 0, result.output
    assert created["project"]["id"] == PROJECT and created["idempotency_key"] == "my-project"
    assert created["project"]["current_revision"] is None
    result, listed = invoke("projects", "list", "--limit", "1", "--cursor", "opaque_next")
    assert result.exit_code == 0 and listed["items"][0]["id"] == PROJECT
    assert listed["next_cursor"] == "opaque_next"
    result, reopened = invoke("projects", "show", PROJECT)
    assert result.exit_code == 0 and reopened == created["project"]
    assert service[1].read_bytes() == before
    assert not (tmp_path / "moviestar").exists()
    assert all(TOKEN not in json.dumps(value) for value in [created, listed, reopened])
    assert any("limit=1&cursor=opaque_next" in call[1] for call in service[0]["calls"])


def test_lost_create_reply_recovers_same_project_with_original_key(service):
    service[0]["lose_reply"] = True
    result, failure = invoke("projects", "create", "Highlights", "--idempotency-key", "retry-key")
    assert result.exit_code == 1
    assert failure["idempotency_key"] == "retry-key" and failure["creation_outcome"] == "unknown"
    result, recovered = invoke("projects", "create", "Highlights", "--idempotency-key", "retry-key")
    assert result.exit_code == 0 and recovered["project"]["id"] == PROJECT
    result, conflict = invoke("projects", "create", "Changed", "--idempotency-key", "retry-key")
    assert result.exit_code == 1 and conflict["status"] == "idempotency_conflict"


def test_revocation_and_missing_projects_have_safe_actionable_errors(service):
    result, body = invoke("projects", "show", PROJECT)
    assert result.exit_code == 1 and body["status"] == "not_found"
    service[0]["revoked"] = True
    result, body = invoke("projects", "list")
    assert result.exit_code == 1 and body["status"] == "credential_invalid"
    assert "account status" in body["hint"] and TOKEN not in result.output


@pytest.mark.parametrize("args", [
    ("projects", "show", "../other"),
    ("projects", "create", " ", "--idempotency-key", "good"),
    ("projects", "create", "Good", "--idempotency-key", "bad key"),
    ("projects", "list", "--cursor", "&owner=other"),
])
def test_bad_inputs_fail_before_network_or_credential_access(service, args):
    result, body = invoke(*args)
    assert result.exit_code != 0 and "hint" in body
    assert service[0]["calls"] == []


def test_unknown_contract_and_unadvertised_writes_are_rejected(service):
    service[0]["capabilities"]["operations"] = ["projects:read"]
    result, body = invoke("projects", "create", "Highlights", "--idempotency-key", "one")
    assert result.exit_code == 1 and body["status"] == "unsupported_operation"
    assert all(call[0] != "POST" for call in service[0]["calls"])
    service[0]["capabilities"]["contract_version"] = "future-version"
    result, body = invoke("capabilities")
    assert result.exit_code == 1 and body["status"] == "unsupported_contract"


def test_cloud_help_is_offline_and_explains_remote_selection(monkeypatch):
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", "/nonexistent/profile")
    for args in [("--help",), ("cloud", "--help"), ("cloud", "projects", "create", "--help")]:
        result = CliRunner().invoke(cli, list(args))
        assert result.exit_code == 0
    assert "cloud" in CliRunner().invoke(cli, ["--help"]).output
    assert "local commands" in CliRunner().invoke(cli, ["cloud", "--help"]).output.lower()


def test_create_replay_accepts_current_project_title_after_a_later_rename(service, monkeypatch):
    from moviestar import cloud
    project = {"id": PROJECT, "title": "Renamed later", "owner_id": OWNER, "state": "active", "current_revision": None}
    real = cloud._request
    def request(url, credential, **kwargs):
        return (200, {"status": "created", "project": project}) if kwargs.get("payload") else real(url, credential, **kwargs)
    monkeypatch.setattr(cloud, "_request", request)
    result, body = invoke("projects", "create", "Original title", "--idempotency-key", "original")
    assert result.exit_code == 0 and body["project"]["title"] == "Renamed later"


def test_redirect_does_not_forward_credentials(service):
    # A different service origin must never receive an installation credential.
    from moviestar import cloud
    seen = []
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_GET(self):
            seen.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/credential-destination")
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        status, _ = cloud._request(f"http://127.0.0.1:{server.server_port}/redirect", TOKEN)
        assert status in (0, 302) and seen == ["/redirect"]
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize("body", [{}, {"items": [{}], "next_cursor": None}, {"items": [], "next_cursor": "&bad"}])
def test_malformed_project_pages_fail_without_echoing_remote_data(service, monkeypatch, body):
    from moviestar import cloud
    real = cloud._request
    monkeypatch.setattr(cloud, "_request", lambda url, credential, **kwargs:
        real(url, credential, **kwargs) if url.endswith("/capabilities") else (200, body))
    result, error = invoke("projects", "list")
    assert result.exit_code == 1 and error["status"] == "invalid_response"
