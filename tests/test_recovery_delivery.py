"""Privacy, duplicate-send, receipt and provider-state regressions. No network."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import order_workflow as w
import notification_provider as n

OID = 'AS-RECOVERYFIX'
PHONE = '+12025550123'
SID = 'SM' + 'a' * 32
ENV = {'TWILIO_SID': 'AC' + 'b' * 32, 'TWILIO_TOKEN': 'fixture-secret',
       'TWILIO_FROM': '+12025550111', 'RESEND_KEY': 'fixture-secret'}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.e = patch.dict(os.environ, ENV); self.e.start(); self.addCleanup(self.e.stop)
        self.order = {'order_id': OID, 'consent_verified': True, 'delivery_valid': True,
                      'delivery': 'sms', 'sms_to': PHONE, 'email': ''}

    def test_sms_queued_is_not_delivered_and_has_no_extra_cc_or_upsell(self):
        calls = []
        def http(req):
            calls.append(req)
            self.assertNotIn(b'upsell', req.data)
            self.assertNotIn(b'another+song', req.data)
            return {'sid': SID, 'status': 'queued'}
        r = n.send(self.order, 'attempt', http=http)
        self.assertEqual(r['delivery_status'], 'submitted')
        self.assertEqual(len(calls), 1)
        self.assertNotIn(PHONE, json.dumps(r))

    def test_sms_failed_is_failed_not_submitted(self):
        r = n.send(self.order, 'attempt', http=lambda req: {'sid': SID, 'status': 'failed'})
        self.assertEqual(r['delivery_status'], 'failed')

    def test_sms_delivered_is_terminal_provider_evidence(self):
        r = n.send(self.order, 'attempt', http=lambda req: {'sid': SID, 'status': 'delivered'})
        self.assertEqual(r['delivery_status'], 'delivered')

    def test_sms_missing_sid_is_ambiguous_not_success(self):
        with self.assertRaises(n.NotificationError) as cm:
            n.send(self.order, 'attempt', http=lambda req: {'status': 'queued'})
        self.assertTrue(cm.exception.ambiguous)

    def test_email_missing_id_is_ambiguous_not_success(self):
        self.order.update(delivery='email', sms_to='', email='recipient@example.test')
        with self.assertRaises(n.NotificationError) as cm:
            n.send(self.order, 'attempt', http=lambda req: {})
        self.assertTrue(cm.exception.ambiguous)

    def test_email_idempotency_is_order_and_attempt_bound(self):
        self.order.update(delivery='email', sms_to='', email='recipient@example.test')
        def http(req):
            self.assertEqual(req.get_header('Idempotency-key'), 'anthemsmith/' + OID + '/attempt')
            self.assertNotIn('cc', json.loads(req.data))
            return {'id': '11111111-1111-4111-8111-111111111111'}
        self.assertEqual(n.send(self.order, 'attempt', http=http)['delivery_status'], 'submitted')

    def test_dual_recipients_refused_before_provider(self):
        self.order['email'] = 'recipient@example.test'
        with self.assertRaises(n.NotificationError):
            n.send(self.order, 'attempt', http=lambda req: self.fail('network'))

    def test_sms_lookup_requires_matching_recipient_and_id(self):
        receipt = {'provider': 'twilio', 'provider_id': SID, 'delivery_status': 'submitted'}
        def http(req):
            self.assertEqual(req.method, 'GET')
            return {'sid': SID, 'to': PHONE, 'status': 'delivered'}
        self.assertEqual(n.lookup(self.order, receipt, http=http)['delivery_status'], 'delivered')
        with self.assertRaises(n.NotificationError):
            n.lookup(self.order, receipt, http=lambda req: {'sid': SID, 'to': '+12025550999', 'status': 'delivered'})

    def test_no_consent_never_sends(self):
        self.order['consent_verified'] = False
        with self.assertRaises(n.NotificationError):
            n.send(self.order, 'attempt', http=lambda req: self.fail('network'))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name); (self.root / 'songs').mkdir()
        self.order = {'order_id': OID, 'consent_verified': True, 'delivery_valid': True,
                      'delivery': 'sms', 'sms_to': PHONE, 'email': '', 'photo_urls': []}
        self.state = {}
        self.read_patch = patch.object(w, 'read_private', return_value=self.order)
        self.audio_patch = patch.object(w, 'audio_ready', return_value=True)
        self.read_patch.start(); self.audio_patch.start()
        self.addCleanup(self.read_patch.stop); self.addCleanup(self.audio_patch.stop)
        self.calls = 0

    def reserve(self, oid, attempt):
        self.assertFalse(self.state)
        self.state.update(order_id=oid, delivery_status='sending', attempt_id=attempt)
        return dict(self.state)

    def persist(self, oid, result):
        self.state.clear(); self.state.update(result)

    def sender(self, order, attempt):
        self.assertEqual(self.state['delivery_status'], 'sending')
        self.calls += 1
        return {'order_id': OID, 'delivery_status': 'submitted', 'provider': 'twilio',
                'provider_id': SID, 'provider_status': 'queued', 'attempt_id': attempt}

    def run_delivery(self, **overrides):
        args = dict(send=self.sender, lookup=lambda *a: self.fail('unexpected lookup'),
                    persist=self.persist, read=lambda oid: dict(self.state), reserve=self.reserve, verify=lambda *a: True)
        args.update(overrides)
        return w.deliver(self.root, **args)

    def test_reserve_before_send_and_private_id_only(self):
        self.assertEqual(self.run_delivery(), 0)
        self.assertEqual(self.calls, 1)
        public = (self.root / 'songs' / (OID + '.delivery.json')).read_text()
        self.assertNotIn(SID, public); self.assertNotIn(PHONE, public)
        self.assertEqual(self.state['provider_id'], SID)

    def test_ambiguous_persistence_cannot_duplicate_after_public_file_loss(self):
        def fail(*a): raise w.GateError('persistence_failed')
        self.assertEqual(self.run_delivery(persist=fail), 1)
        self.assertEqual(self.calls, 1)
        (self.root / 'songs' / (OID + '.delivery.json')).unlink()
        self.assertEqual(self.run_delivery(), 1)
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.state['delivery_status'], 'sending')

    def test_unavailable_deployed_audio_never_reserves_or_sends(self):
        def fail(*a): raise w.GateError("full_audio_not_yet_available_at_customer_url")
        with self.assertRaises(w.GateError): self.run_delivery(verify=fail)
        self.assertEqual(self.calls, 0); self.assertFalse(self.state)

    def test_reservation_failure_does_not_send(self):
        def fail(*a): raise w.GateError('reservation_failed')
        with self.assertRaises(w.GateError): self.run_delivery(reserve=fail)
        self.assertEqual(self.calls, 0)

    def test_submitted_is_reconciled_without_resend(self):
        self.state.update(delivery_status='submitted', provider='twilio', provider_id=SID)
        def lookup(order, receipt): return {**receipt, 'delivery_status': 'delivered', 'provider_status': 'delivered'}
        self.assertEqual(self.run_delivery(lookup=lookup), 0)
        self.assertEqual(self.calls, 0); self.assertEqual(self.state['delivery_status'], 'delivered')

    def test_pending_provider_status_is_not_delivery(self):
        self.state.update(delivery_status='submitted', provider='twilio', provider_id=SID)
        self.assertEqual(self.run_delivery(lookup=lambda order, r: r), 0)
        self.assertEqual(self.state['delivery_status'], 'submitted'); self.assertEqual(self.calls, 0)

    def test_untrusted_public_receipt_does_not_hide_missing_private_state(self):
        w.write_json(self.root / 'songs' / (OID + '.delivery.json'), {'delivery_status': 'submitted'})
        self.assertEqual(self.run_delivery(), 1); self.assertEqual(self.calls, 0)

    def test_ambiguous_send_is_unknown_no_retry(self):
        def send(*a): raise n.NotificationError('provider_result_unknown', ambiguous=True)
        self.assertEqual(self.run_delivery(send=send), 1)
        self.assertEqual(self.state['delivery_status'], 'unknown')
        self.assertEqual(self.run_delivery(), 1); self.assertEqual(self.calls, 0)

    def test_new_generation_without_private_output_or_public_consent_blocks_spend(self):
        order = dict(self.order, public_audio_consent=False)
        run = Mock(side_effect=AssertionError('must not spend'))
        with patch.object(w, 'read_private', return_value=order), patch.object(w, 'audio_ready', return_value=False):
            self.assertEqual(w.forge(self.root, run), 1)
        run.assert_not_called()
        self.assertEqual(json.loads((self.root/'songs'/f'{OID}.blocked.json').read_text())['reason'], 'private_audio_delivery_not_configured')

    def test_existing_full_audio_preserved_without_regeneration(self):
        run = Mock(side_effect=AssertionError('must not regenerate'))
        self.assertEqual(w.forge(self.root, run), 0)
        run.assert_not_called()

    def test_no_consent_keeps_audio_pending_without_side_effect(self):
        self.order['consent_verified'] = False
        self.assertEqual(self.run_delivery(), 1)
        self.assertFalse(self.state); self.assertEqual(self.calls, 0)

    def test_public_json_strip_source_identity_reference_and_error_body(self):
        w.write_json(self.root / 'songs' / (OID + '.json'), {'order_id': OID, 'status': 'audio_ready',
                     'title': 'private name', 'reference': 'private reference', 'sources': {'bio': 'private'},
                     'duration_full': 210})
        w.write_json(self.root / 'songs' / (OID + '.delivery.json'),
                     {'delivery_status': 'failed', 'detail': PHONE, 'provider_id': SID})
        w.write_json(self.root / 'songs' / (OID + '.failed.json'),
                     {'reason': 'Provider rejected ' + PHONE})
        w.sanitize_public_artifacts(self.root, OID)
        combined = ''.join(p.read_text() for p in (self.root / 'songs').glob('*.json'))
        for forbidden in (PHONE, SID, 'private name', 'private reference', 'Provider rejected'):
            self.assertNotIn(forbidden, combined)

    def test_private_receipt_readback_must_match_not_only_order_id(self):
        result = {'delivery_status': 'submitted', 'provider_id': SID}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, *a): return json.dumps([{'order_id': OID, 'delivery_result': {}}]).encode()
        with patch.dict(os.environ, {'SUPABASE_SERVICE_ROLE_KEY': 'fixture'}), patch.object(w, 'store_open', return_value=Response()):
            with self.assertRaises(w.GateError): w.persist_delivery(OID, result)


class MediaTests(unittest.TestCase):
    def test_nonempty_corrupt_audio_not_ready(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); (root/'songs').mkdir()
            w.write_json(root/'songs'/f'{OID}.json', {'order_id':OID,'status':'audio_ready'})
            for suffix in ('.mp3','-full.mp3'): (root/'songs'/(OID+suffix)).write_bytes(b'NOT AUDIO')
            self.assertFalse(w.audio_ready(root, OID))

    def test_actual_ffprobe_ready_full_audio_and_preview(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'songs').mkdir()
            w.write_json(root/'songs'/f'{OID}.json',{'order_id':OID,'status':'audio_ready'})
            for suffix,duration in (('.mp3',2),('-full.mp3',181)):
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=8000',
                                '-t',str(duration),'-c:a','libmp3lame','-b:a','8k',str(root/'songs'/(OID+suffix))],check=True)
            self.assertTrue(w.audio_ready(root, OID))


if __name__ == '__main__': unittest.main()
