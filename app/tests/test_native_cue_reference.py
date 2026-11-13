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
