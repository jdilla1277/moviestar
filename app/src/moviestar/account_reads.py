"""Read the hosted account meter and grouped Activity without local policy math."""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urlencode, urlsplit

import click

_COUNT_MAX = 2**53 - 1
_UUID = re.compile(r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$", re.I)
_CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_ERRORS = {
    "credential_invalid": "Run 'moviestar account status' and reconnect this installation if needed.",
    "account_required": "Complete your account setup on the service website.",
    "account_setup_required": "Complete your account setup on the service website.",
    "account_consent_required": "Run 'moviestar account terms' and review the current terms, then retry.",
    "invalid_request": "Use the command help for supported pagination and filters.",
    "account_unavailable": "Account data is unavailable. Retry later; missing usage is not zero. Your connection has not been removed.",
    "invalid_response": "The service returned unsupported or incomplete account data. Update compatible client/service versions and retry; no balance was inferred.",
}


def _fail(code: str) -> None:
    click.echo(json.dumps({"error": {"code": code, "message": _ERRORS[code],
                                    "retryable": code == "account_unavailable"}}, indent=2))
    raise click.exceptions.Exit(1)


def _count(value):
    return type(value) is int and 0 <= value <= _COUNT_MAX


def _date(value):
    if not isinstance(value, str) or len(value) > 64 or not re.match(r"^\d{4}-\d\d-\d\dT", value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _optional(check):
    return lambda value: value is None or check(value)


def _text(value):
    return isinstance(value, str) and len(value) <= 240


def _id(value):
    return isinstance(value, str) and _UUID.fullmatch(value) is not None


def _fields(value, checks):
    if not isinstance(value, dict) or any(key not in value or not check(value[key]) for key, check in checks.items()):
        return None
    return {key: value[key] for key in checks}


def _usage(value):
    result = _fields(value, {"schema_version": lambda v: type(v) is int and v == 1,
                            "observed_at": _date, "plan": lambda v: v in ("free", "plus"),
                            "period_start": _date, "resets_at": _date})
    if result is None:
        return None
    groups = {
        "limits": ("processing_ms", "output_bytes", "concurrent_jobs"),
        "usage": ("processing_ms", "output_bytes", "reserved_processing_ms", "reserved_output_bytes", "executing_jobs"),
        "remaining": ("processing_ms", "output_bytes"),
    }
    for name, keys in groups.items():
        group = _fields(value.get(name), dict.fromkeys(keys, _count))
        if group is None:
            return None
        result[name] = group
    storage = value.get("storage")
    if not isinstance(storage, dict):
        return None
    result["storage"] = {}
    for name, keys in {
        "limits": ("stored_assets", "total_bytes", "per_file_bytes"),
        "usage": ("stored_assets", "used_assets", "stored_bytes", "used_bytes"),
        "remaining": ("assets", "bytes"),
    }.items():
        group = _fields(storage.get(name), dict.fromkeys(keys, _count))
        if group is None:
            return None
        result["storage"][name] = group
    occupied = result["storage"]["usage"]
    if occupied["used_assets"] < occupied["stored_assets"] or occupied["used_bytes"] < occupied["stored_bytes"]:
        return None
    return result


def _compute_estimate(value):
    if value is None:
        return None
    money = lambda v: isinstance(v, str) and re.fullmatch(r"\d{1,20}(?:\.\d{1,12})?", v) is not None
    result = _fields(value, {
        "status": lambda v: v in ("estimated", "pending", "unavailable"),
        "amount_usd": _optional(money), "base_usd": _optional(money),
        "reason": lambda v: v in (None, "pricing_missing", "missing_timing", "running", "awaiting_attempt", "incomplete_pricing"),
        "scope": lambda v: v == "compute", "method": lambda v: v == "attempt_occupancy_v1",
        "policy_version": _optional(_id),
        "adjustment_multiplier": _optional(lambda v: isinstance(v, str) and re.fullmatch(r"\d+(?:\.\d+)?", v) is not None),
    })
    if result is None:
        return False
    valid = (result["amount_usd"] is not None and result["base_usd"] is not None and result["reason"] is None) if result["status"] == "estimated" else (result["amount_usd"] is None and result["base_usd"] is None and result["reason"] is not None)
    if not valid:
        return False
    if "attempt_count" in value:
        if not _count(value["attempt_count"]):
            return False
        result["attempt_count"] = value["attempt_count"]
    return result


def _entry(value, *, task=False):
    result = _fields(value, {
        "id": _id, "operation_id": _id, "kind": lambda v: _text(v) and 0 < len(v) <= 64,
        "state": lambda v: _text(v) and 0 < len(v) <= 64, "created_at": _date,
        "finished_at": _optional(_date), "project_id": _optional(_id), "asset_id": _optional(_id),
        "parent_operation_id": _optional(_id), "elapsed_ms": _optional(_count),
        "failure_code": _optional(lambda v: _text(v) and len(v) <= 64),
        "actual_cost_usd": _optional(lambda v: isinstance(v, str) and re.fullmatch(r"\d{1,20}(?:\.\d{1,6})?", v) is not None),
        "cost_receipts": _count, "historical": lambda v: type(v) is bool,
    })
    if result is None:
        return None
    if "estimated_compute" in value:
        estimate = _compute_estimate(value["estimated_compute"])
        if estimate is False:
            return None
        result["estimated_compute"] = estimate
    if "cost_status" in value:
        if value["cost_status"] not in ("unmapped", "pending", "missing", "partial", "compute_reconciled", "needs_reconciliation"):
            return None
        result["cost_status"] = value["cost_status"]
    if not task:
        return result
    for name, check in {"resource_name": _optional(_text), "project_name": _optional(_text), "attempt_count": _count}.items():
        if name in value:
            if not check(value[name]):
                return None
            result[name] = value[name]
    if "attempts" in value:
        attempts = value["attempts"]
        if not isinstance(attempts, list) or len(attempts) > 20:
            return None
        parsed = [_entry(a) for a in attempts]
        if any(a is None for a in parsed):
            return None
        result["attempts"] = parsed
    if "rendered_output" in value:
        output = value["rendered_output"]
        result["rendered_output"] = None
        if output is not None:
            parsed = _fields(output, {**dict.fromkeys(("project_id", "revision_id", "render_job_id", "artifact_id"), _id),
                                     "artifact_sha256": lambda v: isinstance(v, str) and re.fullmatch("[a-f0-9]{64}", v) is not None})
            if parsed is None:
                return None
            result["rendered_output"] = parsed
    if "cloud_usage" in value:
        usage = value["cloud_usage"]
        result["cloud_usage"] = None
        if usage is not None:
            parsed = _fields(usage, {"period_start": _date,
                "state": lambda v: v in ("reserved", "running", "succeeded", "released"),
                **dict.fromkeys(("processing_ms", "output_bytes", "reserved_processing_ms", "reserved_output_bytes"), _count)})
            if parsed is None:
                return None
            result["cloud_usage"] = parsed
    return result


def _public_response(body, origin, *, activity):
    if not isinstance(body, dict) or body.get("local_execution_free") is not True or body.get("automatic_overage_charges") is not False:
        return None
    links = body.get("links")
    expected = {"usage_url": origin + "/activity", "activity_url": origin + "/activity?view=activity",
                "manage_assets_url": origin + "/account/assets"}
    if not isinstance(links, dict) or any(links.get(k) != v for k, v in expected.items()):
        return None
    support = _fields(body.get("support"), {"email": lambda v: isinstance(v, str) and re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", v) is not None,
                                            "url": lambda v: isinstance(v, str) and len(v) <= 4096})
    if support is None or not support["url"].startswith("mailto:" + support["email"] + "?"):
        return None
    result = {"links": expected, "support": support, "local_execution_free": True, "automatic_overage_charges": False}
    if not activity:
        usage = _usage(body.get("usage"))
        return None if usage is None else {"usage": usage, **result}
    items, cursor = body.get("items"), body.get("next_cursor")
    if "next_cursor" not in body or not isinstance(items, list) or len(items) > 50 or (cursor is not None and not (isinstance(cursor, str) and _CURSOR.fullmatch(cursor))):
        return None
    parsed = [_entry(item, task=True) for item in items]
    if any(item is None for item in parsed) or body.get("provider_costs_are_customer_charges") is not False:
        return None
    return {"items": parsed, "next_cursor": cursor, **result, "provider_costs_are_customer_charges": False}


def _read(command, *, activity=False, limit=20, cursor=None, cloud_only=False):
    # Share the established installation/origin and no-redirect HTTP helpers.
    # No capability preflight or media operation is necessary for these reads.
    from moviestar.cloud import _connection, _request
    endpoint, credential = _connection(command)
    parsed = urlsplit(endpoint)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = "/api/v1/account/usage"
    if activity:
        query = {"limit": limit}
        if cursor:
            query["cursor"] = cursor
        if cloud_only:
            query["type"] = "cloud"
        path = "/api/v1/account/activity/tasks?" + urlencode(query)
    status, body = _request(origin + path, credential)
    if status != 200:
        code = body.get("error", {}).get("code") if isinstance(body.get("error"), dict) else None
        _fail("credential_invalid" if status == 401 else code if isinstance(code,str) and code in _ERRORS else "account_unavailable")
    result = _public_response(body, origin, activity=activity)
    if result is None:
        _fail("invalid_response")
    click.echo(json.dumps(result, indent=2))


def register_account_reads(account):
    @account.command("usage")
    def usage():
        """Read storage and this month's Cloud allowance for my connected account.

        Shows the same used, reserved, remaining and UTC reset values as Web
        Usage. Local jobs are free. No job, reservation or automatic overage
        charge is created by this read. Missing data is an error, not zero.

        Next: 'moviestar account activity --cloud' explains the tasks behind
        usage. Returned links open Web Usage, assets and a support email
        composer; opening the composer does not send email.
        """
        _read("account usage")

    @account.command("activity")
    @click.option("--limit", type=click.IntRange(1, 50), default=20, show_default=True, help="Tasks per page, not attempt rows.")
    @click.option("--cursor", help="Opaque next_cursor from the previous page; retain it unchanged.")
    @click.option("--cloud", "cloud_only", is_flag=True, help="Show Cloud jobs only.")
    def activity(limit, cursor, cloud_only):
        """Read task history, allowance and nested attempts without executing work.

        Tasks with rendered_output inspect an exact rendered video, using the
        existing asset_screenshot kind. Their output_bytes counts the JPEG plus
        job metadata; image.size_bytes is the downloadable JPEG alone.

        Screenshot estimated_compute is a compute-only estimate, separate from
        actual provider receipts. Read its status/reason and never add the job
        total to its nested attempt amounts.

        Provider costs are diagnostic operating costs, never customer charges;
        null means unknown, not zero. Attempts do not duplicate task usage.
        Historical records are not a current-month usage total. Use 'account
        usage' for current remaining capacity and the exact reset date.
        """
        if cursor is not None and not _CURSOR.fullmatch(cursor):
            _fail("invalid_request")
        _read("account activity", activity=True, limit=limit, cursor=cursor, cloud_only=cloud_only)
