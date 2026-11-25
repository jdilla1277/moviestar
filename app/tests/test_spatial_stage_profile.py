"""Attribution must not double-count child work or hide render failures."""
from pathlib import Path
import runpy

import pytest

Stages=runpy.run_path(str(Path(__file__).parents[2]/'experiments/spatial_stream_performance/stages.py'))['Stages']


def test_nested_wall_attribution_and_failure_identity(monkeypatch):
    clock=iter([0,1,3,5,6,9])
    monkeypatch.setitem(Stages.wrap.__globals__,'perf_counter',lambda:next(clock))
    profile=Stages()
    child=profile.wrap('child',lambda:42)
    parent=profile.wrap('parent',lambda:child())
    assert parent()==42
    failure=ValueError('original render failure')
    def broken():raise failure
    with pytest.raises(ValueError) as raised:profile.wrap('broken',broken)()
    assert raised.value is failure and not profile.stack
    rows=profile.report()['stages']
    assert rows['child']=={'calls':1,'inclusive_s':2,'exclusive_s':2}
    assert rows['parent']=={'calls':1,'inclusive_s':5,'exclusive_s':3}
    assert rows['broken']=={'calls':1,'inclusive_s':3,'exclusive_s':3}
