"""Rename the same remote project without editing assets or local files."""
import json
import pytest
from click.testing import CliRunner
from moviestar import cloud
from moviestar.cli import cli

P = "123e4567-e89b-42d3-a456-426614174001"
PROJECT = {"id": P, "owner_id": "123e4567-e89b-42d3-a456-426614174002", "title": "Original", "state": "active", "current_revision": None, "access": {"role": "owner"}}

@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(cloud, "_connection", lambda _: ("https://example.invalid/api/v1/account/cloud", "synthetic"))
    state = {"project": PROJECT.copy(), "operations": ["projects:read", "projects:rename"], "calls": [], "failure": None, "lost": False}
    def request(url, credential, **kwargs):
        state["calls"].append((url, kwargs))
        if url.endswith("/capabilities"):
            return 200, {"contract_version": "m33.3-personal-projects-1", "project_access": "owner_only", "operations": state["operations"]}
        if kwargs.get("method") == "PATCH":
            if state["failure"]:
                return state["failure"], {"error": {"code": "project_changed" if state["failure"] == 409 else "credential_invalid", "message": "private provider detail"}}
            state["project"]["title"] = kwargs["payload"]["title"]
            if state["lost"]:
                return 0, {}
            return 200, {"status": "renamed", "changed": True, "project": state["project"]}
        return 200, state["project"]
    monkeypatch.setattr(cloud, "_request", request)
    return state

def invoke(*args):
    result = CliRunner().invoke(cli, ["cloud", "projects", "rename", *args])
    return result, json.loads(result.stdout) if result.stdout else {}

def test_rename_reads_current_title_and_patches_same_project(remote):
    result, body = invoke(P, "New title")
    assert result.exit_code == 0, result.output
    assert body["status"] == "renamed" and body["project"]["id"] == P and body["project"]["title"] == "New title"
    assert remote["calls"][-1] == (f"https://example.invalid/api/v1/account/cloud/projects/{P}", {"method": "PATCH", "payload": {"title": "New title", "expected_title": "Original"}})

def test_explicit_previous_title_preserves_a_retry_after_an_uncertain_reply(remote):
    result, _ = invoke(P, "New title", "--expected-title", "Original")
    assert result.exit_code == 0
    assert len(remote["calls"]) == 2  # Capability read and one explicit mutation.
    assert remote["calls"][-1][1]["payload"]["expected_title"] == "Original"

@pytest.mark.parametrize("code", [401, 409])
def test_conflict_or_revoked_access_does_not_trigger_another_write(remote, code):
    remote["failure"] = code
    result, body = invoke(P, "New title")
    assert result.exit_code == 1 and body["status"] == ("project_changed" if code == 409 else "credential_invalid")
    assert sum(args.get("method") == "PATCH" for _, args in remote["calls"]) == 1
    assert "private provider detail" not in result.output

def test_uncertain_reply_reports_the_exact_precondition_without_automatically_retrying(remote):
    remote["lost"] = True
    result, body = invoke(P, "New title")
    assert result.exit_code == 1 and body["rename_outcome"] == "unknown" and body["expected_title"] == "Original"
    assert sum(args.get("method") == "PATCH" for _, args in remote["calls"]) == 1

def test_old_service_and_viewer_do_not_receive_a_write(remote):
    remote["operations"] = ["projects:read"]
    assert invoke(P, "New title")[1]["status"] == "unsupported_operation"
    remote["operations"].append("projects:rename")
    remote["project"]["access"] = {"role": "viewer"}
    assert invoke(P, "New title")[1]["status"] == "project_read_only"
    assert not any(args.get("method") == "PATCH" for _, args in remote["calls"])

@pytest.mark.parametrize("args", [("bad-id", "New"), (P, " "), (P, "New\n"), (P, "New", "--expected-title", "")])
def test_invalid_rename_arguments_never_contact_the_service(remote, args):
    assert invoke(*args)[0].exit_code == 1
    assert not remote["calls"]
