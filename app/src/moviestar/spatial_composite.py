"""In-process composition of browser masks and decoded video pixels.

Chrome still owns layout, clipping and paint order. Placement retains FFmpeg's
8-bit bilinear/overlay boundary; mask math retains the old 16-bit intermediates.
Placement and row-sized arithmetic buffers cover only visible media rectangles.
"""

from fractions import Fraction
from pathlib import Path

import av
import numpy as np


_ROWS = 64


def _read_png(path: Path):
    with av.open(str(path)) as container:
        container.streams.video[0].codec_context.thread_count = 1
        frame = next(container.decode(video=0))
    frame.pts, frame.time_base = 0, Fraction(1, 25)
    return frame


def _channels(frame):
    return [np.frombuffer(plane, dtype='<u2').reshape(frame.height, plane.line_size // 2)[:, :frame.width]
            for plane in frame.planes]


def _place_video(path, rect: dict, width: int, height: int):
    source = path if isinstance(path, av.VideoFrame) else _read_png(path)
    media = source.reformat(width=rect['width'], height=rect['height'],
                                    format='rgb24', interpolation='BILINEAR')
    x, y = rect['x'], rect['y']
    left, top = max(0, x), max(0, y)
    right, bottom = min(width, x + media.width), min(height, y + media.height)
    if right <= left or bottom <= top:
        return None
    # Preserve the full bilinear resize before cropping: rescaling the crop
    # instead would shift its sampling phase. Pixels outside this intersection
    # were black in the old full-canvas buffer and contribute exactly zero.
    placed = av.VideoFrame(right-left, bottom-top, format='rgb24')
    target = np.frombuffer(placed.planes[0], dtype=np.uint8).reshape(placed.height, placed.planes[0].line_size)
    target.fill(0)
    source = np.frombuffer(media.planes[0], dtype=np.uint8).reshape(media.height, media.planes[0].line_size)
    target[:, :placed.width*3] = source[top-y:bottom-y, (left-x)*3:(right-x)*3]
    return placed.reformat(format='gbrp16le'), (left,top,right,bottom)


def _accumulate(accumulators, base_channels, white_channels, media_channels):
    for accumulator, background, foreground, media in zip(
            accumulators, base_channels, white_channels, media_channels):
        for start in range(0, background.shape[0], _ROWS):
            rows = slice(start, start + _ROWS)
            # Subtraction must be signed; clamp before viewing as uint32.
            mask = foreground[rows].astype(np.int32)
            np.subtract(mask, background[rows], out=mask)
            np.maximum(mask, 0, out=mask)
            pixels = media[rows].astype(np.uint32)
            # 65535**2 fits in uint32. Divide before adding the accumulator,
            # then saturate before storing back to 16 bits, as blend does.
            np.multiply(pixels, mask.view(np.uint32), out=pixels)
            np.floor_divide(pixels, 65535, out=pixels)
            np.add(pixels, accumulator[rows], out=pixels)
            np.minimum(pixels, 65535, out=pixels)
            accumulator[rows] = pixels


def compose(temporary: Path, canvas: dict, planes: list[dict], videos: dict, output: Path | None):
    width, height = canvas['width'], canvas['height']
    base = _read_png(temporary / 'base.png').reformat(format='gbrp16le')
    if (base.width, base.height) != (width, height):
        raise ValueError('browser capture dimensions do not match the canvas')
    base_channels = _channels(base)
    result = av.VideoFrame(width, height, format='gbrp16le')
    result.pts, result.time_base = 0, Fraction(1, 25)
    for plane in result.planes:
        np.frombuffer(plane, dtype='<u2').fill(0)
    accumulators = _channels(result)
    for accumulator, background in zip(accumulators, base_channels):
        accumulator[:] = background
    for plane in planes:
        scaled = _place_video(videos[plane['id']]['decoded'], plane['rect'], width, height)
        white = _read_png(temporary / f"{plane['id']}-white.png").reformat(format='gbrp16le')
        if (white.width, white.height) != (width, height):
            raise ValueError('visibility mask dimensions do not match the canvas')
        if scaled is not None:
            media, (left,top,right,bottom) = scaled
            region = lambda channels: [channel[top:bottom,left:right] for channel in channels]
            _accumulate(region(accumulators), region(base_channels), region(_channels(white)), _channels(media))
            del media
        del white, scaled
    frame = result.reformat(format='rgb24')
    if output is None:
        return frame
    encoder = av.CodecContext.create('png', 'w')
    encoder.thread_count = 1
    encoder.width, encoder.height = width, height
    encoder.pix_fmt, encoder.time_base = 'rgb24', Fraction(1, 25)
    frame.pts, frame.time_base = 0, Fraction(1, 25)
    packets = [*encoder.encode(frame), *encoder.encode(None)]
    output.write_bytes(b''.join(bytes(packet) for packet in packets))
