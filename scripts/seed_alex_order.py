"""Seed the canonical test order AS-TEST-ALEX-20261111 — the ALEX mirror of the MAYA seed.

Packet: Brain ref 113836 (anthem_alex_seed_20261112), chorus lock pre-verified
against origin/main scripts/anchor_law.py @ 45965c05 at the AnthemSmith desk
2026-11-12 ~00:1x ET. Ordered via Maya 0 (wires 63b8b3c3 / 1d751fc7), carried
by Nyx 1678 because the talk door to Ox was refusing at the wire layer.

Runs ONLY in GitHub Actions (env SUPABASE_SERVICE_ROLE_KEY). Contract =
scripts/order_workflow.py canonical_order() read from true remote
sha 451f9f9a; bucket = photo_intake.py's BUCKET. Spec: filing 16 at the
Anthemist lane. Companion files in the push batch:
  scripts/seed_details_alex.txt — brief.details, 1,442 chars CLEANED per Brain ref 114094
  (one quoted-lyric caption removed, Maya 0 ruling fix B), sha256 pinned below
  seed-assets/alex/*.jpg        — the 3 photos, first image of the 3 packet posts

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
OID = "AS-TEST-ALEX-20261111"
PAYMENT_REF = "TEST-INTERNAL-20261111"  # test-internal marker: NO money, NO customer
DETAILS_LEN = 1442
DETAILS_SHA256 = "d6235495ddd903b9d87873e2eda999a078baac1a6ab49f57622b29910939ac14"
HERE = pathlib.Path(__file__).resolve().parent

PHOTOS = [  # (filename, expected bytes, expected sha256) — packet posts, first image each
    # https://www.instagram.com/p/DecmW8XGlcc/
    ("68c0d453-f3cd-465c-ae7e-50ac46686547.jpg", 196046,
     "29dcb97321ea1093f52e44d8c0ffdb08b38fe510827f1b237805da61898485b0"),
    # https://www.instagram.com/p/DYbIl0QDcLR/
    ("b292adba-8812-48b0-a556-2c4006d1d565.jpg", 241309,
     "98cd9d55dfadd81f70989786627b7392ac7a12bb903681e85d7fabfbb43a0823"),
    # https://www.instagram.com/p/DL0gZHWJaWF/
    ("f8a7ca43-ade7-4af7-8232-1eb60e07b3c1.jpg", 232058,
     "de97b5cbee509e9dace23e13b5f6af091de71a1f89356b7249c961ce99125c0f"),
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


def analyses_cover(uploads, analyses):
    """Mirror order_workflow.prepare()'s gate: every ready upload has a row for
    its exact object AND its exact source sha. Path match alone is not coverage."""
    need = {(u.get("object_path"), str(u.get("sha256"))) for u in uploads}
    have = {(a.get("object_path"), str(a.get("source_sha256"))) for a in analyses}
    return bool(need) and need.issubset(have)


def analyses_rows():
    """The gate's own read: no analysis body is fetched or printed by the seed."""
    st, rows = req(ORIGIN + "/rest/v1/anthemsmith_recovery_analyses?order_id=eq." + OID +
                   "&select=object_path,source_sha256,model")
    if st != 200 or not isinstance(rows, list):
        return None
    return rows


def main():
    if not KEY:
        fail("no_service_key")

    details = (HERE / "seed_details_alex.txt").read_text(encoding="utf-8")
    if len(details) != DETAILS_LEN or hashlib.sha256(details.encode("utf-8")).hexdigest() != DETAILS_SHA256:
        fail("details_mismatch_len_%d" % len(details))
    print("details verified: %d chars, sha256 matches packet" % len(details))

    assets = HERE.parent / "seed-assets" / "alex"
    blobs = []
    for name, size, sha in PHOTOS:
        raw = (assets / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != size or digest != sha:
            fail("photo_mismatch_" + name)
        blobs.append((name, raw, digest))
    print("photos verified: 3/3 byte-exact")

    brief = {"intake": "photos", "details": details, "category": "My Story",
             "voice": "female", "mood": "warm, uplifting",
             "reference_title": "Alaska", "reference_artist": "Maggie Rogers",
             "reference_url": "", "public_audio_consent": True}
    delivery = {"mode": "email", "email": "cozyafllc@gmail.com",
                "sms_to": "", "consent_verified": True}

    # 1 — the order row (idempotent: an existing gate-shaped row stands)
    st, rows = req(ORIGIN + "/rest/v1/anthemsmith_recovery_orders?order_id=eq." + OID +
                   "&select=order_id,status,payment_state,payment_ref,brief")
    if st != 200:
        fail("orders_read_%s" % st)
    if rows:
        row = rows[0]
        if not (row.get("payment_state") == "verified_paid" and row.get("payment_ref") == PAYMENT_REF
                and row.get("status") == "source_received"):
            fail("existing_row_not_gate_shaped")
        print("order row already stands gate-shaped")
        # Brain ref 114094 (cleaned brief, fix B): an existing row keeps its gate
        # shape, but its brief must carry the cleaned text. PATCH only when the
        # stored digest differs, so a correct row stays zero-writes.
        stored = (row.get("brief") or {}).get("details") or ""
        if hashlib.sha256(stored.encode("utf-8")).hexdigest() != DETAILS_SHA256:
            st, body = req(ORIGIN + "/rest/v1/anthemsmith_recovery_orders?order_id=eq." + OID,
                           method="PATCH", data=json.dumps({"brief": brief}).encode(),
                           extra={"Prefer": "return=representation"})
            if st not in (200, 201, 204):
                fail("order_brief_patch_%s_%s" % (st, str(body)[:300]))
            st, back = req(ORIGIN + "/rest/v1/anthemsmith_recovery_orders?order_id=eq." + OID
                           + "&select=brief")
            got = ((back[0].get("brief") or {}).get("details") or "") if (st == 200 and back) else ""
            if hashlib.sha256(got.encode("utf-8")).hexdigest() != DETAILS_SHA256:
                fail("order_brief_readback_%s" % st)
            print("brief.details reconciled to cleaned text: %d chars, sha256 matches packet" % len(got))
        else:
            print("brief.details already carries the cleaned text")
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

    # 4 — vision analysis rows. order_workflow.prepare() HARD-FAILS the order
    # without them (vision_analysis_missing) and, while the table was missing,
    # died one step earlier at private_order_store_unavailable. Same engine and
    # same persist path as paid intake; read first so a re-run of a complete
    # seed spends nothing.
    rows = analyses_rows()
    if rows is None:
        fail("analyses_unreadable")
    if not analyses_cover(ups, rows):
        sys.path.insert(0, str(HERE))
        from photo_intake import PhotoIntakeError, inspect_private_sources
        api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        try:
            inspect_private_sources([u["object_path"] for u in ups], OID, ORIGIN, KEY, api_key)
        except PhotoIntakeError as exc:
            fail("vision_" + str(exc))
        except Exception:
            fail("vision_unavailable")
        rows = analyses_rows()
    if rows is None or not analyses_cover(ups, rows):
        fail("analysis_readback_incomplete")
    if any(not isinstance(r.get("model"), str) or not r["model"] for r in rows):
        fail("analysis_model_missing")

    print(json.dumps({"seed": "OK", "order_id": OID, "uploads_ready": len(ups),
                      "analyses_ready": len(rows), "total_bytes": total}))
    print("NEXT (separate dispatch, same hand): anthemsmith-order.yml "
          "workflow_dispatch with inputs.order_id=" + OID)


if __name__ == "__main__":
    main()
