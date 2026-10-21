"""Single-frame spatial rendering using browser graphics and FFmpeg media.

For normal source-over paint, each opaque video's contribution is linear in its
RGB pixels. Chrome captures the scene with black video planes, then with each
plane white in turn. The difference measures that plane's visibility through
every foreground, clip, and opacity group. FFmpeg fills those measured regions
with decoded source frames. No second implementation of CSS stacking is needed.
"""

from __future__ import annotations

import base64
from fractions import Fraction
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory
from time import monotonic
import zlib

from moviestar.spatial_layout import SpatialLayoutError, _run_browser, prepare_layout_frame


class SpatialRenderError(SpatialLayoutError):
    """A selected scene cannot produce a complete rendered frame."""


def _run(command: list[str]) -> bytes:
    try:
        result = subprocess.run(command, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SpatialRenderError(f"{command[0]} failed: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode('utf-8', errors='replace').strip()
        raise SpatialRenderError(f"{command[0]} failed: {detail[-2000:]}")
    return result.stdout


def _ffmpeg(arguments: list[str]) -> None:
    _run(['ffmpeg', '-v', 'error', '-y', '-filter_complex_threads', '1', *arguments])


def _image_data(path: Path) -> str:
    return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


@lru_cache(maxsize=16)
def _solid_image(value: int, width: int, height: int) -> str:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = (b'\0' + bytes([value]) * width * 3) * height
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


def _png_size(path: Path) -> tuple[int, int]:
    with path.open('rb') as source:
        header = source.read(24)
    if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n':
        raise SpatialRenderError(f"FFmpeg did not produce a PNG: {path.name}")
    return struct.unpack('>II', header[16:24])


def _walk(nodes: list[dict]):
    for node in nodes:
        yield node
        yield from _walk(node.get('children', []))


def _asset_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def _displayed_frame_pts(path: Path, source_time: float) -> int:
    """Find the on-screen frame in presentation order, not by nominal FPS.

    PyAV is already a package dependency. Seek to the preceding keyframe and
    decode only through the first frame after the requested media position.
    Keep integer timestamps for FFmpeg so fractional frame rates cannot round
    a seek forward into the next frame.
    """
    import av

    deadline = monotonic() + 60
    try:
        with av.open(str(path), timeout=60) as container:
            stream = container.streams.video[0]
            origin = Fraction(container.start_time or 0, av.time_base)
            target = origin + Fraction(str(source_time))
            container.seek(int(target / stream.time_base), stream=stream, backward=True)
            selected = None
            selected_end = None
            for frame in container.decode(stream):
                if monotonic() > deadline:
                    raise SpatialRenderError(f'frame timestamp probe timed out in {path.name}')
                if frame.pts is None:
                    continue
                timestamp = frame.pts * frame.time_base
                if timestamp > target:
                    break
                selected = int(timestamp / stream.time_base)
                duration = getattr(frame, 'duration', None)
                selected_end = timestamp + duration * frame.time_base if duration else None
            else:
                # Do not extend the final frame past its actual display interval.
                if selected_end is None and container.duration is not None:
                    selected_end = origin + Fraction(container.duration, av.time_base)
                if selected_end is not None and target >= selected_end:
                    selected = None
    except (av.error.FFmpegError, ValueError, TypeError, IndexError) as exc:
        raise SpatialRenderError(f'frame timestamp probe failed in {path.name}: {exc}') from exc
    if selected is None:
        raise SpatialRenderError(f'no source frame at {source_time}s in {path.name}')
    return selected


def _decode(path: Path, output: Path, *, source_time: float | None = None) -> None:
    arguments = []
    filters = []
    if source_time is not None:
        pts = _displayed_frame_pts(path, source_time)
        arguments = ['-ss', str(source_time), '-noaccurate_seek', '-copyts']
        filters.append(f'select=eq(pts\\,{pts})')
    filters.extend(['scale=trunc(iw*sar):ih', 'setsar=1'])
    _ffmpeg([*arguments, '-i', str(path), '-map', '0:v:0', '-frames:v', '1',
             '-vf', ','.join(filters), '-fps_mode', 'vfr',
             '-pix_fmt', 'rgba' if source_time is None else 'rgb24',
             '-threads', '1', str(output)])
    if not output.is_file():
        raise SpatialRenderError(f"no source frame at {source_time}s in {path.name}")


def _prepare_assets(spec: dict, frame: dict, prepared: dict, root: Path, temporary: Path,
                    *, reference: bool) -> dict[str, dict]:
    sources = {source['id']: _asset_path(source['path'], root) for source in spec['sources']}
    images = {name: _asset_path(asset['path'], root)
              for name, asset in spec.get('spatial', {}).get('assets', {}).items()}
    nodes = {tuple(node['occurrence_path']): node for node in _walk(frame['children'])}
    decoded = {}
    probed = set()
    videos = {}
    for node in _walk(prepared['nodes']):
        path = '/'.join(node['path'])
        original = nodes[tuple(node['path'])]
        kind = node['kind']
        if kind not in {'component', 'shape', 'text', 'image', 'video'}:
            raise SpatialRenderError(f"{path}: {kind} rendering is not supported by this preview slice")
        content = original.get('content')
        if kind not in {'video', 'image'} or content is None:
            continue
        asset_id = content.get('asset_id')
        source = (sources if kind == 'video' else images).get(asset_id)
        if source is None or not source.is_file():
            raise SpatialRenderError(f"{path}: unavailable {kind} asset {asset_id!r}")
        if kind == 'video':
            media = original.get('media')
            if media is None:
                raise SpatialRenderError(f"{path}: unresolved video timing")
            if not media['visible']:
                continue
            if source not in probed:
                probe = json.loads(_run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                         '-show_entries', 'stream=pix_fmt:stream_tags', '-of', 'json', str(source)]))
                stream = (probe.get('streams') or [{}])[0]
                pixel_format = stream.get('pix_fmt', '')
                tags = {key.lower(): str(value) for key, value in stream.get('tags', {}).items()}
                if (tags.get('alpha_mode', '0') != '0' or pixel_format == 'pal8'
                        or re.match(r'^(yuva|gbrap|rgba|bgra|argb|abgr|ya)', pixel_format)):
                    raise SpatialRenderError(f"{path}: video alpha is not supported by this preview slice")
                probed.add(source)
            source_time = media['source_time_s']
            key = (source, source_time)
        else:
            source_time = None
            key = (source, None)
        if key not in decoded:
            png = temporary / f'asset-{len(decoded)}.png'
            try:
                _decode(source, png, source_time=source_time)
            except SpatialRenderError as exc:
                raise SpatialRenderError(f"{path}: {exc}") from exc
            decoded[key] = png
        png = decoded[key]
        if kind == 'image':
            node['image'] = _image_data(png)
        else:
            identity = f'video{len(videos)}'
            width, height = _png_size(png)
            node['video'] = {'id': identity, 'width': width, 'height': height,
                             'black_image': _solid_image(0, width, height),
                             'white_image': _solid_image(255, width, height)}
            if reference:
                node['video']['reference_image'] = _image_data(png)
            videos[identity] = {'path': node['path'], 'asset_id': asset_id,
                                'source_time_s': source_time, 'decoded': png}
    return videos


