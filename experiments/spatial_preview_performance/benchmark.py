"""Capture shareable synthetic compositor inputs, then run paired comparisons."""
from copy import deepcopy
from hashlib import sha256
import argparse
import json
from math import ceil
import os
from pathlib import Path
import platform
import random
from statistics import median
import subprocess
import time

from compositors import fused_ffmpeg, in_process, vectorized, rgb_bytes


def fixture(root):
    from moviestar.project import save_project
    from moviestar.spec import empty_spec, save_spec
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source.mp4"
    if not source.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "testsrc2=size=640x360:rate=30:duration=6", "-f", "lavfi", "-i",
            "sine=frequency=880:sample_rate=48000:duration=6", "-c:v", "libx264",
            "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True)
    spec = empty_spec([{"id":"camera", "path":str(source)}])
    spec["composition_canvas"] = {"preset":None,"width":1920,"height":1080,"aspect_ratio":"16:9"}
    spec["composition"] = [{"id":"review","name":"Review","layout":{"preset":"single"},
        "slots":[{"slot":"main","source":"camera","source_from":"0:00:00.000","source_to":"0:00:05.000"}],"audio_from":"camera"}]
    spec["spatial"] = {"definitions":{},"scenes":{"review":{"layout":{
        "background_color":"#102030","children":[
            {"id":"video","type":"video","content":{"asset_id":"camera","source":{"start":0,"end":5}},
             "layout":{"position":"absolute","left":"7.3%","top":"19.1%","width":"68%","height":"70%"},
             "style":{"object_fit":"cover","border_radius":27,"opacity":.83}},
            {"id":"title","type":"text","content":{"text":"A reusable scene, reviewed locally"},
             "layout":{"position":"absolute","left":"12%","top":"24%","width":"70%"},
             "style":{"font_size":48,"color":"#ffeeaa","opacity":.8}}
        ]}}}}
    project = {"sources":[{"id":"camera","path":str(source),"duration":{"seconds":6},
                "width":640,"height":360,"fps":30,"has_audio":True}]}
    save_spec(spec, cwd=root)
    save_project(project, cwd=root)
    return spec, project


def capture(root, case, spec, project):
    from moviestar.resolved import resolve_project
    from moviestar.spatial_model import compile_spatial_frame
    from moviestar.spatial_render import render_spatial_frame
    from moviestar.spatial_session import BrowserSession
    import moviestar.spatial_render as renderer
    import shutil
    destination = root / case
    destination.mkdir()
    original = renderer._compose
    def save_inputs(temporary, canvas, planes, videos, output):
        original(temporary, canvas, planes, videos, output)
        for file in temporary.glob("*.png"):
            shutil.copyfile(file, destination / file.name)
        data = {"canvas":canvas,"planes":planes,"videos":{identity:{"decoded":value["decoded"].name} for identity,value in videos.items()}}
        (destination / "inputs.json").write_text(json.dumps(data,indent=2))
    renderer._compose = save_inputs
    try:
        timeline = resolve_project(project,spec)
        frame = compile_spatial_frame(spec,timeline,1.25,source_durations={"camera":6})
        with BrowserSession() as browser:
            render_spatial_frame(spec,frame,timeline.canvas,output_path=destination/"reference.png",
                                 project_root=root,browser_session=browser)
    finally:
        renderer._compose = original
    return destination


