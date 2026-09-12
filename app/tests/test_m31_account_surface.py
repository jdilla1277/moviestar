"""M31a account-claim surface contracts."""

import hashlib
import json
import stat
import uuid

from click.testing import CliRunner

from moviestar.cli import cli


def _invoke(*args: str):
    return CliRunner().invoke(cli, ["account", *args])


def test_account_surface_is_discoverable_from_root_and_group_help():
    root = CliRunner().invoke(cli, ["--help"])
    group = _invoke("--help")

    assert root.exit_code == 0
    assert group.exit_code == 0
    assert "account connect" in root.output
    assert "Connect this installation to its human" in root.output
    for command in ("connect", "status", "disconnect", "open"):
        assert command in group.output


def test_account_help_teaches_human_ownership_and_local_first_boundary():
    result = _invoke("--help")

    assert result.exit_code == 0
    compact = " ".join(result.output.split()).lower()
    assert "agent starts" in compact
    assert "human approves" in compact
    assert "local editing never requires an account" in compact
    assert "mocked surface" not in compact


def test_connect_help_teaches_generic_private_asynchronous_flow():
    result = _invoke("connect", "--help")

    assert result.exit_code == 0
    compact = " ".join(result.output.split()).lower()
    assert "returns immediately" in compact
    assert "same response" in compact
    assert "new or already has an account" in compact
    assert "never receives the email link" in compact
    assert "account status" in compact


