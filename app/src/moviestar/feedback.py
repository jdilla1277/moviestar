"""Anonymous feedback delivery for the MovieStar CLI."""

from __future__ import annotations

import json
import os
import re
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import click

from moviestar import __version__


_DEFAULT_FEEDBACK_URL = "https://trymoviestar.com/api/feedback"
_CLIENT_MARKER = "moviestar-cli-v1"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_FOLLOW_UP_HEADING = "## Follow-up email (optional)"
_FOLLOW_UP_SECTION_RE = re.compile(
    rf"(?ms)^{re.escape(_FOLLOW_UP_HEADING)}[ \t]*\n(?P<body>.*?)(?=^## |\Z)"
)
_HTML_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
_FEEDBACK_TEMPLATE = """## Summary (required)
One sentence describing the feedback.

## What I was trying to do (required)
The goal and relevant context.

## Feedback (required)
What happened, where friction appeared, or what I wish existed.

## Expected behavior (optional)
Especially useful for bugs and confusing behavior.

## Reproduction and evidence (optional)
Minimal steps and deliberately shared, redacted commands or output.

## Impact or workaround (optional)
How serious it was and how I continued.

## What worked well (optional)
Capabilities and contracts MovieStar should protect.

## Ideas (optional)
Concrete improvements suggested by this experience.

## Wild idea (optional)
If MovieStar could change more fundamentally, what would you try?

## Follow-up email (optional)
<!-- Leave an email address to get a note when this feedback has been addressed. -->
"""
_TEMPLATE_HANDOFF = """Next steps:
  moviestar feedback > feedback.md
  Complete the required sections, then submit the report:
  moviestar feedback --file feedback.md
A successful submission returns a structured JSON receipt.
For broader MovieStar update emails, an agent can run:
  moviestar subscribe EMAIL
The human must open the confirmation email before the subscription is active."""


def _resolve_feedback_url() -> str:
    """Return the production endpoint unless a preview URL is configured."""
    return os.environ.get("MOVIESTAR_FEEDBACK_URL") or _DEFAULT_FEEDBACK_URL


