"""Record the SMS send result on the recovery order row. RECORD ONLY.

Writes `delivery_result.sms.<last4>` from send-results.json (written by the
recovery-send-token workflow). It messages nobody, changes no status, touches no
payment field. Without this the order row reads "paid, zero delivery" after a real
send, which is exactly the false picture that re-opened this lane on 2026-11-08.

Exits 0 when there is nothing to record (mint failed, no send attempted).
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = "https://veqpmdsiqcjjpxgcubrp.supabase.co"
TABLE = "anthemsmith_recovery_orders"
OID = "AS-MUIJ11B6"


def _key():
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        print("RECORD_SKIPPED: no_service_key")
        sys.exit(0)
    return key


def main():
    try:
        with open("send-results.json", encoding="utf-8") as handle:
            results = json.load(handle)
    except FileNotFoundError:
        print("RECORD_SKIPPED: no send-results.json (nothing was sent)")
        return
    if not results:
        print("RECORD_SKIPPED: empty results")
        return

    key = _key()
    headers = {"apikey": key, "Authorization": "Bearer " + key,
               "Content-Type": "application/json", "User-Agent": "AnthemSmith/1.0",
               "Prefer": "return=representation"}

    query = urllib.parse.urlencode({"order_id": "eq." + OID, "select": "order_id,delivery_result"})
    request = urllib.request.Request(ORIGIN + "/rest/v1/" + TABLE + "?" + query, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        rows = json.loads(response.read())
    if len(rows) != 1:
        print("RECORD_FAILED: row_count_" + str(len(rows)))
        sys.exit(1)

    current = rows[0].get("delivery_result") or {}
    if not isinstance(current, dict):
        current = {}
    sms = dict(current.get("sms") or {}) if isinstance(current.get("sms"), dict) else {}
    for item in results:
        sms[str(item.get("to_suffix") or "unknown")] = item
    merged = dict(current)
    merged["sms"] = sms

    body = json.dumps({"delivery_result": merged}).encode()
    patch_q = urllib.parse.urlencode({"order_id": "eq." + OID})
    patch = urllib.request.Request(ORIGIN + "/rest/v1/" + TABLE + "?" + patch_q,
                                   data=body, method="PATCH", headers=headers)
    try:
        with urllib.request.urlopen(patch, timeout=30) as response:
            out = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        print("RECORD_FAILED: HTTP " + str(exc.code) + " " + exc.read().decode()[:200])
        sys.exit(1)

    if len(out) != 1:
        print("RECORD_FAILED: readback_count_" + str(len(out)))
        sys.exit(1)

    got = out[0].get("delivery_result") or {}
    got_sms = got.get("sms") if isinstance(got, dict) else None
    ok = isinstance(got_sms, dict) and all(
        str(item.get("to_suffix") or "unknown") in got_sms for item in results)
    keys = ",".join(sorted(got_sms.keys())) if isinstance(got_sms, dict) else "none"
    print("RECORDED: delivery_result.sms keys=" + keys)
    print("VERIFIED: " + str(ok))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
