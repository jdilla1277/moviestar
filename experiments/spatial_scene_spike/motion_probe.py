from pathlib import Path
import json, re, subprocess, time
from moviestar.ffmpeg import build_render_layout_video_command
root=Path(__file__).resolve().parent;frames=root/'frames';key='podcast-1280x720'
slots=json.loads((root/'hybrid_results.json').read_text())[key]['slots']
for s in slots:s['source_from']=0;s['source_to']=6
for s in slots[2:]:s['region']['y']-=18
out=root/'motion_video.mp4'
cmd=build_render_layout_video_command(slots,str(out),(1280,720),6,output_fps=30)
graph=cmd[cmd.index('-filter_complex')+1]
for slot,label in [(slots[2],'lay2'),(slots[3],'outv')]:
    x=slot['region']['x'];y=slot['region']['y']
    old=f'overlay=x={x}:y={y}:shortest=1[{label}]'
    new=f"overlay=x={x}:y='{y}+18*sin(PI*t/6)':shortest=1[{label}]"
    assert graph.count(old)==1,old
    graph=graph.replace(old,new)
cmd[cmd.index('-filter_complex')+1]=graph
start=time.perf_counter();subprocess.run(cmd,check=True,capture_output=True)
print('motion_video_s',round(time.perf_counter()-start,3))
for t,label in [(0,'0'),(3,'3'),(5.9,'5.9')]:
    final=frames/f'{key}-motion-t{label}.png'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-ss',str(t),'-i',str(out),'-i',str(frames/f'{key}-hybrid.png'),'-filter_complex','[0:v][1:v]overlay=0:0:format=auto[out]','-map','[out]','-frames:v','1',str(final)],check=True)
    print(t,final)
