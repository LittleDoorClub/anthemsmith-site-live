// Exercise the real form with browser requests intercepted. Production rejects unsupported
// voices at intake and needs a genre, a track URL, or an artist/title pair before checkout.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { webcrypto } = require('node:crypto');
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const id = 'b8e2e128-f36f-418e-bff2-19431e60b7f2';
// A decodable synthetic 16×16 PNG also used to replay these briefs through the archived API.
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGUlEQVR4nGMUCRBhIAUwkaR6VMOohiGlAQCirwCYc/brhgAAAABJRU5ErkJggg==', 'base64');

function storefront(t) {
  const dom = new JSDOM(read('index.html'), {url:'https://store.example/', runScripts:'outside-only'});
  const w = dom.window;
  t.after(() => dom.window.close());
  const form = w.document.getElementById('songForm');
  Object.defineProperty(w, 'crypto', {value:webcrypto});
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.URL.createObjectURL = () => 'blob:offline-photo';
  w.URL.revokeObjectURL = () => {};
  const requests = [];
  w.fetch = async (url, init) => {
    const route = new URL(url).pathname;
    requests.push({route, method:init.method, body:init.body});
    if (route === '/api/orders') {
      return Response.json({order_id:id, recovery_token:'x'.repeat(43), status:'draft'});
    }
    assert.equal(route, '/api/orders/' + id + '/checkout');
    return Response.json({checkout_url:'https://checkout.stripe.com/c/pay/fixture'});
  };
  w.eval(read('app.js'));
  w.eval(read('order-config.js'));
  w.ANTHEMSMITH_ORDERING_PAUSED = false;
  w.eval(read('order-client.js'));
  const email = form.querySelector('[name=delivery][value=email]');
  email.checked = true;
  email.dispatchEvent(new w.Event('change', {bubbles:true}));
  form.elements.namedItem('email').value = 'fixture@example.invalid';
  w.document.getElementById('consent_rights').checked = true;
  form.querySelector('[name=intake_lane][value=photos]').checked = true;
  w.pickLane(2);
  const file = new w.File([png], 'fixture.png', {type:'image/png'});
  file.arrayBuffer = async () => Uint8Array.from(png).buffer;
  const input = w.document.getElementById('photoUpload');
  Object.defineProperty(input, 'files', {value:[file]});
  input.dispatchEvent(new w.Event('change', {bubbles:true}));
  function set(name, value) {
    const el = form.elements.namedItem(name);
    el.value = value;
    form.querySelector('[name="' + name + '"]').dispatchEvent(new w.Event('input', {bubbles:true}));
  }
  return {w, form, requests, set};
}

test('voice and sound require a buyer choice before any photo is sent', async t => {
  const {w, form, set, requests} = storefront(t);
  const voices = [...form.querySelectorAll('[name=voice]')];
  assert.deepEqual(voices.map(el=>el.value).sort(), ['Female','Male']);
  assert(!voices.some(el=>el.checked));
  assert(voices.some(el=>el.required));
  assert.equal(form.elements.namedItem('genre').value, '');
  set('genre', 'Pop');
  await w.submitInstant();
  assert.equal(requests.length, 0, 'missing voice must stop intake');
  set('voice', 'Female');
  set('genre', '');
  await w.submitInstant();
  assert.equal(requests.length, 0, 'empty reference and genre must stop intake');
});

const youtube = 'https://www.youtube.com/watch?v=abcdefghijk';
const spotify = 'https://open.spotify.com/track/1234567890123456789012';
for (const [label, voice, genre, url, artist, song] of [
  ['genre', 'Male', 'Pop', '', '', ''],
  ['YouTube track', 'Female', '', youtube, '', ''],
  ['Spotify track', 'Male', '', spotify, '', ''],
  ['artist and song', 'Female', '', '', 'Fixture Artist', 'Fixture Title'],
  ['genre and track', 'Female', 'Rock', youtube, '', ''],
]) {
  test('sends the selected ' + label + ' and voice unchanged', async t => {
    const {w, set, requests} = storefront(t);
    Object.entries({voice, genre, referenceUrl:url, artist, song}).forEach(([name,value])=>set(name,value));
    await w.submitInstant();
    assert.deepEqual(requests.map(r=>[r.method,r.route]), [
      ['POST','/api/orders'], ['POST','/api/orders/' + id + '/checkout']
    ]);
    const request = JSON.parse(requests[0].body.get('request'));
    assert.equal(request.voice, voice);
    assert.deepEqual(request.reference, {url,artist,song,genre});
    assert.equal(requests[0].body.getAll('files').length, 1);
  });
}

for (const [label, fields] of [
  ['artist only', {artist:'Fixture Artist',genre:'Pop'}],
  ['title only', {song:'Fixture Title',genre:'Pop'}],
  ['Spotify playlist', {referenceUrl:'https://open.spotify.com/playlist/1234567890123456789012'}],
  ['incomplete video ID', {referenceUrl:'https://youtu.be/short'}],
]) {
  test(label + ' is corrected on the form before any upload', async t => {
    const {w, form, set, requests} = storefront(t);
    set('voice','Male');
    Object.entries(fields).forEach(([name,value])=>set(name,value));
    await w.submitInstant();
    assert.equal(requests.length, 0);
    const feedback = w.document.getElementById('requestStatus').textContent;
    const native = [...form.elements].some(el=>el.validity && !el.validity.valid);
    assert(native || /artist|title|track|song|reference|link/i.test(feedback));
  });
}
