"""Durable visual skims of existing ready Cloud video assets."""
import json
import math
import shutil
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from http.client import HTTPException
import click
from moviestar import cloud as service
from moviestar.account import _CLIENT_MARKER, _NoRedirectHandler
from moviestar.cloud_creation_state import creation_request, CreationBusy, CreationStateError
from moviestar.timecodes import parse_timecode


def _timestamp(value):
    seconds = parse_timecode(value)
    millis = round(seconds * 1000) if math.isfinite(seconds) else -1
    if not 0 <= millis <= 86400000 or abs(seconds*1000-millis)>0.000001:
        raise ValueError('Use finite timestamps between 0 and 86400 seconds at millisecond precision.')
    return millis


def _job(body, asset, job_id=None, options=None):
    job = body.get('job')
    valid = isinstance(job, dict) and isinstance(job.get('id'), str) and service._UUID.fullmatch(job['id'])
    if valid:
        actual = job.get('options')
        valid = (job.get('asset_id') == asset and job.get('state') in ('queued','running','succeeded','failed','cancelled')
                 and isinstance(actual, dict) and set(actual)=={'from_ms','to_ms','count'}
                 and type(actual['from_ms']) is int and 0<=actual['from_ms']<=86400000
                 and (actual['to_ms'] is None or type(actual['to_ms']) is int and actual['from_ms']<actual['to_ms']<=86400000)
                 and type(actual['count']) is int and 1<=actual['count']<=16
                 and (job_id is None or job['id']==job_id) and (options is None or actual==options))
    if not valid:
        service._fail('cloud skim','invalid_response','Retain the original request key and retry; the skim outcome is uncertain.')
    return job


def _check(status, body, **fields):
    if status not in (200,202):
        error=body.get('error',{})
        code=error.get('code','skim_unavailable') if isinstance(error,dict) else 'skim_unavailable'
        allowed={'credential_invalid','not_found','invalid_request','idempotency_conflict','queue_busy','asset_not_ready','range_out_of_bounds','unsupported_asset','skim_limit','skim_unavailable'}
        service._fail('cloud skim',code if code in allowed else 'skim_unavailable',
                      'Retry the same asset, range, count and request key. Reconnect if credentials expired.',**fields)


def _frames(job):
    result=job.get('result')
    frames=result.get('thumbnails') if isinstance(result,dict) and result.get('mode')=='visual_skim' else None
    if not isinstance(frames,list) or len(frames)>job['options']['count']:
        raise ValueError('Invalid skim result')
    previous=-1
    for index,frame in enumerate(frames):
        if (not isinstance(frame,dict) or frame.get('index')!=index or type(frame.get('at_ms')) is not int
                or not previous<frame['at_ms']<=86400000 or frame['at_ms']<job['options']['from_ms']
                or job['options']['to_ms'] is not None and frame['at_ms']>job['options']['to_ms']
                or type(frame.get('file_size_bytes')) is not int or not 4<=frame['file_size_bytes']<=131072
                or frame.get('mime_type')!='image/jpeg'):
            raise ValueError('Invalid thumbnail')
        previous=frame['at_ms']
    return frames


