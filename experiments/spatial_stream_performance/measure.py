"""Synthetic multi-camera export: exact frame parity, bounded native/owned RAM.

Run in a fresh output folder. No private media, uploads or persistent render cache.
The parent records every failure; the worker runs the real CLI in its own group.
"""
from copy import deepcopy
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'spatial_preview_performance'))
from profile_preview import Resources,owned_process_rows


def reference_compose(*args, paint_cache=None):
    # The frozen #304 function has no decoded-cache argument. Keep its source
    # untouched and explicitly ignore only the new optional optimization here.
    import reference_composite
    return reference_composite.compose(*args)


def worker(root, output, *, compositor='production'):
    import av
    from moviestar import cli, project, spatial_stream
    from moviestar.spec import load_spec
    from stages import Stages
    from moviestar import spatial_composite
    chosen = spatial_composite
    if compositor == 'reference':
        import reference_composite as chosen
        spatial_composite.compose = reference_compose
    stages = Stages()
    stages.install(compositor=chosen)
    digest = sha256()
    submitted = [0]
    hash_frame = stages.wrap('rgb_stream_hashing', lambda pixels: digest.update(memoryview(pixels).cast('B')))
    original_write = spatial_stream.FrameEncoder.write
    def write_pixels(encoder, pixels):
        hash_frame(pixels)
        original_write(encoder, pixels)
        submitted[0] += 1
    spatial_stream.FrameEncoder.write = write_pixels
    from moviestar.spatial_paint import PaintCache
    original_pixels = PaintCache.pixels
    paint = {'max_per_session_decoded_bytes': 0, 'decoded_budget_bytes': 128*1024*1024}
    def paint_pixels(cache, *args, **kwargs):
        try:
            return original_pixels(cache, *args, **kwargs)
        finally:
            paint['max_per_session_decoded_bytes'] = max(paint['max_per_session_decoded_bytes'], cache.decoded_bytes)
            paint['decoded_budget_bytes'] = cache.max_decoded_bytes
    PaintCache.pixels = paint_pixels
    spec = load_spec(root)
    sources = {str(Path(s['path']).resolve()) for s in spec['sources']}
    original = av.open
    counts = {}
    def opening(path, *args, **kwargs):
        name = str(Path(path).resolve()) if isinstance(path, (str, Path)) else ''
        if name in sources:
            counts[name] = counts.get(name, 0)+1
        return original(path, *args, **kwargs)
    av.open = opening
    project.set_explicit_workspace_root(root)
    try:
        cli.cli.main(['export','--workspace',str(root),'--out',str(output/'movie.mp4'),'--quiet','--video-encoder','x264'],
                     standalone_mode=False)
    finally:
        (output/'source-opens.json').write_text(json.dumps(counts,indent=2))
        (output/'stages.json').write_text(json.dumps(stages.report(),indent=2))
        (output/'rgb-stream.json').write_text(json.dumps({'frames':submitted[0],'sha256':digest.hexdigest()}))
        (output/'paint-cache.json').write_text(json.dumps(paint,indent=2))
    assert len(counts)==len(sources) and set(counts.values())=={1}, counts


def prepare_sources(folder, width, height, duration):
    folder.mkdir()
    first = folder/'camera0.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
        f'testsrc2=s={width}x{height}:r=24:d={duration}', '-c:v','libx264',
        '-preset','fast','-crf','23','-g','48','-bf','2','-refs','3',
        '-pix_fmt','yuv420p','-threads','2',str(first)],check=True)
    # Distinct files/paths and independent decoders/clocks. Identical synthetic
    # pictures make the fixture reproducible, not a shared decoded-frame cache.
    files=[first]
    for i in range(1,5):
        target=folder/f'camera{i}.mp4';shutil.copyfile(first,target);files.append(target)
    return files


