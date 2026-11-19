"""A malformed source cannot masquerade as a native output failure."""
import numpy as np
import pytest

from tests.native_cue_reference import assert_single_cue_per_window


@pytest.mark.parametrize('rate', [44100, 48000])
@pytest.mark.parametrize('damage', [None, 'missing', 'split'])
def test_encoded_cue_oracle_preserves_one_onset_and_detects_damage(rate, damage):
    t=np.arange(rate*2)/rate
    samples=np.where((t%1>=.2)&(t%1<=.2666667),.25*np.sin(2*np.pi*880*t),0)
    if damage=='missing':samples[(t>=.18)&(t<.29)]=0
    if damage=='split':samples[(t>=.249)&(t<.257)]=0
    if damage:
        with pytest.raises(AssertionError,match='before browser playback'):
            assert_single_cue_per_window(samples,rate,count=2)
    else:assert_single_cue_per_window(samples,rate,count=2)


@pytest.fixture(scope='module')
def quiet_pip_source(tmp_path_factory):
    from pathlib import Path
    import subprocess,sys
    root=tmp_path_factory.mktemp('quiet-pip-source')/'project'
    fixture=Path(__file__).parents[2]/'experiments/live_browser_preview/pip.py'
    subprocess.run([sys.executable,str(fixture),'--out',str(root)],check=True,capture_output=True,text=True,timeout=40)
    import json
    project=json.loads((root/'moviestar/project.json').read_text())
    return {s['id']:s['path']for s in project['sources']}


@pytest.mark.parametrize('rate',[44100,48000])
def test_quiet_camera_cue_survives_canonical_stereo_conversion(quiet_pip_source,rate):
    import subprocess
    rms={}
    for name,path in quiet_pip_source.items():
        raw=subprocess.check_output(['ffmpeg','-v','error','-i',path,'-af',
            'aformat=channel_layouts=stereo','-ar',str(rate),'-f','f32le','-'],timeout=20)
        samples=np.frombuffer(raw,dtype='<f4').reshape(-1,2)
        assert_single_cue_per_window(samples[:,0],rate)
        assert_single_cue_per_window(samples[:,1],rate)
        plateau=samples[int(.21*rate):int(.25*rate)]
        rms[name]=float(np.sqrt(np.mean(plateau*plateau)))
    assert rms['camera']==pytest.approx(.25/np.sqrt(2),rel=.06)
    assert rms['other']/rms['camera']==pytest.approx(.25,rel=.06)
