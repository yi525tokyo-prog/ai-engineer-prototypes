'use strict';
(function () {
  var root = document.getElementById('root');

  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') e.textContent = attrs[k];
      else if (k === 'class') e.className = attrs[k];
      else if (k === 'testid') e.setAttribute('data-testid', attrs[k]);
      else e.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) e.appendChild(c); });
    return e;
  }

  function unavailable() {
    root.textContent = '';
    root.appendChild(el('p', { testid: 'shared-unavailable', text: 'This link is not available.' }));
  }

  var token = location.pathname.replace(/^\/shared\//, '').replace(/\/$/, '');
  fetch('/api/shared/' + encodeURIComponent(token)).then(function (r) {
    if (r.status !== 200) return unavailable();
    return r.json().then(function (data) {
      var list = el('div', { testid: 'shared-note-list' });
      if (!data.notes.length) list.appendChild(el('p', { class: 'muted', text: 'No notes yet.' }));
      data.notes.forEach(function (n) {
        list.appendChild(el('div', { class: 'card' }, [
          el('div', { text: n.body, style: 'white-space: pre-wrap' }),
          n.position ? el('div', { class: 'muted', text: 'At: ' + n.position }) : null,
        ]));
      });
      root.textContent = '';
      root.appendChild(el('div', { testid: 'shared-book-title' }, [
        el('h1', { text: data.book.title }),
        el('div', { class: 'muted', text: data.book.author }),
      ]));
      root.appendChild(el('h2', { text: 'Notes' }));
      root.appendChild(list);
    });
  }).catch(unavailable);
})();
