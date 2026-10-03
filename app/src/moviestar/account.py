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
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import click

from moviestar import __version__


_ACCOUNT_URL = "https://trymoviestar.com/account"
_DEFAULT_CLAIM_URL = "https://trymoviestar.com/api/v1/account/claims"
_DEFAULT_INSTALLATION_URL = "https://trymoviestar.com/api/v1/account/installation"
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


def _installation_url() -> str:
    override = os.environ.get("MOVIESTAR_ACCOUNT_INSTALLATION_URL")
    if override:
        return override
    remembered = _read_state().get("installation_url")
    return remembered if isinstance(remembered, str) and remembered else _DEFAULT_INSTALLATION_URL


def _claim_url() -> str:
    override = os.environ.get("MOVIESTAR_ACCOUNT_CLAIM_URL")
    if override:
        return override
    state = _read_state()
    remembered = state.get("claim_url")
    if isinstance(remembered, str) and remembered:
        return remembered
    installation = state.get("installation_url")
    if isinstance(installation, str) and installation:
        parsed = urlsplit(installation)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/api/v1/account/claims"
    return _DEFAULT_CLAIM_URL


def _new_claim_url() -> str:
    return os.environ.get("MOVIESTAR_ACCOUNT_CLAIM_URL") or _DEFAULT_CLAIM_URL


def _installation_url_for_claim(claim_url: str) -> str:
    parsed = urlsplit(claim_url)
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/account/installation"


def _account_url() -> str:
    override = os.environ.get("MOVIESTAR_ACCOUNT_URL")
    if override:
        return override
    parsed = urlsplit(_installation_url())
    return f"{parsed.scheme}://{parsed.netloc}/account" if parsed.netloc else _ACCOUNT_URL


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


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _service_url_error(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError:
        return "The account service URL is invalid."
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return "The account service URL is invalid."
    if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        return "The account service URL must use HTTPS outside loopback."
    return None


def _send_claim(payload: dict) -> tuple[str | None, dict | None]:
    url = _claim_url()
    url_error = _service_url_error(url)
    if url_error:
        return url_error, None
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
        with build_opener(_NoRedirectHandler()).open(request, timeout=10) as response:
            body = _decode_response(response.read())
            if response.status >= 400:
                message = body.get("error") or f"Server returned HTTP {response.status}."
                return str(message), body
            return None, body
    except HTTPError as error:
        if 300 <= error.code < 400:
            return f"Server returned HTTP {error.code}.", None
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
    claims_url = _claim_url()
    url_error = _service_url_error(claims_url)
    if url_error:
        return url_error, None
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
        with build_opener(_NoRedirectHandler()).open(request, timeout=10) as response:
            body = _decode_response(response.read())
            if response.status >= 400:
                return str(body.get("error") or f"Server returned HTTP {response.status}."), body
            return None, body
    except HTTPError as error:
        if 300 <= error.code < 400:
            return f"Server returned HTTP {error.code}.", None
        body = _decode_response(error.read())
        return str(body.get("error") or f"Server returned HTTP {error.code}."), body
    except URLError as error:
        return str(error.reason), None
    except (TimeoutError, OSError) as error:
        return str(error), None


def _credential_for_connection(state: dict) -> str | None:
    connection = state.get("connection")
    if not isinstance(connection, dict):
        return None
    if connection.get("credential_storage") == "private_file":
        credential = connection.get("credential_fallback")
        return credential if isinstance(credential, str) and credential.startswith("mvs_") else None
    if connection.get("credential_storage") == "os_keyring":
        try:
            import keyring

            credential = keyring.get_password(_CREDENTIAL_SERVICE, str(state.get("installation_id")))
            return credential if isinstance(credential, str) and credential.startswith("mvs_") else None
        except Exception:
            return None
    return None


def _installation_request(method: str, credential: str) -> tuple[int | None, dict | None, str | None]:
    url = _installation_url()
    url_error = _service_url_error(url)
    if url_error:
        return None, None, url_error
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {credential}",
            "x-moviestar-client": _CLIENT_MARKER,
        },
        method=method,
    )
    try:
        with build_opener(_NoRedirectHandler()).open(request, timeout=10) as response:
            return response.status, _decode_response(response.read()), None
    except HTTPError as error:
        return error.code, _decode_response(error.read()), None
    except (URLError, TimeoutError, OSError) as error:
        return None, None, str(error)


