"""Clerk Native API email-code setup and account-wide consent.

Clerk client tokens stay in the existing user-only pending claim file. They are
never printed and are discarded after the revocable device grant is exchanged.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

import click

from moviestar import account as accounts


class TerminalError(Exception):
    def __init__(self, code: str):
        self.code = code if re.fullmatch(r"[a-z][a-z0-9_]{0,100}", code) else "authentication_failed"
        super().__init__(self.code)


def _fail(command: str, code: str, hint: str = "Retry the command; pending account state was kept.") -> None:
    accounts._emit({"command": command, "status": "error", "code": code, "hint": hint})
    raise click.exceptions.Exit(1)


def _save(state: dict) -> None:
    try:
        accounts._write_state(state)
    except OSError as error:
        accounts._state_write_error(error, "account verify")


def _origin(url: str) -> str:
    if not isinstance(url, str) or accounts._service_url_error(url):
        raise TerminalError("invalid_authentication_origin")
    parsed = urlsplit(url)
    if parsed.path not in {"", "/"} or parsed.query:
        raise TerminalError("invalid_authentication_origin")
    return f"{parsed.scheme}://{parsed.netloc}"


def _request(state: dict, method: str, endpoint: str, data=None, session_jwt=None) -> dict:
    """No redirects, no cookies, bounded responses, and no raw provider errors."""
    native = endpoint.startswith("/client")
    auth = state.get("pending_claim", {}).get("terminal_auth", {})
    headers = {}
    if native:
        origin = _origin(auth.get("origin", ""))
        # Clerk's Native API takes the client JWT verbatim, not a Bearer prefix.
        headers["Authorization"] = auth.get("client_jwt", "")
        url = f"{origin}/v1{endpoint}?_is_native=1&__clerk_api_version=2026-05-12"
        encoded = urlencode(data or {}).encode() if method != "GET" else None
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        parsed = urlsplit(state.get("claim_url") or accounts._installation_url())
        origin = _origin(f"{parsed.scheme}://{parsed.netloc}")
        url = origin + endpoint
        headers = {"Authorization": f"Bearer {session_jwt}", "x-moviestar-client": accounts._CLIENT_MARKER,
                   "Content-Type": "application/json"}
        encoded = json.dumps(data).encode() if data is not None else None
    request = Request(url, data=encoded, headers=headers, method=method)
    try:
        try:
            response = build_opener(accounts._NoRedirectHandler()).open(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            if native and response.headers.get("Authorization"):
                auth["client_jwt"] = response.headers["Authorization"]
                _save(state)
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise TerminalError("invalid_authentication_response")
            body = accounts._decode_response(raw)
            errors = body.get("errors")
            if errors and isinstance(errors, list) and isinstance(errors[0], dict):
                raise TerminalError(str(errors[0].get("code", "authentication_failed")))
            if response.status >= 300:
                code = body.get("status") if not native else None
                raise TerminalError(str(code or f"http_{response.status}"))
            result = body.get("response", body)
            if not isinstance(result, dict):
                raise TerminalError("invalid_authentication_response")
            return result
    except (URLError, TimeoutError, OSError):
        raise TerminalError("account_service_unavailable") from None


def _resource_path(auth: dict) -> str:
    kind = auth.get("kind")
    resource = auth.get("resource_id", "")
    if kind not in {"sign_ins", "sign_ups"} or not isinstance(resource, str) or not re.fullmatch(r"(?:sia|sua)_[a-zA-Z0-9]+", resource):
        raise TerminalError("invalid_authentication_response")
    return f"/client/{kind}/{resource}"


def _remember(state: dict, resource: dict) -> None:
    auth = state["pending_claim"]["terminal_auth"]
    auth["resource_id"] = resource.get("id") or auth.get("resource_id")
    auth["status"] = resource.get("status")
    auth["missing_fields"] = resource.get("missing_fields", [])
    auth["second_factors"] = [factor.get("strategy") for factor in resource.get("supported_second_factors") or [] if isinstance(factor, dict)]
    if resource.get("status") == "complete":
        session_id = resource.get("created_session_id")
        if not isinstance(session_id, str) or not re.fullmatch(r"sess_[a-zA-Z0-9]+", session_id):
            raise TerminalError("invalid_authentication_response")
        auth["session_id"] = session_id
        auth["step"] = "approved_email"
    elif resource.get("status") == "needs_second_factor":
        auth["step"] = "second_factor"
    elif auth.get("step") != "email_code":
        auth["step"] = "requirements"
    _save(state)


def begin(state: dict, remote: dict) -> None:
    command = "account connect"
    metadata = remote.get("terminal")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("terms"), dict):
        _fail(command, "terminal_setup_unavailable", "The service must enable terminal account setup first.")
    try:
        origin = _origin(metadata.get("frontend_api", ""))
        pending = state["pending_claim"]
        auth = pending.get("terminal_auth")
        if not isinstance(auth, dict) or auth.get("origin") != origin:
            auth = {"origin": origin, "client_jwt": "", "terms": metadata["terms"], "step": "email_code"}
            pending["terminal_auth"] = auth
            _save(state)
            _request(state, "POST", "/client")
            try:
                resource = _request(state, "POST", "/client/sign_ins", {"identifier": pending["email"]})
                auth["kind"] = "sign_ins"
                auth["resource_id"] = resource.get("id")
                factors = resource.get("supported_first_factors") or []
                email_factor = next((factor for factor in factors if factor.get("strategy") == "email_code"), None)
                if not email_factor:
                    raise TerminalError("email_code_not_enabled")
                _request(state, "POST", _resource_path(auth) + "/prepare_first_factor",
                         {"strategy": "email_code", "email_address_id": email_factor["email_address_id"]})
            except TerminalError as error:
                if error.code != "form_identifier_not_found":
                    raise
                resource = _request(state, "POST", "/client/sign_ups", {"email_address": pending["email"]})
                auth["kind"] = "sign_ups"
                auth["resource_id"] = resource.get("id")
                _request(state, "POST", _resource_path(auth) + "/prepare_verification", {"strategy": "email_code"})
            _save(state)
        accounts._emit({"command": command, "status": "verification_pending", "email": accounts._mask_email(pending["email"]),
            "request_id": pending["request_id"], "installation": {"id": state["installation_id"], "name": accounts._installation_name()},
            "terms": metadata["terms"], "hint": "Enter the emailed code with 'moviestar account verify --code-stdin'. Codes are short-lived; use 'moviestar account resend' for another."})
    except TerminalError as error:
        # Retry starts a fresh auth attempt when code delivery did not complete.
        state["pending_claim"].pop("terminal_auth", None)
        _save(state)
        _fail(command, error.code, "Check Clerk Native API and email-code settings, then retry 'moviestar account connect EMAIL --terminal'.")


def _secret(from_stdin: bool, prompt: str) -> str | None:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    if sys.stdin.isatty():
        return click.prompt(prompt, hide_input=True, err=True)
    return None


def _pending() -> tuple[dict, dict]:
    state = accounts._read_state()
    pending = state.get("pending_claim", {})
    auth = pending.get("terminal_auth")
    if not isinstance(auth, dict):
        _fail("account verify", "no_terminal_request", "Run 'moviestar account connect EMAIL --terminal' first.")
    try:
        expiry = datetime.fromisoformat(str(pending.get("expires_at")).replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError()
    except ValueError:
        _fail("account verify", "expired", "Start a new terminal account request.")
    return state, auth


@click.command("verify")
@click.option("--code-stdin", is_flag=True, help="Read the email or second-factor code from stdin instead of a hidden prompt.")
@click.option("--password-stdin", is_flag=True, help="Set a required signup password from stdin after email verification.")
@click.option("--second-factor", type=click.Choice(["totp", "backup_code"]), help="Complete a required Clerk second factor.")
@click.option("--handle", help="Public Moviestar handle for a new account.")
@click.option("--accept-terms", metavar="VERSION", help="Explicit human acceptance of the displayed terms version.")
@click.option("--age-18-or-older", is_flag=True, help="Explicit human confirmation that they are at least 18.")
@click.option("--approve-installation", is_flag=True, help="Explicitly authorize this CLI installation.")
def verify(code_stdin, password_stdin, second_factor, handle, accept_terms, age_18_or_older, approve_installation):
    """Verify an email code and finish setup without opening a browser.

    Human confirmation is required for age, terms, and installation access.
    Never infer these decisions or copy authentication codes into a project.
    """
    state, auth = _pending()
    try:
        if code_stdin and password_stdin:
            raise TerminalError("choose_one_stdin_secret")
        path = _resource_path(auth)
        if auth["step"] in {"email_code", "second_factor"}:
            second = auth["step"] == "second_factor"
            if second and (not second_factor or second_factor not in auth.get("second_factors", [])):
                accounts._emit({"command": "account verify", "status": "second_factor_required", "strategies": auth.get("second_factors", []), "hint": "Use --second-factor totp or --second-factor backup_code with --code-stdin."})
                return
            code = _secret(code_stdin, "Authentication code" if second else "Email verification code")
            if not code:
                accounts._emit({"command": "account verify", "status": "code_required", "hint": "Provide the human's emailed code using --code-stdin or a terminal prompt."})
                return
            if second:
                suffix, strategy = "/attempt_second_factor", second_factor
            elif auth["kind"] == "sign_ups":
                suffix, strategy = "/attempt_verification", "email_code"
            else:
                suffix, strategy = "/attempt_first_factor", "email_code"
            resource = _request(state, "POST", path + suffix, {"strategy": strategy, "code": code})
            auth["step"] = "requirements"
            _remember(state, resource)
        if auth["step"] == "requirements" and auth["kind"] == "sign_ups":
            updates = {}
            if password_stdin and "password" in auth.get("missing_fields", []):
                updates["password"] = _secret(True, "New password")
            if "username" in auth.get("missing_fields", []) and handle:
                updates["username"] = handle.lstrip("@")
            if "legal_accepted" in auth.get("missing_fields", []) and accept_terms == auth["terms"].get("version") and age_18_or_older:
                updates["legal_accepted"] = "true"
            if updates:
                _remember(state, _request(state, "PATCH", path, updates))
        if not auth.get("session_id"):
            status = "second_factor_required" if auth["step"] == "second_factor" else "signup_requirements"
            accounts._emit({"command": "account verify", "status": status, "missing_fields": auth.get("missing_fields", []), "strategies": auth.get("second_factors", []),
                "hint": "Complete Clerk's required fields. A required signup password can be set with --password-stdin; MFA requires --second-factor and --code-stdin."})
            return
        session = auth["session_id"]
        token = _request(state, "POST", f"/client/sessions/{session}/tokens").get("jwt")
        if not isinstance(token, str) or not token:
            raise TerminalError("invalid_authentication_response")
        claim_path = f"/api/v1/account/claims/{state['pending_claim']['request_id']}"
        claim = _request(state, "GET", claim_path, session_jwt=token)
        if claim.get("installation", {}).get("id") != state["installation_id"]:
            raise TerminalError("installation_mismatch")
        terms = claim.get("terms", {})
        needs_consent = claim.get("consent_required") is not False
        if accept_terms and accept_terms != terms.get("version"):
            raise TerminalError("policy_changed")
        if not approve_installation or (needs_consent and (not age_18_or_older or not accept_terms)) or (claim.get("needs_handle") and not handle):
            accounts._emit({"command": "account verify", "status": "approval_required", "installation": claim["installation"], "handle": claim.get("handle"),
                "needs_handle": claim.get("needs_handle"), "consent_required": needs_consent, "terms": terms,
                "hint": "Ask the human to review the policies and installation. Then use --handle NAME if needed, --accept-terms VERSION --age-18-or-older when consent is required, and --approve-installation."})
            return
        approved = _request(state, "POST", claim_path, {"handle": handle or "", "terms_accepted": True, "age_confirmed": age_18_or_older,
            "terms_version": terms.get("version"), "consent_version": terms.get("consent_version"), "installation_approved": True}, token)
        if approved.get("status") != "connected":
            raise TerminalError("installation_not_approved")
        try:
            _request(state, "POST", f"/client/sessions/{session}/end")
        except TerminalError:
            pass  # Device exchange is durable; cleanup must not block it.
        # Reuse the existing private claim exchange and credential store.
        state["pending_claim"].pop("terminal_auth", None)
        _save(state)
        click.get_current_context().invoke(accounts.status)
    except TerminalError as error:
        _fail("account verify", error.code)


@click.command("resend")
def resend():
    """Resend a terminal email code, subject to Clerk's cooldown and limits."""
    state, auth = _pending()
    try:
        if auth.get("step") != "email_code":
            raise TerminalError("email_already_verified")
        path = _resource_path(auth)
        if auth["kind"] == "sign_ups":
            _request(state, "POST", path + "/prepare_verification", {"strategy": "email_code"})
        else:
            # Clerk retains the prepared email factor on the sign-in resource.
            resource = _request(state, "GET", path)
            factor = next((f for f in resource.get("supported_first_factors") or [] if f.get("strategy") == "email_code"), None)
            if not factor:
                raise TerminalError("email_code_not_enabled")
            _request(state, "POST", path + "/prepare_first_factor", {"strategy": "email_code", "email_address_id": factor["email_address_id"]})
        accounts._emit({"command": "account resend", "status": "verification_pending", "hint": "Enter the newest emailed code with 'moviestar account verify --code-stdin'."})
    except TerminalError as error:
        _fail("account resend", error.code)


@click.command("terms")
@click.option("--accept", metavar="VERSION", help="Accept this exact version of the displayed account terms.")
@click.option("--age-18-or-older", is_flag=True, help="Confirm the human is at least 18.")
def terms(accept, age_18_or_older):
    """Review account terms, or explicitly accept them for all hosted videos."""
    if bool(accept) != age_18_or_older:
        _fail("account terms", "explicit_consent_required", "Use --accept VERSION together with --age-18-or-older after the human reviews the terms.")
    state = accounts._read_state()
    credential = accounts._credential_for_connection(state)
    if not credential:
        _fail("account terms", "not_connected", "Connect an account first.")
    try:
        result = _request(state, "GET", "/api/v1/account/consent", session_jwt=credential)
        if accept:
            policy = result.get("terms", {})
            if accept != policy.get("version"):
                raise TerminalError("policy_changed")
            result = _request(state, "POST", "/api/v1/account/consent", {"terms_accepted": True, "age_confirmed": True,
                "terms_version": accept, "consent_version": policy.get("consent_version")}, credential)
        accounts._emit({"command": "account terms", **result})
    except TerminalError as error:
        _fail("account terms", error.code)
