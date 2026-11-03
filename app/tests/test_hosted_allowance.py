"""Hosted plan reads and offline preparations never assume a paid allowance."""

from copy import deepcopy
import importlib
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli


INSTALLATION = "123e4567-e89b-42d3-a456-426614174000"
CREDENTIAL = "mvs_" + "a" * 43


def service_plan():
    """Independent synthetic response for the public hosted-plan v1 contract."""
    return {
        "schema_version": 1, "observed_at": "2030-04-01T12:00:00Z",
        "revision": 7, "state_updated_at": "2030-04-01T11:00:00Z", "plan": "free",
        "billing": {"status": "none", "cadence": None, "paid_through": None,
                    "renewal_at": None, "cancel_at_period_end": False, "scheduled_change": None},
        "limits": {"stored_videos": 3, "total_bytes": 1_000_000_000, "per_video_bytes": 500_000_000},
        "usage": {"stored_videos": 2, "stored_bytes": 120_000_000,
                  "pending_uploads": 0, "pending_bytes": 0, "used_videos": 2, "used_bytes": 120_000_000},
        "remaining": {"videos": 1, "bytes": 880_000_000}, "over_quota": [],
        "upload": {"allowed": True, "reason": "capacity_available", "max_file_bytes": 500_000_000},
        "lifecycle": {"phase": "free", "plus_ends_at": None, "grace_ends_at": None,
                      "links_pause_at": None, "files_delete_at": None},
    }


@pytest.fixture
def connected(monkeypatch, tmp_path):
    state = tmp_path / "account.json"
    state.write_text(json.dumps({
        "version": 1, "installation_id": INSTALLATION,
        "installation_url": "https://preview.example/api/v1/account/installation",
        "connection": {"email": "c***@example.com", "handle": "@capacity",
                       "installation_name": "Allowance checks", "credential_storage": "private_file",
                       "credential_fallback": CREDENTIAL},
    }))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)
    return state


def status_response(extra):
    return {"status": "connected", "handle": "@capacity",
            "installation": {"id": INSTALLATION, "name": "Allowance checks"}, **extra}


