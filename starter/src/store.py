"""PostgreSQL + pgvector persistent storage, idempotent upsert, and filtered cosine search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .models import Chunk

_ALLOWED_FILTERS: dict[str, tuple[str, str]] = {
    "document_id": ("document_id", "text"),
    "source": ("source", "text"),
    "page": ("page", "int"),
    "section": ("section", "text"),
    "year": ("year", "int"),
    "document_type": ("document_type", "text"),
}


def _to_vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in vec) + "]"


def _chunk_hash(chunk: Chunk) -> str:
    payload = json.dumps(
        {
            "document_id": chunk.document_id,
            "source": chunk.source,
            "page": chunk.page,
            "text": chunk.text,
            "metadata": chunk.metadata,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PgVectorStore:
    """Persistent pgvector store supporting exact cosine search and SQL metadata filters."""

    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url or not database_url.strip():
            raise ValueError("database_url must be non-empty")
        if dimensions <= 0:
            raise ValueError("dimensions must be a positive integer")
        self.database_url = database_url.strip()
        self.dimensions = int(dimensions)
        self.last_upsert_stats: dict[str, int] = {}

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.database_url, connect_timeout=10)

    def create_schema(self) -> None:
        """Create pgvector extension, chunks table, and metadata filter indexes."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS chunks (
                        strategy TEXT NOT NULL,
                        chunk_id TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        source TEXT NOT NULL,
                        page INTEGER NOT NULL,
                        section TEXT,
                        year INTEGER,
                        document_type TEXT,
                        text TEXT NOT NULL,
                        metadata JSONB NOT NULL,
                        embedding vector({self.dimensions}) NOT NULL,
                        content_hash TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (strategy, chunk_id)
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chunks_strategy_doc ON chunks (strategy, document_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chunks_strategy_filters "
                    "ON chunks (strategy, year, document_type, section)"
                )
            conn.commit()

    def count_chunks(self, strategy: str | None = None) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if strategy is None:
                    cur.execute("SELECT COUNT(*) FROM chunks")
                else:
                    cur.execute("SELECT COUNT(*) FROM chunks WHERE strategy = %s", (strategy,))
                row = cur.fetchone()
                return int(row[0]) if row else 0

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Idempotently upsert chunks for a strategy and prune obsolete chunks."""
        if not strategy or not strategy.strip():
            raise ValueError("strategy must be non-empty")
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks/vectors length mismatch: {len(chunks)} chunks vs {len(vectors)} vectors"
            )
        for idx, vec in enumerate(vectors):
            if len(vec) != self.dimensions:
                raise ValueError(
                    f"Vector at index {idx} has {len(vec)} dims; expected {self.dimensions}"
                )

        strategy_key = strategy.strip()
        if not chunks:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM chunks WHERE strategy = %s", (strategy_key,))
                conn.commit()
            self.last_upsert_stats = {"written": 0, "deleted_obsolete": 0, "total_after": 0}
            return

        doc_to_chunk_ids: dict[str, list[str]] = {}
        rows: list[tuple[Any, ...]] = []
        for chunk, vec in zip(chunks, vectors):
            doc_to_chunk_ids.setdefault(chunk.document_id, []).append(chunk.chunk_id)
            meta = dict(chunk.metadata)
            meta.setdefault("document_id", chunk.document_id)
            meta.setdefault("source", chunk.source)
            meta.setdefault("page", chunk.page)

            section_val = meta.get("section")
            year_val = meta.get("year")
            doc_type_val = meta.get("document_type")
            rows.append(
                (
                    strategy_key,
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.source,
                    int(chunk.page),
                    str(section_val) if section_val is not None else None,
                    int(year_val) if year_val is not None else None,
                    str(doc_type_val) if doc_type_val is not None else None,
                    chunk.text,
                    json.dumps(meta, ensure_ascii=False),
                    _to_vector_literal(vec),
                    _chunk_hash(chunk),
                )
            )

        deleted_obsolete = 0
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO chunks (
                            strategy, chunk_id, document_id, source, page,
                            section, year, document_type, text, metadata,
                            embedding, content_hash, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector, %s, NOW())
                        ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                            document_id = EXCLUDED.document_id,
                            source = EXCLUDED.source,
                            page = EXCLUDED.page,
                            section = EXCLUDED.section,
                            year = EXCLUDED.year,
                            document_type = EXCLUDED.document_type,
                            text = EXCLUDED.text,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding,
                            content_hash = EXCLUDED.content_hash,
                            updated_at = NOW()
                        WHERE chunks.content_hash IS DISTINCT FROM EXCLUDED.content_hash
                        """,
                        rows,
                    )

                    active_doc_ids = list(doc_to_chunk_ids.keys())
                    cur.execute(
                        "DELETE FROM chunks WHERE strategy = %s AND NOT (document_id = ANY(%s))",
                        (strategy_key, active_doc_ids),
                    )
                    deleted_obsolete += cur.rowcount

                    for doc_id, valid_ids in doc_to_chunk_ids.items():
                        cur.execute(
                            "DELETE FROM chunks WHERE strategy = %s AND document_id = %s "
                            "AND NOT (chunk_id = ANY(%s))",
                            (strategy_key, doc_id, valid_ids),
                        )
                        deleted_obsolete += cur.rowcount

                    cur.execute("SELECT COUNT(*) FROM chunks WHERE strategy = %s", (strategy_key,))
                    total_after = int(cur.fetchone()[0])

        self.last_upsert_stats = {
            "written": len(rows),
            "deleted_obsolete": deleted_obsolete,
            "total_after": total_after,
        }

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter permitted metadata in SQL, rank by exact cosine distance, return top-k."""
        if len(query_vector) != self.dimensions:
            raise ValueError(
                f"Query vector has {len(query_vector)} dims; expected {self.dimensions}"
            )
        if top_k <= 0:
            raise ValueError("top_k must be positive")

        vec_lit = _to_vector_literal(query_vector)
        where_clauses = ["strategy = %s"]
        where_params: list[Any] = [strategy.strip()]

        if filters:
            for key, raw_val in filters.items():
                if key not in _ALLOWED_FILTERS:
                    raise ValueError(f"Disallowed filter field: {key!r}")
                col_name, col_type = _ALLOWED_FILTERS[key]
                if isinstance(raw_val, (list, tuple, set)):
                    vals = [int(v) if col_type == "int" else str(v) for v in raw_val]
                    if not vals:
                        raise ValueError(f"Empty filter list for {key!r}")
                    where_clauses.append(f"{col_name} = ANY(%s)")
                    where_params.append(vals)
                else:
                    val = int(raw_val) if col_type == "int" else str(raw_val)
                    where_clauses.append(f"{col_name} = %s")
                    where_params.append(val)

        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT
                chunk_id,
                document_id,
                source,
                page,
                section,
                year,
                document_type,
                text,
                metadata,
                (embedding <=> %s::vector) AS distance,
                (1.0 - (embedding <=> %s::vector)) AS score
            FROM chunks
            WHERE {where_sql}
            ORDER BY distance ASC, chunk_id ASC
            LIMIT %s
        """
        params: list[Any] = [vec_lit, vec_lit, *where_params, int(top_k)]

        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        results: list[dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            meta = dict(row["metadata"]) if isinstance(row["metadata"], dict) else {}
            meta.setdefault("document_id", row["document_id"])
            meta.setdefault("source", row["source"])
            meta.setdefault("page", row["page"])
            if row["section"] is not None:
                meta.setdefault("section", row["section"])
            if row["year"] is not None:
                meta.setdefault("year", row["year"])
            if row["document_type"] is not None:
                meta.setdefault("document_type", row["document_type"])
            results.append(
                {
                    "rank": rank,
                    "chunk_id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "source": row["source"],
                    "page": int(row["page"]),
                    "text": row["text"],
                    "distance": float(row["distance"]),
                    "score": float(row["score"]),
                    "metadata": meta,
                }
            )
        return results
