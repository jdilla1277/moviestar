"""Concat must snapshot retained ranges, never restore earlier cut holes (#244)."""

import array
import math
import copy
import json
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.spec import (
    SpecValidationError, append_cut, append_trim, empty_spec, resolve_source,
    set_composition,
)


def _edited_source():
    spec = empty_spec([
        {"id": "speaker", "path": "speaker.mp4"},
        {"id": "camera", "path": "camera.mp4"},
    ])
    spec = append_trim(spec, "speaker", 10, 40, 60)
    spec = append_trim(spec, "speaker", 5, 20, 60)
    return append_cut(spec, "speaker", 5, 10, 60)


@pytest.mark.parametrize("start,end,expected", [
    (0, 10, [(15, 20), (25, 30)]),
    (3, 7, [(18, 20), (25, 27)]),
    (0, 5, [(15, 20)]),
    (5, 10, [(25, 30)]),
    (5, 7, [(25, 27)]),
])
def test_concat_snapshots_retained_spans(start, end, expected):
    spec = _edited_source()
    before = copy.deepcopy(spec)
    canvas = {"preset": None, "width": 320, "height": 240, "aspect_ratio": "4:3"}
    framing = {"mode": "fill", "anchor": "left"}
    result = set_composition(
        spec, [("speaker", start, end), ("camera", 20, 30)],
        {"speaker": 60, "camera": 60}, canvas=canvas,
        framings=[framing, {"mode": "fit"}],
    )
    from moviestar.spec import parse_timecode_string
    spans = [(s["source"], parse_timecode_string(s["source_from"], "from"),
              parse_timecode_string(s["source_to"], "to"))
             for s in result["composition"]]
    assert spans == [("speaker", a, b) for a, b in expected] + [("camera", 20, 30)]
    assert [s["framing"] for s in result["composition"]] == (
        [framing] * len(expected) + [{"mode": "fit"}]
    )
    assert result["composition_canvas"] == canvas
    assert spec == before


def test_concat_preserves_multiple_cuts_and_snapshot_after_later_edits():
    spec = append_cut(_edited_source(), "speaker", 1, 2, 60)
    result = set_composition(spec, [("speaker", 0, 9)], {"speaker": 60})
    assert [(s["source_from"], s["source_to"]) for s in result["composition"]] == [
        ("0:00:15.000", "0:00:16.000"),
        ("0:00:17.000", "0:00:20.000"),
        ("0:00:25.000", "0:00:30.000"),
    ]
    later = append_trim(result, "speaker", 0, 1, 60)
    assert later["composition"] == result["composition"]
    assert resolve_source(later, "speaker", 60).effective_duration == 1


def test_concat_audio_from_uses_retained_duration_and_rejects_underflow():
    spec = _edited_source()
    result = set_composition(
        spec, [("speaker", 0, 7), ("camera", 0, 3)],
        {"speaker": 60, "camera": 60}, audio_from="speaker",
    )
    assert result["composition_audio_from"] == "speaker"
    from moviestar.spec import parse_timecode_string
    assert sum(parse_timecode_string(s["source_to"], "to") -
               parse_timecode_string(s["source_from"], "from")
               for s in result["composition"]) == 10
    with pytest.raises(SpecValidationError, match="Audio would run out"):
        set_composition(spec, [("speaker", 0, 10), ("camera", 0, 1)],
                        {"speaker": 60, "camera": 60}, audio_from="speaker")


@pytest.fixture(scope="module")
def cut_seam_media(tmp_path_factory):
    path = tmp_path_factory.mktemp("cut-seam") / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=blue:s=160x120:r=10:d=6,"
        "drawbox=c=red:t=fill:enable='gte(t,2)*lt(t,4)',"
        "drawbox=c=green:t=fill:enable='gte(t,4)'",
        "-f", "lavfi", "-i",
        "aevalsrc='0.15*sin(2*PI*if(lt(t,2),440,if(lt(t,4),1760,880))*t)':s=48000:d=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path),
    ], check=True)
    return path


@pytest.mark.parametrize("routed_audio", [False, True])
def test_concat_cut_seam_cli_receipt_undo_and_encoded_media(
    tmp_path, monkeypatch, cut_seam_media, routed_audio,
):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    def invoke(*args):
        result = runner.invoke(cli, list(args))
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    invoke("load", str(cut_seam_media), str(cut_seam_media), "--as", "speaker",
           "--as", "camera", "--no-frames", "--no-transcribe")
    invoke("trim", "--source", "speaker", "--from", "0.5", "--to", "5.5")
    invoke("cut", "--source", "speaker", "--from", "1.5", "--to", "3.5")
    spec_path = tmp_path / "moviestar" / "spec.json"
    before = spec_path.read_bytes()
    args = ["concat", "--segment", "speaker", "--from", "0", "--to", "2",
            "--segment", "camera", "--from", "0", "--to", "1"]
    if routed_audio:
        args += ["--audio-from", "speaker"]
    dry = invoke(*args, "--dry-run")
    assert spec_path.read_bytes() == before
    assert dry["segments_count"] == 3
    assert dry["composition_duration"]["seconds"] == 3
    saved = invoke(*args)
    assert saved["composition"] == dry["composition"]
    assert saved["composition_duration"] == dry["composition_duration"]
    assert saved.get("audio_from") == ("speaker" if routed_audio else None)
    output = tmp_path / "out.mp4"
    invoke("export", "--out", str(output), "--video-encoder", "x264")
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output),
    ]))
    for stream in probe["streams"]:
        assert float(stream["duration"]) == pytest.approx(3, abs=0.11)
    # Decode delivered frames independently of the renderer's filter plan.
    rgb = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(output), "-vf", "fps=10,scale=1:1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    frames = list(zip(rgb[::3], rgb[1::3], rgb[2::3]))
    assert len(frames) == 30
    assert all(b > 200 for r, g, b in frames[:15])  # retained blue opening
    assert all(g > 80 for r, g, b in frames[15:20])  # retained green after the cut
    assert all(b > 200 for r, g, b in frames[20:])  # camera blue
    assert all(r < 40 for r, g, b in frames)  # no discarded red footage
    pcm = array.array("f", subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(output), "-vn", "-ac", "1",
        "-ar", "48000", "-f", "f32le", "-",
    ]))
    for at, frequency in [(0.6, 440), (1.7, 880), (2.5, 880 if routed_audio else 440)]:
        samples = pcm[int(at * 48000):int((at + .2) * 48000)]
        def energy(hz):
            real = sum(s * math.cos(2 * math.pi * hz * i / 48000)
                       for i, s in enumerate(samples))
            imag = sum(s * math.sin(2 * math.pi * hz * i / 48000)
                       for i, s in enumerate(samples))
            return real ** 2 + imag ** 2
        energies = {hz: energy(hz) for hz in (440, 880, 1760)}
        assert energies[frequency] > 100 * max(
            value for hz, value in energies.items() if hz != frequency
        )
    invoke("undo")
    old, restored = json.loads(before), json.loads(spec_path.read_bytes())
    for field in ("sources", "composition", "composition_audio_from"):
        assert restored.get(field) == old.get(field)
