"""Private recovery audio: immutable bytes, durable capabilities and spend fences.

No provider/database work at import. Never log requests, response bodies, object
paths, capabilities or customer data. All exception strings are fixed safe codes.
Separate manifest/context writes deliberately fail closed on torn state; operators
must reconcile it, never erase a reservation to make a retry proceed.
"""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

ORIGIN = 'https://veqpmdsiqcjjpxgcubrp.supabase.co'
BUCKET = 'anthemsmith-songs'
TABLE = 'anthemsmith_song_access'
ORDERS = 'anthemsmith_recovery_orders'
EDGE = ORIGIN + '/functions/v1/anthemsmith-song-access/audio'
PLAYER = 'https://anthemsmith.com/private-song.html'
MAX_BYTES = 20 * 1024 * 1024
MANIFEST_FIELDS = ('order_id', 'object_path', 'sha256', 'byte_size', 'mime_type',
                   'duration_seconds', 'token_sha256', 'token_issued_at', 'expires_at')

class PrivateAudioError(Exception):
    """Operator-safe codes only."""

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise PrivateAudioError('private_audio_redirect_refused')

def open_request(request, timeout=45):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)

def request_bytes(request, limit=512 * 1024, mime=None, statuses=(200,)):
    try:
        with open_request(request) as response:
            if response.status not in statuses:
                raise ValueError('status')
            if mime and response.headers.get_content_type() != mime:
                raise ValueError('mime')
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('oversize')
            return raw
    except Exception:
        raise PrivateAudioError('private_audio_transport_unverified') from None

def service_headers():
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    if not key:
        raise PrivateAudioError('private_audio_store_not_configured')
    return {'Authorization': 'Bearer ' + key, 'apikey': key}

def api(path, method='GET', body=None, headers=None):
    h = service_headers()
    h.update(headers or {})
    data = None
    if body is not None:
        h['Content-Type'] = 'application/json'
        data = json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
    try:
        # POST/PATCH may return 201; require an explicit successful representation.
        req = urllib.request.Request(ORIGIN + path, data=data, method=method, headers=h)
        with open_request(req) as response:
            if response.status not in ((200,) if method == 'GET' else (200, 201)):
                raise ValueError('status')
            raw = response.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise ValueError('oversize')
            return json.loads(raw)
    except Exception:
        raise PrivateAudioError('private_audio_store_unverified') from None

def oid_check(oid):
    if not isinstance(oid, str) or not re.fullmatch(r'AS-[A-Z0-9-]{6,64}', oid):
        raise PrivateAudioError('private_audio_order_invalid')
    return oid

def query(table, params):
    return '/rest/v1/' + table + '?' + urllib.parse.urlencode(params)

def snapshot(oid):
    oid_check(oid)
    rows = api(query(ORDERS, {'order_id': 'eq.' + oid,
        'select': 'order_id,status,payment_state,payment_ref,delivery,delivery_result', 'limit': '2'}))
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get('order_id') != oid or
        rows[0].get('status') != 'source_received' or rows[0].get('payment_state') != 'verified_paid' or
        not isinstance(rows[0].get('payment_ref'), str) or not rows[0]['payment_ref'].strip() or
        not isinstance(rows[0].get('delivery'), dict) or not isinstance(rows[0].get('delivery_result'), dict)):
        raise PrivateAudioError('private_audio_order_unavailable')
    return rows[0]

def get_manifest(oid):
    oid_check(oid)
    rows = api(query(TABLE, {'order_id': 'eq.' + oid, 'select': '*', 'limit': '2'}))
    if not isinstance(rows, list) or len(rows) > 1:
        raise PrivateAudioError('private_audio_manifest_invalid')
    if rows and (not isinstance(rows[0], dict) or rows[0].get('order_id') != oid):
        raise PrivateAudioError('private_audio_manifest_invalid')
    return rows[0] if rows else None

def cas_delivery(oid, row, delivery):
    """Whole private delivery JSON CAS preserves recipients and competing workers.
    Also bind payment/source and outbox: never change a capability across a send.
    A timeout or lost readback is not permission to retry this mutation.
    """
    params = {'order_id': 'eq.' + oid, 'status': 'eq.source_received',
        'payment_state': 'eq.verified_paid', 'payment_ref': 'eq.' + row['payment_ref'],
        'delivery': 'eq.' + json.dumps(row['delivery'], separators=(',', ':')),
        'delivery_result': 'eq.' + json.dumps(row['delivery_result'], separators=(',', ':'))}
    rows = api(query(ORDERS, params), 'PATCH', {'delivery': delivery}, {'Prefer': 'return=representation'})
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get('order_id') != oid or
            rows[0].get('delivery') != delivery or snapshot(oid)['delivery'] != delivery):
        raise PrivateAudioError('private_audio_cas_unverified')

