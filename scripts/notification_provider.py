"""Provider boundary: no recipient/body logging, no implicit CC, no success fabrication.

Credentials remain in the existing runner environment. A timed-out POST is UNKNOWN,
not safe to repeat. Status lookup is read-only. Provider acceptance != delivery.
"""
import base64
import json
import os
import re
import uuid
import urllib.error
import urllib.parse
import urllib.request


class NotificationError(Exception):
    def __init__(self, code, ambiguous=False):
        super().__init__(code)
        self.ambiguous = ambiguous


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise NotificationError('provider_redirect_refused', ambiguous=True)


def http_json(request):
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=35) as response:
            raw = response.read(131073)
            if len(raw) > 131072:
                raise NotificationError('provider_response_invalid', ambiguous=request.method == 'POST')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('shape')
            return data
    except urllib.error.HTTPError as exc:
        # Never echo error bodies: they may contain recipient or message contents.
        raise NotificationError('provider_http_' + str(exc.code),
                                ambiguous=request.method == 'POST' and exc.code >= 500) from None
    except NotificationError:
        raise
    except Exception:
        raise NotificationError('provider_result_unknown', ambiguous=request.method == 'POST') from None


def _twilio_headers():
    sid, key = os.environ.get('TWILIO_SID', ''), os.environ.get('TWILIO_TOKEN', '')
    if not re.fullmatch(r'AC[0-9a-fA-F]{32}', sid) or not key:
        raise NotificationError('sms_provider_not_configured')
    return sid, {'Authorization': 'Basic ' + base64.b64encode((sid + ':' + key).encode()).decode(),
                 'User-Agent': 'AnthemSmith/1.0'}


def _resend_headers():
    key = os.environ.get('RESEND_KEY', '')
    if not key:
        raise NotificationError('email_provider_not_configured')
    return {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
            'User-Agent': 'AnthemSmith/1.0'}


def _classify(provider, status):
    if provider == 'twilio':
        if status == 'delivered':
            return 'delivered'
        if status in ('failed', 'undelivered', 'canceled'):
            return 'failed'
        if status in ('accepted', 'scheduled', 'queued', 'sending', 'sent'):
            return 'submitted'
    elif provider == 'resend':
        if status in ('delivered', 'delivery_delayed', 'sent', 'queued', 'scheduled'):
            return 'delivered' if status == 'delivered' else 'submitted'
        if status in ('bounced', 'failed', 'canceled', 'suppressed'):
            return 'failed'
        # Open/click events do not replace a persisted delivery receipt here.
        if status in ('opened', 'clicked', 'complained'):
            return 'submitted'
    return 'unknown'


