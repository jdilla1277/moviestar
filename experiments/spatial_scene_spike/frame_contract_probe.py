"""Drive screenshot and export from a fresh browser frame/motion description."""

import hashlib
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
frames = root / "frames"
frames.mkdir(exist_ok=True)
video = root / "frame_contract_red.mp4"
if not video.exists():
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
        "-i", "color=c=red:s=500x240:r=30:d=6", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(video),
    ], check=True)

motion = {"translate_x": [{"at": 0, "px": 0}, {"at": 2, "px": 40},
                          {"at": 5, "px": 0}]}
contract = {"asset": str(video), "sourceStart": 1, "duration": 5, "motion": motion}
(root / "frame_contract.html").write_text(f'''<!doctype html><html><head><style>
html,body{{margin:0;width:100%;height:100%;background:#101b2b;color:#fff}}
.stage{{position:absolute;inset:0;background:#101b2b;overflow:hidden}}
.title{{position:absolute;top:38px;left:42px;font:bold 38px sans-serif}}
.media{{position:absolute;left:calc(8vw + var(--shift,0px));top:18vh;
 width:min(40vw,500px);height:36vh;transform:translateX(var(--move,0px))}}
.media video{{width:100%;height:100%;object-fit:fill}}
.graphics,.graphics .stage{{background:transparent!important}}
.graphics video{{visibility:hidden}}
</style></head><body><div class="stage"><div class="title">FRAME CONTRACT</div>
<div class="media" data-node="video"><video muted playsinline preload="auto"
src="{video.as_uri()}"></video></div></div><script>
window.contract={json.dumps(contract)};
function offset(t){{const points=window.contract.motion.translate_x;
 for(let i=1;i<points.length;i++)if(t<=points[i].at){{const a=points[i-1],b=points[i];
  return a.px+(b.px-a.px)*(t-a.at)/(b.at-a.at)}}
 return points[points.length-1].px;}}
window.describeFrame=async function(t,shift){{
 document.documentElement.className='';document.body.className='';
 document.documentElement.style.setProperty('--shift',shift+'px');
 document.documentElement.style.setProperty('--move',offset(t)+'px');
 const v=document.querySelector('video'),sourceTime=window.contract.sourceStart+t;
 if(Math.abs(v.currentTime-sourceTime)>1/1000)await new Promise((resolve,reject)=>{{
  const timer=setTimeout(()=>reject(new Error('video seek did not complete')),2000);
  v.addEventListener('seeked',()=>{{clearTimeout(timer);resolve()}},{{once:true}});
  v.addEventListener('error',()=>{{clearTimeout(timer);reject(new Error('video seek failed'))}},{{once:true}});
  v.currentTime=sourceTime;
 }});
 const r=document.querySelector('.media').getBoundingClientRect();
 return {{box:{{x:r.x,y:r.y,width:r.width,height:r.height}},sourceTime}};
}};
window.showGraphics=function(){{document.documentElement.className='graphics';
 document.body.className='graphics'}};
</script></body></html>''')


def motion_expression(points):
    """Compile linear keyframes without a second handwritten motion formula."""
    slopes = [(b["px"] - a["px"]) / (b["at"] - a["at"])
              for a, b in zip(points, points[1:])]
    expression = f'{points[0]["px"]}+({slopes[0]})*t'
    for index in range(1, len(points) - 1):
        change = (slopes[index] - slopes[index - 1]) / 2
        at = points[index]["at"]
        expression += f'+({change})*((t-{at})+abs(t-{at}))'
    return expression


def render(description_file, mode):
    raw_description = description_file.read_bytes()
    description = json.loads(raw_description)
    width = description["canvas"]["width"]
    height = description["canvas"]["height"]
    box = description["sample_box"] if mode == "preview" else description["base_box"]
    x, y, media_width, media_height = (round(box[key]) for key in ("x", "y", "width", "height"))
    if mode == "export":
        x_value = f"'{x}+{motion_expression(description['motion']['translate_x'])}'"
        source_time = description["source_start_s"]
        duration = description["duration_s"]
        output = frames / f'frame-contract-{description["name"]}-export.mp4'
    else:
        x_value = str(x)
        source_time = description["source_time_s"]
        duration = 1 / 30
        output = frames / f'frame-contract-{description["name"]}-preview.png'
    graph = (
        f'[1:v]setpts=PTS-STARTPTS,scale={media_width}:{media_height}[media];'
        f'[0:v][media]overlay=x={x_value}:y={y}:format=rgb:shortest=1[withvideo];'
        '[withvideo][2:v]overlay=0:0:format=rgb:shortest=1[out]'
    )
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=0x101b2b:s={width}x{height}:r=30:d={duration}",
        "-ss", str(source_time), "-t", str(duration), "-i", description["asset"],
        "-loop", "1", "-framerate", "30", "-i", description["graphics"],
        "-filter_complex", graph, "-map", "[out]", "-r", "30", "-t", str(duration),
    ]
    if mode == "export":
        command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    else:
        command.extend(["-frames:v", "1"])
    command.append(str(output))
    subprocess.run(command, check=True, capture_output=True)
    return output, hashlib.sha256(raw_description).hexdigest()


def decoded_image(path, frame=None):
    command = ["ffmpeg", "-v", "error", "-i", str(path)]
    if frame is not None:
        command.extend(["-vf", f"select=eq(n\\,{frame})"])
    command.extend(["-frames:v", "1", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "pipe:1"])
    return subprocess.check_output(command)


def red_left(pixels, canvas, box):
    width = canvas["width"]
    y = round(box["y"] + box["height"] / 2)
    start = max(0, round(box["x"]) - 5)
    return next(x for x in range(start, min(width, start + 40)) if (
        (lambda color: color[0] > 150 and color[1] < 80 and color[2] < 80)(
            pixels[(y * width + x) * 3:(y * width + x) * 3 + 3]
        )
    ))


if __name__ == "__main__":
    results = []
    for name, width, height, sample_time, shift in [
        ("landscape", 1280, 720, 1.25, 0),
        ("portrait_shifted", 720, 1280, 4.5, 100),
    ]:
        subprocess.run([
            "node", str(root / "browser_probe.mjs"), "--frame-contract",
            "--name", name, "--width", str(width), "--height", str(height),
            "--time", str(sample_time), "--shift", str(shift),
        ], check=True, capture_output=True)
        description_file = root / f"frame_contract_{name}.json"
        description = json.loads(description_file.read_text())
        preview, preview_hash = render(description_file, "preview")
        export, export_hash = render(description_file, "export")
        reference_edge = red_left(decoded_image(description["reference"]),
                                  description["canvas"], description["sample_box"])
        preview_edge = red_left(decoded_image(preview), description["canvas"],
                                description["sample_box"])
        export_edge = red_left(decoded_image(export, round(sample_time * 30)),
                               description["canvas"], description["sample_box"])
        expected = description["sample_box"]["x"]
        results.append({
            "canvas": f"{width}x{height}", "sample_time_s": sample_time,
            "source_time_s": description["source_time_s"],
            "measured_box": description["sample_box"],
            "browser_edge_error_px": round(abs(reference_edge - expected), 2),
            "preview_edge_error_px": round(abs(preview_edge - expected), 2),
            "export_edge_error_px": round(abs(export_edge - expected), 2),
            "description_used_for_preview": preview_hash,
            "description_used_for_export": export_hash,
        })
    print(json.dumps({"cases": results}))
