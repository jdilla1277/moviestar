"""Experimental alternatives to the shipped per-plane FFmpeg compositor.

These candidates do not change the product renderer. They preserve its 8-bit
media placement and 16-bit visibility-mask arithmetic; the benchmark verifies pixels.
"""
from fractions import Fraction
from pathlib import Path
import shutil


def fused_ffmpeg(temporary, canvas, planes, videos, output):
    from moviestar.spatial_render import _ffmpeg
    if not planes:
        shutil.copyfile(temporary / "base.png", output)
        return
    width, height = canvas["width"], canvas["height"]
    inputs = ["-i", str(temporary / "base.png")]
    graph = ["[0:v]format=gbrp16le,split=" + str(len(planes)+1)
             + "[acc0]" + "".join(f"[base{i}]" for i in range(len(planes)))]
    for i, plane in enumerate(planes):
        rect = plane["rect"]
        inputs += ["-i", str(temporary / f"{plane['id']}-white.png"),
                   "-i", str(videos[plane["id"]]["decoded"])]
        white, source = 1 + i*2, 2 + i*2
        graph += [
            f"[{source}:v]scale={rect['width']}:{rect['height']}:flags=bilinear,format=rgb24[media{i}]",
            f"color=c=black:s={width}x{height}:r=1,format=rgb24[blank{i}]",
            f"[blank{i}][media{i}]overlay=x={rect['x']}:y={rect['y']}:format=rgb:shortest=1,format=rgb24,format=gbrp16le[video{i}]",
            f"[{white}:v]format=gbrp16le[white{i}]",
            f"[white{i}][base{i}]blend=all_expr='max(0,A-B)'[mask{i}]",
            f"[video{i}][mask{i}]blend=all_expr='A*B/65535'[pixels{i}]",
            f"[acc{i}][pixels{i}]blend=all_expr='min(65535,A+B)'[acc{i+1}]",
        ]
    graph.append(f"[acc{len(planes)}]format=rgb24[out]")
    _ffmpeg([*inputs, "-filter_complex", ";".join(graph), "-map", "[out]",
             "-frames:v", "1", "-threads", "1", str(output)])


def _png(path):
    import av
    with av.open(str(path)) as container:
        frame = next(container.decode(video=0))
    frame.pts, frame.time_base = 0, Fraction(1, 25)
    return frame


def rgb_bytes(path):
    frame = _png(path).reformat(format="rgb24")
    plane = frame.planes[0]
    raw = bytes(plane)
    return b"".join(raw[y*plane.line_size:y*plane.line_size+frame.width*3]
                    for y in range(frame.height))


def in_process(temporary, canvas, planes, videos, output):
    import av
    if not planes:
        shutil.copyfile(temporary / "base.png", output)
        return
    graph = av.filter.Graph()
    graph.threads = 1
    sources = []
    def source(frame):
        node = graph.add_buffer(template=frame)
        sources.append((node, frame))
        return node
    def chain(previous, name, args=None, **options):
        node = graph.add(name, args, **options)
        previous.link_to(node)
        return node
    def combine(left, right, name, **options):
        node = graph.add(name, **options)
        left.link_to(node, input_idx=0)
        right.link_to(node, input_idx=1)
        return node
    base = chain(source(_png(temporary / "base.png")), "format", "gbrp16le")
    split = chain(base, "split", str(len(planes)+1))
    accumulator = graph.add("null")
    split.link_to(accumulator, output_idx=0)
    for i, plane in enumerate(planes):
        rect = plane["rect"]
        media = source(_png(videos[plane["id"]]["decoded"]))
        media = chain(media, "scale", f"{rect['width']}:{rect['height']}:flags=bilinear")
        media = chain(media, "format", "rgb24")
        blank = av.VideoFrame(canvas["width"], canvas["height"], format="rgb24")
        blank.pts, blank.time_base = 0, Fraction(1, 25)
        blank.planes[0].update(bytes(blank.planes[0].buffer_size))
        layer = combine(source(blank), media, "overlay", x=str(rect["x"]), y=str(rect["y"]), format="rgb", shortest="1")
        layer = chain(chain(layer, "format", "rgb24"), "format", "gbrp16le")
        white = chain(source(_png(temporary / f"{plane['id']}-white.png")), "format", "gbrp16le")
        mask = graph.add("blend", all_expr="max(0,A-B)")
        white.link_to(mask, input_idx=0)
        split.link_to(mask, output_idx=i+1, input_idx=1)
        pixels = combine(layer, mask, "blend", all_expr="A*B/65535")
        accumulator = combine(accumulator, pixels, "blend", all_expr="min(65535,A+B)")
    sink = chain(chain(accumulator, "format", "rgb24"), "buffersink")
    graph.configure()
    for node, frame in sources:
        node.push(frame)
        node.push(None)
    frame = sink.pull()
    encoder = av.CodecContext.create("png", "w")
    encoder.thread_count = 1
    encoder.width, encoder.height = canvas["width"], canvas["height"]
    encoder.pix_fmt, encoder.time_base = "rgb24", Fraction(1, 25)
    frame.pts, frame.time_base = 0, Fraction(1, 25)
    Path(output).write_bytes(b"".join(bytes(packet) for packet in encoder.encode(frame)))


