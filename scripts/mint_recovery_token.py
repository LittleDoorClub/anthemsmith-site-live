"""Create a fresh upload capability token for AS-MUIJ11B6, store sha256 in DB, print recovery URL."""
import hashlib, json, os, re, secrets, base64, urllib.request
from datetime import datetime, timezone, timedelta

ORIGIN = "https://veqpmdsiqcjjpxgcubrp.supabase.co"
TABLE = "anthemsmith_recovery_orders"
OID = "AS-MUIJ11B6"
RECOVERY_URL = "https://anthemsmith.com/recovery.html"

KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
if not KEY:
    print("TOKEN_FAILED: no_service_key")
    exit(1)

h = {"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json",
     "User-Agent": "AnthemSmith/1.0", "Prefer": "return=representation"}

# Read current row
params = "order_id=eq." + OID + "&select=order_id,status,payment_state,payment_ref,token_sha256,token_issued_at,expires_at,delivery"
req = urllib.request.Request(ORIGIN + "/rest/v1/" + TABLE + "?" + params, headers=h)
with urllib.request.urlopen(req, timeout=30) as r:
    rows = json.loads(r.read())

if len(rows) != 1:
    print("TOKEN_FAILED: row_not_found")
    exit(1)

row = rows[0]
print(f"STATUS: {row['status']} PAYMENT: {row['payment_state']} REF: {row.get('payment_ref')}")

# Generate fresh token (32 random bytes -> 43-char base64url)
token_bytes = secrets.token_bytes(32)
token = base64.urlsafe_b64encode(token_bytes).decode().rstrip("=")
assert len(token) == 43
token_sha = hashlib.sha256(token.encode()).hexdigest()

now = datetime.now(timezone.utc)
expires = now + timedelta(hours=24)

# CAS update: only if token is not already set
if row.get("token_sha256"):
    # Revoke old token
    print(f"REVOKING existing token issued {row.get('token_issued_at')}")

body = json.dumps({
    "token_sha256": token_sha,
    "token_issued_at": now.isoformat(),
    "expires_at": expires.isoformat(),
    "status": "awaiting_sources"  # reset to awaiting_sources if it was changed
}).encode()

# CAS on no existing token OR explicit override (we're authorized)
if row.get("token_sha256"):
    params_cas = "order_id=eq." + OID + "&payment_state=eq.verified_paid&payment_ref=eq." + row["payment_ref"]
else:
    params_cas = "order_id=eq." + OID + "&token_sha256=is.null&payment_state=eq.verified_paid"

req = urllib.request.Request(ORIGIN + "/rest/v1/" + TABLE + "?" + params_cas,
    data=body, method="PATCH", headers=h)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("TOKEN_FAILED: HTTP " + str(e.code) + " " + e.read().decode()[:300])
    exit(1)

# Readback verify
req = urllib.request.Request(ORIGIN + "/rest/v1/" + TABLE + "?order_id=eq." + OID +
    "&select=order_id,token_sha256,token_issued_at,expires_at,status,delivery", headers=h)
with urllib.request.urlopen(req, timeout=30) as r:
    final = json.loads(r.read())

if len(final) != 1 or final[0].get("token_sha256") != token_sha:
    print("TOKEN_FAILED: readback_mismatch")
    exit(1)

# Get delivery contact
delivery = final[0].get("delivery", {})
phone = delivery.get("sms_to", "")
print(f"PHONE: {phone}")

recovery_url = f"{RECOVERY_URL}#order={OID}&token={token}"
print(f"URL: {recovery_url}")
print(f"TOKEN_SHA: {token_sha}")
print(f"EXPIRES: {expires.isoformat()}")
print(json.dumps({"result": "token_created", "order_id": OID, "delivery_mode": delivery.get("mode"),
                  "delivery_phone": phone, "url": recovery_url}))