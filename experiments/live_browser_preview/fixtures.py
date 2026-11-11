"""Generate portable flash/beep + barcode media and a reusable spatial scene."""

from fractions import Fraction
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import av
import numpy as np

from moviestar.project import load_project
from moviestar.spec import empty_spec, save_spec


def generate(root, portrait=False, *, pattern=False):
    root.mkdir(parents=True, exist_ok=False)
    source = root / 'cues.mp4'
    expression = 'aevalsrc=if(between(mod(t\\,1)\\,0.2\\,0.2666667)\\,0.25*sin(2*PI*880*t)\\,0):s=48000:d=8'
    process = subprocess.Popen(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24',
        '-s','640x360','-r','30','-i','pipe:0','-f','lavfi','-i',expression,'-t','8',
        '-c:v','libx264','-preset','fast','-crf','15','-pix_fmt','yuv420p','-c:a','aac',
        '-movflags','+faststart',str(source)],stdin=subprocess.PIPE)
    try:
        frame = np.empty((360,640,3),dtype=np.uint8)
        for number in range(240):
            frame[:] = (255,255,255) if number % 30 in {6,7} else (16,32,64)
            if pattern and number % 30 not in {6,7}:
                # A crop change must alter visible pixels, not just metadata.
                frame[:, :, 0] = np.arange(640, dtype=np.uint32)*200//640
            for bit in range(8):
                frame[:16,bit*16:(bit+1)*16] = 255 if number & (1<<bit) else 0
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        if process.wait(timeout=30):
            raise RuntimeError('Could not generate native preview media')
    finally:
        if process.poll() is None:
            process.kill();process.wait()
    logo = np.zeros((96,96,4),dtype=np.uint8)
    yy,xx=np.ogrid[:96,:96]
    logo[(xx-48)**2+(yy-48)**2<44**2] = (255,184,28,255)
    png=av.VideoFrame.from_ndarray(logo,format='rgba')
    encoder=av.CodecContext.create('png','w');encoder.width=encoder.height=96
    encoder.pix_fmt='rgba';encoder.time_base=Fraction(1,25);encoder.thread_count=1
    (root/'logo.png').write_bytes(b''.join(bytes(packet) for packet in encoder.encode(png)))
    width,height=(720,1280) if portrait else (1920,1080)
    spec=empty_spec([{'id':'camera','path':str(source)}])
    spec['composition_canvas']={'preset':None,'width':width,'height':height,'aspect_ratio':'9:16' if portrait else '16:9'}
    spec['composition']=[{'id':'review','name':'Live review','layout':{'preset':'single'},
        'slots':[{'id':'slot_0001','slot':'main','source':'camera','source_from':'0:00:01.000','source_to':'0:00:06.000'}],
        'audio_from':None}]
    spec['spatial']={'assets':{'logo':{'type':'image','path':'logo.png'}},'definitions':{
        'speaker_card':{'name':'SpeakerCard','inputs':[{'name':'clip','type':'video','required':True}],
            'children':[{'id':'footage','type':'video','content':{'input':'clip'},
                'layout':{'width':'100%','height':'100%'},'style':{'object_fit':'cover','border_radius':26}}]},
        'subtitles':{'name':'Subtitles','inputs':[{'name':'track','type':'caption','required':True}],
            'children':[{'id':'words','type':'caption','content':{'input':'track'},
                'layout':{'width':'100%','height':'100%'},
                'style':{'font_family':'sans-serif','font_size':32 if portrait else 44,'line_height':1.2,'color':'#ffffff'}}]}},
        'scenes':{'review':{'layout':{'background_color':'#0b122b','children':[
            {'instance_id':'speaker','definition_id':'speaker_card',
                'bindings':{'clip':{'asset_id':'camera','source':{'start':1,'end':6}}},
                'layout':{'position':'absolute','left':50,'top':280 if portrait else 250,
                          'width':600 if portrait else 900,'height':400 if portrait else 506},
                'motion':{'translate_x':[{'at':0,'value':0},{'at':4,'value':40}],
                          'opacity':[{'at':0,'value':.8},{'at':2,'value':1}]}},
            {'id':'logo','type':'image','content':{'asset_id':'logo'},
                'layout':{'position':'absolute','left':50,'top':60,'width':64,'height':64},'style':{'object_fit':'contain'}},
            {'id':'title','type':'text','content':{'text':'Live edit A'},
                'layout':{'position':'absolute','left':50 if portrait else 1080,'top':150 if portrait else 290,
                          'width':620 if portrait else 760,'height':120 if portrait else 220},
                'style':{'font_family':'sans-serif','font_size':48 if portrait else 72,'line_height':1.1,'color':'#ffb81c'}},
            {'instance_id':'subtitles','definition_id':'subtitles','bindings':{'track':{'track':'captions'}},
                'layout':{'position':'absolute','left':50,'top':800 if portrait else 860,
                          'width':620 if portrait else 1600,'height':250 if portrait else 180}}
        ]}}}}
    spec['overlays']=[{'id':identity,'kind':'caption','track':'captions','from':start,'to':end,'text':text,
        'position':{'preset':'bottom'},'style':{'preset':'plain'}} for identity,start,end,text in [
        ('first','0:00:00.000','0:00:02.000','One line, on the scene clock.'),
        ('second','0:00:02.000','0:00:05.000','Two lines change together\nwith picture and sound.')]]
    # Ingest with the real CLI so schema drift cannot be hidden by hand-built
    # metadata. Generation/ingestion is setup, outside timed repeated edits.
    env = os.environ.copy()
    if env.get('PYTHONPATH'):
        env['PYTHONPATH'] = os.pathsep.join(str(Path(entry).resolve())
            for entry in env['PYTHONPATH'].split(os.pathsep) if entry)
    loaded = subprocess.run([sys.executable, '-m', 'moviestar.cli', 'load', str(source),
        '--as', 'camera', '--force', '--no-transcribe', '--no-frames'],
        cwd=root, env=env, capture_output=True, text=True, timeout=30)
    if loaded.returncode:
        raise RuntimeError('Could not load native preview fixture: '+loaded.stdout+loaded.stderr)
    project = load_project(root)
    save_spec(spec,cwd=root)
    return spec,project


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--portrait',action='store_true')
    args=parser.parse_args();generate(args.out.resolve(),args.portrait)
    print(json.dumps({'project':str(args.out.resolve())}))
