"""Synthetic private-audio fixtures only; never read configuration/real data."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from email.message import Message
from threading import RLock
from urllib.parse import urlsplit, parse_qs
import private_audio as pa

OID = 'AS-PRIVATE-OFFLINE'

def pair(oid=OID, data=b'not media: transport-layer fixture only'):
    token = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
    now = datetime.now(timezone.utc) - timedelta(seconds=1)
    context = {'url': pa.PLAYER + '#order=' + oid + '&token=' + token,
        'token_sha256': hashlib.sha256(token.encode()).hexdigest(),
        'expires_at': (now + timedelta(days=29)).isoformat()}
    manifest = {'order_id': oid, 'object_path': oid + '/11111111-1111-4111-8111-111111111111.mp3',
        'sha256': hashlib.sha256(data).hexdigest(), 'byte_size': len(data), 'mime_type': 'audio/mpeg',
        'duration_seconds': 181.0, 'token_sha256': context['token_sha256'],
        'token_issued_at': now.isoformat(), 'expires_at': context['expires_at']}
    return manifest, context

class Response(io.BytesIO):
    def __init__(self, data, status=200, mime='application/json'):
        super().__init__(data)
        self.status = status
        self.headers = Message()
        self.headers['Content-Type'] = mime
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

def clone(x): return json.loads(json.dumps(x))

class Store:
    """Request-level in-memory service double with JSONB CAS and immutable POST.
    Deliberately NOT evidence that deployed SQL/RLS/Storage works.
    """
    def __init__(self, audio=b''):
        self.row = {'order_id': OID, 'status': 'source_received', 'payment_state': 'verified_paid',
            'payment_ref': 'offline-unique-payment', 'delivery_result': {},
            'delivery': {'mode': 'email', 'email': 'offline@example.test', 'sms_to': '', 'consent_verified': True}}
        self.manifest = None
        self.objects = {}
        self.calls = []
        self.lock = RLock()
        self.audio = audio
        self.fault = None
        self.muapi_result = {'status': 'completed', 'data': {'outputs': ['https://media.example.test/song.mp3']}}
    def installed(self):
        self.manifest, context = pair(data=self.audio)
        self.row['delivery']['private_song_access'] = context
        self.objects[self.manifest['object_path']] = self.audio
    def order(self):
        return {'order_id': OID, 'delivery': 'email', 'email': 'offline@example.test', 'sms_to': '',
                'delivery_valid': True, 'consent_verified': True, 'photo_urls': [], 'public_audio_consent': False}
    def open(self, req, **kwargs):
        with self.lock:
            self.calls.append(req)
            if self.fault:
                self.fault(req, self)
            path = urlsplit(req.full_url).path
            params = parse_qs(urlsplit(req.full_url).query)
            method = req.get_method()
            if req.full_url == 'https://api.muapi.ai/api/v1/suno-create-music':
                assert method == 'POST'
                return Response(b'{"request_id":"offline-job-1"}')
            if path == '/api/v1/predictions/offline-job-1/result':
                assert method == 'GET'
                return Response(json.dumps(self.muapi_result).encode())
            if req.full_url == 'https://media.example.test/song.mp3':
                assert method == 'GET'
                return Response(self.audio, mime='audio/mpeg')
            assert req.full_url.startswith(pa.ORIGIN + '/'), 'unexpected origin'
            if path == '/storage/v1/bucket/' + pa.BUCKET:
                return Response(json.dumps({'id': pa.BUCKET, 'public': False,
                    'file_size_limit': pa.MAX_BYTES, 'allowed_mime_types': ['audio/mpeg']}).encode())
            if path == '/rest/v1/' + pa.ORDERS:
                if method == 'PATCH':
                    for k, v in params.items():
                        value = v[0]
                        assert value.startswith('eq.')
                        expected = json.loads(value[3:]) if k in ('delivery', 'delivery_result') else value[3:]
                        if self.row.get(k) != expected:
                            return Response(b'[]')
                    self.row.update(json.loads(req.data))
                return Response(json.dumps([self.row]).encode())
            if path == '/rest/v1/' + pa.TABLE:
                if method == 'POST':
                    assert self.manifest is None, 'immutable manifest insert conflict'
                    assert 'resolution=merge' not in req.get_header('Prefer', '')
                    self.manifest = json.loads(req.data)
                    return Response(json.dumps([self.manifest]).encode(), 201)
                assert method == 'GET'
                return Response(json.dumps([self.manifest] if self.manifest else []).encode())
            if path.startswith('/storage/v1/object/authenticated/' + pa.BUCKET + '/'):
                assert method == 'GET'
                key = path.split(pa.BUCKET + '/', 1)[1]
                return Response(self.objects[key], mime='audio/mpeg')
            if path.startswith('/storage/v1/object/' + pa.BUCKET + '/'):
                assert method == 'POST'
                assert req.get_header('X-upsert') == 'false'
                assert req.get_header('Content-type') == 'audio/mpeg'
                key = path.split(pa.BUCKET + '/', 1)[1]
                assert key not in self.objects, 'immutable storage conflict'
                self.objects[key] = req.data
                return Response(b'{}', 201)
            if req.full_url == pa.EDGE:
                assert method == 'GET'
                assert req.get_header('Authorization').startswith('Bearer ')
                assert req.get_header('X-anthem-order') == OID
                assert 'Apikey' not in req.headers
                token = self.row['delivery']['private_song_access']['url'].split('&token=')[1]
                assert req.get_header('Authorization') == 'Bearer ' + token
                return Response(self.objects[self.manifest['object_path']], mime='audio/mpeg')
            raise AssertionError('unexpected private endpoint')
