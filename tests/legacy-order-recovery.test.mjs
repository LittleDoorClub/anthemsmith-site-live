import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import { setImmediate } from 'node:timers/promises';

const require = createRequire(import.meta.url);
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');
const root = new URL('../', import.meta.url);
const html = readFileSync(new URL('index.html', root), 'utf8');
const privateOrder = {order_id: 'AS-12345678-abcd-efgh-1234', recovery_token: 't'.repeat(48)};

function openPage(t, path, {legacy, modern} = {}) {
  const dom = new JSDOM(html, {url: 'https://anthemsmith.com/' + path, runScripts: 'outside-only'});
  t.after(() => dom.window.close());
  const w = dom.window;
  w.HTMLElement.prototype.scrollIntoView = () => {};
  if (legacy) w.sessionStorage.setItem('anthemsmith_order', JSON.stringify(legacy));
  if (modern) w.localStorage.setItem('anthemsmith_order_v2', JSON.stringify(modern));
  const requests = [];
  w.fetch = async (url, options) => {
    requests.push({url, method: options.method || 'GET'});
    return {ok: true, json: async () => ({order_id: privateOrder.order_id, status: 'refunded'})};
  };
  for (const script of w.document.querySelectorAll('script[src]')) {
    const source = script.getAttribute('src');
    assert.ok(!source.includes(':'), 'fixture only executes local assets');
    w.eval(readFileSync(new URL(source, root), 'utf8'));
  }
  return {brief: w.document.getElementById('brief'), requests};
}

function assertSupport(brief, id) {
  assert.equal(brief.hidden, false);
  assert.match(brief.textContent, /private order or song link/i);
  assert.match(brief.textContent, /do not need to pay again/i);
  const email = new URL(brief.querySelector('a').href);
  assert.equal(email.protocol, 'mailto:');
  assert.equal(email.pathname, 'cozyafllc@gmail.com');
  assert.deepEqual([...email.searchParams.keys()], ['subject']);
  assert.equal(email.searchParams.get('subject'), id ? 'Help with AnthemSmith order ' + id : 'Help with my AnthemSmith order');
  if (id) assert.ok(brief.textContent.includes(id));
}

test('a legacy order link opens private recovery guidance for that order without network activity', t => {
  const {brief, requests} = openPage(t, '?oid=AS-LINK01', {
    legacy: {request_id: 'AS-OLDTAB'}, modern: privateOrder,
  });
  assertSupport(brief, 'AS-LINK01');
  assert.deepEqual(requests, []);
});

test('a returning legacy session supplies only the order ID to support', t => {
  const {brief, requests} = openPage(t, '', {legacy: {request_id: 'AS-OLDTAB', details: 'Private story'}});
  assertSupport(brief, 'AS-OLDTAB');
  assert.doesNotMatch(brief.innerHTML, /Private story/);
  assert.deepEqual(requests, []);
});

test('malformed and ambiguous legacy references use generic recovery without leaking query text', t => {
  for (const path of ['?oid=AS-A%0D%0ABcc%3Aexample', '?oid=AS-FIRST&oid=AS-SECOND', '?paid=1']) {
    const {brief, requests} = openPage(t, path);
    assertSupport(brief);
    assert.doesNotMatch(brief.innerHTML, /Bcc|AS-FIRST|AS-SECOND/);
    assert.deepEqual(requests, []);
  }
});

test('private order credentials take precedence over a legacy session', async t => {
  for (const path of ['', '?order=' + privateOrder.order_id + '#access=' + privateOrder.recovery_token]) {
    const {brief, requests} = openPage(t, path, {legacy: {request_id: 'AS-OLDTAB'}, modern: privateOrder});
    await setImmediate();
    assert.doesNotMatch(brief.textContent, /AS-OLDTAB/);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].method, 'GET');
    assert.ok(requests[0].url.endsWith('/orders/' + privateOrder.order_id));
  }
});
