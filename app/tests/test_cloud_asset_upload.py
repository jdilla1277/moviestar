"""Remote asset creation uses approved installation auth and conditional storage PUTs."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from click.testing import CliRunner

from moviestar.cli import cli

TOKEN = "mvs_" + "a" * 43
ASSET = "123e4567-e89b-42d3-a456-426614174001"
UPLOAD = "123e4567-e89b-42d3-a456-426614174002"
FORMATS = [
    ("mp4", "video", "mp4", "video/mp4"), ("mov", "video", "mov", "video/quicktime"),
    ("webm", "video", "webm", "video/webm"), ("mp3", "audio", "mp3", "audio/mpeg"),
    ("wav", "audio", "wav", "audio/wav"), ("m4a", "audio", "m4a", "audio/mp4"),
    ("flac", "audio", "flac", "audio/flac"), ("jpg", "image", "jpeg", "image/jpeg"),
    ("jpeg", "image", "jpeg", "image/jpeg"), ("png", "image", "png", "image/png"),
    ("webp", "image", "webp", "image/webp"), ("gif", "image", "gif", "image/gif"),
    ("srt", "transcript", "srt", "application/x-subrip"),
    ("vtt", "transcript", "vtt", "text/vtt"), ("txt", "transcript", "txt", "text/plain"),
]


@pytest.fixture
def service(tmp_path, monkeypatch):
    state = {"calls": [], "asset": None, "stored": None, "key": None,
             "error": None, "complete_error": None, "lose_put": False,
             "bad_grant": None, "bad_ready": False, "lost_reserve": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass

        def reply(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            state["calls"].append((self.command, self.path, dict(self.headers)))
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            assert self.headers["Authorization"] == f"Bearer {TOKEN}"
            assert self.headers["x-moviestar-client"] == "moviestar-cli-v1"
            if state["error"]:
                status, code = state["error"]
                return self.reply(status, {"error": {"code": code, "message": TOKEN}})
            if self.path == "/api/v1/assets/uploads":
                key = self.headers["Idempotency-Key"]
                if state["key"] and state["key"] != key:
                    return self.reply(409, {"error": {"code": "idempotency_conflict"}})
                state["key"] = key
                if state["asset"] is None:
                    extension = body["filename"].rsplit(".", 1)[-1].lower()
                    _, kind, format_, mime = next(f for f in FORMATS if f[0] == extension)
                    state["asset"] = {"id": ASSET, "filename": body["filename"], "kind": kind,
                        "format": format_, "size_bytes": body["size_bytes"], "content_type": mime,
                        "state": "pending", "metadata": {}}
                if state["lost_reserve"]:
                    state["lost_reserve"] = False
                    self.close_connection = True
                    return
                grant = {"method": "PUT", "url": f"http://127.0.0.1:{self.server.server_port}/storage?secret=grant",
                    "headers": {"Content-Type": state["asset"]["content_type"],
                                "Content-Length": str(state["asset"]["size_bytes"]), "If-None-Match": "*"}}
                if state["bad_grant"]: state["bad_grant"](grant)
                return self.reply(200 if state["stored"] else 201, {"asset": state["asset"], "upload_id": UPLOAD,
                    "idempotency_key": key, "upload": grant if state["asset"]["state"] == "pending" else None})
            assert self.path == f"/api/v1/assets/uploads/{UPLOAD}/complete"
            if state["complete_error"]:
                status, code = state["complete_error"]
                return self.reply(status, {"error": {"code": code}, "asset": state["asset"]})
            state["asset"]["state"] = "pending" if state["bad_ready"] else "ready"
            self.reply(200, {"asset": {**state["asset"], "object_key": "private/server/path"}, "upload_id": UPLOAD})

        def do_PUT(self):
            state["calls"].append((self.command, self.path, dict(self.headers)))
            assert self.headers.get("Authorization") is None
            assert self.headers.get("x-moviestar-client") is None
            assert self.headers["If-None-Match"] == "*"
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            already = state["stored"] is not None
            if not already: state["stored"] = raw
            if state["lose_put"]:
                state["lose_put"] = False
                self.close_connection = True
                return
            self.send_response(412 if already else 200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = tmp_path / "account.json"
    profile.write_text(json.dumps({"installation_url": f"http://127.0.0.1:{server.server_port}/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": TOKEN}}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(profile))
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)
    yield state, profile
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def invoke(file, *args):
    result = CliRunner().invoke(cli, ["cloud", "assets", "create", str(file), *args])
    return result, json.loads(result.stdout) if result.stdout else {}


@pytest.mark.parametrize("extension,kind,format_,mime", FORMATS)
def test_each_starter_format_streams_and_returns_ready(service, tmp_path, monkeypatch, extension, kind, format_, mime):
    state, profile = service
    original_profile = profile.read_bytes()
    monkeypatch.setenv("PATH", str(tmp_path))  # No FFmpeg or other subprocesses.
    file = tmp_path / f"original.{extension}"
    file.write_bytes(b"original bytes; server validates content")
    result, body = invoke(file)
    assert result.exit_code == 0, result.output
    assert body["status"] == "ready" and body["command"] == "cloud assets create"
    assert body["asset"]["id"] == ASSET and body["upload_id"] == UPLOAD
    assert (body["asset"]["kind"], body["asset"]["format"], body["asset"]["content_type"]) == (kind, format_, mime)
    retry = json.loads(result.stderr)
    assert retry["idempotency_key"] == body["idempotency_key"] == state["key"]
    assert state["stored"] == file.read_bytes() and profile.read_bytes() == original_profile
    assert [(c[0], c[1].split("?")[0]) for c in state["calls"]] == [
        ("POST", "/api/v1/assets/uploads"), ("PUT", "/storage"), ("POST", f"/api/v1/assets/uploads/{UPLOAD}/complete")]
    assert all(secret not in result.output for secret in [TOKEN, "secret=grant", "private/server/path"])
    result, replay = invoke(file, "--idempotency-key", body["idempotency_key"])
    assert result.exit_code == 0 and replay["asset"]["id"] == ASSET
    assert len([c for c in state["calls"] if c[0] == "PUT"]) == 1


@pytest.mark.parametrize("stage", ["lost_reserve", "lose_put", "complete_error"])
def test_interrupted_upload_can_resume_without_duplicates(service, tmp_path, stage):
    state, _ = service
    file = tmp_path / "test.txt"; file.write_text("hello")
    state[stage] = (503, "asset_unavailable") if stage == "complete_error" else True
    result, failure = invoke(file, "--idempotency-key", "stable-key")
    assert result.exit_code == 1 and failure["idempotency_key"] == "stable-key"
    state["complete_error"] = None
    args = ["--resume", failure["upload_id"]] if stage == "complete_error" else ["--idempotency-key", "stable-key"]
    result, ready = invoke(file, *args)
    assert result.exit_code == 0, result.output
    assert ready["asset"]["id"] == ASSET
    assert len([c for c in state["calls"] if c[0] == "PUT"]) == (2 if stage == "lose_put" else 1)


@pytest.mark.parametrize("http,code", [(401,"credential_invalid"), (403,"account_consent_required"),
    (409,"asset_limit"), (409,"storage_limit"), (413,"file_too_large"), (409,"payment_grace"),
    (409,"idempotency_conflict"), (404,"not_found"), (503,"asset_unavailable")])
def test_service_errors_fail_gracefully_without_transfer(service, tmp_path, http, code):
    service[0]["error"] = (http, code)
    file = tmp_path / "test.txt"; file.write_text("hello")
    result, body = invoke(file, "--idempotency-key", "stable")
    assert result.exit_code == 1 and body["idempotency_key"] == "stable"
    assert body["status"] == ("unsupported_operation" if http == 404 else code)
    assert TOKEN not in result.output and not any(c[0] == "PUT" for c in service[0]["calls"])


def test_invalid_media_is_not_reported_ready(service, tmp_path):
    service[0]["complete_error"] = (422, "asset_invalid")
    file = tmp_path / "test.txt"; file.write_text("invalid content")
    result, body = invoke(file)
    assert result.exit_code == 1 and body["status"] == "asset_invalid" and body["upload_id"] == UPLOAD


@pytest.mark.parametrize("change", [lambda g: g.update(url="https://evil.invalid/upload"),
    lambda g: g["headers"].update(Authorization="secret"), lambda g: g["headers"].pop("If-None-Match"),
    lambda g: g["headers"].update({"Content-Length": "999"})])
def test_invalid_transfer_instructions_are_not_used(service, tmp_path, change):
    service[0]["bad_grant"] = change
    file = tmp_path / "test.txt"; file.write_text("hello")
    result, body = invoke(file)
    assert result.exit_code == 1 and body["status"] == "invalid_response"
    assert not any(c[0] == "PUT" for c in service[0]["calls"])


def test_pending_completion_cannot_be_a_success(service, tmp_path):
    service[0]["bad_ready"] = True
    file = tmp_path / "test.txt"; file.write_text("hello")
    result, body = invoke(file)
    assert result.exit_code == 1 and body["status"] == "invalid_response" and body["upload_id"] == UPLOAD


@pytest.mark.parametrize("name,raw", [("image.svg", b"x"), ("empty.png", b"")])
def test_unsupported_and_empty_files_fail_before_network(service, tmp_path, name, raw):
    file = tmp_path / name; file.write_bytes(raw)
    result, body = invoke(file)
    assert result.exit_code == 1 and body["status"] in {"unsupported_format", "invalid_size"}
    assert not service[0]["calls"]


def test_registration_preserves_existing_asset_reference_commands():
    import click
    from moviestar.cloud_asset_upload import register_asset_upload
    group = click.Group("cloud")
    assets = click.Group("assets")
    existing = click.Command("list")
    assets.add_command(existing)
    group.add_command(assets)
    register_asset_upload(group)
    assert group.commands["assets"].commands["list"] is existing
    assert "create" in group.commands["assets"].commands


@pytest.mark.parametrize("option,value", [("--idempotency-key", "invalid key"), ("--resume", "not-an-id")])
def test_invalid_retry_options_do_not_touch_network(service, tmp_path, option, value):
    file = tmp_path / "test.txt"; file.write_text("hello")
    result, body = invoke(file, option, value)
    assert result.exit_code == 1 and body["status"] == "invalid_request"
    assert not service[0]["calls"]


def test_format_size_limit_is_checked_without_reading_or_reserving(service, tmp_path):
    file = tmp_path / "huge.txt"
    with file.open("wb") as stream: stream.truncate(262145)
    result, body = invoke(file)
    assert result.exit_code == 1 and body["status"] == "invalid_size"
    assert not service[0]["calls"]
