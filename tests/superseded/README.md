# Superseded tests — retired relay/player chain (2026-11-20)

`player-failure.test.mjs` and `player-review.test.mjs` exercise `ctx.watchOrder`,
`resumeAfterPayment` and `sendOrder` from the app.js relay chain. That chain was removed by
the v2 client port (`1ef0a4a` lineage: order-client.js + order-config.js drive ordering,
private-song.js owns the private player).

They are kept, not deleted, so the assertions can be mined when the v2 equivalents are written.
They are OUT of the default glob (`node --test tests/*.test.mjs`) on purpose: they fail today by
design (`ctx.watchOrder is not a function`), and a red suite hides real regressions.

Coverage that still runs and covers the same ground:
- tests/private-song*.test.mjs + tests/player-* (private-song.js player)
- review/order-client.test.cjs — 14 behavioural tests incl. paused-refuses-network and
  paused-still-restores-a-paid-order
- tests/ordering-pause.test.mjs — containment gate, re-pointed at order-config/order-client
