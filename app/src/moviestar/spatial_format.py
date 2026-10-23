"""Canvas predicates for declarative spatial format rules."""

from math import isfinite


class SpatialFormatError(ValueError):
    """A format rule cannot be interpreted unambiguously."""


def canonical_property(key: str) -> str:
    key = key.replace('_', '-')
    return {'columns': 'grid-template-columns', 'rows': 'grid-template-rows'}.get(key, key)


def validate_format_rules(rules: object, path: str) -> None:
    if not isinstance(rules, list):
        raise SpatialFormatError(f'{path}: expected an array')
    identities = set()
    constraints = {'orientation', *(f'{bound}_{dimension}' for bound in ('min', 'max')
                                   for dimension in ('width', 'height', 'aspect_ratio'))}
    for index, rule in enumerate(rules):
        where = f'{path}[{index}]'
        if not isinstance(rule, dict) or set(rule) - {'id', 'when', 'priority', 'layout', 'style'}:
            raise SpatialFormatError(f'{where}: expected id, when, priority, layout, or style fields')
        identity = rule.get('id')
        if not isinstance(identity, str) or not identity.strip():
            raise SpatialFormatError(f'{where}.id: expected a non-empty stable rule ID')
        if identity in identities:
            raise SpatialFormatError(f'{where}.id: duplicate rule ID {identity!r}')
        identities.add(identity)
        when = rule.get('when')
        if not isinstance(when, dict) or not when or set(when) - constraints:
            raise SpatialFormatError(f'{where}.when: expected orientation or numeric canvas constraints')
        if 'orientation' in when and when['orientation'] not in ('portrait', 'landscape', 'square'):
            raise SpatialFormatError(f'{where}.when.orientation: use portrait, landscape, or square')
        for key, value in when.items():
            if key == 'orientation':
                continue
            try:
                valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                         and isfinite(value) and value >= 0
                         and (not key.endswith('aspect_ratio') or value > 0))
            except OverflowError:
                valid = False
            if not valid:
                raise SpatialFormatError(f'{where}.when.{key}: expected a finite non-negative canvas bound (positive for ratios)')
        for dimension in ('width', 'height', 'aspect_ratio'):
            lower, upper = when.get(f'min_{dimension}'), when.get(f'max_{dimension}')
            if lower is not None and upper is not None and lower > upper:
                raise SpatialFormatError(f'{where}.when: min_{dimension} exceeds max_{dimension}')
        priority = rule.get('priority', 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise SpatialFormatError(f'{where}.priority: expected an integer')
        for group in ('layout', 'style'):
            if group in rule and not isinstance(rule[group], dict):
                raise SpatialFormatError(f'{where}.{group}: expected an object')


def matches_canvas(when: dict, canvas: dict) -> bool:
    width, height = canvas['width'], canvas['height']
    orientation = 'portrait' if height > width else 'landscape' if width > height else 'square'
    if 'orientation' in when and when['orientation'] != orientation:
        return False
    for dimension, value in (('width', width), ('height', height), ('aspect_ratio', width / height)):
        if value < when.get(f'min_{dimension}', value) or value > when.get(f'max_{dimension}', value):
            return False
    return True
