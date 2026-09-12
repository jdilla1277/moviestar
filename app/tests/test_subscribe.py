"""Tests for the agent-fillable ``moviestar subscribe`` command."""

import json

from click.testing import CliRunner

from moviestar.cli import cli


def test_subscribe_command_is_registered():
    result = CliRunner().invoke(cli, ["subscribe", "--help"])

    assert result.exit_code == 0
    assert "confirmation email" in result.output


def test_subscribe_rejects_invalid_email_without_calling_remote(monkeypatch):
    from moviestar import subscribe as subscribe_module

    monkeypatch.setattr(
        subscribe_module,
        "_send_remote",
        lambda payload: (_ for _ in ()).throw(AssertionError("remote called")),
    )

    result = CliRunner().invoke(cli, ["subscribe", "not-an-email"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["command"] == "subscribe"
    assert payload["status"] == "error"
    assert "email" in payload["error"].lower()


def test_subscribe_normalizes_email_and_includes_agent_context(monkeypatch):
    from moviestar import subscribe as subscribe_module

    captured = {}

    def fake_send(payload):
        captured.update(payload)
        return None, {"status": "pending_confirmation"}

    monkeypatch.setattr(subscribe_module, "_send_remote", fake_send)

    result = CliRunner().invoke(
        cli, ["subscribe", "  Human@Example.COM  "]
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["command"] == "subscribe"
    assert payload["status"] == "success"
    assert payload["email"] == "human@example.com"
    assert payload["remote"]["status"] == "pending_confirmation"
    assert "confirmation email" in payload["hint"]
    assert captured["email"] == "human@example.com"
    assert captured["source"] == "cli"
    assert set(captured["agent_context"]) == {
        "moviestar_version",
        "platform",
        "python_version",
    }


def test_subscribe_reports_remote_failure(monkeypatch):
    from moviestar import subscribe as subscribe_module

    monkeypatch.setattr(
        subscribe_module,
        "_send_remote",
        lambda payload: ("connection refused", None),
    )

    result = CliRunner().invoke(cli, ["subscribe", "human@example.com"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["command"] == "subscribe"
    assert payload["status"] == "error"
    assert payload["error"] == "connection refused"
    assert payload["hint"] == "Try the subscription again later."
