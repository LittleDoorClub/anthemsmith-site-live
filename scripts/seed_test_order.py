"""Seed the canonical test order AS-TEST-MAYA-20261110 — the seed that had no hand.

Written 2026-11-10 ~21:0x ET by the Anthemist lane (16a9) on Gabe's direct
order after the takeover stalled graded-as-blocked: the seed workflow itself
is the service-role hand, because the Actions env is the one place the key
already lives (bind-payment.yml is the shape of record).

Runs ONLY in GitHub Actions (env SUPABASE_SERVICE_ROLE_KEY). Contract =
scripts/order_workflow.py canonical_order() read from true remote
sha 451f9f9a; bucket = photo_intake.py's BUCKET. Spec: filing 16 at the
Anthemist lane. Companion files in the push batch:
  scripts/seed_details.txt  — brief.details, all 20 captions verbatim (4,772 chars)
  seed-assets/*.jpg         — the 3 photos (owner's own public post photos)

Safety shape (house pattern): read first · refuse on any mismatch ·
hash-verify the photo bytes before upload · read-back verify every write ·
idempotent re-run exits 0 when the seed already stands gate-shaped.
"""
import hashlib
import json
import os
import pathlib
import sys
import uuid
import urllib.error
import urllib.request

ORIGIN = "https://veqpmdsiqcjjpxgcubrp.supabase.co"
BUCKET = "anthemsmith-orders"
OID = "AS-TEST-MAYA-20261110"
PAYMENT_REF = "TEST-INTERNAL-20261110"  # test-internal marker: NO money, NO customer
HERE = pathlib.Path(__file__).resolve().parent

PHOTOS = [  # (filename, expected bytes, expected sha256 prefix)
    ("3711a22f-8e7d-414f-bd5f-3ab1a0387e2d.jpg", 747275, "5da54179640a0851"),
    ("5c460949-b240-4328-9d87-17e0822b6443.jpg", 172839, "f472b3e865b20a63"),
    ("67e4f9f1-112c-4cba-85ec-107df6b077b8.jpg", 282551, "2f0b49f072e6dfc7"),
]

KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def fail(msg):
    print("SEED_FAILED: " + msg)
    sys.exit(1)


def req(url, data=None, method="GET", ctype="application/json", extra=None):
    h = {"apikey": KEY, "Authorization": "Bearer " + KEY, "User-Agent": "AnthemSmith/1.0"}
    if ctype:
        h["Content-Type"] = ctype
    if extra:
        h.update(extra)
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body and ctype == "application/json" else body)
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    if not KEY:
        fail("no_service_key")

    details = (HERE / "seed_details.txt").read_text()
    if len(details) != 4772:
        fail("details_len_%d" % len(details))

    assets = HERE.parent / "seed-assets"
    blobs = []
    for name, size, sha12 in PHOTOS:
        raw = (assets / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != size or not digest.startswith(sha12):
            fail("photo_mismatch_" + name)
        blobs.append((name, raw, digest))
    print("photos verified: 3/3 byte-exact")

    brief = {"intake": "photos", "details": details, "category": "My Story",
             "voice": "female", "mood": "warm, funny, gothic",
             "reference_url": "", "public_audio_consent": True}
    delivery = {"mode": "email", "email": "gabrieljtao@gmail.com",
                "sms_to": "", "consent_verified": True}

    # 1 — the order row (idempotent: an existing gate-shaped row stands)
    st, rows = req(ORIGIN + "/rest/v1/anthemsmith_recovery_orders?order_id=eq." + OID +
                   "&select=order_id,status,payment_state,payment_ref")
    if st != 200:
        fail("orders_read_%s" % st)
    if rows:
        row = rows[0]
        if not (row.get("payment_state") == "verified_paid" and row.get("payment_ref") == PAYMENT_REF
                and row.get("status") == "source_received"):
            fail("existing_row_not_gate_shaped")
        print("order row already stands gate-shaped")
    else:
        # token_sha256 is NOT NULL + hex64 + UNIQUE on the live table; a test seed
        # uses the deterministic hash of its own order id (no customer token exists).
        token_sha256 = hashlib.sha256(OID.encode()).hexdigest()
        st, body = req(ORIGIN + "/rest/v1/anthemsmith_recovery_orders", method="POST",
                    data=json.dumps({"order_id": OID, "token_sha256": token_sha256,
                                     "status": "source_received",
                                     "payment_state": "verified_paid", "payment_ref": PAYMENT_REF,
                                     "brief": brief, "delivery": delivery}).encode(),
                    extra={"Prefer": "return=minimal"})
        if st not in (200, 201):
            fail("order_insert_%s_%s" % (st, str(body)[:300]))
        print("order row inserted")

    # payment_ref uniqueness across verified_paid rows (the gate re-reads by ref)
    st, rows = req(ORIGIN + "/rest/v1/anthemsmith_recovery_orders?payment_state=eq.verified_paid"
                   "&payment_ref=eq." + PAYMENT_REF + "&select=order_id")
    if st != 200 or len(rows) != 1 or rows[0].get("order_id") != OID:
        fail("payment_ref_not_unique")

    # 2 — objects + upload rows
    for name, raw, digest in blobs:
        path = OID + "/" + name
        st, _ = req(ORIGIN + "/storage/v1/object/" + BUCKET + "/" + path, method="POST",
                    data=raw, ctype="image/jpeg", extra={"x-upsert": "true"})
        if st not in (200, 201):
            fail("object_put_%s_%s" % (st, name))
        st, rows = req(ORIGIN + "/rest/v1/anthemsmith_recovery_uploads?object_path=eq." + path +
                       "&select=object_path,status,sha256")
        if st != 200:
            fail("uploads_read_%s" % st)
        if rows:
            if rows[0].get("status") != "ready" or rows[0].get("sha256") != digest:
                fail("existing_upload_row_mismatch_" + name)
        else:
            st, body = req(ORIGIN + "/rest/v1/anthemsmith_recovery_uploads", method="POST",
                        data=json.dumps({"id": str(uuid.uuid4()), "order_id": OID,
                                         "object_path": path, "status": "ready",
                                         "byte_size": len(raw), "mime_type": "image/jpeg",
                                         "sha256": digest}).encode(),
                        extra={"Prefer": "return=minimal"})
            if st not in (200, 201):
                fail("upload_row_insert_%s_%s_%s" % (st, name, str(body)[:300]))
        print("object + row ready:", path)

    # 3 — read-back: the gate's own shape
    st, ups = req(ORIGIN + "/rest/v1/anthemsmith_recovery_uploads?order_id=eq." + OID +
                  "&status=eq.ready&select=object_path,byte_size,sha256")
    total = sum(u["byte_size"] for u in ups) if st == 200 else -1
    if st != 200 or len(ups) != 3 or total > 50_000_000:
        fail("final_readback")
    print(json.dumps({"seed": "OK", "order_id": OID, "uploads_ready": len(ups),
                      "total_bytes": total}))
    print("NEXT (separate dispatch, same hand): anthemsmith-order.yml "
          "workflow_dispatch with inputs.order_id=" + OID)


if __name__ == "__main__":
    main()
