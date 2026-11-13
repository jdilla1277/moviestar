"""Project-scoped download links and an explicit browser sharing handoff."""
import json

import pytest
from click.testing import CliRunner

from moviestar import cloud
from moviestar.cli import cli
P = "123e4567-e89b-42d3-a456-426614174001"
R = "123e4567-e89b-42d3-a456-426614174003"
ASSET = {"id": "123e4567-e89b-42d3-a456-426614174002", "owner_account_id": "123e4567-e89b-42d3-a456-426614174004", "kind": "image", "format": "png", "filename": "image.png", "content_type": "image/png", "size_bytes": 3, "state": "ready", "metadata": {}, "created_at": "2026-11-13T00:00:00Z", "ready_at": "2026-11-13T00:00:00Z"}
CAP = {"contract_version": "project-asset-references-v1", "access": "owned_assets_in_owned_projects", "ready_only": True, "operations": ["assets:list", "references:list", "references:attach", "references:detach"], "kinds": ["video", "audio", "image", "transcript"]}


@pytest.fixture
def remote(monkeypatch):
    endpoint = "https://service.invalid/api/v1/account/cloud"
    state = {"path": f"/api/v1/account/cloud/projects/{P}/asset-references/{R}/download", "role": "owner", "sharing": True, "returned_id": P, "calls": []}
    monkeypatch.setattr(cloud, "_connection", lambda _: (endpoint, "synthetic"))

    def request(url, credential, **kwargs):
        state["calls"].append((url, kwargs))
        if url.endswith("/capabilities"):
            return 200, {"contract_version": "m33.3-personal-projects-1", "project_access": "owner_only", "operations": ["projects:read"],
                         "project_assets": CAP, **({"project_sharing": {"contract_version": "recipient-projects-v1"}} if state["sharing"] else {})}
        if url.endswith(f"/projects/{P}"):
            return 200, {"id": state["returned_id"], "title": "Shared project", "state": "active", "owner_id": ASSET["owner_account_id"], "current_revision": None, "access": {"role": state["role"]}}
        return 200, {"contract_version": "project-asset-references-v1", "project_id": P,
                     "items": [{"reference_id": R, "asset": ASSET, "download_path": state["path"]}], "next_cursor": None}

    monkeypatch.setattr(cloud, "_request", request)
    return state


def invoke(*args):
    result = CliRunner().invoke(cli, ["cloud", "projects", *args])
    assert result.stdout, repr(result.exception)
    return result, json.loads(result.stdout)


def test_project_asset_links_are_scoped_authenticated_urls_on_the_connected_service(remote):
    result, body = invoke("assets", P)
    assert result.exit_code == 0, result.output
    assert body["items"][0]["download_url"] == "https://service.invalid" + remote["path"]
    assert all(not options for _, options in remote["calls"])


@pytest.mark.parametrize("path", ["https://foreign.invalid/download", "//foreign.invalid/download", "/api/v1/assets/unknown/download"])
def test_foreign_or_unscoped_download_paths_are_rejected(remote, path):
    remote["path"] = path
    result, body = invoke("assets", P)
    assert result.exit_code == 1 and body["status"] == "invalid_response"


def test_sharing_handoff_requires_current_owner_access_and_never_sends_an_invitation(remote):
    result, body = invoke("share", P)
    assert result.exit_code == 0, result.output
    assert body["status"] == "browser_required"
    assert body["url"] == f"https://service.invalid/account/projects?project={P}#project-sharing"
    assert all(not options for _, options in remote["calls"])
    remote["role"] = "viewer"
    result, body = invoke("share", P)
    assert result.exit_code == 1 and body["status"] == "permission_denied"
    assert "url" not in body


def test_disabled_sharing_fails_before_reading_the_project(remote):
    remote["sharing"] = False
    result, body = invoke("share", P)
    assert result.exit_code == 1 and body["status"] == "unsupported_operation"
    assert len(remote["calls"]) == 1


def test_sharing_handoff_rejects_another_projects_ownership_receipt(remote):
    remote["returned_id"] = R
    result, body = invoke("share", P)
    assert result.exit_code == 1 and body["status"] == "invalid_response"
    assert "url" not in body
