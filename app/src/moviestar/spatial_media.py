"""Bounded source preparation owned by one spatial rendering job.

No cross-job state or rendered-clip cache. Each occurrence has an independent
cursor so the same recording can appear at unrelated source times.
"""
from collections import OrderedDict
from fractions import Fraction
import math
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from time import monotonic

import av


class SpatialMediaError(ValueError):
    pass


def _identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


class _Cursor:
    def __init__(self, path):
        self.path = path
        self.container = None
        self._open()

    def _open(self):
        self.container = av.open(str(self.path), timeout=60)
        try:
            self.stream = self.container.streams.video[0]
            self.stream.codec_context.thread_count = 1
            self.origin = Fraction(self.container.start_time or 0, av.time_base)
            self.reset()
        except BaseException:
            self.close()
            raise

    def reset(self):
        self.frames = None
        self.current = self.next = None
        self.eof = False
        self.last_target = None

    def close(self):
        if self.container is not None:
            self.container.close()
            self.container = None
        self.reset()

    def _next(self, deadline):
        while True:
            if monotonic() > deadline:
                raise SpatialMediaError(f'frame timestamp probe timed out in {self.path.name}')
            try:
                frame = next(self.frames)
            except StopIteration:
                self.eof = True
                return None
            if frame.pts is not None:
                return frame, frame.pts * frame.time_base

    def select(self, at, max_gap):
        target = self.origin + Fraction(str(at))
        deadline = monotonic() + 60
        seek = (self.frames is None or self.current is None or target < self.current[1]
                or (self.last_target is not None and target-self.last_target > max_gap))
        if seek:
            self.reset()
            try:
                self.container.seek(int(target / self.stream.time_base), stream=self.stream, backward=True)
            except av.error.FFmpegError:
                self.close()
                self._open()
            self.frames = self.container.decode(self.stream)
        self.last_target = target
        while True:
            if self.next is None and not self.eof:
                self.next = self._next(deadline)
            if self.next is None or self.next[1] > target:
                break
            self.current, self.next = self.next, None
        if self.current is None and seek:
            # Some demuxers overshoot even backward seeks. Match the reference
            # renderer's recovery by reopening from the beginning once.
            self.close()
            self._open()
            self.frames = self.container.decode(self.stream)
            self.last_target = target
            while True:
                self.next = self._next(deadline)
                if self.next is None or self.next[1] > target:
                    break
                self.current, self.next = self.next, None
        if self.current is None:
            raise SpatialMediaError(f'no source frame at {at}s in {self.path.name}')
        frame, timestamp = self.current
        if self.eof:
            duration = getattr(frame, 'duration', None)
            end = (timestamp + duration*frame.time_base if duration else
                   self.origin + Fraction(self.container.duration, av.time_base)
                   if self.container.duration is not None else None)
            if end is not None and target >= end:
                raise SpatialMediaError(f'no source frame at {at}s in {self.path.name}')
        return frame


