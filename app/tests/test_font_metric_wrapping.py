"""Selected-font wrapping regressions, including real FFmpeg rendering (#98)."""

import struct
import subprocess

import pytest

from moviestar.ffmpeg import build_overlay_filter_parts
from moviestar.fonts import add_font
from moviestar.overlays import overlay_plan_summary, plan_overlays


def _test_font(path, *, advance=300, italic=False):
    """Build an original tiny outline font; no third-party font assets needed.

    All printable non-space ASCII characters use one simple outline. The italic
    variant deliberately overhangs both side bearings, independently of advance.
    """
    def words(*values):
        return struct.pack('>' + 'H' * len(values), *values)

    def checksum(data):
        data += b'\0' * (-len(data) % 4)
        return sum(struct.unpack('>' + 'I' * (len(data) // 4), data)) & 0xffffffff

    left, right = (-100, advance + 200) if italic else (0, advance - 50)
    glyph = struct.pack('>hhhhh', 1, left, 0, right, 700)
    glyph += words(3, 0) + b'\x01' * 4
    xs = (left, advance, 300, -advance) if italic else (left, right-left, 0, left-right)
    glyph += struct.pack('>hhhhhhhh', *xs, 0, 0, 700, 0)
    glyph += b'\0' * (-len(glyph) % 4)
    head = bytearray(54)
    struct.pack_into('>IIIIHH', head, 0, 0x10000, 0x10000, 0, 0x5f0f3cf5, 0, 1000)
    struct.pack_into('>hhhhHHhhh', head, 36, left, 0, right, 700, 2 if italic else 0, 8, 2, 1, 0)
    hhea = bytearray(36)
    struct.pack_into('>IhhhH', hhea, 0, 0x10000, 800, -200, 0, advance)
    struct.pack_into('>H', hhea, 34, 3)
    # A format-4 cmap maps printable ASCII to glyph 1, except space (glyph 2).
    cmap4 = words(4, 32 + 95 * 2, 0, 4, 4, 1, 0, 126, 65535,
                  0, 32, 65535, 0, 1, 4, 0, 2, *([1] * 94))
    family = 'Wrapping Italic' if italic else f'Wrapping {advance}'
    name = family.encode('utf-16-be')
    name_table = words(0, 1, 18, 3, 1, 0x409, 1, len(name), 0) + name
    tables = {
        b'head': bytes(head), b'hhea': bytes(hhea),
        b'maxp': struct.pack('>I', 0x10000) + words(3, 4, 1, *([0] * 11)),
        b'hmtx': words(advance, 0, advance, left & 65535, advance // 2, 0),
        b'loca': struct.pack('>IIII', 0, 0, len(glyph), len(glyph)),
        b'glyf': glyph, b'cmap': words(0, 1, 3, 1) + struct.pack('>I', 12) + cmap4,
        b'name': name_table, b'post': struct.pack('>I', 0x30000) + b'\0' * 28,
    }
    count = len(tables)
    offset = 12 + count * 16
    directory, body = bytearray(), bytearray()
    for tag, data in sorted(tables.items()):
        directory += tag + struct.pack('>III', checksum(data), offset, len(data))
        body += data + b'\0' * (-len(data) % 4)
        offset = 12 + count * 16 + len(body)
    font = bytearray(words(1, 0, count, 128, 3, count*16-128) + directory + body)
    head_entry = sorted(tables).index(b'head')
    head_offset = struct.unpack_from('>I', directory, head_entry * 16 + 8)[0]
    struct.pack_into('>I', font, head_offset + 8, (0xb1b0afba-checksum(font)) & 0xffffffff)
    path.write_bytes(font)
    return family


@pytest.fixture
def fonts(tmp_path, monkeypatch):
    monkeypatch.setattr('moviestar.fonts._USER_FONT_DIR', tmp_path / 'managed')
    monkeypatch.setattr('moviestar.fonts.find_project_dir', lambda: None)
    result = {}
    for key, advance, italic in [('narrow', 300, False), ('wide', 800, False), ('italic', 300, True)]:
        path = tmp_path / f'{key}.ttf'
        family = _test_font(path, advance=advance, italic=italic)
        add_font(path, name=family, scope='user')
        result[key] = family
    return result


def record(family='Inter', text='iii iii', **style):
    return {
        'id': 'manual_0001', 'track': 'titles', 'kind': 'manual',
        'from': '0:00:00.000', 'to': '0:00:01.000', 'text': text,
        'position': {'anchor': 'top-left', 'margin_x': 40, 'margin_y': 40},
        'style': {'resolved': {'font_family': family, 'font_size': 100,
                               'max_width': '300px', **style}},
    }


def planned(record):
    return plan_overlays([record], (1000, 400), 0, 1)['plans'][0]


def pixels(plan):
    graph = ';'.join(build_overlay_filter_parts(
        [plan], (1000, 400), 1, input_label='0:v', output_label='outv', static=True,
    ))
    proc = subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=1000x400:d=1',
        '-filter_complex', graph, '-map', '[outv]', '-frames:v', '1',
        '-pix_fmt', 'gray', '-f', 'rawvideo', '-',
    ], capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode()
    assert len(proc.stdout) == 1000 * 400
    return proc.stdout


def test_short_phrase_that_fits_selected_font_stays_on_one_line():
    plan = planned(record(max_width='200px', font_size=96))
    assert plan['lines'] == ['iii iii']


def test_selected_font_changes_wrapping_and_rendered_lines(fonts):
    narrow = planned(record(fonts['narrow'], 'fit title'))
    wide = planned(record(fonts['wide'], 'fit title'))
    assert narrow['lines'] == ['fit title']
    assert wide['lines'] == ['fit', 'title']
    for plan, expected_bands in [(narrow, 1), (wide, 2)]:
        image = pixels(plan)
        rows = [any(value > 200 for value in image[y*1000:(y+1)*1000]) for y in range(400)]
        assert sum(row and (y == 0 or not rows[y-1]) for y, row in enumerate(rows)) == expected_bands


def test_italic_ink_overhang_does_not_force_extra_line(fonts):
    plan = planned(record(fonts['italic'], 'fit title', max_width='260px'))
    assert plan['lines'] == ['fit title']
    metrics = overlay_plan_summary(plan)['text_metrics']
    assert metrics['method'] == 'harfbuzz'
    assert metrics['lines'][0]['advance_width'] == pytest.approx(255)
    assert metrics['lines'][0]['ink_bounds']['x'] < 0
    assert metrics['lines'][0]['ink_bounds']['width'] > 260
    assert any(value > 200 for value in pixels(plan))


@pytest.mark.parametrize('style, expected', [
    ({'padding': '0px 30px'}, ['fit', 'title']),
    ({'transform': 'scale(2)'}, ['fit', 'title']),
    ({'max_width': '200px'}, ['fit', 'title']),
])
def test_content_width_and_scaled_font_used_before_wrapping(fonts, style, expected):
    assert planned(record(fonts['narrow'], 'fit title', **style))['lines'] == expected


def test_manual_blank_lines_and_overlong_words_preserved(fonts):
    assert planned(record(fonts['narrow'], 'fit\n\ntitle unbreakable', max_width='100px'))['lines'] == [
        'fit', '', 'title', 'unbreakable',
    ]


def test_wide_glyphs_wrap_even_when_character_estimate_would_fit():
    assert planned(record(text='WWW WWW', font_size=96, max_width='600px',
                          font_weight='bold'))['lines'] == ['WWW', 'WWW']


def test_italic_ink_expands_overflow_bounds_without_changing_anchor(fonts):
    overlay = record(fonts['italic'], 'fit title', max_width='260px')
    overlay['position']['margin_x'] = 0
    report = plan_overlays([overlay], (1000, 400), 0, 1)
    [plan] = report['plans']
    assert plan['lines'] == ['fit title']
    assert plan['line_exprs'][0][0] == '0'
    assert plan['estimated_bounds']['x'] == -10
    assert any('overflow' in warning for warning in report['warnings'])


def test_font_replacement_invalidates_measurement_cache(tmp_path):
    from moviestar.text_metrics import TextMeasurer

    path = tmp_path / 'replace.ttf'
    _test_font(path, advance=300)
    assert TextMeasurer(str(path), 100).measure('fit')['advance_width'] == 90
    _test_font(path, advance=800)
    assert TextMeasurer(str(path), 100).measure('fit')['advance_width'] == 240


def test_unmeasurable_face_reports_estimate_without_font_substitution(tmp_path, monkeypatch):
    path = tmp_path / 'unsupported.bdf'
    path.write_text('not an OpenType outline font')
    monkeypatch.setattr('moviestar.overlays.resolve_font', lambda *args: {
        'path': str(path), 'family': 'Bitmap', 'weight': 'normal', 'source': 'system',
    })
    report = plan_overlays([record()], (1000, 400), 0, 1)
    [plan] = report['plans']
    assert plan['font']['path'] == str(path)
    assert plan['text_metrics']['method'] == 'character-estimate'
    assert plan['text_metrics']['estimated'] is True
    assert plan['text_metrics']['fallback_reason']
    assert any('character estimates' in warning for warning in report['warnings'])


def test_padding_that_consumes_max_width_does_not_disable_wrapping(fonts):
    assert planned(record(fonts['narrow'], 'fit title', max_width='40px',
                          padding='30px'))['lines'] == ['fit', 'title']


def test_shaping_applies_kerning_and_agrees_with_drawtext_advance():
    import math
    import re

    from moviestar.fonts import bundled_font_dir
    from moviestar.text_metrics import TextMeasurer

    path = bundled_font_dir() / 'Inter-Regular.ttf'
    measure = TextMeasurer(str(path), 96)
    width = measure.measure('AV')['advance_width']
    assert width < sum(measure.measure(ch)['advance_width'] for ch in 'AV') - 1
    result = subprocess.run([
        'ffmpeg', '-v', 'info', '-f', 'lavfi', '-i', 'color=s=320x160:d=1',
        '-vf', f'drawtext=fontfile={path}:text=AV:fontsize=96:ft_load_flags=no_bitmap:x=print(text_w)',
        '-frames:v', '1', '-f', 'null', '-',
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    measured = [float(x) for x in re.findall(r'(?:^|\] )([0-9]+\.[0-9]+)\s*$', result.stderr, re.MULTILINE)]
    assert measured, result.stderr
    assert measured[0] == pytest.approx(math.ceil(width), abs=1)


def test_screenshot_watch_export_share_wrapping_and_measurement(
    tmp_path, loaded_project, fonts
):
    import json

    from click.testing import CliRunner
    from moviestar.cli import cli

    video = tmp_path / 'black.mp4'
    subprocess.run([
        'ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'color=black:s=1000x400:r=30:d=1', '-c:v', 'mpeg4', str(video),
    ], check=True)
    loaded_project(video, frames=False)
    runner = CliRunner()

    def invoke(*args):
        result = runner.invoke(cli, list(args))
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    invoke('scenes', 'set', '--canvas', '1000x400', '--scene', 'proof=single',
           '--slot', 'proof:main=src_0', '--from', '0', '--to', '1')
    invoke('overlays', 'add', '--text', 'fit title', '--from', '0', '--to', '1',
           '--position', 'top', '--style', 'title', '--css',
           f'font-family: {fonts["narrow"]}; font-size: 100px; max-width: 300px; '
           'padding: 0px; -moviestar-stroke: 0px black')
    spec = tmp_path / 'moviestar' / 'spec.json'
    before = spec.read_bytes()
    measurements = []
    for command, extra, suffix in [
        ('screenshot', ['--at', '0.5'], '.png'),
        ('watch', ['--from', '0', '--to', '1', '--precise'], '.mp4'),
        ('export', [], '.mp4'),
    ]:
        output = tmp_path / (command + suffix)
        dry_run = invoke(command, *extra, '--out', str(output), '--dry-run')
        data = invoke(command, *extra, '--out', str(output))
        for envelope in [dry_run, data]:
            [item] = envelope['overlays']['items']
            assert item['lines'] == ['fit title']
            assert item['text_metrics']['method'] == 'harfbuzz'
            measurements.append(item['text_metrics'])
        image = subprocess.check_output([
            'ffmpeg', '-v', 'error', '-i', str(output), '-frames:v', '1',
            '-pix_fmt', 'gray', '-f', 'rawvideo', '-',
        ])
        rows = [any(v > 200 for v in image[y*1000:(y+1)*1000]) for y in range(400)]
        assert sum(row and (y == 0 or not rows[y-1]) for y, row in enumerate(rows)) == 1
        assert spec.read_bytes() == before
    assert all(item == measurements[0] for item in measurements)
