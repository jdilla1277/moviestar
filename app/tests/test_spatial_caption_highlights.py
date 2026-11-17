"""Highlighted caption elements retain their component layout and result clock."""
from copy import deepcopy
import pytest
from moviestar.spatial_layout import prepare_layout_frame
from tests.test_spatial_captions import caption_project, _frame, _caption


def highlighted(spec):
    cue = spec['overlays'][0]
    cue.update(text='First  line\nagain', to='0:00:01.500',
        highlight={'mode':'spoken-word','color':'#ffe94a'}, tokens=[
            {'text':'First','from':'0:00:00.250','to':'0:00:00.500'},
            {'text':'line','from':'0:00:00.750','to':'0:00:01.000'},
            {'text':'again','from':'0:00:01.000','to':'0:00:01.250'}])
    return cue


@pytest.mark.parametrize('at,active',[(.25,0),(.499,0),(.5,None),(.749,None),(.75,1),(1,2),(1.25,None)])
def test_highlight_boundaries_and_whitespace(caption_project, at, active):
    spec, project=caption_project;highlighted(spec);spec['overlays']=spec['overlays'][:1]
    before=deepcopy(spec);caption=_caption(_frame(spec,project,at))['caption'];cue=caption['cues'][0]
    assert cue['active_word']==active
    assert cue['highlight_color']=='#ffe94a'
    assert cue['word_spans']==[{'start':0,'end':5},{'start':7,'end':11},{'start':12,'end':17}]
    assert caption['text']=='First  line\nagain'
    assert spec==before


@pytest.mark.parametrize('change',['missing','text','count'])
def test_invalid_tokens_report_blocking_occurrence_diagnostic(caption_project,change):
    spec,project=caption_project;cue=highlighted(spec)
    if change=='missing':cue.pop('tokens')
    if change=='text':cue['tokens'][0]['text']='Stale'
    if change=='count':cue['tokens'].pop()
    frame=_frame(spec,project,.3)
    prepared=prepare_layout_frame(frame,{'width':640,'height':360})
    issue=next(d for d in prepared['diagnostics']if d['code']=='invalid_caption_tokens' and d['severity']=='error')
    assert issue['severity']=='error'
    assert issue['path']==['first','subtitles','words']
    assert issue['overlay_id']=='one'
    assert 'Regenerate' in issue['message']


def test_element_highlight_color_and_stroke_follow_format_cascade(caption_project):
    spec,project=caption_project;highlighted(spec)
    node=spec['spatial']['definitions']['subtitles']['children'][0]
    node['style'].update(highlight_color='#00ff00',stroke_color='#000000',stroke_width=2)
    prepared=prepare_layout_frame(_frame(spec,project,.3),{'width':640,'height':360})
    style=prepared['nodes'][0]['children'][0]['style']
    assert style['--moviestar-highlight-color']=='#00ff00'
    assert style['-webkit-text-stroke-color']=='#000000'
    assert style['-webkit-text-stroke-width']=='2px'


@pytest.mark.parametrize('canvas',[{'width':640,'height':360},{'width':360,'height':640}])
def test_browser_words_keep_wrapping_and_apply_component_styling(caption_project,tmp_path,canvas):
    from moviestar.spatial_layout import browser_available,measure_spatial_layout
    from moviestar.spatial_render import render_spatial_frame
    import av,numpy as np
    if not browser_available():pytest.skip('Chrome and Node required')
    spec,project=caption_project;highlighted(spec);spec['overlays']=spec['overlays'][:1]
    spec['spatial']['definitions']['subtitles']['children'][0]['style'].update(
        highlight_color='#00ff00',stroke_color='#000000',stroke_width=2)
    boxes=[]
    for at,active in [(.3,0),(.8,1),(1.1,2)]:
        frame=_frame(spec,project,at)
        result=render_spatial_frame(spec,frame,canvas,output_path=tmp_path/f'{at}.png',
            layout_output_path=tmp_path/f'{at}-boxes.png')
        measured=measure_spatial_layout(frame,canvas)
        assert result['boxes']==measured['boxes']
        box=result['boxes'][1];words=box['caption_words']
        assert [w['word_index']for w in words if w['active']]==[active]
        assert words[active]['color']=='rgb(0, 255, 0)'
        assert len(box['text_lines'])==2 and not box['overflow']
        boxes.append([(w['rect'],w['text'])for w in words])
        def green(path):
            with av.open(str(path))as container:rgb=next(container.decode(video=0)).to_ndarray(format='rgb24')
            return int(((rgb[:,:,1]>160)&(rgb[:,:,0]<80)&(rgb[:,:,2]<80)).sum())
        assert green(tmp_path/f'{at}.png')>20
        assert green(tmp_path/f'{at}-boxes.png')==0,'Labeled view hides highlighted glyphs too'
    assert boxes[0]==boxes[1]==boxes[2]