def vectorized(temporary, canvas, planes, videos, output):
    """Use the same 16-bit mask math with array operations, without expression parsing per pixel."""
    import av
    import numpy as np
    if not planes:
        shutil.copyfile(temporary / "base.png", output)
        return
    width, height = canvas["width"], canvas["height"]
    def channels(frame):
        frame = frame.reformat(format="gbrp16le")
        return np.stack([np.frombuffer(plane,dtype="<u2").reshape(height,plane.line_size//2)[:,:width]
                         for plane in frame.planes]).astype(np.uint32)
    base = channels(_png(temporary/"base.png"))
    accumulator = base.copy()
    for plane in planes:
        rect = plane["rect"]
        media = _png(videos[plane["id"]]["decoded"])
        blank = av.VideoFrame(width,height,format="rgb24")
        blank.pts, blank.time_base = 0, Fraction(1,25)
        blank.planes[0].update(bytes(blank.planes[0].buffer_size))
        graph = av.filter.Graph()
        graph.threads = 1
        source = graph.add_buffer(template=media)
        background = graph.add_buffer(template=blank)
        scale = graph.add("scale",f"{rect['width']}:{rect['height']}:flags=bilinear")
        rgb = graph.add("format","rgb24")
        overlay = graph.add("overlay",x=str(rect["x"]),y=str(rect["y"]),format="rgb",shortest="1")
        sink = graph.add("buffersink")
        source.link_to(scale); scale.link_to(rgb); rgb.link_to(overlay,input_idx=1)
        background.link_to(overlay,input_idx=0); overlay.link_to(sink)
        graph.configure()
        source.push(media); source.push(None); background.push(blank); background.push(None)
        scaled = channels(sink.pull())
        white = channels(_png(temporary/f"{plane['id']}-white.png"))
        mask = np.maximum(white.astype(np.int32)-base.astype(np.int32),0).astype(np.uint32)
        pixels = scaled*mask//65535
        accumulator = np.minimum(accumulator+pixels,65535)
    frame = av.VideoFrame(width,height,format="gbrp16le")
    frame.pts, frame.time_base = 0, Fraction(1,25)
    for index,plane in enumerate(frame.planes):
        padded = np.zeros((height,plane.line_size//2),dtype="<u2")
        padded[:,:width] = accumulator[index]
        plane.update(padded.tobytes())
    frame = frame.reformat(format="rgb24")
    encoder = av.CodecContext.create("png","w")
    encoder.thread_count = 1
    encoder.width, encoder.height = width,height
    encoder.pix_fmt, encoder.time_base = "rgb24",Fraction(1,25)
    Path(output).write_bytes(b"".join(bytes(packet) for packet in encoder.encode(frame)))
