'use strict';
(function () {
  var root = document.getElementById('root');
  var state = { shelf: [], results: null, searchMsg: '', query: '' };

  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') e.textContent = attrs[k];
      else if (k === 'class') e.className = attrs[k];
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), attrs[k]);
      else if (k === 'testid') e.setAttribute('data-testid', attrs[k]);
      else e.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) e.appendChild(c); });
    return e;
  }

  function api(method, url, body) {
    var opts = { method: method, headers: {} };
    var tok = localStorage.getItem('rp_token');
    if (tok) opts.headers.Authorization = 'Bearer ' + tok;
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      if (r.status === 401 && url !== '/api/login') { showLogin(); throw new Error('unauthorised'); }
      return r.status === 204 ? { status: 204, data: null } : r.json().catch(function () { return null; }).then(function (data) { return { status: r.status, data: data }; });
    });
  }

  function mount() {
    root.textContent = '';
    for (var i = 0; i < arguments.length; i++) root.appendChild(arguments[i]);
  }

  function showLogin(failed) {
    var input = el('input', { type: 'password', testid: 'login-passphrase', placeholder: 'Passphrase', autocomplete: 'current-password' });
    var err = el('p', { class: 'error', testid: 'login-error', text: 'Wrong passphrase.' });
    err.hidden = !failed;
    function submit() {
      api('POST', '/api/login', { passphrase: input.value }).then(function (r) {
        if (r.status === 200 && r.data && r.data.token) { localStorage.setItem('rp_token', r.data.token); route(); } else showLogin(true);
      }).catch(function () {});
    }
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
    mount(el('h1', { text: 'Reading Place' }), input, el('button', { testid: 'login-submit', text: 'Log in', onclick: submit }), err);
    if (!failed) input.focus();
  }

  function logout() {
    localStorage.removeItem('rp_token');
    location.hash = '';
    showLogin();
  }

  function loadShelf() {
    return api('GET', '/api/shelf').then(function (r) { state.shelf = (r.data && r.data.books) || []; });
  }

  function showHome() {
    loadShelf().then(renderHome).catch(function () {});
  }

  function renderHome() {
    var q = el('input', { type: 'search', testid: 'search-input', placeholder: 'Search the LindyBooks catalogue', value: state.query });
    function search() {
      state.query = q.value;
      state.searchMsg = 'Searching…';
      state.results = null;
      renderHome();
      api('GET', '/api/catalogue/search?q=' + encodeURIComponent(state.query)).then(function (r) {
        if (r.status === 200) { state.results = r.data.books; state.searchMsg = r.data.books.length ? '' : 'No books found.'; }
        else { state.results = null; state.searchMsg = r.status === 400 ? 'Enter a search term.' : 'The catalogue is unreachable right now.'; }
        renderHome();
      }).catch(function () {});
    }
    q.addEventListener('keydown', function (e) { if (e.key === 'Enter') search(); });

    var results = el('div', { testid: 'search-results' });
    if (state.searchMsg) results.appendChild(el('p', { class: 'muted', text: state.searchMsg }));
    (state.results || []).slice(0, 30).forEach(function (b) {
      var btn = el('button', { testid: 'add-book-' + b.catalogue_id, text: 'Add to shelf', onclick: function () {
        api('POST', '/api/shelf', b).then(function (r) {
          if (r.status === 201 || r.status === 409) showHome();
          else { state.searchMsg = 'Could not add the book.'; renderHome(); }
        }).catch(function () {});
      } });
      results.appendChild(el('div', { class: 'card' }, [
        el('strong', { text: b.title }),
        el('div', { class: 'muted', text: b.author + (b.year !== null ? ' · ' + b.year : '') }),
        btn,
      ]));
    });

    var shelf = el('div', { testid: 'shelf-list' });
    if (!state.shelf.length) shelf.appendChild(el('p', { class: 'muted', text: 'Your shelf is empty. Search above to add a book.' }));
    state.shelf.forEach(function (b) {
      shelf.appendChild(el('div', { class: 'card', testid: 'shelf-item-' + b.catalogue_id }, [
        el('strong', { text: b.title }),
        el('div', { class: 'muted', text: b.author }),
        b.position ? el('div', { class: 'muted', text: 'Stopped at: ' + b.position }) : null,
        el('button', { testid: 'open-book-' + b.catalogue_id, text: 'Open', onclick: function () { location.hash = '#book/' + b.id; } }),
      ]));
    });

    mount(
      el('div', { class: 'row' }, [
        el('h1', { text: 'Reading Place' }),
        el('a', { class: 'btn secondary', testid: 'export-link', href: '/api/export', download: 'lindy-reading-place-export.json', text: 'Export JSON', onclick: function (ev) {
          ev.preventDefault();
          fetch('/api/export', { headers: { Authorization: 'Bearer ' + (localStorage.getItem('rp_token') || '') } })
            .then(function (r) { if (!r.ok) throw new Error('export failed'); return r.blob(); })
            .then(function (blob) {
              var a = document.createElement('a');
              a.href = URL.createObjectURL(blob);
              a.download = 'lindy-reading-place-export.json';
              document.body.appendChild(a);
              a.click();
              a.remove();
            }).catch(function () { showLogin(); });
        } }),
        el('button', { class: 'secondary', testid: 'logout', text: 'Log out', onclick: logout }),
      ]),
      el('div', { class: 'row' }, [q, el('button', { testid: 'search-submit', text: 'Search', onclick: search })]),
      results,
      el('h2', { text: 'Your shelf' }),
      shelf
    );
  }

  function showBook(id) {
    Promise.all([api('GET', '/api/shelf/' + id), api('GET', '/api/shelf/' + id + '/notes'), api('GET', '/api/shelf/' + id + '/shares')]).then(function (rs) {
      if (rs[0].status !== 200) { location.hash = ''; return; }
      renderBook(rs[0].data, (rs[1].data && rs[1].data.notes) || [], (rs[2].data && rs[2].data.shares) || []);
    }).catch(function () {});
  }

  function shareSection(book, shares) {
    var box = el('div', {}, [
      el('h2', { text: 'Share notes' }),
      el('button', { testid: 'share-create', text: 'Create share link', onclick: function () {
        api('POST', '/api/shelf/' + book.id + '/shares').then(function () { showBook(book.id); }).catch(function () {});
      } }),
    ]);
    var s = shares[shares.length - 1];
    if (s) {
      var copyMsg = el('span', { class: 'muted' });
      box.appendChild(el('p', { class: 'muted', text: 'Anyone with this link can read this book’s notes, and nothing else.' }));
      box.appendChild(el('p', { testid: 'share-link', text: s.url, style: 'word-break: break-all; user-select: all' }));
      box.appendChild(el('div', { class: 'row' }, [
        el('button', { class: 'secondary', testid: 'share-copy', text: 'Copy link', onclick: function () {
          function fallback() {
            var ta = el('textarea', {});
            ta.value = s.url;
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand('copy'); copyMsg.textContent = 'Copied.'; } catch (e) { copyMsg.textContent = 'Select the link and copy it.'; }
            ta.remove();
          }
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(s.url).then(function () { copyMsg.textContent = 'Copied.'; }, fallback);
          } else fallback();
        } }),
        el('button', { class: 'secondary', testid: 'share-revoke', text: 'Revoke link', onclick: function () {
          api('DELETE', '/api/shares/' + encodeURIComponent(s.token)).then(function () { showBook(book.id); }).catch(function () {});
        } }),
        copyMsg,
      ]));
    }
    return box;
  }

  function renderBook(book, notes, shares) {
    var posInput = el('input', { testid: 'position-input', placeholder: 'e.g. Book 2, ch. 3', value: book.position || '' });
    var posMsg = el('p', { class: 'error' });
    var posBtn = el('button', { testid: 'position-save', text: 'Save position', onclick: function () {
      api('PUT', '/api/shelf/' + book.id + '/position', { position: posInput.value }).then(function (r) {
        if (r.status === 200) showBook(book.id); else posMsg.textContent = 'Enter where you stopped.';
      }).catch(function () {});
    } });

    var noteInput = el('textarea', { testid: 'note-input', rows: '3', placeholder: 'Write a note' });
    var noteMsg = el('p', { class: 'error' });
    var noteBtn = el('button', { testid: 'note-save', text: 'Save note', onclick: function () {
      api('POST', '/api/shelf/' + book.id + '/notes', { body: noteInput.value, position: book.position || '' }).then(function (r) {
        if (r.status === 201) showBook(book.id); else noteMsg.textContent = 'A note cannot be empty.';
      }).catch(function () {});
    } });

    var list = el('div', { testid: 'note-list' });
    notes.forEach(function (n) {
      var body = el('div', { text: n.body, style: 'white-space: pre-wrap' });
      var card = el('div', { class: 'card', 'data-note-id': n.id });
      function view() {
        card.textContent = '';
        card.appendChild(body);
        if (n.position) card.appendChild(el('div', { class: 'muted', text: 'At: ' + n.position }));
        card.appendChild(el('div', { class: 'row' }, [
          el('button', { class: 'secondary', testid: 'note-edit', text: 'Edit', onclick: edit }),
          el('button', { class: 'secondary', testid: 'note-delete', text: 'Delete', onclick: function () {
            api('DELETE', '/api/notes/' + n.id).then(function () { showBook(book.id); }).catch(function () {});
          } }),
        ]));
      }
      function edit() {
        var ta = el('textarea', { rows: '3', testid: 'note-edit-input' });
        ta.value = n.body;
        card.textContent = '';
        card.appendChild(ta);
        card.appendChild(el('div', { class: 'row' }, [
          el('button', { testid: 'note-edit-save', text: 'Save', onclick: function () {
            api('PUT', '/api/notes/' + n.id, { body: ta.value }).then(function (r) {
              if (r.status === 200) showBook(book.id);
            }).catch(function () {});
          } }),
          el('button', { class: 'secondary', text: 'Cancel', onclick: view }),
        ]));
      }
      view();
      list.appendChild(card);
    });
    if (!notes.length) list.appendChild(el('p', { class: 'muted', text: 'No notes yet.' }));

    mount(
      el('button', { class: 'secondary', testid: 'back', text: '← Shelf', onclick: function () { location.hash = ''; } }),
      el('h1', { text: book.title }),
      el('div', { class: 'muted', text: book.author }),
      el('h2', { text: 'Where I stopped' }),
      el('p', { testid: 'position-display', text: book.position || 'Not set yet' }),
      posInput, posBtn, posMsg,
      el('p', {}, [el('a', { class: 'btn', testid: 'resume-link', href: book.text_url, target: '_blank', rel: 'noopener noreferrer', text: 'Open the text in LindyBooks' })]),
      el('h2', { text: 'Notes' }),
      noteInput, noteBtn, noteMsg, list,
      shareSection(book, shares)
    );
  }

  function route() {
    var m = location.hash.match(/^#book\/(\d+)$/);
    if (m) showBook(m[1]); else showHome();
  }

  // A browser login lasts as long as the server run that issued it: a restart asks for the passphrase again.
  function start() {
    fetch('/api/health').then(function (r) { return r.json(); }).then(function (h) {
      var boot = h && h.boot;
      if (boot && localStorage.getItem('rp_boot') !== boot) {
        localStorage.removeItem('rp_token');
        localStorage.setItem('rp_boot', boot);
      }
    }).catch(function () {}).then(function () {
      if (localStorage.getItem('rp_token')) route(); else showLogin();
    });
  }

  window.addEventListener('hashchange', function () {
    if (localStorage.getItem('rp_token')) route();
  });
  start();
})();
