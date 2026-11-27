"""Exact-version Cloud preview and full-quality renders; ordinary local export stays local."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime,timezone
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from urllib.error import URLError, HTTPError
from http.client import HTTPException
import click
from moviestar import cloud as service
from moviestar.account import _NoRedirectHandler
from moviestar.cloud_creation_state import creation_request, CreationBusy, CreationStateError


def fail(code,message,**fields):
    click.echo(json.dumps({'error':{'code':code,'message':message,'details':{},'retryable':code in ('submission_unconfirmed','renders_unavailable')},**fields},indent=2))
    raise click.exceptions.Exit(1)


def checked(status,body,**fields):
    if status in (200,202):
        return
    error=body.get('error')
    if (isinstance(error,dict) and set(error)=={'code','message','details','retryable'}
            and isinstance(error['code'],str) and isinstance(error['message'],str)
            and isinstance(error['details'],dict) and type(error['retryable']) is bool):
        click.echo(json.dumps({'error':error,**({'request_id':body['request_id']} if isinstance(body.get('request_id'),str) else {}),**fields},indent=2));raise click.exceptions.Exit(1)
    fail('renders_unavailable','Reopen the same IDs; no new render was requested.',**fields)


def ids(*values):
    if any(not isinstance(v,str) or not service._UUID.fullmatch(v) for v in values):
        fail('invalid_request','Use the exact project, saved revision, job or artifact UUIDs.')
    return [v.lower() for v in values]


def valid_job(value,project,job=None,revision=None,kind=None):
    return (isinstance(value,dict) and isinstance(value.get('id'),str) and service._UUID.fullmatch(value['id'])
            and value.get('project_id')==project and (job is None or value['id']==job)
            and isinstance(value.get('revision_id'),str) and service._UUID.fullmatch(value['revision_id'])
            and (revision is None or value['revision_id']==revision) and value.get('kind') in ('preview','final') and (kind is None or value['kind']==kind)
            and value.get('state') in ('queued','running','verifying','succeeded','failed','cancelled')
            and (isinstance(value.get('artifact_id'),str) and service._UUID.fullmatch(value['artifact_id'])
                 if value.get('state')=='succeeded' else value.get('artifact_id') is None))


def valid_artifact(value,project,artifact=None,revision=None):
    return (isinstance(value,dict) and value.get('project_id')==project and value.get('kind') in ('preview','final')
            and all(isinstance(value.get(k),str) and service._UUID.fullmatch(value[k]) for k in ('id','revision_id','job_id','attempt_id'))
            and (artifact is None or value['id']==artifact) and (revision is None or value['revision_id']==revision)
            and isinstance(value.get('engine_version'),str) and re.fullmatch('[a-f0-9]{40}',value['engine_version'])
            and isinstance(value.get('sha256'),str) and re.fullmatch('[a-f0-9]{64}',value['sha256'])
            and type(value.get('size_bytes')) is int and 12<=value['size_bytes']<=(67108864 if value['kind']=='final' else 16777216))


def download(body,project,artifact,out,service_url):
    grant=body.get('download')
    if (not valid_artifact(body,project,artifact) or body.get('content_type')!='video/mp4'
            or not isinstance(grant,dict) or grant.get('method')!='GET' or grant.get('required_headers')!={}):
        fail('invalid_response','The artifact grant did not match the requested verified file.')
    if not isinstance(grant.get('url'),str) or not isinstance(grant.get('expires_at'),str):
        fail('invalid_response','The artifact grant has no valid URL/expiry.')
    try:
        url=urlsplit(grant['url'])
        expires=datetime.fromisoformat(grant['expires_at'].replace('Z','+00:00'))
        if expires.utcoffset() is None or not 0<(expires-datetime.now(timezone.utc)).total_seconds()<=120:
            raise ValueError('Expired or invalid grant')
        connected=urlsplit(service_url)
        same_local=(connected.scheme=='http' and connected.hostname in ('localhost','127.0.0.1','::1')
                    and url.scheme=='http' and url.netloc==connected.netloc)
        if (not (same_local or url.scheme=='https' and url.hostname and url.hostname.endswith('.r2.cloudflarestorage.com'))
                or url.username or url.password or url.fragment):
            raise ValueError('Invalid private storage URL')
        target=out.expanduser().absolute()
        if os.path.lexists(target) or not target.parent.is_dir():
            fail('destination_exists','Choose a new file in an existing directory; existing paths are never overwritten.')
        temporary=None
        try:
            fd,name=tempfile.mkstemp(prefix='.preview-',suffix='.part',dir=target.parent);temporary=Path(name)
            sha=hashlib.sha256();size=0
            # Storage gets only its grant; no Moviestar account credential.
            with os.fdopen(fd,'wb') as output, build_opener(_NoRedirectHandler()).open(Request(grant['url']),timeout=60) as response:
                if response.status!=200 or response.headers.get_content_type()!='video/mp4':
                    raise ValueError('Unexpected transfer')
                length=response.headers.get('Content-Length')
                if length is not None and length!=str(body['size_bytes']):
                    raise ValueError('Unexpected length')
                while part:=response.read(65536):
                    size+=len(part)
                    if size>body['size_bytes']:
                        raise ValueError('Oversized media')
                    sha.update(part);output.write(part)
                if size!=body['size_bytes'] or sha.hexdigest()!=body['sha256']:
                    raise ValueError('Integrity mismatch')
                output.flush();os.fsync(output.fileno())
            os.link(temporary,target)  # Atomic publication without overwrite.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # A leftover private .part must not invalidate a published file.
        return str(target)
    except (OSError,URLError,HTTPError,HTTPException,ValueError,KeyError):
        fail('download_incomplete','The file was not verified/saved. Refresh the grant for this same artifact and retry to a new file; do not render again.',project_id=project,artifact_id=artifact)


def register_renders(cloud):
    @cloud.group()
    def renders():
        """Render, reopen or cancel an exact saved Cloud revision."""

    @renders.command('submit')
    @click.argument('project_id')
    @click.option('--revision',required=True,help='Exact immutable saved revision; never inferred from latest.')
    @click.option('--kind',type=click.Choice(['preview','final']),default='preview',show_default=True)
    @click.option('--idempotency-key',help='Retain on retries. Generated and saved before sending if omitted.')
    def submit(project_id,revision,kind,idempotency_key):
        """Request preview (480p) or final (full source dimensions). Accepted means queued.

        Both render this exact revision without changing its spec or undo journal.
        Final matches local export with x264; no preview approval is inferred.
        Successful output allowance includes the MP4 plus 8 KiB of job metadata.

        After an uncertain reply, repeat the identical command/key. A new key
        deliberately requests new work. Poll renders show with the returned IDs.
        """
        project_id,revision=ids(project_id,revision)
        if idempotency_key is not None and not service._KEY.fullmatch(idempotency_key):
            fail('invalid_request','Use a retry key containing 1–128 letters, digits, underscores or hyphens.')
        endpoint,credential=service._connection('cloud renders submit')
        url=endpoint+'/projects/'+project_id+'/render-jobs'
        intent={'revision_id':revision,'kind':kind}
        try:
            with creation_request(url,credential,json.dumps(intent,sort_keys=True),idempotency_key) as pending:
                pending.save()
                status,body=service._request(url,credential,payload=intent,key=pending.key)
                recovery=f'moviestar cloud renders submit {project_id} --revision {revision} --kind {kind} --idempotency-key {pending.key}'
                if status not in (200,202):
                    if status==0 or status>=500:
                        fail('submission_unconfirmed','The outcome is unknown. Retry this exact command/key to recover the original job.',idempotency_key=pending.key,recovery_command=recovery)
                    if isinstance(body.get('error'),dict) and body['error'].get('code')=='idempotency_conflict':
                        checked(status,body,idempotency_key=pending.key,
                                hint='Recover the original project/revision/kind/key. Use a new key only to deliberately request different work.')
                    checked(status,body,idempotency_key=pending.key,recovery_command=recovery)
                if (body.get('status')!='accepted' or body.get('idempotency_key')!=pending.key or type(body.get('replayed')) is not bool
                        or not valid_job(body.get('job'),project_id,revision=revision,kind=kind)):
                    fail('submission_unconfirmed','No matching durable receipt was confirmed. Retain this revision/key and retry.',idempotency_key=pending.key,recovery_command=recovery)
                click.echo(json.dumps(body,indent=2))
        except (CreationBusy,CreationStateError):
            fail('request_state_unavailable','Preserve the existing retry key and repair local request state before retrying.')

    @renders.command('show')
    @click.argument('project_id')
    @click.argument('job_id')
    def show(project_id,job_id):
        """Reopen/poll a durable job without creating work."""
        project_id,job_id=ids(project_id,job_id)
        endpoint,credential=service._connection('cloud renders show')
        status,body=service._request(endpoint+f'/projects/{project_id}/render-jobs/{job_id}',credential)
        checked(status,body)
        if not valid_job(body.get('job'),project_id,job=job_id):
            fail('invalid_response','Reopen the original project/job IDs; response identities were inconsistent.')
        click.echo(json.dumps(body,indent=2))

    @renders.command('cancel')
    @click.argument('project_id')
    @click.argument('job_id')
    def cancel(project_id,job_id):
        """Fence an unfinished job; no edit/undo or deletion of old outputs."""
        project_id,job_id=ids(project_id,job_id)
        endpoint,credential=service._connection('cloud renders cancel')
        status,body=service._request(endpoint+f'/projects/{project_id}/render-jobs/{job_id}/cancel',credential,payload={})
        checked(status,body)
        if not valid_job(body.get('job'),project_id,job=job_id):
            fail('invalid_response','Reopen the same job to confirm cancellation.')
        click.echo(json.dumps(body,indent=2))

    @cloud.group()
    def artifacts():
        """Read or download verified outputs bound to saved revisions."""

    from moviestar.cloud_render_frames import register_frame_commands
    register_frame_commands(artifacts)

    @artifacts.command('list')
    @click.argument('project_id')
    @click.option('--revision',help='Filter by this exact saved version.')
    def list_artifacts(project_id,revision):
        project_id=ids(project_id)[0]
        if revision:
            revision=ids(revision)[0]
        endpoint,credential=service._connection('cloud artifacts list')
        status,body=service._request(endpoint+f'/projects/{project_id}/artifacts'+('?revision_id='+revision if revision else ''),credential)
        checked(status,body)
        if (not isinstance(body.get('items'),list) or len(body['items'])>20 or body.get('next_cursor','missing') is not None
                or any(not valid_artifact(a,project_id,revision=revision) for a in body['items'])):
            fail('invalid_response','The bounded artifact list is incomplete or incompatible.')
        click.echo(json.dumps(body,indent=2))

    @artifacts.command('show')
    @click.argument('project_id')
    @click.argument('artifact_id')
    def show_artifact(project_id,artifact_id):
        project_id,artifact_id=ids(project_id,artifact_id)
        endpoint,credential=service._connection('cloud artifacts show')
        status,body=service._request(endpoint+f'/projects/{project_id}/artifacts/{artifact_id}',credential);checked(status,body)
        if not valid_artifact(body,project_id,artifact_id):
            fail('invalid_response','The artifact identity did not match.')
        click.echo(json.dumps(body,indent=2))

    @artifacts.command('download')
    @click.argument('project_id')
    @click.argument('artifact_id')
    @click.option('--out',required=True,type=click.Path(path_type=Path),help='New MP4 path; never overwrites. Size and SHA-256 are verified before success.')
    def download_artifact(project_id,artifact_id,out):
        project_id,artifact_id=ids(project_id,artifact_id)
        endpoint,credential=service._connection('cloud artifacts download')
        status,body=service._request(endpoint+f'/projects/{project_id}/artifacts/{artifact_id}/download',credential,payload={});checked(status,body)
        path=download(body,project_id,artifact_id,out,endpoint)
        click.echo(json.dumps({'status':'downloaded','project_id':project_id,'revision_id':body.get('revision_id'),'artifact_id':artifact_id,'sha256':body['sha256'],'size_bytes':body['size_bytes'],'out':path,'file_saved':True},indent=2))
