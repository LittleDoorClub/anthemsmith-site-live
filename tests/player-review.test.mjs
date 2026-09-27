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

test('review: body abort returns retryable null', {timeout: 2000}, async () => {
  const h = await harness(); let bodyReady;
  const ready = new Promise(resolve => { bodyReady = resolve; });
  h.setFetch((_url, options) => ({ ok: true, json: () => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => { const e = new Error('offline body timeout'); e.name = 'AbortError'; reject(e); });
    bodyReady();
  }) }));
  const pending = h.ctx.readOrderStatus('https://fixture.invalid/status');
  await ready; h.timeout(); assert.equal(await pending, null); assert.equal(h.timeouts.size, 0);
});
test('review: malformed JSON and invalid shapes do not become markers', {timeout: 2000}, async () => {
  const h = await harness();
  h.setFetch(() => ({ ok: true, json: async () => { throw new SyntaxError('offline malformed body'); } }));
  assert.equal(await h.ctx.readOrderStatus('https://fixture.invalid/status'), null);
  for (const data of [null, false, 1, 'audio_ready', []]) {
    h.setFetch(() => ({ ok: true, json: async () => data }));
    assert.equal(await h.ctx.readOrderStatus('https://fixture.invalid/status'), null);
  }
  assert.equal(h.timeouts.size, 0);
});
test('review: network stall must not overwrite playable or hard-error state', {timeout: 2000}, async () => {
  const h = await harness(ready); await h.start(); const audio = h.el('audio');
  audio.readyState = 4; audio.paused = false; audio.emit('playing'); audio.emit('stalled');
  const playableThenStalled = { title: h.el('[data-order-title]').textContent, downloadHidden: h.el('[data-song-download]').hidden };
  audio.emit('error'); const hardError = h.el('[data-order-title]').textContent;
  audio.emit('stalled'); const errorThenStalled = h.el('[data-order-title]').textContent;
  console.log('STALL_STATE_OBSERVATION', JSON.stringify({ playableThenStalled, hardError, errorThenStalled }));
  assert.equal(playableThenStalled.title, 'Your song is ready.', 'stalled does not mean buffered playback stopped');
  assert.equal(playableThenStalled.downloadHidden, false);
  assert.equal(errorThenStalled, hardError, 'stall must not erase the decode/load failure explanation');
});
test('review: superseded in-flight request cannot overwrite a newer player', {timeout: 2000}, async () => {
  const h = await harness(); const pending = [];
  h.setFetch(url => url.includes('/AS-OLD.') ? new Promise(resolve => pending.push(resolve)) :
    { ok: url.includes('/AS-NEW.json?'), json: async () => ({ status: 'audio_ready' }) });
  h.ctx.watchOrder('AS-OLD', h.box); const oldTick = h.tick(); assert.equal(pending.length, 5);
  h.ctx.watchOrder('AS-NEW', h.box); await h.tick(); const newAudio = h.el('audio'), html = h.box.innerHTML;
  for (const resolve of pending) resolve({ ok: true, json: async () => ({ status: 'audio_ready' }) });
  await oldTick;
  assert.equal(h.box.innerHTML, html); assert.equal(h.el('audio'), newAudio);
  assert.match(newAudio.src, /AS-NEW-full\.mp3/); assert.equal(h.intervals.size, 0);
});
test('review: repeated old retry clicks create only one replacement GET batch', {timeout: 2000}, async () => {
  const h = await harness(ready); await h.start(); const audio = h.el('audio'); audio.emit('error');
  const button = h.el('[data-retry-audio]'); button.emit('click'); button.emit('click');
  assert.equal(h.intervals.size, 1); assert.equal(button.disabled, true); assert.equal(audio.paused, true);
  assert.equal([...audio.listeners.values()].flat().length, 0);
  const before = h.calls.length; await h.tick(); assert.equal(h.calls.length - before, 5);
  assert.ok(h.calls.every(c => c.method === 'GET' && /\/songs\/AS-TEST[.]/.test(c.url)));
  const html = h.box.innerHTML; audio.emit('canplay'); audio.emit('error'); button.emit('click');
  assert.equal(h.box.innerHTML, html); assert.equal(h.intervals.size, 0);
});
test('review: malformed order identifiers are rejected before HTML or network', {timeout: 2000}, async () => {
  for (const id of ['AS-X"><img src=x onerror=alert(1)>', 'AS-../X', 'AS-X?y=1', 'AS-x', null]) {
    const h = await harness(); h.ctx.watchOrder(id, h.box); await h.tick();
    assert.equal(h.calls.length, 0); assert.equal(h.box.innerHTML, ''); assert.equal(h.intervals.size, 0);
  }
});
