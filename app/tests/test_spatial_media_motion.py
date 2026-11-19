"""Camera tracks move media inside an unchanged decorated viewport."""
from copy import deepcopy
import subprocess
import pytest
from moviestar.spec import validate_spec,SpecValidationError,save_spec,load_spec
from moviestar.spatial_layout import prepare_layout_frame,browser_available,measure_spatial_layout
from moviestar.spatial_edit import edit_spatial_model
from tests.test_spatial_motion import _frame,_track
from tests.test_spatial_layout import _project
from tests.test_spatial_render import project,_frame as rendered_frame,_rgb


def test_media_tracks_sample_scene_time_and_survive_local_shared_edits(tmp_path):
    spec=_project();leaf=spec['spatial']['definitions']['pair']['children'][0]
    leaf['media_motion']={'scale':_track(1,2),'translate_x':_track('0%','-50%')}
    original=deepcopy(spec)
    frame=_frame(spec,2);node=prepare_layout_frame(frame,{'width':640,'height':360})['nodes'][0]['children'][0]
    assert node['media_motion']['scale']==1.5
    assert node['media_motion']['translate_x']=='-25%'
    assert 'motion' not in node
    assert spec==original
    target={'occurrence_path':['scene_a','listeners','left_video']}
    custom={'op':'instance.customize','target':target,'property':'media_motion.scale','value':_track(1,3)}
    spec,_=edit_spatial_model(spec,custom)
    spec,_=edit_spatial_model(spec,{'op':'component.update','target':{'definition_id':'pair','path':['left_video']},
        'property':'media_motion.scale','value':_track(1,4)})
    assert _frame(spec,2)['children'][0]['children'][0]['media_motion']['scale']==_track(1,3)
    save_spec(spec,cwd=tmp_path);assert load_spec(cwd=tmp_path)==spec
    varied,_=edit_spatial_model(spec,{'op':'component.save_variation',
        'source':{'occurrence_path':['scene_a','listeners']},'new_definition_id':'camera_variant','relink_source':True})
    assert _frame(varied,2)['children'][0]['children'][0]['media_motion']['scale']==_track(1,3)
    spec,_=edit_spatial_model(spec,{'op':'instance.reset','target':target,'property':'media_motion.scale'})
    assert _frame(spec,2)['children'][0]['children'][0]['media_motion']['scale']==_track(1,4)


@pytest.mark.parametrize('motion',[None,{'scale':_track(0,1)},{'translate_x':_track('0%',10)},{'rotate':_track(0,90)}])
def test_invalid_media_tracks_reject_before_save(motion):
    spec=_project();spec['spatial']['definitions']['pair']['children'][0]['media_motion']=motion
    with pytest.raises(SpecValidationError,match='media_motion'):validate_spec(spec)


@pytest.mark.parametrize('owner',['text','instance','definition','root'])
def test_media_motion_requires_video_or_image(owner):
    spec=_project();definition=spec['spatial']['definitions']['pair']
    target=definition['children'][2] if owner=='text' else definition if owner=='definition' else spec['spatial']['scenes']['scene_a']['layout']
    if owner=='instance':target=target['children'][0]
    target['media_motion']={'scale':_track(1,2)}
    with pytest.raises(SpecValidationError,match='media_motion'):validate_spec(spec)


@pytest.mark.skipif(not browser_available(),reason='Chrome and Node required')
@pytest.mark.parametrize('at,camera_rect',[(0,{'x':30,'y':30,'width':80,'height':80}),
    (.5,{'x':10,'y':10,'width':120,'height':120}),(1,{'x':-10,'y':-10,'width':160,'height':160})])
