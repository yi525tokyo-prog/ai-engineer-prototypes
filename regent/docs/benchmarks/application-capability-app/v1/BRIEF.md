# Build this application
## What the person needs
I read old books on LindyBooks on my phone and keep losing track of where I stopped and what I thought about them. I want my own private place, usable from any browser, where I can pick books from the LindyBooks catalogue and keep my place and my notes - not in some other company's app.

Requirements:
- [r1] (core) Browse or search the LindyBooks catalogue and add chosen books to the principal's personal shelf/list — accepted when: Principal searches for a book that exists in the LindyBooks catalogue, adds it, and it appears on their shelf with title and author matching the catalogue entry
- [r2] (core) Record and persist reading position per book (where they stopped), and let them resume from it — accepted when: After setting a position in a book, closing the browser and reopening from a different device/browser, the same position is shown and a link/action returns to it in LindyBooks
- [r3] (core) Create, edit, view and delete personal notes attached to a book (optionally to a position) — accepted when: A note written on one device is visible and editable on another after reload; deleting it removes it; notes are listed per book
- [r4] (core) Private to the principal: access only after authentication, data not visible to others or publicly indexed — accepted when: An unauthenticated request to any shelf, position or note page/data is denied; no public URL exposes them
- [r5] (core) Works in any modern browser including a phone browser, with no app install — accepted when: Core flows (r1-r3) complete in current mobile and desktop browsers (Chrome, Safari, Firefox) without installing anything
- [r6] (supporting) Principal controls and can export their data; hosted under their control, not a third-party reading app — accepted when: Principal can export all shelf, positions and notes in an open format (e.g. JSON/Markdown) and the service is run under their own ownership

