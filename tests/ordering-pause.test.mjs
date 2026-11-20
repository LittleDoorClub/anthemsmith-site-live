// Offline source/boundary fixture only. No orders, payments, messages or media are sent.
//
// AS-MUIJ11B6 containment guard, RE-POINTED for the v2 client (2026-11-20 integration).
// The pause moved out of app.js (relay chain, retired) and into two places that must agree:
//   order-config.js  -> window.ANTHEMSMITH_ORDERING_PAUSED (release owner's switch)
//   order-client.js  -> reads it fail-closed: `!== false`, so a missing/typo'd config
//                       still resolves to PAUSED. That default is the load-bearing part.
// The defect class this guards is unchanged: a pause flag that is declared and never read,
// or a button that ships enabled while the client is told not to sell.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const read = (p) => fs.readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');
const config = read('order-config.js');
const client = read('order-client.js');
const html = read('index.html');

test('config declares the pause as a real, readable switch', () => {
  const m = config.match(/window\.ANTHEMSMITH_ORDERING_PAUSED\s*=\s*(true|false)\s*;/);
  assert.ok(m, 'order-config.js no longer sets window.ANTHEMSMITH_ORDERING_PAUSED');
  assert.equal(m[1], 'true', 'public build ships with ordering paused (flip only on written go)');
});

test('the client reads the switch and fails closed when it is absent', () => {
  assert.match(
    client,
    /window\.ANTHEMSMITH_ORDERING_PAUSED\s*!==\s*false/,
    'client must treat anything other than an explicit false as PAUSED',
  );
});

test('the pause gate is reached before any network call can start', () => {
  const gate = client.search(/ANTHEMSMITH_ORDERING_PAUSED/);
  const fetchAt = client.search(/\bfetch\s*\(/);
  assert.ok(gate > -1, 'no gate in order-client.js');
  assert.ok(fetchAt > -1, 'no fetch in order-client.js (client cannot ever submit)');
  assert.ok(gate < fetchAt, 'a fetch() precedes the pause gate in source order');
});

test('the shipped button cannot be pressed and says why', () => {
  const btn = html.match(/<button[^>]*id="checkoutButton"[^>]*>([^<]*)<\/button>/);
  assert.ok(btn, 'index.html lost the checkout button');
  assert.match(btn[0], /\bdisabled\b/, 'submit must ship disabled');
  assert.equal(btn[1].trim(), 'Ordering paused', 'submit must ship labelled as paused');
});

test('no payable link ships in the page', () => {
  assert.doesNotMatch(html, /buy\.stripe\.com/, 'a payable Stripe link is in index.html while paused');
});

test('the config loads before the client that reads it', () => {
  const order = [...html.matchAll(/<script[^>]*src="([^"]+)"/g)].map((m) => m[1]);
  const iConfig = order.indexOf('order-config.js');
  const iClient = order.indexOf('order-client.js');
  assert.ok(iConfig > -1 && iClient > -1, 'index.html must load order-config.js and order-client.js');
  assert.ok(iConfig < iClient, 'order-config.js must load before order-client.js');
});
