"""Generated dialogue audio independent of two native cameras and their cuts."""
import argparse
from pathlib import Path
import subprocess
import sys
from pip import prepare_pip
from caption_benchmark import command


def prepare_audio(root, *, cuts=True, with_track=True):
    prepare_pip(root,cuts=cuts)
    add_dialogue(root,with_track=with_track)


def add_dialogue(root, *, with_track=True):
    audio=root/'dialogue.wav'
    expression='aevalsrc=if(between(mod(t\\,1)\\,0.2\\,0.2666667)\\,0.25*sin(2*PI*1170*t)\\,0)|0.02*sin(2*PI*220*t):s=48000:d=7'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',expression,'-c:a','pcm_f32le',str(audio)],check=True,timeout=15)
    command(root,['audio','source','--mute'])
    if with_track:command(root,['audio','add',str(audio),'--as','dialogue','--kind','voiceover','--from','1','--to','6'])


def add_bed(root, *, with_track=True):
    """A distinguishable right-channel bed, independent of dialogue cue timing."""
    audio=root/'bed.wav'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        'aevalsrc=0|0.02*sin(2*PI*330*t):s=48000:d=6',
        '-c:a','pcm_f32le',str(audio)],check=True,timeout=15)
    if with_track:
        command(root,['audio','add',str(audio),'--as','bed','--kind','music','--at','.5',
            '--from','.25','--to','4.25','--gain-db','-12','--fade-in','.2','--fade-out','.2'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--add-bed',action='store_true');parser.add_argument('--single',action='store_true');parser.add_argument('--existing',action='store_true');args=parser.parse_args()
    if args.add_bed:add_bed(args.out.resolve())
    elif args.existing:add_dialogue(args.out.resolve())
    else:prepare_audio(args.out.resolve(),cuts=not args.single)
