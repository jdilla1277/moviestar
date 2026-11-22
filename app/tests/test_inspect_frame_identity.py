"""Decode synthetic frames independently to verify inspect's source clock."""
import json
from pathlib import Path
import subprocess

from click.testing import CliRunner
import pytest

from moviestar.cli import cli


def run(*args):
    return subprocess.run(list(args), capture_output=True, check=True).stdout


def gray(path):
    return run('ffmpeg', '-v', 'error', '-i', str(path), '-frames:v', '1',
               '-vf', 'scale=64:64', '-pix_fmt', 'gray', '-f', 'rawvideo', '-')


@pytest.fixture
def counter(tmp_path):
    path = tmp_path / 'counter.mp4'
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        "nullsrc=s=64x64:r=30:d=4,geq=lum='16+N':cb=128:cr=128",
        '-c:v', 'libx264', '-crf', '0', str(path))
    return path


def inspect(runner, start=0, end=2, interval=.5, *extra):
    response = runner.invoke(cli, ['inspect', '--from', str(start), '--to', str(end),
                                  '--interval', str(interval), '--width', '64', *extra])
    assert response.exit_code == 0, response.output
    return json.loads(response.stdout)


def test_inspect_zero_is_the_first_source_frame(counter, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    loaded = runner.invoke(cli, ['load', str(counter), '--no-transcribe', '--no-frames'])
    assert loaded.exit_code == 0, loaded.output
    result = inspect(runner)
    reference = gray(counter)
    assert abs(sum(gray(result['thumbnails'][0]['path'])) / 4096 - sum(reference) / 4096) < 1
    assert result['thumbnails'][0]['timecode']['seconds'] == 0


def decoded_source(path):
    info = json.loads(run('ffprobe', '-v', 'error', '-select_streams', 'v:0',
                          '-show_frames', '-show_format', '-show_entries',
                          'frame=best_effort_timestamp_time:format=start_time', '-of', 'json', str(path)))
    origin = float(info['format'].get('start_time', 0))
    times = [float(f['best_effort_timestamp_time']) - origin for f in info['frames']]
    pixels = run('ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
                 '-fps_mode', 'passthrough', '-pix_fmt', 'gray', '-f', 'rawvideo', '-')
    means = [sum(pixels[i:i + 4096]) / 4096 for i in range(0, len(pixels), 4096)]
    assert len(means) == len(times)
    return times, means


@pytest.mark.parametrize('rate,vfr,start,end,interval,offset', json.loads((Path(__file__).parent / 'fixtures' / 'inspection-clock-cases.json').read_text()))
def test_labels_identify_decoded_frames(tmp_path, monkeypatch, rate, vfr, start, end, interval, offset):
    path = tmp_path / 'identity.mp4'
    vf = "nullsrc=s=64x64:r=" + rate + ":d=4,geq=lum='16+N':cb=128:cr=128"
    if vfr:
        vf += ",select='eq(n,0)+eq(n,3)+eq(n,12)+eq(n,42)+eq(n,57)+eq(n,69)+eq(n,117)'"
    if offset:
        vf += f',setpts=PTS+{offset}/TB'
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', vf, '-fps_mode', 'vfr',
        '-c:v', 'libx264', '-crf', '0', '-g', '30', '-bf', '2', str(path))
    times, means = decoded_source(path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    load = runner.invoke(cli, ['load', str(path), '--no-transcribe', '--no-frames'])
    assert load.exit_code == 0, load.output
    result = inspect(runner, start, end, interval)
    assert len(result['thumbnails']) == len([i for i in range(100) if start + i * interval < end - 1e-9])
    for i, thumb in enumerate(result['thumbnails']):
        target = start + i * interval
        expected_index = max(j for j, t in enumerate(times) if t <= target + 1e-6)
        actual_mean = sum(gray(thumb['path'])) / 4096
        assert abs(actual_mean - means[expected_index]) < 1, (target, actual_mean, means[expected_index])
        assert thumb['requested_timecode']['seconds'] == pytest.approx(round(target, 3))
        assert thumb['timecode']['seconds'] == pytest.approx(round(times[expected_index], 3), abs=.001)
    repeat = inspect(runner, start, end, interval)
    assert repeat['extraction']['cached'] is True
    assert repeat['thumbnails'] == result['thumbnails']


def test_cache_requires_frame_timing_and_all_images(counter, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ['load', str(counter), '--no-transcribe', '--no-frames'])
    result = inspect(runner)
    Path(result['thumbnails'][1]['path']).unlink()
    dry = inspect(runner, 0, 2, .5, '--dry-run')
    assert dry['would_reuse_cache'] is False
    repaired = inspect(runner)
    assert repaired['extraction']['cached'] is False
    assert len(repaired['thumbnails']) == 4
    # Legacy/partial image-only caches cannot supply truthful timing.
    for manifest in Path(repaired['extraction']['frames_dir']).glob('*.json'):
        manifest.unlink()
    dry = inspect(runner, 0, 2, .5, '--dry-run')
    assert dry['would_reuse_cache'] is False


def test_cache_invalidated_when_source_changes(counter, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ['load', str(counter), '--no-transcribe', '--no-frames'])
    inspect(runner)
    # Replacing a source at the same path must not reuse old images/timestamps.
    counter.write_bytes(counter.read_bytes() + b'\0')
    assert inspect(runner, 0, 2, .5, '--dry-run')['would_reuse_cache'] is False


@pytest.mark.parametrize('trace', [
    '',
    'config in time_base: 1/30\nn: 0 pts: NOPTS pts_time:NOPTS',
    'config in time_base: 1/0\nn: 0 pts: 0 pts_time:0',
    'config in time_base: 1/30\nn: 0 pts: 0 pts_time:0\nn: 2 pts: 2 pts_time:.06',
    'config in time_base: 1/30\nn: 0 pts: 2 pts_time:.06\nn: 1 pts: 1 pts_time:.03',
    'config in time_base: 1/30\nconfig in time_base: 1/60\nn: 0 pts: 0 pts_time:0',
])
def test_missing_or_inconsistent_pts_fails_closed(counter, tmp_path, monkeypatch, trace):
    from types import SimpleNamespace
    from moviestar.ffmpeg import extract_inspection_frames
    (tmp_path / 'sample_000001.jpg').write_bytes(b'jpeg')
    paired = '\n'.join(f'[showinfo@{label} @ 0x0] {line}'
                       for label in ('source', 'sample') for line in trace.splitlines())
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=0, stderr=paired, stdout=''))
    with pytest.raises(RuntimeError, match='timestamp'):
        extract_inspection_frames(str(counter), .5, tmp_path, 'sample', 64, 0, 1)


