"""Request browser sign-in mail without handling human authentication secrets."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

import click

from moviestar import account as accounts


def _uuid(value: object) -> str | None:
    try:
        parsed = uuid.UUID(str(value))
        return str(parsed) if parsed.variant == uuid.RFC_4122 and parsed.version in range(1, 9) else None
    except (ValueError, TypeError, AttributeError):
        return None


def _origin(url: str) -> str:
    if accounts._service_url_error(url) or any(char.isspace() for char in url):
        raise ValueError("Invalid service URL")
    parsed = urlsplit(url)
    if parsed.query:
        raise ValueError("Invalid service URL")
    # Force validation of malformed and out-of-range ports before network work.
    parsed.port
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def _service_origin(state: dict) -> str:
    url = (
        os.environ.get("MOVIESTAR_ACCOUNT_INSTALLATION_URL")
        or os.environ.get("MOVIESTAR_ACCOUNT_CLAIM_URL")
        or state.get("installation_url")
        or state.get("claim_url")
        or accounts._DEFAULT_INSTALLATION_URL
    )
    if not isinstance(url, str):
        raise ValueError("Invalid service URL")
    return _origin(url)


def _pending_claim(state: dict, email: str, origin: str) -> str | None:
    pending = state.get("pending_claim")
    if not isinstance(pending, dict) or pending.get("email") != email:
        return None
    try:
        remembered = state.get("claim_url") or state.get("installation_url") or accounts._DEFAULT_CLAIM_URL
        if not isinstance(remembered, str) or _origin(remembered) != origin:
            return None
        expiry = datetime.fromisoformat(str(pending.get("expires_at")).replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            return None
    except (ValueError, TypeError):
        return None
    return _uuid(pending.get("request_id"))


def _post(origin: str, payload: dict) -> tuple[int | None, dict | None, str | None]:
    request = Request(
        origin + "/api/v1/account/sign-in-links",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-moviestar-client": accounts._CLIENT_MARKER},
        method="POST",
    )
    try:
        with build_opener(accounts._NoRedirectHandler()).open(request, timeout=10) as response:
            raw = response.read(4097)
            retry = response.headers.get("Retry-After")
            if len(raw) > 4096:
                return response.status, None, retry
            return response.status, accounts._decode_response(raw), retry
    except HTTPError as error:
        try:
            return error.code, None, error.headers.get("Retry-After") if error.headers else None
        finally:
            error.close()
    except (URLError, OSError, ValueError, UnicodeError, HTTPException):
        # Exception messages and response bodies can contain URLs or credentials.
        return None, None, None


def request_browser_sign_in(email: str | None, request_id: str | None, claim_id: str | None, dry_run: bool) -> None:
    def fail(status: str, hint: str, extra: dict | None = None) -> None:
        accounts._emit({"command": "account open", "status": status, "hint": hint, **(extra or {})})
        raise click.exceptions.Exit(1)

    normalized = email.strip().lower() if email is not None else ""
    if not accounts._valid_email(normalized) or len(normalized) > 254:
        fail("invalid_request", "Use --email-link EMAIL with a valid account email address.")
    key = _uuid(request_id) if request_id is not None else str(uuid.uuid4())
    explicit_claim = _uuid(claim_id) if claim_id is not None else None
    if not key or (claim_id is not None and not explicit_claim):
        fail("invalid_request", "Use UUID values for --request-id and --claim-id.")
    state = accounts._read_state()
    try:
        origin = _service_origin(state)
    except (ValueError, TypeError):
        fail("invalid_service_url", "Configure an HTTPS account service URL, or HTTP on loopback, without credentials, query or fragment.")
    continuation = explicit_claim or _pending_claim(state, normalized, origin)
    payload = {"email": normalized, "request_id": key}
    receipt = {
        "email": accounts._mask_email(normalized), "request_id": key, "service_origin": origin,
        "opened_browser": False, "account_state_changed": False,
    }
    if continuation:
        payload["claim_id"] = continuation
        receipt["claim_id"] = continuation
    if dry_run:
        accounts._emit({"command": "account open", "status": "dry_run", **receipt, "would_request_email": True,
                        "hint": "Run without --dry-run to request an email. No email or authentication was performed."})
        return
    code, body, retry = _post(origin, payload)
    if code is None:
        fail("request_unconfirmed", f"The request may have reached the service. Retry the same email and claim with --request-id {key} to avoid duplicate emails.", receipt)
    if code == 429:
        wait = int(retry) if isinstance(retry, str) and retry.isascii() and retry.isdigit() and len(retry) <= 5 else 3600
        fail("rate_limited", "Wait for retry_after_seconds, then retry the same request ID.", {**receipt, "retry_after_seconds": max(1, min(wait, 3600))})
    if code == 409:
        fail("request_conflict", "Use a new --request-id when changing the email or claim.", receipt)
    if code in {404, 503}:
        fail("unavailable", "Email sign-in requests are unavailable on this service. Use the website sign-in page or try later.", receipt)
    if code != 202:
        fail("request_rejected", "The account service rejected the request. Check the service settings and try later.", {**receipt, "http_status": code})
    if (not isinstance(body, dict) or body.get("status") != "accepted"
            or type(body.get("expires_in_seconds")) is not int
            or not 1 <= body["expires_in_seconds"] <= 3600
            or set(body) - {"status", "expires_in_seconds", "hint"}):
        fail("invalid_response", "The service returned an unsupported receipt. Do not treat this as delivery or sign-in success; retry with the same request ID.", receipt)
    accounts._emit({
        "command": "account open", "status": "email_link_requested", **receipt,
        "expires_in_seconds": body["expires_in_seconds"], "delivery": "unconfirmed",
        "hint": "Ask the account owner to open the email and choose Continue with email link. This receipt does not confirm delivery or browser sign-in.",
    })