def timestamp(value):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError('timezone')
        return date
    except Exception:
        raise PrivateAudioError('private_audio_expiry_invalid') from None

def validate_manifest(oid, manifest):
    oid_check(oid)
    try:
        path_pattern = re.escape(oid) + r'/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.mp3'
        if (manifest['order_id'] != oid or not re.fullmatch(path_pattern, manifest['object_path']) or
            not re.fullmatch(r'[0-9a-f]{64}', manifest['sha256']) or
            not re.fullmatch(r'[0-9a-f]{64}', manifest['token_sha256']) or
            type(manifest['byte_size']) is not int or not 1 <= manifest['byte_size'] <= MAX_BYTES or
            manifest['mime_type'] != 'audio/mpeg' or type(manifest['duration_seconds']) not in (int, float) or
            not 180 <= manifest['duration_seconds'] <= 240 or manifest.get('revoked_at') is not None):
            raise ValueError('manifest')
        issue, expiry = timestamp(manifest['token_issued_at']), timestamp(manifest['expires_at'])
        if not issue <= datetime.now(timezone.utc) < expiry or not timedelta(0) < expiry - issue <= timedelta(days=30):
            raise ValueError('expired')
    except Exception:
        raise PrivateAudioError('private_audio_manifest_invalid') from None

def context_token(oid, context):
    """An exact fragment URL, not an arbitrary destination or query-token fallback."""
    oid_check(oid)
    try:
        if set(context) != {'url', 'token_sha256', 'expires_at'}:
            raise ValueError('shape')
        match = re.fullmatch(re.escape(PLAYER + '#order=' + oid + '&token=') + r'([A-Za-z0-9_-]{43})', context['url'])
        if not match:
            raise ValueError('url')
        token = match[1]
        # Require canonical 32-byte base64url, not a 43-char noncanonical alias.
        raw = base64.urlsafe_b64decode(token + '=')
        if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip('=') != token:
            raise ValueError('token')
        if hashlib.sha256(token.encode()).hexdigest() != context['token_sha256']:
            raise ValueError('hash')
        if timestamp(context['expires_at']) <= datetime.now(timezone.utc):
            raise ValueError('expired')
        return token
    except Exception:
        raise PrivateAudioError('private_audio_send_context_invalid') from None

def read_state(oid):
    """Always read BOTH records before deciding that generation is allowed."""
    manifest, row = get_manifest(oid), snapshot(oid)
    delivery = row['delivery']
    if 'private_audio_job' in delivery and not isinstance(delivery['private_audio_job'], dict):
        raise PrivateAudioError('generation_state_invalid')
    context = delivery.get('private_song_access')
    if manifest is not None or context is not None:
        if manifest is None or context is None or 'private_audio_pending' in delivery:
            raise PrivateAudioError('private_audio_torn_write_reconciliation_required')
        validate_manifest(oid, manifest)
        context_token(oid, context)
        if (manifest['token_sha256'] != context['token_sha256'] or
                timestamp(manifest['expires_at']) != timestamp(context['expires_at'])):
            raise PrivateAudioError('private_audio_context_manifest_mismatch')
    elif 'private_audio_pending' in delivery:
        raise PrivateAudioError('private_audio_torn_write_reconciliation_required')
    return manifest, context, row

def require_config():
    if os.environ.get('AS_PRIVATE_AUDIO_ENABLED') != '1':
        raise PrivateAudioError('private_audio_delivery_not_configured')
    bucket = api('/storage/v1/bucket/' + BUCKET)
    if (not isinstance(bucket, dict) or bucket.get('id') != BUCKET or bucket.get('public') is not False or
        type(bucket.get('file_size_limit')) is not int or not 1 <= bucket['file_size_limit'] <= MAX_BYTES or
        bucket.get('allowed_mime_types') != ['audio/mpeg']):
        raise PrivateAudioError('private_audio_bucket_not_private')

def qc_file(path):
    """Probe codec AND decode every sample; a surviving Xing header is not QC."""
    path = Path(path)
    try:
        size = path.stat().st_size
        if not 1 <= size <= MAX_BYTES:
            raise ValueError('size')
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
            'format=duration:stream=codec_type,codec_name', '-of', 'json', str(path)],
            capture_output=True, text=True, timeout=25, check=True)
        meta = json.loads(probe.stdout)
        duration = float(meta['format']['duration'])
        if (not math.isfinite(duration) or not 180 <= duration <= 240 or
                not any(s.get('codec_type') == 'audio' and s.get('codec_name') == 'mp3' for s in meta.get('streams', []))):
            raise ValueError('probe')
        # File-backed PCM avoids unbounded captured stdout. -t bounds pathological
        # decoder output at 241s; reaching the bound fails rather than truncates QC.
        with tempfile.TemporaryFile() as pcm:
            decoded = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-err_detect', 'explode',
                '-i', str(path), '-vn', '-t', '241', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'],
                stdout=pcm, stderr=subprocess.PIPE, timeout=45, check=True)
            seconds = pcm.tell() / 16000
        if decoded.stderr or not 180 <= seconds <= 240 or abs(duration - seconds) > 1:
            raise ValueError('decode')
        return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'byte_size': size,
                'mime_type': 'audio/mpeg', 'duration_seconds': round(seconds, 3)}
    except Exception:
        raise PrivateAudioError('private_audio_qc_failed') from None

