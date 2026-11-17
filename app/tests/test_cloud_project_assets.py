"""Existing account assets can have independent, retryable project memberships."""
import json
from uuid import UUID

import pytest
from click.testing import CliRunner

from moviestar import cloud
from moviestar.cli import cli

P = "123e4567-e89b-42d3-a456-426614174001"
A = "123e4567-e89b-42d3-a456-426614174002"
R = "123e4567-e89b-42d3-a456-426614174003"
O = "123e4567-e89b-42d3-a456-426614174004"
CONTRACT = "project-asset-references-v1"
CAP = {"contract_version": CONTRACT, "access": "owned_assets_in_owned_projects", "ready_only": True,
       "operations": ["assets:list", "references:list", "references:attach", "references:detach"],
       "kinds": ["video", "audio", "image", "transcript"]}
ASSET = {"id": A, "owner_account_id": O, "kind": "image", "format": "png", "filename": "photo.png",
         "content_type": "image/png", "size_bytes": 3, "state": "ready", "metadata": {},
         "created_at": "2026-11-11T00:00:00Z", "ready_at": "2026-11-11T00:00:00Z"}
REF = {"id": R, "project_id": P, "asset_id": A, "added_by_account_id": O, "created_at": ASSET["created_at"], "removed_at": None}


@pytest.fixture
def remote(monkeypatch, tmp_path):
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(tmp_path / "account.json"))
    monkeypatch.setattr(cloud, "_connection", lambda _: ("https://example.invalid/api/v1/account/cloud", "synthetic"))
    state = {"cap": CAP.copy(), "calls": [], "lost": False, "code": None, "asset": ASSET.copy()}
    def request(url, credential, **kwargs):
        state["calls"].append((url, kwargs))
        if url.endswith("/capabilities"):
            return 200, {"contract_version": "m33.3-personal-projects-1", "project_access": "owner_only",
                         "operations": ["projects:read", "projects:write"], "project_assets": state["cap"]}
        if kwargs.get("payload"):
            if state["lost"]:
                state["lost"] = False
                return 0, {}
            if state["code"]:
                return 409, {"error": {"code": state["code"]}}
            return 201, {"contract_version": CONTRACT, "status": "attached", "reference": REF,
                         "idempotency_key": kwargs["key"], "replayed": False}
        if kwargs.get("method") == "DELETE":
            return 204, {}
        project = "/projects/" in url
        return 200, {"contract_version": CONTRACT, **({"project_id": P} if project else {}),
                     "items": [{"reference_id": R, "asset": state["asset"]}] if project else [state["asset"]], "next_cursor": None}
    monkeypatch.setattr(cloud, "_request", request)
    return state


def invoke(*args):
    result = CliRunner().invoke(cli, ["cloud", *args])
    assert result.stdout, repr(result.exception)
    return result, json.loads(result.stdout)


def test_catalog_attach_reopen_and_detach_use_distinct_reference_identity(remote):
    assert invoke("assets", "list")[1]["items"][0]["id"] == A
    result, added = invoke("projects", "add-asset", P, A)
    assert result.exit_code == 0, result.output
    assert UUID(added["idempotency_key"])
    assert added["reference"]["id"] == R
    assert invoke("projects", "assets", P)[1]["items"][0] == {"reference_id": R, "asset": ASSET}
    result, removed = invoke("projects", "remove-asset", P, R)
    assert result.exit_code == 0 and removed["status"] == "removed"
    assert remote["calls"][-1] == (f"https://example.invalid/api/v1/account/cloud/projects/{P}/asset-references/{R}", {"method": "DELETE"})
    assert not any("/uploads" in url or "/download" in url for url, _ in remote["calls"])


def test_lost_attachment_reply_preserves_the_generated_key_across_invocations(remote):
    remote["lost"] = True
    result, failure = invoke("projects", "add-asset", P, A)
    assert result.exit_code == 1 and failure["attachment_outcome"] == "unknown"
    result, recovered = invoke("projects", "add-asset", P, A)
    assert result.exit_code == 0, result.output
    assert recovered["idempotency_key"] == failure["idempotency_key"]
    writes = [args for _, args in remote["calls"] if args.get("payload")]
    assert writes == [{"payload": {"asset_id": A}, "key": recovered["idempotency_key"]}] * 2


def test_removed_receipt_is_not_automatically_reattached(remote):
    remote["code"] = "reference_removed"
    result, body = invoke("projects", "add-asset", P, A, "--idempotency-key", "old-attempt")
    assert result.exit_code == 1 and body["status"] == "reference_removed"
    assert body["idempotency_key"] == "old-attempt"
    assert UUID(body["suggested_idempotency_key"])
    assert len([args for _, args in remote["calls"] if args.get("payload")]) == 1


@pytest.mark.parametrize("field,value", [("contract_version", "future"), ("access", "all_assets"), ("ready_only", False)])
def test_unsupported_references_fail_before_any_mutation(remote, field, value):
    remote["cap"][field] = value
    result, body = invoke("projects", "add-asset", P, A)
    assert result.exit_code == 1 and body["status"] == "unsupported_operation"
    assert len(remote["calls"]) == 1


@pytest.mark.parametrize("field,value", [("kind", "future"), ("size_bytes", True), ("state", "pending"), ("id", "bad")])
def test_malformed_assets_are_not_presented_as_ready(remote, field, value):
    remote["asset"][field] = value
    result, body = invoke("projects", "assets", P)
    assert result.exit_code == 1 and body["status"] == "invalid_response"


