#!/usr/bin/env python3
"""Read-only projection: did the lane run, and did it deliver?

Three sources are shown side by side and never blended:

    intake.*     public.anthemsmith_recovery_orders -- the row's real gate fields
    manifest.*   songs/<oid>.json                 -- the pipeline's own record
    delivery.*   songs/<oid>.delivery.json        -- the transport's own record

Rules this file exists to enforce:
  * `lifecycle` is NEVER derived from `intake.status`. That column is an intake
    gate (see docs/status-is-an-intake-gate.md); reading it as lifecycle is how a
    healthy-looking pipeline stops.
  * a missing manifest projects `unknown` -- not `source_received`, not silence.
  * disagreement between sources is reported as `contradiction`, never smoothed.

No writes: this module has no PATCH or POST path at all.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = "https://veqpmdsiqcjjpxgcubrp.supabase.co"
ROW_SELECT = "order_id,status,payment_state,delivery_result"


def load_json(path):
    """Return the parsed file, or None when it simply is not there."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def read_row(oid):
    """Read the intake row with the service key (Actions only). Returns (row, note)."""
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        return None, "row_unreadable:no_service_key"
    query = urllib.parse.urlencode({"order_id": "eq." + oid, "select": ROW_SELECT, "limit": "2"})
    req = urllib.request.Request(
        ORIGIN + "/rest/v1/anthemsmith_recovery_orders?" + query,
        headers={"apikey": key, "Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read() or b"[]")
    except urllib.error.HTTPError as exc:
        return None, "row_unreadable:http_%d" % exc.code
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, "row_unreadable:" + type(exc).__name__
    if not rows:
        return None, "row_missing"
    if len(rows) > 1:
        return rows[0], "row_ambiguous:count_%d" % len(rows)
    return rows[0], None


def _failed(delivery):
    return bool(delivery) and "failed" in (delivery.get("delivery_status"), delivery.get("provider_status"))


def project(oid, row=None, manifest=None, delivery=None, notes=()):
    """Pure: three sources in, one verdict out. All the judgement lives here."""
    degraded = [n for n in notes if n]
    intake = {
        "status": (row or {}).get("status"),
        "payment_state": (row or {}).get("payment_state"),
    }
    if row is None:
        degraded.append("intake_missing")
    if manifest is None:
        degraded.append("manifest_missing")
    if delivery is None:
        degraded.append("delivery_missing")

    if manifest is None:
        lifecycle = "unknown"
    elif _failed(delivery) and manifest.get("status") == "audio_ready":
        lifecycle = "contradiction"
    else:
        lifecycle = manifest.get("status") or "unknown"

    return {
        "order_id": oid,
        "lifecycle": lifecycle,
        "did_the_lane_run": "yes" if manifest else "unknown",
        "did_it_deliver": (
            "no -- source says failed" if _failed(delivery)
            else "yes" if (delivery or {}).get("delivery_status") in ("delivered", "submitted")
            else "unknown"
        ),
        "answer": _answer(manifest, delivery, lifecycle),
        "intake": intake,
        "manifest": None if manifest is None else {
            "status": manifest.get("status"),
            "private_audio": manifest.get("private_audio"),
            "duration_full": manifest.get("duration_full"),
        },
        "delivery": None if delivery is None else {
            "delivery_status": delivery.get("delivery_status"),
            "provider_status": delivery.get("provider_status"),
        },
        "delivery_result": (row or {}).get("delivery_result"),
        "degraded": degraded,
    }


def _answer(manifest, delivery, lifecycle):
    """One sentence, built only from what the two source files say."""
    if manifest is None:
        return ("no manifest for this order: lifecycle unknown. The intake gate is not "
                "lifecycle evidence, so nothing is inferred from it.")
    ran = "lane ran: %s" % manifest.get("status", "unknown")
    if manifest.get("private_audio") is True:
        ran += " (private audio, %ss)" % manifest.get("duration_full", "?")
    if lifecycle == "contradiction":
        return ("sources disagree: manifest says audio_ready but delivery says %s/%s -- "
                "resolve before reporting either." % (
                    delivery.get("delivery_status"), delivery.get("provider_status")))
    if delivery is None:
        return "%s; delivery: unknown, no delivery record for this order." % ran
    return "%s; delivery %s (provider: %s)." % (
        ran, delivery.get("delivery_status", "unknown"), delivery.get("provider_status", "unknown"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--order-id", required=True)
    ap.add_argument("--songs-dir", default="songs")
    ap.add_argument("--row-file", help="offline: JSON row instead of a live read")
    args = ap.parse_args(argv)

    oid = args.order_id
    if not oid or "/" in oid or oid in (".", ".."):
        print("ORDER_LIFECYCLE_HELD: bad_order_id", file=sys.stderr)
        return 2

    if args.row_file:
        row = load_json(args.row_file)
        note = None if row else "row_unreadable:file_missing"
    else:
        row, note = read_row(oid)

    p = project(
        oid,
        row=row,
        manifest=load_json(os.path.join(args.songs_dir, oid + ".json")),
        delivery=load_json(os.path.join(args.songs_dir, oid + ".delivery.json")),
        notes=(note,),
    )
    print("ORDER_LIFECYCLE " + json.dumps(p, sort_keys=True))
    return 0 if p["lifecycle"] != "contradiction" else 1


if __name__ == "__main__":
    sys.exit(main())