def test_highlight_color_inherits_from_component_with_local_override(caption_project):
    from moviestar.spatial_layout import measure_spatial_layout,browser_available
    if not browser_available():pytest.skip('Chrome and Node required')
    spec,project=caption_project;highlighted(spec)
    parent=spec['spatial']['scenes']['first']['layout']['children'][0]
    parent['style']={'highlight_color':'#ff0000'}
    parent['motion']={'translate_x':[{'at':0,'value':0},{'at':1,'value':40}]}
    first=measure_spatial_layout(_frame(spec,project,.3),{'width':640,'height':360})['boxes']
    assert first[1]['caption_words'][0]['color']=='rgb(255, 0, 0)'
    assert first[0]['rect']['x']==pytest.approx(first[0]['base_rect']['x']+12,abs=.01)
    node=spec['spatial']['definitions']['subtitles']['children'][0]
    node['style']['highlight_color']='#00ff00'
    node['format_rules']=[{'id':'portrait','when':{'orientation':'portrait'},'style':{'highlight_color':'#0000ff'}}]
    portrait=measure_spatial_layout(_frame(spec,project,.3),{'width':360,'height':640})['boxes'][1]
    assert portrait['caption_words'][0]['color']=='rgb(0, 0, 255)'


def test_component_can_enable_or_disable_word_timing(caption_project):
    spec,project=caption_project;cue=highlighted(spec)
    parent=spec['spatial']['scenes']['first']['layout']['children'][0]
    cue['highlight']['mode']='none'
    parent['style']={'highlight_mode':'spoken-word'}
    prepared=prepare_layout_frame(_frame(spec,project,.3),{'width':640,'height':360})
    assert prepared['nodes'][0]['children'][0]['caption']['cues'][0]['highlight_mode']=='spoken-word'
    assert not prepared['diagnostics']
    cue['highlight']['mode']='spoken-word';cue.pop('tokens')
    parent['style']['highlight_mode']='none'
    prepared=prepare_layout_frame(_frame(spec,project,.3),{'width':640,'height':360})
    assert prepared['nodes'][0]['children'][0]['caption']['cues'][0]['highlight_mode']=='none'
    assert not any(d.get('severity')=='error' for d in prepared['diagnostics'])


def test_local_responsive_mode_overrides_component(caption_project):
    spec,project=caption_project;highlighted(spec)
    spec['spatial']['scenes']['first']['layout']['children'][0]['style']={'highlight_mode':'none'}
    node=spec['spatial']['definitions']['subtitles']['children'][0]
    node['format_rules']=[{'id':'portrait','when':{'orientation':'portrait'},'style':{'highlight_mode':'spoken-word'}}]
    for canvas,mode in [({'width':640,'height':360},'none'),({'width':360,'height':640},'spoken-word')]:
        prepared=prepare_layout_frame(_frame(spec,project,.3),canvas)
        assert prepared['nodes'][0]['children'][0]['caption']['cues'][0]['highlight_mode']==mode


def test_unicode_word_spans_preserve_supplementary_characters(caption_project):
    from moviestar.spatial_layout import browser_available,measure_spatial_layout
    if not browser_available():pytest.skip('Chrome and Node required')
    spec,project=caption_project;cue=highlighted(spec)
    cue['text']='Hello😀  café\n世界';cue['tokens'][0]['text']='Hello😀'
    cue['tokens'][1]['text']='café';cue['tokens'][2]['text']='世界'
    box=measure_spatial_layout(_frame(spec,project,.8),{'width':640,'height':360})['boxes'][1]
    assert [w['text']for w in box['caption_words']]==['Hello😀','café','世界']
    assert box['caption']['text']==cue['text']


def test_token_overlap_clipping_and_zero_duration(caption_project):
    from moviestar.spatial_caption import highlight_state
    spec,_=caption_project;cue=highlighted(spec)
    cue['tokens'][0].update({'from':'0:00:00.000','to':'0:00:00.800'})
    cue['tokens'][1].update({'from':'0:00:00.500','to':'0:00:02.000'})
    cue['tokens'][2].update({'from':'0:00:01.000','to':'0:00:01.000'})
    assert highlight_state(cue,.799)['active_word']==0
    state=highlight_state(cue,.8)
    assert state['active_word']==1
    assert state['word_windows']==[{'word_index':0,'from_s':.25,'to_s':.8},
                                   {'word_index':1,'from_s':.8,'to_s':1.5}]
