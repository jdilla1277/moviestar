from pathlib import Path
import subprocess,json
root=Path(__file__).resolve().parent;frames=root/'frames'
font=root.parents[1]/'app/src/moviestar/fonts/Inter-Bold.ttf'
textfile=root/'title.txt';textfile.write_text('Three voices, one conversation')
def rgb(path):return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
ref=rgb(frames/'podcast-1280x720-rendered.png');w=1280
def mask(raw):
    pts=set()
    for y in range(25,100):
        for x in range(25,750):
            k=(y*w+x)*3;r,g,b=raw[k:k+3]
            if min(r,g,b)>200 and max(r,g,b)-min(r,g,b)<25:pts.add((x,y))
    return pts
refmask=mask(ref);best=None
for y in range(28,53):
    out=frames/'ffmpeg-text.png'
    graph=f'drawtext=fontfile={font}:textfile={textfile}:fontsize=42:fontcolor=white:x=34:y={y}'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=0x101b2b:s=1280x720:d=0.1','-vf',graph,'-frames:v','1',str(out)],check=True)
    got=mask(rgb(out));intersection=len(refmask&got);union=len(refmask|got);score=intersection/union
    if best is None or score>best[0]:best=(score,y,len(refmask),len(got),len(refmask-got),len(got-refmask))
print(json.dumps({'best_white_glyph_iou':round(best[0],4),'best_drawtext_y':best[1],'browser_white_pixels':best[2],'ffmpeg_white_pixels':best[3],'browser_only_pixels':best[4],'ffmpeg_only_pixels':best[5]}))
