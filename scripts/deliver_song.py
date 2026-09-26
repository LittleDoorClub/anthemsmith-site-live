#!/usr/bin/env python3
"""AnthemSmith delivery — SMS (Twilio) or Email (Resend) the finished song link.
Runs on GitHub Actions after the song commit. Reads ORDER_ID/DELIVERY/SMS_TO/EMAIL from env.
Delivery outcome is written to songs/<OID>.delivery.json for status tracking.
Never fails the forge; delivery errors are logged, not raised (song is already safe)."""
import json, os, sys, time, urllib.parse, urllib.request

OID = os.environ["ORDER_ID"]
DELIVERY = (os.environ.get("DELIVERY") or "").strip().lower()
SMS_TO = (os.environ.get("SMS_TO") or "").strip()
EMAIL = (os.environ.get("EMAIL") or "").strip()
GABE_CC = (os.environ.get("GABE_CC") or "").strip()
TW_SID = os.environ.get("TWILIO_SID", "")
TW_TOKEN = os.environ.get("TWILIO_TOKEN", "")
TW_FROM = os.environ.get("TWILIO_FROM", "")
RESEND = os.environ.get("RESEND_KEY", "")

SONG_URL = f"https://anthemsmith.com/songs/{OID}.mp3"
FULL_URL = f"https://anthemsmith.com/songs/{OID}-full.mp3"
LINK = FULL_URL  # full version is the deliverable

# ---- delivery status tracking ----
# Status values: submitted (provider accepted), delivered (terminal), failed (error)
# SMS: Twilio returns "queued"/"sent" on POST — NOT terminal "delivered".
# Email: Resend returns an id on POST — NOT proof of inbox delivery.
# Record the provider's own reported status in detail so app.js can show it.
def write_delivery_status(status, detail="", provider_status=""):
    """Write songs/<OID>.delivery.json so app.js can show notification outcome."""
    d = {"order_id": OID, "delivery_status": status, "ts": time.time(),
         "mode": DELIVERY, "detail": str(detail)[:400]}
    if provider_status:
        d["provider_status"] = str(provider_status)[:40]
    try:
        os.makedirs("songs", exist_ok=True)
        json.dump(d, open(f"songs/{OID}.delivery.json", "w"), indent=1)
    except Exception:
        pass  # never crash the forge over delivery tracking

def sms_send(to, body):
    data = urllib.parse.urlencode({"From": TW_FROM, "To": to, "Body": body}).encode()
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{TW_SID}/Messages.json",
        data=data, method="POST",
        headers={"Authorization": "Basic " + __import__("base64").b64encode(f"{TW_SID}:{TW_TOKEN}".encode()).decode(),
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    print(f"SMS -> {to}: {d.get('status')} ({d.get('sid')})")
    return {"status": d.get("status"), "sid": d.get("sid"), "provider": "twilio"}

def email_send(to, subject, text):
    data = json.dumps({"from": "AnthemSmith <songs@anthemsmith.com>",
                       "to": [to], "subject": subject, "text": text}).encode()
    req = urllib.request.Request("https://api.resend.com/emails",
        data=data, method="POST",
        headers={"Authorization": "Bearer " + RESEND, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        # Capture Resend's JSON error body — it names the exact reason
        # (domain_not_verified / missing_permissions / invalid_key) vs the
        # useless bare "HTTP Error 403: Forbidden" from str(e) alone.
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise RuntimeError(f"resend {e.code} {body[:200]}") from e
    print(f"EMAIL -> {to}: {d}")
    return {"status": "sent", "id": d.get("id"), "provider": "resend"}

def notify_gabe(line):
    if GABE_CC:
        try:
            sms_send(GABE_CC, line)
        except Exception as e:
            print("gabe cc failed:", e)

msg = (f"\U0001f3b5 Your AnthemSmith song is ready! Play/download: {LINK} "
       f"(order {OID}) \u2014 love it? Add another song for $3 at anthemsmith.com")

# ---- DELIVERY CONTRACT: require exactly one valid destination ----
# Do NOT default to SMS when the mode is missing or ambiguous —
# a missing mode with a valid email is silently lost in the old code.
if not DELIVERY:
    # No delivery mode specified — try to infer from available contacts
    if SMS_TO and not EMAIL:
        DELIVERY = "sms"
    elif EMAIL and not SMS_TO:
        DELIVERY = "email"
    else:
        write_delivery_status("failed", "no delivery mode specified")
        print("no delivery mode on order", OID)
        notify_gabe(f"\u26a0\ufe0f AS order {OID}: NO delivery mode — song waiting at {LINK}")
        sys.exit(0)

if DELIVERY == "sms":
    if not SMS_TO:
        write_delivery_status("failed", "SMS mode selected but no phone number")
        notify_gabe(f"\u26a0\ufe0f AS order {OID}: SMS mode but NO phone — song at {LINK}")
        print("delivery skip: SMS mode, no phone number")
    else:
        try:
            result = sms_send(SMS_TO, msg)
            # Twilio POST returns "queued" or "sent" — NOT terminal "delivered".
            # Record as submitted with the provider status + SID for later lookup.
            sid = result.get("sid", "")
            pstat = result.get("status", "unknown")
            write_delivery_status("submitted", f"twilio:{sid}", provider_status=pstat)
            notify_gabe(f"\U0001f402 AS order {OID} SMS submitted to {SMS_TO[:6]}*** — Twilio {pstat} ({sid})")
        except Exception as e:
            write_delivery_status("failed", str(e)[:120])
            notify_gabe(f"\u26a0\ufe0f AS order {OID}: SMS FAILED ({str(e)[:80]}) — song at {LINK}")
            print("delivery error:", e)

elif DELIVERY == "email":
    if not EMAIL:
        write_delivery_status("failed", "Email mode selected but no email address")
        notify_gabe(f"\u26a0\ufe0f AS order {OID}: Email mode but NO address — song at {LINK}")
        print("delivery skip: email mode, no address")
    else:
        try:
            result = email_send(EMAIL, "Your AnthemSmith song is ready \U0001f3b5",
                       f"Your song is ready!\n\nPlay / download: {LINK}\n\n30-second hook: {SONG_URL}\n\n"
                       f"Order ID: {OID}\nLove it? Add another song for $3 at https://anthemsmith.com\n\n\u2014 AnthemSmith")
            # Resend returns an id on POST — NOT proof of inbox delivery.
            write_delivery_status("submitted", f"resend:{result.get('id', '')}", provider_status="sent")
            notify_gabe(f"\U0001f402 AS order {OID} delivered by EMAIL to {EMAIL}")
        except Exception as e:
            write_delivery_status("failed", str(e)[:400])
            notify_gabe(f"\u26a0\ufe0f AS order {OID}: EMAIL FAILED ({str(e)[:120]}) — song at {LINK}")
            print("delivery error:", e)

else:
    write_delivery_status("failed", f"unknown delivery mode: {DELIVERY}")
    notify_gabe(f"\u26a0\ufe0f AS order {OID}: unknown delivery mode '{DELIVERY}' — song at {LINK}")
    print("unknown delivery mode", DELIVERY)

print("delivery step complete")
