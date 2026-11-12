# `anthemsmith_recovery_orders.status` is an intake gate, not a lifecycle mirror

Why this file exists: a status "repair" was ordered on `AS-TEST-MAYA-20261110` to flip the row from
`source_received` to `audio_ready`. The database refused it (`23514`). **The refusal was correct.**

## The vocabulary is closed

```
anthemsmith_recovery_orders_status_check
CHECK ((status = ANY (ARRAY['awaiting_sources'::text, 'source_received'::text])))
```

`audio_ready` is not a value the column can hold. Before planning any status write:

```sql
SELECT conname || ' :: ' || pg_get_constraintdef(oid)
FROM pg_constraint WHERE conrelid = 'public.anthemsmith_recovery_orders'::regclass;
```

## `source_received` is read as a gate by live paths

| site | behaviour |
|---|---|
| `scripts/private_audio.py:97` | refuses unless `status == 'source_received'` **and** `payment_state == 'verified_paid'` |
| `scripts/private_audio.py:117` | PATCH filter `status = eq.source_received` |
| `scripts/order_workflow.py:155` | refuses unless `status == 'source_received'` |
| `scripts/order_workflow.py:423` | filter `status=eq.source_received, payment_state=eq.verified_paid` |
| `scripts/recovery_restore_original.py:110,117` | the only other legal value, `awaiting_sources` |

Flatten that gate into a lifecycle mirror and an idempotent re-run (private-audio job, order workflow)
finds nothing to do: **a pipeline that reads healthy while it is stopped** — a worse lie than a column
that lags.

## Lifecycle lives in the source files, and is projected — never written

`scripts/order_lifecycle.py` answers *"did the lane run, and did it deliver?"* in one read-only call,
showing three sources side by side and never blending them:

```
intake.status · intake.payment_state          the row's real gate fields (untouched)
manifest.status · private_audio · duration_full   songs/<oid>.json
delivery.delivery_status · provider_status        songs/<oid>.delivery.json
delivery_result                                   the row's own jsonb, passed through as-is
```

Rules the projection enforces:

* a **missing manifest** projects `lifecycle: unknown` — never `source_received`, never silence;
* sources that **disagree** project `contradiction` and exit nonzero, rather than smoothing a story;
* `intake.*` is reported, never promoted to the verdict;
* no writes: the module has no PATCH/POST path at all, and `tests/test_order_lifecycle.py` asserts that
  structurally (a grep for the verbs plus the absence of a `rest()` writer).

Usage:

```
gh workflow run recovery-order-lifecycle.yml -f order_id=AS-TEST-MAYA-20261110   # live, with the row
python scripts/order_lifecycle.py --order-id <oid> --songs-dir songs --row-file <row.json>   # offline
```

## Why not extend the CHECK (the rejected option)

Adding `audio_ready` to the constraint plus provenance columns (`status_updated_at`, `evidence_run_id`,
`evidence_main_sha`) would make the row a lifecycle record — but it also requires moving
`private_audio.py:97,117` and `order_workflow.py:155,423` to accept both values, on a lane that gates
paid orders. Projection gets the same answer with zero blast radius; the schema change can come later
if a job ever needs to *query* by lifecycle in SQL.

## Two traps worth remembering

* **PostgREST `23514` carries a `details` field that quotes the failing row's values.** Never echo an
  error body into a CI log; `postgrest_reason()` in `scripts/set_order_status.py` keeps SQLSTATE,
  constraint name and a truncated message, and drops the rest.
* **`information_schema` returns nothing to a non-owner role** (`42501` on a plain `count(*)`), which
  looks like "column missing" or "table empty". Read shape from `pg_constraint`; read row content only
  from inside Actions.
