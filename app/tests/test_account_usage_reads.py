"""Remote account usage and task history reuse the hosted read contracts."""
import json
from click.testing import CliRunner
import pytest
from moviestar.cli import cli
from moviestar import cloud

def usage_body():
 return {'usage':{'schema_version':1,'observed_at':'2030-01-12T12:00:00Z','plan':'free','period_start':'2030-01-01T00:00:00Z','resets_at':'2030-02-01T00:00:00Z',
  'limits':{'processing_ms':600000,'output_bytes':1000000000,'concurrent_jobs':1},
  'usage':{'processing_ms':0,'output_bytes':0,'reserved_processing_ms':0,'reserved_output_bytes':0,'executing_jobs':0},'remaining':{'processing_ms':600000,'output_bytes':1000000000},
  'storage':{'limits':{'stored_assets':100,'total_bytes':1000000000,'per_file_bytes':500000000},'usage':{'stored_assets':0,'used_assets':0,'stored_bytes':0,'used_bytes':0},'remaining':{'assets':100,'bytes':1000000000}}},**context()}
def context():
 return {'links':{'usage_url':'https://service.invalid/activity','activity_url':'https://service.invalid/activity?view=activity','manage_assets_url':'https://service.invalid/account/assets'},'support':{'email':'support@example.invalid','url':'mailto:support@example.invalid?subject=Limits'},'local_execution_free':True,'automatic_overage_charges':False}
@pytest.fixture
def remote(monkeypatch):
 calls=[]
 monkeypatch.setattr(cloud,'_connection',lambda command:('https://service.invalid/api/v1/account/cloud','mvs_'+'a'*43))
 body=usage_body()
 def request(url,credential,**kwargs):calls.append((url,kwargs));return 200,body
 monkeypatch.setattr(cloud,'_request',request)
 return calls,body

def invoke(*args):return CliRunner().invoke(cli,['account',*args])
def test_usage_and_activity_are_discoverable_and_read_only(remote):
 calls,body=remote
 group=invoke('--help');assert 'usage' in group.output and 'activity' in group.output
 help=invoke('usage','--help');assert help.exit_code==0 and 'local' in help.output.lower()
 result=invoke('usage');assert result.exit_code==0,result.output
 assert json.loads(result.output)==body
 assert calls==[('https://service.invalid/api/v1/account/usage',{})]
 body.clear();body.update(items=[],next_cursor=None,provider_costs_are_customer_charges=False,**context())
 activity=invoke('activity','--cloud','--limit','2')
 assert activity.exit_code==0,activity.output
 assert json.loads(activity.output)==body
 assert calls[-1]==('https://service.invalid/api/v1/account/activity/tasks?limit=2&type=cloud',{})

@pytest.mark.parametrize('change',[lambda b:b['usage'].pop('storage'),lambda b:b['usage']['usage'].update(processing_ms=True),lambda b:b['usage'].update(plan='enterprise'),lambda b:b['links'].update(usage_url='https://evil.invalid/')])
def test_malformed_response_is_unavailable_not_free_or_zero(remote,change):
 _,body=remote;change(body)
 result=invoke('usage');assert result.exit_code==1
 value=json.loads(result.output);assert value['error']['code']=='invalid_response';assert 'usage' not in value

def test_private_fields_are_not_echoed(remote):
 _,body=remote;body['credential']='private';body['usage']['owner_id']='private';body['usage']['storage']['storage_key']='private'
 result=invoke('usage');assert result.exit_code==0,result.output
 assert 'private' not in result.output

@pytest.mark.parametrize('status,code',[(401,'credential_invalid'),(403,'account_consent_required'),(503,'account_unavailable'),(0,'account_unavailable')])
def test_safe_error_recovery_uses_existing_connection_without_leaking_diagnostics(remote,monkeypatch,status,code):
 monkeypatch.setattr(cloud,'_request',lambda *a,**k:(status,{'error':{'code':code,'message':'private credential'}}))
 result=invoke('usage');assert result.exit_code==1
 payload=json.loads(result.output);assert payload['error']['code']==code;assert 'usage' not in payload;assert 'private credential' not in result.output

@pytest.mark.parametrize('args',[('activity','--cursor','bad cursor'),('activity','--limit','51')])
def test_invalid_pagination_never_reads_the_service(remote,args):
 calls,_=remote;assert invoke(*args).exit_code!=0;assert calls==[]


def test_tasks_keep_nested_attempts_pagination_and_exact_unknown_costs(remote):
    calls, body = remote
    entry = {
        "id": "10000000-0000-0000-0000-000000000001",
        "operation_id": "10000000-0000-0000-0000-000000000002",
        "kind": "asset_screenshot", "state": "succeeded",
        "created_at": "2030-01-12T12:00:00Z", "finished_at": "2030-01-12T12:00:01Z",
        "project_id": None, "asset_id": "10000000-0000-0000-0000-000000000003",
        "parent_operation_id": None, "elapsed_ms": 1000, "failure_code": None,
        "actual_cost_usd": None, "cost_receipts": 0, "historical": False,
        "cost_status": "pending",
    }
    attempt = {**entry, "id": "10000000-0000-0000-0000-000000000004",
        "kind": "asset_screenshot_attempt", "parent_operation_id": entry["operation_id"],
        "actual_cost_usd": "0.000123", "cost_receipts": 1,
        "provider_receipt": "private"}
    task = {**entry, "resource_name": "Example.mp4", "project_name": None,
        "attempt_count": 1, "attempts": [attempt], "cloud_usage": {
            "period_start": "2030-01-01T00:00:00Z", "state": "succeeded",
            "processing_ms": 1000, "output_bytes": 100,
            "reserved_processing_ms": 0, "reserved_output_bytes": 0},
        "responsible_account_id": "private"}
    estimate = {"status":"estimated","amount_usd":"0.002000000000","base_usd":"0.001000000000","reason":None,"scope":"compute","method":"attempt_occupancy_v1","policy_version":None,"adjustment_multiplier":None,"attempt_count":1}
    task["estimated_compute"] = {**estimate,"provider_resource":"private"}
    body.clear()
    body.update(items=[task], next_cursor="opaque_cursor", **context(),
                provider_costs_are_customer_charges=False)
    result = invoke("activity", "--cursor", "opaque_cursor", "--limit", "1")
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert "private" not in result.output
    assert value["items"][0]["actual_cost_usd"] is None
    assert value["items"][0]["attempts"][0]["actual_cost_usd"] == "0.000123"
    assert value["next_cursor"] == "opaque_cursor"
    assert value["items"][0]["estimated_compute"] == estimate
    assert calls[-1][0].endswith("?limit=1&cursor=opaque_cursor")
    body.pop("next_cursor")
    assert json.loads(invoke("activity").output)["error"]["code"] == "invalid_response"

@pytest.mark.parametrize('amount', ['NaN', None, '-1', '0.0000000000001'])
def test_invalid_estimates_are_unavailable_not_zero(amount):
    from moviestar.account_reads import _compute_estimate
    value = {'status':'estimated', 'amount_usd':amount, 'base_usd':'0.001',
             'reason':None, 'scope':'compute', 'method':'attempt_occupancy_v1',
             'policy_version':None, 'adjustment_multiplier':None}
    assert _compute_estimate(value) is False
