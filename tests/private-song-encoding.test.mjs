import test from 'node:test';
import assert from 'node:assert/strict';
import { gzipSync } from 'node:zlib';
import { loadAudio } from '../private-song.js';

const order = 'AS-PRIVATE-PAGETEST', token = 'A'.repeat(43);
const capability = { order, token };
const bytes = new Uint8Array([1, 2, 3, 4]);
const manifest = { status: 'ready', byte_size: bytes.length, mime_type: 'audio/mpeg', duration_seconds: 204 };

// A browser's fetch transparently decodes Content-Encoding: the body reader
// yields the DECODED bytes while the Content-Length header still counts the
// ENCODED wire bytes. These Responses reproduce exactly that view: decoded
// body bytes under a wire-length Content-Length with a gzip Content-Encoding.
function browserResponse(decodedBytes, contentType) {
  const wire = gzipSync(decodedBytes);
  assert.notEqual(wire.length, decodedBytes.length);
  return new Response(decodedBytes, {
    headers: {
      'Content-Type': contentType,
      'Content-Encoding': 'gzip',
      'Content-Length': String(wire.length),
    },
  });
}

test('content-encoded status and audio (the browser view) still load the song', async () => {
  const blob = await loadAudio(capability, async url => url.endsWith('/status')
    ? browserResponse(new TextEncoder().encode(JSON.stringify(manifest)), 'application/json')
    : browserResponse(bytes, 'audio/mpeg'));
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), bytes);
});

test('a truncated content-encoded stream still fails', async () => {
  await assert.rejects(
    loadAudio(capability, async url => url.endsWith('/status')
      ? browserResponse(new TextEncoder().encode(JSON.stringify(manifest)), 'application/json')
      : browserResponse(bytes.subarray(0, 1), 'audio/mpeg')),
    /audio_unavailable/);
});
