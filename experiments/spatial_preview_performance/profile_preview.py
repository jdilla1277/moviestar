"""Profile the real completed-preview worker, optionally replacing composition.

This experiment does not alter installed commands. It records generation only;
viewer/navigation/agent-edit latency and combined child memory are separate.
"""
from collections import defaultdict
from functools import wraps
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def worker(request_file, variant):
    import av
    import moviestar.cli as cli
    import moviestar.spatial_render as render
    from moviestar.spatial_session import BrowserSession
    from moviestar.preview_worker import render as run_preview
    from compositors import fused_ffmpeg, in_process, vectorized
    metrics = defaultdict(lambda: {"calls":0,"inclusive_ms":0,"exclusive_ms":0})
    counts = defaultdict(int)
    stack = []
    def timed(name,function):
        @wraps(function)
        def wrapped(*args,**kwargs):
            entry = [time.perf_counter(),0]
            stack.append(entry)
            try:
                return function(*args,**kwargs)
            finally:
                elapsed = (time.perf_counter()-entry[0])*1000
                stack.pop()
                row = metrics[name]
                row["calls"] += 1
                row["inclusive_ms"] += elapsed
                row["exclusive_ms"] += elapsed-entry[1]
                if stack:
                    stack[-1][1] += elapsed
        return wrapped
    chosen = {"baseline":render._compose,"fused_ffmpeg":fused_ffmpeg,
              "in_process":in_process,"vectorized":vectorized}[variant]
    def compose(temporary,canvas,planes,videos,output):
        counts["compose_png_bytes_before_sum"] += sum(path.stat().st_size for path in temporary.glob("*.png"))
        chosen(temporary,canvas,planes,videos,output)
        counts["compose_png_bytes_after_sum"] += sum(path.stat().st_size for path in temporary.glob("*.png"))
    render._compose = timed("composition",compose)
    render._prepare_assets = timed("asset_preparation",render._prepare_assets)
    render._decode = timed("decode",render._decode)
    render._displayed_frame_pts = timed("source_pts_selection",render._displayed_frame_pts)
    render._publish = timed("publish_frame",render._publish)
    browser_run = BrowserSession.run
    def browser_request(self,request):
        result = browser_run(self,request)
        counts["browser_requests"] += 1
        counts["browser_png_captures"] += (1 + len(result.get("video_planes",[]))
            + bool(request.get("layout_output_path")) + bool(request.get("reference_path")))
        return result
    BrowserSession.run = timed("browser_request",browser_request)
    cli.render_layout_video = timed("scene_encode",cli.render_layout_video)
    cli.render_segments = timed("assembly_encode",cli.render_segments)
    popen = subprocess.Popen
    def launch(*args,**kwargs):
        command = args[0] if args else kwargs.get("args",[])
        if command:
            counts["process."+Path(command[0]).name] += 1
        return popen(*args,**kwargs)
    subprocess.Popen = launch
    av_open = av.open
    def open_media(*args,**kwargs):
        path = str(args[0]) if args else str(kwargs.get("file",""))
        counts["av_open.png" if path.lower().endswith(".png") else "av_open.media"] += 1
        return av_open(*args,**kwargs)
    av.open = open_media
    start = time.perf_counter()
    try:
        run_preview(request_file)
    finally:
        report = {"variant":variant,"worker_after_import_ms":(time.perf_counter()-start)*1000,
                  "stages":dict(metrics),"counts":dict(counts),"notes":[
                      "Inclusive stages are nested; do not sum them.",
                      "Worker time excludes interpreter/import startup; parent generation_ms includes it.",
                      "Node process count excludes Chrome descendants launched by Node.",
                      "PNG byte sums observe files before/after composition, not total physical I/O or peak disk.",
                      "No combined child-memory or peak-disk measurement."]}
        (Path(request_file).parent/"profile.json").write_text(json.dumps(report,indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",type=Path)
    parser.add_argument("--out",type=Path)
    parser.add_argument("--variant",choices=["baseline","fused_ffmpeg","in_process","vectorized"],default="baseline")
    parser.add_argument("--worker",type=Path,help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker,args.variant)
        return
    if args.project is None or args.out is None:
        parser.error("--project and --out are required")
    from moviestar.local_preview import read_snapshot
    root,output = args.project.resolve(),args.out.resolve()
    output.mkdir(parents=True,exist_ok=False)
    request = read_snapshot(root,0,5)
    request_file = output/"request.json"
    request_file.write_text(json.dumps(request))
    temporary = output/"tmp"
    temporary.mkdir()
    source_commit = subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    code_hashes = {name:sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                  for name in ["profile_preview.py","compositors.py"]}
    before = sha256((root/"moviestar/spec.json").read_bytes()).hexdigest()
    load_before = os.getloadavg()
    start = time.perf_counter()
    with (output/"worker.log").open("wb") as log:
        process = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),"--worker",str(request_file),
            "--variant",args.variant],cwd=root,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
            env={**os.environ,"TMPDIR":str(temporary)})
        try:
            code = process.wait(timeout=600)
        finally:
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                if process.poll() is None:
                    process.kill()
            process.wait(timeout=2)
    elapsed = (time.perf_counter()-start)*1000
    if code:
        raise RuntimeError((output/"worker.log").read_text()[-3000:])
    if before != sha256((root/"moviestar/spec.json").read_bytes()).hexdigest():
        raise AssertionError("Preview generation changed the saved project")
    report = json.loads((output/"profile.json").read_text())
    probe = json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-of","json",str(output/"preview.mp4")]))
    video = next(row for row in probe["streams"] if row["codec_type"]=="video")
    audio = next(row for row in probe["streams"] if row["codec_type"]=="audio")
    assert (video["width"],video["height"],video["nb_frames"],video["r_frame_rate"])==(1280,720,"150","30/1")
    assert abs(float(audio["duration"])-5)<.05
    report.update(generation_ms=elapsed,state_id=request["state_id"],profile=request["profile"],
        source_commit=source_commit,experiment_hashes=code_hashes,
        load_before=load_before,load_after=os.getloadavg(),
        video={key:video.get(key) for key in ["width","height","nb_frames","r_frame_rate","duration"]},
        audio={key:audio.get(key) for key in ["sample_rate","duration","channels","codec_name"]})
    (output/"profile.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)


if __name__=="__main__":
    main()
