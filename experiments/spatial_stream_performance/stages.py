"""Wall-clock stage attribution for a fresh, single-producer export worker."""
from collections import defaultdict
from functools import wraps
from time import perf_counter


class Stages:
    def __init__(self):
        self.rows = defaultdict(lambda: {'calls': 0, 'inclusive_s': 0, 'exclusive_s': 0})
        self.stack = []

    def wrap(self, name, function):
        @wraps(function)
        def timed(*args, **kwargs):
            entry = [perf_counter(), 0]
            self.stack.append(entry)
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = perf_counter() - entry[0]
                self.stack.pop()
                row = self.rows[name]
                row['calls'] += 1
                row['inclusive_s'] += elapsed
                row['exclusive_s'] += elapsed - entry[1]
                if self.stack:
                    self.stack[-1][1] += elapsed
        return timed

    def install(self):
        from moviestar import cli, spatial_render, spatial_composite, spatial_media, spatial_stream
        from moviestar.spatial_session import BrowserSession
        for owner, attr, label in [
            (spatial_render, '_prepare_assets', 'asset_preparation'),
            (spatial_media.MediaSession, 'pixels', 'source_pixels'),
            (spatial_media._Cursor, 'select', 'source_frame_selection'),
            (BrowserSession, 'run', 'browser_paint_or_reuse'),
            (spatial_render, '_compose', 'composition'),
            (spatial_composite, '_read_png', 'paint_png_decode'),
            (spatial_composite, '_place_video', 'video_placement'),
            (spatial_composite, '_accumulate', 'mask_accumulation'),
            (spatial_stream.FrameEncoder, 'write', 'encoder_write_wait'),
            (spatial_stream.FrameEncoder, 'finish', 'encoder_finish_wait'),
            (cli, 'render_layout_video', 'scene_generation_and_encoding'),
            (cli, 'render_segments', 'assembly_encoding'),
        ]:
            setattr(owner, attr, self.wrap(label, getattr(owner, attr)))

    def report(self):
        return {'stages': dict(self.rows), 'notes': [
            'Wall times, not CPU times. Inclusive stages overlap; do not sum them.',
            'Exclusive time subtracts instrumented child calls on the producer thread.',
            'source_pixels includes selection and RGB conversion; selection is also measured separately.',
            'composition exclusive includes format conversion and uninstrumented work.',
            'Encoder work overlaps production; write/finish waits do not measure all encoder CPU.',
        ]}