def valid_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def send(order, attempt, http=http_json):
    oid = order.get('order_id', '')
    if not re.fullmatch(r'AS-[A-Za-z0-9_-]{1,90}', oid):
        raise NotificationError('invalid_order_id')
    if order.get('consent_verified') is not True or not order.get('delivery_valid'):
        raise NotificationError('notification_not_authorized')
    from private_audio import context_token, PrivateAudioError
    context = order.get('private_song_access')
    try:
        context_token(oid, context)
    except PrivateAudioError:
        raise NotificationError('private_song_access_required') from None
    link = context['url']
    mode = order.get('delivery')
    if mode == 'sms':
        if order.get('email') or not re.fullmatch(r'\+[1-9]\d{7,14}', order.get('sms_to', '')):
            raise NotificationError('invalid_or_ambiguous_recipient')
        sid, headers = _twilio_headers()
        sender = os.environ.get('TWILIO_FROM', '')
        if not re.fullmatch(r'\+[1-9]\d{7,14}', sender):
            raise NotificationError('sms_sender_not_configured')
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        body = 'AnthemSmith: your full song is ready. Play or download: ' + link + '\nNo additional payment is required.'
        request = urllib.request.Request('https://api.twilio.com/2010-04-01/Accounts/' + sid + '/Messages.json',
            data=urllib.parse.urlencode({'From': sender, 'To': order['sms_to'], 'Body': body}).encode(),
            headers=headers, method='POST')
        result = http(request)
        provider_id, status = result.get('sid'), result.get('status')
        if not isinstance(provider_id, str) or not re.fullmatch(r'SM[0-9a-fA-F]{32}', provider_id):
            raise NotificationError('provider_receipt_missing', ambiguous=True)
        if result.get('to') not in (None, order['sms_to']):
            raise NotificationError('provider_recipient_mismatch', ambiguous=True)
        provider = 'twilio'
    elif mode == 'email':
        if order.get('sms_to') or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', order.get('email', '')):
            raise NotificationError('invalid_or_ambiguous_recipient')
        headers = _resend_headers()
        headers['Idempotency-Key'] = 'anthemsmith/' + oid + '/' + attempt
        request = urllib.request.Request('https://api.resend.com/emails', method='POST', headers=headers,
            data=json.dumps({'from': 'AnthemSmith <songs@anthemsmith.com>', 'to': [order['email']],
                'subject': 'Your AnthemSmith song is ready',
                'text': 'Your full song is ready.\n\nPlay or download: ' + link +
                        '\n\nNo additional payment is required.\n\n— AnthemSmith'}).encode())
        result = http(request)
        provider_id = result.get('id')
        if not valid_uuid(provider_id):
            raise NotificationError('provider_receipt_missing', ambiguous=True)
        if result.get('to') is not None:
            recipients = result['to'] if isinstance(result['to'], list) else [result['to']]
            if recipients != [order['email']]:
                raise NotificationError('provider_recipient_mismatch', ambiguous=True)
        status, provider = 'queued', 'resend'
    else:
        raise NotificationError('invalid_delivery_mode')
    return {'order_id': oid, 'delivery_status': _classify(provider, status), 'provider': provider,
            'provider_id': provider_id, 'provider_status': status, 'attempt_id': attempt}


def lookup(order, receipt, http=http_json):
    result = dict(receipt)
    provider, ident = result.get('provider'), result.get('provider_id')
    if not provider or not ident:
        # Upgrade the previous private legacy receipt format, never a public file.
        detail = result.get('detail', '')
        if isinstance(detail, str) and detail.startswith('twilio:'):
            provider, ident = 'twilio', detail.split(':', 1)[1]
        elif isinstance(detail, str) and detail.startswith('resend:'):
            provider, ident = 'resend', detail.split(':', 1)[1]
        else:
            raise NotificationError('provider_receipt_missing')
    if provider == 'twilio':
        if not re.fullmatch(r'SM[0-9a-fA-F]{32}', ident):
            raise NotificationError('provider_receipt_invalid')
        sid, headers = _twilio_headers()
        data = http(urllib.request.Request('https://api.twilio.com/2010-04-01/Accounts/' + sid +
            '/Messages/' + ident + '.json', headers=headers, method='GET'))
        if data.get('sid') != ident or data.get('to') != order.get('sms_to'):
            raise NotificationError('provider_receipt_owner_mismatch')
        status = data.get('status')
    elif provider == 'resend':
        if not valid_uuid(ident):
            raise NotificationError('provider_receipt_invalid')
        data = http(urllib.request.Request('https://api.resend.com/emails/' + ident,
                                           headers=_resend_headers(), method='GET'))
        recipients = data.get('to', [])
        if isinstance(recipients, str):
            recipients = [recipients]
        if data.get('id') != ident or recipients != [order.get('email')]:
            raise NotificationError('provider_receipt_owner_mismatch')
        status = data.get('last_event', '')
    else:
        raise NotificationError('provider_receipt_invalid')
    if not isinstance(status, str) or not re.fullmatch(r'[a-z_]{1,40}', status):
        raise NotificationError('provider_status_invalid')
    result.update({'provider': provider, 'provider_id': ident, 'provider_status': status,
                   'delivery_status': _classify(provider, status)})
    result.pop('detail', None)
    return result
