import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from click.testing import CliRunner
import surface as s


class SurfaceTests(unittest.TestCase):
    def call(self, surface='cli', scenario='ready', command='screenshot', extra=None):
        args = ['--surface', surface, '--scenario', scenario, command, s.PROJECT, s.ARTIFACT]
        args += ['--at-ms', '10000', '--idempotency-key', 'result-frame-10s'] if command == 'screenshot' else [s.JOB]
        result = CliRunner().invoke(s.app, args + (extra or []))
        return result, json.loads(result.output)

    def test_api_cli_mcp_share_the_complete_canonical_resource(self):
        values = [self.call(surface)[1] for surface in ['api', 'cli', 'mcp']]
        self.assertEqual(values[0]['response'], values[1]['response'])
        self.assertEqual(values[0]['response'], values[2]['response'])
        for v in values:
            self.assertTrue(v['mock'])
            self.assertFalse(v['file_saved'])
            self.assertEqual(v['effects'], {'jobs_created': 0, 'usage_reserved': 0, 'edit_writes': 0})
            self.assertEqual(v['http_status'], 202)

    def test_exact_render_binding_and_real_fixture_digest(self):
        _, v = self.call(command='screenshot-show')
        j = v['response']['job']
        self.assertEqual([j[k] for k in ['project_id', 'artifact_id', 'revision_id', 'render_job_id']], [s.PROJECT, s.ARTIFACT, s.REVISION, s.RENDER])
        self.assertEqual(j['clock'], 'rendered_result')
        self.assertNotIn('source_id', j)
        self.assertEqual(j['result']['image']['sha256'], hashlib.sha256(s.IMAGE.read_bytes()).hexdigest())
        self.assertEqual(j['result']['image']['size_bytes'], len(s.IMAGE.read_bytes()))

    def test_recovery_recovers_original_job_without_a_new_request(self):
        _, original = self.call()
        _, replay = self.call(scenario='replay')
        self.assertEqual(original['response']['job']['id'], replay['response']['job']['id'])
        self.assertTrue(replay['response']['replayed'])
        self.assertEqual(replay['response']['job']['state'], 'succeeded')

    def test_unknown_reply_does_not_claim_zero_effects(self):
        r, v = self.call(scenario='lost-response')
        self.assertEqual(r.exit_code, 1)
        self.assertIsNone(v['effects'])
        self.assertEqual(v['response']['error']['code'], 'submission_unconfirmed')
        self.assertEqual(v['response']['error']['details']['recovery']['idempotency_key'], 'result-frame-10s')

    def test_missing_foreign_and_revoked_have_identical_errors(self):
        responses = [self.call(scenario=n)[1] for n in ['missing', 'foreign', 'revoked']]
        self.assertTrue(all(v['http_status'] == 404 for v in responses))
        self.assertEqual(responses[0]['response'], responses[1]['response'])
        self.assertEqual(responses[0]['response'], responses[2]['response'])

    def test_disabled_new_requests_keep_reads_available(self):
        _, v = self.call(scenario='disabled')
        self.assertEqual(v['http_status'], 503)
        self.assertTrue(v['response']['error']['retryable'])
        self.assertEqual(self.call(scenario='disabled', command='screenshot-show')[1]['http_status'], 200)

    def test_changed_intent_conflicts_in_canned_conflict_scenario(self):
        _, v = self.call(scenario='conflict')
        self.assertEqual(v['http_status'], 409)
        self.assertEqual(v['response']['error']['code'], 'idempotency_conflict')

    def test_invalid_keys_and_valid_boundaries(self):
        for key in ['', 'a'*129, 'unicode-é', 'space key', 'line\nbreak']:
            _, v = self.call(extra=['--idempotency-key', key])
            self.assertEqual(v['http_status'], 400)
        for key in ['a', 'a'*128]:
            self.assertEqual(self.call(extra=['--idempotency-key', key])[0].exit_code, 0)

    def test_range_is_result_time_and_excludes_artifact_end(self):
        for at in ['-1', '30000']:
            _, v = self.call(extra=['--at-ms', at])
            self.assertEqual(v['http_status'], 422)
        for at, decoded in [('0', 0), ('10017', 10000), ('29999', 29967)]:
            _, v = self.call(scenario='replay', extra=['--at-ms', at])
            self.assertEqual(v['response']['job']['result']['requested_at_ms'], int(at))
            self.assertEqual(v['response']['job']['result']['at_ms'], decoded)

    def test_fractional_milliseconds_have_a_canonical_error(self):
        _, v = self.call(extra=['--at-ms', '10000.5'])
        self.assertEqual(v['response']['error']['code'], 'invalid_request')
        self.assertIn('integer', v['response']['error']['message'])

    def test_file_saved_only_after_actual_fixture_copy_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)/'frame.jpg'
            _, v = self.call(command='screenshot-show', extra=['--out', str(out)])
            self.assertTrue(v['file_saved'])
            self.assertEqual(out.read_bytes(), s.IMAGE.read_bytes())
            self.assertEqual(v['out'], str(out.resolve()))
            _, v = self.call(command='screenshot-show', extra=['--out', str(out)])
            self.assertEqual(v['response']['error']['code'], 'output_exists')
            self.assertEqual(out.read_bytes(), s.IMAGE.read_bytes())

    def test_mcp_models_actual_content_blocks_without_claiming_perception(self):
        import base64
        _, v = self.call(surface='mcp', command='screenshot-show')
        blocks = v['mcp_result']['content']
        self.assertEqual(v['mcp_result']['structuredContent'], v['response'])
        image = next(c for c in blocks if c['type'] == 'image')
        self.assertEqual(base64.b64decode(image['data']), s.IMAGE.read_bytes())
        self.assertFalse(v['proposed_image_delivery']['native_perception_verified'])
        _, error = self.call(surface='mcp', scenario='foreign')
        self.assertTrue(error['mcp_result']['isError'])
        self.assertEqual(error['mcp_result']['structuredContent'], error['response'])

    def test_non_aligned_request_reopens_the_exact_original_sample(self):
        _, request = self.call(scenario='replay', extra=['--at-ms', '10017'])
        j = request['response']['job']
        self.assertNotEqual(j['id'], s.JOB)
        result = CliRunner().invoke(s.app, ['--surface', 'api', 'screenshot-show', s.PROJECT, s.ARTIFACT, j['id']])
        reopened = json.loads(result.output)['response']['job']
        self.assertEqual(reopened, j)
        self.assertEqual(reopened['result']['at_ms'], 10000)
        self.assertEqual(reopened['result']['requested_at_ms'], 10017)

    def test_failed_job_file_request_preserves_terminal_failure(self):
        with tempfile.TemporaryDirectory() as d:
            _, v = self.call(scenario='failed', command='screenshot-show', extra=['--out', str(Path(d)/'failed.jpg')])
            self.assertEqual(v['response']['error']['code'], 'frame_extraction_failed')
            self.assertNotIn('Poll', v['response']['error']['message'])
            self.assertFalse(v['response']['error']['retryable'])
            self.assertFalse((Path(d)/'failed.jpg').exists())

    def test_pending_and_failed_never_offer_a_native_image(self):
        for state in ['pending', 'failed']:
            _, v = self.call(surface='mcp', scenario=state, command='screenshot-show')
            self.assertIsNone(v['response']['job']['result'])
            self.assertNotIn('proposed_image_delivery', v)


if __name__ == '__main__':
    unittest.main()
