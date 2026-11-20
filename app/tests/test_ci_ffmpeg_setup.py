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


def test_package_cache_is_opt_in_and_only_changes_apt_archive_location(setup, commands, tmp_path, capsys):
    calls, state = commands
    state['installed'] = False
    cache = tmp_path / 'archives with spaces'
    cache.mkdir()
    (cache / 'libavcodec.deb').write_bytes(b'cached-package')
    assert setup.main(['--apt-cache', str(cache)]) == 0
    apt = [args for args, _ in calls if 'apt-get' in args]
    assert len(apt) == 2
    assert all(f'Dir::Cache::archives={cache.resolve()}' in args for args in apt)
    assert all('APT::Keep-Downloaded-Packages=true' in args for args in apt)
    assert all('Acquire::Retries=0' in args for args in apt)
    assert '1 cached .deb' in capsys.readouterr().out
    assert any('-filter_complex' in args for args, _ in calls)


def test_missing_cache_is_created_but_check_only_never_creates_it(setup, commands, tmp_path):
    calls, state = commands
    state['installed'] = False
    cache = tmp_path / 'archives'
    assert setup.main(['--check-only', '--apt-cache', str(cache)]) == 1
    assert not cache.exists()
    assert not any('apt-get' in args for args, _ in calls)
    assert setup.main(['--apt-cache', str(cache)]) == 0
    assert cache.is_dir()


def test_install_timeout_with_restored_cache_does_not_retry_or_report_success(setup, commands, monkeypatch, tmp_path, capsys):
    calls, state = commands
    state['installed'] = False
    fake_run = setup.subprocess.run

    def fail_install(args, **kwargs):
        if 'apt-get' in args and 'install' in args:
            calls.append((args, kwargs))
            raise subprocess.CalledProcessError(124, args, 'slow package mirror')
        return fake_run(args, **kwargs)

    monkeypatch.setattr(setup.subprocess, 'run', fail_install)
    assert setup.main(['--apt-cache', str(tmp_path / 'cache')]) == 1
    assert sum('install' in args for args, _ in calls) == 1
    assert not any('-filters' in args for args, _ in calls)
    message = capsys.readouterr().err
    assert 'slow package mirror' in message
    assert 'Package tests were not run' in message
    assert '#255' in message


def test_ci_caches_only_downloads_and_saves_after_setup_before_package_tests():
    text = (ROOT / '.github/workflows/tests.yml').read_text()
    job = text.split('\n  test:\n', 1)[1]
    assert 'actions/cache/restore@v5' in job
    assert 'actions/cache/save@v5' in job
    assert 'moviestar-apt/*.deb' in job
    assert '--apt-cache "$RUNNER_TEMP/moviestar-apt"' in job
    assert 'steps.ffmpeg-cache.outputs.image' in job
    assert job.index('actions/cache/save@v5') < job.index('- name: Install moviestar')
    assert 'suite: [fast, full]' in job
    assert 'timeout-minutes: 25' in job


@pytest.mark.parametrize('variant', [None, 'amd64v3'])
@pytest.mark.parametrize('kind', ['valid', 'tampered', 'stale', 'symlink', 'no-metadata'])
def test_restored_archive_must_match_current_package_sha256(setup, tmp_path, kind, variant):
    import hashlib
    cache = tmp_path / 'cache'
    cache.mkdir()
    content = b'ubuntu-deb'
    archive_arch = variant or 'amd64'
    archive = cache / f'libexample_7%3a8.0.1-3ubuntu2_{archive_arch}.deb'
    if kind == 'symlink':
        target = tmp_path / 'outside'
        target.write_bytes(content)
        archive.symlink_to(target)
    else:
        archive.write_bytes(b'x' * len(content) if kind == 'tampered' else content)
    metadata = '\n'.join([
        'Package: libexample', 'Version: ' + ('7:old' if kind == 'stale' else '7:8.0.1-3ubuntu2'),
        'Architecture: amd64', f'Size: {len(content)}',
        'SHA256: ' + hashlib.sha256(content).hexdigest(), '',
    ])

    if variant:
        metadata += f'Architecture-Variant: {variant}\n'

    class MetadataRunner:
        deadline = time.monotonic() + 30

        def run(self, args, **kwargs):
            assert args[:2] == ['apt-cache', 'show']
            # Like APT, a CPU-variant architecture selector supplies no native
            # package record. Test retention, not one preferred argv spelling.
            if 'libexample:amd64v3' in args:
                return ''
            assert 'libexample' in args or 'libexample:amd64' in args
            if kind == 'no-metadata':
                raise RuntimeError('no current package record')
            return metadata

    setup.verify_cached_archives(MetadataRunner(), cache)
    assert archive.exists() == (kind == 'valid')
    if kind == 'symlink':
        assert target.read_bytes() == content


def test_optional_cache_service_cannot_consume_the_media_job_budget():
    text = (ROOT / '.github/workflows/tests.yml').read_text()
    for title in ['Restore Ubuntu FFmpeg downloads', 'Save verified Ubuntu FFmpeg downloads']:
        step = text.split(f'- name: {title}\n', 1)[1].split('\n      - ', 1)[0]
        assert 'timeout-minutes: 1' in step
        assert 'continue-on-error: true' in step
    setup_step = text.split('- name: Set up and verify FFmpeg\n', 1)[1].split('\n      - ', 1)[0]
    assert 'continue-on-error' not in setup_step