## Design (Regent's interface contract; keep paths, fields and data-testids exactly)
```json
{
 "name": "lindy-reading-place",
 "summary": "A private, single-owner web app: search the LindyBooks catalogue, add books to a personal shelf, record where you stopped in each book (with a link back to the text), keep notes per book, and export everything as JSON. All data endpoints require the owner's passphrase.",
 "entities": [
  {
   "name": "ShelfBook",
   "fields": [
    "id (server-assigned)",
    "catalogue_id (int, LindyBooks id, unique per shelf)",
    "title",
    "author",
    "year (int or null)",
    "text_url",
    "position (string, free text such as 'Book 2, ch. 3', empty by default)",
    "position_updated_at (ISO timestamp or null)",
    "added_at"
   ]
  },
  {
   "name": "Note",
   "fields": [
    "id (server-assigned)",
    "shelf_id (ShelfBook id)",
    "body (string)",
    "position (optional string)",
    "created_at",
    "updated_at"
   ]
  }
 ],
 "auth": {
  "why": "The shelf, reading positions and notes are personal and must not be publicly reachable; a single owner needs one passphrase (sent as a Bearer token on API calls, or via the session cookie set by POST /api/login) rather than accounts. Only /api/health and /api/login are public.",
  "scheme": "passphrase"
 },
 "api": [
  {
   "id": "health",
   "path": "/api/health",
   "method": "GET",
   "public": true,
   "purpose": "Liveness check",
   "request": null,
   "response": "200 {\"status\":\"ok\"}",
   "requirements": [
    "r5"
   ]
  },
  {
   "id": "login",
   "path": "/api/login",
   "method": "POST",
   "public": true,
   "purpose": "Check the passphrase and set a session cookie for the browser UI",
   "request": {
    "passphrase": "string"
   },
   "response": "200 {\"ok\":true} with session cookie; 401 on wrong passphrase",
   "requirements": [
    "r4"
   ]
  },
  {
   "id": "search",
   "path": "/api/catalogue/search",
   "method": "GET",
   "public": false,
   "purpose": "Search the LindyBooks catalogue (server proxies the external search by query q) and return normalised results",
   "request": null,
   "response": "200 {\"books\":[{\"catalogue_id\",\"title\",\"author\",\"year\",\"text_url\"}],\"total\"}; query param q required (400 if empty); 401 without credential; 502 if catalogue unreachable",
   "requirements": [
    "r1",
    "r4"
   ]
  },
  {
   "id": "shelf_add",
   "path": "/api/shelf",
   "method": "POST",
   "public": false,
   "purpose": "Add a catalogue book to the shelf",
   "request": {
    "year": "int|null",
    "title": "string",
    "author": "string",
    "text_url": "string",
    "catalogue_id": "int"
   },
   "response": "201 ShelfBook; 409 if catalogue_id already on shelf; 400 if title/author missing; 401 without credential",
   "requirements": [
    "r1",
    "r4"
   ]
  },
  {
   "id": "shelf_list",
   "path": "/api/shelf",
   "method": "GET",
   "public": false,
   "purpose": "List shelf books",
   "request": null,
   "response": "200 {\"books\":[ShelfBook]}",
   "requirements": [
    "r1",
    "r2",
    "r4"
   ]
  },
  {
   "id": "shelf_get",
   "path": "/api/shelf/{id}",
   "method": "GET",
   "public": false,
   "purpose": "One shelf book with position and resume link",
   "request": null,
   "response": "200 ShelfBook plus resume_url (= text_url); 404 if unknown",
   "requirements": [
    "r2",
    "r4"
   ]
  },
  {
   "id": "shelf_remove",
   "path": "/api/shelf/{id}",
   "method": "DELETE",
   "public": false,
   "purpose": "Remove a book and its notes from the shelf",
   "request": null,
   "response": "204; 404 if unknown",
   "requirements": [
    "r1",
    "r4"
   ]
  },
  {
   "id": "position_set",
   "path": "/api/shelf/{id}/position",
   "method": "PUT",
   "public": false,
   "purpose": "Set where the reader stopped",
   "request": {
    "position": "string (non-empty)"
   },
   "response": "200 ShelfBook with new position; 400 if empty; 404 if unknown",
   "requirements": [
    "r2",
    "r4"
   ]
  },
  {
   "id": "note_add",
   "path": "/api/shelf/{id}/notes",
   "method": "POST",
   "public": false,
   "purpose": "Add a note to a book",
   "request": {
    "body": "string (non-empty)",
    "position": "string (optional)"
   },
   "response": "201 Note; 400 if body empty; 404 if book unknown",
   "requirements": [
    "r3",
    "r4"
   ]
  },
  {
   "id": "note_list",
   "path": "/api/shelf/{id}/notes",
   "method": "GET",
   "public": false,
   "purpose": "List notes of one book",
   "request": null,
   "response": "200 {\"notes\":[Note]}; 404 if book unknown",
   "requirements": [
    "r3",
    "r4"
   ]
  },
  {
   "id": "note_edit",
   "path": "/api/notes/{note_id}",
   "method": "PUT",
   "public": false,
   "purpose": "Edit a note",
   "request": {
    "body": "string",
    "position": "string (optional)"
   },
   "response": "200 Note; 400 if body empty; 404 if unknown",
   "requirements": [
    "r3",
    "r4"
   ]
  },
  {
   "id": "note_delete",
   "path": "/api/notes/{note_id}",
   "method": "DELETE",
   "public": false,
   "purpose": "Delete a note",
   "request": null,
   "response": "204; 404 if unknown",
   "requirements": [
    "r3",
    "r4"
   ]
  },
  {
   "id": "export",
   "path": "/api/export",
   "method": "GET",
   "public": false,
   "purpose": "Export all shelf books, positions and notes as JSON",
   "request": null,
   "response": "200 {\"exported_at\",\"books\":[ShelfBook + \"notes\":[Note]]}",
   "requirements": [
    "r6",
    "r4"
   ]
  }
 ],
 "ui": [
  {
   "page": "/",
   "testid": "login-passphrase",
   "purpose": "Passphrase input shown when not logged in"
  },
  {
   "page": "/",
   "testid": "login-submit",
   "purpose": "Log in button"
  },
  {
   "page": "/",
   "testid": "login-error",
   "purpose": "Shown after a wrong passphrase"
  },
  {
   "page": "/",
   "testid": "search-input",
   "purpose": "Catalogue search field"
  },
  {
   "page": "/",
   "testid": "search-submit",
   "purpose": "Run search"
  },
  {
   "page": "/",
   "testid": "add-book-2680",
   "purpose": "Add button on the search result whose catalogue id is 2680 (pattern add-book-{catalogue_id})"
  },
  {
   "page": "/",
   "testid": "shelf-list",
   "purpose": "Shelf container"
  },
  {
   "page": "/",
   "testid": "shelf-item-2680",
   "purpose": "Shelf entry showing title and author (pattern shelf-item-{catalogue_id})"
  },
  {
   "page": "/",
   "testid": "open-book-2680",
   "purpose": "Opens the book detail view (pattern open-book-{catalogue_id})"
  },
  {
   "page": "book detail",
   "testid": "position-input",
   "purpose": "Reading position field"
  },
  {
   "page": "book detail",
   "testid": "position-save",
   "purpose": "Save position"
  },
  {
   "page": "book detail",
   "testid": "position-display",
   "purpose": "Shows saved position"
  },
  {
   "page": "book detail",
   "testid": "resume-link",
   "purpose": "Link to the book text in LindyBooks/Gutenberg"
  },
  {
   "page": "book detail",
   "testid": "note-input",
   "purpose": "New note text field"
  },
  {
   "page": "book detail",
   "testid": "note-save",
   "purpose": "Save new note"
  },
  {
   "page": "book detail",
   "testid": "note-list",
   "purpose": "Notes of this book"
  },
  {
   "page": "book detail",
   "testid": "note-delete",
   "purpose": "Delete button of the (first) note"
  },
  {
   "page": "/",
   "testid": "export-link",
   "purpose": "Download JSON export"
  },
  {
   "page": "/",
   "testid": "logout",
   "purpose": "Log out"
  }
 ],
 "external_hosts": [
  "lindy-api.yi525tokyo.workers.dev"
 ],
 "migration": null
}
```

