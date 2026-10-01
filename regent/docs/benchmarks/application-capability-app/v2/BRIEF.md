# Build this application
## What the person needs
I want to send a friend a link to my notes on one book, without them seeing anything else.

Requirements:
- [v2-r1] (core) Produce a single shareable link that resolves to the notes on exactly one specified book — accepted when: Opening the link in a fresh browser session shows all and only the principal's notes for that book
- [v2-r2] (core) Recipient can open the link without being given access to the principal's account or workspace — accepted when: A friend with no access to the principal's account opens the link and sees the notes; no login to the principal's account is needed, and no navigation leads to other notes, other books, or the account's index
- [v2-r3] (core) Scope isolation: the link exposes nothing beyond that book's notes (no other books' notes, library list, account details, or other links) — accepted when: Altering the link (e.g. guessing IDs, trimming the path) or following in-page links does not reveal any other content
- [v2-r4] (supporting) The link can be sent to the friend through a channel the principal chooses (copyable link) — accepted when: The link can be copied as text and pasted into a message
- [v2-r5] (supporting) Principal can revoke the link afterward — accepted when: After revoking, the link no longer shows the notes

## Design (Regent's interface contract; keep paths, fields and data-testids exactly)
```json
{
 "name": "lindy-reading-place",
 "summary": "Private single-owner reading shelf (catalogue search, shelf, positions, notes, export) plus v2: the owner can create an unguessable, revocable share link that publicly shows only one book's title, author and notes, with no navigation to anything else.",
 "entities": [
  {
   "name": "ShelfBook",
   "fields": [
    "id (server-assigned)",
    "catalogue_id (int, unique per shelf)",
    "title",
    "author",
    "year (int or null)",
    "text_url",
    "position (string, default empty)",
    "position_updated_at (ISO timestamp or null)",
    "added_at"
   ]
  },
  {
   "name": "Note",
   "fields": [
    "id (server-assigned)",
    "shelf_id",
    "body",
    "position (optional string)",
    "created_at",
    "updated_at"
   ]
  },
  {
   "name": "Share (new)",
   "fields": [
    "token (random, at least 128 bits, unguessable, primary key)",
    "shelf_id (exactly one ShelfBook)",
    "created_at"
   ]
  }
 ],
 "auth": {
  "why": "The shelf, positions and notes are personal, so one owner passphrase (Bearer token or session cookie from POST /api/login) protects every data endpoint. Public exceptions: /api/health, /api/login, and the new GET /api/shared/{token} plus the /shared/{token} page, which are guarded only by an unguessable random token that exposes a single book's title, author and notes and can be revoked.",
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
   "purpose": "Check the passphrase and set a session cookie",
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
   "purpose": "Search the LindyBooks catalogue by query q",
   "request": null,
   "response": "200 {\"books\":[{\"catalogue_id\",\"title\",\"author\",\"year\",\"text_url\"}],\"total\"}; 400 if q empty; 401 without credential; 502 if catalogue unreachable",
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
   "response": "200 ShelfBook plus resume_url; 404 if unknown",
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
   "purpose": "Remove a book, its notes and its share links",
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
   "response": "200 ShelfBook; 400 if empty; 404 if unknown",
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
  },
  {
   "id": "share_create",
   "path": "/api/shelf/{id}/shares",
   "method": "POST",
   "public": false,
   "purpose": "Create a share link for exactly this book's notes",
   "request": null,
   "response": "201 {\"token\",\"path\":\"/shared/{token}\",\"url\" (absolute, copyable),\"shelf_id\",\"created_at\"}; 404 if book unknown; 401 without credential",
   "requirements": [
    "v2-r1",
    "v2-r4"
   ]
  },
  {
   "id": "share_list",
   "path": "/api/shelf/{id}/shares",
   "method": "GET",
   "public": false,
   "purpose": "List active share links of one book",
   "request": null,
   "response": "200 {\"shares\":[{\"token\",\"path\",\"url\",\"created_at\"}]}; 404 if book unknown; 401 without credential",
   "requirements": [
    "v2-r4",
    "v2-r5"
   ]
  },
  {
   "id": "share_revoke",
   "path": "/api/shares/{token}",
   "method": "DELETE",
   "public": false,
   "purpose": "Revoke a share link",
   "request": null,
   "response": "204; 404 if unknown or already revoked; 401 without credential",
   "requirements": [
    "v2-r5"
   ]
  },
  {
   "id": "shared_get",
   "path": "/api/shared/{token}",
   "method": "GET",
   "public": true,
   "purpose": "Public, credential-free view of one shared book: only title, author and its notes; no ids of other records, no links to other content",
   "request": null,
   "response": "200 {\"book\":{\"title\",\"author\"},\"notes\":[{\"body\",\"position\",\"created_at\"}]}; 404 for unknown or revoked token (same body for both)",
   "requirements": [
    "v2-r1",
    "v2-r2",
    "v2-r3",
    "v2-r5"
   ]
  }
 ],
 "ui": [
  {
   "page": "/",
   "testid": "login-passphrase",
   "purpose": "Passphrase input"
  },
  {
   "page": "/",
   "testid": "login-submit",
   "purpose": "Log in button"
  },
  {
   "page": "/",
   "testid": "login-error",
   "purpose": "Shown after wrong passphrase"
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
   "purpose": "Add button for catalogue id 2680 (pattern add-book-{catalogue_id})"
  },
  {
   "page": "/",
   "testid": "shelf-list",
   "purpose": "Shelf container"
  },
  {
   "page": "/",
   "testid": "shelf-item-2680",
   "purpose": "Shelf entry (pattern shelf-item-{catalogue_id})"
  },
  {
   "page": "/",
   "testid": "open-book-2680",
   "purpose": "Open book detail (pattern open-book-{catalogue_id})"
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
   "purpose": "Saved position"
  },
  {
   "page": "book detail",
   "testid": "resume-link",
   "purpose": "Link to book text"
  },
  {
   "page": "book detail",
   "testid": "note-input",
   "purpose": "New note field"
  },
  {
   "page": "book detail",
   "testid": "note-save",
   "purpose": "Save note"
  },
  {
   "page": "book detail",
   "testid": "note-list",
   "purpose": "Notes of this book"
  },
  {
   "page": "book detail",
   "testid": "note-delete",
   "purpose": "Delete first note"
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
  },
  {
   "page": "book detail",
   "testid": "share-create",
   "purpose": "Create a share link for this book"
  },
  {
   "page": "book detail",
   "testid": "share-link",
   "purpose": "Shows the full share URL as selectable text"
  },
  {
   "page": "book detail",
   "testid": "share-copy",
   "purpose": "Copy the share URL to clipboard"
  },
  {
   "page": "book detail",
   "testid": "share-revoke",
   "purpose": "Revoke the shown share link"
  },
  {
   "page": "/shared/{token}",
   "testid": "shared-book-title",
   "purpose": "Title and author of the shared book on the public page"
  },
  {
   "page": "/shared/{token}",
   "testid": "shared-note-list",
   "purpose": "Notes of the shared book; the page has no login form, shelf, or links to other content"
  },
  {
   "page": "/shared/{token}",
   "testid": "shared-unavailable",
   "purpose": "Shown when the token is unknown or revoked (no content)"
  }
 ],
 "external_hosts": [
  "lindy-api.yi525tokyo.workers.dev"
 ],
 "migration": "Purely additive. Existing shelf books, positions and notes tables/records are left untouched. On startup, create the new shares store (token, shelf_id, created_at) if it does not exist. No existing rows are rewritten, and all existing endpoints keep their paths and fields. Deleting a book also deletes its shares."
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
     "catalogue_id": 2680
    }
   }
  ],
  "requirement": "r1"
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
    "api": "shelf_list",
    "auth": false,
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
    "api": "note_list",
    "auth": false,
    "path_params": {
     "id": "{bid}"
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
   }
  ],
  "requirement": "r5"
 },
 {
  "id": "v1",
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
    "api": "shelf_add",
    "body": {
     "year": -650,
     "title": "The Odyssey: Rendered into English prose for the use of those who cannot read the original",
     "author": "Homer",
     "text_url": "https://www.gutenberg.org/ebooks/1727.txt.utf-8",
     "catalogue_id": 1727
    },
    "save": {
     "oid": "id"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "note_add",
    "body": {
     "body": "Meditations shared note alpha"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "note_add",
    "body": {
     "body": "Odyssey private note omega"
    },
    "path_params": {
     "id": "{oid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "share_create",
    "save": {
     "token": "token"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201,
    "expect_json_contains": {
     "path": "/shared/"
    }
   },
   {
    "do": "call",
    "api": "shared_get",
    "auth": false,
    "path_params": {
     "token": "{token}"
    },
    "expect_status": 200,
    "expect_json_contains": {
     "body": "Meditations shared note alpha",
     "title": "Meditations"
    }
   },
   {
    "do": "call",
    "api": "share_list",
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 200,
    "expect_json_contains": {
     "token": "{token}"
    }
   }
  ],
  "requirement": "v2-r1 v2-r4"
 },
 {
  "id": "v2",
  "kind": "browser",
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
    "api": "shelf_add",
    "body": {
     "year": -650,
     "title": "The Odyssey: Rendered into English prose for the use of those who cannot read the original",
     "author": "Homer",
     "text_url": "https://www.gutenberg.org/ebooks/1727.txt.utf-8",
     "catalogue_id": 1727
    },
    "save": {
     "oid": "id"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "note_add",
    "body": {
     "body": "Meditations shared note alpha"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "note_add",
    "body": {
     "body": "Odyssey private note omega"
    },
    "path_params": {
     "id": "{oid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "share_create",
    "save": {
     "token": "token"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "logout"
   },
   {
    "do": "goto",
    "path": "/shared/{token}"
   },
   {
    "do": "expect_visible",
    "target": "shared-book-title"
   },
   {
    "do": "expect_text",
    "text": "Meditations shared note alpha"
   },
   {
    "do": "expect_no_text",
    "text": "Odyssey private note omega"
   },
   {
    "do": "expect_no_text",
    "text": "Homer"
   },
   {
    "do": "expect_hidden",
    "target": "shelf-list"
   },
   {
    "do": "expect_hidden",
    "target": "login-passphrase"
   },
   {
    "do": "expect_hidden",
    "target": "export-link"
   }
  ],
  "requirement": "v2-r1 v2-r2 v2-r3"
 },
 {
  "id": "v3",
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
    "api": "share_create",
    "save": {
     "token": "token"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "shared_get",
    "auth": false,
    "path_params": {
     "token": "{bid}"
    },
    "expect_status": 404
   },
   {
    "do": "call",
    "api": "shared_get",
    "auth": false,
    "path_params": {
     "token": "1"
    },
    "expect_status": 404
   },
   {
    "do": "call",
    "api": "shared_get",
    "auth": false,
    "path_params": {
     "token": "not-a-real-token-0000"
    },
    "expect_status": 404
   },
   {
    "do": "call",
    "api": "share_create",
    "auth": false,
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "share_list",
    "auth": false,
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "share_revoke",
    "auth": false,
    "path_params": {
     "token": "{token}"
    },
    "expect_status": 401
   },
   {
    "do": "call",
    "api": "shelf_list",
    "auth": false,
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
    "api": "share_create",
    "path_params": {
     "id": "999999"
    },
    "expect_status": 404
   },
   {
    "do": "goto",
    "path": "/shared/not-a-real-token-0000"
   },
   {
    "do": "expect_visible",
    "target": "shared-unavailable"
   },
   {
    "do": "expect_hidden",
    "target": "shared-note-list"
   }
  ],
  "requirement": "v2-r3 v2-r2"
 },
 {
  "id": "v4",
  "kind": "browser",
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
     "body": "Meditations shared note alpha"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
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
    "do": "click",
    "target": "share-create"
   },
   {
    "do": "expect_visible",
    "target": "share-link"
   },
   {
    "do": "expect_visible",
    "target": "share-copy"
   },
   {
    "do": "expect_text",
    "text": "/shared/"
   },
   {
    "do": "click",
    "target": "share-revoke"
   },
   {
    "do": "expect_hidden",
    "target": "share-link"
   },
   {
    "do": "reload"
   },
   {
    "do": "expect_hidden",
    "target": "share-link"
   }
  ],
  "requirement": "v2-r4 v2-r5"
 },
 {
  "id": "v5",
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
    "api": "note_add",
    "body": {
     "body": "Meditations shared note alpha"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "share_create",
    "save": {
     "token": "token"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "shared_get",
    "auth": false,
    "path_params": {
     "token": "{token}"
    },
    "expect_status": 200
   },
   {
    "do": "call",
    "api": "share_revoke",
    "path_params": {
     "token": "{token}"
    },
    "expect_status": 204
   },
   {
    "do": "call",
    "api": "shared_get",
    "auth": false,
    "path_params": {
     "token": "{token}"
    },
    "expect_status": 404
   },
   {
    "do": "call",
    "api": "share_revoke",
    "path_params": {
     "token": "{token}"
    },
    "expect_status": 404
   },
   {
    "do": "goto",
    "path": "/shared/{token}"
   },
   {
    "do": "expect_visible",
    "target": "shared-unavailable"
   },
   {
    "do": "expect_no_text",
    "text": "Meditations shared note alpha"
   }
  ],
  "requirement": "v2-r5"
 },
 {
  "id": "v6",
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
     "body": "Restless hearts passage"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "call",
    "api": "share_create",
    "save": {
     "token": "token"
    },
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 201
   },
   {
    "do": "restart_app"
   },
   {
    "do": "call",
    "api": "shared_get",
    "auth": false,
    "path_params": {
     "token": "{token}"
    },
    "expect_status": 200,
    "expect_json_contains": {
     "body": "Restless hearts passage",
     "title": "The City of God, Volume I"
    }
   },
   {
    "do": "call",
    "api": "shelf_remove",
    "path_params": {
     "id": "{bid}"
    },
    "expect_status": 204
   },
   {
    "do": "call",
    "api": "shared_get",
    "auth": false,
    "path_params": {
     "token": "{token}"
    },
    "expect_status": 404
   }
  ],
  "requirement": "v2-r1 v2-r5"
 },
 {
  "id": "prev-s1",
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
  "regression": true,
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
  "regression": true,
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
  "regression": true,
  "requirement": "r3"
 },
 {
  "id": "prev-s4",
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
  "regression": true,
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
  "regression": true,
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
  "regression": true,
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
  "regression": true,
  "requirement": "r6"
 },
 {
  "id": "prev-s8",
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
  "regression": true,
  "requirement": "r5"
 }
]
```

## Data sources you may use (observed live by Regent)
```json
[]
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

## This is an upgrade of version 1 which is in use
The current code is in this directory. Real data written by that version will be in DATA_DIR when this version starts: migrate it on start, losing nothing, and keep every existing endpoint working with the same paths and fields. Regent will start this version on a copy of the real data and compare every record before and after.
