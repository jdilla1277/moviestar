"""Contract rehearsal only: no server, credentials or rendered media."""
import json
import importlib.util
from pathlib import Path

from click.testing import CliRunner
spec = importlib.util.spec_from_file_location('delivery_surface', Path(__file__).parents[2]/'examples/cloud_delivery_mock.py')
surface = importlib.util.module_from_spec(spec)
spec.loader.exec_module(surface)
cli = surface.cli

P = '00000000-0000-4000-8000-000000000001'
R = '00000000-0000-4000-8000-000000000101'


def call(tmp_path, *args):
    result = CliRunner().invoke(cli, ['--state', str(tmp_path/'mock.json'), *args])
    return result, json.loads(result.output)


def submit(tmp_path, *extra):
    return call(tmp_path, 'renders', 'submit', P, '--revision', R,
                '--kind', 'preview', '--idempotency-key', 'retained-key', *extra)


def test_lost_reply_reconnect_and_changed_payload(tmp_path):
    first, unknown = submit(tmp_path, '--lose-reply')
    assert first.exit_code == 1
    assert unknown['error']['code'] == 'submission_unconfirmed'
    assert unknown['idempotency_key'] == 'retained-key'
    result, replay = submit(tmp_path)
    assert result.exit_code == 0 and replay['replayed']
    job = replay['job']
    assert job['revision_id'] == R and job['state'] == 'queued'
    result, conflict = call(tmp_path, 'renders', 'submit', P, '--revision', R,
                            '--kind', 'final', '--idempotency-key', 'retained-key')
    assert result.exit_code == 1 and conflict['error']['code'] == 'idempotency_conflict'
    _, reopened = call(tmp_path, 'renders', 'show', P, job['id'])
    assert reopened['job'] == job


def test_reads_download_and_cancel_do_not_create_work(tmp_path):
    _, accepted = submit(tmp_path)
    job_id = accepted['job']['id']
    _, completed = call(tmp_path, 'fixture', 'complete', P, job_id)
    artifact = completed['artifact']
    before = (tmp_path/'mock.json').read_bytes()
    for args in [('renders', 'show', P, job_id), ('artifacts', 'list', P),
                 ('artifacts', 'show', P, artifact['id']),
                 ('artifacts', 'download', P, artifact['id'], '--out', str(tmp_path/'final.mp4'))]:
        result, body = call(tmp_path, *args)
        assert result.exit_code == 0, body
        assert body['mock'] and body['media_delivered'] is False
        assert (tmp_path/'mock.json').read_bytes() == before
    assert not (tmp_path/'final.mp4').exists()
    _, cancelled = call(tmp_path, 'renders', 'cancel', P, job_id)
    assert cancelled['job']['state'] == 'succeeded'
    assert len(json.loads((tmp_path/'mock.json').read_text())['jobs']) == 1


def test_current_access_before_replay_and_artifact_reads(tmp_path):
    _, accepted = submit(tmp_path)
    _, completed = call(tmp_path, 'fixture', 'complete', P, accepted['job']['id'])
    for actor in ('foreign', 'revoked'):
        result, denied = call(tmp_path, '--actor', actor, 'renders', 'submit', P,
                              '--revision', R, '--kind', 'preview', '--idempotency-key', 'retained-key')
        assert result.exit_code == 1
        assert denied['error']['code'] in ('not_found', 'credential_invalid')
        result, _ = call(tmp_path, '--actor', actor, 'artifacts', 'show', P, completed['artifact']['id'])
        assert result.exit_code == 1


def test_fixture_success_and_expiry_are_not_execution_or_download(tmp_path):
    _, accepted = submit(tmp_path)
    _, completed = call(tmp_path, 'fixture', 'complete', P, accepted['job']['id'])
    artifact_id = completed['artifact']['id']
    _, evidence = call(tmp_path, 'review', P, artifact_id, '--at-ms', '500')
    assert evidence['review_target']['artifact_id'] == artifact_id
    assert evidence['review_target']['sha256'] == completed['artifact']['sha256']
    assert evidence['review_recorded'] is False
    call(tmp_path, 'fixture', 'expire', P, artifact_id)
    result, expired = call(tmp_path, 'artifacts', 'download', P, artifact_id, '--out', 'never.mp4')
    assert result.exit_code == 1 and expired['error']['code'] == 'artifact_unavailable'
    _, reopened = call(tmp_path, 'renders', 'show', P, accepted['job']['id'])
    assert reopened['job']['state'] == 'succeeded'
    assert reopened['job']['artifact_id'] == artifact_id


def test_cancelled_jobs_cannot_publish_fixture_results(tmp_path):
    _, accepted = submit(tmp_path)
    _, cancelled = call(tmp_path, 'renders', 'cancel', P, accepted['job']['id'])
    assert cancelled['job']['state'] == 'cancelled'
    result, body = call(tmp_path, 'fixture', 'complete', P, accepted['job']['id'])
    assert result.exit_code == 1 and body['error']['code'] == 'terminal_job'


def test_capabilities_identify_unimplemented_surfaces(tmp_path):
    result, body = call(tmp_path, 'capabilities')
    assert result.exit_code == 0
    assert body['render_submission_available'] is False
    assert body['composition_inspection_available'] is False
    assert body['clip_batch_available'] is False


def test_final_is_distinct_and_terminal_hints_lead_to_download(tmp_path):
    _, accepted = submit(tmp_path)
    _, preview = call(tmp_path, 'fixture', 'complete', P, accepted['job']['id'])
    _, read = call(tmp_path, 'renders', 'show', P, accepted['job']['id'])
    assert preview['artifact']['id'] in read['hint'] and 'artifacts download' in read['hint']
    _, accepted_final = call(tmp_path, 'renders', 'submit', P, '--revision', R,
                             '--kind', 'final', '--idempotency-key', 'final-key')
    _, final = call(tmp_path, 'fixture', 'complete', P, accepted_final['job']['id'])
    for field in ('id', 'sha256', 'output_profile_sha256'):
        assert preview['artifact'][field] != final['artifact'][field]
    _, reviewed = call(tmp_path, 'review', P, final['artifact']['id'], '--at-ms', '0')
    assert 'id' not in reviewed['review_target']


def test_execution_key_includes_project_in_payload_not_scope(tmp_path):
    submit(tmp_path)
    result, conflict = call(tmp_path, 'renders', 'submit', '00000000-0000-4000-8000-000000000002',
                            '--revision', R, '--kind', 'preview', '--idempotency-key', 'retained-key')
    assert result.exit_code == 1 and conflict['error']['code'] == 'idempotency_conflict'


def test_canonical_api_mcp_fixture_identity(tmp_path):
    _, preview = call(tmp_path, 'renders', 'submit', P, '--revision', R,
                       '--kind', 'preview', '--idempotency-key', 'preview-retained-key')
    assert preview['job']['id'] == 'e5bc3231-a4cd-543b-b092-3b078e5ba247'
    _, completed = call(tmp_path, 'fixture', 'complete', P, preview['job']['id'])
    assert completed['artifact']['id'] == '3efce716-003f-5846-9350-11cc8da7c827'
    _, final = call(tmp_path, 'renders', 'submit', P, '--revision', '00000000-0000-4000-8000-000000000102',
                     '--kind', 'final', '--idempotency-key', 'final-retained-key')
    assert final['job']['id'] == 'e8d04da3-4b1a-537a-ac1f-e51549d6b53a'
    _, completed = call(tmp_path, 'fixture', 'complete', P, final['job']['id'])
    assert completed['artifact']['id'] == '0a458ea4-309b-527e-a884-df2dad782a51'