## How Regent will test it (it runs these itself against your running application)
```json
[
 {
  "id": "s1",
  "kind": "api",
  "steps": [
   {
    "do": "call",
    "api": "search",
    "query": {
     "q": "Meditations"
    },
    "expect_status": 200,
    "expect_json_contains": {
     "title": "Meditations",
     "author": "Marcus Aurelius"
    }
   },
   {
    "do": "call",
    "api": "shelf_add",
    "body": {
     "year": 180,
     "title": "Meditations",
     "author": "Marcus Aurelius, Emperor of Rome",
     "text_url": "https://www.gutenberg.org/ebooks/2680.txt.utf-8",
     "catalogue_id": 2680
    },
    "expect_status": 201,
    "expect_json_contains": {
     "title": "Meditations"
    }
   },
   {
    "do": "call",
    "api": "shelf_list",
    "expect_status": 200,
    "expect_json_contains": {
     "title": "Meditations",
     "author": "Marcus Aurelius, Emperor of Rome",
     "catalogue_id": 2680
    }
   },
   {
    "do": "call",
    "api": "shelf_add",
    "body": {
     "year": 180,
     "title": "Meditations",
     "author": "Marcus Aurelius, Emperor of Rome",
     "text_url": "https://www.gutenberg.org/ebooks/2680.txt.utf-8",
     "catalogue_id": 2680
    },
    "expect_status": 409
   }
  ],
  "requirement": "r1"
 },
 {
  "id": "s2",
  "kind": "api",
  "steps": [
   {
    "do": "call",
    "api": "shelf_add",
    "body": {
     "year": -650,
     "title": "The Odyssey: Rendered into English prose for the use of those who cannot read the original",
     "author": "Homer",
     "text_url": "https://www.gutenberg.org/ebooks/1727.txt.utf-8",
     "catalogue_id": 1727
    },
    "save": {
     "bid": "id"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "position_set",
    "body": {
     "position": "Book 5, line 120"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 200,
    "expect_json_contains": {
     "position": "Book 5, line 120"
    }
   },
   {
    "do": "call",
    "api": "shelf_get",
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 200,
    "expect_json_contains": {
     "position": "Book 5, line 120",
     "resume_url": "https://www.gutenberg.org/ebooks/1727.txt.utf-8"
    }
   },
   {
    "do": "restart_app"
   },
   {
    "do": "call",
    "api": "shelf_get",
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 200,
    "expect_json_contains": {
     "position": "Book 5, line 120"
    }
   },
   {
    "do": "call",
    "api": "position_set",
    "body": {
     "position": ""
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 400
   }
  ],
  "requirement": "r2"
 },
 {
  "id": "s3",
  "kind": "api",
  "steps": [
   {
    "do": "call",
    "api": "shelf_add",
    "body": {
     "year": 430,
     "title": "The City of God, Volume I",
     "author": "Augustine, of Hippo, Saint",
     "text_url": "https://www.gutenberg.org/ebooks/45304.txt.utf-8",
     "catalogue_id": 45304
    },
    "save": {
     "bid": "id"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "note_add",
    "body": {
     "body": "Restless hearts passage",
     "position": "Book 1"
    },
    "save": {
     "nid": "id"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "note_edit",
    "body": {
     "body": "Restless hearts passage, revisit"
    },
    "path_params": {
     "note_id": "{nid}"
    },
    "expect_status": 200,
    "expect_json_contains": {
     "body": "Restless hearts passage, revisit"
    }
   },
   {
    "do": "restart_app"
   },
   {
    "do": "call",
    "api": "note_list",
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 200,
    "expect_json_contains": {
     "body": "Restless hearts passage, revisit"
    }
   },
   {
    "do": "call",
    "api": "note_delete",
    "path_params": {
     "note_id": "{nid}"
    },
    "expect_status": 204
   },
   {
    "do": "call",
    "api": "note_edit",
    "body": {
     "body": "x"
    },
    "path_params": {
     "note_id": "{nid}"
    },
    "expect_status": 404
   },
   {
    "do": "call",
    "api": "note_add",
    "body": {
     "body": "orphan"
    },
    "path_params": {
     "id": "999999"
    },
    "expect_status": 404
   }
  ],
  "requirement": "r3"
 },
 {
  "id": "s4",
  "kind": "negative",
  "steps": [
   {
    "do": "call",
    "api": "shelf_add",
    "body": {
     "year": 180,
     "title": "Meditations",
     "author": "Marcus Aurelius, Emperor of Rome",
     "text_url": "https://www.gutenberg.org/ebooks/2680.txt.utf-8",
     "catalogue_id": 2680
    },
    "save": {
     "bid": "id"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "note_add",
    "body": {
     "body": "secret thought"
    },
    "save": {
     "nid": "id"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "shelf_list",
    "auth": false,
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "shelf_get",
    "auth": false,
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "note_list",
    "auth": false,
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "note_delete",
    "auth": false,
    "path_params": {
     "note_id": "{nid}"
    },
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "position_set",
    "auth": false,
    "body": {
     "position": "hack"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "shelf_add",
    "auth": false,
    "body": {
     "year": null,
     "title": "x",
     "author": "y",
     "text_url": "z",
     "catalogue_id": 1
    },
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "search",
    "auth": false,
    "query": {
     "q": "Meditations"
    },
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "export",
    "auth": false,
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "login",
    "auth": false,
    "body": {
     "passphrase": "definitely-wrong-passphrase"
    },
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "health",
    "auth": false,
    "expect_status": 200
   }
  ],
  "requirement": "r4"
 },
 {
  "id": "s5",
  "kind": "browser",
  "steps": [
   {
    "do": "goto",
    "path": "/"
   },
   {
    "do": "expect_visible",
    "target": "login-passphrase"
   },
   {
    "do": "fill",
    "value": "wrong-passphrase-entered",
    "target": "login-passphrase"
   },
   {
    "do": "click",
    "target": "login-submit"
   },
   {
    "do": "expect_visible",
    "target": "login-error"
   },
   {
    "do": "expect_hidden",
    "target": "shelf-list"
   }
  ],
  "requirement": "r4"
 },
 {
  "id": "s6",
  "kind": "browser",
  "steps": [
   {
    "do": "goto",
    "path": "/"
   },
   {
    "do": "fill",
    "value": "{passphrase}",
    "target": "login-passphrase"
   },
   {
    "do": "click",
    "target": "login-submit"
   },
   {
    "do": "fill",
    "value": "Meditations",
    "target": "search-input"
   },
   {
    "do": "click",
    "target": "search-submit"
   },
   {
    "do": "click",
    "target": "add-book-2680"
   },
   {
    "do": "expect_visible",
    "target": "shelf-item-2680"
   },
   {
    "do": "expect_text",
    "text": "Meditations"
   },
   {
    "do": "click",
    "target": "open-book-2680"
   },
   {
    "do": "fill",
    "value": "Book 4 section 3",
    "target": "position-input"
   },
   {
    "do": "click",
    "target": "position-save"
   },
   {
    "do": "expect_text",
    "text": "Book 4 section 3"
   },
   {
    "do": "expect_visible",
    "target": "resume-link"
   },
   {
    "do": "fill",
    "value": "The inner citadel idea",
    "target": "note-input"
   },
   {
    "do": "click",
    "target": "note-save"
   },
   {
    "do": "expect_text",
    "text": "The inner citadel idea"
   },
   {
    "do": "reload"
   },
   {
    "do": "expect_text",
    "text": "Book 4 section 3"
   },
   {
    "do": "expect_text",
    "text": "The inner citadel idea"
   },
   {
    "do": "restart_app"
   },
   {
    "do": "goto",
    "path": "/"
   },
   {
    "do": "fill",
    "value": "{passphrase}",
    "target": "login-passphrase"
   },
   {
    "do": "click",
    "target": "login-submit"
   },
   {
    "do": "click",
    "target": "open-book-2680"
   },
   {
    "do": "expect_text",
    "text": "Book 4 section 3"
   },
   {
    "do": "expect_text",
    "text": "The inner citadel idea"
   },
   {
    "do": "click",
    "target": "note-delete"
   },
   {
    "do": "expect_no_text",
    "text": "The inner citadel idea"
   },
   {
    "do": "reload"
   },
   {
    "do": "expect_no_text",
    "text": "The inner citadel idea"
   }
  ],
  "requirement": "r1 r2 r3 r5"
 },
 {
  "id": "s7",
  "kind": "api",
  "steps": [
   {
    "do": "call",
    "api": "shelf_add",
    "body": {
     "year": 180,
     "title": "Meditations",
     "author": "Marcus Aurelius, Emperor of Rome",
     "text_url": "https://www.gutenberg.org/ebooks/2680.txt.utf-8",
     "catalogue_id": 2680
    },
    "save": {
     "bid": "id"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "position_set",
    "body": {
     "position": "Book 2"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 200
   },
   {
    "do": "call",
    "api": "note_add",
    "body": {
     "body": "exported note text"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "export",
    "expect_status": 200,
    "expect_json_contains": {
     "body": "exported note text",
     "title": "Meditations",
     "position": "Book 2"
    }
   }
  ],
  "requirement": "r6"
 },
 {
  "id": "s8",
  "kind": "api",
  "steps": [
   {
    "do": "call",
    "api": "health",
    "auth": false,
    "expect_status": 200,
    "expect_json_contains": {
     "status": "ok"
    }
   },
   {
    "do": "call",
    "api": "login",
    "auth": false,
    "body": {
     "passphrase": "{passphrase}"
    },
    "expect_status": 200,
    "expect_json_contains": {
     "ok": true
    }
   }
  ],
  "requirement": "r5"
 }
]
```

