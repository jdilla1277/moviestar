"""Remote exact-output storyboards; reuse account transport and verified image saving."""
import json
import re
from pathlib import Path
import click
from moviestar import cloud as service
from moviestar.cloud_creation_state import creation_request, CreationBusy, CreationStateError
from moviestar.cloud_render_frames import fail,checked,ids,save_image


def timecode(ms):
    seconds,milliseconds=divmod(ms,1000);minutes,seconds=divmod(seconds,60);hours,minutes=divmod(minutes,60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}'


def valid_job(j, project, artifact, job=None):
    try:
        if (not isinstance(j, dict) or j['project_id'] != project or j['artifact_id'] != artifact
                or not all(isinstance(j[k], str) and service._UUID.fullmatch(j[k]) for k in ('id','revision_id','render_job_id'))
                or job is not None and j['id'] != job or j['clock'] != 'rendered_result'
                or not re.fullmatch('[a-f0-9]{64}',j['artifact_sha256']) or j['request'] != {}
                or j['state'] not in ('queued','running','succeeded','failed','cancelled')):
            return False
        p=j['sampling'];keys=('from_ms','to_ms','interval_ms','columns','rows','frame_count')
        if (not all(type(p[k]) is int for k in keys) or p['from_ms'] != 0 or not 100<=p['to_ms']<=30000
                or not 1<=p['interval_ms']<p['to_ms'] or p['interval_auto'] is not True
                or not 1<=p['columns']<=12 or not 1<=p['frame_count']<=60
                or p['frame_count'] != (p['to_ms']+p['interval_ms']-1)//p['interval_ms']
                or p['rows'] != (p['frame_count']+p['columns']-1)//p['columns']
                or not all(type(t)is int for t in p['requested_times_ms'])
                or p['requested_times_ms'] != [i*p['interval_ms']for i in range(p['frame_count'])]):
            return False
        if j['state'] != 'succeeded':return j.get('result') is None
        r=j['result'];image=r['image']
        return (r['selection']=='frame_at_or_before' and r['labels']=='decoded_result_time'
            and re.fullmatch('[a-f0-9]{40}',r['extractor']['engine_version']) is not None
            and re.fullmatch('[a-f0-9]{64}',r['extractor']['runtime_sha256']) is not None
            and len(r['frames'])==p['frame_count']
            and all(type(f['index'])is int and f['index']==i and type(f['requested_at_ms'])is int
                and f['requested_at_ms']==p['requested_times_ms'][i] and type(f['at_ms'])is int
                and 0<=f['at_ms']<=f['requested_at_ms'] and f['timecode']==timecode(f['at_ms'])
                for i,f in enumerate(r['frames']))
            and image['mime_type']=='image/jpeg' and all(type(image[k])is int for k in ('width','height','size_bytes'))
            and image['width']==384*p['columns'] and 48*p['rows']<=image['height']<=16384 and image['height']%p['rows']==0
            and image['width']*image['height']<=16777216 and 4<=image['size_bytes']<=2097152
            and re.fullmatch('[a-f0-9]{64}',image['sha256'])is not None)
    except (KeyError,TypeError,ValueError):return False


def register_rendered_storyboard_commands(artifacts):
    @artifacts.command('storyboard')
    @click.argument('project_id')
    @click.argument('artifact_id')
    @click.option('--idempotency-key', help='Retained on retry; generated and saved if omitted. A new key deliberately requests new processing.')
    def storyboard(project_id, artifact_id, idempotency_key):
        """Request one whole-video automatic contact sheet of an existing verified output. Never rerenders,
        imports an asset, edits the spec or silently chooses the latest output.
        Poll/reopen using storyboard-show. Poll only queued/running jobs;
        failed/cancelled jobs are terminal. Actual and requested sample times differ.
        """
        project_id, artifact_id = ids(project_id, artifact_id)
        if idempotency_key is not None and not service._KEY.fullmatch(idempotency_key):
            fail('invalid_request', 'Use a key of 1–128 ASCII letters, digits, underscores or hyphens.')
        endpoint, credential = service._connection('cloud artifacts storyboard')
        url = endpoint + f'/projects/{project_id}/artifacts/{artifact_id}/storyboards'
        try:
            with creation_request(url, credential, '{}', idempotency_key) as pending:
                pending.save()
                status, body = service._request(url, credential, payload={}, key=pending.key)
                recovery = f'moviestar cloud artifacts storyboard {project_id} {artifact_id} --idempotency-key {pending.key}'
                if status == 0 or status >= 500:
                    fail('submission_unconfirmed', 'The outcome is unknown. Retry the exact artifact, whole-video request and retained key.', idempotency_key=pending.key, recovery_command=recovery)
                checked(status, body)
                if (body.get('idempotency_key') != pending.key or type(body.get('replayed')) is not bool
                        or not valid_job(body.get('job'), project_id, artifact_id)):
                    fail('submission_unconfirmed', 'No matching receipt was confirmed. Preserve the original request/key.', idempotency_key=pending.key, recovery_command=recovery)
                click.echo(json.dumps(body, indent=2))
        except (CreationBusy, CreationStateError):
            fail('request_state_unavailable', 'Preserve the retained key and repair local retry state before retrying.')

    @artifacts.command('storyboard-show')
    @click.argument('project_id')
    @click.argument('artifact_id')
    @click.argument('job_id')
    @click.option('--out', type=click.Path(path_type=Path), help='Save a new JPEG only after size/SHA verification; never overwrites.')
    def show(project_id, artifact_id, job_id, out):
        """Reopen the exact storyboard job after reconnecting. No new extraction or
        usage reservation. The result binds the rendered file hash and revision.
        Poll only queued/running jobs; failed/cancelled jobs are terminal.
        Reopening a failed job preserves diagnostics and never restarts it.
        """
        project_id, artifact_id, job_id = ids(project_id, artifact_id, job_id)
        endpoint, credential = service._connection('cloud artifacts storyboard-show')
        url = endpoint + f'/projects/{project_id}/artifacts/{artifact_id}/storyboards/{job_id}'
        status, body = service._request(url, credential)
        checked(status, body)
        if not valid_job(body.get('job'), project_id, artifact_id, job_id):
            fail('invalid_response', 'Storyboard identity or manifest did not match. Reopen the original IDs.')
        result = {**body, 'file_saved': False}
        if out is not None:
            j = body['job']
            if j['state'] in ('failed', 'cancelled'):
                code=j.get('failure_code') or 'storyboard_failed'
                message=('Required timecode labels were unavailable. Reopen this job for its diagnostic context; resolve label/font support before deliberately requesting new work.' if code=='labels_unavailable' else 'This saved job ended without an image. Reopening does not restart it; review the failure before deliberately requesting new work.')
                fail(code,message,details={'job_id':job_id,'state':j['state'],'failure_code':code},job=j)
            if j['state'] != 'succeeded':
                fail('storyboard_not_ready', 'Poll these same IDs until the storyboard job ends.', job=j)
            result.update(out=save_image(url, credential, j, out,max_bytes=2097152), file_saved=True)
        click.echo(json.dumps(result, indent=2))
