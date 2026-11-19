"""Contract checks for the canned audio interface, not Cloud acceptance."""
import importlib.util
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

HERE = Path(__file__).parent


def module():
    spec = importlib.util.spec_from_file_location("audio_mock", HERE / "surface.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def invoke(*args, code=0):
    result = CliRunner().invoke(module().cli, args)
    assert result.exit_code == code, result.output
    return json.loads(result.output)


def test_read_is_exact_and_has_separate_current_pointer():
    value = invoke("show", "--revision", "00000000-0000-4000-8000-000000000101")
    assert value["mock"] is True
    response = value["response"]
    assert response["current_revision"] != response["revision"]["id"]
    assert response["revision"]["spec"]["audio_mix"]["source_audio"] == {
        "gain_db": 0.0, "muted": False, "fade_in": 0.0, "fade_out": 0.0,
    }
    assert value["effects"] == dict(saved_revisions=0, activity_events=0, processing_jobs=0, customer_usage=0)


def test_validate_apply_and_mcp_share_request_and_audio_semantics():
    path = str(HERE / "requests" / "gain.json")
    values = [invoke("--surface", surface, "validate", "--request", path) for surface in ("cli", "api", "mcp")]
    assert all(v["response"] == values[0]["response"] for v in values)
    assert values[1]["transport"]["body"] == values[2]["transport"]["arguments"]["request"]
    assert all(not any(v["effects"].values()) for v in values)
    assert values[0]["response"]["changed_fields"] == ["audio_mix"]
    assert values[0]["response"]["changes"] is True
    assert values[0]["response"]["changed_paths"] == ["audio_mix.source_audio.gain_db"]
    applied = invoke("--surface", "api", "apply", "--request", path, "--idempotency-key", "retained-gain")
    assert applied["response"]["revision"]["spec"]["audio_mix"]["source_audio"]["gain_db"] == -6.0
    assert applied["response"]["status"] == "committed"
    assert applied["response"]["changed_fields"] == ["audio_mix"]
    assert applied["effects"] == dict(saved_revisions=1, activity_events=1, processing_jobs=0, customer_usage=0)
    assert applied["response"]["revision"]["resolved"]["routing"] == values[0]["response"]["projected_resolved"]["routing"]
    assert values[0]["response"]["current_undo_remaining"] == 0
    assert values[0]["response"]["projected_resolved"]["undo_remaining"] == 1


def test_mute_and_undo_restore_complete_previous_mix():
    muted = invoke("apply", "--request", str(HERE / "requests" / "mute.json"), "--idempotency-key", "mute")
    undo = invoke("apply", "--request", str(HERE / "requests" / "undo.json"), "--idempotency-key", "undo")
    assert muted["response"]["revision"]["spec"]["audio_mix"]["source_audio"]["muted"] is True
    assert undo["response"]["revision"]["spec"]["audio_mix"]["source_audio"] == {
        "gain_db": -6.0, "muted": False, "fade_in": 0.0, "fade_out": 0.0,
    }
    assert undo["response"]["revision"]["resolved"]["undo_remaining"] == 1


def test_replay_recovers_original_revision_not_newer_pointer():
    value = invoke("--scenario", "retry", "apply", "--request", str(HERE / "requests" / "gain.json"), "--idempotency-key", "retained-gain")
    response = value["response"]
    assert response["replayed"] is True
    assert response["revision"]["id"].endswith("102")
    assert response["current_revision"].endswith("103")
    assert not any(value["effects"].values())


def test_undo_replay_and_exact_read_do_not_rewind_pointer():
    value = invoke("--scenario", "retry", "apply", "--request", str(HERE / "requests" / "undo.json"), "--idempotency-key", "retained-undo")
    response = value["response"]
    assert response["replayed"] is True
    assert response["revision"]["id"].endswith("104")
    assert response["current_revision"] == response["revision"]["id"]
    assert not any(value["effects"].values())
    exact = invoke("show", "--revision", response["revision"]["id"])
    assert exact["response"]["current_revision"] == response["revision"]["id"]


@pytest.mark.parametrize("key", ["", "a" * 129, "bad/key", "bad key", "é", "bad\n"])
def test_invalid_key_never_commits_even_in_retry_fixture(key):
    for scenario in ("normal", "retry"):
        value = invoke("--scenario", scenario, "apply", "--request", str(HERE / "requests" / "gain.json"), "--idempotency-key", key, code=1)
        assert value["response"]["error"]["code"] == "invalid_request"
        assert value["response"]["error"]["details"]["field"] == "idempotency_key"
        assert "revision" not in value["response"]
        assert not any(value["effects"].values())


@pytest.mark.parametrize("key", ["a", "a" * 128, "A_z-09"])
def test_valid_key_boundaries(key):
    value = invoke("apply", "--request", str(HERE / "requests" / "gain.json"), "--idempotency-key", key)
    assert value["response"]["status"] == "committed"


def test_errors_and_lost_reply_are_explicit_and_not_acceptance():
    path = str(HERE / "requests" / "gain.json")
    for scenario, code in (("stale", "revision_conflict"), ("unauthorized", "not_found"), ("changed-key", "idempotency_conflict"), ("disabled", "edits_unavailable")):
        response = invoke("--scenario", scenario, "apply", "--request", path, "--idempotency-key", "retained", code=1)
        assert response["response"]["error"]["code"] == code
        assert set(response["response"]["error"]) == {"code", "message", "details", "retryable"}
        assert response["response"]["error"]["retryable"] is (scenario == "disabled")
        assert response["response"]["request_id"]
        assert not any(response["effects"].values())
    lost = invoke("--scenario", "lost-response", "apply", "--request", path, "--idempotency-key", "retained", code=1)
    assert lost["response"]["outcome"] == "unknown"
    assert "revision" not in lost["response"]
    assert lost["effects"] is None
    assert lost["fixture_expected_effects"]["saved_revisions"] == 1
    stale = invoke("--scenario", "stale", "apply", "--request", path, "--idempotency-key", "retained", code=1)
    assert stale["response"]["error"]["details"]["expected_base_revision"].endswith("101")
    assert stale["response"]["error"]["details"]["current_revision"].endswith("103")
    disabled_validate = invoke("--scenario", "disabled", "validate", "--request", path, code=1)
    assert disabled_validate["response"]["error"]["code"] == "edits_unavailable"


def test_unknown_request_is_rejected_as_fixture_limit(tmp_path):
    path = tmp_path / "unexpected.json"
    path.write_text(json.dumps({"operation": {"type": "audio_source", "source_id": "camera"}}))
    response = invoke("validate", "--request", str(path), code=1)
    assert response["response"]["error"]["code"] == "mock_fixture_required"


@pytest.mark.parametrize("surface", ["cli", "api", "mcp"])
def test_noop_validate_apply_and_replay_preserve_original_result(surface):
    path = str(HERE / "requests" / "noop.json")
    preview = invoke("--surface", surface, "validate", "--request", path)
    response = preview["response"]
    assert response["status"] == "would_apply"
    assert response["changes"] is False
    assert response["changed_fields"] == []
    assert response["current_undo_remaining"] == response["projected_resolved"]["undo_remaining"] == 1
    assert "revision" not in response
    assert not any(preview["effects"].values())
    saved = invoke("--surface", surface, "apply", "--request", path, "--idempotency-key", "retained-noop")
    recovered = invoke("--surface", surface, "--scenario", "retry", "apply", "--request", path, "--idempotency-key", "retained-noop")
    for value in (saved, recovered):
        assert value["http_status"] == 200
        assert value["response"]["status"] == "unchanged"
        assert value["response"]["changes"] is False
        assert value["response"]["changed_fields"] == []
        assert value["response"]["revision"]["id"].endswith("102")
        assert value["response"]["revision"]["resolved"]["undo_remaining"] == 1
        assert not any(value["effects"].values())
    assert saved["response"]["current_revision"].endswith("102")
    assert recovered["response"]["current_revision"].endswith("103")
    assert saved["response"]["replayed"] is False
    assert recovered["response"]["replayed"] is True
    assert saved["response"]["request_sha256"] == recovered["response"]["request_sha256"]


def test_noop_lost_reply_has_unknown_client_effects_and_zero_expected_writes():
    value = invoke("--scenario", "lost-response", "apply", "--request", str(HERE / "requests" / "noop.json"), "--idempotency-key", "retained-noop", code=1)
    assert value["response"]["outcome"] == "unknown"
    assert value["effects"] is None
    assert not any(value["fixture_expected_effects"].values())
