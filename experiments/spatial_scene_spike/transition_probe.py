from pathlib import Path
import subprocess,time,json
root=Path(__file__).resolve().parent;frames=root/'frames';key='podcast-1280x720'
def run(args):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*args],check=True)
podcast=root/'podcast_motion_final.mp4'
run(['-i',str(root/'motion_video.mp4'),'-loop','1','-i',str(frames/f'{key}-hybrid.png'),'-filter_complex','[0:v][1:v]overlay=0:0:format=auto:shortest=1[out]','-map','[out]','-t','6','-r','30','-c:v','libx264','-pix_fmt','yuv420p',str(podcast)])
legacy=root/'legacy_full.mp4'
run(['-i',str(root/'host.mp4'),'-vf','scale=1280:720','-r','30','-c:v','libx264','-pix_fmt','yuv420p',str(legacy)])
for name,a,b in [('transition_structured',podcast,root/'civic_tag_motion.mp4'),('transition_legacy',root/'civic_tag_motion.mp4',legacy)]:
    out=root/f'{name}.mp4';start=time.perf_counter()
    run(['-i',str(a),'-i',str(b),'-filter_complex','[0:v]settb=AVTB,setpts=PTS-STARTPTS[a];[1:v]settb=AVTB,setpts=PTS-STARTPTS[b];[a][b]xfade=transition=fade:duration=0.5:offset=5.5[out]','-map','[out]','-r','30','-c:v','libx264','-pix_fmt','yuv420p',str(out)])
    elapsed=time.perf_counter()-start
    png=frames/f'{name}-ffmpeg.png';run(['-ss','5.75','-i',str(out),'-frames:v','1',str(png)])
    print(name,'encode_seconds',round(elapsed,3),'bytes',out.stat().st_size)
