"""Render spatial scene clips through the existing audio/transition pipeline.

Temporary PNG sequences are frame inputs, not a reusable rendered-clip cache.
The final movie is staged until rendering, mixing, and mastering all succeed.
"""

from __future__ import annotations

from contextlib import nullcontext
from math import ceil, floor
import os
from pathlib import Path
import tempfile
from time import monotonic

import click

from moviestar.resolved import ResolvedProjectError, resolve_project
from moviestar.spatial_layout import SpatialLayoutError, prepare_layout_frame
from moviestar.spatial_model import compile_spatial_scene_frame, expand_spatial_scene
from moviestar.spatial_render import _asset_path, render_spatial_frame
from moviestar.spatial_session import BrowserSession
from moviestar.timecodes import format_timecode
from moviestar.transitions import resolve_edge_transition


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get('children', []))


def _frame_window(plan):
    start = plan.get('result_start_s', plan['scene_window']['result_range']['from']['seconds']) - plan.get('transition_preroll', 0)
    end = (plan['result_end_s'] + plan.get('transition_postroll', 0) if 'result_end_s' in plan
           else start + plan.get('video_duration', plan['duration']))
    first = ceil(round(start * plan['output_fps'], 9))
    count = ceil(round(end * plan['output_fps'], 9)) - first
    if count <= 0:
        raise ValueError(f"{plan['scene_window']['scene']}: scene window has no frame on the output grid")
    return first, count


def _frame_count(plan):
    return _frame_window(plan)[1]


def render_spatial_sequence(spec, project, plan, *, project_root, progress_cb=None, browser_session=None):
    timeline = resolve_project(project, spec)
    durations = {source['id']: source['duration']['seconds'] for source in project['sources']}
    directory = Path(plan['frame_sequence']).parent
    directory.mkdir(parents=True, exist_ok=True)
    first, count = _frame_window(plan)
    last_progress = 0
    context = nullcontext(browser_session) if browser_session is not None else BrowserSession()
    with context as browser:
        for index in range(count):
            at = (first + index) / plan['output_fps']
            scene_index = plan['scene_window']['index']
            scene_start = min(span.scene_start_s for span in timeline.spans if span.scene_index == scene_index)
            try:
                frame = compile_spatial_scene_frame(spec, timeline, scene_index, at - scene_start,
                                                     source_durations=durations)
                render_spatial_frame(spec, frame, timeline.canvas, output_path=directory / f'frame{index:08d}.png',
                                      project_root=project_root, browser_session=browser)
            except (ValueError, SpatialLayoutError) as exc:
                raise SpatialLayoutError(f"{plan['spatial_render']['scene_id']} at result {at:.6f}s: {exc}") from exc
            if progress_cb and (index + 1 == count or monotonic() - last_progress >= 1):
                progress_cb(f"{plan['spatial_render']['scene_id']}: spatial frame {index + 1}/{count}")
                last_progress = monotonic()


def _context_range(spec, timeline, start, end):
    """Include full transitions with a video origin on the global frame grid.

    Audio retains its exact authored range; the finish stage crops both streams
    back to the requested window independently of video sampling.
    """
    windows = []
    for index, scene in enumerate(spec['composition']):
        transition = scene.get('transition_in')
        if index and transition:
            cut = min(span.scene_start_s for span in timeline.spans if span.scene_index == index)
            half = transition['duration'] / 2
            windows.append((cut-half, cut+half))
    for field in ('opening_transition', 'closing_transition'):
        transition = spec.get(field)
        if transition:
            windows.append((0, transition['duration']) if field.startswith('opening') else
                           (timeline.duration_s-transition['duration'], timeline.duration_s))
    changed = True
    while changed:
        previous = start, end
        start = floor(round(start * timeline.output_fps, 9)) / timeline.output_fps
        for left, right in windows:
            if start < right and end > left:
                start, end = min(start, left), max(end, right)
        changed = previous != (start, end)
    return max(0, start), min(timeline.duration_s, end)


def _finish_command(spec, timeline, source, output, *, render_start, start, end, has_audio, video_encoder):
    from moviestar.ffmpeg import _h264_video_args

    filters = []
    for field, direction in [('opening_transition', 'in'), ('closing_transition', 'out')]:
        transition = spec.get(field)
        if not transition:
            continue
        left = 0 if direction == 'in' else timeline.duration_s-transition['duration']
        if render_start <= left < end:
            color = 'white' if transition['type'] == 'dip-white' else 'black'
            filters.append(f"fade=t={direction}:st={round(left-render_start, 6)}:d={transition['duration']}:color={color}")
    offset, duration = round(start-render_start, 6), round(end-start, 6)
    # Select video on its global frame grid, separately from exact audio time.
    # Time-based trims can round up at recurring frame timestamps (e.g. 30 fps).
    first = ceil(round(render_start * timeline.output_fps, 9))
    begin = ceil(round(start * timeline.output_fps, 9)) - first
    stop = ceil(round(end * timeline.output_fps, 9)) - first
    filters += [f'trim=start_frame={begin}:end_frame={stop}', 'setpts=PTS-STARTPTS']
    command = ['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-map', '0:v:0', '-vf', ','.join(filters)]
    if has_audio:
        command += ['-map', '0:a:0', '-af', f'atrim=start={offset}:duration={duration},asetpts=PTS-STARTPTS', '-c:a', 'aac']
    command += _h264_video_args(False, video_encoder)
    return command + ['-t', str(duration), str(output)]