def _forget_connection(state: dict, command: str) -> bool:
    installation_id = state.pop("installation_id", None)
    connection = state.pop("connection", None)
    state.pop("installation_url", None)
    state.pop("claim_url", None)
    try:
        _write_state(state)
    except OSError as error:
        _state_write_error(error, command)
    if isinstance(connection, dict) and connection.get("credential_storage") == "os_keyring":
        try:
            import keyring

            keyring.delete_password(_CREDENTIAL_SERVICE, str(installation_id))
        except Exception:
            return False
    return True


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
            expires_at = pending.get("expires_at")
            if valid_pending and expires_at is not None:
                expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                valid_pending = expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
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
    claim_url = _claim_url() if valid_pending else _new_claim_url()
    installation_url = os.environ.get("MOVIESTAR_ACCOUNT_INSTALLATION_URL") or (
        _installation_url_for_claim(claim_url)
    )
    return payload, {
        "version": 1,
        "installation_id": installation_id,
        "installation_url": installation_url,
        "claim_url": claim_url,
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

    ``connect`` creates an email claim, ``status`` checks the grant, and
    ``disconnect`` revokes it. ``open`` opens the human's account page.
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
                        "Run 'moviestar account disconnect' before connecting another account."
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
            if not state.get("installation_url") and not os.environ.get("MOVIESTAR_ACCOUNT_INSTALLATION_URL"):
                _emit({
                    "command": "account status",
                    "status": "error",
                    "error": "The connected account service origin was not recorded.",
                    "hint": "Set MOVIESTAR_ACCOUNT_INSTALLATION_URL to the service that approved this installation, then retry.",
                })
                raise click.exceptions.Exit(1)
            credential = _credential_for_connection(state)
            if not credential:
                _emit({
                    "command": "account status",
                    "status": "error",
                    "error": "The local installation credential is unavailable.",
                    "hint": "Unlock the credential store and retry, or revoke it in your account and run 'moviestar account disconnect --local'.",
                })
                raise click.exceptions.Exit(1)
            response_status, remote, request_error = _installation_request("GET", credential)
            if request_error:
                _emit({
                    "command": "account status",
                    "status": "error",
                    "error": request_error,
                    "remote_status_checked": False,
                    "hint": "Retry 'moviestar account status' when the account service is available.",
                })
                raise click.exceptions.Exit(1)
            if response_status == 401 and isinstance(remote, dict) and remote.get("status") == "not_connected":
                _forget_connection(state, "account status")
                _emit({
                    "command": "account status",
                    "status": "not_connected",
                    "remote_status_checked": True,
                    "hint": "This installation was revoked. Run 'moviestar account connect EMAIL' to reconnect.",
                })
                return
            remote_installation = remote.get("installation") if isinstance(remote, dict) else None
            if (
                response_status != 200
                or not isinstance(remote_installation, dict)
                or remote_installation.get("id") != installation_id
                or remote.get("status") != "connected"
                or not isinstance(remote.get("handle"), str)
                or not _HANDLE_RE.fullmatch(remote["handle"])
                or not isinstance(remote_installation.get("name"), str)
            ):
                _emit({
                    "command": "account status",
                    "status": "error",
                    "error": (remote or {}).get("error") or "The account service returned an invalid installation status.",
                    "remote_status_checked": response_status is not None,
                    "hint": "Retry later; the local credential was kept.",
                })
                raise click.exceptions.Exit(1)
            if remote["handle"] != handle or remote_installation["name"] != installation_name:
                connection["handle"] = remote["handle"]
                connection["installation_name"] = remote_installation["name"]
                try:
                    _write_state(state)
                except OSError as error:
                    _state_write_error(error, "account status")
            payload = {
                "command": "account status",
                "status": "connected",
                "email": email,
                "handle": remote["handle"],
                "installation": {
                    "id": installation_id,
                    "name": remote_installation["name"],
                    "status": "approved",
                    "credential_stored": True,
                    "credential_storage": storage,
                },
                "remote_status_checked": True,
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
                    "installation_url": os.environ.get("MOVIESTAR_ACCOUNT_INSTALLATION_URL")
                    or state.get("installation_url")
                    or _installation_url_for_claim(_claim_url()),
                    "claim_url": state.get("claim_url") or _claim_url(),
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
@click.option("--local", "local_only", is_flag=True, help="Forget this installation locally without confirming remote revocation.")
def disconnect(local_only: bool) -> None:
    """Revoke this installation's account access.

    Normally this revokes the installation remotely before removing its local
    credential. If that credential is unreadable, use ``--local`` to forget
    it locally, then revoke the remaining grant from the human's account page.
    Projects and local editing remain available afterward.

    """
    state = _read_state()
    if not isinstance(state.get("connection"), dict):
        _emit({
            "command": "account disconnect", "status": "not_connected",
            "local_editing_available": True,
            "hint": "No connected installation needs revocation.",
        })
        return
    if local_only:
        removed = _forget_connection(state, "account disconnect")
        _emit({
            "command": "account disconnect", "status": "disconnected",
            "remote_grant_revoked": False,
            "local_credential_removed": removed,
            "local_editing_available": True,
            "warning": "The remote grant may still be active. Revoke this installation from your MovieStar account page.",
        })
        return
    if not state.get("installation_url") and not os.environ.get("MOVIESTAR_ACCOUNT_INSTALLATION_URL"):
        _emit({
            "command": "account disconnect", "status": "error",
            "error": "The connected account service origin was not recorded.",
            "hint": "Set MOVIESTAR_ACCOUNT_INSTALLATION_URL to the service that approved this installation, then retry.",
        })
        raise click.exceptions.Exit(1)
    credential = _credential_for_connection(state)
    if not credential:
        _emit({
            "command": "account disconnect", "status": "error",
            "error": "The local installation credential is unavailable.",
            "hint": "Unlock the credential store and retry, or revoke it in your account and run 'moviestar account disconnect --local'.",
        })
        raise click.exceptions.Exit(1)
    response_status, remote, request_error = _installation_request("DELETE", credential)
    revoked = response_status == 200 and isinstance(remote, dict) and remote.get("status") == "revoked"
    already_revoked = response_status == 401 and isinstance(remote, dict) and remote.get("status") == "not_connected"
    if request_error or not (revoked or already_revoked):
        _emit({
            "command": "account disconnect", "status": "error",
            "error": request_error or (remote or {}).get("error") or "The account service could not revoke this installation.",
            "hint": "Retry later; the local credential was kept.",
        })
        raise click.exceptions.Exit(1)
    removed = _forget_connection(state, "account disconnect")
    _emit({
        "command": "account disconnect", "status": "disconnected",
        "remote_grant_revoked": revoked,
        "local_credential_removed": removed,
        "local_editing_available": True,
        "hint": "This installation no longer has access to hosted services.",
        **({"warning": "The revoked credential could not be removed from the OS store."} if not removed else {}),
    })


@account.command("open")
def open_account() -> None:
    """Open the human's MovieStar account page.

    The account page shows the human's handle and lets them revoke connected
    installations. It is separate from local MovieStar projects.

    """
    url = _account_url()
    try:
        opened = bool(webbrowser.open(url))
    except Exception:
        opened = False
    _emit(
        {
            "command": "account open",
            "status": "opened" if opened else "browser_unavailable",
            "url": url,
            "opened_browser": opened,
            "hint": "Give this URL to the account owner if a browser did not open.",
        }
    )
