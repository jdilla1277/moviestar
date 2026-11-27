"""Actual decoded samples, existing local plan and reusable tile builders."""
import hashlib
import subprocess
import pytest


def test_saved_plan_matches_local_auto_sampling():
    from moviestar.rendered_storyboard import plan_storyboard
    from moviestar.cli import _storyboard_plan
    for duration in [100,501,3000,5177,30000]:
        plan=plan_storyboard(duration)
        local=_storyboard_plan(duration/1000,'result duration',None,None,None,None)
        assert plan['requested_times_ms']==[round(t*1000)for t in local['frame_times']]
        assert (plan['columns'],plan['rows'])==(local['columns'],local['rows'])

@pytest.mark.parametrize('vfr,offset,sar,limited,rate,duration',[(False,0,'1/1',False,'30',3000),(True,0,'1/1',False,'30',2300),(True,5,'2/1',False,'30',2300),(False,0,'1/1',True,'30',3000),(False,0,'1/1',False,'30000/1001',3000),(False,0,'1/1',False,'30',1700)])
def test_real_sheet_preserves_observed_pts_and_image_identity(tmp_path,vfr,offset,sar,limited,rate,duration):
    from moviestar.rendered_storyboard import plan_storyboard,render_storyboard
    import json
    video=tmp_path/'input.mp4';source=f'nullsrc=s=64x64:r={rate}:d=3,geq=lum=16+N:cb=128:cr=128'
    if vfr:source+=",select='eq(n,0)+eq(n,3)+eq(n,12)+eq(n,42)+eq(n,57)+eq(n,69)'"
    source+=f',setsar={sar}'
    if offset:source+=f',setpts=PTS+{offset}/TB'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',source,'-fps_mode','vfr',*(['-t',str(duration/1000)]if not offset and not vfr else []),'-c:v','libx264','-crf','0',*(['-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709']if limited else []),str(video)],check=True,capture_output=True)
    plan=plan_storyboard(duration);out=tmp_path/'sheet.jpg'
    result=render_storyboard(str(video),str(out),plan)
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_frames','-show_format','-select_streams','v:0','-show_entries','frame=best_effort_timestamp_time:format=start_time','-of','json',str(video)]))
    times=[float(f['best_effort_timestamp_time'])-float(data['format']['start_time'])for f in data['frames']]
    assert len(result['frames'])==plan['frame_count']
    for f,requested in zip(result['frames'],plan['requested_times_ms']):
        expected=round(max(t for t in times if t<=requested/1000+1e-6)*1000)
        assert f['requested_at_ms']==requested and f['at_ms']==expected
        assert f['timecode'].endswith(f'{expected%1000:03d}')
    # Independent decoded-pixel oracle, excluding the lower label strip.
    original=subprocess.check_output(['ffmpeg','-v','error','-i',str(video),'-map','0:v:0','-fps_mode','passthrough','-pix_fmt','gray','-f','rawvideo','-'])
    sheet=subprocess.check_output(['ffmpeg','-v','error','-i',str(out),'-pix_fmt','gray','-f','rawvideo','-'])
    width=result['image']['width'];tile_height=result['image']['height']//plan['rows']
    for i,f in enumerate(result['frames']):
        selected=max(k for k,t in enumerate(times)if t<=f['requested_at_ms']/1000+1e-6)
        truth=original[selected*4096:(selected+1)*4096]
        x=(i%plan['columns'])*384+192;y=(i//plan['columns'])*tile_height+tile_height//3
        assert abs(sheet[y*width+x]-sum(truth)/len(truth))<=3
    assert result['labels']=='decoded_result_time'
    assert result['image']['sha256']==hashlib.sha256(out.read_bytes()).hexdigest()
    assert result['image']['size_bytes']==out.stat().st_size
    before=out.read_bytes()
    with pytest.raises(FileExistsError):render_storyboard(str(video),str(out),plan)
    assert out.read_bytes()==before


@pytest.mark.parametrize('fault',['unreadable_label_space','missing_font'])
def test_never_publishes_a_sheet_without_legible_required_labels(tmp_path,monkeypatch,fault):
    from moviestar.rendered_storyboard import render_storyboard,plan_storyboard
    from moviestar import cli as local
    video=tmp_path/'input.mp4';output=tmp_path/'never.jpg'
    size='640x2'if fault=='unreadable_label_space'else'64x64'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=red:s={size}:d=1','-c:v','libx264',str(video)],check=True,capture_output=True)
    if fault=='missing_font':monkeypatch.setattr(local,'_storyboard_font',lambda _: (None,False,None))
    with pytest.raises(ValueError,match='resource_limit'if fault=='unreadable_label_space'else'labels_unavailable'):
        render_storyboard(str(video),str(output),plan_storyboard(1000))
    assert not output.exists()
