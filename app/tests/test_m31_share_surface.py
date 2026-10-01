"""The first sharing PR establishes an honest, non-publishing CLI contract."""

import json

from click.testing import CliRunner

from moviestar.cli import cli


def invoke(*args: str):
    result = CliRunner().invoke(cli, list(args))
    return result, json.loads(result.stdout)


def test_share_commands_are_discoverable_and_explain_the_handoff():
    root = CliRunner().invoke(cli, ["--help"])
    share = CliRunner().invoke(cli, ["share", "--help"])
    shares = CliRunner().invoke(cli, ["shares", "--help"])

    assert root.exit_code == share.exit_code == shares.exit_code == 0
    assert "share" in root.output
    assert "shares" in root.output
    assert "unshare" in root.output
    assert "--to" in share.output
    assert "--dry-run" in share.output
    assert "forwarded" in share.output.lower()
    assert "download" in share.output.lower()
    assert "finished mp4" in share.output.lower()
    assert "exact email address" in share.output.lower()
    assert "delete" in shares.output
    assert "video and all its links" in shares.output.lower()


def test_share_preflight_uses_real_file_and_exact_recipient_without_uploading(
    tmp_path, monkeypatch
):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"movie")
    state = tmp_path / "account.json"
    state.write_text(
        json.dumps(
            {
                "installation_id": "test-installation",
                "connection": {"handle": "@alex", "email": "owner@example.com"},
            }
        )
    )
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(state))

    result, payload = invoke(
        "share", str(source), "--to", "Editor@Example.com", "--dry-run"
    )

    assert result.exit_code == 0
    assert payload["status"] == "would_share"
    assert payload["file"]["path"] == str(source)
    assert payload["file"]["size_bytes"] == 5
    assert payload["owner"]["handle"] == "@alex"
    assert payload["delivery"]["to"] == "Editor@Example.com"
    assert payload["delivery"]["would_send_email"] is True
    assert payload["access"] == {
        "mode": "unlisted",
        "forwardable": True,
        "downloadable": True,
    }
    assert payload["quota"]["plan"] == "unknown"
    assert payload["quota"]["current_usage"] == "unavailable"
    assert payload["quota"]["projected_usage"] == "unavailable"
    assert payload["quota"]["free_limits"] == {
        "stored_videos": 3,
        "total_bytes": 1_000_000_000,
        "per_video_bytes": 500_000_000,
    }
    assert payload["upload_performed"] is False
    assert payload["email_sent"] is False
    assert "url" not in payload
    assert source.read_bytes() == b"movie"


def test_share_requires_an_address_only_when_email_delivery_is_requested(
    tmp_path, monkeypatch
):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"movie")
    monkeypatch.setenv("MOVIESTAR_ACCOUNT_STATE", str(tmp_path / "missing-account.json"))

    result, payload = invoke("share", str(source), "--dry-run")

    assert result.exit_code == 0
    assert payload["delivery"]["to"] is None
    assert payload["delivery"]["would_send_email"] is False
    assert payload["owner"]["connection"] == "not_connected"
    assert "account connect" in payload["hint"]


def test_share_without_dry_run_does_not_claim_to_send_or_publish(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"movie")

    result, payload = invoke("share", str(source), "--to", "alex@example.com")

    assert result.exit_code == 1
    assert payload["status"] == "not_available"
    assert payload["delivery"]["to"] == "alex@example.com"
    assert payload["upload_performed"] is False
    assert payload["email_sent"] is False
    assert "url" not in payload


def test_share_asks_for_an_exact_email_and_a_finished_mp4(tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"movie")
    invalid_email, recipient_error = invoke(
        "share", str(source), "--to", "Alex", "--dry-run"
    )

    assert invalid_email.exit_code == 1
    assert recipient_error["status"] == "error"
    assert "exact email address" in recipient_error["hint"]
    assert recipient_error["email_sent"] is False

    unfinished = tmp_path / "project.mov"
    unfinished.write_bytes(b"movie")
    invalid_file, file_error = invoke("share", str(unfinished), "--dry-run")

    assert invalid_file.exit_code == 1
    assert file_error["status"] == "error"
    assert ".mp4" in file_error["hint"]
    assert file_error["upload_performed"] is False


def test_revoke_and_delete_are_distinct_and_never_mutate_in_surface_preview():
    revoke, revocation = invoke("unshare", "lnk_example", "--dry-run")
    delete, deletion = invoke("shares", "delete", "vid_example", "--dry-run")

    assert revoke.exit_code == delete.exit_code == 0
    assert revocation["status"] == "would_revoke_link"
    assert revocation["link_id"] == "lnk_example"
    assert revocation["video_deleted"] is False
    assert revocation["link_revoked"] is False
    assert deletion["status"] == "would_delete_video"
    assert deletion["video_id"] == "vid_example"
    assert deletion["video_deleted"] is False
    assert deletion["all_links_revoked"] is False
    assert deletion["quota_released"] is False

    listed, listing = invoke("shares")
    assert listed.exit_code == 1
    assert listing["status"] == "not_available"
    assert "videos" not in listing
