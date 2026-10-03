'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createApp } = require('../server.js');

const fakeFetch = async () => ({
  ok: true,
  json: async () => ({ total: 1, books: [{ id: 2680, t: 'Meditations', a: 'Marcus Aurelius', year: 180, txt: 'https://example/2680' }] }),
});

function start(dir) {
  const server = createApp({ dataDir: dir, passphrase: 'pw', fetch: fakeFetch });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

async function call(server, method, p, body, token) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = 'Bearer ' + token;
  const r = await fetch(`http://127.0.0.1:${server.address().port}${p}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  return { status: r.status, json: text ? JSON.parse(text) : null };
}

test('auth, shelf, position, notes, persistence, export', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rp-'));
  let s = await start(dir);
  assert.strictEqual((await call(s, 'GET', '/api/health')).status, 200);
  assert.strictEqual((await call(s, 'GET', '/api/shelf')).status, 401);
  assert.strictEqual((await call(s, 'POST', '/api/login', { passphrase: 'bad' })).status, 401);
  const token = (await call(s, 'POST', '/api/login', { passphrase: 'pw' })).json.token;

  const search = await call(s, 'GET', '/api/catalogue/search?q=med', undefined, token);
  assert.strictEqual(search.json.books[0].catalogue_id, 2680);
  assert.strictEqual((await call(s, 'GET', '/api/catalogue/search?q=', undefined, token)).status, 400);

  const book = { year: 180, title: 'Meditations', author: 'MA', text_url: 'u', catalogue_id: 2680 };
  const added = await call(s, 'POST', '/api/shelf', book, token);
  assert.strictEqual(added.status, 201);
  assert.strictEqual((await call(s, 'POST', '/api/shelf', book, token)).status, 409);
  const id = added.json.id;
  assert.strictEqual((await call(s, 'PUT', `/api/shelf/${id}/position`, { position: '' }, token)).status, 400);
  assert.strictEqual((await call(s, 'PUT', `/api/shelf/${id}/position`, { position: 'Book 2' }, token)).status, 200);
  const note = await call(s, 'POST', `/api/shelf/${id}/notes`, { body: 'hello' }, token);
  assert.strictEqual(note.status, 201);

  await new Promise((r) => s.close(r));
  s = await start(dir);
  const got = await call(s, 'GET', `/api/shelf/${id}`, undefined, token);
  assert.strictEqual(got.json.position, 'Book 2');
  assert.strictEqual(got.json.resume_url, 'u');
  const exp = await call(s, 'GET', '/api/export', undefined, token);
  assert.strictEqual(exp.json.books[0].notes[0].body, 'hello');
  assert.strictEqual((await call(s, 'DELETE', `/api/notes/${note.json.id}`, undefined, token)).status, 204);
  assert.strictEqual((await call(s, 'PUT', `/api/notes/${note.json.id}`, { body: 'x' }, token)).status, 404);
  assert.strictEqual((await call(s, 'POST', '/api/shelf/999999/notes', { body: 'x' }, token)).status, 404);
  await new Promise((r) => s.close(r));
});