def _compose(temporary: Path, canvas: dict, planes: list[dict], videos: dict, output: Path) -> None:
    if not planes:
        shutil.copyfile(temporary / 'base.png', output)
        return
    width, height = canvas['width'], canvas['height']
    inputs = ['-i', str(temporary / 'base.png')]
    for index, plane in enumerate(planes):
        rect = plane['rect']
        if rect['width'] <= 0 or rect['height'] <= 0:
            raise SpatialRenderError(f"{'/'.join(plane['path'])}: unresolved video geometry")
        full = temporary / f'video-layer-{index}.png'
        # Chrome snaps the media sampling plane to pixels and owns all clipping.
        _ffmpeg(['-i', str(videos[plane['id']]['decoded']), '-f', 'lavfi',
                 '-i', f'color=c=black:s={width}x{height}:r=1,format=rgb24',
                 '-filter_complex',
                 f"[0:v]scale={rect['width']}:{rect['height']}:flags=bilinear,format=rgb24[media];"
                 f"[1:v]format=rgb24[blank];[blank][media]overlay=x={rect['x']}:y={rect['y']}:format=rgb:shortest=1[out]",
                 '-map', '[out]', '-frames:v', '1', '-threads', '1', str(full)])
        inputs += ['-i', str(temporary / f"{plane['id']}-white.png"), '-i', str(full)]
    # Sixteen-bit intermediates retain fractional contributions until the final
    # PNG conversion; source and browser captures remain normal eight-bit RGB.
    graph = ['[0:v]format=gbrp16le,split=' + str(len(planes) + 1)
             + '[acc0]' + ''.join(f'[base{i}]' for i in range(len(planes)))]
    for index in range(len(planes)):
        white, video = 1 + index * 2, 2 + index * 2
        graph.extend([
            f'[{white}:v]format=gbrp16le[white{index}]',
            f"[white{index}][base{index}]blend=all_expr='max(0,A-B)'[mask{index}]",
            f'[{video}:v]format=gbrp16le[video{index}]',
            f"[video{index}][mask{index}]blend=all_expr='A*B/65535'[pixels{index}]",
            f"[acc{index}][pixels{index}]blend=all_expr='min(65535,A+B)'[acc{index + 1}]",
        ])
    graph.append(f'[acc{len(planes)}]format=rgb24[out]')
    _ffmpeg([*inputs, '-filter_complex', ';'.join(graph), '-map', '[out]',
             '-frames:v', '1', '-threads', '1', str(output)])


