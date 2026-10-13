from pathlib import Path
import json,subprocess,time
from moviestar.ffmpeg import build_render_layout_video_command
root=Path(__file__).resolve().parent;frames=root/'frames';key='podcast-1280x720'
slots=json.loads((root/'hybrid_results.json').read_text())[key]['slots']
for s in slots:s['source_from']=3;s['source_to']=3+1/30
for s in slots[2:]:s['region']['y']-=18
out=root/'singleframe_preview.mp4'
cmd=build_render_layout_video_command(slots,str(out),(1280,720),1/30,output_fps=30)
idx=cmd.index('-filter_complex');overlay_input=cmd[:idx].count('-i');cmd[idx:idx]=['-loop','1','-framerate','30','-i',str(frames/f'{key}-hybrid.png')]
gidx=cmd.index('-filter_complex')+1;graph=cmd[gidx].replace(':shortest=1[',':shortest=1:format=rgb[')
for slot,label in [(slots[2],'lay2'),(slots[3],'outv')]:
 x=slot['region']['x'];y=slot['region']['y'];old=f'overlay=x={x}:y={y}:shortest=1:format=rgb[{label}]'
 new=f"overlay=x={x}:y='{y}+18*sin(PI*(t+3)/6)':shortest=1:format=rgb[{label}]"
 assert graph.count(old)==1,old
 graph=graph.replace(old,new)
assert graph.endswith('[outv]')
cmd[gidx]=graph[:-6]+'[vbase]'+f';[vbase][{overlay_input}:v]overlay=0:0:format=rgb:shortest=1[outv]'
cmd[-1:-1]=['-g','1','-bf','0','-frames:v','1']
t=time.perf_counter();result=subprocess.run(cmd,capture_output=True,text=True)
if result.returncode:raise RuntimeError(result.stderr)
print(json.dumps({'preview_render_ms':round((time.perf_counter()-t)*1000),'bytes':out.stat().st_size}))
subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(out),'-frames:v','1',str(frames/f'{key}-preview-decoded.png')],check=True)
