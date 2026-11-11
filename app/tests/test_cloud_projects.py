"""Public CLI behavior for owned Cloud project metadata, using synthetic HTTP."""

import json
import os
import subprocess
import sys
from uuid import UUID
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
    state = {"project": None, "projects": {}, "key": None, "calls": [], "revoked": False, "lose_reply": False,
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
            if key in state["projects"]:
                original = state["projects"][key]
                if body["title"] != original["title"]:
                    return self.reply(409, {"error": {"code": "idempotency_conflict"}})
                return self.reply(200, {"status": "created", "project": original["project"]})
            state["key"] = key
            project_id = str(UUID(int=UUID(PROJECT).int + len(state["projects"])))
            state["project"] = {"id": project_id, "title": body["title"], "state": "active", "owner_id": OWNER, "current_revision": None}
            state["projects"][key] = {"title": body["title"], "project": state["project"]}
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


def test_create_generates_a_key_and_new_confirmed_invocations_create_new_projects(service):
    first_result, first = invoke("projects", "create", "Highlights")
    assert first_result.exit_code == 0, first_result.output
    assert UUID(first["idempotency_key"])
    second_result, second = invoke("projects", "create", "Highlights")
    assert second_result.exit_code == 0, second_result.output
    assert second["idempotency_key"] != first["idempotency_key"]
    assert second["project"]["id"] != first["project"]["id"]
    assert len(service[0]["projects"]) == 2


def test_automatic_key_survives_a_lost_response_and_process_restart(service, tmp_path):
    service[0]["lose_reply"] = True
    def create():
        result = subprocess.run([sys.executable, "-m", "moviestar", "cloud", "projects", "create", "Highlights"],
                                cwd=tmp_path, env=os.environ.copy(), capture_output=True, text=True, timeout=30)
        assert result.stdout, result.stderr
        return result, json.loads(result.stdout)
    result, failure = create()
    assert result.returncode == 1 and failure["creation_outcome"] == "unknown"
    result, recovered = create()
    assert result.returncode == 0, result.stdout + result.stderr
    assert recovered["idempotency_key"] == failure["idempotency_key"]
    assert recovered["project"]["id"] == PROJECT
    assert len(service[0]["projects"]) == 1


@pytest.mark.parametrize("title", ["Highlights", "Different title"])
def test_reused_explicit_key_suggests_a_new_key_without_a_second_write(service, title):
    invoke("projects", "create", "Highlights", "--idempotency-key", "existing")
    result, body = invoke("projects", "create", title, "--idempotency-key", "existing")
    assert result.exit_code == (0 if title == "Highlights" else 1)
    assert body["suggested_idempotency_key"] != "existing"
    assert UUID(body["suggested_idempotency_key"])
    assert body["suggested_idempotency_key"] in body["hint"]
    assert "already" in body["hint"].lower()
    assert len(service[0]["projects"]) == 1
    result, additional = invoke("projects", "create", title, "--idempotency-key", body["suggested_idempotency_key"])
    assert result.exit_code == 0
    assert additional["idempotency_key"] == body["suggested_idempotency_key"]
    assert additional["project"]["id"] != PROJECT
    assert len(service[0]["projects"]) == 2


def test_explicit_new_key_keeps_an_unresolved_automatic_request_recoverable(service):
    service[0]["lose_reply"] = True
    _, pending = invoke("projects", "create", "Highlights")
    assert pending["creation_outcome"] == "unknown"
    service[0]["lose_reply"] = False
    result, separate = invoke("projects", "create", "Highlights", "--idempotency-key", "deliberately-separate")
    assert result.exit_code == 0
    result, recovered = invoke("projects", "create", "Highlights")
    assert result.exit_code == 0
    assert recovered["idempotency_key"] == pending["idempotency_key"]
    assert recovered["project"]["id"] != separate["project"]["id"]


def test_generated_key_is_saved_before_post_without_credentials(service, monkeypatch):
    from moviestar import cloud
    real = cloud._request
    def request(url, credential, **kwargs):
        if kwargs.get("payload"):
            records = list(service[1].parent.glob("cloud-project-requests/**/*.json"))
            assert len(records) == 1
            text = records[0].read_text()
            assert kwargs["key"] in text and "Highlights" in text
            assert TOKEN not in text
            if os.name != "nt":
                assert records[0].stat().st_mode & 0o777 == 0o600
        return real(url, credential, **kwargs)
    monkeypatch.setattr(cloud, "_request", request)
    result, _ = invoke("projects", "create", "Highlights")
    assert result.exit_code == 0, result.output


def test_unknown_create_is_not_forgotten_after_a_rejected_retry(service, monkeypatch):
    from moviestar import cloud
    real = cloud._request
    def request(url, credential, **kwargs):
        if kwargs.get("payload") and rejected[0]:
            return 403, {"error": {"code": "account_consent_required"}}
        return real(url, credential, **kwargs)
    rejected = [False]
    monkeypatch.setattr(cloud, "_request", request)
    service[0]["lose_reply"] = True
    _, pending = invoke("projects", "create", "Highlights")
    rejected[0] = True
    result, failure = invoke("projects", "create", "Highlights")
    assert result.exit_code == 1 and failure["creation_outcome"] == "unknown"
    rejected[0] = False
    result, recovered = invoke("projects", "create", "Highlights")
    assert result.exit_code == 0
    assert recovered["idempotency_key"] == pending["idempotency_key"]


def test_corrupt_pending_state_stops_creation_and_preserves_recovery(service):
    service[0]["lose_reply"] = True
    invoke("projects", "create", "Highlights")
    records = list(service[1].parent.glob("cloud-project-requests/**/*.json"))
    assert len(records) == 1
    records[0].write_text("broken")
    posts = len([call for call in service[0]["calls"] if call[0] == "POST"])
    result, failure = invoke("projects", "create", "Highlights")
    assert result.exit_code == 1 and failure["status"] == "creation_state_unavailable"
    assert "projects list" in failure["hint"]
    assert records[0].read_text() == "broken"
    assert len([call for call in service[0]["calls"] if call[0] == "POST"]) == posts


def test_unwritable_recovery_state_stops_before_post(service, monkeypatch):
    from moviestar import cloud_creation_state
    def unavailable(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(cloud_creation_state.tempfile, "mkstemp", unavailable)
    result, failure = invoke("projects", "create", "Highlights")
    assert result.exit_code == 1 and failure["status"] == "creation_state_unavailable"
    assert all(call[0] != "POST" for call in service[0]["calls"])


@pytest.mark.parametrize("status, body", [(201, {}), (503, {"error": {"code": "cloud_unavailable"}})])
def test_unconfirmed_responses_keep_generated_keys(service, monkeypatch, status, body):
    from moviestar import cloud
    real = cloud._request
    unknown = [True]
    def request(url, credential, **kwargs):
        if kwargs.get("payload") and unknown[0]:
            real(url, credential, **kwargs)  # Commit before the bad response.
            return status, body
        return real(url, credential, **kwargs)
    monkeypatch.setattr(cloud, "_request", request)
    result, failure = invoke("projects", "create", "Highlights")
    assert result.exit_code == 1 and failure["creation_outcome"] == "unknown"
    unknown[0] = False
    result, recovered = invoke("projects", "create", "Highlights")
    assert result.exit_code == 0 and recovered["idempotency_key"] == failure["idempotency_key"]
    assert len(service[0]["projects"]) == 1


def test_confirmed_creation_reports_local_state_failure_without_losing_project(service, monkeypatch):
    from moviestar import cloud_creation_state
    real = cloud_creation_state._write
    def write(path, record):
        if record["state"] == "resolved":
            raise cloud_creation_state.CreationStateError("disk full")
        return real(path, record)
    monkeypatch.setattr(cloud_creation_state, "_write", write)
    result, created = invoke("projects", "create", "Highlights")
    assert result.exit_code == 0 and created["project"]["id"] == PROJECT
    assert created["recovery_warning"]
    monkeypatch.setattr(cloud_creation_state, "_write", real)
    result, recovered = invoke("projects", "create", "Highlights")
    assert result.exit_code == 0 and recovered["idempotency_key"] == created["idempotency_key"]
    assert len(service[0]["projects"]) == 1


def test_concurrent_same_title_creation_does_not_send_a_second_post(service, monkeypatch):
    from moviestar import cloud
    real = cloud._request
    concurrent = []
    def request(url, credential, **kwargs):
        if kwargs.get("payload"):
            result = subprocess.run([sys.executable, "-m", "moviestar", "cloud", "projects", "create", "Highlights"],
                                    env=os.environ.copy(), capture_output=True, text=True, timeout=30)
            concurrent.append(json.loads(result.stdout))
            assert result.returncode == 1
        return real(url, credential, **kwargs)
    monkeypatch.setattr(cloud, "_request", request)
    result, _ = invoke("projects", "create", "Highlights")
    assert result.exit_code == 0, result.output
    assert concurrent[0]["status"] == "creation_in_progress"
    assert len([call for call in service[0]["calls"] if call[0] == "POST"]) == 1


@pytest.mark.parametrize("changed", ["service", "installation", "title"])
def test_pending_automatic_keys_are_isolated(service, monkeypatch, changed):
    from moviestar import cloud
    real_connection = cloud._connection
    endpoint, token = real_connection("test")
    observed = []
    def request(url, credential, **kwargs):
        if not kwargs.get("payload"):
            return 200, service[0]["capabilities"]
        observed.append(kwargs["key"])
        return 0, {}
    monkeypatch.setattr(cloud, "_request", request)
    invoke("projects", "create", "Highlights")
    title = "Other" if changed == "title" else "Highlights"
    monkeypatch.setattr(cloud, "_connection", lambda _command:
        (endpoint + ("-other" if changed == "service" else ""), "mvs_" + "b" * 43 if changed == "installation" else token))
    invoke("projects", "create", title)
    assert len(observed) == 2 and observed[0] != observed[1]
    monkeypatch.setattr(cloud, "_connection", real_connection)
    invoke("projects", "create", "Highlights")
    assert observed[2] == observed[0]


def test_revocation_and_missing_projects_have_safe_actionable_errors(service):
    result, body = invoke("projects", "show", PROJECT)
    assert result.exit_code == 1 and body["status"] == "not_found"
    service[0]["revoked"] = True
    result, body = invoke("projects", "list")
    assert result.exit_code == 1 and body["status"] == "credential_invalid"
    assert "account status" in body["hint"] and TOKEN not in result.output


@pytest.mark.parametrize("saved_origin", [None, "", 42])
@pytest.mark.parametrize("args", [
    ("capabilities",),
    ("projects", "list"),
    ("projects", "show", PROJECT),
    ("projects", "create", "Highlights", "--idempotency-key", "origin-recovery"),
])
def test_missing_service_origin_blocks_credential_access_until_explicitly_supplied(service, monkeypatch, args, saved_origin):
    from moviestar import cloud

    state = json.loads(service[1].read_text())
    installation = state.pop("installation_url")
    if saved_origin is not None:
        state["installation_url"] = saved_origin
    service[1].write_text(json.dumps(state))
    before = service[1].read_bytes()
    real_credential = cloud._credential_for_connection
    credential_reads = []

    def credential(saved_state):
        credential_reads.append(saved_state)
        return real_credential(saved_state)

    requests = []
    real_request = cloud._request

    def request(url, token, **kwargs):
        requests.append(url)
        # Keep the regression test offline if the production fallback returns.
        if not url.startswith(installation.removesuffix("/installation") + "/cloud/"):
            return 0, {}
        return real_request(url, token, **kwargs)

    monkeypatch.setattr(cloud, "_credential_for_connection", credential)
    monkeypatch.setattr(cloud, "_request", request)
    result, failure = invoke(*args)
    assert result.exit_code == 1 and failure["status"] == "invalid_configuration"
    assert "MOVIESTAR_ACCOUNT_INSTALLATION_URL" in failure["hint"]
    assert "approved this installation" in failure["hint"]
    assert credential_reads == [] and requests == [] and service[0]["calls"] == []
    assert TOKEN not in result.output and service[1].read_bytes() == before

    monkeypatch.setenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", installation)
    result, body = invoke(*args)
    assert len(credential_reads) == 1 and service[0]["calls"]
    assert all(url.startswith(installation.removesuffix("/installation") + "/cloud/") for url in requests)
    assert result.exit_code == (1 if args[:2] == ("projects", "show") else 0), body
    assert service[1].read_bytes() == before


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
