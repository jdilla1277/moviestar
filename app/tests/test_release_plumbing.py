"""Contracts that keep Moviestar releases deliberate and reproducible."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
VERSION_CHECK = ROOT / "bin" / "check-release-version"
PREFLIGHT_MODE = ROOT / "bin" / "select-preflight-mode"
RELEASE_NOTES = ROOT / "docs" / "releases" / "v0.7.0.md"
HOTFIX_NOTES = ROOT / "docs" / "releases" / "v0.7.1.md"
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

    assert "Moviestar v0.7.0" in notes.splitlines()[0]
    assert "Apache License 2.0" in notes
    assert "v0.6.0" in notes
    assert "pip install --upgrade \"moviestar==0.7.0\"" in notes
    assert "moviestar doctor" in notes


def test_public_install_guides_use_the_released_pypi_package() -> None:
    for path in (ROOT / "README.md", ROOT / "docs" / "getting-started.md"):
        text = path.read_text(encoding="utf-8")

        assert "python -m pip install --upgrade moviestar" in text
        assert "git+https://" not in text
        assert "has not reached PyPI" not in text
        assert "earlier v0.6.0 release" not in text


def test_preflight_mode_keeps_docs_and_skill_changes_focused() -> None:
    def select(*paths: str) -> str:
        result = subprocess.run(
            [sys.executable, str(PREFLIGHT_MODE)],
            cwd=ROOT,
            input="\n".join(paths),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    assert (
        select(
            "README.md",
            "docs/getting-started.md",
            "plugins/moviestar/skills/moviestar/SKILL.md",
            "app/tests/test_public_docs_and_skill.py",
            "app/tests/test_release_plumbing.py",
        )
        == "docs"
    )
    assert select("docs/getting-started.md", "app/tests/test_cli.py") == "slow"
    assert select("app/src/moviestar/cli.py") == "slow"
    assert select("bin/preflight") == "fast"


def test_docs_ci_runs_both_public_documentation_contracts() -> None:
    workflow = TEST_WORKFLOW.read_text(encoding="utf-8")

    assert "app/tests/test_public_docs_and_skill.py" in workflow
    assert "app/tests/test_release_plumbing.py" in workflow


def test_package_ci_caches_pip_downloads() -> None:
    # A slow PyPI mirror once stretched dependency install to 18 minutes and
    # the job hit its 25-minute timeout before the tests finished. Caching
    # pip downloads keeps install time independent of PyPI throughput.
    workflow = TEST_WORKFLOW.read_text(encoding="utf-8")
    test_job = workflow.split("\n  test:\n", 1)[1]

    assert "cache: pip" in test_job
    assert "cache-dependency-path: app/pyproject.toml" in test_job


def test_v071_hotfix_notes_explain_the_transcription_fix() -> None:
    notes = HOTFIX_NOTES.read_text(encoding="utf-8")

    assert "Moviestar v0.7.1" in notes.splitlines()[0]
    assert "PyAV 19" in notes
    assert "metadata_errors" in notes
    assert "pip install --upgrade \"moviestar==0.7.1\"" in notes
    assert "SYSTRAN/faster-whisper/issues/1492" in notes
