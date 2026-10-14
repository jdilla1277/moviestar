from pathlib import Path
import json,subprocess,time
from moviestar.ffmpeg import render_layout_video
root=Path(__file__).resolve().parent;frames=root/'frames';key='civic-1280x720'
slots=json.loads((root/'hybrid_results.json').read_text())[key]['slots']
for s in slots:s['source_from']=0;s['source_to']=6
raw=root/'civic_video_layer.mp4';out=root/'civic_tag_motion.mp4'
t=time.perf_counter();render_layout_video(slots,str(raw),(1280,720),6,output_fps=30,progress_cb=lambda _:None)
first=time.perf_counter()-t
t=time.perf_counter();subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(raw),'-loop','1','-i',str(frames/f'{key}-static-overlay.png'),'-loop','1','-i',str(frames/f'{key}-tag-only.png'),'-filter_complex',"[0:v][1:v]overlay=0:0:format=auto[v1];[v1][2:v]overlay=x='12*t':y=0:format=auto[out]",'-map','[out]','-t','6','-r','30','-c:v','libx264','-pix_fmt','yuv420p',str(out)],check=True)
second=time.perf_counter()-t
print('video',round(first,3),'overlay_and_tag',round(second,3),'total',round(first+second,3))
for t,label in [(0,'0'),(3,'3'),(5.9,'5.9')]:
    png=frames/f'{key}-tag-motion-t{label}.png'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-ss',str(t),'-i',str(out),'-frames:v','1',str(png)],check=True)
