"""Decoded-time contact sheets from immutable rendered-file bytes.

Uses the local storyboard planner/font/grid and inspection's observed PTS.
No account, remote access, asset registration or video rendering.
"""
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import subprocess
import tempfile
from . import ffmpeg


def plan_storyboard(duration_ms):
    from .cli import _storyboard_plan
    if type(duration_ms) is not int or not 100 <= duration_ms <= 30000:
        raise ValueError('unsupported_media')
    p = _storyboard_plan(duration_ms / 1000, 'rendered duration', None, None, None, None)
    return {'from_ms': 0, 'to_ms': duration_ms, 'interval_ms': round(p['interval'] * 1000),
            'interval_auto': True, 'columns': p['columns'], 'rows': p['rows'],
            'frame_count': p['frame_count'], 'requested_times_ms': [round(t * 1000) for t in p['frame_times']]}


def timecode(at_ms):
    seconds, ms = divmod(at_ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}'


def render_storyboard(filename, output, sampling):
    from .cli import _storyboard_font, _missing_drawtext_author_warning
    target = Path(output)
    if target.exists() or target.is_symlink():
        raise FileExistsError(output)
    if not isinstance(sampling, dict) or sampling != plan_storyboard(sampling.get('to_ms')):
        raise ValueError('result_mismatch')
    probe = ffmpeg.run_ffprobe(filename)
    videos = [s for s in probe['streams'] if s.get('codec_type') == 'video']
    if len(videos) != 1:
        raise ValueError('unsupported_media')
    video = videos[0]
    try:
        sar = Fraction(video.get('sample_aspect_ratio', '1:1').replace(':', '/'))
        height = max(1, round(384 * int(video['height']) / (int(video['width']) * sar)))
        width_out, height_out = 384 * sampling['columns'], height * sampling['rows']
        if sar <= 0 or height<48 or max(width_out, height_out) > 16384 or width_out * height_out > 16777216:
            raise ValueError()
    except (ValueError, ZeroDivisionError, TypeError):
        raise ValueError('resource_limit') from None
    font, enabled, _ = _storyboard_font(False)
    if not enabled or not font or _missing_drawtext_author_warning() is not None:
        raise ValueError('labels_unavailable')
    with tempfile.TemporaryDirectory(prefix='moviestar-decoded-sheet-', dir=target.parent) as scratch:
        root = Path(scratch)
        paths, observed, _ = ffmpeg.extract_inspection_frames(
            filename, sampling['interval_ms'] / 1000, root, 'decoded', 384,
            0, sampling['to_ms'] / 1000)
        if len(paths) != sampling['frame_count'] or len(observed) != len(paths):
            raise ValueError('result_mismatch')
        frames = []
        for i, (image, seconds, requested) in enumerate(zip(paths, observed, sampling['requested_times_ms'])):
            at = round(seconds * 1000)
            if not math.isfinite(seconds) or not 0 <= at <= requested:
                raise ValueError('result_mismatch')
            label = timecode(at)
            tile = root / f'tile_{i+1:04d}.jpg'
            command = ffmpeg.build_storyboard_frame_command(str(image), 0, str(tile), 384, label=label, font_path=font)
            # This input is the already selected JPEG, not a video seek.
            seek = command.index('-ss'); del command[seek:seek+2]
            vf = command.index('-vf') + 1
            command[vf] = command[vf].replace('scale=384:-1', f'scale=384:{height}:out_range=full,setsar=1', 1)
            # Normalize JPEG pixel format explicitly: inherited limited-range
            # video metadata otherwise makes some MJPEG encoders reject the tile.
            command[-1:-1] = ['-pix_fmt','yuvj420p','-threads','1','-strict','unofficial']
            subprocess.run(command, capture_output=True, check=True)
            if not tile.is_file() or tile.stat().st_size<4:
                raise ValueError('result_mismatch')
            frames.append({'index': i, 'requested_at_ms': requested, 'at_ms': at, 'timecode': label})
        for i in range(len(paths), sampling['columns'] * sampling['rows']):
            ffmpeg.render_storyboard_padding_tile(384, height, str(root/f'tile_{i+1:04d}.jpg'), label='(end)', font_path=font)
        image = root / 'sheet.jpg'
        ffmpeg.compose_storyboard_tiles(str(root/'tile_%04d.jpg'), sampling['columns'], sampling['rows'], str(image))
        meta = next(s for s in ffmpeg.run_ffprobe(str(image))['streams'] if s.get('codec_type') == 'video')
        data = image.read_bytes()
        if not 4 <= len(data) <= 2097152 or (meta['width'], meta['height']) != (width_out, height_out):
            raise ValueError('resource_limit')
        # No partial/overwritten output: publish only a validated complete JPEG.
        import os
        os.link(image, target)
    return {'selection': 'frame_at_or_before', 'labels': 'decoded_result_time', 'frames': frames,
            'image': {'mime_type': 'image/jpeg', 'width': width_out, 'height': height_out,
                      'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}
