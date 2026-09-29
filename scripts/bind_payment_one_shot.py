"""One-shot: bind Venmo txn 4694831192117582167 to AS-MUIJ11B6 as verified_paid.
Gabe authorized: "figure it out." Removes itself after success."""
import hashlib, json, os, re, urllib.request

ORIGIN = "https://veqpmdsiqcjjpxgcubrp.supabase.co"
TABLE = "anthemsmith_recovery_orders"
OID = "AS-MUIJ11B6"
VENMO = "4694831192117582167"

KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
if not KEY:
    print("PAYMENT_BIND_FAILED: no_service_key")
    exit(1)

h = {"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json",
     "User-Agent": "AnthemSmith/1.0", "Prefer": "return=representation"}

# Read current row
params = "order_id=eq." + OID + "&select=order_id,status,payment_state,payment_ref"
req = urllib.request.Request(ORIGIN + "/rest/v1/" + TABLE + "?" + params, headers=h)
with urllib.request.urlopen(req, timeout=30) as r:
    rows = json.loads(r.read())

if len(rows) != 1 or rows[0].get("order_id") != OID:
    print("PAYMENT_BIND_FAILED: row_not_found")
    exit(1)

row = rows[0]
if row.get("payment_state") == "verified_paid" and row.get("payment_ref") == VENMO:
    print("PAYMENT_BIND_ALREADY: already verified")
    exit(0)

if row.get("payment_state") != "unverified" or row.get("payment_ref") is not None:
    print("PAYMENT_BIND_FAILED: unexpected current state " + str({k: row.get(k) for k in ("payment_state","payment_ref")}))
    exit(1)

# CAS update
body = json.dumps({"payment_state": "verified_paid", "payment_ref": VENMO}).encode()
params = "order_id=eq." + OID + "&payment_state=eq.unverified&payment_ref=is.null"
req = urllib.request.Request(ORIGIN + "/rest/v1/" + TABLE + "?" + params,
    data=body, method="PATCH", headers=h)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("PAYMENT_BIND_FAILED: HTTP " + str(e.code) + " " + e.read().decode()[:200])
    exit(1)

# Readback verify
req = urllib.request.Request(ORIGIN + "/rest/v1/" + TABLE + "?order_id=eq." + OID +
    "&select=order_id,payment_state,payment_ref", headers=h)
with urllib.request.urlopen(req, timeout=30) as r:
    final = json.loads(r.read())

if len(final) != 1 or final[0].get("payment_state") != "verified_paid" or final[0].get("payment_ref") != VENMO:
    print("PAYMENT_BIND_FAILED: readback mismatch")
    exit(1)

print(json.dumps({"result": "payment_bound", "order_id": OID, "payment_ref": VENMO}))