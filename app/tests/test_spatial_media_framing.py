"""Media content boxes and CSS crop anchors agree across renderers."""
import subprocess
import pytest
from moviestar.spatial_layout import browser_available
from moviestar.spatial_render import render_spatial_frame
from tests.test_spatial_render import project, _frame, _rgb
from tests.test_native_pip import pip_project
from tests.test_live_browser_preview import live_project
from moviestar.spec import save_spec
from moviestar.native_preview import compile_snapshot
from moviestar.local_preview import read_snapshot


@pytest.mark.skipif(not browser_available(),reason='Chrome and Node required')
@pytest.mark.parametrize('position,expected,color',[
    ('left top',{'x':20,'y':20,'width':178,'height':100},(255,0,0)),
    ('right bottom',{'x':-58,'y':20,'width':178,'height':100},(0,0,255)),
    ('right 10px bottom 5px',{'x':-68,'y':15,'width':178,'height':100},(0,0,255)),
    ('calc(100% - 10px) 50%',{'x':-68,'y':20,'width':178,'height':100},(0,0,255)),
])
def test_css_position_drives_video_plane_and_browser_paint(project,tmp_path,position,expected,color):
    spec,metadata=project
    video=tmp_path/'tones.mkv'
    # RGB pixels avoid YUV codec tolerances in the framing oracle.
    row=bytes((255,0,0))*80+bytes((0,0,255))*80
    subprocess.run(['ffmpeg','-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size','160x90',
        '-framerate','10','-i','pipe:0','-c:v','ffv1','-pix_fmt','bgr0',str(video)],input=row*90*20,check=True)
    spec['sources'][0]['path']=str(video)
    spec['spatial']['scenes']['interview']['layout']={'background_color':'#00ff00','children':[{
        'id':'footage','type':'video','content':{'asset_id':'camera','source':{'start':0,'end':1.5}},
        'layout':{'position':'absolute','left':20,'top':20,'width':100,'height':100},
        'style':{'object_fit':'cover','object_position':position}}]}
    output=tmp_path/'frame.png';reference=tmp_path/'reference.png'
    result=render_spatial_frame(spec,_frame(spec,metadata),{'width':320,'height':180},
        output_path=output,project_root=tmp_path,reference_path=reference)
    assert result['media'][0]['rect']==expected
    for path in (output,reference):
        rgb=_rgb(path);actual=tuple(rgb[(60*320+70)*3:(60*320+70)*3+3])
        assert max(abs(a-b) for a,b in zip(actual,color))<=1


def test_native_accepts_authored_media_content_box_and_crop(pip_project):
    spec,_,root=pip_project
    video=spec['spatial']['definitions']['card']['children'][0]
    video['layout']['padding']='4px 8px'
    video['style'].update(object_position='right 10px bottom 5px',border_width=3,border_style='solid',border_color='#ffee00')
    save_spec(spec,cwd=root)
    packet,_=compile_snapshot(read_snapshot(root,0,5))
    assert packet['media'][0]['object_position']=='right 10px bottom 5px'
    assert packet['initial']['nodes'][0]['children'][0]['style']['padding']=='4px 8px'


@pytest.mark.skipif(not browser_available(),reason='Chrome and Node required')
@pytest.mark.parametrize('position,expected',[('left center',(255,255,0)),('right center',(24,40,56))])
def test_alpha_image_crop_and_decorated_content_box(project,tmp_path,position,expected):
    from moviestar.spatial_layout import measure_spatial_layout
    spec,metadata=project
    spec['spatial']['scenes']['interview']['layout']={'background_color':'#00ff00','children':[{
        'id':'picture','type':'image','content':{'asset_id':'logo'},
        'layout':{'position':'absolute','left':20,'top':20,'width':100,'height':60,'padding':4},
        'style':{'object_fit':'contain','object_position':position,'border_width':2,'border_style':'solid',
            'border_color':'#ffffff','background_color':'#182838'}}]}
    frame=_frame(spec,metadata);canvas={'width':320,'height':180};output=tmp_path/'image.png'
    result=render_spatial_frame(spec,frame,canvas,output_path=output,project_root=tmp_path)
    measured=measure_spatial_layout(frame,canvas)
    assert result['boxes']==measured['boxes']
    assert measured['boxes'][0]['media_box']['content_rect']=={'x':26,'y':26,'width':88,'height':48}
    rgb=_rgb(output);assert tuple(rgb[(50*320+60)*3:(50*320+60)*3+3])==expected
