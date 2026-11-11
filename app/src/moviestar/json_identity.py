"""Deterministic JSON identity without changing the authored document."""

from __future__ import annotations

import json
from math import isfinite


def _identity_value(value: object) -> object:
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Identity requires finite JSON numbers.")
        # Preserve exact equality, including negative zero. Never cast ints to
        # float: large integers and close fractional values must stay distinct.
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {key: _identity_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_identity_value(item) for item in value]
    return value


def canonical_json(value: object) -> str:
    """Serialize identity-only data with equivalent integral numbers unified.

    Booleans retain their JSON type; fractional floats retain their full Python
    JSON precision. Neither the input nor persisted authoring values are changed.
    """
    return json.dumps(_identity_value(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)
