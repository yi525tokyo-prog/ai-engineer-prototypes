"""Memory: semantic recall over observations, decisions and outcomes.

Embeddings default to a deterministic feature-hashing embedder (no external
calls); on PostgreSQL recall uses pgvector cosine distance, elsewhere it is
computed in Python. A provider embedding model can replace ``HashEmbedder``
without schema changes as long as it emits ``EMBEDDING_DIM`` floats.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from regent.db import EMBEDDING_DIM, Memory
from regent.ids import new_id


class HashEmbedder:
    dim = EMBEDDING_DIM

    def embed(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        toks = re.findall(r"[a-z0-9]+", text.lower())
        grams = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        for g in grams:
            h = int(hashlib.blake2b(g.encode(), digest_size=8).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


class MemoryStore:
    def __init__(self, db: Session, embedder: HashEmbedder | None = None):
        self.db = db
        self.embedder = embedder or HashEmbedder()

    def remember(self, text: str, *, kind: str, domain: str = "private", mission_id: str | None = None,
                 meta: dict[str, Any] | None = None) -> Memory:
        m = Memory(id=new_id("mem"), domain=domain, kind=kind, text=text, embedding=self.embedder.embed(text),
                   meta=meta or {}, mission_id=mission_id)
        self.db.add(m)
        self.db.flush()
        return m

    def recall(self, query: str, k: int = 5, *, domains: tuple[str, ...] = ("private", "shared", "global"),
               kind: str | None = None) -> list[tuple[Memory, float]]:
        qv = self.embedder.embed(query)
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            dist = Memory.embedding.cosine_distance(qv)  # type: ignore[attr-defined]
            q = select(Memory, dist.label("d")).where(Memory.domain.in_(domains))
            if kind:
                q = q.where(Memory.kind == kind)
            rows = self.db.execute(q.order_by(dist).limit(k)).all()
            return [(m, round(1 - float(d), 4)) for m, d in rows]
        q = select(Memory).where(Memory.domain.in_(domains))
        if kind:
            q = q.where(Memory.kind == kind)
        scored = []
        for m in self.db.scalars(q):
            if m.embedding:
                scored.append((m, round(sum(a * b for a, b in zip(qv, m.embedding)), 4)))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]
