"""Independent decoded-content checks for source audio post-passes (#130)."""

import json
import shutil
import subprocess

import numpy as np
import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import mix_audio, normalize_loudness, resolve_video_encoder


RATE = 48_000
DURATION = 6


def _run(args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def _timeline_audio(path):
    """Place decoded samples on their presentation clock, including AAC delay.

    Raw PCM has no timestamps. Reading it as if sample zero were always movie
    time zero would hide the exact offset this regression needs to detect.
    The expected clock comes from decoded frame PTS, not the mix filtergraph.
    """
    info = json.loads(_run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-read_intervals", "%+#4", "-show_frames", "-of", "json", str(path),
    ]))
    first_sample = round(float(info["frames"][0]["pts_time"]) * RATE)
    pcm = np.frombuffer(_run([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-",
    ]), dtype="<f4").astype(np.float64)
    if first_sample < 0:
        return pcm[-first_sample:]
    return np.pad(pcm, (first_sample, 0))


@pytest.fixture(scope="module")
def source_media(tmp_path_factory):
    root = tmp_path_factory.mktemp("source-postpass")
    source = root / "source.mp4"
    encoder = resolve_video_encoder("auto")
    video_args = (["-c:v", "libx264", "-preset", "ultrafast"]
                  if encoder == "x264"
                  else ["-c:v", "h264_videotoolbox", "-b:v", "1M"])
    _run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        f"color=black:s=160x120:r=30:d={DURATION}",
        "-f", "lavfi", "-i",
        f"anoisesrc=r={RATE}:d={DURATION}:a=0.3:seed=130,lowpass=f=2000,"
        "volume='0.6+0.3*sin(2*PI*0.7*t)':eval=frame",
        *video_args, "-c:a", "aac", "-b:a", "192k",
        "-ac", "2", "-shortest", "-movie_timescale", str(RATE), str(source),
    ])
    # Lossless audio removes ambiguity about codec priming from the exact
    # 1040-sample offset case. Its final sample is at the picture's ending;
    # moving it early and padding silence cannot satisfy the content checks.
    delayed = root / "delayed.mov"
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(source),
        "-filter_complex",
        f"[0:a]atrim=end_sample={DURATION * RATE - 1040},"
        "asetpts=PTS+1040/48000/TB[a]",
        "-map", "0:v:0", "-map", "[a]", "-c:v", "copy",
        "-c:a", "pcm_s16le", "-movie_timescale", str(RATE), str(delayed),
    ])
    delayed_aac = root / "delayed-aac.mp4"
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(source),
        "-itsoffset", "0.3", "-i", str(source),
        "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
        "-movie_timescale", str(RATE),
        str(delayed_aac),
    ])
    return {"aac": source, "positive_pts": delayed, "positive_pts_aac": delayed_aac}


def _assert_content_and_coverage(expected, actual, duration=DURATION):
    count = duration * RATE
    assert len(actual) >= count, "decoded audio ends before the intended range"
    assert len(expected) >= count, "fixture must cover the full intended range"
    # Quantization of a muxer's movie clock can move AAC by a fraction of a
    # millisecond. Search independently, then enforce the existing 1ms bound;
    # never accept a content match at an arbitrary offset (the defect is 21ms).
    start, size, radius = RATE // 2, RATE // 4, RATE // 20
    reference = expected[start:start + size]
    search = actual[start - radius:start + size + radius]
    lag = int(np.argmax(np.correlate(search, reference, mode="valid"))) - radius
    assert abs(lag) <= RATE // 1000, f"source audio shifted by {lag / RATE}s"
    # Check opening and ending against the original independently decoded
    # source, including the complete last quarter second (not only energy).
    for start in (RATE // 2, count - RATE // 4):
        end = start + RATE // 4
        reference = expected[start:end]
        delivered = actual[start + lag:end + lag]
        assert len(delivered) == len(reference), "ending source window is incomplete"
        correlation = np.corrcoef(reference, delivered)[0, 1]
        assert correlation > 0.97, (
            f"source content shifted or missing at {start / RATE}s: {correlation}"
        )


@pytest.mark.parametrize("source_kind", ["aac", "positive_pts", "positive_pts_aac"])
@pytest.mark.parametrize("mixed,normalized", [(False, False), (True, False), (False, True), (True, True)])
def test_source_audio_postpasses_preserve_presentation_timing_and_ending(
    source_media, tmp_path, source_kind, mixed, normalized
):
    source = source_media[source_kind]
    expected = _timeline_audio(source)
    output = tmp_path / "output.mp4"
    if mixed:
        mix_audio(
            str(source), str(output),
            {"source_audio": {"gain_db": 3.5}, "tracks": []},
            DURATION, source_audio_available=True,
        )
    else:
        output = output.with_suffix(source.suffix)
        shutil.copyfile(source, output)
    if normalized:
        normalize_loudness(str(output), -16)
    _assert_content_and_coverage(expected, _timeline_audio(output))


@pytest.mark.parametrize("command,mixed,normalized", [
    ("export", False, False), ("export", True, False),
    ("export", False, True), ("export", True, True),
    ("watch", False, False), ("watch", True, False),
])
@pytest.mark.parametrize("start", [0, 1])
def test_cli_trimmed_aac_preserves_source_clock_through_postpasses(
    source_media, loaded_project, tmp_path, command, mixed, normalized, start
):
    source = source_media["positive_pts_aac"]
    expected = _timeline_audio(source)[start * RATE:]
    loaded_project(source, frames=False)
    runner = CliRunner()

    def invoke(args):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    invoke(["trim", "--from", str(start), "--to", str(DURATION)])
    if mixed:
        invoke(["audio", "source", "--gain-db", "3.5"])
    spec = tmp_path / "moviestar" / "spec.json"
    before = spec.read_bytes()
    output = tmp_path / "rendered.mp4"
    args = [command, "--out", str(output)]
    if command == "export":
        args += ["--preview"]
    else:
        # Flat watch retains its documented source-time contract, while
        # export renders the edited result. Both select the same passage here.
        args += ["--from", str(start), "--to", str(DURATION), "--precise"]
    if normalized:
        args += ["--loudness-target", "-16", "--loudness-report"]
    invoke(args)
    assert spec.read_bytes() == before
    _assert_content_and_coverage(expected, _timeline_audio(output), DURATION - start)
