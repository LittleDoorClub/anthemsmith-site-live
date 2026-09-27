"""Private outbox and provider transport must fail closed, before any send."""
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import order_workflow as w
import notification_provider as p
OID='AS-CAS-TEST'
class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self,*a): self.close()
class ReservationTests(unittest.TestCase):
    def test_reserve_matches_empty_json_and_reads_back_exact_attempt(self):
        proposed={'order_id':OID,'delivery_status':'sending','attempt_id':'fixture-attempt'}
        calls=[]
        def http(req,**kwargs):
            calls.append(req)
            return Response(json.dumps([{'order_id':OID,'delivery_result':proposed}]).encode())
        with patch.dict(os.environ,{'SUPABASE_SERVICE_ROLE_KEY':'fixture-only'}), patch.object(w,'store_open',side_effect=http), patch.object(w,'private_delivery',return_value=proposed):
            self.assertEqual(w.reserve_notification(OID,'fixture-attempt'),proposed)
        self.assertIn('delivery_result=eq.%7B%7D',calls[0].full_url)
        self.assertEqual(calls[0].method,'PATCH')
    def test_cas_loser_cannot_send(self):
        with patch.dict(os.environ,{'SUPABASE_SERVICE_ROLE_KEY':'fixture-only'}), patch.object(w,'store_open',return_value=Response(b'[]')):
            with self.assertRaisesRegex(w.GateError,'reservation_unverified'): w.reserve_notification(OID,'attempt')
    def test_readback_mismatch_fails(self):
        proposed={'order_id':OID,'delivery_status':'sending','attempt_id':'attempt'}
        with patch.dict(os.environ,{'SUPABASE_SERVICE_ROLE_KEY':'fixture-only'}), patch.object(w,'store_open',return_value=Response(json.dumps([{'order_id':OID,'delivery_result':proposed}]).encode())), patch.object(w,'private_delivery',return_value={}):
            with self.assertRaisesRegex(w.GateError,'reservation_unverified'): w.reserve_notification(OID,'attempt')
    def test_redirect_never_forwards_authorization(self):
        with self.assertRaises(p.NotificationError) as ctx: p.NoRedirect().redirect_request(None,None,None,None,None,None)
        self.assertTrue(ctx.exception.ambiguous)
if __name__=='__main__': unittest.main()