def test_connect_creates_a_private_claim_and_only_prints_the_public_receipt(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "account.json"
    captured = []
    monkeypatch.setattr("moviestar.account._account_state_path", lambda: state_path)
    monkeypatch.setattr(
        "moviestar.account._installation_name", lambda: "James's MacBook"
    )
    monkeypatch.setattr(
        "moviestar.account._send_claim",
        lambda payload: captured.append(payload)
        or (
            None,
            {
                "status": "approval_pending",
                "request_id": payload["request_id"],
                "email": "h***@example.com",
                "expires_at": "2026-09-06T12:00:00.000Z",
                "hint": (
                    "Ask the recipient to open the MovieStar email, then run "
                    "'moviestar account status'."
                ),
            },
        ),
    )

    result = CliRunner().invoke(
        cli,
        ["account", "connect", "  Human@Example.COM  "],
    )

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data == {
        "command": "account connect",
        "status": "approval_pending",
        "email": "h***@example.com",
        "request_id": captured[0]["request_id"],
        "expires_at": "2026-09-06T12:00:00.000Z",
        "hint": (
            "Ask the recipient to open the MovieStar email, then run "
            "'moviestar account status'."
        ),
    }
    assert captured[0]["email"] == "human@example.com"
    assert captured[0]["installation_name"] == "James's MacBook"
    uuid.UUID(captured[0]["installation_id"])
    uuid.UUID(captured[0]["request_id"])

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["installation_id"] == captured[0]["installation_id"]
    assert state["pending_claim"]["request_id"] == captured[0]["request_id"]
    assert state["pending_claim"]["email"] == "human@example.com"
    assert state["pending_claim"]["expires_at"] == data["expires_at"]
    claim_secret = state["pending_claim"]["claim_secret"]
    assert hashlib.sha256(claim_secret.encode()).hexdigest() == captured[0][
        "claim_challenge"
    ]
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    serialized = json.dumps(data).lower()
    for private_field in ("claim_url", "invitation_url", "password", "token", "secret"):
        assert private_field not in serialized


def test_connect_failure_is_structured_and_keeps_retryable_private_state(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "account.json"
    sent = []
    monkeypatch.setattr("moviestar.account._account_state_path", lambda: state_path)
    monkeypatch.setattr("moviestar.account._installation_name", lambda: "Test Mac")
    monkeypatch.setattr(
        "moviestar.account._send_claim",
        lambda payload: sent.append(payload) or ("Service unavailable.", None),
    )

    first = _invoke("connect", "human@example.com")
    second = _invoke("connect", "human@example.com")

    assert first.exit_code == 1
    assert second.exit_code == 1
    assert json.loads(first.stdout) == {
        "command": "account connect",
        "status": "error",
        "error": "Service unavailable.",
        "hint": "Retry 'moviestar account connect human@example.com' later.",
    }
    assert sent[0] == sent[1]
    assert state_path.exists()
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_connect_reports_private_state_write_failures_before_network(monkeypatch):
    monkeypatch.setattr(
        "moviestar.account._write_state",
        lambda _state: (_ for _ in ()).throw(PermissionError("read-only config")),
    )
    monkeypatch.setattr(
        "moviestar.account._send_claim",
        lambda _payload: (_ for _ in ()).throw(
            AssertionError("must not send without retry state")
        ),
    )

    result = _invoke("connect", "human@example.com")

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["command"] == "account connect"
    assert data["status"] == "error"
    assert "private account state" in data["error"].lower()
    assert "MOVIESTAR_ACCOUNT_STATE" in data["hint"]


def test_connect_replaces_incomplete_pending_state(monkeypatch, tmp_path):
    state_path = tmp_path / "account.json"
    state_path.write_text(
        json.dumps(
            {
                "installation_id": "not-a-uuid",
                "pending_claim": {"email": "human@example.com"},
            }
        ),
        encoding="utf-8",
    )
    captured = []
    monkeypatch.setattr("moviestar.account._account_state_path", lambda: state_path)
    monkeypatch.setattr("moviestar.account._installation_name", lambda: "Test Mac")
    monkeypatch.setattr(
        "moviestar.account._send_claim",
        lambda payload: captured.append(payload)
        or (
            None,
            {
                "status": "approval_pending",
                "request_id": payload["request_id"],
                "expires_at": "2026-09-06T12:00:00.000Z",
            },
        ),
    )

    result = _invoke("connect", "human@example.com")

    assert result.exit_code == 0, result.stdout
    uuid.UUID(captured[0]["installation_id"])
    uuid.UUID(captured[0]["request_id"])
    assert len(captured[0]["claim_challenge"]) == 64


def test_connect_does_not_replace_an_existing_connection(monkeypatch, tmp_path):
    state_path = tmp_path / "account.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "installation_id": "123e4567-e89b-42d3-a456-426614174000",
                "connection": {
                    "email": "h***@example.com",
                    "handle": "@m24",
                    "installation_name": "Test Mac",
                    "device_grant_id": "123e4567-e89b-42d3-a456-426614174004",
                    "credential_storage": "os_keyring",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("moviestar.account._account_state_path", lambda: state_path)
    monkeypatch.setattr(
        "moviestar.account._send_claim",
        lambda _payload: (_ for _ in ()).throw(
            AssertionError("must not replace an active connection")
        ),
    )

    result = _invoke("connect", "another@example.com")

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "command": "account connect",
        "status": "already_connected",
        "handle": "@m24",
        "hint": (
            "Run 'moviestar account status' to inspect this connection. "
            "Account switching will land with remote disconnect."
        ),
    }


def test_connect_rejects_invalid_email_without_claiming_side_effects():
    result = _invoke("connect", "not-an-email")

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["command"] == "account connect"
    assert data["status"] == "error"
    assert "email" in data["error"].lower()
    assert "complete email address" in data["hint"].lower()


def test_connect_missing_email_uses_nested_structured_usage_error():
    result = _invoke("connect")

    assert result.exit_code == 2
    data = json.loads(result.stdout)
    assert data["command"] == "account connect"
    assert "missing argument" in data["error"].lower()
    assert "moviestar account connect --help" in data["hint"]


def test_status_checks_the_pending_claim_remotely_without_exposing_its_secret(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "account.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "installation_id": "123e4567-e89b-42d3-a456-426614174000",
                "pending_claim": {
                    "request_id": "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
                    "email": "human@example.com",
                    "claim_secret": "private-secret-must-not-be-printed",
                    "expires_at": "2026-09-06T12:00:00.000Z",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("moviestar.account._account_state_path", lambda: state_path)
    exchanged = []
    monkeypatch.setattr(
        "moviestar.account._exchange_claim",
        lambda request_id, installation_id, claim_secret: exchanged.append(
            (request_id, installation_id, claim_secret)
        )
        or (
            None,
            {
                "status": "approval_pending",
                "request_id": request_id,
                "expires_at": "2026-09-06T12:00:00.000Z",
            },
        ),
    )

    result = _invoke("status")

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data == {
        "command": "account status",
        "status": "approval_pending",
        "request_id": "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
        "email": "h***@example.com",
        "expires_at": "2026-09-06T12:00:00.000Z",
        "remote_status_checked": True,
        "hint": (
            "Ask the recipient to open the MovieStar email, then retry "
            "'moviestar account status'."
        ),
    }
    assert exchanged == [
        (
            "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
            "123e4567-e89b-42d3-a456-426614174000",
            "private-secret-must-not-be-printed",
        )
    ]
    assert "private-secret" not in result.stdout


def test_status_exchanges_approval_for_a_stored_device_credential(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "account.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "installation_id": "123e4567-e89b-42d3-a456-426614174000",
                "pending_claim": {
                    "request_id": "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
                    "email": "human@example.com",
                    "claim_secret": "private-secret-must-not-be-printed",
                    "expires_at": "2026-09-12T12:00:00.000Z",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("moviestar.account._account_state_path", lambda: state_path)
    monkeypatch.setattr(
        "moviestar.account._exchange_claim",
        lambda *_args: (
            None,
            {
                "status": "connected",
                "request_id": "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
                "email": "h***@example.com",
                "handle": "@m24",
                "installation": {
                    "id": "123e4567-e89b-42d3-a456-426614174000",
                    "name": "James's MacBook",
                    "status": "approved",
                },
                "credential": "mvs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "device_grant_id": "123e4567-e89b-42d3-a456-426614174004",
            },
        ),
    )
    stored = []
    monkeypatch.setattr(
        "moviestar.account._store_device_credential",
        lambda installation_id, credential: stored.append(
            (installation_id, credential)
        )
        or ("os_keyring", None),
    )

    result = _invoke("status")

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == {
        "command": "account status",
        "status": "connected",
        "email": "h***@example.com",
        "handle": "@m24",
        "installation": {
            "id": "123e4567-e89b-42d3-a456-426614174000",
            "name": "James's MacBook",
            "status": "approved",
            "credential_stored": True,
            "credential_storage": "os_keyring",
        },
        "remote_status_checked": True,
        "hint": "Run 'moviestar account open' to manage this connection.",
    }
    assert stored == [
        (
            "123e4567-e89b-42d3-a456-426614174000",
            "mvs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
    ]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert "pending_claim" not in saved
    assert saved["connection"]["handle"] == "@m24"
    assert "credential_fallback" not in saved["connection"]
    assert "mvs_aaaaaaaa" not in json.dumps(saved)
    assert "mvs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in result.stdout


def test_status_uses_private_file_fallback_when_os_store_is_unavailable(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "account.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "installation_id": "123e4567-e89b-42d3-a456-426614174000",
                "pending_claim": {
                    "request_id": "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
                    "email": "human@example.com",
                    "claim_secret": "private-secret-must-not-be-printed",
                    "expires_at": "2026-09-12T12:00:00.000Z",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("moviestar.account._account_state_path", lambda: state_path)
    monkeypatch.setattr(
        "moviestar.account._exchange_claim",
        lambda *_args: (
            None,
            {
                "status": "connected",
                "request_id": "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
                "email": "h***@example.com",
                "handle": "@m24",
                "installation": {
                    "id": "123e4567-e89b-42d3-a456-426614174000",
                    "name": "Test Linux",
                    "status": "approved",
                },
                "credential": "mvs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "device_grant_id": "123e4567-e89b-42d3-a456-426614174004",
            },
        ),
    )
    monkeypatch.setattr(
        "moviestar.account._store_device_credential",
        lambda *_args: (
            "private_file",
            "mvs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
    )

    result = _invoke("status")

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["installation"]["credential_storage"] == "private_file"
    assert "warning" in data
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["connection"]["credential_fallback"] == (
        "mvs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert "mvs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in result.stdout


def test_status_keeps_retryable_state_when_remote_check_fails(monkeypatch, tmp_path):
    state_path = tmp_path / "account.json"
    pending = {
        "version": 1,
        "installation_id": "123e4567-e89b-42d3-a456-426614174000",
        "pending_claim": {
            "request_id": "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
            "email": "human@example.com",
            "claim_secret": "private-secret-must-not-be-printed",
            "expires_at": "2026-09-12T12:00:00.000Z",
        },
    }
    state_path.write_text(json.dumps(pending), encoding="utf-8")
    monkeypatch.setattr("moviestar.account._account_state_path", lambda: state_path)
    monkeypatch.setattr(
        "moviestar.account._exchange_claim",
        lambda *_args: ("Service unavailable.", None),
    )

    result = _invoke("status")

    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "error"
    assert json.loads(state_path.read_text(encoding="utf-8")) == pending


def test_status_without_local_account_state_is_honestly_disconnected(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "moviestar.account._account_state_path", lambda: tmp_path / "missing.json"
    )

    result = _invoke("status")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "command": "account status",
        "status": "not_connected",
        "remote_status_checked": False,
        "hint": "Run 'moviestar account connect EMAIL' to email the human an account claim.",
    }


def test_status_help_explains_handle_and_credential_boundary():
    result = _invoke("status", "--help")

    assert result.exit_code == 0
    compact = " ".join(result.output.split()).lower()
    for state in ("approval_pending", "connected", "denied", "expired"):
        assert state in compact
    assert "@handle" in compact
    assert "os credential store" in compact
    assert "project" in compact


def test_disconnect_previews_local_and_remote_revocation_without_doing_it():
    result = _invoke("disconnect")

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["command"] == "account disconnect"
    assert data["status"] == "would_disconnect"
    assert data["installation_id"] == "ins_mock_current"
    assert data["revokes_remote_grant"] is False
    assert data["removes_local_credential"] is False
    assert data["local_editing_available"] is True
    assert "nothing was revoked" in data["mocked_surface_note"]
    assert "local editing" in data["hint"].lower()


def test_open_previews_account_page_without_launching_a_browser():
    result = _invoke("open")

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["command"] == "account open"
    assert data["status"] == "would_open_account"
    assert data["url"] == "https://trymoviestar.com/account"
    assert data["opened_browser"] is False
    assert "no browser was opened" in data["mocked_surface_note"]
    assert data["hint"] == (
        "The real command opens the account page for the connected human."
    )
