"""Live CLI handoffs: private upload composition and owner link lifecycle."""
import json

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar import sharing

VIDEO = "123e4567-e89b-42d3-a456-426614174001"
LINK = "123e4567-e89b-42d3-a456-426614174002"
CREDENTIAL = "mvs_" + "a" * 43
ORIGIN = "https://preview.example"
URL = f"{ORIGIN}/v/" + "s" * 43
QUOTA = {"before": {"used_videos": 1, "used_bytes": 100}, "after": {"used_videos": 1, "used_bytes": 100}, "changed": False}


def link(revoked=False):
    return {"id": LINK, "video_id": VIDEO, "url": None if revoked else URL,
            "access": "unlisted", "forwardable": True, "downloadable": True,
            "created_at": "2026-10-27T12:00:00Z", "revoked_at": "2026-10-27T12:01:00Z" if revoked else None}


def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    assert result.stdout, repr(result.exception)
    return result, json.loads(result.stdout)


@pytest.fixture
def connected(tmp_path, monkeypatch):
    state = tmp_path / "account.json"
    state.write_text(json.dumps({"installation_url": f"{ORIGIN}/api/v1/account/installation",
        "connection": {"handle": "@editor", "email": "e***@example.com", "credential_storage": "private_file", "credential_fallback": CREDENTIAL}}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)
    monkeypatch.delenv("MOVIESTAR_WATCH_ORIGIN", raising=False)
    return state


def test_share_existing_video_uses_device_auth_and_one_structured_result(connected, monkeypatch):
    calls = []
    def request(url, credential, payload=None, *, method="POST"):
        calls.append((url, credential, payload, method))
        return 201, {"status": "shared", "video_id": VIDEO, "link": link(), "quota": QUOTA}
    monkeypatch.setattr(sharing, "_json_request", request)
    result, body = invoke("share", VIDEO, "--idempotency-key", "handoff-1")
    assert result.exit_code == 0, result.output
    assert body["status"] == "shared"
    assert body["video_id"] == VIDEO and body["link_id"] == LINK and body["url"] == URL
    assert body["upload_performed"] is False and body["email_sent"] is False
    assert body["quota"] == QUOTA
    assert CREDENTIAL not in result.output
    assert calls == [(f"{ORIGIN}/api/v1/videos/{VIDEO}/shares", CREDENTIAL, {"idempotency_key": "handoff-1"}, "POST")]


def test_local_file_composes_upload_and_share_and_reuses_retry_key(connected, tmp_path, monkeypatch):
    file = tmp_path / "cut.mp4"
    file.write_bytes(b"video")
    uploads, requests = [], []
    def upload(file, **kwargs):
        uploads.append((file, kwargs))
        return {"status": "ready", "video_id": VIDEO, "upload_performed": len(uploads) == 1}
    def request(url, credential, payload=None, *, method="POST"):
        requests.append(payload)
        return (503, {"status": "publication_paused"}) if len(requests) == 1 else (200, {"status": "already_shared", "video_id": VIDEO, "link": link(), "quota": QUOTA})
    monkeypatch.setattr(sharing, "upload_video", upload)
    monkeypatch.setattr(sharing, "_json_request", request)
    first, failure = invoke("share", str(file), "--idempotency-key", "one-key")
    assert first.exit_code == 1 and failure["status"] == "publication_paused"
    assert failure["video_id"] == VIDEO and failure["upload_performed"] is True
    assert "url" not in failure and failure["idempotency_key"] == "one-key"
    retry, success = invoke("share", str(file), "--idempotency-key", "one-key")
    assert retry.exit_code == 0 and success["url"] == URL
    assert success["quota_scope"] == "link_publication"
    assert [i[1]["idempotency_key"] for i in uploads] == ["one-key", "one-key"]
    assert requests == [{"idempotency_key": "one-key"}] * 2
    assert file.read_bytes() == b"video"


def test_to_email_rejects_before_any_upload_or_request(connected, tmp_path, monkeypatch):
    file = tmp_path / "cut.mp4"; file.write_bytes(b"video")
    monkeypatch.setattr(sharing, "upload_video", lambda *a, **k: pytest.fail("unexpected upload"))
    monkeypatch.setattr(sharing, "_json_request", lambda *a, **k: pytest.fail("unexpected publication"))
    result, body = invoke("share", str(file), "--to", "editor@example.com")
    assert result.exit_code == 1 and body["status"] == "not_available"
    assert body["upload_performed"] is False and body["email_sent"] is False


@pytest.mark.parametrize("status,code", [("consent_required",409), ("video_not_ready",409), ("not_connected",401), ("over_quota",409), ("rate_limited",429), ("link_revoked",409), ("idempotency_conflict",409)])
def test_share_errors_are_retryable_and_redact_remote_details(connected, monkeypatch, status, code):
    monkeypatch.setattr(sharing, "_json_request", lambda *a, **k: (code, {"status": status, "hint": CREDENTIAL, "url": "https://secret", "private": "secret"}))
    result, body = invoke("share", VIDEO, "--idempotency-key", "retry")
    assert result.exit_code == 1 and body["status"] == status
    assert body["idempotency_key"] == "retry" and body["video_id"] == VIDEO
    assert CREDENTIAL not in result.output and "https://secret" not in result.output


def test_generated_share_key_is_announced_before_interruption(connected, monkeypatch):
    def request(*args, **kwargs): raise KeyboardInterrupt()
    monkeypatch.setattr(sharing, "_json_request", request)
    result = CliRunner().invoke(cli, ["share", VIDEO])
    assert result.exit_code != 0
    announcement = json.loads(result.stderr.splitlines()[0])
    assert announcement["command"] == "share" and announcement["status"] == "retry_key"
    assert announcement["idempotency_key"]


def test_list_links_scopes_video_and_cursor_and_preserves_revoked_history(connected, monkeypatch):
    calls=[]
    def request(url, credential, payload=None, *, method="POST"):
        calls.append((url, method))
        return 200, {"status":"ok", "links":[link(),link(True)], "next_cursor":LINK, "quota":QUOTA["after"]}
    monkeypatch.setattr(sharing, "_json_request", request)
    result, body = invoke("shares", "--video", VIDEO, "--cursor", LINK)
    assert result.exit_code == 0 and body["links"][1]["url"] is None
    assert body["next_cursor"] == LINK
    assert calls == [(f"{ORIGIN}/api/v1/videos/{VIDEO}/shares?cursor={LINK}", "GET")]
    result, body = invoke("shares")
    assert result.exit_code == 0
    assert calls[-1] == (f"{ORIGIN}/api/v1/shares", "GET")


def test_unshare_preserves_video_and_quota_and_is_retryable(connected, monkeypatch):
    calls=[]
    def request(url, credential, payload=None, *, method="POST"):
        calls.append((url,method))
        return 200,{"status":"already_revoked","video_id":VIDEO,"link":link(True),"video_deleted":False,"quota":QUOTA}
    monkeypatch.setattr(sharing,"_json_request",request)
    result, body = invoke("unshare",LINK)
    assert result.exit_code == 0 and body["status"] == "already_revoked"
    assert body["link_revoked"] is True and body["video_deleted"] is False and body["quota_released"] is False
    assert calls == [(f"{ORIGIN}/api/v1/shares/{LINK}","DELETE")]


def test_share_id_preflight_is_offline_and_uses_no_additional_quota(connected, monkeypatch):
    monkeypatch.setattr(sharing,"_json_request",lambda *a, **k:pytest.fail("unexpected request"))
    result,body=invoke("share",VIDEO,"--dry-run")
    assert result.exit_code == 0 and body["video_id"] == VIDEO
    assert body["quota"]["would_use_bytes"] == 0
    assert body["upload_performed"] is False


def test_full_http_handoff_retry_list_download_and_revoke(tmp_path, monkeypatch):
    """A real local HTTP contract, including PUT streaming and a lost link reply."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    body_bytes = b"\x00\x00\x00\x18ftypisom" + b"sample" * 100
    state = {"puts": 0, "published": False, "revoked": False, "keys": [], "stored": None}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, code, data):
            raw = json.dumps(data).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def authorized(self):
            assert self.headers["Authorization"] == f"Bearer {CREDENTIAL}"
            assert self.headers["x-moviestar-client"] == "moviestar-cli-v1"
        def item(self):
            return {**link(state["revoked"]), "url": None if state["revoked"] else f"{origin}/v/" + "s" * 43}
        def do_POST(self):
            self.authorized()
            incoming = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path.endswith("/uploads"):
                state["keys"].append(incoming["idempotency_key"])
                if state["stored"] is not None:
                    return self.reply(200,{"status":"already_completed","reservation_id":VIDEO})
                return self.reply(201,{"status":"upload_pending","reservation_id":VIDEO,
                    "upload":{"method":"PUT","url":origin+"/put?signed=secret", "headers":{"Content-Type":"video/mp4","Content-Length":str(len(body_bytes))}}})
            if self.path.endswith("/complete"):
                return self.reply(200,{"status":"stored_unverified","video_id":VIDEO,"size_bytes":len(body_bytes)})
            if self.path.endswith("/validate"):
                return self.reply(200,{"status":"ready","video_id":VIDEO,"media":{"durationMs":1000,"width":320,"height":180,"videoCodec":"avc","audioCodec":None}})
            if self.path.endswith("/shares"):
                state["keys"].append(incoming["idempotency_key"])
                if not state["published"]:
                    state["published"] = True
                    return self.reply(503,{"status":"sharing_unavailable"})
                return self.reply(200,{"status":"already_shared","video_id":VIDEO,"link":self.item(),"quota":QUOTA})
            self.reply(404,{"status":"not_found"})
        def do_PUT(self):
            assert self.headers.get("Authorization") is None
            state["stored"] = self.rfile.read(int(self.headers["Content-Length"]))
            state["puts"] += 1
            self.send_response(200); self.end_headers()
        def do_GET(self):
            if self.path.startswith("/api/"):
                self.authorized()
                return self.reply(200,{"status":"ok","links":[self.item()],"next_cursor":None,"quota":QUOTA["after"]})
            assert self.headers.get("Authorization") is None
            if self.path.endswith("/download"):
                if state["revoked"]: return self.reply(404,{"status":"not_found"})
                self.send_response(307); self.send_header("Location",origin+"/object?signed=secret"); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type","video/mp4")
            self.send_header("Content-Length",str(len(body_bytes))); self.end_headers();self.wfile.write(body_bytes)
        def do_DELETE(self):
            self.authorized(); state["revoked"]=True
            self.reply(200,{"status":"revoked","video_id":VIDEO,"link":self.item(),"video_deleted":False,"quota":QUOTA})
    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    file=tmp_path/"final.mp4";file.write_bytes(body_bytes)
    state_file=tmp_path/"account.json";state_file.write_text(json.dumps({"connection":{"credential_storage":"private_file","credential_fallback":CREDENTIAL}}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE",str(state_file))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL",origin+"/api/v1/account/installation")
    try:
        first, failure = invoke("share",str(file),"--idempotency-key","same-handoff")
        assert first.exit_code == 1 and failure["video_id"] == VIDEO
        assert failure["upload_performed"] is True
        assert state["published"] is True and failure["link_created"] is None
        assert failure["publication_outcome"] == "unknown"
        second, success = invoke("share",str(file),"--idempotency-key","same-handoff")
        assert second.exit_code == 0,second.output
        assert success["status"] == "already_shared" and state["puts"] == 1
        assert success["publication_outcome"] == "already_shared"
        assert state["keys"] == ["same-handoff"]*4 and state["stored"] == body_bytes
        assert success["upload_performed"] is False
        listed, listing=invoke("shares")
        assert listed.exit_code == 0 and listing["links"][0]["id"] == LINK
        out=tmp_path/"incoming.mp4"
        saved,downloaded=invoke("download",success["url"],"--out",str(out))
        assert saved.exit_code == 0 and out.read_bytes() == body_bytes
        revoked,removal=invoke("unshare",LINK)
        assert revoked.exit_code == 0 and removal["quota_released"] is False
        denied,unavailable=invoke("download",success["url"],"--out",str(tmp_path/"revoked.mp4"))
        assert denied.exit_code == 1 and unavailable["status"] == "not_found"
        for result in (first,second,listed,saved,revoked,denied):
            assert CREDENTIAL not in result.output and "signed=secret" not in result.output
    finally:
        server.shutdown();server.server_close();worker.join()


@pytest.mark.parametrize("corrupt", ["foreign_url", "foreign_video", "storage_fields", "invalid_quota"])
def test_share_validates_success_contract_and_never_echoes_storage_fields(connected,monkeypatch,corrupt):
    item=link()
    reply={"status":"shared","video_id":VIDEO,"link":item,"quota":QUOTA}
    if corrupt == "foreign_url": item["url"]="https://evil.example/v/"+"s"*43
    elif corrupt == "foreign_video": item["video_id"]=LINK
    elif corrupt == "invalid_quota": reply["quota"]={"secret":"private"}
    else: item.update({"credential":CREDENTIAL,"object_key":"private-object-key"})
    monkeypatch.setattr(sharing,"_json_request",lambda *a, **k:(201,reply))
    result,body=invoke("share",VIDEO)
    assert CREDENTIAL not in result.output and "private-object-key" not in result.output
    if corrupt == "storage_fields":
        assert result.exit_code == 0 and "object_key" not in body["link"]
    else:
        assert result.exit_code == 1 and "url" not in body


def test_owner_api_redirect_is_not_followed_with_device_credential(tmp_path,monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from threading import Thread
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            calls.append(self.path)
            self.send_response(307);self.send_header("Location","/credential-leak");self.end_headers()
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    origin=f"http://127.0.0.1:{server.server_port}"
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL",origin+"/api/v1/account/installation")
    state_file=tmp_path/"state.json";state_file.write_text(json.dumps({"connection":{"credential_storage":"private_file","credential_fallback":CREDENTIAL}}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE",str(state_file))
    try:
        result,body=invoke("share",VIDEO)
        assert result.exit_code == 1 and body["status"] == "sharing_unavailable"
        assert calls == [f"/api/v1/videos/{VIDEO}/shares"]
    finally:
        server.shutdown();server.server_close();worker.join()


@pytest.mark.parametrize("response", [(0, {}), (503, {"status": "sharing_unavailable"}), (201, {"status": "shared"}), (201, {"status": ["invalid"]})])
def test_unconfirmed_publication_is_unknown_and_requires_same_key_reconciliation(connected, monkeypatch, response):
    monkeypatch.setattr(sharing, "_json_request", lambda *a, **k: response)
    result, body = invoke("share", VIDEO, "--idempotency-key", "original-key")
    assert result.exit_code == 1
    assert body["link_created"] is None
    assert body["publication_outcome"] == "unknown"
    assert "may already be public" in body["hint"]
    assert VIDEO in body["hint"] and "original-key" in body["hint"]
    assert "url" not in body


@pytest.mark.parametrize("code,status", [(409,"consent_required"),(503,"publication_paused"),(429,"rate_limited")])
def test_explicit_publication_rejections_are_distinct_from_unknown_outcomes(connected,monkeypatch,code,status):
    monkeypatch.setattr(sharing,"_json_request",lambda *a, **k:(code,{"status":status}))
    result,body=invoke("share",VIDEO,"--idempotency-key","original-key")
    assert result.exit_code == 1 and body["link_created"] is False
    assert body["publication_outcome"] == "rejected"


def test_explicit_watch_origin_allows_canonical_preview_links_across_commands(connected,monkeypatch,tmp_path):
    from moviestar.download import watch_link
    api="https://app-deployment-example.vercel.app"
    watch="https://app-branch-example.vercel.app"
    canonical=f"{watch}/v/"+"s"*43
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL",api+"/api/v1/account/installation")
    monkeypatch.setenv("MOVIESTAR_WATCH_ORIGIN",watch)
    calls=[]
    def request(url,credential,payload=None,*,method="POST"):
        calls.append((url,credential,method))
        item={**link(),"url":canonical}
        if method == "GET": return 200,{"status":"ok","links":[item],"next_cursor":None,"quota":QUOTA["after"]}
        return 201,{"status":"shared","video_id":VIDEO,"link":item,"quota":QUOTA}
    monkeypatch.setattr(sharing,"_json_request",request)
    result,body=invoke("share",VIDEO)
    assert result.exit_code == 0 and body["url"] == canonical,result.output
    assert body["publication_outcome"] == "shared"
    for args in [("shares",),("shares","--video",VIDEO)]:
        listed,listing=invoke(*args)
        assert listed.exit_code == 0 and listing["links"][0]["url"] == canonical
    downloaded,preview=invoke("download",canonical,"--out",str(tmp_path/"video.mp4"),"--dry-run")
    assert downloaded.exit_code == 0 and preview["status"] == "would_download"
    assert watch_link(f"https://other-branch-example.vercel.app/v/"+"s"*43) is None
    assert all(url.startswith(api+"/api/") and credential == CREDENTIAL for url,credential,_ in calls)


@pytest.mark.parametrize("origin", ["http://remote.example", "https://branch.example/path", "https://user:password@branch.example", "https://branch.example?token=secret", "https://branch.example#fragment"])
def test_invalid_watch_origin_fails_before_publication_or_credential_lookup(connected,monkeypatch,origin):
    monkeypatch.setenv("MOVIESTAR_WATCH_ORIGIN",origin)
    monkeypatch.setattr(sharing,"_json_request",lambda *a, **k:pytest.fail("unexpected API request"))
    monkeypatch.setattr(sharing,"_credential_for_connection",lambda *a:pytest.fail("unexpected credential lookup"))
    result,body=invoke("share",VIDEO)
    assert result.exit_code == 1 and body["status"] == "invalid_configuration"
    assert "MOVIESTAR_WATCH_ORIGIN" in body["hint"]


def test_bad_viewer_configuration_does_not_block_owner_revocation(connected,monkeypatch):
    monkeypatch.setenv("MOVIESTAR_WATCH_ORIGIN","https://branch.example/private/path")
    monkeypatch.setattr(sharing,"_json_request",lambda *a, **k:(200,{"status":"revoked","video_id":VIDEO,"link":link(True),"video_deleted":False,"quota":QUOTA}))
    result,body=invoke("unshare",LINK)
    assert result.exit_code == 0 and body["link_revoked"] is True