## Data sources you may use (observed live by Regent)
```json
[
 {
  "url": "https://lindy-api.yi525tokyo.workers.dev/api/books",
  "fields": {
   "books": "list[200]",
   "total": "int",
   "corpus": "int",
   "books.0.a": "str",
   "books.0.s": "float",
   "books.0.t": "str",
   "books.0.dl": "int",
   "books.0.id": "int",
   "books.0.txt": "str",
   "books.0.lang": "str",
   "books.0.year": "int"
  },
  "sample": {
   "books": [
    {
     "a": "Augustine, of Hippo, Saint",
     "s": 15.7915,
     "t": "The City of God, Volume I",
     "dl": 102512,
     "id": 45304,
     "txt": "https://www.gutenberg.org/ebooks/45304.txt.utf-8",
     "lang": "en",
     "year": 430
    },
    {
     "a": "Homer",
     "s": 15.784,
     "t": "The Odyssey: Rendered into English prose for the use of those who cannot read the original",
     "dl": 35917,
     "id": 1727,
     "txt": "https://www.gutenberg.org/ebooks/1727.txt.utf-8",
     "lang": "en",
     "year": -650
    },
    {
     "a": "Marcus Aurelius, Emperor of Rome",
     "s": 15.4806,
     "t": "Meditations",
     "dl": 60197,
     "id": 2680,
     "txt": "https://www.gutenberg.org/ebooks/2680.txt.utf-8",
     "lang": "en",
     "year": 180
    },
    "… 197 more"
   ],
   "total": 5000,
   "corpus": 78696
  },
  "used_by_product_code_like": "ng={}; function fetchPageAt(offset){ var start=Math.floor(offset/3500)*3500; if(start<=0||_fetching[start]) return; _fetching[start]=1; fetch(API+\"/api/books?limit=3500&offset=\"+start).then(function(r){ return r.ok?r.json():null; }).then(function(d){ if(!d||!d.books||!d.books.length) return; var hav"
 },
 {
  "url": "https://lindy-api.yi525tokyo.workers.dev/api/fund",
  "fields": {
   "at": "str",
   "caps": "dict",
   "paid": "dict",
   "today": "dict",
   "total": "int",
   "recent": "list[2]",
   "sealed": "int",
   "paid.jpy": "int",
   "poolLeft": "int",
   "caps.meta": "int",
   "caps.prose": "int",
   "paid.count": "int",
   "recent.0.t": "int",
   "today.meta": "int",
   "today.prose": "int",
   "recent.0.cur": "str",
   "recent.0.amount": "int"
  },
  "sample": {
   "at": "2026-10-01T07:05:21.175Z",
   "caps": {
    "meta": 200000,
    "prose": 2000
   },
   "paid": {
    "jpy": 1000,
    "count": 2
   },
   "today": {
    "meta": 6,
    "prose": 45
   },
   "total": 78696,
   "recent": [
    {
     "t": 1785548408224,
     "cur": "usd",
     "amount": 1200
    },
    {
     "t": 1785394870691,
     "cur": "jpy",
     "amount": 1000
    }
   ],
   "sealed": 58,
   "poolLeft": 20000
  },
  "used_by_product_code_like": "entDefault();}; } panel.appendChild(a); }); var fund=document.createElement(\"div\"); fund.className=\"sp-fund\"; fund.textContent=\"…\"; fetch(API+\"/api/fund\").then(function(r){return r.ok?r.json():null;}).then(function(f){ if(!f){ fund.remove(); return; } fund.textContent=\"¥\"+(((f.paid||{}).jpy)||0).toL"
 },
 {
  "url": "https://lindy-api.yi525tokyo.workers.dev/api/search",
  "fields": {
   "books": "list[0]",
   "total": "int"
  },
  "sample": {
   "books": [],
   "total": 0
  },
  "used_by_product_code_like": "s=null; renderShelf(); return; } searchResults=null; renderShelf(); // show \"searching…\" immediately var mySeq=++searchSeq; fetch(API+\"/api/search?q=\"+encodeURIComponent(q)).then(function(r){ return r.ok?r.json():null; }).then(function(d){ if(mySeq!==searchSeq) return; // a newer "
 },
 {
  "url": "https://lindy-api.yi525tokyo.workers.dev/api/dam",
  "fields": {
   "at": "str",
   "total": "int",
   "sealed": "int"
  },
  "sample": {
   "at": "2026-10-01T07:05:21.175Z",
   "total": 78696,
   "sealed": 58
  },
  "used_by_product_code_like": " try{ history.replaceState({}, \"\", location.pathname); }catch(e){} // 再読込で再表示しない setTimeout(function(){ toast(tr(\"thanksGot\")); fetch(API+\"/api/dam\").then(function(r){ return r.ok?r.json():null; }).then(function(d){ if(!d || !d.total) return; setTimeout(function(){ toast(tr(\"sealedAll\")+\" \""
 }
]
```

