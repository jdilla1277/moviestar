"""Canvas predicates and effective CSS assignments for spatial format rules."""

from math import isfinite
import re

_CSS_WIDE = {'initial', 'inherit', 'unset', 'revert', 'revert-layer'}


class SpatialFormatError(ValueError):
    """A format rule cannot be interpreted unambiguously."""


def canonical_property(key: str) -> str:
    key = key.replace('_', '-')
    return {'columns': 'grid-template-columns', 'rows': 'grid-template-rows'}.get(key, key)


def _trim_css(value: str) -> str:
    value = value.lstrip()
    end = len(value)
    while end and value[end - 1].isspace():
        backslash = end - 2
        while backslash >= 0 and value[backslash] == '\\':
            backslash -= 1
        if (end - 2 - backslash) % 2:
            break
        end -= 1
    return value[:end]


def _css_parts(value: str, separator: str | None = None) -> list[str]:
    """Split shorthand components, retaining functions, strings, and CSS escapes."""
    parts, current = [], []
    depth, quote, index = 0, None, 0
    while index < len(value):
        char = value[index]
        if char == '\\':
            end = index + 1
            if end < len(value) and value[end] in '0123456789abcdefABCDEF':
                while end < min(index + 7, len(value)) and value[end] in '0123456789abcdefABCDEF':
                    end += 1
                if end < len(value) and value[end].isspace():
                    end += 1
            else:
                end = min(end + 1, len(value))
            current.append(value[index:end])
            index = end
            continue
        if not quote and value.startswith('/*', index):
            end = value.find('*/', index + 2)
            if end < 0:
                raise SpatialFormatError('unterminated CSS comment')
            index = end + 2
            char = ' '
        else:
            index += 1
        if quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char in '([':
            depth += 1
        elif char in ')]':
            depth -= 1
        if not quote and depth == 0 and (char == separator if separator else char.isspace()):
            part = _trim_css(''.join(current))
            if part or separator:
                parts.append(part)
            current = []
        else:
            current.append(char)
    if depth or quote:
        raise SpatialFormatError('unbalanced CSS shorthand')
    part = _trim_css(''.join(current))
    if part or separator:
        parts.append(part)
    return parts


def _implicit_grid_end(value: str) -> str:
    tokens = _css_parts(value)
    # A single custom identifier repeats when the corresponding end is omitted.
    # Integers, auto, span placements, and math functions instead default to auto.
    if (len(tokens) == 1 and value.lower() not in {'auto', 'span'} and '(' not in value
            and not re.fullmatch(r'[+-]?[0-9]+', value)
            and (value[0].isalpha() or value[0] in '_-\\' or ord(value[0]) > 127)):
        return value
    return 'auto'


def _expand_declaration(key: str, value: object) -> dict:
    key = canonical_property(key)
    if key not in {'gap', 'grid-area', 'grid-row', 'grid-column'}:
        return {key: value}
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise SpatialFormatError('expected a CSS shorthand value')
    tokens = _css_parts(_trim_css(str(value)))
    text = ' '.join(tokens)
    if re.search(r'\b(?:var|env|attr)\s*\(', text, re.I):
        raise SpatialFormatError('substitution in shorthands requires explicit longhand properties')
    if key == 'gap':
        parts = tokens
        if not 1 <= len(parts) <= 2:
            raise SpatialFormatError('gap needs one or two values')
        if len(parts) > 1 and any(part.lower() in _CSS_WIDE for part in parts):
            raise SpatialFormatError('CSS-wide keywords must occupy the entire shorthand')
        if not isinstance(value, str):
            parts = [value]
        return {'row-gap': parts[0], 'column-gap': parts[-1]}
    if text.lower() in _CSS_WIDE:
        parts = [text] * (4 if key == 'grid-area' else 2)
    else:
        parts = _css_parts(text, '/')
        if not 1 <= len(parts) <= (4 if key == 'grid-area' else 2) or not all(parts):
            raise SpatialFormatError(f'invalid {key} placement')
        if any(part.lower() in _CSS_WIDE for part in parts):
            raise SpatialFormatError('CSS-wide keywords must occupy the entire shorthand')
        if key == 'grid-area':
            if len(parts) == 1:
                parts.append(_implicit_grid_end(parts[0]))
            if len(parts) == 2:
                parts.append(_implicit_grid_end(parts[0]))
            if len(parts) == 3:
                parts.append(_implicit_grid_end(parts[1]))
        elif len(parts) == 1:
            parts.append(_implicit_grid_end(parts[0]))
    names = (['grid-row-start', 'grid-column-start', 'grid-row-end', 'grid-column-end']
             if key == 'grid-area' else [f'{key}-start', f'{key}-end'])
    return dict(zip(names, parts))


def expand_properties(properties: dict, path: str) -> dict:
    """Resolve overlapping accepted shorthands into their effective longhands."""
    result = {}
    for key, value in properties.items():
        try:
            result.update(_expand_declaration(key, value))
        except SpatialFormatError as exc:
            raise SpatialFormatError(f'{path}.{key}: {exc}') from exc
    return result


def remove_properties(properties: dict, removed: set[str], path: str) -> dict:
    """Remove only affected assignments, retaining a shorthand's other effects."""
    result = {}
    for key, value in properties.items():
        expanded = expand_properties({key: value}, path)
        replacements = ({part: item for part, item in expanded.items() if part not in removed}
                        if removed.intersection(expanded) else {key: value})
        for replacement, item in replacements.items():
            for earlier in list(result):
                if canonical_property(earlier) == canonical_property(replacement):
                    del result[earlier]
            result[replacement] = item
    return result


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
