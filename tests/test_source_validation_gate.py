"""Separate source validation from paid generation; no fake payment state."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import photo_intake as pi

OID = 'AS-OX-CANARY-GATE-TEST'
PATH = OID + '/12345678-1234-4123-8123-123456789abc.png'

def transport(state, source_status='source_received'):
    def http(req, timeout, limit):
        if 'anthemsmith_recovery_orders?' in req.full_url:
            return json.dumps([{'order_id': OID, 'status': source_status, 'payment_state': state}]).encode(), {}
        if 'anthemsmith_recovery_uploads?' in req.full_url:
            return json.dumps([{'order_id': OID, 'status': 'ready', 'object_path': PATH}]).encode(), {}
        raise AssertionError('unexpected endpoint')
    return http

class SourceGateTests(unittest.TestCase):
    def test_paid_default_rejects_unverified(self):
        with self.assertRaisesRegex(pi.PhotoIntakeError, 'order_not_verified_paid_with_sources'):
            pi._verified_sources(transport('unverified'), 'https://fixture.supabase.co', 'fixture', OID, [PATH])
    def test_preflight_allows_unpaid_owned_submitted_sources(self):
        result = pi._verified_sources(transport('unverified'), 'https://fixture.supabase.co', 'fixture', OID, [PATH], require_paid=False)
        self.assertEqual(list(result), [PATH])
    def test_refunds_cannot_run_either_lane(self):
        for paid in [True, False]:
            with self.assertRaises(pi.PhotoIntakeError):
                pi._verified_sources(transport('refunded'), 'https://fixture.supabase.co', 'fixture', OID, [PATH], require_paid=paid)
    def test_preflight_requires_submitted_source(self):
        with self.assertRaises(pi.PhotoIntakeError):
            pi._verified_sources(transport('unverified','awaiting_sources'), 'https://fixture.supabase.co', 'fixture', OID, [PATH], require_paid=False)
    def test_paid_entrypoint_hardcodes_paid_gate(self):
        args = ([PATH], OID, 'https://fixture.supabase.co', 'fixture-store', 'fixture-model')
        with patch.object(pi, '_intake_photos', return_value='paid') as work:
            self.assertEqual(pi.intake_photos(*args), 'paid')
        self.assertIs(work.call_args.args[-1], True)
    def test_preflight_entrypoint_does_not_write_payment(self):
        args = ([PATH], OID, 'https://fixture.supabase.co', 'fixture-store', 'fixture-model')
        with patch.object(pi, '_intake_photos', return_value='source-only') as work:
            self.assertEqual(pi.inspect_private_sources(*args), 'source-only')
        self.assertIs(work.call_args.args[-1], False)

if __name__ == '__main__':
    unittest.main()
