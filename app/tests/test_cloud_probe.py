"""Remote probing recovery uses synthetic metadata, never local media."""
import json
from click.testing import CliRunner
from moviestar.cli import cli
from moviestar import cloud as module

ASSET = '123e4567-e89b-42d3-a456-426614174001'
JOB = '123e4567-e89b-42d3-a456-426614174002'
TOKEN = 'mvs_' + 'a' * 43
RESULT = {'duration': {'seconds': 0.3, 'text': '0:00:00.300'}, 'file_size_bytes': 3509,
          'format': 'mp4', 'bitrate': 80, 'video': None, 'audio': None}

def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('MOVIESTAR_ACCOUNT_STATE', str(tmp_path / 'account.json'))
    monkeypatch.setattr(module, '_connection', lambda _: ('https://example.test/api/v1/account/cloud', TOKEN))

def invoke(*args):
    result = CliRunner().invoke(cli, ['cloud', 'probe', ASSET, *args])
    assert result.stdout, repr(result.exception)
    return result, json.loads(result.stdout)

def job(state='queued'):
    return {'id': JOB, 'asset_id': ASSET, 'state': state, 'attempt_number': 1,
            'created_at': '2026-01-01T00:00:00Z', 'finished_at': None,
            'failure_code': None, 'result': RESULT if state == 'succeeded' else None}

