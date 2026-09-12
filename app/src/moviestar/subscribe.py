"""Agent-fillable double-opt-in signup for MovieStar updates."""

from __future__ import annotations

import json
import os
import platform
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import click

from moviestar import __version__


DEFAULT_SUBSCRIBE_URL = "https://trymoviestar.com/api/subscribe"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _agent_context() -> dict:
    return {
        "moviestar_version": __version__,
        "python_version": sys.version,
        "platform": platform.platform(),
    }


def _decode_response(body: bytes) -> dict:
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _send_remote(payload: dict) -> tuple[str | None, dict | None]:
    """POST a signup and return ``(error, response_body)``."""
    url = os.environ.get("MOVIESTAR_SUBSCRIBE_URL", DEFAULT_SUBSCRIBE_URL)
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = _decode_response(response.read())
            if response.status >= 400:
                return body.get("error", f"Server returned {response.status}"), body
            return None, body
    except HTTPError as error:
        body = _decode_response(error.read())
        return body.get("error", f"Server returned {error.code}"), body
    except URLError as error:
        return str(error.reason), None
    except Exception as error:  # Keep the CLI envelope stable for network failures.
        return str(error), None


@click.command()
@click.argument("email")
def subscribe(email: str) -> None:
    """Sign EMAIL up for updates via a confirmation email.

    This command is safe for an agent to run on a human's behalf. MovieStar
    sends the address a confirmation link and does not activate the
    subscription until the human opens it.
    """
    normalized = email.strip().lower()
    if not EMAIL_RE.fullmatch(normalized) or len(normalized) > 254:
        click.echo(
            json.dumps(
                {
                    "command": "subscribe",
                    "status": "error",
                    "error": f"Invalid email address: {email!r}",
                    "hint": "Pass the human's complete email address.",
                },
                indent=2,
            )
        )
        raise click.exceptions.Exit(1)

    error, remote = _send_remote(
        {
            "email": normalized,
            "source": "cli",
            "agent_context": _agent_context(),
        }
    )
    if error:
        click.echo(
            json.dumps(
                {
                    "command": "subscribe",
                    "status": "error",
                    "error": error,
                    "hint": "Try the subscription again later.",
                    "remote": remote,
                },
                indent=2,
            )
        )
        raise click.exceptions.Exit(1)

    remote = remote or {}
    already_confirmed = remote.get("status") == "already_confirmed"
    hint = (
        "The address is already confirmed."
        if already_confirmed
        else "A confirmation email was sent. Ask the human to open it and confirm the subscription."
    )
    click.echo(
        json.dumps(
            {
                "command": "subscribe",
                "status": "success",
                "email": normalized,
                "remote": remote,
                "hint": hint,
            },
            indent=2,
        )
    )