def bytes_match(data, manifest):
    if len(data) != manifest['byte_size'] or hashlib.sha256(data).hexdigest() != manifest['sha256']:
        raise PrivateAudioError('private_audio_bytes_mismatch')

def verify_edge(oid, manifest, context):
    validate_manifest(oid, manifest)
    token = context_token(oid, context)
    if (context['token_sha256'] != manifest['token_sha256'] or
            timestamp(context['expires_at']) != timestamp(manifest['expires_at'])):
        raise PrivateAudioError('private_audio_context_manifest_mismatch')
    data = request_bytes(urllib.request.Request(EDGE, headers={
        'Authorization': 'Bearer ' + token, 'X-Anthem-Order': oid,
        'Cache-Control': 'no-store', 'Accept': 'audio/mpeg'}), MAX_BYTES, 'audio/mpeg')
    bytes_match(data, manifest)
    return {'sha256': manifest['sha256'], 'bytes': manifest['byte_size']}

def register_audio(oid, path):
    require_config()
    manifest, context, row = read_state(oid)
    if manifest:
        verify_edge(oid, manifest, context)
        return manifest, context
    if row['delivery_result']:
        raise PrivateAudioError('private_audio_notification_reconciliation_required')
    qc = qc_file(path)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    context = {'url': PLAYER + '#order=' + oid + '&token=' + token,
               'token_sha256': hashlib.sha256(token.encode()).hexdigest(),
               'expires_at': (now + timedelta(days=30)).isoformat()}
    manifest = {'order_id': oid, 'object_path': oid + '/' + str(uuid.uuid4()) + '.mp3',
                **qc, 'token_sha256': context['token_sha256'], 'token_issued_at': now.isoformat(),
                'expires_at': context['expires_at']}
    pending = {**row['delivery'], 'private_audio_pending': {'manifest': manifest, 'context': context}}
    cas_delivery(oid, row, pending)  # Durable path/token before first storage side effect.
    data = Path(path).read_bytes()
    bytes_match(data, manifest)
    headers = service_headers()
    headers.update({'Content-Type': 'audio/mpeg', 'x-upsert': 'false'})
    storage = ORIGIN + '/storage/v1/object/' + BUCKET + '/' + manifest['object_path']
    try:
        with open_request(urllib.request.Request(storage, method='POST', data=data, headers=headers)) as response:
            if response.status not in (200, 201):
                raise ValueError('upload')
            response.read(65537)  # Never print returned path/body.
    except Exception:
        raise PrivateAudioError('private_audio_upload_reconciliation_required') from None
    fetched = request_bytes(urllib.request.Request(ORIGIN + '/storage/v1/object/authenticated/' +
        BUCKET + '/' + manifest['object_path'], headers=service_headers()), MAX_BYTES, 'audio/mpeg')
    bytes_match(fetched, manifest)
    rows = api('/rest/v1/' + TABLE, 'POST', manifest, {'Prefer': 'return=representation'})
    if not isinstance(rows, list) or len(rows) != 1:
        raise PrivateAudioError('private_audio_registration_unverified')
    stored = get_manifest(oid)
    if stored is None or any(stored.get(k) != manifest[k] for k in MANIFEST_FIELDS if k not in ('token_issued_at', 'expires_at')):
        raise PrivateAudioError('private_audio_registration_unverified')
    validate_manifest(oid, stored)
    for field in ('token_issued_at', 'expires_at'):
        if timestamp(stored[field]) != timestamp(manifest[field]):
            raise PrivateAudioError('private_audio_registration_unverified')
    current = snapshot(oid)
    if current['delivery'] != pending or current['delivery_result']:
        raise PrivateAudioError('private_audio_cas_unverified')
    final = {**pending, 'private_song_access': context}
    final.pop('private_audio_pending')
    cas_delivery(oid, current, final)
    stored, context, _ = read_state(oid)
    verify_edge(oid, stored, context)
    return stored, context

