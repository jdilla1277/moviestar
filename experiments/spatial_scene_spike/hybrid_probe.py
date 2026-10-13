from pathlib import Path
import json, subprocess, time
from moviestar.ffmpeg import build_render_layout_frame_command

root = Path(__file__).resolve().parent
frames = root/'frames'
results = json.loads((root/'browser_results.json').read_text())
bg = root/'background.mp4'
if not bg.exists():
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=0x101b2b:s=1280x720:r=30:d=6','-c:v','libx264','-pix_fmt','yuv420p',str(bg)],check=True)
media_nodes = {'podcast':[('host-video','host',16),('alice-video','alice',14),('bob-video','bob',14)],'civic':[('civic-video','civic',18)],'explainer':[('speaker-video','host',0)],'static':[]}
records = {}
for key,rec in results['scenes'].items():
    if '-' not in key:
        continue
    scene, dimensions = key.split('-',1)
    w,h = map(int,dimensions.split('x'))
    boxes={x['id']:x for x in rec['boxes']}
    slots=[{'path':str(bg),'source_from':0,'source_to':0.1,'region':{'x':0,'y':0,'width':w,'height':h},'framing':{'mode':'fill','anchor':'center'}}]
    for node,name,radius in media_nodes[scene]:
        b=boxes[node];reg={k:round(b[k]) for k in ('x','y','width','height')}
        slot={'path':str(root/f'{name}.mp4'),'source_from':3,'source_to':3.1,'region':reg,'framing':{'mode':'fill','anchor':'center'}}
        if radius:
            slot['shape']={'kind':'rounded','content_mask':str(root/'masks'/f'mask_{reg["width"]}x{reg["height"]}_r{radius}.png')}
        slots.append(slot)
    layer=frames/f'{key}-video-layer.png'
    cmd=build_render_layout_frame_command(slots,str(layer),(w,h))
    graph_index=cmd.index('-filter_complex')+1
    cmd[graph_index]=cmd[graph_index].replace(':shortest=1[',':shortest=1:format=rgb[')
    t=time.perf_counter();subprocess.run(cmd,check=True,capture_output=True);video_ms=(time.perf_counter()-t)*1000
    final=frames/f'{key}-hybrid-final.png'
    t=time.perf_counter()
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(layer),'-i',str(frames/f'{key}-hybrid.png'),'-filter_complex','[0:v][1:v]overlay=0:0:format=auto[out]','-map','[out]','-frames:v','1',str(final)],check=True)
    overlay_ms=(time.perf_counter()-t)*1000
    records[key]={'video_layer_ms':video_ms,'overlay_ms':overlay_ms,'slots':slots}
    print(key,round(video_ms),round(overlay_ms))
(root/'hybrid_results.json').write_text(json.dumps(records,indent=2))
