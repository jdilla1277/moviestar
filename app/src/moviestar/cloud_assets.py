"""Account asset discovery and project membership, without local media work."""
from __future__ import annotations

import json
from urllib.parse import urlencode
from uuid import uuid4

import click

from moviestar import cloud as service
from moviestar.cloud_creation_state import CreationBusy, CreationStateError, creation_request

CONTRACT = "project-asset-references-v1"
KINDS = {"video", "audio", "image", "transcript"}
HINTS = {**service._HINTS,
    "not_found": "This project, asset or reference is unavailable. Only owned account assets and projects are supported here.",
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


def _connection(command, operation):
    endpoint, credential = service._connection(command)
    cap = service._capabilities(command, endpoint, credential).get("project_assets")
    if (not isinstance(cap, dict) or cap.get("contract_version") != CONTRACT
            or cap.get("access") != "owned_assets_in_owned_projects" or cap.get("ready_only") is not True
            or cap.get("kinds") != ["video", "audio", "image", "transcript"]
            or not isinstance(cap.get("operations"), list) or operation not in cap["operations"]):
        service._fail(command, "unsupported_operation", "The connected service does not advertise this asset-reference operation. Existing project commands remain available.")
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
    return {k: raw[k] for k in ("id", "owner_account_id", "kind", "format", "filename", "content_type", "size_bytes", "state", "metadata", "created_at", "ready_at")}


def _reference(raw, project, asset):
    if not isinstance(raw, dict) or any(not isinstance(raw.get(k), str) or not service._UUID.fullmatch(raw[k]) for k in ("id", "project_id", "asset_id", "added_by_account_id")):
        return None
    if (raw["project_id"].lower() != project.lower() or raw["asset_id"].lower() != asset.lower()
            or raw.get("removed_at", "missing") is not None or not isinstance(raw.get("created_at"), str)):
        return None
    return {k: raw[k] for k in ("id", "project_id", "asset_id", "added_by_account_id", "created_at", "removed_at")}


def _list(command, project, limit, cursor):
    if project:
        _ids(command, project)
    if cursor is not None and not service._CURSOR.fullmatch(cursor):
        service._fail(command, "invalid_request", "Use the previous page's next_cursor unchanged.")
    endpoint, credential = _connection(command, "references:list" if project else "assets:list")
    path = f"/projects/{project.lower()}/assets" if project else "/assets"
    status, body = service._request(endpoint + path + "?" + urlencode({"limit": limit, **({"cursor": cursor} if cursor else {})}), credential)
    if status != 200:
        _fail(command, status, body)
    items, next_cursor = body.get("items"), body.get("next_cursor", "missing")
    valid = body.get("contract_version") == CONTRACT and isinstance(items, list) and len(items) <= limit
    valid = valid and (next_cursor is None or isinstance(next_cursor, str) and bool(service._CURSOR.fullmatch(next_cursor)))
    if project:
        valid = valid and isinstance(body.get("project_id"), str) and body["project_id"].lower() == project.lower()
    parsed = []
    if valid:
        for item in items:
            asset = _asset(item.get("asset")) if project and isinstance(item, dict) else _asset(item)
            if asset is None or (project and (not isinstance(item.get("reference_id"), str) or not service._UUID.fullmatch(item["reference_id"]))):
                valid = False
                break
            parsed.append({"reference_id": item["reference_id"], "asset": asset} if project else asset)
    if not valid or len({item["reference_id"] if project else item["id"] for item in parsed}) != len(parsed):
        service._fail(command, "invalid_response", "The service returned incomplete asset metadata. Retry later.")
    click.echo(json.dumps({"contract_version": CONTRACT, **({"project_id": project.lower()} if project else {}), "items": parsed, "next_cursor": next_cursor}, indent=2))


def register_asset_commands(cloud, projects):
    @cloud.group("assets")
    def assets():
        """Find existing ready account assets. No upload or local editing."""

    @assets.command("list")
    @click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
    @click.option("--cursor")
    def list_assets(limit, cursor):
        """List owned video, audio, image and transcript/caption originals.

        Legacy project sources and shared assets are not in this catalog yet.
        """
        _list("cloud assets list", None, limit, cursor)

    @projects.command("assets")
    @click.argument("project_id")
    @click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
    @click.option("--cursor")
    def project_assets(project_id, limit, cursor):
        """List existing account-asset references in an owned PROJECT_ID."""
        _list("cloud projects assets", project_id, limit, cursor)

    @projects.command("add-asset")
    @click.argument("project_id")
    @click.argument("asset_id")
    @click.option("--idempotency-key", help="Optional retry key; generated and saved when omitted.")
    def add_asset(project_id, asset_id, idempotency_key):
        """Reference a ready ASSET_ID in owned PROJECT_ID without copying it.

        Retry the same command after an uncertain reply. One asset can belong
        to several projects; each membership has a distinct reference ID.
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