@pytest.mark.parametrize('invalid', ['{', 'null', '{}', '{"frames": 2}',
    '{"identity": {}, "frames": [{"file": "missing.jpg", "seconds": "bad"}]}'])
def test_invalid_manifest_is_not_a_cache_hit(counter, tmp_path, monkeypatch, invalid):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ['load', str(counter), '--no-transcribe', '--no-frames'])
    result = inspect(runner)
    manifest = Path(result['extraction']['frames_dir']) / 'source-times.json'
    manifest.write_text(invalid)
    assert inspect(runner, 0, 2, .5, '--dry-run')['would_reuse_cache'] is False
    assert inspect(runner)['extraction']['cached'] is False


def test_dry_run_uses_same_sampling_command_without_decoding(counter, tmp_path, monkeypatch):
    from moviestar.ffmpeg import build_extract_frames_command
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ['load', str(counter), '--no-transcribe', '--no-frames'])
    def no_decode(*a, **kw):
        raise AssertionError('dry-run must not invoke media tools')
    monkeypatch.setattr(subprocess, 'run', no_decode)
    result = inspect(runner, .51, 1.99, .37, '--dry-run')
    assert result['ffmpeg_command'] == build_extract_frames_command(
        str(counter), .37, str(Path(result['would_write_to']) / 'counter_inspect_%06d.jpg'),
        64, start_time=.51, duration=1.48, source_timestamps=True)
    assert not Path(result['would_write_to']).exists()


@pytest.mark.parametrize('times', [[0, float('nan'), 1, 1.5], [0, True, 1, 1.5],
                                  [0, '0.5', 1, 1.5], [0, 1, .5, 1.5]])
def test_invalid_cached_times_are_not_relabelled(counter, tmp_path, monkeypatch, times):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ['load', str(counter), '--no-transcribe', '--no-frames'])
    result = inspect(runner)
    path = Path(result['extraction']['frames_dir']) / 'source-times.json'
    manifest = json.loads(path.read_text())
    for entry, value in zip(manifest['frames'], times):
        entry['seconds'] = value
    path.write_text(json.dumps(manifest))
    assert inspect(runner, 0, 2, .5, '--dry-run')['would_reuse_cache'] is False


def test_cache_does_not_alias_rounded_arguments(counter, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ['load', str(counter), '--no-transcribe', '--no-frames'])
    first = inspect(runner, .0333, 1.0333, .5)
    second = inspect(runner, .0334, 1.0334, .5)
    assert second['extraction']['cached'] is False
    assert first['extraction']['frames_dir'] != second['extraction']['frames_dir']
    assert sum(gray(first['thumbnails'][0]['path'])) == 0
    assert first['thumbnails'][0]['timecode']['seconds'] == 0
    assert second['thumbnails'][0]['timecode']['seconds'] == .033


def test_warm_cache_does_not_run_media_tools(counter, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ['load', str(counter), '--no-transcribe', '--no-frames'])
    first = inspect(runner)
    def no_decode(*a, **kw):
        raise AssertionError('warm cache must not invoke media tools')
    monkeypatch.setattr(subprocess, 'run', no_decode)
    second = inspect(runner)
    assert second['extraction']['cached'] is True
    assert second['thumbnails'] == first['thumbnails']


def test_fractional_interval_labels_match_the_actual_pixels(counter, tmp_path, monkeypatch):
    # Do not infer frame identity from a rounded ffprobe timestamp at a sample
    # only picoseconds before a frame boundary. Match decoded image content.
    times, means = decoded_source(counter)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ['load', str(counter), '--no-transcribe', '--no-frames'])
    result = inspect(runner, 0, 1, .0333333333)
    for thumb in result['thumbnails']:
        actual_mean = sum(gray(thumb['path'])) / 4096
        index = min(range(len(means)), key=lambda i: abs(means[i] - actual_mean))
        assert abs(means[index] - actual_mean) < 1
        assert thumb['timecode']['seconds'] == pytest.approx(round(times[index], 3), abs=.001)


def test_audio_only_tail_keeps_an_empty_inspection_result(tmp_path, monkeypatch):
    path = tmp_path / 'audio-tail.mp4'
    run('ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=blue:s=64x64:r=30:d=1', '-f', 'lavfi', '-i',
        'anullsrc=r=48000:cl=stereo', '-t', '4', '-c:v', 'libx264', '-c:a', 'aac', str(path))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    loaded = runner.invoke(cli, ['load', str(path), '--no-transcribe', '--no-frames'])
    assert loaded.exit_code == 0, loaded.output
    result = inspect(runner, 3, 4, .2)
    assert result['thumbnails'] == []
    assert result['thumbnails_returned'] == 0
