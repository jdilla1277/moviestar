"""Read/validate CLI contracts; transport examples use synthetic responses."""
import json
from pathlib import Path
from click.testing import CliRunner
import pytest
from moviestar import cloud
from moviestar.cli import cli

P = '00000000-0000-4000-8000-000000000010'
REQUEST = {'contract_version':'cloud-editing-v1','expected_base_revision':None,
           'operation':{'type':'trim','source_id':'speaker','time_space':'source_result','from_ms':10000,'to_ms':40000}}

@pytest.fixture
def remote(monkeypatch):
    calls=[]
    monkeypatch.setattr(cloud,'_connection',lambda _:('https://service.invalid/api/v1/account/cloud','synthetic'))
    capability={'contract_version':'m33.3-personal-projects-1','project_access':'owner_only','operations':['projects:read'],
                'editing':{'contract_version':'cloud-editing-v1','read_available':True,'validation_available':True,'writes_available':False,'state_scope':'unsaved_baseline_only'}}
    response={'contract_version':'cloud-editing-v1','status':'no_saved_edit','project_id':P,'revision':None,'current_revision':None,'baseline':None}
    def request(url,credential,**kwargs):
        calls.append((url,kwargs))
        return 200, capability if url.endswith('/capabilities') else response
    monkeypatch.setattr(cloud,'_request',request)
    return calls,response,capability

def test_show_and_validate_use_same_canonical_envelopes_and_never_mutate(remote,tmp_path):
    calls,response,_=remote
    read=CliRunner().invoke(cli,['cloud','edits','show',P])
    assert read.exit_code==0,read.output
    assert json.loads(read.output)==response
    response.update(status='would_apply',changes=True,current_undo_remaining=0,projected_resolved={'undo_remaining':1})
    response.pop('revision')
    file=tmp_path/'trim.json';file.write_text(json.dumps(REQUEST))
    result=CliRunner().invoke(cli,['cloud','edits','validate',P,'--request',str(file)])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)==response
    assert calls[-1]==('https://service.invalid/api/v1/account/cloud/projects/'+P+'/edit/validate',{'payload':REQUEST})
    assert all('/mutations' not in url for url,_ in calls)


def test_saved_mix_reads_and_candidate_keep_same_revision_and_transport(remote,tmp_path):
    calls,response,_=remote
    revision='00000000-0000-4000-8000-000000000099'
    response.update(status='opened',current_revision=revision,revision=saved_mix_read(revision))
    read=CliRunner().invoke(cli,['cloud','edits','show',P])
    assert read.exit_code==0, read.output
    assert json.loads(read.output)==response
    response.update(status='would_apply',current_undo_remaining=3,changes=True,projected_resolved={'undo_remaining':4})
    response.pop('revision')
    request={'contract_version':'cloud-editing-v1','expected_base_revision':revision,'operation':{'type':'audio_source','gain_db':-6}}
    value=saved_mix_read(revision);value['spec']['revisions']=[{}]*4
    value['spec']['audio_mix']['source_audio']['gain_db']=-6
    value['resolved']['audio_mix']['source_audio']['gain_db']=-6;value['resolved']['undo_remaining']=4
    response.update(candidate={'spec':value['spec']},projected_resolved=value['resolved'],normalized_request=request,changed_fields=['audio_mix'],effects={'saved_revisions':0,'activity_events':0,'processing_jobs':0,'customer_usage':0})
    file=tmp_path/'audio.json';file.write_text(json.dumps(request))
    checked=CliRunner().invoke(cli,['cloud','edits','validate',P,'--request',str(file)])
    assert checked.exit_code==0,checked.output
    assert json.loads(checked.output)==response
    assert calls[-1][1]=={'payload':request}

@pytest.mark.parametrize('raw',['[]','null','{','{"owner_id":"untrusted"}','x'*4097])
def test_malformed_or_oversized_request_is_rejected_before_connection(remote,tmp_path,raw):
    calls,_,_=remote
    file=tmp_path/'request.json';file.write_text(raw)
    result=CliRunner().invoke(cli,['cloud','edits','validate',P,'--request',str(file)])
    assert result.exit_code!=0
    assert json.loads(result.output)['error']['code']=='invalid_request'
    assert calls==[]

