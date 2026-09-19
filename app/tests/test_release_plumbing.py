"""Contracts that keep MovieStar releases deliberate and reproducible."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
VERSION_CHECK = ROOT / "bin" / "check-release-version"
RELEASE_NOTES = ROOT / "docs" / "releases" / "v0.7.0.md"
PYPI_PUBLISH_ACTION_SHA = "dc37677b2e1c63e2034f94d8a5b11f265b73ba33"


def test_release_version_check_accepts_only_the_package_version() -> None:
    matching = subprocess.run(
        [sys.executable, str(VERSION_CHECK), "v0.7.0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    mismatch = subprocess.run(
        [sys.executable, str(VERSION_CHECK), "v0.7.1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert matching.returncode == 0, matching.stderr
    assert "v0.7.0 matches package version 0.7.0" in matching.stdout
    assert mismatch.returncode == 1
    assert "release tag v0.7.1 does not match package version 0.7.0" in mismatch.stderr


def test_publish_workflow_is_release_only_and_uses_trusted_publishing() -> None:
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert "types: [published]" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "name: pypi" in workflow
    assert "url: https://pypi.org/project/moviestar/" in workflow
    assert "contents: read" in workflow
    assert "id-token: write" in workflow
    assert 'python bin/check-release-version "$GITHUB_REF_NAME"' in workflow
    assert "python -m build app" in workflow
    assert "python -m twine check app/dist/*" in workflow
    assert (
        "uses: pypa/gh-action-pypi-publish@" + PYPI_PUBLISH_ACTION_SHA
        in workflow
    )
    assert "packages-dir: app/dist" in workflow


def test_v070_release_notes_capture_the_open_source_transition() -> None:
    notes = RELEASE_NOTES.read_text(encoding="utf-8")

    assert "MovieStar v0.7.0" in notes.splitlines()[0]
    assert "Apache License 2.0" in notes
    assert "v0.6.0" in notes
    assert "pip install --upgrade \"moviestar==0.7.0\"" in notes
    assert "moviestar doctor" in notes


def test_public_readme_does_not_claim_the_unreleased_build_is_on_pypi() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "v0.7.0 has not reached PyPI yet" in readme
    assert (
        'pip install "moviestar @ '
        'git+https://github.com/jdilla1277/moviestar.git@main#subdirectory=app"'
        in readme
    )
