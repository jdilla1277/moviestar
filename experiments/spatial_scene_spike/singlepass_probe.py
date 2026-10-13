from pathlib import Path
import json,subprocess,time,sys
from moviestar.ffmpeg import build_render_layout_video_command
root=Path(__file__).resolve().parent;frames=root/'frames';key='podcast-1280x720'
slots=json.loads((root/'hybrid_results.json').read_text())[key]['slots']
for s in slots:s['source_from']=0;s['source_to']=6
for s in slots[2:]:s['region']['y']-=18
gop='--gop' in sys.argv
out=root/('singlepass_gop.mp4' if gop else 'singlepass_all_intra.mp4')
cmd=build_render_layout_video_command(slots,str(out),(1280,720),6,output_fps=30)
idx=cmd.index('-filter_complex');overlay_input=cmd[:idx].count('-i');cmd[idx:idx]=['-loop','1','-framerate','30','-i',str(frames/f'{key}-hybrid.png')]
gidx=cmd.index('-filter_complex')+1;graph=cmd[gidx].replace(':shortest=1[',':shortest=1:format=rgb[')
for slot,label in [(slots[2],'lay2'),(slots[3],'outv')]:
 x=slot['region']['x'];y=slot['region']['y'];old=f'overlay=x={x}:y={y}:shortest=1:format=rgb[{label}]'
 new=f"overlay=x={x}:y='{y}+18*sin(PI*t/6)':shortest=1:format=rgb[{label}]"
 assert graph.count(old)==1,old
 graph=graph.replace(old,new)
assert graph.endswith('[outv]')
graph=graph[:-6]+'[vbase]'+f';[vbase][{overlay_input}:v]overlay=0:0:format=rgb:shortest=1[outv]'
cmd[gidx]=graph
if not gop:cmd[-1:-1]=['-g','1','-bf','0']
t=time.perf_counter();process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
peak_kib=0
while process.poll() is None:
 try:
  peak_kib=max(peak_kib,int(subprocess.check_output(['ps','-o','rss=','-p',str(process.pid)],text=True).strip()))
 except (subprocess.CalledProcessError,ValueError):pass
 time.sleep(.02)
stdout,stderr=process.communicate()
if process.returncode:raise RuntimeError(stderr)
print(json.dumps({'seconds':round(time.perf_counter()-t,3),'bytes':out.stat().st_size,'video_inputs':overlay_input,'peak_ffmpeg_rss_mib':round(peak_kib/1024)}))
subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-ss','3','-i',str(out),'-frames:v','1',str(frames/f'{key}-{ "gop" if gop else "singlepass" }-t3.png')],check=True)