def test_unsupported_and_foreign_responses_fail_closed(remote,monkeypatch):
    _,response,capability=remote
    capability['editing']['read_available']=False
    result=CliRunner().invoke(cli,['cloud','edits','show',P])
    assert result.exit_code!=0
    capability['editing']['read_available']=True
    response['project_id']='00000000-0000-4000-8000-000000000011'
    result=CliRunner().invoke(cli,['cloud','edits','show',P])
    assert result.exit_code!=0
    assert json.loads(result.output)['error']['code']=='invalid_response'

@pytest.mark.parametrize('status,code',[(401,'credential_invalid'),(403,'account_consent_required'),(503,'cloud_unavailable')])
@pytest.mark.parametrize('action',['show','validate','capabilities'])
def test_capability_errors_preserve_resource_error_envelope(remote,monkeypatch,tmp_path,status,code,action):
    envelope={'error':{'code':code,'message':'Service diagnostic','details':{},'retryable':status==503},'request_id':'synthetic-request'}
    monkeypatch.setattr(cloud,'_request',lambda *a,**k:(status,envelope))
    args=['cloud','edits',action]
    if action!='capabilities': args.append(P)
    if action=='validate':
        file=tmp_path/'request.json';file.write_text(json.dumps(REQUEST));args+=['--request',str(file)]
    result=CliRunner().invoke(cli,args)
    assert result.exit_code==1
    assert json.loads(result.output)==envelope
    # Legacy clients retain their existing preflight envelope.
    legacy=CliRunner().invoke(cli,['cloud','projects','show',P])
    assert json.loads(legacy.output)['status']==code

@pytest.mark.parametrize('status,code',[(401,'credential_invalid'),(403,'account_consent_required'),(503,'cloud_unavailable')])
@pytest.mark.parametrize('action',['show','validate'])
def test_resource_errors_use_same_envelope_after_successful_capabilities(remote,monkeypatch,tmp_path,status,code,action):
    _,_,capability=remote
    envelope={'error':{'code':code,'message':'Service diagnostic','details':{},'retryable':status==503},'request_id':'synthetic-resource'}
    monkeypatch.setattr(cloud,'_request',lambda url,*a,**k:(200,capability) if url.endswith('/capabilities') else (status,envelope))
    args=['cloud','edits',action,P]
    if action=='validate':
        file=tmp_path/'request.json';file.write_text(json.dumps(REQUEST));args+=['--request',str(file)]
    result=CliRunner().invoke(cli,args)
    assert result.exit_code==1
    assert json.loads(result.output)==envelope

def test_apply_retains_exact_request_before_lost_reply_and_fresh_recovery(remote,tmp_path,monkeypatch):
    calls,response,capability=remote
    from moviestar import account
    monkeypatch.setattr(account,'_account_state_path',lambda:tmp_path/'account.json')
    from moviestar import cloud_creation_state
    monkeypatch.setattr(cloud_creation_state,'_account_state_path',lambda:tmp_path/'account.json')
    file=tmp_path/'trim.json';file.write_text(json.dumps(REQUEST))
    mutation_calls=[]
    def request(url,credential,**kwargs):
        if url.endswith('/capabilities'):return 200,capability
        mutation_calls.append(kwargs)
        if len(mutation_calls)==1:return 0,{}
        return 200,{'contract_version':'cloud-editing-v1','project_id':P,'status':'committed','current_revision':'00000000-0000-4000-8000-000000000099',
                    'revision':{'id':'00000000-0000-4000-8000-000000000099','spec':{},'resolved':{}},'replayed':True,
                    'changes':True,'normalized_request':REQUEST,'idempotency_key':kwargs['key'],'request_sha256':'a'*64,'canonical_format':'postgres-jsonb-text-v1',
                    'effects':{'saved_revisions':0,'activity_events':0,'processing_jobs':0,'customer_usage':0},
                    'verification':{'format':'cloud-edit-state-proof-1','saved_state':'read_back_match','output':'not_checked'}}
    monkeypatch.setattr(cloud,'_request',request)
    lost=CliRunner().invoke(cli,['cloud','edits','apply',P,'--request',str(file)])
    assert lost.exit_code==1
    assert json.loads(lost.output)['error']['code']=='mutation_unconfirmed'
    # A changed file must never replace an uncertain command under the retained key.
    file.write_text(json.dumps({**REQUEST,'operation':{'type':'undo'}}))
    blocked=CliRunner().invoke(cli,['cloud','edits','apply',P,'--request',str(file)])
    assert blocked.exit_code==1 and len(mutation_calls)==1
    recovered=CliRunner().invoke(cli,['cloud','edits','apply',P,'--recover'])
    assert recovered.exit_code==0,recovered.output
    assert mutation_calls[0]==mutation_calls[1]
    assert mutation_calls[0]['payload']==REQUEST


