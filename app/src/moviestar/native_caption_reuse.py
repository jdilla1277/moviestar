"""Disposable caption candidates from completed requests; copies keep ownership local."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from time import perf_counter

MAX_JOBS = 8
MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 8192
LOOKUP_SECONDS = .5


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def file_stamp(path):
    path=Path(path);s=path.stat()
    return [str(path),str(path.resolve()),s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]


def _coretext_fonts():
    """Include fonts activated outside Fontconfig on macOS, without a dependency."""
    import ctypes as c
    core=c.CDLL('/System/Library/Frameworks/CoreText.framework/CoreText')
    cf=c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    core.CTFontManagerCopyAvailableFontURLs.restype=c.c_void_p
    cf.CFArrayGetCount.argtypes=[c.c_void_p];cf.CFArrayGetCount.restype=c.c_long
    cf.CFArrayGetValueAtIndex.argtypes=[c.c_void_p,c.c_long];cf.CFArrayGetValueAtIndex.restype=c.c_void_p
    cf.CFURLGetFileSystemRepresentation.argtypes=[c.c_void_p,c.c_bool,c.c_void_p,c.c_long]
    cf.CFURLGetFileSystemRepresentation.restype=c.c_bool
    cf.CFRelease.argtypes=[c.c_void_p]
    array=core.CTFontManagerCopyAvailableFontURLs()
    if not array:raise ValueError('Font inventory unavailable')
    try:
        count=cf.CFArrayGetCount(array)
        if count>MAX_FILES:raise ValueError('Font inventory too large')
        paths=[]
        for i in range(count):
            data=c.create_string_buffer(65536)
            if not cf.CFURLGetFileSystemRepresentation(cf.CFArrayGetValueAtIndex(array,i),True,data,len(data)):
                raise ValueError('Font inventory unavailable')
            paths.append(Path(os.fsdecode(data.value)))
        return paths
    finally:cf.CFRelease(array)


def _tool(args,deadline):
    remaining=deadline-perf_counter()
    if remaining<=0:raise subprocess.TimeoutExpired(args,LOOKUP_SECONDS)
    return subprocess.run(args,capture_output=True,text=True,check=True,timeout=remaining).stdout


def _font_dependencies(jobs,deadline=None):
    deadline=perf_counter()+LOOKUP_SECONDS if deadline is None else deadline
    # Fontconfig controls aliases/fallback on Linux and may also be used by a
    # macOS FFmpeg build. Unknown inventories disable reuse, never painting.
    listed=_tool(['fc-list','-f','%{file}\\n'],deadline)
    config=_tool(['fc-conflist'],deadline)
    paths={Path(p)for p in listed.splitlines()if p}
    if not paths:raise ValueError('Font inventory unavailable')
    for line in config.splitlines():
        if not line.startswith(('+ ','- ')):raise ValueError('Unknown font configuration inventory')
        path=Path(line[2:].split(': ',1)[0])
        if not path.is_absolute():raise ValueError('Unknown font configuration path')
        paths.add(path)
    if sys.platform=='darwin':paths.update(_coretext_fonts())
    elif not sys.platform.startswith('linux'):raise ValueError('Unknown font provider')
    for job in jobs:
        for plan in job['plans']:
            font=Path(plan['font']['path']);paths.add(font)
            # libass loads the entire supplied fontsdir, not only one font.
            paths.update(p for p in font.parent.iterdir()if p.is_file())
    if len(paths)>MAX_FILES:raise ValueError('Font inventory too large')
    return paths


def environment_fingerprint(jobs):
    """Fixed renderer/runtime plus complete discovered font/config file identities."""
    try:
        if not shutil.which('fc-list') or not shutil.which('fc-conflist'):return None
        import av
        import numpy
        executable=shutil.which('ffmpeg')
        if executable is None:return None
        deadline=perf_counter()+LOOKUP_SECONDS
        version=_tool([executable,'-version'],deadline)
        files=_font_dependencies(jobs,deadline)
        files.update(p for p in Path(__file__).parent.iterdir()if p.suffix in ('.py','.mjs'))
        files.add(Path(executable))
        # Same-path/same-size replacements with restored mtime still change
        # inode/ctime. A symlink's resolved destination is part of the stamp.
        stamps=[]
        for path in sorted(files):
            if perf_counter()>deadline:return None
            stamps.append(file_stamp(path))
        environment={k:v for k,v in os.environ.items()if k.startswith(('FONTCONFIG_','FC_','ASS_','LC_','DYLD_','LD_'))
            or k in {'LANG','HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_DATA_DIRS','FREETYPE_PROPERTIES'}}
        return digest([version,stamps,environment,sys.version,av.__version__,av.library_versions,numpy.__version__])
    except (OSError,ValueError,AttributeError,subprocess.SubprocessError):
        return None


def job_key(job,packet,environment):
    if environment is None:return None
    packets=packet if isinstance(packet,list) else [packet]
    visual=[{k:p[k]for k in ('range','canvas','fps','initial','media','samples')}for p in packets]
    # Compile the current snapshot first; audio intent and revision identity
    # cannot invalidate identical pixels. Visual/range changes are conservative
    # misses even where a particular caption happens to look the same.
    return digest([job,visual,environment])


def copy_candidate(record,directory,count):
    """Validate every byte and copy; a disappearing old request is just a miss."""
    try:
        if not isinstance(record,dict) or len(record['files'])!=count:return None
        directory.mkdir()
        outputs=[];total=0
        for i,item in enumerate(record['files']):
            source=Path(item['path'])
            if source.is_symlink() or source.parent.is_symlink() or source.parent.parent.is_symlink():
                raise ValueError('Caption candidate ownership changed')
            if source.stat().st_size>MAX_BYTES-total:raise ValueError('Caption candidate too large')
            with source.open('rb') as stream:data=stream.read(MAX_BYTES-total+1)
            total+=len(data)
            if total>MAX_BYTES or len(data)!=item['bytes'] or sha256(data).hexdigest()!=item['sha256']:
                raise ValueError('Caption candidate no longer matches')
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Caption candidate is not PNG')
            rect=item['rect']
            if set(rect)!={'x','y','width','height'} or any(type(v)is not int or v<0 for v in rect.values()) or not rect['width'] or not rect['height']:
                raise ValueError('Invalid caption rectangle')
            target=directory/f'state-{i+1:03d}.png';target.write_bytes(data)
            outputs.append((target,dict(rect)))
        return outputs
    except (OSError,ValueError,TypeError,KeyError):
        shutil.rmtree(directory,ignore_errors=True)
        return None


def describe(paths):
    files=[];total=0
    for path,rect in paths:
        if path.stat().st_size>MAX_BYTES-total:return None
        data=path.read_bytes();total+=len(data)
        if total>MAX_BYTES:return None
        files.append({'path':str(path),'rect':dict(rect),'bytes':len(data),'sha256':sha256(data).hexdigest()})
    return {'files':files,'bytes':total}


class CaptionCandidates:
    """References only: no shared artifacts, and never more than 8 jobs / 32 MiB."""
    def __init__(self):self.entries={}

    def prune(self):
        def available(record):
            try:return all(Path(f['path']).is_file()for f in record['files'])
            except (OSError,ValueError,TypeError,KeyError):return False
        self.entries={k:v for k,v in self.entries.items()if available(v)}

    def snapshot(self):
        self.prune()
        return dict(self.entries)

    def accept(self,records,folder):
        if not isinstance(records,dict):return
        for key,record in list(records.items())[:MAX_JOBS]:
            try:
                if not re.fullmatch('[a-f0-9]{64}',key):continue
                files=record['files']
                if not isinstance(files,list) or not 0<len(files)<=600:continue
                total=0;safe=[]
                for item in files:
                    path=Path(item['path'])
                    if (path.parent.parent!=folder or not path.resolve().is_relative_to(folder.resolve())
                            or path.is_symlink() or path.parent.is_symlink()
                            or not re.fullmatch('captions-[a-f0-9]{24}',path.parent.name)
                            or path.suffix!='.png' or not path.is_file()):
                        raise ValueError('Caption candidate outside its request')
                    if type(item['bytes'])is not int or item['bytes']!=path.stat().st_size:raise ValueError('Invalid caption size')
                    total+=item['bytes'];safe.append(dict(item))
                if total>MAX_BYTES:continue
                self.entries.pop(key,None);self.entries[key]={'files':safe,'bytes':total}
                while len(self.entries)>MAX_JOBS or sum(x['bytes']for x in self.entries.values())>MAX_BYTES:
                    self.entries.pop(next(iter(self.entries)))
            except (OSError,ValueError,TypeError,KeyError):continue
        self.prune()


def prepare_jobs(jobs,contexts,folder,candidates,timing):
    from moviestar.native_captions import paint_plane
    from moviestar.preview_timing import phase
    stats={'hits':0,'misses':0,'copied_bytes':0,'eligible_jobs':0}
    if not jobs:return {},{},stats
    with phase(timing,'caption_reuse_check'):
        environment=environment_fingerprint(list(jobs.values()))
    outputs={};keys={};hits=set()
    for identity,job in jobs.items():
        keys[identity]=job_key(job,contexts[identity],environment)
        directory=folder/('captions-'+identity)
        paths=None
        if keys[identity] and keys[identity] in candidates:
            with phase(timing,'caption_reuse_copy'):
                paths=copy_candidate(candidates[keys[identity]],directory,len(job['frames']))
        if paths is None:
            with phase(timing,'caption_paint'):paths=paint_plane(job,directory)
        else:hits.add(identity)
        outputs[identity]=paths
    with phase(timing,'caption_reuse_check'):
        stable=environment is not None and environment_fingerprint(list(jobs.values()))==environment
    if not stable:
        # A font/config/tool change during copy is a miss. Repaint hit jobs
        # normally and publish no new candidates from this unstable request.
        for identity in hits:
            shutil.rmtree(folder/('captions-'+identity),ignore_errors=True)
            with phase(timing,'caption_paint'):
                outputs[identity]=paint_plane(jobs[identity],folder/('captions-'+identity))
        hits.clear()
    records={}
    if stable:
        for identity,paths in outputs.items():
            item=describe(paths)
            if item is not None:records[keys[identity]]=item
    stats.update(hits=len(hits),misses=len(jobs)-len(hits),eligible_jobs=len(records),
        copied_bytes=sum(p.stat().st_size for identity in hits for p,_ in outputs[identity]))
    return outputs,records,stats
