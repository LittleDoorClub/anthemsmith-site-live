// Offline browser-boundary fixture only. No orders, payments, messages or media are sent.
// Proves the ordering pause is load-bearing: with AS_ORDERING_PAUSED=true the CTA cannot
// reveal the form or enable submit, and the payable link can never be rendered.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../app.js', import.meta.url), 'utf8');
const html = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');

// The defect class this guards: a flag that is declared and never read.
assert.ok((source.match(/AS_ORDERING_PAUSED/g) || []).length > 1, 'AS_ORDERING_PAUSED is declared but never read');

// Seed the fixture from the shipped markup so the test cannot drift from index.html.
const formMarkup = html.match(/<form id="songForm"([^>]*)>/);
const submitMarkup = html.match(/<button class="primary" type="submit"([^>]*)>([^<]*)<\/button>/);
const categoryDefault = html.match(/id="categorySelect">\s*<option value="([^"]+)"/)?.[1];
assert.ok(formMarkup && submitMarkup && categoryDefault, 'index.html lost the steadied form, submit or category markup');
assert.match(formMarkup[1], /\bhidden\b/, 'form must ship hidden');
assert.match(formMarkup[1], /\binert\b/, 'form must ship inert');
assert.match(submitMarkup[1], /\bdisabled\b/, 'submit must ship disabled');
assert.equal(submitMarkup[2].trim(), 'Ordering paused', 'submit must ship labelled as paused');

function element(tag = 'div') {
  return {
    tagName: String(tag).toUpperCase(), hidden: false, disabled: false, required: false,
    textContent: '', innerHTML: '', value: '', className: '', src: '', href: '', files: [],
    dataset: {}, style: {}, classList: { toggle() {}, add() {}, remove() {} },
    setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
    querySelector() { return element(); }, querySelectorAll() { return []; },
    replaceWith() {}, prepend() {}, append() {}, replaceChildren() {},
    close() {}, showModal() {}, focus() {}, scrollIntoView() {},
    checkValidity() { return true; }, reportValidity() {}, setCustomValidity() {},
    closest() { return null; }, reset() {}
  };
}

// Boot the real app.js with the flag forced to the state under test.
function boot(flag) {
  let matched = false;
  const code = source.replace(/const AS_ORDERING_PAUSED=(?:true|false);/, () => {
    matched = true;
    return `const AS_ORDERING_PAUSED=${flag};`;
  });
  assert.ok(matched, 'flag substitution did not match app.js');
  const nodes = new Map();
  const node = selector => { if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector); };
  const clicks = [];
  const document = {
    body: element('body'),
    querySelector: node,
    getElementById: id => node('#' + id),
    querySelectorAll: () => [],
    createElement: tag => element(tag),
    addEventListener: (name, fn) => { if (name === 'click') clicks.push(fn); },
    removeEventListener() {}
  };
  const store = new Map();
  const sessionStorage = {
    getItem: key => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: key => store.delete(key)
  };
  const location = { search: '', hash: '' };
  const sandbox = {
    document, location, sessionStorage, console, Date, Promise, WeakMap, JSON, Math, URL,
    URLSearchParams, AbortController, setTimeout: () => 0, clearTimeout() {},
    setInterval: () => 0, clearInterval() {},
    fetch: async () => { throw new Error('network disabled in fixture'); },
    FormData: class { *[Symbol.iterator]() {} }
  };
  sandbox.window = { addEventListener() {}, location, sessionStorage };

  // Ship-time state comes from index.html, and exists before app.js runs (as in a browser).
  node('#categorySelect').value = categoryDefault;
  node('#songForm').hidden = /\bhidden\b/.test(formMarkup[1]);
  const submit = node('#songForm button[type=submit]');
  submit.disabled = /\bdisabled\b/.test(submitMarkup[1]);
  submit.textContent = submitMarkup[2].trim();

  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: 'app.js' });

  return {
    sandbox,
    form: node('#songForm'),
    submit,
    brief: node('#brief'),
    payment: node('#payment'),
    clickCta() {
      const target = { closest: selector => (selector === '[data-create]' ? { dataset: { create: '0' } } : null) };
      for (const fn of clicks) fn({ target });
    }
  };
}

test('paused: one CTA click cannot reveal the form, enable submit or render a payable link', async () => {
  const h = boot(true);
  // Boot invariant: the shipped markup state app.js leaves in place.
  assert.equal(h.form.hidden, true, 'form was not hidden at boot');
  assert.equal(h.submit.disabled, true, 'submit was not disabled at boot');
  assert.equal(h.submit.textContent, 'Ordering paused', 'submit label was not the paused label at boot');
  const before = { hidden: h.form.hidden, disabled: h.submit.disabled, label: h.submit.textContent };
  h.clickCta();
  assert.equal(h.form.hidden, before.hidden, 'CTA changed the hidden state of the order form while paused');
  assert.equal(h.submit.disabled, before.disabled, 'CTA changed the disabled state of submit while paused');
  assert.equal(h.submit.textContent, before.label, 'CTA relabelled submit while paused');
  assert.doesNotMatch(h.payment.innerHTML, /buy\.stripe\.com/, 'paused click rendered a payable link');
});

test('paused: the submit path still cannot reach the payable link', async () => {
  const h = boot(true);
  await h.sandbox.submitInstant();
  assert.doesNotMatch(h.payment.innerHTML, /buy\.stripe\.com/, 'submit path rendered a payable link while paused');
});

// Control. Same code, flag flipped: the assertions above must be able to fail.
test('control: with the flag off the same click does open the form and enable submit', async () => {
  const h = boot(false);
  // Boot state is still the steadied markup; only the flag decides what the CTA may do.
  assert.equal(h.form.hidden, true, 'control boot state diverged from shipped markup');
  h.clickCta();
  assert.equal(h.form.hidden, false, 'unpaused CTA failed to open the form');
  assert.equal(h.submit.disabled, false, 'unpaused CTA failed to enable submit');
  assert.equal(h.submit.textContent, 'Get your song', 'unpaused submit label unchanged');
  await h.sandbox.submitInstant();
  assert.match(h.payment.innerHTML, /buy\.stripe\.com/, 'unpaused submit path never reached checkout');
});
