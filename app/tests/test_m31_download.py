"""Recipient downloads stream anonymously, atomically, and without redirect trust leaks."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from click.testing import CliRunner
from moviestar.cli import cli

SLUG = "s" * 43
MEDIA = b"\x00\x00\x00\x18ftypisom" + b"video" * 5000

@pytest.fixture
def server(monkeypatch):
    state = {"requests": [], "redirect": None, "content_type": "video/mp4", "body": MEDIA, "length": len(MEDIA), "status": 200}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            state["requests"].append((self.path, dict(self.headers)))
            if self.path == f"/v/{SLUG}/download":
                self.send_response(307); self.send_header("Location", state["redirect"] or "/object?signed=secret-token"); self.end_headers(); return
            self.send_response(state["status"])
            self.send_header("Content-Type", state["content_type"])
            self.send_header("Content-Length", str(state["length"]))
            self.end_headers(); self.wfile.write(state["body"])
    httpd=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    origin=f"http://127.0.0.1:{httpd.server_port}"
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL",origin+"/api/v1/account/installation")
    worker=Thread(target=httpd.serve_forever,daemon=True);worker.start()
    yield origin, state
    httpd.shutdown();httpd.server_close();worker.join()


def invoke(*args):
    result=CliRunner().invoke(cli,list(args))
    return result,json.loads(result.stdout)


def test_download_streams_original_bytes_without_account_or_bearer(server,tmp_path,monkeypatch):
    origin,state=server
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE",str(tmp_path/"absent.json"))
    out=tmp_path/"project"/"cut.mp4"
    result,body=invoke("download",f"{origin}/v/{SLUG}","--out",str(out))
    assert result.exit_code == 0,result.output
    assert body["status"] == "downloaded" and body["download_performed"] is True
    assert out.read_bytes() == MEDIA and body["size_bytes"] == len(MEDIA)
    assert "secret-token" not in result.output and "signed=" not in result.output
    assert len(state["requests"]) == 2
    for _,headers in state["requests"]:
        assert "Authorization" not in headers and "Cookie" not in headers


def test_download_dry_run_does_not_fetch_create_dirs_or_need_credentials(server,tmp_path):
    origin,state=server;out=tmp_path/"new"/"video.mp4"
    result,body=invoke("download",f"{origin}/v/{SLUG}","--out",str(out),"--dry-run")
    assert result.exit_code == 0 and body["download_performed"] is False
    assert not out.parent.exists() and not state["requests"]


@pytest.mark.parametrize("redirect",["https://evil.example/secret", "http://169.254.169.254/latest/meta-data", "https://trymoviestar.com@evil.example/object", "https://bad.r2.cloudflarestorage.com.evil.example/object"])
def test_download_refuses_untrusted_redirect_without_second_request(server,tmp_path,redirect):
    origin,state=server;state["redirect"]=redirect;out=tmp_path/"cut.mp4"
    result,body=invoke("download",f"{origin}/v/{SLUG}","--out",str(out))
    assert result.exit_code == 1 and body["download_performed"] is False
    assert not out.exists() and len(state["requests"]) == 1


@pytest.mark.parametrize("failure",["truncated", "wrong_type", "not_mp4", "too_large", "expired", "redirect_again"])
def test_failed_transfer_leaves_no_partial_destination_or_tempfile(server,tmp_path,failure):
    origin,state=server;out=tmp_path/"cut.mp4"
    if failure == "truncated": state["length"]+=100
    elif failure == "wrong_type": state["content_type"]="text/html"
    elif failure == "not_mp4": state["body"]=b"x"*len(MEDIA)
    elif failure == "too_large": state["length"]=1_000_000_001
    elif failure == "expired": state["status"]=403
    elif failure == "redirect_again": state["status"]=307
    result,body=invoke("download",f"{origin}/v/{SLUG}","--out",str(out))
    assert result.exit_code == 1 and body["download_performed"] is False,result.output
    assert not out.exists() and list(tmp_path.iterdir()) == []


def test_destination_dangling_symlink_is_not_followed_or_replaced(server,tmp_path):
    origin,state=server;out=tmp_path/"cut.mp4";out.symlink_to(tmp_path/"missing.mp4")
    result,body=invoke("download",f"{origin}/v/{SLUG}","--out",str(out))
    assert result.exit_code == 1 and out.is_symlink()
    assert not (tmp_path/"missing.mp4").exists() and not state["requests"]


def test_download_does_not_overwrite_a_destination_created_during_transfer(server,tmp_path,monkeypatch):
    from moviestar import download as downloads
    origin,state=server;out=tmp_path/"cut.mp4"
    original_link=downloads.os.link
    def destination_race(source,destination):
        destination.write_bytes(b"existing edit")
        return original_link(source,destination)
    monkeypatch.setattr(downloads.os,"link",destination_race)
    result,body=invoke("download",f"{origin}/v/{SLUG}","--out",str(out))
    assert result.exit_code == 1 and body["download_performed"] is False
    assert out.read_bytes() == b"existing edit" and list(tmp_path.iterdir()) == [out]
