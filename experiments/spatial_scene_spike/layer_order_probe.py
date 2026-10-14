"""Prove a browser backplate, clipped video, and browser foreground paint order."""

import json
import subprocess
from pathlib import Path

from model_spike import expand

root = Path(__file__).resolve().parent
frames = root / "frames"
frames.mkdir(exist_ok=True)
video = root / "layer_order_red.mp4"
subprocess.run([
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
    "-i", "color=c=red:s=500x240:r=30:d=1", "-c:v", "libx264",
    "-pix_fmt", "yuv420p", str(video),
], check=True)
card = expand({"component": "VideoCard", "props": {
    "source": "layer_order_red", "badge": "ON TOP",
}})
assert 'data-path="VideoCard/card"' in card

(root / "layer_order.html").write_text(f'''<!doctype html><html><head><style>
html,body{{margin:0;width:1280px;height:720px;background:#101b2b}}
.card{{position:absolute;left:80px;top:60px;width:500px;height:360px;
  overflow:hidden;border-radius:28px;background:#ff6600}}
.media{{position:absolute;left:40px;top:80px;width:500px;height:240px;
  border-radius:20px;overflow:hidden}}
.media video{{display:block;width:100%;height:100%;object-fit:fill}}
.badge{{position:absolute;left:230px;top:110px;width:170px;height:80px;
  background:#ffe066;color:#16233d;font:bold 28px sans-serif;
  display:flex;align-items:center;justify-content:center}}
.backplate video,.backplate .badge,.flat video{{visibility:hidden}}
.foreground,.mask{{background:transparent!important}}
.foreground .card,.mask .card{{background:transparent}}
.foreground video,.mask video,.mask .badge{{visibility:hidden}}
.mask .media::after{{content:"";position:absolute;inset:0;background:white}}
</style></head><body>{card}<script>
window.setLayerView=async function(view){{
 document.documentElement.className=view;document.body.className=view;
 const v=document.querySelector('video');
 if(Math.abs(v.currentTime-.5)>.001)await new Promise((resolve,reject)=>{{
  const timer=setTimeout(()=>reject(new Error('video seek timed out')),2000);
  v.addEventListener('seeked',()=>{{clearTimeout(timer);resolve()}},{{once:true}});
  v.addEventListener('error',()=>{{clearTimeout(timer);reject(new Error('video seek failed'))}},{{once:true}});
  v.currentTime=.5;
 }});
 await document.fonts.ready;
 return [...document.querySelectorAll('[data-node]')].map(e=>{{const r=e.getBoundingClientRect();
  return {{id:e.dataset.node,x:r.x,y:r.y,width:r.width,height:r.height}}}});
}};
</script></body></html>''')
subprocess.run(["node", str(root / "browser_probe.mjs"), "--layer-order"], check=True, capture_output=True)
boxes = {box["id"]: box for box in json.loads((root / "layer_order_boxes.json").read_text())}
region = {key: round(boxes["video"][key]) for key in ("x", "y", "width", "height")}
assert region == {"x": 120, "y": 140, "width": 500, "height": 240}

output = frames / "layer-order-ordered.png"
subprocess.run([
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
    "-i", str(frames / "layer-order-backplate.png"),
    "-ss", "0.5", "-i", str(video),
    "-i", str(frames / "layer-order-mask.png"),
    "-i", str(frames / "layer-order-foreground.png"),
    "-filter_complex",
    f'[1:v]scale={region["width"]}:{region["height"]},format=rgba,'
    f'pad=1280:720:{region["x"]}:{region["y"]}:color=black@0[vfull];'
    "[2:v]format=rgba,alphaextract[mask];[vfull][mask]alphamerge[clipped];"
    "[0:v][clipped]overlay=0:0:format=rgb[with_video];"
    "[with_video][3:v]overlay=0:0:format=rgb[out]",
    "-map", "[out]", "-frames:v", "1", str(output),
], check=True, capture_output=True)


def raw(path):
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ])


reference = raw(frames / "layer-order-rendered.png")
ordered = raw(output)
flat = raw(frames / "layer-order-flat.png")


def error(actual, x, y):
    offset = (y * 1280 + x) * 3
    return max(abs(reference[offset + channel] - actual[offset + channel]) for channel in range(3))


metrics = {
    "flat_inside_video_error": error(flat, 200, 200),
    "ordered_inside_video_error": error(ordered, 200, 200),
    "ordered_card_background_error": error(ordered, 90, 130),
    "ordered_foreground_error": error(ordered, 350, 220),
    "ordered_outside_clip_error": max(error(ordered, 590, 200), error(ordered, 81, 61)),
}
print(json.dumps(metrics))