def prepare_project(root, files, count, canvas, duration, source_size, source_duration):
    from moviestar.spec import empty_spec,save_spec
    from moviestar.project import save_project
    root.mkdir()
    sources=[{'id':f'camera{i}','path':str(path)} for i,path in enumerate(files[:count])]
    spec=empty_spec(sources)
    spec['composition_canvas']={'preset':None,'width':canvas[0],'height':canvas[1],'aspect_ratio':'16:9'}
    spec['composition']=[{'id':'load','name':'Load','layout':{'preset':'single'},'slots':[
        {'slot':'main','source':'camera0','source_from':'0:00:00.000','source_to':f'0:00:{duration:06.3f}'}]}]
    spec['spatial']={'assets':{},'definitions':{},'scenes':{'load':{'layout':{
        'display':'grid','columns':'1fr 1fr 1fr','rows':'1fr 1fr','gap':8,'padding':8,
        'background_color':'#102030','children':[
            {'id':f'video{i}','type':'video','content':{'asset_id':f'camera{i}',
                'source':{'start':i/24,'end':i/24+duration}},
             'layout':{'width':'100%','height':'100%'},
             'style':{'object_fit':'cover','border_radius':12}}
            for i in range(count)]}}}}
    project={'sources':[{**s,'duration':{'seconds':source_duration},'fps':24,
        'width':source_size[0],'height':source_size[1],'has_audio':False} for s in sources]}
    save_spec(spec,cwd=root);save_project(project,cwd=root)
    return spec,project


def frame_parity(root, spec, project, folder):
    import av
    from moviestar.resolved import resolve_project
    from moviestar.spatial_model import compile_spatial_frame
    from moviestar.spatial_render import render_spatial_frame,render_spatial_pixels
    from moviestar.spatial_session import BrowserSession
    from moviestar.spatial_media import MediaSession
    from moviestar import spatial_composite
    import reference_composite
    timeline=resolve_project(project,spec)
    durations={s['id']:s['duration']['seconds'] for s in project['sources']}
    frame=compile_spatial_frame(spec,timeline,.25,source_durations=durations)
    path=folder/'reference.png'
    with BrowserSession() as browser,MediaSession() as media:
        original = spatial_composite.compose
        try:
            spatial_composite.compose = reference_compose
            render_spatial_frame(spec,frame,timeline.canvas,output_path=path,
                project_root=root,browser_session=browser,media_session=media)
        finally:
            spatial_composite.compose = original
        direct=render_spatial_pixels(spec,frame,timeline.canvas,project_root=root,
            browser_session=browser,media_session=media)
        actual=sha256(direct.to_ndarray(format='rgb24').tobytes()).hexdigest()
    with av.open(str(path)) as image:
        expected=sha256(next(image.decode(video=0)).to_ndarray(format='rgb24').tobytes()).hexdigest()
    assert actual==expected,'4K source/direct-compositor pixel mismatch'
    path.unlink()
    return actual


