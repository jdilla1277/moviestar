"""The private upload CLI speaks the real initiation/PUT/completion contract."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from click.testing import CliRunner

from moviestar.cli import cli


CREDENTIAL = "mvs_" + "a" * 43
VIDEO_ID = "123e4567-e89b-42d3-a456-426614174000"
MEDIA = {"durationMs": 1000, "width": 64, "height": 64, "videoCodec": "avc", "audioCodec": "aac"}


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
                    "status": "upload_pending", "reservation_id": VIDEO_ID,
                    "upload": {
                        "method": "PUT",
                        "url": f"http://127.0.0.1:{self.server.server_port}/signed?secret=private",
                        "headers": {"Content-Type": "video/mp4", "Content-Length": str(len(video.read_bytes()))},
                    },
                }
                self.send_response(201)
            elif self.path == f"/api/v1/videos/{VIDEO_ID}/validate":
                payload = {"status": "ready", "video_id": VIDEO_ID, "media": MEDIA}
                self.send_response(200)
            else:
                payload = {"status": "stored_unverified", "video_id": VIDEO_ID, "filename": video.name,
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
    assert payload["video_id"] == VIDEO_ID
    assert payload["media"] == MEDIA
    assert payload["idempotency_key"] == "test-key"
    assert "secret=private" not in result.output
    assert CREDENTIAL not in result.output
    assert [item[0].split("?")[0] for item in seen] == [
        "/api/v1/videos/uploads", "/signed",
        "/api/v1/videos/uploads/123e4567-e89b-42d3-a456-426614174000/complete",
        f"/api/v1/videos/{VIDEO_ID}/validate",
    ]
    assert seen[0][1]["Authorization"] == f"Bearer {CREDENTIAL}"
    assert {key.lower(): value for key, value in seen[0][1].items()}["x-moviestar-client"] == "moviestar-cli-v1"
    assert seen[0][2] == {"filename": video.name, "size_bytes": video.stat().st_size,
                          "idempotency_key": "test-key"}
    assert seen[1][1].get("Authorization") is None
    assert seen[1][1]["Content-Type"] == "video/mp4"
    assert seen[1][2] == video.read_bytes()
    assert seen[2][1]["Authorization"] == f"Bearer {CREDENTIAL}"
    assert seen[3][1]["Authorization"] == f"Bearer {CREDENTIAL}"


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
        if url.endswith("/validate"):
            return 200, {"status": "ready", "video_id": VIDEO_ID, "media": MEDIA}
        return 200, {"status": "stored_unverified", "video_id": VIDEO_ID}

    monkeypatch.setattr(upload_module, "_json_request", request)
    result = CliRunner().invoke(cli, ["upload", str(video), "--resume",
                                      "123e4567-e89b-42d3-a456-426614174000"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["video_id"] == VIDEO_ID
    assert calls == [(
        "https://preview.example/api/v1/videos/uploads/123e4567-e89b-42d3-a456-426614174000/complete",
        None,
    ), (f"https://preview.example/api/v1/videos/{VIDEO_ID}/validate", None)]


def test_invalid_media_reports_quota_release_and_does_not_claim_a_share(tmp_path, monkeypatch):
    import moviestar.upload as upload_module

    video = tmp_path / "bad.mp4"
    video.write_bytes(b"not a playable video")
    state = tmp_path / "account.json"
    state.write_text(json.dumps({
        "installation_url": "https://preview.example/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": CREDENTIAL},
    }))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))
    calls = []

    def request(url, *_args):
        calls.append(url)
        if url.endswith("/complete"):
            return 200, {"status": "stored_unverified", "video_id": VIDEO_ID}
        return 422, {"status": "invalid_video", "video_id": VIDEO_ID,
                     "reason": "invalid_mp4", "quota_released": True}

    monkeypatch.setattr(upload_module, "_json_request", request)
    result = CliRunner().invoke(cli, ["upload", str(video), "--resume", VIDEO_ID])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "invalid_video"
    assert payload["reason"] == "invalid_mp4"
    assert payload["quota_released"] is True
    assert "url" not in payload
    assert calls == [f"https://preview.example/api/v1/videos/uploads/{VIDEO_ID}/complete",
                     f"https://preview.example/api/v1/videos/{VIDEO_ID}/validate"]


def test_validation_failure_is_retryable_without_a_second_transfer(tmp_path, monkeypatch):
    import moviestar.upload as upload_module

    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom")
    state = tmp_path / "account.json"
    state.write_text(json.dumps({
        "installation_url": "https://preview.example/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": CREDENTIAL},
    }))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))
    validations = []

    def request(url, *_args):
        if url.endswith("/validate"):
            validations.append(url)
            if len(validations) == 1:
                return 503, {"status": "validation_unavailable"}
            return 200, {"status": "ready", "video_id": VIDEO_ID, "media": MEDIA}
        return 200, {"status": "ready" if validations else "stored_unverified", "video_id": VIDEO_ID}

    monkeypatch.setattr(upload_module, "_json_request", request)
    monkeypatch.setattr(upload_module, "_put_file", lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected PUT")))
    first = CliRunner().invoke(cli, ["upload", str(video), "--resume", VIDEO_ID])
    assert first.exit_code == 1
    first_payload = json.loads(first.stdout)
    assert first_payload["status"] == "validation_unavailable"
    assert first_payload["video_id"] == VIDEO_ID
    assert "--resume" in first_payload["hint"]

    retry = CliRunner().invoke(cli, ["upload", str(video), "--resume", VIDEO_ID])
    assert retry.exit_code == 0
    assert json.loads(retry.stdout)["status"] == "ready"
    assert len(validations) == 2


def test_malformed_validation_response_never_claims_ready(tmp_path, monkeypatch):
    import moviestar.upload as upload_module

    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom")
    state = tmp_path / "account.json"
    state.write_text(json.dumps({
        "installation_url": "https://preview.example/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": CREDENTIAL},
    }))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))

    def request(url, *_args):
        if url.endswith("/validate"):
            return 200, {"status": "ready", "video_id": VIDEO_ID,
                         "media": {"durationMs": 1000, "width": 64, "height": 64, "videoCodec": "avc"}}
        return 200, {"status": "stored_unverified", "video_id": VIDEO_ID}

    monkeypatch.setattr(upload_module, "_json_request", request)
    result = CliRunner().invoke(cli, ["upload", str(video), "--resume", VIDEO_ID])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "validation_unavailable"


def test_upload_uses_legacy_installation_url_override(tmp_path, monkeypatch):
    import moviestar.upload as upload_module

    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom")
    state = tmp_path / "account.json"
    state.write_text(json.dumps({"connection": {
        "credential_storage": "private_file", "credential_fallback": CREDENTIAL,
    }}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL",
                       "https://preview.example/api/v1/account/installation")
    urls = []

    def request(url, *_args):
        urls.append(url)
        return 503, {"status": "upload_unavailable"}

    monkeypatch.setattr(upload_module, "_json_request", request)
    result = CliRunner().invoke(cli, ["upload", str(video)])
    assert result.exit_code == 1
    assert urls == ["https://preview.example/api/v1/videos/uploads"]


def test_completed_idempotency_key_recovers_lost_completion_response(tmp_path, monkeypatch):
    import moviestar.upload as upload_module

    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom")
    state = tmp_path / "account.json"
    state.write_text(json.dumps({
        "installation_url": "https://preview.example/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": CREDENTIAL},
    }))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))
    reservation = "123e4567-e89b-42d3-a456-426614174000"
    calls = []

    def request(url, *_args):
        calls.append(url)
        if url.endswith("/uploads"):
            return 200, {"status": "already_completed", "reservation_id": reservation}
        if url.endswith("/validate"):
            return 200, {"status": "ready", "video_id": VIDEO_ID, "media": MEDIA}
        return 200, {"status": "stored_unverified", "video_id": reservation}

    monkeypatch.setattr(upload_module, "_json_request", request)
    monkeypatch.setattr(upload_module, "_put_file", lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected PUT")))
    result = CliRunner().invoke(cli, ["upload", str(video), "--idempotency-key", "test-key"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["video_id"] == reservation
    assert len(calls) == 3


def test_upload_dry_run_reports_target_and_quota_without_network(tmp_path, monkeypatch):
    import moviestar.upload as upload_module

    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom")
    state = tmp_path / "account.json"
    state.write_text(json.dumps({
        "installation_url": "https://preview.example/api/v1/account/installation",
        "connection": {"handle": "@editor", "email": "e***@example.com",
                       "credential_storage": "private_file", "credential_fallback": CREDENTIAL},
    }))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))
    monkeypatch.setattr(upload_module, "_json_request", lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected API call")))
    monkeypatch.setattr(upload_module, "_put_file", lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected PUT")))
    result = CliRunner().invoke(cli, ["upload", str(video), "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "would_upload"
    assert payload["file"]["size_bytes"] == video.stat().st_size
    assert payload["account"]["handle"] == "@editor"
    assert payload["account"]["service_origin"] == "https://preview.example"
    assert payload["quota"]["would_use_bytes"] == video.stat().st_size
    assert payload["quota"]["free_limits"]["stored_videos"] == 3
    assert payload["quota"]["current_usage"] == "unavailable"


def test_interrupted_upload_reports_generated_key_before_network_and_reuses_it(tmp_path, monkeypatch):
    import moviestar.upload as upload_module

    video = tmp_path / "short.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom")
    state = tmp_path / "account.json"
    state.write_text(json.dumps({
        "installation_url": "https://preview.example/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": CREDENTIAL},
    }))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))
    reservation = "123e4567-e89b-42d3-a456-426614174000"
    seen_keys = []

    def request(url, _credential, payload=None):
        if url.endswith("/uploads"):
            seen_keys.append(payload["idempotency_key"])
            if len(seen_keys) == 1:
                raise KeyboardInterrupt()
            return 200, {"status": "already_completed", "reservation_id": reservation}
        if url.endswith("/validate"):
            return 200, {"status": "ready", "video_id": VIDEO_ID, "media": MEDIA}
        return 200, {"status": "stored_unverified", "video_id": reservation}

    monkeypatch.setattr(upload_module, "_json_request", request)
    monkeypatch.setattr(upload_module, "_put_file", lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected PUT")))

    interrupted = CliRunner().invoke(cli, ["upload", str(video)])
    assert interrupted.exit_code != 0
    announced = json.loads(interrupted.stderr.splitlines()[0])
    assert announced["status"] == "retry_key"
    assert announced["idempotency_key"] == seen_keys[0]
    assert "signed" not in interrupted.stderr.lower()

    recovered = CliRunner().invoke(cli, ["upload", str(video),
                                         "--idempotency-key", announced["idempotency_key"]])
    assert recovered.exit_code == 0, recovered.output
    assert json.loads(recovered.stdout)["video_id"] == reservation
    assert seen_keys == [announced["idempotency_key"]] * 2
