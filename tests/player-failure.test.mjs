// Offline browser-boundary fixtures only. No orders, payments, messages or media are sent.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
const start = source.indexOf('const GH_OWNER=');
const end = source.indexOf('function resumeAfterPayment()', start);
class Element {
  constructor() { this.listeners = new Map(); this.hidden = false; this.disabled = false; this.textContent = ''; this.src = ''; this.href = ''; }
  addEventListener(name, fn) { const list = this.listeners.get(name) || []; list.push(fn); this.listeners.set(name, list); }
  removeEventListener(name, fn) { this.listeners.set(name, (this.listeners.get(name) || []).filter(f => f !== fn)); }
  emit(name) { for (const fn of [...(this.listeners.get(name) || [])]) fn({ target: this }); }
  pause() { this.paused = true; }
}
class Box {
  constructor() { this.html = ''; this.elements = new Map(); }
  set innerHTML(value) { this.html = value; this.elements = new Map(); }
  get innerHTML() { return this.html; }
  querySelector(selector) { if (!this.elements.has(selector)) this.elements.set(selector, new Element()); return this.elements.get(selector); }
}
async function harness(responses = {}) {
  let next = 1, fetchImpl;
  const intervals = new Map(), timeouts = new Map(), calls = [], box = new Box();
  const ctx = { Date, Promise, WeakMap, AbortController, AS_EMAIL: 'support@example.test', showOrderingPaused() {},
    setInterval(fn) { const key = next++; intervals.set(key, fn); return key; },
    clearInterval(key) { intervals.delete(key); },
    setTimeout(fn) { const key = next++; timeouts.set(key, fn); return key; },
    clearTimeout(key) { timeouts.delete(key); },
    fetch: async (url, options) => {
      calls.push({ url, method: options?.method || 'GET' });
      if (fetchImpl) return fetchImpl(url, options);
      const key = url.split('/').at(-1).split('?')[0];
      return { ok: Object.hasOwn(responses, key), json: async () => responses[key] };
    }
  };
  vm.createContext(ctx); vm.runInContext(source.slice(start, end), ctx);
  const tick = async () => { for (const fn of [...intervals.values()]) await fn(); };
  return { ctx, box, calls, intervals, timeouts, tick, setFetch(fn) { fetchImpl = fn; },
    el(selector) { return box.querySelector(selector); },
    async start(id = 'AS-TEST') { ctx.watchOrder(id, box); await tick(); },
    timeout() { for (const fn of [...timeouts.values()]) fn(); }
  };
}
const ready = { 'AS-TEST.json': { status: 'audio_ready' } };
test('manifest alone does not say playable; browser canplay reveals full link', async () => {
  const h = await harness(ready); await h.start();
  assert.equal(h.el('[data-song-download]').hidden, true);
  assert.doesNotMatch(h.el('[data-order-title]').textContent, /is ready/);
  assert.match(h.el('audio').src, /AS-TEST-full\.mp3/);
  h.el('audio').emit('canplay');
  assert.equal(h.el('[data-order-title]').textContent, 'Your song is ready.');
  assert.equal(h.el('[data-song-download]').hidden, false);
  assert.equal(h.intervals.size, 0);
});
test('failed audio exposes support and read-only retry, never leaves ready claim', async () => {
  const h = await harness(ready); await h.start(); h.el('audio').emit('canplay'); h.el('audio').emit('error');
  assert.match(h.el('[data-order-title]').textContent, /could not load/);
  assert.equal(h.el('[data-song-download]').hidden, true);
  assert.equal(h.el('[data-order-help]').hidden, false);
  assert.equal(h.el('[data-retry-audio]').hidden, false);
  assert.match(h.box.innerHTML, /AS-TEST/); assert.match(h.box.innerHTML, /Do not pay again/);
  const first = h.calls.length; h.el('[data-retry-audio]').emit('click'); await h.tick();
  assert.ok(h.calls.length > first); assert.ok(h.calls.every(x => x.method === 'GET'));
  assert.ok(h.calls.every(x => x.url.includes('/songs/AS-TEST')));
});
test('loading timeout and stall offer a safe retry', async () => {
  const h = await harness(ready); await h.start(); h.timeout();
  assert.match(h.el('[data-order-title]').textContent, /still loading/);
  assert.equal(h.el('[data-retry-audio]').hidden, false);
  h.el('audio').emit('canplay'); assert.equal(h.el('[data-retry-audio]').hidden, true);
  h.el('audio').emit('stalled'); assert.equal(h.el('[data-retry-audio]').hidden, true);
  assert.equal(h.el('[data-order-title]').textContent,'Your song is ready.');
});
test('queued or submitted notification is not described as delivered', async () => {
  const h = await harness({ ...ready, 'AS-TEST.delivery.json': { order_id: 'AS-TEST', delivery_status: 'submitted', provider_status: 'queued' } });
  await h.start(); assert.match(h.el('[data-order-notification]').textContent, /not yet confirmed/);
  assert.doesNotMatch(h.el('[data-order-notification]').textContent, /delivery confirmed|was delivered/);
});
test('terminal delivery wording requires matching order and terminal provider state', async () => {
  for (const [receipt, confirmed] of [
    [{ order_id: 'AS-TEST', delivery_status: 'delivered', provider_status: 'delivered' }, true],
    [{ order_id: 'AS-OTHER', delivery_status: 'delivered', provider_status: 'delivered' }, false],
    [{ order_id: 'AS-TEST', delivery_status: 'delivered', provider_status: 'queued' }, false],
    [{ order_id: 'AS-TEST', delivery_status: 'delivered' }, false],
  ]) {
    const h = await harness({ ...ready, 'AS-TEST.delivery.json': receipt }); await h.start();
    assert.equal(h.el('[data-order-notification]').textContent === 'Notification delivery confirmed.', confirmed);
  }
});
test('failed notification never hides a playable full song', async () => {
  const h = await harness({ ...ready, 'AS-TEST.delivery.json': { order_id: 'AS-TEST', delivery_status: 'failed' } });
  await h.start(); h.el('audio').emit('canplay');
  assert.equal(h.el('[data-song-download]').hidden, false);
  assert.match(h.el('[data-order-notification]').textContent, /notification could not be delivered/);
});
test('provider bodies and manifest URLs cannot enter the player', async () => {
  const attack = '<img src=x onerror=alert(1)>';
  const h = await harness({ 'AS-TEST.json': { status: 'audio_ready', audio_url: 'https://evil.test/a.mp3', title: attack },
    'AS-TEST.delivery.json': { order_id: 'AS-TEST', delivery_status: attack, provider_status: attack, reason: attack } });
  await h.start();
  assert.ok(!h.box.innerHTML.includes(attack)); assert.ok(!h.el('[data-order-notification]').textContent.includes(attack));
  assert.ok(h.el('audio').src.startsWith('https://raw.githubusercontent.com/'));
});
test('new read cancels old poll and stale audio events cannot repaint', async () => {
  const h = await harness(ready); await h.start(); const oldAudio = h.el('audio');
  h.ctx.watchOrder('AS-OTHER', h.box); const html = h.box.innerHTML;
  oldAudio.emit('error'); assert.equal(h.box.innerHTML, html); assert.equal(h.intervals.size, 1);
});
test('overlapping ticks do not duplicate reads', async () => {
  const h = await harness(); const waits = [];
  h.setFetch(() => new Promise(resolve => waits.push(resolve)));
  h.ctx.watchOrder('AS-TEST', h.box); const first = h.tick(); const count = h.calls.length;
  const second = h.tick(); const overlapped = h.calls.length;
  for (const resolve of waits) resolve({ ok: false }); await Promise.all([first, second]);
  assert.equal(overlapped, count);
});
test('unresponsive status request is aborted and later reads can recover', async () => {
  const h = await harness();
  h.setFetch((_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('fixture aborted')));
  }));
  h.ctx.watchOrder('AS-TEST', h.box); const first = h.tick();
  assert.equal(h.timeouts.size, 5); h.timeout(); await first;
  assert.equal(h.timeouts.size, 0);
  h.setFetch(async url => ({ ok: url.includes('/AS-TEST.json?'), json: async () => ({status:'audio_ready'}) }));
  await h.tick(); assert.match(h.el('audio').src, /AS-TEST-full\.mp3/);
});
test('stalled JSON response body remains under the request deadline', {timeout: 2000}, async () => {
  const h = await harness(); let started = 0, bodiesReady;
  const ready = new Promise(resolve => { bodiesReady = resolve; });
  h.setFetch((_url, options) => Promise.resolve({ok:true, json: () => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('fixture body aborted')));
    if (++started === 5) bodiesReady();
  })}));
  h.ctx.watchOrder('AS-TEST', h.box); const first = h.tick(); await ready;
  assert.equal(h.timeouts.size, 5); h.timeout(); await first;
  assert.equal(h.timeouts.size, 0); assert.match(h.box.innerHTML, /Checking order/);
  assert.doesNotMatch(h.box.innerHTML, /recover your order/);
});
test('recovery record still wins without a ready manifest and intake stays paused', async () => {
  const h = await harness({ 'AS-TEST.recovery.json': { status: 'pending_customer_source' } }); await h.start();
  assert.match(h.box.innerHTML, /recover your order/); assert.match(h.box.innerHTML, /Do not pay again/);
  assert.equal(h.intervals.size, 0); assert.match(source, /const AS_ORDERING_PAUSED=true/);
  assert.doesNotMatch(source, /as-anthemsmith-photos|uploadPhotosToNtfy/);
});
