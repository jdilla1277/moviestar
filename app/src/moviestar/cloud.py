"""Explicit personal-account Cloud project metadata commands."""

from __future__ import annotations

import json
import os
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener
from uuid import uuid4

import click

from moviestar.account import (
    _CLIENT_MARKER, _NoRedirectHandler, _credential_for_connection,
    _installation_url, _read_state, _service_url_error,
)
from moviestar.cloud_creation_state import CreationBusy, CreationStateError, creation_request

_CONTRACT = "m33.3-personal-projects-1"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_HINTS = {
    "credential_invalid": "Run 'moviestar account status' and reconnect this installation if necessary.",
    "account_setup_required": "Finish account setup in your browser, then retry.",
    "account_consent_required": "Run 'moviestar account terms' and ask the account owner to review the current terms.",
    "personal_cloud_unavailable": "Personal Cloud projects are not enabled on this service. Retry when access is available.",
    "cloud_unavailable": "The Cloud service is unavailable. Retry later.",
    "not_found": "This project is unavailable to the connected account. Check its ID and account access.",
    "idempotency_conflict": "Retry with the original title and key, or choose a new key to deliberately create another project.",
    "capacity_exceeded": "The service's active project allowance has been reached. This is separate from finished-video storage.",
    "invalid_request": "Check the title, project ID, pagination, and idempotency key in the command help.",
    "request_too_large": "Use a project title of at most 200 characters.",
}


def _fail(command: str, status: str, hint: str, **fields) -> None:
    click.echo(json.dumps({"command": command, "status": status, "hint": hint, **fields}, indent=2))
    raise click.exceptions.Exit(1)


def _connection(command: str) -> tuple[str, str]:
    state = _read_state()
    recorded = state.get("installation_url")
    if not (isinstance(recorded, str) and recorded) and not os.environ.get("MOVIESTAR_ACCOUNT_INSTALLATION_URL"):
        _fail(command, "invalid_configuration",
              "Set MOVIESTAR_ACCOUNT_INSTALLATION_URL to the service that approved this installation, then retry.")
    installation = _installation_url()
    try:
        parsed = urlsplit(installation)
        valid = not _service_url_error(installation) and parsed.path == "/api/v1/account/installation" and not parsed.query
        parsed.port  # Reject malformed ports before touching credentials.
    except ValueError:
        valid = False
    if not valid:
        _fail(command, "invalid_configuration", "Use the service origin recorded by account connection.")
    credential = _credential_for_connection(state)
    if not credential or not re.fullmatch(r"mvs_[A-Za-z0-9_-]{43}", credential):
        _fail(command, "credential_invalid", _HINTS["credential_invalid"])
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/account/cloud", credential


def _request(url: str, credential: str, *, payload=None, key=None) -> tuple[int, dict]:
    request = Request(url, method="GET" if payload is None else "POST",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {credential}", "x-moviestar-client": _CLIENT_MARKER,
                 "Accept": "application/json", **({"Content-Type": "application/json"} if payload is not None else {}),
                 **({"Idempotency-Key": key} if key else {})})
    try:
        try:
            response = build_opener(_NoRedirectHandler()).open(request, timeout=15)
        except HTTPError as failure:
            response = failure
        with response:
            status, raw = response.status, response.read(1_048_577)
        if len(raw) > 1_048_576:
            return status, {}
        body = json.loads(raw.decode("utf-8"))
        return status, body if isinstance(body, dict) else {}
    except (URLError, HTTPException, OSError, ValueError):
        return 0, {}


def _failure(command: str, status: int, body: dict, **fields) -> None:
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    code = "credential_invalid" if status == 401 else code
    safe = code if isinstance(code, str) and code in _HINTS else "cloud_unavailable"
    hint = _HINTS[safe]
    if safe == "idempotency_conflict":
        suggested = str(uuid4())
        fields["suggested_idempotency_key"] = suggested
        hint = ("This key already belongs to a different creation request. "
                f"To create an additional project, use --idempotency-key {suggested}.")
    if fields.get("creation_outcome") == "unknown":
        hint += " Creation may have succeeded. Retry the exact title and idempotency key to recover the same project."
    _fail(command, safe, hint, **fields)


