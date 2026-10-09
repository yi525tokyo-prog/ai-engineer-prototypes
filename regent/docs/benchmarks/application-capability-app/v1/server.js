'use strict';
// Lindy Reading Place: single-owner reading shelf. No dependencies.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const PUBLIC_DIR = path.join(__dirname, 'public');
const STATIC = {
  '/': ['index.html', 'text/html; charset=utf-8'],
  '/index.html': ['index.html', 'text/html; charset=utf-8'],
  '/app.js': ['app.js', 'application/javascript; charset=utf-8'],
  '/style.css': ['style.css', 'text/css; charset=utf-8'],
};

function sha(s) {
  return crypto.createHash('sha256').update(s).digest();
}

function safeEqual(a, b) {
  return crypto.timingSafeEqual(sha(String(a)), sha(String(b)));
}

function now() {
  return new Date().toISOString();
}

// JSON-file store; every mutation is written through atomically.
class Store {
  constructor(dir) {
    this.dir = dir;
    this.file = path.join(dir, 'reading-place.json');
    this.data = { nextBook: 1, nextNote: 1, books: [], notes: [] };
    if (fs.existsSync(this.file)) {
      this.data = JSON.parse(fs.readFileSync(this.file, 'utf8'));
    }
  }

  save() {
    fs.mkdirSync(this.dir, { recursive: true });
    const tmp = this.file + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(this.data));
    fs.renameSync(tmp, this.file);
  }
}

