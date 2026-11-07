"""Read-only shape probe for one recovery order. Mutates nothing.

Answers the only question the recovery lane needs answered without exposing
personal data: does the owed order still hold its brief and delivery contact,
is any payment reference present, is its capability token set and expired, and
how many uploads does it have?

Prints NO phone, NO email, NO brief values, NO token bytes, NO key.
Any write in this file is a defect.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ORIGIN = 'https://veqpmdsiqcjjpxgcubrp.supabase.co'
ORDER = 'AS-MUIJ11B6'
TABLE = 'anthemsmith_recovery_orders'
SELECT = 'order_id,status,payment_state,payment_ref,token_sha256,token_issued_at,expires_at,brief,delivery,delivery_result'


class StateError(Exception):
    pass


def _key():
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    if not key:
        raise StateError('no_service_key')
    return key


def get(path, params, extra=None):
    key = _key()
    url = ORIGIN + '/rest/v1/' + path + '?' + urllib.parse.urlencode(params)
    headers = {'apikey': key, 'Authorization': 'Bearer ' + key,
               'User-Agent': 'AnthemSmith/1.0', 'Accept': 'application/json'}
    headers.update(extra or {})
    request = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 206):
                raise StateError('unexpected_status_' + str(response.status))
            return response.read(512 * 1024), dict(response.headers)
    except urllib.error.HTTPError as exc:
        raise StateError('rest_http_' + str(exc.code)) from None


def main():
    raw, _ = get(TABLE, {'order_id': 'eq.' + ORDER, 'select': SELECT, 'limit': '2'})
    rows = json.loads(raw)
    if len(rows) != 1 or rows[0].get('order_id') != ORDER:
        raise StateError('order_row_count_' + str(len(rows)))
    row = rows[0]
    brief = row.get('brief') or {}
    delivery = row.get('delivery') or {}
    receipt = row.get('delivery_result') or {}
    phone = str(delivery.get('sms_to') or '')
    now = datetime.now(timezone.utc)
    expires = row.get('expires_at')
    expired = None
    if expires:
        try:
            expired = datetime.fromisoformat(expires) < now
        except Exception:
            expired = 'unparsed'
    up_raw, _ = get('anthemsmith_recovery_uploads',
                    {'order_id': 'eq.' + ORDER, 'select': 'object_path,status,sha256,byte_size'},
                    {'Prefer': 'count=exact'})
    uploads = json.loads(up_raw)
    state = {
        'order_id': ORDER,
        'status': row.get('status'),
        'payment_state': row.get('payment_state'),
        'payment_ref_present': row.get('payment_ref') is not None,
        'brief_keys': sorted(brief.keys()),
        'brief_has_recovery_evidence': bool(brief.get('recovery_evidence')),
        'brief_category_present': bool(brief.get('category')),
        'brief_voice_present': bool(brief.get('voice')),
        'delivery_mode': delivery.get('mode'),
        'delivery_phone_present': bool(phone),
        'delivery_phone_suffix': phone[-4:] if len(phone) >= 4 else '',
        'delivery_consent_verified': delivery.get('consent_verified'),
        'delivery_contact_source': delivery.get('contact_source'),
        'token_present': bool(row.get('token_sha256')),
        'token_issued_at': row.get('token_issued_at'),
        'expires_at': expires,
        'token_expired': expired,
        'delivery_result_keys': sorted(receipt.keys()) if isinstance(receipt, dict) else 'not_object',
        'uploads_count': len(uploads),
        'uploads_ready': sum(1 for u in uploads if u.get('status') == 'ready'),
        'read_only': True,
        'writes_performed': 0,
    }
    print(json.dumps(state, indent=2))
    with open('order-state.json', 'w', encoding='utf-8') as handle:
        handle.write(json.dumps(state, indent=2))


if __name__ == '__main__':
    try:
        main()
    except StateError as exc:
        print('STATE_FAILED: ' + str(exc))
        sys.exit(1)
