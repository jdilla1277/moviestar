"""Exercise the public replacement protocol through real HTTP and the CLI."""
import json
import socket
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner

from moviestar.cli import cli


@pytest.mark.parametrize("lost_reply", ["upload", "completion", "redirect"])
def test_conditional_transfer_and_uncertain_reply_recovery(tmp_path, monkeypatch, test_video, lost_reply):
    video, job, link = [f"c8000000-0000-4000-8000-{n:012d}" for n in (1, 2, 3)]
    token = "mvs_" + "t" * 43
    file = tmp_path / "cut.mp4"
    file.write_bytes(Path(test_video).read_bytes())
    original = file.read_bytes()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    state = {"object": None, "published": False, "dropped": False, "status_down": False,
             "puts": 0, "reservations": 0, "completions": 0, "trap": 0, "errors": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def drop(self):
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def status(self):
            return {"status": "replaced" if state["published"] else "reserved", "video_id": video,
                "replacement_id": job, "filename": file.name, "size_bytes": len(original), "base_version": 1,
                "version": 2 if state["published"] else 1, "expires_at": expires,
                "updated_at": "2026-11-04T12:00:00Z" if state["published"] else None,
                "cleanup_pending": not state["published"]}

        def authenticated(self):
            if self.headers.get("Authorization") != "Bearer " + token or self.headers.get("x-moviestar-client") != "moviestar-cli-v1":
                state["errors"].append("missing owner authentication")
                self.reply(401, {})
                return False
            return True

        def do_POST(self):
            if not self.authenticated():
                return
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if self.path.endswith("/preflight"):
                self.reply(200, {"status": "ok", "video_id": video, "owner_handle": "maker", "current_version": 1,
                    "current_size_bytes": 10, "filename": file.name, "size_bytes": len(original), "used_videos": 3,
                    "current_used_bytes": 30, "resulting_used_bytes": 20 + len(original), "active_link_count": 1,
                    "active_links": [{"id": link, "url": origin + "/v/" + "w" * 43, "created_at": "2026-11-04T10:00:00Z"}],
                    "next_cursor": None, "links_path": f"/api/v1/videos/{video}/shares", "temporary_bytes_reserved": 2 * len(original)})
            elif self.path.endswith("/replacements"):
                state["reservations"] += 1
                if payload != {"filename": file.name, "size_bytes": len(original), "expected_version": 1, "idempotency_key": "http-cut"}:
                    state["errors"].append("changed reservation input")
                self.reply(200, {**self.status(), "upload": {"method": "PUT", "url": origin + "/object?capability=private",
                    "expires_at": expires, "headers": {"Content-Type": "video/mp4", "Content-Length": str(len(original)), "If-None-Match": "*"}}})
            elif self.path.endswith("/complete"):
                state["completions"] += 1
                if state["object"] != original:
                    state["errors"].append("incomplete object")
                    return self.reply(409, {"status": "upload_missing"})
                state["published"] = True
                if lost_reply == "completion" and not state["dropped"]:
                    state["dropped"] = state["status_down"] = True
                    return self.drop()
                self.reply(200, self.status())
            else:
                self.reply(404, {})

        def do_GET(self):
            if self.path == "/trap":
                state["trap"] += 1
                return self.reply(500, {})
            if not self.authenticated():
                return
            if state["status_down"]:
                state["status_down"] = False
                return self.reply(503, {"status": "replacement_unavailable"})
            self.reply(200, self.status())

        def do_PUT(self):
            if self.path == "/trap":
                state["trap"] += 1
                return self.reply(500, {})
            state["puts"] += 1
            if any(self.headers.get(header) for header in ("Authorization", "Cookie", "Referer")):
                state["errors"].append("owner credentials sent to object storage")
            if self.headers.get("If-None-Match") != "*" or self.headers.get("Content-Type") != "video/mp4" or self.headers.get("Content-Length") != str(len(original)):
                state["errors"].append("missing signed upload headers")
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if lost_reply == "redirect":
                self.send_response(307)
                self.send_header("Location", origin + "/trap")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if state["object"] is not None:
                return self.reply(412, {})
            state["object"] = data
            if lost_reply == "upload" and not state["dropped"]:
                state["dropped"] = True
                return self.drop()
            self.reply(200, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    account = tmp_path / "config" / "account.json"
    account.parent.mkdir()
    account.write_text(json.dumps({"installation_url": origin + "/api/v1/account/installation", "connection": {
        "credential_storage": "private_file", "credential_fallback": token}}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(account))
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)
    monkeypatch.delenv("MOVIESTAR_WATCH_ORIGIN", raising=False)

    def invoke(*args):
        result = CliRunner().invoke(cli, ["shares", "replace", video, str(file), *args])
        assert result.stdout, repr(result.exception)
        return result, json.loads(result.stdout)

    try:
        preview, body = invoke("--dry-run")
        assert preview.exit_code == 0 and body["active_link_count"] == 1
        assert state["reservations"] == 0 and state["puts"] == 0
        assert not list(account.parent.glob("replacement-requests"))
        first, body = invoke("--idempotency-key", "http-cut")
        assert first.exit_code == 1, first.output
        assert body["video_replaced"] is (None if lost_reply == "completion" else False)
        if lost_reply == "redirect":
            assert state["trap"] == state["completions"] == 0
        else:
            retry, body = invoke()
            assert retry.exit_code == 0, retry.output
            assert body["video_replaced"] is True and body["version"] == 2
            assert state["completions"] == 1
            assert state["puts"] == (2 if lost_reply == "upload" else 1)
            assert state["reservations"] == (2 if lost_reply == "upload" else 1)
            assert state["object"] == original
        assert not state["errors"]
        assert file.read_bytes() == original
        assert token not in first.output and "capability=private" not in first.output
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