function createApp(opts) {
  const passphrase = opts.passphrase;
  const store = new Store(opts.dataDir);
  const catalogueUrl = opts.catalogueUrl || 'https://lindy-api.yi525tokyo.workers.dev/api/search';
  const fetchImpl = opts.fetch || fetch;
  // The bearer token is derived from the passphrase so it survives restarts.
  const bearerToken = crypto.createHmac('sha256', passphrase).update('lindy-reading-place-token').digest('hex');

  // Changes on every start; the browser UI ends its login when it sees a new one.
  const bootId = crypto.randomBytes(8).toString('hex');

  function authed(req) {
    const h = req.headers.authorization || '';
    // Bearer only: a cookie must never authorise API calls.
    return h.startsWith('Bearer ') && safeEqual(h.slice(7).trim(), bearerToken);
  }

  function send(res, status, body, headers) {
    const h = Object.assign({ 'X-Robots-Tag': 'noindex, nofollow', 'Cache-Control': 'no-store' }, headers);
    if (body === undefined) {
      res.writeHead(status, h);
      res.end();
      return;
    }
    h['Content-Type'] = h['Content-Type'] || 'application/json; charset=utf-8';
    res.writeHead(status, h);
    res.end(typeof body === 'string' ? body : JSON.stringify(body));
  }

  function readBody(req) {
    return new Promise((resolve, reject) => {
      const chunks = [];
      let size = 0;
      req.on('data', (c) => {
        size += c.length;
        if (size > 1e6) {
          reject(new Error('too large'));
          req.destroy();
        } else chunks.push(c);
      });
      req.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        if (!raw.trim()) return resolve({});
        try {
          const v = JSON.parse(raw);
          resolve(v && typeof v === 'object' && !Array.isArray(v) ? v : {});
        } catch (e) {
          reject(new Error('bad json'));
        }
      });
      req.on('error', reject);
    });
  }

  const d = store.data;
  const findBook = (id) => d.books.find((b) => b.id === Number(id));
  const findNote = (id) => d.notes.find((n) => n.id === Number(id));
  const notesOf = (bookId) => d.notes.filter((n) => n.shelf_id === bookId);
  const nonEmpty = (v) => typeof v === 'string' && v.trim() !== '';

  async function handleApi(req, res, url) {
    const p = url.pathname;
    const m = req.method;

    if (p === '/api/health' && m === 'GET') return send(res, 200, { status: 'ok', boot: bootId });

    if (p === '/api/login' && m === 'POST') {
      let body;
      try {
        body = await readBody(req);
      } catch (e) {
        return send(res, 400, { error: 'bad request' });
      }
      if (typeof body.passphrase !== 'string' || !safeEqual(body.passphrase, passphrase)) {
        return send(res, 401, { error: 'wrong passphrase' });
      }
      return send(res, 200, { ok: true, token: bearerToken });
    }

    if (!authed(req)) return send(res, 401, { error: 'authentication required' });

    let match;
    if (p === '/api/catalogue/search' && m === 'GET') {
      const q = (url.searchParams.get('q') || '').trim();
      if (!q) return send(res, 400, { error: 'q required' });
      try {
        const r = await fetchImpl(catalogueUrl + '?q=' + encodeURIComponent(q), {
          signal: AbortSignal.timeout(15000),
        });
        if (!r.ok) throw new Error('upstream ' + r.status);
        const j = await r.json();
        const books = (j.books || []).map((b) => ({
          catalogue_id: b.id,
          title: b.t,
          author: b.a,
          year: Number.isInteger(b.year) ? b.year : null,
          text_url: b.txt,
        }));
        return send(res, 200, { books, total: typeof j.total === 'number' ? j.total : books.length });
      } catch (e) {
        return send(res, 502, { error: 'catalogue unreachable' });
      }
    }

    if (p === '/api/shelf' && m === 'GET') return send(res, 200, { books: d.books });

    if (p === '/api/shelf' && m === 'POST') {
      let b;
      try {
        b = await readBody(req);
      } catch (e) {
        return send(res, 400, { error: 'bad request' });
      }
      if (!nonEmpty(b.title) || !nonEmpty(b.author)) return send(res, 400, { error: 'title and author required' });
      if (!Number.isInteger(b.catalogue_id)) return send(res, 400, { error: 'catalogue_id must be an integer' });
      if (d.books.some((x) => x.catalogue_id === b.catalogue_id)) return send(res, 409, { error: 'already on shelf' });
      const book = {
        id: d.nextBook++,
        catalogue_id: b.catalogue_id,
        title: b.title,
        author: b.author,
        year: Number.isInteger(b.year) ? b.year : null,
        text_url: typeof b.text_url === 'string' ? b.text_url : '',
        position: '',
        position_updated_at: null,
        added_at: now(),
      };
      d.books.push(book);
      store.save();
      return send(res, 201, book);
    }

    if ((match = p.match(/^\/api\/shelf\/(\d+)$/))) {
      const book = findBook(match[1]);
      if (!book) return send(res, 404, { error: 'not found' });
      if (m === 'GET') return send(res, 200, Object.assign({}, book, { resume_url: book.text_url }));
      if (m === 'DELETE') {
        d.books = d.books.filter((x) => x !== book);
        d.notes = d.notes.filter((n) => n.shelf_id !== book.id);
        store.save();
        return send(res, 204);
      }
    }

    if ((match = p.match(/^\/api\/shelf\/(\d+)\/position$/)) && m === 'PUT') {
      const book = findBook(match[1]);
      if (!book) return send(res, 404, { error: 'not found' });
      let b;
      try {
        b = await readBody(req);
      } catch (e) {
        return send(res, 400, { error: 'bad request' });
      }
      if (!nonEmpty(b.position)) return send(res, 400, { error: 'position required' });
      book.position = b.position;
      book.position_updated_at = now();
      store.save();
      return send(res, 200, book);
    }

    if ((match = p.match(/^\/api\/shelf\/(\d+)\/notes$/))) {
      const book = findBook(match[1]);
      if (!book) return send(res, 404, { error: 'not found' });
      if (m === 'GET') return send(res, 200, { notes: notesOf(book.id) });
      if (m === 'POST') {
        let b;
        try {
          b = await readBody(req);
        } catch (e) {
          return send(res, 400, { error: 'bad request' });
        }
        if (!nonEmpty(b.body)) return send(res, 400, { error: 'body required' });
        const t = now();
        const note = {
          id: d.nextNote++,
          shelf_id: book.id,
          body: b.body,
          position: typeof b.position === 'string' ? b.position : '',
          created_at: t,
          updated_at: t,
        };
        d.notes.push(note);
        store.save();
        return send(res, 201, note);
      }
    }

    if ((match = p.match(/^\/api\/notes\/(\d+)$/))) {
      const note = findNote(match[1]);
      if (!note) return send(res, 404, { error: 'not found' });
      if (m === 'PUT') {
        let b;
        try {
          b = await readBody(req);
        } catch (e) {
          return send(res, 400, { error: 'bad request' });
        }
        if (!nonEmpty(b.body)) return send(res, 400, { error: 'body required' });
        note.body = b.body;
        if (typeof b.position === 'string') note.position = b.position;
        note.updated_at = now();
        store.save();
        return send(res, 200, note);
      }
      if (m === 'DELETE') {
        d.notes = d.notes.filter((n) => n !== note);
        store.save();
        return send(res, 204);
      }
    }

    if (p === '/api/export' && m === 'GET') {
      const out = { exported_at: now(), books: d.books.map((b) => Object.assign({}, b, { notes: notesOf(b.id) })) };
      return send(res, 200, JSON.stringify(out, null, 2), {
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Disposition': 'attachment; filename="lindy-reading-place-export.json"',
      });
    }

    return send(res, 404, { error: 'not found' });
  }

  return http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/api/')) {
      handleApi(req, res, url).catch(() => send(res, 500, { error: 'internal error' }));
      return;
    }
    if (url.pathname === '/robots.txt') {
      return send(res, 200, 'User-agent: *\nDisallow: /\n', { 'Content-Type': 'text/plain' });
    }
    const entry = req.method === 'GET' && STATIC[url.pathname];
    if (!entry) return send(res, 404, { error: 'not found' });
    try {
      send(res, 200, fs.readFileSync(path.join(PUBLIC_DIR, entry[0]), 'utf8'), { 'Content-Type': entry[1] });
    } catch (e) {
      send(res, 500, { error: 'asset missing' });
    }
  });
}

module.exports = { createApp };

if (require.main === module) {
  const port = Number(process.env.PORT || 8000);
  const dataDir = process.env.DATA_DIR || path.join(__dirname, 'data');
  const passphrase = process.env.APP_PASSPHRASE;
  if (!passphrase) {
    console.error('APP_PASSPHRASE is required');
    process.exit(1);
  }
  createApp({ dataDir, passphrase }).listen(port, '127.0.0.1', () => {
    console.log(`lindy-reading-place listening on 127.0.0.1:${port}`);
  });
}
