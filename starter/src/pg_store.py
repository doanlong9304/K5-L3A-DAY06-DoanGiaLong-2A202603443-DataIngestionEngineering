"""PostgreSQL + pgvector store: stable IDs, no duplicates, SQL metadata filter, cosine search."""
from __future__ import annotations

import hashlib
from typing import Any

import psycopg

from .models import Chunk

TABLE = "lab_chunks"
FILTERABLE = {"chapter", "section", "article", "document_id"}  # whitelisted filter columns
META_COLS = ["page_end", "chapter", "chapter_title", "section", "article", "char_start", "char_end"]


def _vec(v: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int, embedding_model: str) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.url = database_url
        self.dimensions = dimensions
        self.embedding_model = embedding_model
        try:
            self.conn = psycopg.connect(database_url, connect_timeout=5, autocommit=False)
        except psycopg.Error as exc:
            raise RuntimeError(f"Cannot connect to PostgreSQL: {exc}") from exc

    def close(self) -> None:
        self.conn.close()

    def create_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            if not cur.fetchone():
                raise RuntimeError("pgvector extension missing")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                    id              text PRIMARY KEY,
                    strategy        text NOT NULL,
                    document_id     text NOT NULL,
                    source          text NOT NULL,
                    page            integer NOT NULL,
                    page_end        integer NOT NULL,
                    chapter         text,
                    chapter_title   text,
                    section         text,
                    article         integer,
                    char_start      integer NOT NULL,
                    char_end        integer NOT NULL,
                    text            text NOT NULL,
                    char_count      integer NOT NULL,
                    content_hash    text NOT NULL,
                    doc_version     text NOT NULL,
                    embedding_model text NOT NULL,
                    embedding       vector({self.dimensions}) NOT NULL,
                    ingested_at     timestamptz NOT NULL DEFAULT now()
                )""")
            cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_strategy_chapter ON {TABLE} (strategy, chapter)")
            cur.execute("""SELECT atttypmod FROM pg_attribute
                           WHERE attrelid = %s::regclass AND attname = 'embedding'""", (TABLE,))
            dim = cur.fetchone()[0]
            if dim != self.dimensions:
                raise RuntimeError(f"{TABLE}.embedding has {dim} dims, model has {self.dimensions}")
        self.conn.commit()

    @staticmethod
    def content_hash(chunk: Chunk) -> str:
        return hashlib.sha256(chunk.text.encode()).hexdigest()

    def existing(self, strategy: str, document_id: str) -> dict[str, tuple[str, str]]:
        """id -> (content_hash, embedding_model) already stored."""
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT id, content_hash, embedding_model FROM {TABLE} "
                        "WHERE strategy = %s AND document_id = %s", (strategy, document_id))
            return {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    def needs_embedding(self, strategy: str, chunks: list[Chunk]) -> list[Chunk]:
        """Chunks whose ID/content/model are not stored yet (others reuse stored vectors)."""
        if not chunks:
            return []
        have = self.existing(strategy, chunks[0].document_id)
        return [c for c in chunks
                if have.get(c.chunk_id) != (self.content_hash(c), self.embedding_model)]

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: dict[str, list[float]],
               doc_version: str) -> dict[str, int]:
        """Write all chunks of one document for a strategy, in one transaction.

        `vectors` holds new embeddings keyed by chunk_id (only for chunks that need
        them); stored chunks get metadata refreshed and keep their vector. Rows of
        the same strategy+document that are not in `chunks` are deleted (obsolete).
        """
        ids = [c.chunk_id for c in chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate chunk IDs")
        doc_ids = {c.document_id for c in chunks}
        if len(doc_ids) != 1:
            raise ValueError("upsert expects chunks of exactly one document")
        document_id = doc_ids.pop()
        have = self.existing(strategy, document_id)
        for cid, v in vectors.items():
            if len(v) != self.dimensions:
                raise ValueError(f"{cid}: vector has {len(v)} dims")
        missing = [c.chunk_id for c in chunks if c.chunk_id not in vectors
                   and have.get(c.chunk_id) != (self.content_hash(c), self.embedding_model)]
        if missing:
            raise ValueError(f"{len(missing)} chunks have no stored or new vector")

        inserted = updated = 0
        with self.conn.cursor() as cur:
            for c in chunks:
                m = c.metadata
                row = [c.chunk_id, strategy, c.document_id, c.source, c.page,
                       *[m.get(k) for k in META_COLS], c.text, len(c.text),
                       self.content_hash(c), doc_version, self.embedding_model]
                if c.chunk_id in vectors:
                    cur.execute(f"""
                        INSERT INTO {TABLE} (id, strategy, document_id, source, page, {", ".join(META_COLS)},
                            text, char_count, content_hash, doc_version, embedding_model, embedding)
                        VALUES ({", ".join(["%s"] * (len(row) + 1))}::vector)
                        ON CONFLICT (id) DO UPDATE SET text = EXCLUDED.text,
                            content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding,
                            embedding_model = EXCLUDED.embedding_model, doc_version = EXCLUDED.doc_version,
                            ingested_at = now()""", row + [_vec(vectors[c.chunk_id])])
                    inserted += 1
                else:
                    sets = ", ".join(f"{k} = %s" for k in ["source", "page", *META_COLS, "doc_version"])
                    cur.execute(f"UPDATE {TABLE} SET {sets} WHERE id = %s",
                                [c.source, c.page, *[m.get(k) for k in META_COLS], doc_version, c.chunk_id])
                    updated += 1
            cur.execute(f"DELETE FROM {TABLE} WHERE strategy = %s AND document_id = %s AND NOT (id = ANY(%s))",
                        (strategy, document_id, ids))
            deleted = cur.rowcount
        self.conn.commit()
        return {"inserted_or_reembedded": inserted, "kept_metadata_refreshed": updated, "deleted_obsolete": deleted}

    def count(self, strategy: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT count(*), count(DISTINCT id), count(DISTINCT content_hash) FROM {TABLE} "
                        "WHERE strategy = %s", (strategy,))
            total, distinct, _ = cur.fetchone()
            if total != distinct:
                raise RuntimeError("duplicate rows detected")
            return total

    def chunk_at(self, strategy: str, document_id: str, offset: int) -> dict[str, Any] | None:
        """First chunk (by char_start) whose span covers a stream offset."""
        with self.conn.cursor() as cur:
            cur.execute(f"""SELECT id, page, page_end, chapter, article, char_start, char_end, text
                            FROM {TABLE} WHERE strategy = %s AND document_id = %s
                              AND char_start <= %s AND char_end > %s
                            ORDER BY char_start LIMIT 1""", (strategy, document_id, offset, offset))
            row = cur.fetchone()
            cols = [d.name for d in cur.description]
        self.conn.rollback()
        return dict(zip(cols, row)) if row else None

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if len(query_vector) != self.dimensions:
            raise ValueError("query vector dimension mismatch")
        where, params = ["strategy = %s", "embedding_model = %s"], [strategy, self.embedding_model]
        for key, value in (filters or {}).items():
            if key not in FILTERABLE:
                raise ValueError(f"filter on '{key}' not allowed")
            where.append(f"{key} = %s")
            params.append(value)
        qv = _vec(query_vector)
        with self.conn.cursor() as cur:
            cur.execute(f"""
                SELECT id, document_id, source, page, {", ".join(META_COLS)}, text,
                       1 - (embedding <=> %s::vector) AS score
                FROM {TABLE} WHERE {" AND ".join(where)}
                ORDER BY embedding <=> %s::vector, id
                LIMIT %s""", [qv, *params, qv, top_k])
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        self.conn.rollback()  # read-only; end the implicit transaction
        return [{"chunk_id": r["id"], "text": r["text"], "score": float(r["score"]),
                 "metadata": {"document_id": r["document_id"], "source": r["source"], "page": r["page"],
                              **{k: r[k] for k in META_COLS if k not in ("char_start", "char_end")}}}
                for r in rows]
