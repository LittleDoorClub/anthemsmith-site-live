"""Restore ONLY AS-MUIJ11B6's lost private brief/contact from its original job.
No payment verification, token issue, customer send, or generation is authorized here.
GitHub credentials never follow a cross-origin log redirect; no log contents printed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = 'https://veqpmdsiqcjjpxgcubrp.supabase.co'
REPO = 'LittleDoorClub/anthemsmith-site-live'
ORDER = 'AS-MUIJ11B6'
RUN = 36253190570
JOB = 108434955217
TABLE = 'anthemsmith_recovery_orders'

class RecoveryError(Exception): pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def parse_original(raw):
    if len(raw) > 5 * 1024 * 1024:
        raise RecoveryError('original_log_too_large')
    text = raw.decode('utf-8', errors='strict')
    fields = {}
    for line in text.splitlines():
        m = re.search(r'Z\s{2,}(ORDER_ID|IG_URL|CATEGORY|MOOD|VOICE|DETAILS|DELIVERY|SMS_TO|EMAIL|PAID_STATUS|REFERENCE_URL): ?(.*)$', line)
        if not m:
            continue
        key, value = m.groups()
        if key in fields and fields[key] != value:
            raise RecoveryError('original_log_ambiguous_fields')
        fields[key] = value
    if fields.get('ORDER_ID') != ORDER or fields.get('DELIVERY') != 'sms':
        raise RecoveryError('original_order_or_delivery_mismatch')
    phone = fields.get('SMS_TO', '')
    if not re.fullmatch(r'\+[1-9]\d{7,14}', phone):
        raise RecoveryError('original_recipient_invalid')
    # Verified incident discriminator: later retry contains a different suffix.
    if not phone.endswith('6352'):
        raise RecoveryError('not_the_original_checkout_recipient')
    if fields.get('EMAIL') or fields.get('IG_URL') or fields.get('DETAILS') or fields.get('REFERENCE_URL'):
        raise RecoveryError('original_source_evidence_changed')
    if not fields.get('CATEGORY') or not fields.get('VOICE'):
        raise RecoveryError('original_preferences_missing')
    source = {'run_id': RUN, 'job_id': JOB, 'log_sha256': hashlib.sha256(raw).hexdigest()}
    brief = {'intake': 'photos', 'category': fields['CATEGORY'], 'mood': fields.get('MOOD', ''),
             'voice': fields['VOICE'], 'details': '', 'ig_url': '', 'ig_url2': '', 'reference_url': '',
             'recovery_evidence': source}
    delivery = {'mode': 'sms', 'sms_to': phone, 'email': '', 'consent_verified': False,
                'contact_source': 'original_checkout_request', 'contact_evidence': source}
    return brief, delivery


def original_log():
    token = os.environ['GH_TOKEN']
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
               'User-Agent': 'AnthemSmith/1.0', 'Cache-Control': 'no-cache'}
    base = 'https://api.github.com/repos/' + REPO
    with urllib.request.urlopen(urllib.request.Request(base + '/actions/jobs/' + str(JOB), headers=headers), timeout=35) as response:
        job = json.load(response)
    if job.get('run_id') != RUN or job.get('id') != JOB:
        raise RecoveryError('original_job_binding_failed')
    request = urllib.request.Request(base + '/actions/jobs/' + str(JOB) + '/logs?nocache=' + str(time.time_ns()), headers=headers)
    try:
        response = urllib.request.build_opener(NoRedirect()).open(request, timeout=35)
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise RecoveryError('original_log_access_failed') from None
        location = exc.headers.get('Location', '')
    else:
        with response:
            return response.read(5 * 1024 * 1024 + 1)
    parsed = urllib.parse.urlsplit(location)
    if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.blob.core.windows.net'):
        raise RecoveryError('unexpected_log_storage_host')
    with urllib.request.urlopen(urllib.request.Request(location, headers={'User-Agent': 'AnthemSmith/1.0'}), timeout=35) as response:
        return response.read(5 * 1024 * 1024 + 1)


def rest(params, body=None):
    key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
    headers = {'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
               'User-Agent': 'AnthemSmith/1.0', 'Prefer': 'return=representation'}
    request = urllib.request.Request(ORIGIN + '/rest/v1/' + TABLE + '?' + urllib.parse.urlencode(params),
        headers=headers, method='PATCH' if body is not None else 'GET',
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request, timeout=35) as response:
        data = json.loads(response.read(256 * 1024))
    if not isinstance(data, list):
        raise RecoveryError('private_order_store_shape')
    return data


def restore(raw, db=rest):
    brief, delivery = parse_original(raw)
    query = {'order_id': 'eq.' + ORDER, 'select': 'order_id,status,payment_state,payment_ref,brief,delivery', 'limit': '2'}
    rows = db(query)
    if len(rows) != 1 or rows[0].get('order_id') != ORDER:
        raise RecoveryError('existing_order_not_found')
    row = rows[0]
    if row.get('payment_state') != 'unverified' or row.get('payment_ref') is not None or row.get('status') != 'awaiting_sources':
        raise RecoveryError('existing_order_changed_review_required')
    if row.get('brief') == brief and row.get('delivery') == delivery:
        return {'order_id': ORDER, 'result': 'already_restored', 'payment_state': 'unverified',
                'consent_verified': False, 'contact_restored': True, 'sent': False}
    if row.get('brief') or row.get('delivery'):
        raise RecoveryError('existing_private_context_conflicts')
    changed = db({'order_id': 'eq.' + ORDER, 'status': 'eq.awaiting_sources',
                  'payment_state': 'eq.unverified', 'payment_ref': 'is.null', 'brief': 'eq.{}', 'delivery': 'eq.{}'},
                 {'brief': brief, 'delivery': delivery})
    if len(changed) != 1:
        raise RecoveryError('context_cas_lost')
    final = db(query)
    if len(final) != 1 or final[0].get('brief') != brief or final[0].get('delivery') != delivery:
        raise RecoveryError('context_readback_mismatch')
    if final[0].get('payment_state') != 'unverified' or final[0].get('payment_ref') is not None:
        raise RecoveryError('payment_state_changed')
    return {'order_id': ORDER, 'result': 'original_private_context_restored',
            'original_job': JOB, 'log_sha256': hashlib.sha256(raw).hexdigest(),
            'payment_state': 'unverified', 'consent_verified': False, 'contact_restored': True,
            'token_issued': False, 'sent': False, 'music_generated': False}


def main():
    result = restore(original_log())
    Path('restore-evidence.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        code = str(exc) if isinstance(exc, RecoveryError) else type(exc).__name__
        print('RESTORE_FAILED:', code)
        sys.exit(1)
