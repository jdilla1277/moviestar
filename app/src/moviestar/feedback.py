"""User-requested feedback; account contact resolution stays on the service."""

from __future__ import annotations

import json
import os
import re
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

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
Capabilities and contracts Moviestar should protect.

## Ideas (optional)
Concrete improvements suggested by this experience.

## Wild idea (optional)
If Moviestar could change more fundamentally, what would you try?
## Likelihood to recommend Moviestar (optional)
On a scale from 0 (not at all likely) to 10 (extremely likely), how likely are you to recommend Moviestar to others?

## Why or why not? (optional)
What is the main reason for your score?

## Follow-up email (optional)
<!-- Leave an email address to get a note when this feedback has been addressed. -->
<!-- Connected feedback uses your Moviestar account for follow-up. Use --anonymous
to omit the account connection and leave this email section blank. No device
files or profiles are searched for contact information. -->
"""
_TEMPLATE_HANDOFF = """Next steps:
  moviestar feedback > feedback.md
  Complete the required sections, then submit the report:
  moviestar feedback --file feedback.md
A successful submission returns a structured JSON receipt.
For broader Moviestar update emails, an agent can run:
  moviestar subscribe EMAIL
The human must open the confirmation email before the subscription is active."""


def _resolve_feedback_url() -> str:
    """Return the production endpoint unless a preview URL is configured."""
    return os.environ.get("MOVIESTAR_FEEDBACK_URL") or _DEFAULT_FEEDBACK_URL


def urlopen(request, *, timeout):
    """Feedback never follows redirects with a report or account credential."""
    from moviestar.account import _NoRedirectHandler
    return build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _feedback_credential(url: str) -> tuple[str | None, str | None]:
    from moviestar import account
    state = account._read_state()
    if not isinstance(state.get("connection"), dict):
        return None, None
    hint = "Reconnect to the intended Moviestar service, or use --anonymous to send without account linkage."
    recorded = state.get("installation_url")
    try:
        if not isinstance(recorded, str) or account._service_url_error(recorded) or account._service_url_error(url):
            raise ValueError
        approved, target = urlsplit(recorded), urlsplit(url)
        def origin(parsed):
            port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
            return parsed.scheme, parsed.hostname, port
        if (approved.path != "/api/v1/account/installation" or approved.query or approved.fragment
                or target.path != "/api/feedback" or target.query or target.fragment
                or origin(approved) != origin(target)):
            raise ValueError
    except ValueError:
        return None, "Feedback endpoint does not match the approved account service. " + hint
    # Resolve only this application's existing connection, after checking its
    # exact destination. Never inspect local email/profile data for feedback.
    credential = account._credential_for_connection(state)
    if not credential or not re.fullmatch(r"mvs_[A-Za-z0-9_-]{43}", credential):
        return None, "The connected account credential is unavailable. " + hint
    return credential, None


def _send_remote(payload: dict, *, anonymous: bool = False) -> dict:
    """Send a feedback payload and return a small, non-raising result."""
    url = _resolve_feedback_url()
    from moviestar.account import _service_url_error
    url_error = _service_url_error(url)
    try:
        port = urlsplit(url).port
        if port == 0:
            raise ValueError
    except ValueError:
        url_error = "The feedback service URL is invalid."
    if url_error:
        return {"err": url_error.replace("account service", "feedback service"), "discord": None}
    credential, error = (None, None) if anonymous else _feedback_credential(url)
    if error:
        return {"err": error, "discord": None, "anonymous": None}
    if credential:
        click.echo("Sending account-linked feedback to your approved Moviestar service; use --anonymous to omit account linkage.", err=True)
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-moviestar-feedback": _CLIENT_MARKER,
            **({"Authorization": f"Bearer {credential}", "x-moviestar-client": _CLIENT_MARKER} if credential else {}),
        },
        method="POST",
    )
    # A failed receipt does not prove the service rejected the report. Retain
    # the attempted linkage without claiming anonymity or encouraging duplicates.
    delivery_context = {
        "anonymous": None,
        "hint": "The account-linked report may have been received before the receipt failed. Do not resend automatically; verify delivery before submitting another report.",
    } if credential else {}
    try:
        with urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object from feedback service.")
            if response.status != 200:
                return {
                    "err": f"Server returned HTTP {response.status}.",
                    **delivery_context,
                    "discord": body.get("discord"),
                    "feedback_id": body.get("feedback_id"),
                }
            if credential and body.get("account_linked") is not True:
                return {"err": "The service did not confirm account linkage. The report may have been received; check its feedback_id before retrying.",
                        "discord": body.get("discord"), "feedback_id": body.get("feedback_id"), "anonymous": body.get("anonymous"),
                        "hint": "Do not resend automatically. Keep feedback_id for follow-up and update the service before sending account-linked reports."}
            return {
                "err": None,
                "discord": body.get("discord"),
                "feedback_id": body.get("feedback_id"),
                "anonymous": body.get("anonymous"),
                "follow_up": body.get("follow_up"),
                "account_linked": body.get("account_linked") is True,
                "contact_available": body.get("contact_available") is True,
            }
    except HTTPError as exc:
        discord = None
        try:
            body = json.loads(exc.read().decode("utf-8"))
            discord = body.get("discord") if isinstance(body, dict) else None
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass
        return {
            "err": f"HTTP {exc.code}: {exc.reason}",
            **delivery_context,
            "discord": discord,
            "feedback_id": None,
            **({"hint": "Reconnect or review your Moviestar account, or deliberately use --anonymous for an unlinked report."} if credential and exc.code in {401, 403} else {}),
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {"err": str(exc), "discord": None, **delivery_context}
    except (ValueError, UnicodeDecodeError) as exc:
        return {
            "err": f"Invalid response from feedback service: {exc}",
            **delivery_context,
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
@click.option("--anonymous", is_flag=True, help="Omit account linkage; never read the saved account connection or credential.")
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
    anonymous: bool,
) -> None:
    """Create or send guided feedback to the Moviestar team.

    Run without options to print a guided report with required context plus
    optional debugging, positive-feedback, idea, wild-idea, and recommendation
    prompts. Fill it in and submit it with --file. Use --quick only for a
    deliberately short note. --template remains an explicit alias for printing
    the report.

    When connected, feedback uses your existing approved Moviestar account
    connection so the service can retain verified account contact for follow-up.
    The CLI does not discover or send account email/profile details. Use
    --anonymous to omit account linkage; logged-out feedback remains anonymous
    unless you complete the optional follow-up email section. That explicitly
    supplied address is stored separately so the team can send a note when
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

    if anonymous and follow_up_email:
        _input_error("Anonymous feedback cannot include a follow-up email section.",
                     "Leave the follow-up email section blank, or omit --anonymous to deliberately supply contact.")

    payload = {"message": message, "moviestar_version": __version__}
    if follow_up_email is not None:
        payload["follow_up_email"] = follow_up_email
    result = _send_remote(payload, anonymous=True) if anonymous else _send_remote(payload)
    if result["err"] is not None:
        click.echo(
            json.dumps(
                {
                    "command": "feedback",
                    "status": "failed",
                    "anonymous": result.get("anonymous", follow_up_email is None),
                    "discord": result.get("discord"),
                    **({"feedback_id":result["feedback_id"]} if result.get("feedback_id") else {}),
                    "error": result["err"],
                    "hint": result.get("hint") or "Retry later or set MOVIESTAR_FEEDBACK_URL to another feedback endpoint.",
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
    if result.get("account_linked") is True:
        response["account_linked"] = True
        response["contact_available"] = result.get("contact_available") is True
        response["hint"] = "Feedback is linked to your Moviestar account for this report's follow-up; no newsletter subscription or follow-up email was sent."
    if result.get("follow_up") == "requested":
        response["follow_up"] = "requested"
        response["hint"] = (
            "We'll email the supplied address when this feedback has been addressed."
        )
    click.echo(json.dumps(response, indent=2))