def _send_remote(payload: dict) -> dict:
    """Send a feedback payload and return a small, non-raising result."""
    request = Request(
        _resolve_feedback_url(),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-moviestar-feedback": _CLIENT_MARKER,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
            if response.status != 200:
                return {
                    "err": f"Server returned HTTP {response.status}.",
                    "discord": body.get("discord"),
                    "feedback_id": body.get("feedback_id"),
                }
            return {
                "err": None,
                "discord": body.get("discord"),
                "feedback_id": body.get("feedback_id"),
                "anonymous": body.get("anonymous"),
                "follow_up": body.get("follow_up"),
            }
    except HTTPError as exc:
        discord = None
        try:
            body = json.loads(exc.read().decode("utf-8"))
            discord = body.get("discord")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        return {
            "err": f"HTTP {exc.code}: {exc.reason}",
            "discord": discord,
            "feedback_id": None,
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {"err": str(exc), "discord": None}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "err": f"Invalid response from feedback service: {exc}",
            "discord": None,
        }


def _input_error(message: str, hint: str) -> None:
    click.echo(
        json.dumps(
            {
                "command": "feedback",
                "status": "failed",
                "anonymous": True,
                "error": message,
                "hint": hint,
            },
            indent=2,
        )
    )
    raise click.exceptions.Exit(1)


def _print_template() -> None:
    """Print editable Markdown to stdout and its clean handoff to stderr."""
    click.echo(_FEEDBACK_TEMPLATE)
    click.echo(_TEMPLATE_HANDOFF, err=True)


def _extract_follow_up_email(message: str) -> tuple[str, str | None]:
    """Remove the template's optional email section and return its value."""
    match = _FOLLOW_UP_SECTION_RE.search(message)
    if match is None:
        return message, None

    value = _HTML_COMMENT_RE.sub("", match.group("body")).strip()
    without_section = (message[: match.start()] + message[match.end() :]).strip()
    if not value:
        return without_section, None

    normalized = value.lower()
    if not _EMAIL_RE.fullmatch(normalized) or len(normalized) > 254:
        _input_error(
            "Follow-up email must be a valid email address.",
            "Correct the address under '## Follow-up email (optional)', or "
            "leave the section blank.",
        )
    return without_section, normalized


@click.command()
@click.option(
    "--quick",
    "quick_message",
    metavar="MESSAGE",
    help="Send a deliberately short note without the guided template.",
)
@click.option(
    "--file",
    "feedback_file",
    type=click.File("r", encoding="utf-8"),
    help="Read feedback from a Markdown file; use '-' for stdin.",
)
@click.option(
    "--template",
    "show_template",
    is_flag=True,
    help="Print a guided Markdown feedback template without sending it.",
)
def feedback(
    quick_message: str | None,
    feedback_file: TextIO | None,
    show_template: bool,
) -> None:
    """Create or send guided feedback to the MovieStar team.

    Run without options to print a guided report with required context plus
    optional debugging, positive-feedback, idea, and wild-idea prompts. Fill
    it in and submit it with --file. Use --quick only for a deliberately short
    note. --template remains an explicit alias for printing the report.

    Feedback is anonymous unless you complete the optional follow-up email
    section. That address is stored separately so the team can send a note when
    the feedback is addressed; it does not subscribe you to general updates.
    For update emails, an agent can run 'moviestar subscribe EMAIL' and the
    human can confirm the subscription. The feedback payload never includes
    project files, paths, session logs, account identifiers, or command history.

    Set MOVIESTAR_FEEDBACK_URL to use a preview or self-hosted endpoint.
    """
    selected_inputs = int(quick_message is not None) + int(feedback_file is not None)
    if show_template:
        if selected_inputs:
            _input_error(
                "--template cannot be combined with --quick or --file.",
                "Run 'moviestar feedback --template' by itself, fill in the "
                "output, then submit it with --file.",
            )
        _print_template()
        return

    if selected_inputs > 1:
        _input_error(
            "Pass feedback either with --quick or --file, not both.",
            "Choose --quick for a short note, or --file for a completed guided "
            "report. Use --file - to read from stdin.",
        )

    if selected_inputs == 0:
        _print_template()
        return

    if feedback_file is not None:
        message = feedback_file.read()
    else:
        message = quick_message

    message = (message or "").strip()
    follow_up_email = None
    if feedback_file is not None:
        message, follow_up_email = _extract_follow_up_email(message)
    if not message:
        _input_error(
            "Feedback message cannot be empty.",
            'Pass a short note with --quick, or run "moviestar feedback" to '
            "create a guided report.",
        )

    payload = {"message": message, "moviestar_version": __version__}
    if follow_up_email is not None:
        payload["follow_up_email"] = follow_up_email
    result = _send_remote(payload)
    if result["err"] is not None:
        click.echo(
            json.dumps(
                {
                    "command": "feedback",
                    "status": "failed",
                    "anonymous": follow_up_email is None,
                    "discord": result.get("discord"),
                    "error": result["err"],
                    "hint": "Retry later or set MOVIESTAR_FEEDBACK_URL to another feedback endpoint.",
                },
                indent=2,
            )
        )
        raise click.exceptions.Exit(1)

    response = {
        "command": "feedback",
        "status": "success",
        "anonymous": result.get("anonymous", follow_up_email is None),
        "remote": "sent",
        "discord": result.get("discord"),
    }
    if result.get("feedback_id"):
        response["feedback_id"] = result["feedback_id"]
    if result.get("follow_up") == "requested":
        response["follow_up"] = "requested"
        response["hint"] = (
            "We'll email the supplied address when this feedback has been addressed."
        )
    click.echo(json.dumps(response, indent=2))
