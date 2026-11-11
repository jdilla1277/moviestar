"""Measure real CLI edits and native A/B/A cuts with fixed fidelity budgets."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from benchmark import driver_hashes, run_project_case
from cuts import prepare_cuts
from real_benchmark import srt_cues
from moviestar.spec import save_spec


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--profile',choices=['reference','long-source'],default='reference')
    parser.add_argument('--source',type=Path,action='append',help='two optional private recordings; outputs must stay private')
    parser.add_argument('--offsets',type=float,nargs=3,default=[1,0,4],help='source offsets for A, B and return to A')
    parser.add_argument('--srt',type=Path,help='optional private source-clock captions')
    parser.add_argument('--orientation',choices=['both','landscape','portrait'],default='both')
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    chrome=os.environ.get('MOVIESTAR_CHROME_PATH')
    if not chrome:parser.error('set MOVIESTAR_CHROME_PATH')
    if args.source and len(args.source)!=2:parser.error('supply exactly two --source recordings')
    if os.environ.get('PYTHONPATH'):
        os.environ['PYTHONPATH']=os.pathsep.join(str(Path(p).resolve()) for p in os.environ['PYTHONPATH'].split(os.pathsep) if p)
    report={'profile':'private-real-cuts' if args.source else args.profile,'driver_files_sha256':driver_hashes(),'versions':{
        'python':sys.version.split()[0],'node':subprocess.check_output(['node','--version'],text=True).strip(),
        'chrome':subprocess.check_output([chrome,'--version'],text=True).strip(),'platform':platform.platform()},
        'cases':[],'resources':[],'problems':[]}
    sources=tuple(p.resolve(strict=True) for p in args.source) if args.source else None
    positions=[('reference',tuple(args.offsets))]
    if not sources and args.profile=='long-source':
        from scaling import long_source
        a=long_source(out);prepare_cuts(out/'short-seed')
        sources=(a,out/'short-seed/source-b.mp4')
        positions=[('beginning',(1,0,4)),('middle',(2705,0,2709)),('end',(5385,0,5389))]
    for orientation,portrait in [('landscape',False),('portrait',True)]:
        if args.orientation not in ['both',orientation]:continue
        for position,offsets in positions:
            folder=out/(orientation+'-'+position);folder.mkdir();root=folder/'project'
            spec,project,review=prepare_cuts(root,portrait,sources,offsets,long_project=sources is not None)
            if args.srt:
                spec['overlays']=[]
                for i,offset in enumerate(offsets):
                    for cue in srt_cues(args.srt,offset,offset+3,result_start=review['from']+i*3):
                        cue['id']='segment_'+str(i)+'_'+cue['id'];spec['overlays'].append(cue)
                save_spec(spec,cwd=root)
            fields={'require_source_buffering':True,'continuous_pilot':review['continuous_pilot'],
                'source_position':position,'media_kind':'real' if args.source else 'synthetic'}
            times=[0,2,3-1/project['sources'][0]['fps'],3,3+1/project['sources'][0]['fps'],
                6-1/project['sources'][0]['fps'],6,6+1/project['sources'][0]['fps'],8]
            result,resources=run_project_case(root,folder,review['from'],review['to'],chrome,fields,sample_times=times)
            result['canvas']=orientation+'-'+position;report['cases'].append(result)
            report['resources'].append({'case':result['canvas'],**resources})
            report['problems'].extend(result['canvas']+': '+p for p in result['problems'])
            (out/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'report':str(out/'report.json'),'cases':[{'case':c['canvas'],**c.get('summary',{})} for c in report['cases']], 'problems':report['problems']}))
    return bool(report['problems'])


if __name__=='__main__':raise SystemExit(main())
