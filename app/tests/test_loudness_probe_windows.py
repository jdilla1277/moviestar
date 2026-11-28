"""Bounded loudness must exclude audio outside the requested window (#319)."""
import array
import json
import math
import subprocess
import wave

import pytest

from moviestar.ffmpeg import run_loudness_probe


@pytest.fixture(scope="module", params=["wav", "aac", "stereo-aac"])
def level_steps(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("loudness-windows")
    path = root / "steps.wav"
    rate = 48000
    levels = [.02, .04, .08, .16, .32, .64, .02, .02, .02, .02, .02, .02]
    samples = array.array("h", (
        round(32767 * levels[min(int(i / rate * 2), 11)] * math.sin(2 * math.pi * 1000 * i / rate))
        for i in range(rate * 6)
    ))
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(samples.tobytes())
    channels = 1
    if request.param != "wav":
        source, path = path, root / "steps.mp4"
        if request.param == "stereo-aac":
            rate, channels = 44100, 2
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=96x64:r=30:d=6",
            "-i", str(source), "-c:v", "libx264", "-g", "90", "-keyint_min", "90",
            "-sc_threshold", "0", "-bf", "2", "-c:a", "aac", "-ar", str(rate),
            "-ac", str(channels), str(path),
        ], check=True)
        keys = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-skip_frame", "nokey", "-select_streams", "v:0",
            "-show_entries", "frame=pts_time", "-of", "json", str(path),
        ]))["frames"]
        assert [float(f["pts_time"]) for f in keys] == [0, 3]
    # Decode the complete source independently; slicing PCM by sample index
    # avoids both seeking and the probe's duration/measurement pipeline.
    pcm = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", str(channels),
        "-ar", str(rate), "-f", "s16le", "-",
    ])
    return root, path, rate, channels, pcm


@pytest.mark.parametrize("start,end", [
    (0, .5), (.5, 1), (1, 1.5), (.1, .4), (.6, .9),
    (1.499, 1.501), (3.2, 3.7), (4.2, 4.7), (5.5, 6), (0, 6),
])
def test_bounded_probe_matches_independently_sliced_pcm(level_steps, start, end):
    root, path, rate, channels, pcm = level_steps
    reference = root / f"reference-{start}-{end}.wav"
    frame_bytes = channels * 2
    selected = pcm[round(start * rate) * frame_bytes:round(end * rate) * frame_bytes]
    with wave.open(str(reference), "wb") as stream:
        stream.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(selected)
    actual, _ = run_loudness_probe(str(path), start_time=start, duration=end - start)
    expected, _ = run_loudness_probe(str(reference))
    assert actual["true_peak_dbtp"] == pytest.approx(expected["true_peak_dbtp"], abs=.08)
    if math.isfinite(expected["integrated_lufs"]):
        assert actual["integrated_lufs"] == pytest.approx(expected["integrated_lufs"], abs=.08)
    else:
        # Integrated LUFS can be unavailable for very short windows; future
        # audio must not turn that into a plausible finite measurement.
        assert actual["integrated_lufs"] == expected["integrated_lufs"]


def test_cli_keeps_range_and_measures_distinct_adjacent_windows(level_steps):
    from click.testing import CliRunner
    from moviestar.cli import cli

    _, path, _, _, _ = level_steps
    runner = CliRunner()
    peaks = []
    for start in (0, .5, 1):
        result = runner.invoke(cli, ["probe", str(path), "--loudness", "--from", str(start), "--to", str(start + .5)])
        assert result.exit_code == 0, result.output
        loudness = json.loads(result.stdout)["loudness"]
        assert loudness["range"]["from"]["seconds"] == start
        assert loudness["range"]["duration"]["seconds"] == .5
        peaks.append(loudness["true_peak_dbtp"])
    assert peaks[1] - peaks[0] > 5
    assert peaks[2] - peaks[1] > 5
