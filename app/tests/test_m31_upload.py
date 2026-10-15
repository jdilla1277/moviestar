"""The private upload CLI speaks the real initiation/PUT/completion contract."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from click.testing import CliRunner

from moviestar.cli import cli


CREDENTIAL = "mvs_" + "a" * 43


def test_upload_streams_mp4_then_completes_without_exposing_secrets(tmp_path, monkeypatch):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 1024)
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length)) if length else None
            seen.append((self.path, dict(self.headers), body))
            if self.path == "/api/v1/videos/uploads":
                payload = {
                    "status": "upload_pending", "reservation_id": "123e4567-e89b-42d3-a456-426614174000",
                    "upload": {
                        "method": "PUT",
                        "url": f"http://127.0.0.1:{self.server.server_port}/signed?secret=private",
                        "headers": {"Content-Type": "video/mp4", "Content-Length": str(len(video.read_bytes()))},
                    },
                }
                self.send_response(201)
            else:
                payload = {"status": "ready", "video_id": "vid_test", "filename": video.name,
                           "size_bytes": video.stat().st_size}
                self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def do_PUT(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state = tmp_path / "account.json"
    state.write_text(json.dumps({
        "installation_id": "123e4567-e89b-42d3-a456-426614174001",
        "installation_url": f"http://127.0.0.1:{server.server_port}/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": CREDENTIAL},
    }))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))
    try:
        result = CliRunner().invoke(cli, ["upload", str(video), "--idempotency-key", "test-key"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "ready"
    assert payload["video_id"] == "vid_test"
    assert payload["idempotency_key"] == "test-key"
    assert "secret=private" not in result.output
    assert CREDENTIAL not in result.output
    assert [item[0].split("?")[0] for item in seen] == [
        "/api/v1/videos/uploads", "/signed",
        "/api/v1/videos/uploads/123e4567-e89b-42d3-a456-426614174000/complete",
    ]
    assert seen[0][1]["Authorization"] == f"Bearer {CREDENTIAL}"
    assert {key.lower(): value for key, value in seen[0][1].items()}["x-moviestar-client"] == "moviestar-cli-v1"
    assert seen[0][2] == {"filename": video.name, "size_bytes": video.stat().st_size,
                          "idempotency_key": "test-key"}
    assert seen[1][1].get("Authorization") is None
    assert seen[1][1]["Content-Type"] == "video/mp4"
    assert seen[1][2] == video.read_bytes()
    assert seen[2][1]["Authorization"] == f"Bearer {CREDENTIAL}"


def test_upload_needs_a_connected_account_and_valid_mp4(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(tmp_path / "missing-account.json"))
    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom")
    result = CliRunner().invoke(cli, ["upload", str(video)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "not_connected"

    wrong = tmp_path / "short.mov"
    wrong.write_bytes(video.read_bytes())
    result = CliRunner().invoke(cli, ["upload", str(wrong)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "invalid_file"


def test_upload_resume_retries_completion_without_putting_file_again(tmp_path, monkeypatch):
    import moviestar.upload as upload_module

    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom")
    state = tmp_path / "account.json"
    state.write_text(json.dumps({
        "installation_url": "https://preview.example/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": CREDENTIAL},
    }))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))
    calls = []

    def request(url, credential, payload=None):
        calls.append((url, payload))
        return 200, {"status": "ready", "video_id": "vid_test"}

    monkeypatch.setattr(upload_module, "_json_request", request)
    result = CliRunner().invoke(cli, ["upload", str(video), "--resume",
                                      "123e4567-e89b-42d3-a456-426614174000"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["video_id"] == "vid_test"
    assert calls == [(
        "https://preview.example/api/v1/videos/uploads/123e4567-e89b-42d3-a456-426614174000/complete",
        None,
    )]