@pytest.mark.parametrize('fade',[False,True])
@pytest.mark.parametrize('outer_scale',[1,1.25])
def test_camera_moves_source_pixels_but_not_viewport_or_border(project,tmp_path,at,camera_rect,fade,outer_scale):
    from moviestar.spatial_render import render_spatial_frame
    spec,metadata=project;path=tmp_path/'gradient.mkv'
    pixels=bytes(v for y in range(90) for x in range(160) for v in (x, y, 255-x))
    subprocess.run(['ffmpeg','-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size','160x90',
        '-framerate','10','-i','pipe:0','-c:v','ffv1','-pix_fmt','bgr0',str(path)],input=pixels*20,check=True)
    spec['sources'][0]['path']=str(path)
    spec['spatial']['scenes']['interview']['layout']={'background_color':'#00ff00','children':[{
        'id':'picture','type':'video','content':{'asset_id':'camera','source':{'start':0,'end':1.5}},
        'layout':{'position':'absolute','left':20,'top':20,'width':100,'height':100,'padding':6},
        'style':{'object_fit':'cover','border_width':4,'border_style':'solid','border_color':'#ffe000','border_radius':20,'background_color':'#183848'},
        'media_motion':{'scale':[{'at':0,'value':1},{'at':1,'value':2}],
            'translate_x':[{'at':0,'value':'0%'},{'at':1,'value':'-50%'}],
            'translate_y':[{'at':0,'value':'0%'},{'at':1,'value':'-50%'}]}}]}
    if fade:spec['spatial']['scenes']['interview']['layout']['children'][0]['media_motion']['opacity']=[{'at':0,'value':1},{'at':1,'value':0}]
    if outer_scale!=1:spec['spatial']['scenes']['interview']['layout']['children'][0]['motion']={'scale':[{'at':0,'value':outer_scale}],'opacity':[{'at':0,'value':.5}]}
    frame=rendered_frame(spec,metadata,at);canvas={'width':320,'height':180}
    box=measure_spatial_layout(frame,canvas)['boxes'][0]
    assert box['rect']=={'x':20,'y':20,'width':100*outer_scale,'height':100*outer_scale}
    assert box['media_box']['content_rect']=={'x':20+10*outer_scale,'y':20+10*outer_scale,'width':80*outer_scale,'height':80*outer_scale}
    expected_camera={k:20+(v-20)*outer_scale if k in {'x','y'} else v*outer_scale for k,v in camera_rect.items()}
    assert box['media_box']['camera_rect']==expected_camera
    assert box['effective_opacity']==(1 if outer_scale==1 else .5)
    assert box['media_box']['media_opacity']==(1-at if fade else 1)
    output=tmp_path/'frame.png';reference=tmp_path/'reference.png';labels=tmp_path/'layout.png'
    result=render_spatial_frame(spec,frame,canvas,output_path=output,project_root=tmp_path,reference_path=reference,layout_output_path=labels)
    assert labels.is_file()
    assert result['boxes']==[box]
    actual,expected=_rgb(output),_rgb(reference)
    for x,y in [(5,5),(22,60),(25,40),(35,60),(60,60),(80,80),(115,60)]:
        offset=(y*320+x)*3
        assert max(abs(a-b)for a,b in zip(actual[offset:offset+3],expected[offset:offset+3]))<=2
    if fade and at==1 and outer_scale==1:
        offset=(60*320+60)*3
        assert tuple(actual[offset:offset+3])==(24,56,72)


def test_saved_layout_keeps_camera_tracks_and_reports_last_keyframe():
    from tests.test_spatial_saved_layouts import _arrangement
    from moviestar.spatial_saved_layouts import save_layout,inspect_saved_layout
    spec=_arrangement();node=spec['spatial']['scenes']['scene_a']['layout']['children'][0]
    node['media_motion']={'scale':[{'at':0,'value':1},{'at':10,'value':2}]}
    saved,_=save_layout(spec,'camera','scene_a')
    assert saved['spatial']['layouts']['camera']['layout']['children'][0]['media_motion']==node['media_motion']
    assert inspect_saved_layout(saved,'camera')['motion']['last_keyframe_s']==10


from tests.test_spatial_video import video_project,_invoke,_pixels,_rms


