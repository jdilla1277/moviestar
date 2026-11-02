"""Standard screenshot and thumbnail inspection of the shared spatial frame.

Inspection batches containing spatial frames always produce fresh artifacts.
This deliberately introduces no asset/clip cache or invalidation contract.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from uuid import uuid4

from moviestar.spatial_layout import prepare_layout_frame
from moviestar.spatial_model import compile_spatial_frame, spatial_frame_sample_time
from moviestar.spatial_render import _asset_path, _publish, _run, render_spatial_frame
from moviestar.spatial_session import BrowserSession
from moviestar.timecodes import format_timecode


def _captions(nodes):
    for node in nodes:
        if node.get('type') == 'caption':
            yield node
        yield from _captions(node.get('children', []))


def spatial_frame_context(project, spec, timeline, at_s, dimensions):
    """Refresh only placed live caption tracks, in memory, for every still view."""
    from moviestar.cli import _caption_staleness, _refresh_caption_tracks

    durations = {s['id']: s['duration']['seconds'] for s in project.get('sources', [])
                 if isinstance(s.get('duration'), dict)
                 and isinstance(s['duration'].get('seconds'), (int, float))}
    frame = compile_spatial_frame(spec, timeline, at_s, source_durations=durations)
    from moviestar.spatial_transition import scene_frames
    tracks = {node['caption']['track'] for child in scene_frames(frame) for node in _captions(child['children'])
              if node['caption']['track'] is not None}
    if tracks:
        spec = _refresh_caption_tracks(project, spec, tracks=tracks)
        frame = compile_spatial_frame(spec, timeline, at_s, source_durations=durations)
    stale = {recipe['track'] for recipe in spec.get('captions', [])
             if recipe['track'] in tracks and not recipe.get('frozen')
             and _caption_staleness(project, spec, recipe)}
    for node in (node for child in scene_frames(frame) for node in _captions(child['children'])):
        if node['caption']['track'] in stale:
            frame['diagnostics'].append({'code': 'stale_caption_track', 'path': node['occurrence_path'],
                'track': node['caption']['track'],
                'reason': 'live caption cues could not be refreshed; check the transcript and audio routing'})
    return spec, frame, dimensions


def spatial_frame_plan(project, spec, timeline, at_s):
    from moviestar.cli import _scene_result_range

    spec, frame, canvas = spatial_frame_context(project, spec, timeline, at_s, timeline.canvas)
    # Dry-run validates authored rules and readiness without launching Chrome.
    prepared = prepare_layout_frame(frame, canvas)
    errors = [d for d in prepared['diagnostics'] if d.get('severity') != 'warning']
    if errors:
        raise ValueError(f"{errors[0]['code']} at {'/'.join(errors[0]['path'])}")
    span = timeline.span_at(spatial_frame_sample_time(timeline, at_s))
    index = span.scene_index
    scene = spec['composition'][index]
    return {'scene': scene, 'canvas': canvas, 'canvas_tuple': (canvas['width'], canvas['height']),
            'spatial_spec': spec, 'spatial_frame': frame, 'render_slots': [], 'slots': [],
            'camera_states': {}, 'camera_render_status': 'not_applicable',
            'scene_window': {'scene': scene.get('name', frame['scene_id']), 'scene_id': frame['scene_id'],
                'index': index, 'pacing_segment_id': frame['pacing_segment_id'],
                'result_range': _scene_result_range(frame['sample_time_s'], 0),
                'scene_result_range': _scene_result_range(span.scene_start_s, frame['scene_duration_s']),
                'scene_local_range': _scene_result_range(frame['scene_time_s'], 0),
                'layout': prepared['scene_layout'],
                'slots': []}}


def _validate_image_destination(spec, output, root):
    output = Path(output).resolve()
    if output.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        raise ValueError('spatial screenshot output must be PNG or JPEG')
    if not output.parent.is_dir():
        raise ValueError(f'output parent directory does not exist: {output.parent}')
    assets = [s['path'] for s in spec['sources']]
    assets += [a['path'] for a in (spec.get('spatial') or {}).get('assets', {}).values()]
    if output in {_asset_path(path, root) for path in assets}:
        raise ValueError('screenshot output cannot overwrite a project asset')
    return output


def _convert_image(source, output, *, width=None):
    if output.suffix.lower() == '.png' and width is None:
        shutil.copyfile(source, output)
        return
    command = ['ffmpeg', '-v', 'error', '-y', '-i', str(source)]
    if width is not None:
        command += ['-vf', f'scale={width}:-1']
    command += ['-frames:v', '1', '-threads', '1']
    if output.suffix.lower() in {'.jpg', '.jpeg'}:
        command += ['-q:v', '2', '-pix_fmt', 'yuvj444p']
    _run(command + [str(output)])


def _platform_preview(plan, output, platform, report=None):
    from moviestar import cli as c

    # The platform adapter calls this field estimated_bounds; these rectangles
    # are actual browser measurements, including component motion.
    text = [{'id': '/'.join(box['path']), 'estimated_bounds': box['rect']}
            for box in (report or {}).get('boxes', []) if box['kind'] in {'text', 'caption'}
            and box.get('text_lines') and box.get('transition_weight', 1) > 0]
    media = [{'slot': '/'.join(item['path']), 'region': item['rect']}
             for item in (report or {}).get('media', []) if item.get('transition_weight', 1) > 0]
    return c._plan_platform_preview(platform, plan['canvas_tuple'], str(output),
        planned_overlays={'plans': text}, slots=media, scene_name=plan['scene_window']['scene'])


def _render_image(plan, output, *, root, browser=None, width=None, platform=None):
    from moviestar import cli as c

    with TemporaryDirectory(prefix='.moviestar-still-', dir=output.parent) as directory:
        temporary = Path(directory)
        report = render_spatial_frame(plan['spatial_spec'], plan['spatial_frame'], plan['canvas'],
            output_path=temporary / 'frame.png', project_root=root, browser_session=browser)
        staged = temporary / ('result' + output.suffix.lower())
        _convert_image(temporary / 'frame.png', staged, width=width)
        annotation = _platform_preview(plan, staged, platform, report) if platform else None
        if annotation:
            c._render_platform_preview_in_place('screenshot', annotation)
        image_width, image_height = c._probe_output_dimensions(str(staged))
        _publish(staged, output)
    return {**report, 'artifact': str(output),
            'image_dimensions': {'width': image_width, 'height': image_height}}


def screenshot(plan, composition_plan, *, output, at_s, paths_only, platform, dry_run):
    from moviestar import cli as c

    try:
        if output is None:
            output = c._resolve_unique_output_path(f'screenshot_{at_s:.3f}s.jpg')
        root = c.get_project_dir().parent
        output = _validate_image_destination(plan['spatial_spec'], output, root)
        frame = plan['spatial_frame']
        result = {'mode': 'spatial_composition', 'composition_type': 'scenes',
            'composition_canvas': plan['canvas'],
            'composition_duration': format_timecode(composition_plan['composition_duration']),
            'result_timecode': format_timecode(at_s), 'sample_timecode': format_timecode(frame['sample_time_s']),
            'active_scene': plan['scene_window'], 'spatial_frame': frame}
        if dry_run:
            result.update({'dry_run': True, 'status': 'would_extract', 'would_write_to': str(output),
                'render_available': True,
                'spatial_render_plan': {'scene_id': frame['scene_id'], 'result_time_s': at_s,
                                        'canvas': plan['canvas'], 'out': str(output)},
                'hint': 'Re-run screenshot without --dry-run to render the shared spatial frame.'})
            c._attach_platform_preview(result, _platform_preview(plan, output, platform), annotated=False)
            if platform:
                result['platform_preview']['geometry_measurement'] = 'pending_render'
        else:
            report = _render_image(plan, output, root=root, platform=platform)
            width, height = report['image_dimensions']['width'], report['image_dimensions']['height']
            result.update({'status': 'extracted', 'out': str(output), 'width': width, 'height': height,
                'file_size_bytes': output.stat().st_size, 'spatial_preview': report})
            if not paths_only:
                result['image'] = c._read_image_inline(str(output))
            c._attach_platform_preview(result, _platform_preview(plan, output, platform, report), annotated=True)
        c.click.echo(json.dumps(result, indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        c._error_exit('screenshot', str(exc))


def inspect_frames(project, spec, composition_plan, window_plans, samples, frame_plans, *,
                   from_s, to_s, interval, width, paths_only, dry_run):
    from moviestar import cli as c

    root = c.get_project_dir().parent
    frames_out = c.get_project_dir() / c.INSPECT_FRAMES_DIR / f'spatial_{uuid4().hex}'
    paths = [frames_out / f'scene_inspect_{i+1:04d}.jpg' for i in range(len(samples))]
    plans = [{'scene_id': p['scene'].get('id'), 'renderer': 'spatial' if 'spatial_frame' in p else 'preset',
              'result_time_s': sample['result_time'], 'out': str(path), 'canvas': p['canvas'],
              'thumbnail_width': width} for sample, p, path in zip(samples, frame_plans, paths)]
    for summary, plan in zip(plans, frame_plans):
        frame = plan.get('spatial_frame', {})
        if frame.get('kind') == 'transition':
            summary['transition'] = {'transitions': frame['transitions'], 'color_weights': frame['color_weights'],
                'layers': [{key: layer[key] for key in ('scene_id', 'role', 'weight')} for layer in frame['layers']]}
    result = {'mode': 'spatial_composition', 'composition_type': 'scenes',
        'composition_canvas': composition_plan['canvas'],
        'composition_duration': format_timecode(composition_plan['composition_duration']),
        'range': c._scene_result_range(from_s, to_s-from_s),
        'scene_windows': [p['scene_window'] for p in window_plans],
        'scenes_count': len({p['scene_window']['index'] for p in window_plans}),
        'pacing_segments_count': len(window_plans),
        'slots_count': sum(len(p['slots']) for p in window_plans),
        'extraction': {'interval_seconds': interval, 'sampling_mode': 'per_scene',
            'window_anchor_samples': len(window_plans), 'scene_interval_samples': len(samples)-len(window_plans),
            'scale_width': width, 'cached': False},
        'transcript': None, 'transcription_skipped_reason': 'scene_composition',
        'spatial_render_plan': plans}
    if dry_run:
        result.update({'dry_run': True, 'status': 'would_extract', 'would_write_to': str(frames_out.resolve()),
            'would_reuse_cache': False, 'would_extract_frames_count': len(samples), 'render_available': True,
            'hint': 'Re-run inspect without --dry-run. Spatial inspection always renders fresh thumbnails.'})
        c.click.echo(json.dumps(result, indent=2))
        return
    try:
        frames_out.parent.mkdir(parents=True, exist_ok=True)
        thumbnails = []
        with TemporaryDirectory(prefix='.spatial-inspect-', dir=frames_out.parent) as directory:
            stage = Path(directory)
            with BrowserSession() as browser:
                for sample, plan, path in zip(samples, frame_plans, paths):
                    rendered = stage / path.name
                    report = None
                    if 'spatial_frame' in plan:
                        report = _render_image(plan, rendered, root=root, browser=browser, width=width)
                    else:
                        full = stage / 'preset.png'
                        context = c._overlay_render_context(project, spec, plan['canvas_tuple'],
                            sample['result_time'], sample['result_time']+.001, 'inspect')
                        c._write_highlight_ass(context)
                        c.render_layout_frame(plan['render_slots'], str(full), plan['canvas_tuple'],
                            overlay_plans=context['plans'] if context else None,
                            highlight_ass=c._highlight_ass_arg(context))
                        _convert_image(full, rendered, width=width)
                        full.unlink()
                    thumbnail = {'path': str(path.resolve()), 'timecode': format_timecode(sample['result_time']),
                        'scene': plan['scene_window']['scene'], 'scene_id': plan['scene'].get('id'),
                        'scene_local_timecode': format_timecode(sample['scene_local_time']),
                        'sample_kind': sample['sample_kind'], 'slots': c._instant_render_slots(plan, sample['result_time'])}
                    if report is not None:
                        thumbnail['spatial_preview'] = {**report, 'artifact': str(path.resolve())}
                    if not paths_only:
                        thumbnail['image'] = c._read_image_inline(str(rendered))
                    thumbnails.append(thumbnail)
            # Publish only the complete batch, after normal browser shutdown.
            os.replace(stage, frames_out)
        result.update({'status': 'extracted', 'thumbnails': thumbnails, 'thumbnails_returned': len(thumbnails)})
        result['extraction']['frames_dir'] = str(frames_out.resolve())
        result['hint'] = 'Spatial thumbnails use the model preview frame. Geometry is in the authored canvas; JPEGs are resized afterward.'
        c.click.echo(json.dumps(result, indent=2))
    except c.FFmpegCapabilityError as exc:
        c._ffmpeg_capability_error_exit('inspect', exc, 'Frame extraction failed')
    except (ValueError, OSError, RuntimeError) as exc:
        c._error_exit('inspect', f'Frame extraction failed: {exc}')
