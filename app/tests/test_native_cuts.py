"""A bounded native sequence preserves the canonical clock on each side of cuts."""
from copy import deepcopy

import pytest

from moviestar.local_preview import read_snapshot
from moviestar.native_preview import compile_snapshot, NativePreviewUnsupported
from moviestar.project import save_project
from moviestar.spec import save_spec, parse_timecode_string
from tests.test_live_browser_preview import live_project


@pytest.fixture
def cut_project(live_project):
    spec, project, root = live_project
    other = root / 'other.mp4'; other.write_bytes(b'other source fixture')
    spec['sources'].append({'id': 'other', 'path': str(other), 'operations': []})
    metadata = deepcopy(project['sources'][0]); metadata.update(id='other', path=str(other))
    project['sources'].append(metadata)
    original, tree = deepcopy(spec['composition'][0]), deepcopy(spec['spatial']['scenes']['scene'])
    spec['composition'] = []
    for identity, source, start in [('scene', 'camera', 1), ('middle', 'other', 2), ('return', 'camera', 4)]:
        scene = deepcopy(original); scene.update(id=identity, name=identity, audio_from=source)
        scene['slots'][0].update(id='slot_'+identity, source=source,
            source_from=f'0:00:0{start}.000', source_to=f'0:00:0{start+3}.000')
        spec['composition'].append(scene)
        design = deepcopy(tree)
        design['layout']['background_color'] = '#334455' if identity == 'middle' else '#112244'
        clip = design['layout']['children'][0]['bindings']['clip']
        clip.update(asset_id=source, source={'start': start, 'end': start+3})
        design['layout']['children'][1]['content']['text'] = identity
        spec['spatial']['scenes'][identity] = design
    save_project(project, cwd=root); save_spec(spec, cwd=root)
    return spec, project, root


@pytest.mark.parametrize('window,offsets,sources', [
    ((0,9),[0,3,6],[(1,4),(2,5),(4,7)]),
    ((2,8),[0,1,4],[(3,4),(2,5),(4,6)]),
])
def test_cut_packet_preserves_each_scene_and_source_clock(cut_project, window, offsets, sources):
    _, _, root = cut_project
    packet, assets = compile_snapshot(read_snapshot(root,*window))
    assert packet['range'] == {'from':window[0], 'to':window[1]}
    assert len(packet['segments']) == 3 and len(assets) == 2
    assert len(packet['media']) == 3
    first=packet['segments'][0]['packet']['media'][0]['asset']
    middle=packet['segments'][1]['packet']['media'][0]['asset']
    returned=packet['segments'][2]['packet']['media'][0]['asset']
    assert first == returned and first != middle
    assert assets[first]['path'] == root/'camera.mp4'
    assert assets[middle]['path'] == root/'other.mp4'
    assert 'source_start' not in packet, 'A multi-source sequence has no single source clock'
    for index, (segment, offset, source) in enumerate(zip(packet['segments'],offsets,sources)):
        assert segment['offset'] == offset
        child = segment['packet']
        assert (child['source_start'], child['source_end']) == source
        assert child['initial']['scene_id'] == ['scene','middle','return'][index]
        assert len(child['samples']) == round((source[1]-source[0])*30)
    assert packet['segments'][0]['packet']['samples'][0]['nodes'][0]['motion']['translate_x'] == ('0px' if window[0]==0 else '40px')
    assert packet['segments'][1]['packet']['samples'][0]['nodes'][0]['motion']['translate_x'] == '0px'


def test_single_span_protocol_stays_compatible(cut_project):
    packet,_ = compile_snapshot(read_snapshot(cut_project[2],3,6))
    assert 'segments' not in packet
    assert packet['source_start'] == 2 and packet['source_end'] == 5
    assert packet['initial']['scene_id'] == 'middle'


