#!/usr/bin/env python3
"""Create the private AnthemSmith store tables that the paid path hard-requires.

WHY THIS EXISTS: plant-form failures, all of them 404s from a table that never existed:

- `anthemsmith_recovery_analyses` -- order_workflow.prepare() refuses every order with
  `private_order_store_unavailable` until the vision rows the ENFORCER LAW (2026-11-10)
  keys to each uploaded object and source sha are there. Three dispatches died on it
  (runs 42953608507 / 42953794737 / 42953864070).
- `anthemsmith_recovery_compositions` -- photo_intake.persist_composition() saves the
  grounded lyrics before any music spend; without the table the forge holds at
  `photo_lyrics: private_order_store_unavailable` (runs 43070650457).

WHY IT RUNS HERE: PostgREST cannot execute DDL and this repo holds no database password,
so the only hand that can create a table is the service-role key inside Actions. The only
DDL door that key reaches is the house safe-deploy organ
public.gabs_safe_deploy_function (service_role-only ACL, blast-radius and recursion gates,
per-statement verify SQL, pg_notify schema reload).

Shape is never a guess -- each table mirrors its writer's row and conflict target exactly:

- analyses: photo_intake writes `{order_id, object_path, source_sha256, model,
  provider_request_id, analysis}` with on_conflict=(order_id, object_path, source_sha256,
  model); order_workflow reads `order_id, object_path, source_sha256` per ready upload.
- compositions: photo_intake writes `{order_id, source_digest, title, lyrics, provenance,
  model}` with on_conflict=(order_id, source_digest, model); it reads the row back and
  compares every field, so a missing column is a hard failure, not a silent drop.

Both tables are private: RLS on, no policy, anon and authenticated revoked, service_role
granted. Idempotent: a correctly-shaped table is one read and no writes; a table the caller
cannot read is repairable, because every statement is idempotent (that is how a
half-finished deploy heals); a table that exists with the WRONG shape is a stop, never an
auto-alter.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

ORIGIN = "https://veqpmdsiqcjjpxgcubrp.supabase.co"
KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
DEPLOYER = "anthemsmith-seed-workflow"
REASON = "create the private order-store tables the paid path hard-requires"
ORDER_CHECK = "CHECK (order_id ~ '^AS-[A-Za-z0-9_-]{1,90}$')"
SHA_CHECK = "CHECK (source_sha256 ~ '^[0-9a-f]{64}$')"

# table -> (columns read back by the probe, CREATE body)
TABLES = {
    "anthemsmith_recovery_analyses": (
        ("order_id", "object_path", "source_sha256", "model", "analysis"),
        "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n"
        "  order_id text NOT NULL " + ORDER_CHECK + ",\n"
        "  object_path text NOT NULL,\n"
        "  source_sha256 text NOT NULL " + SHA_CHECK + ",\n"
        "  model text NOT NULL,\n"
        "  provider_request_id text,\n"
        "  analysis jsonb NOT NULL,\n"
        "  created_at timestamptz NOT NULL DEFAULT now(),\n"
        "  CONSTRAINT anthemsmith_recovery_analyses_upsert_key\n"
        "    UNIQUE (order_id, object_path, source_sha256, model)\n",
    ),
    "anthemsmith_recovery_compositions": (
        ("order_id", "source_digest", "title", "lyrics", "provenance", "model"),
        "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),\n"
        "  order_id text NOT NULL " + ORDER_CHECK + ",\n"
        "  source_digest text NOT NULL CHECK (source_digest ~ '^[0-9a-f]{64}$'),\n"
        "  title text NOT NULL,\n"
        "  lyrics text NOT NULL,\n"
        "  provenance jsonb NOT NULL,\n"
        "  model text NOT NULL,\n"
        "  created_at timestamptz NOT NULL DEFAULT now(),\n"
        "  CONSTRAINT anthemsmith_recovery_compositions_upsert_key\n"
        "    UNIQUE (order_id, source_digest, model)\n",
    ),
}


def statements(table, body):
    """The same four idempotent steps for every table, each with its own verify SQL."""
    return [
        ("create", "CREATE TABLE IF NOT EXISTS public." + table + " (\n" + body + ")",
         "SELECT to_regclass('public." + table + "') IS NOT NULL"),
        ("rls", "ALTER TABLE public." + table + " ENABLE ROW LEVEL SECURITY",
         "SELECT relrowsecurity FROM pg_class WHERE oid = 'public." + table + "'::regclass"),
        ("revoke_public", "REVOKE ALL ON public." + table + " FROM anon, authenticated",
         "SELECT NOT has_table_privilege('anon', 'public." + table + "', 'SELECT')"),
        ("grant_service_role",
         "GRANT SELECT, INSERT, UPDATE, DELETE ON public." + table + " TO service_role",
         "SELECT has_table_privilege('service_role', 'public." + table + "', 'SELECT') "
         "AND has_table_privilege('service_role', 'public." + table + "', 'INSERT')"),
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


def probe(table, columns):
    """Return (state, detail) where state is ok | missing | private | shape | error."""
    url = ORIGIN + "/rest/v1/" + table + "?select=" + ",".join(columns) + "&limit=1"
    try:
        return ("ok", req(url)[0])
    except urllib.error.HTTPError as exc:
        raw = exc.read(4096).decode("utf-8", "replace")
        try:
            code = json.loads(raw).get("code")
        except Exception:
            code = None
        if exc.code == 404 and code == "42P01":
            return ("missing", "42P01")
        # Visible but unreadable: the table is there and its grants are not. Repairable,
        # because GRANT/REVOKE are the last two statements.
        if exc.code in (401, 403) or code == "42501":
            return ("private", "42501")
        if exc.code == 400:
            return ("shape", raw[:300])
        return ("error", "%s %s" % (exc.code, raw[:300]))
    except Exception as exc:
        return ("error", type(exc).__name__)


def safe_deploy(table, label, statement, verify_sql):
    """Apply one statement through the house organ; a refusal is a legible stop."""
    body = {"p_function_name": table, "p_function_args": "", "p_new_body": statement,
            "p_verify_sql": verify_sql, "p_override_recursion_check": False,
            "p_override_catastrophic": False, "p_deployer": DEPLOYER, "p_reason": REASON}
    try:
        _, result = req(ORIGIN + "/rest/v1/rpc/gabs_safe_deploy_function", body)
    except urllib.error.HTTPError as exc:
        return ("http_%s" % exc.code, exc.read(4096).decode("utf-8", "replace")[:300])
    except Exception as exc:
        return ("network", type(exc).__name__)
    result = result if isinstance(result, dict) else {}
    if result.get("applied") is True and result.get("verify_result") is True:
        return ("applied", result.get("status"))
    # Refusals are the organ speaking: blocked_at_* / apply_failed /
    # verify_failed_rolled_back, plus apply_error or verify_error when present.
    return ("refused", result)


def wait_for(table, columns, attempts=8, delay=5):
    """PostgREST reloads its schema cache asynchronously after the deploy notifies it."""
    state = detail = None
    for _ in range(attempts):
        state, detail = probe(table, columns)
        if state in ("ok", "shape"):
            return state, detail
        time.sleep(delay)
    return state, detail


def ensure(table, columns, body):
    state, detail = probe(table, columns)
    if state == "ok":
        print(json.dumps({"table": table, "state": "already_present", "created": 0}))
        return
    if state == "shape":
        fail(table + "_existing_table_wrong_shape", detail)
    if state not in ("missing", "private"):
        fail(table + "_probe_" + state, detail)
    print(json.dumps({"table": table, "probe": state, "action": "apply idempotent statements"}))
    applied = 0
    for label, statement, verify_sql in statements(table, body):
        outcome, note = safe_deploy(table, label, statement, verify_sql)
        if outcome != "applied":
            fail(table + "_" + label + "_" + outcome, note)
        applied += 1
        print(json.dumps({"table": table, "step": label, "outcome": "applied", "status": note}))
    state, detail = wait_for(table, columns)
    if state != "ok":
        fail(table + "_not_readable_after_ddl", detail)
    print(json.dumps({"table": table, "state": "created", "steps": applied}))


def main():
    if not KEY:
        fail("no_service_key")
    for table, (columns, body) in TABLES.items():
        ensure(table, columns, body)
    print(json.dumps({"ensure": "OK", "tables": list(TABLES)}))


if __name__ == "__main__":
    main()
