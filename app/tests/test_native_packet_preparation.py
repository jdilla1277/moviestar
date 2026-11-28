"""Original-packet preparation has explicit, input-free timing evidence."""
import json
from moviestar.preview_timing import PreparationTiming
from moviestar.native_preview import _build, asset_identity


def test_three_picture_packet_indexing_has_an_explicit_timing_phase(tmp_path, monkeypatch):
    source=tmp_path/'source.mp4';source.write_bytes(b'packet index fixture')
    assets={'video':{'path':source,'identity':asset_identity(source),'kind':'video'}}
    packet={'fps':30,'media':[{'kind':'video','asset':'video','source_start':0,'source_end':3,'source_last':2.966667} for _ in range(3)]}
    monkeypatch.setattr('moviestar.native_preview.compile_snapshot',lambda request:(packet,assets))
    monkeypatch.setattr('moviestar.native_decode.packet_index',lambda *args:{'start':0,'end':3,'fps':30,'config':{},'packets':[{}]})
    timing=PreparationTiming()
    _build(tmp_path/'request.json',{'request_id':'request','requested_at':0,'input_identity':'fixture'},timing)
    report=timing.public()
    assert [p['name'] for p in report['phases']]==['descriptor_compile','packet_index','descriptor_publish']
    assert str(tmp_path) not in json.dumps(report)
