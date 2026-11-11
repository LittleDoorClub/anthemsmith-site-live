#!/usr/bin/env python3
"""Create public.anthemsmith_recovery_analyses if it is missing, and prove its shape.

WHY THIS EXISTS: order_workflow.prepare() hard-fails the whole order with
`private_order_store_unavailable` whenever that table is absent — it demands the
vision rows the ENFORCER LAW (2026-11-10) keys to each uploaded object and its
source sha. Three anthemsmith-order dispatches for AS-TEST-MAYA-20261110 died on
the same 404 (runs 42953608507 / 42953794737 / 42953864070); the table has never
existed in this database, so no amount of re-dispatch could pass.

WHY IT RUNS HERE: PostgREST cannot execute DDL and this repo holds no database
password, so the only hand that can create a table is the service-role key inside
Actions. The only DDL door that key can reach is the house safe-deploy organ
public.gabs_safe_deploy_function (service_role-only ACL, blast-radius and
recursion gates, per-statement verify SQL, pg_notify schema reload).

Shape is not a guess: photo_intake.py:264 writes
`{order_id, object_path, source_sha256, model, provider_request_id, analysis}`
with on_conflict=(order_id, object_path, source_sha256, model), and
order_workflow.py:155 reads `order_id, object_path, source_sha256` for every
ready upload. The unique constraint therefore matches the conflict target
exactly, and the table is private: RLS on, no policy, service_role only.

Idempotent: an existing correctly-shaped table is one read and exit 0. A table
the caller cannot read is treated as repairable, because every statement is
idempotent — that is how a half-finished deploy (table created, grants missed)
heals instead of stalling. A table that exists with the WRONG shape is a stop,
never an auto-alter.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

ORIGIN = "https://veqpmdsiqcjjpxgcubrp.supabase.co"
TABLE = "anthemsmith_recovery_analyses"
KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
COLUMNS = ("order_id", "object_path", "source_sha256", "model", "analysis")
DEPLOYER = "anthemsmith-seed-workflow"
REASON = "create the private vision-analysis store the order gate hard-requires"

# (label, statement, boolean verify SQL). One statement per safe-deploy call so a
# refusal names the exact step instead of hiding behind a multi-statement body.
STATEMENTS = [
    ("create",
     "CREATE TABLE IF NOT EXISTS public." + TABLE + " (\n"
     "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n"
     "  order_id text NOT NULL CHECK (order_id ~ '^AS-[A-Za-z0-9_-]{1,90}$'),\n"
     "  object_path text NOT NULL,\n"
     "  source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),\n"
     "  model text NOT NULL,\n"
     "  provider_request_id text,\n"
     "  analysis jsonb NOT NULL,\n"
     "  created_at timestamptz NOT NULL DEFAULT now(),\n"
     "  CONSTRAINT " + TABLE + "_source_key\n"
     "    UNIQUE (order_id, object_path, source_sha256, model)\n"
     ")",
     "SELECT to_regclass('public." + TABLE + "') IS NOT NULL"),
    ("rls",
     "ALTER TABLE public." + TABLE + " ENABLE ROW LEVEL SECURITY",
     "SELECT relrowsecurity FROM pg_class WHERE oid = 'public." + TABLE + "'::regclass"),
    ("revoke_public",
     "REVOKE ALL ON public." + TABLE + " FROM anon, authenticated",
     "SELECT NOT has_table_privilege('anon', 'public." + TABLE + "', 'SELECT')"),
    ("grant_service_role",
     "GRANT SELECT, INSERT, UPDATE, DELETE ON public." + TABLE + " TO service_role",
     "SELECT has_table_privilege('service_role', 'public." + TABLE + "', 'SELECT') "
     "AND has_table_privilege('service_role', 'public." + TABLE + "', 'INSERT')"),
]


def fail(code, detail=""):
    print(json.dumps({"ensure": "FAILED", "code": code, "detail": str(detail)[:400]}))
    sys.exit(1)


def req(url, body=None, timeout=60):
    headers = {"apikey": KEY, "Authorization": "Bearer " + KEY,
               "User-Agent": "AnthemSmith/1.0", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                     headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return response.status, (json.loads(raw) if raw else None)


def probe():
    """Return (state, detail) where state is ok | missing | private | shape | error."""
    url = (ORIGIN + "/rest/v1/" + TABLE + "?select=" + ",".join(COLUMNS) + "&limit=1")
    try:
        status, _ = req(url)
        return ("ok", status)
    except urllib.error.HTTPError as exc:
        raw = exc.read(4096).decode("utf-8", "replace")
        try:
            code = json.loads(raw).get("code")
        except Exception:
            code = None
        if exc.code == 404 and code == "42P01":
            return ("missing", "42P01")
        # Visible but unreadable: the table is there and its grants are not.
        # Repairable, because GRANT/REVOKE are the last two statements.
        if exc.code in (401, 403) or code == "42501":
            return ("private", "42501")
        if exc.code == 400:
            return ("shape", raw[:300])
        return ("error", "%s %s" % (exc.code, raw[:300]))
    except Exception as exc:
        return ("error", type(exc).__name__)


def safe_deploy(label, statement, verify_sql):
    """Apply one statement through the house organ; a refusal is a legible stop."""
    body = {"p_function_name": TABLE, "p_function_args": "", "p_new_body": statement,
            "p_verify_sql": verify_sql, "p_override_recursion_check": False,
            "p_override_catastrophic": False, "p_deployer": DEPLOYER, "p_reason": REASON}
    try:
        status, result = req(ORIGIN + "/rest/v1/rpc/gabs_safe_deploy_function", body)
    except urllib.error.HTTPError as exc:
        raw = exc.read(4096).decode("utf-8", "replace")
        return ("http_%s" % exc.code, raw[:300])
    except Exception as exc:
        return ("network", type(exc).__name__)
    result = result if isinstance(result, dict) else {}
    if result.get("applied") is True and result.get("verify_result") is True:
        return ("applied", result.get("status"))
    # Refusals are the organ speaking: status=blocked_at_* / apply_failed /
    # verify_failed_rolled_back, plus apply_error or verify_error when present.
    return ("refused", result)


def wait_for_table(attempts=8, delay=5):
    """PostgREST reloads its schema cache asynchronously after the deploy notifies it."""
    for _ in range(attempts):
        state, detail = probe()
        if state in ("ok", "shape"):
            return state, detail
        time.sleep(delay)
    return state, detail


def main():
    if not KEY:
        fail("no_service_key")
    state, detail = probe()
    if state == "ok":
        print(json.dumps({"ensure": "OK", "table": TABLE, "created": 0,
                          "columns": list(COLUMNS), "state": "already_present"}))
        return
    if state == "shape":
        fail("existing_table_wrong_shape", detail)
    if state not in ("missing", "private"):
        fail("probe_" + state, detail)
    print(json.dumps({"probe": state, "action": "apply idempotent statements"}))

    applied = []
    for label, statement, verify_sql in STATEMENTS:
        outcome, note = safe_deploy(label, statement, verify_sql)
        if outcome != "applied":
            fail(label + "_" + outcome, note)
        applied.append({"step": label, "status": note})
        print(json.dumps({"step": label, "outcome": "applied", "status": note}))

    state, detail = wait_for_table()
    if state != "ok":
        fail("table_not_readable_after_ddl", detail)
    print(json.dumps({"ensure": "OK", "table": TABLE, "created": len(applied),
                      "columns": list(COLUMNS), "steps": applied}))


if __name__ == "__main__":
    main()
