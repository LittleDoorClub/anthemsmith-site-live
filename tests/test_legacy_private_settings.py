"""Recovery operator actions keep customer identifiers out of public output."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import recovery_restore_original as original
import recovery_order_state as state
from test_restore_original import fixture

ORDER = 'AS-FIXTURE-ORIGINAL'
REFERENCE = '7' * 19
SETTINGS = {
    'ANTHEMSMITH_LEGACY_ORDER_ID': ORDER,
    'ANTHEMSMITH_LEGACY_PAYMENT_REF': REFERENCE,
    'SUPABASE_SERVICE_ROLE_KEY': 'offline-fixture',
}


def bind():
    runpy.run_path(str(SCRIPTS / 'bind_payment_one_shot.py'), run_name='__main__')


class PrivateSettingsTests(unittest.TestCase):
    def test_invalid_or_missing_private_setting_fails_before_any_request(self):
        for name, bad in [('ANTHEMSMITH_LEGACY_ORDER_ID', ''), ('ANTHEMSMITH_LEGACY_PAYMENT_REF', ''),
                          ('ANTHEMSMITH_LEGACY_ORDER_ID', 'bad-format'), ('ANTHEMSMITH_LEGACY_PAYMENT_REF', 'bad-format')]:
            with self.subTest(name=name, bad=bad), patch.dict(os.environ, {**SETTINGS, name: bad}, clear=True), \
                 patch('urllib.request.urlopen') as http, self.assertRaises(SystemExit):
                try:
                    bind()
                finally:
                    http.assert_not_called()

    def test_payment_bind_is_guarded_and_logs_only_the_outcome(self):
        calls = []
        row = {'order_id': ORDER, 'payment_state': 'unverified', 'payment_ref': None}

        def http(req, timeout):
            calls.append(req.method if req.data is not None else 'GET')
            if req.data is not None:
                self.assertIn('payment_state=eq.unverified&payment_ref=is.null', req.full_url)
                row.update(json.loads(req.data))
            return io.BytesIO(json.dumps([row]).encode())

        stdout = io.StringIO()
        with patch.dict(os.environ, SETTINGS, clear=True), patch('urllib.request.urlopen', http), contextlib.redirect_stdout(stdout):
            bind()
            with self.assertRaises(SystemExit) as again:
                bind()
        self.assertEqual(again.exception.code, 0)
        self.assertEqual(calls, ['GET', 'PATCH', 'GET', 'GET'])
        self.assertEqual(row['payment_ref'], REFERENCE)
        self.assertNotIn(ORDER, stdout.getvalue())
        self.assertNotIn(REFERENCE, stdout.getvalue())

    def test_restore_print_and_artifact_have_no_source_identifiers_or_private_fields(self):
        env = {**SETTINGS, 'ANTHEMSMITH_LEGACY_RUN_ID': '41000311', 'ANTHEMSMITH_LEGACY_JOB_ID': '42000312',
               'ANTHEMSMITH_LEGACY_CONTACT_SUFFIX': '0104'}
        row = {'order_id': ORDER, 'status': 'awaiting_sources', 'payment_state': 'unverified',
               'payment_ref': None, 'brief': {}, 'delivery': {}}

        def db(query, body=None):
            if body:
                row.update(body)
            return [row.copy()]

        raw = fixture()
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp), patch.dict(os.environ, env, clear=True), \
             patch.object(original, 'original_log', return_value=raw), patch.object(original, 'restore', wraps=lambda raw: restore(raw, db=db)), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            original.main()
            result = json.loads(Path('restore-evidence.json').read_text())
            self.assertEqual(json.loads(out.getvalue()), result)
            self.assertEqual(set(result), {'result', 'payment_state', 'consent_verified', 'contact_restored',
                                          'token_issued', 'sent', 'music_generated'})
            for private in [ORDER, REFERENCE, env['ANTHEMSMITH_LEGACY_RUN_ID'], env['ANTHEMSMITH_LEGACY_JOB_ID'],
                            row['delivery']['sms_to'], row['brief']['mood']]:
                self.assertNotIn(private, out.getvalue())

    def test_manual_public_state_input_accepts_only_synthetic_order_ids(self):
        with patch.dict(os.environ, {'TEST_ORDER_ID': ORDER}, clear=True), patch.object(state, 'get') as fetch:
            with self.assertRaises(SystemExit):
                state.main()
            fetch.assert_not_called()


restore = original.restore
