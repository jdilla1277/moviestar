"""Bounded caption planes painted by the canonical export filters.

Only distinct cue/word states are painted. Source video is never opened and no
preview movie is encoded. Files belong to one request and expire with it.
"""
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import threading

import av
import numpy as np

from moviestar.ffmpeg import build_highlight_ass, build_overlay_filter_parts, highlight_events
from moviestar.fonts import FontResolutionError
from moviestar.overlays import plan_overlays
from moviestar.resolved import resolve_overlays
from moviestar.timecodes import parse_timecode


def compile_plane(spec, timeline, start, end, fail):
    canvas = (timeline.canvas['width'], timeline.canvas['height'])
    try:
        resolved = resolve_overlays(timeline, spec.get('overlays', []))
        planned = plan_overlays(list(resolved.records), canvas, start, end)
    except (ValueError, FontResolutionError) as exc:
        fail(str(exc))
    plans = planned['plans']
    if not plans:
        return None, None
    if any(p['rotate_deg'] for p in plans):
        fail('rotated preset text requires rendered mode')
    # Do not silently drop authored properties even where export currently warns.
    if any(any(word in warning for word in ('ignored', 'not rendered', 'skipped', 'without highlight'))
           for warning in planned['warnings']):
        fail('caption style/tokens are not faithfully supported: ' + '; '.join(planned['warnings']))
    events = {p['id']: highlight_events(p) for p in plans if p.get('highlight')}
    states, frames, seen = [], [], {}
    for frame in range(round((end-start)*timeline.output_fps)):
        at = frame/timeline.output_fps
        cues, paint_events = [], []
        for plan in plans:
            active = None
            if plan.get('highlight'):
                visible = [e for e in events[plan['id']] if parse_timecode(e['from']) <= at < parse_timecode(e['to'])]
                if not visible:
                    continue
                # Overlapping rounded events are painted in canonical ASS order.
                active = visible[-1]['word_index']
                # All overlapping ASS events paint, including transient ones
                # with the same final word. Preserve their order in the key.
                paint_events.append((plan['id'], visible))
            elif not plan['enable_from'] <= at < plan['enable_to']:
                continue
            cues.append({'id': plan['id'], 'track': plan['track'], 'text': plan['text'],
                'lines': plan['lines'], 'active_word': active, 'font_size': plan['font_size'],
                'highlight_color': plan['highlight']['color'] if plan.get('highlight') else None,
                'font': {k: plan['font'][k] for k in ('family', 'weight')},
                'estimated_bounds': plan['estimated_bounds'], 'style': plan['resolved_style']})
        signature = json.dumps([cues, paint_events], sort_keys=True)
        if signature not in seen:
            seen[signature] = len(states)
            states.append({'frame': frame, 'cues': cues})
        frames.append(seen[signature])
    job = {'kind': 'caption_job', 'plans': plans, 'canvas': canvas,
           'fps': timeline.output_fps, 'duration': end-start,
           'frames': [state['frame'] for state in states]}
    identity = sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()[:24]
    return {'job': identity, 'frames': frames, 'states': states,
            'warnings': planned['warnings']}, (identity, job)


def paint_plane(job, directory):
    """Paint export's filters over black/white mattes and recover straight alpha.

    Drawtext/libass blend alpha differently on transparent inputs. Two opaque
    mattes preserve export's actual edge coverage, opacity, boxes and strokes.
    Crop the recovered plane, keeping its authored canvas position unchanged.
    """
    directory = Path(directory); directory.mkdir()
    width, height = job['canvas']
    highlighted = [p for p in job['plans'] if p.get('highlight')]
    ass = None
    if highlighted:
        path = directory/'words.ass'
        path.write_text(build_highlight_ass(highlighted, job['canvas']))
        ass = (str(path), str(Path(highlighted[0]['font']['path']).parent))
    selected = '+'.join(f'eq(n\\,{frame})' for frame in job['frames'])
    filters = []
    rate = Fraction(str(job['fps'])).limit_denominator(100000)
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-filter_complex_threads', '1']
    for index, color in enumerate(('black', 'white')):
        args += ['-f', 'lavfi', '-i', f'color=c={color}:s={width}x{height}:r={rate}:d={job["duration"]},format=rgb24']
        filters.append(f'[{index}:v]select={selected}[{color}selected]')
        filters += build_overlay_filter_parts(job['plans'], job['canvas'], job['duration'],
            input_label=color+'selected', output_label=color+'paint', highlight_ass=ass, label_prefix=color)
    filters.append('[blackpaint][whitepaint]hstack=inputs=2[both]')
    args += ['-filter_complex', ';'.join(filters), '-map', '[both]', '-an', '-fps_mode', 'vfr',
        '-frames:v', str(len(job['frames'])), '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1']
    outputs = []
    # Keep one matte pair in memory, not a full-canvas sequence on disk.
    # A deadline also bounds a blocked pipe read, not only process.wait().
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors)
        timer = threading.Timer(30, process.kill); timer.daemon = True; timer.start()
        try:
            for index in range(len(job['frames'])):
                raw = process.stdout.read(width*height*6)
                if len(raw) != width*height*6:
                    raise ValueError('Native caption painting did not produce every required state; use --mode rendered.')
                pair = np.frombuffer(raw, dtype=np.uint8).reshape(height, width*2, 3)
                black, white = pair[:, :width], pair[:, width:]
                # Scan empty canvas cheaply before allocating/color-converting it.
                # Captions occupy a small strip even on an authored 1080p canvas.
                covered = (black[:, :, 0] | black[:, :, 1] | black[:, :, 2]) != 0
                covered |= (white[:, :, 0] & white[:, :, 1] & white[:, :, 2]) != 255
                ys, xs = np.nonzero(covered)
                left, top = (int(xs.min()), int(ys.min())) if len(xs) else (0, 0)
                right, bottom = (int(xs.max())+1, int(ys.max())+1) if len(xs) else (1, 1)
                black = black[top:bottom, left:right].astype(np.int16)
                white = white[top:bottom, left:right].astype(np.int16)
                crop_alpha = np.clip(255 - np.median(white-black, axis=2), 0, 255).astype(np.uint8)
                rgba = np.empty((bottom-top, right-left, 4), dtype=np.uint8)
                rgba[:, :, :3] = np.clip(np.rint(black*255.0 /
                    np.maximum(crop_alpha[:, :, None], 1)), 0, 255).astype(np.uint8)
                rgba[:, :, 3] = crop_alpha
                encoder = av.CodecContext.create('png', 'w')
                encoder.width, encoder.height = right-left, bottom-top
                encoder.pix_fmt = 'rgba'; encoder.time_base = Fraction(1, 25); encoder.thread_count = 1
                path = directory/f'state-{index+1:03d}.png'
                path.write_bytes(b''.join(bytes(p) for p in encoder.encode(av.VideoFrame.from_ndarray(rgba, format='rgba'))))
                outputs.append((path, {'x': left, 'y': top, 'width': right-left, 'height': bottom-top}))
            if process.stdout.read(1) or process.wait():
                errors.seek(0)
                raise ValueError('Native caption painting failed; use --mode rendered. ' + errors.read().decode(errors='replace')[-2000:])
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait(); process.stdout.close()
    return outputs
