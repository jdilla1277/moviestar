"""Contracts for the resolved dependency-license policy check."""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "bin" / "check-dependency-licenses"


def _load_checker():
    loader = SourceFileLoader("dependency_license_check", str(SCRIPT))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


checker = _load_checker()


def _record(
    *,
    name="example",
    version="1.0",
    expression="",
    license_value="",
    classifiers=(),
):
    return {
        "name": name,
        "version": version,
        "license_expression": expression,
        "license": license_value,
        "classifiers": list(classifiers),
    }


def _policy(*, reviewed=None):
    return {
        "allowed_simple": ["Apache-2.0", "BSD-3-Clause", "MIT"],
        "reviewed": reviewed or {},
    }


def test_simple_permissive_spdx_expression_is_allowed():
    ok, detail = checker.evaluate_record(
        _record(expression="MIT"),
        _policy(),
    )

    assert ok is True
    assert detail == "allowed: MIT"


def test_compound_expression_requires_explicit_review():
    record = _record(name="compound", expression="Apache-2.0 AND MIT")

    ok, detail = checker.evaluate_record(record, _policy())

    assert ok is False
    assert "explicit review" in detail


def test_weak_copyleft_requires_explicit_review():
    record = _record(name="weak", license_value="MPL-2.0")

    ok, detail = checker.evaluate_record(record, _policy())

    assert ok is False
    assert "explicit review" in detail


def test_missing_metadata_requires_upstream_evidence():
    ok, detail = checker.evaluate_record(_record(name="missing"), _policy())

    assert ok is False
    assert "no license metadata" in detail


def test_reviewed_metadata_fingerprint_is_allowed():
    record = _record(name="reviewed", expression="Apache-2.0 AND MIT")
    fingerprint = checker.metadata_fingerprint(record)
    policy = _policy(
        reviewed={
            "reviewed": {
                "metadata_sha256": fingerprint,
                "license": "Apache-2.0 AND MIT",
                "evidence": "https://example.com/license",
            }
        }
    )

    ok, detail = checker.evaluate_record(record, policy)

    assert ok is True
    assert detail == "reviewed: Apache-2.0 AND MIT"


def test_review_fails_closed_when_license_metadata_changes():
    original = _record(name="reviewed", expression="Apache-2.0 AND MIT")
    changed = _record(name="reviewed", expression="GPL-3.0-only")
    policy = _policy(
        reviewed={
            "reviewed": {
                "metadata_sha256": checker.metadata_fingerprint(original),
                "license": "Apache-2.0 AND MIT",
                "evidence": "https://example.com/license",
            }
        }
    )

    ok, detail = checker.evaluate_record(changed, policy)

    assert ok is False
    assert "metadata changed" in detail


def test_review_of_missing_metadata_is_bound_to_the_reviewed_version():
    original = _record(name="missing", version="1.0")
    changed = _record(name="missing", version="2.0")
    policy = _policy(
        reviewed={
            "missing": {
                "version": "1.0",
                "metadata_sha256": checker.metadata_fingerprint(original),
                "license": "MIT",
                "evidence": "https://example.com/license",
            }
        }
    )

    ok, detail = checker.evaluate_record(changed, policy)

    assert ok is False
    assert "reviewed version changed" in detail


def test_preflight_runs_dependency_license_check_after_tests():
    preflight = (REPO_ROOT / "bin" / "preflight").read_text(encoding="utf-8")

    checker_call = (
        '"$REPO_ROOT/app/.venv/bin/python" '
        '"$REPO_ROOT/bin/check-dependency-licenses"'
    )
    assert checker_call in preflight
    assert preflight.index('"$REPO_ROOT/bin/test"') < preflight.index(
        checker_call
    )


def test_unknown_requested_extra_is_rejected():
    assert checker.unknown_extras({"diarize"}, {"diarize"}) == set()
    assert checker.unknown_extras({"diarize"}, {"diarize", "typo"}) == {"typo"}


def test_package_ci_runs_dependency_license_check():
    workflow = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )

    assert "bin/check-dependency-licenses" in workflow
