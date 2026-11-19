"""Odd-height portrait sheets must retain every source tile with legacy FFmpeg."""
import json
import re
import subprocess

from click.testing import CliRunner
from moviestar.cli import cli


def test_portrait_padding_preserves_all_source_cells_with_legacy_color_rounding(monkeypatch, tmp_path):
    source, output = tmp_path / 'portrait.mp4', tmp_path / 'sheet.jpg'
    real_run = subprocess.run
    real_run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
              'color=red:size=108x192:rate=24', '-t', '11', '-c:v', 'libx264',
              '-pix_fmt', 'yuv420p', str(source)], check=True, capture_output=True)

    def legacy_run(command, *args, **kwargs):
        # Older color sources round odd dimensions down before later filters.
        # Keep the actual FFmpeg graph/encoder, emulating only that negotiation.
        if isinstance(command, list) and command[0] == 'ffmpeg' and '-i' in command:
            command = command.copy()
            index = command.index('-i') + 1
            command[index] = re.sub(
                r'(color=.*?:size=)(\d+)x(\d+)',
                lambda m: f'{m[1]}{int(m[2])//2*2}x{int(m[3])//2*2}',
                command[index],
            )
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', legacy_run)
    invoked = CliRunner().invoke(cli, ['storyboard', str(source), '--out', str(output)])
    assert invoked.exit_code == 0, invoked.output
    result = json.loads(invoked.output)
    assert result['labels'] is True
    assert result['frame_count'] == 11
    assert result['padding_cells'] == 1
    assert result['grid'] == {'columns': 4, 'rows': 3}
    assert (result['width'], result['height']) == (1536, 2049)
    rgb = real_run(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 'rawvideo',
                    '-pix_fmt', 'rgb24', '-'], check=True, capture_output=True).stdout
    for index in range(11):
        offset = ((index//4*683+200)*1536 + index%4*384+192)*3
        red, green, blue = rgb[offset:offset+3]
        assert red > 220 and green < 30 and blue < 30, f'source cell {index} disappeared'
    padding = ((2*683+200)*1536+3*384+192)*3
    assert all(25 < channel < 60 for channel in rgb[padding:padding+3])