def run_case(root, folder, timeout, *, parity=False, compositor='production'):
    folder.mkdir();(folder/'tmp').mkdir()
    before={p.name:sha256(p.read_bytes()).hexdigest() for p in (root/'moviestar').glob('*.json')}
    start=time.perf_counter()
    with (folder/'stdout').open('wb') as stdout,(folder/'stderr').open('wb') as stderr:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker',str(root),
            '--out',str(folder),'--compositor',compositor, *(['--parity'] if parity else [])],stdout=stdout,stderr=stderr,start_new_session=True,
            env={**os.environ,'TMPDIR':str(folder/'tmp')})
        monitor=Resources(process.pid,folder);monitor.start()
        try:
            code=process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code=124
        finally:
            # Normal CLI cancellation reaps its encoder groups. On a harness
            # deadline, terminate tracked descendants too, before the parent.
            if process.poll() is None:
                groups={group for pid,parent,group,rss in owned_process_rows(process.pid)}
                for group in groups-{os.getpgrp()}:
                    try:os.killpg(group,signal.SIGKILL)
                    except ProcessLookupError:pass
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait();monitor.close()
    report={'exit_code':code,'elapsed_s':time.perf_counter()-start,'resources':monitor.report(),
            'compositor':compositor}
    (folder/'result.json').write_text(json.dumps(report,indent=2))
    assert code==0,((folder/'stderr').read_text()+(folder/'stdout').read_text())[-4000:]
    assert before=={p.name:sha256(p.read_bytes()).hexdigest() for p in (root/'moviestar').glob('*.json')}
    assert not list(folder.rglob('frame*.png')),'unbounded output PNG sequence returned'
    if parity:
        report['parity']=json.loads((folder/'parity.json').read_text())
        (folder/'result.json').write_text(json.dumps(report,indent=2))
        return report
    report['probe']=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(folder/'movie.mp4')],text=True))
    report['source_opens']=json.loads((folder/'source-opens.json').read_text())
    report['stage_profile']=json.loads((folder/'stages.json').read_text())
    report['rgb_stream']=json.loads((folder/'rgb-stream.json').read_text())
    report['paint_cache']=json.loads((folder/'paint-cache.json').read_text())
    (folder/'result.json').write_text(json.dumps(report,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--parity',action='store_true')
    parser.add_argument('--compositor',choices=['production','reference'],default='production')
    parser.add_argument('--compare-reference',action='store_true',
                        help='Pair each export with the frozen full-canvas compositor')
    parser.add_argument('--short',type=int,default=4)
    parser.add_argument('--long',type=int,default=16)
    parser.add_argument('--source-width',type=int,default=3840)
    parser.add_argument('--source-height',type=int,default=2160)
    parser.add_argument('--canvas',action='append',choices=['1080p','4k'],
                        help='Limit smoke tests; acceptance defaults to both sizes')
    parser.add_argument('--sources',action='append',type=int,choices=[4,5],
                        help='Select a CI cell; defaults to both source counts')
    parser.add_argument('--timeout',type=int,default=1800)
    parser.add_argument('--require-limit-mib',type=int)
    parser.add_argument('--require-cpus',type=int)
    args=parser.parse_args();args.out=args.out.resolve()
    if args.worker:
        if args.parity:
            from moviestar.spec import load_spec
            from moviestar.project import load_project
            digest=frame_parity(args.worker,load_spec(args.worker),load_project(args.worker),args.out)
            (args.out/'parity.json').write_text(json.dumps({'canonical_frame_sha256':digest}))
            return
        worker(args.worker,args.out,compositor=args.compositor);return
    import av,numpy,moviestar
    def cgroup():
        root=Path('/sys/fs/cgroup')
        return {name:(root/name).read_text().strip() for name in
            ['memory.max','memory.peak','memory.current','memory.events','memory.swap.max','memory.swap.current','cpu.max'] if (root/name).is_file()}
    limits=cgroup()
    if args.require_limit_mib:
        assert int(limits['memory.max'])<=args.require_limit_mib*1024*1024,limits
        assert int(limits.get('memory.swap.max','0'))==0,limits
    if args.require_cpus:
        assert len(os.sched_getaffinity(0))==args.require_cpus
    if not 1 <= args.short < args.long:
        parser.error('require 1 <= short < long')
    args.out.mkdir(parents=True,exist_ok=False)
    source_size=args.source_width,args.source_height
    files=prepare_sources(args.out/'sources',*source_size,args.long+2)
    code=Path(moviestar.__file__).parent
    report={'runtime':{'python':sys.version,'av':av.__version__,'numpy':numpy.__version__,
        'modules':{p.name:sha256(p.read_bytes()).hexdigest() for p in code.iterdir() if p.suffix in ('.py','.mjs')}},
        'reference_compositor_sha256':sha256(Path(__file__).with_name('reference_composite.py').read_bytes()).hexdigest(),
        'input':{'source_size':source_size,'fps':24,'codec':'H264/yuv420p','b_frames':2,'refs':3},
        'affinity':sorted(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None,
        'cgroup_before':limits,'cases':{},'reference_cases':{},'notes':['Owned process-tree RSS is sampled, can double-count shared pages.',
        'Python handoff is one frame; native decoder reference frames and encoder lookahead add memory.',
        'Memory comparisons include fresh worker startup; all samples retained.']}
    try:
        for count in (args.sources or [4,5]):
            for size in (args.canvas or ['1080p','4k']):
                canvas={'1080p':(1920,1080),'4k':(3840,2160)}[size]
                key=f'{count}-sources-{canvas[0]}x{canvas[1]}'
                pair={};report['cases'][key]=pair
                reference_pair={}
                if args.compare_reference:
                    report['reference_cases'][key]=reference_pair
                for label,duration in [('short',args.short),('long',args.long)]:
                    name=f'{key}-{label}'
                    root=args.out/f'{name}-project'
                    spec,project=prepare_project(root,files,count,canvas,duration,source_size,args.long+2)
                    variants=([('reference',reference_pair),('production',pair)] if args.compare_reference
                              else [('production',pair)])
                    if label=='long':variants.reverse()
                    paths={}
                    for variant,results in variants:
                        folder=args.out/(name if variant=='production' else 'reference-'+name)
                        result=run_case(root,folder,args.timeout,compositor=variant)
                        video=next(s for s in result['probe']['streams'] if s['codec_type']=='video')
                        assert (video['width'],video['height'],int(video['nb_frames']))==(*canvas,duration*24)
                        assert result['rgb_stream']['frames']==duration*24
                        expected_decodes = (count+1) * (duration*24 if variant=='reference' else 1)
                        assert result['stage_profile']['stages']['paint_png_decode']['calls']==expected_decodes, (name,variant,'paint decode count')
                        cached=result['paint_cache']['max_per_session_decoded_bytes']
                        assert cached <= result['paint_cache']['decoded_budget_bytes']
                        assert (cached > 0) == (variant=='production')
                        results[label]=result
                        paths[variant]=folder/'movie.mp4'
                        if label=='short' and variant=='production':
                            result['canonical_frame_parity']=run_case(root,folder/'parity',args.timeout,parity=True)
                        print(json.dumps({'case':name,'compositor':variant,'elapsed_s':result['elapsed_s'],
                            'resources':result['resources']}),flush=True)
                    if args.compare_reference:
                        hashes={variant:sha256(path.read_bytes()).hexdigest() for variant,path in paths.items()}
                        pair[label]['movie_sha256']=hashes['production']
                        reference_pair[label]['movie_sha256']=hashes['reference']
                        assert hashes['production']==hashes['reference'],(name,'encoded MP4 differs from full-canvas reference')
                        assert pair[label]['rgb_stream']==reference_pair[label]['rgb_stream'],(name,'raw RGB stream differs')
                for variant,results in [('production',pair), *([('reference',reference_pair)] if args.compare_reference else [])]:
                    small=results['short']['resources']['sampled_peak_group_rss_mib']
                    large=results['long']['resources']['sampled_peak_group_rss_mib']
                    # A 4x-duration job cannot retain frames proportional to length.
                    # Allow 256 MiB/25% for allocator/native warm-up and sampling.
                    assert large <= small+max(256,small*.25),(key,variant,small,large,'RAM did not plateau')
    except BaseException as exc:
        report['failure']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['cgroup_after']=cgroup()
        (args.out/'summary.json').write_text(json.dumps(report,indent=2))
    if 'memory.events' in report['cgroup_after']:
        before=dict(line.split() for line in limits['memory.events'].splitlines())
        after=dict(line.split() for line in report['cgroup_after']['memory.events'].splitlines())
        assert int(after.get('oom_kill',0))==int(before.get('oom_kill',0)),report['cgroup_after']
    print('All source-open, frame-count, pixel-parity and memory-plateau checks passed.',flush=True)


if __name__=='__main__':main()
