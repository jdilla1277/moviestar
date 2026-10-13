from pathlib import Path
import json, subprocess, statistics
root=Path(__file__).resolve().parent;frames=root/'frames'
records=json.loads((root/'browser_results.json').read_text())['scenes']
media={'podcast':{'host-video','alice-video','bob-video'},'civic':{'civic-video'},'explainer':{'speaker-video'},'static':set()}
def rgb(path):
    return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
for key,rec in records.items():
    if '-' not in key:continue
    scene,dims=key.split('-',1);w,h=map(int,dims.split('x'))
    a=rgb(frames/f'{key}-rendered.png');b=rgb(frames/f'{key}-hybrid-final.png')
    rects=[x for x in rec['boxes'] if x['id'] in media[scene]]
    diffs=[];bad=0;total=0
    for y in range(h):
        for x in range(w):
            if any(r['x']-1<=x<r['x']+r['width']+1 and r['y']-1<=y<r['y']+r['height']+1 for r in rects):continue
            k=(y*w+x)*3;d=max(abs(a[k+j]-b[k+j]) for j in range(3));diffs.append(d);total+=1;bad+=d>8
    diffs.sort();p95=diffs[int(.95*(len(diffs)-1))];maxd=diffs[-1]
    print(key,'outside_video_p95',p95,'max',maxd,'pixels_gt8',bad,'/',total,'pct',round(100*bad/total,4))
