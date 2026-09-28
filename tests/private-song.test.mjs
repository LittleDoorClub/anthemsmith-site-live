import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parseCapability, loadAudio, initialize, MAX_AUDIO } from '../private-song.js';

const order = 'AS-PRIVATE-PAGETEST', token = 'A'.repeat(43);
const hash = '#order=' + order + '&token=' + token;
const capability = { order, token };
const bytes = new Uint8Array([1, 2, 3, 4]);
const json = (data, status = 200) => new Response(JSON.stringify(data), { status });
const manifest = { status: 'ready', byte_size: bytes.length, mime_type: 'audio/mpeg', duration_seconds: 204 };
function ui() {
  const elements = Object.fromEntries(['status', 'player', 'audio', 'download'].map(id => [id, {
    hidden: true, disabled: true, dataset: {}, textContent: '', events: {},
    addEventListener(name, handler) { this.events[name] = handler; },
    removeAttribute(name) { delete this[name]; },
  }]));
  const historyCalls = [], blobs = [];
  return { elements, historyCalls, blobs,
    document: { getElementById: id => elements[id] },
    location: { hash, search: '?token=never-used', pathname: '/private-song.html' },
    history: { replaceState(...args) { historyCalls.push(args); } },
    urlAPI: { createObjectURL(blob) { blobs.push(blob); return 'blob:synthetic'; }, revokeObjectURL() {} },
    lifecycle: { addEventListener() {} },
  };
}

test('strict fragment parsing rejects missing, query, duplicate and malformed credentials', () => {
  assert.deepEqual(parseCapability(hash), capability);
  for (const value of ['', '?order=' + order + '&token=' + token,
    hash + '&token=' + token, hash + '&extra=1', '#order=../bad&token=' + token,
    '#order=' + order + '&token=short']) assert.throws(() => parseCapability(value), /invalid_capability/);
});

test('actual bounded audio bytes use fixed API and bearer headers only', async () => {
  const calls = [];
  const blob = await loadAudio(capability, async (url, options) => {
    calls.push(url);
    assert.ok(url.startsWith('https://veqpmdsiqcjjpxgcubrp.supabase.co/functions/v1/anthemsmith-song-access/'));
    assert.ok(!url.includes('?') && !url.includes(token));
    assert.equal(options.headers.Authorization, 'Bearer ' + token);
    assert.equal(options.headers['X-Anthem-Order'], order);
    assert.equal(options.cache, 'no-store');
    assert.equal(options.credentials, 'omit');
    assert.equal(options.referrerPolicy, 'no-referrer');
    assert.equal(options.redirect, 'error');
    return url.endsWith('/status') ? json(manifest) : new Response(bytes, { headers: { 'Content-Type': 'audio/mpeg' } });
  });
  assert.equal(calls.length, 2);
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), bytes);
});

test('failed status/audio fetch, denied access, wrong MIME and truncated bytes never enable player', async t => {
  const variants = [
    async () => { throw Error('network private body'); },
    async () => json({ error: 'expired_capability' }, 410),
    async url => url.endsWith('/status') ? json(manifest) : json({ error: 'access_unavailable' }, 403),
    async url => url.endsWith('/status') ? json(manifest) : new Response(bytes, { headers: { 'Content-Type': 'text/html' } }),
    async url => url.endsWith('/status') ? json(manifest) : new Response(bytes.subarray(0, 1), { headers: { 'Content-Type': 'audio/mpeg' } }),
    async () => json({ ...manifest, byte_size: MAX_AUDIO + 1 }),
  ];
  for (let i = 0; i < variants.length; i++) await t.test(String(i), async () => {
    const env = ui();
    await initialize({ ...env, fetchImpl: variants[i] });
    assert.equal(env.elements.player.hidden, true);
    assert.equal(env.elements.download.disabled, true);
    assert.equal(env.blobs.length, 0);
    assert.equal(env.elements.status.dataset.error, 'true');
    assert.ok(!env.elements.status.textContent.includes('private body'));
    assert.deepEqual(env.historyCalls, [[null, '', '/private-song.html']]);
  });
});

test('no query fallback; invalid fragment is erased without any request', async () => {
  const env = ui();
  env.location.hash = '';
  env.location.search = '?order=' + order + '&token=' + token;
  let calls = 0;
  await initialize({ ...env, fetchImpl: async () => { calls++; } });
  assert.equal(calls, 0);
  assert.equal(env.elements.download.disabled, true);
  assert.deepEqual(env.historyCalls, [[null, '', '/private-song.html']]);
});

test('fragment is erased before fetch and player enables only after bytes arrive', async () => {
  const env = ui();
  await initialize({ ...env, fetchImpl: async url => {
    assert.equal(env.historyCalls.length, 1);
    assert.equal(env.elements.download.disabled, true);
    assert.equal(env.blobs.length, 0);
    return url.endsWith('/status') ? json(manifest) : new Response(bytes, { headers: { 'Content-Type': 'audio/mpeg' } });
  } });
  assert.equal(env.elements.audio.src, 'blob:synthetic');
  assert.equal(env.elements.player.hidden, false);
  assert.equal(env.elements.download.disabled, true);
  env.elements.audio.duration = 204;
  env.elements.audio.oncanplay();
  assert.equal(env.elements.download.disabled, false);
  assert.equal(env.blobs[0].size, bytes.length);
});

test('page has no analytics, third-party scripts, persistence or query token read', async () => {
  const html = await readFile(new URL('../private-song.html', import.meta.url), 'utf8');
  const js = await readFile(new URL('../private-song.js', import.meta.url), 'utf8');
  assert.equal((html.match(/<script\b/g) || []).length, 1);
  assert.match(html, /src="\.\/private-song\.js"/);
  assert.match(html, /name="referrer" content="no-referrer"/);
  assert.match(html, /aria-live="polite"/);
  assert.ok(!/localStorage|sessionStorage|document\.cookie|location\.search|sendBeacon|analytics|googletag/i.test(js));
  assert.ok(!/<(?:img|iframe)|https?:\/\/[^" ]+\.js/i.test(html));
});
