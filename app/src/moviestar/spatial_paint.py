"""One bounded browser-paint entry owned by a single rendering session."""
from hashlib import sha256
import json
from pathlib import Path
import re


class PaintCache:
    def __init__(self, *, max_bytes=64*1024*1024):
        if max_bytes < 0:
            raise ValueError('paint budget must be nonnegative')
        self.max_bytes = max_bytes
        self.clear()

    def clear(self):
        self._entry = None
        self.retained_bytes = 0

    @staticmethod
    def key(request):
        # Fail closed for future request modes/fields. All node data, including
        # captions/active words, images, styles, dimensions and paths stays keyed.
        allowed = {'scene_id', 'canvas', 'reserved_regions', 'scene_time_s',
                   'result_time_s', 'root_style', 'scene_layout', 'nodes',
                   'diagnostics', 'paint_dir', 'reference_path', 'layout_output_path',
                   'output_path'}
        if (not request.get('paint_dir') or set(request)-allowed
                or any(request.get(k) for k in ('reference_path', 'layout_output_path', 'output_path'))):
            return None
        def dynamic(nodes):
            for node in nodes:
                if (node.get('motion') or node.get('media_motion')
                        or any(node.get('video', {}).get(k) for k in ('raster_image', 'reference_image'))
                        or dynamic(node.get('children', []))):
                    return True
            return False
        if dynamic(request.get('nodes', [])):
            return None
        # spatial_dom.mjs consumes resolved visual state, never these two root
        # clocks. Reports likewise contain no root clock. Do not strip any
        # nested values: caption state and future visual fields must invalidate.
        visual = {k: v for k, v in request.items() if k not in {
            'paint_dir', 'reference_path', 'layout_output_path', 'output_path',
            'scene_time_s', 'result_time_s'}}
        # CSS shorthand/longhand application order can affect paint. Preserve
        # object insertion order too; equivalent-but-reordered maps may miss.
        return sha256(json.dumps(visual, separators=(',', ':'), allow_nan=False).encode()).digest()

    def restore(self, request):
        if self._entry is None:
            return None
        key, report, artifacts = self._entry
        if self.key(request) != key:
            return None
        for name, data in artifacts:
            (Path(request['paint_dir']) / name).write_bytes(data)
        # Callers append per-frame reservation diagnostics. Never lend mutable
        # report state back to a consumer or let a hit poison the next frame.
        return json.loads(report)

    def store(self, request, result):
        self.clear()
        key = self.key(request)
        if (key is None or not isinstance(result.get('video_planes'), list)
                or any(d.get('severity') == 'error' for d in result.get('diagnostics', []))):
            return
        names = ['base.png']
        for plane in result['video_planes']:
            identity = plane.get('id', '')
            if not re.fullmatch(r'video[0-9]+', identity):
                return
            names.append(f'{identity}-white.png')
        report = json.dumps(result).encode()
        paths = [Path(request['paint_dir']) / name for name in names]
        if len(report) + sum(path.stat().st_size for path in paths) > self.max_bytes:
            return
        artifacts = tuple((name, path.read_bytes()) for name, path in zip(names, paths))
        size = len(report) + sum(len(data) for _, data in artifacts)
        if size <= self.max_bytes:
            self._entry = key, report, artifacts
            self.retained_bytes = size
