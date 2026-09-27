"""Offline canary scope/idempotence guards. No network, charges or notifications."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('canary', Path(__file__).resolve().parents[1] / 'scripts/recovery_canary.py')
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)
OID = 'AS-OX-CANARY-TEST-20260927'
SHA = 'a' * 64


class ScopeTests(unittest.TestCase):
    def test_real_customer_and_arbitrary_ids_rejected_before_network(self):
        for oid in ['AS-MUIJ11B6', 'AS-WHALE-NORMAL-20260926', '../AS-OX-CANARY-TEST', 'AS-OX-CANARY-x']:
            with tempfile.TemporaryDirectory() as tmp:
                event = Path(tmp) / 'event.json'
                event.write_text(json.dumps({'inputs': {'action': 'seed', 'order_id': oid, 'token_sha256': SHA}}))
                with patch.dict(os.environ, {'GITHUB_EVENT_PATH': str(event)}), patch.object(c, 'request', side_effect=AssertionError('network')):
                    with self.assertRaisesRegex(c.CanaryError, 'synthetic_namespace_required'):
                        c.main()

    def test_seed_persists_hash_only_unverified_and_no_recipient(self):
        db = []
        def rest(table, params=None, method='GET', body=None):
            if method == 'POST':
                db.append(body)
            return list(db)
        with patch.object(c, 'request', side_effect=rest):
            result = c.seed(OID, SHA)
            again = c.seed(OID, SHA)
        self.assertEqual(len(db), 1)
        self.assertEqual(db[0]['token_sha256'], SHA)
        self.assertEqual(db[0]['payment_state'], 'unverified')
        self.assertIsNone(db[0]['payment_ref'])
        self.assertEqual(db[0]['delivery'], {})
        self.assertTrue(result['readback'])
        self.assertTrue(again['idempotent'])
        self.assertNotIn('token', db[0])

    def test_existing_paid_order_cannot_be_used_as_canary(self):
        row = {'order_id': OID, 'payment_state': 'verified_paid', 'payment_ref': 'provider-id'}
        with patch.object(c, 'request', return_value=[row]):
            with self.assertRaisesRegex(c.CanaryError, 'canary_must_not_be_paid'):
                c.order(OID)

    def test_seed_refuses_to_overwrite_existing_capability(self):
        row = {'order_id': OID, 'payment_state': 'unverified', 'payment_ref': None,
               'token_sha256': 'b' * 64, 'brief': {'synthetic_acceptance': True}, 'delivery': {}}
        with patch.object(c, 'request', return_value=[row]) as req:
            with self.assertRaisesRegex(c.CanaryError, 'existing_canary_token_mismatch'):
                c.seed(OID, SHA)
            self.assertTrue(all(call.kwargs.get('method', 'GET') == 'GET' for call in req.call_args_list))

    def test_live_analysis_requires_customer_submit_and_ready_files(self):
        with patch.object(c, 'inspect', return_value=({'status': 'awaiting_sources'}, [])):
            with self.assertRaisesRegex(c.CanaryError, 'canary_sources_not_ready'):
                c.analyze(OID)

    def test_table_scope_cannot_expand(self):
        with self.assertRaisesRegex(c.CanaryError, 'table_not_allowed'):
            c.request('vault.decrypted_secrets')

    def test_live_analysis_calls_real_intake_function_and_verifies_private_provenance(self):
        import sys
        from types import SimpleNamespace
        script_dir = str(Path(c.__file__).resolve().parent)
        sys.path.insert(0, script_dir)
        import photo_intake
        row = {'order_id': OID, 'status': 'source_received'}
        upload = {'order_id': OID, 'status': 'ready', 'object_path': OID + '/fixture.png', 'sha256': 'b' * 64}
        analysis = {'bytes': 70, 'width': 4, 'height': 5,
                    'result': {'observations': ['synthetic red circle'], 'visible_text': []}}
        persisted = {'id': 'fixture-analysis', 'source_sha256': 'b' * 64,
                     'object_path': upload['object_path'], 'provider_request_id': 'fixture-request',
                     'model': 'fixture-model', 'analysis': analysis}
        with patch.object(c, 'inspect', return_value=(row, [upload])), \
             patch.object(c, 'request', return_value=[persisted]), \
             patch.object(c, 'order', return_value=row), \
             patch.object(photo_intake, 'inspect_private_sources', return_value=SimpleNamespace(analysis=[analysis])) as intake, \
             patch.dict(os.environ, {'SUPABASE_SERVICE_ROLE_KEY': 'fixture-key', 'OPENAI_API_KEY': 'fixture-vision'}):
            result = c.analyze(OID)
        intake.assert_called_once_with([upload['object_path']], OID, c.ORIGIN, 'fixture-key', 'fixture-vision')
        self.assertEqual(result['stored_images_examined'], 1)
        self.assertTrue(result['readback'])
        self.assertFalse(result['music_generated'])

    def test_revocation_preserves_payment_and_source_rows(self):
        row = {'order_id': OID, 'payment_state': 'unverified', 'payment_ref': None,
               'brief': {'synthetic_acceptance': True}, 'delivery': {},
               'token_issued_at': (dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=5)).isoformat()}
        updates = []
        def rest(table, params=None, method='GET', body=None):
            if method == 'PATCH':
                updates.append(body)
                row.update(body)
            return [dict(row)]
        with patch.object(c, 'request', side_effect=rest):
            result = c.revoke(OID)
        self.assertTrue(result['revoked'])
        self.assertEqual(list(updates[0]), ['expires_at'])


if __name__ == '__main__':
    unittest.main()
