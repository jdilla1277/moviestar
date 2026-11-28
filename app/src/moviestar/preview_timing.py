"""Opt-in local numeric timings; never include project data or exception text."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import math
import os
from time import perf_counter, time

NAMES = {'snapshot_validation','queue_dispatch','worker_startup','module_imports',
         'request_read','descriptor_compile','packet_index','audio_prepare','caption_paint',
         'descriptor_publish','worker_exit_and_publication','caption_reuse_check','caption_reuse_copy'}


class PreparationTiming:
    def __init__(self, document=None):
        self.document = deepcopy(document) if document is not None else {
            'schema_version': 1, 'origin_perf': perf_counter(), 'started_at': time()*1000,
            'phases': []}

    def add(self, name, start, end):
        if name not in NAMES or not self.document['origin_perf'] <= start <= end:
            raise ValueError('Invalid preparation timing interval')
        origin=self.document['origin_perf']
        self.document['phases'].append({'name':name,'start_ms':(start-origin)*1000,
            'end_ms':(end-origin)*1000,'duration_ms':(end-start)*1000})

    @contextmanager
    def phase(self, name):
        start=perf_counter()
        try:
            yield
        finally:
            self.add(name,start,perf_counter())

    def public(self, *, completed=None, end=None):
        end=perf_counter() if end is None else end
        elapsed=(end-self.document['origin_perf'])*1000
        result={key:deepcopy(self.document[key]) for key in ('schema_version','started_at','phases')}
        result.update(elapsed_ms=elapsed,unattributed_ms=elapsed-sum(p['duration_ms'] for p in result['phases']))
        if completed is not None:result['completed']=completed
        if 'generation_started_perf' in self.document:
            result['generation_started_ms']=(self.document['generation_started_perf']-self.document['origin_perf'])*1000
        return result


def start_timing():
    return PreparationTiming() if os.environ.get('MOVIESTAR_PREVIEW_TIMING')=='1' else None


def phase(timing, name):
    return timing.phase(name) if timing is not None else nullcontext()


def published_timing(document, *, end=None):
    """Read only the bounded schema we wrote, even if a worker file is corrupt."""
    if not isinstance(document,dict) or document.get('schema_version')!=1:
        raise ValueError('Invalid preparation timing schema')
    phases=document.get('phases')
    if not isinstance(phases,list) or len(phases)>32:
        raise ValueError('Invalid preparation timing phases')
    for key in ('origin_perf','started_at','generation_started_perf','worker_started_perf','worker_finished_perf'):
        if type(document.get(key)) not in (int,float) or not math.isfinite(document[key]):
            raise ValueError('Invalid preparation timing clock')
    end=perf_counter() if end is None else end
    if not document['origin_perf'] <= document['generation_started_perf'] <= document['worker_started_perf'] <= document['worker_finished_perf'] <= end:
        raise ValueError('Invalid preparation timing clock order')
    clean={key:document[key] for key in ('schema_version','origin_perf','started_at','generation_started_perf')}
    clean['phases']=[]
    previous=0
    for item in phases:
        if not isinstance(item,dict) or item.get('name') not in NAMES:
            raise ValueError('Invalid preparation timing phase')
        for key in ('start_ms','end_ms','duration_ms'):
            if type(item.get(key)) not in (int,float) or not math.isfinite(item[key]):
                raise ValueError('Invalid preparation timing interval')
        if item['start_ms']<previous-.001 or item['end_ms']<item['start_ms'] or abs(item['duration_ms']-(item['end_ms']-item['start_ms']))>.001:
            raise ValueError('Invalid preparation timing order')
        previous=item['end_ms']
        clean['phases'].append({key:item[key] for key in ('name','start_ms','end_ms','duration_ms')})
    if type(document.get('completed')) is not bool:
        raise ValueError('Invalid preparation timing completion')
    if previous > (document['worker_finished_perf']-document['origin_perf'])*1000+.001:
        raise ValueError('Invalid preparation timing end')
    timer=PreparationTiming(clean)
    timer.add('worker_exit_and_publication',document['worker_finished_perf'],end)
    return timer.public(completed=document['completed'],end=end)