def test_exact_show_does_not_conflate_selected_and_current(remote):
    calls,response,_=remote
    old='00000000-0000-4000-8000-000000000098';current='00000000-0000-4000-8000-000000000099'
    response.update(status='opened',current_revision=current,revision=saved_mix_read(old))
    result=CliRunner().invoke(cli,['cloud','edits','show',P,'--revision',old])
    assert result.exit_code==0,result.output
    assert calls[-1][0].endswith('/revisions/'+old)


def test_incomplete_success_never_clears_pending_edit(remote,tmp_path,monkeypatch):
    from moviestar import cloud_creation_state
    monkeypatch.setattr(cloud_creation_state,'_account_state_path',lambda:tmp_path/'account.json')
    _,_,capability=remote
    file=tmp_path/'trim.json';file.write_text(json.dumps(REQUEST))
    monkeypatch.setattr(cloud,'_request',lambda url,*a,**k:(200,capability) if url.endswith('/capabilities') else
        (200,{'contract_version':'cloud-editing-v1','project_id':P,'status':'committed','verification':{}}))
    result=CliRunner().invoke(cli,['cloud','edits','apply',P,'--request',str(file)])
    assert result.exit_code==1,result.output
    assert json.loads(result.output)['error']['code']=='mutation_unconfirmed'
    records=list((tmp_path/'cloud-edit-requests').rglob('*.json'))
    assert len(records)==1 and json.loads(records[0].read_text())['state']=='pending'


def test_new_explicit_key_cannot_bypass_pending_edit_storage(remote,tmp_path,monkeypatch):
    from moviestar import cloud_creation_state
    monkeypatch.setattr(cloud_creation_state,'_account_state_path',lambda:tmp_path/'account.json')
    _,_,capability=remote
    file=tmp_path/'trim.json';file.write_text(json.dumps(REQUEST))
    writes=[]
    def request(url,credential,**kwargs):
        if url.endswith('/capabilities'):return 200,capability
        writes.append(kwargs)
        return 0,{}
    monkeypatch.setattr(cloud,'_request',request)
    runner=CliRunner()
    assert runner.invoke(cli,['cloud','edits','apply',P,'--request',str(file),'--idempotency-key','first']).exit_code==1
    record=next((tmp_path/'cloud-edit-requests').rglob('*.json'));original=record.read_bytes()
    blocked=runner.invoke(cli,['cloud','edits','apply',P,'--request',str(file),'--idempotency-key','second'])
    assert blocked.exit_code==1
    assert json.loads(blocked.output)['error']['code']=='invalid_request'
    assert len(writes)==1  # Key B is never sent without a durable record.
    assert record.read_bytes()==original
    blocked_recovery=runner.invoke(cli,['cloud','edits','apply',P,'--recover','--idempotency-key','second'])
    assert blocked_recovery.exit_code==1 and len(writes)==1
    # New invocation/process state recovers only A with A's exact retained body.
    recovered=CliRunner().invoke(cli,['cloud','edits','apply',P,'--recover'])
    assert recovered.exit_code==1  # second simulated response loss
    assert writes==[{'payload':REQUEST,'key':'first'}]*2
    assert record.read_bytes()==original


