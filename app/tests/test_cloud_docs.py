"""Private developer guides through the approved installation, never bundled content."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from click.testing import CliRunner
from moviestar.cli import cli

TOKEN = "mvs_" + "a" * 43
PAGES = [{"slug": "quickstart", "title": "Quickstart", "description": "Create a project", "status": "Preview", "group": "Start building"}]

@pytest.fixture
def service(tmp_path, monkeypatch):
    state = {"calls": [], "status": 200, "malformed": False, "redirect": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_GET(self):
            state["calls"].append((self.path, dict(self.headers)))
            assert self.headers["Authorization"] == "Bearer " + TOKEN
            assert self.headers["x-moviestar-client"] == "moviestar-cli-v1"
            body = ({"private_error": TOKEN} if state["status"] != 200 else {} if state["malformed"] else
                    {"pages": PAGES} if self.path.endswith("developer-docs") else
                    {"page": "quickstart", "title": "Quickstart", "markdown": "# Quickstart\n\nPrivate preview.\n", "path": "/account/developer-docs/quickstart"})
            raw = json.dumps(body).encode()
            self.send_response(302 if state["redirect"] else state["status"])
            if state["redirect"]: self.send_header("Location", "/foreign")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    profile = tmp_path / "account.json"
    profile.write_text(json.dumps({"installation_url": f"http://127.0.0.1:{server.server_port}/api/v1/account/installation",
        "connection": {"credential_storage": "private_file", "credential_fallback": TOKEN}}))
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(profile))
    monkeypatch.delenv("MOVIESTAR_ACCOUNT_INSTALLATION_URL", raising=False)
    yield state, profile
    server.shutdown(); server.server_close(); thread.join(timeout=2)

def invoke(*args):
    return CliRunner().invoke(cli, ["cloud", "docs", *args])

def test_lists_json_and_reads_default_markdown_or_explicit_formats_from_same_service(service):
    state, profile = service; before = profile.read_bytes()
    result = invoke("list")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["pages"] == PAGES
    result = invoke("show", "quickstart")
    assert result.exit_code == 0 and result.stdout == "# Quickstart\n\nPrivate preview.\n"
    result = invoke("show", "quickstart", "--json")
    assert result.exit_code == 0 and json.loads(result.stdout) == {
        "page": "quickstart", "title": "Quickstart",
        "markdown": "# Quickstart\n\nPrivate preview.\n",
        "path": "/account/developer-docs/quickstart",
    }
    result = invoke("show", "quickstart", "--markdown")
    assert result.exit_code == 0 and result.stdout == "# Quickstart\n\nPrivate preview.\n"
    assert [path for path, _ in state["calls"]] == ["/api/v1/account/developer-docs", "/api/v1/account/developer-docs/quickstart", "/api/v1/account/developer-docs/quickstart", "/api/v1/account/developer-docs/quickstart"]
    assert profile.read_bytes() == before

@pytest.mark.parametrize("status,expected", [(401,"credential_invalid"),(404,"docs_unavailable"),(503,"docs_unavailable")])
@pytest.mark.parametrize("format_args", [(), ("--json",), ("--markdown",)])
def test_denied_revoked_and_unavailable_never_echo_private_errors(service,status,expected,format_args):
    service[0]["status"] = status
    result = invoke("show", "quickstart", *format_args)
    assert result.exit_code == 1 and json.loads(result.stdout)["status"] == expected
    assert TOKEN not in result.output

def test_rejects_paths_before_network_and_does_not_follow_redirects(service):
    for slug in ("../account", "https://foreign.test", "quickstart?user=other"):
        assert invoke("show", slug).exit_code != 0
    assert not service[0]["calls"]
    service[0]["redirect"] = True
    assert invoke("list").exit_code == 1
    assert len(service[0]["calls"]) == 1

def test_rejects_malformed_content_and_requires_connection(service,monkeypatch,tmp_path):
    service[0]["malformed"] = True
    result = invoke("show", "quickstart")
    assert result.exit_code == 1 and json.loads(result.stdout)["status"] == "invalid_response"
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(tmp_path/"missing.json"))
    count = len(service[0]["calls"])
    assert invoke("list").exit_code == 1
    assert len(service[0]["calls"]) == count


@pytest.mark.parametrize("format_args", [("--json", "--markdown"), ("--markdown", "--json")])
def test_rejects_conflicting_formats_before_contacting_service(service, format_args):
    result = invoke("show", "quickstart", *format_args)
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "invalid_request"
    assert not service[0]["calls"]
