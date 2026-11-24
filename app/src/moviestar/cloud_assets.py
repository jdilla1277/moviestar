"""Account asset discovery and project membership, without local media work."""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode
from uuid import uuid4

import click

from moviestar import cloud as service
from moviestar.cloud_creation_state import CreationBusy, CreationStateError, creation_request

CONTRACT = "project-asset-references-v1"
KINDS = {"video", "audio", "image", "transcript"}
HINTS = {**service._HINTS,
    "not_found": "This project, asset or reference is unavailable to the connected account.",
    "project_assets_not_shared": "This invitation includes project metadata only. Ask the owner for a new invitation that includes assets.",
    "asset_changed": "This asset was renamed elsewhere. Read its current display name and review your change before retrying.",
    "asset_not_ready": "Complete asset validation before adding it to a project.",
    "reference_removed": "That attachment remains removed. Use a new key only to deliberately add it again.",
    "idempotency_conflict": "Retry this attachment's original asset/key, or use a new key for another attachment."}


def _fail(command, status, body, **fields):
    error = body.get("error")
    code = "credential_invalid" if status == 401 else error.get("code") if isinstance(error, dict) else None
    code = code if isinstance(code, str) and code in HINTS else "cloud_unavailable"
    if code in {"reference_removed", "idempotency_conflict"}:
        fields["suggested_idempotency_key"] = str(uuid4())
    service._fail(command, code, HINTS[code], **fields)


def _connection(command, operation, shared=False):
    endpoint, credential = service._connection(command)
    cap = service._capabilities(command, endpoint, credential).get("project_assets")
    if (not isinstance(cap, dict) or cap.get("contract_version") != CONTRACT
            or cap.get("access") != "owned_assets_in_owned_projects" or cap.get("ready_only") is not True
            or cap.get("kinds") != ["video", "audio", "image", "transcript"]
            or not isinstance(cap.get("operations"), list) or operation not in cap["operations"]):
        service._fail(command, "unsupported_operation", "The connected service does not advertise this asset-reference operation. Existing project commands remain available.")
    if shared and cap.get("shared_collection") != "accepted_project_assets":
        service._fail(command, "unsupported_operation", "The connected service does not advertise the shared asset collection.")
    return endpoint, credential


def _ids(command, *values):
    if any(not service._UUID.fullmatch(value) for value in values):
        service._fail(command, "invalid_request", "Use the asset, project and reference UUIDs returned by the service.")