def test_shared_collection_is_explicit_and_preserves_access_provenance(remote, monkeypatch):
    remote['cap']['shared_collection'] = 'accepted_project_assets'
    previous = cloud._request
    def request(url, credential, **kwargs):
        status, body = previous(url, credential, **kwargs)
        if 'collection=shared' in url:
            body.update(collection='shared', items=[{**ASSET, 'shared_via': {'project_id': P, 'project_title': 'Shared project', 'reference_id': R, 'shared_by': 'alex'}}])
        return status, body
    monkeypatch.setattr(cloud, '_request', request)
    result, body = invoke('assets', 'list', '--shared')
    assert result.exit_code == 0, result.output
    assert body['collection'] == 'shared'
    assert body['items'][0]['shared_via']['project_id'] == P
    assert 'collection=shared' in remote['calls'][-1][0]


def test_old_service_does_not_silently_return_owned_assets_for_shared(remote):
    result, body = invoke('assets', 'list', '--shared')
    assert result.exit_code == 1 and body['status'] == 'unsupported_operation'
    assert len(remote['calls']) == 1


@pytest.mark.parametrize('change', ['missing_collection', 'missing_provenance', 'bad_reference', 'duplicate'])
def test_shared_catalog_never_accepts_downgraded_or_malformed_rows(remote, monkeypatch, change):
    remote['cap']['shared_collection'] = 'accepted_project_assets'
    previous = cloud._request
    def request(url, credential, **kwargs):
        status, body = previous(url, credential, **kwargs)
        if 'collection=shared' in url:
            item = {**ASSET, 'shared_via': {'project_id': P, 'project_title': 'Shared', 'reference_id': R, 'shared_by': 'alex'}}
            body.update(collection='shared', items=[item])
            if change == 'missing_collection':
                body.pop('collection')
            elif change == 'missing_provenance':
                item.pop('shared_via')
            elif change == 'bad_reference':
                item['shared_via']['reference_id'] = 'bad'
            else:
                body['items'].append(item)
        return status, body
    monkeypatch.setattr(cloud, '_request', request)
    result, body = invoke('assets', 'list', '--shared')
    assert result.exit_code == 1 and body['status'] == 'invalid_response'


def test_rename_asset_preserves_original_and_sends_previous_name(remote, monkeypatch):
    remote['cap']['operations'] = [*remote['cap']['operations'], 'assets:rename']
    previous = cloud._request
    def request(url, credential, **kwargs):
        if kwargs.get('method') == 'PATCH':
            remote['calls'].append((url, kwargs))
            return 200, {'status': 'renamed', 'changed': True, 'asset': {**ASSET, 'display_name': 'Opening'}}
        return previous(url, credential, **kwargs)
    monkeypatch.setattr(cloud, '_request', request)
    result, body = invoke('assets', 'rename', A, 'Opening', '--expected-name', 'photo.png')
    assert result.exit_code == 0, result.output
    assert body['asset']['filename'] == 'photo.png' and body['asset']['display_name'] == 'Opening'
    assert remote['calls'][-1] == (f'https://example.invalid/api/v1/account/cloud/assets/{A}', {'method': 'PATCH', 'payload': {'display_name': 'Opening', 'expected_display_name': 'photo.png'}})


def test_rename_asset_on_old_services_fails_before_mutation(remote):
    result, body = invoke('assets', 'rename', A, 'Opening')
    assert result.exit_code == 1 and body['status'] == 'unsupported_operation'
    assert len(remote['calls']) == 1


def test_rename_reads_the_owned_name_when_no_compare_value_is_supplied(remote, monkeypatch):
    remote['cap']['operations'] = [*remote['cap']['operations'], 'assets:rename']
    previous = cloud._request
    def request(url, credential, **kwargs):
        if url.endswith(f'/api/v1/assets/{A}'):
            remote['calls'].append((url, kwargs))
            return 200, {'asset': {**ASSET, 'display_name': 'Previous'}}
        if kwargs.get('method') == 'PATCH':
            remote['calls'].append((url, kwargs))
            return 200, {'status': 'renamed', 'changed': True, 'asset': {**ASSET, 'display_name': 'Opening'}}
        return previous(url, credential, **kwargs)
    monkeypatch.setattr(cloud, '_request', request)
    result, body = invoke('assets', 'rename', A, 'Opening')
    assert result.exit_code == 0, result.output
    assert remote['calls'][-1][1]['payload']['expected_display_name'] == 'Previous'
    assert any(f'/api/v1/assets/{A}' in url for url, _ in remote['calls'])


@pytest.mark.parametrize('status,response,expected', [(0, {}, 'rename_unconfirmed'), (409, {'error': {'code': 'asset_changed'}}, 'asset_changed')])
def test_rename_unknown_or_stale_outcomes_preserve_the_compare_value_without_retry(remote, monkeypatch, status, response, expected):
    remote['cap']['operations'] = [*remote['cap']['operations'], 'assets:rename']
    previous = cloud._request
    def request(url, credential, **kwargs):
        if kwargs.get('method') == 'PATCH':
            remote['calls'].append((url, kwargs))
            return status, response
        return previous(url, credential, **kwargs)
    monkeypatch.setattr(cloud, '_request', request)
    result, body = invoke('assets', 'rename', A, 'Opening', '--expected-name', 'Previous')
    assert result.exit_code == 1 and body['status'] == expected
    assert body['expected_display_name'] == 'Previous'
    assert len([kw for _, kw in remote['calls'] if kw.get('method') == 'PATCH']) == 1


@pytest.mark.parametrize('name', ['', ' padded ', 'a\nb', 'a' * 241, '\u202Ehidden'])
def test_invalid_asset_display_names_fail_before_auth_or_network(remote, name):
    result, body = invoke('assets', 'rename', A, name)
    assert result.exit_code == 1 and body['status'] == 'invalid_request'
    assert remote['calls'] == []
