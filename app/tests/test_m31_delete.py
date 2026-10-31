"""Owner deletion is retryable, explicit about uncertainty, and distinct from unshare."""
import json
import pytest
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import sharing

VIDEO = "123e4567-e89b-42d3-a456-426614174001"
ORIGIN = "https://preview.example"
CREDENTIAL = "mvs_" + "a" * 43

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(tmp_path / "account.json"))

@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setattr(sharing, "_connection", lambda *_a, **_k: (ORIGIN, ORIGIN + "/api/v1/videos/uploads", CREDENTIAL))

def invoke(*args):
    result = CliRunner().invoke(cli, list(args))
    return result, json.loads(result.stdout)

@pytest.mark.parametrize("prefix", [("delete",), ("shares", "delete")])
def test_delete_preview_is_offline_and_truthful(monkeypatch, prefix):
    monkeypatch.setattr(sharing, "_connection", lambda *_a, **_k: pytest.fail("credential access"))
    monkeypatch.setattr(sharing, "_json_request", lambda *_a, **_k: pytest.fail("network"))
    result, body = invoke(*prefix, VIDEO, "--dry-run")
    assert result.exit_code == 0
    assert body["status"] == "would_delete_video" and body["video_id"] == VIDEO
    assert body["video_deleted"] is body["quota_released"] is body["all_links_revoked"] is False
    assert "downloaded" in body["hint"].lower()

@pytest.mark.parametrize("prefix", [("delete",), ("shares", "delete")])
def test_delete_is_discoverable_and_validates_id_before_credentials(monkeypatch, prefix):
    assert CliRunner().invoke(cli, [*prefix, "--help"]).exit_code == 0
    monkeypatch.setattr(sharing, "_connection", lambda *_a, **_k: pytest.fail("credential access"))
    result, body = invoke(*prefix, "../../local.mp4")
    assert result.exit_code == 1 and body["status"] == "invalid_request"

@pytest.mark.parametrize("status", ["deleted", "already_deleted"])
def test_delete_validates_server_ack_and_keeps_only_public_fields(connected, monkeypatch, status):
    calls = []
    def request(url, credential, **kwargs):
        calls.append((url, credential, kwargs))
        return 200, {"status": status, "video_id": VIDEO, "video_deleted": True,
                     "quota_released": True, "all_links_revoked": True,
                     "quota": {"used_videos": 2, "used_bytes": 100},
                     "object_key": "private/key", "hint": "secret server detail"}
    monkeypatch.setattr(sharing, "_json_request", request)
    result, body = invoke("delete", VIDEO)
    assert result.exit_code == 0 and body["status"] == status
    assert body["deletion_outcome"] == status and body["quota_released"] is True
    assert body["quota"] == {"used_videos": 2, "used_bytes": 100}
    assert calls == [(ORIGIN + "/api/v1/videos/" + VIDEO, CREDENTIAL, {"method": "DELETE"})]
    assert "private/key" not in result.stdout and "secret" not in result.stdout

@pytest.mark.parametrize("code,reply", [
    (0, {}), (503, {"status": "deletion_unavailable"}),
    (200, {"status": "deleted", "video_id": "wrong"}),
    (200, {"status": "deleted", "video_id": VIDEO, "video_deleted": True,
           "quota_released": True, "all_links_revoked": True,
           "quota": {"used_videos": -1, "used_bytes": 0}}),
])
def test_uncertain_delete_does_not_claim_nothing_happened(connected, monkeypatch, code, reply):
    monkeypatch.setattr(sharing, "_json_request", lambda *_a, **_k: (code, reply))
    result, body = invoke("delete", VIDEO)
    assert result.exit_code == 1 and body["deletion_outcome"] == "unknown"
    assert body["video_deleted"] is body["quota_released"] is body["all_links_revoked"] is None
    assert VIDEO in body["hint"] and "retry" in body["hint"].lower()

def test_pending_delete_records_links_disabled_but_no_quota_release(connected, monkeypatch):
    monkeypatch.setattr(sharing, "_json_request", lambda *_a, **_k: (503, {
        "status": "deletion_pending", "video_id": VIDEO, "video_deleted": False,
        "all_links_revoked": True, "quota_released": False}))
    result, body = invoke("delete", VIDEO)
    assert result.exit_code == 1 and body["deletion_outcome"] == "pending"
    assert body["all_links_revoked"] is True and body["quota_released"] is False
    assert body["video_deleted"] is False

@pytest.mark.parametrize("code,status", [(401,"not_connected"), (404,"not_found"), (400,"invalid_request")])
def test_rejected_delete_does_not_report_success(connected, monkeypatch, code, status):
    monkeypatch.setattr(sharing, "_json_request", lambda *_a, **_k: (code, {"status": status}))
    result, body = invoke("delete", VIDEO)
    assert result.exit_code == 1 and body["status"] == status
    assert body["deletion_outcome"] == "not_attempted" and body["video_deleted"] is False