def reserve_generation(oid):
    manifest, _, row = read_state(oid)
    prior = row['delivery'].get('private_audio_job')
    # A confirmed child exit while still reserved proves no music POST was made:
    # submit_generation CASes to submitting before its side effect. Only that
    # terminal preflight state can be retried; never take over reserved/submitting.
    if manifest or row['delivery_result'] or (prior is not None and prior.get('status') != 'preflight_failed'):
        raise PrivateAudioError('generation_reconciliation_required')
    job = {'attempt_id': str(uuid.uuid4()), 'status': 'reserved', 'model': 'V6'}
    cas_delivery(oid, row, {**row['delivery'], 'private_audio_job': job})
    return job

def job_transition(oid, expected, updated):
    row = snapshot(oid)
    if row['delivery_result'] or row['delivery'].get('private_audio_job') != expected:
        raise PrivateAudioError('generation_reservation_unverified')
    cas_delivery(oid, row, {**row['delivery'], 'private_audio_job': updated})

def submit_generation(oid, body):
    """Exactly one local attempt, no model fallback, no retry of unknown POST.
    The actual legacy MuAPI contract uses request_id/requestId/data.request_id.
    Successful acceptance is durable before polling or downloading starts.
    """
    manifest, _, row = read_state(oid)
    job = row['delivery'].get('private_audio_job', {})
    if (manifest or row['delivery_result'] or job.get('status') != 'reserved' or
            job.get('attempt_id') != os.environ.get('AS_GENERATION_ATTEMPT') or body.get('model') != 'V6'):
        raise PrivateAudioError('generation_reservation_unverified')
    key = os.environ.get('MUAPI_KEY', '')
    if not key:
        raise PrivateAudioError('generation_provider_not_configured')
    submitting = {**job, 'status': 'submitting'}
    job_transition(oid, job, submitting)
    try:
        raw = request_bytes(urllib.request.Request('https://api.muapi.ai/api/v1/suno-create-music',
            method='POST', data=json.dumps(body).encode(), headers={'x-api-key': key, 'Content-Type': 'application/json'}),
            statuses=(200, 201, 202))
        result = json.loads(raw)
        rid = result.get('request_id') or result.get('requestId') or (result.get('data') or {}).get('request_id')
        if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', rid):
            raise ValueError('request id')
    except Exception:
        raise PrivateAudioError('generation_submission_unknown') from None
    submitted = {**submitting, 'status': 'submitted', 'request_id': rid}
    job_transition(oid, submitting, submitted)
    return rid

def poll_generation(rid, max_seconds=840):
    if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', rid):
        raise PrivateAudioError('generation_request_id_invalid')
    key = os.environ.get('MUAPI_KEY', '')
    if not key:
        raise PrivateAudioError('generation_provider_not_configured')
    until = time.monotonic() + max_seconds
    while time.monotonic() < until:
        try:
            raw = request_bytes(urllib.request.Request('https://api.muapi.ai/api/v1/predictions/' + rid + '/result',
                headers={'x-api-key': key}))
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError('response shape')
        except (PrivateAudioError, ValueError):
            raise PrivateAudioError('generation_poll_pending') from None
        if result.get('status') in ('completed', 'succeeded', 'success'):
            return result
        if result.get('status') in ('failed', 'error'):
            raise PrivateAudioError('generation_failed_reconciliation_required')
        time.sleep(20)
    raise PrivateAudioError('generation_poll_pending')

def output_hosts():
    hosts = os.environ.get('AS_MUAPI_OUTPUT_HOSTS', '').split(',')
    if not hosts or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', h) or
            '.' not in h or h.endswith(('.local', '.internal', '.localhost')) or
            re.fullmatch(r'[0-9.]+', h) for h in hosts):
        raise PrivateAudioError('generation_output_hosts_not_configured')
    return set(hosts)

def download_output(result, path):
    try:
        outs = (result.get('data') or result).get('outputs') or []
        url = outs[0]
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port is not None or
                parsed.fragment or parsed.hostname not in output_hosts()):
            raise ValueError('output url')
        req = urllib.request.Request(url, headers={'User-Agent': 'AnthemSmith/1.0'})
        first, second = request_bytes(req, MAX_BYTES), request_bytes(req, MAX_BYTES)
        if not first or first != second:
            raise ValueError('byte stability')
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'xb') as handle:
            handle.write(first)
        qc_file(path)
    except PrivateAudioError:
        raise
    except Exception:
        raise PrivateAudioError('generation_output_unverified') from None

def resume_generation(oid, path):
    manifest, _, row = read_state(oid)
    job = row['delivery'].get('private_audio_job', {})
    if manifest or row['delivery_result'] or job.get('status') != 'submitted' or job.get('model') != 'V6':
        raise PrivateAudioError('generation_reconciliation_required')
    download_output(poll_generation(job.get('request_id')), path)
