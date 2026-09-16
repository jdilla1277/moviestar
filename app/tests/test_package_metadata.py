"""Contracts for MovieStar's first Apache-2.0 package release."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tomllib


APP_ROOT = Path(__file__).resolve().parents[1]
APACHE_2_LICENSE_SHA256 = (
    "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
)


def _pyproject() -> dict:
    return tomllib.loads((APP_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_v070_is_declared_as_the_first_apache_release():
    pyproject = _pyproject()

    assert pyproject["project"]["version"] == "0.7.0"
    assert pyproject["project"]["license"] == "Apache-2.0"


def test_package_metadata_routes_users_to_public_project_surfaces():
    pyproject = _pyproject()

    assert pyproject["project"]["urls"] == {
        "Homepage": "https://trymoviestar.com",
        "Documentation": "https://github.com/jdilla1277/moviestar/tree/main/docs",
        "Repository": "https://github.com/jdilla1277/moviestar",
        "Issues": "https://github.com/jdilla1277/moviestar/issues",
    }


def test_build_metadata_declares_both_distributed_license_files():
    pyproject = _pyproject()

    assert pyproject["build-system"]["requires"] == ["setuptools>=77.0.3"]
    assert pyproject["project"]["license-files"] == [
        "LICENSE",
        "src/moviestar/fonts/OFL-LICENSE.txt",
    ]


def test_package_license_is_the_canonical_apache_2_text():
    license_bytes = (APP_ROOT / "LICENSE").read_bytes()

    assert hashlib.sha256(license_bytes).hexdigest() == APACHE_2_LICENSE_SHA256


def test_sdist_excludes_repository_tests():
    manifest = (APP_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert manifest == "prune tests\n"
