"""Explicit scene layout reservations, checked against browser-measured boxes.

These are layout occupancy rules, not a pixel-occlusion or general overlap
heuristic. Unrelated boxes may layer freely unless a region was declared.
"""
from __future__ import annotations


class SpatialRegionError(ValueError):
    """A reservation is malformed."""


def validate_reserved_regions(value, where):
    def fail(message):
        raise SpatialRegionError(f'{where}: {message}')

    def path(value):
        if (not isinstance(value, list) or not value
                or any(not isinstance(part, str) or not part.strip() for part in value)):
            fail('target and allow paths must be non-empty arrays of stable child IDs')

    if not isinstance(value, list):
        fail('must be an array')
    seen = set()
    for region in value:
        if not isinstance(region, dict) or set(region) - {'id', 'target', 'allow'}:
            fail('each region needs id, target, and optional allow')
        identity = region.get('id')
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            fail('region IDs must be non-empty and unique within the scene')
        seen.add(identity)
        path(region.get('target'))
        allowed = region.get('allow', [])
        if not isinstance(allowed, list):
            fail('allow must be an array of paths')
        seen_paths = set()
        for item in allowed:
            path(item)
            if tuple(item) in seen_paths:
                fail('allow paths must be unique')
            seen_paths.add(tuple(item))


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get('children', []))


def region_reference_diagnostics(regions, scene_id, children):
    paths = {tuple(node['occurrence_path']) for node in _walk(children)}
    result = []
    for region in regions:
        for field, relative in [('target', region['target']),
                                *(('allow', path) for path in region.get('allow', []))]:
            path = [scene_id, *relative]
            if tuple(path) not in paths:
                result.append({'code': 'unknown_reserved_region_target', 'severity': 'error',
                    'path': path, 'region_id': region['id'], 'field': field,
                    'reason': 'update the reservation path or restore the named occurrence'})
    return result


def _intersection(a, b):
    x, y = max(a['x'], b['x']), max(a['y'], b['y'])
    right = min(a['x'] + a['width'], b['x'] + b['width'])
    bottom = min(a['y'] + a['height'], b['y'] + b['height'])
    return {'x': round(x, 3), 'y': round(y, 3), 'width': round(max(0, right-x), 3),
            'height': round(max(0, bottom-y), 3)}


def _prefix(parent, child):
    return child[:len(parent)] == parent


def inspect_reserved_regions(frame, canvas, boxes):
    """Report clipped border-box intersections at this frame, after motion.

    A target owns its complete subtree. Its ancestors are layout context, not
    intruders. Each allow path explicitly exempts that occurrence and descendants.
    Geometry is conservative: border radii, glyph silhouettes, and occlusion by
    a foreground layer do not shrink occupancy. No box is repositioned.
    """
    indexed = {tuple(box['path']): box for box in boxes}
    viewport = {'x': 0, 'y': 0, **canvas}
    reports, diagnostics = [], []
    for region in frame.get('reserved_regions', []):
        target = [frame['scene_id'], *region['target']]
        allowed = [[frame['scene_id'], *path] for path in region.get('allow', [])]
        box = indexed.get(tuple(target))
        if box is None:
            continue  # Expansion reports unresolved references, including allow paths.
        rect = _intersection(box.get('visible_rect', box['rect']), viewport)
        report = {'id': region['id'], 'path': target, 'rect': rect, 'allow': allowed, 'overlaps': []}
        reports.append(report)
        if rect['width'] <= 0 or rect['height'] <= 0:
            diagnostics.append({'code': 'unresolved_reserved_region_geometry', 'severity': 'error',
                'path': target, 'region_id': region['id'],
                'reason': 'the reserved target has no area on the requested canvas'})
            continue
        for other in boxes:
            path = other['path']
            if (_prefix(target, path) or _prefix(path, target)
                    or any(_prefix(allow, path) for allow in allowed)
                    or not other.get('occupies_layout', other.get('effective_opacity', 1) > 0)):
                continue
            intersection = _intersection(rect, other.get('visible_rect', other['rect']))
            # Ignore subpixel contact from rounded browser rectangles, not overlap.
            if intersection['width'] <= .001 or intersection['height'] <= .001:
                continue
            report['overlaps'].append({'path': path, 'intersection': intersection})
            diagnostics.append({'code': 'reserved_region_overlap', 'severity': 'error', 'path': path,
                'region_id': region['id'], 'region_path': target, 'intersection': intersection,
                'result_time_s': frame.get('sample_time_s', frame.get('result_time_s')),
                'reason': 'move this box, resize the reserved target, or explicitly allow this occurrence'})
    return {'reserved_regions': reports, 'diagnostics': diagnostics}
