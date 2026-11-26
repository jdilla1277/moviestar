"""Watch discloses its clock without changing which source pictures it returns."""
import json
from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner
from moviestar.cli import cli


def invoke(runner, *args):
    response = runner.invoke(cli, list(args))
    assert response.exit_code == 0, response.output
    return json.loads(response.stdout)


def first_gray(path, at=0):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(at),
        '-i', str(path), '-frames:v', '1', '-vf', 'scale=64:64',
        '-pix_fmt', 'gray', '-f', 'rawvideo', '-'])


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / 'counter.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        "nullsrc=s=64x64:r=30:d=3,geq=lum='16+N':cb=128:cr=128",
        '-c:v', 'libx264', '-crf', '0', str(source)], check=True)
    runner = CliRunner()
    invoke(runner, 'load', str(source), '--no-transcribe', '--no-frames')
    invoke(runner, 'trim', '--from', '1', '--to', '2')
    return runner, source


def compose(runner, kind):
    if kind == 'scene':
        invoke(runner, 'scenes', 'set', '--canvas', '64x64', '--scene',
               'intro=single', '--slot', 'intro:main=counter', '--from', '0', '--to', '0.5')
    elif kind == 'layout':
        invoke(runner, 'concat', '--canvas', '64x64', '--layout', 'single',
               '--slot', 'main=counter', '--from', '0', '--to', '0.5')


@pytest.mark.parametrize('start', [0, 0.1])
@pytest.mark.parametrize('kind', ['trim', 'scene', 'layout'])
@pytest.mark.parametrize('explicit', [False, True])
def test_watch_clock_and_source_identity_match_pixels_and_dry_run(project, kind, explicit, start):
    runner, source = project
    compose(runner, kind)
    spec_path = Path('moviestar/spec.json')
    before = spec_path.read_bytes()
    args = ['watch', '--from', str(start), '--to', str(start + .25), '--precise', '--out', 'excerpt.mp4']
    if explicit:
        args += ['--source', 'counter']
    dry = invoke(runner, *args, '--dry-run')
    assert not Path('excerpt.mp4').exists()
    real = invoke(runner, *args)
    source_clock = explicit or kind == 'trim'
    expected = first_gray(source, start if source_clock else 1 + start)
    actual = first_gray(real['out'])
    assert abs(sum(actual)/len(actual) - sum(expected)/len(expected)) < 2
    assert spec_path.read_bytes() == before
    assert dry['clock'] == real['clock'] == ('source' if source_clock else 'result')
    assert dry['range']['requested'] == real['range']['requested']
    if source_clock:
        assert dry['source_id'] == real['source_id'] == 'counter'
        assert dry['source'] == real['source'] == str(source.resolve())
    else:
        assert 'source_id' not in real  # A composition can contain multiple sources.
        assert real['scene_windows'][0]['slots'][0]['source'] == 'counter'
        assert real['scene_windows'][0]['slots'][0]['source_range']['from']['seconds'] == pytest.approx(1 + start)


@pytest.mark.parametrize('mode', [[], ['--precise'], ['--review']])
def test_trim_watch_preserves_source_zero_while_export_starts_at_trim(project, mode):
    runner, source = project
    watched = invoke(runner, 'watch', '--from', '0', '--to', '0.25', *mode)
    exported = invoke(runner, 'export', '--out', 'edited.mp4')
    def mean(path, at=0):
        pixels = first_gray(path, at)
        return sum(pixels)/len(pixels)
    assert abs(mean(watched['out']) - mean(source)) < 2
    assert abs(mean(exported['out']) - mean(source, 1)) < 2
    assert abs(mean(exported['out']) - mean(watched['out'])) > 20
    assert watched['clock'] == 'source'


def test_watch_help_explains_trim_only_clock():
    result = CliRunner().invoke(cli, ['watch', '--help'])
    assert result.exit_code == 0
    assert 'trim-only' in result.output
    assert 'clock' in result.output


def test_explicit_watch_identifies_selected_source_in_multi_source_project(project):
    runner, source = project
    alternate = source.with_name('alternate.mp4')
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        'color=white:s=64x64:r=30:d=1','-c:v','libx264',str(alternate)],check=True)
    invoke(runner,'load','--add',str(alternate),'--no-transcribe','--no-frames')
    args=['watch','--source','alternate','--from','0','--to','0.25','--precise']
    dry=invoke(runner,*args,'--dry-run')
    real=invoke(runner,*args)
    assert dry['clock']==real['clock']=='source'
    assert dry['source_id']==real['source_id']=='alternate'
    assert dry['source']==real['source']==str(alternate.resolve())
    pixels=first_gray(real['out'])
    assert sum(pixels)/len(pixels)>250
