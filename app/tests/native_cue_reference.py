"""Validate the encoded cue source independently of browser playback."""
import subprocess

import numpy as np


def assert_single_cue_per_window(samples, rate, count=8):
    # Exercise every placement of a 128-sample Web Audio render quantum. A
    # codec-induced notch must not turn one authored tone into two onsets.
    for cue in range(count):
        for phase in range(128):
            first = int((cue + .18) * rate) + phase
            chunk = samples[first:int((cue + .29) * rate)]
            chunks = chunk[:len(chunk) // 128 * 128].reshape(-1, 128)
            active = np.sqrt(np.mean(chunks * chunks, axis=1)) > .03
            starts = np.flatnonzero(active & ~np.r_[False, active[:-1]])
            assert len(starts) == 1, (f'Encoded fixture cue {cue}, quantum phase {phase}: '
                f'expected one onset, found {len(starts)} before browser playback')


def assert_surround_cue_reference(path):
    for rate in (44100, 48000):
        # The center-only cue reaches stereo left at sqrt(1/2); the FR pilot
        # remains separate. Match Web Audio's speaker downmix, not a mono sum.
        data = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
            '-af', 'pan=mono|c0=0.7071067811865476*c2', '-ar', str(rate),
            '-c:a', 'pcm_f32le', '-f', 'f32le', '-'], timeout=20)
        assert_single_cue_per_window(np.frombuffer(data, dtype='<f4'), rate)