@pytest.mark.parametrize('rejected_status,code',[(422,'invalid_edit'),(409,'revision_conflict'),(0,None)])
def test_deliberate_abandon_preserves_recovery_and_allows_corrected_request(remote,tmp_path,monkeypatch,rejected_status,code):
    from moviestar import cloud_creation_state
    monkeypatch.setattr(cloud_creation_state,'_account_state_path',lambda:tmp_path/'account.json')
    _,_,capability=remote
    rejected={**REQUEST,'operation':{**REQUEST['operation'],'from_ms':200,'to_ms':100}} if code=='invalid_edit' else REQUEST
    corrected=REQUEST if code=='invalid_edit' else {**REQUEST,'expected_base_revision':'00000000-0000-4000-8000-000000000099'}
    file=tmp_path/'trim.json';file.write_text(json.dumps(rejected))
    writes=[];calls=[]
    def request(url,credential,**kwargs):
        calls.append(url)
        if url.endswith('/capabilities'):return 200,capability
        writes.append(kwargs)
        if len(writes)==1:return rejected_status,({'error':{'code':code,'message':'Rejected','details':{},'retryable':False}} if code else {})
        return 0,{}  # retain the new command after a simulated lost reply
    monkeypatch.setattr(cloud,'_request',request)
    runner=CliRunner();first=runner.invoke(cli,['cloud','edits','apply',P,'--request',str(file)])
    assert first.exit_code==1
    active=next((tmp_path/'cloud-edit-requests').rglob('*.json'));old=active.read_bytes();old_record=json.loads(old)
    file.write_text(json.dumps(corrected))
    blocked=runner.invoke(cli,['cloud','edits','apply',P,'--request',str(file)])
    assert blocked.exit_code==1 and len(writes)==1
    before=len(calls)
    abandoned=runner.invoke(cli,['cloud','edits','apply',P,'--abandon-pending'])
    assert abandoned.exit_code==0,abandoned.output
    value=json.loads(abandoned.output)
    assert value['status']=='pending_abandoned' and value['server_outcome']=='unknown'
    assert len(calls)==before  # purely local, including when service is offline
    recovery=value['recovery']
    assert recovery['idempotency_key']==old_record['idempotency_key']
    assert json.loads(Path(recovery['request_file']).read_text())==rejected
    assert json.loads(Path(recovery['record_file']).read_text())==old_record
    next_result=CliRunner().invoke(cli,['cloud','edits','apply',P,'--request',str(file)])
    assert next_result.exit_code==1
    assert writes[1]['payload']==corrected and writes[1]['key']!=writes[0]['key']
    recovered=CliRunner().invoke(cli,['cloud','edits','apply',P,'--recover'])
    assert recovered.exit_code==1 and writes[2]==writes[1]


@pytest.mark.parametrize('extra',[['--recover'],['--request','unused.json'],['--idempotency-key','new-key']])
def test_abandon_does_not_accept_competing_intents(remote,extra):
    calls,_,_=remote
    result=CliRunner().invoke(cli,['cloud','edits','apply',P,'--abandon-pending',*extra])
    assert result.exit_code==1 and json.loads(result.output)['error']['code']=='invalid_request'
    assert calls==[]


@pytest.mark.parametrize('failure',['mkdir','parent_sync'])
def test_archive_failure_does_not_forget_an_uncertain_edit(remote,tmp_path,monkeypatch,failure):
    from moviestar import cloud_creation_state,cloud_edits
    if failure=='parent_sync' and cloud_edits.os.name=='nt':pytest.skip('Directory fsync is POSIX only')
    monkeypatch.setattr(cloud_creation_state,'_account_state_path',lambda:tmp_path/'account.json')
    _,_,capability=remote
    file=tmp_path/'trim.json';file.write_text(json.dumps(REQUEST))
    writes=[]
    def request(url,credential,**kwargs):
        if url.endswith('/capabilities'):return 200,capability
        writes.append(kwargs);return 0,{}
    monkeypatch.setattr(cloud,'_request',request)
    assert CliRunner().invoke(cli,['cloud','edits','apply',P,'--request',str(file)]).exit_code==1
    active=next((tmp_path/'cloud-edit-requests').rglob('*.json'));before=active.read_bytes()
    def fail(**kwargs):raise OSError('Synthetic full disk')
    if failure=='mkdir':monkeypatch.setattr(cloud_edits.tempfile,'mkdtemp',fail)
    else:
        original_open=cloud_edits.os.open
        def fail_parent(path,flags,*args,**kwargs):
            if Path(path)==active.parent:raise OSError('Synthetic parent sync failure')
            return original_open(path,flags,*args,**kwargs)
        monkeypatch.setattr(cloud_edits.os,'open',fail_parent)
    abandoned=CliRunner().invoke(cli,['cloud','edits','apply',P,'--abandon-pending'])
    assert abandoned.exit_code==1 and json.loads(abandoned.output)['error']['code']=='invalid_request'
    assert active.read_bytes()==before and len(writes)==1
    assert CliRunner().invoke(cli,['cloud','edits','apply',P,'--recover']).exit_code==1
    assert writes==[writes[0]]*2


