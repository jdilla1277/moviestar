"""Agent-first MovieStar account connection commands."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import tempfile
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import click

from moviestar import __version__


_ACCOUNT_URL = "https://trymoviestar.com/account"
_DEFAULT_CLAIM_URL = "https://trymoviestar.com/api/v1/account/claims"
_CLIENT_MARKER = "moviestar-cli-v1"
_CREDENTIAL_SERVICE = "trymoviestar.com"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_HANDLE_RE = re.compile(r"^@[a-z0-9][a-z0-9_]{2,29}$")


def _emit(payload: dict) -> None:
    click.echo(json.dumps(payload, indent=2))


def _mask_email(email: str) -> str:
    local, domain = email.split("@", 1)
    return f"{local[0]}***@{domain}"


def _account_state_path() -> Path:
    override = os.environ.get("MOVIESTAR_ACCOUNT_STATE")
    if override:
        return Path(override).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "moviestar" / "account.json"


def _read_state() -> dict:
    path = _account_state_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(state: dict) -> None:
    """Atomically save private installation state outside MovieStar projects."""
    path = _account_state_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        os.chmod(temporary, 0o600)
        with handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _installation_name() -> str:
    name = platform.node().strip()
    return name[:100] if name else f"MovieStar CLI on {platform.system() or 'computer'}"


def _decode_response(body: bytes) -> dict:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _send_claim(payload: dict) -> tuple[str | None, dict | None]:
    url = os.environ.get("MOVIESTAR_ACCOUNT_CLAIM_URL", _DEFAULT_CLAIM_URL)
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-moviestar-client": _CLIENT_MARKER,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = _decode_response(response.read())
            if response.status >= 400:
                message = body.get("error") or f"Server returned HTTP {response.status}."
                return str(message), body
            return None, body
    except HTTPError as error:
        body = _decode_response(error.read())
        return str(body.get("error") or f"Server returned HTTP {error.code}."), body
    except URLError as error:
        return str(error.reason), None
    except (TimeoutError, OSError) as error:
        return str(error), None
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return f"Invalid response from account service: {error}", None


def _exchange_claim(
    request_id: str, installation_id: str, claim_secret: str
) -> tuple[str | None, dict | None]:
    claims_url = os.environ.get("MOVIESTAR_ACCOUNT_CLAIM_URL", _DEFAULT_CLAIM_URL)
    request = Request(
        f"{claims_url.rstrip('/')}/{request_id}/exchange",
        data=json.dumps(
            {
                "installation_id": installation_id,
                "claim_secret": claim_secret,
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-moviestar-client": _CLIENT_MARKER,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = _decode_response(response.read())
            if response.status >= 400:
                return str(body.get("error") or f"Server returned HTTP {response.status}."), body
            return None, body
    except HTTPError as error:
        body = _decode_response(error.read())
        return str(body.get("error") or f"Server returned HTTP {error.code}."), body
    except URLError as error:
        return str(error.reason), None
    except (TimeoutError, OSError) as error:
        return str(error), None


def _store_device_credential(
    installation_id: str, credential: str
) -> tuple[str, str | None]:
    """Prefer the OS store; return a private-file fallback when unavailable."""
    try:
        import keyring

        keyring.set_password(_CREDENTIAL_SERVICE, installation_id, credential)
        return "os_keyring", None
    except Exception:
        return "private_file", credential


def _pending_request(email: str) -> tuple[dict, dict]:
    state = _read_state()
    installation_id = state.get("installation_id")
    try:
        uuid.UUID(str(installation_id))
    except (ValueError, TypeError, AttributeError):
        installation_id = str(uuid.uuid4())

    pending = state.get("pending_claim")
    valid_pending = isinstance(pending, dict) and pending.get("email") == email
    if valid_pending:
        try:
            uuid.UUID(str(pending.get("request_id")))
            valid_pending = (
                isinstance(pending.get("claim_secret"), str)
                and len(pending["claim_secret"]) >= 32
            )
        except (ValueError, TypeError, AttributeError):
            valid_pending = False
    if not valid_pending:
        pending = {
            "request_id": str(uuid.uuid4()),
            "email": email,
            "claim_secret": secrets.token_urlsafe(32),
            "expires_at": None,
        }

    payload = {
        "request_id": pending["request_id"],
        "email": email,
        "installation_id": installation_id,
        "installation_name": _installation_name(),
        "claim_challenge": hashlib.sha256(
            pending["claim_secret"].encode("utf-8")
        ).hexdigest(),
        "moviestar_version": __version__,
    }
    return payload, {
        "version": 1,
        "installation_id": installation_id,
        "pending_claim": pending,
    }


def _state_write_error(error: OSError, command: str = "account connect") -> None:
    _emit(
        {
            "command": command,
            "status": "error",
            "error": f"Could not save private account state: {error}",
            "hint": (
                "Make the user config directory writable, or set "
                "MOVIESTAR_ACCOUNT_STATE to a private writable path."
            ),
        }
    )
    raise click.exceptions.Exit(1)


@click.group()
def account() -> None:
    """Connect this installation to its human-owned MovieStar account.

    The agent starts the request with the human's email. The human approves
    the installation in a private browser flow; the agent never receives the
    email link or password. Local editing never requires an account.

    ``connect`` creates a real email claim and ``status`` completes an approved
    connection. ``disconnect`` and ``open`` still preview later lifecycle work.
    """


@account.command("connect")
@click.argument("email")
def connect(email: str) -> None:
    """Ask MovieStar to email an account claim to EMAIL.

    The request returns immediately. It gives the agent the same response
    whether the human is new or already has an account, so account existence
    is not disclosed.

    The human opens the private email, signs up or signs in, chooses a handle
    when needed, and approves this installation. The agent never receives the
    email link or password. Run ``moviestar account status`` afterward.

    A retryable private claim secret is saved with user-only permissions in
    MovieStar's user config directory, never in a MovieStar project. Set
    MOVIESTAR_ACCOUNT_CLAIM_URL to target a preview or self-hosted control plane.
    """
    normalized = email.strip().lower()
    if not _EMAIL_RE.fullmatch(normalized) or len(normalized) > 254:
        _emit(
            {
                "command": "account connect",
                "status": "error",
                "error": f"Invalid email address: {email!r}",
                "hint": "Pass the human's complete email address.",
            }
        )
        raise click.exceptions.Exit(1)

    existing_connection = _read_state().get("connection")
    if isinstance(existing_connection, dict):
        existing_handle = existing_connection.get("handle")
        if isinstance(existing_handle, str) and _HANDLE_RE.fullmatch(existing_handle):
            _emit(
                {
                    "command": "account connect",
                    "status": "already_connected",
                    "handle": existing_handle,
                    "hint": (
                        "Run 'moviestar account status' to inspect this connection. "
                        "Account switching will land with remote disconnect."
                    ),
                }
            )
            raise click.exceptions.Exit(1)

    payload, state = _pending_request(normalized)
    try:
        _write_state(state)
    except OSError as state_error:
        _state_write_error(state_error)
    error, remote = _send_claim(payload)
    if error:
        _emit(
            {
                "command": "account connect",
                "status": "error",
                "error": error,
                "hint": f"Retry 'moviestar account connect {normalized}' later.",
            }
        )
        raise click.exceptions.Exit(1)

    remote = remote or {}
    if (
        remote.get("status") != "approval_pending"
        or remote.get("request_id") != payload["request_id"]
        or not isinstance(remote.get("expires_at"), str)
    ):
        _emit(
            {
                "command": "account connect",
                "status": "error",
                "error": "The account service returned an invalid response.",
                "hint": f"Retry 'moviestar account connect {normalized}' later.",
            }
        )
        raise click.exceptions.Exit(1)

    state["pending_claim"]["expires_at"] = remote["expires_at"]
    try:
        _write_state(state)
    except OSError as state_error:
        _state_write_error(state_error)
    _emit(
        {
            "command": "account connect",
            "status": "approval_pending",
            "email": _mask_email(normalized),
            "request_id": payload["request_id"],
            "expires_at": remote["expires_at"],
            "hint": (
                "Ask the recipient to open the MovieStar email, then run "
                "'moviestar account status'."
            ),
        }
    )


@account.command("status")
def status() -> None:
    """Show this installation's account connection state.

    The completed flow reports ``approval_pending``, ``connected``, ``denied``,
    or ``expired``. A connected result includes the human's public ``@handle``.

    On first connection, MovieStar stores the revocable installation
    credential in the OS credential store, never in a MovieStar project.

    A pending claim is checked remotely. The private claim secret is exchanged
    for a device credential only after the human approves this installation.
    """
    state = _read_state()
    connection = state.get("connection")
    if isinstance(connection, dict):
        installation_id = state.get("installation_id")
        email = connection.get("email")
        handle = connection.get("handle")
        installation_name = connection.get("installation_name")
        storage = connection.get("credential_storage")
        if (
            isinstance(installation_id, str)
            and isinstance(email, str)
            and isinstance(handle, str)
            and _HANDLE_RE.fullmatch(handle)
            and isinstance(installation_name, str)
            and storage in {"os_keyring", "private_file"}
            and (
                storage != "private_file"
                or (
                    isinstance(connection.get("credential_fallback"), str)
                    and len(connection["credential_fallback"]) >= 32
                )
            )
        ):
            payload = {
                "command": "account status",
                "status": "connected",
                "email": email,
                "handle": handle,
                "installation": {
                    "id": installation_id,
                    "name": installation_name,
                    "status": "approved",
                    "credential_stored": True,
                    "credential_storage": storage,
                },
                "remote_status_checked": False,
                "hint": "Run 'moviestar account open' to manage this connection.",
            }
            if storage == "private_file":
                payload["warning"] = (
                    "The OS credential store was unavailable, so the credential "
                    "is in MovieStar's user-only account state file."
                )
            _emit(payload)
            return

    pending = state.get("pending_claim")
    if isinstance(pending, dict):
        email = pending.get("email")
        request_id = pending.get("request_id")
        expires_at = pending.get("expires_at")
        claim_secret = pending.get("claim_secret")
        installation_id = state.get("installation_id")
        if (
            isinstance(email, str)
            and _EMAIL_RE.fullmatch(email)
            and isinstance(request_id, str)
            and isinstance(expires_at, str)
            and isinstance(claim_secret, str)
            and len(claim_secret) >= 32
            and isinstance(installation_id, str)
        ):
            error, remote = _exchange_claim(
                request_id, installation_id, claim_secret
            )
            if error:
                _emit(
                    {
                        "command": "account status",
                        "status": "error",
                        "error": error,
                        "hint": "Retry 'moviestar account status' later.",
                    }
                )
                raise click.exceptions.Exit(1)
            remote = remote or {}
            remote_status = remote.get("status")
            if remote.get("request_id") != request_id or remote_status not in {
                "approval_pending",
                "connected",
                "denied",
                "expired",
            }:
                _emit(
                    {
                        "command": "account status",
                        "status": "error",
                        "error": "The account service returned an invalid response.",
                        "hint": "Retry 'moviestar account status' later.",
                    }
                )
                raise click.exceptions.Exit(1)

            if remote_status in {"denied", "expired"}:
                state.pop("pending_claim", None)
                try:
                    _write_state(state)
                except OSError as state_error:
                    _state_write_error(state_error, "account status")
                _emit(
                    {
                        "command": "account status",
                        "status": remote_status,
                        "request_id": request_id,
                        "remote_status_checked": True,
                        "hint": (
                            "Run 'moviestar account connect EMAIL' to start a new "
                            "request."
                        ),
                    }
                )
                return

            if remote_status == "connected":
                remote_email = remote.get("email")
                handle = remote.get("handle")
                installation = remote.get("installation")
                credential = remote.get("credential")
                grant_id = remote.get("device_grant_id")
                valid_installation = (
                    isinstance(installation, dict)
                    and installation.get("id") == installation_id
                    and installation.get("status") == "approved"
                    and isinstance(installation.get("name"), str)
                )
                try:
                    uuid.UUID(str(grant_id))
                    valid_grant = True
                except (ValueError, TypeError, AttributeError):
                    valid_grant = False
                if not (
                    isinstance(remote_email, str)
                    and isinstance(handle, str)
                    and _HANDLE_RE.fullmatch(handle)
                    and valid_installation
                    and isinstance(credential, str)
                    and credential.startswith("mvs_")
                    and len(credential) >= 32
                    and valid_grant
                ):
                    _emit(
                        {
                            "command": "account status",
                            "status": "error",
                            "error": "The account service returned an invalid approval.",
                            "hint": "Retry 'moviestar account status' later.",
                        }
                    )
                    raise click.exceptions.Exit(1)

                credential_storage, fallback = _store_device_credential(
                    installation_id, credential
                )
                connection = {
                    "email": remote_email,
                    "handle": handle,
                    "installation_name": installation["name"],
                    "device_grant_id": grant_id,
                    "credential_storage": credential_storage,
                }
                if fallback:
                    connection["credential_fallback"] = fallback
                connected_state = {
                    "version": 1,
                    "installation_id": installation_id,
                    "connection": connection,
                }
                try:
                    _write_state(connected_state)
                except OSError as state_error:
                    _state_write_error(state_error, "account status")
                payload = {
                    "command": "account status",
                    "status": "connected",
                    "email": remote_email,
                    "handle": handle,
                    "installation": {
                        "id": installation_id,
                        "name": installation["name"],
                        "status": "approved",
                        "credential_stored": True,
                        "credential_storage": credential_storage,
                    },
                    "remote_status_checked": True,
                    "hint": "Run 'moviestar account open' to manage this connection.",
                }
                if fallback:
                    payload["warning"] = (
                        "The OS credential store was unavailable, so the credential "
                        "is in MovieStar's user-only account state file."
                    )
                _emit(payload)
                return

            remote_expiry = remote.get("expires_at")
            if not isinstance(remote_expiry, str):
                _emit(
                    {
                        "command": "account status",
                        "status": "error",
                        "error": "The account service returned an invalid response.",
                        "hint": "Retry 'moviestar account status' later.",
                    }
                )
                raise click.exceptions.Exit(1)
            _emit(
                {
                    "command": "account status",
                    "status": "approval_pending",
                    "request_id": request_id,
                    "email": _mask_email(email),
                    "expires_at": remote_expiry,
                    "remote_status_checked": True,
                    "hint": (
                        "Ask the recipient to open the MovieStar email, then retry "
                        "'moviestar account status'."
                    ),
                }
            )
            return

    _emit(
        {
            "command": "account status",
            "status": "not_connected",
            "remote_status_checked": False,
            "hint": (
                "Run 'moviestar account connect EMAIL' to email the human an "
                "account claim."
            ),
        }
    )


@account.command("disconnect")
def disconnect() -> None:
    """Revoke this installation's account access.

    The real command revokes the installation remotely and removes its local
    credential. Projects and local editing remain available afterward.

    \b
    MOCKED SURFACE
      Reports the planned revocation without changing remote or local state.
    """
    _emit(
        {
            "command": "account disconnect",
            "status": "would_disconnect",
            "installation_id": "ins_mock_current",
            "revokes_remote_grant": False,
            "removes_local_credential": False,
            "local_editing_available": True,
            "mocked_surface_note": (
                "Surface preview only: nothing was revoked and no local "
                "credential was removed."
            ),
            "hint": (
                "The real command disconnects only hosted services; local "
                "editing remains available."
            ),
        }
    )


@account.command("open")
def open_account() -> None:
    """Open the human's MovieStar account page.

    The account page manages the handle, password, and connected installations.
    It is separate from local MovieStar projects.

    \b
    MOCKED SURFACE
      Reports the destination without opening a browser.
    """
    _emit(
        {
            "command": "account open",
            "status": "would_open_account",
            "url": _ACCOUNT_URL,
            "opened_browser": False,
            "mocked_surface_note": (
                "Surface preview only: no browser was opened."
            ),
            "hint": (
                "The real command opens the account page for the connected human."
            ),
        }
    )