def register_skim_commands(cloud):
    @cloud.command('skim')
    @click.argument('asset_id')
    @click.option('--from','start',default='0',show_default=True,help='Inclusive nominal index time: seconds, M:SS or HH:MM:SS.mmm.')
    @click.option('--to','end',help='Inclusive nominal index end; defaults to video duration.')
    @click.option('--count',default=10,type=int,show_default=True)
    @click.option('--idempotency-key',help='Retain this retry key; generated and saved when omitted.')
    def skim(asset_id,start,end,count,idempotency_key):
        """Request a bounded visual skim without loading a local project.

        Returns a durable job ID. Retry the same options after an uncertain reply;
        use skim-show to reopen status and save private timestamped JPEG files.
        Existing transcript association is currently unavailable; no transcription
        is started. Count=1 selects the middle prepared index frame in the range.
        """
        command='cloud skim'
        if not 1<=count<=16:
            service._fail(command,'invalid_request','Count must be between 1 and 16.')
        try:
            options={'from_ms':_timestamp(start),'to_ms':_timestamp(end) if end is not None else None,'count':count}
            if options['from_ms']>=86400000:
                raise ValueError('The start must be earlier than 86400 seconds.')
            if options['to_ms'] is not None and options['from_ms']>=options['to_ms']:
                raise ValueError('The end must be later than the start.')
        except ValueError as error:
            service._fail(command,'invalid_request',str(error))
        if not service._UUID.fullmatch(asset_id) or idempotency_key is not None and not service._KEY.fullmatch(idempotency_key):
            service._fail(command,'invalid_request','Use an asset UUID and a valid retry key.')
        asset_id=asset_id.lower()
        endpoint,credential=service._connection(command)
        url=endpoint.removesuffix('/account/cloud')+f'/assets/{asset_id}/skim'
        try:
            with creation_request(url,credential,json.dumps(options,sort_keys=True,separators=(',',':')),idempotency_key) as pending:
                pending.save()
                # Keep the canonical null end in retry state/response validation,
                # but the API represents an open end by an omitted field.
                payload={name:value for name,value in options.items() if value is not None}
                status,body=service._request(url,credential,payload=payload,key=pending.key)
                _check(status,body,idempotency_key=pending.key)
                job=_job(body,asset_id,options=options)
                if body.get('idempotency_key')!=pending.key:
                    service._fail(command,'invalid_response','Retry the same options to recover the skim.',idempotency_key=pending.key)
                click.echo(json.dumps({'command':command,'job':job,'idempotency_key':pending.key},indent=2))
        except (CreationBusy,CreationStateError):
            service._fail(command,'request_state_unavailable','Wait for the active request or repair retry state while retaining its key.')

    @cloud.command('skim-show')
    @click.argument('asset_id')
    @click.argument('job_id')
    @click.option('--out-dir',type=click.Path(path_type=Path),help='Save JPEG frames and skim.json into a new directory; never overwrites.')
    def show(asset_id,job_id,out_dir):
        """Reopen a visual skim by durable IDs, without creating more work."""
        command='cloud skim-show'
        if not all(service._UUID.fullmatch(i) for i in (asset_id,job_id)):
            service._fail(command,'invalid_request','Use the original asset and job UUIDs.')
        asset_id,job_id=asset_id.lower(),job_id.lower()
        endpoint,credential=service._connection(command)
        url=endpoint.removesuffix('/account/cloud')+f'/assets/{asset_id}/skims/{job_id}'
        status,body=service._request(url,credential);_check(status,body)
        job=_job(body,asset_id,job_id);result={'command':command,'job':job}
        if out_dir is not None:
            if job['state']!='succeeded':
                service._fail(command,'result_not_ready','Poll the same IDs until the skim succeeds.',job=job)
            created=False
            try:
                frames=_frames(job)
                out_dir.mkdir();created=True
                paths=[]
                opener=build_opener(_NoRedirectHandler())
                for frame in frames:
                    req=Request(url+f"/images/{frame['index']}",headers={'Authorization':'Bearer '+credential,'x-moviestar-client':_CLIENT_MARKER,'Accept':'image/jpeg'})
                    with opener.open(req,timeout=30) as response:
                        if response.status!=200 or response.headers.get_content_type()!='image/jpeg':
                            raise ValueError('Invalid image response')
                        image=response.read(131073)
                    if len(image)!=frame['file_size_bytes'] or len(image)>131072 or not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
                        raise ValueError('Invalid image')
                    path=out_dir/f"frame-{frame['index']:02d}-{frame['at_ms']:010d}ms.jpg"
                    with path.open('xb') as output:output.write(image)
                    paths.append(str(path.resolve()))
                result['images']=paths;result['out_dir']=str(out_dir.resolve())
                with (out_dir/'skim.json').open('x') as manifest:json.dump(result,manifest,indent=2)
            except (HTTPError,URLError,HTTPException,OSError,ValueError):
                if created:shutil.rmtree(out_dir)
                service._fail(command,'images_unavailable','Use a new output directory and retry the same IDs; the server result is retained.')
        click.echo(json.dumps(result,indent=2))