def saved_mix_read(revision):
    controls={'gain_db':-2,'muted':False,'fade_in':0.1,'fade_out':0.2}
    return {'id':revision,'spec':{'audio_mix':{'source_audio':controls,'tracks':[]}},
            'resolved':{'audio_mix':{'source_audio':dict(controls),'tracks':[]},'track_count':0,
                        'routing':[{'source_id':'speaker'}],
                        'source_audio_availability':{'speaker':'stream_reported'},
                        'loudness':None,'loudness_status':'not_measured'}}


@pytest.mark.parametrize('damage',['missing_resolved','missing_mix','missing_muted','changed_gain','missing_routing','missing_availability','missing_track','bad_track_count','overflow_gain'])
def test_saved_read_never_confirms_incomplete_or_contradictory_audio(remote,damage):
    _,response,_=remote
    revision='00000000-0000-4000-8000-000000000099'
    value=saved_mix_read(revision)
    if damage=='missing_resolved':value.pop('resolved')
    elif damage=='missing_mix':value['resolved'].pop('audio_mix')
    elif damage=='missing_muted':value['resolved']['audio_mix']['source_audio'].pop('muted')
    elif damage=='changed_gain':value['resolved']['audio_mix']['source_audio']['gain_db']=-6
    elif damage=='missing_routing':value['resolved'].pop('routing')
    elif damage=='missing_availability':value['resolved'].pop('source_audio_availability')
    elif damage=='missing_track':value['spec']['audio_mix']['tracks']=[{'id':'music'}]
    elif damage=='bad_track_count':value['resolved']['track_count']=True
    elif damage=='overflow_gain':
        value['resolved']['audio_mix']['source_audio']['gain_db']=10**1000
        value['spec']['audio_mix']['source_audio']['gain_db']=10**1000
    response.update(status='opened',current_revision=revision,revision=value)
    result=CliRunner().invoke(cli,['cloud','edits','show',P])
    assert result.exit_code==1,result.output
    assert json.loads(result.output)['error']['code']=='invalid_response'


def test_saved_read_help_explains_audio_intent_and_unknowns():
    result=CliRunner().invoke(cli,['cloud','edits','show','--help'])
    text=' '.join(result.output.split())
    for term in ['source_audio','gain_db','muted','source_audio_availability','not measured','fade_in','fade_out','seconds','milliseconds','dependency_manifest','asset_id','reference_id']:
        assert term in text


@pytest.mark.parametrize('fault',['missing_candidate','wrong_gain','nonzero_effects','wrong_depth','wrong_base'])
def test_audio_validation_requires_matching_candidate_and_zero_effects(remote,tmp_path,fault):
    _,response,_=remote
    revision='00000000-0000-4000-8000-000000000099'
    request={'contract_version':'cloud-editing-v1','expected_base_revision':revision,'operation':{'type':'audio_source','gain_db':-6}}
    value=saved_mix_read(revision);value['spec']['revisions']=[{}];value['spec']['audio_mix']['source_audio']['gain_db']=-6;value['resolved']['audio_mix']['source_audio']['gain_db']=-6;value['resolved']['undo_remaining']=1
    response.pop('revision');response.update(status='would_apply',current_revision=revision,changes=True,current_undo_remaining=0,changed_fields=['audio_mix'],normalized_request=request,candidate={'spec':value['spec']},projected_resolved=value['resolved'],effects={'saved_revisions':0,'activity_events':0,'processing_jobs':0,'customer_usage':0})
    if fault=='missing_candidate':response.pop('candidate')
    if fault=='wrong_gain':response['candidate']['spec']['audio_mix']['source_audio']['gain_db']=-12
    if fault=='nonzero_effects':response['effects']['saved_revisions']=1
    if fault=='wrong_depth':response['projected_resolved']['undo_remaining']=0
    if fault=='wrong_base':response['normalized_request']={**request,'expected_base_revision':None}
    file=tmp_path/'audio.json';file.write_text(json.dumps(request))
    result=CliRunner().invoke(cli,['cloud','edits','validate',P,'--request',str(file)])
    assert result.exit_code==1,result.output
    assert json.loads(result.output)['error']['code']=='invalid_response'


