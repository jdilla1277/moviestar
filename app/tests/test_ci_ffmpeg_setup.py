"""CI setup must reuse capable tools and stop promptly on broken downloads."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import os
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup():
    loader = SourceFileLoader("ci_ffmpeg_setup", str(ROOT / "bin/setup-ci-ffmpeg"))
    spec = spec_from_loader(loader.name, loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def commands(setup, monkeypatch):
    calls = []
    state = {"installed": True, "missing_filter": False, "apt_error": None,
             "missing_encoder": False, "bad_probe": False, "probe_major": 8}

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if "apt-get" in args:
            if state["apt_error"]:
                raise subprocess.CalledProcessError(state["apt_error"], args, "mirror unavailable")
            state["installed"] = True
            return subprocess.CompletedProcess(args, 0, "apt installed ffmpeg")
        if not state["installed"]:
            raise FileNotFoundError(args[0])
        if "-version" in args:
            tool = Path(args[0]).name
            major = state["probe_major"] if tool == "ffprobe" else 8
            output = f"{tool} version {major}.0.1-3ubuntu2 Copyright"
        elif "-filters" in args:
            names = {"drawtext", "ass", "subtitles", "concat", "overlay", "xfade", "aresample", "atempo"}
            if state["missing_filter"]:
                names.remove("drawtext")
            output = "\n".join(f" ... {name} V->V" for name in names)
        elif "-encoders" in args:
            names = {"libx264", "aac", "mpeg4", "png", "mjpeg", "pcm_s16le"}
            if state["missing_encoder"]:
                names.remove("libx264")
            output = "\n".join(f" V..... {name} encoder" for name in names)
        elif Path(args[0]).name == "ffprobe":
            output = '{"streams":[{"codec_type":"video","codec_name":"h264"},{"codec_type":"audio","codec_name":"aac"}],"format":{"duration":"0.25"}}'
            if state["bad_probe"]:
                output = '{"streams":[],"format":{"duration":"0.25"}}'
        else:
            output = ""
        return subprocess.CompletedProcess(args, 0, output)

    monkeypatch.setattr(setup.subprocess, "run", run)
    return calls, state


def test_capable_preinstalled_tools_never_contact_apt(setup, commands, capsys):
    calls, _ = commands
    assert setup.main([]) == 0
    assert not any("apt-get" in args for args, _ in calls)
    assert any("-filter_complex" in args for args, _ in calls)
    assert all(0 < kwargs["timeout"] <= 30 for _, kwargs in calls)
    assert "8.0.1" in capsys.readouterr().out


def test_missing_tools_install_once_then_verify_real_capabilities(setup, commands):
    calls, state = commands
    state["installed"] = False
    assert setup.main([]) == 0
    apt = [args for args, _ in calls if "apt-get" in args]
    assert len(apt) == 2
    for args in apt:
        assert args[:4] == ["sudo", "-n", "timeout", "--kill-after=5s"]
        assert 0 < float(args[4].removesuffix("s")) <= 120
        assert "Acquire::Retries=0" in args
        assert "Acquire::http::Timeout=15" in args
    assert "update" in apt[0] and "install" in apt[1]
    install_index = next(i for i, (args, _) in enumerate(calls) if "install" in args)
    assert any("-filters" in args for args, _ in calls[install_index + 1:])


@pytest.mark.parametrize("error", [100, 124])
def test_failed_or_timed_out_download_stops_before_install(setup, commands, capsys, error):
    calls, state = commands
    state.update(installed=False, apt_error=error)
    assert setup.main([]) == 1
    assert len([args for args, _ in calls if "apt-get" in args]) == 1
    message = capsys.readouterr().err
    assert "mirror unavailable" in message
    assert "setup" in message.lower() and "#135" in message


def test_package_install_does_not_hide_missing_drawtext(setup, commands, capsys):
    _, state = commands
    state["missing_filter"] = True
    assert setup.main([]) == 1
    assert "drawtext" in capsys.readouterr().err


def test_check_only_never_installs_missing_capabilities(setup, commands):
    calls, state = commands
    state["missing_filter"] = True
    assert setup.main(["--check-only"]) == 1
    assert not any("apt-get" in args for args, _ in calls)


@pytest.mark.parametrize("change,diagnostic", [
    ({"missing_encoder": True}, "libx264"),
    ({"bad_probe": True}, "unexpected streams"),
    ({"probe_major": 7}, "major versions differ"),
])
def test_rejects_incompatible_or_broken_media_tools(setup, commands, capsys, change, diagnostic):
    _, state = commands
    state.update(change)
    assert setup.main(["--check-only"]) == 1
    assert diagnostic in capsys.readouterr().err


def test_slow_command_is_actually_terminated(setup):
    runner = setup.Runner()
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        runner.run([sys.executable, "-c", "import time; time.sleep(10)"], seconds=.1)
    assert time.monotonic() - started < 3


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true",
    reason="exercise sudo/GNU timeout only on the real Linux CI runner",
)
def test_privileged_timeout_on_real_ci_runner(setup):
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="exit 124"):
        setup.Runner().run(
            ["sleep", "10"], seconds=.1, root=True,
        )
    assert time.monotonic() - started < 3


def test_exhausted_overall_budget_starts_no_more_commands(setup, monkeypatch):
    runner = setup.Runner()
    runner.deadline = time.monotonic() - 1
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **k: pytest.fail("ran after deadline"))
    with pytest.raises(RuntimeError, match="budget"):
        runner.run(["ffmpeg", "-version"])


def test_workflow_bounds_setup_without_reducing_test_coverage():
    text = (ROOT / ".github/workflows/tests.yml").read_text()
    job = text.split("\n  test:\n", 1)[1]
    assert "runs-on: ubuntu-26.04" in job
    assert "timeout-minutes: 4" in job
    assert "python bin/setup-ci-ffmpeg" in job
    assert "sudo apt-get update" not in job
    assert "suite: [fast, full]" in job
    assert "pytest --run-slow" in job