@pytest.mark.parametrize('change,reason', [('audio','audio'),('fps','rate'),('transition','transition'),('live_caption','live caption'),('too_many','three')])
def test_every_segment_is_validated_and_resources_are_bounded(cut_project,change,reason):
    spec, project, root = cut_project
    if change=='audio':spec['composition_audio_from']='camera'
    elif change=='fps':project['sources'][1]['fps']=24
    elif change=='transition':spec['composition'][1]['transition_in']={'type':'dip-black','duration':.02}
    elif change=='live_caption':
        spec['spatial']['scenes']['middle']['layout']['children'][2]['content']['track']='late'
        spec['captions']=[{'track':'late','frozen':False}]
    else:
        fourth=deepcopy(spec['composition'][-1]);fourth.update(id='fourth',name='fourth');fourth['slots'][0]['id']='slot_fourth'
        # Make four two-second scenes fit the existing ten-second review cap.
        spec['composition'].append(fourth)
        spec['spatial']['scenes']['fourth']=deepcopy(spec['spatial']['scenes']['return'])
        for scene in spec['composition']:
            slot=scene['slots'][0];start=int(parse_timecode_string(slot['source_from'],'from'));slot['source_to']=f'0:00:0{start+2}.000'
            spec['spatial']['scenes'][scene['id']]['layout']['children'][0]['bindings']['clip']['source']['end']=start+2
    save_spec(spec,cwd=root);save_project(project,cwd=root)
    with pytest.raises(NativePreviewUnsupported,match=reason):
        compile_snapshot(read_snapshot(root,0,8 if change=='too_many' else 9))


def test_segment_packets_are_scoped_to_the_retained_parent_request(cut_project, tmp_path, monkeypatch):
    import json
    import threading
    from urllib.request import urlopen
    from urllib.error import HTTPError
    from moviestar.local_preview import PreviewServer, PreviewSession
    from tests.test_native_preview import _fake_workers, _complete
    root = cut_project[2]; folder = tmp_path / 'packets'; folder.mkdir()
    _fake_workers(monkeypatch)
    session = PreviewSession(root,0,9,folder,mode='native');_complete(session)
    server = PreviewServer(session.queue,token='a'*48,port=0,mode='native',session=session)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    request = session.queue.state()['displayed']['request_id']
    try:
        parent = json.load(urlopen(server.url+'packet?request='+request))
        child = json.load(urlopen(server.url+'packet?request='+request+'&segment=1'))
        assert child['request_id'] == request and child['state_id'] == parent['state_id']
        assert child['source_start'] == 2 and child['source_end'] == 5
        assert child['cut_segment'] is True and 'segments' not in child
        assert str(root) not in json.dumps(child)
        for suffix in ['&segment=-1','&segment=3','&segment=no','&segment=0&segment=1']:
            with pytest.raises(HTTPError) as error:urlopen(server.url+'packet?request='+request+suffix)
            assert error.value.code == 404
        with pytest.raises(HTTPError) as error:urlopen(server.url+'packet?request=missing&segment=1')
        assert error.value.code == 404
    finally:
        server.shutdown();server.server_close();thread.join()


def test_cuts_reject_non_frame_aligned_scene_boundaries(cut_project):
    spec,_,root=cut_project
    spec['composition'][0]['slots'][0]['source_to']='0:00:03.050'
    spec['spatial']['scenes']['scene']['layout']['children'][0]['bindings']['clip']['source']['end']=3.05
    save_spec(spec,cwd=root)
    with pytest.raises(NativePreviewUnsupported,match='frame-aligned scene'):
        compile_snapshot(read_snapshot(root,0,8))


def test_cuts_do_not_treat_pacing_changes_as_scene_cuts(cut_project):
    spec,_,root=cut_project
    spec['motion']={'version':1,'scenes':[{'scene':'scene','scene_id':'scene',
        'slots':[{'slot':'main','pacing':[{'id':'pause','mode':'hold','at':'0:00:02.000',
            'space':'source-local','duration':'0:00:01.000'}],'camera':[]}]}]}
    save_spec(spec,cwd=root)
    with pytest.raises(NativePreviewUnsupported,match='pacing'):
        compile_snapshot(read_snapshot(root,0,3))