## Runtime contract (Regent runs, tests and operates the application; it must follow this exactly)

- Write `regent.json` at the root: {"name": ..., "version": ..., "build": <shell command or null>,
  "test": <shell command>, "start": <shell command>, "health": "/api/health"}.
  `start` must listen on 127.0.0.1 at the port in the PORT environment variable (or `{port}` in the command)
  and keep ALL state under the directory in the DATA_DIR environment variable (or `{data_dir}`). Nothing else on
  disk may be written at run time.
- Dependencies: declare them (e.g. requirements.txt, package.json) and install them in `build`
  into the project directory (a virtualenv or node_modules inside it). Python 3.11 and Node 22 are available.
- `test` runs your own automated tests and exits non-zero on failure. They must not need network access.
- GET /api/health returns 200 without credentials.
- Credential: if the design requires one, the passphrase is in the APP_PASSPHRASE environment variable.
  POST /api/login with {"passphrase": "..."} returns {"token": "..."} (401 on a wrong passphrase); every
  non-public endpoint requires the header `Authorization: Bearer <token>` and returns 401 without it. Tokens
  must survive an application restart or be re-obtainable by logging in again. The UI login form uses
  data-testid="login-passphrase" for the input and data-testid="login-submit" for the button.
- The UI is served at / and works in a phone-sized browser. Put the listed data-testid attributes on the elements.
- Network: only the hosts listed under external_hosts may be called, from the server side. No analytics,
  tracking, ads, CDNs or third-party scripts; serve all assets yourself.
- You cannot run commands. Regent will build, test, start and exercise the application and send you the
  failures to fix. Write code that you are confident runs as written.
