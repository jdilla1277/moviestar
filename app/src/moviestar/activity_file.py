"""Bounded, path-free visual activity evidence on a file's own timeline.

Uses the local activity detector unchanged. The process runner must additionally
bound wall time, scratch and process memory when handling untrusted media.
"""
from __future__ import annotations
import json
import math
import os
import sys
from moviestar.activity import analyze_visual_activity, ACTIVITY_ANALYSIS_WIDTH, ACTIVITY_PIXEL_DELTA
from moviestar.ffmpeg import run_ffprobe
from moviestar.timecodes import format_timecode

ACTIVITY_HINT = 'Visual change is a heuristic, not editorial importance, speech detection or semantic understanding. Ranges are candidates. Both sample clocks refer to this asset file.'
MAX_SAMPLES = 5000
MAX_RESULT_BYTES = 3_500_000

class ActivityError(ValueError):
    """Stable failure code safe to return without media paths."""


def _sample_count(span, interval):
    ratio = span / interval
    if not math.isfinite(ratio) or ratio > MAX_SAMPLES + 0.000000001:
        raise ActivityError('sample_limit')
    count = math.ceil(round(ratio, 9))
    if not 2 <= count <= MAX_SAMPLES: raise ActivityError('sample_limit')
    return count


def normalize_options(options):
    defaults = {'from_seconds':0.0,'interval_seconds':0.25,'threshold':0.005,'merge_gap_seconds':0.5}
    if not isinstance(options, dict) or set(options) - (set(defaults) | {'to_seconds'}):
        raise ActivityError('invalid_request')
    result = {**defaults, **options}
    for name, value in result.items():
        if type(value) not in (float, int) or not 0 <= value <= 86400:
            raise ActivityError('invalid_request')
        if name == 'threshold': valid = 0 <= value <= 1
        elif name == 'interval_seconds': valid = 0 < value <= 86400
        elif name == 'from_seconds': valid = 0 <= value < 86400
        elif name == 'to_seconds': valid = result['from_seconds'] < value <= 86400
        else: valid = 0 <= value <= 86400
        if not valid: raise ActivityError('invalid_request')
    if 'to_seconds' in result:
        _sample_count(result['to_seconds'] - result['from_seconds'], result['interval_seconds'])
    return result


def analyze_file_activity(filename, options):
    options = normalize_options(options)
    metadata = run_ffprobe(filename)
    video = next((s for s in metadata.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    if not video or not video.get('width') or not video.get('height'):
        raise ActivityError('no_video')
    duration = float(metadata['format'].get('duration', 0))
    start, end = options['from_seconds'], options.get('to_seconds', duration)
    if not math.isfinite(duration) or not 0 <= start < end <= duration + 0.001 or end > 86400:
        raise ActivityError('invalid_range')
    interval = options['interval_seconds']
    count = _sample_count(end-start, interval)
    width, height = int(video['width']), int(video['height'])
    scaled_height = max(2, round(height * ACTIVITY_ANALYSIS_WIDTH / width))
    # Bound raw output accumulated by the unchanged local analyzer (including
    # its frame slices). Extreme aspect ratios must not bypass the pixel budget.
    if width > 16384 or height > 16384 or scaled_height > 640 or count * 160 * (scaled_height + 1) > 128_000_000:
        raise ActivityError('resource_limit')
    samples = [{'result_seconds':round(start+i*interval,9),'source_seconds':round(start+i*interval,9)} for i in range(count)]
    analysis = analyze_visual_activity(filename, source_width=width, source_height=height, samples=samples,
        interval=interval, threshold=options['threshold'], merge_gap=options['merge_gap_seconds'], range_from=start, range_to=end)
    analysis.pop('commands')
    result = {'file_size_bytes':os.path.getsize(filename),
        'range':{'clock':'asset','from':format_timecode(start),'to':format_timecode(end),'duration':format_timecode(end-start)},
        'settings':{'interval_seconds':interval,'active_threshold':options['threshold'],'merge_gap_seconds':options['merge_gap_seconds'],
            'metric':'mean_absolute_luma_difference','metric_range':[0.0,1.0],'changed_pixel_min_luma_delta':ACTIVITY_PIXEL_DELTA,'analysis_width':ACTIVITY_ANALYSIS_WIDTH},
        **analysis,
        'hint':ACTIVITY_HINT}
    if len(json.dumps(result, separators=(',', ':'), allow_nan=False).encode()) > MAX_RESULT_BYTES:
        raise ActivityError('resource_limit')
    return result


def main():
    try:
        result = analyze_file_activity(sys.argv[1], json.loads(sys.argv[2]))
    except ActivityError as failure:
        print(json.dumps({'error':str(failure)})); return 1
    except (ValueError, KeyError, OSError, RuntimeError):
        print(json.dumps({'error':'activity_failed'})); return 1
    print(json.dumps(result, separators=(',', ':'), allow_nan=False)); return 0

if __name__ == '__main__':
    raise SystemExit(main())
