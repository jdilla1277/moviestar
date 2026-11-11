"""Opt-in acceptance against a normal, isolated base-wheel installation.

Set MOVIESTAR_WHEEL_TEST_PYTHON to that environment's Python executable.
The service and installation identity are synthetic; the CLI is a fresh process.
"""

import json
import os
from pathlib import Path
import subprocess

import pytest

from .test_cloud_projects import PROJECT, TOKEN, service  # noqa: F401 -- pytest fixture


WHEEL_PYTHON = os.environ.get("MOVIESTAR_WHEEL_TEST_PYTHON")
pytestmark = pytest.mark.skipif(not WHEEL_PYTHON, reason="requires an isolated base wheel environment")


def test_base_wheel_account_and_cloud_journey_without_media_dependencies(service, tmp_path, monkeypatch):
    # Preserve the venv executable path: resolving its symlink selects the host Python.
    python = str(Path(WHEEL_PYTHON).absolute())
    monkeypatch.setenv("PATH", str(tmp_path / "no-binaries"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "models"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "models" / "hub"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("PYTHONHOME", raising=False)

    def run(*args):
        result = subprocess.run([python, "-I", *args], cwd=tmp_path,
                                capture_output=True, text=True, timeout=30)
        assert TOKEN not in result.stdout + result.stderr
        return result

    result = run("-c", """
import importlib.metadata as m, json, pathlib, shutil, moviestar
print(json.dumps({'packages': [d.metadata['Name'].lower().replace('_', '-') for d in m.distributions()],
                  'module': str(pathlib.Path(moviestar.__file__).resolve()),
                  'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe')}))
""")
    assert result.returncode == 0, result.stderr
    installed = json.loads(result.stdout)
    assert {"av", "faster-whisper", "ctranslate2", "onnxruntime", "torch", "numpy"}.isdisjoint(installed["packages"])
    assert "moviestar" in installed["packages"]
    assert Path(__file__).resolve().parents[1] / "src" not in Path(installed["module"]).parents
    assert "site-packages" in Path(installed["module"]).parts
    assert installed["ffmpeg"] is None and installed["ffprobe"] is None
    for args in [("--help",), ("cloud", "--help"), ("account", "--help")]:
        result = run("-m", "moviestar", *args)
        assert result.returncode == 0, result.stderr

    before = service[1].read_bytes()
    # No connection or network is needed to inspect a disconnected account.
    service[1].write_text("{}")
    result = run("-m", "moviestar", "account", "status")
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "not_connected"
    assert service[0]["calls"] == []
    service[1].write_bytes(before)

    def command(*args):
        result = run("-m", "moviestar", "cloud", *args)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    assert command("capabilities")["project_access"] == "owner_only"
    created = command("projects", "create", "Wheel project")
    assert created["project"]["id"] == PROJECT
    assert created["idempotency_key"]
    assert command("projects", "list")["items"][0]["id"] == PROJECT
    assert command("projects", "show", PROJECT) == created["project"]
    assert command("projects", "create", "Wheel project", "--idempotency-key", created["idempotency_key"])["project"] == created["project"]
    assert all(path.startswith("/api/v1/account/cloud/") or path.startswith("/api/v1/account/cloud?")
               for _, path, _ in service[0]["calls"])

    result = run("-m", "moviestar", "models", "pull", "tiny")
    assert result.returncode == 1
    error = json.loads(result.stdout)
    assert error["code"] == "transcription_not_installed"
    assert "moviestar[transcribe]" in error["hint"]
    assert service[1].read_bytes() == before
    assert not (tmp_path / "models").exists()
    assert not (tmp_path / "moviestar").exists()
