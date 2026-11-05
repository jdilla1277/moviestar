"""Recipient discovery and authenticated download over real loopback HTTP."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from click.testing import CliRunner

from moviestar.cli import cli

VIDEO = "123e4567-e89b-42d3-a456-426614174001"
CURSOR = "123e4567-e89b-42d3-a456-426614174002"
TOKEN = "mvs_" + "r" * 43
MEDIA = b"\x00\x00\x00\x18ftypisom" + b"synthetic-mp4" * 16


def item():
    return {"id": VIDEO, "filename": "received.mp4", "shared_by": "sender", "accepted_at": "2026-11-04T12:00:00.000Z",
            "size_bytes": len(MEDIA), "duration_ms": 1000, "width": 320, "height": 180,
            "permissions": ["watch", "download"], "watch_path": f"/account/shared-videos/{VIDEO}",
            "download_path": f"/api/v1/shared-videos/{VIDEO}/download"}


@pytest.fixture
def server(tmp_path, monkeypatch):
    state = {"requests": [], "videos": [item()], "cursor": CURSOR, "contract": "recipient-videos-v1",
             "list_status": 200, "status": 200, "head_status": 200, "length": len(MEDIA), "body": MEDIA,
             "mime": "video/mp4", "redirect": "https://external.example/secret?token=private", "chunked": True}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args): pass
        def reply(self, head=False):
            state["requests"].append((self.command, self.path, dict(self.headers)))
            if self.path.split("?")[0] == "/api/v1/shared-videos":
                body = state.get("list_body") or json.dumps({"status": "ok" if state["list_status"] == 200 else "unavailable", "contract_version": state["contract"],
                                   "videos": state["videos"], "next_cursor": state["cursor"], "secret": TOKEN}).encode()
                self.send_response(state["list_status"]); self.send_header("Content-Type", "application/json")
                if state["list_status"] in (301,302,303,307,308): self.send_header("Location", state["redirect"])
                self.send_header("Content-Length", str(len(body))); self.end_headers()
                if not head: self.wfile.write(body)
                return
            status = state["head_status"] if head else state["status"]
            self.send_response(status); self.send_header("Content-Type", state["mime"] if head else state.get("get_mime", state["mime"]))
            if state.get("encoding"): self.send_header("Content-Encoding", state["encoding"])
            if status in (301,302,303,307,308): self.send_header("Location", state["redirect"])
            if head or not state["chunked"]:
                if not (head and state.get("head_missing_length")):
                    self.send_header("Content-Length", str(state["length"] if head else state.get("get_length", state["length"])))
                self.send_header("Connection", "close")
            else: self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if not head:
                body = state["body"]
                if state["chunked"]: self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
                else: self.wfile.write(body); self.close_connection = True
        def do_GET(self): self.reply()
        def do_HEAD(self): self.reply(True)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{httpd.server_port}"
    account = tmp_path / "account.json"
    account.write_text(json.dumps({"installation_url": origin + "/api/v1/account/installation",
                                   "connection": {"credential_storage": "private_file", "credential_fallback": TOKEN}}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(account))
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)
    worker = Thread(target=httpd.serve_forever, daemon=True); worker.start()
    yield origin, state, account
    httpd.shutdown(); httpd.server_close(); worker.join()


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.stdout, repr(result.exception)
    return result, json.loads(result.stdout)


def test_discovers_one_page_with_verified_contract_and_safe_projection(server):
    origin, state, account = server
    state["videos"][0]["recipient_email"] = "private@example.test"
    before = account.read_bytes()
    result, body = invoke("shared", "videos", "--cursor", VIDEO)
    assert result.exit_code == 0, result.output
    assert body["videos"] == [item()] and body["next_cursor"] == CURSOR
    assert body["contract_version"] == "recipient-videos-v1"
    assert "shared download" in body["hint"] and "--cursor" in body["hint"]
    assert TOKEN not in result.output and "private@example" not in result.output
    assert state["requests"][0][:2] == ("GET", f"/api/v1/shared-videos?cursor={VIDEO}")
    assert state["requests"][0][2]["Authorization"] == f"Bearer {TOKEN}"
    assert state["requests"][0][2]["X-Moviestar-Client"] == "moviestar-cli-v1"
    assert account.read_bytes() == before


def test_download_handles_chunked_private_bytes_without_redirect(server, tmp_path):
    _, state, _ = server; out = tmp_path / "received" / "video.mp4"
    result, body = invoke("shared", "download", VIDEO.upper(), "--out", str(out))
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == MEDIA
    assert body["video_id"] == VIDEO and body["out"] == str(out)
    assert body["status"] == "downloaded" and body["download_performed"] is True
    assert body["account_required"] is True and body["size_bytes"] == len(MEDIA)
    assert [(method,path) for method,path,_ in state["requests"]] == [("HEAD", f"/api/v1/shared-videos/{VIDEO}/download"), ("GET", f"/api/v1/shared-videos/{VIDEO}/download")]
    assert all(headers["Authorization"] == f"Bearer {TOKEN}" and "Cookie" not in headers for _,_,headers in state["requests"])
    assert TOKEN not in result.output and "secret?token" not in result.output


def test_dry_run_is_offline_and_does_not_read_credentials_or_create_directories(server, tmp_path, monkeypatch):
    from moviestar import incoming
    _, state, _ = server; out = tmp_path / "new" / "received.mp4"
    monkeypatch.setattr(incoming, "_credential_for_connection", lambda *args: pytest.fail("credential lookup"))
    result, body = invoke("shared", "download", VIDEO, "--out", str(out), "--dry-run")
    assert result.exit_code == 0, result.output
    assert body["status"] == "would_download" and body["download_performed"] is False
    assert body["dry_run"] is True and body["would_write_to"] == str(out) and "out" not in body
    assert body["access_verified"] is False and not out.parent.exists() and not state["requests"]


@pytest.mark.parametrize("bad", ["external_path", "browser_download", "bad_watch", "permissions", "version", "oversized", "bad_cursor", "duplicate", "bad_number"])
def test_rejects_malformed_or_untrusted_discovery_without_issuing_download(server, bad):
    _, state, _ = server
    if bad == "external_path": state["videos"][0]["download_path"] = "https://external.example/private"
    elif bad == "browser_download": state["videos"][0]["download_path"] = f"/api/v1/account/shared-videos/{VIDEO}/download"
    elif bad == "bad_watch": state["videos"][0]["watch_path"] = "//external.example/account"
    elif bad == "permissions": state["videos"][0]["permissions"] = ["watch", "download", "edit"]
    elif bad == "version": state["contract"] = "recipient-videos-v999"
    elif bad == "oversized": state["videos"] *= 51
    elif bad == "bad_cursor": state["cursor"] = "https://external.example/next"
    elif bad == "duplicate": state["videos"] *= 2
    elif bad == "bad_number": state["videos"][0]["size_bytes"] = True
    result, body = invoke("shared", "videos")
    assert result.exit_code == 1
    assert body["status"] in {"invalid_response", "unsupported_contract"}
    assert "videos" not in body and TOKEN not in result.output and len(state["requests"]) == 1


@pytest.mark.parametrize("failure", ["head_redirect", "get_redirect", "head_denied", "get_denied", "truncated", "oversized", "wrong_type", "not_mp4", "too_large", "changed_size"])
def test_failed_private_transfer_never_publishes_partial_output(server, tmp_path, failure):
    _, state, _ = server; out = tmp_path / "cut.mp4"
    if failure == "head_redirect": state["head_status"] = 307
    elif failure == "get_redirect": state["status"] = 307
    elif failure == "head_denied": state["head_status"] = 404
    elif failure == "get_denied": state["status"] = 404
    elif failure == "truncated": state["body"] = MEDIA[:-1]
    elif failure == "oversized": state["body"] = MEDIA + b"extra"
    elif failure == "wrong_type": state["mime"] = "text/html"
    elif failure == "not_mp4": state["body"] = b"x" * len(MEDIA)
    elif failure == "too_large": state["length"] = 1_000_000_001
    elif failure == "changed_size": state["chunked"] = False; state["length"] = len(MEDIA) + 1
    result, body = invoke("shared", "download", VIDEO, "--out", str(out))
    assert result.exit_code == 1 and body["download_performed"] is False, result.output
    assert not out.exists() and not list(tmp_path.glob(".moviestar-download-*"))
    assert all(path == f"/api/v1/shared-videos/{VIDEO}/download" for _,path,_ in state["requests"])
    assert "external.example" not in result.output and TOKEN not in result.output


def test_preserves_existing_destination_and_racing_writer(server, tmp_path, monkeypatch):
    from moviestar import download as transfer
    _, state, _ = server; out = tmp_path / "cut.mp4"; out.symlink_to(tmp_path / "missing.mp4")
    result, body = invoke("shared", "download", VIDEO, "--out", str(out))
    assert result.exit_code == 1 and out.is_symlink() and not state["requests"]
    out.unlink()
    original = transfer.os.link
    def race(source, destination):
        destination.write_bytes(b"other edit")
        return original(source, destination)
    monkeypatch.setattr(transfer.os, "link", race)
    result, body = invoke("shared", "download", VIDEO, "--out", str(out))
    assert result.exit_code == 1 and out.read_bytes() == b"other edit"
    assert not list(tmp_path.glob(".moviestar-download-*"))


def test_refuses_configured_origin_change_before_reading_keyring(server, monkeypatch):
    from moviestar import incoming
    _, state, _ = server
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", "https://external.example/api/v1/account/installation")
    monkeypatch.setattr(incoming, "_credential_for_connection", lambda *args: pytest.fail("credential lookup"))
    result, body = invoke("shared", "videos")
    assert result.exit_code == 1 and body["status"] == "invalid_configuration" and not state["requests"]


def test_empty_page_and_revoked_installation_have_distinct_results(server):
    _, state, _ = server; state["videos"] = []; state["cursor"] = None
    result, body = invoke("shared", "videos")
    assert result.exit_code == 0 and body["videos"] == []
    state["list_status"] = 401
    result, body = invoke("shared", "videos")
    assert result.exit_code == 1 and body["status"] == "not_connected" and "account status" in body["hint"]


def test_help_explains_acceptance_recipient_auth_and_anonymous_download_compatibility():
    for arguments, words in [(["--help"], ["shared"]), (["shared", "--help"], ["accepted", "account"]),
                             (["shared", "videos", "--help"], ["--cursor", "50", "browser"]),
                             (["shared", "download", "--help"], ["--out", "--dry-run", "redirect", "overwritten"]),
                             (["download", "--help"], ["unlisted", "no recipient account"])]:
        result = CliRunner().invoke(cli, arguments)
        assert result.exit_code == 0
        for word in words: assert word in result.output.lower()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 409, 404, 429, 503])
def test_list_refusals_are_redacted_and_never_followed(server, status):
    _, state, _ = server; state["list_status"] = status
    result, body = invoke("shared", "videos")
    assert result.exit_code == 1 and len(state["requests"]) == 1
    assert "videos" not in body and TOKEN not in result.output and "external.example" not in result.output
    if status == 409: assert body["status"] == "account_action_required" and "account terms" in body["hint"]
    if status == 429: assert body["status"] == "rate_limited"


@pytest.mark.parametrize("payload", [b"not-json", b"x" * 1_048_577, b"[" * 2000 + b"0" + b"]" * 2000])
def test_bounds_and_redacts_invalid_json(server, payload):
    _, state, _ = server; state["list_body"] = payload
    result, body = invoke("shared", "videos")
    assert result.exit_code == 1 and body["status"] in {"shared_videos_unavailable", "invalid_response"}
    assert "videos" not in body and TOKEN not in result.output


@pytest.mark.parametrize("field,value", [("head_missing_length", True), ("get_mime", "text/html"), ("encoding", "gzip"), ("get_length", len(MEDIA) + 1)])
def test_requires_compatible_metadata_before_private_transfer(server, tmp_path, field, value):
    _, state, _ = server; state[field] = value; state["chunked"] = False
    out = tmp_path / "received.mp4"
    result, body = invoke("shared", "download", VIDEO, "--out", str(out))
    assert result.exit_code == 1 and body["download_performed"] is False
    assert not out.exists() and not list(tmp_path.glob(".moviestar-download-*"))


@pytest.mark.parametrize("url", ["http://remote.example/api/v1/account/installation", "https://user:password@example.test/api/v1/account/installation", "https://example.test/api/v1/account/installation?secret=x", "https://example.test:99999/api/v1/account/installation", "https://example.test/\\/api/v1/account/installation", "https://example.test/api/v1/account/installation\n"])
def test_bad_service_configuration_never_reads_credentials(server, monkeypatch, url):
    from moviestar import incoming
    _, state, _ = server
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", url)
    monkeypatch.setattr(incoming, "_credential_for_connection", lambda *args: pytest.fail("credential lookup"))
    result, body = invoke("shared", "videos")
    assert result.exit_code == 1 and body["status"] == "invalid_configuration" and not state["requests"]


def test_invalid_arguments_fail_before_credentials_and_download_default_is_uuid(server, tmp_path, monkeypatch):
    from moviestar import incoming
    _, state, _ = server
    monkeypatch.setattr(incoming, "_credential_for_connection", lambda *args: pytest.fail("credential lookup"))
    monkeypatch.chdir(tmp_path)
    for args in [("videos", "--cursor", "https://wrong.test"), ("download", "../../../escape")]:
        result, body = invoke("shared", *args)
        assert result.exit_code == 1 and body["status"] in {"invalid_cursor", "invalid_request"}
    result, body = invoke("shared", "download", VIDEO, "--dry-run")
    assert result.exit_code == 0 and body["would_write_to"] == str(tmp_path / (VIDEO + ".mp4")) and "out" not in body and not state["requests"]