def test_lost_reply_reuses_durable_key(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    calls = []
    def request(url, credential, **kwargs):
        calls.append((url, credential, kwargs))
        return (0, {}) if len(calls) == 1 else (200, {'job': job('succeeded')})
    monkeypatch.setattr(module, '_request', request)
    first, body = invoke()
    assert first.exit_code == 1
    assert body['status'] == 'probe_unavailable'
    second, recovered = invoke()
    assert second.exit_code == 0
    assert recovered['job']['result'] == RESULT
    assert calls[0][2]['key'] == calls[1][2]['key'] == body['idempotency_key']
    assert calls[0][0] == f'https://example.test/api/v1/assets/{ASSET}/probe'
    assert TOKEN not in first.output + second.output

def test_resume_only_reads_and_never_queues(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    calls = []
    def request(url, credential, **kwargs):
        calls.append((url, kwargs))
        return 200, {'job': job('succeeded')}
    monkeypatch.setattr(module, '_request', request)
    result, body = invoke('--job-id', JOB)
    assert result.exit_code == 0
    assert body['job']['result'] == RESULT
    assert calls == [(f'https://example.test/api/v1/assets/{ASSET}/probes/{JOB}', {})]

def test_pending_no_wait_retry_and_failed_job(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    keys = []
    def request(url, credential, **kwargs):
        keys.append(kwargs['key'])
        return 202, {'job': job()}
    monkeypatch.setattr(module, '_request', request)
    assert invoke('--no-wait')[0].exit_code == 0
    assert invoke('--no-wait')[0].exit_code == 0
    assert keys[0] == keys[1]
    monkeypatch.setattr(module, '_request', lambda *a, **k: (200, {'job': {**job('failed'), 'failure_code': 'probe_failed'}}))
    result, body = invoke()
    assert result.exit_code == 1
    assert body['job']['state'] == 'failed'

def test_malformed_or_wrong_asset_preserves_recovery(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    keys = []
    def request(url, credential, **kwargs):
        keys.append(kwargs['key'])
        return 200, {'job': {**job('succeeded'), 'asset_id': JOB}}
    monkeypatch.setattr(module, '_request', request)
    assert invoke()[1]['status'] == 'invalid_response'
    monkeypatch.setattr(module, '_request', lambda *a, **k: (200, {'job': job('succeeded')}))
    result, body = invoke()
    assert result.exit_code == 0
    assert body['idempotency_key'] == keys[0]

def test_timeout_and_interrupt_keep_saved_request(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    keys = []
    def request(url, credential, **kwargs):
        if 'key' in kwargs: keys.append(kwargs['key'])
        return 202, {'job': job()}
    monkeypatch.setattr(module, '_request', request)
    result, body = invoke('--timeout', '0')
    assert result.exit_code == 0 and body['status'] == 'queued'
    assert '--job-id' in body['hint']
    def interrupted(*a, **kw): raise KeyboardInterrupt()
    monkeypatch.setattr(module, '_request', interrupted)
    result, body = invoke()
    assert result.exit_code == 1 and body['status'] == 'interrupted'
    assert body['idempotency_key'] == keys[0]


def test_wait_polls_same_job_until_ready(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    calls = []
    def request(url, credential, **kwargs):
        calls.append((url, kwargs))
        return (202, {'job': job()}) if len(calls) == 1 else (200, {'job': job('succeeded')})
    monkeypatch.setattr(module, '_request', request)
    monkeypatch.setattr('moviestar.cloud_probe.time.sleep', lambda _: None)
    result, body = invoke()
    assert result.exit_code == 0 and body['job']['state'] == 'succeeded'
    assert calls[1] == (f'https://example.test/api/v1/assets/{ASSET}/probes/{JOB}', {})


def test_untrusted_error_and_result_never_echo_private_details(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(module, '_request', lambda *a, **k: (503, {'error': {'code': TOKEN, 'message': TOKEN}}))
    result, body = invoke()
    assert result.exit_code == 1 and TOKEN not in result.output
    poisoned = {**RESULT, 'source': '/private/user/video.mp4'}
    monkeypatch.setattr(module, '_request', lambda *a, **k: (200, {'job': {**job('succeeded'), 'result': poisoned}}))
    result, body = invoke()
    assert body['status'] == 'invalid_response' and '/private/user' not in result.output


def test_oversized_json_integer_is_a_structured_invalid_response(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    malformed = {**RESULT, 'bitrate': 10**1000}
    monkeypatch.setattr(module, '_request', lambda *a, **k: (200, {'job': {**job('succeeded'), 'result': malformed}}))
    result, body = invoke()
    assert result.exit_code == 1
    assert body['status'] == 'invalid_response' and body['idempotency_key']

LOUDNESS = {'integrated_lufs': -18.2, 'true_peak_dbtp': -3.1, 'lra_lu': 1.2,
            'threshold_lufs': -28.2, 'target_offset_lu': 0.1,
            'normalization_target': {'integrated_lufs': -14, 'true_peak_dbtp': -1.5, 'lra_lu': 11},
            'range': {'from': {'seconds': 1, 'text': '0:00:01.000'},
                      'to': {'seconds': 2, 'text': '0:00:02.000'},
                      'duration': {'seconds': 1, 'text': '0:00:01.000'}}}

def test_remote_loudness_uses_same_job_contract_and_source_time_range(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    calls = []
    measured = {**RESULT, 'loudness': LOUDNESS}
    def request(url, credential, **kwargs):
        calls.append(kwargs)
        return 200, {'job': {**job('succeeded'), 'result': measured}}
    monkeypatch.setattr(module, '_request', request)
    result, body = invoke('--loudness', '--from', '00:01', '--to', '2')
    assert result.exit_code == 0
    assert calls[0]['payload'] == {'loudness': True, 'from_seconds': 1.0, 'to_seconds': 2.0}
    assert body['job']['result']['loudness'] == LOUDNESS

def test_loudness_retries_do_not_inherit_plain_probe_request_keys(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    calls = []
    def request(url, credential, **kwargs):
        calls.append(kwargs)
        return 0, {}
    monkeypatch.setattr(module, '_request', request)
    invoke()
    invoke('--loudness')
    invoke('--loudness')
    invoke()
    assert len(calls) == 4
    assert calls[0]['key'] == calls[3]['key']
    assert calls[1]['key'] == calls[2]['key'] != calls[0]['key']

def test_loudness_range_validation_happens_before_network(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(module, '_request', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not send')))
    for args in [('--from', '1'), ('--loudness', '--from', 'NaN'), ('--loudness', '--from', '3', '--to', '2')]:
        result, body = invoke(*args)
        assert result.exit_code == 1 and body['status'] == 'invalid_request'

def test_unmeasurable_loudness_is_null_and_reopens_without_compute(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    measured = {**RESULT, 'loudness': {**LOUDNESS, 'integrated_lufs': None, 'true_peak_dbtp': None}}
    monkeypatch.setattr(module, '_request', lambda *a, **k: (200, {'job': {**job('succeeded'), 'result': measured}}))
    result, body = invoke('--job-id', JOB)
    assert result.exit_code == 0 and body['job']['result']['loudness']['integrated_lufs'] is None