def _publish(staged: Path, destination: Path) -> None:
    # Staging beside the destination makes replacement atomic across filesystems.
    with NamedTemporaryFile(dir=destination.parent, suffix='.png', delete=False) as handle:
        target = Path(handle.name)
    try:
        shutil.copyfile(staged, target)
        os.replace(target, destination)
    finally:
        target.unlink(missing_ok=True)


def render_spatial_frame(spec: dict, frame: dict, canvas: dict, *,
                         output_path: Path, project_root: Path | None = None,
                         layout_output_path: Path | None = None,
                         reference_path: Path | None = None) -> dict:
    """Render a single PNG; the optional browser reference supports parity tests."""
    root = (project_root or Path.cwd()).resolve()
    for diagnostic in frame.get('diagnostics', []):
        if diagnostic.get('severity') != 'warning':
            raise SpatialRenderError(f"{diagnostic['code']} at {'/'.join(diagnostic['path'])}")
    destinations = [Path(path).resolve() for path in (output_path, layout_output_path, reference_path) if path]
    asset_paths = {_asset_path(source['path'], root) for source in spec['sources']}
    asset_paths.update(_asset_path(asset['path'], root) for asset in spec.get('spatial', {}).get('assets', {}).values())
    if len(set(destinations)) != len(destinations):
        raise SpatialRenderError('preview output paths must be distinct')
    for destination in destinations:
        if destination.suffix.lower() != '.png' or not destination.parent.is_dir():
            raise SpatialRenderError('preview output must be a PNG in an existing directory')
        if destination in asset_paths:
            raise SpatialRenderError('preview output cannot overwrite a project asset')
    prepared = prepare_layout_frame(frame, canvas)
    with TemporaryDirectory(prefix='moviestar-frame-') as directory:
        temporary = Path(directory)
        videos = _prepare_assets(spec, frame, prepared, root, temporary, reference=reference_path is not None)
        request = {**prepared, 'paint_dir': str(temporary),
                   'reference_path': str(temporary / 'reference.png') if reference_path else None,
                   'layout_output_path': str(temporary / 'layout.png') if layout_output_path else None}
        measured = _run_browser(request)
        staged = temporary / 'rendered.png'
        _compose(temporary, prepared['canvas'], measured['video_planes'], videos, staged)
        _publish(staged, output_path.resolve())
        if layout_output_path:
            _publish(temporary / 'layout.png', layout_output_path.resolve())
        if reference_path:
            _publish(temporary / 'reference.png', reference_path.resolve())
    return {'view': 'rendered', 'canvas': prepared['canvas'], 'boxes': measured['boxes'],
            'diagnostics': [*prepared['diagnostics'], *measured['diagnostics']],
            'media': [{**{key: value for key, value in videos[plane['id']].items() if key != 'decoded'},
                       'rect': plane['rect']} for plane in measured['video_planes']],
            'artifact': str(output_path.resolve()),
            'layout_artifact': str(layout_output_path.resolve()) if layout_output_path else None}
