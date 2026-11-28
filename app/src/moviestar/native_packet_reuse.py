"""Bounded original-packet metadata owned by completed native requests."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sys
from time import perf_counter

CACHE_NAME = 'packet-indexes.json'
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_BYTES = 6 * MAX_INDEX_BYTES
MAX_HISTORY = 8


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def digest(value):return sha256(encoded(value)).hexdigest()


def stamp(path):
    path=Path(path);s=path.stat()
    return [str(path),str(path.resolve()),s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]


def runtime_key():
    from moviestar import native_decode
    try:
        probe=shutil.which('ffprobe')
        if probe is None:return None
        return digest([1,stamp(Path(probe)),stamp(Path(native_decode.__file__)),sys.version,
            {k:v for k,v in os.environ.items() if k.startswith(('LD_','DYLD_'))}])
    except (OSError,ValueError):return None


def candidates(paths,folder,needed):
    result={};remaining=MAX_BYTES;deadline=perf_counter()+.25
    if not isinstance(paths,list):return result
    for raw in paths[:MAX_HISTORY]:
        if perf_counter()>deadline or not needed-result.keys():break
        try:
            path=Path(raw)
            if (path.name!=CACHE_NAME or not re.fullmatch('[a-f0-9]{24}',path.parent.name)
                    or path.parent.parent.resolve()!=folder.parent.resolve()
                    or path.is_symlink() or path.parent.is_symlink()):continue
            if path.stat().st_size>remaining:continue
            with path.open('rb') as stream:data=stream.read(remaining+1)
            if len(data)>remaining:break
            remaining-=len(data);doc=json.loads(data)
            if doc.get('version')!=1 or not isinstance(doc.get('indexes'),dict) or len(doc['indexes'])>6:continue
            for key,record in doc['indexes'].items():
                if key not in needed or key in result:continue
                value=record['value'];body=encoded(value)
                if (len(body)<=MAX_INDEX_BYTES and sha256(body).hexdigest()==record['sha256']
                        and isinstance(value.get('config'),dict) and isinstance(value.get('packets'),list)
                        and 0<len(value['packets'])<=4096):result[key]=value
        except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):continue
    return result


def prepare_indexes(packets,assets,folder,paths):
    from moviestar.native_decode import packet_index
    from moviestar.native_preview import asset_identity
    runtime=runtime_key();rows=[]
    for child in packets:
        child['decoded_transport']=True
        for item in child['media']:
            if item['kind']!='video':continue
            asset=assets[item['asset']];path=asset['path']
            if asset_identity(path)!=tuple(asset['identity']):raise ValueError('Native source changed')
            identity=stamp(path)
            key=digest([runtime,identity,item['source_start'],item['source_end'],child['fps']])
            rows.append((item,asset,identity,key,child['fps']))
    if len(rows)>6:raise ValueError('Native packet preparation exceeds six pictures')
    known=candidates(paths,folder,{r[3] for r in rows}) if runtime is not None else {}
    current={};stats={'indexed':0,'reused':0}
    for item,asset,identity,key,fps in rows:
        index=current.get(key,known.get(key))
        if (index is not None and (index.get('start'),index.get('end'),index.get('fps'))
                !=(item['source_start'],item['source_end'],fps)):index=None
        if index is None:
            index=packet_index(asset['path'],item['source_start'],item['source_end'],fps);stats['indexed']+=1
        else:stats['reused']+=1
        if stamp(asset['path'])!=identity:raise ValueError('Native source changed')
        current[key]=index
        # The index is source metadata. The last authored image belongs to the
        # freshly compiled occurrence, never to a previous request or alias.
        item['video_decode']={**index,'last':item['source_last']}
    for _,asset,identity,_,_ in rows:
        if stamp(asset['path'])!=identity:raise ValueError('Native source changed')
    if runtime is not None and runtime_key()!=runtime:raise ValueError('Native packet indexer changed during preparation')
    if runtime is not None:
        records={key:{'value':value,'sha256':digest(value)} for key,value in current.items() if len(encoded(value))<=MAX_INDEX_BYTES}
        # Hash canonically, but retain descriptor field order on disk: the
        # browser's playback fingerprint uses JSON.stringify on these values.
        data=json.dumps({'version':1,'indexes':records},separators=(',',':'),allow_nan=False).encode()
        if len(data)<=MAX_BYTES:
            staged=folder/(CACHE_NAME+'.tmp')
            try:
                with staged.open('xb') as stream:stream.write(data)
                staged.replace(folder/CACHE_NAME)
            except OSError:
                try:staged.unlink(missing_ok=True)
                except OSError:pass  # Optional metadata cannot reject valid playback.
    return stats