def test_audio_validation_help_explains_candidate_origin_and_noop_status():
    text=' '.join(CliRunner().invoke(cli,['cloud','edits','validate','--help']).output.split())
    assert 'saved_candidate' in text and 'derived from saved state' in text
    assert 'changes:false' in text and 'no-op' in text


@pytest.mark.parametrize('depth',[49,50])
@pytest.mark.parametrize('change',[True,False])
def test_audio_projection_accepts_real_capped_engine_journal(remote,tmp_path,depth,change):
    from moviestar.spec import empty_spec,set_audio_mix,append_revision,REVISION_LIMIT
    from moviestar.saved_edit_validation import prepare_saved_edit
    asset='00000000-0000-4000-8000-000000000021'
    spec=empty_spec([{'id':'speaker','path':'asset:'+asset}])
    for i in range(depth):
        mix=json.loads(json.dumps(spec['audio_mix']));mix['source_audio']['gain_db']=-1 if i%2==0 else -2
        spec=append_revision(spec,set_audio_mix(spec,mix),'audio '+str(i))
    gain=-6 if change else spec['audio_mix']['source_audio']['gain_db']
    revision='00000000-0000-4000-8000-000000000099'
    request={'contract_version':'cloud-editing-v1','expected_base_revision':revision,'operation':{'type':'audio_source','gain_db':gain}}
    before=json.loads(json.dumps(spec))
    prepared=prepare_saved_edit([{'source_id':'speaker','asset_id':asset,'media':{'duration_ms':10000,'width':640,'height':360,'audio_codec':'aac'}}],spec,request['operation'])
    journal=prepared['candidate_spec']['revisions']
    assert len(journal)==min(REVISION_LIMIT,depth+int(change))
    assert spec==before
    if change:
        assert journal[:-1]==spec['revisions'][max(0,depth+1-REVISION_LIMIT):]
    else:
        assert journal==spec['revisions']
    _,response,_=remote;response.pop('revision');response.update(status='would_apply',current_revision=revision,changes=prepared['changes'],current_undo_remaining=prepared['current_undo_remaining'],changed_fields=prepared['changed_fields'],normalized_request=request,candidate={'spec':prepared['candidate_spec']},projected_resolved=prepared['projected_resolved'],effects={'saved_revisions':0,'activity_events':0,'processing_jobs':0,'customer_usage':0})
    file=tmp_path/'audio.json';file.write_text(json.dumps(request));result=CliRunner().invoke(cli,['cloud','edits','validate',P,'--request',str(file)])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)==response



def test_apply_help_names_separate_audio_enrollment_and_no_render():
    text=' '.join(CliRunner().invoke(cli,['cloud','edits','apply','--help']).output.split())
    assert 'audio_source' in text and 'enrollment' in text and 'without rendering' in text


def test_audio_save_confirmation_requires_matching_readback_not_just_status():
    from moviestar.cloud_edits import _confirmed_mutation
    revision='00000000-0000-4000-8000-000000000099'
    proposal={**REQUEST,'expected_base_revision':revision,'operation':{'type':'audio_source','gain_db':-6}}
    value=saved_mix_read(revision);value['spec']['audio_mix']['source_audio']['gain_db']=-6;value['resolved']['audio_mix']['source_audio']['gain_db']=-6
    body={'contract_version':'cloud-editing-v1','project_id':P,'revision':value,'current_revision':revision,'status':'committed','changes':True,'replayed':False,'idempotency_key':'audio','request_sha256':'a'*64,'canonical_format':'postgres-jsonb-text-v1','normalized_request':proposal,'verification':{'format':'cloud-edit-state-proof-1','saved_state':'read_back_match'},'effects':{'saved_revisions':1,'activity_events':1,'processing_jobs':0,'customer_usage':0}}
    assert _confirmed_mutation(body,P,'audio',proposal)
    value['resolved']['audio_mix']['source_audio']['gain_db']=0
    assert not _confirmed_mutation(body,P,'audio',proposal)
    value['spec']['audio_mix']['source_audio']['gain_db']=0
    assert not _confirmed_mutation(body,P,'audio',proposal)
