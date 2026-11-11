"""Shared project discovery and access labels through the public Cloud CLI."""
import json

import pytest
from click.testing import CliRunner

from moviestar import cloud
from moviestar.cli import cli

PROJECT = "123e4567-e89b-42d3-a456-426614174001"
OWNER = "123e4567-e89b-42d3-a456-426614174002"
PROJECT_DATA = {"id": PROJECT, "owner_id": OWNER, "title": "Highlights", "state": "active", "current_revision": None}
SHARING = {"contract_version": "recipient-projects-v1", "discovery_path": "/api/v1/account/cloud/shared-projects",
           "detail_path": "/api/v1/account/cloud/projects/:id", "roles": {"viewer": ["projects:read"]},
           "permissions_are_resource_specific": True, "account_scopes_do_not_grant_collaborator_writes": True}


@pytest.fixture
def remote(monkeypatch):
    caps = {"contract_version": "m33.3-personal-projects-1", "project_access": "owner_only", "operations": ["projects:read"], "project_sharing": SHARING}
    page = {"contract_version": "recipient-projects-v1", "items": [{"project": PROJECT_DATA, "shared_by": "alex", "role": "viewer", "permissions": ["projects:read"]}], "next_cursor": "next_page"}
    responses = {"capabilities": caps, "page": page, "project": {**PROJECT_DATA, "access": {"role": "viewer"}}}
    calls = []
    monkeypatch.setattr(cloud, "_connection", lambda _: ("https://example.test/api/v1/account/cloud", "synthetic"))
    def request(url, credential, **kwargs):
        calls.append(url)
        assert not kwargs  # Read-only: never uploads or creates a project.
        return 200, responses["capabilities" if url.endswith("/capabilities") else "page" if "?" in url else "project"]
    monkeypatch.setattr(cloud, "_request", request)
    return responses, calls


def invoke(*args):
    result = CliRunner().invoke(cli, ["cloud", "projects", *args])
    return result, json.loads(result.stdout)


def test_shared_projects_keep_canonical_id_role_and_same_collection_cursor(remote):
    responses, calls = remote
    result, listed = invoke("list", "--shared", "--limit", "1", "--cursor", "previous_page")
    assert result.exit_code == 0, result.output
    assert calls[-1].endswith("/shared-projects?limit=1&cursor=previous_page")
    assert listed["items"] == [{**PROJECT_DATA, "access": {"role": "viewer"}, "shared_by": "alex"}]
    assert listed["next_cursor"] == "next_page"
    result, opened = invoke("show", PROJECT)
    assert result.exit_code == 0 and opened["id"] == PROJECT
    assert opened["access"] == {"role": "viewer"}
    assert calls[-1].endswith("/projects/" + PROJECT)


def test_owned_list_is_explicit_and_old_services_remain_usable(remote):
    responses, calls = remote
    responses["capabilities"].pop("project_sharing")
    responses["page"] = {"items": [PROJECT_DATA], "next_cursor": None}
    result, body = invoke("list")
    assert result.exit_code == 0
    assert body["items"][0]["access"] == {"role": "owner"}
    assert calls[-1].endswith("/projects?limit=20")
    responses["project"] = PROJECT_DATA
    assert invoke("show", PROJECT)[0].exit_code == 0
    result, body = invoke("list", "--shared")
    assert result.exit_code == 1 and body["status"] == "unsupported_operation"
    assert "shared" in body["hint"].lower()
    assert not any("/shared-projects" in url for url in calls)


@pytest.mark.parametrize("field,value", [("contract_version", "future"), ("roles", {"editor": ["projects:write"]}),
    ("discovery_path", "https://foreign.invalid/projects")])
def test_rejects_incompatible_sharing_before_following_discovery(remote, field, value):
    responses, calls = remote
    responses["capabilities"]["project_sharing"] = {**SHARING, field: value}
    result, body = invoke("list", "--shared")
    assert result.exit_code == 1 and body["status"] == "unsupported_operation"
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [{"role": "editor"}, {"permissions": ["projects:read", "projects:write"]},
    {"shared_by": "bad handle"}, {"project": {}}])
def test_rejects_malformed_or_widened_shared_metadata(remote, changes):
    responses, _ = remote
    responses["page"]["items"][0] = {**responses["page"]["items"][0], **changes}
    result, body = invoke("list", "--shared")
    assert result.exit_code == 1 and body["status"] == "invalid_response"
    assert "Highlights" not in result.output


@pytest.mark.parametrize("role", ["editor", "admin", None])
def test_never_silently_drops_an_unknown_detail_role(remote, role):
    responses, _ = remote
    responses["project"] = {**PROJECT_DATA, "access": {"role": role}}
    result, body = invoke("show", PROJECT)
    assert result.exit_code == 1 and body["status"] == "invalid_response"
