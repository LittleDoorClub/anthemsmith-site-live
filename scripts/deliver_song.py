#!/usr/bin/env python3
"""AnthemSmith delivery — SMS (Twilio) or Email (Resend) the finished song link.
Runs on GitHub Actions after the song commit. Reads ORDER_ID/DELIVERY/SMS_TO/EMAIL from env.
Never fails the forge: delivery errors are logged, not raised (song is already safe in the repo)."""
import json, os, sys, urllib.parse, urllib.request

OID = os.environ["ORDER_ID"]
DELIVERY = (os.environ.get("DELIVERY") or "sms").strip().lower()
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
    return d.get("status")

def email_send(to, subject, text):
    data = json.dumps({"from": "AnthemSmith <songs@anthemsmith.com>",
                       "to": [to], "subject": subject, "text": text}).encode()
    req = urllib.request.Request("https://api.resend.com/emails",
        data=data, method="POST",
        headers={"Authorization": "Bearer " + RESEND, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    print(f"EMAIL -> {to}: {d}")
    return d

def notify_gabe(line):
    if GABE_CC:
        try:
            sms_send(GABE_CC, line)
        except Exception as e:
            print("gabe cc failed:", e)

msg = (f"🎵 Your AnthemSmith song is ready! Play/download: {LINK} "
       f"(order {OID}) — love it? Add another song for $3 at anthemsmith.com")

if DELIVERY == "sms" and SMS_TO:
    try:
        st = sms_send(SMS_TO, msg)
        notify_gabe(f"🐂 AS order {OID} delivered by SMS to {SMS_TO[:6]}*** — status {st}")
    except Exception as e:
        print("delivery error:", e)
        notify_gabe(f"⚠️ AS order {OID}: SMS to customer FAILED ({str(e)[:80]}) — song is at {LINK}")
elif DELIVERY == "email" and EMAIL:
    try:
        email_send(EMAIL, "Your AnthemSmith song is ready 🎵",
                   f"Your song is ready!\n\nPlay / download: {LINK}\n\n30-second hook: {SONG_URL}\n\nOrder ID: {OID}\nLove it? Add another song for $3 at https://anthemsmith.com\n\n— AnthemSmith")
        notify_gabe(f"🐂 AS order {OID} delivered by EMAIL to {EMAIL}")
    except Exception as e:
        print("delivery error:", e)
        notify_gabe(f"⚠️ AS order {OID}: EMAIL to customer FAILED ({str(e)[:80]}) — song is at {LINK}")
else:
    # no delivery target captured (legacy order) — tell Gabe so nothing strands silently
    print("no delivery target on order", OID)
    notify_gabe(f"⚠️ AS order {OID}: NO delivery target — song waiting at {LINK}")

print("delivery step complete")
