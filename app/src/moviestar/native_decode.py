"""Bounded original H.264 packet descriptors for explicit browser decoding."""
import json
import subprocess
from pathlib import Path


def packet_index(path, start, end, fps):
    if not (0 <= start < end and end-start <= 10 and 24 <= fps <= 60):
        raise ValueError('Unsupported native decoded window')
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-read_intervals', f'{start}%{end+1}', '-show_packets', '-show_streams',
            '-show_data', '-show_entries',
            'packet=pos,size,pts_time,duration_time,flags:stream=codec_name,width,height,extradata,color_space,color_range,pix_fmt,sample_aspect_ratio:stream_tags=rotate:stream_side_data=side_data_type,rotation',
            '-of', 'json', str(path)], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Native packet indexing timed out; use --mode rendered') from exc
    if result.returncode or len(result.stdout) > 2*1024*1024:
        raise ValueError('Native packet indexing failed or exceeded its bound')
    doc = json.loads(result.stdout)
    stream = doc['streams'][0]
    if stream['codec_name'] != 'h264':
        raise ValueError('Native decoded pictures require H.264')
    # The canonical renderer deliberately keeps these on FFmpeg's transform
    # path. Do not silently change orientation, anamorphic scale or quantization.
    formats={None,'yuv420p','yuvj420p','yuv422p','yuvj422p','yuv444p','yuvj444p','gbrp'}
    if (stream.get('sample_aspect_ratio') not in (None,'N/A','0:1','1:1')
            or stream.get('pix_fmt') not in formats
            or stream.get('tags',{}).get('rotate') not in (None,'0')
            or any(s.get('side_data_type')=='Display Matrix' for s in stream.get('side_data_list',[]))):
        raise ValueError('Source display transforms or high bit depth require --mode rendered')
    extra = bytes.fromhex(''.join(line.split(':', 1)[1].split('  ')[0].replace(' ', '')
        for line in stream['extradata'].strip().splitlines()))
    width, height = stream['width'], stream['height']
    if not (extra and extra[0] == 1 and len(extra) >= 7 and 0 < width <= 4096 and 0 < height <= 4096 and width*height <= 4096*2160):
        raise ValueError('Unsupported native AVC configuration or picture size')
    matrices={'bt709':'bt709','smpte170m':'smpte170m','bt470bg':'bt470bg',
              'bt2020nc':'bt2020-ncl','gbr':'rgb',None:'smpte170m','unknown':'smpte170m'}
    if stream.get('color_space') not in matrices:
        raise ValueError('Unsupported native source color matrix')
    color={'matrix':matrices[stream.get('color_space')]}
    if stream.get('color_range') in ('pc','tv'):
        color['fullRange']=stream['color_range']=='pc'
    packets = []
    length = Path(path).stat().st_size
    for item in doc['packets']:
        row = {'timestamp': round(float(item['pts_time'])*1e6),
               'duration': round(float(item.get('duration_time', 1/fps))*1e6),
               'offset': int(item['pos']), 'size': int(item['size']), 'key': 'K' in item['flags']}
        if row['offset'] < 0 or not 0 < row['size'] <= 32*1024*1024 or row['offset']+row['size'] > length or row['duration'] <= 0:
            raise ValueError('Invalid native packet range or duration')
        packets.append(row)
    if not packets or len(packets) > 4096 or not packets[0]['key'] or packets[0]['timestamp'] > start*1e6+2:
        raise ValueError('Native source has no bounded preceding keyframe')
    return {'start': start, 'end': end, 'fps': fps,
            'config': {'codec': 'avc1.'+extra[1:4].hex(), 'codedWidth': width,
                       'codedHeight': height, 'description': list(extra), 'colorSpace':color}, 'packets': packets}
