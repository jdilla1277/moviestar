"""Bounded canonical audio preparation; pictures remain native source playback."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from moviestar.audio_surface import resolve_audio_mix
from moviestar.ffmpeg import (_audio_from_filter_parts, build_mix_audio_window_command,
    AUDIO_MIX_SAMPLE_RATE, AUDIO_MIX_CHANNEL_LAYOUT, AUDIO_MIX_SAMPLE_FORMAT)


def compile_audio(snapshot, timeline, start, end, fail):
    from moviestar.cli import _audio_probe_file
    root=Path(snapshot['root']);spec=snapshot['spec']
    try:
        mix=resolve_audio_mix(spec['audio_mix'],result_duration=timeline.duration_s,
            base_dir=root,probe_audio=_audio_probe_file)
    except ValueError as exc:
        fail(str(exc))
    if mix['source_audio']['fade_in'] or mix['source_audio']['fade_out']:
        fail('source audio fades are not implemented in native review')
    selected=[]
    for track in mix['tracks']:
        left=max(start,track['placed_range']['from']['seconds'])
        right=min(end,track['placed_range']['to']['seconds'])
        if right<=left:continue
        if track['loop']:fail('audio looping is not implemented in native review')
        if track['ducking']:fail('audio ducking is not implemented in native review')
        if track['fade_in'] or track['fade_out']:fail('audio track fades are not implemented in native review')
        source=track['source_range']['from']['seconds']+left-track['placed_range']['from']['seconds']
        selected.append({'id':track['id'],'kind':track['kind'],'gain_db':track['gain_db'],'muted':track['muted'],
            'result_range':{'from':left,'to':right},'source_range':{'from':source,'to':source+right-left}})
    if sum(not t['muted'] for t in selected)>1:
        fail('native review supports one independent audio track in the selected window')
    metadata={s['id']:s for s in snapshot['project']['sources']}
    paths={s['id']:(root/s['path']).resolve() for s in spec['sources']}
    pieces=[]
    for span in timeline.spans_overlapping(start,end):
        left,right=max(start,span.start_s),min(end,span.end_s)
        route=span.audio
        audible=not mix['source_audio']['muted'] and bool(metadata.get(route.source_id,{}).get('audio_codec'))
        if audible and (len(route.slices)!=1 or abs(route.speed-1)>1e-8):
            fail('routed source audio requires an ordinary unit-speed clock')
        at=route.slices[0][0]+left-span.start_s if len(route.slices)==1 and abs(route.speed-1)<1e-8 else 0
        if audible and route.slices[0][1]+1e-6<at+right-left:
            fail('routed source audio does not cover the review window')
        path=paths.get(route.source_id) if audible else None
        if audible and (path is None or not path.is_file()):fail('unavailable routed audio source')
        pieces.append({'source_id':route.source_id,'path':str(path) if path else None,
            'source_start':at,'duration':right-left,'result_from':left})
    job={'kind':'audio_job','mix':mix,'source_pieces':pieces,'duration':end-start,
        'result_duration':timeline.duration_s,'window_start':start}
    identity=sha256(json.dumps(job,sort_keys=True).encode()).hexdigest()[:24]
    plane={'job':identity,'duration':end-start,'sample_rate':AUDIO_MIX_SAMPLE_RATE,'channels':2,
        'source_audio':mix['source_audio'],'tracks':selected,
        'source_ranges':[{k:v for k,v in p.items() if k!='path'} for p in pieces]}
    return plane,(identity,job)


def prepare_audio(job,directory):
    """Decode only <=10 seconds of audio, using the export mix and limiter."""
    directory=Path(directory);directory.mkdir()
    base=directory/'routed.wav';output=directory/'mix.wav'
    args=['ffmpeg','-v','error','-y','-nostdin','-filter_complex_threads','1']
    slices=[]
    for piece in job['source_pieces']:
        at,duration=piece['source_start'],piece['duration']
        if piece['path']:
            args+=['-ss',str(at),'-t',str(duration),'-i',piece['path']]
        else:
            args+=['-f','lavfi','-t',str(duration),'-i','anullsrc=r=48000:cl=stereo']
        slices.append((at,at+duration))
    normalize=f'aresample={AUDIO_MIX_SAMPLE_RATE},aformat=sample_fmts={AUDIO_MIX_SAMPLE_FORMAT}:channel_layouts={AUDIO_MIX_CHANNEL_LAYOUT}'
    parts,label=_audio_from_filter_parts(start_index=0,audio_from_input=('',slices),audio_format=normalize)
    args+=['-filter_complex',';'.join(parts),'-map',label,'-vn','-c:a','pcm_f32le',
        '-ar','48000','-ac','2','-t',str(job['duration']),'-map_metadata','-1',str(base)]
    try:
        subprocess.run(args,check=True,capture_output=True,timeout=20)
        command=build_mix_audio_window_command(str(base),str(output),job['mix'],job['result_duration'],
            source_audio_available=any(p['path'] for p in job['source_pieces']),
            window_start=job['window_start'],window_duration=job['duration'])
        subprocess.run(command,check=True,capture_output=True,timeout=20)
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
        raise ValueError('Native audio preparation failed; use --mode rendered.') from exc
    finally:
        base.unlink(missing_ok=True)
    return output