@pytest.mark.parametrize("payment_grace", [False, True])
def test_status_reports_live_allowance_and_dates_without_persisting_them(connected, monkeypatch, payment_grace):
    plan = service_plan()
    if payment_grace:
        plan["plan"] = "plus"
        plan["billing"].update(status="payment_grace", cadence="annual", paid_through="2030-03-31T12:00:00Z")
        plan["limits"].update(stored_videos=50, total_bytes=25_000_000_000, per_video_bytes=1_000_000_000)
        plan["remaining"].update(videos=48, bytes=24_880_000_000)
        plan["upload"].update(allowed=False, reason="payment_grace", max_file_bytes=0)
        plan["lifecycle"].update(phase="payment_grace", grace_ends_at="2030-04-05T12:00:00Z")
    read = lambda *_args: (200, status_response({"plan_status": "ok", "plan": plan}), None)
    monkeypatch.setattr("moviestar.account._installation_request", read)
    before = connected.read_text()
    result = CliRunner().invoke(cli, ["account", "status"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "connected"
    assert payload["plan_status"] == "ok"
    assert payload["plan"] == plan
    assert connected.read_text() == before
    assert CREDENTIAL not in result.output


@pytest.mark.parametrize("case", ["older_service", "unavailable", "bad_plan_status", "new_schema", "boolean_bytes", "naive_date", "bad_reason"])
def test_unverified_plan_does_not_break_a_valid_connection_or_invent_free(connected, monkeypatch, case):
    plan = service_plan()
    extra = {"plan_status": "ok", "plan": plan}
    if case == "older_service": extra = {}
    elif case == "unavailable": extra["plan_status"] = "unavailable"
    elif case == "bad_plan_status": extra["plan_status"] = {"secret": "do-not-print"}
    elif case == "new_schema": plan["schema_version"] = 2
    elif case == "boolean_bytes": plan["usage"]["used_bytes"] = True
    elif case == "naive_date": plan["observed_at"] = "2030-04-01T12:00:00"
    elif case == "bad_reason": plan["upload"]["reason"] = {"credential": "do-not-print"}
    monkeypatch.setattr("moviestar.account._installation_request", lambda *_args: (200, status_response(extra), None))
    before = connected.read_text()
    result = CliRunner().invoke(cli, ["account", "status"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "connected"
    assert payload["plan_status"] == "unavailable"
    assert payload["plan"] is None
    assert connected.read_text() == before
    assert "do-not-print" not in result.output


def test_plan_output_only_contains_allowlisted_service_fields(connected, monkeypatch):
    expected = service_plan()
    plan = deepcopy(expected)
    plan["provider_token"] = "secret-extra-field"
    plan["limits"]["object_key"] = "private/object"
    plan["billing"]["customer_id"] = "private-customer"
    monkeypatch.setattr("moviestar.account._installation_request", lambda *_args: (
        200, status_response({"plan_status": "ok", "plan": plan}), None))
    result = CliRunner().invoke(cli, ["account", "status"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["plan"] == expected
    assert "secret-extra-field" not in result.output
    assert "private/" not in result.output
    assert "private-customer" not in result.output


@pytest.mark.parametrize("command", ["upload", "share"])
def test_700_mb_offline_preflight_leaves_actual_allowance_unverified(connected, monkeypatch, tmp_path, command):
    file = tmp_path / "large.mp4"
    with file.open("wb") as stream:
        stream.write(b"\x00\x00\x00\x18ftypisom")
        stream.truncate(700_000_000)
    def unexpected(*_args, **_kwargs):
        pytest.fail("offline preparation attempted a service call or transfer")
    monkeypatch.setattr("moviestar.upload._json_request", unexpected)
    monkeypatch.setattr("moviestar.upload._put_file", unexpected)
    monkeypatch.setattr("moviestar.sharing._json_request", unexpected)
    monkeypatch.setattr("moviestar.account._installation_request", unexpected)
    result = CliRunner().invoke(cli, [command, str(file), "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["file"]["size_bytes"] == 700_000_000
    assert payload["quota"]["plan"] == "unknown"
    assert payload["quota"]["limits_verified"] is False
    assert payload["quota"]["current_usage"] == "unavailable"
    assert payload["upload_performed"] is False
    assert "account status" in payload["hint"]


@pytest.mark.parametrize("command", ["upload", "share"])
@pytest.mark.parametrize("size", [1_000_000_000, 1_000_000_001])
def test_cli_file_safety_ceiling_is_independent_of_account_quota(connected, tmp_path, command, size):
    file = tmp_path / "boundary.mp4"
    with file.open("wb") as stream:
        stream.truncate(size)
    result = CliRunner().invoke(cli, [command, str(file), "--dry-run"])
    assert result.exit_code == (0 if size == 1_000_000_000 else 1), result.output
    if size > 1_000_000_000:
        assert "1 GB" in json.loads(result.stdout)["hint"]


@pytest.mark.parametrize("command", ["upload", "share"])
@pytest.mark.parametrize("error", ["file_too_large", "video_limit", "storage_limit", "existing_video_over_limit", "payment_grace", "account_deleting"])
def test_large_upload_reaches_service_and_returns_specific_quota_recovery(connected, monkeypatch, tmp_path, command, error):
    file = tmp_path / "large.mp4"
    with file.open("wb") as stream:
        stream.truncate(700_000_000)
    calls = []
    def request(url, credential, payload=None):
        calls.append((url, payload))
        return 409, {"status": error, "quota": {"used_videos": 3, "used_bytes": 900_000_000, "credential": "secret"}}
    monkeypatch.setattr("moviestar.upload._json_request", request)
    monkeypatch.setattr("moviestar.upload._put_file", lambda *_args: pytest.fail("a refused upload transferred bytes"))
    result = CliRunner().invoke(cli, [command, str(file), "--idempotency-key", "allowance-check"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == error
    assert payload["quota"] == {"used_videos": 3, "used_bytes": 900_000_000}
    assert payload["idempotency_key"] == "allowance-check"
    assert len(calls) == 1
    assert calls[0][1]["size_bytes"] == 700_000_000
    assert "secret" not in result.output
    assert "account status" in payload["hint"] or "account open" in payload["hint"]
    if command == "share":
        assert payload["publication_outcome"] == "not_attempted"
        assert payload["link_created"] is False


def test_existing_consent_recovery_is_preserved(connected, tmp_path, monkeypatch):
    uploads = importlib.import_module("moviestar.upload")
    file = tmp_path / "small.mp4"
    file.write_bytes(b"movie")
    monkeypatch.setattr(uploads, "_json_request", lambda *_args: (409, {"status": "consent_required"}))
    result = CliRunner().invoke(cli, ["upload", str(file)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "consent_required"
    assert "account terms" in result.stdout


def test_completion_refusal_keeps_the_reservation_for_recovery(connected, tmp_path, monkeypatch):
    file = tmp_path / "small.mp4"
    file.write_bytes(b"movie")
    monkeypatch.setattr("moviestar.upload._json_request", lambda *_args: (409, {"status": "payment_grace"}))
    result = CliRunner().invoke(cli, ["upload", str(file), "--resume", INSTALLATION])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "payment_grace"
    assert payload["reservation_id"] == INSTALLATION
    assert "account open" in payload["hint"]
