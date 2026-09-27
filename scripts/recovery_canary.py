#!/usr/bin/env python3
"""Bounded synthetic acceptance for the existing private recovery service.

Never creates/verifies payments, sends notifications, or generates music.
Only AS-OX-CANARY-* IDs may be created/updated; credentials stay in this runner.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = 'https://veqpmdsiqcjjpxgcubrp.supabase.co'
PREFIX = 'AS-OX-CANARY-'


class CanaryError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CanaryError('redirect_rejected')


def request(table, params=None, method='GET', body=None):
    if table not in {'anthemsmith_recovery_orders', 'anthemsmith_recovery_uploads', 'anthemsmith_recovery_analyses'}:
        raise CanaryError('table_not_allowed')
    key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
    url = ORIGIN + '/rest/v1/' + table
    if params:
        url += '?' + urllib.parse.urlencode(params)
    headers = {'apikey': key, 'Authorization': 'Bearer ' + key,
               'Content-Type': 'application/json', 'Prefer': 'return=representation'}
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise CanaryError('response_too_large')
        result = json.loads(raw)
        if not isinstance(result, list):
            raise CanaryError('invalid_rest_response')
        return result
    except CanaryError:
        raise
    except Exception:
        raise CanaryError('private_test_store_unavailable') from None


def order(oid):
    rows = request('anthemsmith_recovery_orders', {'order_id': 'eq.' + oid})
    if len(rows) != 1 or rows[0].get('order_id') != oid:
        raise CanaryError('canary_order_missing')
    row = rows[0]
    if row.get('payment_state') != 'unverified' or row.get('payment_ref') is not None:
        raise CanaryError('canary_must_not_be_paid')
    if row.get('brief', {}).get('synthetic_acceptance') is not True or row.get('delivery') != {}:
        raise CanaryError('not_our_synthetic_order')
    return row


def seed(oid, token_sha):
    if not re.fullmatch(r'[0-9a-f]{64}', token_sha):
        raise CanaryError('invalid_token_hash')
    existing = request('anthemsmith_recovery_orders', {'order_id': 'eq.' + oid})
    if existing:
        row = order(oid)
        if row['token_sha256'] != token_sha:
            raise CanaryError('existing_canary_token_mismatch')
        return {'stage': 'seed', 'order_id': oid, 'idempotent': True, 'payment_state': 'unverified'}
    now = dt.datetime.now(dt.timezone.utc)
    row = {'order_id': oid, 'token_sha256': token_sha, 'token_issued_at': now.isoformat(),
           'expires_at': (now + dt.timedelta(hours=2)).isoformat(),
           'payment_state': 'unverified', 'payment_ref': None,
           'brief': {'intake': 'photos', 'synthetic_acceptance': True},
           'delivery': {}, 'delivery_result': {}, 'status': 'awaiting_sources'}
    request('anthemsmith_recovery_orders', method='POST', body=row)
    readback = order(oid)
    if readback['token_sha256'] != token_sha or readback['status'] != 'awaiting_sources':
        raise CanaryError('seed_readback_failed')
    return {'stage': 'seed', 'order_id': oid, 'payment_state': 'unverified', 'readback': True}


def inspect(oid):
    row = order(oid)
    uploads = request('anthemsmith_recovery_uploads', {'order_id': 'eq.' + oid,
        'select': 'order_id,object_path,status,sha256,byte_size,mime_type'})
    return row, uploads


def analyze(oid):
    row, uploads = inspect(oid)
    if row['status'] != 'source_received' or not uploads or any(r['status'] != 'ready' for r in uploads):
        raise CanaryError('canary_sources_not_ready')
    from photo_intake import analyze_private_photos
    result = analyze_private_photos(oid, [r['object_path'] for r in uploads], ORIGIN,
                                   os.environ['SUPABASE_SERVICE_ROLE_KEY'], os.environ['OPENAI_API_KEY'])
    persisted = request('anthemsmith_recovery_analyses', {'order_id': 'eq.' + oid,
        'select': 'id,order_id,object_path,source_sha256,model,provider_request_id,analysis'})
    if len(persisted) != len(uploads):
        raise CanaryError('analysis_readback_count_mismatch')
    by_path = {r['object_path']: r for r in uploads}
    summary = []
    for row in persisted:
        if row['source_sha256'] != by_path[row['object_path']]['sha256'] or not row.get('provider_request_id'):
            raise CanaryError('analysis_provenance_mismatch')
        a = row['analysis']
        # Only this deliberately synthetic canary may return its observations as test evidence.
        summary.append({'analysis_id': row['id'], 'source_sha256': row['source_sha256'],
            'provider_request_id': row['provider_request_id'], 'model': row['model'],
            'bytes': a['bytes'], 'width': a['width'], 'height': a['height'],
            'observations': a['result']['observations'], 'visible_text': a['result']['visible_text']})
    # Assert this stage never mutated or bypassed the payment ledger.
    order(oid)
    return {'stage': 'analyze', 'order_id': oid, 'payment_state': 'unverified',
            'worker_read_durable_objects': True, 'stored_images_examined': len(result.analysis),
            'readback': True, 'sources': summary, 'music_generated': False, 'notification_sent': False}


def revoke(oid):
    row = order(oid)
    now = dt.datetime.now(dt.timezone.utc)
    issued = dt.datetime.fromisoformat(row['token_issued_at'])
    expiry = max(issued + dt.timedelta(seconds=1), now - dt.timedelta(seconds=1))
    request('anthemsmith_recovery_orders', {'order_id': 'eq.' + oid}, method='PATCH',
            body={'expires_at': expiry.isoformat()})
    check = order(oid)
    if dt.datetime.fromisoformat(check['expires_at']) >= now:
        raise CanaryError('revocation_readback_failed')
    return {'stage': 'revoke', 'order_id': oid, 'revoked': True, 'payment_state': 'unverified'}


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    inputs = event.get('inputs', {})
    oid, action = inputs.get('order_id', ''), inputs.get('action', '')
    if not re.fullmatch(r'AS-OX-CANARY-[A-Z0-9-]{6,40}', oid):
        raise CanaryError('synthetic_namespace_required')
    if action == 'seed':
        result = seed(oid, inputs.get('token_sha256', ''))
    elif action == 'analyze':
        result = analyze(oid)
    elif action == 'status':
        row, uploads = inspect(oid)
        result = {'stage': 'status', 'order_id': oid, 'status': row['status'],
                  'payment_state': row['payment_state'], 'uploads': uploads}
    elif action == 'revoke':
        result = revoke(oid)
    else:
        raise CanaryError('unsupported_action')
    Path('canary-evidence.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Do not print API error bodies, keys, request headers or customer rows.
        reason = str(exc) if isinstance(exc, CanaryError) else type(exc).__name__
        print('CANARY_FAILED:', reason)
        sys.exit(1)
