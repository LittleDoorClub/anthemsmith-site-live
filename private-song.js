export const MAX_AUDIO = 20 * 1024 * 1024;
const API = 'https://veqpmdsiqcjjpxgcubrp.supabase.co/functions/v1/anthemsmith-song-access';
const ERROR_TEXT = {
  invalid_capability: 'This secure link is invalid or has been revoked. Open the complete original link or contact support.',
  expired_capability: 'This secure link has expired. Contact support for help accessing your existing song.',
  access_unavailable: 'Access to this song is currently unavailable. Contact support so we can check your existing order.',
  audio_unavailable: 'Your song could not be loaded. Reopen your original link to retry, or contact support.',
  service_unavailable: 'We could not connect to your song. Reopen your original link to retry, or contact support.',
};

export function parseCapability(hash) {
  if (typeof hash !== 'string' || !hash.startsWith('#')) throw new Error('invalid_capability');
  const params = new URLSearchParams(hash.slice(1));
  if ([...params].length !== 2 || params.getAll('order').length !== 1 || params.getAll('token').length !== 1) {
    throw new Error('invalid_capability');
  }
  const order = params.get('order'), token = params.get('token');
  if (!/^AS-[A-Z0-9-]{6,64}$/.test(order || '') || !/^[A-Za-z0-9_-]{43}$/.test(token || '')) {
    throw new Error('invalid_capability');
  }
  return { order, token };
}

async function readBounded(response, limit) {
  const length = response.headers.get('Content-Length');
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > limit)) {
    await response.body?.cancel();
    throw new Error('audio_unavailable');
  }
  if (!response.body) throw new Error('audio_unavailable');
  const reader = response.body.getReader(), chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new Error('audio_unavailable'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  if (length !== null && Number(length) !== size) throw new Error('audio_unavailable');
  return { chunks, size };
}

async function readJSON(response) {
  const { chunks } = await readBounded(response, 4096);
  return JSON.parse(await new Blob(chunks).text());
}

export async function loadAudio(capability, fetchImpl = fetch, signal) {
  async function request(action) {
    let response;
    try {
      response = await fetchImpl(API + '/' + action, {
        method: 'GET', headers: { Authorization: 'Bearer ' + capability.token, 'X-Anthem-Order': capability.order },
        credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer', redirect: 'error',
        signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(90000)]) : AbortSignal.timeout(90000),
      });
    } catch { throw new Error('service_unavailable'); }
    if (!response.ok) {
      let code = 'service_unavailable';
      try { const data = await readJSON(response); if (Object.hasOwn(ERROR_TEXT, data?.error)) code = data.error; } catch {}
      throw new Error(code);
    }
    return response;
  }
  let status;
  try { status = await readJSON(await request('status')); }
  catch (error) { throw new Error(Object.hasOwn(ERROR_TEXT, error.message) ? error.message : 'service_unavailable'); }
  if (status.status !== 'ready' || status.mime_type !== 'audio/mpeg' ||
      !Number.isSafeInteger(status.byte_size) || status.byte_size < 1 || status.byte_size > MAX_AUDIO ||
      typeof status.duration_seconds !== 'number' || status.duration_seconds < 180 || status.duration_seconds > 240) {
    throw new Error('audio_unavailable');
  }
  const response = await request('audio');
  if (response.status !== 200 || response.headers.get('Content-Type')?.split(';')[0].trim() !== 'audio/mpeg') {
    await response.body?.cancel();
    throw new Error('audio_unavailable');
  }
  const { chunks, size } = await readBounded(response, status.byte_size);
  if (size !== status.byte_size) throw new Error('audio_unavailable');
  return new Blob(chunks, { type: 'audio/mpeg' });
}

export async function initialize({ document, location, history, urlAPI = URL, fetchImpl = fetch, lifecycle = globalThis }) {
  const status = document.getElementById('status'), player = document.getElementById('player');
  const audio = document.getElementById('audio'), download = document.getElementById('download');
  const retry = document.getElementById('retry');
  let capability, objectURL, controller, timer, departed = false, busy = false, failed = false, generation = 0;
  const clearMedia = () => {
    clearTimeout(timer);
    audio.pause?.();
    audio.removeAttribute('src');
    if (objectURL) urlAPI.revokeObjectURL(objectURL);
    objectURL = null;
    player.hidden = true;
    download.disabled = true;
  };
  lifecycle.addEventListener?.('pagehide', () => {
    departed = true;
    generation++;
    controller?.abort();
    capability = null;
    clearMedia();
    if (retry) retry.hidden = true;
    status.textContent = 'Reopen your original secure link to load your song again.';
  }, { once: true });
  const error = text => {
    failed = true;
    busy = false;
    controller?.abort();
    clearMedia();
    status.textContent = text;
    status.dataset.error = 'true';
    if (retry) { retry.hidden = false; retry.disabled = false; }
  };
  async function load() {
    if (busy || departed || !capability) return;
    const current = ++generation;
    busy = true;
    failed = false;
    clearMedia();
    controller = new AbortController();
    if (retry) { retry.hidden = true; retry.disabled = true; }
    status.dataset.error = 'false';
    status.textContent = 'Loading your private song…';
    try {
      const blob = await loadAudio(capability, fetchImpl, controller.signal);
      if (departed || current !== generation) return;
      objectURL = urlAPI.createObjectURL(blob);
      const valid = () => !departed && current === generation && !failed;
      const ready = () => {
        if (!valid()) return;
        if (!Number.isFinite(audio.duration) || audio.duration < 180 || audio.duration > 240) {
          error('This audio did not pass the full-song check. Retry your existing song or contact support. Do not pay again.');
          return;
        }
        clearTimeout(timer);
        busy = false;
        download.disabled = false;
        status.textContent = 'Your full song is ready to play or download.';
      };
      audio.onloadedmetadata = () => {
        if (valid() && (!Number.isFinite(audio.duration) || audio.duration < 180 || audio.duration > 240)) {
          error('This audio did not pass the full-song check. Retry your existing song or contact support. Do not pay again.');
        }
      };
      audio.oncanplay = ready;
      audio.onplaying = ready;
      audio.onerror = () => { if (valid()) error('We could not play your song. Retry the existing song or contact support. Do not pay again.'); };
      audio.src = objectURL;
      audio.preload = 'auto';
      player.hidden = false;
      status.textContent = 'Checking that your full song can play…';
      timer = setTimeout(() => { if (valid() && download.disabled) error('Your song is taking too long to load. Retry the existing song. Do not pay again.'); }, 15000);
      timer.unref?.();
      audio.load?.();
    } catch (cause) {
      if (!departed && current === generation) error(ERROR_TEXT[cause.message] || ERROR_TEXT.service_unavailable);
    }
  }
  download.addEventListener('click', () => {
    if (departed || failed || download.disabled || !objectURL) return;
    const link = document.createElement('a');
    link.href = objectURL;
    link.download = 'AnthemSmith-song.mp3';
    document.body.append(link);
    link.click();
    link.remove();
  });
  retry?.addEventListener('click', load);
  try {
    try { capability = parseCapability(location.hash); }
    // Erase always (even for an invalid fragment); a throwing history must never mislabel it.
    finally { try { history.replaceState(null, '', location.pathname); } catch {} }
  } catch {
    clearMedia();
    status.textContent = ERROR_TEXT.invalid_capability;
    status.dataset.error = 'true';
    return;
  }
  await load();
}

if (typeof document !== 'undefined') {
  initialize({ document, location, history });
}
