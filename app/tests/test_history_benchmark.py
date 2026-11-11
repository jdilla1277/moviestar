"""Benchmark preparation holds the current model fixed while history grows."""
import importlib.util
from pathlib import Path

import pytest

from moviestar.spec import load_spec, pop_revision, validate_spec


@pytest.mark.parametrize('depth', [0,20,50])
def test_history_fixture_is_exact_valid_and_current_state_is_constant(tmp_path,depth):
    folder=Path(__file__).parents[2]/'experiments/live_browser_preview'
    def load(name):
        entry=importlib.util.spec_from_file_location('history_fixture_'+name,folder/(name+'.py'))
        module=importlib.util.module_from_spec(entry);entry.loader.exec_module(module);return module
    root=tmp_path/'project';fixtures=load('fixtures');history=load('history')
    original,_=fixtures.generate(root)
    expected=history.current_hash(original)
    details=history.seed_history(root,depth)
    spec=load_spec(root)
    assert len(spec['revisions'])==details['initial_history']==depth
    assert history.current_hash(spec)==expected==details['initial_current_hash']
    assert details['initial_spec_bytes']==(root/'moviestar/spec.json').stat().st_size
    validate_spec(spec)
    while spec['revisions']:
        spec,_=pop_revision(spec)
    assert history.current_hash(spec)==expected
