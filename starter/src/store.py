"""PostgreSQL + pgvector storage, exact cosine search, and metadata filtering."""
from __future__ import annotations

import json
import re
from typing import Any

from .models import Chunk


_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_ALLOWED_FILTERS = {
    "document_id",
    "source",
    "page",
    "section",
    "category",
    "year",
    "language",
    "document_type",
    "audience",
    "version",
}
_TABLE = "chunks"


def _vector_literal(vector: list[float]) -> str:
    if not vector:
        raise ValueError("empty vector")
    return "[" + ",".join(f"{float(value):.8f}" for value in vector) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("database_url is required")
        if not isinstance(dimensions, int) or dimensions <= 0:
            raise ValueError("dimensions must be a positive integer")
        self.database_url = database_url
        self.dimensions = dimensions

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url, connect_timeout=10)

    def create_schema(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {_TABLE} (
                        strategy TEXT NOT NULL,
                        chunk_id TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        source TEXT NOT NULL,
                        page INTEGER NOT NULL,
                        section TEXT NOT NULL DEFAULT '',
                        text TEXT NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding vector({self.dimensions}) NOT NULL,
                        PRIMARY KEY (strategy, chunk_id)
                    )
                    """
                )
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS {_TABLE}_strategy_idx ON {_TABLE} (strategy)"
                )
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS {_TABLE}_metadata_idx ON {_TABLE} USING GIN (metadata)"
                )
                # Small corpus: exact sequential cosine scan. No ANN index is
                # created, so every query is exhaustive and reproducible.

    def drop_schema(self) -> None:
        """Drop the lab table; useful when switching embedding dimensions."""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {_TABLE}")

    def delete_strategy(self, strategy: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {_TABLE} WHERE strategy = %s", (strategy,))

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        if not chunks:
            self.delete_strategy(strategy)
            return
        for chunk, vector in zip(chunks, vectors):
            if len(vector) != self.dimensions:
                raise ValueError(
                    f"Dimension mismatch for {chunk.chunk_id}: "
                    f"expected {self.dimensions}, got {len(vector)}"
                )

        payload = []
        for chunk, vector in zip(chunks, vectors):
            metadata = dict(chunk.metadata)
            metadata.setdefault("section", "")
            metadata["document_id"] = chunk.document_id
            metadata["source"] = chunk.source
            metadata["page"] = chunk.page
            payload.append(
                (
                    strategy,
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.source,
                    chunk.page,
                    str(metadata.get("section", "")),
                    chunk.text,
                    json.dumps(metadata, ensure_ascii=False),
                    _vector_literal(vector),
                )
            )

        chunk_ids = [chunk.chunk_id for chunk in chunks]
        with self._connect() as connection:
            with connection.cursor() as cursor:
                # Remove chunks that no longer exist after re-ingestion.
                cursor.execute(
                    f"DELETE FROM {_TABLE} WHERE strategy = %s AND NOT (chunk_id = ANY(%s))",
                    (strategy, chunk_ids),
                )
                cursor.executemany(
                    f"""
                    INSERT INTO {_TABLE}
                        (strategy, chunk_id, document_id, source, page, section,
                         text, metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector)
                    ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                        document_id = EXCLUDED.document_id,
                        source = EXCLUDED.source,
                        page = EXCLUDED.page,
                        section = EXCLUDED.section,
                        text = EXCLUDED.text,
                        metadata = EXCLUDED.metadata,
                        embedding = EXCLUDED.embedding
                    """,
                    payload,
                )

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if len(query_vector) != self.dimensions:
            raise ValueError(
                f"Query dimension mismatch: expected {self.dimensions}, got {len(query_vector)}"
            )
        if top_k <= 0:
            raise ValueError("top_k must be positive")

        clauses = ["strategy = %s"]
        filter_json: str | None = None
        if filters:
            cleaned: dict[str, Any] = {}
            for key, value in filters.items():
                if not _IDENTIFIER_RE.match(key) or key not in _ALLOWED_FILTERS:
                    raise ValueError(f"Filter key is not allowed: {key}")
                if value is not None:
                    cleaned[key] = value
            if cleaned:
                clauses.append("metadata @> %s::jsonb")
                filter_json = json.dumps(cleaned, ensure_ascii=False)

        sql = f"""
            SELECT chunk_id, document_id, source, page, section, text, metadata,
                   1 - (embedding <=> %s::vector) AS score
            FROM {_TABLE}
            WHERE {' AND '.join(clauses)}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        vector_literal = _vector_literal(query_vector)
        sql_args: list[Any] = [vector_literal, strategy]
        if filter_json is not None:
            sql_args.append(filter_json)
        sql_args.extend([vector_literal, top_k])

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, sql_args)
                rows = cursor.fetchall()

        return [
            {
                "chunk_id": row[0],
                "document_id": row[1],
                "source": row[2],
                "page": row[3],
                "section": row[4],
                "text": row[5],
                "metadata": row[6],
                "score": float(row[7]),
            }
            for row in rows
        ]

    def strategy_stats(self, strategy: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*), COALESCE(SUM(length(text)), 0),
                           COALESCE(MIN(length(text)), 0), COALESCE(MAX(length(text)), 0),
                           COALESCE(AVG(length(text)), 0)
                    FROM {_TABLE} WHERE strategy = %s
                    """,
                    (strategy,),
                )
                count, total_chars, min_chars, max_chars, avg_chars = cursor.fetchone()
        return {
            "chunks": int(count),
            "total_chars": int(total_chars),
            "min_chars": int(min_chars),
            "max_chars": int(max_chars),
            "avg_chars": round(float(avg_chars), 2),
        }
