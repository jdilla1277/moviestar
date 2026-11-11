"""The API-key journey hands humans to the website without issuing credentials."""
import json
from click.testing import CliRunner
from moviestar.cli import cli


def test_keys_opens_website_without_an_account_connection(monkeypatch, tmp_path):
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_URL", "https://trymoviestar.com/account")
    opened = []
    monkeypatch.setattr("moviestar.account.webbrowser.open", lambda url: opened.append(url) or True)
    result = CliRunner().invoke(cli, ["account", "keys"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["url"] == "https://trymoviestar.com/account/api-keys"
    assert body["opened_browser"] is True
    assert opened == [body["url"]]
    assert not (tmp_path / "missing.json").exists()
    assert "key" not in body and "token" not in body


def test_keys_can_return_a_link_without_opening_a_browser(monkeypatch):
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_URL", "https://preview.example.com/account?ignore=yes#section")
    monkeypatch.setattr("moviestar.account.webbrowser.open", lambda _: (_ for _ in ()).throw(AssertionError("no browser expected")))
    result = CliRunner().invoke(cli, ["account", "keys", "--no-browser"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["url"] == "https://preview.example.com/account/api-keys"
    assert body["status"] == "website_required"
    assert body["opened_browser"] is False


def test_keys_handles_browser_failure_with_a_usable_link(monkeypatch):
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_URL", "https://trymoviestar.com/account")
    monkeypatch.setattr("moviestar.account.webbrowser.open", lambda _: (_ for _ in ()).throw(OSError("headless")))
    result = CliRunner().invoke(cli, ["account", "keys"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["status"] == "browser_unavailable"
    assert body["url"].endswith("/account/api-keys")
    assert "website" in body["hint"].lower()