def spatial_video_command(*, project, spec, command, output, from_s=0.0, to_s=None,
                          dry_run=False, preview=False, fast=False, quiet=False,
                          loudness_target=None, audio_join_fade=None,
                          loudness_report=False, video_encoder='auto'):
    # Import command adapters lazily: this module also serves the clip worker.
    from moviestar import cli as c

    try:
        timeline = resolve_project(project, spec)
        end = timeline.duration_s if to_s is None else to_s
        if not 0 <= from_s < end <= timeline.duration_s:
            raise ValueError(f'range {from_s}-{end} must be within 0-{timeline.duration_s} seconds')
        for field, edge in [('opening_transition', 'opening'), ('closing_transition', 'closing')]:
            transition = spec.get(field)
            if transition:
                resolved_edge = resolve_edge_transition(timeline, edge=edge, duration_s=transition['duration'])
                if not resolved_edge.sufficient:
                    raise ValueError(f"{field}: duration exceeds project limit {resolved_edge.maximum_duration_s}s")
        render_start, render_end = _context_range(spec, timeline, from_s, end)
        plan, render_plans = c._scene_render_plans_for_range(
            project=project, spec=spec, composition=spec['composition'],
            start_s=render_start, end_s=render_end, command=command)
        root = c.get_project_dir().parent
        structured = (spec.get('spatial') or {}).get('scenes', {})
        selected_ids = {item['scene'].get('id') for item in render_plans} & set(structured)
        tracks = set()
        for identity in selected_ids:
            expanded = expand_spatial_scene(spec, identity)
            for node in _walk(expanded['children']):
                if node.get('type') == 'caption':
                    content = node.get('content') or {}
                    if not content.get('empty'):
                        tracks.add(content.get('track', 'captions'))
        if tracks:
            spec = c._refresh_caption_tracks(project, spec, tracks=tracks)
        for recipe in spec.get('captions', []):
            if recipe['track'] in tracks and not recipe.get('frozen') and c._caption_staleness(project, spec, recipe):
                raise ValueError(f"stale_caption_track {recipe['track']}: live caption cues could not be refreshed")
        durations = {source['id']: source['duration']['seconds'] for source in project['sources']}
        for item in render_plans:
            if item['scene'].get('id') not in selected_ids:
                continue
            at = item['result_start_s']
            scene_index = item['scene_window']['index']
            scene_start = min(span.scene_start_s for span in timeline.spans if span.scene_index == scene_index)
            frame = compile_spatial_scene_frame(spec, timeline, scene_index, at-scene_start,
                                                 source_durations=durations)
            errors = [d for d in frame['diagnostics'] if d.get('severity') != 'warning']
            if errors:
                raise ValueError(f"{errors[0]['code']} at {'/'.join(errors[0]['path'])}")
            prepared = prepare_layout_frame(frame, timeline.canvas)
            if any(d.get('severity') == 'error' for d in prepared['diagnostics']):
                raise ValueError('spatial layout has unresolved errors')
        if output is None:
            output = (c._default_export_output_path(create_parent=not dry_run) if command == 'export'
                      else c._resolve_unique_output_path(f'watch_{round(from_s, 3)}s_{round(end, 3)}s.mp4'))
        destination = Path(output).resolve()
        if destination.suffix.lower() != '.mp4':
            raise ValueError('spatial video output must be an MP4')
        protected = [source['path'] for source in spec['sources']]
        protected += [asset['path'] for asset in (spec.get('spatial') or {}).get('assets', {}).values()]
        if destination in {_asset_path(path, root) for path in protected}:
            raise ValueError('spatial video output cannot overwrite a project asset')
        if not dry_run and not destination.parent.is_dir():
            raise ValueError(f'output parent directory does not exist: {destination.parent}')
        resolved_mix = c._resolve_project_audio_mix(command, spec, timeline.duration_s, c.get_project_dir())
        if destination in {Path(track['path']).resolve() for track in resolved_mix.get('tracks', []) if track.get('path')}:
            raise ValueError('spatial video output cannot overwrite an audio asset')
        context = (nullcontext(str(c.get_project_dir() / 'scene-renders' / 'spatial-plan')) if dry_run
                   else tempfile.TemporaryDirectory(prefix='.moviestar-spatial-', dir=destination.parent))
        with context as temporary:
            staged = Path(temporary) / 'movie.mp4'
            artifacts = c._scene_video_render_artifacts(
                command=command, project=project, spec=spec, render_plans=render_plans,
                abs_output=str(staged), work_dir=Path(temporary) / 'scenes', preview=preview,
                audio_join_fade=audio_join_fade, video_encoder=video_encoder, render_transitions=True)
            c._validate_audio_join_fade_durations(audio_join_fade,
                [item['duration'] for item in render_plans], command, noun='scene')
            has_audio = any(artifacts['segment_has_audio'])
            if loudness_target is not None and not has_audio and not c._audio_mix_is_edited(spec):
                raise ValueError('--loudness-target requires an exported audio track')
            spatial_plan = [{**item['spatial_render'], 'frame_count': _frame_count(item),
                             'result_range': item['scene_window']['result_range'],
                             'frame_sequence': item['frame_sequence']}
                            for item in artifacts['render_plans'] if item.get('spatial_render')]
            needs_finish = (render_start != from_s or render_end != end
                            or spec.get('opening_transition') or spec.get('closing_transition'))
            finish_command = (_finish_command(spec, timeline, staged, Path(temporary) / 'finished.mp4',
                render_start=render_start, start=from_s, end=end, has_audio=has_audio,
                video_encoder=artifacts['video_encoder']) if needs_finish else None)
            result = {
                'mode': 'spatial_composition', 'composition_type': 'scenes',
                'output_fps': timeline.output_fps, 'composition_canvas': timeline.canvas,
                'composition_duration': format_timecode(timeline.duration_s),
                'result_duration': format_timecode(end-from_s),
                'range': {'requested': c._scene_result_range(from_s, end-from_s)},
                'scenes_count': len({item['scene_window']['index'] for item in render_plans}),
                'scene_windows': [item['scene_window'] for item in render_plans],
                'audio_routes': [{key: item[key] for key in ('audio_from', 'audio_from_source', 'audio_from_range')}
                                 for item in render_plans],
                'preview': preview, 'fast': fast, 'spatial_render_plan': spatial_plan,
                'render_context_range': c._scene_result_range(render_start, render_end-render_start),
                'finish_command': finish_command,
                'scene_render_commands': artifacts['scene_render_commands'],
                'ffmpeg_command': artifacts['concat_command'], 'render_available': True,
                'transition_render_plan': artifacts['transition_render_plan'],
                'temporary_artifacts': True,
                'hint': 'Spatial frames use the same scene tree and clock as composition model preview.',
            }
            if artifacts['audio_dropped_note']:
                result['audio_dropped_note'] = artifacts['audio_dropped_note']
            if fast:
                result['note'] = '--fast is incompatible with spatial composition; video is re-encoded.'
            if dry_run:
                result.update({'dry_run': True, 'status': 'would_render' if command == 'export' else 'would_extract',
                               'would_render_to' if command == 'export' else 'would_extract_to': str(destination)})
                c._apply_dry_run_setup(result, artifacts['setup_commands'])
            else:
                if not quiet:
                    click.echo(f'Rendering spatial composition ({sum(p["frame_count"] for p in spatial_plan)} spatial frames)...', err=True)
                c._render_scene_video_artifacts(artifacts=artifacts, abs_output=str(staged), preview=preview,
                                                audio_join_fade=audio_join_fade, quiet=quiet)
                if finish_command:
                    from moviestar.spatial_render import _run
                    _run(finish_command)
                    os.replace(Path(temporary) / 'finished.mp4', staged)
                result.update({'status': 'exported' if command == 'export' else 'extracted', 'out': str(staged),
                               'file_size_bytes': staged.stat().st_size})
            c._attach_audio_schema_envelope(result, spec=spec, result_duration=timeline.duration_s,
                command=command, source_audio_available=has_audio, window_start=from_s, window_duration=end-from_s,
                resolved_audio_mix=resolved_mix, loudness_report=loudness_report)
            c._add_loudness_result(result, loudness_target, dry_run=dry_run)
            if not dry_run:
                c._finalize_loudness_normalization(command, result, loudness_target, str(staged))
                result['actual_duration'] = format_timecode(float(c.run_ffprobe(str(staged))['format']['duration']))
                os.replace(staged, destination)
                result['out'] = str(destination)
            c._echo_render_result(result)
    except (ValueError, OSError, RuntimeError, SpatialLayoutError, ResolvedProjectError) as exc:
        c._error_exit(command, str(exc))
