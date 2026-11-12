"""Set ONE recovery-order status, with the evidence that justifies it in the same write.

WHY THIS EXISTS
    order_workflow.py writes songs/<oid>.json with status "audio_ready" but never
    PATCHes anthemsmith_recovery_orders.status, so the stored row can still read
    "source_received" long after the lane delivered. A lying status row is worse
    than a missing one: the next reader concludes the lane never ran.

CONTRACT
    Runs in GitHub Actions only — SUPABASE_SERVICE_ROLE_KEY from the repo secret
    is the one hand that can write this table (RLS on, zero policies: deny-all to
    anon/authenticated, service_role bypasses). Same shape of record as
    seed_test_order.py and bind_payment_one_shot.py.

    --mode inspect   read only. Prints the row's column names and current status.
                     Mutates nothing. Safe to run any time.
    --mode write     exactly one PATCH, and only if all of these hold:
                       * songs/<oid>.json in the checked-out main already says the
                         target status (the repo is the truth this row must match);
                       * the named run_id exists in this repo, is completed and
                         successful, and its head_sha equals --main-sha;
                       * the row's current status is source_received (the only
                         transition this tool allows) or already the target, in
                         which case it writes nothing and exits 0;
                       * the PATCH returns exactly one row;
                       * a fresh GET re-reads the target status.
                     Any failed guard is a hold: exit 1, zero writes.

    Never prints phone, email, brief values, token bytes or the key. Evidence is
    column names, statuses, ids and counts only.

    Any second write path to this table is a defect — this tool is the single
    sanctioned hand for status repair.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ORIGIN = "https://veqpmdsiqcjjpxgcubrp.supabase.co"
TABLE = "anthemsmith_recovery_orders"
API = "https://api.github.com"
ALLOWED_TARGETS = ("audio_ready", "delivered")
ALLOWED_SOURCES = ("source_received",)
# Columns that could carry run/sha provenance, most specific first. Chosen from
# the live row's own key list at run time — never assumed.
PROVENANCE_COLUMNS = ("status_provenance", "provenance", "status_evidence")


class Hold(Exception):
    """A guard refused the write. Always means zero writes performed."""


def service_key():
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise Hold("no_service_key")
    return key


def rest(method, params=None, body=None, prefer=None):
    key = service_key()
    url = ORIGIN + "/rest/v1/" + TABLE
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "User-Agent": "AnthemSmith/1.0",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(512 * 1024)
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raise Hold("rest_http_%s:%s" % (exc.code, postgrest_reason(exc.read(400))))


def postgrest_reason(raw):
    """PostgREST's `details` field quotes the failing row's values.

    Keep the SQLSTATE code, the constraint name and the message — enough to
    diagnose — and drop `details` entirely so CI logs never carry row content.
    """
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        body = json.loads(text)
    except ValueError:
        return text[:120]
    if not isinstance(body, dict):
        return text[:120]
    parts = [str(body.get("code") or "").strip(),
             _constraint_name(body),
             str(body.get("message") or "").strip()[:80]]
    return "/".join(part for part in parts if part)


def _constraint_name(body):
    """PostgREST only fills `constraint` for some SQLSTATEs; the 23514 message
    still names it, so recover it from there rather than losing the diagnosis."""
    name = str(body.get("constraint") or "").strip()
    if name:
        return name
    match = re.search(r'check constraint "([^"]+)"', str(body.get("message") or ""))
    return match.group(1) if match else ""


def gh(path):
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise Hold("no_gh_token")
    request = urllib.request.Request(
        API + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "anthemsmith-order-status",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise Hold("gh_http_%s:%s" % (exc.code, path))


def repo_manifest(oid):
    path = os.path.join("songs", oid + ".json")
    if not os.path.isfile(path):
        raise Hold("manifest_missing:" + path)
    with open(path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("order_id") != oid:
        raise Hold("manifest_order_mismatch")
    return manifest


def verify_run(run_id, main_sha):
    repo = os.environ.get("GH_REPO", "")
    if not repo:
        raise Hold("no_gh_repo")
    run = gh("/repos/%s/actions/runs/%s" % (repo, run_id))
    state, conclusion = run.get("status"), run.get("conclusion")
    if state != "completed" or conclusion != "success":
        raise Hold("run_not_green:%s/%s" % (state, conclusion))
    if run.get("head_sha") != main_sha:
        raise Hold("run_head_sha_mismatch:" + str(run.get("head_sha")))
    return {
        "run_id": run.get("id"),
        "name": run.get("name"),
        "event": run.get("event"),
        "head_sha": run.get("head_sha"),
        "created_at": run.get("created_at"),
        "conclusion": conclusion,
    }


def finish(evidence):
    name = evidence["order_id"] + ".status-evidence.json"
    with open(name, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(evidence, indent=2))
    print("ORDER_STATUS_EVIDENCE " + json.dumps(evidence, sort_keys=True))
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--main-sha", required=True)
    parser.add_argument("--mode", choices=("inspect", "write"), default="inspect")
    args = parser.parse_args(argv)

    oid = args.order_id
    if not re.fullmatch(r"AS-[A-Z0-9-]{3,60}", oid):
        raise Hold("bad_order_id")
    if args.status not in ALLOWED_TARGETS:
        raise Hold("target_not_allowed:" + args.status)
    if not re.fullmatch(r"[0-9a-f]{40}", args.main_sha):
        raise Hold("bad_main_sha")
    if not re.fullmatch(r"[0-9]{6,20}", args.run_id):
        raise Hold("bad_run_id")

    rows = rest("GET", {"order_id": "eq." + oid, "select": "*", "limit": "2"})[1]
    if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("order_id") != oid:
        raise Hold("row_count_%s" % (len(rows) if isinstance(rows, list) else "unparsed"))
    row = rows[0]

    manifest = repo_manifest(oid)
    run = verify_run(args.run_id, args.main_sha)

    evidence = {
        "order_id": oid,
        "mode": args.mode,
        "row_columns": sorted(row.keys()),
        "from_status": row.get("status"),
        "to_status": args.status,
        "repo_manifest": {
            "status": manifest.get("status"),
            "private_audio": manifest.get("private_audio"),
            "duration_full": manifest.get("duration_full"),
        },
        "run": run,
        "main_sha_claimed": args.main_sha,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "writes_performed": 0,
    }

    if manifest.get("status") != args.status:
        raise Hold("repo_manifest_not_" + args.status + ":" + str(manifest.get("status")))

    if args.mode == "inspect":
        evidence["verdict"] = "INSPECT_ONLY"
        finish(evidence)
        return 0

    current = row.get("status")
    if current == args.status:
        evidence["verdict"] = "ALREADY_CORRECT"
        finish(evidence)
        return 0
    if current not in ALLOWED_SOURCES:
        raise Hold("transition_not_allowed:%s->%s" % (current, args.status))

    provenance = {
        "source": "recovery-set-order-status",
        "run_id": run["run_id"],
        "run_url": "https://github.com/" + os.environ.get("GH_REPO", "")
        + "/actions/runs/" + str(run["run_id"]),
        "main_sha": args.main_sha,
        "repo_manifest_status": manifest.get("status"),
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    patch = {"status": args.status}
    provenance_column = next((name for name in PROVENANCE_COLUMNS if name in row), None)
    if provenance_column:
        patch[provenance_column] = provenance
    elif "run_id" in row and "main_sha" in row:
        patch["run_id"] = run["run_id"]
        patch["main_sha"] = args.main_sha
    else:
        evidence["provenance_columns_absent"] = True

    status, written = rest(
        "PATCH",
        {"order_id": "eq." + oid},
        patch,
        prefer="return=representation",
    )
    if status not in (200, 206):
        raise Hold("patch_status_" + str(status))
    if not isinstance(written, list) or len(written) != 1 or written[0].get("order_id") != oid:
        raise Hold("patch_row_count_%s" % (len(written) if isinstance(written, list) else "unparsed"))

    _, back = rest("GET", {"order_id": "eq." + oid, "select": "order_id,status", "limit": "2"})
    if not isinstance(back, list) or len(back) != 1 or back[0].get("status") != args.status:
        raise Hold("readback_mismatch:" + json.dumps(back)[:200])

    evidence.update(
        verdict="WRITTEN",
        writes_performed=1,
        columns_written=sorted(patch.keys()),
        provenance_column=provenance_column,
        readback=back[0],
    )
    finish(evidence)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Hold as exc:
        print("ORDER_STATUS_HELD: " + str(exc))
        sys.exit(1)