@pytest.mark.skipif(not browser_available(),reason='Chrome and Node required')
def test_exported_camera_movie_preserves_frame_geometry_clock_and_audio(video_project,tmp_path):
    spec,metadata=video_project;source=tmp_path/'moving-source.mkv'
    raw=b''.join(bytes(v for y in range(90) for x in range(160) for v in (x,40+8*n,255-x)) for n in range(20))
    subprocess.run(['ffmpeg','-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size','160x90',
        '-framerate','10','-i','pipe:0','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
        '-c:v','ffv1','-pix_fmt','bgr0','-c:a','pcm_s16le','-shortest',str(source)],input=raw,check=True)
    spec['sources'][0]['path']=str(source);metadata['sources'][0]['path']=str(source)
    from moviestar.project import save_project
    from moviestar.spatial_render import render_spatial_frame
    from moviestar.spatial_model import compile_spatial_frame
    from moviestar.resolved import resolve_project
    spec['spatial']['scenes']['interview']['layout']={'background_color':'#102030','children':[{
        'id':'camera','type':'video','content':{'asset_id':'camera','source':{'start':0,'end':.4}},
        'layout':{'position':'absolute','left':20,'top':20,'width':128,'height':96,'padding':4},
        'style':{'object_fit':'cover','border_width':8,'border_style':'solid','border_color':'#ffe000',
            'border_radius':20,'background_color':'#183848'},
        'media_motion':{'scale':[{'at':0,'value':1},{'at':.3,'value':1.5}],
            'translate_x':[{'at':0,'value':'0%'},{'at':.3,'value':'-25%'}],
            'translate_y':[{'at':0,'value':'0%'},{'at':.3,'value':'-25%'}]}}]}
    save_project(metadata,cwd=tmp_path);save_spec(spec,cwd=tmp_path);before=load_spec()
    result=_invoke(['export','--out','camera.mp4','--video-encoder','x264','--quiet'])
    assert result['status']=='exported' and result['output_fps']==10
    assert load_spec()==before
    for i in (0,2,3):
        reference=tmp_path/f'camera-{i}.png'
        frame=compile_spatial_frame(spec,resolve_project(metadata,spec),i/10,source_durations={'camera':2})
        measured=render_spatial_frame(spec,frame,{'width':320,'height':180},project_root=tmp_path,output_path=reference)
        assert measured['boxes'][0]['rect']=={'x':20,'y':20,'width':128,'height':96}
        actual,expected=_pixels(tmp_path/'camera.mp4',i),_pixels(reference)
        # Same lossy H.264 tolerance as the existing spatial-video export tests.
        for x,y in ((5,5),(24,60),(60,60),(100,84)):
            offset=(y*320+x)*3
            assert max(abs(a-b)for a,b in zip(actual[offset:offset+3],expected[offset:offset+3]))<=12
    assert _rms(tmp_path/'camera.mp4')==pytest.approx(_rms(source),rel=.12)
    import json
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(tmp_path/'camera.mp4')]))
    assert abs(float(info['format']['duration'])-.4)<=.1
    assert next(s for s in info['streams'] if s['codec_type']=='video')['width']==320


@pytest.mark.parametrize('pacing,at,source_time',[
    ({'id':'slow','mode':'speed','range':{'from':'0:00:00.000','to':'0:00:08.000','space':'source-local'},'speed':.5},4,2),
    ({'id':'pause','mode':'hold','at':'0:00:02.000','space':'source-local','duration':'0:00:02.000'},3,2)])
def test_camera_uses_result_time_while_source_slowed_or_held(pacing,at,source_time):
    spec=_project();spec['spatial']['definitions']['pair']['children'][0]['media_motion']={'scale':_track(1,2)}
    spec['motion']={'version':1,'scenes':[{'scene':'scene_a','scene_id':'scene_a',
        'slots':[{'slot':'main','pacing':[pacing],'camera':[]}]}]}
    frame=_frame(spec,at)
    assert frame['children'][0]['children'][0]['media']['source_time_s']==source_time
    motion=prepare_layout_frame(frame,{'width':640,'height':360})['nodes'][0]['children'][0]['media_motion']
    assert motion['scene_time_s']==at and motion['scale']==2