def compare(directory, runs):
    from moviestar.spatial_render import _compose
    data = json.loads((directory / "inputs.json").read_text())
    videos = {key:{"decoded":directory/value["decoded"]} for key,value in data["videos"].items()}
    functions = {"baseline":_compose,"fused_ffmpeg":fused_ffmpeg,"in_process":in_process,"vectorized":vectorized}
    reference = rgb_bytes(directory/"reference.png")
    expected = sha256(reference).hexdigest()
    rows = {name:{"runs_ms":[],"pixel_exact":True,"max_channel_error":0,"ffmpeg_launches":0} for name in functions}
    rng = random.Random(20261106)
    original_popen = subprocess.Popen
    current = [None]
    def popen(*args,**kwargs):
        command = args[0] if args else kwargs.get("args",[])
        if current[0] and command and Path(command[0]).name=="ffmpeg":
            rows[current[0]]["ffmpeg_launches"]+=1
        return original_popen(*args,**kwargs)
    subprocess.Popen = popen
    try:
        for index in range(runs):
            order = list(functions)
            rng.shuffle(order)
            for name in order:
                current[0] = name
                output = directory / f"{name}.png"
                start = time.perf_counter()
                functions[name](directory,data["canvas"],data["planes"],videos,output)
                rows[name]["runs_ms"].append((time.perf_counter()-start)*1000)
                pixels = rgb_bytes(output)
                if sha256(pixels).hexdigest()!=expected:
                    rows[name]["pixel_exact"] = False
                    rows[name]["max_channel_error"] = max(rows[name]["max_channel_error"],max(abs(a-b) for a,b in zip(reference,pixels)))
                if len(pixels)!=len(reference):
                    raise AssertionError("Output dimensions changed")
    finally:
        subprocess.Popen = original_popen
    for name,row in rows.items():
        row["median_ms"] = median(row["runs_ms"])
        row["p95_ms"] = sorted(row["runs_ms"])[ceil(.95*runs)-1] if runs>=20 else None
        row["median_speedup"] = median(rows["baseline"]["runs_ms"])/row["median_ms"]
        row["paired_median_speedup"] = median(a/b for a,b in zip(rows["baseline"]["runs_ms"],row["runs_ms"]))
    return {"case":directory.name,"canvas":data["canvas"],"planes":len(data["planes"]),"runs":runs,
            "reference_rgb_sha256":expected,"variants":rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True,help="New output directory; generated media is never committed.")
    parser.add_argument("--runs",type=int,default=20)
    args = parser.parse_args()
    if args.runs<1:
        parser.error("--runs must be positive")
    root = args.out.resolve()
    root.mkdir(parents=True,exist_ok=False)
    spec,project = fixture(root)
    cases = [("landscape",deepcopy(spec)),("portrait",deepcopy(spec)),("two-videos",deepcopy(spec)),("moving-video",deepcopy(spec))]
    cases[1][1]["composition_canvas"] = {"preset":None,"width":720,"height":1280,"aspect_ratio":"9:16"}
    second = deepcopy(cases[2][1]["spatial"]["scenes"]["review"]["layout"]["children"][0])
    second.update(id="inset",layout={"position":"absolute","left":"55%","top":"48%","width":"38%","height":"43%","z_index":3})
    second["content"]["source"] = {"start":.2,"end":5.2}
    cases[2][1]["spatial"]["scenes"]["review"]["layout"]["children"].append(second)
    cases[3][1]["spatial"]["scenes"]["review"]["layout"]["children"][0]["motion"] = {
        "translate_y":[{"at":0,"value":0},{"at":2,"value":20}]}
    import av
    environment = {"platform":platform.platform(),"python":platform.python_version(),"cpu_count":os.cpu_count(),
        "load_start":os.getloadavg(),"av":av.__version__,"av_libraries":av.library_versions,
        "commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        "experiment_hashes":{name:sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                             for name in ["benchmark.py","compositors.py"]},
        "ffmpeg":subprocess.check_output(["ffmpeg","-version"],text=True).splitlines()[0],
        "node":subprocess.check_output(["node","--version"],text=True).strip(),
        "chrome":subprocess.check_output([os.environ["MOVIESTAR_CHROME_PATH"],"--version"],text=True).strip(),
        "fonts":"No explicit scene family; host browser default serif. Candidate and reference share the same captured text pixels."}
    report = {"environment":environment,"cases":[],"scope":"Composition only; predecoded media and browser masks. Not edit-to-preview or export throughput."}
    for name,variant in cases:
        directory = capture(root,name,variant,project)
        row = compare(directory,args.runs)
        report["cases"].append(row)
        report["environment"]["load_end"] = os.getloadavg()
        (root/"report.json").write_text(json.dumps(report,indent=2))
        print(json.dumps(row),flush=True)
    report["pixel_acceptance"] = {variant:all(case["variants"][variant]["pixel_exact"] for case in report["cases"])
                                  for variant in ["fused_ffmpeg","in_process","vectorized"]}
    (root/"report.json").write_text(json.dumps(report,indent=2))
    if not all(report["pixel_acceptance"].values()):
        raise SystemExit("Candidate pixel checks failed; see report.json. Timing alone cannot accept a candidate.")


if __name__=="__main__":
    main()
