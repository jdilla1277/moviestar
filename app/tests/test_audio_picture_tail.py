"""Audio replacement must retain reordered picture packets through EOF (#257)."""
import json
import subprocess

import pytest
from click.testing import CliRunner

from moviestar.cli import cli
from moviestar.ffmpeg import build_extract_clip_command, build_mix_audio_command


def run(args):
    return subprocess.run(args, check=True, capture_output=True).stdout


def picture(path):
    data = json.loads(run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_packets',
        '-show_data_hash', 'sha256', '-of', 'json', str(path),
    ]))
    return [(p['data_hash'], p['pts_time'], p['dts_time']) for p in data['packets']]


def decoded(path):
    raw = run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
               '-fps_mode', 'passthrough', '-f', 'framemd5', '-']).decode()
    return [row.split(',')[-1].strip() for row in raw.splitlines() if not row.startswith('#')]


def media(root, frames, offset=0):
    source, cue = root / 'source.mp4', root / 'cue.wav'
    run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
         'testsrc2=s=160x90:r=60', '-frames:v', str(frames), '-c:v', 'libx264',
         '-bf', '3', '-output_ts_offset', str(offset), str(source)])
    return source, cue


@pytest.mark.parametrize('frames,offset,cue_seconds', [
    (782, 0, 1), (782, 0, 15), (180, 0, 1), (180, 0, 5),
    (182, 0.35, 1), (182, 0.35, 5),
])
def test_audio_remux_preserves_every_picture_packet_and_final_decoded_frames(
    tmp_path, frames, offset, cue_seconds,
):
    source, cue = media(tmp_path, frames, offset)
    run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
         f'sine=frequency=440:duration={cue_seconds}', str(cue)])
    info = json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', str(source)]))
    duration = float(info['format']['duration'])
    base, mixed = tmp_path / 'base.mp4', tmp_path / 'mixed.mp4'
    run(build_extract_clip_command(str(source), 0, duration, str(base)))
    # A whole-file copy must already retain the complete encoded picture.
    assert [p[0] for p in picture(base)] == [p[0] for p in picture(source)]
    selected = min(cue_seconds, duration)
    mix = {'source_audio': {'muted': True}, 'tracks': [{
        'id': 'music', 'path': str(cue), 'gain_db': 0, 'muted': False,
        'loop': False, 'fade_in': 0, 'fade_out': 0, 'ducking': None,
        'source_range': {'from': {'seconds': 0}, 'to': {'seconds': selected},
                         'duration': {'seconds': selected}},
        'placed_range': {'from': {'seconds': 0}, 'to': {'seconds': selected},
                         'duration': {'seconds': selected}},
    }]}
    run(build_mix_audio_command(str(base), str(mixed), mix, duration, source_audio_available=False))
    assert picture(mixed) == picture(base), 'audio replacement lost or retimed video packets'
    assert decoded(mixed) == decoded(source), 'ending decoded picture changed or disappeared'
    audio = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                            '-show_streams', '-of', 'json', str(mixed)]))['streams'][0]
    # The graph bounds/pads the authored audio window independently of video.
    assert abs(float(audio['duration']) - duration) <= 1024 / 48000


@pytest.mark.parametrize('profile', ['fast', 'final', 'preview'])
def test_export_audio_replacement_preserves_whole_file_and_authored_trim(
    tmp_path, loaded_project, profile,
):
    source, _ = media(tmp_path, 782)
    loaded_project(source, frames=False)
    runner = CliRunner()
    def invoke(args):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)
    # Mute the original and author the replacement on the result clock.
    invoke(['audio', 'source', '--mute'])
    # Add a real replacement track, so an audio stream is emitted.
    cue = tmp_path / 'music.wav'
    run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
         'sine=frequency=440:duration=15', str(cue)])
    invoke(['audio', 'add', str(cue), '--as', 'music', '--kind', 'music'])
    for trimmed in [False, True]:
        if trimmed:invoke(['trim', '--from', '1', '--to', '3'])
        out = tmp_path / f'output-{trimmed}.mp4'
        before = (tmp_path / 'moviestar/spec.json').read_bytes()
        flags = ['--fast'] if profile == 'fast' else ['--preview'] if profile == 'preview' else []
        plan = invoke(['export', *flags, '--dry-run', '--out', str(out)])
        result = invoke(['export', *flags, '--out', str(out), '--quiet'])
        assert result['audio_mix_stage'] == 'post_render_remux'
        assert result['ffmpeg_command'] == plan['ffmpeg_command']
        assert (tmp_path / 'moviestar/spec.json').read_bytes() == before
        if not trimmed and profile == 'fast':
            assert picture(out) == picture(source)
            assert decoded(out) == decoded(source)
        else:
            # Compare the intended picture pass, keeping existing fast-trim
            # keyframe semantics and final/preview encoding profiles intact.
            base = tmp_path / 'trim-control.mp4'
            command = list(plan['ffmpeg_command']);command[-1] = str(base)
            run(command)
            assert picture(out) == picture(base)
            assert decoded(out) == decoded(base)
            if not trimmed:assert len(decoded(out)) == 782
