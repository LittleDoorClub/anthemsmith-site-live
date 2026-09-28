"""Bounded offline fault probes. Failing tests express required safety properties."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse
import urllib.request
import urllib.response
from email.message import Message

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import order_workflow as w
import notification_provider as n
from private_audio_fixtures import pair

OID = 'AS-OFFLINEREVIEW'
PHONE = '+' + '12025550123'
SID = 'SM' + 'a' * 32
EMAIL_ID = '11111111-1111-4111-8111-111111111111'
ENV = {'SUPABASE_SERVICE_ROLE_KEY': 'offline-store-sentinel',
       'TWILIO_SID': 'AC' + 'b' * 32, 'TWILIO_TOKEN': 'offline-token',
       'TWILIO_FROM': '+' + '12025550111', 'RESEND_KEY': 'offline-email-sentinel',
       'GABE_CC': '+' + '12025550199'}

class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

class FakeWire(urllib.request.BaseHandler):
    handler_order = 100
    def __init__(self, responder):
        self.calls = []
        self.responder = responder
    def https_open(self, request):
        self.calls.append(request)
        status, payload, location = self.responder(request)
        headers = Message()
        headers['Content-Type'] = 'audio/mpeg'
        if location: headers['Location'] = location
        response = urllib.response.addinfourl(io.BytesIO(payload), headers, request.full_url, status)
        response.msg = 'offline fixture'
        return response
    http_open = https_open

class IndependentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'songs').mkdir()
        self.order = {'order_id': OID, 'consent_verified': True, 'delivery_valid': True,
                      'delivery': 'sms', 'sms_to': PHONE, 'email': '', 'photo_urls': [], 'private_song_access': pair(OID)[1]}
        access = patch.object(w, 'load_private_access', return_value=(pair(OID)[1], {}))
        access.start(); self.addCleanup(access.stop)
        self.env = patch.dict(os.environ, ENV)
        self.env.start(); self.addCleanup(self.env.stop)
    def email_order(self):
        return {**self.order, 'delivery': 'email', 'sms_to': '', 'email': 'review@example.test'}
    def test_public_only_success_receipt_must_block_every_retry(self):
        state, sends = {}, []
        path = self.root / 'songs' / (OID + '.delivery.json')
        w.write_json(path, {'order_id': OID, 'delivery_status': 'submitted'})
        def reserve(oid, attempt):
            self.assertFalse(state)
            state.update(order_id=oid, delivery_status='sending', attempt_id=attempt)
        def send(order, attempt):
            sends.append(attempt)
            return {'order_id': OID, 'delivery_status': 'submitted', 'provider_id': SID}
        def persist(oid, receipt): state.clear(); state.update(receipt)
        with patch.object(w, 'read_private', return_value=self.order), patch.object(w, 'audio_ready', return_value=True):
            kwargs = dict(read=lambda oid: dict(state), reserve=reserve, send=send,
                          persist=persist, verify=lambda *args: True)
            first = w.deliver(self.root, **kwargs)
            second = w.deliver(self.root, **kwargs)
        self.assertEqual(first, 1)
        self.assertEqual(len(sends), 0, 'second retry sent after public success was overwritten as pending; return=' + str(second))
    def test_store_redirect_never_forwards_service_headers(self):
        wire = FakeWire(lambda req: (302, b'', 'https://foreign.invalid/collect')
                        if urllib.parse.urlparse(req.full_url).hostname != 'foreign.invalid'
                        else (200, b'[]', None))
        opener = urllib.request.build_opener(wire)
        actual_build = urllib.request.build_opener
        with patch.object(w.urllib.request, 'build_opener', side_effect=lambda *args: actual_build(wire, *args)):
            with self.assertRaises(w.GateError):
                w.rest(w.ORDER_TABLE, {'order_id': 'eq.' + OID})
        leaked = [req for req in wire.calls if urllib.parse.urlparse(req.full_url).hostname == 'foreign.invalid'
                  and ('Authorization' in req.headers or 'Apikey' in req.headers)]
        self.assertEqual(len(leaked), 0, 'cross-origin request retained Authorization/apikey (dummy values only)')
    def test_provider_redirect_refused_without_forward_or_secret_log(self):
        wire = FakeWire(lambda req: (302, b'fixture-private-body', 'https://foreign.invalid/collect'))
        actual_build = urllib.request.build_opener
        logs = io.StringIO()
        req = urllib.request.Request('https://api.resend.com/emails', data=b'fixture-private-body',
                                     headers={'Authorization': 'Bearer offline-secret'}, method='POST')
        with patch.object(n.urllib.request, 'build_opener', side_effect=lambda *args: actual_build(wire, *args)):
            with contextlib.redirect_stdout(logs), contextlib.redirect_stderr(logs):
                with self.assertRaises(n.NotificationError) as error: n.http_json(req)
        self.assertEqual(str(error.exception), 'provider_redirect_refused')
        self.assertTrue(error.exception.ambiguous)
        self.assertEqual(len(wire.calls), 1)
        self.assertEqual(logs.getvalue(), '')
    def test_email_reply_requires_valid_uuid(self):
        with self.assertRaises(n.NotificationError):
            n.send(self.email_order(), 'attempt', http=lambda request: {'id': '-' * 36})
    def test_email_reply_rejects_explicit_wrong_recipient(self):
        with self.assertRaises(n.NotificationError):
            n.send(self.email_order(), 'attempt', http=lambda request: {'id': EMAIL_ID, 'to': ['other@example.test']})
    def test_lookup_rejects_wrong_email_owner_and_unsafe_status(self):
        order = self.email_order()
        receipt = {'provider': 'resend', 'provider_id': EMAIL_ID, 'delivery_status': 'submitted'}
        for data in ({'id': EMAIL_ID, 'to': ['other@example.test'], 'last_event': 'delivered'},
                     {'id': EMAIL_ID, 'to': [order['email']], 'last_event': 'recipient@example.test'}):
            with self.subTest(data=data), self.assertRaises(n.NotificationError):
                n.lookup(order, receipt, http=lambda request: data)
    def test_send_refuses_mode_and_dual_contact_before_http(self):
        for order in ({**self.order, 'delivery': 'email'}, {**self.email_order(), 'sms_to': PHONE}):
            with self.subTest(order=order), self.assertRaises(n.NotificationError):
                n.send(order, 'attempt', http=lambda request: self.fail('unexpected HTTP'))
    def test_atomic_reservation_filter_and_readback_mismatch(self):
        calls = []
        def urlopen(request, **kwargs):
            calls.append(request)
            if request.get_method() == 'PATCH':
                params = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
                self.assertEqual(params['delivery_result'], ['eq.{}'])
                self.assertEqual(params['order_id'], ['eq.' + OID])
                proposed = json.loads(request.data)['delivery_result']
                return Response(json.dumps([{'order_id': OID, 'delivery_result': proposed}]).encode())
            return Response(json.dumps([{'order_id': OID, 'delivery_result': {}}]).encode())
        with patch.object(w, 'store_open', side_effect=urlopen):
            with self.assertRaisesRegex(w.GateError, 'notification_reservation_unverified'):
                w.reserve_notification(OID, 'attempt')
        self.assertEqual([request.get_method() for request in calls], ['PATCH', 'GET'])
    def test_fresh_media_exact_bytes_and_hash_mismatch(self):
        payload = b'offline fixture bytes, hash layer only'
        manifest, context = pair(OID, payload)
        state = patch.object(w.pa, 'read_state', return_value=(manifest, context, {}))
        state.start(); self.addCleanup(state.stop)
        for served, valid in ((payload, True), (payload[:-1], False), (b'x' * len(payload), False), (payload + b'x', False)):
            wire = FakeWire(lambda request: (200, served, None))
            actual_build = urllib.request.build_opener
            with patch.object(w.urllib.request, 'build_opener', side_effect=lambda *args: actual_build(wire, *args)):
                if valid:
                    self.assertEqual(w.verify_served_audio(self.root, OID), {'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)})
                else:
                    with self.assertRaises(w.GateError): w.verify_served_audio(self.root, OID)
    def test_truncated_mp3_must_not_be_considered_full_ready(self):
        w.write_json(self.root / 'songs' / (OID + '.json'), {'order_id': OID, 'status': 'audio_ready'})
        for suffix, duration in (('.mp3', 2), ('-full.mp3', 181)):
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=8000',
                            '-t', str(duration), '-c:a', 'libmp3lame', '-b:a', '8k', str(self.root / 'songs' / (OID + suffix))], check=True)
        full = self.root / 'songs' / (OID + '-full.mp3')
        original_bytes = full.stat().st_size
        full.write_bytes(full.read_bytes()[:4096])
        decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(full), '-f', 's16le', '-ar', '8000', '-ac', '1', '-'], capture_output=True, check=True)
        decoded_seconds = len(decoded.stdout) / 16000
        self.assertLess(decoded_seconds, 180)
        self.assertFalse(w.audio_ready(self.root, OID), 'truncated full mp3 accepted: original_bytes=' + str(original_bytes) + ', bytes=4096, decoded_seconds=' + str(decoded_seconds))

if __name__ == '__main__': unittest.main()
