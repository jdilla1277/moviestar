"""Read a trusted portable saved spec or project an audio patch, without IO.

The service owns revision authority, dependency authorization and persistence.
All timeline/audio meaning comes from the existing public engine.
"""
from __future__ import annotations
import copy
import math
from pathlib import Path
from uuid import UUID

from moviestar.edit_validation import EditValidationError, _sources, _integer, _resolved
from moviestar.spec import validate_spec, set_audio_mix, append_revision, append_trim, pop_revision, REVISION_FIELDS, SpecValidationError
from moviestar.resolved import resolve_project, ResolvedProjectError
from moviestar.audio_surface import resolve_audio_mix, AudioSurfaceValidationError


def parse_audio_source(value):
    if (not isinstance(value, dict) or value.get('type') != 'audio_source'
            or not {'type'} < set(value) <= {'type', 'gain_db', 'muted'}):
        raise EditValidationError('invalid_edit', 'Supply audio_source and gain_db and/or muted only.')
    if 'gain_db' in value and (type(value['gain_db']) not in (int, float)
            or not math.isfinite(value['gain_db']) or not -60 <= value['gain_db'] <= 24):
        raise EditValidationError('invalid_edit', 'Use finite gain from -60 through 24 dB.', 'operation.gain_db')
    if 'muted' in value and type(value['muted']) is not bool:
        raise EditValidationError('invalid_edit', 'Use a boolean mute control.', 'operation.muted')
    return {k: v for k, v in value.items() if k != 'type'}


def _asset(value):
    try:
        if not isinstance(value, str) or not value.startswith('asset:'):
            raise ValueError()
        return str(UUID(value[6:]))
    except (ValueError, TypeError, AttributeError):
        raise EditValidationError('invalid_edit', 'Saved paths must be symbolic asset UUIDs.', 'spec') from None