class MediaSession:
    def __init__(self, *, max_decoders=4, max_gap_s=2, image_cache_bytes=64*1024*1024):
        if max_decoders < 1 or max_gap_s <= 0 or image_cache_bytes < 0:
            raise ValueError('invalid media session limits')
        self.max_decoders = max_decoders
        self.max_gap = Fraction(str(max_gap_s))
        self.image_budget = image_cache_bytes
        self._identities, self._probes = {}, {}
        self._decoders, self._images = OrderedDict(), OrderedDict()
        self._temporary = TemporaryDirectory(prefix='moviestar-source-job-')
        self._image_bytes = 0
        self._next_image = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            from contextlib import ExitStack
            with ExitStack() as stack:
                for decoder in self._decoders.values():
                    stack.callback(decoder.close)
        finally:
            self._decoders.clear()
            self._images.clear()
            self._probes.clear()
            self._identities.clear()
            self._temporary.cleanup()

    def _check(self, path):
        if self.closed:
            raise SpatialMediaError('media session is closed')
        path = Path(path).resolve()
        identity = _identity(path)
        if path in self._identities and self._identities[path] != identity:
            raise SpatialMediaError(f'source changed during render: {path.name}')
        self._identities[path] = identity
        return path

    def probe(self, path):
        import json
        from moviestar.spatial_render import _run
        path = self._check(path)
        if path not in self._probes:
            self._probes[path] = json.loads(_run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=pix_fmt:stream_tags:stream_side_data', '-of', 'json', str(path)]))
        return self._probes[path]

    def decode(self, path, output, *, source_time=None, occurrence=()):
        path = self._check(path)
        output = Path(output)
        if source_time is None:
            from moviestar.spatial_render import _decode
            if path in self._images:
                cached = self._images.pop(path)
                self._images[path] = cached
                shutil.copyfile(cached, output)
                return
            _decode(path, output)
            size = output.stat().st_size
            if size <= self.image_budget:
                while self._images and self._image_bytes+size > self.image_budget:
                    _, old = self._images.popitem(last=False)
                    self._image_bytes -= old.stat().st_size
                    old.unlink()
                cached = Path(self._temporary.name) / f'{self._next_image}.png'
                self._next_image += 1
                shutil.copyfile(output, cached)
                self._images[path] = cached
                self._image_bytes += size
            return
        if not math.isfinite(source_time) or source_time < 0:
            raise SpatialMediaError(f'no source frame at {source_time}s in {path.name}')
        key = path, tuple(occurrence)
        decoder = self._decoders.pop(key, None)
        if decoder is None:
            if len(self._decoders) >= self.max_decoders:
                _, old = self._decoders.popitem(last=False)
                old.close()
            try:
                decoder = _Cursor(path)
            except (av.error.FFmpegError, ValueError, IndexError) as exc:
                raise SpatialMediaError(f'frame timestamp probe failed in {path.name}: {exc}') from exc
        self._decoders[key] = decoder
        try:
            metadata = (self.probe(path).get('streams') or [{}])[0]
            sar = decoder.stream.sample_aspect_ratio or Fraction(1)
            pixel_format = decoder.stream.codec_context.format
            # FFmpeg's anamorphic scaling and display-matrix transforms are
            # authoritative. Keep that path until an in-process equivalent
            # matches its pixels; high-bit-depth RGB quantization also differs.
            # Ordinary 8-bit square-pixel sources decode forward.
            if (pixel_format is None or any(c.bits != 8 for c in pixel_format.components)
                    or sar != 1 or metadata.get('tags', {}).get('rotate') not in (None, '0')
                    or any(s.get('side_data_type') == 'Display Matrix'
                           for s in metadata.get('side_data_list', []))):
                from moviestar.spatial_render import _decode
                _decode(path, output, source_time=source_time)
                return
            frame = decoder.select(source_time, self.max_gap)
            rgb = av.video.reformatter.VideoReformatter().reformat(frame,
                width=int(frame.width*sar), height=frame.height, format='rgb24',
                interpolation='BICUBIC', src_color_range=frame.color_range,
                dst_color_range='JPEG', threads=1)
            # Encode an owned copy: PNG timestamp assignment must never change
            # the cursor's retained decoded frame or its presentation clock.
            picture = av.VideoFrame.from_ndarray(rgb.to_ndarray(), format='rgb24')
            picture.pts, picture.time_base = 0, Fraction(1)
            codec = av.CodecContext.create('png', 'w')
            codec.width, codec.height, codec.pix_fmt = picture.width, picture.height, 'rgb24'
            codec.time_base, codec.thread_count = Fraction(1), 1
            # These intermediates live for one frame. Fast lossless compression
            # preserves RGB exactly without spending export time minimizing PNGs.
            codec.options = {'compression_level': '1'}
            output.write_bytes(b''.join(bytes(p) for p in [*codec.encode(picture), *codec.encode(None)]))
        except (av.error.FFmpegError, ValueError, TypeError, IndexError) as exc:
            if isinstance(exc, SpatialMediaError):
                raise
            raise SpatialMediaError(f'frame decode failed in {path.name}: {exc}') from exc
