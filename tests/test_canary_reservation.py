"""Offline boundaries for live-CAS test. No provider or real database access."""
import copy
import datetime as dt
import json
import sys
import threading
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recovery_canary as c
import order_workflow as w

OID = 'AS-OX-CANARY-RESERVATION-TEST'


def synthetic():
    now = dt.datetime.now(dt.timezone.utc)
    return {'order_id': OID, 'status': 'source_received', 'payment_state': 'unverified',
            'payment_ref': None, 'brief': {'synthetic_acceptance': True}, 'delivery': {},
            'delivery_result': {}, 'token_sha256': 'b' * 64,
            'token_issued_at': (now - dt.timedelta(hours=2)).isoformat(),
            'expires_at': (now - dt.timedelta(hours=1)).isoformat()}


class ReservationScopeTests(unittest.TestCase):
    def fixture(self, row=None):
        row = row or synthetic()
        lock = threading.Lock()
        calls = []
        def read(_):
            with lock:
                return copy.deepcopy(row['delivery_result'])
        def inspect(_):
            with lock:
                return copy.deepcopy(row), [{'object_path': OID + '/test.png', 'status': 'ready', 'sha256': 'c' * 64}]
        def reserve(oid, attempt):
            with lock:
                if row['delivery_result']:
                    raise w.GateError('notification_reservation_unverified')
                row['delivery_result'] = {'order_id': oid, 'delivery_status': 'sending', 'attempt_id': attempt}
                return copy.deepcopy(row['delivery_result'])
        def request(table, params=None, method='GET', body=None):
            calls.append((table, params, method, body))
            with lock:
                self.assertEqual(table, 'anthemsmith_recovery_orders')
                self.assertEqual(params['order_id'], 'eq.' + OID)
                self.assertEqual(params['payment_state'], 'eq.unverified')
                self.assertEqual(params['payment_ref'], 'is.null')
                self.assertEqual(params['delivery'], 'eq.{}')
                self.assertEqual(params['delivery_result'], 'eq.' + json.dumps(row['delivery_result'], separators=(',', ':')))
                self.assertEqual(body, {'delivery_result': {}})
                row.update(copy.deepcopy(body))
                return [copy.deepcopy(row)]
        return row, calls, read, inspect, reserve, request

    def test_only_revoked_recipient_free_canary_races_and_restores_exact_row(self):
        row, calls, read, inspect, reserve, request = self.fixture()
        before = copy.deepcopy(row)
        with patch.object(c, 'inspect', side_effect=inspect), patch.object(c, 'request', side_effect=request), \
             patch.object(w, 'deliver', side_effect=AssertionError('no sender')):
            result = c.reservation_test(OID, reserve=reserve, read_receipt=read)
        self.assertEqual(row, before)
        self.assertEqual((result['winners'], result['losers']), (1, 1))
        self.assertEqual(len(calls), 1)
        for flag in ('notification_sent', 'music_generated', 'model_called'):
            self.assertFalse(result[flag])
        self.assertTrue(result['cleanup_readback'])

    def test_customer_namespace_refused_before_import_or_db(self):
        with patch.object(c, 'inspect', side_effect=AssertionError('no customer reads')):
            with self.assertRaisesRegex(c.CanaryError, 'synthetic_namespace_required'):
                c.reservation_test('AS-MUIJ11B6')

    def test_live_capability_refused_before_cas(self):
        row = synthetic()
        row['expires_at'] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
        with patch.object(c, 'inspect', return_value=(row, [])):
            with self.assertRaisesRegex(c.CanaryError, 'canary_must_be_revoked'):
                c.reservation_test(OID, reserve=Mock(side_effect=AssertionError('no CAS')))

    def test_existing_reservation_is_never_cleared(self):
        row = synthetic()
        row['delivery_result'] = {'delivery_status': 'sending', 'attempt_id': 'foreign'}
        with patch.object(c, 'inspect', return_value=(row, [])), \
             patch.object(c, 'request', side_effect=AssertionError('no clear')):
            with self.assertRaisesRegex(c.CanaryError, 'canary_outbox_not_empty'):
                c.reservation_test(OID)

    def test_foreign_winning_write_is_not_cleared(self):
        row, calls, read, inspect, reserve, request = self.fixture()
        def rogue(oid, attempt):
            with threading.Lock():
                row['delivery_result'] = {'order_id': oid, 'delivery_status': 'sending', 'attempt_id': 'foreign'}
            raise w.GateError('notification_reservation_unverified')
        with patch.object(c, 'inspect', side_effect=inspect), patch.object(c, 'request', side_effect=request):
            with self.assertRaisesRegex(c.CanaryError, 'foreign_reservation_do_not_clear'):
                c.reservation_test(OID, reserve=rogue, read_receipt=read)
        self.assertEqual(calls, [])
        self.assertEqual(row['delivery_result']['attempt_id'], 'foreign')

    def test_two_false_successes_fail_not_mark_canary_passed(self):
        row, calls, read, inspect, reserve, request = self.fixture()
        def false_success(oid, attempt):
            return {'order_id': oid, 'delivery_status': 'sending', 'attempt_id': attempt}
        with patch.object(c, 'inspect', side_effect=inspect), patch.object(c, 'request', side_effect=request):
            with self.assertRaisesRegex(c.CanaryError, 'reservation_winner_count_mismatch'):
                c.reservation_test(OID, reserve=false_success, read_receipt=read)
        self.assertEqual(calls, [])

    def test_context_change_fails_closed_instead_of_cleanup(self):
        row, calls, read, inspect, reserve, request = self.fixture()
        def changed(oid, attempt):
            row['brief']['external_change'] = True
            raise w.GateError('notification_reservation_unverified')
        with patch.object(c, 'inspect', side_effect=inspect), patch.object(c, 'request', side_effect=request):
            with self.assertRaisesRegex(c.CanaryError, 'canary_context_changed_do_not_clear'):
                c.reservation_test(OID, reserve=changed, read_receipt=read)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
