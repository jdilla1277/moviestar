"""Executable source-frame, caption, transition, and render-budget gate.

All media is synthetic. Browser inspection and graphics feed the same FFmpeg
preview/export description; generated artifacts stay ignored by Git.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRAMES = ROOT / "frames"
FRAMES.mkdir(exist_ok=True)
FPS = 30
SOURCE_SIZE = (320, 180)
DURATION_FRAMES = 180
SAMPLES = (44, 45, 46, 74, 75, 76, 82, 89, 90)
TRANSITION_SAMPLES = (74, 75, 76, 82, 89, 90)
FONT = ROOT.parents[1] / "app/src/moviestar/fonts/Inter-Bold.ttf"
DIGITS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}


def paint(frame: bytearray, x: int, y: int, width: int, height: int,
          color: tuple[int, int, int]) -> None:
    row = bytes(color) * width
    for top in range(y, y + height):
        start = (top * SOURCE_SIZE[0] + x) * 3
        frame[start:start + width * 3] = row


def generate_video(path: Path, base_color: tuple[int, int, int]) -> None:
    width, height = SOURCE_SIZE
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
        "-r", str(FPS), "-i", "pipe:0", "-c:v", "libx264",
        "-preset", "ultrafast", "-qp", "0", "-g", "1", "-bf", "0",
        "-pix_fmt", "yuv420p", str(path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame_number in range(210):
            frame = bytearray(bytes(base_color) * (width * height))
            for bit in range(8):
                color = (245, 245, 245) if frame_number & (1 << bit) else (12, 12, 12)
                paint(frame, 16 + 23 * bit, 16, 17, 17, color)
            for digit_index, digit in enumerate(f"{frame_number:03d}"):
                for row_index, row in enumerate(DIGITS[digit]):
                    for column_index, pixel in enumerate(row):
                        if pixel == "1":
                            paint(frame, 215 + digit_index * 21 + column_index * 5,
                                  17 + row_index * 5, 4, 4, (245, 245, 245))
            process.stdin.write(frame)
        process.stdin.close()
        error = process.stderr.read().decode()
        if process.wait() != 0:
            raise RuntimeError(error)
    finally:
        if process.poll() is None:
            process.kill()


def prepare_fixture() -> None:
    source_a = ROOT / "temporal_a.mp4"
    source_b = ROOT / "temporal_b.mp4"
    generate_video(source_a, (205, 36, 35))
    generate_video(source_b, (34, 55, 205))
    config = {
        "fps": FPS, "duration_frames": DURATION_FRAMES,
        "source_start_a_frames": 30, "source_start_b_frames": 15,
        "transition_start_frame": 75, "transition_frames": 15,
        "caption_switch_frame": 45,
        "asset_a": str(source_a), "asset_b": str(source_b),
    }
    page = r'''<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{font-family:Inter;src:url("__FONT_URI__");font-weight:700}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;background:#101b2b;color:white}
.stage{position:absolute;inset:0;overflow:hidden;background:#101b2b;font-family:Inter,sans-serif}
.title{position:absolute;left:6vw;top:3vh;font-size:32px;font-weight:700}
.media{position:absolute;left:6vw;top:12vh;width:min(75vw,640px);aspect-ratio:16/9;overflow:hidden}
.media video{position:absolute;inset:0;width:100%;height:100%;object-fit:fill}
.caption{position:absolute;left:6vw;bottom:5vh;width:88vw;height:110px;background:#203852;
 border-radius:10px;display:flex;align-items:center;justify-content:center;text-align:center;
 font-size:30px;font-weight:700;line-height:1.2}
.cue[hidden]{display:none!important}
html.graphics,body.graphics,.graphics .stage{background:transparent!important}
.graphics .media video{visibility:hidden}
.source-a .incoming,.source-b .outgoing{visibility:hidden}
@media (orientation:portrait){.caption{height:150px;font-size:34px}.media{top:15vh}}
</style></head><body><div class="stage"><div class="title">TEMPORAL GATE</div>
<div class="media" data-node="video"><video class="outgoing" muted playsinline preload="auto"
src="__A_URI__"></video><video class="incoming" muted playsinline preload="auto"
src="__B_URI__"></video></div><div class="caption" data-node="caption">
<span class="cue one">FIRST CAPTION</span>
<span class="cue two" hidden>SECOND CAPTION<br>WITH TWO LINES</span></div></div><script>
window.temporalConfig=__CONFIG__;
async function seekVideo(video,time){
 if(Math.abs(video.currentTime-time)<.001&&video.readyState>=2)return;
 await new Promise((resolve,reject)=>{
  const cleanup=()=>{clearTimeout(timer);video.removeEventListener('seeked',done);
   video.removeEventListener('loadeddata',done);video.removeEventListener('error',fail)};
  const done=()=>{if(Math.abs(video.currentTime-time)>.001)return;cleanup();resolve()};
  const fail=()=>{cleanup();reject(new Error('video seek failed at '+time))};
  const timer=setTimeout(()=>{cleanup();reject(new Error('video seek timed out at '+time))},2000);
  video.addEventListener('seeked',done);video.addEventListener('loadeddata',done);
  video.addEventListener('error',fail);video.currentTime=time;
 });
}
function box(element){const r=element.getBoundingClientRect();
 return {x:r.x,y:r.y,width:r.width,height:r.height}}
function lines(element){const grouped=new Map(),walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);let node;
 while(node=walker.nextNode())for(const match of node.textContent.matchAll(/\S+/g)){
  const range=document.createRange();range.setStart(node,match.index);
  range.setEnd(node,match.index+match[0].length);
  const y=Math.round(range.getBoundingClientRect().y);
  if(!grouped.has(y))grouped.set(y,[]);grouped.get(y).push(match[0]);
 }
 return [...grouped].sort((a,b)=>a[0]-b[0]).map(([y,words])=>({y,text:words.join(' ')}));
}
window.describeTemporalFrame=async function(frame){
 const cfg=window.temporalConfig,fps=cfg.fps;
 document.documentElement.className='';document.body.className='';
 const a=cfg.source_start_a_frames+frame;
 const b=cfg.source_start_b_frames+Math.max(0,frame-cfg.transition_start_frame);
 const outgoing=document.querySelector('.outgoing'),incoming=document.querySelector('.incoming');
 await Promise.all([seekVideo(outgoing,a/fps),seekVideo(incoming,b/fps)]);
 const opacity=Math.max(0,Math.min(1,(frame-cfg.transition_start_frame)/cfg.transition_frames));
 incoming.style.opacity=opacity;
 const cue=frame<cfg.caption_switch_frame?1:2;
 document.querySelector('.one').hidden=cue!==1;document.querySelector('.two').hidden=cue!==2;
 await document.fonts.ready;
 await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 return {frame,scene_time_s:frame/fps,source_frame_a:a,source_frame_b:b,
  actual_time_a:outgoing.currentTime,actual_time_b:incoming.currentTime,
  incoming_opacity:opacity,cue,caption_lines:lines(document.querySelector(cue===1?'.one':'.two')),
  media_box:box(document.querySelector('.media')),caption_box:box(document.querySelector('.caption'))};
};
window.showTemporalGraphics=function(show){
 document.documentElement.classList.toggle('graphics',show);
 document.body.classList.toggle('graphics',show)};
window.showTemporalSource=function(source){
 document.body.className=source==='a'?'source-a':'source-b';
 document.querySelector('.incoming').style.opacity=1};
</script></body></html>'''
    page = (page.replace("__FONT_URI__", FONT.as_uri())
            .replace("__A_URI__", source_a.as_uri())
            .replace("__B_URI__", source_b.as_uri())
            .replace("__CONFIG__", json.dumps(config)))
    (ROOT / "temporal_gate.html").write_text(page)


def run_browser(name: str, width: int, height: int, samples: str) -> dict:
    command = [
        "node", str(ROOT / "browser_probe.mjs"), "--temporal", "--name", name,
        "--width", str(width), "--height", str(height), "--samples", samples,
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def sample_description(path: Path) -> dict:
    return json.loads(path.read_text())


def ffmpeg(command: list[str]) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *command],
                   check=True, capture_output=True)


def render_export(description: dict) -> Path:
    cfg = description["config"]
    canvas = description["canvas"]
    box = description["media_box"]
    width, height = round(box["width"]), round(box["height"])
    x, y = round(box["x"]), round(box["y"])
    output = FRAMES / f'temporal-{description["name"]}-export.mp4'
    graph = (
        "[0:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[a];"
        "[1:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[b];"
        f"[a][b]xfade=transition=fade:duration={cfg['transition_frames']/FPS}:"
        f"offset={cfg['transition_start_frame']/FPS},scale={width}:{height}[video];"
        f"[4:v][video]overlay=x={x}:y={y}:format=rgb:shortest=1[base];"
        f"[base][2:v]overlay=0:0:format=rgb:enable='lt(t,{cfg['caption_switch_frame']/FPS})'[cue1];"
        f"[cue1][3:v]overlay=0:0:format=rgb:enable='gte(t,{cfg['caption_switch_frame']/FPS})'[out]"
    )
    ffmpeg([
        "-ss", str(cfg["source_start_a_frames"] / FPS), "-t", "6", "-i", cfg["asset_a"],
        "-ss", str(cfg["source_start_b_frames"] / FPS), "-t", "6", "-i", cfg["asset_b"],
        "-loop", "1", "-framerate", str(FPS), "-i", description["graphics"]["1"],
        "-loop", "1", "-framerate", str(FPS), "-i", description["graphics"]["2"],
        "-f", "lavfi", "-i", f"color=c=0x101b2b:s={canvas['width']}x{canvas['height']}:r={FPS}:d=6",
        "-filter_complex", graph, "-map", "[out]", "-frames:v", str(DURATION_FRAMES),
        "-r", str(FPS), "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0",
        "-pix_fmt", "yuv420p", str(output),
    ])
    return output


def render_preview(description: dict) -> Path:
    frame_number = int(next(iter(description["samples"])))
    sample = description["samples"][str(frame_number)]
    cfg = description["config"]
    canvas = description["canvas"]
    box = sample["media_box"]
    width, height = round(box["width"]), round(box["height"])
    x, y = round(box["x"]), round(box["y"])
    opacity = sample["incoming_opacity"]
    output = FRAMES / f'temporal-{description["name"]}-preview.png'
    graph = (
        "[0:v]setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];"
        f"[a][b]blend=all_expr='A*(1-{opacity})+B*{opacity}',"
        f"scale={width}:{height}[video];"
        f"[3:v][video]overlay=x={x}:y={y}:format=rgb:shortest=1[base];"
        "[base][2:v]overlay=0:0:format=rgb:shortest=1[out]"
    )
    ffmpeg([
        "-ss", str(sample["source_frame_a"] / FPS), "-t", str(1 / FPS), "-i", cfg["asset_a"],
        "-ss", str(sample["source_frame_b"] / FPS), "-t", str(1 / FPS), "-i", cfg["asset_b"],
        "-loop", "1", "-framerate", str(FPS), "-i", description["graphics"][str(sample["cue"])],
        "-f", "lavfi", "-i", f"color=c=0x101b2b:s={canvas['width']}x{canvas['height']}:r={FPS}:d={1/FPS}",
        "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", str(output),
    ])
    return output


class MemoryMonitor:
    def __init__(self) -> None:
        self.maximum_kib = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self.stop.wait(0.05):
            try:
                rows = subprocess.check_output(
                    ["ps", "-axo", "pid=,ppid=,rss="], text=True
                ).splitlines()
                entries = [tuple(map(int, row.split())) for row in rows if len(row.split()) == 3]
                descendants = {os.getpid()}
                changed = True
                while changed:
                    changed = False
                    for pid, parent, _ in entries:
                        if parent in descendants and pid not in descendants:
                            descendants.add(pid)
                            changed = True
                self.maximum_kib = max(
                    self.maximum_kib,
                    sum(rss for pid, _, rss in entries if pid in descendants),
                )
            except (OSError, subprocess.CalledProcessError, ValueError):
                pass

    def __enter__(self) -> "MemoryMonitor":
        self.thread.start()
        return self

    def __exit__(self, *_unused: object) -> None:
        self.stop.set()
        self.thread.join()


def image_rgb(path: Path) -> bytes:
    return subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ])


def export_rgb(path: Path, canvas: dict, frame_numbers: tuple[int, ...]) -> dict[int, bytes]:
    expression = "+".join(f"eq(n\\,{number})" for number in frame_numbers)
    data = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select={expression}",
        "-fps_mode", "vfr", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ])
    size = canvas["width"] * canvas["height"] * 3
    if len(data) != size * len(frame_numbers):
        raise RuntimeError(f"expected {len(frame_numbers)} decoded frames, got {len(data)//size}")
    return {number: data[index*size:(index+1)*size]
            for index, number in enumerate(frame_numbers)}


def pixel(rgb: bytes, canvas: dict, x: int, y: int) -> tuple[int, int, int]:
    start = (y * canvas["width"] + x) * 3
    return tuple(rgb[start:start + 3])


def barcode(rgb: bytes, canvas: dict, box: dict) -> int:
    value = 0
    for bit in range(8):
        x = round(box["x"] + (24 + 23 * bit) * box["width"] / SOURCE_SIZE[0])
        y = round(box["y"] + 24 * box["height"] / SOURCE_SIZE[1])
        color = pixel(rgb, canvas, x, y)
        if min(color) > 120:
            value |= 1 << bit
    return value


def colored_edge(rgb: bytes, canvas: dict, box: dict, source: str) -> int:
    y = round(box["y"] + box["height"] * 0.65)
    left = max(0, round(box["x"]) - 4)
    for x in range(left, min(canvas["width"], left + 16)):
        red, green, blue = pixel(rgb, canvas, x, y)
        if source == "a" and red > 120 and green < 110 and blue < 110:
            return x
        if source == "b" and blue > 120 and red < 110 and green < 110:
            return x
    raise RuntimeError("no colored video edge at measured box")


def caption_line_count(rgb: bytes, canvas: dict, box: dict) -> int:
    left = round(box["x"]) + 8
    right = round(box["x"] + box["width"]) - 8
    top = round(box["y"]) + 8
    bottom = round(box["y"] + box["height"]) - 8
    active = []
    for y in range(top, bottom):
        bright = 0
        for x in range(left, right):
            if min(pixel(rgb, canvas, x, y)) > 205:
                bright += 1
        if bright >= 3:
            active.append(y)
    groups = []
    for y in active:
        if not groups or y - groups[-1][-1] > 5:
            groups.append([y])
        else:
            groups[-1].append(y)
    return sum(group[-1] - group[0] >= 4 for group in groups)


def caption_glyph_bounds(rgb: bytes, canvas: dict, box: dict) -> tuple[int, int, int, int]:
    left = round(box["x"]) + 8
    right = round(box["x"] + box["width"]) - 8
    top = round(box["y"]) + 8
    bottom = round(box["y"] + box["height"]) - 8
    bounds = [right, bottom, left, top]
    for y in range(top, bottom):
        for x in range(left, right):
            if min(pixel(rgb, canvas, x, y)) > 205:
                bounds = [min(bounds[0], x), min(bounds[1], y),
                          max(bounds[2], x), max(bounds[3], y)]
    if bounds[0] > bounds[2]:
        raise RuntimeError("caption has no visible glyphs")
    return tuple(bounds)


def barcode_pixel_error(reference: bytes, output: bytes, canvas: dict, box: dict) -> int:
    errors = []
    for bit in range(8):
        x = round(box["x"] + (24 + 23 * bit) * box["width"] / SOURCE_SIZE[0])
        y = round(box["y"] + 24 * box["height"] / SOURCE_SIZE[1])
        errors.append(max(abs(a-b) for a, b in
                          zip(pixel(reference, canvas, x, y), pixel(output, canvas, x, y))))
    return max(errors)


def region_p95_error(reference: bytes, output: bytes, canvas: dict, box: dict) -> int:
    errors = []
    for y in range(round(box["y"]) + 5, round(box["y"] + box["height"]) - 5):
        for x in range(round(box["x"]) + 5, round(box["x"] + box["width"]) - 5):
            start = (y * canvas["width"] + x) * 3
            errors.extend(abs(reference[start + channel] - output[start + channel])
                          for channel in range(3))
    errors.sort()
    return errors[int(0.95 * (len(errors) - 1))]


def verify_case(name: str, width: int, height: int) -> tuple[dict, list[str], dict]:
    browser = run_browser(name, width, height, ",".join(map(str, SAMPLES)))
    description = sample_description(ROOT / f"temporal_{name}.json")
    output = render_export(description)
    canvas = description["canvas"]
    samples = {int(key): value for key, value in description["samples"].items()}
    decoded = export_rgb(output, canvas, SAMPLES)
    preview = image_rgb(render_preview(description))
    preview_sample = samples[SAMPLES[0]]
    preview_barcode_error = barcode_pixel_error(
        preview, decoded[SAMPLES[0]], canvas, preview_sample["media_box"])
    preview_caption_bound_error = max(abs(a-b) for a, b in zip(
        caption_glyph_bounds(preview, canvas, preview_sample["caption_box"]),
        caption_glyph_bounds(decoded[SAMPLES[0]], canvas, preview_sample["caption_box"])))
    errors = []
    source_errors = []
    transition_errors = []
    geometry_errors = []
    caption_errors = []
    caption_bound_errors = []
    barcode_errors = []
    visual_lines = []
    for frame_number in SAMPLES:
        sample = samples[frame_number]
        expected_a = 30 + frame_number
        expected_b = 15 + max(0, frame_number - 75)
        if (sample["source_frame_a"], sample["source_frame_b"]) != (expected_a, expected_b):
            errors.append(f"{name} frame {frame_number}: inspection source mapping differs")
        for source, expected in (("a", expected_a), ("b", expected_b)):
            actual = sample[f"actual_time_{source}"] * FPS
            source_errors.append(abs(round(actual) - expected))
            if frame_number in TRANSITION_SAMPLES:
                isolated = image_rgb(FRAMES / f"temporal-{name}-{frame_number}-{source}.png")
                source_errors.append(abs(barcode(isolated, canvas, sample["media_box"]) - expected))
        reference = image_rgb(FRAMES / f"temporal-{name}-{frame_number}-browser.png")
        if frame_number in (44, 74, 90):
            source = "a" if frame_number < 90 else "b"
            expected = expected_a if source == "a" else expected_b
            source_errors.append(abs(barcode(reference, canvas, sample["media_box"]) - expected))
            source_errors.append(abs(barcode(decoded[frame_number], canvas, sample["media_box"]) - expected))
            edge = colored_edge(decoded[frame_number], canvas, sample["media_box"], source)
            geometry_errors.append(abs(edge - sample["media_box"]["x"]))
        if frame_number in TRANSITION_SAMPLES:
            box = sample["media_box"]
            barcode_errors.append(barcode_pixel_error(reference, decoded[frame_number], canvas, box))
            for fraction in (0.45, 0.6, 0.75):
                x = round(box["x"] + box["width"] * fraction)
                y = round(box["y"] + box["height"] * 0.65)
                actual = pixel(decoded[frame_number], canvas, x, y)
                wanted = pixel(reference, canvas, x, y)
                transition_errors.append(max(abs(a-b) for a, b in zip(actual, wanted)))
        if frame_number in (44, 45, 46):
            box = sample["caption_box"]
            caption_errors.append(region_p95_error(reference, decoded[frame_number], canvas, box))
            visual_lines.append(caption_line_count(decoded[frame_number], canvas, box))
            caption_bound_errors.append(max(abs(a-b) for a, b in zip(
                caption_glyph_bounds(reference, canvas, box),
                caption_glyph_bounds(decoded[frame_number], canvas, box))))
    line_counts = [len(samples[number]["caption_lines"]) for number in (44, 45, 46)]
    boxes = [samples[number]["caption_box"] for number in (44, 45, 46)]
    caption_delta = max(abs(boxes[0][key] - box[key]) for box in boxes[1:]
                        for key in ("x", "y", "width", "height"))
    if line_counts != [1, 2, 2] or visual_lines != [1, 2, 2]:
        errors.append(f"{name}: caption cue did not switch at frame 45: {line_counts}/{visual_lines}")
    result = {
        "canvas": f"{width}x{height}",
        "max_source_frame_error": max(source_errors),
        "max_transition_color_error": max(transition_errors),
        "max_geometry_error_px": round(max(geometry_errors), 2),
        "max_caption_pixel_error": max(caption_errors),
        "max_caption_glyph_bound_error_px": max(caption_bound_errors),
        "max_transition_barcode_pixel_error": max(barcode_errors),
        "preview_export_barcode_pixel_error": preview_barcode_error,
        "preview_export_caption_glyph_bound_error_px": preview_caption_bound_error,
        "caption_line_counts_at_boundary": line_counts,
        "export_caption_line_counts": visual_lines,
        "caption_box_delta_px": round(caption_delta, 2),
        "export_bytes": output.stat().st_size,
    }
    if result["max_source_frame_error"] > 1:
        errors.append(f"{name}: source-frame error exceeds one frame")
    if result["max_transition_color_error"] > 20:
        errors.append(f"{name}: transition color mismatch exceeds 20/255")
    if result["max_geometry_error_px"] > 1:
        errors.append(f"{name}: media edge exceeds one pixel")
    if result["max_caption_pixel_error"] > 20 or caption_delta:
        errors.append(f"{name}: caption pixels or reserved box changed")
    if result["max_caption_glyph_bound_error_px"] > 2:
        errors.append(f"{name}: caption glyph bounds differ by more than two pixels")
    if result["max_transition_barcode_pixel_error"] > 20:
        errors.append(f"{name}: encoded transition source frame markers differ from browser")
    if preview_barcode_error > 20 or preview_caption_bound_error > 2:
        errors.append(f"{name}: preview and export pixels differ at caption boundary")
    return result, errors, browser["versions"]


def measure_performance() -> dict:
    cold = []
    for index in range(5):
        started = time.perf_counter()
        name = f"cold_{index}"
        run_browser(name, 1280, 720, "90")
        render_preview(sample_description(ROOT / f"temporal_{name}.json"))
        cold.append(round((time.perf_counter() - started) * 1000))
    warm = subprocess.run([
        "node", str(ROOT / "browser_probe.mjs"), "--temporal-warm",
        "--name", "warm", "--width", "1280", "--height", "720",
    ], check=True, capture_output=True, text=True)
    warm_result = json.loads(warm.stdout)
    with MemoryMonitor() as memory:
        started = time.perf_counter()
        run_browser("export_budget", 1280, 720, "none")
        description = sample_description(ROOT / "temporal_export_budget.json")
        output = render_export(description)
        elapsed = round((time.perf_counter() - started) * 1000)
    return {
        "cold_preview_p95_ms": max(cold),
        "cold_preview_runs_ms": cold,
        "warm_preview_p95_ms": warm_result["warm_preview_p95_ms"],
        "export_end_to_end_ms": elapsed,
        "peak_rss_mib": round(memory.maximum_kib / 1024),
        "export_bytes": output.stat().st_size,
    }


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--render-preview":
        render_preview(sample_description(Path(sys.argv[2])))
        return
    if sys.argv[1:] != ["--verify"]:
        raise SystemExit("usage: temporal_gate.py --verify | --render-preview DESCRIPTION.json")
    prepare_fixture()
    cases = []
    problems = []
    versions = {}
    for name, width, height in (("landscape", 1280, 720), ("portrait", 720, 1280)):
        case, issues, versions = verify_case(name, width, height)
        cases.append(case)
        problems.extend(issues)
    performance = measure_performance()
    for measure, budget in (("cold_preview_p95_ms", 3000),
                            ("warm_preview_p95_ms", 700),
                            ("export_end_to_end_ms", 6000),
                            ("peak_rss_mib", 2048)):
        if performance[measure] > budget:
            problems.append(f"{measure} {performance[measure]} exceeds {budget}")
    print(json.dumps({"cases": cases, "performance": performance,
                      "versions": versions, "problems": problems}))
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