def prepare_saved_edit(sources, spec, operation=None, *, audio_assets=None):
    """Return exact saved intent and resolved audio, or an in-memory candidate.

    Audio file durations come only from authorized pinned metadata; the probe
    callback never opens media. The input spec and command journal are untouched.
    """
    sources = _sources(copy.deepcopy(sources))
    spec = copy.deepcopy(spec)
    audio_assets = copy.deepcopy(audio_assets or [])
    if not isinstance(audio_assets, list) or len(audio_assets) > 32:
        raise EditValidationError('invalid_edit', 'Use at most 32 audio dependencies.', 'dependencies')
    assets = {}
    for item in audio_assets:
        try:
            asset = str(UUID(item['asset_id']))
            duration = item['media']['duration_ms']
            if type(duration) is not int or not 1 <= duration <= 86400000 or asset in assets:
                raise ValueError()
            media = item['media']
            for key, bound in [('sample_rate',10000000),('channels',1024)]:
                if media.get(key) is not None and (type(media[key]) is not int or not 1 <= media[key] <= bound):
                    raise ValueError()
            if media.get('audio_codec') is not None and (not isinstance(media['audio_codec'],str) or len(media['audio_codec'])>64):
                raise ValueError()
            assets[asset] = dict(duration_seconds=duration / 1000,codec=media.get('audio_codec'),
                                 sample_rate=media.get('sample_rate'),channels=media.get('channels'))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise EditValidationError('invalid_edit', 'Use unique audio assets with measured duration.', 'dependencies') from None
    source_by_id = {s['id']: s for s in sources}
    allowed_assets = set(assets) | {_asset(s['path']) for s in sources}
    def portable(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'path' and _asset(child) not in allowed_assets:
                    raise EditValidationError('invalid_edit', 'A dependency is not pinned.', 'spec')
                portable(child)
        elif isinstance(value, list):
            for child in value: portable(child)
    portable(spec)  # Includes restorable journal state; no client/local paths.
    try:
        validate_spec(spec)
        active_ids = {s['id'] for s in spec['sources']}
        if not active_ids <= set(source_by_id):
            raise EditValidationError('invalid_edit', 'Saved sources must match authorized metadata.', 'spec.sources')
        for source in spec['sources']:
            if source['path'] != source_by_id[source['id']]['path']:
                raise EditValidationError('invalid_edit', 'Saved source/asset mapping changed.', 'spec.sources')
        project = {'sources': [dict(s, duration={'seconds':s['duration_ms'] / 1000}) for s in sources if s['id'] in active_ids]}
        timeline = resolve_project(project, spec)
        def summarize(value):
            # normalize_audio_document resolves paths before invoking its probe;
            # only the opaque symbolic filename is accepted by this callback.
            def probe(path):
                asset = _asset(Path(path).name)
                if asset not in assets:
                    raise ValueError('Audio dependency metadata is unavailable.')
                return copy.deepcopy(assets[asset])
            active = {s['id'] for s in value['sources']}
            state_project = {'sources': [dict(s, duration={'seconds':s['duration_ms']/1000}) for s in sources if s['id'] in active]}
            state_timeline = resolve_project(state_project, value)
            mix = resolve_audio_mix(value['audio_mix'], result_duration=state_timeline.duration_s,
                                    base_dir=Path('/'), probe_audio=probe)
            for track in mix['tracks']:
                track['path'] = 'asset:' + _asset(Path(track['path']).name)
            return dict(audio_mix=mix, track_count=len(mix['tracks']),
                        routing=[dict(scene_id=span.scene_id, result_range_ms=[round(span.start_s*1000),round(span.end_s*1000)],
                                      source_id=span.audio.source_id, routing=span.audio.routing,
                                      source_slices_ms=[[round(a*1000),round(b*1000)] for a,b in span.audio.slices]) for span in state_timeline.spans],
                        sources=_resolved(value,[s for s in sources if s['id'] in active])['sources'],
                        result_duration_ms=round(state_timeline.duration_s*1000), undo_remaining=len(value['revisions']),
                        source_audio_availability={s['id']:('unreported' if 'audio_codec' not in s else 'stream_reported' if s['audio_codec'] else 'no_stream_in_metadata') for s in sources if s['id'] in active_ids},
                        loudness=None, loudness_status='not_measured')
        prior = spec
        while prior.get('revisions'):
            prior, _ = pop_revision(prior)
            summarize(prior)
        result = dict(origin='saved_revision', spec=spec, resolved=summarize(spec))
        if operation is None:
            return result
        if not isinstance(operation, dict):
            raise EditValidationError('invalid_edit', 'Supply an operation object.')
        kind = operation.get('type')
        if kind == 'trim':
            if set(operation) != {'type','source_id','time_space','from_ms','to_ms'} or operation['time_space'] != 'source_result':
                raise EditValidationError('invalid_edit', 'Trim requires source_id, source_result time_space and integer from_ms/to_ms.')
            source = source_by_id.get(operation['source_id']) if isinstance(operation['source_id'],str) else None
            if source is None or source['id'] not in active_ids:
                raise EditValidationError('invalid_edit', 'Select a source in the current edit.', 'operation.source_id')
            start = _integer(operation['from_ms'],0,86400000,'operation.from_ms')
            end = _integer(operation['to_ms'],1,86400000,'operation.to_ms')
            candidate = append_trim(spec,source['id'],start/1000,end/1000,source['duration_ms']/1000)
            candidate = append_revision(spec,candidate,'trim')
        elif kind == 'undo':
            if set(operation) != {'type'}:
                raise EditValidationError('invalid_edit', 'Undo accepts type only.')
            if not spec['revisions']:
                raise EditValidationError('nothing_to_undo','There is no accepted command to undo.')
            candidate, _ = pop_revision(spec)
        elif kind == 'audio_source':
            patch = parse_audio_source(operation)
            mix = copy.deepcopy(spec['audio_mix'])
            mix['source_audio'].update(patch)
            candidate = set_audio_mix(spec, mix)
            candidate = append_revision(spec,candidate,'audio source')
        else:
            raise EditValidationError('unsupported_feature','Supported operations: trim, undo, audio_source.')
        changes = candidate != spec
        validate_spec(candidate)
        result.update(changes=changes, changed_fields=[f for f in REVISION_FIELDS if spec.get(f) != candidate.get(f)],
                      current_undo_remaining=len(spec['revisions']), candidate_spec=candidate,
                      projected_resolved=summarize(candidate), warnings=[])
        return result
    except EditValidationError:
        raise
    except (SpecValidationError, ResolvedProjectError, AudioSurfaceValidationError, KeyError, ValueError) as exc:
        raise EditValidationError('invalid_edit', str(exc), 'spec') from None