def _asset(raw):
    if not isinstance(raw, dict):
        return None
    if any(not isinstance(raw.get(k), str) or not service._UUID.fullmatch(raw[k]) for k in ("id", "owner_account_id")):
        return None
    if (not isinstance(raw.get("kind"), str) or raw["kind"] not in KINDS or raw.get("state") != "ready" or type(raw.get("size_bytes")) is not int or raw["size_bytes"] < 1
            or any(not isinstance(raw.get(k), str) or not raw[k] for k in ("filename", "format", "content_type", "created_at"))
            or not isinstance(raw.get("metadata"), dict) or "ready_at" not in raw
            or (raw["ready_at"] is not None and not isinstance(raw["ready_at"], str))):
        return None
    result = {k: raw[k] for k in ("id", "owner_account_id", "kind", "format", "filename", "content_type", "size_bytes", "state", "metadata", "created_at", "ready_at")}
    if "display_name" in raw:
        name = raw["display_name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 240:
            return None
        result["display_name"] = name
    return result


def _reference(raw, project, asset):
    if not isinstance(raw, dict) or any(not isinstance(raw.get(k), str) or not service._UUID.fullmatch(raw[k]) for k in ("id", "project_id", "asset_id", "added_by_account_id")):
        return None
    if (raw["project_id"].lower() != project.lower() or raw["asset_id"].lower() != asset.lower()
            or raw.get("removed_at", "missing") is not None or not isinstance(raw.get("created_at"), str)):
        return None
    return {k: raw[k] for k in ("id", "project_id", "asset_id", "added_by_account_id", "created_at", "removed_at")}


def _list(command, project, limit, cursor, shared=False):
    if project:
        _ids(command, project)
    if cursor is not None and not service._CURSOR.fullmatch(cursor):
        service._fail(command, "invalid_request", "Use the previous page's next_cursor unchanged.")
    endpoint, credential = _connection(command, "references:list" if project else "assets:list", shared)
    path = f"/projects/{project.lower()}/assets" if project else "/assets"
    status, body = service._request(endpoint + path + "?" + urlencode({"limit": limit, **({"cursor": cursor} if cursor else {}), **({"collection": "shared"} if shared else {})}), credential)
    if status != 200:
        _fail(command, status, body)
    items, next_cursor = body.get("items"), body.get("next_cursor", "missing")
    valid = body.get("contract_version") == CONTRACT and isinstance(items, list) and len(items) <= limit
    valid = valid and (next_cursor is None or isinstance(next_cursor, str) and bool(service._CURSOR.fullmatch(next_cursor)))
    if shared:
        valid = valid and body.get("collection") == "shared"
    if project:
        valid = valid and isinstance(body.get("project_id"), str) and body["project_id"].lower() == project.lower()
    parsed = []
    if valid:
        for item in items:
            asset = _asset(item.get("asset")) if project and isinstance(item, dict) else _asset(item)
            if asset is None or (project and (not isinstance(item.get("reference_id"), str) or not service._UUID.fullmatch(item["reference_id"]))):
                valid = False
                break
            if shared:
                via = item.get("shared_via")
                direct = isinstance(via, dict) and via.get("kind") == "asset"
                path_valid = (isinstance(via.get("invitation_id"), str) and bool(service._UUID.fullmatch(via["invitation_id"]))) if direct else (
                    isinstance(via, dict) and all(isinstance(via.get(k), str) and service._UUID.fullmatch(via[k]) for k in ("project_id", "reference_id")) and isinstance(via.get("project_title"), str))
                if (not path_valid or not isinstance(via.get("shared_by"), str) or not via["shared_by"]):
                    valid = False
                    break
                asset["shared_via"] = {k: via[k] for k in (("kind", "invitation_id", "shared_by") if direct else ("project_id", "project_title", "reference_id", "shared_by"))}
            entry = {"reference_id": item["reference_id"], "asset": asset} if project else asset
            if project and "download_path" in item:
                expected = f"/api/v1/account/cloud/projects/{project.lower()}/asset-references/{item['reference_id']}/download"
                if item["download_path"] != expected:
                    valid = False
                    break
                entry["download_url"] = endpoint.removesuffix("/api/v1/account/cloud") + expected
            parsed.append(entry)
    if not valid or len({item["reference_id"] if project else item["id"] for item in parsed}) != len(parsed):
        service._fail(command, "invalid_response", "The service returned incomplete asset metadata. Retry later.")
    click.echo(json.dumps({"contract_version": CONTRACT, **({"project_id": project.lower()} if project else {}), **({"collection": "shared"} if shared else {}), "items": parsed, "next_cursor": next_cursor}, indent=2))


def register_asset_commands(cloud, projects):
    @cloud.group("assets")
    def assets():
        """Manage account assets on the connected Cloud service."""

    from moviestar.cloud_asset_sharing import register_asset_sharing
    register_asset_sharing(assets)
    from moviestar.cloud_asset_download import register_asset_download
    register_asset_download(assets)

    @assets.command("get")
    @click.argument("asset_id")
    def get_asset(asset_id):
        """Read an owned asset or an asset available through accepted sharing.

        Returns a private download URL. Open it while signed in to the same
        account; API callers must send their own credential. Access is checked
        again on each download. No local file is written by this command.
        """
        command = "cloud assets get"
        _ids(command, asset_id)
        endpoint, credential = _connection(command, "assets:read")
        status, body = service._request(f"{endpoint}/assets/{asset_id.lower()}", credential)
        if status != 200:
            _fail(command, status, body)
        asset = _asset(body.get("asset"))
        expected = f"/api/v1/account/cloud/assets/{asset_id.lower()}/download"
        if asset is None or asset["id"].lower() != asset_id.lower() or body.get("download_path") != expected:
            service._fail(command, "invalid_response", "Could not confirm the asset or its private download URL.")
        click.echo(json.dumps({"command": command, "asset": asset, "download_url": endpoint.removesuffix("/api/v1/account/cloud") + expected}, indent=2))

    @assets.command("rename")
    @click.argument("asset_id")
    @click.argument("display_name")
    @click.option("--expected-name", help="Exact display name previously read; retain it when retrying an uncertain rename. Otherwise read it first.")
    def rename_asset(asset_id, display_name, expected_name):
        """Rename an owned asset's display name everywhere it is referenced.

        The original filename and bytes stay unchanged. If another name is
        current, stop and review it. After an uncertain reply retry with the
        SAME --expected-name; never silently replace a newer name.
        """
        command = "cloud assets rename"
        valid = (isinstance(display_name, str) and display_name == display_name.strip() and 1 <= len(display_name) <= 240
                 and not re.search(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", display_name))
        if not valid or (expected_name is not None and (not 1 <= len(expected_name) <= 240 or "\x00" in expected_name)):
            service._fail(command, "invalid_request", "Use a trimmed display name of 1–240 characters without control or bidirectional formatting characters.")
        _ids(command, asset_id)
        endpoint, credential = _connection(command, "assets:rename")
        if expected_name is None:
            url = endpoint.removesuffix("/api/v1/account/cloud") + f"/api/v1/assets/{asset_id.lower()}"
            status, body = service._request(url, credential)
            if status != 200:
                _fail(command, status, body)
            original = _asset(body.get("asset"))
            if original is None or original["id"].lower() != asset_id.lower():
                service._fail(command, "invalid_response", "Could not read this owned asset's current display name.")
            expected_name = original.get("display_name", original["filename"])
        fields = {"asset_id": asset_id.lower(), "display_name": display_name, "expected_display_name": expected_name}
        status, body = service._request(f"{endpoint}/assets/{asset_id.lower()}", credential, method="PATCH",
                                       payload={"display_name": display_name, "expected_display_name": expected_name})
        if status != 200 and 400 <= status < 500:
            _fail(command, status, body, rename_outcome="rejected", **fields)
        result = _asset(body.get("asset"))
        if (status != 200 or body.get("status") != "renamed" or type(body.get("changed")) is not bool or result is None
                or result["id"].lower() != asset_id.lower() or result.get("display_name") != display_name):
            service._fail(command, "rename_unconfirmed", "The rename may have succeeded. Read the asset, or retry with the same --expected-name. No automatic retry was sent.",
                          rename_outcome="unknown", **fields)
        click.echo(json.dumps({"command": command, "status": "renamed", "changed": body["changed"], "asset": result}, indent=2))

    @assets.command("list")
    @click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
    @click.option("--cursor")
    @click.option("--shared", is_flag=True, help="List assets available through accepted asset or project shares, once per asset.")
    def list_assets(limit, cursor, shared):
        """List owned video, audio, image and transcript/caption originals.

        Use --shared for assets accessible through accepted asset or project invitations.
        Legacy project sources are excluded.
        """
        _list("cloud assets list", None, limit, cursor, shared)

    @projects.command("assets")
    @click.argument("project_id")
    @click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
    @click.option("--cursor")
    def project_assets(project_id, limit, cursor):
        """List assets in an owned project or an asset-sharing viewer/editor invitation.

        Download URLs require the same account in a browser or authenticated
        developer client. Every download checks current access again.
        """
        _list("cloud projects assets", project_id, limit, cursor)

    @projects.command("add-asset")
    @click.argument("project_id")
    @click.argument("asset_id")
    @click.option("--idempotency-key", help="Optional retry key; generated and saved when omitted.")
    def add_asset(project_id, asset_id, idempotency_key):
        """Reference a ready ASSET_ID in a project you own or can edit.

        Retry the same command after an uncertain reply. An original you own or
        can independently access can belong to several projects; each membership has a distinct reference ID.
        """
        command = "cloud projects add-asset"
        _ids(command, project_id, asset_id)
        if idempotency_key is not None and not service._KEY.fullmatch(idempotency_key):
            service._fail(command, "invalid_request", "Use a retry key of 1–128 letters, digits, underscores or hyphens.")
        endpoint, credential = _connection(command, "references:attach")
        url = f"{endpoint}/projects/{project_id.lower()}/asset-references"
        fields = {"project_id": project_id.lower(), "asset_id": asset_id.lower()}
        try:
            with creation_request(url, credential, asset_id.lower(), idempotency_key) as pending:
                fields["idempotency_key"] = pending.key
                pending.save()
                status, body = service._request(url, credential, payload={"asset_id": asset_id.lower()}, key=pending.key)
                if status not in (200, 201):
                    code = body.get("error", {}).get("code") if isinstance(body.get("error"), dict) else None
                    rejected = 400 <= status < 500 and (not pending.recovering or code == "reference_removed")
                    if rejected:
                        pending.resolve()
                    _fail(command, status, body, attachment_outcome="rejected" if rejected else "unknown", **fields)
                reference = _reference(body.get("reference"), project_id, asset_id)
                if (body.get("contract_version") != CONTRACT or body.get("status") != "attached" or reference is None
                        or body.get("idempotency_key") != pending.key or type(body.get("replayed")) is not bool):
                    service._fail(command, "invalid_response", "Attachment may have succeeded. Retry the same command/key to check it.", attachment_outcome="unknown", **fields)
                try:
                    pending.resolve()
                except CreationStateError:
                    fields["recovery_warning"] = "Project updated, but local recovery could not be cleared. A retry checks the same attachment."
        except CreationBusy:
            service._fail(command, "attachment_in_progress", "Another attachment of this asset is running. Wait, then retry.")
        except CreationStateError:
            service._fail(command, "attachment_state_unavailable", "Could not save attachment recovery. Preserve the original key and inspect project assets before repairing local retry files.", **fields)
        click.echo(json.dumps({"command": command, "status": "attached", "reference": reference, "replayed": body["replayed"], **fields}, indent=2))

    @projects.command("remove-asset")
    @click.argument("project_id")
    @click.argument("reference_id")
    def remove_asset(project_id, reference_id):
        """Remove REFERENCE_ID only. The original and other projects survive.

        Use the reference ID from 'cloud projects assets', not the asset ID.
        Retry the same IDs after a lost reply; old removals cannot detach a
        newly added reference. Does not delete media or release its storage.
        """
        command = "cloud projects remove-asset"
        _ids(command, project_id, reference_id)
        endpoint, credential = _connection(command, "references:detach")
        status, body = service._request(f"{endpoint}/projects/{project_id.lower()}/asset-references/{reference_id.lower()}", credential, method="DELETE")
        fields = {"project_id": project_id.lower(), "reference_id": reference_id.lower()}
        if status != 204:
            _fail(command, status, body, removal_outcome="unknown" if status == 0 or status >= 500 else "rejected", **fields)
        click.echo(json.dumps({"command": command, "status": "removed", **fields,
            "hint": "Only this reference was removed. The original asset and its other project references remain."}, indent=2))
