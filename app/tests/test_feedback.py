"""Anonymous CLI feedback delivery contracts."""

import json

from click.testing import CliRunner

from moviestar.cli import cli


def test_feedback_is_listed_in_top_level_help():
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "feedback" in result.output
    assert "Create or send guided feedback" in result.output


def test_feedback_is_registered_and_explains_privacy():
    result = CliRunner().invoke(cli, ["feedback", "--help"])

    assert result.exit_code == 0
    assert "anonymous" in result.output.lower()
    assert "project files" in result.output.lower()
    assert "without options" in result.output.lower()
    assert "--quick" in result.output
    assert "--template" in result.output
    assert "--file" in result.output
    assert "subscribe" in result.output.lower()


def test_feedback_defaults_to_template_with_required_and_optional_agent_prompts(
    monkeypatch,
):
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: (_ for _ in ()).throw(AssertionError("template must not send")),
    )

    result = CliRunner().invoke(cli, ["feedback"])

    assert result.exit_code == 0, result.stdout
    assert "## Summary (required)" in result.stdout
    assert "## What I was trying to do (required)" in result.stdout
    assert "## Feedback (required)" in result.stdout
    assert "## Reproduction and evidence (optional)" in result.stdout
    assert "## What worked well (optional)" in result.stdout
    assert "## Ideas (optional)" in result.stdout
    assert "## Wild idea (optional)" in result.stdout
    assert "## Follow-up email (optional)" in result.stdout
    assert "when this feedback has been addressed" in result.stdout


def test_feedback_template_prints_submission_handoff_outside_the_report(monkeypatch):
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: (_ for _ in ()).throw(AssertionError("template must not send")),
    )

    result = CliRunner().invoke(cli, ["feedback"])

    assert result.exit_code == 0
    assert "moviestar feedback > feedback.md" in result.stderr
    assert "moviestar feedback --file feedback.md" in result.stderr
    assert "structured JSON receipt" in result.stderr
    assert "moviestar subscribe EMAIL" in result.stderr
    assert "confirmation" in result.stderr.lower()
    assert "moviestar feedback --file feedback.md" not in result.stdout


def test_feedback_template_flag_remains_an_explicit_alias(monkeypatch):
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: (_ for _ in ()).throw(AssertionError("template must not send")),
    )

    default = CliRunner().invoke(cli, ["feedback"])
    explicit = CliRunner().invoke(cli, ["feedback", "--template"])

    assert default.exit_code == 0
    assert explicit.exit_code == 0
    assert explicit.stdout == default.stdout
    assert explicit.stderr == default.stderr


def test_feedback_can_send_a_template_filled_in_from_a_file(monkeypatch, tmp_path):
    report = tmp_path / "feedback.md"
    report.write_text(
        "## Summary (required)\nCaptions drifted\n\n"
        "## What I was trying to do (required)\nExport a promo\n\n"
        "## Feedback (required)\nThe last cue appeared late\n",
        encoding="utf-8",
    )
    captured = []
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: captured.append(payload) or {"err": None, "discord": "ok"},
    )

    result = CliRunner().invoke(cli, ["feedback", "--file", str(report)])

    assert result.exit_code == 0, result.stdout
    assert captured[0]["message"] == report.read_text(encoding="utf-8").strip()


def test_feedback_extracts_follow_up_email_without_putting_it_in_the_message(
    monkeypatch, tmp_path
):
    report = tmp_path / "feedback.md"
    report.write_text(
        "## Summary (required)\nCaptions drifted\n\n"
        "## Follow-up email (optional)\n  Human@Example.COM  \n\n"
        "## Feedback (required)\nThe last cue appeared late\n",
        encoding="utf-8",
    )
    captured = []
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: captured.append(payload)
        or {
            "err": None,
            "discord": "ok",
            "feedback_id": "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
            "anonymous": False,
            "follow_up": "requested",
        },
    )

    result = CliRunner().invoke(cli, ["feedback", "--file", str(report)])

    assert result.exit_code == 0, result.stdout
    assert captured[0]["follow_up_email"] == "human@example.com"
    assert "human@example.com" not in captured[0]["message"].lower()
    assert "Follow-up email" not in captured[0]["message"]
    data = json.loads(result.stdout)
    assert data["anonymous"] is False
    assert data["follow_up"] == "requested"
    assert "when this feedback has been addressed" in data["hint"]


