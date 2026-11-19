"""The benchmark edits active media, even with unused definitions retained."""
from copy import deepcopy
from pathlib import Path
import pytest


@pytest.mark.parametrize('scene,instance,definition,leaf',[
    ('title','panel','panel','background'),('review','main','speaker_card','footage')])
def test_camera_target_uses_active_occurrence_not_unused_definitions(monkeypatch,scene,instance,definition,leaf):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'experiments/live_browser_preview'))
    from camera_benchmark import camera_target
    spec={'spatial':{'definitions':{
        'speaker_card':{'children':[{'id':'text','type':'text'},{'id':'footage','type':'video','media_motion':{'scale':[]}}]},
        'panel':{'children':[{'id':'title','type':'text'},{'id':'background','type':'image','media_motion':{'scale':[]}}]}},
        'scenes':{scene:{'layout':{'children':[{'instance_id':instance,'definition_id':definition}]}}}}}
    original=deepcopy(spec)
    selected,element,target=camera_target(spec)
    assert (selected,element['id'],target)==(definition,leaf,[scene,instance,leaf])
    assert spec==original
