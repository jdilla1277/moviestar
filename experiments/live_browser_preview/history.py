"""Prepare explicit undo depths outside timed benchmark edits."""
from copy import deepcopy
from hashlib import sha256
import json

from moviestar.spec import REVISION_LIMIT, append_revision, load_spec, save_spec


def current_hash(spec):
    return sha256(json.dumps({k: v for k, v in spec.items() if k != 'revisions'}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def seed_history(root, count):
    """Use real revision diffs; even depths finish on the same current model.

    Only call on a new, owned benchmark project, after CLI normalization. This
    intentionally replaces setup history outside the timer; it never edits a
    supplied user's project or removes history during the measured series.
    """
    if count not in (0, 20, REVISION_LIMIT):
        raise ValueError('History benchmark supports depths 0, 20 and 50')
    spec = load_spec(root)
    original = current_hash(spec)
    spec['revisions'] = []
    scenes = [name for name in ('review', 'cut_b', 'cut_return') if name in spec['spatial']['scenes']]
    for i in range(count):
        after = {**spec, 'spatial': deepcopy(spec['spatial'])}
        for name in scenes:
            title = next(node for node in after['spatial']['scenes'][name]['layout']['children'] if node.get('id') == 'title')
            title['content']['text'] = 'History seed' if i % 2 == 0 else 'Live edit A'
        spec = append_revision(spec, after, f'benchmark seed {i + 1}')
    if current_hash(spec) != original:
        raise ValueError('Seeding changed the current document')
    save_spec(spec, cwd=root, record=False)
    return {'initial_history': count, 'revision_limit': REVISION_LIMIT,
            'initial_spec_bytes': (root/'moviestar/spec.json').stat().st_size,
            'initial_current_hash': original}