def test_feedback_ignores_the_unfilled_follow_up_placeholder(monkeypatch, tmp_path):
    report = tmp_path / "feedback.md"
    report.write_text(
        "## Summary (required)\nCaptions drifted\n\n"
        "## Feedback (required)\nThe last cue appeared late\n\n"
        "## Follow-up email (optional)\n"
        "<!-- Leave an email address to get a note when this feedback has been addressed. -->\n",
        encoding="utf-8",
    )
    captured = []
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: captured.append(payload) or {"err": None, "discord": "ok"},
    )

    result = CliRunner().invoke(cli, ["feedback", "--file", str(report)])

    assert result.exit_code == 0, result.stdout
    assert set(captured[0]) == {"message", "moviestar_version"}
    assert "Follow-up email" not in captured[0]["message"]
    assert json.loads(result.stdout)["anonymous"] is True


def test_feedback_rejects_invalid_follow_up_email_without_sending(
    monkeypatch, tmp_path
):
    report = tmp_path / "feedback.md"
    report.write_text(
        "## Summary (required)\nCaptions drifted\n\n"
        "## Follow-up email (optional)\nnot-an-email\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: (_ for _ in ()).throw(AssertionError("invalid input must not send")),
    )

    result = CliRunner().invoke(cli, ["feedback", "--file", str(report)])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert "email" in data["error"].lower()
    assert "leave the section blank" in data["hint"].lower()


def test_feedback_file_dash_reads_stdin(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: captured.append(payload) or {"err": None, "discord": "ok"},
    )

    result = CliRunner().invoke(
        cli,
        ["feedback", "--file", "-"],
        input="feedback supplied over stdin\n",
    )

    assert result.exit_code == 0, result.stdout
    assert captured[0]["message"] == "feedback supplied over stdin"


def test_feedback_rejects_conflicting_input_modes_without_sending(monkeypatch, tmp_path):
    report = tmp_path / "feedback.md"
    report.write_text("from file", encoding="utf-8")
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: (_ for _ in ()).throw(AssertionError("invalid input must not send")),
    )

    result = CliRunner().invoke(
        cli,
        ["feedback", "--quick", "inline", "--file", str(report)],
    )

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "failed"
    assert "either" in data["error"].lower()
    assert "--quick" in data["hint"]


def test_feedback_rejects_legacy_positional_message_with_quick_hint(monkeypatch):
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: (_ for _ in ()).throw(AssertionError("invalid input must not send")),
    )

    result = CliRunner().invoke(cli, ["feedback", "old positional message"])

    assert result.exit_code == 2
    data = json.loads(result.stdout)
    assert data["command"] == "feedback"
    assert "--quick" in data["hint"]


def test_feedback_sends_only_message_and_version(monkeypatch):
    captured = []

    def fake_send(payload):
        captured.append(payload)
        return {
            "err": None,
            "discord": "ok",
            "feedback_id": "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
        }

    monkeypatch.setattr("moviestar.feedback._send_remote", fake_send)

    result = CliRunner().invoke(
        cli, ["feedback", "--quick", "the trim hint helped"]
    )

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data == {
        "command": "feedback",
        "status": "success",
        "anonymous": True,
        "remote": "sent",
        "discord": "ok",
        "feedback_id": "018f47f2-c3d2-7b28-8f65-3c50b52f6f23",
    }
    assert len(captured) == 1
    assert captured[0]["message"] == "the trim hint helped"
    assert set(captured[0]) == {"message", "moviestar_version"}


def test_feedback_remote_failure_is_structured(monkeypatch):
    monkeypatch.setattr(
        "moviestar.feedback._send_remote",
        lambda payload: {"err": "HTTP 502: delivery failed", "discord": "failed"},
    )

    result = CliRunner().invoke(cli, ["feedback", "--quick", "could not export"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["command"] == "feedback"
    assert data["status"] == "failed"
    assert data["anonymous"] is True
    assert data["discord"] == "failed"
    assert "502" in data["error"]
    assert data["hint"]


def test_feedback_url_can_be_overridden(monkeypatch):
    from moviestar.feedback import _resolve_feedback_url

    monkeypatch.setenv(
        "MOVIESTAR_FEEDBACK_URL", "https://preview.example/api/feedback"
    )
    assert _resolve_feedback_url() == "https://preview.example/api/feedback"

    monkeypatch.delenv("MOVIESTAR_FEEDBACK_URL")
    assert _resolve_feedback_url() == "https://trymoviestar.com/api/feedback"
