"""Validate the hosted finished-video plan read without inferring entitlements."""

from datetime import datetime


def _number(value: object) -> bool:
    return type(value) is int and 0 <= value <= 9_007_199_254_740_991


def _date(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def _optional_date(value: object) -> bool:
    return value is None or _date(value)


def _choice(*values):
    return lambda value: isinstance(value, str) and value in values


def _fields(value: object, fields: dict) -> dict | None:
    if not isinstance(value, dict) or any(name not in value or not check(value[name]) for name, check in fields.items()):
        return None
    return {name: value[name] for name in fields}


def public_plan(value: object) -> dict | None:
    """Return only known v1 fields; malformed or newer contracts stay unknown.

    The service calculates capacity, eligibility and dates. This parser checks
    transport types and never recalculates billing or quota policy locally.
    """
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return None
    result = _fields(value, {
        "schema_version": lambda v: v == 1, "observed_at": _date, "revision": _number,
        "state_updated_at": _optional_date, "plan": _choice("free", "plus"),
    })
    if result is None:
        return None
    groups = {
        "limits": dict.fromkeys(("stored_videos", "total_bytes", "per_video_bytes"), _number),
        "usage": dict.fromkeys(("stored_videos", "stored_bytes", "pending_uploads", "pending_bytes", "used_videos", "used_bytes"), _number),
        "remaining": dict.fromkeys(("videos", "bytes"), _number),
        "upload": {"allowed": lambda v: type(v) is bool,
                   "reason": _choice("capacity_available", "video_limit", "storage_limit", "existing_video_over_limit", "payment_grace", "account_deleting"),
                   "max_file_bytes": _number},
        "lifecycle": {"phase": _choice("free", "paid", "payment_grace", "downgrade_live", "paused", "deletion_due", "deleting"),
                      **dict.fromkeys(("plus_ends_at", "grace_ends_at", "links_pause_at", "files_delete_at"), _optional_date)},
        "billing": {"status": _choice("none", "paid", "payment_grace", "ended", "refunded", "deleting"),
                    "cadence": lambda v: v is None or _choice("monthly", "annual")(v),
                    "paid_through": _optional_date, "renewal_at": _optional_date,
                    "cancel_at_period_end": lambda v: type(v) is bool},
    }
    for name, fields in groups.items():
        group = _fields(value.get(name), fields)
        if group is None:
            return None
        result[name] = group
    billing = value["billing"]
    if "scheduled_change" not in billing:
        return None
    scheduled = billing["scheduled_change"]
    if scheduled is not None:
        scheduled = _fields(scheduled, {"cadence": _choice("monthly", "annual"), "effective_at": _date})
        if scheduled is None:
            return None
    result["billing"]["scheduled_change"] = scheduled
    over = value.get("over_quota")
    if not isinstance(over, list) or len(over) > 3 or not all(_choice("video_count", "total_bytes", "per_video_bytes")(item) for item in over):
        return None
    result["over_quota"] = list(over)
    return result


def plan_fields(response: dict) -> dict:
    plan = public_plan(response.get("plan")) if response.get("plan_status") in (None, "ok") else None
    return {"plan_status": "ok" if plan is not None else "unavailable", "plan": plan}


_CAPACITY_ERRORS = {
    "file_too_large": "This file exceeds your account's per-video limit. Run 'moviestar account status' to check the limit, then export a smaller MP4.",
    "video_limit": "All video slots are in use. Run 'moviestar account status' to inspect stored videos and pending reservations; delete a hosted video to make room.",
    "storage_limit": "The file does not fit your remaining storage. Run 'moviestar account status' and delete hosted videos to make room.",
    "existing_video_over_limit": "A stored video exceeds the current per-video limit. Run 'moviestar account status' and ask the owner to remove that video or manage the account.",
    "over_quota": "The account exceeds its current allowance. Run 'moviestar account status' to inspect the limits and reduce stored usage.",
    "payment_grace": "A renewal payment needs attention and new uploads are blocked. Run 'moviestar account open' to manage the account with the human.",
    "account_deleting": "This account is being deleted and cannot accept uploads. Run 'moviestar account open' to inspect the account with the human.",
}


def capacity_failure(code: int, response: dict) -> tuple[str, str, dict] | None:
    """Keep a known refusal and safe usage fields, never provider diagnostics."""
    status = response.get("status")
    if code not in {400, 409} or not isinstance(status, str) or status not in _CAPACITY_ERRORS:
        return None
    quota = _fields(response.get("quota"), {"used_videos": _number, "used_bytes": _number})
    return status, _CAPACITY_ERRORS[status], {"quota": quota} if quota is not None else {}
