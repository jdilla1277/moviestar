"""Compare the captured final browser output with its full canonical PCM window."""
import av
import numpy as np


def verify_pcm_reference(review, path, *, duration):
    metrics=review['metrics'];audio=metrics['prepared_audio'];capture=metrics['audio_pcm']
    assert capture['sample_rate']==48000
    actual=np.asarray(capture['samples'])
    with av.open(str(path)) as container:
        resampler=av.AudioResampler(format='fltp',layout='stereo',rate=48000)
        expected=np.concatenate([frame.to_ndarray() for source in container.decode(audio=0)
            for frame in resampler.resample(source)],axis=1)
    assert actual.ndim==2 and actual.shape[0]==expected.shape[0]==2
    index=round((capture['start_context_time']-audio['started_context_time']+audio['started_offset'])*48000)
    stop=round((metrics['pause_context_time']-capture['start_context_time'])*48000)
    errors=[]
    for shift in (-1,0,1):
        begin=index+shift;left=max(0,-begin)
        right=min(actual.shape[1],expected.shape[1]-begin,stop-128)
        assert right>left
        errors.append((shift,(actual[:,left:right]-expected[:,begin+left:begin+right]).ravel()))
    shift,error=min(errors,key=lambda item:(np.sqrt(np.mean(item[1]*item[1])),abs(item[0])))
    assert len(error)>(duration-.2)*48000*2,'Compare the complete review, allowing only its final stop quantum'
    maximum=float(abs(error).max())
    assert maximum<1e-5,f'Canonical PCM maximum sample error {maximum}'
    return {'compared_stereo_seconds':len(error)/2/48000,'maximum_sample_error':maximum,'sample_shift':shift}
