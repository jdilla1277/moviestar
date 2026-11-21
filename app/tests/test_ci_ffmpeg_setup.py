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
    for title in [
        'Restore Ubuntu FFmpeg downloads', 'Save verified Ubuntu FFmpeg downloads',
        'Fingerprint restored FFmpeg downloads', 'Fingerprint verified FFmpeg downloads',
    ]:
        step = text.split(f'- name: {title}\n', 1)[1].split('\n      - ', 1)[0]
        assert 'timeout-minutes: 1' in step
        assert 'continue-on-error: true' in step
    setup_step = text.split('- name: Set up and verify FFmpeg\n', 1)[1].split('\n      - ', 1)[0]
    assert 'continue-on-error' not in setup_step


def test_dangling_archive_link_cannot_prevent_installation_through_main(setup, commands, tmp_path):
    calls, state = commands
    state['installed'] = False
    cache = tmp_path / 'cache'
    cache.mkdir()
    link = cache / 'libexample_1_amd64.deb'
    link.symlink_to(tmp_path / 'missing-target')
    assert setup.main(['--apt-cache', str(cache)]) == 0
    assert not link.is_symlink()
    assert sum('install' in args for args, _ in calls) == 1
    assert any('-filter_complex' in args for args, _ in calls)


def test_archive_fingerprint_tracks_bytes_and_names_not_path_or_mtime(setup, commands, tmp_path, capsys):
    calls, _ = commands
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    for directory in [first, second]:
        (directory / 'libexample_1_all.deb').write_bytes(b'valid-package')
    os.utime(second / 'libexample_1_all.deb', (1, 1))
    assert setup.main(['--archive-fingerprint', str(first)]) == 0
    output = capsys.readouterr().out.strip()
    assert output == 'digest=' + setup.archive_fingerprint(second)
    assert not calls
    original = setup.archive_fingerprint(first)
    (first / 'libexample_1_all.deb').write_bytes(b'other-package')
    assert setup.archive_fingerprint(first) != original
    (first / 'libexample_1_all.deb').write_bytes(b'valid-package')
    (first / 'libexample_1_all.deb').rename(first / 'libexample_2_all.deb')
    assert setup.archive_fingerprint(first) != original


def test_fingerprint_empty_or_invalid_cache_does_not_offer_a_save_key(setup, tmp_path, capsys):
    assert setup.main(['--archive-fingerprint', str(tmp_path / 'absent')]) == 0
    assert capsys.readouterr().out.strip() == 'digest='
    (tmp_path / 'bad.deb').symlink_to(tmp_path / 'absent')
    assert setup.main(['--archive-fingerprint', str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert 'symlink' in captured.err


@pytest.mark.parametrize('change', ['repair', 'update'])
def test_immutable_exact_hit_can_be_repaired_and_restored_next_run(
    setup, commands, monkeypatch, tmp_path, change,
):
    import hashlib
    # The remote key can still contain the original good digest even when its
    # restored payload is corrupt. A digest-only immutable key cannot heal it.
    calls, state = commands
    cache = tmp_path / 'cache'
    cache.mkdir()
    old_name = 'libexample_1_all.deb'
    (cache / old_name).write_bytes(b'valid-package')
    original_digest = setup.archive_fingerprint(cache)
    prefix = 'ffmpeg-ubuntu26-X64-image-helper'
    old_key = f'{prefix}-content-{original_digest}-run1-1'
    stored = {old_key: {old_name: b'broken-cache!' if change == 'repair' else b'valid-package'}}
    current_version = '1' if change == 'repair' else '2'
    current_name = f'libexample_{current_version}_all.deb'
    current_bytes = b'valid-package' if change == 'repair' else b'updated-build'
    metadata = '\n'.join([
        'Package: libexample', f'Version: {current_version}', 'Architecture: all',
        'SHA256: ' + hashlib.sha256(current_bytes).hexdigest(), '',
    ])
    downloads = []
    fake_run = setup.subprocess.run

    def apt_with_downloads(args, **kwargs):
        if args[:2] == ['apt-cache', 'show']:
            return subprocess.CompletedProcess(args, 0, metadata)
        result = fake_run(args, **kwargs)
        if 'apt-get' in args and 'install' in args:
            destination = cache / current_name
            if not destination.exists():
                downloads.append(current_name)
                destination.write_bytes(current_bytes)
        return result

    monkeypatch.setattr(setup.subprocess, 'run', apt_with_downloads)

    def restore(key):
        for file in cache.glob('*.deb'):
            file.unlink()
        for name, content in stored[key].items():
            (cache / name).write_bytes(content)
        state['installed'] = False

    matched_key = old_key  # exact cache hit does not suppress the save decision
    restore(matched_key)
    before = setup.archive_fingerprint(cache)
    assert setup.main(['--apt-cache', str(cache)]) == 0
    after = setup.archive_fingerprint(cache)
    assert downloads == [current_name]
    refresh = before != after or not matched_key.startswith(f'{prefix}-content-{after}-')
    assert refresh
    if change == 'repair':
        assert after == original_digest
    new_key = f'{prefix}-content-{after}-run2-1'
    assert new_key != old_key
    stored.setdefault(new_key, {p.name: p.read_bytes() for p in cache.glob('*.deb')})
    # Prefix restore chooses the newest generation rather than the old exact
    # key. A second complete setup needs no replacement download or save.
    selected = next(k for k in reversed(stored) if k.startswith(f'{prefix}-content-'))
    restore(selected)
    next_before = setup.archive_fingerprint(cache)
    assert setup.main(['--apt-cache', str(cache)]) == 0
    next_after = setup.archive_fingerprint(cache)
    assert downloads == [current_name]
    assert (cache / current_name).read_bytes() == current_bytes
    assert next_before == next_after == after
    assert not (next_before != next_after or not selected.startswith(f'{prefix}-content-{next_after}-'))


def test_workflow_refreshes_changed_content_even_if_restore_was_an_exact_hit():
    job = (ROOT / '.github/workflows/tests.yml').read_text().split('\n  test:\n', 1)[1]
    restore = job.split('- name: Restore Ubuntu FFmpeg downloads\n', 1)[1].split('\n      - ', 1)[0]
    save = job.split('- name: Save verified Ubuntu FFmpeg downloads\n', 1)[1].split('\n      - ', 1)[0]
    assert 'github.run_id' in restore and 'github.run_attempt' in restore
    first_prefix = restore.split('restore-keys: |', 1)[1].strip().splitlines()[0].strip()
    assert first_prefix == '${{ steps.ffmpeg-cache.outputs.prefix }}-content-'
    assert 'cache-hit' not in save
    assert "steps.ffmpeg-after.outputs.digest != ''" in save
    assert 'steps.ffmpeg-before.outputs.digest != steps.ffmpeg-after.outputs.digest' in save
    assert 'cache-matched-key' in save
    assert 'outputs.digest }}-${{ github.run_id }}-${{ github.run_attempt }}' in save
    assert job.index('Fingerprint restored FFmpeg downloads') < job.index('- name: Set up and verify FFmpeg')
    assert job.index('Fingerprint verified FFmpeg downloads') < job.index('actions/cache/save@v5')