def _capabilities(command: str, endpoint: str, credential: str) -> dict:
    status, body = _request(endpoint + "/capabilities", credential)
    if status != 200:
        _failure(command, status, body)
    if body.get("contract_version") != _CONTRACT:
        _fail(command, "unsupported_contract", "This service uses an unsupported Cloud contract. Update the CLI or check the connected service.")
    operations = body.get("operations")
    if not isinstance(operations, list) or not all(isinstance(item, str) for item in operations) or body.get("project_access") != "owner_only":
        _fail(command, "invalid_response", "The service returned incomplete Cloud capabilities. Retry later.")
    fields = ("contract_version", "operations", "scopes", "project_access", "limits", "source_uploads_enabled",
              "source_imports_enabled", "media_processing_enabled", "saved_edits_enabled", "transcript_imports_enabled",
              "edit_formats", "source_formats", "transcript_formats", "retention", "project_access_scope", "project_sharing")
    return {name: body[name] for name in fields if name in body}


def _require(command: str, capabilities: dict, operation: str) -> None:
    if operation not in capabilities["operations"]:
        _fail(command, "unsupported_operation", "The connected service does not advertise this project operation. Inspect 'moviestar cloud capabilities'.")


def _project(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    if not all(isinstance(raw.get(name), str) and _UUID.fullmatch(raw[name]) for name in ("id", "owner_id")):
        return None
    title = raw.get("title")
    if not isinstance(title, str) or not 1 <= len(title) <= 200 or title != title.strip() or re.search(r"[\x00-\x1f\x7f]", title):
        return None
    if raw.get("state") != "active" or "current_revision" not in raw:
        return None
    revision = raw["current_revision"]
    if revision is not None and (not isinstance(revision, str) or not _UUID.fullmatch(revision)):
        return None
    parsed = {name: raw[name] for name in ("id", "title", "state", "owner_id", "current_revision")}
    if "access" in raw:
        access = raw["access"]
        if not isinstance(access, dict) or access.get("role") not in ("owner", "viewer"):
            return None
        parsed["access"] = {"role": access["role"]}
    return parsed


def _sharing_supported(capabilities: dict) -> bool:
    sharing = capabilities.get("project_sharing")
    return (isinstance(sharing, dict) and sharing.get("contract_version") == "recipient-projects-v1"
            and sharing.get("discovery_path") == "/api/v1/account/cloud/shared-projects"
            and sharing.get("detail_path") == "/api/v1/account/cloud/projects/:id"
            and sharing.get("roles") == {"viewer": ["projects:read"]}
            and sharing.get("permissions_are_resource_specific") is True
            and sharing.get("account_scopes_do_not_grant_collaborator_writes") is True)


@click.group()
def cloud():
    """Work explicitly with personal Cloud projects using this approved installation.

    Ordinary local commands stay local. These commands create or read Cloud
    metadata; they do not select a local workspace, upload media, or render.
    Connect the installation with 'moviestar account connect' first.
    """


@cloud.command("capabilities")
def capabilities_command():
    """Read the connected service's supported operations and limits."""
    endpoint, credential = _connection("cloud capabilities")
    click.echo(json.dumps(_capabilities("cloud capabilities", endpoint, credential), indent=2))


@cloud.group()
def projects():
    """Create, find, and reopen owned projects or accepted shared projects."""


@projects.command("create")
@click.argument("title")
@click.option("--idempotency-key", help="Optional retry key. Generated and saved automatically when omitted.")
def create_project(title: str, idempotency_key: str | None):
    """Create TITLE. Retry an interrupted command to recover the same project.

    Requires explicit remote operation and current account consent. This only
    creates project metadata. After success, another invocation creates a new
    project. An explicit key can recover a request or start a separate project.
    """
    command = "cloud projects create"
    if (not 1 <= len(title) <= 200 or title != title.strip() or re.search(r"[\x00-\x1f\x7f]", title)
            or (idempotency_key is not None and not _KEY.fullmatch(idempotency_key))):
        _fail(command, "invalid_request", _HINTS["invalid_request"])
    endpoint, credential = _connection(command)
    _require(command, _capabilities(command, endpoint, credential), "projects:write")
    common = {}
    try:
        with creation_request(endpoint, credential, title, idempotency_key) as pending:
            common["idempotency_key"] = pending.key
            pending.save()  # Durable before the service can commit a creation.
            status, body = _request(endpoint + "/projects", credential, payload={"title": title}, key=pending.key)
            if status not in (200, 201):
                rejected = 400 <= status < 500 and not pending.recovering
                if rejected:
                    try:
                        pending.resolve()
                    except CreationStateError:
                        common["recovery_warning"] = "The service rejected this request, but local retry state could not be updated. A retry may reuse the same key."
                _failure(command, status, body, creation_outcome="rejected" if rejected else "unknown", **common)
            project = _project(body.get("project"))
            if body.get("status") != "created" or project is None:
                _fail(command, "invalid_response", "Creation may have succeeded. Rerun this command to recover it, or retry the original title with the returned idempotency_key.", creation_outcome="unknown", **common)
            try:
                pending.resolve()
            except CreationStateError:
                # The project is confirmed. Retaining the pending request is
                # safe, but tell the caller why another invocation may reopen it.
                common["recovery_warning"] = "Project created, but local retry state could not be updated. Retry the original title/key to recover it; use an explicit new key for an additional project."
    except CreationBusy:
        _fail(command, "creation_in_progress", "Another creation with this title is running. Wait for it, then retry this command.")
    except CreationStateError:
        _fail(command, "creation_state_unavailable", "Could not read or save project retry state. No new creation was sent. Check 'moviestar cloud projects list' before repairing files under cloud-project-requests beside your account profile; preserve the original title/key for recovery.", **common)
    hint = f"Reopen with 'moviestar cloud projects show {project['id']}'."
    if status == 200:
        suggested = str(uuid4())
        common.update(replayed=True, suggested_idempotency_key=suggested)
        hint = (f"This key already belongs to an existing project; that project was recovered. {hint} "
                f"To create an additional project, use --idempotency-key {suggested}.")
    click.echo(json.dumps({"command": command, "status": "created", "project": project, **common,
                          "hint": hint}, indent=2))


@projects.command("list")
@click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
@click.option("--cursor", help="Use next_cursor from the previous page unchanged.")
@click.option("--shared", is_flag=True, help="List accepted shared projects instead of owned projects.")
def list_projects(limit: int, cursor: str | None, shared: bool):
    """Read one owned page, or --shared for accepted viewer invitations.

    Call both collections to find all accessible projects. Keep pagination
    cursors with their original collection. Missing media fields are
    unavailable, not empty. Listing does not authorize editing or resharing.
    """
    command = "cloud projects list"
    if cursor is not None and not _CURSOR.fullmatch(cursor):
        _fail(command, "invalid_request", _HINTS["invalid_request"])
    endpoint, credential = _connection(command)
    capabilities = _capabilities(command, endpoint, credential)
    _require(command, capabilities, "projects:read")
    if shared and not _sharing_supported(capabilities):
        _fail(command, "unsupported_operation", "Shared project discovery is not supported on this service. Owned projects can still be listed without --shared.")
    query = urlencode({"limit": limit, **({"cursor": cursor} if cursor else {})})
    status, body = _request(endpoint + ("/shared-projects?" if shared else "/projects?") + query, credential)
    if status != 200:
        _failure(command, status, body)
    items, cursor = body.get("items"), body.get("next_cursor")
    parsed = [] if isinstance(items, list) and len(items) <= limit else None
    if parsed is not None:
        for item in items:
            project = _project(item.get("project")) if shared and isinstance(item, dict) else _project(item)
            if project is None:
                parsed = None
                break
            if shared:
                handle = item.get("shared_by")
                if (body.get("contract_version") != "recipient-projects-v1" or item.get("role") != "viewer"
                        or item.get("permissions") != ["projects:read"] or not isinstance(handle, str)
                        or not re.fullmatch(r"[a-z0-9][a-z0-9_]{2,29}", handle)):
                    parsed = None
                    break
                project["shared_by"] = handle
            project["access"] = {"role": "viewer" if shared else "owner"}
            parsed.append(project)
    if shared and body.get("contract_version") != "recipient-projects-v1":
        parsed = None
    if parsed is None or any(item is None for item in parsed) or "next_cursor" not in body or (cursor is not None and (not isinstance(cursor, str) or not _CURSOR.fullmatch(cursor))):
        _fail(command, "invalid_response", "The service returned an incomplete project page. Retry later.")
    click.echo(json.dumps({"items": parsed, "next_cursor": cursor}, indent=2))


@projects.command("show")
@click.argument("project_id")
def show_project(project_id: str):
    """Reopen an owned or accepted shared PROJECT_ID without a local workspace.

    Access roles come from the service. Viewer access is metadata-only and
    does not grant media access, editing, rendering or resharing.
    """
    command = "cloud projects show"
    if not _UUID.fullmatch(project_id):
        _fail(command, "invalid_request", _HINTS["invalid_request"])
    endpoint, credential = _connection(command)
    _require(command, _capabilities(command, endpoint, credential), "projects:read")
    status, body = _request(endpoint + "/projects/" + project_id.lower(), credential)
    if status != 200:
        _failure(command, status, body)
    project = _project(body)
    if project is None or project["id"].lower() != project_id.lower():
        _fail(command, "invalid_response", "The service did not return the requested project. Retry later.")
    click.echo(json.dumps(project, indent=2))
