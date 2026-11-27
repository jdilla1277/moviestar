"""Packet metadata keeps byte ranges and source clocks, never media payloads."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from moviestar.native_decode import packet_index

@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='FFmpeg required')
def test_real_avc_index_retains_original_packet_bytes_and_pts(tmp_path):
    source=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=64x48:r=30:d=2','-c:v','libx264','-threads','1','-g','30','-bf','2',str(source)],check=True)
    before=source.read_bytes();index=packet_index(source,.5,1.5,30)
    assert index['config']['codec'].startswith('avc1.')
    assert index['config']['colorSpace']['matrix']=='smpte170m', 'Unspecified source matrix must match canonical FFmpeg BT.601 conversion'
    assert index['packets'][0]['key'] and index['packets'][0]['timestamp']<=500000
    assert any(p['timestamp']==500000 for p in index['packets'])
    assert all(0<=p['offset']<p['offset']+p['size']<=len(before) for p in index['packets'])
    assert all('data' not in p for p in index['packets'])
    assert source.read_bytes()==before

@pytest.mark.parametrize('mutation', ['offset','size','duration','key','codec'])
def test_invalid_packet_descriptors_fail_closed(tmp_path,monkeypatch,mutation):
    source=tmp_path/'source.mp4';source.write_bytes(b'x'*100)
    packet={'pos':'5','size':'5','pts_time':'0','duration_time':'.033333','flags':'K__'}
    stream={'codec_name':'h264','width':64,'height':48,'extradata':'\n00000000: 0164 001e ffe1 01  ....\n'}
    if mutation=='offset':packet['pos']='-1'
    elif mutation=='size':packet['size']='100'
    elif mutation=='duration':packet['duration_time']='0'
    elif mutation=='key':packet['flags']='___'
    else:stream['codec_name']='hevc'
    monkeypatch.setattr('moviestar.native_decode.subprocess.run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps({'streams':[stream],'packets':[packet]})))
    with pytest.raises(ValueError):packet_index(source,0,.5,30)


def test_tagged_matrix_and_full_range_are_preserved(tmp_path,monkeypatch):
    source=tmp_path/'source.mp4';source.write_bytes(b'x'*100)
    stream={'codec_name':'h264','width':64,'height':48,'extradata':'\n00000000: 0164 001e ffe1 01  ....\n','color_space':'bt709','color_range':'pc'}
    packet={'pos':'5','size':'5','pts_time':'0','duration_time':'.033333','flags':'K__'}
    monkeypatch.setattr('moviestar.native_decode.subprocess.run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps({'streams':[stream],'packets':[packet]})))
    assert packet_index(source,0,.5,30)['config']['colorSpace']=={'matrix':'bt709','fullRange':True}

@pytest.mark.parametrize('extra', [
    {'sample_aspect_ratio':'2:1'},
    {'tags':{'rotate':'90'}},
    {'side_data_list':[{'side_data_type':'Display Matrix','rotation':90}]},
    {'pix_fmt':'yuv420p10le'},
])
def test_sources_requiring_canonical_ffmpeg_transform_use_rendered_mode(tmp_path,monkeypatch,extra):
    source=tmp_path/'source.mp4';source.write_bytes(b'x'*100)
    stream={'codec_name':'h264','width':64,'height':48,'extradata':'\n00000000: 0164 001e ffe1 01  ....\n',**extra}
    packet={'pos':'5','size':'5','pts_time':'0','duration_time':'.033333','flags':'K__'}
    monkeypatch.setattr('moviestar.native_decode.subprocess.run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps({'streams':[stream],'packets':[packet]})))
    with pytest.raises(ValueError,match='rendered'):packet_index(source,0,.5,30)


def test_index_timeout_is_an_explicit_preview_failure(tmp_path,monkeypatch):
    source=tmp_path/'source.mp4';source.write_bytes(b'x'*100)
    def timeout(*a,**kw):raise subprocess.TimeoutExpired('ffprobe',5)
    monkeypatch.setattr('moviestar.native_decode.subprocess.run',timeout)
    with pytest.raises(ValueError,match='timed out'):packet_index(source,0,.5,30)
